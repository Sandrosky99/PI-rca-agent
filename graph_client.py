"""
graph_client.py — Cliente MCP para afkg-graph-mcp (Step 2 del workflow RCA)

¿Qué hace este fichero?
  Lanza afkg-graph-mcp como subproceso (protocolo MCP sobre stdio, igual que
  hace Claude Code según .mcp.json) y usa su tool graph_neighborhood para
  recorrer recursivamente el árbol del Asset Framework de un elemento,
  construyendo main_asset_context / nearby_elements_context: la lista plana
  de variables reales (con su piApiPath) que se pasará al modelo en el Step 3,
  para que elija solo entre atributos que existen de verdad.

¿Por qué un recorrido recursivo y no una sola llamada?
  graph_neighborhood solo devuelve el vecindario directo de un elemento (sus
  atributos y sus hijos PARENT_OF inmediatos), no el árbol completo. Los
  activos del AF tienen varios niveles (p.ej. Bomba > Indicators > Hydraulic
  Effiency > Level Alert), así que hay que bajar nivel a nivel.

¿Por qué se usa el "path" del hijo como path_contains en la llamada recursiva?
  graph_neighborhood empareja por nombre exacto, y varios elementos del AF
  comparten nombre (p.ej. "Level Alert" o "Reference" aparecen repetidos bajo
  distintos indicadores). Sin desambiguar, la consulta puede devolver el
  elemento equivocado (visto en producción explorando manualmente el AF). El
  "path" que ya trae cada hijo en la respuesta de su padre es único, así que
  sirve como desambiguador fiable sin tener que reconstruirlo a mano.
"""

import json
import logging
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import config

# Separador de rutas del AF. Se escribe asi para que no se confunda con una
# secuencia de escape al leer el fichero.
SEPARADOR_AF = chr(92)

log = logging.getLogger(__name__)

_SERVER_PARAMS = StdioServerParameters(
    command="uv",
    args=[
        "run", "--directory", config.AFKG_GRAPH_MCP_DIR,
        "python", "src/afkg_graph_mcp/server.py",
    ],
)


@asynccontextmanager
async def sesion_af():
    """Abre una sesion contra afkg-graph-mcp y la cierra al salir.

    Existe porque el bucle de la busqueda hace VARIAS consultas seguidas -- una
    por vuelta, hasta cinco -- y cada una abriendo su propia sesion significaria
    arrancar cinco veces el subproceso del servidor. Con esto se abre una y se
    reutiliza.

    Quien conduce el bucle es workflow.py, no este fichero: las decisiones del
    modelo viven alli y el acceso al grafo vive aqui. Por eso lo que se exporta
    es la sesion, y no un bucle que llamaria al modelo desde el cliente del
    grafo -- que hoy ni sabe que existe un modelo.
    """
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session

# Atributos de metadatos/bookkeeping del AF: identifican o describen el activo
# (o son configuración interna del PI Point), pero no son variables de proceso
# consultables para un diagnóstico -- se omiten del contexto que ve el modelo.
_METADATA_ATTRS = {
    "Area Code", "Asset Code", "Asset Model", "Asset Type", "Naming Convention",
    "PI Data Archive", "Path", "Plant Code", "Plant", "System", "Subsystem",
    "Asset", "KPI Name", "Manufacturer", "Serial Number", "Tag Name",
    "Equipment Model",
}

# Salvaguarda ante ciclos o jerarquías anómalas en el AF -- en la práctica los
# activos de este dominio no bajan de 3-4 niveles (asset > Indicators > KPI > Level Alert).
_MAX_DEPTH = 8

# De todo lo que _METADATA_ATTRS descarta, estos son los que SI dicen algo al
# diagnosticar: que maquina es. No entran en la seleccion de variables del
# Step 4 -- ahi solo estorbarian, no son magnitudes de proceso -- pero se
# consultan aparte y entran en el contexto del Step 3 como ficha del equipo.
#
# El motivo (2026-09-23). El Step 6 conocia el equipo solo por AssetType y
# AssetModel del payload, y PI no siempre los manda: el payload real del
# 2026-09-08 no los traia, y el resumen se quedaba en "una desviacion en el KPI
# Hydraulic Efficiency de PS20102 A03 PS02 Pump 02", sin decir que era una bomba
# centrifuga monocanal -- que es justo lo que hace plausible un atascamiento por
# trapos. El AF si lo sabe.
_IDENTIDAD_ATTRS = ("Asset Type", "Asset Model", "Manufacturer",
                    "Equipment Model", "Serial Number", "Tag Name")


def _filter_attributes(raw_attrs: list[dict]) -> list[dict]:
    """Descarta atributos de metadatos y normaliza los campos que ve el modelo.

    description/uom vacíos ("") se dejan explícitos (no se omite la clave):
    el AF simplemente no documenta ese campo para ese atributo, no significa
    que el atributo no exista o no sea relevante.
    """
    filtered = []
    for attr in raw_attrs:
        name = attr.get("name", "")
        if name in _METADATA_ATTRS:
            continue
        filtered.append({
            "name": name,
            "uom": attr.get("uom", ""),
            "description": attr.get("description", ""),
            "piApiPath": attr.get("piApiPath", ""),
        })
    return filtered


async def _call_graph_neighborhood(session: ClientSession, name: str, path_contains: str | None) -> dict:
    result = await session.call_tool(
        "graph_neighborhood",
        {"name": name, "path_contains": path_contains, "limit": 200},
    )
    text = result.content[0].text
    return json.loads(text)


async def _walk_subtree(
    session: ClientSession,
    name: str,
    path_contains: str | None,
    breadcrumb: list[str],
    skip_names: set[str],
    depth: int = _MAX_DEPTH,
) -> list[dict]:
    """Recorre recursivamente el subárbol PARENT_OF de un elemento del AF.

    Devuelve una lista plana de grupos {element, description, attributes}.
    Los nodos puramente organizativos (sin atributos propios, p.ej. "Meters"
    o "Indicators") no generan grupo propio -- su nombre solo queda reflejado
    en el breadcrumb de sus descendientes, que es donde aporta significado.
    """
    if depth <= 0:
        log.warning("Profundidad máxima alcanzada explorando '%s'; se corta la recursión.", name)
        return []

    data = await _call_graph_neighborhood(session, name, path_contains)
    if "error" in data:
        log.warning(
            "graph_neighborhood no encontró '%s' (path_contains=%s): %s",
            name, path_contains, data["error"],
        )
        return []

    element = data["element"]
    relationships = data.get("relationships", {})
    attributes = _filter_attributes(relationships.get("HAS_ATTRIBUTE", []))
    children = relationships.get("PARENT_OF", [])

    groups = []
    if attributes:
        groups.append({
            "element": " > ".join(breadcrumb),
            "description": element.get("description", ""),
            "attributes": attributes,
        })

    for child in children:
        child_name = child.get("name", "")
        if child_name in skip_names:
            continue
        groups.extend(await _walk_subtree(
            session, child_name, child.get("path"), breadcrumb + [child_name], skip_names, depth - 1,
        ))

    return groups


async def _call_graph_search(session: ClientSession, nombre: str,
                             path_contains: str | None) -> list[dict]:
    """Todos los elementos cuyo nombre contiene 'nombre' dentro de ese path.

    graph_search y no graph_neighborhood porque devuelve TODOS los candidatos y
    su numero. graph_neighborhood devuelve uno, y no dice si habia mas -- que es
    justo el dato que hace falta para saber si la pregunta estaba bien hecha.
    """
    _TOPE = 200
    result = await session.call_tool(
        "graph_search",
        {"node_type": "Element", "name_contains": nombre,
         "path_contains": path_contains, "limit": _TOPE},
    )
    texto = "".join(getattr(c, "text", "") for c in result.content)
    i, j = texto.find("{"), texto.rfind("}")
    if i < 0:
        return []
    datos = json.loads(texto[i:j + 1])
    if datos.get("count", 0) >= _TOPE:
        # graph_search devuelve EXACTAMENTE el limite pedido sin avisar de que
        # hay mas. Visto el 2026-09-22 dando por bueno un recuento truncado.
        log.warning("AF: la busqueda de '%s' toco el tope de %d; puede haber mas.", nombre, _TOPE)
    return datos.get("results", [])


def _segmentos(path: str) -> list[str]:
    return [t for t in (path or "").split(SEPARADOR_AF) if t]


async def _resolver_cadena(session: ClientSession, asset_name: str,
                           subsystem_name: str, system_name: str) -> tuple[str | None, str | None]:
    """El path del activo y el de su subsistema, usando la CADENA entera.

    Lo que identifica un elemento en el AF no es su nombre: es la cadena
    System + Subsystem + Asset. Cualquiera de los tres por separado se repite
    -- medido el 2026-09-22 sobre el grafo entero: 47 nombres del WWTP existen
    tambien en otra raiz y 45 se repiten dentro del propio WWTP, y PI puede
    mandar perfectamente dos alertas con Subsystem="Biological" bajo Systems
    distintos --, pero los tres juntos no.

    Por eso NO se resuelve el subsistema primero por su nombre, que fue el
    primer intento (2026-09-22) y estaba mal por este mismo motivo: apoyaba toda
    la desambiguacion en dos de los tres eslabones.

    Se busca el ACTIVO, se exige que su path contenga los otros dos como
    SEGMENTOS -- no como subcadena: "WWTP" es subcadena de "WWTP_Demo", que es
    un modelo espejo de esta misma planta -- y el subsistema sale de ese mismo
    path, asi que no hace falta buscarlo y no puede salir otro.

    Si sobra mas de un candidato se dice en el log en vez de callarlo: significa
    que la cadena no basto, y eso es informacion sobre el modelo de PI.
    """
    planta = (config.AF_PLANT_ROOT + SEPARADOR_AF) if config.AF_PLANT_ROOT else None
    candidatos = [c for c in await _call_graph_search(session, asset_name, planta)
                  if c.get("name") == asset_name]
    if not candidatos:
        log.warning("AF: no hay ningun elemento llamado '%s' dentro de %s.",
                    asset_name, config.AF_PLANT_ROOT or "el grafo")
        return None, None

    # Se estrecha con los dos eslabones restantes, y se afloja en orden inverso
    # si la cadena no casa: un System mal configurado en PI no puede costar el
    # analisis entero -- quedarse sin contexto es peor que la ambiguedad.
    for exigidos in ((subsystem_name, system_name), (subsystem_name,), (system_name,), ()):
        pedidos = [e for e in exigidos if e]
        quedan = [c for c in candidatos
                  if all(e in _segmentos(c.get("path", "")) for e in pedidos)]
        if quedan:
            break
    else:
        quedan = candidatos

    if len(quedan) > 1:
        log.warning("AF: '%s' sigue siendo ambiguo con System='%s' y Subsystem='%s': "
                    "%d candidatos. Se usa el primero: %s",
                    asset_name, system_name, subsystem_name, len(quedan),
                    quedan[0].get("path"))

    path_activo = quedan[0].get("path")
    # El subsistema NO se busca: se recorta del path del activo, asi que es por
    # construccion el que contiene a ese activo y no otro con el mismo nombre.
    path_subsistema = None
    seg = _segmentos(path_activo)
    if subsystem_name in seg:
        corte = len(seg) - 1 - seg[::-1].index(subsystem_name)
        path_subsistema = SEPARADOR_AF.join(seg[:corte + 1])

    log.info("AF: cadena resuelta | activo=%s | subsistema=%s", path_activo, path_subsistema)
    return path_activo, path_subsistema


# Tope de resultados que se le devuelven al modelo por busqueda. No es coste:
# es que una lista larga no se elige, se hojea. Si hacen falta mas, que afine el
# termino -- para eso el bucle tiene cinco vueltas.
_TOPE_RESULTADOS = 8

# Tope de longitud del termino. El modelo escribe este parametro, asi que es
# entrada no confiable como cualquier otra cosa que venga de el.
_TOPE_TERMINO = 80


def _es_proceso(path: str) -> bool:
    """Si ese path esta en una rama que mide algo.

    Las ramas de diagramas y modelos importados devuelven elementos con pinta de
    dato que no lo son. Se comprueba por SEGMENTO y no por subcadena, igual que
    el resto: "Sankey Diagrams" no puede colarse como parte de otro nombre.
    """
    return not (set(_segmentos(path)) & set(config.AF_RAMAS_NO_PROCESO))


# Tope de palabras por termino. Mas de cinco no afina, solo gasta consultas.
_TOPE_PALABRAS = 5


async def _buscar_palabra(session: ClientSession, palabra: str) -> dict:
    """Una consulta al grafo por una sola palabra. {clave: acierto}, SIN tope.

    El tope se aplica al final, despues de cruzar las palabras: recortar aqui
    podria tirar justo el resultado que sobrevive al cruce.
    """
    planta = (config.AF_PLANT_ROOT + SEPARADOR_AF) if config.AF_PLANT_ROOT else None
    salida: dict = {}

    for tipo in ("Element", "Attribute"):
        # El filtro de planta del servidor solo sirve para elementos: el 'path'
        # de un ATRIBUTO es unicamente su nombre ("Turbidity inlet"), asi que
        # path_contains="WWTP" no casa con ninguno y los descartaba todos.
        # Comprobado el 2026-09-28: con el filtro, 0 resultados; sin el, 2.
        # Para atributos se acota aqui abajo, contra su piApiPath.
        result = await session.call_tool(
            "graph_search",
            {"node_type": tipo, "name_contains": palabra,
             "path_contains": planta if tipo == "Element" else None, "limit": 200},
        )
        texto = "".join(getattr(c, "text", "") for c in result.content)
        i, j = texto.find("{"), texto.rfind("}")
        if i < 0:
            continue
        for nodo in json.loads(texto[i:j + 1]).get("results", []):
            # De un atributo interesa DONDE vive, que es lo que lo identifica;
            # su 'path' a secas es solo su nombre.
            ruta = (nodo.get("piApiPath") or nodo.get("path") or "")
            donde = ruta.split("|")[0].split("BBDD_DEMO" + SEPARADOR_AF)[-1]
            # La planta se comprueba SIEMPRE aqui, tambien para los elementos:
            # que el servidor ya haya filtrado no quita que esta sea la unica
            # linea que garantiza que nada de otra instalacion se cuela.
            if not donde or not donde.startswith(config.AF_PLANT_ROOT + SEPARADOR_AF):
                continue
            if not _es_proceso(donde):
                continue
            clave = donde + "|" + (nodo.get("name") or "")
            if clave in salida:
                continue
            salida[clave] = {
                "tipo": "elemento" if tipo == "Element" else "variable",
                "nombre": nodo.get("name") or "",
                "donde": donde,
                "descripcion": nodo.get("description") or "",
            }
    return salida


async def buscar_en_la_planta(session: ClientSession, termino: str) -> list[dict]:
    """Busca en el AF de esta planta. Es la UNICA herramienta que recibe el modelo.

    No se le pasa el servidor MCP, se le pasa esto. La diferencia importa: entre
    las tools de afkg-graph-mcp esta refresh_graph, que es de ESCRITURA -- su
    ultima pasada real inserto 1.087 nodos, actualizo 1.280 y borro 1.457 sobre
    un Neo4j que comparten las demas demos. Darle el servidor seria darle eso, y
    no hace falta que se le ocurra usarlo mal: basta con que lo llame "para
    asegurarse de que el grafo esta al dia".

    Asi que el modelo es agentico en la DECISION -- que buscar, cuando parar --
    pero no en el ALCANCE: la planta la fija el codigo, las ramas que no son
    proceso se descartan aqui, y el numero de resultados tambien.

    EL TERMINO SE DESCOMPONE EN PALABRAS, y eso es lo que hace que esto funcione.
    graph_search casa por fragmento CONTIGUO del nombre, asi que "pH primary" no
    encuentra nada aunque exista "PH213 Primary Line 1 Outlet pH". En la segunda
    ejecucion real (2026-09-29) el modelo gasto sus cinco vueltas en variantes de
    eso y no encontro un elemento que estaba ahi. Medido despues, palabra a
    palabra:

        pH -> 8 (con PH213)    primary -> 8 (con PH213)    outlet -> 8 (con PH213)
        clarifier -> 0         primario -> 0               salida -> 0

    Todas las que uso habrian funcionado por separado. Fallo por juntarlas.

    Asi que se consulta cada palabra y se CRUZAN los resultados. Las palabras que
    no devuelven nada se IGNORAN en vez de vaciar el cruce: que una palabra no
    aparezca en esta planta no significa que no exista lo que se busca --
    significa que esa palabra no es del vocabulario de aqui. Es lo que permite
    que "pH a la salida del primario", en castellano, encuentre PH213: "salida" y
    "primario" dan cero, se ignoran, y queda "pH".

    Se puede hacer porque una consulta al grafo cuesta 0,04 s y una vuelta del
    modelo entre 5 y 15 -- ademas de contar para el limite de ritmo del
    proveedor, que es lo que tumbo aquel analisis. El trabajo va donde es barato.

    Busca elementos Y atributos porque un operario puede nombrar cualquiera de
    los dos: "el pH de salida del primario" es un elemento (PH213...), pero "la
    turbidez de entrada" es un atributo de otro elemento (1 - Intake|Turbidity
    inlet). Cual sea no lo sabe quien escribe la sospecha.

    Devuelve una lista de {tipo, nombre, donde, descripcion}, sin piApiPath: lo
    que el modelo elige es un SITIO, y las rutas las resuelve el codigo despues.
    Asi no puede copiar mal una ruta ni inventarsela.
    """
    termino = (termino or "").strip()[:_TOPE_TERMINO]
    palabras = termino.split()[:_TOPE_PALABRAS]
    if not palabras:
        return []

    por_palabra = []
    for palabra in palabras:
        encontrados = await _buscar_palabra(session, palabra)
        if encontrados:
            por_palabra.append((palabra, encontrados))
        else:
            log.info("Busqueda '%s': la palabra '%s' no existe en esta planta; se ignora.",
                     termino, palabra)

    if not por_palabra:
        log.info("Busqueda '%s': 0 resultado(s).", termino)
        return []

    # El cruce, en el orden en que se escribieron las palabras.
    comunes = set(por_palabra[0][1])
    for _, encontrados in por_palabra[1:]:
        comunes &= set(encontrados)

    if comunes:
        base = por_palabra[0][1]
        salida = [base[c] for c in base if c in comunes]
    else:
        # Cruzar puede pasarse de estrecho: dos palabras que existen pero en
        # sitios distintos dejan el cruce vacio. Antes de devolver nada, se da
        # lo de la palabra MAS SELECTIVA -- la que menos cosas encontro, que es
        # la que mas informacion aporta. La vuelta no se pierde.
        palabra, base = min(por_palabra, key=lambda x: len(x[1]))
        salida = list(base.values())
        log.info("Busqueda '%s': ninguna coincidencia con todas las palabras; "
                 "se devuelve lo de '%s' (%d).", termino, palabra, len(salida))

    if len(salida) > _TOPE_RESULTADOS:
        log.info("Busqueda '%s': %d resultado(s), se recorta al tope de %d.",
                 termino, len(salida), _TOPE_RESULTADOS)
        salida = salida[:_TOPE_RESULTADOS]
    else:
        log.info("Busqueda '%s': %d resultado(s).", termino, len(salida))
    return salida


async def traer_al_contexto(session: ClientSession, acierto: dict,
                            presupuesto: int) -> tuple[list[dict], bool]:
    """Convierte un acierto de la busqueda en grupos con la forma de los demas
    bloques: {element, description, attributes}.

    Lo que se trae depende del TIPO, y la diferencia es de 1 a 74:

      - 'variable' -- un instrumento suelto: "Turbidity inlet". Se trae ese
        atributo y nada mas.
      - 'elemento' -- un activo: una bomba. Se trae CON SU SUBARBOL. Medido el
        2026-09-28 sobre PS20102: el subarbol son 16 grupos y 74 atributos, y
        sus atributos propios son solo 4 -- identidad y configuracion. Head,
        Hydraulic Efficiency y Hydraulic Power cuelgan todos de 'Indicators'.
        Traerlo sin hijos seria la apariencia de haber atendido la sospecha sin
        atenderla.

    La decision la toma el CODIGO a partir del tipo, no el modelo: aqui ya no se
    le pregunta nada. El eligio un sitio; el alcance de lo que entra lo fija
    esta funcion.

    'presupuesto' son los atributos que aun caben en el bloque -- se comparte
    entre todos los objetivos de la pasada, ver config.MAX_ATRIBUTOS_ANADIDOS.

    Devuelve (grupos, truncado). 'truncado' importa: un activo a medias sin
    avisar es peor que uno ausente, asi que quien llama lo dice en el prompt.

    Una lista vacia significa que el acierto no aporta nada consultable. No es
    un error -- el grafo tiene elementos sin atributos utiles -- pero quien
    llama debe tratarlo como "no encontrado".
    """
    nombre = acierto.get("nombre") or ""
    donde = acierto.get("donde") or ""
    if not nombre or not donde or presupuesto <= 0:
        return [], False

    if acierto.get("tipo") == "variable":
        # 'donde' es el path del ELEMENTO que lo contiene; el atributo no tiene
        # path propio (ver buscar_en_la_planta). Se consulta ese elemento y se
        # filtra por nombre: traerlo entero seria traer lo que nadie pidio.
        padre = _segmentos(donde)[-1] if _segmentos(donde) else ""
        if not padre:
            return [], False
        datos = await _call_graph_neighborhood(session, padre, donde)
        if "error" in datos:
            log.warning("No se pudo traer '%s' de '%s': %s", nombre, donde, datos["error"])
            return [], False
        attrs = [a for a in _filter_attributes(
            datos.get("relationships", {}).get("HAS_ATTRIBUTE", []))
            if a.get("name") == nombre]
        if not attrs:
            return [], False
        return [{"element": padre,
                 "description": (datos.get("element") or {}).get("description", ""),
                 "attributes": attrs}], False

    # Un elemento: su subarbol entero. 'donde' es su propio path, que es unico,
    # asi que sirve de desambiguador -- los nombres se repiten en el grafo.
    grupos = await _walk_subtree(session, nombre, donde, [nombre], skip_names=set())

    salida, contados = [], 0
    for g in grupos:
        cuantos = len(g.get("attributes") or [])
        if contados + cuantos > presupuesto:
            log.warning("Contexto anadido: '%s' no cabe entero (%d atributos, "
                        "presupuesto %d); se corta.", nombre, contados + cuantos, presupuesto)
            return salida, True
        salida.append(g)
        contados += cuantos

    log.info("Contexto anadido: '%s' aporta %d grupo(s), %d atributo(s).",
             nombre, len(salida), contados)
    return salida, False



async def _contexto_de_planta(session: ClientSession) -> list[dict]:
    """Elementos que se anaden al contexto de TODA alerta de la planta.

    El Step 2 recorre el subarbol del activo y el de su subsistema, asi que solo
    ve lo que cuelga de ahi. Una alerta del biologico no alcanza los solidos en
    suspension de la entrada -- que viven en
    WWTP\Operational - Intake\SS101 Intake Inlet Suspended Solids, otra rama --
    aunque sean justo lo que la explica.

    Que va en la lista lo decide quien conoce la planta, no el codigo: ver
    config.AF_PLANT_CONTEXT_ELEMENTS.

    Formato de cada entrada: el path del elemento, entero o desde cualquier
    punto. El ultimo segmento es el nombre y el resto acota, porque los nombres
    se repiten ("1 - Intake" esta dos veces dentro del WWTP). Con el sufijo
    "\*" entra ademas su subarbol; sin el, solo sus atributos propios -- que es
    lo normal, porque estos sensores tienen un unico atributo 'Value' y sus
    hijos son totalizadores y configuracion, puro ruido para el modelo.

    Una entrada que no resuelve NO corta el analisis: queda un WARNING y se
    sigue. Es configuracion escrita a mano contra un grafo que cambia, asi que
    lo raro no es que se equivoque, es que no se entere nadie.
    """
    entradas = config.AF_PLANT_CONTEXT_ELEMENTS
    if not entradas:
        return []

    planta = (config.AF_PLANT_ROOT + SEPARADOR_AF) if config.AF_PLANT_ROOT else None
    grupos: list[dict] = []
    atributos = 0

    for entrada in entradas:
        con_descendientes = entrada.endswith(SEPARADOR_AF + "*")
        limpia = entrada[:-2] if con_descendientes else entrada
        seg = _segmentos(limpia)
        if not seg:
            continue
        nombre = seg[-1]
        # El resto del path acota; si solo dieron el nombre, acota la planta.
        filtro = SEPARADOR_AF.join(seg[:-1]) if len(seg) > 1 else planta

        if con_descendientes:
            nuevos = await _walk_subtree(
                session, nombre, filtro, [nombre], skip_names=set(),
            )
        else:
            # Un solo elemento. No se usa _walk_subtree con depth=1 porque su
            # guarda de profundidad avisa por cada hijo que no visita, y ese
            # WARNING esta ahi para detectar jerarquias anomalas: llenarlo de
            # avisos esperados lo convertiria en ruido.
            nuevos = []
            datos = await _call_graph_neighborhood(session, nombre, filtro)
            if "error" not in datos:
                attrs = _filter_attributes(
                    datos.get("relationships", {}).get("HAS_ATTRIBUTE", []))
                if attrs:
                    nuevos = [{
                        "element": nombre,
                        "description": (datos.get("element") or {}).get("description", ""),
                        "attributes": attrs,
                    }]
        if not nuevos:
            log.warning("Contexto de planta: '%s' no aporta nada (no existe, o "
                        "no tiene atributos utiles). Revisa AF_PLANT_CONTEXT_ELEMENTS.",
                        entrada)
            continue

        for g in nuevos:
            if atributos >= config.MAX_PLANT_CONTEXT_ATTRIBUTES:
                log.warning("Contexto de planta: se alcanzo el tope de %d atributos; "
                            "el resto de la lista no entra.",
                            config.MAX_PLANT_CONTEXT_ATTRIBUTES)
                return grupos
            grupos.append(g)
            atributos += len(g.get("attributes") or [])

    log.info("Contexto de planta: %d grupo(s), %d atributo(s), de %d entrada(s).",
             len(grupos), atributos, len(entradas))
    return grupos


async def build_af_context(asset_name: str, subsystem_name: str,
                           system_name: str = "") -> dict:
    """Step 2: construye main_asset_context / nearby_elements_context desde el AF.

    Args:
        asset_name: valor "Asset" del payload de PI -- el activo directamente
                    afectado por la alerta. Se usa como raiz de main_asset_context.
        subsystem_name: valor "Subsystem" del payload -- el elemento que agrupa
                         al activo principal. Se usa como raiz de
                         nearby_elements_context; su rama correspondiente al
                         propio asset_name se omite (ya esta en main_asset_context).
        system_name: valor "System" del payload. NO es decorativo: es lo que
                     desambigua cuando el nombre del subsistema se repite.

    Returns:
        {"main_asset_context": [...], "nearby_elements_context": [...]}
        Listo para incluirse en el mensaje del Step 3 (build_analysis_context).
    """
    # Techo de planta, anclado con el separador: path_contains es una
    # SUBCADENA, y "WWTP" a secas casa tambien con "WWTP_Demo" -- un modelo
    # espejo de esta misma planta que entra por el conector de Wonderware, con
    # los mismos nombres de equipo y atributos distintos. Ver config.AF_PLANT_ROOT.
    planta = (config.AF_PLANT_ROOT + SEPARADOR_AF) if config.AF_PLANT_ROOT else None

    async with sesion_af() as session:
        # La jerarquia del payload de PI se usa ENTERA, y hasta el
        # 2026-09-22 se tiraba. Lo que identifica un elemento no es su
        # nombre sino la cadena System + Subsystem + Asset:
        #
        #   payload:   System=External Pumping  Subsystem=Pumping Station 01
        #              Asset=PS20102 A03 PS02 Pump 02
        #   path real: WWTP\Operational\External Pumping\Area C\Pumping
        #              Station 01\PS20102 A03 PS02 Pump 02
        #
        # Hay segmentos intercalados (Operational, Area C), asi que la
        # cadena no reconstruye el path -- pero si lo identifica.
        path_activo, path_subsistema = await _resolver_cadena(
            session, asset_name, subsystem_name, system_name,
        )

        # El recorrido arranca del path exacto que salio de la cadena. Si
        # no se encontro, se cae al techo de planta: quedarse sin contexto
        # es peor que la ambiguedad.
        main_asset_context = await _walk_subtree(
            session, asset_name, path_activo or planta,
            [asset_name], skip_names=set(),
        )

        # Se anade a TODA alerta de la planta, este donde este en la
        # jerarquia. Va en su propio bloque y no mezclado con los otros dos
        # porque su prioridad es distinta: puede influir o no, y el prompt
        # lo dice.
        # Ficha del equipo: no entra en el menu del Step 4, va directa
        # al Step 6. Ver _IDENTIDAD_ATTRS.
        identidad = []
        if path_activo:
            datos = await _call_graph_neighborhood(session, asset_name, path_activo)
            if "error" not in datos:
                for a in datos.get("relationships", {}).get("HAS_ATTRIBUTE", []):
                    if a.get("name") in _IDENTIDAD_ATTRS and a.get("piApiPath"):
                        identidad.append({"name": a["name"], "piApiPath": a["piApiPath"]})

        plant_context = await _contexto_de_planta(session)

        nearby_elements_context = []
        if subsystem_name and subsystem_name != asset_name:
            nearby_elements_context = await _walk_subtree(
                session, subsystem_name, path_subsistema or planta,
                [subsystem_name], skip_names={asset_name},
            )

    return {
        "main_asset_context": main_asset_context,
        "nearby_elements_context": nearby_elements_context,
        "plant_context": plant_context,
        # Aparte de los tres bloques: no se le ofrece al modelo para elegir, se
        # consulta siempre y se le entrega hecha en el Step 6.
        "asset_identity": identidad,
    }

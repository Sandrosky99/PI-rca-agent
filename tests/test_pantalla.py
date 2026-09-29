"""Pruebas de los endpoints de lectura y de la pagina de la sala de control.

Fase 1 del diseno de docs/DISENO-INTERACCION-HUMANA.md: solo lectura. Lo que se
comprueba aqui es que la pantalla pueda pintar lo que necesita, que no exponga
lo que no debe, y que el id que llega por la URL no sirva para leer ficheros de
la maquina.
"""
import io
import json
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

TMP = Path(tempfile.mkdtemp(prefix="rca_pant_"))
config.INCIDENTS_DIR = str(TMP / "inc")
config.AUDIT_FILE = str(TMP / "audit.jsonl")
config.LOG_FILE = str(TMP / "app.log")
config.WORKFLOW_ENABLED = True

import observability
observability.configure_logging()
import incidents
import webhook
from fastapi.testclient import TestClient

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


CLIENTE = TestClient(webhook.app)

DIAGNOSTICO = {
    "root_causes": [
        {"cause": "Obstruccion parcial del rodete por solidos",
         "evidence": ["El caudal cae de 296,5 a 265,9 m3/h.",
                      "La altura sube de 49,9 a 51,0 m: mas resistencia."],
         "recommended_action": "Inspeccion visual del rodete."},
        {"cause": "Desgaste del rodete",
         "evidence": ["Rendimiento a la baja sostenido durante seis semanas."],
         "recommended_action": "Medir holguras en la proxima parada."},
    ],
    "_ai_generated": {"provider": "anthropic", "model": "claude-opus-5", "notice": "Hipotesis."},
}

# Esquema anterior al 2026-09-14: un parrafo unico en "explanation". Los
# incidentes ya guardados lo conservan y no se migran, asi que tiene que seguir
# viendose.
DIAGNOSTICO_VIEJO = {
    "root_causes": [
        {"cause": "Cavitacion", "explanation": "La presion de aspiracion cae por debajo de la NPSH requerida.",
         "recommended_action": "Revisar el nivel del pozo."},
    ],
}


def sembrar(activo, estado, con_diagnostico=False, start="2026-09-14T09:00:00Z"):
    payload = {
        "KPIName": "Hydraulic Efficiency", "Asset": activo,
        "Subsystem": "Pumping Station 01", "System": "External Pumping", "Plant": "WWTP",
        "KPI": 41.3, "Limit": 48.0, "LimitThresholdType": "Low", "StartTime": start,
    }
    r = incidents.claim(payload)
    assert r is not None, f"claim devolvio None para {activo}"
    incidents.mark(r["id"], estado, diagnostico=DIAGNOSTICO if con_diagnostico else None,
                   trace={"step3_prompt": "x" * 5000, "step4_response": "y" * 5000})
    return r


print("\n=== 1. La lista trae lo que la vista de conjunto necesita ===")
a = sembrar("PS20102 A03 PS02 Pump 02", incidents.FINALIZADO, True, "2026-09-14T09:00:00Z")
b = sembrar("PS20103 A01 PS01 Pump 03", incidents.ANALIZANDO, False, "2026-09-14T09:30:00Z")

resp = CLIENTE.get("/incidentes")
check("responde 200", resp.status_code == 200, resp.status_code)
cuerpo = resp.json()
check("el total cuadra", cuerpo["total"] == 2, cuerpo["total"])
uno = cuerpo["incidentes"][0]
for campo in ("id", "asset", "kpiName", "estado", "startTime", "numCausas"):
    check(f"trae '{campo}'", campo in uno, list(uno))
check("cuenta las causas", any(i["numCausas"] == 2 for i in cuerpo["incidentes"]))

print("\n=== 2. La lista NO trae lo pesado ni lo innecesario ===")
# Una lista puede tener docenas de incidentes; el payload y el trace no pintan
# nada ahi y multiplicarian el tamano de cada refresco.
for campo in ("payload", "trace", "diagnostico"):
    check(f"no trae '{campo}'", campo not in uno, list(uno))

print("\n=== 3. Orden: lo mas reciente arriba ===")
# En un monitor de sala de control, lo que acaba de llegar tiene que estar
# arriba. El orden lo da 'recibido_en'.
ids = [i["id"] for i in cuerpo["incidentes"]]
recibidos = [i["recibidoEn"] for i in cuerpo["incidentes"]]
check("descendente por fecha de recepcion", recibidos == sorted(recibidos, reverse=True), recibidos)

print("\n=== 4. El detalle trae el diagnostico y el payload ===")
resp = CLIENTE.get(f"/incidentes/{a['id']}")
check("responde 200", resp.status_code == 200, resp.status_code)
d = resp.json()
check("trae el payload para identificar la alerta", d.get("payload", {}).get("Asset") == "PS20102 A03 PS02 Pump 02")
check("trae las dos causas", len(d["diagnostico"]["root_causes"]) == 2)
check("trae la etiqueta de contenido generado por IA", "_ai_generated" in d["diagnostico"])
check("las evidencias llegan como lista",
      isinstance(d["diagnostico"]["root_causes"][0]["evidence"], list))

print("\n=== 4b. Un diagnostico con el esquema viejo se sigue sirviendo ===")
# Los incidentes anteriores al 2026-09-14 llevan "explanation" en vez de
# "evidence". No se migran: un diagnostico es el registro de lo que el modelo
# dijo aquel dia, y reescribirlo para que encaje en el esquema de hoy seria
# falsearlo. Tiene que poder leerse igual.
viejo = sembrar("PS20104 B02 PS01 Pump 09", incidents.FINALIZADO, start="2026-09-14T07:00:00Z")
incidents.mark(viejo["id"], incidents.FINALIZADO, diagnostico=DIAGNOSTICO_VIEJO)
dv = CLIENTE.get(f"/incidentes/{viejo['id']}").json()
check("se sirve sin error", dv["estado"] == incidents.FINALIZADO)
check("conserva 'explanation' tal cual se guardo",
      "explanation" in dv["diagnostico"]["root_causes"][0])
check("y la lista lo cuenta como una causa",
      any(i["id"] == viejo["id"] and i["numCausas"] == 1 for i in CLIENTE.get("/incidentes").json()["incidentes"]))

print("\n=== 5. El detalle NO trae los PROMPTS ===")
# Medido sobre un incidente real, el trace es el 98,9 % del fichero, y son los
# prompts en crudo. No pinta nada en la pantalla y se transferiria en cada clic.
#
# Lo que se comprueba es eso -- que no viajen los prompts --, no que no exista
# la clave 'traces': de cada pasada sobreviven tres campos cortos que son de
# los que la pantalla deriva que fue de cada sospecha. Podar por tamaño, que es
# el motivo real, y no por nombre.
check("el trace suelto se queda fuera", "trace" not in d, list(d))
volcado = json.dumps(d)
for pesado in ("step3_prompt", "step4_response", "system_prompt", "step6_prompt"):
    check("no viaja %s" % pesado, pesado not in volcado)
check("y lo que queda de cada pasada es corto", len(volcado) < 20000, len(volcado))
fichero = json.loads((TMP / "inc" / f"{a['id']}.json").read_text(encoding="utf-8"))
check("pero sigue guardado en disco", "traces" in fichero, list(fichero))

print("\n=== 6. EL CASO QUE IMPORTA: el id no sirve para salirse del directorio ===")
# El id llega desde la URL. Sin validar el formato, un id con ".." leeria
# cualquier fichero de la maquina.
(TMP / "secreto.json").write_text('{"clave": "no deberia verse"}', encoding="utf-8")
for intento in ("../secreto", "..%2Fsecreto", "....//secreto", "..\\secreto",
                "/etc/passwd", "a/../../secreto"):
    r = CLIENTE.get(f"/incidentes/{intento}")
    ok = r.status_code in (403, 404) and "no deberia verse" not in r.text
    check(f"rechaza '{intento}'", ok, f"{r.status_code}: {r.text[:60]}")

# Y la misma comprobacion saltandose el enrutador, que es la que de verdad
# prueba el guardian: FastAPI normaliza algunas de esas rutas antes de llegar al
# codigo, asi que pasar por HTTP solo no demuestra nada.
#
# Verificado que el riesgo era real: sin validar, Path(INCIDENTS_DIR)/"../secreto.json"
# resuelve fuera del directorio y se lee sin problema.
for intento in ("../secreto", "..\\secreto", "a/../../secreto", "inc/../../secreto", ""):
    check(f"leer_por_id({intento!r}) devuelve None",
          incidents.leer_por_id(intento) is None)

print("\n=== 7. Un id que no existe da 404, no un error ===")
r = CLIENTE.get("/incidentes/deadbeef__20260101T000000Z")
check("404 limpio", r.status_code == 404, r.status_code)

print("\n=== 8. La pagina se sirve y es autonoma ===")
r = CLIENTE.get("/pantalla")
check("responde 200", r.status_code == 200, r.status_code)
check("es HTML", "text/html" in r.headers.get("content-type", ""), r.headers.get("content-type"))
html = r.text
check("lleva el aviso de que el contenido lo genera una IA",
      "inteligencia artificial" in html or "Generado por IA" in html)
# Sin dependencias externas: la maquina puede no tener salida a internet, y un
# monitor en blanco porque no carga un CDN es peor que uno feo.
for fuera in ("http://", "https://"):
    check(f"no carga nada de '{fuera}'", fuera not in html)

print("\n=== 8b. La pagina sabe leer los dos esquemas de causa ===")
# La funcion evidencias() de la pagina es la que sostiene la compatibilidad. Si
# alguien la simplifica quitando la rama de 'explanation', los incidentes
# antiguos se quedarian sin texto en pantalla y nadie se enteraria hasta abrir
# uno viejo.
check("la pagina contempla el campo nuevo", "c.evidence" in html)
check("y sigue contemplando el viejo", "c.explanation" in html)
# Un parrafo de ocho lineas metido en una vinieta parece una lista rota, asi que
# la pagina distingue como pintar cada forma.
check("distingue lista de parrafo", '"lista"' in html and '"parrafo"' in html)
check("tiene los tres bloques etiquetados",
      ">Problema<" in html and ">Evidencias<" in html and ">Acción recomendada<" in html)

print("\n=== 8f. Un fallo dice POR QUE fallo ===")
# Hasta el 2026-09-14 un incidente en FALLIDO no decia en ninguna parte que
# habia fallado: el log lo tenia y el fichero del caso no, asi que la pantalla
# solo podia enseñar "fallo del analisis" y encogerse de hombros.
roto = sembrar("PS20104 B02 PS01 Pump 11", incidents.RECIBIDO, start="2026-09-14T06:00:00Z")
incidents.mark(roto["id"], incidents.FALLIDO, motivo="No se pudieron obtener los datos de PI.")
dr = CLIENTE.get(f"/incidentes/{roto['id']}").json()
check("el motivo se guarda en el fichero del caso", dr.get("motivo") == "No se pudieron obtener los datos de PI.")
check("la pagina lo pinta", "d.motivo" in html)
# Va en campo propio y no dentro del trace porque el trace se poda a los 90
# dias y el motivo del fallo forma parte del caso.
incidents.podar_traces.__doc__  # (solo para dejar constancia de la relacion)
check("sobrevive a la poda del trace", "motivo" in json.loads(
      (TMP / "inc" / f"{roto['id']}.json").read_text(encoding="utf-8")))

print("\n=== 8h. Los motivos de fallo son texto fijo, sin datos del error dentro ===")
# La version anterior de esta prueba buscaba palabras prohibidas ("Step",
# "JSON"...) en los mensajes. No servia de gran cosa: los mensajes son cadenas
# literales, no cambian en ejecucion, y leerlos una vez ya dice si tienen jerga.
#
# Lo que SI puede romperse es que alguien interpole el error dentro del motivo
# -- _fallo(trace, f"Fallo: {exc}") -- y entonces el texto deja de ser fijo y
# puede acabar enseñando en el monitor de la sala de control una traza, una ruta
# de la maquina o la respuesta cruda del modelo. Eso es lo que se vigila aqui.
import re as _re
fuente = Path(RAIZ / "workflow.py").read_text(encoding="utf-8")
motivos = _re.findall(r"_fallo\(trace,(.*?)\)\n", fuente, _re.S)
check("hay un motivo por cada salida por fallo", len(motivos) == 6, len(motivos))
for m in motivos:
    etiqueta = _re.sub(r"\s+", " ", m).strip()[:46]
    check(f"literal, sin interpolar: {etiqueta}...",
          "f\"" not in m and "{" not in m and "%" not in m and "+" not in m and "str(" not in m)

print("\n=== 8i. Los paneles se desplazan por separado ===")
# Con muchos incidentes, si la pagina entera se desplaza hay que bajar tanto que
# se pierde de vista cual esta seleccionado, y el panel central queda sin
# referencia. min-height:0 es lo que hace que el overflow de los paneles llegue
# a activarse: sin el, un hijo de grid no se encoge por debajo de su contenido.
check("la pagina no se desplaza entera", "height:100vh" in html and "overflow:hidden" in html)
check("los paneles pueden encogerse", html.count("min-height:0") >= 3)
check("los filtros quedan fijos", "position:sticky" in html)
check("el detalle vuelve arriba al cambiar", "scrollTop = 0" in html)

print("\n=== 8g. 'En cola' y 'procesando' no dicen lo mismo ===")
# Eran la misma rama, y la pantalla decia "el analisis esta en marcha" de algo
# que ni siquiera habia empezado.
check("'recibido' tiene su propio texto", "espera turno" in html)
# Y NO explica el porque. El limite de simultaneos es fontaneria nuestra: al
# operario le basta con saber que esta registrada y que se va a analizar.
check("sin detalles de fontaneria interna",
      "sobrecargar" not in html and "simultáneos" not in html)
check("'analizando' conserva el suyo", "El análisis está en marcha" in html)
check("'interrumpido' tambien tiene el suyo", "se cortó a mitad" in html)

print("\n=== 8e. El filtro por estado ===")
check("hay botonera de filtros", 'id="filtros"' in html)
check("las opciones salen de los estados que devuelve el servidor",
      "Object.keys(cuentas)" in html)
# Si la opcion filtrada desaparece (termina el ultimo 'procesando', o se cambia
# de pestaña), volver a Todos en vez de dejar una lista vacia sin explicacion.
# Se hace al refrescar y no al pintar: al pintar dejaba en pantalla una lista
# traida con el filtro viejo. El 'reintento' corta cualquier posibilidad de
# bucle.
#
# La comprobacion mira el recuento de la pestaña ACTUAL, no siempre
# 'datos.recuento': eso fue un fallo real (2026-09-18) -- comparaba el filtro de
# Cerrados contra las claves de Activos, que nunca coinciden, asi que todo
# filtro en Cerrados se autoanulaba antes de pintarse. Ver test_revision.py,
# seccion 17e bis, para la reproduccion del fallo.
check("se recupera si la opcion filtrada desaparece",
      "!cuentasPestanaActual[filtro]" in html and "reintento: true" in html)
check("limpia el detalle cuando no hay seleccion", "Selecciona un incidente" in html)

print("\n=== 8c. El prompt del Step 6 pide el esquema nuevo ===")
import workflow
instr = workflow._DIAGNOSIS_FINAL_INSTRUCTION
check('pide "evidence"', '"evidence"' in instr)
check('ya no pide "explanation"', '"explanation"' not in instr, instr[:200])
check("pide que la causa sea una sola frase", "UNA sola frase" in instr)
check("pide las evidencias como array", "array de 1 a 4 cadenas" in instr)

print("\n=== 8d. _evidencias() lee los dos esquemas ===")
check("esquema nuevo", workflow._evidencias({"evidence": ["a", "b"]}) == ["a", "b"])
check("esquema viejo", workflow._evidencias({"explanation": "un parrafo"}) == ["un parrafo"])
check("evidence como cadena suelta", workflow._evidencias({"evidence": "solo una"}) == ["solo una"])
check("sin nada", workflow._evidencias({"cause": "x"}) == [])
check("descarta cadenas vacias", workflow._evidencias({"evidence": ["a", "", "  "]}) == ["a"])

print("\n=== 8j. La lista se acota: sin esto no sirve para un monitor ===")
# No se borra ningun incidente nunca: con veinte alertas al dia son 7.300 al
# ano. Una lista de 7.300 elementos en un monitor de sala de control no la lee
# nadie, asi que el endpoint tiene que poder acotarse por fecha y por cantidad.
import datetime as _dt

def _sello(horas_atras):
    return (_dt.datetime.now(_dt.timezone.utc)
            - _dt.timedelta(hours=horas_atras)).strftime("%Y-%m-%dT%H:%M:%SZ")

def _envejecer(registro, horas, movimiento=False):
    """Retrasa el incidente en disco.

    'recibido_en' es por donde filtra el periodo.

    'movimiento_workflow' es lo que mira el reloj de 12 h para cerrarlo: la
    ultima vez que el WORKFLOW lo movio, no la ultima escritura de cualquiera.
    Se retrasa solo cuando la prueba quiere un incidente CERRADO, no solo
    antiguo. Se toca tambien 'actualizado_en' para que el fichero sea coherente.
    """
    f = TMP / "inc" / f"{registro['id']}.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    d["recibido_en"] = _sello(horas)
    if movimiento:
        d["actualizado_en"] = _sello(horas)
        d["movimiento_workflow"] = _sello(horas)
    f.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

for h in (1, 5, 30, 200):
    r = sembrar(f"PS20199 Z99 PS99 Pump {h:02d}", incidents.FINALIZADO, True,
                start=f"2026-09-1{h % 9}T0{h % 9}:00:00Z")
    _envejecer(r, h)

def _pedir(qs=""):
    return CLIENTE.get("/incidentes" + qs).json()

recientes = _pedir("?horas=24&limite=100")
edades = {i["asset"] for i in recientes["incidentes"] if "PS20199" in i["asset"]}
check("con horas=24 entran los de 1 y 5 h", {"PS20199 Z99 PS99 Pump 01", "PS20199 Z99 PS99 Pump 05"} <= edades, edades)
check("y quedan fuera los de 30 y 200 h",
      not {"PS20199 Z99 PS99 Pump 30", "PS20199 Z99 PS99 Pump 200"} & edades, edades)

todo = _pedir("?horas=0&limite=500")
todos_los_nombres = {i["asset"] for i in todo["incidentes"] if "PS20199" in i["asset"]}
check("con horas=0 ('Todo') entran los cuatro, incluido el de 200 h",
      len(todos_los_nombres) == 4, todos_los_nombres)
check("y no se pone 'desde'", todo["filtro"]["desde"] is None, todo["filtro"]["desde"])

print("\n=== 8k. El limite recorta y la pantalla puede decirlo ===")
# Recortar en silencio seria mentir por omision: quien mira tiene que saber que
# hay mas de los que ve.
cap = _pedir("?horas=0&limite=2")
check("devuelve como mucho el limite", cap["mostrados"] == 2, cap["mostrados"])
check("marca que esta truncado", cap["truncado"] is True)
check("y dice cuantos habia en total", cap["coincidentes"] > 2, cap["coincidentes"])
check("sin recorte, truncado es False", _pedir("?horas=0&limite=500")["truncado"] is False)

print("\n=== 8l. Un fallido viejo sale de la vista por defecto ===")
# Un fallo tecnico interesa mientras alguien pueda hacer algo con el. Pasado un
# turno completo deja de competir por la atencion en un monitor donde lo que
# importa son las alertas vivas -- pero NO desaparece: se va a la otra pestaña.
viejo_roto = sembrar("PS20199 Z99 PS99 Pump 77", incidents.RECIBIDO, start="2026-09-02T03:00:00Z")
incidents.mark(viejo_roto["id"], incidents.FALLIDO, motivo="algo se torcio")
_envejecer(viejo_roto, incidents.HORAS_EN_ACTIVOS + 2, movimiento=True)

activos = [i["id"] for i in _pedir("?horas=0&limite=500&cerrados=false")["incidentes"]]
check("ya no esta en activos", viejo_roto["id"] not in activos)
cerrados = [i["id"] for i in _pedir("?horas=0&limite=500&cerrados=true")["incidentes"]]
check("pero si en cerrados", viejo_roto["id"] in cerrados)

reciente_roto = sembrar("PS20199 Z99 PS99 Pump 78", incidents.RECIBIDO, start="2026-09-02T04:00:00Z")
incidents.mark(reciente_roto["id"], incidents.FALLIDO, motivo="acaba de pasar")
check("un fallido reciente sigue en activos",
      reciente_roto["id"] in [i["id"] for i in _pedir("?horas=0&limite=500&cerrados=false")["incidentes"]])

print("\n=== 8o. EL FALLO: el limite se aplica DESPUES de filtrar por estado ===")
# Antes el servidor recortaba a los N mas recientes de TODOS y luego el
# navegador descartaba de esos los que no eran del estado pedido. Con "listo" y
# maximo 2 podian salir cero incidentes aunque hubiera diez finalizados.
for n in range(4):
    r = sembrar(f"PS20177 Y77 PS77 Pump {n:02d}", incidents.FINALIZADO, True,
                start=f"2026-09-14T1{n}:00:00Z")
for n in range(2):
    r = sembrar(f"PS20177 Y77 PS77 Bomb {n:02d}", incidents.RECIBIDO,
                start=f"2026-09-14T2{n}:00:00Z")

dos_listos = _pedir("?horas=0&limite=2&estado=finalizado")
check("pide 2 finalizados y devuelve 2", dos_listos["mostrados"] == 2, dos_listos["mostrados"])
check("y los 2 SON finalizados",
      all(i["estado"] == incidents.FINALIZADO for i in dos_listos["incidentes"]),
      [i["estado"] for i in dos_listos["incidentes"]])
check("cuenta los finalizados que habia, no el total",
      dos_listos["coincidentes"] == len([i for i in _pedir("?horas=0&limite=500")["incidentes"]
                                         if i["estado"] == incidents.FINALIZADO]),
      dos_listos["coincidentes"])

print("\n=== 8p. Las cuentas de las pastillas las da el servidor ===")
# Contarlas en el navegador sobre la lista recibida daria numeros falsos en
# cuanto el limite recortase algo: con maximo 2 diria "finalizado 2" habiendo
# diez. Y cada pastilla debe decir lo que saldria AL PULSARLA.
recortado = _pedir("?horas=0&limite=1")
check("el recuento no depende del limite",
      recortado["recuento"][incidents.FINALIZADO] > 1,
      recortado["recuento"])
for estado_pedido, cuenta in recortado["recuento"].items():
    real = _pedir(f"?horas=0&limite=500&estado={estado_pedido}")["mostrados"]
    check(f"la pastilla '{estado_pedido}' dice lo que saldria al pulsarla",
          cuenta == real, f"dice {cuenta}, salen {real}")
check("la pantalla usa el recuento del servidor", "datos.recuento" in html)

print("\n=== 8m bis. Pasada una semana se da la fecha, no 'hace 92 dias' ===")
# No es por coste -- son tres restas por fila sobre una lista ya recortada a 500
# como mucho --, es porque "hace 92 dias 7 h" obliga a hacer la cuenta al reves
# para saber de que dia habla. Lo relativo sirve mientras el suceso siga en la
# cabeza de quien lee; a partir de ahi, la fecha.
check("hay un umbral declarado", "DIAS_EN_RELATIVO" in html)
check("y por encima se formatea como fecha", "toLocaleDateString" in html)
# El año solo cuando no es el actual: repetirlo en todas las filas es ruido.
check("el año solo si no es el actual", "esteAno" in html)

print("\n=== 8m ter. Sin comentarios de Python dentro del JavaScript ===")
# Una linea que empiece por '#' dentro del <script> es un error de sintaxis que
# deja la pagina EN BLANCO, sin nada en el log del servidor que lo explique. Se
# colo uno el 2026-09-15 al editar; esto lo caza en el sitio.
_js = html[html.index("<script>") + 8:html.rindex("</script>")]
_malas = [l.strip()[:60] for l in _js.splitlines() if l.strip().startswith("#")]
check("ninguna linea del script empieza por '#'", not _malas, _malas)

print("\n=== 8m. La pantalla trae los controles de rango ===")
check("selector de periodo", 'id="periodo"' in html)
check("el maximo se teclea, no se elige de una lista",
      'type="number" id="limite"' in html)
# Ver "50 de 213" sin mas deja la duda de cuales son esos 50. La etiqueta lo
# dice; que ademas sea el maximo se entiende solo.
check("la casilla se etiqueta 'Más recientes'", ">Más recientes</label>" in html)
check("y el aviso de recorte tambien lo dice", "más\n         recientes de" in html or
      "más recientes de" in html.replace("\n", " ").replace("         ", " "))
check("rango concreto con fechas", "datetime-local" in html)
check("avisa cuando recorta", "aviso-truncado" in html)

print("\n=== 8n. El limite tecleado se acota en el servidor ===")
# Lo escribe una persona: un cero de mas no debe traducirse en leer miles de
# ficheros del disco para un navegador que no los va a pintar.
for pedido, esperado in ((999999, webhook._LIMITE_MAXIMO), (0, 1), (-5, 1)):
    r = CLIENTE.get(f"/incidentes?horas=0&limite={pedido}").json()
    check(f"limite={pedido} se acota a {esperado}", r["filtro"]["limite"] == esperado,
          r["filtro"]["limite"])
# Los valores iniciales los pone el servidor, para que la pantalla no lleve su
# propia copia que se desincronice del config.
check("los valores por defecto vienen del servidor", "datos.filtro.limite" in html)

print("\n=== 9. Los endpoints no dependen del interruptor de parada ===")
# La pantalla debe poder consultarse aunque el analisis automatico este
# desactivado: precisamente entonces es cuando alguien quiere mirar que hay.
config.WORKFLOW_ENABLED = False
try:
    check("la lista sigue respondiendo", CLIENTE.get("/incidentes").status_code == 200)
    check("el detalle sigue respondiendo", CLIENTE.get(f"/incidentes/{a['id']}").status_code == 200)
    check("la pantalla sigue respondiendo", CLIENTE.get("/pantalla").status_code == 200)
finally:
    config.WORKFLOW_ENABLED = True

print("\n=== 10. Un fichero corrupto no tumba la lista ===")
# Si un incidente quedo a medias, la pantalla debe seguir mostrando los demas en
# vez de quedarse en blanco entera.
sanos = CLIENTE.get("/incidentes").json()["total"]
(TMP / "inc" / "corrupto__20260101T000000Z.json").write_text("{ esto no es json", encoding="utf-8")
r = CLIENTE.get("/incidentes")
check("responde 200 igualmente", r.status_code == 200, r.status_code)
check("y sigue listando los sanos", r.json()["total"] == sanos, f"{r.json()['total']} de {sanos}")

print("\n=== 8p. La pantalla puede contar que fue de una sospecha ===")
# El dato lo compone el SERVIDOR, por lo mismo que 'causas': se deduce de los
# traces y ese calculo no puede vivir en dos sitios. La pantalla escribe la
# frase; el dato sale del endpoint.
r = sembrar("PS-BUSQ-01", incidents.FINALIZADO, con_diagnostico=True)
incidents.mark(r["id"], incidents.FINALIZADO, trace={
    "busqueda_resultado": [
        {"causa": "1:0", "que_busca": "la turbidez de entrada",
         "acierto": {"tipo": "variable", "nombre": "Turbidity inlet",
                     "donde": "WWTP" + chr(92) + "Operational" + chr(92) + "1 - Intake"},
         "motivo": "encontrado"}],
    "sospechas_pendientes": [{"causa": "1:1", "sospecha": "y la temperatura del digestor"}],
})
det = CLIENTE.get("/incidentes/" + r["id"]).json()
check("el detalle trae que fue de cada sospecha", "sospechas" in det, list(det))
check("con el desenlace y el sitio",
      det["sospechas"]["1:0"]["motivo"] == "encontrado"
      and det["sospechas"]["1:0"]["nombre"] == "Turbidity inlet", det.get("sospechas"))
check("y lo pendiente como pendiente",
      det["sospechas"]["1:1"]["motivo"] == "pendiente", det.get("sospechas"))
check("y trae lo anadido al expediente",
      [a["nombre"] for a in det.get("anadido") or []] == ["Turbidity inlet"], det.get("anadido"))
# El trace sigue sin salir: esto es un resumen, no el volcado.
check("sin arrastrar los prompts", "step3_prompt" not in json.dumps(det), list(det))

print("\n=== 8q. Y sabe escribir las cuatro frases ===")
check("tiene la funcion", "function suerteDeLaSospecha(" in html)
for motivo, trozo in (("encontrado", "se añadió al análisis"),
                      ("no_existe", "aquí no se mide eso"),
                      ("sin_presupuesto", "no se llegó a localizar"),
                      ("pendiente", "Pendiente de buscar")):
    check("dice algo para '%s'" % motivo, trozo in html, trozo)
# "Pendiente" y no "descartada", y con el motivo: si no, aparecer en un
# reanalisis posterior seria inexplicable para quien la escribio.
check("y explica por que quedo pendiente",
      "se atendieron antes otras sospechas" in html)

print("\n=== 8r. La linea del recuadro la escribe el CODIGO, y se nota ===")
# En esta pantalla conviven lo que propuso la IA y lo que hizo el sistema.
# Confundirlos es exactamente lo que este proyecto lleva evitando.
check("la frase acordada", "Añadido a este expediente por tu sospecha" in html)
check("va marcada aparte del texto del modelo", 'class="anadido"' in html)
check("con estilo propio", ".anadido{" in html)
# El recuadro tiene que salir aunque el modelo no escribiera su frase: lo que se
# anadio es un hecho, y no depende de que el modelo lo comente.
check("el recuadro no depende de que_cambia",
      "(cambio || bloqueAnadido)" in html)

print("\n=== 8s. El recuento junto al titulo de las causas ===")
# Cuantas hay y en que estado, para leerlo sin contar tarjetas.
check("existe la funcion", "function recuento(estados){" in html)
check("y el titulo la usa", "${recuento(estados)}" in html)
check("con estilo de dato, no de etiqueta",
      ".recuento{" in html and "text-transform:none" in html)

# LA REGLA: un cero no se escribe. "3 - 0 descartadas" ocupa el mismo sitio que
# el dato util y no dice nada que no se vea ya.
i = html.index("function recuento(estados){")
cuerpo = html[i:html.index("function zonaVeredicto", i)]
check("las descartadas solo salen si las hay", "if(desc) partes.push" in cuerpo, cuerpo[-400:])
check("y las confirmadas igual", "if(conf) partes.push" in cuerpo, cuerpo[-400:])
check("el total SI sale siempre", "partes = [String(estados.length)]" in cuerpo)
# Singular y plural: "1 descartada" y no "1 descartadas".
check("concuerda en numero", 'desc === 1 ? "" : "s"' in cuerpo and 'conf === 1 ? "" : "s"' in cuerpo)
# "confirmada" y no "aceptada": es la palabra del resto de la pantalla. Dos
# nombres para lo mismo en la misma vista se leen como dos cosas distintas.
check("usa la palabra del resto de la pantalla",
      "confirmada" in cuerpo and "aceptada" not in cuerpo, cuerpo[-300:])
check("y esa palabra es la del veredicto", "Confirmada por el operario" in html)

print("\n=== 8t. La marca de la fila no puede contradecir a su pastilla ===")
# En activos se filtra por ESTADO, asi que la marca de una fila abierta tiene que
# decir lo que dice su pastilla. Si no, el mismo nombre etiqueta dos conjuntos
# distintos: se vio con cuatro incidentes bajo la pastilla "interrumpido"
# marcados "Fallo del analisis", que es el nombre de OTRA pastilla.
i = html.index("function chipEstado(x){")
fn = html[i:html.index("async function pedir", i)]
# La condicion tiene que MIRAR el estado, no deducir los veredictos de que la
# etiqueta no sea 'sin_veredicto': FALLIDO e INTERRUMPIDO sacan su etiqueta del
# estado, no de que alguien haya revisado nada.
check("la marca de cierre exige estado finalizado",
      'x.estado === "finalizado"' in fn, fn[-500:])
check("y sigue exigiendo que haya etiqueta distinta de sin_veredicto",
      'x.cierre !== "sin_veredicto"' in fn)
check("un incidente cerrado sigue enseñando su cierre", "x.cerrado ||" in fn)

# La pastilla de estado de interrumpido existe y es NEUTRA: no es un diagnostico
# que fallo, es un proceso que murio debajo.
check("interrumpido tiene estilo propio y neutro",
      ".e-pausado,.e-interrumpido{background:var(--neutro-fondo)" in html)
# Y la de fallido comparte color con su etiqueta de cierre, por eso el arreglo
# no le cambia el aspecto.
check("fallido se ve igual por cualquiera de las dos vias",
      ".e-fallido{background:var(--malo-fondo); color:var(--malo)}" in html
      and ".c-fallo_del_analisis{background:var(--malo-fondo); color:var(--malo)}" in html)

# Y el nombre de la pastilla de estado de cada uno sigue siendo el suyo.
check("las pastillas no se llaman igual",
      'fallido:     "fallo del análisis"' in html and 'interrumpido:"interrumpido"' in html)

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

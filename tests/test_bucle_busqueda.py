"""Pruebas del BUCLE de la busqueda (Fase 3, §5 ter del diseño).

Es el unico paso agentico del proyecto: el modelo decide QUE buscar y CUANDO
parar. El workflow global no cambia -- sigue siendo una secuencia fija -- y lo
que se prueba aqui es exactamente la frontera de ese paso.

El modelo NO decide el alcance. Tres cosas se comprueban una y otra vez:

  - que el bucle TERMINE siempre, conteste lo que conteste. Una vuelta es una
    llamada, tambien si la desperdicia;
  - que no pueda referirse a nada que este codigo no le haya dado -- ni un
    objetivo, ni un resultado;
  - que NADA de aqui pueda tumbar el analisis. Es contexto que lo mejora, no un
    requisito, igual que la ficha del equipo.

Y la herencia entre pasadas, que es lo que evita que un reanalisis 2 repita la
busqueda del 1 o la pierda.

Sin AF, sin PI y sin modelo: dobles para todo.
"""
import asyncio
import io
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

config.INCIDENTS_DIR = str(Path(__file__).parent / "_no_usado")
import graph_client
import incidents
import llm_client
import workflow

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


SEP = chr(92)
INTAKE = SEP.join(("WWTP", "Operational", "1 - Intake"))
PRIMARIO = SEP.join(("WWTP", "Operational", "2 - Pretreatment", "Line 1", "PH213"))

TURBIDEZ = {"tipo": "variable", "nombre": "Turbidity inlet", "donde": INTAKE, "descripcion": ""}
PH213 = {"tipo": "elemento", "nombre": "PH213", "donde": PRIMARIO, "descripcion": ""}

OBJ1 = {"causa": "1:0", "sospecha": "mirad la turbidez", "que_busca": "la turbidez de entrada"}
OBJ2 = {"causa": "1:2", "sospecha": "y el pH", "que_busca": "el pH de salida del primario"}

# --- dobles -------------------------------------------------------------
guion = []
prompts = []
buscados = []

AF_FALSO = {"Turbidity": [TURBIDEZ], "PH": [PH213], "pH": [PH213]}


async def _generar(mensaje):
    prompts.append(mensaje)
    if not guion:
        raise AssertionError("el bucle pidio mas vueltas de las previstas")
    siguiente = guion.pop(0)
    if isinstance(siguiente, Exception):
        raise siguiente
    return siguiente if isinstance(siguiente, str) else json.dumps(siguiente)


async def _buscar(session, termino):
    buscados.append(termino)
    return list(AF_FALSO.get(termino, []))


workflow._generar = _generar
graph_client.buscar_en_la_planta = _buscar


def correr(objetivos, acciones, trace=None):
    guion.clear(); guion.extend(acciones)
    prompts.clear(); buscados.clear()
    return asyncio.run(workflow.buscar_lo_que_falta(objetivos, None, trace))


print("\n=== 1. Busca y elige: el camino normal ===")
r = correr([OBJ1], [{"accion": "buscar", "termino": "Turbidity"},
                    {"accion": "elegir", "objetivo": "1:0", "resultado": 1}])
check("un resultado por objetivo", len(r) == 1, r)
check("encontrado", r[0]["motivo"] == "encontrado", r)
check("y trae el sitio, no una ruta", r[0]["acierto"] == TURBIDEZ, r[0]["acierto"])
# El primer termino NO lo pone el modelo: lo pone el codigo con el 'que_busca'
# que ya escribio la puerta, antes de gastar una vuelta.
check("el codigo busca primero, con lo que dijo la puerta",
      buscados == ["la turbidez de entrada", "Turbidity"], buscados)

print("\n=== 2. Rendirse es un desenlace VALIDO ===")
# Conductivity no existe en WWTP: comprobado contra el AF real. Que la planta no
# mida algo es informacion, no un fallo.
r = correr([OBJ1], [{"accion": "buscar", "termino": "Conductivity"},
                    {"accion": "rendirse", "objetivo": "1:0"}])
check("no_existe", r[0]["motivo"] == "no_existe", r)
check("sin acierto", r[0]["acierto"] is None, r)

print("\n=== 3. El bucle TERMINA siempre ===")
# Aunque el modelo no resuelva nada. Agotar el presupuesto no es un fallo.
r = correr([OBJ1], [{"accion": "buscar", "termino": "Turbidity"}] * config.MAX_ITERACIONES_BUSQUEDA)
check("sin_presupuesto", r[0]["motivo"] == "sin_presupuesto", r)
check("gasto justo las vueltas del tope",
      len(prompts) == config.MAX_ITERACIONES_BUSQUEDA, len(prompts))

print("\n=== 4. Una accion invalida GASTA vuelta ===")
# Es lo que garantiza la terminacion: si no consumiera presupuesto, un modelo
# atascado repitiendo basura no pararia nunca.
for etiqueta, accion in (
    ("accion desconocida", {"accion": "reiniciar_el_grafo"}),
    ("buscar sin termino", {"accion": "buscar", "termino": "   "}),
    ("respuesta que no es JSON", "lo siento"),
    ("JSON que no es objeto", "[1,2,3]"),
    ("el proveedor falla", llm_client.LLMGenerationError("503")),
):
    r = correr([OBJ1], [accion] * config.MAX_ITERACIONES_BUSQUEDA)
    check(etiqueta + " -> termina igual", r[0]["motivo"] == "sin_presupuesto", r)
    check(etiqueta + " -> gasto el presupuesto",
          len(prompts) == config.MAX_ITERACIONES_BUSQUEDA, len(prompts))

print("\n=== 5. No puede referirse a nada que no le hayamos dado ===")
r = correr([OBJ1], [{"accion": "buscar", "termino": "Turbidity"},
                    {"accion": "elegir", "objetivo": "9:9", "resultado": 1},
                    {"accion": "elegir", "objetivo": "1:0", "resultado": 99},
                    {"accion": "elegir", "objetivo": "1:0", "resultado": "la primera"},
                    {"accion": "elegir", "objetivo": "1:0", "resultado": 1}])
check("un objetivo inventado no resuelve nada, y el bueno si",
      r[0]["motivo"] == "encontrado", r)
check("hizo falta llegar a la ultima vuelta", len(prompts) == 5, len(prompts))

r = correr([OBJ1], [{"accion": "elegir", "objetivo": "1:0", "resultado": 1}]
                   * config.MAX_ITERACIONES_BUSQUEDA)
check("no puede elegir de un catalogo vacio", r[0]["motivo"] == "sin_presupuesto", r)

print("\n=== 6. Dos objetivos COMPARTEN el presupuesto ===")
# No son 5 vueltas por objetivo: son 5 para el paso entero. Por eso dos
# objetivos no obligan a tocar ANALYSIS_TIMEOUT_SECONDS.
r = correr([OBJ1, OBJ2], [{"accion": "buscar", "termino": "Turbidity"},
                          {"accion": "elegir", "objetivo": "1:0", "resultado": 1},
                          {"accion": "buscar", "termino": "PH"},
                          {"accion": "elegir", "objetivo": "1:2", "resultado": 2}])
check("los dos resueltos", [x["motivo"] for x in r] == ["encontrado", "encontrado"], r)
check("en 4 vueltas de las 5", len(prompts) == 4, len(prompts))
check("cada uno con su acierto",
      [x["acierto"]["nombre"] for x in r] == ["Turbidity inlet", "PH213"], r)

r = correr([OBJ1, OBJ2], [{"accion": "buscar", "termino": "Turbidity"},
                          {"accion": "elegir", "objetivo": "1:0", "resultado": 1}] +
                         [{"accion": "buscar", "termino": "nada"}] * 3)
check("lo que no se resuelve sale sin presupuesto",
      [x["motivo"] for x in r] == ["encontrado", "sin_presupuesto"], r)

print("\n=== 7. Para en cuanto no queda nada que resolver ===")
r = correr([OBJ1], [{"accion": "buscar", "termino": "Turbidity"},
                    {"accion": "elegir", "objetivo": "1:0", "resultado": 1}])
check("no gasta las vueltas de mas", len(prompts) == 2, len(prompts))

print("\n=== 8. El catalogo acumula y no repite ===")
r = correr([OBJ1], [{"accion": "buscar", "termino": "Turbidity"},
                    {"accion": "buscar", "termino": "Turbidity"},
                    {"accion": "elegir", "objetivo": "1:0", "resultado": 1}])
check("elegir sigue valiendo tras una busqueda repetida",
      r[0]["motivo"] == "encontrado", r)
check("y el catalogo no duplica la entrada",
      prompts[-1].count("Turbidity inlet") == 1, prompts[-1].count("Turbidity inlet"))

print("\n=== 9. Lo que se le dice al modelo ===")
correr([OBJ1], [{"accion": "buscar", "termino": "Turbidity"},
                {"accion": "elegir", "objetivo": "1:0", "resultado": 1}])
p0, p1 = prompts[0], prompts[1]
check("el objetivo, con su identificador", "[1:0]" in p0 and "turbidez de entrada" in p0)
check("le dice que NO diagnostique", "no diagnostiques" in p0.lower())
# El AF de esta planta esta en ingles aunque la sospecha venga en castellano.
check("le dice que busque en ingles", "INGL" in p0.upper())
check("rendirse es una salida legitima, y se lo dice", "valida" in p0)
check("sabe cuantas vueltas le quedan", "5 respuesta" in p0 and "4 respuesta" in p1)
check("la segunda vuelta ve lo que ya probo", "LO QUE YA HAS PROBADO" in p1)
check("y el catalogo numerado", "1. [variable] Turbidity inlet" in p1, p1[-600:])

print("\n=== 10. El trace se queda con todo ===")
# Va al trace y no con el diagnostico porque webhook lo persiste TAMBIEN cuando
# el analisis falla. Sin eso, una busqueda con exito seguida de un fallo se
# perderia y el reanalisis siguiente la repetiria.
tr = {}
correr([OBJ1], [{"accion": "buscar", "termino": "Turbidity"},
                {"accion": "elegir", "objetivo": "1:0", "resultado": 1}], trace=tr)
check("guarda prompt y respuesta de cada vuelta", len(tr.get("busqueda") or []) == 2, tr.keys())
check("con el prompt entero", "turbidez" in tr["busqueda"][0]["prompt"])

print("\n=== 11. ampliar_contexto: nada de esto puede tumbar el analisis ===")


def ampliar(revision, af, atendidas=frozenset(), previos=None, trace=None):
    return asyncio.run(workflow.ampliar_contexto(revision, af, atendidas, previos, trace))


AF_CTX = {"main_asset_context": [{"element": "Bomba",
                                  "attributes": [{"name": "Flow", "uom": "m3/h"}]}],
          "nearby_elements_context": [], "plant_context": []}


def descartada(indice, sospecha):
    return {"iteracion": 1, "indice": indice, "cause": "c", "evidence": [],
            "recommended_action": "", "estado": incidents.DESCARTADA,
            "evidencia": "Comprobado.", "sospecha": sospecha}


@asynccontextmanager
async def _sesion_rota():
    raise RuntimeError("el subproceso del grafo no arranca")
    yield  # pragma: no cover


guardado = graph_client.sesion_af
graph_client.sesion_af = _sesion_rota
guion.clear()
guion.append({"decisiones": [{"sospecha": "1:0", "senala_fuera": True,
                              "que_busca": "la turbidez"}]})
grupos, truncado, hallazgos, pend = ampliar([descartada(0, "mirad la turbidez")], AF_CTX)
check("el grafo revienta y NO propaga", grupos == [] and hallazgos == [], (grupos, hallazgos))
graph_client.sesion_af = guardado

print("\n=== 12. La herencia entre pasadas ===")
# Un reanalisis 2 con un acierto de la pasada 1: no se busca nada, pero lo
# encontrado sigue en el contexto. Es lo que pediste que no se perdiera.
traidos = []


@asynccontextmanager
async def _sesion_falsa():
    yield "sesion"


async def _traer(session, acierto, presupuesto):
    traidos.append((acierto["nombre"], presupuesto))
    return [{"element": acierto["nombre"], "description": "",
             "attributes": [{"name": "Value", "piApiPath": "X"}]}], False


graph_client.sesion_af = _sesion_falsa
graph_client.traer_al_contexto = _traer

guion.clear()   # la puerta no debe llamarse: la sospecha ya esta atendida
grupos, truncado, hallazgos, pend = ampliar(
    [descartada(0, "mirad la turbidez")], AF_CTX,
    atendidas=frozenset({"1:0"}), previos=[TURBIDEZ])
check("no se busca nada", hallazgos == [], hallazgos)
check("pero lo de la pasada anterior sigue en el contexto",
      [g["element"] for g in grupos] == ["Turbidity inlet"], grupos)
check("y no se llamo al modelo", guion == [], guion)

print("\n=== 13. El presupuesto de atributos se reparte ===")
traidos.clear()
guion.clear()
grupos, truncado, hallazgos, pend = ampliar([], AF_CTX, previos=[TURBIDEZ, PH213])
check("el segundo ve el presupuesto ya gastado",
      [t[1] for t in traidos] == [config.MAX_ATRIBUTOS_ANADIDOS,
                                  config.MAX_ATRIBUTOS_ANADIDOS - 1], traidos)

print("\n=== 14. Sin nada que hacer, ni siquiera abre sesion ===")
abiertas = []


@asynccontextmanager
async def _sesion_contada():
    abiertas.append(1)
    yield "sesion"


graph_client.sesion_af = _sesion_contada
guion.clear()
grupos, truncado, hallazgos, pend = ampliar([], AF_CTX)
check("sin objetivos ni herencia, no se abre el grafo", abiertas == [], abiertas)
check("ni se devuelve nada", grupos == [] and hallazgos == [])

print("\n=== 15. Lo buscado se DERIVA de los traces ===")
# No se almacena aparte. Un segundo sitio donde guardarlo seria un segundo sitio
# que puede discrepar, y ademas los traces se persisten TAMBIEN cuando el
# analisis falla -- que es lo que evita perder una busqueda con exito seguida de
# un fallo.
REG = {"traces": [
    {"step3_prompt": "...",
     "busqueda_resultado": [
         {"causa": "1:0", "que_busca": "la turbidez", "acierto": TURBIDEZ, "motivo": "encontrado"},
         {"causa": "1:2", "que_busca": "conductividad", "acierto": None, "motivo": "no_existe"}]},
    # Pasada que FALLO: marcar_fallo tambien guarda su trace, y lo de aqui
    # cuenta igual. Sin esto, una busqueda con exito seguida de un fallo se
    # perderia y el reanalisis siguiente la repetiria.
    {"motivo_fallo": "El modelo no respondio.",
     "busqueda_resultado": [
         {"causa": "2:1", "que_busca": "el pH", "acierto": PH213, "motivo": "encontrado"}]},
]}
check("las recoge todas, en orden",
      [b["causa"] for b in incidents.busquedas(REG)] == ["1:0", "1:2", "2:1"],
      incidents.busquedas(REG))
check("incluida la de la pasada que fallo",
      "2:1" in incidents.sospechas_atendidas(REG), incidents.sospechas_atendidas(REG))
# Lo que acabo en "no existe" tambien cuenta como atendido: saber que algo no
# existe es un resultado, y repetirlo cuesta lo mismo que la primera vez.
check("lo que no se encontro tambien queda atendido",
      "1:2" in incidents.sospechas_atendidas(REG))
check("pero no aporta contexto",
      [a["nombre"] for a in incidents.aciertos_anadidos(REG)]
      == ["Turbidity inlet", "PH213"], incidents.aciertos_anadidos(REG))

REG2 = {"traces": [{"busqueda_resultado": [
    {"causa": "1:0", "que_busca": "x", "acierto": TURBIDEZ, "motivo": "encontrado"}]},
    {"busqueda_resultado": [
        {"causa": "2:0", "que_busca": "y", "acierto": TURBIDEZ, "motivo": "encontrado"}]}]}
check("el mismo sitio no se anade dos veces",
      len(incidents.aciertos_anadidos(REG2)) == 1, incidents.aciertos_anadidos(REG2))

check("un expediente sin busquedas no rompe nada",
      incidents.busquedas({}) == [] and incidents.aciertos_anadidos({"traces": [{}]}) == [])

print("\n=== 16. Que fue de cada sospecha ===")
# Cuatro desenlaces, y los cuatro se pueden enseñar. El que NO aparece es el de
# una sospecha que hablaba de algo ya disponible: no hubo busqueda porque no
# hacia falta, y no hay nada que contar.
EST = incidents.estado_de_sospechas({"traces": [
    {"busqueda_resultado": [
        {"causa": "1:0", "que_busca": "la turbidez", "acierto": TURBIDEZ, "motivo": "encontrado"},
        {"causa": "1:1", "que_busca": "conductividad", "acierto": None, "motivo": "no_existe"},
        {"causa": "1:2", "que_busca": "el pH", "acierto": None, "motivo": "sin_presupuesto"}],
     "sospechas_pendientes": [{"causa": "1:3", "sospecha": "y la temperatura"}]},
]})
check("encontrado trae el sitio",
      EST["1:0"]["nombre"] == "Turbidity inlet" and EST["1:0"]["donde"] == INTAKE, EST["1:0"])
check("no_existe se distingue de no haber buscado",
      EST["1:1"]["motivo"] == "no_existe", EST.get("1:1"))
check("sin_presupuesto tambien", EST["1:2"]["motivo"] == "sin_presupuesto", EST.get("1:2"))
check("y lo pendiente figura como pendiente",
      EST["1:3"]["motivo"] == "pendiente", EST.get("1:3"))
check("una causa sin sospecha no aparece", "1:9" not in EST, list(EST))

# Lo que estaba pendiente en la pasada 1 y se atendio en la 2 sale ya atendido:
# manda la ultima pasada. Sin esto, la pantalla seguiria diciendo "pendiente"
# de algo que ya se busco.
EST2 = incidents.estado_de_sospechas({"traces": [
    {"sospechas_pendientes": [{"causa": "1:3", "sospecha": "y la temperatura"}]},
    {"busqueda_resultado": [
        {"causa": "1:3", "que_busca": "la temperatura", "acierto": PH213, "motivo": "encontrado"}]},
]})
check("la ultima pasada manda", EST2["1:3"]["motivo"] == "encontrado", EST2)

check("un expediente sin nada de esto no rompe",
      incidents.estado_de_sospechas({}) == {}
      and incidents.estado_de_sospechas({"traces": [{}]}) == {})

print("\n=== 17. El bucle presenta los objetivos igual, y le pasa lo mismo ===")
# _BUSQUEDA_PROMPT lista cada objetivo como '[1:0] "la turbidez"'. El mismo
# adorno, el mismo riesgo: el modelo copia lo que ve. Se arreglo a la vez que la
# puerta, cuando la ejecucion real del 2026-09-29 lo destapo alli.
r = correr([OBJ1], [{"accion": "buscar", "termino": "Turbidity"},
                    {"accion": "elegir", "objetivo": "[1:0]", "resultado": 1}])
check("elegir con corchetes funciona", r[0]["motivo"] == "encontrado", r)
r = correr([OBJ1], [{"accion": "rendirse", "objetivo": " 1:0 "}])
check("rendirse con espacios tambien", r[0]["motivo"] == "no_existe", r)
# Y un objetivo inventado sigue cayendose, con corchetes o sin ellos.
r = correr([OBJ1], [{"accion": "elegir", "objetivo": "[9:9]", "resultado": 1}]
                   * config.MAX_ITERACIONES_BUSQUEDA)
check("uno inventado se cae igual", r[0]["motivo"] == "sin_presupuesto", r)
# El prompt sigue usando corchetes a proposito: separan el identificador del
# texto que escribio la persona. Lo que cambia es que ya no los exigimos de
# vuelta.
check("el prompt sigue presentandolos con corchetes", "[1:0]" in prompts[0])

print("\n=== 18. EL TERMINO ES UN FRAGMENTO CONTIGUO, Y HAY QUE DECIRLO ===")
# Segunda ejecucion real (2026-09-29): la puerta acerto, el bucle arranco, y
# gasto sus CINCO vueltas en "pH primary", "pH line", "pH primary effluent",
# "pH primary outlet" y "clarifier pH". Cero resultados en las cinco.
#
# Y el elemento existia: PH213 Primary Line 1 Outlet pH. Medido contra el AF:
#   "pH primary"  -> 0      "Outlet pH"   -> 2  (con PH213)
#   "pH"          -> 8      "Primary Line"-> 4  (con PH213)
# El termino tiene que aparecer letra a letra y SEGUIDO dentro del nombre; el
# modelo compuso las palabras en SU orden. El prompt decia "casa por trozo de
# nombre", pero su ejemplo se leia como "traduce al ingles y se breve".
check("dice que una sola palabra", "UNA SOLA PALABRA" in workflow._BUSQUEDA_PROMPT)
check("explica que es contiguo",
      "letra a letra y seguido" in workflow._BUSQUEDA_PROMPT)
check("con el ejemplo que fallo de verdad",
      "pH primary" in workflow._BUSQUEDA_PROMPT
      and "PH213 Primary Line 1 Outlet pH" in workflow._BUSQUEDA_PROMPT)
check("y le dice que elija mirando donde vive",
      'columna' in workflow._BUSQUEDA_PROMPT and '"en"' in workflow._BUSQUEDA_PROMPT)

# El aviso lo da el CODIGO, que es quien sabe el hecho: varias palabras y cero
# resultados es casi siempre esto.
correr([OBJ1], [{"accion": "buscar", "termino": "pH primary"},
                {"accion": "buscar", "termino": "Turbidity"},
                {"accion": "elegir", "objetivo": "1:0", "resultado": 1}])
check("tras un termino de varias palabras sin resultados, se avisa",
      "prueba UNA sola" in prompts[1], prompts[1][:900])
# Y no se avisa cuando no toca: una sola palabra sin resultados es informacion
# distinta -- puede que la planta no lo mida.
correr([OBJ1], [{"accion": "buscar", "termino": "Conductivity"},
                {"accion": "buscar", "termino": "Turbidity"},
                {"accion": "elegir", "objetivo": "1:0", "resultado": 1}])
check("con una sola palabra no se avisa de nada", "prueba UNA sola" not in prompts[1])

print("\n=== 19. SI LA BUSQUEDA PREVIA YA LO ENCUENTRA, UNA SOLA VUELTA ===")
# 'que_busca' existe antes de la primera llamada al modelo -- lo escribio la
# puerta --, asi que pagar una vuelta para que el modelo lo repita no tiene
# sentido. Importa por el RITMO, no por el reloj: un reanalisis con busqueda
# llegaba a 9 llamadas en menos de un minuto y eso agoto el limite del proveedor
# en la segunda ejecucion real.
AF_FALSO["la turbidez de entrada"] = [TURBIDEZ]
try:
    r = correr([OBJ1], [{"accion": "elegir", "objetivo": "1:0", "resultado": 1}])
    check("resuelto", r[0]["motivo"] == "encontrado", r)
    check("en UNA llamada al modelo", len(prompts) == 1, len(prompts))
    check("y el catalogo ya venia lleno", "Turbidity inlet" in prompts[0], prompts[0][-500:])
    check("el historial dice que ya se busco", "ya se busco" in prompts[0])
finally:
    del AF_FALSO["la turbidez de entrada"]

print("\n=== 20. Y ve como se llaman las cosas en esta planta ===")
# Buscaba a ciegas: el prompt de la puerta si le enseña las variables
# disponibles, el del bucle no le enseñaba ni un nombre. Sin ver
# "OX313 Biological Line 1 Aerobic Area Oxygen" no hay forma de deducir que aqui
# el tag va delante y la zona detras.
ctx = {"main_asset_context": [{"element": "Linea 1 > OX313 Biological Line 1 Aerobic Area Oxygen",
                               "attributes": []}],
       "nearby_elements_context": [{"element": "SS324 Biological Line 2 Suspended Solids",
                                    "attributes": []}],
       "plant_context": []}
ej = workflow._ejemplos_de_nombres(ctx)
check("salen del af_context", ej[0] == "OX313 Biological Line 1 Aerobic Area Oxygen", ej)
check("sin el camino delante", " > " not in ej[0], ej)
check("varios", len(ej) == 2, ej)

guion.clear(); guion.extend([{"accion": "rendirse", "objetivo": "1:0"}])
prompts.clear()
asyncio.run(workflow.buscar_lo_que_falta([OBJ1], None, None, ej))
check("y llegan al prompt", "OX313 Biological Line 1 Aerobic Area Oxygen" in prompts[0])
check("presentados como ejemplos de la planta",
      "ASI SE LLAMAN LAS COSAS EN ESTA PLANTA" in prompts[0])

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

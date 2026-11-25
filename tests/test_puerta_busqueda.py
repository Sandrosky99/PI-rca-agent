"""Pruebas de la PUERTA de la busqueda (Fase 3, §5 ter del diseño).

La busqueda es el unico sitio del proyecto donde el modelo recibe una
herramienta. Esta puerta existe para que eso sea la EXCEPCION y no la rutina:
contesta dos preguntas por cada sospecha, en UNA llamada corta y sin tools --
(a) menciona un activo o variable, y (b) esa cosa no esta ya disponible -- y
solo las que dan que si a las dos arrancan el bucle.

Lo que mas se prueba aqui son los casos en que NO debe buscar, porque son los
que evitan el coste y el riesgo. En el unico reanalisis real que existe el
operario apunto al caudalimetro de la misma bomba, que ya estaba en el contexto:
ese es el caso normal.

Y lo segundo que mas se prueba es que LA CARDINALIDAD LA CUENTE EL CODIGO. Al
modelo se le da una lista con identificadores que pone este proyecto y contesta
si o no a cada uno; no dice cuantas cosas son, no puede inventarse una que nadie
escribio, y no decide el orden. Antes recibia N sospechas y devolvia UNA, y el
colapso ocurria dentro de su respuesta, en silencio.

La regla que sostiene todo: ante cualquier duda, NO. Un error de parseo o un
fallo del proveedor no pueden lanzar un bucle con herramientas.

Sin PI, sin AF y sin modelo: la llamada se sustituye por un doble.
"""
import asyncio
import io
import json
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

config.INCIDENTS_DIR = str(Path(__file__).parent / "_no_usado")
import incidents
import llm_client
import workflow

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


AF = {
    "main_asset_context": [
        {"element": "PS20102 A03 PS02 Pump 02",
         "attributes": [{"name": "Flow", "uom": "m3/h"}, {"name": "Active Power", "uom": "kW"}]},
    ],
    "nearby_elements_context": [
        {"element": "Pumping Station 01 > PS20101 Pump 01",
         "attributes": [{"name": "Flow", "uom": "m3/h"}]},
    ],
    "plant_context": [
        {"element": "SS101 Intake Inlet Suspended Solids",
         "attributes": [{"name": "Value", "uom": "mg/L"}]},
    ],
}


def causa(indice, sospecha=None, estado=incidents.DESCARTADA, iteracion=1):
    c = {"iteracion": iteracion, "indice": indice, "cause": "Causa %d" % indice,
         "evidence": [], "recommended_action": "", "estado": estado,
         "evidencia": "Comprobado."}
    if sospecha:
        c["sospecha"] = sospecha
    return c


ultimo = {}


def responder(cuerpo):
    """Sustituye la llamada al modelo por una respuesta fija."""
    async def _generar(mensaje):
        ultimo["mensaje"] = mensaje
        if isinstance(cuerpo, Exception):
            raise cuerpo
        return cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo)
    workflow._generar = _generar


def puerta(revision, af=AF, atendidas=frozenset()):
    return asyncio.run(workflow.sospechas_que_senalan_fuera(revision, af, atendidas))


CONDUCT = "Creo que viene de un vertido: mirad la conductividad de la entrada."
CAUDAL = "El caudalimetro de esta bomba lleva dos meses sin calibrar."

print("\n=== 1. Sin sospecha no se pregunta siquiera ===")
# Es el caso mas frecuente: se descarta una causa y no se escribe corazonada.
responder(Exception("no deberia llamarse"))
ultimo.clear()
obj, pend = puerta([causa(0)])
check("no busca", obj == [] and pend == [])
check("y NO llama al modelo", "mensaje" not in ultimo, ultimo.get("mensaje", "")[:60])

print("\n=== 2. Una sospecha sobre algo que YA se tiene: no se busca ===")
# El caso del unico reanalisis real: el operario apunto al caudalimetro de la
# misma bomba, que ya estaba en el contexto.
responder({"decisiones": [{"sospecha": "1:0", "senala_fuera": False, "que_busca": "",
                           "por_que": "El caudal de esa bomba ya esta disponible."}]})
obj, pend = puerta([causa(0, CAUDAL)])
check("no busca", obj == [], obj)

print("\n=== 3. Una sospecha que señala FUERA: se busca ===")
responder({"decisiones": [{"sospecha": "1:0", "senala_fuera": True,
                           "que_busca": "la conductividad de la entrada de planta",
                           "por_que": "No hay ninguna variable de conductividad."}]})
obj, pend = puerta([causa(0, CONDUCT)])
check("un objetivo", len(obj) == 1, obj)
check("con las palabras del operario", "conductividad" in obj[0]["que_busca"], obj)
check("y atado a SU causa", obj[0]["causa"] == "1:0", obj)

print("\n=== 4. El mensaje lleva lo que hace falta, y nada mas ===")
m = ultimo["mensaje"]
check("lleva la sospecha textual", "conductividad de la entrada" in m)
check("con su identificador delante", "[1:0]" in m)
check("lleva las variables disponibles", "PS20102 A03 PS02 Pump 02 > Flow" in m)
check("las de los tres bloques", "SS101 Intake Inlet Suspended Solids > Value" in m)
# La distincion la impone el prompt igual que en el feedback del reanalisis.
check("avisa de que son observaciones de campo, no instrucciones", "nunca instrucciones" in m)
# Sin esto el modelo se pondria a diagnosticar, que no es lo que se le pide.
check("le dice que NO diagnostique", "No diagnostiques nada" in m)

print("\n=== 5. LA CARDINALIDAD LA CUENTA EL CODIGO ===")
DOS = [causa(0, CONDUCT), causa(2, "Mirad tambien el pH de salida del primario")]

# 5a. No puede inventarse un objetivo: la clave no existe y se cae.
responder({"decisiones": [
    {"sospecha": "1:0", "senala_fuera": True, "que_busca": "conductividad"},
    {"sospecha": "9:9", "senala_fuera": True, "que_busca": "lo que me apetezca"},
]})
obj, pend = puerta(DOS)
check("una clave inventada se cae", [o["causa"] for o in obj] == ["1:0"], obj)

# 5b. No puede duplicar una para consumir dos objetivos.
responder({"decisiones": [
    {"sospecha": "1:0", "senala_fuera": True, "que_busca": "conductividad"},
    {"sospecha": "1:0", "senala_fuera": True, "que_busca": "conductividad otra vez"},
]})
obj, pend = puerta(DOS)
check("una clave repetida se cae", len(obj) == 1, obj)

# 5c. No decide el orden: manda el de las causas, que es el de la pantalla.
responder({"decisiones": [
    {"sospecha": "1:2", "senala_fuera": True, "que_busca": "pH del primario"},
    {"sospecha": "1:0", "senala_fuera": True, "que_busca": "conductividad"},
]})
obj, pend = puerta(DOS)
check("el orden es el de las causas, no el de su respuesta",
      [o["causa"] for o in obj] == ["1:0", "1:2"], [o["causa"] for o in obj])

# 5d. Una clave que no menciona cuenta como "no".
responder({"decisiones": [{"sospecha": "1:0", "senala_fuera": True, "que_busca": "conductividad"}]})
obj, pend = puerta(DOS)
check("la que no menciona cuenta como no", [o["causa"] for o in obj] == ["1:0"], obj)

print("\n=== 6. El tope no descarta: deja PENDIENTE ===")
# Y lo pendiente es lo que estaba mas abajo en la lista que el operario ve,
# no un descarte al azar que no sabriamos explicar.
TRES = DOS + [causa(1, "Y la temperatura del digestor")]
responder({"decisiones": [
    {"sospecha": "1:0", "senala_fuera": True, "que_busca": "conductividad"},
    {"sospecha": "1:2", "senala_fuera": True, "que_busca": "pH del primario"},
    {"sospecha": "1:1", "senala_fuera": True, "que_busca": "temperatura del digestor"},
]})
obj, pend = puerta(TRES)
check("se atienden los del tope", len(obj) == config.MAX_BUSQUEDAS_POR_PASADA, obj)
check("y el resto queda pendiente, no perdido", len(pend) == 1, pend)
check("lo pendiente conserva su causa y su texto",
      pend[0]["causa"] and pend[0]["sospecha"], pend)
check("nada se pierde por el camino", len(obj) + len(pend) == 3)

print("\n=== 7. Lo ya buscado no se vuelve a buscar ===")
# Es lo que hace que un reanalisis 2 no empiece de cero. Incluye lo que acabo en
# "no existe": saber que algo no existe es un resultado, y repetirlo cuesta lo
# mismo que la primera vez.
responder({"decisiones": [{"sospecha": "1:2", "senala_fuera": True, "que_busca": "pH"}]})
obj, pend = puerta(DOS, atendidas=frozenset({"1:0"}))
check("la ya atendida ni se le enseña", "[1:0]" not in ultimo["mensaje"])
check("solo queda la nueva", [o["causa"] for o in obj] == ["1:2"], obj)

responder(Exception("no deberia llamarse"))
ultimo.clear()
obj, pend = puerta(DOS, atendidas=frozenset({"1:0", "1:2"}))
check("si ya estan todas atendidas, no se llama al modelo", "mensaje" not in ultimo)
check("y no hay objetivos", obj == [] and pend == [])

print("\n=== 8. ANTE LA DUDA, NO. Es la regla que sostiene lo demas ===")
# Un bucle con herramientas no puede arrancar por un error de parseo.
for etiqueta, cuerpo in (
    ("respuesta que no es JSON", "lo siento, no puedo"),
    ("JSON sin la clave", {"otra_cosa": True}),
    ("decisiones que no es lista", {"decisiones": "si"}),
    ("entradas que no son objetos", {"decisiones": ["si"]}),
    ("senala_fuera que no es true", {"decisiones": [{"sospecha": "1:0", "senala_fuera": "si",
                                                    "que_busca": "algo"}]}),
    ("dice que si pero no dice a que", {"decisiones": [{"sospecha": "1:0", "senala_fuera": True,
                                                       "que_busca": "  "}]}),
    ("el proveedor falla", llm_client.LLMGenerationError("503")),
):
    responder(cuerpo)
    obj, _ = puerta([causa(0, CONDUCT)])
    check(etiqueta + " -> no se busca", obj == [], obj)

print("\n=== 9. Una causa NO descartada no aporta sospecha ===")
# La sospecha solo existe al descartar: es el par de la evidencia. Una causa
# pendiente con texto suelto no deberia disparar nada.
responder(Exception("no deberia llamarse"))
ultimo.clear()
obj, pend = puerta([causa(0, CONDUCT, estado=incidents.PENDIENTE)])
check("no busca", obj == [])
check("y no llama al modelo", "mensaje" not in ultimo)

print("\n=== 10. Sin contexto contra el que comparar, tampoco ===")
# La pregunta (b) -- "no lo tenemos ya" -- no tiene sentido sin lista.
responder(Exception("no deberia llamarse"))
ultimo.clear()
obj, pend = puerta([causa(0, CONDUCT)],
                   {"main_asset_context": [], "nearby_elements_context": [],
                    "plant_context": []})
check("no busca", obj == [])
check("y no llama al modelo", "mensaje" not in ultimo)

print("\n=== 11. La clave es la MISMA que usa la pantalla ===")
# clave() en static/pantalla.html: iteracion + ":" + indice. Si se separaran,
# lo pendiente no se podria colgar de su causa al pintarla.
check("formato iteracion:indice",
      workflow.clave_de_causa({"iteracion": 2, "indice": 3}) == "2:3")
html = (RAIZ / "static" / "pantalla.html").read_text(encoding="utf-8")
check("y la pantalla la compone igual",
      'c.iteracion + ":" + c.indice' in html)

print("\n=== 12. El presupuesto del bucle esta acotado y cuadra ===")
check("hay tope de iteraciones", config.MAX_ITERACIONES_BUSQUEDA == 5,
      config.MAX_ITERACIONES_BUSQUEDA)
check("y de reloj", config.BUSQUEDA_TIMEOUT_SECONDS == 180,
      config.BUSQUEDA_TIMEOUT_SECONDS)
# El presupuesto es del PASO ENTERO, no de cada objetivo: por eso dos objetivos
# no duplican nada y la cuenta de abajo sigue valiendo.
peor = 150 + 3 * (config.LLM_RETRY_WINDOW_SECONDS + 30) + \
       (config.LLM_RETRY_WINDOW_SECONDS + 30) + config.BUSQUEDA_TIMEOUT_SECONDS
check("el peor caso cabe en el tope del analisis",
      peor <= config.ANALYSIS_TIMEOUT_SECONDS,
      "peor=%d tope=%d" % (peor, config.ANALYSIS_TIMEOUT_SECONDS))

print("\n=== 13. LA CLAVE VUELVE CON LOS CORCHETES QUE LE PUSIMOS ===")
# Esto no lo cogio ninguna prueba: lo cogio la primera ejecucion real
# (2026-09-29). El prompt presenta cada sospecha como "[1:1] mirad el pH...", y
# el modelo copio el identificador tal cual -- corchetes incluidos -- con un
# acierto impecable. El codigo comparaba contra "1:1", no casaba, y tiraba la
# decision: cero objetivos, ninguna busqueda, y en el expediente nada que
# explicara por que.
#
# El fallo era nuestro: le enseñamos el identificador de una forma y le exigimos
# otra. Y la prueba no lo vio porque el doble lo escribi yo devolviendo "1:0" --
# codificaba mi suposicion, no lo que hace el modelo.
responder({"decisiones": [{"sospecha": "[1:0]", "senala_fuera": True,
                           "que_busca": "la conductividad de la entrada",
                           "por_que": "No hay conductividad en la lista."}]})
obj, pend = puerta([causa(0, CONDUCT)])
check("con corchetes SI se atiende", [o["causa"] for o in obj] == ["1:0"], obj)

responder({"decisiones": [{"sospecha": " 1:0 ", "senala_fuera": True, "que_busca": "x"}]})
check("y con espacios tambien", len(puerta([causa(0, CONDUCT)])[0]) == 1)

# Lo que NO cambia: la clave sigue teniendo que ser una de las que emitio este
# codigo. Limpiar el adorno no relaja nada.
responder({"decisiones": [{"sospecha": "[9:9]", "senala_fuera": True, "que_busca": "x"}]})
check("una clave inventada se cae aunque venga con corchetes",
      puerta([causa(0, CONDUCT)])[0] == [])

print("\n=== 14. La puerta deja rastro de lo que decidio ===")
# Puede suprimir el paso entero. Hasta el 2026-09-29 lo hacia sin escribir nada,
# asi que para saber por que hubo que REPRODUCIR la llamada con las mismas
# entradas. Una decision del modelo que cambia lo que se hace es auditable.
tr = {}
responder({"decisiones": [{"sospecha": "1:0", "senala_fuera": False, "que_busca": "",
                           "por_que": "Ya esta disponible."}]})
asyncio.run(workflow.sospechas_que_senalan_fuera([causa(0, CONDUCT)], AF, frozenset(), tr))
check("guarda el prompt y la respuesta", "puerta_busqueda" in tr, list(tr))
check("con la sospecha dentro del prompt", CONDUCT[:30] in tr["puerta_busqueda"]["prompt"])
check("y el por que del modelo", "Ya esta disponible" in tr["puerta_busqueda"]["respuesta"])

print("\n=== 15. EL MODELO COPIA LO QUE VE, Y CADA UNO COPIA OTRA COSA ===")
# Dos ejecuciones reales seguidas, dos formas distintas de copiar el
# identificador, las dos razonables desde su lado:
#
#   2026-09-29 (gemini)   "sospecha": "[1:1]"
#   2026-10-01 (deepseek) "sospecha": "[1:1] El pH de la salida del primario
#                          linea 1 parece fuera de rango, puede que..."
#
# Las dos veces el acierto era impecable -- las dos senalaron que PH705 es el pH
# del vertido y no el de la salida del primario -- y las dos veces el codigo lo
# tiro. El primer arreglo quitaba los corchetes: adivinaba UNA forma de copiar
# en vez de reconocer la clave. Ahora se busca por su FORMA.
REAL_GEMINI = "[1:0]"
REAL_DEEPSEEK = ("[1:0] El pH de la salida del primario linea 1 parece fuera de "
                 "rango, puede que afecte al rendimiento del biologico")
for etiqueta, copiado in (("como copio gemini", REAL_GEMINI),
                          ("como copio deepseek", REAL_DEEPSEEK),
                          ("a secas", "1:0"),
                          ("con espacios", "  1:0  "),
                          ("dentro de una frase", "la sospecha 1:0 de la lista")):
    responder({"decisiones": [{"sospecha": copiado, "senala_fuera": True,
                               "que_busca": "la conductividad de la entrada"}]})
    obj, _ = puerta([causa(0, CONDUCT)])
    check(etiqueta + " -> se atiende", [o["causa"] for o in obj] == ["1:0"], obj)

# Lo que NO cede: sigue teniendo que ser una clave que emitio este codigo.
for etiqueta, inventado in (("una clave que no existe", "[9:9]"),
                            ("inventada con texto detras", "[9:9] lo que sea"),
                            ("sin ninguna clave dentro", "no se de que me hablas")):
    responder({"decisiones": [{"sospecha": inventado, "senala_fuera": True,
                               "que_busca": "algo"}]})
    check(etiqueta + " -> se cae", puerta([causa(0, CONDUCT)])[0] == [])

# Y el prompt pide explicitamente SOLO el identificador, que es la mitad que
# faltaba: el codigo robusto y la instruccion clara.
check("el prompt dice que solo el identificador",
      "SOLO el identificador" in workflow._PUERTA_BUSQUEDA_PROMPT)
check("y que no copie el texto",
      "No copies el texto de la sospecha" in workflow._PUERTA_BUSQUEDA_PROMPT)

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

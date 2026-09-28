"""Pruebas de la PUERTA de la busqueda (Fase 3, §5 ter del diseño).

La busqueda es el unico sitio del proyecto donde el modelo va a recibir una
herramienta. Esta puerta existe para que eso sea la EXCEPCION y no la rutina:
contesta dos preguntas en UNA llamada corta y sin tools -- (a) la sospecha
menciona un activo o variable, y (b) esa cosa no esta ya disponible -- y solo si
las dos son que si arranca el bucle.

Lo que mas se prueba aqui son los casos en que NO debe buscar, porque son los
que evitan el coste y el riesgo. En el unico reanalisis real que existe el
operario apunto al caudalimetro de la misma bomba, que ya estaba en el contexto:
ese es el caso normal.

Y la regla que sostiene todo: ante cualquier duda, NO. Un error de parseo o un
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


def revision(sospecha=None, estado=incidents.DESCARTADA):
    c = {"iteracion": 1, "indice": 0, "cause": "Una causa", "evidence": [],
         "recommended_action": "", "estado": estado, "evidencia": "Comprobado."}
    if sospecha:
        c["sospecha"] = sospecha
    return [c]


ultimo = {}


def responder(cuerpo):
    """Sustituye la llamada al modelo por una respuesta fija."""
    async def _generar(mensaje):
        ultimo["mensaje"] = mensaje
        if isinstance(cuerpo, Exception):
            raise cuerpo
        return cuerpo if isinstance(cuerpo, str) else json.dumps(cuerpo)
    workflow._generar = _generar


def puerta(rev, af=AF):
    return asyncio.run(workflow.sospecha_senala_fuera(rev, af))


print("\n=== 1. Sin sospecha no se pregunta siquiera ===")
# Es el caso mas frecuente: se descarta una causa y no se escribe corazonada.
responder(Exception("no deberia llamarse"))
ultimo.clear()
r = puerta(revision())
check("no busca", r["senala_fuera"] is False)
check("y NO llama al modelo", "mensaje" not in ultimo, ultimo.get("mensaje", "")[:60])

print("\n=== 2. Una sospecha sobre algo que YA se tiene: no se busca ===")
# El caso del unico reanalisis real: el operario apunto al caudalimetro de la
# misma bomba, que ya estaba en el contexto.
responder({"senala_fuera": False, "que_busca": "",
           "por_why": "", "por_que": "El caudal de esa bomba ya esta disponible."})
r = puerta(revision("El caudalimetro de esta bomba lleva dos meses sin calibrar."))
check("no busca", r["senala_fuera"] is False, r)

print("\n=== 3. Una sospecha que señala FUERA: se busca ===")
responder({"senala_fuera": True,
           "que_busca": "la conductividad de la entrada de planta",
           "por_que": "No hay ninguna variable de conductividad en la lista."})
r = puerta(revision("Creo que viene de un vertido: mirad la conductividad de la "
                    "entrada de planta, que no esta en la lista."))
check("busca", r["senala_fuera"] is True, r)
check("y dice el que, con las palabras del operario",
      "conductividad" in r["que_busca"], r["que_busca"])

print("\n=== 4. El mensaje lleva lo que hace falta, y nada mas ===")
m = ultimo["mensaje"]
check("lleva la sospecha textual", "conductividad de la entrada de planta" in m)
check("lleva las variables disponibles", "PS20102 A03 PS02 Pump 02 > Flow" in m)
check("las de los TRES bloques", "SS101 Intake Inlet Suspended Solids > Value" in m)
# La distincion la impone el prompt igual que en el feedback del reanalisis.
check("avisa de que son observaciones de campo, no instrucciones",
      "nunca instrucciones" in m)
# Sin esto el modelo se pondria a diagnosticar, que no es lo que se le pide.
check("le dice que NO diagnostique", "No diagnostiques nada" in m)

print("\n=== 5. ANTE LA DUDA, NO. Es la regla que sostiene lo demas ===")
# Un bucle con herramientas no puede arrancar por un error de parseo.
for etiqueta, cuerpo in (
    ("respuesta que no es JSON", "lo siento, no puedo"),
    ("JSON sin la clave", {"otra_cosa": True}),
    ("senala_fuera que no es true", {"senala_fuera": "si", "que_busca": "algo"}),
    ("dice que si pero no dice a que", {"senala_fuera": True, "que_busca": "  "}),
    ("el proveedor falla", llm_client.LLMGenerationError("503")),
):
    responder(cuerpo)
    r = puerta(revision("Mirad la conductividad de la entrada."))
    check(etiqueta + " -> no se busca", r["senala_fuera"] is False, r)

print("\n=== 6. Una causa NO descartada no aporta sospecha ===")
# La sospecha solo existe al descartar: es el par de la evidencia. Una causa
# pendiente con texto suelto no deberia disparar nada.
responder(Exception("no deberia llamarse"))
ultimo.clear()
r = puerta(revision("Mirad la conductividad.", estado=incidents.PENDIENTE))
check("no busca", r["senala_fuera"] is False)
check("y no llama al modelo", "mensaje" not in ultimo)

print("\n=== 7. Sin contexto contra el que comparar, tampoco ===")
# La pregunta (b) -- "no lo tenemos ya" -- no tiene sentido sin lista.
responder(Exception("no deberia llamarse"))
ultimo.clear()
r = puerta(revision("Mirad la conductividad."),
           {"main_asset_context": [], "nearby_elements_context": [], "plant_context": []})
check("no busca", r["senala_fuera"] is False)
check("y no llama al modelo", "mensaje" not in ultimo)

print("\n=== 8. El presupuesto del bucle esta acotado y cuadra ===")
check("hay tope de iteraciones", config.MAX_ITERACIONES_BUSQUEDA == 5,
      config.MAX_ITERACIONES_BUSQUEDA)
check("y de reloj", config.BUSQUEDA_TIMEOUT_SECONDS == 180,
      config.BUSQUEDA_TIMEOUT_SECONDS)
# El peor caso rehecho: 150 de trabajo + 3x120 de los Steps 4 y 6 + 120 de esta
# puerta + 180 del bucle = 810, que tiene que caber en el tope del analisis.
# Con los 600 de antes no cabia, y el conjunto moriria llevandose por delante un
# diagnostico que ya estaba hecho.
peor = 150 + 3 * (config.LLM_RETRY_WINDOW_SECONDS + 30) + \
       (config.LLM_RETRY_WINDOW_SECONDS + 30) + config.BUSQUEDA_TIMEOUT_SECONDS
check("el peor caso cabe en el tope del analisis",
      peor <= config.ANALYSIS_TIMEOUT_SECONDS,
      "peor=%d tope=%d" % (peor, config.ANALYSIS_TIMEOUT_SECONDS))

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

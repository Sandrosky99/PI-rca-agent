"""Pruebas del limite de analisis simultaneos.

La deduplicacion protege de repetir el MISMO incidente; nada protegia de que
cinco activos distintos disparasen a la vez. Hasta el 2026-09-11 el problema
quedaba enmascarado porque la llamada al modelo bloqueaba el bucle de eventos y
los analisis se serializaban solos. Al arreglar aquello (ver test_bucle.py,
seccion 9) la concurrencia paso a ser real y hubo que ponerle un techo.

Lo que se mide aqui es el SOLAPAMIENTO de verdad -- cuantos analisis llegan a
estar dentro a la vez -- y no que el semaforo exista.
"""
import asyncio
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

TMP = Path(tempfile.mkdtemp(prefix="rca_conc_"))
config.INCIDENTS_DIR = str(TMP / "inc")
config.AUDIT_FILE = str(TMP / "audit.jsonl")
config.LOG_FILE = str(TMP / "app.log")
config.WORKFLOW_ENABLED = True

import observability
observability.configure_logging()
import incidents
import webhook
import workflow

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


def payload(activo):
    return {
        "KPIName": "Hydraulic Efficiency", "Asset": activo,
        "Subsystem": "Pumping Station 01", "System": "External Pumping", "Plant": "WWTP",
        "KPI": 41.3, "Limit": 48.0, "LimitThresholdType": "Low",
        "StartTime": "2026-09-11T10:00:00Z",
    }


class Vigia:
    """Doble del analisis que anota cuantos coinciden dentro a la vez."""

    def __init__(self, duracion=0.05):
        self.duracion = duracion
        self.dentro = 0
        self.pico = 0
        self.completados = 0

    async def __call__(self, payload, trace=None):
        self.dentro += 1
        self.pico = max(self.pico, self.dentro)
        try:
            await asyncio.sleep(self.duracion)
            return {"root_causes": [{"cause": "x", "explanation": "y", "recommended_action": "z"}]}
        finally:
            self.dentro -= 1
            self.completados += 1


def limpiar():
    """Vacia el registro entre secciones.

    Hace falta porque cada seccion reutiliza los mismos activos y StartTime, y
    la deduplicacion exacta -- correctamente -- rechazaria el segundo intento.
    """
    for f in (TMP / "inc").glob("*.json"):
        f.unlink()


def con_limite(n):
    """Recrea el semaforo como si el proceso hubiera arrancado con ese limite."""
    config.MAX_CONCURRENT_ANALYSES = n
    webhook._ANALISIS_EN_CURSO = asyncio.Semaphore(n if n > 0 else webhook._SIN_LIMITE)


async def lanzar(cuantos, vigia):
    """Lanza N analisis a la vez, como haria el webhook con N alertas simultaneas."""
    workflow.run_rca_analysis = vigia
    registros = []
    for i in range(cuantos):
        r = incidents.claim(payload(f"PS20102 A03 PS02 Pump {i:02d}"))
        assert r is not None, f"claim devolvio None para la bomba {i}"
        registros.append(r)
    await asyncio.gather(*(webhook._analizar_incidente(r["payload"], r) for r in registros))
    return registros


print("\n=== 1. Con limite 3, nunca hay mas de 3 dentro ===")
con_limite(3)
v = Vigia()
registros = asyncio.run(lanzar(10, v))
check("el pico de simultaneos no pasa de 3", v.pico <= 3, f"pico={v.pico}")
check("pero llega a 3 (no se serializa de una en una)", v.pico == 3, f"pico={v.pico}")
check("se ejecutan los 10, no se pierde ninguno", v.completados == 10, f"{v.completados}")

print("\n=== 2. Ninguna alerta se queda sin analizar ===")
finales = [incidents._leer(TMP / "inc" / f"{r['id']}.json") for r in registros]
estados = {f["estado"] for f in finales if f}
check("todos acaban en 'finalizado'", estados == {incidents.FINALIZADO}, f"{estados}")
check("todos tienen diagnostico", all(f and f.get("diagnostico") for f in finales))

print("\n=== 3. Con limite 1 se serializa del todo ===")
limpiar()
con_limite(1)
v = Vigia()
asyncio.run(lanzar(5, v))
check("nunca hay dos a la vez", v.pico == 1, f"pico={v.pico}")
check("se ejecutan los 5", v.completados == 5, f"{v.completados}")

print("\n=== 4. Con limite 0 no hay techo ===")
# 0 desactiva el limite, misma convencion que INCIDENT_COOLDOWN_MINUTES.
limpiar()
con_limite(0)
v = Vigia()
asyncio.run(lanzar(8, v))
check("los 8 entran a la vez", v.pico == 8, f"pico={v.pico}")

print("\n=== 5. EL CASO QUE MOTIVA TODO ESTO: sin limite no habria techo ===")
# Sin el semaforo, diez alertas de diez activos distintos levantarian diez
# analisis a la vez -- diez tandas de llamadas al modelo y diez baterias de
# consultas contra PI Web API, que es el historiador de la planta.
limpiar()
con_limite(3)
v = Vigia()
asyncio.run(lanzar(10, v))
sin_techo = 10
check(f"el limite recorta el pico de {sin_techo} a 3", v.pico == 3, f"pico={v.pico}")

print("\n=== 6. Un analisis que revienta libera su turno ===")
# Si una excepcion dejase el semaforo tomado, el proceso se quedaria sin cupos
# hasta reiniciarlo. 'async with' lo suelta pase lo que pase, pero conviene
# comprobarlo: es un fallo que solo aparece en produccion y tras varias horas.
limpiar()
con_limite(2)

async def _revienta(payload, trace=None):
    raise RuntimeError("fallo dentro del analisis")

workflow.run_rca_analysis = _revienta
r = incidents.claim(payload("PS20102 A03 PS02 Pump 99"))
asyncio.run(webhook._analizar_incidente(r["payload"], r))
check("el semaforo queda libre tras el error",
      webhook._ANALISIS_EN_CURSO._value == 2, f"cupos={webhook._ANALISIS_EN_CURSO._value}")
final = incidents._leer(TMP / "inc" / f"{r['id']}.json")
check("y el incidente queda 'fallido', no colgado en 'analizando'",
      final and final["estado"] == incidents.FALLIDO, final and final["estado"])

print("\n=== 7. El incidente espera turno en 'recibido', no en 'analizando' ===")
# Esto es lo que hace honesto el estado que va a leer la pantalla de la sala de
# control: 'analizando' debe significar que corre de verdad, no que hace cola.
limpiar()
con_limite(1)
estados_vistos = []

async def _mirar(payload, trace=None):
    # Mientras este corre, el otro esta esperando turno. Se mira su fichero.
    otro = TMP / "inc" / f"{en_cola['id']}.json"
    reg = incidents._leer(otro)
    estados_vistos.append(reg["estado"] if reg else None)
    await asyncio.sleep(0.05)
    return {"root_causes": [{"cause": "x", "explanation": "y", "recommended_action": "z"}]}

workflow.run_rca_analysis = _mirar
primero = incidents.claim(payload("PS20102 A03 PS02 Pump A1"))
en_cola = incidents.claim(payload("PS20102 A03 PS02 Pump A2"))


async def _dos():
    await asyncio.gather(
        webhook._analizar_incidente(primero["payload"], primero),
        webhook._analizar_incidente(en_cola["payload"], en_cola),
    )

asyncio.run(_dos())
# Solo cuenta la PRIMERA observacion: la que hace el analisis que si tiene turno
# mientras el otro espera. La segunda la hace el propio 'en_cola' leyendo su
# fichero cuando ya le ha tocado, y ahi 'analizando' es lo correcto.
check("el que espera figura como 'recibido', no como 'analizando'",
      estados_vistos[0] == incidents.RECIBIDO, f"estados observados: {estados_vistos}")
check("y cuando le toca el turno, si pasa a 'analizando'",
      estados_vistos[1] == incidents.ANALIZANDO, f"estados observados: {estados_vistos}")

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

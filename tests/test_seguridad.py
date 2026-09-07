"""Pruebas de las decisiones de seguridad del webhook (ver webhook.py).

Comprueban lo que SÍ se ha implementado y dejan constancia ejecutable de lo que
se decidió no implementar, para que un cambio accidental salte aquí.
"""
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

TMP = Path(tempfile.mkdtemp(prefix="rca_sec_"))
config.INCIDENTS_DIR = str(TMP / "inc")
config.AUDIT_FILE = str(TMP / "audit.jsonl")
config.LOG_FILE = str(TMP / "app.log")

import observability
observability.configure_logging()
import webhook
import workflow
from fastapi.testclient import TestClient


# Doble del workflow: sin esto, cada POST lanzaria un analisis REAL contra el
# LLM y los MCP servers -- minutos de espera y coste por cada peticion de la
# prueba. Aqui solo interesa el comportamiento del endpoint.
async def _sin_analisis(payload, trace=None):
    return {"root_causes": [{"cause": "x", "explanation": "y", "recommended_action": "z"}]}


workflow.run_rca_analysis = _sin_analisis

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


PAYLOAD = {
    "KPIName": "Hydraulic Efficiency", "Asset": "PS20102 A03 PS02 Pump 02",
    "Subsystem": "Pumping Station 01", "System": "External Pumping", "Plant": "WWTP",
    "KPI": 41.3, "Limit": 48.0, "LimitThresholdType": "Low",
    "StartTime": "2026-09-06T10:49:00Z",
}

print("\n=== 1. /notifications/history apagado por defecto ===")
check("el valor por defecto es false", config.NOTIFICATION_HISTORY_ENABLED is False,
      f"{config.NOTIFICATION_HISTORY_ENABLED}")
with TestClient(webhook.app) as c:
    r = c.get("/notifications/history")
check("responde 404, no 403", r.status_code == 404, f"{r.status_code}")
check("no filtra datos en el cuerpo", "PS20102" not in r.text, r.text[:120])

print("\n=== 2. Encendido, sirve el historial ===")
config.NOTIFICATION_HISTORY_ENABLED = True
webhook._notification_history.clear()
with TestClient(webhook.app) as c:
    c.post("/notification", json=dict(PAYLOAD))
    r = c.get("/notifications/history")
check("responde 200", r.status_code == 200, f"{r.status_code}")
check("devuelve la notificacion", "PS20102" in r.text)
config.NOTIFICATION_HISTORY_ENABLED = False

print("\n=== 3. El endpoint de salud no filtra datos de planta ===")
# /health esta abierto (ver DECISIONES DE SEGURIDAD), asi que lo que importa no
# es cuantos campos devuelve sino QUE devuelve: estado y numeros, nunca nombres
# de activo, rutas de PI ni valores de proceso.
webhook._notification_history.clear()
with TestClient(webhook.app) as c:
    c.post("/notification", json=dict(PAYLOAD))   # deja datos reales en memoria
    r = c.get("/health")
cuerpo = r.json()
check("responde 200", r.status_code == 200)
check("no expone el nombre del activo", "PS20102" not in r.text, r.text[:140])
check("no expone la jerarquia de planta",
      not any(x in r.text for x in ("Pumping Station", "External Pumping", "WWTP")), r.text[:140])
check("no expone valores de proceso", "41.3" not in r.text and "48.0" not in r.text)
check("el recuento de incidentes son solo numeros",
      all(isinstance(v, int) for v in cuerpo.get("incidentes", {}).values()),
      cuerpo.get("incidentes"))

print("\n=== 4. WEBHOOK_SECRET: si se configura, se exige ===")
# No se usa en este despliegue (PI no puede enviar cabeceras propias), pero el
# mecanismo debe seguir funcionando por si el emisor cambia.
config.WEBHOOK_SECRET = "secreto-de-prueba"
with TestClient(webhook.app) as c:
    sin = c.post("/notification", json=dict(PAYLOAD))
    malo = c.post("/notification", json=dict(PAYLOAD), headers={"X-PI-Secret": "incorrecto"})
    bueno = c.post("/notification", json={**PAYLOAD, "Asset": "Bomba Z"},
                   headers={"X-PI-Secret": "secreto-de-prueba"})
check("sin cabecera -> 401", sin.status_code == 401, f"{sin.status_code}")
check("cabecera incorrecta -> 401", malo.status_code == 401, f"{malo.status_code}")
check("cabecera correcta -> 202", bueno.status_code == 202, f"{bueno.status_code}")
config.WEBHOOK_SECRET = ""

print("\n=== 5. Los errores no exponen rutas internas ni trazas ===")
with TestClient(webhook.app, raise_server_exceptions=False) as c:
    r = c.get("/notifications/history")
cuerpo = r.text
check("sin rutas de Windows", "C:\\" not in cuerpo, cuerpo[:120])
check("sin traza de pila", "Traceback" not in cuerpo)

print("\n=== 6. El log no vuelca el payload entero ===")
# El truncado a 200 del logging estructurado limita lo que queda en el log,
# aunque el payload completo si se guarde en el fichero del incidente.
log = Path(config.LOG_FILE).read_text(encoding="utf-8", errors="replace")
import json
largos = [e for e in (json.loads(l) for l in log.splitlines() if l.startswith("{"))
          if len(e.get("msg", "")) > 201]
check("ningun mensaje supera el limite del spec", not largos, f"{len(largos)} mensajes largos")

import shutil
shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

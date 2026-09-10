"""Pruebas de la puerta de entrada: solo pasan notificaciones analizables.

Lo que se comprueba aqui no es cosmetico. Antes de esta puerta, un cuerpo sin
campos utilizables llegaba hasta el Step 4 y gastaba una llamada real al modelo
antes de que la validacion del Step 5 lo cortara.
"""
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

TMP = Path(tempfile.mkdtemp(prefix="rca_val_"))
config.INCIDENTS_DIR = str(TMP / "inc")
config.AUDIT_FILE = str(TMP / "audit.jsonl")
config.LOG_FILE = str(TMP / "app.log")
# El interruptor de parada se fija a mano a proposito. Si se hereda del .env de
# la maquina, esta suite pasa en CI (que no tiene .env, asi que sale el valor por
# defecto) y falla en el servidor justo cuando alguien acaba de dar a la parada
# en caliente -- que es precisamente cuando uno mira las pruebas. Lo que se
# prueba aqui es la puerta de entrada, no el interruptor: ese tiene su propia
# comprobacion en test_seguridad.py.
config.WORKFLOW_ENABLED = True

import observability
observability.configure_logging()
import webhook
import workflow
from fastapi.testclient import TestClient

# Contador de llamadas al modelo: la comprobacion que de verdad importa es que
# una notificacion rechazada NO llegue a costar dinero.
llamadas_llm = []


async def _analisis_espia(payload, trace=None):
    llamadas_llm.append(payload)
    return {"root_causes": [{"cause": "x", "explanation": "y", "recommended_action": "z"}]}


workflow.run_rca_analysis = _analisis_espia

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


VALIDO = {
    "KPIName": "Hydraulic Efficiency", "Asset": "PS20102 A03 PS02 Pump 02",
    "Subsystem": "Pumping Station 01", "System": "External Pumping", "Plant": "WWTP",
    "KPI": 41.3, "Limit": 48.0, "LimitThresholdType": "Low",
    "StartTime": "2026-09-06T10:49:00Z",
}


def enviar(cuerpo, **kw):
    with TestClient(webhook.app) as c:
        return c.post("/notification", json=cuerpo, **kw)


print("\n=== 1. La funcion de validacion, aislada ===")
check("payload valido no da problemas", workflow.validate_notification(VALIDO) == [],
      workflow.validate_notification(VALIDO))
check("sin Asset -> problema", any("Asset" in p for p in workflow.validate_notification(
    {k: v for k, v in VALIDO.items() if k != "Asset"})))
check("sin KPIName -> problema", any("KPIName" in p for p in workflow.validate_notification(
    {k: v for k, v in VALIDO.items() if k != "KPIName"})))
check("Asset vacio -> problema", workflow.validate_notification({**VALIDO, "Asset": "   "}) != [])
check("Asset numerico -> problema", workflow.validate_notification({**VALIDO, "Asset": 42}) != [])
check("cuerpo que no es objeto -> problema", workflow.validate_notification(["a", "b"]) != [])
check("marca de cuerpo no-JSON -> problema",
      workflow.validate_notification({"_raw": "<xml/>", "_format": "no-json"}) != [])
check("se listan los DOS campos si faltan ambos",
      len(workflow.validate_notification({"Plant": "WWTP"})) == 2,
      workflow.validate_notification({"Plant": "WWTP"}))

print("\n=== 2. Notificacion valida: pasa y se analiza ===")
llamadas_llm.clear()
r = enviar(dict(VALIDO))
check("responde 202 accepted", r.status_code == 202 and r.json()["status"] == "accepted", r.text[:90])
check("se llama al modelo", len(llamadas_llm) == 1, f"{len(llamadas_llm)}")
check("se crea el incidente", len(list((TMP / "inc").glob("*.json"))) == 1)

print("\n=== 3. EL CASO QUE MOTIVA LA PUERTA: cuerpo que no es JSON ===")
llamadas_llm.clear()
for f in (TMP / "inc").glob("*.json"):
    f.unlink()
with TestClient(webhook.app) as c:
    r = c.post("/notification", content=b"<xml>esto no es el payload de PI</xml>",
               headers={"Content-Type": "application/json"})
check("responde 202 (no 4xx: PI no debe reintentar)", r.status_code == 202, f"{r.status_code}")
check("estado 'rejected'", r.json()["status"] == "rejected", r.json().get("status"))
check("NO se llama al modelo", llamadas_llm == [], f"{len(llamadas_llm)} llamadas")
check("NO se crea incidente", list((TMP / "inc").glob("*.json")) == [],
      [p.name for p in (TMP / "inc").glob("*.json")])

print("\n=== 4. Cuerpos incompletos ===")
for nombre, cuerpo in (
    ("sin Asset", {k: v for k, v in VALIDO.items() if k != "Asset"}),
    ("sin KPIName", {k: v for k, v in VALIDO.items() if k != "KPIName"}),
    ("objeto vacio", {}),
    ("Asset en blanco", {**VALIDO, "Asset": "  "}),
):
    llamadas_llm.clear()
    r = enviar(cuerpo)
    ok = r.status_code == 202 and r.json()["status"] == "rejected" and not llamadas_llm
    check(f"{nombre}: rechazado sin coste", ok, f"{r.status_code} {r.json().get('status')} llm={len(llamadas_llm)}")

print("\n=== 5. Lo que SI se tolera (no debe rechazarse) ===")
# Casos reales vistos en produccion: PI mando Limit como fecha y KPI como
# estado digital. _valid_field los descarta dentro del analisis, pero la
# notificacion es analizable y no debe morir en la puerta.
for nombre, cuerpo in (
    ("KPI como 'No Result' (visto el 2026-08-20)", {**VALIDO, "KPI": "No Result"}),
    ("Limit como fecha (visto el 2026-07-02)", {**VALIDO, "Limit": "1970-01-01T00:00:00Z"}),
    ("sin StartTime", {k: v for k, v in VALIDO.items() if k != "StartTime"}),
    ("sin jerarquia", {k: v for k, v in VALIDO.items() if k not in ("Subsystem", "System", "Plant")}),
):
    check(f"{nombre}: se acepta", workflow.validate_notification(cuerpo) == [],
          workflow.validate_notification(cuerpo))

print("\n=== 6. El rechazo queda auditado y en el log ===")
audit = [json.loads(l) for l in Path(config.AUDIT_FILE).read_text(encoding="utf-8").splitlines() if l.strip()]
bloqueos = [e for e in audit if e["op"] == "notification.accept" and e["status"] == "BLOCKED"]
check("hay entradas BLOCKED en el audit trail", len(bloqueos) >= 5, f"{len(bloqueos)}")
check("con el motivo", all(e.get("detail") for e in bloqueos))
log = Path(config.LOG_FILE).read_text(encoding="utf-8", errors="replace")
check("y un WARNING en el log", "Notificacion RECHAZADA" in log)

import shutil
shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

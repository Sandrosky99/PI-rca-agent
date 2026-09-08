"""Pruebas del registro de incidentes: deduplicacion y persistencia."""
import io
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

# Directorio aislado: no tocar el del proyecto
TMP = Path(tempfile.mkdtemp(prefix="rca_inc_"))
config.INCIDENTS_DIR = str(TMP)

import incidents

logging.basicConfig(level=logging.WARNING, format="      [%(levelname)s] %(message)s")

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


def payload(kpi_valor=58.4, start="2026-08-19T22:10:00Z", asset="PS20102 A03 PS02 Pump 02",
            kpi="Hydraulic Efficiency", **campos):
    p = {
        "KPIName": kpi, "Asset": asset, "Subsystem": "Pumping Station 01",
        "System": "External Pumping", "Plant": "WWTP", "KPI": kpi_valor,
        "Limit": 70.0, "LimitThresholdType": "Low", "StartTime": start,
    }
    p.update(campos)
    return p


def limpiar():
    for f in TMP.glob("*"):
        f.unlink()


print("\n=== 1. Primera notificacion: se reserva ===")
limpiar()
r = incidents.claim(payload())
check("devuelve registro", r is not None)
check("estado inicial 'recibido'", r and r["estado"] == incidents.RECIBIDO, f"{r and r['estado']}")
check("crea un fichero", len(list(TMP.glob("*.json"))) == 1)
check("guarda los campos legibles", r["asset"] == "PS20102 A03 PS02 Pump 02" and r["kpi_name"] == "Hydraulic Efficiency")

print("\n=== 2. Duplicado exacto: mismo sujeto y mismo StartTime ===")
check("se descarta", incidents.claim(payload()) is None)
check("no crea otro fichero", len(list(TMP.glob("*.json"))) == 1)

print("\n=== 3. El VALOR del KPI cambia (el caso que rompia comparar el payload) ===")
check("mismo incidente aunque KPI valga otra cosa", incidents.claim(payload(kpi_valor=57.9)) is None)
check("mismo incidente aunque KPI sea 'No Result'", incidents.claim(payload(kpi_valor="No Result")) is None)
check("sigue habiendo un solo fichero", len(list(TMP.glob("*.json"))) == 1)

print("\n=== 4. Enfriamiento: otro StartTime, mismo activo y KPI ===")
check("se descarta dentro de la ventana",
      incidents.claim(payload(start="2026-08-19T22:25:00Z")) is None)
check("sigue habiendo un solo fichero", len(list(TMP.glob("*.json"))) == 1)

print("\n=== 5. Otro activo / otro KPI: NO se deduplica ===")
r2 = incidents.claim(payload(asset="PS20101 A03 PS01 Pump 01"))
check("otro activo pasa", r2 is not None)
r3 = incidents.claim(payload(kpi="Motor Temperature"))
check("otro KPI pasa", r3 is not None)
check("tres ficheros", len(list(TMP.glob("*.json"))) == 3, f"{len(list(TMP.glob('*.json')))}")

print("\n=== 6. Enfriamiento a 0: solo dedup exacto ===")
limpiar()
orig = config.INCIDENT_COOLDOWN_MINUTES
config.INCIDENT_COOLDOWN_MINUTES = 0
incidents.claim(payload())
check("duplicado exacto se sigue descartando", incidents.claim(payload()) is None)
check("otro StartTime SI pasa", incidents.claim(payload(start="2026-08-19T23:00:00Z")) is not None)
config.INCIDENT_COOLDOWN_MINUTES = orig

print("\n=== 7. Enfriamiento caducado ===")
limpiar()
r = incidents.claim(payload())
# Envejecer el registro mas alla de la ventana
ruta = TMP / f"{r['id']}.json"
reg = json.loads(ruta.read_text(encoding="utf-8"))
reg["recibido_en"] = "2026-08-19T00:00:00Z"
ruta.write_text(json.dumps(reg), encoding="utf-8")
check("pasada la ventana, se acepta de nuevo",
      incidents.claim(payload(start="2026-08-20T10:00:00Z")) is not None)

print("\n=== 8. Ciclo de estados y diagnostico ===")
limpiar()
r = incidents.claim(payload())
incidents.mark(r, incidents.ANALIZANDO)
guardado = json.loads((TMP / f"{r['id']}.json").read_text(encoding="utf-8"))
check("persiste 'analizando'", guardado["estado"] == incidents.ANALIZANDO)

diag = {"root_causes": [{"cause": "Desgaste", "explanation": "e", "recommended_action": "a"}]}
incidents.mark(r, incidents.FINALIZADO, diagnostico=diag)
guardado = json.loads((TMP / f"{r['id']}.json").read_text(encoding="utf-8"))
check("persiste 'finalizado'", guardado["estado"] == incidents.FINALIZADO)
check("guarda el diagnostico en el fichero", guardado["diagnostico"] == diag)
check("conserva el payload original", guardado["payload"]["KPI"] == 58.4)

print("\n=== 9. Barrido de interrumpidos al arrancar ===")
limpiar()
a = incidents.claim(payload(start="2026-08-19T01:00:00Z"))
b = incidents.claim(payload(asset="Bomba B", start="2026-08-19T02:00:00Z"))
c = incidents.claim(payload(asset="Bomba C", start="2026-08-19T03:00:00Z"))
incidents.mark(a, incidents.ANALIZANDO)
incidents.mark(c, incidents.FINALIZADO, diagnostico=diag)
# b se queda en 'recibido'
n = incidents.sweep_interrupted()
check("marca 2 (uno analizando, uno recibido)", n == 2, f"{n}")
est = {p.stem: json.loads(p.read_text(encoding='utf-8'))["estado"] for p in TMP.glob("*.json")}
check("el que estaba analizando -> interrumpido", est[a["id"]] == incidents.INTERRUMPIDO)
check("el que estaba recibido -> interrumpido", est[b["id"]] == incidents.INTERRUMPIDO)
check("el finalizado NO se toca", est[c["id"]] == incidents.FINALIZADO)
check("un segundo barrido no marca nada", incidents.sweep_interrupted() == 0)

print("\n=== 10. Payload sin StartTime ===")
limpiar()
p = payload()
del p["StartTime"]
r1 = incidents.claim(dict(p))
check("se registra igualmente", r1 is not None)
config.INCIDENT_COOLDOWN_MINUTES = 0
r2 = incidents.claim(dict(p))
check("sin StartTime no hay dedup exacto (solo enfriamiento protege)", r2 is not None)
config.INCIDENT_COOLDOWN_MINUTES = orig

print("\n=== 11. EL CASO REAL: la re-notificacion a los 2 minutos ===")
limpiar()
r = incidents.claim(payload(start="2026-08-19T22:10:00Z", kpi_valor=58.4))
check("la primera se acepta", r is not None)
# Misma alarma, 2 min despues: StartTime distinto y valor derivado
check("a los 2 min -> descartada",
      incidents.claim(payload(start="2026-08-19T22:12:00Z", kpi_valor=58.1)) is None)
check("a los 4 min -> descartada",
      incidents.claim(payload(start="2026-08-19T22:14:00Z", kpi_valor="No Result")) is None)
check("a los 6 min -> descartada",
      incidents.claim(payload(start="2026-08-19T22:16:00Z", kpi_valor=57.6)) is None)
check("un solo incidente registrado", len(list(TMP.glob("*.json"))) == 1,
      f"{len(list(TMP.glob('*.json')))}")

print("\n=== 12. Jerarquia: forma parte de la identidad ===")
limpiar()
incidents.claim(payload())
check("otro Subsystem -> incidente distinto",
      incidents.claim(payload(Subsystem="Pumping Station 02")) is not None)
check("otro System -> incidente distinto",
      incidents.claim(payload(System="Internal Pumping")) is not None)
check("otra Plant -> incidente distinto",
      incidents.claim(payload(Plant="WWTP-2")) is not None)
check("otro LimitThresholdType -> incidente distinto",
      incidents.claim(payload(LimitThresholdType="High")) is not None)
check("cinco incidentes", len(list(TMP.glob("*.json"))) == 5, f"{len(list(TMP.glob('*.json')))}")

print("\n=== 13. Normalizacion: un campo que baila no rompe la dedup ===")
limpiar()
incidents.claim(payload())
check("espacio de mas en Asset -> sigue siendo el mismo",
      incidents.claim(payload(asset="PS20102  A03 PS02 Pump 02")) is None)
check("mayusculas distintas en KPIName -> sigue siendo el mismo",
      incidents.claim(payload(kpi="HYDRAULIC EFFICIENCY")) is None)
check("espacios al borde de Plant -> sigue siendo el mismo",
      incidents.claim(payload(Plant="  WWTP  ")) is None)
check("un solo incidente", len(list(TMP.glob("*.json"))) == 1, f"{len(list(TMP.glob('*.json')))}")

print("\n=== 14. Un incidente INTERRUMPIDO se puede re-lanzar ===")
limpiar()
r = incidents.claim(payload())
incidents.mark(r, incidents.ANALIZANDO)
incidents.sweep_interrupted()          # muere el proceso y se rearranca
ruta = TMP / (r["id"] + ".json")
check("queda interrumpido", json.loads(ruta.read_text(encoding="utf-8"))["estado"] == incidents.INTERRUMPIDO)

r2 = incidents.claim(payload())        # reenviar la MISMA notificacion
check("reenviar la misma notificacion ahora SI funciona", r2 is not None, f"{r2}")
check("no crea un fichero nuevo", len(list(TMP.glob("*.json"))) == 1)
check("vuelve al estado inicial", r2 and r2["estado"] == incidents.RECIBIDO)
check("cuenta el intento", r2 and r2.get("intentos") == 2, r2 and r2.get("intentos"))

print("\n=== 15. Un incidente FALLIDO sigue bloqueado ===")
limpiar()
r = incidents.claim(payload())
incidents.mark(r, incidents.FALLIDO)
check("reenviar no lo relanza", incidents.claim(payload()) is None)

print("\n=== 16. El enfriamiento no bloquea a un interrumpido ===")
limpiar()
r = incidents.claim(payload())
incidents.mark(r, incidents.ANALIZANDO)
incidents.sweep_interrupted()
# Otro StartTime dentro de la ventana: antes lo paraba el enfriamiento
r2 = incidents.claim(payload(start="2026-08-19T22:12:00Z"))
check("con otro StartTime tambien se acepta", r2 is not None, f"{r2}")

print("\n=== 17. Recuento por estado (para /health) ===")
limpiar()
a = incidents.claim(payload(asset="Bomba A"))
b = incidents.claim(payload(asset="Bomba B"))
c = incidents.claim(payload(asset="Bomba C"))
incidents.mark(a, incidents.FINALIZADO, diagnostico={"root_causes": []})
incidents.mark(b, incidents.ANALIZANDO)
incidents.sweep_interrupted()
rec = incidents.contar_por_estado()
check("cuenta el finalizado", rec.get(incidents.FINALIZADO) == 1, rec)
check("cuenta los interrumpidos", rec.get(incidents.INTERRUMPIDO) == 2, rec)
check("solo numeros, sin datos de planta",
      all(isinstance(v, int) for v in rec.values()) and "Bomba A" not in str(rec), rec)

print("\n=== 19. Poda del trace por antiguedad ===")
limpiar()
orig_ret = config.INCIDENT_TRACE_RETENTION_DAYS
config.INCIDENT_TRACE_RETENTION_DAYS = 90

# Incidente ANTIGUO, con trace
viejo = incidents.claim(payload(asset="Bomba Vieja"))
incidents.mark(viejo, incidents.FINALIZADO, diagnostico={"root_causes": [{"cause": "x"}]},
               trace={"step3_prompt": "P" * 60000, "step6_prompts": ["Q" * 40000]})
rv = TMP / (viejo["id"] + ".json")
reg = json.loads(rv.read_text(encoding="utf-8"))
reg["actualizado_en"] = "2026-01-01T00:00:00Z"      # mas de 90 dias
rv.write_text(json.dumps(reg), encoding="utf-8")
tam_antes = rv.stat().st_size

# Incidente RECIENTE, tambien con trace
nuevo_i = incidents.claim(payload(asset="Bomba Nueva"))
incidents.mark(nuevo_i, incidents.FINALIZADO, diagnostico={"root_causes": []},
               trace={"step3_prompt": "R" * 50000})
rn = TMP / (nuevo_i["id"] + ".json")

podados, liberados = incidents.podar_traces()
check("poda solo el antiguo", podados == 1, f"podados={podados}")
check("reporta los bytes liberados", liberados > 90000, f"{liberados}")

v = json.loads(rv.read_text(encoding="utf-8"))
check("el trace desaparece", "trace" not in v, list(v))
check("el diagnostico SE CONSERVA", v["diagnostico"]["root_causes"][0]["cause"] == "x")
check("el payload SE CONSERVA", v["payload"]["Asset"] == "Bomba Vieja")
check("el estado SE CONSERVA", v["estado"] == incidents.FINALIZADO)
check("queda constancia de la poda", "trace_podado" in v, list(v))
check("con las claves que habia",
      sorted(v["trace_podado"]["claves"]) == ["step3_prompt", "step6_prompts"],
      v["trace_podado"].get("claves"))
check("y con el peso que ocupaba", v["trace_podado"]["bytes"] > 90000)
check("el fichero encoge mucho", rv.stat().st_size < tam_antes / 10,
      f"{tam_antes} -> {rv.stat().st_size}")

n = json.loads(rn.read_text(encoding="utf-8"))
check("el reciente NO se toca", "trace" in n and "trace_podado" not in n, list(n))

check("una segunda pasada no hace nada", incidents.podar_traces() == (0, 0))

print("\n=== 20. Retencion a 0 desactiva la poda ===")
config.INCIDENT_TRACE_RETENTION_DAYS = 0
limpiar()
x = incidents.claim(payload())
incidents.mark(x, incidents.FINALIZADO, trace={"step3_prompt": "Z" * 10000})
rx = TMP / (x["id"] + ".json")
reg = json.loads(rx.read_text(encoding="utf-8"))
reg["actualizado_en"] = "2020-01-01T00:00:00Z"
rx.write_text(json.dumps(reg), encoding="utf-8")
check("no poda nada", incidents.podar_traces() == (0, 0))
check("el trace sigue ahi", "trace" in json.loads(rx.read_text(encoding="utf-8")))
config.INCIDENT_TRACE_RETENTION_DAYS = orig_ret

print("\n=== 21. Escritura atomica: no quedan .tmp ===")
limpiar()
r = incidents.claim(payload())
incidents.mark(r, incidents.FINALIZADO, diagnostico=diag)
check("sin ficheros .tmp residuales", list(TMP.glob("*.tmp")) == [], f"{list(TMP.glob('*.tmp'))}")

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

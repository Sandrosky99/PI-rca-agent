"""Pruebas de la ventana variable (4.7): catalogo de opciones,
_parse_history_request, DEMO_MODE y las correcciones de redaccion del prompt.
"""
import io
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import workflow
import config
import pi_client

logging.basicConfig(level=logging.INFO, format="      [%(levelname)s] %(message)s")

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


DETECTADA = "2026-08-31T22:10:00Z"


def req(needed, option=None, **extra):
    r = {"needed": needed, "reason": "porque la tendencia empieza antes"}
    if option is not None:
        r["window_option"] = option
    r.update(extra)
    return {"root_causes": [], "history_request": r}


def pedir(diagnosis, current_hours=24):
    return workflow._parse_history_request(diagnosis, current_hours, DETECTADA)


T = config.PI_TARGET_POINTS_PER_VARIABLE
MENU = pi_client.window_menu(config.PI_WINDOW_LADDER_HOURS, T)

print("\n=== 1. Catalogo ventana/resolucion ===")
check("6 opciones", len(MENU) == 6, f"{len(MENU)}")
check("empieza en 2 h / 1 min",
      MENU[0]["hours"] == 2 and MENU[0]["interval"] == (1, "minutes"), f"{MENU[0]}")
check("PI_LOOKBACK_HOURS esta en el catalogo",
      any(o["hours"] == config.PI_LOOKBACK_HOURS for o in MENU))
check("ninguna supera el objetivo de puntos", all(o["points"] <= T for o in MENU),
      f"{[o['points'] for o in MENU]}")
check("puntos aprox. constantes (resolucion proporcional)",
      max(o["points"] for o in MENU) - min(o["points"] for o in MENU) <= 40,
      f"{[o['points'] for o in MENU]}")
check("ids legibles", [o["id"] for o in MENU] == ["2h", "8h", "24h", "2d", "7d", "14d"], f"{[o['id'] for o in MENU]}")
check("a mas ventana, menos resolucion",
      all(MENU[i]["hours"] < MENU[i + 1]["hours"] for i in range(len(MENU) - 1)))

print("\n=== 2. Opciones ofrecidas segun la ventana actual ===")
for actual, esperado in ((24, ["2h", "8h", "2d", "7d", "14d"]), (336, ["2h", "8h", "24h", "2d", "7d"])):
    ids = [o["id"] for o in workflow._available_window_options(actual)]
    check(f"desde {actual} h ofrece {esperado}", ids == esperado, f"{ids}")

print("\n=== 3. _parse_history_request con el catalogo ===")
check("needed=false -> no amplia", pedir(req(False), 24) is None)
check("sin history_request -> no amplia", pedir({"root_causes": []}, 24) is None)

r = pedir(req(True, "7d"), 24)
check("opcion valida se concede tal cual", r == (168, (2, "hours"), "porque la tendencia empieza antes"), f"{r}")

r = pedir(req(True, "  7D  "), 24)
check("tolera espacios y mayusculas en el id", r is not None and r[0] == 168, f"{r}")

r = pedir(req(True, "30d"), 24)
check("id inventado -> escalon minimo", r is not None and r[0] == 48, f"{r}")

r = pedir(req(True, "24h"), 24)
check("pedir la ventana actual -> ampliacion minima", r is not None and r[0] == 48, f"{r}")

r = pedir(req(True), 24)
check("sin window_option -> escalon minimo", r is not None and r[0] == 48, f"{r}")

r = pedir(req(True, None, hours=168), 24)
check("esquema viejo (hours=168) -> se ajusta a 7d", r is not None and r[0] == 168, f"{r}")

r = pedir(req(True, None, hours=99999), 24)
check("esquema viejo desmedido -> mayor opcion", r is not None and r[0] == 336, f"{r}")

check("en el maximo, pedir el maximo -> ampliacion imposible",
      pedir(req(True, "14d"), 336) is None)

for basura in ({"history_request": "no soy un objeto"}, {"history_request": None}, "ni un dict"):
    check(f"tolera basura: {str(basura)[:32]}", pedir(basura, 24) is None)

print("\n=== 4. Correccion 1: unidades en castellano ===")
check("15 minutes -> 15 minutos", workflow._describe_interval(15, "minutes") == "15 minutos")
check("1 hours -> 1 hora (singular)", workflow._describe_interval(1, "hours") == "1 hora",
      workflow._describe_interval(1, "hours"))
check("2 hours -> 2 horas", workflow._describe_interval(2, "hours") == "2 horas")
check("1 days -> 1 dia (singular)", workflow._describe_interval(1, "days") == "1 día",
      workflow._describe_interval(1, "days"))
check("ventana 24 h", workflow._describe_window(24) == "24 horas")
check("ventana 168 h -> 7 dias", workflow._describe_window(168) == "7 días")

# --- Prompt completo para las comprobaciones de redaccion -------------------
ctx = {"summary": "Alerta de prueba."}
hist = {"data": {}, "lookback_hours": 24, "interval": (15, "minutes"),
        "start_date": "2026-08-30T22:10:00Z", "end_date": "2026-08-31T22:10:00Z"}
orig_demo = config.DEMO_MODE
config.DEMO_MODE = False
p = workflow.build_diagnosis_context(ctx, [], hist, [])
config.DEMO_MODE = True
p_demo = workflow.build_diagnosis_context(ctx, [], hist, [])
config.DEMO_MODE = orig_demo

check("el prompt ya no dice 'minutes'", "minutes" not in p, "sigue apareciendo")
check("el prompt dice 'cada 15 minutos'", "cada 15 minutos" in p)

print("\n=== 5. Correccion 2: se elimina 'en la resolucion configurada' ===")
check("frase eliminada", "en la resolución configurada" not in p)

print("\n=== 6. Correccion 3: history_request se menciona una sola vez en prosa ===")
s3 = p[p.index("3. Datos históricos"):p.index("[", p.index("3. Datos históricos"))]
check("la seccion 3 ya no invita a pedir ampliacion", "history_request" not in s3, s3[-160:])
check("la seccion 1 si lo sigue haciendo",
      "history_request" in p[p.index("1. Objetivo"):p.index("2. Resumen")])

print("\n=== 7. El catalogo aparece junto al esquema de respuesta ===")
check("se sustituye el marcador", "{window_menu}" not in p)
check("lista las 5 opciones ofrecidas", all(f'"{i}"' in p for i in ("2h", "8h", "2d", "7d", "14d")))
check("no ofrece la ventana actual", '- "24h"' not in p)
check("explica el intercambio span/detalle", "la resolución es proporcional a la ventana" in p)
check("prohibe inventarse una ventana", "No propongas una ventana distinta" in p)

print("\n=== 8. Desde la ventana maxima ===")
hist_max = dict(hist, lookback_hours=336, interval=(3, "hours"))
p_max = workflow.build_diagnosis_context(ctx, [], hist_max, [])
check("desde el maximo sigue ofreciendo estrechar", '- "24h"' in p_max)
check("marca las opciones de mas detalle", "más detalle, menos histórico" in p_max)

print("\n=== 9. DEMO_MODE ===")
check("off: no menciona la demo", "generador de pruebas" not in p)
check("on: avisa al modelo", "generador de pruebas" in p_demo)

print("\n=== 10. Estrechar la ventana: cobertura de la tendencia ===")
# Alerta detectada a las 22:10Z. Ventana actual 24 h -> arranca a las 22:10Z del dia anterior.

r = pedir(req(True, "8h", trend_start="2026-08-31T17:00:00Z"), 24)
check("estrecha a 8 h si la tendencia arranca 5,2 h antes",
      r == (8, (5, "minutes"), "porque la tendencia empieza antes"), f"{r}")

r = pedir(req(True, "2h", trend_start="2026-08-31T17:00:00Z"), 24)
check("2 h NO cubre 5,2 h -> sube a la mas corta que si (8 h)",
      r is not None and r[0] == 8, f"{r}")

r = pedir(req(True, "2h", trend_start="2026-08-31T21:30:00Z"), 24)
check("2 h si cubre una tendencia de 40 min", r is not None and r[0] == 2, f"{r}")

r = pedir(req(True, "2h", trend_start="2026-08-30T23:00:00Z"), 24)
check("tendencia de 23 h: ninguna ventana mas corta la cubre -> no estrecha",
      r is None, f"{r}")

r = pedir(req(True, "8h"), 24)
check("estrechar SIN trend_start -> se deniega", r is None, f"{r}")

r = pedir(req(True, "8h", trend_start="ayer por la tarde"), 24)
check("trend_start no interpretable -> se deniega", r is None, f"{r}")

r = pedir(req(True, "8h", trend_start="2026-09-05T00:00:00Z"), 24)
check("trend_start posterior a la deteccion -> se deniega", r is None, f"{r}")

r = pedir(req(True, "7d"), 24)
check("ampliar NO exige trend_start", r is not None and r[0] == 168, f"{r}")

r = pedir(req(True, "24h", trend_start="2026-08-31T20:00:00Z"), 336)
check("desde 14 dias se puede estrechar a 24 h", r is not None and r[0] == 24, f"{r}")

print("\n=== 11. El prompt explica la regla de cobertura ===")
check("pide trend_start al estrechar", "Obligatorio si eliges una ventana más corta" in p)
check("explica que se corrige sola", "se te concederá" in p)
check("marca la direccion de cada opcion",
      "más detalle, menos histórico" in p and "más histórico, menos detalle" in p)

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

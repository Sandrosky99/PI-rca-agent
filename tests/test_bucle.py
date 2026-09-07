"""Prueba de integracion del bucle Steps 5-6 con ampliacion de ventana.

Sustituye graph_client, llm_client y pi_client por dobles para ejercitar
run_rca_analysis() entera sin tocar PI ni el LLM.
"""
import asyncio
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, r"C:\MCPServer\rca-agent")
import workflow
import config
import graph_client
import llm_client
import pi_client

logging.basicConfig(level=logging.WARNING, format="      [%(levelname)s] %(message)s")

FLOW = r"\\DATAINFRA\WWTP\Pump 02|Flow"

AF = {
    "main_asset_context": [{
        "element": "Pump 02", "description": "",
        "attributes": [{"name": "Flow", "uom": "m3/h", "description": "", "piApiPath": FLOW}],
    }],
    "nearby_elements_context": [],
}

PAYLOAD = {
    "KPIName": "Hydraulic Efficiency", "Asset": "Pump 02", "Subsystem": "PS01",
    "System": "External Pumping", "Plant": "WWTP", "KPI": 60.0, "Limit": 70.0,
    "LimitThresholdType": "Low", "StartTime": "2026-08-19T22:10:00Z",
}

STEP4 = json.dumps({"variables": [{"element": "Pump 02", "piApiPath": FLOW}], "missing_variables": []})


def diagnostico(needed, option="", reason=""):
    return json.dumps({
        "root_causes": [
            {"cause": "Desgaste", "explanation": "cae la eficiencia", "recommended_action": "inspeccionar"},
            {"cause": "Obstruccion", "explanation": "baja el caudal", "recommended_action": "revisar linea"},
        ],
        "history_request": {"needed": needed, "window_option": option, "reason": reason},
    })


llamadas_pi = []
fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


async def fake_af(asset, subsystem):
    return AF


async def fake_pi(variables, detected_at_utc, lookback_hours=None, interval=None):
    llamadas_pi.append((lookback_hours, interval))
    return {
        "bucket_id": "rca_test", "start_date": "X", "end_date": "Y",
        "lookback_hours": lookback_hours, "interval": interval,
        "piApiPaths": [FLOW], "excluded_piApiPaths": [],
        "data": {FLOW: {"Content": {"Items": [
            {"Timestamp": "2026-08-19T20:00:00Z", "Value": 61.0, "UnitsAbbreviation": "%"}]}}},
    }


graph_client.build_af_context = fake_af
pi_client.fetch_historical_data = fake_pi


def correr(respuestas_step6):
    """Ejecuta run_rca_analysis con una secuencia dada de respuestas del Step 6."""
    llamadas_pi.clear()
    guion = [STEP4] + list(respuestas_step6)
    estado = {"i": 0}

    def fake_generate(system, user):
        r = guion[min(estado["i"], len(guion) - 1)]
        estado["i"] += 1
        return r

    llm_client.generate = fake_generate
    asyncio.run(workflow.run_rca_analysis(dict(PAYLOAD)))
    return estado["i"] - 1  # llamadas al Step 6


print("\n=== 1. El modelo NO pide mas ventana: una sola pasada ===")
n6 = correr([diagnostico(False)])
check("una sola consulta a PI", len(llamadas_pi) == 1, f"{len(llamadas_pi)}")
check("una sola llamada al Step 6", n6 == 1, f"{n6}")
check("ventana inicial de config", llamadas_pi[0][0] == config.PI_LOOKBACK_HOURS, f"{llamadas_pi}")
check("resolucion inicial de config",
      llamadas_pi[0][1] == (config.PI_QUERY_INTERVAL_VALUE, config.PI_QUERY_INTERVAL_UNIT), f"{llamadas_pi}")

print("\n=== 2. El modelo pide 7 dias: se amplia una vez ===")
n6 = correr([diagnostico(True, "7d", "la tendencia empieza antes de la ventana"),
             diagnostico(False)])
check("dos consultas a PI", len(llamadas_pi) == 2, f"{len(llamadas_pi)}")
check("dos llamadas al Step 6", n6 == 2, f"{n6}")
check("la 2a ventana es de 168 h", llamadas_pi[1][0] == 168, f"{llamadas_pi}")
check("la 2a resolucion se engrosa a 2 h", llamadas_pi[1][1] == (2, "hours"), f"{llamadas_pi}")
print(f"      pasadas: {llamadas_pi}")

print("\n=== 3. Lo pide dos veces: se corta en MAX_HISTORY_ADJUSTMENTS ===")
n6 = correr([diagnostico(True, "7d", "mas"), diagnostico(True, "14d", "aun mas"), diagnostico(True, "14d", "y mas")])
check(f"se para en {config.MAX_HISTORY_ADJUSTMENTS} ampliacion(es)",
      len(llamadas_pi) == config.MAX_HISTORY_ADJUSTMENTS + 1, f"{len(llamadas_pi)}")
check("no se llama al Step 6 de mas", n6 == config.MAX_HISTORY_ADJUSTMENTS + 1, f"{n6}")

print("\n=== 4. MAX_HISTORY_ADJUSTMENTS=0 desactiva la ampliacion ===")
orig = config.MAX_HISTORY_ADJUSTMENTS
config.MAX_HISTORY_ADJUSTMENTS = 0
n6 = correr([diagnostico(True, "7d", "quiero mas")])
check("una sola consulta pese a pedirlo", len(llamadas_pi) == 1, f"{len(llamadas_pi)}")
config.MAX_HISTORY_ADJUSTMENTS = orig

print("\n=== 5. Pide la ventana mayor del catalogo ===")
n6 = correr([diagnostico(True, "14d", "lo maximo"), diagnostico(False)])
check("la 2a ventana es la mayor del catalogo", llamadas_pi[1][0] == config.PI_MAX_LOOKBACK_HOURS, f"{llamadas_pi}")

print("\n=== 6. Estrechar la ventana en el bucle ===")
n6 = correr([json.dumps({
    "root_causes": [{"cause": "Cavitacion", "explanation": "y", "recommended_action": "z"}],
    "history_request": {"needed": True, "window_option": "8h",
                        "trend_start": "2026-08-19T18:00:00Z",
                        "reason": "sospecha de transitorio, necesito detalle de minutos"}}),
             diagnostico(False)])
check("dos consultas a PI", len(llamadas_pi) == 2, f"{len(llamadas_pi)}")
check("la 2a ventana se estrecha a 8 h", llamadas_pi[1][0] == 8, f"{llamadas_pi}")
check("la 2a resolucion se afina a 5 min", llamadas_pi[1][1] == (5, "minutes"), f"{llamadas_pi}")
print(f"      pasadas: {llamadas_pi}")

print("\n=== 7. Estrechar sin trend_start: se deniega ===")
n6 = correr([json.dumps({
    "root_causes": [{"cause": "x", "explanation": "y", "recommended_action": "z"}],
    "history_request": {"needed": True, "window_option": "2h", "reason": "quiero detalle"}})])
check("una sola pasada", len(llamadas_pi) == 1, f"{len(llamadas_pi)}")

print("\n=== 8. Step 6 sin history_request (esquema viejo): no rompe ===")
n6 = correr([json.dumps({"root_causes": [{"cause": "x", "explanation": "y", "recommended_action": "z"}]})])
check("una sola pasada, sin error", len(llamadas_pi) == 1, f"{len(llamadas_pi)}")

print("\n" + "=" * 60)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

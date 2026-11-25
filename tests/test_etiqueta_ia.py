"""Pruebas de la etiqueta de contenido generado por IA ('_ai_generated').

Lo que se protege: que cada diagnostico guardado nombre el proveedor Y EL MODELO
que de verdad se llamaron (ai-governance §5.4 y §7.1, base §1.5).

Existe por un fallo real. webhook.py calculaba el modelo con
`GEMINI_MODEL if gemini else ANTHROPIC_MODEL`, escrito cuando habia dos
proveedores. Al llegar DeepSeek (2026-09-30) siguio igual, y cuatro expedientes
reales quedaron como `"provider": "deepseek", "model": "claude-opus-4-8"`. Nada
fallaba: el dato era falso pero tenia la forma correcta, y ninguna suite miraba
el modelo con un proveedor distinto de los dos originales. Se descubrio el
2026-11-25 revisando la documentacion contra los expedientes, no por una prueba.

Por eso se comprueban DOS cosas, y las dos hacen falta:
  1. Que la etiqueta que escribe el webhook es la de modelo_en_uso(), para los
     tres proveedores, con nombres de modelo inventados -- asi no depende del
     .env de quien ejecute la suite.
  2. Que modelo_en_uso() dice lo mismo que la llamada real: para cada
     proveedor, la variable que devuelve es la que su _generate_*() pasa como
     model=. Sin esto, la tabla podria quedar bien probada y desalineada de la
     llamada, que es justo como nacio el fallo.
"""
import asyncio
import io
import re
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

TMP = Path(tempfile.mkdtemp(prefix="rca_etiqueta_"))
config.INCIDENTS_DIR = str(TMP / "inc")
config.AUDIT_FILE = str(TMP / "audit.jsonl")
config.LOG_FILE = str(TMP / "app.log")
config.WORKFLOW_ENABLED = True

import observability
observability.configure_logging()
import incidents
import llm_client
import webhook
import workflow

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


# Nombres inventados: si la etiqueta muestra uno de otro proveedor, se ve al
# instante cual. Nunca se imprime config.*_MODEL real (ver test_config.py §14:
# una clave pegada por error en esa variable acabo una vez en la consola).
FALSOS = {
    "gemini": "modelo-de-prueba-gemini",
    "anthropic": "modelo-de-prueba-anthropic",
    "deepseek": "modelo-de-prueba-deepseek",
}
guardado = {v: getattr(config, v) for v in llm_client._MODELO_DE.values()}
guardado_proveedor = config.LLM_PROVIDER
for proveedor, variable in llm_client._MODELO_DE.items():
    setattr(config, variable, FALSOS[proveedor])


async def _diagnostico_fijo(payload, trace=None, revision=None, **_):
    return {"root_causes": [{"cause": "x", "evidence": ["y"], "recommended_action": "z"}]}

workflow.run_rca_analysis = _diagnostico_fijo


def payload(activo):
    return {
        "KPIName": "Hydraulic Efficiency", "Asset": activo,
        "Subsystem": "Pumping Station 02", "System": "External Pumping", "Plant": "WWTP",
        "KPI": 41.3, "Limit": 48.0, "LimitThresholdType": "Low",
        "StartTime": "2026-11-25T10:00:00Z",
    }


try:
    print("\n=== 1. La etiqueta del expediente nombra el modelo del proveedor activo ===")
    for i, proveedor in enumerate(FALSOS):
        config.LLM_PROVIDER = proveedor
        r = incidents.claim(payload(f"PS20102 A03 PS02 Pump {i:02d}"))
        asyncio.run(webhook._analizar_incidente(r["payload"], r))
        final = incidents._leer(TMP / "inc" / f"{r['id']}.json")
        etiqueta = (incidents.iteraciones(final) or [{}])[-1].get("_ai_generated") or {}
        check(f"{proveedor}: proveedor", etiqueta.get("provider") == proveedor, etiqueta)
        check(f"{proveedor}: modelo", etiqueta.get("model") == FALSOS[proveedor], etiqueta)
        check(f"{proveedor}: lleva el aviso", bool(etiqueta.get("notice")), etiqueta)

    print("\n=== 2. El caso que fallo: DeepSeek no se etiqueta con un modelo de Claude ===")
    config.LLM_PROVIDER = "deepseek"
    check("modelo_en_uso() con deepseek no es ANTHROPIC_MODEL",
          llm_client.modelo_en_uso() != config.ANTHROPIC_MODEL, llm_client.modelo_en_uso())

    print("\n=== 3. modelo_en_uso() coincide con el model= de cada llamada ===")
    fuente = (RAIZ / "llm_client.py").read_text(encoding="utf-8")
    for proveedor, variable in llm_client._MODELO_DE.items():
        inicio = fuente.index(f"def _generate_{proveedor}(")
        fin = fuente.find("\ndef ", inicio + 1)
        cuerpo = fuente[inicio:fin if fin != -1 else None]
        usados = re.findall(r"\bmodel=config\.(\w+)", cuerpo)
        check(f"_generate_{proveedor}() llama con config.{variable}",
              usados == [variable], usados)

    print("\n=== 4. Los tres proveedores que acepta generate() tienen fila en la tabla ===")
    # Si se anade un cuarto a generate() y no aqui, modelo_en_uso() lanzaria
    # ValueError despues de un analisis que si se hizo.
    i = fuente.index("def generate(")
    aceptados = re.search(r"if provider not in \(([^)]*)\)", fuente[i:]).group(1)
    aceptados = set(re.findall(r'"(\w+)"', aceptados))
    check("mismos proveedores", aceptados == set(llm_client._MODELO_DE), aceptados)

    print("\n=== 5. Un proveedor desconocido no se etiqueta con un modelo cualquiera ===")
    config.LLM_PROVIDER = "otro"
    try:
        llm_client.modelo_en_uso()
        check("lanza ValueError", False, "no lanzo")
    except ValueError:
        check("lanza ValueError", True)
finally:
    config.LLM_PROVIDER = guardado_proveedor
    for variable, valor in guardado.items():
        setattr(config, variable, valor)

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

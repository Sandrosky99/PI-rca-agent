"""Vuelca a disco todo lo de una pasada, y REPRODUCE la puerta de la busqueda.

Existe porque la puerta no deja rastro: decide si se busca o no, con una llamada
al modelo, y no escribe ni su prompt ni su respuesta en el trace. Cuando dice que
no -- que es lo normal -- el expediente no conserva por que.

Uso:
    python dev-fixtures/volcar_busqueda.py <id-del-incidente> [carpeta]

Hace UNA llamada al modelo (la puerta) contra el AF y el proveedor reales.
"""
import asyncio
import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
os.environ.setdefault("INCIDENTS_DIR", "dev-fixtures/incidents")

import config  # noqa: E402

config.INCIDENTS_DIR = os.environ["INCIDENTS_DIR"]
import graph_client  # noqa: E402
import incidents  # noqa: E402
import workflow  # noqa: E402


def escribir(carpeta: Path, nombre: str, contenido) -> None:
    ruta = carpeta / nombre
    if isinstance(contenido, str):
        ruta.write_text(contenido, encoding="utf-8")
    else:
        ruta.write_text(json.dumps(contenido, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  %-46s %7d bytes" % (nombre, ruta.stat().st_size))


async def main() -> None:
    incidente_id = sys.argv[1]
    carpeta = Path(sys.argv[2] if len(sys.argv) > 2 else "prompts-revision/busqueda")
    carpeta.mkdir(parents=True, exist_ok=True)

    ruta = Path(config.INCIDENTS_DIR) / (incidente_id + ".json")
    registro = json.loads(ruta.read_text(encoding="utf-8"))

    escribir(carpeta, "00-expediente-completo.json", registro)
    escribir(carpeta, "01-payload-de-pi.json", registro.get("payload") or {})

    for n, t in enumerate(incidents.traces(registro), 1):
        for clave in ("step3_prompt", "step4_response", "step6_prompt", "step6_response"):
            if t.get(clave):
                escribir(carpeta, "1%d-pasada%d-%s.txt" % (n, n, clave), t[clave])
        if t.get("busqueda"):
            escribir(carpeta, "1%d-pasada%d-busqueda.json" % (n, n), t["busqueda"])

    revision = incidents.revision_de_causas(registro)
    escribir(carpeta, "20-revision-humana.json", revision)

    # --- la reproduccion de la puerta ---
    payload = registro.get("payload") or {}
    af = await graph_client.build_af_context(
        payload.get("Asset") or "", payload.get("Subsystem") or "",
        payload.get("System") or "")
    af["contexto_anadido"] = []
    escribir(carpeta, "30-af-context.json", af)

    disponibles = workflow._variables_disponibles(af)
    escribir(carpeta, "31-variables-disponibles.txt", "\n".join(disponibles))

    sospechas = [(workflow.clave_de_causa(c), c["sospecha"]) for c in revision
                 if c.get("estado") == incidents.DESCARTADA and c.get("sospecha")]
    mensaje = workflow._PUERTA_BUSQUEDA_PROMPT.format(
        sospechas="\n".join("[%s] %s" % (k, s) for k, s in sospechas),
        cuantas=len(disponibles), disponibles="\n".join(disponibles))
    escribir(carpeta, "32-puerta-ENTRADA-al-modelo.txt", mensaje)

    crudo = await workflow._generar(mensaje)
    escribir(carpeta, "33-puerta-SALIDA-del-modelo.txt", crudo)

    objetivos, pendientes = await workflow.sospechas_que_senalan_fuera(revision, af)
    escribir(carpeta, "34-puerta-decision.json",
             {"objetivos": objetivos, "pendientes": pendientes})

    print()
    print("  sospechas que entraron : %d" % len(sospechas))
    print("  variables disponibles  : %d" % len(disponibles))
    print("  objetivos de busqueda  : %d" % len(objetivos))
    print()
    print("  RESPUESTA CRUDA DE LA PUERTA:")
    print("  " + crudo.strip().replace("\n", "\n  ")[:1200])


asyncio.run(main())

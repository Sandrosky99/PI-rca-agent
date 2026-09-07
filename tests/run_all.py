"""Ejecuta todas las suites de pruebas del workflow RCA.

    python tests/run_all.py

No hace falta PI System, ni los MCP servers, ni claves de API: las suites usan
dobles para el LLM y para los clientes MCP, y un directorio temporal para los
incidentes. Devuelve código de salida 0 si todo pasa, 1 si algo falla, que es lo
que usa el gate de CI (ai-governance §2.4).

Se usa un runner propio en vez de pytest a propósito: evita añadir una
dependencia que habría que verificar (ai-governance §1.3) para un proyecto que
no la necesita. Si el proyecto crece, migrar a pytest es directo.
"""

import io
import subprocess
import sys
from pathlib import Path

# La consola de Windows va en cp1252 y las suites imprimen acentos y "…".
# Sin esto, el propio runner revienta al volcar la salida de una suite fallida.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SUITES = [
    ("Puerta de autorizacion del Step 4", "test_authorization.py"),
    ("Ventana variable y catalogo", "test_ventana.py"),
    ("Bucle de reajuste Steps 5-6", "test_bucle.py"),
    ("Registro de incidentes", "test_incidents.py"),
    ("Observabilidad: logging y audit trail", "test_observability.py"),
    ("Decisiones de seguridad del webhook", "test_seguridad.py"),
    ("Validacion de la notificacion entrante", "test_validacion.py"),
]


def main() -> int:
    aqui = Path(__file__).resolve().parent
    fallidas = []

    print("=" * 66)
    print("PRUEBAS DEL WORKFLOW RCA")
    print("=" * 66)

    for titulo, fichero in SUITES:
        proc = subprocess.run(
            [sys.executable, str(aqui / fichero)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        ok = proc.returncode == 0
        print(f"  [{'PASA' if ok else 'FALLA'}] {titulo}")
        if not ok:
            fallidas.append(fichero)
            # Solo se vuelca la salida de lo que falla: en verde no aporta.
            for linea in (proc.stdout or "").splitlines():
                if "FALLA" in linea or "FALLOS" in linea:
                    print(f"         {linea.strip()}")
            if proc.stderr:
                print(f"         stderr: {proc.stderr.strip()[:500]}")

    print("=" * 66)
    if fallidas:
        print(f"RESULTADO: {len(fallidas)} suite(s) con fallos -> {fallidas}")
        return 1
    print(f"RESULTADO: las {len(SUITES)} suites pasan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

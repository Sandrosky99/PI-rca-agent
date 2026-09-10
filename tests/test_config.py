"""Pruebas de la carga de configuracion.

La comprobacion importante es la ultima: que copiar .env.example a .env -- que
es literalmente lo que instruye el README -- deje una configuracion cargable.
Hasta el 2026-09-08 no lo era: reventaba con
"ValueError: invalid literal for int() with base 10: ''"
sin decir siquiera de que variable venia.
"""
import io
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


print("\n=== 1. _entero(): variable ausente o vacia -> valor por defecto ===")
import os

CLAVE = "RCA_PRUEBA_ENTERO"
os.environ.pop(CLAVE, None)
check("ausente", config._entero(CLAVE, 24) == 24)
for vacio, etiqueta in (("", "cadena vacia"), ("   ", "solo espacios"), ("\t", "tabulador")):
    os.environ[CLAVE] = vacio
    check(f"{etiqueta} -> defecto", config._entero(CLAVE, 24) == 24, config._entero(CLAVE, 24))

print("\n=== 2. _entero(): valores validos ===")
for crudo, esperado in (("90", 90), ("  90  ", 90), ("0", 0), ("-5", -5)):
    os.environ[CLAVE] = crudo
    check(f"{crudo!r} -> {esperado}", config._entero(CLAVE, 24) == esperado, config._entero(CLAVE, 24))

print("\n=== 3. _entero(): valor no numerico -> defecto, sin reventar ===")
for basura in ("noventa", "90.5", "9 0", "true"):
    os.environ[CLAVE] = basura
    try:
        v = config._entero(CLAVE, 24)
        check(f"{basura!r} -> 24 sin excepcion", v == 24, v)
    except Exception as exc:
        check(f"{basura!r} -> 24 sin excepcion", False, f"{type(exc).__name__}")
os.environ.pop(CLAVE, None)

print("\n=== 4. EL CASO REAL: copiar .env.example a .env y arrancar ===")
# En un subproceso y en un directorio aparte: config tiene efectos al importarse
# y hay que cargarlo de cero contra ese .env, no contra el del proyecto.
TMP = Path(tempfile.mkdtemp(prefix="rca_cfg_"))
shutil.copy2(RAIZ / "config.py", TMP / "config.py")
shutil.copy2(RAIZ / ".env.example", TMP / ".env")

guion = (
    "import sys; sys.path.insert(0, '.')\n"
    "import config\n"
    "print('OK', config.WEBHOOK_PORT, config.PI_LOOKBACK_HOURS, config.LLM_MAX_TOKENS,\n"
    "      config.INCIDENT_COOLDOWN_MINUTES, config.MAX_SELECTED_VARIABLES)\n"
)
proc = subprocess.run([sys.executable, "-c", guion], cwd=TMP,
                      capture_output=True, text=True, encoding="utf-8", errors="replace")

check("config carga sin excepcion", proc.returncode == 0,
      (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else "")
if proc.returncode == 0:
    partes = proc.stdout.split()
    check("aplica los valores por defecto documentados",
          partes[1:6] == ["8090", "24", "16000", "20", "40"], proc.stdout.strip())

print("\n=== 5. Todas las variables del ejemplo estan contempladas en config.py ===")
import re

ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
fuente = (RAIZ / "config.py").read_text(encoding="utf-8")
claves = re.findall(r"^([A-Z_][A-Z0-9_]*)=", ejemplo, re.M)
huerfanas = [k for k in claves if k not in fuente]
check(f"las {len(claves)} claves del ejemplo se leen en config.py",
      not huerfanas, f"sin uso: {huerfanas}")

print("\n=== 6. validate_config solo exige la clave del proveedor elegido ===")
orig_prov, orig_g, orig_a = config.LLM_PROVIDER, config.GEMINI_API_KEY, config.ANTHROPIC_API_KEY
config.LLM_PROVIDER, config.GEMINI_API_KEY, config.ANTHROPIC_API_KEY = "gemini", "x", ""
check("con gemini no exige la de anthropic", config.validate_config() == [])
config.GEMINI_API_KEY = ""
check("con gemini y sin clave, la reclama", config.validate_config() == ["GEMINI_API_KEY"],
      config.validate_config())
config.LLM_PROVIDER, config.ANTHROPIC_API_KEY = "anthropic", "y"
check("con anthropic no exige la de gemini", config.validate_config() == [])
config.LLM_PROVIDER = "otro"
check("proveedor desconocido se reporta", config.validate_config() != [])
config.LLM_PROVIDER, config.GEMINI_API_KEY, config.ANTHROPIC_API_KEY = orig_prov, orig_g, orig_a

shutil.rmtree(TMP, ignore_errors=True)
print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

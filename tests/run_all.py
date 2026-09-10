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
import re
import subprocess
import sys
from pathlib import Path

# La consola de Windows va en cp1252 y las suites imprimen acentos y "…".
# Sin esto, el propio runner revienta al volcar la salida de una suite fallida.
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

SUITES = [
    ("Carga de configuracion", "test_config.py"),
    ("Puerta de autorizacion del Step 4", "test_authorization.py"),
    ("Ventana variable y catalogo", "test_ventana.py"),
    ("Bucle de reajuste Steps 5-6", "test_bucle.py"),
    ("Registro de incidentes", "test_incidents.py"),
    ("Observabilidad: logging y audit trail", "test_observability.py"),
    ("Decisiones de seguridad del webhook", "test_seguridad.py"),
    ("Validacion de la notificacion entrante", "test_validacion.py"),
]


def _comprobar_portabilidad(aqui: Path) -> list[str]:
    """Ninguna suite debe llevar rutas absolutas de una maquina concreta.

    Esto existe por un fallo real (2026-09-08). Cuatro suites conservaban un
    sys.path.insert con la ruta del proyecto en Windows. Aqui pasaban -- la
    ruta existe -- y en el CI de Linux morian las cuatro con
    ModuleNotFoundError. Peor: la comprobacion que se hizo entonces para
    descartarlo tenia el mismo error de escape que el arreglo, asi que dio un
    falso "sin rutas absolutas".

    Una comprobacion en Python, sin pasar por el shell, no se puede equivocar
    de esa manera.
    """
    # Solo se miran las lineas que manipulan sys.path. Una ruta de Windows
    # dentro de una cadena cualquiera puede ser un dato de prueba legitimo --
    # test_seguridad comprueba justamente que los errores NO expongan rutas
    # como "C:\\" -- y marcarla seria un falso positivo.
    unidad = r"[A-Za-z]:" + re.escape(chr(92))     # C:\
    raiz_unix = r"""["']/"""                        # "/algo
    sospechosas = []
    for _, fichero in SUITES:
        texto = (aqui / fichero).read_text(encoding="utf-8")
        for n, linea in enumerate(texto.splitlines(), 1):
            if "sys.path" not in linea:
                continue
            if re.search(unidad, linea) or re.search(raiz_unix, linea):
                sospechosas.append(f"{fichero}:{n}: {linea.strip()[:70]}")
    return sospechosas


def _comprobar_aislamiento(aqui: Path) -> list[str]:
    """Una suite que ejercita el webhook debe fijar el interruptor de parada.

    Esto tambien existe por un fallo real (2026-09-10). test_validacion heredaba
    WORKFLOW_ENABLED del .env de la maquina. Con el interruptor echado -- lo
    normal despues de probarlo -- el webhook responde "paused" antes de llegar
    al workflow y la suite fallaba en el servidor. En CI no fallaba nunca,
    porque alli no hay .env y sale el valor por defecto.

    Ese es el patron peligroso: verde en CI, rojo solo en la maquina real y
    solo a ratos. El resultado de las pruebas no puede depender de en que
    estado operativo se dejo el despliegue.
    """
    sin_fijar = []
    for _, fichero in SUITES:
        texto = (aqui / fichero).read_text(encoding="utf-8")
        if not re.search(r"^import webhook", texto, re.M):
            continue
        if not re.search(r"^config\.WORKFLOW_ENABLED\s*=", texto, re.M):
            sin_fijar.append(fichero)
    return sin_fijar


def main() -> int:
    aqui = Path(__file__).resolve().parent
    fallidas = []

    print("=" * 66)
    print("PRUEBAS DEL WORKFLOW RCA")
    print("=" * 66)

    rutas = _comprobar_portabilidad(aqui)
    if rutas:
        print("  [FALLA] Portabilidad: hay rutas absolutas en las suites")
        for r in rutas:
            print(f"         {r}")
        print("  Las suites deben resolver el proyecto con")
        print("  Path(__file__).resolve().parent.parent, no con una ruta fija.")
        print("=" * 66)
        return 1
    print("  [PASA] Portabilidad: ninguna suite lleva rutas absolutas")

    heredan = _comprobar_aislamiento(aqui)
    if heredan:
        print("  [FALLA] Aislamiento: hay suites que heredan el interruptor del .env")
        for f in heredan:
            print(f"         {f}: importa webhook y no fija config.WORKFLOW_ENABLED")
        print("  Una suite que ejercita el webhook debe fijar el interruptor a mano,")
        print("  o su resultado dependera del estado operativo de la maquina.")
        print("=" * 66)
        return 1
    print("  [PASA] Aislamiento: las suites del webhook no dependen del .env")

    for titulo, fichero in SUITES:
        proc = subprocess.run(
            [sys.executable, str(aqui / fichero)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
        ok = proc.returncode == 0
        print(f"  [{'PASA' if ok else 'FALLA'}] {titulo}")
        if not ok:
            fallidas.append(fichero)
            # Se vuelca TODO lo de la suite que falla. Antes se filtraba a las
            # lineas con "FALLA" y se recortaba stderr a 500 caracteres, y eso
            # dejaba ciego el diagnostico cuando el fallo era un error de
            # importacion: sin comprobaciones ejecutadas no hay lineas "FALLA"
            # que filtrar, y la traza se cortaba antes de lo interesante.
            # En CI el log es lo unico que hay, asi que mas vale que sobre.
            print(f"         --- salida (codigo {proc.returncode}) ---")
            for linea in (proc.stdout or "").splitlines():
                print(f"         {linea}")
            if proc.stderr:
                print(f"         --- stderr ---")
                for linea in proc.stderr.splitlines():
                    print(f"         {linea}")
            print(f"         --- fin de {fichero} ---")

    print("=" * 66)
    if fallidas:
        print(f"RESULTADO: {len(fallidas)} suite(s) con fallos -> {fallidas}")
        return 1
    print(f"RESULTADO: las {len(SUITES)} suites pasan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

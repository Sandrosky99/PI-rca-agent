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
import llm_client

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
print("\n=== 12. Razonamiento extendido en la ruta de Anthropic ===")
# Existe para IGUALAR LOS DOS PROVEEDORES: gemini-2.5-flash razona por defecto y
# la llamada a Anthropic no lo pedia, asi que cambiar de proveedor cambiaba algo
# mas que el proveedor.
check("encendido por defecto", config.ANTHROPIC_THINKING is True, config.ANTHROPIC_THINKING)
fuente = (RAIZ / "llm_client.py").read_text(encoding="utf-8")
# "adaptive" y no un presupuesto de tokens: en claude-opus-4-8 budget_tokens ya
# no se acepta -- la API responde 400 --, asi que la forma vieja no es solo peor,
# es un error.
check("es adaptive", '{"type": "adaptive"}' in fuente)
# Se mira LA LLAMADA, no el fichero: los comentarios de al lado nombran
# budget_tokens justo para explicar por que no se usa.
i = fuente.index("response = client.messages.create(")
llamada = fuente[i:fuente.index(")", fuente.index("**extra", i))]
check("y NO se pasa budget_tokens", "budget_tokens" not in llamada, llamada)
check("el thinking entra por el extra", "**extra" in llamada, llamada)
# Con razonamiento la API solo admite la temperatura por defecto.
check("no se pasa temperatura", "temperature=" not in fuente)

print("\n=== 12b. EL RIESGO: el primer bloque ya no es el texto ===")
# Con razonamiento extendido la respuesta llega en VARIOS bloques y el primero
# es el pensamiento. response.content[0].text -- que es lo que habia -- habria
# devuelto el razonamiento en lugar del JSON, y habria roto el parseo de TODOS
# los pasos con un error que no apunta ni de lejos a esa linea.
class Bloque:
    def __init__(self, tipo, texto=None):
        self.type = tipo
        if texto is not None:
            self.text = texto


class Respuesta:
    def __init__(self, bloques, stop="end_turn"):
        self.content = bloques
        self.stop_reason = stop


check("se salta el bloque de razonamiento",
      llm_client._texto_de(Respuesta([Bloque("thinking"), Bloque("text", '{"ok":1}')]))
      == '{"ok":1}')
check("y sigue valiendo sin razonamiento",
      llm_client._texto_de(Respuesta([Bloque("text", "hola")])) == "hola")
# Un bloque de razonamiento NO tiene .text: acceder a el reventaria.
check("no toca el .text del que no lo tiene",
      not hasattr(Bloque("thinking"), "text"))

# Sin bloque de texto no hay respuesta. Pasa si max_tokens se agota razonando, y
# el aviso tiene que decir que hacer.
try:
    llm_client._texto_de(Respuesta([Bloque("thinking")], stop="max_tokens"))
    check("sin texto avisa", False, "no lanzo nada")
except llm_client.LLMGenerationError as exc:
    check("sin texto avisa, y es el error del modulo", True)
    check("dice el motivo de parada", "max_tokens" in str(exc), str(exc))
    check("y que hacer", "ANTHROPIC_THINKING" in str(exc), str(exc))

print("\n=== 13. Un 429 se reintenta; el resto de 4xx no ===")
# El 429 dice "ahora mismo no, vuelve en un momento": es EL error reintentable
# por definicion. Estaba fuera porque la regla era "4xx no se reintenta" --
# cierta para todos los demas -- y eso dejaba a los dos proveedores
# comportandose distinto ante lo mismo: la rama de Anthropic si reintenta su
# RateLimitError.
#
# Costo un analisis real (2026-09-29): el paso de busqueda subio las llamadas
# por reanalisis de 2-3 a 8, ocho en 45 s agotaron el limite por minuto, y el
# 429 llego en el Step 6 -- con el diagnostico a una llamada de distancia.
from google.genai import errors as _gerr


def _gemini(clase, code):
    return clase(code, {"error": {"code": code, "status": "RESOURCE_EXHAUSTED"}}, None)


check("503 se reintenta", llm_client._is_transient_gemini_error(_gemini(_gerr.ServerError, 503)))
check("429 TAMBIEN", llm_client._is_transient_gemini_error(_gemini(_gerr.ClientError, 429)))
check("400 no", not llm_client._is_transient_gemini_error(_gemini(_gerr.ClientError, 400)))
check("401 tampoco", not llm_client._is_transient_gemini_error(_gemini(_gerr.ClientError, 401)))
check("y algo que no es del SDK, tampoco",
      not llm_client._is_transient_gemini_error(ValueError("x")))

# Los dos proveedores, ante un limite de ritmo, hacen lo mismo.
import anthropic as _anth
check("Anthropic ya lo hacia", "RateLimitError" in
      (RAIZ / "llm_client.py").read_text(encoding="utf-8"))
# Y la ventana de reintentos cubre de sobra un limite por minuto.
check("la ventana aguanta mas de un minuto",
      config.LLM_RETRY_WINDOW_SECONDS >= 60, config.LLM_RETRY_WINDOW_SECONDS)

print("\n=== 14. DeepSeek como tercer proveedor ===")
# Se habla con el SDK de Anthropic apuntado a otra base_url: DeepSeek publica un
# endpoint que habla ese protocolo y la llamada tiene la misma forma. Asi no
# entra una dependencia nueva, que aqui no es gratis -- cada una lleva revision
# de mantenimiento, adopcion y licencia (izSpecs 1.3).
# Se miran las lineas que DECLARAN dependencia, no el texto del fichero: los
# comentarios nombran a DeepSeek justo para explicar que no hace falta una.
requisitos = (RAIZ / "requirements.txt").read_text(encoding="utf-8")
declaradas = sorted(l.split("==")[0].strip() for l in requisitos.splitlines()
                    if "==" in l and not l.strip().startswith("#"))
check("NO se anade dependencia nueva", len(declaradas) == 8, declaradas)
check("y ninguna es de deepseek ni de openai",
      not any("deepseek" in d or "openai" in d for d in declaradas), declaradas)
check("se reutiliza el SDK que ya estaba", "anthropic" in declaradas, declaradas)

fuente = (RAIZ / "llm_client.py").read_text(encoding="utf-8")
i = fuente.index("def _generate_deepseek")
cuerpo = fuente[i:fuente.index("def _texto_de", i)]

# Configuracion PROPIA. Que un proveedor herede la de otro es como se acaba sin
# saber con que se genero un diagnostico.
check("clave propia", "config.DEEPSEEK_API_KEY" in cuerpo)
check("modelo propio", "config.DEEPSEEK_MODEL" in cuerpo)
check("y su base_url", "config.DEEPSEEK_BASE_URL" in cuerpo)
check("NO hereda nada de ANTHROPIC_", "config.ANTHROPIC" not in cuerpo, cuerpo[:200])
# El endpoint por defecto es el que documenta DeepSeek.
check("el endpoint por defecto es el compatible con Anthropic",
      config.DEEPSEEK_BASE_URL == "https://api.deepseek.com/anthropic",
      config.DEEPSEEK_BASE_URL)
check("y el modelo por defecto es el rapido",
      config.DEEPSEEK_MODEL == "deepseek-flash", config.DEEPSEEK_MODEL)

# NO se manda razonamiento: la ruta de Anthropic usa {"type": "adaptive"}, que es
# una forma de Claude 4.6+, y la documentacion de DeepSeek muestra otra distinta
# para su ruta de OpenAI sin decir que acepta la de Anthropic. Mandar un
# parametro sin comprobarlo es como se consiguen 400 en produccion.
# Se mira LA LLAMADA, no el texto: el docstring nombra 'thinking' justo para
# explicar por que no se manda.
llamada = cuerpo[cuerpo.index("client.messages.create("):]
llamada = llamada[:llamada.index("return")]
check("no se manda thinking sin haberlo comprobado",
      "thinking" not in llamada and "extra" not in llamada, llamada)
check("y si los cuatro parametros que DeepSeek documenta",
      all(x in llamada for x in ("model=", "max_tokens=", "system=", "messages=")), llamada)
# Y la funcion dice que no esta verificada contra la API real.
check("consta que no esta verificada en vivo", "NO VERIFICADO CONTRA LA API REAL" in cuerpo)

# Reintentos: los mismos, y por el mismo predicado -- las excepciones las levanta
# ese SDK, asi que son sus clases. Incluye el 429.
# El decorador va en la linea justo encima del def. Las excepciones las levanta
# el SDK de Anthropic -- es el que hace la llamada -- asi que el predicado es el
# suyo, y eso incluye el 429.
lineas = fuente.split(chr(10))
n = next(k for k, x in enumerate(lineas) if x.startswith("def _generate_deepseek"))
check("reintenta, con el predicado de Anthropic",
      "_is_transient_anthropic_error" in lineas[n - 1] and "_RETRY_COMMON" in lineas[n - 1],
      lineas[n - 1])

print("\n=== 14b. La eleccion es del usuario, en el .env ===")
check("el proveedor se acepta", "deepseek" in fuente and '"deepseek"' in fuente)
ejemplo = (RAIZ / ".env.example").read_text(encoding="utf-8")
for clave in ("DEEPSEEK_API_KEY", "DEEPSEEK_MODEL", "DEEPSEEK_BASE_URL"):
    check("%s esta en el .env.example" % clave, clave + "=" in ejemplo)
check("y el selector menciona los tres", '"deepseek"' in ejemplo and '"anthropic"' in ejemplo)

print("\n=== 14c. validate_config solo exige SU clave ===")
guardado = (config.LLM_PROVIDER, config.DEEPSEEK_API_KEY)
try:
    config.LLM_PROVIDER, config.DEEPSEEK_API_KEY = "deepseek", ""
    falta = config.validate_config()
    check("sin clave la reclama", "DEEPSEEK_API_KEY" in falta, falta)
    check("y no pide las de los otros",
          not any(k.startswith(("GEMINI", "ANTHROPIC")) for k in falta), falta)
    config.DEEPSEEK_API_KEY = "sk-loquesea"
    check("con clave no falta nada del LLM",
          not any("API_KEY" in k for k in config.validate_config()), config.validate_config())
    config.LLM_PROVIDER = "gemini"
    check("y con gemini no exige la de deepseek",
          "DEEPSEEK_API_KEY" not in config.validate_config())
    config.LLM_PROVIDER = "otracosa"
    check("un proveedor desconocido nombra los tres",
          all(x in str(config.validate_config()) for x in ("gemini", "anthropic", "deepseek")),
          config.validate_config())
finally:
    config.LLM_PROVIDER, config.DEEPSEEK_API_KEY = guardado

print("\n=== 14d. El despachador lo enruta ===")
llamadas = []
guardado = (llm_client._generate_deepseek, config.LLM_PROVIDER)
try:
    llm_client._generate_deepseek = lambda sp, um: llamadas.append(("deepseek", sp, um)) or "OK"
    config.LLM_PROVIDER = "deepseek"
    check("responde", llm_client.generate("sistema", "usuario") == "OK")
    check("por la rama de deepseek", llamadas and llamadas[0][0] == "deepseek", llamadas)
    check("con el system prompt y el mensaje", llamadas[0][1:] == ("sistema", "usuario"), llamadas)
    config.LLM_PROVIDER = "marciano"
    try:
        llm_client.generate("a", "b")
        check("un proveedor invalido revienta", False, "no lanzo nada")
    except ValueError as exc:
        check("un proveedor invalido revienta, y dice cuales valen",
              "deepseek" in str(exc), str(exc))
finally:
    llm_client._generate_deepseek, config.LLM_PROVIDER = guardado

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

"""Pruebas de observability.py contra extensions/observability-spec.md v1.1."""
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

TMP = Path(tempfile.mkdtemp(prefix="rca_obs_"))
config.AUDIT_FILE = str(TMP / "audit.jsonl")
config.LOG_FILE = str(TMP / "app.log")

import observability

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


def formatear(nivel=logging.INFO, msg="mensaje de prueba", args=(), extra=None, exc_info=None):
    rec = logging.LogRecord("test", nivel, __file__, 1, msg, args, exc_info)
    for k, v in (extra or {}).items():
        setattr(rec, k, v)
    return json.loads(observability.JsonFormatter().format(rec))


print("\n=== 1. §1: campos obligatorios del log ===")
e = formatear()
for campo in ("ts", "level", "component", "msg"):
    check(f"incluye '{campo}'", campo in e, f"{list(e)}")
check("component es el nombre del recurso", e["component"] == "pi-rca-workflow", e.get("component"))
check("msg es el texto legible", e["msg"] == "mensaje de prueba", e.get("msg"))

print("\n=== 2. §1: ts en ISO 8601 UTC con milisegundos ===")
import re
check("formato ...THH:MM:SS.mmmZ",
      re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", e["ts"]) is not None, e["ts"])

print("\n=== 3. §1: niveles admitidos (ERROR/WARN/INFO/DEBUG) ===")
for nivel, esperado in ((logging.ERROR, "ERROR"), (logging.WARNING, "WARN"),
                        (logging.INFO, "INFO"), (logging.DEBUG, "DEBUG"),
                        (logging.CRITICAL, "ERROR")):
    got = formatear(nivel)["level"]
    check(f"{logging.getLevelName(nivel)} -> {esperado}", got == esperado, got)

print("\n=== 4. §1: una linea por entrada, JSON valido ===")
rec = logging.LogRecord("test", logging.INFO, __file__, 1, "con\nsaltos\nde linea", (), None)
salida = observability.JsonFormatter().format(rec)
check("la salida no contiene saltos crudos", "\n" not in salida, repr(salida[:60]))
check("es JSON valido", isinstance(json.loads(salida), dict))

print("\n=== 5. §1: truncado a 200 caracteres ===")
largo = "x" * 500
e = formatear(msg=largo)
check("msg truncado", len(e["msg"]) == 201, len(e["msg"]))
check("acaba en el caracter de elision", e["msg"].endswith("…"))
e = formatear(extra={"promptText": largo})
check("los extras tambien se truncan", len(e["promptText"]) == 201, len(e["promptText"]))

print("\n=== 6. §1: campos extra en camelCase se conservan ===")
e = formatear(extra={"incidentId": "abc123", "promptChars": 5500})
check("incidentId presente", e.get("incidentId") == "abc123", e.get("incidentId"))
check("valores no textuales intactos", e.get("promptChars") == 5500, e.get("promptChars"))

print("\n=== 6-bis. Se descarta el ruido que anade uvicorn ===")
# uvicorn adjunta 'color_message': el mismo texto con codigos ANSI para la
# consola. En un log JSON solo es ruido ilegible.
_ESC = chr(27)
e = formatear(extra={"color_message": f"Started {_ESC}[36m%d{_ESC}[0m", "incidentId": "abc"})
check("color_message no aparece", "color_message" not in e, f"{sorted(e)}")
check("sin codigos de escape ANSI", _ESC not in json.dumps(e))
check("los extras utiles si pasan", e.get("incidentId") == "abc")

print("\n=== 7. base §5: sin trazas de pila ni rutas internas ===")
try:
    raise ValueError("algo ha fallado en C:\\ruta\\interna\\modulo.py")
except ValueError:
    e = formatear(msg="fallo", exc_info=sys.exc_info())
check("registra el tipo de error", e.get("errorType") == "ValueError", e.get("errorType"))
check("no vuelca la traza", "Traceback" not in json.dumps(e))
check("no incluye el nombre de este fichero", "test_observability.py" not in json.dumps(e))

print("\n=== 8. §2: el audit trail escribe JSON Lines ===")
Path(config.AUDIT_FILE).unlink(missing_ok=True)
observability.audit("create_timeseries_bucket", {"bucketId": "rca_1234", "paths": 30})
observability.audit("incident.claim", {"incidentId": "abc"}, status="BLOCKED", detail="duplicado")
lineas = Path(config.AUDIT_FILE).read_text(encoding="utf-8").strip().splitlines()
check("una linea por operacion", len(lineas) == 2, f"{len(lineas)}")
entradas = [json.loads(l) for l in lineas]
for campo in ("ts", "op", "args", "status"):
    check(f"incluye '{campo}'", all(campo in x for x in entradas))
check("status OK por defecto", entradas[0]["status"] == "OK", entradas[0]["status"])
check("status BLOCKED con detalle",
      entradas[1]["status"] == "BLOCKED" and entradas[1]["detail"] == "duplicado", entradas[1])
check("args conserva los parametros", entradas[0]["args"]["bucketId"] == "rca_1234")

print("\n=== 9. §2.1: args largos truncados a 120 ===")
observability.audit("query", {"piApiPath": "y" * 400})
ultima = json.loads(Path(config.AUDIT_FILE).read_text(encoding="utf-8").strip().splitlines()[-1])
check("valor truncado", len(ultima["args"]["piApiPath"]) == 121, len(ultima["args"]["piApiPath"]))

print("\n=== 10. §2.3: destino separado del log de aplicacion ===")
check("audit y log son ficheros distintos", config.AUDIT_FILE != config.LOG_FILE)
observability.configure_logging()
logging.getLogger("prueba").info("esto es log de aplicacion, no auditoria")
contenido_audit = Path(config.AUDIT_FILE).read_text(encoding="utf-8")
check("el log de aplicacion no acaba en el audit trail",
      "esto es log de aplicacion" not in contenido_audit)

print("\n=== 11. §2.4: el audit trail sobrevive al proceso ===")
antes = len(Path(config.AUDIT_FILE).read_text(encoding="utf-8").strip().splitlines())
observability.audit("otra.op", {"x": 1})
despues = len(Path(config.AUDIT_FILE).read_text(encoding="utf-8").strip().splitlines())
check("es append-only sobre fichero", despues == antes + 1, f"{antes} -> {despues}")

print("\n=== 12. Un fallo de auditoria no tumba la operacion ===")
config.AUDIT_FILE = "\x00ruta/imposible/audit.jsonl"
try:
    observability.audit("op.imposible", {"x": 1})
    check("no propaga la excepcion", True)
except Exception as exc:
    check("no propaga la excepcion", False, f"{type(exc).__name__}")
config.AUDIT_FILE = str(TMP / "audit.jsonl")

logging.getLogger().handlers.clear()
shutil.rmtree(TMP, ignore_errors=True)
print("\n=== El cuerpo que manda PI no se recorta a 200 ===")
# El webhook registra una linea titulada "Body raw (primeros 1000 bytes)" para
# poder diagnosticar lo que envia PI. El formateador la dejaba en 200, cortando
# justo antes de donde esta el error.
#
# Visto el 2026-10-08: llego una notificacion cuyo cuerpo no parseaba como JSON
# y el log guardo hasta 'Plant": "WW...'. El sistema guardaba el cuerpo para
# depurarlo y acto seguido lo tiraba.
_flujo = io.StringIO()
_h = logging.StreamHandler(_flujo); _h.setFormatter(observability.JsonFormatter())
_lg = logging.getLogger("prueba_crudo"); _lg.handlers = [_h]
_lg.setLevel(logging.INFO); _lg.propagate = False

_lg.info("normal: %s", "X" * 900)
_lg.info("crudo: %s", "X" * 900, extra={"_crudo": True})
_normal, _crudo = [json.loads(x) for x in _flujo.getvalue().strip().split(chr(10))]

check("una linea normal se sigue recortando a 200", len(_normal["msg"]) <= 201, len(_normal["msg"]))
check("una marcada llega al tope grande", 900 < len(_crudo["msg"]) <= 1001, len(_crudo["msg"]))
# No es "sin recorte": un cuerpo de 10 MB reventaria el log igual.
_flujo.truncate(0); _flujo.seek(0)
_lg.info("crudo: %s", "Y" * 5000, extra={"_crudo": True})
check("y sigue habiendo tope", len(json.loads(_flujo.getvalue())["msg"]) <= 1001)

# La marca cambia COMO se formatea, no QUE se publica: lleva guion bajo, y el
# bucle de extras salta las claves que empiezan por "_".
check("la marca no se cuela en el JSON", "_crudo" not in _crudo, sorted(_crudo))
check("ni aparece con otro nombre",
      sorted(_crudo) == ["component", "level", "msg", "ts"], sorted(_crudo))

# Y el webhook la usa donde toca.
_fuente = (Path(__file__).resolve().parent.parent / "webhook.py").read_text(encoding="utf-8")
# El tope grande va SOLO en el camino del fallo. La linea incondicional -- que
# sale para TODA notificacion, tambien las validas -- se queda en 200: de esas
# el cuerpo completo ya vive en el fichero del incidente, y volcarlo tambien al
# log seria duplicarlo donde no hace falta. Lo cazo test_seguridad.py.
_i = _fuente.index("Body raw (primeros 1000 bytes): %s")
check("la linea incondicional NO va marcada",
      "_crudo" not in _fuente[_i:_i+160], _fuente[_i:_i+160])
# Ademas del cuerpo, el MOTIVO del parseo: dice en que linea y columna falla.
_j = _fuente.index("no es JSON valido")
check("y se registra por que no parsea", "%s" in _fuente[_j:_j+40], _fuente[_j:_j+120])

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

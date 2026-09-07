"""
observability.py — Logging estructurado y audit trail

Implementa extensions/observability-spec.md (v1.1) de izSpecs:

  §1 Structured Logging [MUST]
      Todo log es JSON válido de una línea, con ts / level / component / msg.
      Los campos extra van en camelCase. Los valores de texto de más de 200
      caracteres se truncan con "…".

  §2 Audit Trail [MUST]
      Toda operación que modifique estado externo se registra ANTES de
      ejecutarse, en un destino separado del log de aplicación, con
      ts / op / args / status / detail, y sobrevive a reinicios del proceso.

Sobre el truncado y los prompts
  El log de este proyecto guardaba los prompts completos del Step 3 y el Step 6
  (~55.000 caracteres cada uno). Truncar a 200 los haría inservibles, y esos
  prompts son justamente lo que permite reconstruir por qué el modelo respondió
  lo que respondió.

  Solución: el prompt deja de ir al log y pasa al fichero del incidente, que es
  su sitio natural -- es el registro del caso, y base/software-spec §1.5 pide
  precisamente preservar el prompt que llevó a un artefacto generado por IA. El
  log conserva un resumen truncado.

Sobre "escribir antes de ejecutar" (§2.2)
  La entrada de auditoría registra la INTENCIÓN de ejecutar, no su resultado:
    OK      -> la operación está permitida y se va a ejecutar a continuación
    BLOCKED -> se ha rechazado (duplicado, validación, modo de solo lectura)
    ERROR   -> no se ha podido ni intentar
  El resultado posterior se ve en el log de aplicación, que va correlacionado
  por el mismo incidentId.
"""

import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

import config

# Nombre del recurso tal y como se declara en su repositorio (spec §1).
COMPONENT = "pi-rca-workflow"

# §1: los valores de texto de más de 200 caracteres SHOULD truncarse.
_MAX_MSG = 200
# §2.1: los valores de args de más de 120 caracteres SHOULD truncarse.
_MAX_ARG = 120

# Campos que logging pone en el LogRecord y que no son extras nuestros.
# "color_message" lo añade uvicorn: es el mismo mensaje con códigos de escape
# ANSI para colorear la consola. En un log JSON solo es ruido ilegible.
_RESERVADOS = {
    "args", "asctime", "color_message", "created", "exc_info", "exc_text",
    "filename", "funcName", "levelname", "levelno", "lineno", "module", "msecs",
    "message", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
}

# El spec admite ERROR/WARN/INFO/DEBUG; logging emite WARNING.
_NIVELES = {"WARNING": "WARN", "CRITICAL": "ERROR"}


def _ahora() -> str:
    """ISO 8601 en UTC con precisión de milisegundos (spec §1)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"


def _truncar(valor, limite: int):
    """Recorta un texto largo y le añade "…". Deja intactos los no-texto."""
    if isinstance(valor, str) and len(valor) > limite:
        return valor[:limite] + "…"
    return valor


class JsonFormatter(logging.Formatter):
    """Formatea cada registro como una línea de JSON (spec §1)."""

    def format(self, record: logging.LogRecord) -> str:
        entrada = {
            "ts": _ahora(),
            "level": _NIVELES.get(record.levelname, record.levelname),
            "component": COMPONENT,
            "msg": _truncar(record.getMessage(), _MAX_MSG),
        }
        # Campos extra del que llama, ya en camelCase por convención del spec.
        for clave, valor in record.__dict__.items():
            if clave not in _RESERVADOS and not clave.startswith("_"):
                entrada[clave] = _truncar(valor, _MAX_MSG)
        if record.exc_info:
            # Sin traza completa: base/software-spec §5 pide no exponer stack
            # traces ni rutas internas en la salida de errores.
            exc = record.exc_info[1]
            entrada["errorType"] = type(exc).__name__
            entrada["errorMsg"] = _truncar(str(exc), _MAX_MSG)
        return json.dumps(entrada, ensure_ascii=False)


def configure_logging() -> None:
    """Deja el logging raíz emitiendo JSON a consola y a fichero rotativo.

    Se llama una sola vez, al importar webhook.py.
    """
    formatter = JsonFormatter()
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    for h in list(raiz.handlers):
        raiz.removeHandler(h)

    consola = logging.StreamHandler()
    consola.setFormatter(formatter)
    raiz.addHandler(consola)

    fichero = RotatingFileHandler(
        config.LOG_FILE, maxBytes=10_000_000, backupCount=5, encoding="utf-8",
    )
    fichero.setFormatter(formatter)
    raiz.addHandler(fichero)


def audit(op: str, args: dict, status: str = "OK", detail: str | None = None) -> None:
    """Escribe una entrada en el audit trail. Llamar ANTES de la operación.

    Args:
        op: identificador de la operación o tool (p.ej. "create_timeseries_bucket").
        args: parámetros de entrada. Los valores largos se truncan a 120 caracteres.
        status: "OK" (permitida, se va a ejecutar), "BLOCKED" (rechazada) o
                "ERROR" (no se ha podido intentar).
        detail: motivo, obligatorio en la práctica para BLOCKED y ERROR.

    Destino separado del log de aplicación (§2.3), en fichero para que
    sobreviva a los reinicios (§2.4). Nunca propaga excepciones: un fallo
    escribiendo la auditoría no debe tumbar la operación auditada, pero sí deja
    constancia en el log de aplicación.
    """
    entrada = {
        "ts": _ahora(),
        "op": op,
        "args": {k: _truncar(v, _MAX_ARG) for k, v in (args or {}).items()},
        "status": status,
    }
    if detail:
        entrada["detail"] = _truncar(detail, _MAX_MSG)
    try:
        destino = Path(config.AUDIT_FILE)
        destino.parent.mkdir(parents=True, exist_ok=True)
        with open(destino, "a", encoding="utf-8") as f:
            f.write(json.dumps(entrada, ensure_ascii=False) + "\n")
    except Exception as exc:
        # Captura amplia a propósito. Un fallo escribiendo la auditoría no debe
        # nunca tumbar la operación auditada, y las formas de fallar son más
        # que OSError: una ruta mal configurada da ValueError, un objeto no
        # serializable en args da TypeError. Que la auditoría falle es grave y
        # queda en el log de aplicación como ERROR, pero el análisis sigue.
        logging.getLogger(__name__).error(
            "No se pudo escribir en el audit trail",
            extra={"auditOp": op, "errorType": type(exc).__name__, "errorMsg": str(exc)},
        )

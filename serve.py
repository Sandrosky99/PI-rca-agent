"""
serve.py — Punto de entrada único del servidor

¿Por qué existe este fichero?
  Porque el puerto y los parámetros de uvicorn estaban repetidos en tres sitios
  —start.bat, install_service.bat y la documentación— y ya habían divergido:
  los .bat cableaban el 8090 ignorando WEBHOOK_PORT del .env, y usaban un
  --timeout-keep-alive distinto entre sí y distinto del documentado.

  Con esto, .env es la única fuente de verdad (base/software-spec §3) y los .bat
  se limitan a invocar este script.

Uso:
    .venv\\Scripts\\python.exe serve.py

  O, ya arrancado como servicio de Windows, es lo que NSSM ejecuta.
"""

import logging

import uvicorn

import config
import observability

# Los clientes HTTP de PI pueden tardar en cerrar la conexión; 30 s evita que
# uvicorn la corte antes de tiempo. Valor documentado en el README desde julio.
_KEEP_ALIVE = 30


def main() -> None:
    # El logging se configura antes de que uvicorn arranque para que también
    # sus mensajes salgan en JSON estructurado (observability-spec §1) y no en
    # el formato propio de uvicorn.
    observability.configure_logging()
    log = logging.getLogger(__name__)

    faltan = config.validate_config()
    if faltan:
        # Se arranca igualmente: el servidor puede recibir y registrar
        # notificaciones aunque no pueda llamar al modelo. Mejor eso que un
        # arranque fallido que deja a PI enviando a un puerto muerto.
        log.warning("Faltan variables obligatorias en .env; el análisis fallará.",
                    extra={"faltan": ", ".join(faltan)})

    log.info("Arrancando servidor.", extra={
        "port": config.WEBHOOK_PORT,
        "workflowEnabled": config.WORKFLOW_ENABLED,
        "provider": config.LLM_PROVIDER,
    })

    uvicorn.run(
        "webhook:app",
        # Escuchar en todas las interfaces es intencionado y necesario: PI
        # conecta desde 172.21.28.55, otra máquina de la red industrial, así
        # que 127.0.0.1 no serviría. Quién puede alcanzar el puerto es una
        # cuestión de firewall, no de a qué interfaz se enlaza -- ver
        # "DECISIONES DE SEGURIDAD" en webhook.py.
        # nosec B104 -- justificado arriba
        host="0.0.0.0",  # nosec B104
        port=config.WEBHOOK_PORT,
        timeout_keep_alive=_KEEP_ALIVE,
        # log_config=None: si no, uvicorn reinstala sus propios handlers y
        # deshace el formato JSON que acabamos de configurar.
        log_config=None,
        access_log=True,
    )


if __name__ == "__main__":
    main()

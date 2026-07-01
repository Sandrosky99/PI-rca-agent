@echo off
REM =============================================================================
REM start.bat — Arranca el servidor webhook del Agente RCA
REM =============================================================================
REM
REM ¿Qué hace este script?
REM   Activa el entorno virtual de Python y arranca el servidor webhook.
REM   El servidor quedará escuchando peticiones de PI System hasta que
REM   lo pares con Ctrl+C o cierres la terminal.
REM
REM ¿Cuándo ejecutarlo?
REM   Cada vez que quieras arrancar el agente manualmente.
REM   Para que arranque automáticamente con Windows, usa install_service.bat.
REM
REM URLs disponibles una vez arrancado:
REM   Recepción de alertas de PI:  http://localhost:8090/notification
REM   Comprobación de estado:      http://localhost:8090/health
REM   Documentación de la API:     http://localhost:8090/docs
REM
REM Nota: el puerto 8080 puede cambiarse en el fichero .env (variable WEBHOOK_PORT)
REM
REM =============================================================================

REM Comprobar que el entorno virtual existe (es decir, que se ejecutó setup.bat)
if not exist ".venv\Scripts\activate.bat" (
    echo ERROR: No se encuentra el entorno virtual.
    echo Ejecuta primero "setup.bat" para configurar el entorno.
    pause
    exit /b 1
)

REM Comprobar que existe el fichero de configuración
if not exist ".env" (
    echo ADVERTENCIA: No se encuentra el fichero ".env".
    echo Copia ".env.example" a ".env" y rellena tu ANTHROPIC_API_KEY.
    echo El servidor arrancara igualmente pero el agente no podra llamar a Claude.
    echo.
)

echo Arrancando servidor RCA Agent...
echo Pulsa Ctrl+C para detenerlo.
echo.

REM Usar directamente el uvicorn del entorno virtual (no requiere activar el venv)
.venv\Scripts\uvicorn.exe webhook:app --host 0.0.0.0 --port 8090 --log-level info --timeout-keep-alive 5

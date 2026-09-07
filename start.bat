@echo off
REM =============================================================================
REM start.bat - Arranca el servidor webhook del Workflow RCA (modo manual)
REM =============================================================================
REM
REM  Arranca el servidor en primer plano. Vive mientras esta ventana este
REM  abierta; se para con Ctrl+C.
REM
REM  Para que arranque solo con Windows y sobreviva al cierre de sesion, usa
REM  install_service.bat en su lugar. Esto es para desarrollo y pruebas.
REM
REM  El puerto y los parametros NO se fijan aqui: los lee serve.py de .env
REM  (WEBHOOK_PORT), que es la unica fuente de verdad. Antes estaban cableados
REM  en este fichero y habian divergido de la configuracion.
REM
REM =============================================================================

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: No se encuentra el entorno virtual.
    echo Ejecuta primero "setup.bat".
    pause
    exit /b 1
)

if not exist ".env" (
    echo ADVERTENCIA: No existe el fichero ".env".
    echo El servidor arrancara y registrara las notificaciones, pero el analisis
    echo fallara al no haber clave de API. Copia ".env.example" a ".env".
    echo.
)

echo Arrancando servidor RCA Workflow...
echo Pulsa Ctrl+C para detenerlo.
echo.

.venv\Scripts\python.exe serve.py

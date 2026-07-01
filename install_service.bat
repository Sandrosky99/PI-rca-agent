@echo off
REM =============================================================================
REM install_service.bat — Registra el Agente RCA como Windows Service
REM =============================================================================
REM
REM ¿Para qué sirve esto?
REM   Usando "start.bat" el servidor solo funciona mientras la terminal está
REM   abierta. Si cierras la terminal o reinicias el servidor, el agente se para.
REM
REM   Este script registra el agente como un "Servicio de Windows", de modo que:
REM     - Arranca automáticamente cuando se inicia Windows.
REM     - Corre en segundo plano sin necesidad de mantener ninguna terminal abierta.
REM     - Se puede controlar desde el panel de servicios (services.msc).
REM     - Se puede arrancar y parar con: sc start RCA-Agent-Webhook / sc stop RCA-Agent-Webhook
REM
REM ¿Qué necesitas antes de ejecutarlo?
REM   1. Haber ejecutado setup.bat al menos una vez (el .venv debe existir).
REM   2. Descargar NSSM (Non-Sucking Service Manager) — es gratuito y no requiere instalador:
REM        https://nssm.cc/download
REM      Descarga la versión win64, extrae el ZIP y copia "nssm.exe" en:
REM        C:\MCPServer\rca-agent\  (la misma carpeta que este script)
REM
REM ¿Cómo ejecutarlo?
REM   Abre una terminal como ADMINISTRADOR en esta carpeta y escribe:
REM       install_service.bat
REM
REM Para desinstalar el servicio en el futuro:
REM   nssm remove RCA-Agent-Webhook confirm
REM
REM =============================================================================

REM Verificar que NSSM está disponible
if not exist "nssm.exe" (
    echo ERROR: No se encuentra nssm.exe en esta carpeta.
    echo.
    echo Descarga NSSM desde https://nssm.cc/download
    echo Extrae el ZIP y copia el fichero "nssm.exe" ^(carpeta win64^) aqui:
    echo   C:\MCPServer\rca-agent\nssm.exe
    echo.
    pause
    exit /b 1
)

REM Verificar que el entorno virtual existe
if not exist "%BASE_DIR%\.venv\Scripts\uvicorn.exe" (
    echo ERROR: No se encuentra el entorno virtual o uvicorn.
    echo Ejecuta primero "setup.bat".
    pause
    exit /b 1
)

REM Definir las rutas absolutas que NSSM necesita
set SERVICE_NAME=RCA-Agent-Webhook
set BASE_DIR=C:\MCPServer\rca-agent
set UVICORN_EXE=%BASE_DIR%\.venv\Scripts\uvicorn.exe

echo Instalando servicio Windows "%SERVICE_NAME%"...

REM Registrar el servicio
REM   --host 0.0.0.0  → acepta conexiones desde cualquier IP de la red (incluido PI)
REM   --port 8090     → puerto en el que escucha (debe coincidir con WEBHOOK_PORT en .env)
nssm.exe install %SERVICE_NAME% "%UVICORN_EXE%" "webhook:app --host 0.0.0.0 --port 8090 --log-level info"

REM Configurar el directorio de trabajo (donde están webhook.py, config.py, etc.)
nssm.exe set %SERVICE_NAME% AppDirectory "%BASE_DIR%"

REM Configurar los ficheros de log del servicio
nssm.exe set %SERVICE_NAME% AppStdout "%BASE_DIR%\logs\service.log"
nssm.exe set %SERVICE_NAME% AppStderr "%BASE_DIR%\logs\service_error.log"
nssm.exe set %SERVICE_NAME% AppRotateFiles 1
nssm.exe set %SERVICE_NAME% AppRotateBytes 10485760

REM Crear la carpeta de logs si no existe
if not exist "%BASE_DIR%\logs" mkdir "%BASE_DIR%\logs"

REM Arrancar el servicio
echo.
echo Arrancando el servicio...
nssm.exe start %SERVICE_NAME%

echo.
echo =============================================================================
echo  Servicio "%SERVICE_NAME%" instalado y arrancado.
echo =============================================================================
echo.
echo  Para verificar que esta corriendo: abre services.msc y busca "%SERVICE_NAME%"
echo  Para ver los logs:  %BASE_DIR%\logs\service.log
echo  Para parar:         sc stop %SERVICE_NAME%
echo  Para arrancar:      sc start %SERVICE_NAME%
echo  Para desinstalar:   nssm.exe remove %SERVICE_NAME% confirm
echo.
pause

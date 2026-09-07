@echo off
setlocal enabledelayedexpansion
REM =============================================================================
REM install_service.bat - Registra el Workflow RCA como servicio de Windows
REM =============================================================================
REM
REM  POR QUE HACE FALTA
REM    Con start.bat el servidor solo vive mientras la terminal este abierta. Si
REM    se cierra la sesion o se reinicia la maquina, PI se queda enviando
REM    notificaciones a un puerto muerto y nadie se entera.
REM
REM  QUE NECESITAS ANTES
REM    1. Haber ejecutado setup.bat (debe existir .venv).
REM    2. nssm.exe en esta misma carpeta. Si no esta, este script te dice como
REM       obtenerlo. NSSM es un envoltorio que convierte cualquier ejecutable en
REM       un servicio de Windows; uvicorn por si solo no habla el protocolo de
REM       servicios, por eso hace falta.
REM    3. Ejecutar ESTA VENTANA COMO ADMINISTRADOR.
REM
REM  COMO SE EJECUTA
REM    Boton derecho sobre "Simbolo del sistema" -> "Ejecutar como
REM    administrador", ir a esta carpeta y escribir:
REM        install_service.bat
REM
REM  PARA DESINSTALARLO
REM        nssm.exe remove RCA-Workflow-Webhook confirm
REM
REM  NOTA SOBRE EL PUERTO
REM    Este script NO fija el puerto. Lo lee serve.py de WEBHOOK_PORT (.env), que
REM    es la unica fuente de verdad. Antes estaba cableado aqui y en start.bat, y
REM    habia divergido de la configuracion.
REM =============================================================================

set SERVICE_NAME=RCA-Workflow-Webhook
REM %~dp0 es la carpeta de este script, con barra final. Asi el script funciona
REM desde cualquier ubicacion, no solo desde C:\MCPServer\rca-agent.
set BASE_DIR=%~dp0
if "%BASE_DIR:~-1%"=="\" set BASE_DIR=%BASE_DIR:~0,-1%
set PYTHON_EXE=%BASE_DIR%\.venv\Scripts\python.exe

echo.
echo === Comprobaciones previas ===

REM --- Administrador ---------------------------------------------------------
REM Sin esto NSSM falla con un error poco claro a mitad de la instalacion.
net session >nul 2>&1
if errorlevel 1 (
    echo   [ERROR] Esta ventana NO tiene permisos de administrador.
    echo           Registrar un servicio de Windows los requiere.
    echo.
    echo           Cierra esta ventana, abre "Simbolo del sistema" con boton
    echo           derecho -^> "Ejecutar como administrador", vuelve a esta
    echo           carpeta y repite.
    echo.
    pause
    exit /b 1
)
echo   [OK] Permisos de administrador

REM --- Entorno virtual -------------------------------------------------------
REM OJO: esta comprobacion usaba %BASE_DIR% antes de definirla, asi que la ruta
REM quedaba vacia y el script abortaba SIEMPRE con "no se encuentra el entorno
REM virtual", incluso teniendolo. Por eso el servicio nunca llego a registrarse.
if not exist "%PYTHON_EXE%" (
    echo   [ERROR] No se encuentra %PYTHON_EXE%
    echo           Ejecuta primero setup.bat.
    pause
    exit /b 1
)
echo   [OK] Entorno virtual

REM --- NSSM ------------------------------------------------------------------
if not exist "%BASE_DIR%\nssm.exe" (
    echo   [ERROR] No se encuentra nssm.exe en esta carpeta.
    echo.
    echo           Descargalo de https://nssm.cc/download , abre el ZIP y copia
    echo           win64\nssm.exe aqui:
    echo             %BASE_DIR%\nssm.exe
    echo.
    pause
    exit /b 1
)
echo   [OK] nssm.exe

REM --- Configuracion ---------------------------------------------------------
if not exist "%BASE_DIR%\.env" (
    echo   [AVISO] No existe .env. El servidor arrancara, pero el analisis
    echo           fallara al no haber clave de API. Copia .env.example a .env.
) else (
    echo   [OK] .env presente
)

REM --- Puerto libre ----------------------------------------------------------
REM Si hay un servidor arrancado a mano, el servicio no podra abrir el puerto y
REM se quedara reiniciandose en bucle.
for /f "tokens=2 delims==" %%p in ('findstr /b /c:"WEBHOOK_PORT=" "%BASE_DIR%\.env" 2^>nul') do set PORT=%%p
if "%PORT%"=="" set PORT=8090
netstat -ano | findstr /r /c:":%PORT% .*LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo   [ERROR] El puerto %PORT% ya esta ocupado.
    echo           Hay un servidor arrancado a mano. Cierralo antes de instalar
    echo           el servicio, o los dos pelearan por el mismo puerto.
    echo.
    netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"
    echo.
    pause
    exit /b 1
)
echo   [OK] Puerto %PORT% libre

REM --- Servicio ya existente -------------------------------------------------
sc query %SERVICE_NAME% >nul 2>&1
if not errorlevel 1 (
    echo.
    echo   [AVISO] El servicio "%SERVICE_NAME%" ya existe. Se reinstala.
    "%BASE_DIR%\nssm.exe" stop %SERVICE_NAME% >nul 2>&1
    "%BASE_DIR%\nssm.exe" remove %SERVICE_NAME% confirm >nul 2>&1
    timeout /t 2 /nobreak >nul
)

REM =============================================================================
echo.
echo === Instalando "%SERVICE_NAME%" ===

REM Se registra serve.py, no uvicorn directamente: asi el puerto y los
REM parametros salen de .env y no quedan cableados en este fichero.
"%BASE_DIR%\nssm.exe" install %SERVICE_NAME% "%PYTHON_EXE%" "%BASE_DIR%\serve.py"
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% AppDirectory "%BASE_DIR%"
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% DisplayName "PI RCA Workflow - Webhook"
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% Description "Recibe notificaciones de AVEVA PI System y ejecuta el workflow de analisis de causa raiz."
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% Start SERVICE_AUTO_START

REM Salida del proceso. El log de la aplicacion va aparte (webhook.log, en JSON);
REM esto captura lo que uvicorn escriba antes de que ese logging exista, y
REM cualquier traza de un fallo de arranque.
if not exist "%BASE_DIR%\logs" mkdir "%BASE_DIR%\logs"
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% AppStdout "%BASE_DIR%\logs\service.log"
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% AppStderr "%BASE_DIR%\logs\service_error.log"
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% AppRotateFiles 1
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% AppRotateBytes 10485760

REM Reinicio automatico ante caida, con espera creciente para no entrar en bucle
REM cerrado si el fallo es permanente (p.ej. .env mal configurado).
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% AppExit Default Restart
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% AppRestartDelay 5000
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% AppThrottle 10000

REM Al parar el servicio, dar tiempo a que un analisis en vuelo termine de
REM escribir su fichero de incidente antes de matar el proceso.
"%BASE_DIR%\nssm.exe" set %SERVICE_NAME% AppStopMethodConsole 15000

echo.
echo === Arrancando ===
"%BASE_DIR%\nssm.exe" start %SERVICE_NAME%
timeout /t 5 /nobreak >nul

sc query %SERVICE_NAME% | findstr /c:"RUNNING" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   [ERROR] El servicio no ha llegado a arrancar.
    echo           Mira %BASE_DIR%\logs\service_error.log
    echo.
    sc query %SERVICE_NAME%
    pause
    exit /b 1
)

echo.
echo =============================================================================
echo   Servicio "%SERVICE_NAME%" instalado y EN MARCHA.
echo =============================================================================
echo.
echo   Comprueba:      curl http://localhost:%PORT%/health
echo   Panel:          services.msc  ^(busca "PI RCA Workflow"^)
echo   Log del workflow: %BASE_DIR%\webhook.log      ^(JSON, una linea por evento^)
echo   Log del servicio: %BASE_DIR%\logs\service.log ^(arranque y caidas^)
echo   Parar:          sc stop %SERVICE_NAME%
echo   Arrancar:       sc start %SERVICE_NAME%
echo   Desinstalar:    nssm.exe remove %SERVICE_NAME% confirm
echo.
echo   Arranque automatico con Windows: ACTIVADO
echo.
pause

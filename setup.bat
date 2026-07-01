@echo off
REM =============================================================================
REM setup.bat — Configura el entorno del Agente RCA
REM =============================================================================
REM
REM ¿Qué hace este script?
REM   1. Crea un entorno virtual de Python (.venv) en esta misma carpeta.
REM      Un entorno virtual es una copia aislada de Python con sus propias
REM      librerías, de modo que no interfiere con otros proyectos del servidor.
REM   2. Instala todas las librerías necesarias (definidas en requirements.txt).
REM
REM ¿Cuándo ejecutarlo?
REM   Solo la PRIMERA VEZ, o si borras la carpeta .venv y quieres reconstruirla.
REM   No es necesario ejecutarlo cada vez que arranques el servidor.
REM
REM ¿Cómo ejecutarlo?
REM   Abre una terminal en esta carpeta (C:\MCPServer\rca-agent) y escribe:
REM       setup.bat
REM
REM =============================================================================

echo.
echo [1/3] Comprobando uv (gestor de entornos Python)...
uv --version
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: uv no encontrado. Este servidor usa uv para gestionar Python.
    echo Instala uv desde: https://docs.astral.sh/uv/getting-started/installation/
    pause
    exit /b 1
)

echo.
echo [2/3] Creando entorno virtual en .venv con Python 3.11 ...
uv venv --python 3.11 .venv
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: No se pudo crear el entorno virtual.
    pause
    exit /b 1
)
echo     Entorno virtual creado correctamente.

echo.
echo [3/3] Instalando dependencias desde requirements.txt ...
uv pip install -r requirements.txt
if %ERRORLEVEL% NEQ 0 (
    echo ERROR: Fallo al instalar las dependencias.
    pause
    exit /b 1
)

echo.
echo =============================================================================
echo  Setup completado correctamente.
echo =============================================================================
echo.
echo  Proximos pasos:
echo    1. Copia el fichero ".env.example" y renombralo ".env"
echo    2. Abre ".env" y rellena tu ANTHROPIC_API_KEY
echo    3. Ejecuta "start.bat" para arrancar el servidor
echo.
pause

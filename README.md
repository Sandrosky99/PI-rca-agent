# PI RCA Agent — Agente de Análisis de Causa Raíz

Agente de inteligencia artificial que diagnostica automáticamente la causa raíz de alertas operacionales generadas por **AVEVA PI System**. Cuando PI detecta una desviación en un parámetro monitorizado, el agente se activa, consulta datos históricos y produce un diagnóstico con posibles causas y recomendaciones de acción.

---

## Índice

- [¿Qué problema resuelve?](#qué-problema-resuelve)
- [Arquitectura general](#arquitectura-general)
- [Flujo de funcionamiento](#flujo-de-funcionamiento)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Requisitos previos](#requisitos-previos)
- [Instalación](#instalación)
- [Configuración](#configuración)
- [Uso](#uso)
- [Endpoints de la API](#endpoints-de-la-api)
- [Integración con PI System](#integración-con-pi-system)
- [Despliegue en producción](#despliegue-en-producción)
- [Estado actual del desarrollo](#estado-actual-del-desarrollo)

---

## ¿Qué problema resuelve?

En entornos de producción industrial, cuando se produce una alerta operacional (temperatura fuera de rango, caudal anómalo, nivel crítico…) el operador debe investigar manualmente qué causó la desviación consultando múltiples variables de proceso en diferentes sistemas.

Este agente automatiza ese proceso de investigación:

1. **Recibe** la alerta directamente de PI System en tiempo real.
2. **Analiza** el contexto del evento con un modelo de lenguaje (Claude de Anthropic).
3. **Consulta** los datos históricos necesarios en PI a través del MCP Server.
4. **Produce** un diagnóstico con las causas raíz más probables y recomendaciones.

El operador pasa de investigar manualmente durante minutos u horas a recibir un diagnóstico estructurado en segundos.

---

## Arquitectura general

```
┌─────────────────────────────────────────────────────────────────┐
│                        AVEVA PI System                          │
│  (PI Notifications → detecta desviación → envía HTTP POST)     │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP POST
                               │ {"KPIName": "Level Alert"}
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                    PI RCA Agent (este proyecto)                  │
│                                                                 │
│  webhook.py          agent.py              config.py            │
│  ┌──────────┐       ┌──────────────────┐  ┌─────────────────┐  │
│  │ FastAPI  │──────▶│  Agente RCA      │  │ Variables de    │  │
│  │ :8090    │       │                  │  │ entorno (.env)  │  │
│  │/notific. │       │ Step 2: Contexto │  └─────────────────┘  │
│  └──────────┘       │ Step 3: Claude ◀─┼──▶ Anthropic API      │
│                     │ Step 4: Datos  ◀─┼──▶ MCP Server PI      │
│                     │ Step 5: Diagnós. │                        │
│                     └──────────────────┘                        │
└─────────────────────────────────────────────────────────────────┘
                               │
              ┌────────────────┴──────────────────┐
              ▼                                   ▼
┌─────────────────────────┐         ┌─────────────────────────────┐
│   aveva-pi-mcp          │         │   afkg-graph-mcp            │
│   (MCP Server PI)       │         │   (MCP Server Grafo AF)     │
│   C:\MCPServer\         │         │   C:\MCPServer\             │
│   MCP Server\           │         │   afkg-graph-mcp\           │
│                         │         │                             │
│ · create_timeseries_    │         │ · graph_search              │
│   bucket                │         │ · graph_neighborhood        │
│ · query_by_path         │         │   → devuelve piApiPath      │
│ · search_event_frames   │         └─────────────────────────────┘
└─────────────────────────┘
              │
              ▼
┌─────────────────────────┐
│   PI Web API            │
│   https://datainfra/    │
│   piwebapi              │
│                         │
│ · Series temporales     │
│ · EventFrames           │
│ · Atributos AF          │
└─────────────────────────┘
```

---

## Flujo de funcionamiento

El agente sigue 5 pasos desde que llega la alerta hasta que produce el diagnóstico:

### Step 1 — Recibir la notificación de PI ✅ *Implementado*

PI System envía un HTTP POST al webhook del agente cuando detecta una desviación:

```
POST http://172.21.28.72:8090/notification
Content-Type: application/json

{"KPIName": "Level Alert"}
```

El servidor responde `202 Accepted` inmediatamente y activa el análisis en segundo plano, de modo que PI no queda bloqueado esperando.

### Step 2 — Preparar el contexto para Claude 🔲 *Pendiente*

Con el nombre del KPI recibido, el agente construye un mensaje estructurado para Claude que incluye:
- Tipo de alerta y asset afectado
- Valor que disparó la alerta y umbral configurado
- Momento del evento
- Instrucciones sobre el formato de respuesta esperado

### Step 3 — Claude identifica las variables a analizar 🔲 *Pendiente*

Claude analiza el contexto y devuelve una lista estructurada de los atributos de PI que necesita consultar para determinar la causa raíz. Por ejemplo:
- Temperatura del fluido de entrada
- Caudal de refrigeración
- Posición de la válvula de control

### Step 4 — Obtener datos históricos de PI 🔲 *Pendiente*

Con la lista de atributos del Step 3, el agente consulta el MCP Server `aveva-pi-mcp` para obtener las series temporales históricas en la ventana temporal alrededor del evento.

### Step 5 — Diagnóstico final 🔲 *Pendiente*

Claude analiza los datos históricos y produce:
- Lista de posibles causas raíz ordenadas por probabilidad
- Explicación de por qué cada causa es plausible
- Recomendaciones de acción para cada causa

---

## Estructura del proyecto

```
PI-rca-agent/
│
├── webhook.py           ← Servidor HTTP (Step 1): recibe alertas de PI System
├── agent.py             ← Lógica del agente RCA (Steps 2–5)
├── config.py            ← Carga y validación de variables de entorno
│
├── .env.example         ← Plantilla de configuración (copia a .env y rellena)
├── .env                 ← Configuración real con credenciales (NO en git)
├── .gitignore           ← Excluye .env, .venv, __pycache__
├── requirements.txt     ← Dependencias Python del proyecto
│
├── setup.bat            ← Instala el entorno virtual (ejecutar solo 1 vez)
├── start.bat            ← Arranca el servidor webhook
└── install_service.bat  ← Registra el agente como Windows Service (NSSM)
```

### Descripción de cada fichero

| Fichero | Función |
|---|---|
| `webhook.py` | Servidor FastAPI que escucha en `:8090/notification`. Recibe el POST de PI, lo registra, y lanza `agent.run_rca_analysis()` en segundo plano. También expone `/health` y `/notifications/history` para diagnóstico. |
| `agent.py` | Contiene `run_rca_analysis()`. Actualmente es un stub documentado con los TODOs de los Steps 2–5. Aquí se implementará la lógica de Claude API y la consulta a los MCP Servers. |
| `config.py` | Lee el fichero `.env` y expone las variables como constantes Python. La función `validate_config()` comprueba que las variables obligatorias están presentes al arrancar. |
| `requirements.txt` | Declara las dependencias: `fastapi`, `uvicorn`, `anthropic`, `python-dotenv`, `pytz`. |
| `setup.bat` | Crea el entorno virtual `.venv` con `uv` e instala las dependencias. Solo hace falta ejecutarlo una vez. |
| `start.bat` | Activa el entorno virtual y arranca `uvicorn` en el puerto 8090. Para uso en desarrollo o pruebas. |
| `install_service.bat` | Usa NSSM para registrar el servidor como Windows Service. Para uso en producción: el agente arranca con Windows sin necesidad de mantener una terminal abierta. |

---

## Requisitos previos

- **Python 3.11+** gestionado con [uv](https://docs.astral.sh/uv/) (ya instalado en el servidor)
- **Windows Server 2019** (o cualquier Windows con Python)
- Acceso de red a **PI Web API** (`https://datainfra/piwebapi`)
- **AVEVA PI System** con el módulo **PI Notifications** configurado
- Clave de API de **Anthropic** (para los Steps 2–5)
- Los servidores MCP `aveva-pi-mcp` y `afkg-graph-mcp` corriendo en la misma máquina (para los Steps 3–5)

---

## Instalación

```cmd
:: 1. Clona el repositorio
git clone https://github.com/Sandrosky99/PI-rca-agent.git
cd PI-rca-agent

:: 2. Crea el entorno virtual e instala dependencias
setup.bat

:: 3. Crea el fichero de configuración
copy .env.example .env
```

Edita `.env` y rellena al menos la clave de Anthropic:

```
ANTHROPIC_API_KEY=sk-ant-tu-clave-aqui
```

---

## Configuración

Todas las opciones se configuran en el fichero `.env`. Copia `.env.example` como punto de partida:

| Variable | Obligatoria | Descripción | Por defecto |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ Sí | Clave de API de Anthropic para llamar a Claude | — |
| `WEBHOOK_PORT` | No | Puerto en el que escucha el servidor | `8090` |
| `WEBHOOK_SECRET` | No | Token para validar que las peticiones vienen de PI. PI debe enviarlo en la cabecera `X-PI-Secret`. Si está vacío no se valida el origen. | vacío |
| `PI_LOCAL_TIMEZONE` | No | Zona horaria para mostrar timestamps en el log | `Europe/Madrid` |

---

## Uso

### Arrancar el servidor (desarrollo)

```cmd
start.bat
```

El servidor queda escuchando. Verás en la consola:

```
Servidor RCA Agent arrancado y escuchando en:
  http://0.0.0.0:8090/notification  <- PI envia aqui sus alertas
  http://localhost:8090/health       <- comprobacion de estado
  http://localhost:8090/docs         <- documentacion de la API
```

### Verificar que funciona

```powershell
# Desde cualquier terminal del servidor
Invoke-RestMethod http://localhost:8090/health
# → {"status": "ok", "timestamp": "...", "service": "rca-agent-webhook"}
```

### Simular una notificación de PI (para pruebas)

```powershell
Invoke-RestMethod -Uri "http://localhost:8090/notification" `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"KPIName": "Level Alert"}'
# → {"status": "accepted", "message": "...", "received_at": "..."}
```

### Ver el historial de notificaciones recibidas

```powershell
Invoke-RestMethod http://localhost:8090/notifications/history
```

---

## Endpoints de la API

La documentación interactiva completa está disponible en `http://localhost:8090/docs` (Swagger UI generado automáticamente por FastAPI).

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/health` | Estado del servidor. Devuelve `200 OK` si está en marcha. |
| `POST` | `/notification` | Recibe alertas de PI System. Acepta cualquier JSON. Devuelve `202 Accepted`. |
| `GET` | `/notifications/history` | Lista las últimas 50 notificaciones recibidas (en memoria, se borra al reiniciar). |

---

## Integración con PI System

### Configuración en PI Notifications

En **PI System Explorer → Notifications**, configura el canal de entrega HTTP con estos parámetros:

| Parámetro | Valor |
|---|---|
| **URL** | `http://172.21.28.72:8090/notification` ⚠️ usar `http://`, no `https://` |
| **Método** | `POST` |
| **Content-Type** | `application/json` |
| **Body** | `{"KPIName": "{KPIName}"}` (o el template que uses en PI) |

> **Nota importante:** El servidor usa HTTP plano (no HTTPS). Asegúrate de que la URL en PI empieza por `http://` y no por `https://`. En una red industrial interna esto es habitual y no supone un riesgo de seguridad significativo.

### Payload que envía PI

PI System envía un JSON mínimo con el nombre del KPI que disparó la alerta:

```json
{"KPIName": "Level Alert"}
```

En los próximos pasos del desarrollo se ampliará este payload o se complementará con datos del grafo AF para enriquecer el contexto del análisis.

---

## Despliegue en producción

Para que el agente arranque automáticamente con Windows y corra en segundo plano sin necesidad de mantener una terminal abierta, se usa **NSSM** (Non-Sucking Service Manager):

1. Descarga NSSM desde [nssm.cc/download](https://nssm.cc/download) (gratuito, sin instalador)
2. Copia `nssm.exe` (carpeta `win64`) a `C:\MCPServer\rca-agent\`
3. Abre una terminal como **Administrador** en esa carpeta
4. Ejecuta:
   ```cmd
   install_service.bat
   ```

El servicio aparecerá en `services.msc` como **RCA-Agent-Webhook** y arrancará automáticamente con el servidor.

Comandos de control del servicio:

```cmd
sc start RCA-Agent-Webhook   :: arrancar
sc stop  RCA-Agent-Webhook   :: parar
sc query RCA-Agent-Webhook   :: ver estado
```

Logs del servicio:
```
C:\MCPServer\rca-agent\logs\service.log
C:\MCPServer\rca-agent\logs\service_error.log
```

---

## Estado actual del desarrollo

| Step | Descripción | Estado |
|---|---|---|
| 1 | Recibir notificación HTTP POST de PI System | ✅ Completado |
| 2 | Preparar contexto estructurado para Claude | 🔲 Pendiente |
| 3 | Claude identifica variables a consultar en PI | 🔲 Pendiente |
| 4 | Obtener datos históricos de PI vía MCP Server | 🔲 Pendiente |
| 5 | Claude produce diagnóstico y recomendaciones | 🔲 Pendiente |

---

## Servidores MCP relacionados

Este agente se integra con dos servidores MCP que deben estar corriendo en la misma máquina:

| Servidor | Ruta | Función |
|---|---|---|
| `aveva-pi-mcp` | `C:\MCPServer\MCP Server\` | Consulta series temporales en PI Web API |
| `afkg-graph-mcp` | `C:\MCPServer\afkg-graph-mcp\` | Exploración del grafo de activos AF (piApiPath) |

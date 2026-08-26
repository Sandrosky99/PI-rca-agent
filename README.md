# PI RCA — Diagnóstico automático de causa raíz para AVEVA PI System

Workflow que diagnostica la causa raíz de alertas operacionales generadas por **AVEVA PI System**. Cuando PI detecta que un KPI se sale de su umbral, el sistema recibe la notificación, averigua qué variables existen realmente en el Asset Framework, consulta sus datos históricos y produce un diagnóstico con 2–3 causas raíz y su acción correctiva.

> **Nota sobre el nombre.** El repositorio se llama `PI-rca-agent` y el módulo principal es `agent.py`, pero **esto es un workflow, no un agente**. El control de flujo vive entero en código Python; al modelo se le consulta dos veces y no dirige el proceso. Ver [¿Workflow o agente?](#workflow-o-agente) — la distinción importa al leer el código.

---

## Índice

- [¿Qué problema resuelve?](#qué-problema-resuelve)
- [¿Workflow o agente?](#workflow-o-agente)
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
- [Servidores MCP relacionados](#servidores-mcp-relacionados)

---

## ¿Qué problema resuelve?

En una planta de tratamiento de aguas residuales, cuando salta una alerta operacional (eficiencia hidráulica por debajo del mínimo, caudal anómalo, nivel crítico…) el ingeniero de procesos debe investigar manualmente qué la causó, consultando decenas de variables en PI y cruzándolas a mano.

Este sistema automatiza esa investigación:

1. **Recibe** la alerta directamente de PI System en tiempo real.
2. **Consulta** la estructura real del Asset Framework para saber qué variables existen y cuál es su ruta exacta en PI Web API.
3. **Pregunta** al modelo cuáles de esas variables hacen falta para diagnosticar esta desviación concreta.
4. **Obtiene** las series temporales de esas variables en la ventana previa a la alerta.
5. **Produce** un diagnóstico con las causas raíz más probables, la evidencia que las sustenta y una acción concreta para cada una.

El ingeniero pasa de investigar durante minutos u horas a recibir un diagnóstico estructurado en segundos.

---

## ¿Workflow o agente?

Merece la pena tener clara la distinción antes de leer el código, porque los nombres del proyecto inducen a buscar un bucle de decisión que no existe.

- **Workflow** — el LLM y las herramientas se orquestan a través de rutas de código predefinidas. La secuencia la fija el programador y es la misma en cada ejecución. *Esto es lo que hay aquí.*
- **Agente** — el LLM dirige su propio proceso y su propio uso de herramientas, y mantiene el control sobre cómo cumple la tarea.

| Criterio | Este sistema | Lo que haría un agente |
|---|---|---|
| Quién decide el siguiente paso | `run_rca_analysis()`: los 6 steps están escritos en orden en el cuerpo de la función | El modelo, en cada vuelta de un bucle |
| Llamadas al modelo | Exactamente 2, en posiciones fijas (Steps 4 y 6) | Un número indeterminado |
| Acceso a herramientas | **Ninguno.** `llm_client.generate()` nunca pasa un parámetro `tools` | El modelo recibe las tools y decide cuál invocar |
| Quién llama a los MCP servers | El código Python (`graph_client.py`, `pi_client.py`) | El modelo, vía *tool use* |
| Reintentos y recuperación | Código determinista (`tenacity`, `_query_with_retry()`) | El modelo observa el error y decide qué probar |
| Condición de parada | La última línea de la función | El modelo decide cuándo ha terminado |

**El matiz:** el modelo *sí* decide algo — en el Step 4 elige qué variables consultar, y eso cambia qué datos se piden a PI. Pero cambia el *contenido* de un paso que se iba a ejecutar igualmente, no el *orden* ni la *existencia* de los pasos. Eso es selección de datos dentro de una ruta fija, no autonomía.

El patrón es **encadenamiento de prompts** (*prompt chaining*), con una puerta de código entre las dos llamadas que valida, deduplica y filtra. Es el patrón adecuado aquí: la tarea está bien definida y su secuencia se conoce de antemano, así que no hay razón para pagar la latencia, el coste y la impredecibilidad de un bucle autónomo.

---

## Arquitectura general

```
┌─────────────────────────────────────────────────────────────────┐
│                        AVEVA PI System                          │
│    PI Notifications → detecta desviación → envía HTTP POST      │
└──────────────────────────────┬──────────────────────────────────┘
                               │ HTTP POST (payload completo del KPI)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     PI RCA (este proyecto)                      │
│                                                                 │
│  webhook.py ──▶ agent.py : run_rca_analysis()                   │
│  FastAPI        ┌─────────────────────────────────────────┐     │
│  :8090          │ Step 2 · graph_client.py ───────────────┼──┐  │
│  /notification  │ Step 3 · build_analysis_context()       │  │  │
│                 │ Step 4 · llm_client.generate()  ────────┼──┼──┼──▶ Gemini
│                 │ Step 5 · pi_client.py ──────────────────┼──┼─▶│    o Claude
│                 │ Step 6 · llm_client.generate()  ────────┼──┼──┘  │
│                 └─────────────────────────────────────────┘  │     │
│                                                              │     │
│  config.py · variables de entorno (.env)                     │     │
└──────────────────────────────────────────────────────────────┼─────┘
                    ┌─────────────────────────────────────────┘
                    ▼                              ▼
┌───────────────────────────────┐   ┌──────────────────────────────┐
│  afkg-graph-mcp    (Step 2)   │   │  aveva-pi-mcp      (Step 5)  │
│  C:\MCPServer\afkg-graph-mcp\ │   │  C:\MCPServer\MCP Server\    │
│                               │   │                              │
│  · graph_neighborhood         │   │  · create_timeseries_bucket  │
│    → estructura del AF        │   │  · query_by_path             │
│    → piApiPath de cada        │   │  · search_event_frames       │
│      atributo                 │   │                              │
└───────────────┬───────────────┘   └───────────────┬──────────────┘
                │                                   │
                ▼                                   ▼
    ┌──────────────────────┐            ┌──────────────────────────┐
    │  Grafo AF (Neo4j)    │            │  PI Web API              │
    │  estructura de       │            │  https://datainfra/      │
    │  activos y atributos │            │  piwebapi                │
    └──────────────────────┘            └──────────────────────────┘
```

El `piApiPath` es la moneda de cambio entre los dos servidores MCP: el Step 2 lo extrae del grafo, el modelo lo copia literalmente en el Step 4, y el Step 5 lo pasa directamente a `query_by_path`.

---

## Flujo de funcionamiento

Seis pasos desde que llega la alerta hasta el diagnóstico. Los seis están implementados.

### Step 1 — Recibir la notificación de PI ✅

PI System envía un HTTP POST al webhook cuando detecta una desviación:

```
POST http://172.21.28.72:8090/notification
Content-Type: application/json

{
  "KPIName":            "Hydraulic Efficiency",
  "Asset":              "PS20102 A03 PS02 Pump 02",
  "Subsystem":          "Pumping Station 01",
  "System":             "External Pumping",
  "Plant":              "WWTP",
  "KPI":                60.0,
  "Limit":              70.0,
  "LimitThresholdType": "Low",
  "StartTime":          "2026-07-02T09:15:47Z"
}
```

El servidor responde `202 Accepted` inmediatamente y lanza el análisis en segundo plano, de modo que PI no queda bloqueado esperando. Los campos se validan por tipo antes de usarlos: si PI envía un valor con un tipo inesperado (ha pasado en real con `Limit`), se omite del mensaje al modelo y se registra un WARNING, en vez de propagar un dato roto.

### Step 2 — Consultar la estructura real del Asset Framework ✅

El sistema lanza `afkg-graph-mcp` y recorre recursivamente el árbol del activo afectado y del subsistema que lo agrupa. El resultado son dos listas planas:

- `main_asset_context` — el árbol completo del activo de la alerta: sensores, transmisores, indicadores calculados, alarmas.
- `nearby_elements_context` — el árbol del subsistema y sus otros activos hermanos, para comparar o descartar causas fuera del activo principal.

Cada elemento trae sus atributos con nombre, unidad, descripción y el `piApiPath` exacto. Se filtran los atributos de bookkeeping (`Area Code`, `Tag Name`, `Manufacturer`…) que no son variables de proceso.

**Por qué existe este paso:** sin él, el modelo proponía variables «de libro» (`Discharge Flow Rate`) que no existen con ese naming en el AF real, y no había forma de saberlo hasta que la consulta fallaba. Dándole la lista cerrada de lo que existe, elige solo entre variables reales.

### Step 3 — Construir el mensaje para el modelo ✅

Se compone un mensaje de cuatro secciones: objetivo de la interacción, resumen de la alerta en lenguaje natural, explicación del modelo de datos, y el JSON del AF. Va acompañado siempre del mismo *system prompt*, que fija el rol y el dominio (depuración de aguas residuales y bombeo) para que el modelo nunca responda como un asistente genérico.

El objetivo incluye una jerarquía de prioridad explícita: primero el KPI en alerta y su baseline, luego otros indicadores del mismo activo, luego los medidores físicos que los alimentan, y solo después el nivel superior.

### Step 4 — El modelo elige las variables a analizar ✅

Primera llamada al LLM. Devuelve un JSON con dos claves:

```json
{
  "variables": [
    {"element": "PS20102 A03 PS02 Pump 02 > Indicators > Hydraulic Efficiency",
     "piApiPath": "\\\\...\\Hydraulic Efficiency|Calculation"}
  ],
  "missing_variables": [
    "Vibración del rodamiento del lado de acoplamiento, para descartar desalineación"
  ]
}
```

`missing_variables` es informativo: describe qué variable adicional ayudaría al diagnóstico pero no existe en el AF. No se usa para consultar PI, pero sí se arrastra al Step 6 como limitación declarada, para que el modelo module su confianza en vez de inventar datos.

### Step 5 — Obtener los datos históricos de PI ✅

Se lanza `aveva-pi-mcp`, se crea un bucket temporal y se consultan todos los paths en una sola llamada:

- **Ventana:** 24 h hacia atrás desde la detección de la alerta (configurable con `PI_LOOKBACK_HOURS`). Se mira siempre hacia atrás: son degradaciones graduales de KPI, no picos instantáneos.
- **Resolución:** 15 minutos, uniforme para todas las variables, para que los valores sean correlacionables directamente por timestamp.

Si algún `piApiPath` no resuelve a WebID (existe en el grafo pero ya no en PI Web API), se excluye y se reintenta con el resto en vez de perder el lote entero.

### Step 6 — Diagnóstico final ✅

Segunda y última llamada al LLM, con las series ya etiquetadas por elemento y unidad. Devuelve:

```json
{
  "root_causes": [
    {
      "cause": "Desgaste interno de la bomba",
      "explanation": "La eficiencia hidráulica cae de forma monótona durante las últimas 14 h mientras el caudal se mantiene estable...",
      "recommended_action": "Programar inspección del impulsor y los anillos de desgaste; medir holguras internas contra las tolerancias del fabricante."
    }
  ]
}
```

Entre 2 y 3 causas, ordenadas de mayor a menor probabilidad. El prompt pide explícitamente que no rellene hasta 3 si los datos solo sustentan una o dos con confianza razonable.

> ⚠️ **El diagnóstico solo se escribe en el log.** Falta decidir el canal de salida real (correo, interfaz web, o anotación del event frame en PI). Es la decisión pendiente principal del proyecto.

---

## Estructura del proyecto

```
PI-rca-agent/
│
├── webhook.py           ← Servidor HTTP (Step 1): recibe alertas de PI System
├── agent.py             ← Orquestación del workflow + Steps 3, 4 y 6
├── graph_client.py      ← Cliente MCP para afkg-graph-mcp (Step 2)
├── pi_client.py         ← Cliente MCP para aveva-pi-mcp (Step 5)
├── llm_client.py        ← Abstracción sobre el proveedor de LLM (Gemini/Anthropic)
├── config.py            ← Carga y validación de variables de entorno
│
├── .env.example         ← Plantilla de configuración (copia a .env y rellena)
├── .env                 ← Configuración real con credenciales (NO en git)
├── .gitignore           ← Excluye .env, .venv, __pycache__
├── requirements.txt     ← Dependencias Python del proyecto
│
├── setup.bat            ← Instala el entorno virtual (ejecutar solo 1 vez)
├── start.bat            ← Arranca el servidor webhook
└── install_service.bat  ← Registra el servicio en Windows (NSSM)
```

### Descripción de cada fichero

| Fichero | Función |
|---|---|
| `webhook.py` | Servidor FastAPI que escucha en `:8090/notification`. Recibe el POST de PI, lo registra en consola y en `webhook.log` (rotativo, 10 MB × 5), y lanza `agent.run_rca_analysis()` en segundo plano. Expone además `/health` y `/notifications/history`. Incluye el middleware que gestiona la cabecera `Expect: 100-continue` que envía PI. |
| `agent.py` | El corazón del workflow. Contiene `run_rca_analysis()` (los 6 steps en orden), los prompts fijos, `build_analysis_context()` (Step 3), `build_diagnosis_context()` (Step 6) y la validación de tipos del payload. |
| `graph_client.py` | Lanza `afkg-graph-mcp` como subproceso por stdio y recorre recursivamente el árbol del AF. Filtra metadatos y desambigua elementos con nombre repetido usando su `path` único. |
| `pi_client.py` | Lanza `aveva-pi-mcp` por stdio, crea el bucket temporal y consulta las series. Deduplica paths repetidos y excluye los que no resuelven a WebID. |
| `llm_client.py` | Expone una única función `generate(system, user) → str`. Absorbe la diferencia entre los SDK de Anthropic y Google, y reintenta con backoff exponencial los errores transitorios (5xx, rate limit, timeout). |
| `config.py` | Lee `.env` y expone las variables como constantes. `validate_config()` comprueba al arrancar que está la API key del proveedor seleccionado. |
| `setup.bat` | Crea el entorno virtual `.venv` con `uv` e instala las dependencias. Solo hace falta una vez. |
| `start.bat` | Arranca `uvicorn` en el puerto 8090. Para desarrollo o pruebas. |
| `install_service.bat` | Usa NSSM para registrar el servidor como Windows Service, para que arranque con Windows sin mantener una terminal abierta. |

---

## Requisitos previos

- **Python 3.11+** gestionado con [uv](https://docs.astral.sh/uv/) (ya instalado en el servidor)
- **Windows Server 2019** (o cualquier Windows con `uv`)
- Acceso de red a **PI Web API** (`https://datainfra/piwebapi`)
- **AVEVA PI System** con el módulo **PI Notifications** configurado
- Clave de API de **Google (Gemini)** o de **Anthropic**, según el proveedor elegido en `LLM_PROVIDER`
- Los servidores MCP `afkg-graph-mcp` y `aveva-pi-mcp` disponibles en la misma máquina (se lanzan como subproceso, no hace falta arrancarlos a mano)

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

Edita `.env` y rellena la clave del proveedor que vayas a usar. Con la configuración por defecto (`LLM_PROVIDER=gemini`) basta con:

```
GEMINI_API_KEY=tu-clave-de-gemini
```

---

## Configuración

Todas las opciones se configuran en `.env`. Copia `.env.example` como punto de partida.

### Proveedor de LLM

| Variable | Obligatoria | Descripción | Por defecto |
|---|---|---|---|
| `LLM_PROVIDER` | No | `gemini` o `anthropic` | `gemini` |
| `GEMINI_API_KEY` | ✅ si `LLM_PROVIDER=gemini` | Clave de API de Google — [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | — |
| `GEMINI_MODEL` | No | Modelo de Gemini. `gemini-2.5-pro` requiere facturación activa en Google Cloud; sin ella devuelve un 429 de cuota 0. | `gemini-2.5-flash` |
| `ANTHROPIC_API_KEY` | ✅ si `LLM_PROVIDER=anthropic` | Clave de API de Anthropic — [console.anthropic.com](https://console.anthropic.com/settings/keys) | — |
| `ANTHROPIC_MODEL` | No | Modelo de Claude | `claude-opus-4-8` |

Solo se exige la clave del proveedor realmente seleccionado, no ambas.

### Servidor y entorno

| Variable | Obligatoria | Descripción | Por defecto |
|---|---|---|---|
| `WEBHOOK_PORT` | No | Puerto en el que escucha el servidor | `8090` |
| `WEBHOOK_SECRET` | No | Token para validar que las peticiones vienen de PI. PI debe enviarlo en la cabecera `X-PI-Secret`. Vacío = no se valida el origen. | vacío |
| `PI_LOCAL_TIMEZONE` | No | Zona horaria para mostrar timestamps en el log (nombre IANA) | `Europe/Madrid` |

### Consultas a PI y a los MCP servers

| Variable | Obligatoria | Descripción | Por defecto |
|---|---|---|---|
| `AFKG_GRAPH_MCP_DIR` | No | Carpeta del proyecto `afkg-graph-mcp` (Step 2) | `C:\MCPServer\afkg-graph-mcp` |
| `AVEVA_PI_MCP_DIR` | No | Carpeta del proyecto `aveva-pi-mcp` (Step 5) | `C:\MCPServer\MCP Server` |
| `PI_LOOKBACK_HOURS` | No | Horas antes de la detección para la ventana de consulta | `24` |
| `PI_QUERY_INTERVAL_VALUE` | No | Resolución temporal de las series (valor) | `15` |
| `PI_QUERY_INTERVAL_UNIT` | No | Resolución temporal de las series (unidad) | `minutes` |

> ⚠️ **`PI_LOOKBACK_HOURS` es corto a propósito.** Además de ser suficiente para diagnosticar una degradación gradual, mantiene al modelo por debajo del ciclo con el que se provocan las desviaciones de prueba en este entorno de demo, evitando que confunda esa periodicidad artificial con una causa real. Conviene no subirlo sin tener esto en cuenta.

---

## Uso

### Arrancar el servidor (desarrollo)

```cmd
start.bat
```

Verás en la consola:

```
Servidor RCA Agent arrancado y escuchando en:
  http://0.0.0.0:8090/notification  <- PI envia aqui sus alertas
  http://localhost:8090/health       <- comprobacion de estado
  http://localhost:8090/docs         <- documentacion de la API
```

### Verificar que funciona

```powershell
Invoke-RestMethod http://localhost:8090/health
# → {"status": "ok", "timestamp": "...", "service": "rca-agent-webhook"}
```

### Simular una notificación de PI (para pruebas)

```powershell
$body = @{
    KPIName            = "Hydraulic Efficiency"
    Asset              = "PS20102 A03 PS02 Pump 02"
    Subsystem          = "Pumping Station 01"
    System             = "External Pumping"
    Plant              = "WWTP"
    KPI                = 60.0
    Limit              = 70.0
    LimitThresholdType = "Low"
    StartTime          = "2026-07-02T09:15:47Z"
} | ConvertTo-Json

Invoke-RestMethod -Uri "http://localhost:8090/notification" `
  -Method POST -ContentType "application/json" -Body $body
# → {"status": "accepted", "message": "...", "received_at": "..."}
```

El análisis corre en segundo plano. El recorrido completo — incluidos los prompts enviados y el diagnóstico final — queda en la consola y en `webhook.log`.

### Ver el historial de notificaciones recibidas

```powershell
Invoke-RestMethod http://localhost:8090/notifications/history
```

---

## Endpoints de la API

Documentación interactiva completa en `http://localhost:8090/docs` (Swagger UI generado por FastAPI).

| Método | Endpoint | Descripción |
|---|---|---|
| `GET` | `/health` | Estado del servidor. Devuelve `200 OK` si está en marcha. |
| `POST` | `/notification` | Recibe alertas de PI System. Acepta cualquier body (si no es JSON lo guarda como texto para diagnóstico). Devuelve `202 Accepted`. |
| `GET` | `/notifications/history` | Lista las últimas 50 notificaciones recibidas (en memoria, se pierde al reiniciar). |

---

## Integración con PI System

### Configuración en PI Notifications

En **PI System Explorer → Notifications**, configura el canal de entrega HTTP:

| Parámetro | Valor |
|---|---|
| **URL** | `http://172.21.28.72:8090/notification` ⚠️ usar `http://`, no `https://` |
| **Método** | `POST` |
| **Content-Type** | `application/json` |
| **Body** | El JSON completo del payload (ver [Step 1](#step-1--recibir-la-notificación-de-pi-)) |

> **Importante:** el servidor usa HTTP plano. Si configuras la URL con `https://`, el handshake TLS falla **en silencio**: la conexión TCP queda ESTABLISHED pero ni una sola petición llega a FastAPI. En una red industrial interna, HTTP plano es habitual y no supone un riesgo significativo.

El puerto es el 8090 porque el 8080 está ocupado por IIS en este servidor.

### Campos del payload

| Campo | Descripción |
|---|---|
| `KPIName` | Nombre del indicador que disparó la alerta |
| `Asset` | Nombre del equipo en PI AF — se usa como raíz de `main_asset_context` |
| `Subsystem` | Elemento que agrupa al activo — raíz de `nearby_elements_context` |
| `System` / `Plant` | Resto de la jerarquía, solo para contexto |
| `KPI` | Valor actual que disparó la alerta |
| `Limit` | Umbral configurado en PI |
| `LimitThresholdType` | `"Low"` (por debajo del límite) o `"High"` (por encima) |
| `StartTime` | Momento de detección en UTC (ISO 8601 con `Z`). Define el extremo final de la ventana de consulta. Si falta, se aproxima con la hora del servidor. |
| `AssetType` | *(opcional)* Tipo de equipo, p.ej. `"pump"` |
| `AssetModel` | *(opcional)* Modelo o descripción, p.ej. `"single-channel centrifugal pump"` |

> **Anomalía conocida:** en una prueba real, `Limit` llegó como `"1970-01-01T00:00:00Z"` en lugar de un número — un fallo de mapeo en la Notification Rule de PI. El sistema lo detecta, lo omite y sigue adelante, pero conviene revisar la variable enlazada a `Limit` en PI si se repite.

> **Cuidado con `NonrepetitionInterval=0`:** con ese valor, PI genera una notificación nueva en *cada* evaluación periódica de la alarma, no solo al cruzar el umbral. Por eso el log es rotativo.

---

## Despliegue en producción

Para que el servidor arranque automáticamente con Windows sin mantener una terminal abierta, se usa **NSSM** (Non-Sucking Service Manager):

1. Descarga NSSM desde [nssm.cc/download](https://nssm.cc/download) (gratuito, sin instalador)
2. Copia `nssm.exe` (carpeta `win64`) a `C:\MCPServer\rca-agent\`
3. Abre una terminal como **Administrador** en esa carpeta
4. Ejecuta:
   ```cmd
   install_service.bat
   ```

El servicio aparecerá en `services.msc` como **RCA-Agent-Webhook**.

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
| 2 | Consultar la estructura real del AF vía `afkg-graph-mcp` | ✅ Completado |
| 3 | Construir el mensaje de contexto para el modelo | ✅ Completado |
| 4 | El modelo identifica qué variables reales necesita | ✅ Completado |
| 5 | Obtener datos históricos de PI vía `aveva-pi-mcp` | ✅ Completado |
| 6 | El modelo produce diagnóstico y recomendaciones | ✅ Completado |

**Validación:** un ciclo completo registrado el 2026-08-20, con una alerta real de `Hydraulic Efficiency` sobre `PS20102 A03 PS02 Pump 02`. El diagnóstico apuntó a desgaste interno de la bomba y recomendó inspeccionar impulsor y anillos de desgaste. Validación real pero no exhaustiva: falta ejercitarlo con otros KPIs y tipos de activo.

### Pendiente

1. **Canal de salida del diagnóstico.** Hoy solo va al log. Opciones a valorar: correo al ingeniero de proceso, interfaz web, o anotación del event frame en PI. Es la decisión principal del proyecto y es de producto, no técnica.
2. **`webhook.log` no está en `.gitignore`** y contiene los prompts completos con datos de planta. Añadir `webhook.log*` antes del próximo push.
3. **Revisar la configuración de la llamada a Anthropic.** No usa *adaptive thinking* y `max_tokens=2048` va justo para el Step 6. Sin probar todavía.
4. **Ampliar la validación** a más KPIs y tipos de activo.

---

## Servidores MCP relacionados

El workflow lanza estos dos servidores como subproceso (`uv run --directory ...`, protocolo MCP sobre stdio). No hace falta arrancarlos a mano.

| Servidor | Ruta | Tools que se usan | Step |
|---|---|---|---|
| `afkg-graph-mcp` | `C:\MCPServer\afkg-graph-mcp\` | `graph_neighborhood` | Step 2 |
| `aveva-pi-mcp` | `C:\MCPServer\MCP Server\` | `create_timeseries_bucket`, `query_by_path` | Step 5 |

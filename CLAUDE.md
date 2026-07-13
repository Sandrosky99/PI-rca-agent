# PI RCA Agent — Instrucciones para Claude Code

Agente de análisis de causa raíz (RCA) para alertas operacionales de AVEVA PI System.
Cuando PI detecta una desviación en un KPI monitorizado, este agente recibe la notificación,
consulta datos históricos en PI Web API y usa Claude para diagnosticar causas raíz.

---

## Estado del proyecto

| Step | Descripción | Estado |
|---|---|---|
| 1 | Recibir notificación HTTP POST de PI System | ✅ Completado |
| 2 | Consultar la estructura del AF (afkg-graph-mcp) para el `Asset`/`Subsystem` de la alerta → `main_asset_context`/`nearby_elements_context` con atributos reales (`piApiPath`) | ✅ Completado (`graph_client.py`) |
| 3 | Preparar el mensaje de 4 secciones (objetivo, payload, modelo de datos, JSON del AF) para el modelo | ✅ Completado (`build_analysis_context()`) |
| 4 | El modelo identifica, de esas variables reales, cuáles necesita analizar | ✅ Completado (llamada a `llm_client.generate()`, respuesta solo logueada — falta parsear/usar la lista) |
| 5 | Obtener datos históricos de PI vía MCP Server | 🔲 Pendiente |
| 6 | El modelo produce diagnóstico y recomendaciones | 🔲 Pendiente |

**Motivo del cambio (2026-07-03):** con el flujo original, el modelo proponía nombres de atributos "de libro" (p.ej. `Discharge Flow Rate`) que no necesariamente existen con ese naming en la estructura real del AF, y el agente no tenía forma de saber si esas variables estaban realmente disponibles. Se añadió un paso intermedio (Step 2) para consultar primero la estructura real del AF y pasarle esa lista de atributos disponibles al modelo, de forma que elija **solo entre lo que existe** en vez de proponer variables que luego no se podrán consultar.

**Detalle del Step 2 (implementado, 2026-07-13):** `graph_client.py` lanza `afkg-graph-mcp` como subproceso (protocolo MCP sobre stdio, vía el SDK `mcp`) y usa `graph_neighborhood` para recorrer recursivamente el árbol PARENT_OF del `Asset` (→ `main_asset_context`) y del `Subsystem` (→ `nearby_elements_context`, excluyendo la rama del propio `Asset` para no duplicarla). Los nodos organizativos sin atributos propios (`Meters`, `Indicators`) no generan grupo propio — su nombre solo queda reflejado en el breadcrumb (`>`) de sus descendientes. Se filtran los atributos de metadatos/bookkeeping (`Area Code`, `Asset Code`, `Path`, `Tag Name`, `Manufacturer`, etc. — ver `_METADATA_ATTRS`) que no aportan al diagnóstico. La desambiguación entre elementos con nombre repetido (p.ej. varios "Level Alert"/"Reference" bajo distintos indicadores) usa el `path` único que cada hijo ya trae en la respuesta de su padre, en vez de reconstruirlo a mano. Probado end-to-end contra el servidor y Neo4j reales el 2026-07-13 con `PS20102 A03 PS02 Pump 02`/`Pumping Station 01` (15 grupos en `main_asset_context`, 5 en `nearby_elements_context`).

**Detalle del Step 3 (implementado, 2026-07-13):** `build_analysis_context(payload, af_context)` en `agent.py` construye `claude_prompt` con 4 secciones fijas/dinámicas: 1) objetivo de la interacción (`_OBJECTIVE_SECTION`, fijo), 2) payload de la notificación (`summary`, dinámico — misma lógica de antes: jerarquía, umbral explicado en lenguaje natural, hora UTC+local), 3) explicación del modelo de datos (`_DATA_MODEL_SECTION`, fijo — describe `main_asset_context`/`nearby_elements_context` y el significado de cada campo), 4) el JSON de `af_context` (dinámico), seguido de `_FINAL_INSTRUCTION` (pide la lista de atributos a consultar con su elemento y `piApiPath`). El rol y dominio del agente (EDAR + bombeos externos, nunca genérico) siguen fijados aparte en `SYSTEM_PROMPT`, enviado vía el parámetro `system`/`system_instruction` en todas las llamadas al modelo (Steps 4 y 6).

**Detalle del Step 4 (implementado, 2026-07-13):** `run_rca_analysis()` llama a `llm_client.generate(SYSTEM_PROMPT, context["claude_prompt"])` y loguea la respuesta. **Pendiente:** la respuesta del modelo (lista de atributos + piApiPath) todavía no se parsea ni se usa para el Step 5 — de momento solo queda en el log.

**Dependencia nueva:** paquete `mcp` (SDK oficial de Model Context Protocol) en `requirements.txt`, usado por `graph_client.py` para hablar con `afkg-graph-mcp` vía stdio. Variable de entorno opcional `AFKG_GRAPH_MCP_DIR` (por defecto `C:\MCPServer\afkg-graph-mcp`) en `config.py`.

---

## Arquitectura

```
PI System (172.21.28.55)
    │ HTTP POST → {"KPIName": ..., "Asset": ..., "KPI": ..., "Limit": ...}
    ▼
webhook.py  (FastAPI :8090)
    │ background task
    ▼
agent.py  (run_rca_analysis)
    ├── Step 2: afkg-graph-mcp → estructura real del AF del asset (atributos disponibles)
    ├── Step 3: construir contexto para el modelo (incluye los atributos del Step 2)
    ├── Step 4: modelo (Gemini/Claude) → qué atributos reales necesita
    ├── Step 5: MCP Server (aveva-pi-mcp) → datos históricos de PI
    └── Step 6: modelo (Gemini/Claude) → diagnóstico final
```

---

## Entorno técnico

### Red
- **IP servidor RCA Agent:** `172.21.28.72`
- **IP PI System:** `172.21.28.55`
- **Puerto webhook:** `8090` (el 8080 está ocupado por IIS — no usar)
- **Firewall Windows Defender:** deshabilitado en ambas VMs
- PI debe apuntar a `http://172.21.28.72:8090/notification` con `http://` (NO `https://`)
  - PI envía `Expect: 100-continue` → el middleware `Expect100ContinueMiddleware` en `webhook.py` lo gestiona
  - Si se configura con `https://`, el handshake TLS falla silenciosamente (TCP ESTABLISHED pero 0 peticiones llegan a FastAPI)

### Python
- **No hay Python en el PATH del sistema** — gestionado con `uv`
- Crear venv: `uv venv --python 3.11 .venv`
- Instalar deps: `uv pip install -r requirements.txt`
- Arrancar servidor: `.venv\Scripts\uvicorn.exe webhook:app --host 0.0.0.0 --port 8090 --log-level info --timeout-keep-alive 30`

### Git / GitHub
- **Repo:** https://github.com/Sandrosky99/PI-rca-agent
- **Branch:** `master`
- **Usuario git:** `Sandrosky99` / `sandracerveron@gmail.com`
- Push: `git push https://<PAT>@github.com/Sandrosky99/PI-rca-agent.git master`

---

## Payload real de PI System

Confirmado el 2026-07-02 (con `StartTime` desde las 09:16 UTC). PI envía este JSON en cada notificación:

```json
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

| Campo | Descripción |
|---|---|
| `KPIName` | Nombre del indicador / alerta |
| `Asset` | Nombre del equipo en PI AF |
| `Subsystem` / `System` / `Plant` | Jerarquía del asset |
| `KPI` | Valor actual que disparó la alerta |
| `Limit` | Umbral configurado en PI |
| `LimitThresholdType` | `"Low"` (por debajo del límite) o `"High"` (por encima) |
| `StartTime` | Momento de detección en UTC (ISO 8601 con `Z`). Usado por `agent.py` para la ventana temporal del análisis (conversión a local vía pytz, igual que `search_event_frames` en aveva-pi-mcp). Si falta, se aproxima con la hora actual del servidor. |
| `AssetType` | Confirmado 2026-07-02. Tipo de equipo (p.ej. `"pump"`). Genérico, no limitado a bombas. Se añade al mensaje de contexto para Claude. |
| `AssetModel` | Confirmado 2026-07-02. Modelo/descripción del equipo (p.ej. `"single-channel centrifugal pump"`). Se añade al mensaje de contexto para Claude. |

⚠️ **Anomalía detectada 2026-07-02:** en una prueba real, `Limit` llegó como `"1970-01-01T00:00:00Z"` en vez de un valor numérico — probable fallo de mapeo en la configuración de PI Notifications para esa alerta concreta. Revisar la variable enlazada a `Limit` en PI si se repite.

**Validación de tipos (Step 3):** `_valid_field(payload, key, expected_type)` en `agent.py` comprueba el tipo de cada campo del payload contra el tipo esperado (`str` para nombres/jerarquía, `(int, float)` para `KPI`/`Limit`, excluyendo `bool`). Si un campo no coincide con el tipo esperado, se omite del mensaje a Claude (no se pasa el dato "roto") y se registra un `WARNING` en el log con el valor recibido, para poder detectar fallos de configuración en PI. `build_analysis_context()` construye la frase del resumen de forma condicional según qué combinación de `KPI`/`Limit` sea válida.

**Proveedor de LLM configurable (2026-07-02):** `llm_client.py` es una capa de abstracción con una única función `generate(system_prompt, user_message) -> str` que llama a Gemini o a Claude según `LLM_PROVIDER` (`.env`), sin que el resto del agente conozca la diferencia. Anthropic y Google **no** comparten un estándar de API, por eso cada proveedor tiene su propia función interna dentro de `llm_client.py`:

| | Anthropic (Claude) | Google (Gemini) |
|---|---|---|
| SDK | `anthropic` | `google-genai` |
| System prompt | parámetro `system` | `GenerateContentConfig(system_instruction=...)` |
| Mensaje | `messages=[{"role": "user", "content": "..."}]` | `contents="..."` |
| Respuesta | `response.content[0].text` | `response.text` |

Por defecto `LLM_PROVIDER=gemini`; `validate_config()` en `config.py` solo exige la API key del proveedor realmente seleccionado (no ambas).

---

## Estructura de ficheros

```
rca-agent/
├── webhook.py           ← FastAPI: recibe POST de PI (Step 1 ✅)
│                          Endpoints: GET /health, POST /notification, GET /notifications/history
├── agent.py             ← Agente RCA — Steps 2-4 implementados, Steps 5-6 pendientes
├── graph_client.py      ← Cliente MCP para afkg-graph-mcp (Step 2)
├── llm_client.py        ← Abstracción sobre el proveedor de LLM (Gemini/Anthropic)
├── config.py            ← Carga .env y valida variables obligatorias
├── .env.example         ← Plantilla (copiar a .env y rellenar)
├── .env                 ← Credenciales reales (NO en git)
├── requirements.txt     ← fastapi, uvicorn, google-genai, anthropic, python-dotenv, pytz, mcp
├── setup.bat            ← Crea .venv e instala deps (ejecutar 1 vez)
├── start.bat            ← Arranca el servidor (desarrollo)
├── install_service.bat  ← Windows Service con NSSM (producción)
└── CLAUDE.md            ← Este fichero
```

---

## Variables de entorno (.env)

| Variable | Obligatoria | Descripción | Por defecto |
|---|---|---|---|
| `LLM_PROVIDER` | No | Proveedor de LLM: `gemini` o `anthropic` | `gemini` |
| `GEMINI_API_KEY` | ✅ si `LLM_PROVIDER=gemini` | Clave API de Google para Gemini | — |
| `GEMINI_MODEL` | No | Modelo de Gemini a usar | `gemini-2.5-flash` |
| `ANTHROPIC_API_KEY` | ✅ si `LLM_PROVIDER=anthropic` | Clave API de Anthropic para Claude | — |
| `ANTHROPIC_MODEL` | No | Modelo de Claude a usar | `claude-opus-4-8` |
| `WEBHOOK_PORT` | No | Puerto del servidor | `8090` |
| `WEBHOOK_SECRET` | No | Token para validar origen de PI | vacío |
| `PI_LOCAL_TIMEZONE` | No | Zona horaria para logs | `Europe/Madrid` |
| `AFKG_GRAPH_MCP_DIR` | No | Carpeta del proyecto afkg-graph-mcp (Step 2, lanzado como subproceso) | `C:\MCPServer\afkg-graph-mcp` |

---

## MCP Servers relacionados

El agente habla con estos dos MCP servers (ya funcionando en la misma máquina):

| Servidor | Ruta | Tools que usa el agente | Step | Cliente en rca-agent |
|---|---|---|---|---|
| `afkg-graph-mcp` | `C:\MCPServer\afkg-graph-mcp\` | `graph_neighborhood` → estructura del AF del asset/subsistema y `piApiPath` de sus atributos | Step 2 | `graph_client.py` (implementado) |
| `aveva-pi-mcp` | `C:\MCPServer\MCP Server\` | `create_timeseries_bucket`, `query_by_path`, `search_event_frames` | Step 5 | *(pendiente)* |

El Step 2 consulta el AF para el `Asset` y el `Subsystem` de la alerta y obtiene los atributos reales disponibles (con su `piApiPath`); esa lista se incluye en el mensaje del Step 3 para que el modelo (Step 4) elija solo entre esas variables. El `piApiPath` de las elegidas alimentará `query_by_path` del MCP de PI en el Step 5 (pendiente de implementar un cliente MCP equivalente a `graph_client.py` para `aveva-pi-mcp`).

---

## Convenciones de desarrollo

- **Proveedor de LLM:** configurable vía `LLM_PROVIDER` (`.env`) — `gemini` por defecto (modelo `gemini-2.5-flash`; `gemini-2.5-pro` requiere facturación activada en el proyecto de Google Cloud, sin ella da error 429 de cuota 0), o `anthropic` (`claude-opus-4-8` con `thinking: {type: "adaptive"}` y streaming). Ver `llm_client.py`.
- **Idioma comentarios:** español
- **Commits:** en español, con `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
- **Sin hardcodear credenciales** — todo via `.env`
- Hacer push a GitHub al final de cada sesión de trabajo

---

## Cómo actualizar este fichero

Al finalizar cada sesión de trabajo, actualizar:
1. La tabla de **Estado del proyecto** (marcar steps completados)
2. La sección **Próximo paso**
3. Cualquier decisión técnica nueva relevante

Así el siguiente chat arranca con el contexto exacto sin necesidad de explicaciones.

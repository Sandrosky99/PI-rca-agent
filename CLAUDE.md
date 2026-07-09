# PI RCA Agent — Instrucciones para Claude Code

Agente de análisis de causa raíz (RCA) para alertas operacionales de AVEVA PI System.
Cuando PI detecta una desviación en un KPI monitorizado, este agente recibe la notificación,
consulta datos históricos en PI Web API y usa Claude para diagnosticar causas raíz.

---

## Estado del proyecto

| Step | Descripción | Estado |
|---|---|---|
| 1 | Recibir notificación HTTP POST de PI System | ✅ Completado |
| 2 | **(Nuevo)** Consultar la estructura del AF (afkg-graph-mcp) para el asset de la alerta → obtener los atributos reales disponibles (`piApiPath`) | 🔲 Pendiente |
| 3 | Preparar contexto estructurado para el modelo (antes Step 2) | 🔶 Implementado, pendiente de actualizar — debe incorporar los atributos reales del Step 2 |
| 4 | El modelo identifica, de esas variables reales, cuáles necesita analizar (antes Step 3) | 🔲 Pendiente |
| 5 | Obtener datos históricos de PI vía MCP Server (antes Step 4) | 🔲 Pendiente |
| 6 | El modelo produce diagnóstico y recomendaciones (antes Step 5) | 🔲 Pendiente |

**Motivo del cambio (2026-07-03):** con el flujo original, el modelo proponía nombres de atributos "de libro" (p.ej. `Discharge Flow Rate`) que no necesariamente existen con ese naming en la estructura real del AF, y el agente no tenía forma de saber si esas variables estaban realmente disponibles. Se añade un paso intermedio (nuevo Step 2) para consultar primero la estructura real del AF y pasarle esa lista de atributos disponibles al modelo, de forma que elija **solo entre lo que existe** en vez de proponer variables que luego no se podrán consultar.

**Próximo paso:** Implementar el nuevo Step 2 en `agent.py` (consulta a `afkg-graph-mcp` — `graph_search`/`graph_neighborhood` — para el `Asset` de la alerta) y después actualizar `build_analysis_context()` (Step 3) para incluir esa lista de atributos reales en `claude_prompt`, de modo que el Step 4 elija únicamente entre variables existentes en el AF.

**Detalle del Step 3 (implementado, pendiente de actualizar):** `build_analysis_context(payload)` en `agent.py` extrae los campos del payload real de PI (`KPIName`, `Asset`, `Subsystem`, `System`, `Plant`, `KPI`, `Limit`, `LimitThresholdType`, `StartTime`, y opcionalmente `AssetType`/`AssetModel`), valida el tipo esperado de cada campo (`_valid_field`, omite del mensaje los que no coincidan, p.ej. un `Limit` no numérico), convierte `StartTime` (UTC) a hora local vía `pytz`/`PI_LOCAL_TIMEZONE` (DST-aware, con fallback a `datetime.now()` si falta), y devuelve un dict con el mensaje dinámico de la alerta en `claude_prompt`. **Pendiente:** todavía no incorpora la lista de atributos reales del AF (Step 2) — hay que añadirla al mensaje cuando el Step 2 esté implementado. El rol y dominio del agente (EDAR + bombeos externos, nunca genérico) están fijados aparte en la constante `SYSTEM_PROMPT`, pensada para enviarse vía el parámetro `system`/`system_instruction` en **todas** las llamadas al modelo (Steps 4 y 6), no repetida en cada mensaje. `run_rca_analysis()` ya invoca `build_analysis_context()` y deja `context` listo, pero su llamada tendrá que moverse después del nuevo Step 2 en el flujo de `run_rca_analysis()`.

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
├── agent.py             ← Agente RCA — aquí van los Steps 2-6
├── llm_client.py        ← Abstracción sobre el proveedor de LLM (Gemini/Anthropic)
├── config.py            ← Carga .env y valida variables obligatorias
├── .env.example         ← Plantilla (copiar a .env y rellenar)
├── .env                 ← Credenciales reales (NO en git)
├── requirements.txt     ← fastapi, uvicorn, google-genai, anthropic, python-dotenv, pytz
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

---

## MCP Servers relacionados

El agente usará estos dos MCP servers (ya funcionando en la misma máquina):

| Servidor | Ruta | Tools que usará el agente | Step |
|---|---|---|---|
| `afkg-graph-mcp` | `C:\MCPServer\afkg-graph-mcp\` | `graph_search`, `graph_neighborhood` → estructura del AF del asset y `piApiPath` de sus atributos | Step 2 |
| `aveva-pi-mcp` | `C:\MCPServer\MCP Server\` | `create_timeseries_bucket`, `query_by_path`, `search_event_frames` | Step 5 |

El Step 2 consulta el AF para el `Asset` de la alerta y obtiene los atributos reales disponibles (con su `piApiPath`); esa lista se incluye en el contexto del Step 3 para que el modelo (Step 4) elija solo entre esas variables. El `piApiPath` de las elegidas alimenta directamente `query_by_path` del MCP de PI en el Step 5.

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

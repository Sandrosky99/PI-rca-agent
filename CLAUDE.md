# PI RCA Agent — Instrucciones para Claude Code

Agente de análisis de causa raíz (RCA) para alertas operacionales de AVEVA PI System.
Cuando PI detecta una desviación en un KPI monitorizado, este agente recibe la notificación,
consulta datos históricos en PI Web API y usa Claude para diagnosticar causas raíz.

---

## Estado del proyecto

| Step | Descripción | Estado |
|---|---|---|
| 1 | Recibir notificación HTTP POST de PI System | ✅ Completado |
| 2 | Preparar contexto estructurado para Claude | ✅ Completado |
| 3 | Claude identifica variables de PI a consultar | 🔲 Pendiente |
| 4 | Obtener datos históricos de PI vía MCP Server | 🔲 Pendiente |
| 5 | Claude produce diagnóstico y recomendaciones | 🔲 Pendiente |

**Próximo paso:** Implementar Step 3 en `agent.py` → llamar a la API de Anthropic con el `context["claude_prompt"]` generado por `build_analysis_context()` (Step 2) para obtener la lista de atributos de PI a consultar.

**Detalle del Step 2 (implementado):** `build_analysis_context(payload)` en `agent.py` extrae los campos del payload real de PI (`KPIName`, `Asset`, `Subsystem`, `System`, `Plant`, `KPI`, `Limit`, `LimitThresholdType`, `StartTime`, y opcionalmente `AssetType`/`AssetModel`), valida el tipo esperado de cada campo (`_valid_field`, omite del mensaje los que no coincidan, p.ej. un `Limit` no numérico), convierte `StartTime` (UTC) a hora local vía `pytz`/`PI_LOCAL_TIMEZONE` (DST-aware, con fallback a `datetime.now()` si falta), y devuelve un dict con el mensaje dinámico de la alerta en `claude_prompt`. El rol y dominio del agente (EDAR + bombeos externos, nunca genérico) están fijados aparte en la constante `SYSTEM_PROMPT`, pensada para enviarse vía el parámetro `system` de la API de Anthropic en **todas** las llamadas (Steps 3 y 5), no repetida en cada mensaje. `run_rca_analysis()` ya invoca `build_analysis_context()` y deja `context` listo para el Step 3.

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
    ├── Step 2: construir contexto para Claude
    ├── Step 3: Claude API → qué atributos de PI necesita
    ├── Step 4: MCP Server → datos históricos de PI
    └── Step 5: Claude API → diagnóstico final
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

**Validación de tipos (Step 2):** `_valid_field(payload, key, expected_type)` en `agent.py` comprueba el tipo de cada campo del payload contra el tipo esperado (`str` para nombres/jerarquía, `(int, float)` para `KPI`/`Limit`, excluyendo `bool`). Si un campo no coincide con el tipo esperado, se omite del mensaje a Claude (no se pasa el dato "roto") y se registra un `WARNING` en el log con el valor recibido, para poder detectar fallos de configuración en PI. `build_analysis_context()` construye la frase del resumen de forma condicional según qué combinación de `KPI`/`Limit` sea válida.

---

## Estructura de ficheros

```
rca-agent/
├── webhook.py           ← FastAPI: recibe POST de PI (Step 1 ✅)
│                          Endpoints: GET /health, POST /notification, GET /notifications/history
├── agent.py             ← Agente RCA — aquí van los Steps 2-5
├── config.py            ← Carga .env y valida variables obligatorias
├── .env.example         ← Plantilla (copiar a .env y rellenar)
├── .env                 ← Credenciales reales (NO en git)
├── requirements.txt     ← fastapi, uvicorn, anthropic, python-dotenv, pytz
├── setup.bat            ← Crea .venv e instala deps (ejecutar 1 vez)
├── start.bat            ← Arranca el servidor (desarrollo)
├── install_service.bat  ← Windows Service con NSSM (producción)
└── CLAUDE.md            ← Este fichero
```

---

## Variables de entorno (.env)

| Variable | Obligatoria | Descripción | Por defecto |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Clave API de Anthropic para Claude | — |
| `WEBHOOK_PORT` | No | Puerto del servidor | `8090` |
| `WEBHOOK_SECRET` | No | Token para validar origen de PI | vacío |
| `PI_LOCAL_TIMEZONE` | No | Zona horaria para logs | `Europe/Madrid` |

---

## MCP Servers relacionados

El agente usará estos dos MCP servers (ya funcionando en la misma máquina) en los Steps 3-4:

| Servidor | Ruta | Tools que usará el agente |
|---|---|---|
| `aveva-pi-mcp` | `C:\MCPServer\MCP Server\` | `create_timeseries_bucket`, `query_by_path`, `search_event_frames` |
| `afkg-graph-mcp` | `C:\MCPServer\afkg-graph-mcp\` | `graph_search`, `graph_neighborhood` → devuelve `piApiPath` |

El `piApiPath` del grafo alimenta directamente `query_by_path` del MCP de PI.

---

## Convenciones de desarrollo

- **Modelo Claude:** `claude-opus-4-8` con `thinking: {type: "adaptive"}` y streaming
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

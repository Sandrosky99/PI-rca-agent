# Trazabilidad de desarrollo asistido por IA

Registro exigido por `base/software-spec.md` §1.4–§1.7 y `extensions/ai-governance-spec.md`
§7.1 (versionado de modelo/herramienta) y §3.3 (registro de herramienta para auditoría de PI).

> **Append-only.** Este fichero no se reescribe: se le añaden entradas. Las correcciones se
> hacen con una entrada nueva que enmiende la anterior, nunca editando lo ya escrito
> (base §1.7). El historial de git es la garantía de esa inmutabilidad.

---

## 1. Herramientas y modelos usados en el desarrollo (§7.1)

| Periodo | Herramienta | Modelo | Uso |
|---|---|---|---|
| 2026-07-01 → 2026-08-07 | Claude Code | Claude Sonnet 4.6 | Desarrollo de los Steps 1–6, clientes MCP, guardarraíles |
| 2026-08-26 → 2026-09-07 | Claude Code | Claude Opus 5 | Documentación, puerta de autorización del Step 4, ventana variable, registro de incidentes, conformidad izSpecs |

Nota: la convención de commit anterior al 2026-09-07 usaba `Co-Authored-By: Claude Sonnet 4.6`
también en sesiones ejecutadas con Opus 5, por arrastre de la convención escrita en el
antiguo `CLAUDE.md`. La tabla de arriba es la fuente correcta; los commits afectados **no se
reescriben** (§1.7).

## 2. Modelos en tiempo de ejecución (§7.1)

Distintos de los usados para *desarrollar*: estos son los que el workflow invoca al
diagnosticar. Ver también el inventario en [`AI-GOVERNANCE.md`](./AI-GOVERNANCE.md) §7.

| Desde | Proveedor | Modelo | Notas |
|---|---|---|---|
| 2026-07-02 | Google | `gemini-2.5-flash` | Por defecto. Razona por defecto (`thinking_level`), no desactivable del todo |
| 2026-07-02 | Anthropic | `claude-opus-4-8` | Alternativa vía `LLM_PROVIDER=anthropic`. Sin `thinking` |

**Cambio de proveedor (§7.3):** cambiar `LLM_PROVIDER` es un cambio evaluable, no un ajuste.
Las dos rutas no están en igualdad de condiciones —Gemini razona y Anthropic no—, así que
cualquier comparación de calidad entre ellas debe tenerlo en cuenta. Documentar aquí la
evaluación antes de adoptar un cambio de forma general.

## 3. Parámetros de reproducibilidad (§7.2)

Lo que hace falta para reproducir o auditar un diagnóstico concreto:

| Parámetro | Dónde está |
|---|---|
| System prompt | `workflow.SYSTEM_PROMPT`, y copiado en `incidents/<id>.json` → `trace.system_prompt` |
| Prompt del Step 3 | `incidents/<id>.json` → `trace.step3_prompt` |
| Respuesta del Step 4 | `incidents/<id>.json` → `trace.step4_response` |
| Prompts y respuestas del Step 6 | `incidents/<id>.json` → `trace.step6_prompts` / `trace.step6_responses` |
| Modelo y proveedor | `incidents/<id>.json` → `diagnostico._ai_generated` |
| Payload original | `incidents/<id>.json` → `payload` |

**Temperature no se fija** en ninguna de las dos rutas: se usa el valor por defecto del
proveedor. La reproducción exacta no está garantizada; la auditoría de *qué se envió y qué
se recibió*, sí.

## 4. Procedencia de los prompts (§1.5)

Los prompts que se envían al modelo en producción no se escriben a mano: los construyen
`build_analysis_context()` y `build_diagnosis_context()` a partir de constantes en
`workflow.py` (`_OBJECTIVE_SECTION`, `_DATA_MODEL_SECTION`, `_DIAGNOSIS_*`). El historial de
git de ese fichero es el registro de cómo han evolucionado y por qué.

El prompt exacto de cada ejecución queda persistido en el fichero de su incidente (§3).

## 5. Fuentes de datos e información usadas en el desarrollo (§1.6)

Datos, documentos y código a los que la IA tuvo acceso para generar la solución:

| Fuente | Naturaleza | Uso |
|---|---|---|
| Código de `aveva-pi-mcp` (`C:\MCPServer\MCP Server\`) | Código interno | Entender el formato de respuesta de `query_by_path` y el fallo de resolución de WebID |
| Código de `afkg-graph-mcp` (`C:\MCPServer\afkg-graph-mcp\`) | Código interno | Entender `graph_neighborhood` y la forma del grafo del AF |
| `webhook.log` del entorno de demo | Datos operacionales de planta | Reconstruir el payload real de PI, calibrar `MAX_SELECTED_VARIABLES` contra la ejecución del 2026-08-20 y medir el tamaño real de las respuestas |
| Estructura del Asset Framework vía Neo4j | Datos operacionales de planta | Validar el recorrido del Step 2 contra el AF real |
| Documentación pública de Google Gemini (`ai.google.dev`) | Documentación de terceros | Verificar el comportamiento de `thinking_level` y de `maxOutputTokens` |
| Documentación de la API de Anthropic | Documentación de terceros | Verificar que `max_tokens` es obligatorio y los IDs de modelo vigentes |
| izSpecs bundle `2026.12-09011521` | Estándar interno | Conformidad |
| Revisión técnica del proyecto aportada por el propietario (2026-08-27) | Documento interno | Priorización del trabajo: deduplicación, persistencia, validación de rutas, ventana |

**Ningún dato personal** en ninguna de esas fuentes. Los datos de planta son magnitudes de
proceso y nombres de activo.

## 6. Verificación de dependencias introducidas con asistencia de IA (§1.3)

Revisadas el 2026-09-07. Todas fijadas a versión exacta en `requirements.txt` (base §5).

| Dependencia | Versión | Licencia | Valoración |
|---|---|---|---|
| `fastapi` | 0.138.2 | MIT | Mantenimiento muy activo, adopción masiva |
| `uvicorn[standard]` | 0.49.0 | BSD-3-Clause | Servidor ASGI de referencia, mismo ecosistema |
| `google-genai` | 2.10.0 | Apache-2.0 | SDK oficial de Google |
| `anthropic` | 0.115.0 | MIT | SDK oficial de Anthropic |
| `python-dotenv` | 1.2.2 | BSD-3-Clause | Estándar de facto para `.env` |
| `pytz` | 2026.2 | MIT | Misma versión que usa `aveva-pi-mcp`, por coherencia de fechas |
| `mcp` | 1.28.1 | MIT | SDK oficial de Model Context Protocol |
| `tenacity` | 9.1.4 | Apache-2.0 | Ya venía como dependencia transitiva de `google-genai`; se declara explícita al usarse directamente |

Todas con licencia permisiva y compatible con Apache-2.0 (la de este recurso). Ninguna
copyleft.

**Binario de terceros (no es dependencia Python):**

| Herramienta | Version | Licencia | Verificacion |
|---|---|---|---|
| NSSM | 2.24 | Dominio publico | Descargado de nssm.cc (fuente oficial) el 2026-09-07. SHA256 del ZIP: `727D1E42...AA6743`. SHA256 del `nssm.exe` extraido: `F689EE9A...9A06C97`. No se versiona: esta en `.gitignore` |

NSSM hace falta porque uvicorn no implementa el protocolo de servicios de Windows; NSSM actua
de envoltorio. Solo se usa en la instalacion del servicio, no en tiempo de ejecucion del
workflow. El escaneo de vulnerabilidades lo ejecuta `pip-audit` en el gate de CI.

## 7. Verificación de licencias del código generado (§3.1)

El código generado es específico de este dominio —integración con PI System, protocolo MCP,
prompts de diagnóstico de bombeo— y no reproduce fragmentos sustanciales de bibliotecas de
terceros. No se han incorporado bloques de código copiados de fuentes externas. Riesgo de
reutilización de código con derechos de terceros: bajo; no se ha considerado necesaria una
herramienta de detección de similitud (§3.2, SHOULD).

## 8. Validación funcional (§2.3)

| Fecha | Qué se validó | Entorno | Evidencia |
|---|---|---|---|
| 2026-07-13 | Step 2 contra el AF real | Neo4j y `afkg-graph-mcp` reales | 15 grupos en `main_asset_context`, 5 en `nearby_elements_context` para `PS20102 A03 PS02 Pump 02` |
| 2026-08-20 | Ciclo completo Steps 1–6 | PI System y LLM reales | `webhook.log`: alerta de `Hydraulic Efficiency`, diagnóstico de desgaste interno |
| 2026-09-01 | Puerta del Step 4 contra datos reales | Reproducción del caso del 2026-08-20 | 30 de 30 rutas del modelo válidas, 0 rechazos |
| 2026-09-07 | Suites completas | Local, con dobles | `python tests/run_all.py` — 7 suites, 214 comprobaciones |
| 2026-09-07 | **Ciclo completo Steps 1-6 con el código actual** | PI System, Neo4j y Gemini reales | Alerta de `Hydraulic Efficiency` (StartTime 2026-09-06T10:49:00Z). 2 min 23 s. 22 de 22 variables autorizadas. **Primera vez que el modelo ejerce la ampliación de ventana**: pidió `7d` alegando un hueco de datos. Log: 56 líneas, 0 no-JSON. Audit: 6 entradas |

⚠️ **Limitación conocida:** los **dos** ciclos completos reales son del mismo activo y el
mismo KPI, y ninguna causa propuesta ha sido confirmada. No es evidencia suficiente sobre la
calidad del diagnóstico; ver el punto 8 de la
revisión del propietario y `AI-GOVERNANCE.md` §2 (límites conocidos).

---

## Cómo añadir una entrada

Al terminar una sesión de trabajo asistida con IA:

1. Si has usado un modelo o herramienta distinta a los de §1, añade una fila.
2. Si has dado a la IA acceso a una fuente de datos nueva, añade una fila en §5.
3. Si has incorporado una dependencia nueva, añade una fila en §6 con su verificación.
4. Si has validado funcionalmente algo, añade una fila en §8.

No edites filas existentes.

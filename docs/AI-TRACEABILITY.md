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
| 2026-09-08 → 2026-10-08 | Claude Code | Claude Opus 5 | Gate de CI, pantalla de la sala de control (Fases 1 a 3), reanálisis, búsqueda, ruta de DeepSeek. Sacado de los commits el 2026-11-25: `AI-assisted: claude-opus-5` hasta el 2026-09-28 y `Co-Authored-By: Claude Opus 5` después. Entre el 2026-10-08 y el 2026-11-24 no hay commits |
| 2026-11-24 → 2026-11-25 | Claude Code | Claude Opus 5.5 | Mantenimiento de los incidentes de prueba del servidor de pruebas (puerto 8099) y revisión de la documentación contra el código |
| 2026-11-25 | Claude Code | Claude Opus 5.5 | También código, no solo documentación: `llm_client.modelo_en_uso()` (etiqueta del modelo), `config.ficheros_compartidos_con_el_servicio()` (aviso al arrancar), docstring de `incidents.traces()` y las suites `test_etiqueta_ia.py` y `test_config.py` §17. La fila anterior lo describía de menos |

Nota: la convención de commit anterior al 2026-09-07 usaba `Co-Authored-By: Claude Sonnet 4.6`
también en sesiones ejecutadas con Opus 5, por arrastre de la convención escrita en el
antiguo `CLAUDE.md`. La tabla de arriba es la fuente correcta; los commits afectados **no se
reescriben** (§1.7).

> **Enmienda del 2026-11-25.** Falta el periodo **2026-09-08 → 2026-11-23**: en él se
> desarrollaron las Fases 2 y 3 y la ruta de DeepSeek, y no se añadió ninguna fila. El modelo
> usado consta en el trailer `AI-assisted:` de cada commit (convención de `AGENTS.md`), pero no
> en la copia local, que no lleva historial de git. Hay que completar la tabla desde los commits
> del repositorio con una fila nueva, no estimarlo.

> **Enmienda del 2026-11-25 (5): la copia local sí es un repositorio git.** La comprobación se hizo
> desde la carpeta superior por error. Con el historial, el hueco queda cubierto con la fila
> «2026-09-08 → 2026-10-08» de la tabla. Y aparece otra brecha: los **19 commits del 2026-09-28 al
> 2026-10-08** (de `806c810` a `bfa86cd`) llevan solo `Co-Authored-By: Claude Opus 5`, sin la línea
> `AI-assisted` con fecha que pide la convención desde el 2026-09-07. La fecha está en la del propio
> commit, pero no en el trailer. No se reescriben (§1.7).

## 2. Modelos en tiempo de ejecución (§7.1)

Distintos de los usados para *desarrollar*: estos son los que el workflow invoca al
diagnosticar. Ver también el inventario en [`AI-GOVERNANCE.md`](./AI-GOVERNANCE.md) §7.

| Desde | Proveedor | Modelo | Notas |
|---|---|---|---|
| 2026-07-02 | Google | `gemini-2.5-flash` | Por defecto. Razona por defecto (`thinking_level`), no desactivable del todo |
| 2026-07-02 | Anthropic | `claude-opus-4-8` | Alternativa vía `LLM_PROVIDER=anthropic`. Sin `thinking` |
| 2026-09-28 | Anthropic | `claude-opus-4-8` | Pasa a razonar: `thinking: {type: "adaptive"}`, encendido por defecto (`ANTHROPIC_THINKING`) |
| 2026-09-30 | DeepSeek | `deepseek-flash` | Tercera ruta, vía `LLM_PROVIDER=deepseek` y el SDK de Anthropic (endpoint compatible). Razona siempre; no se configura. Techo de tokens propio (`DEEPSEEK_MAX_TOKENS`) |
| ≤ 2026-10-08 | DeepSeek | `deepseek-flash` | **Proveedor en ejecución** del servicio: `LLM_PROVIDER=deepseek` en el `.env` (comprobado el 2026-11-25). Todos los análisis reales desde el 2026-10-08 son suyos. La fecha exacta del cambio no consta |

**Cambio de proveedor (§7.3):** cambiar `LLM_PROVIDER` es un cambio evaluable, no un ajuste.
Las dos rutas no están en igualdad de condiciones —Gemini razona y Anthropic no—, así que
cualquier comparación de calidad entre ellas debe tenerlo en cuenta. Documentar aquí la
evaluación antes de adoptar un cambio de forma general.

> **Enmienda del 2026-11-25.** La asimetría de la frase anterior quedó **cerrada el
> 2026-09-28**: desde entonces razonan las dos rutas, y desde el 2026-09-30 también DeepSeek,
> la tercera. Ver las filas nuevas de la tabla.
>
> Y una brecha frente a este mismo párrafo: el servicio **pasó a DeepSeek** y aquí no consta
> ninguna evaluación previa del cambio. Queda pendiente de registrar (o de hacer) como entrada
> nueva.

## 3. Parámetros de reproducibilidad (§7.2)

Lo que hace falta para reproducir o auditar un diagnóstico concreto:

Todo lo del trace vive en `incidents/<id>.json` → **`traces[]`**, una entrada por
pasada del análisis. Era un objeto suelto (`trace`) hasta el 2026-09-28; los
expedientes anteriores conservan esa forma y **no se migran** (base §1.7), así que
se leen como la pasada 1.

| Parámetro | Dónde está, dentro de `traces[n]` |
|---|---|
| System prompt | `workflow.SYSTEM_PROMPT`, y copiado en `system_prompt` |
| Prompt del Step 3 | `step3_prompt` |
| Respuesta del Step 4 | `step4_response` |
| Prompts y respuestas del Step 6 | `step6_prompts` / `step6_responses` |
| Por qué se buscó, o por qué no | `puerta_busqueda` (su prompt y su respuesta) |
| Las vueltas del bucle de búsqueda | `busqueda` (prompt y respuesta de cada una) |
| Qué se buscó y con qué desenlace | `busqueda_resultado` |
| Sospechas que no se llegaron a atender | `sospechas_pendientes` |
| Causas que el código no autorizó | `causas_no_autorizadas` (repetidas o fuera de presupuesto) |
| Modelo y proveedor | `diagnostico._ai_generated` (fuera del trace) |
| Payload original | `payload` (fuera del trace) |

Los cinco campos de búsqueda son de la Fase 3 y existen por lo mismo que los
demás: son **decisiones del modelo que cambian lo que se hace**. La puerta puede
suprimir el paso entero, y hasta el 2026-09-29 lo hacía sin dejar constancia —
averiguar por qué obligó a reproducir la llamada con las mismas entradas.

Un trace se guarda **también cuando la pasada falla**. No es un detalle de
completitud: de `busqueda_resultado` sale que un reanálisis posterior no repita
una búsqueda ya hecha, y una pasada que buscó y luego murió es exactamente el
caso donde eso importa.

**Temperature no se fija** en ninguna de las dos rutas: se usa el valor por defecto del
proveedor. La reproducción exacta no está garantizada; la auditoría de *qué se envió y qué
se recibió*, sí.

> **Enmienda del 2026-11-25**, contrastada con `incidents.py` y `webhook.py`:
>
> 1. **Fecha.** `traces[]` sustituyó a `trace` el **2026-09-23**, no el 2026-09-28 (docstring de
>    `incidents.traces()`).
> 2. **«No se migran» es cierto solo a medias.** No hay migración en bloque, y un expediente
>    antiguo que no se vuelve a escribir conserva `trace`. Pero si se vuelve a escribir (un
>    reanálisis, un fallo), `mark()` y `marcar_fallo()` pasan el `trace` suelto a `traces[0]` y
>    **borran el campo antiguo**. Lo mismo pasa con `diagnostico` → `diagnosticos`. Para
>    auditar, el contenido es el mismo; la forma del fichero, no.
> 3. **Modelo y proveedor** está ahora en `diagnosticos[n]._ai_generated`, uno por pasada.
> 4. ⚠️ **Defecto abierto: el modelo que se registra con DeepSeek es falso.** `webhook.py`
>    escribe `GEMINI_MODEL` si el proveedor es `gemini` y `ANTHROPIC_MODEL` en cualquier otro
>    caso. Con `LLM_PROVIDER=deepseek` el expediente dice `"provider": "deepseek", "model":
>    "claude-opus-4-8"`, cuando el modelo llamado fue `DEEPSEEK_MODEL` (`deepseek-flash`).
>    Afecta a los cuatro expedientes reales analizados con DeepSeek: `83a851414036__…`,
>    `913daf118753__20261008T085500Z`, `913daf118753__20261008T102410Z` (sus dos pasadas) y
>    `913daf118753__20261028T231000Z`. En esos ficheros el dato fiable es el **proveedor**; el
>    modelo hay que tomarlo de esta sección (§2). Los expedientes no se corrigen a mano (§1.7);
>    cuando se arregle el código, anotarlo aquí con fecha.
> 5. **Temperature** tampoco se fija en la tercera ruta (DeepSeek).

> **Enmienda del 2026-11-25 (2): el defecto del punto 4 está corregido.** La etiqueta ya no
> decide el modelo por su cuenta: `webhook.py` lo pide a `llm_client.modelo_en_uso()`, que lo
> saca de la misma tabla (`_MODELO_DE`) que nombra la variable que cada `_generate_*()` pasa
> como `model=`. Lo protege la suite nueva `tests/test_etiqueta_ia.py`:
>
> - etiqueta un análisis con cada uno de los tres proveedores, usando nombres de modelo
>   inventados para no depender del `.env`;
> - comprueba que la tabla coincide con la llamada real de cada proveedor;
> - y comprueba que `generate()` y la tabla aceptan los mismos proveedores.
>
> Se verificó que la suite **falla con el código antiguo** (en una copia, con la línea
> anterior restaurada) y pasa con el nuevo. Los expedientes escritos antes siguen con el modelo
> equivocado y no se tocan (§1.7). **El servicio en marcha no lo tiene hasta que se
> reinicie**, y hasta entonces seguiría etiquetando mal un análisis nuevo con DeepSeek.

> **Enmienda del 2026-11-25 (3): el punto 1 de la primera enmienda estaba mal.** El texto original
> de esta sección (2026-09-28) era el correcto. El 2026-09-23 es cuando una prueba de punta a punta
> **destapó** que un reanálisis pisaba el `trace`, y ese mismo día quedó escrito que el arreglo
> «no está hecho» (`prompts-revision/e2e/INDICE.md`). La lista `traces[]` llegó con la Fase 3, el
> 2026-09-28, como dice `AGENTS.md`. El docstring de `incidents.traces()`, que llevó al error,
> se ha corregido.

> **Enmienda del 2026-11-25 (4): `prompts-revision/` es solo local.** Está en `.gitignore` porque
> lleva la estructura del AF y las rutas de PI. La evidencia que se cita de ahí en §8 existe en
> el servidor, no en el repositorio, y quien lo clone no la tendrá.

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
| Documentación pública de DeepSeek (`api-docs.deepseek.com`), 2026-09-30 | Documentación de terceros | Endpoint compatible con Anthropic, IDs de modelo, comportamiento del razonamiento |
| Expedientes de `incidents/` del servicio, 2026-11-24 → 2026-11-25 | Datos operacionales de planta y **texto escrito por operarios** (evidencia y sospecha de los veredictos) | Verificar las afirmaciones de la documentación contra los casos reales |

**Ningún dato personal** en ninguna de esas fuentes. Los datos de planta son magnitudes de
proceso y nombres de activo.

> **Enmienda del 2026-11-25.** Desde la Fase 3 los expedientes llevan texto libre escrito por
> operarios. En los revisados no aparece ningún nombre de persona, pero ya no es cierto por
> construcción, como sí lo era al escribir el párrafo anterior: lo es por norma de uso
> (`AI-GOVERNANCE.md` §6, punto 3).

## 6. Verificación de dependencias introducidas con asistencia de IA (§1.3)

Revisadas el 2026-09-07, repasadas el 2026-09-30. Todas fijadas a versión exacta en
`requirements.txt` (base §5).

| Dependencia | Versión | Licencia | Valoración |
|---|---|---|---|
| `fastapi` | 0.138.2 | MIT | Mantenimiento muy activo, adopción masiva |
| `uvicorn[standard]` | 0.49.0 | BSD-3-Clause | Servidor ASGI de referencia, mismo ecosistema |
| `google-genai` | 2.10.0 | Apache-2.0 | SDK oficial de Google |
| `anthropic` | 0.115.0 | MIT | SDK oficial de Anthropic. Sirve a **dos** proveedores: `LLM_PROVIDER=anthropic` y `LLM_PROVIDER=deepseek`, que publica un endpoint compatible con ese protocolo. Reutilizarlo fue deliberado: DeepSeek también admite el SDK de OpenAI, y añadirlo habría metido una novena dependencia para no ganar nada |
| `python-dotenv` | 1.2.2 | BSD-3-Clause | Estándar de facto para `.env` |
| `pytz` | 2026.2 | MIT | Misma versión que usa `aveva-pi-mcp`, por coherencia de fechas |
| `mcp` | 1.28.1 | MIT | SDK oficial de Model Context Protocol |
| `tenacity` | 9.1.4 | Apache-2.0 | Ya venía como dependencia transitiva de `google-genai`; se declara explícita al usarse directamente |

Todas con licencia permisiva y compatible con Apache-2.0 (la de este recurso). Ninguna
copyleft.

> **Repaso del 2026-11-25:** `requirements.txt` coincide con la tabla, las mismas ocho
> dependencias y las mismas versiones. Sin cambios.

**Siguen siendo ocho después de añadir DeepSeek** (2026-09-30). Merece la pena dejarlo escrito
como criterio: un proveedor nuevo no justifica por sí solo una dependencia nueva si el protocolo
que habla ya está cubierto por una que está declarada y revisada.

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
| 2026-09-08 | **Interruptor de parada (§9.5)** | Servicio real, con `WORKFLOW_ENABLED=false` | Notificación aceptada y registrada como `pausado`, `BLOCKED` en el audit trail y **cero entradas de Steps 2-6**: no se llamó al modelo ni a PI. Destapó que un incidente `pausado` no se puede relanzar |
| 2026-09-08 | Carga de configuración desde `.env.example` | Subproceso con el ejemplo copiado tal cual | Seguir la instrucción del README dejaba la app sin arrancar (`ValueError` en `int('')`). Corregido con `_entero()` y cubierto por `tests/test_config.py` |
| 2026-09-08 | Portabilidad de las suites | GitHub Actions, ubuntu-latest | El gate detectó que 4 suites llevaban la ruta de Windows cableada: `ModuleNotFoundError` en Linux. Corregido y protegido con una comprobación en `run_all.py` |
| 2026-09-07 | Suites completas | Local, con dobles | `python tests/run_all.py` — 8 suites, 260 comprobaciones |
| 2026-09-07 | **Ciclo completo Steps 1-6 con el código actual** | PI System, Neo4j y Gemini reales | Alerta de `Hydraulic Efficiency` (StartTime 2026-09-06T10:49:00Z). 2 min 23 s. 22 de 22 variables autorizadas. **Primera vez que el modelo ejerce la ampliación de ventana**: pidió `7d` alegando un hueco de datos. Log: 56 líneas, 0 no-JSON. Audit: 6 entradas |
| 2026-09-23 | **Reanálisis de punta a punta** | AF, PI y `gemini-2.5-flash` reales, por los endpoints reales | `prompts-revision/e2e/INDICE.md`: alerta → análisis → veredicto → reanálisis, 90 s + 90 s, 4 llamadas al modelo. El modelo contrastó la sospecha del operario y la rechazó. El veredicto lo escribió quien hizo la prueba, no alguien de planta. Destapó que un reanálisis pisaba el `trace` de la pasada anterior (de ahí `traces[]`) |
| 2026-10-08 | **Primer reanálisis real desde la pantalla, con búsqueda** | Servicio real, DeepSeek | `913daf118753__20261008T102410Z`. Causa descartada a las 15:41 con evidencia y una sospecha sobre el nivel del intake de la WWTP. La puerta abrió la búsqueda: 5 vueltas, presupuesto agotado (`sin_presupuesto`), y el reanálisis terminó sin lo que buscaba, como está previsto. A las 16:50 se **confirmó** otra causa de la primera pasada. Primera causa confirmada del proyecto; no consta que viniera de una inspección de mantenimiento |
| 2026-09-07 → 2026-10-28 | Análisis reales terminados | Servicio real | Cinco en `incidents/`: uno con Gemini (2026-09-07) y cuatro con DeepSeek. Uno reanalizado. Dos bombas (`PS20102 A03 PS02 Pump 02` y `PS20101 A03 PS01 Pump 01`) y **todos de `Hydraulic Efficiency`**. Además hay cinco en `fallido` (2026-10-06, dos del 2026-10-08, 2026-11-11 y una prueba con fecha de 1970), y el `pausado` del 2026-09-08 sigue sin relanzar |
| 2026-11-24 | Suites completas | Local, con dobles | `python tests/run_all.py` — 18 suites, todas en verde. `run_all.py` no cuenta comprobaciones. No se volvió a mirar el CI |
| 2026-11-25 | Etiqueta `_ai_generated` contra el modelo realmente llamado | Expedientes reales de `incidents/` | **Falla con DeepSeek**: registra `claude-opus-4-8`. Ver la enmienda del §3, punto 4 |
| 2026-11-25 | Corrección de la etiqueta del modelo | Local, con dobles | `tests/test_etiqueta_ia.py`: falla con el código anterior y pasa con el corregido. `python tests/run_all.py`: **19 suites**, todas en verde. Falta reiniciar el servicio para que se aplique |
| 2026-11-25 | Separación del servidor de pruebas | Servidor de pruebas (8099), reiniciado con `LOG_FILE` y `AUDIT_FILE` propios | Una notificación vacía se rechazó y quedó `BLOCKED` en `dev-fixtures/audit.jsonl`; el `audit.jsonl` real siguió con 20.279 líneas. Hasta entonces el de pruebas escribía en el audit real: quedan 106 entradas de `dev-fixtures/` en él. El servicio real (8090) **no** se pudo reiniciar: hace falta una consola de Administrador |
| 2026-11-25 | Aviso al arrancar si un servidor de pruebas comparte log o audit con el servicio | Local, y subproceso con las variables reales | `tests/test_config.py` §17 (seis comprobaciones). Con solo `INCIDENTS_DIR` cambiado, `config.ficheros_compartidos_con_el_servicio()` devuelve `LOG_FILE` y `AUDIT_FILE`; con las tres, nada; con la configuración del servicio, nada. 19 suites en verde |

⚠️ **Limitación conocida:** los **dos** ciclos completos reales son del mismo activo y el
mismo KPI, y ninguna causa propuesta ha sido confirmada. No es evidencia suficiente sobre la
calidad del diagnóstico; ver el punto 8 de la
revisión del propietario y `AI-GOVERNANCE.md` §2 (límites conocidos).

> **Enmienda del 2026-11-25.** Ya no son dos ciclos ni ninguna confirmada: hay cinco análisis
> terminados sobre **dos** bombas y **una** causa confirmada desde la pantalla (filas de arriba).
> Sigue en pie lo principal: **un solo KPI** y ninguna confirmación de mantenimiento tras
> inspeccionar el equipo. Sigue sin ser evidencia suficiente sobre la calidad del diagnóstico.

---

## Cómo añadir una entrada

Al terminar una sesión de trabajo asistida con IA:

1. Si has usado un modelo o herramienta distinta a los de §1, añade una fila.
2. Si has dado a la IA acceso a una fuente de datos nueva, añade una fila en §5.
3. Si has incorporado una dependencia nueva, añade una fila en §6 con su verificación.
4. Si has validado funcionalmente algo, añade una fila en §8.

No edites filas existentes.

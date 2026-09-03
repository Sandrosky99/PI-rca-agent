# PI RCA Workflow — Instrucciones para Claude Code

Workflow de análisis de causa raíz (RCA) para alertas operacionales de AVEVA PI System.
Cuando PI detecta una desviación en un KPI monitorizado, este sistema recibe la notificación,
consulta la estructura real del Asset Framework, obtiene datos históricos de PI Web API y usa un
LLM para diagnosticar causas raíz y proponer acciones correctivas.

---

## Terminología: esto es un workflow, no un agente

**La arquitectura es la de un workflow**, y desde el 2026-09-03 los nombres lo dicen también: el
módulo es `workflow.py`, el log imprime `WORKFLOW RCA` y el servicio es `RCA-Workflow-Webhook`.
**Queda una excepción: el repositorio sigue llamándose `PI-rca-agent`** — renombrarlo en GitHub es
una acción pendiente. La distinción importa al leer y modificar el código, porque induce a buscar
un bucle de decisión que no existe:

| | Este sistema | Lo que haría un agente |
|---|---|---|
| Quién decide el siguiente paso | `run_rca_analysis()`: los 6 steps están escritos en orden en el cuerpo de la función | El modelo, en cada vuelta de un bucle |
| Llamadas al LLM | Exactamente 2, en posiciones fijas (Step 4 y Step 6) | Un número indeterminado |
| Acceso a herramientas | **Ninguno.** `llm_client.generate()` nunca pasa el parámetro `tools` | El modelo recibe las tools y decide cuál invocar |
| Quién llama a los MCP servers | El código Python (`graph_client.py`, `pi_client.py`) | El modelo, vía tool use |
| Reintentos y recuperación | Código determinista (`tenacity`, `_query_with_retry()`) | El modelo observa el error y decide |
| Condición de parada | La última línea de la función | El modelo decide cuándo ha terminado |

**El matiz:** el modelo *sí* decide algo — en el Step 4 elige qué variables consultar, y eso cambia
qué datos se piden a PI. Pero cambia el *contenido* de un paso que se iba a ejecutar igualmente, no
el *orden* ni la *existencia* de los pasos. El grafo de control es idéntico para todas las alertas.
Eso es selección de datos dentro de una ruta fija, no autonomía.

El patrón es **encadenamiento de prompts** (*prompt chaining*): la salida de la primera llamada
alimenta una consulta determinista de datos, y el resultado de esa consulta alimenta la segunda,
con una puerta de código entre medias que valida y filtra. Es el patrón correcto para este problema
—la tarea está bien definida y su secuencia se conoce de antemano—, así que no hay razón para pagar
la latencia, el coste y la impredecibilidad de un bucle autónomo.

Si algún día se quisiera convertir en un agente de verdad: habría que dar las tools de los MCP
servers directamente al modelo y envolverlo en un bucle. Se ganaría poder perseguir una hipótesis
inesperada (ampliar la ventana, saltar a un activo vecino); se pagaría latencia, coste variable por
alerta y un flujo mucho más difícil de auditar.

---

## Estado del proyecto

| Step | Descripción | Estado |
|---|---|---|
| 1 | Recibir notificación HTTP POST de PI System | ✅ Completado |
| 2 | Consultar la estructura del AF (afkg-graph-mcp) para el `Asset`/`Subsystem` de la alerta → `main_asset_context`/`nearby_elements_context` con atributos reales (`piApiPath`) | ✅ Completado (`graph_client.py`) |
| 3 | Preparar el mensaje de 4 secciones (objetivo, payload, modelo de datos, JSON del AF) para el modelo | ✅ Completado (`build_analysis_context()`) |
| 4 | El modelo identifica, de esas variables reales, cuáles necesita analizar | ✅ Completado (parseado y usado en el Step 5) |
| 5 | Obtener datos históricos de PI vía MCP Server (aveva-pi-mcp) | ✅ Completado (`pi_client.py`) |
| 6 | El modelo produce diagnóstico y recomendaciones | ✅ Completado (`build_diagnosis_context()`) |

**Los 6 steps están implementados.** El flujo se recorre entero y produce un JSON con 2–3 causas
raíz, cada una con su explicación y su acción recomendada.

**Validación:** un ciclo completo registrado en `webhook.log` el 2026-08-20, con una alerta real de
`Hydraulic Efficiency` sobre `PS20102 A03 PS02 Pump 02`. El diagnóstico apuntó a desgaste interno
de la bomba (impulsor y anillos de desgaste). Validación real pero no exhaustiva: falta ejercitarlo
con otros KPIs y otros tipos de activo.

### Próximo paso

**Decidir el canal de salida del diagnóstico.** Desde el 2026-09-03 el diagnóstico se guarda en el
fichero del incidente (`incidents/<id>.json`), junto al payload que lo originó, así que ya no se
pierde en el log. Lo que falta es el **aviso**: correo al ingeniero de proceso, interfaz web, o
devolverlo a PI como anotación del event frame. Es una decisión de producto, no técnica.

Ese fichero es además la base natural de la revisión humana: añadirle campos de `revision` y
`causa_confirmada` no requiere diseñar nada nuevo.

Otros cabos sueltos, por orden de urgencia:

1. **El servicio no está registrado en Windows.** El proceso corre a mano desde el 2026-08-07
   (uvicorn, PID 29548). `RCA-Workflow-Webhook` no aparece en `services.msc`: si la máquina
   reinicia, el webhook no vuelve solo y PI enviará a un puerto muerto. Ejecutar
   `install_service.bat` como administrador.
2. **La llamada a Anthropic no usa adaptive thinking**, mientras que Gemini sí razona por defecto
   (ver «Asimetría de razonamiento» más abajo). Activar `thinking: {type: "adaptive"}` en
   `_generate_anthropic()` igualaría las dos rutas, y `LLM_MAX_TOKENS=16000` ya deja sitio para
   ello. Pendiente de decidir: sin la evaluación del punto 8 no habría forma de medir si mejora el
   diagnóstico. Si se activa, hacerlo detrás de un flag y en ambos proveedores.
3. **Renombrar el repositorio** en GitHub: `PI-rca-agent` → `PI-rca-workflow`. Es lo único que
   sigue diciendo «agente» tras el renombrado del 2026-09-03. GitHub deja una redirección, así que
   el remoto local sigue funcionando, pero conviene actualizarlo con `git remote set-url`.
4. **Las pruebas no están en el repositorio.** Las cuatro suites (~110 comprobaciones sobre la
   puerta del Step 4, el catálogo de ventanas, el bucle de reajuste y el registro de incidentes)
   viven fuera del proyecto. Deberían entrar como `tests/`.
5. **Ampliar la validación** a más KPIs y tipos de activo antes de dar por buena la calidad del
   diagnóstico de forma general.

---

## Arquitectura

```
PI System (172.21.28.55)
    │ HTTP POST → {"KPIName": ..., "Asset": ..., "KPI": ..., "Limit": ...}
    ▼
webhook.py  (FastAPI :8090)          Step 1 ✅
    │ background task
    ▼
workflow.py  (run_rca_analysis)      ← el control de flujo vive aquí, de principio a fin
    ├── Step 2 ✅ graph_client.py  → afkg-graph-mcp  → estructura real del AF
    ├── Step 3 ✅ build_analysis_context()           → mensaje de 4 secciones
    ├── Step 4 ✅ llm_client.generate()              → qué atributos reales necesita
    ├── Step 5 ✅ pi_client.py     → aveva-pi-mcp    → datos históricos de PI
    └── Step 6 ✅ llm_client.generate()              → diagnóstico final (JSON)
                     │
                     ▼
        incidents/<id>.json  +  log   ← falta un canal de aviso (correo, web...)
```

---

## Detalle de cada step

### Step 1 — Recepción (`webhook.py`)

FastAPI escuchando en `:8090`. Responde `202 Accepted` de inmediato y lanza el análisis como
background task para no dejar a PI esperando. Registra siempre el request completo (cabeceras +
body raw) antes de parsearlo, y acepta el body aunque no sea JSON válido, guardándolo como texto
para diagnóstico. Timeout de 10 s leyendo el body, por si PI no cierra la conexión.

Endpoints: `GET /health`, `POST /notification`, `GET /notifications/history` (últimas 50 en
memoria, se pierde al reiniciar), `GET /docs` (Swagger autogenerado).

El log va a consola **y** a `webhook.log` con rotación a los 10 MB y 5 ficheros de respaldo. El
rotativo se añadió el 2026-08-06 porque una Notification Rule de PI con `NonrepetitionInterval=0`
genera una notificación nueva en *cada* evaluación periódica de la alarma, no solo al cruzar el
umbral (visto en real).

#### Registro de incidentes: deduplicación y persistencia (`incidents.py`, añadido 2026-09-03)

Resuelve dos problemas de fiabilidad que comparten el mismo estado.

**Deduplicación.** PI puede enviar varias notificaciones del mismo incidente — con
`NonrepetitionInterval=0`, una por cada evaluación de la alarma. Sin guarda, cada una lanzaría un
análisis completo, con su coste de LLM, sus subprocesos MCP y diagnósticos potencialmente
contradictorios sobre el mismo suceso.

⚠️ **Esto se arregla ANTES en PI**, con el `NonrepetitionInterval` de la Notification Rule y el
deadband del análisis que calcula el KPI. Lo del código es una red de seguridad, no el mecanismo.

**Identidad de un incidente — no es el payload completo.** Los campos se reparten en tres grupos:

| Grupo | Campos | Papel |
|---|---|---|
| **Fijos** | `Asset`, `KPIName`, `LimitThresholdType`, `Subsystem`, `System`, `Plant` | Identifican el elemento y qué alarma suya ha saltado. Forman la clave. |
| **Volátil** | `KPI`, `Limit` | `KPI` es el valor actual y cambia entre re-evaluaciones (58.4 → 58.1 → `"No Result"`). Fuera de la clave: incluirlo no deduplicaría nada. |
| **Cercano** | `StartTime` | No se repite exacto, pero queda próximo: la re-notificación llega minutos después. No sirve como igualdad, sirve como proximidad. |

```
incidents/a3f19c2b4e01__20260819T221000Z.json
          └─ hash de los 6 campos fijos   └─ StartTime
```

Cada campo fijo se **normaliza** (espacios colapsados, sin distinguir mayúsculas) antes de
mezclarlo en el hash. Con una clave de seis campos basta que uno llegue con un espacio de más para
que el hash cambie y la deduplicación falle justo cuando debía actuar.

Dos capas, **por orden de importancia**:

1. **Enfriamiento** (`INCIDENT_COOLDOWN_MINUTES`, 20) — misma identidad dentro de la ventana, aunque
   el `StartTime` sea otro. **Es la capa que hace el trabajo**: corta la re-notificación que llega
   dos minutos después con un valor de KPI ligeramente distinto.
2. **Clave exacta** — misma identidad *y* mismo `StartTime` al carácter. Red secundaria: solo
   dispara si PI reenvía literalmente el mismo POST sin recalcular el campo.

**Por qué ficheros y no SQLite.** El sistema de ficheros ya da la primitiva necesaria:
`open(ruta, "x")` es una creación exclusiva que falla si el fichero existe. **Eso es la reserva del
incidente**, atómica y sin ventana de carrera entre consultar e insertar — y sigue funcionando con
varios workers de uvicorn, donde un diccionario en memoria no serviría. Además el fichero ya es el
registro del caso: cuando llegue la revisión humana, se le añaden campos al mismo JSON.

Lo que se pierde: no hay consultas ni agregación. Para las métricas de evaluación habrá que leer
todos los ficheros y agregarlos en Python — trivial con unas pocas alertas al día. Migrar a SQLite
después es mecánico: cada fichero es una fila.

**Persistencia.** Estados: `recibido` → `analizando` → `finalizado` / `fallido`. Las escrituras son
atómicas (temporal + `os.replace`, que también lo es en Windows). Al arrancar,
`sweep_interrupted()` marca como `interrumpido` lo que quedó en curso: si hay un incidente en
`analizando`, el proceso murió durante su análisis y nadie lo va a terminar. **No se relanzan
automáticamente** a propósito — un payload problemático provocaría un bucle en cada arranque.

**El diagnóstico se guarda en el fichero del incidente**, no solo en el log. Es el primer paso
hacia un canal de salida de verdad. Para ello `run_rca_analysis()` devuelve ahora el diagnóstico
(o `None` si cortó de forma controlada).

**Sobre el enfriamiento (20 min).** El caso a cortar son re-notificaciones cada pocos minutos, así
que 20 va sobrado y cubre unas diez repeticiones. Se bajó desde 60 el 2026-09-03: una hora
suprimiría también una desviación **genuinamente nueva** sobre el mismo activo y KPI, y ese margen
no hacía falta para el escenario real.

`incidents/` está en `.gitignore`: cada fichero guarda el payload completo y el diagnóstico, con
datos de planta.

### Step 2 — Estructura real del AF (`graph_client.py`, implementado 2026-07-13)

**Motivo del cambio (2026-07-03):** con el flujo original, el modelo proponía nombres de atributos
"de libro" (p.ej. `Discharge Flow Rate`) que no necesariamente existen con ese naming en la
estructura real del AF, y el sistema no tenía forma de saber si esas variables estaban realmente
disponibles. Se añadió este paso intermedio para consultar primero la estructura real del AF y
pasarle esa lista de atributos disponibles al modelo, de forma que elija **solo entre lo que
existe** en vez de proponer variables que luego no se podrán consultar. Es el cambio estructural
más importante del proyecto.

`graph_client.py` lanza `afkg-graph-mcp` como subproceso (protocolo MCP sobre stdio, vía el SDK
`mcp`) y usa `graph_neighborhood` para recorrer recursivamente el árbol PARENT_OF del `Asset`
(→ `main_asset_context`) y del `Subsystem` (→ `nearby_elements_context`, excluyendo la rama del
propio `Asset` para no duplicarla). El recorrido es recursivo porque `graph_neighborhood` solo
devuelve el vecindario directo, no el árbol completo, y los activos tienen varios niveles.

- Los nodos organizativos sin atributos propios (`Meters`, `Indicators`) no generan grupo propio —
  su nombre solo queda reflejado en el breadcrumb (`>`) de sus descendientes, que es donde aporta
  significado.
- Se filtran los atributos de metadatos/bookkeeping (`Area Code`, `Asset Code`, `Path`,
  `Tag Name`, `Manufacturer`, etc. — ver `_METADATA_ATTRS`) que no aportan al diagnóstico.
- `description`/`uom` vacíos se dejan explícitos (`""`), no se omite la clave: el AF simplemente no
  documenta ese campo, no significa que el atributo no exista.
- La desambiguación entre elementos con nombre repetido (p.ej. varios "Level Alert"/"Reference"
  bajo distintos indicadores) usa el `path` único que cada hijo ya trae en la respuesta de su
  padre, en vez de reconstruirlo a mano. `graph_neighborhood` empareja por nombre exacto y sin esto
  devuelve el elemento equivocado.
- `_MAX_DEPTH = 8` como salvaguarda ante ciclos o jerarquías anómalas.

Probado end-to-end contra el servidor y Neo4j reales el 2026-07-13 con `PS20102 A03 PS02 Pump 02` /
`Pumping Station 01` (15 grupos en `main_asset_context`, 5 en `nearby_elements_context`).

### Step 3 — Construcción del mensaje (`build_analysis_context()`, implementado 2026-07-13)

Construye `claude_prompt` con 4 secciones fijas/dinámicas:

1. **Objetivo de la interacción** (`_OBJECTIVE_SECTION`, fijo) — incluye la jerarquía de prioridad
   que debe seguir el modelo al elegir variables: (1) el KPI en alerta y su `Reference`/baseline,
   (2) otros indicadores calculados del mismo activo que compartan entradas físicas, (3) los
   meters/atributos físicos que alimentan esos cálculos, (4) el nivel superior solo si aporta una
   causa compartida plausible. Incluye reglas anti-duplicado: de cada indicador quedarse solo con
   su resultado propio e ignorar las copias de magnitudes físicas de entrada; y si la misma
   magnitud aparece en varios sitios del árbol, elegir una sola.
2. **Payload de la notificación** (`summary`, dinámico) — jerarquía, umbral explicado en lenguaje
   natural, hora UTC+local.
3. **Explicación del modelo de datos** (`_DATA_MODEL_SECTION`, fijo) — describe
   `main_asset_context`/`nearby_elements_context` y el significado de cada campo, e impone la regla
   estricta de elegir exclusivamente entre los `piApiPath` listados.
4. **JSON de `af_context`** (dinámico).

Seguido de `_FINAL_INSTRUCTION`, que fija el formato de respuesta esperado.

El rol y dominio del sistema (EDAR + bombeos externos, nunca genérico) están fijados aparte en
`SYSTEM_PROMPT`, enviado vía el parámetro `system`/`system_instruction` en **todas** las llamadas al
modelo (Steps 4 y 6). Está separado del mensaje dinámico a propósito: la API es *stateless* entre
llamadas, no hay memoria real entre el Step 4 y el Step 6 salvo lo que se reenvíe en cada request,
así que el rol se fija como constante en vez de repetirlo a mano en cada prompt.

### Step 4 — El modelo elige las variables (implementado 2026-07-13, endurecido 2026-07-28)

`run_rca_analysis()` llama a `llm_client.generate(SYSTEM_PROMPT, context["claude_prompt"])`.

Formato de respuesta esperado (`_FINAL_INSTRUCTION`): un objeto JSON con dos claves.

- `variables`: array de `{"element", "piApiPath"}` — los atributos cuyos datos hay que consultar.
- `missing_variables`: array de strings en lenguaje natural describiendo qué variable adicional,
  **que no aparece en el AF**, ayudaría a precisar el diagnóstico. Es solo informativo: no se usa
  para consultar PI (así se evita meter nombres sin `piApiPath` real en el bucket del Step 5), pero
  sí se arrastra al Step 6 como limitación declarada del diagnóstico.

**Guardarraíles (el prompt no es infalible):**

- `_extract_json_payload()` busca las llaves/corchetes más externos del primer objeto o array JSON
  que aparezca, en vez de exigir que el texto completo sea exactamente un bloque de fence. Así da
  igual si el modelo lo envuelve en ```` ```json ````, con texto antes/después, o a pelo. Visto en
  pruebas reales.
- El parseo tolera también un array plano en vez del objeto de dos claves, por si el modelo ignora
  el esquema.
- Si `llm_client` agota los reintentos, se captura `LLMGenerationError` y el análisis se corta de
  forma controlada — sin dejar que el traceback se propague sin control en la background task.

#### Puerta de autorización antes del Step 5 (añadida 2026-08-27)

`_DATA_MODEL_SECTION` le dice al modelo, como «regla estricta», que elija solo entre los
`piApiPath` del `af_context`. **Eso es una instrucción, no una garantía.** Antes la respuesta se
pasaba tal cual a `query_by_path`, y el fallo aparecía tarde y mal: `aveva-pi-mcp` resuelve *todos*
los paths a WebID antes de consultar nada, así que una sola ruta inventada tumbaba el batch entero.

`_validate_selected_variables(parsed_response, af_context)` invierte la responsabilidad — **el
modelo propone, el código autoriza**. Comprobaciones, en orden:

1. **Forma de la respuesta.** Objeto de dos claves; se tolera un array plano (esquema anterior).
   Cualquier otra cosa aborta el análisis.
2. **Cada entrada** debe ser un objeto con un `piApiPath` de texto no vacío.
3. **Pertenencia a la lista blanca.** `_collect_authorized_paths()` indexa todos los `piApiPath` de
   `main_asset_context` + `nearby_elements_context`; lo que no esté ahí se descarta.
4. **Canonicalización.** El *lookup* usa `_normalize_pi_path()` (colapsa espacios, ignora
   mayúsculas) porque el modelo reescribe el path más a menudo de lo que admite el prompt y un
   espacio de más no debería costar una variable legítima. **A PI siempre se le manda la cadena
   canónica del AF**, nunca la del modelo.
5. **Duplicados y claves no previstas** se descartan; la variable se normaliza a `element` +
   `piApiPath`.
6. **Tope `MAX_SELECTED_VARIABLES`** (40 por defecto): se conservan las primeras, porque
   `_OBJECTIVE_SECTION` pide al modelo que priorice.

**Calibración del tope (2026-08-27):** reproduciendo la ejecución real del 2026-08-20 contra esta
puerta, de **164 atributos** en la lista blanca el modelo seleccionó **30, todas legítimas** — cero
rechazos por validación. Un tope de 20 habría recortado 10, incluida la comparación con la bomba
hermana (`PS20101 A03 PS01 Pump 01`: `Flow`, `Active Power`, `Hydraulic Effiency`), que es
justamente la prioridad 4 que pide `_OBJECTIVE_SECTION`. De ahí el 40: margen sobre el caso real
sin dejar de frenar una respuesta desbocada.

⚠️ **Esta puerta NO sustituye a `_query_with_retry`.** Son dos fallos distintos y hay que mantener
los dos guardarraíles:

| | Ruta inventada | WebID que no resuelve |
|---|---|---|
| Qué pasa | El modelo se saca un `piApiPath` que no está en el AF | El path existe en el grafo AF pero ya no en PI Web API |
| Quién lo detecta | `_validate_selected_variables` (antes de salir de `workflow.py`) | `_query_with_retry` (respuesta de `aveva-pi-mcp`) |

Comprobado con el caso real: `Hydraulic Effiency|Mechanic Power`, el path que tumbó el batch el
2026-08-20, **sí está** en el `af_context` — la puerta lo autoriza correctamente y el fallo aparece
después, en PI. El grafo y PI Web API no están sincronizados, y eso solo se puede ver consultando.

**Descartar en vez de abortar es deliberado**, y es la misma política de `_query_with_retry` con los
WebID: una ruta inventada no debería invalidar las que sí eran buenas. Solo se lanza
`VariableSelectionError` (y se corta antes de lanzar el subproceso MCP) si no sobrevive **ninguna**
variable, o si el `af_context` viene vacío — sin lista blanca no hay nada que autorizar.

Todo lo descartado queda en el log con su motivo. Ese log es el que permite detectar si el prompt
se está degradando: si empiezan a aparecer WARNINGs de rutas inventadas, el problema está en el
Step 3, no aquí.

### Step 5 — Datos históricos de PI (`pi_client.py`, implementado 2026-07-28)

Lanza `aveva-pi-mcp` como subproceso (MCP sobre stdio, igual que `graph_client.py`) y usa
`create_timeseries_bucket` + `query_by_path`.

- **Ventana:** `[StartTime - PI_LOOKBACK_HOURS, StartTime]` — se mira hacia atrás desde la
  detección, nunca hacia adelante (son desviaciones de KPI, degradación gradual, no picos
  instantáneos).
- **Resolución:** `PI_QUERY_INTERVAL_VALUE`/`PI_QUERY_INTERVAL_UNIT`, uniforme para todas las
  variables — un único bucket y una única llamada a `query_by_path`, para que los valores sean
  correlacionables directamente por timestamp.
- **Deduplicación** (`_dedupe_pi_paths`): el modelo a veces repite el mismo `piApiPath` bajo
  distintos "elementos" lógicos; se piden una sola vez, preservando el orden de aparición.

**Manejo de `piApiPath` que no resuelven a WebID (`_query_with_retry`):** `aveva-pi-mcp` resuelve
*todos* los paths a WebID antes de consultar nada; si **uno solo** falla, descarta el batch entero y
no devuelve datos ni siquiera de los que sí existen. Visto en producción el 2026-07-28: el modelo
eligió un atributo presente en el grafo AF pero ya no en PI Web API (`Mechanic Power`).
`_query_with_retry` detecta el patrón del error (`_WEBID_ERROR_PREFIX` + `_FAILED_PATH_RE`), extrae
los paths culpables, los excluye y reintenta con el resto. Cada iteración descarta al menos uno, así
que termina en como mucho `len(pi_paths)` intentos. Los excluidos se devuelven en
`excluded_piApiPaths` y se registran como WARNING.

**Parseo de la respuesta (`_parse_batch_json`):** `query_by_path` antepone una cabecera legible
("📊 Consulta Batch…", "WebIDs resueltos…", "Status: NNN") antes del JSON real, así que
`json.loads()` a secas siempre falla. Además, `/batch` de PI Web API puede devolver `207
Multi-Status` (éxito parcial) aunque cada sub-respuesta interna sea válida, y `aveva-pi-mcp` lo
etiqueta como "❌ Error en la petición" pese a que el JSON que sigue es utilizable — por eso se
prueban los dos marcadores, no solo el de éxito.

### Step 6 — Diagnóstico final (`build_diagnosis_context()`, implementado 2026-08-07)

Segunda y última llamada al modelo. El mensaje tiene 3 secciones + instrucción final:

1. **Objetivo** (`_DIAGNOSIS_OBJECTIVE_SECTION`) — pide entre 2 y 3 causas raíz ordenadas por
   probabilidad, sin rellenar hasta 3 si los datos solo sustentan una o dos con confianza
   razonable, cada una con una acción de resolución concreta.
2. **Resumen de la alerta** — se reutiliza el `summary` del Step 3 para no repetir la descripción.
   Si el Step 4 devolvió `missing_variables`, se añaden aquí como limitación conocida, para que el
   modelo module su confianza en vez de inventarlas.
3. **Datos históricos** — la salida de `_label_historical_data()`.

**`_label_historical_data()`** combina las variables del Step 4 con los valores del Step 5 y
normaliza dos casos que confundían al modelo:

- **Sin historización real:** algunos atributos del AF (típicamente `Reference` y estáticos) no son
  series temporales; PI devuelve una serie entera con el timestamp `1970-01-01T00:00:00Z`
  (`_EPOCH_MARKER`). Se marcan explícitamente como «atributo estático/de configuración, no una
  medición» en vez de mostrar una lista de valores.
- **Estados digitales:** PI los representa como un dict `{"Name": ..., "Value": <código>,
  "IsSystem": true}` — p.ej. `No Result` cuando el cálculo no pudo evaluarse. Se simplifican a su
  nombre, y el prompt indica explícitamente que se interpreten como contexto (el KPI no estaba
  disponible en ese instante), no como un fallo que haya que explicar.

**Formato de respuesta** (`_DIAGNOSIS_FINAL_INSTRUCTION`): un objeto JSON con dos claves —
`root_causes` (array de 2 o 3 objetos con `cause`, `explanation` y `recommended_action`, ordenado
por probabilidad) y `history_request` (ver abajo). Se parsea con el mismo `_extract_json_payload()`
del Step 4.

#### Ventana variable: el modelo puede pedir más histórico (añadido 2026-08-31)

Una ventana fija sirve mal a problemas de escalas distintas: un golpe de ariete se ve en minutos,
una obstrucción en horas, el desgaste en semanas. Pero hacer que el modelo elija la ventana *antes*
de ver los datos es peor — tendría que adivinar. La solución es que la pida **después**, ya
informado, y que el código decida si se la concede:

1. El Step 6 se ejecuta con `PI_LOOKBACK_HOURS` (24 h). El prompt ahora **le dice al modelo qué
   ventana y qué resolución está mirando** — antes solo recibía timestamps y tenía que deducirlo.
2. Junto al diagnóstico, devuelve `history_request`:
   `{"needed": bool, "window_option": "7d", "trend_start": "...", "reason": "..."}`. El prompt
   exige que diagnostique **igualmente** con lo que tiene; la petición es adicional, no sustitutiva.
3. `_parse_history_request()` valida la elección contra el catálogo (ver abajo) y, si estrecha,
   comprueba la cobertura de la tendencia.
4. Se repiten los Steps 5 y 6 con la ventana nueva, hasta `MAX_HISTORY_ADJUSTMENTS` veces (1 por
   defecto; `0` desactiva el mecanismo).

**Catálogo cerrado en vez de un número libre de horas.** El modelo elige un `window_option` de una
lista, no propone una cifra. Elimina de raíz las respuestas inservibles (horas como texto, o que no
cambian nada) y sobre todo **le hace visible el coste de lo que pide**: antes solicitaba horas y la
resolución se engrosaba por debajo sin que lo supiera. El catálogo lo construye
`pi_client.window_menu()` a partir de `PI_WINDOW_LADDER_HOURS`, derivando cada resolución con
`derive_interval()`, así que catálogo y cálculo real no pueden desalinearse:

| id | Ventana | Resolución | Puntos/variable |
|---|---|---|---|
| `2h` | 2 horas | 1 minuto | 120 |
| `8h` | 8 horas | 5 minutos | 96 |
| `24h` | 24 horas | 15 minutos | 96 |
| `2d` | 2 días | 30 minutos | 96 |
| `7d` | 7 días | 2 horas | 84 |
| `14d` | 14 días | 3 horas | 112 |

**La resolución es proporcional a la ventana**: los puntos por variable se mantienen entre 84 y 120
en todo el rango, así que el tamaño del prompt del Step 6 es aproximadamente constante se elija lo
que se elija. Ampliar sin tocar la resolución lo multiplicaría por el mismo factor, y son decenas
de variables. **24 h siguen saliendo a 15 min**, así que la primera pasada es idéntica a la de
antes: nada cambia hasta que el modelo pide algo.

**El catálogo va en las dos direcciones** (añadido 2026-09-01). Hacia arriba para tendencias
lentas; hacia abajo (`8h`, `2h`) porque hay modos de fallo que a 15 minutos son invisibles o quedan
solapados — cavitación, golpe de ariete, ciclado anómalo de arranques, oscilación de una válvula.
Se le ofrecen todas las opciones distintas de la actual, anotadas con su dirección («más detalle,
menos histórico» / «más histórico, menos detalle»).

⚠️ **Estrechar no es simétrico a ampliar: puede recortar la evidencia.** Ampliar solo añade
contexto, así que se concede sin más. Estrechar exige que el modelo indique en `trend_start` cuándo
arranca el cambio de tendencia, y `_enforce_trend_coverage()` comprueba **aritméticamente** que la
ventana pedida lo sigue cubriendo. El modelo aporta el dato; el código hace la comprobación, no se
fía de que la haya hecho él. Reglas:

- Sin `trend_start`, con formato no interpretable, o posterior a la detección → **no se estrecha**.
- Si la ventana pedida no cubre el inicio de la tendencia → se concede la más corta que sí lo cubra.
- Si ninguna ventana más corta lo cubre → no se estrecha.
- Nunca se estrecha por defecto: ante una petición sin opción utilizable se concede la ampliación
  mínima, no un estrechamiento.

**Protección contra oscilación.** El bucle recuerda las ventanas ya analizadas (`visitadas`) y
corta si el modelo vuelve a pedir una. Con `MAX_HISTORY_ADJUSTMENTS=1` no puede darse, pero con un
presupuesto mayor un catálogo bidireccional permitiría 24 h → 8 h → 24 h sin converger.

**Sobre los datos interpolados (aclarado 2026-09-01).** `create_timeseries_bucket` devuelve valores
a intervalo fijo, y eso es el comportamiento correcto y esperado, no una limitación que haya que
sortear. PI resuelve cada punto según el atributo `step` del PI Point:

| `step` | Tipo de señal | Qué devuelve PI en el intervalo |
|---|---|---|
| `0` | Continua / analógica (caudal, presión, temperatura) | Valor **interpolado** entre los archivados |
| `1` | Escalonada (estados, consignas, digitales) | El **último valor almacenado**, mantenido |

En ambos casos con su timestamp asociado. Lo que importa es que **`step` esté bien configurado en
PI**, y esa es una responsabilidad del modelado de datos en PI, no de este workflow: afecta por
igual a cualquier aplicación que explote esos datos, PI Vision incluida. Si una señal escalonada
está marcada como continua, PI la interpolará mal para todo el mundo, no solo aquí.

Corolario para las ventanas finas (`2h` a 1 minuto): no hay nada que verificar en el lado del
workflow. Si los datos a 1 minuto no reflejan bien un transitorio, el sitio donde mirar es la
configuración del PI Point, no el catálogo de ventanas.

**Sigue siendo un workflow.** El modelo no controla el bucle: no decide cuántas iteraciones hay
(`MAX_HISTORY_ADJUSTMENTS`), ni cuánta ventana obtiene (`PI_MAX_LOOKBACK_HOURS`), ni qué resolución
(`derive_interval`). Emite una petición estructurada dentro de un paso fijo y el código la
autoriza, recorta o deniega — la misma forma que la puerta del Step 4.

**Señal de evaluación.** Con qué frecuencia el modelo pide más ventana, y si el diagnóstico cambia
tras ampliarla, dice si `PI_LOOKBACK_HOURS` está bien dimensionado para cada tipo de alerta. Las
peticiones se registran siempre, **incluidas las denegadas por presupuesto agotado** (WARNING
distinto), para poder contarlas.

---

## Entorno técnico

### Red
- **IP servidor RCA Workflow:** `172.21.28.72`
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
| `StartTime` | Momento de detección en UTC (ISO 8601 con `Z`). Usado por `workflow.py` para la ventana temporal del análisis (conversión a local vía pytz, igual que `search_event_frames` en aveva-pi-mcp). Si falta, se aproxima con la hora actual del servidor. |
| `AssetType` | Confirmado 2026-07-02. Tipo de equipo (p.ej. `"pump"`). Genérico, no limitado a bombas. Se añade al mensaje de contexto para el modelo. |
| `AssetModel` | Confirmado 2026-07-02. Modelo/descripción del equipo (p.ej. `"single-channel centrifugal pump"`). Se añade al mensaje de contexto para el modelo. |

⚠️ **Anomalía detectada 2026-07-02:** en una prueba real, `Limit` llegó como `"1970-01-01T00:00:00Z"` en vez de un valor numérico — probable fallo de mapeo en la configuración de PI Notifications para esa alerta concreta. Revisar la variable enlazada a `Limit` en PI si se repite.

**Validación de tipos (Step 3):** `_valid_field(payload, key, expected_type)` en `workflow.py` comprueba el tipo de cada campo del payload contra el tipo esperado (`str` para nombres/jerarquía, `(int, float)` para `KPI`/`Limit`, excluyendo `bool`). Si un campo no coincide con el tipo esperado, se omite del mensaje al modelo (no se pasa el dato "roto") y se registra un `WARNING` en el log, para poder detectar fallos de configuración en PI. `build_analysis_context()` construye la frase del resumen de forma condicional según qué combinación de `KPI`/`Limit` sea válida.

---

## Proveedor de LLM

`llm_client.py` es una capa de abstracción con una única función `generate(system_prompt,
user_message) -> str` que llama a Gemini o a Claude según `LLM_PROVIDER` (`.env`), sin que el resto
del workflow conozca la diferencia. Anthropic y Google **no** comparten un estándar de API, por eso
cada proveedor tiene su propia función interna:

| | Anthropic (Claude) | Google (Gemini) |
|---|---|---|
| SDK | `anthropic` | `google-genai` |
| System prompt | parámetro `system` | `GenerateContentConfig(system_instruction=...)` |
| Mensaje | `messages=[{"role": "user", "content": "..."}]` | `contents="..."` |
| Respuesta | `response.content[0].text` | `response.text` |

Por defecto `LLM_PROVIDER=gemini` con `gemini-2.5-flash`; `validate_config()` en `config.py` solo
exige la API key del proveedor realmente seleccionado (no ambas).

**Reintentos (añadidos 2026-07-28):** visto en producción, Gemini devolvió un 503 "high demand"
(sobrecarga transitoria). `_generate_gemini`/`_generate_anthropic` reintentan hasta 3 veces con
backoff exponencial (2–30 s) vía `tenacity`, **solo** para errores que un reintento puede arreglar
(5xx, rate limit, timeout, conexión). Los 4xx por auth o payload inválido no se reintentan, porque
reintentar no los soluciona. Si se agotan (o el error no es transitorio), `generate()` lanza
`LLMGenerationError`.

**Techo de tokens de salida (`LLM_MAX_TOKENS`, 16000, revisado 2026-09-03).** Los dos SDK tienen el
mismo concepto, pero con defaults opuestos:

| | Parámetro | ¿Obligatorio? | Si se omite |
|---|---|---|---|
| Anthropic | `max_tokens` | **Sí** | `messages.create()` falla |
| Gemini | `max_output_tokens` | No | Se aplica el máximo de salida del modelo |

De ahí venía un fallo latente: en Anthropic hubo que poner un número al escribir la ruta (julio) y
se puso 2048, que envejeció mal. La respuesta del Step 4 de la ejecución real del 2026-08-20 ocupa
**~2.800 tokens** con 30 variables (y hasta ~3.700 con `MAX_SELECTED_VARIABLES=40`), así que
`LLM_PROVIDER=anthropic` habría truncado el JSON y matado el análisis en el Step 5. No llegó a
pasar porque el proveedor por defecto es Gemini, que no llevaba tope.

Se aplica también a Gemini, que no lo exige. Eso **baja** su techo efectivo, y es deliberado: coste
acotado por llamada y mismo comportamiento en ambas rutas, que es para lo que existe `llm_client`.
Si truncara, se vería como un `JSONDecodeError` en el log, no en silencio.

**⚠️ Asimetría de razonamiento entre proveedores (verificado 2026-09-03).** Los dos proveedores
**no** están en igualdad de condiciones, y conviene saberlo antes de comparar su calidad:

| | Razonamiento |
|---|---|
| `gemini-2.5-flash` | **Activado por defecto.** Se configura con `thinking_level` (`low`/`medium`/`high`) y no puede apagarse del todo; lo más bajo es `low`. Los tokens de pensamiento se facturan como salida. El código no fija el parámetro, así que usa el default: **activado**. |
| `claude-opus-4-8` | **Desactivado.** `_generate_anthropic()` no pasa `thinking`, y en Opus 4.8 omitirlo significa ejecutar sin razonar. |

Consecuencia práctica: **el único diagnóstico real que existe (2026-08-20) se produjo con
razonamiento**, porque lo hizo Gemini. Cambiar a `anthropic` tal como está el código daría
diagnósticos sin razonar, y sería fácil atribuir el empeoramiento al modelo en vez de a la
configuración. Activar `thinking: {type: "adaptive"}` en la ruta de Anthropic *corregiría* esta
asimetría en vez de crearla — pero sin la evaluación del punto 8 no habría forma de medir si mejora.

**Otras notas por proveedor:**

- `gemini-2.5-pro` requiere facturación activada en el proyecto de Google Cloud; sin ella da error
  429 de cuota 0. Por eso el default es `gemini-2.5-flash`.

---

## Estructura de ficheros

```
rca-agent/
├── webhook.py           ← FastAPI: recibe POST de PI (Step 1)
│                          Endpoints: GET /health, POST /notification, GET /notifications/history
├── workflow.py             ← Orquestación del workflow (run_rca_analysis) + Steps 3, 4 y 6
├── graph_client.py      ← Cliente MCP para afkg-graph-mcp (Step 2)
├── pi_client.py         ← Cliente MCP para aveva-pi-mcp (Step 5)
├── incidents.py         ← Registro de incidentes: deduplicación y persistencia
├── llm_client.py        ← Abstracción sobre el proveedor de LLM (Gemini/Anthropic)
├── config.py            ← Carga .env y valida variables obligatorias
├── .env.example         ← Plantilla (copiar a .env y rellenar)
├── .env                 ← Credenciales reales (NO en git)
├── requirements.txt     ← fastapi, uvicorn, google-genai, anthropic, python-dotenv, pytz, mcp, tenacity
├── setup.bat            ← Crea .venv e instala deps (ejecutar 1 vez)
├── start.bat            ← Arranca el servidor (desarrollo)
├── install_service.bat  ← Windows Service con NSSM (producción)
├── webhook.log          ← Log rotativo (10 MB × 5). En .gitignore
├── incidents/           ← Un JSON por incidente. En .gitignore (datos de planta)
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
| `LLM_MAX_TOKENS` | No | Techo de tokens de la respuesta, para ambos proveedores | `16000` |
| `WEBHOOK_PORT` | No | Puerto del servidor | `8090` |
| `WEBHOOK_SECRET` | No | Token para validar origen de PI (cabecera `X-PI-Secret`) | vacío |
| `PI_LOCAL_TIMEZONE` | No | Zona horaria para logs | `Europe/Madrid` |
| `INCIDENTS_DIR` | No | Carpeta del registro de incidentes | `<proyecto>\incidents` |
| `INCIDENT_COOLDOWN_MINUTES` | No | Ventana en que una alerta del mismo activo+KPI se considera el mismo incidente. `0` deja solo la dedup exacta | `20` |
| `AFKG_GRAPH_MCP_DIR` | No | Carpeta del proyecto afkg-graph-mcp (Step 2) | `C:\MCPServer\afkg-graph-mcp` |
| `AVEVA_PI_MCP_DIR` | No | Carpeta del proyecto aveva-pi-mcp (Step 5) | `C:\MCPServer\MCP Server` |
| `PI_LOOKBACK_HOURS` | No | Ventana de la **primera** consulta del Step 5, en horas | `24` |
| `PI_QUERY_INTERVAL_VALUE` | No | Resolución de la primera consulta (valor) | `15` |
| `PI_QUERY_INTERVAL_UNIT` | No | Resolución de la primera consulta (unidad) | `minutes` |
| `PI_MAX_LOOKBACK_HOURS` | No | Techo absoluto de la ventana al ampliar (14 días) | `336` |
| `MAX_HISTORY_ADJUSTMENTS` | No | Ampliaciones de ventana permitidas por alerta. `0` desactiva | `1` |
| `PI_TARGET_POINTS_PER_VARIABLE` | No | Puntos por variable a los que se ajusta la resolución al ampliar | `120` |
| `DEMO_MODE` | No | Avisa al modelo de que las desviaciones son sintéticas y periódicas | `false` |
| `MAX_SELECTED_VARIABLES` | No | Tope de variables aceptadas de la selección del Step 4 | `40` |

**`PI_LOOKBACK_HOURS` es un punto de partida, no un límite** (cambiado el 2026-08-27; ver «Ventana
variable» en el Step 6). Hasta esa fecha el valor se justificaba en parte por mantener al modelo
por debajo del ciclo de las desviaciones sintéticas de la demo, para que no detectase esa
periodicidad. Eso era ocultar evidencia para dirigir el diagnóstico, y además era incompatible con
dejar que la ventana crezca. Lo sustituye `DEMO_MODE`, que se lo dice al modelo explícitamente.

---

## MCP Servers relacionados

El workflow habla con estos dos MCP servers, lanzándolos como subproceso vía `uv run --directory`
(igual que hace Claude Code según `.mcp.json`):

| Servidor | Ruta | Tools que usa | Step | Cliente |
|---|---|---|---|---|
| `afkg-graph-mcp` | `C:\MCPServer\afkg-graph-mcp\` | `graph_neighborhood` → estructura del AF del asset/subsistema y `piApiPath` de sus atributos | Step 2 | `graph_client.py` ✅ |
| `aveva-pi-mcp` | `C:\MCPServer\MCP Server\` | `create_timeseries_bucket`, `query_by_path` | Step 5 | `pi_client.py` ✅ |

El `piApiPath` es la moneda de cambio entre los dos: el Step 2 lo extrae del grafo, el modelo lo
copia literalmente en el Step 4, y el Step 5 lo pasa directamente a `query_by_path`.

---

## Convenciones de desarrollo

- **Proveedor de LLM:** configurable vía `LLM_PROVIDER` (`.env`) — ver la sección «Proveedor de
  LLM» arriba.
- **Idioma comentarios:** español
- **Commits:** en español, con `Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>`
- **Sin hardcodear credenciales** — todo via `.env`
- **Documentar el porqué, no el qué.** Casi todo lo que tiene una forma rara en este código está
  ahí porque algo falló contra el sistema real. Cuando se añada un guardarraíl, dejar en el
  docstring o el comentario **qué se vio fallar y cuándo** — es lo que hace mantenible el código.
- Hacer push a GitHub al final de cada sesión de trabajo

---

## Cómo actualizar este fichero

Al finalizar cada sesión de trabajo, actualizar:
1. La tabla de **Estado del proyecto**
2. La sección **Próximo paso**
3. El **Detalle de cada step** afectado, incluyendo el motivo de cualquier decisión nueva
4. Las **Variables de entorno** si se han añadido

Así el siguiente chat arranca con el contexto exacto sin necesidad de explicaciones.

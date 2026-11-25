# Gobernanza de IA — PI RCA Workflow

Documento exigido por `extensions/ai-governance-spec.md` (v1.2) de izSpecs.
Cubre las secciones §5 (transparencia), §6 (riesgo) y §9 (post-despliegue).

**Propietario:** Sandrosky99 &lt;sandracerveron@gmail.com&gt;
**Última revisión:** 2026-11-25, contrastada con el código

---

## 1. Clasificación de riesgo (§6.1)

### **BAJO**

| Factor | Valoración |
|---|---|
| **Autonomía** | Ninguna sobre la planta. El workflow **solo lee** de PI y del grafo del AF. No escribe consignas, no modifica el AF, no cierra alarmas, no actúa sobre ningún equipo. Lo único que escribe es el expediente del propio incidente (`incidents/`) y el `audit.jsonl`. |
| **Impacto de un error** | Un diagnóstico equivocado es una hipótesis equivocada. Un ingeniero la lee y decide; ninguna acción física se deriva automáticamente de ella. |
| **Datos personales** | Ninguno por diseño. Datos de proceso industrial (caudales, presiones, eficiencias, estados de equipo) y, desde la Fase 3, la evidencia y la sospecha que escribe el operario al descartar una causa. Los veredictos no llevan autor ni identidad. Como ese texto es libre, *podría* contener un nombre: lo cubre la norma del §6, punto 3, no un filtro. |
| **Alcance** | Entorno de demostración: el AF de una EDAR de pruebas (raíz `WWTP`, con estaciones de bombeo y líneas de tratamiento), dentro de un grafo compartido con otras demos. Las alertas reales recibidas hasta hoy son de dos bombas. No hay despliegue productivo. |
| **Reversibilidad** | Total. No hay nada que revertir porque no se modifica nada externo. |

### Consecuencias de la clasificación baja

- **§6.5 Sesgo y equidad — N/A.** La salida no afecta a personas: diagnostica el
  comportamiento de una bomba. No hay decisiones sobre individuos, ni acceso a servicios,
  ni datos demográficos.
- **§6.6 Pruebas adversarias / red-team — N/A.** Reservadas a sistemas de riesgo alto.
- **§6.3 Revisión por dos personas** (base §6.3) no es exigible; sí lo es la revisión por
  el propietario.

### Qué elevaría el riesgo a MEDIO o ALTO

Reclasificar **antes** de hacer cualquiera de estas cosas:

1. Que el workflow **escriba** en PI (anotar event frames, cambiar consignas, cerrar alarmas).
2. Que se despliegue sobre una planta real en producción, no sobre el entorno de demo.
3. Que el diagnóstico dispare acciones automáticas (órdenes de trabajo, avisos a operación
   sin intervención humana).
4. Que se le den las tools de los MCP servers al modelo, convirtiéndolo en un agente.
5. Que empiece a tratar datos que identifiquen a personas (turnos, operarios, firmas).

**Sobre el punto 4 (revisado el 2026-09-29).** Sigue sin cruzarse, y conviene dejar escrito
por qué se estuvo cerca. El paso de búsqueda del reanálisis le da al modelo **una** función,
`graph_client.buscar_en_la_planta()`, que es **nuestra y de sólo lectura**: no es una tool de
`afkg-graph-mcp`. La diferencia es la que separa este punto de su contrario — entre las tools
de ese servidor está `refresh_graph`, de **escritura** sobre un Neo4j que comparten las demás
demos, y no le es alcanzable. La planta, las ramas válidas, el número de resultados y el
presupuesto de vueltas los fija el código; el modelo elige un sitio, no una ruta.

Dicho de otro modo: el modelo decide **dentro** de lo que le dejamos, no decide qué le
dejamos. Darle las tools del servidor, o cualquier herramienta de escritura, **sí** dispara
la reclasificación. Ver `DISENO-INTERACCION-HUMANA.md` §5 ter y `AGENTS.md` §La excepción.

---

## 2. Propósito y límites (§5.1)

**Qué hace.** A partir de una alerta de PI, selecciona variables de proceso del Asset
Framework, consulta su histórico y propone entre 2 y 3 causas raíz plausibles con una
acción correctiva para cada una. Si una persona descarta alguna y lo pide, vuelve a analizar
teniendo en cuenta lo descartado; en esa segunda pasada puede proponer menos causas, o
ninguna si los datos no sostienen otra (`root_causes` vacío).

**Qué decide, y con cuánta autonomía.** Cinco cosas, todas acotadas por código. Las tres
últimas solo ocurren en un reanálisis, que siempre lo pide una persona:

| Decisión del modelo | Límite impuesto por el código |
|---|---|
| Qué variables consultar (Step 4) | `_validate_selected_variables()`: solo se aceptan `piApiPath` presentes en el AF entregado; el resto se descarta. Tope `MAX_SELECTED_VARIABLES`. |
| Si necesita más histórico (Step 6) | `_parse_history_request()`: solo puede elegir de un catálogo cerrado, hasta `MAX_HISTORY_ADJUSTMENTS` veces. |
| Qué sospechas del operario señalan algo que no está en el contexto (puerta de la búsqueda) | `sospechas_que_senalan_fuera()`: contesta sí o no a claves que pone el código. Las claves que no existen o se repiten se caen, y la que no mencione cuenta como «no». |
| Qué buscar en la planta y cuándo parar (bucle de búsqueda, el **único paso agéntico**) | Una sola función, nuestra y de solo lectura: `graph_client.buscar_en_la_planta()`. La planta (`AF_PLANT_ROOT`), las ramas válidas (`AF_RAMAS_NO_PROCESO`), los resultados y el presupuesto los fija el código: `MAX_ITERACIONES_BUSQUEDA` (5), `BUSQUEDA_TIMEOUT_SECONDS` (180), `MAX_BUSQUEDAS_POR_PASADA`, `MAX_ATRIBUTOS_ANADIDOS`. Agotarlo no es un fallo: se sigue sin lo que no encontró. Ver §1, punto 4. |
| Qué causas devuelve en el reanálisis | `autorizar_causas()`: se descartan las que repiten una ya descartada y se recorta a lo que cabe (`MAX_CAUSAS_ABIERTAS` = 3, contando las que siguen abiertas). |

No decide nada más. La secuencia de pasos es fija y está escrita en `run_rca_analysis()`; el
bucle de búsqueda es un paso acotado dentro de ella, no un cambio de esa secuencia. El texto
del operario entra en el prompt **marcado como observaciones de campo, nunca como
instrucciones**.

**Límites conocidos.** Documentados para que nadie los descubra por sorpresa:

- **El diagnóstico es una hipótesis, no una causa confirmada.** El modelo no ha abierto la
  bomba. Solo la inspección o la intervención confirman una causa.
- **Puede confundir correlación con causalidad.** No hay preprocesado determinista de las
  series: el modelo recibe los valores en bruto.
- **Solo ve lo que está en el AF.** Si falta instrumentación (p. ej. vibración), lo declara
  en `missing_variables`, pero el diagnóstico se resiente.
- **Validado con pocos casos reales.** Cinco análisis terminados entre el 2026-09-07 y el
  2026-10-28 (uno reanalizado), sobre dos bombas y **todos del mismo KPI**
  (`Hydraulic Efficiency`). Hay una causa confirmada desde la pantalla (2026-10-08), pero no
  consta que la confirmara mantenimiento tras inspeccionar el equipo. No hay evidencia
  estadística de su acierto.
- **Depende de la configuración de PI.** El atributo `step` de cada PI Point determina si
  los valores se interpolan o se mantienen; mal configurado, el modelo razona sobre datos
  engañosos.

## 3. Mecanismo de intervención humana (§5.2)

- El diagnóstico **nunca dispara una acción**: se guarda en `incidents/<id>.json` y espera.
- Desde la **pantalla de la sala de control** (`GET /pantalla`, ver
  `DISENO-INTERACCION-HUMANA.md`), una persona:
  - **confirma o descarta** cada causa, y al descartar deja evidencia y, si la tiene, una
    sospecha (`revision.veredictos`, de solo añadir);
  - **marca la alerta como no válida** si no debió dispararse (`revision.reclasificacion`);
  - **pide volver a analizar** con ese feedback, hasta `MAX_REANALISIS` (2) veces y solo
    mientras el incidente no esté cerrado. El modelo nunca se relanza por su cuenta.
- El cierre (causa confirmada, causa no determinada, alerta no válida, etc.) lo deduce el
  código de esos veredictos; no lo decide el modelo.
- Todo queda en el `audit.jsonl`: `incident.veredicto`, `incident.reclasificacion`,
  `incident.reanalisis` e `incident.reanalisis_fallido`.
- **Interruptor de parada:** `WORKFLOW_ENABLED=false` detiene todo análisis nuevo sin
  desplegar ni revertir. Ver §5 de este documento.

## 4. Etiquetado de contenido generado por IA (§5.4)

Todo diagnóstico persistido lleva un bloque `_ai_generated` con el proveedor, el modelo y
un aviso explícito de que es una hipótesis pendiente de verificación humana. Va **por
pasada**: cada diagnóstico de `diagnosticos` lleva el suyo, porque el proveedor puede cambiar
entre una y otra. Quien abra el fichero del incidente no puede confundirlo con un análisis
hecho por una persona, y la pantalla lo muestra junto a las causas («Generado por IA»).

El modelo de la etiqueta sale de `llm_client.modelo_en_uso()`, la misma tabla que decide a
quién se llama. Hasta el 2026-11-25 se calculaba aparte, y con DeepSeek registraba un modelo
de Claude. En los expedientes anteriores a esa fecha, el dato fiable es el proveedor
(`AI-TRACEABILITY.md` §3).

---

## 5. Interruptor de parada en caliente (§9.5)

`WORKFLOW_ENABLED=false` en el `.env` (o en el entorno del servicio) y reinicio del proceso.

Con el interruptor bajado:
- El webhook **sigue aceptando** las notificaciones de PI y respondiendo `202`. PI no ve
  errores ni reintenta.
- Cada notificación **se registra igualmente** como incidente, en estado `pausado`, y queda
  auditada con `status: BLOCKED`.
- **No se llama al LLM ni a los MCP servers.** El gasto se detiene.

No se pierde ninguna alerta: quedan registradas con su payload íntegro.

**Para relanzarlas:** `WORKFLOW_ENABLED=true`, reiniciar y reenviar la notificación desde PI.
Desde el 2026-09-11 `claim()` readmite los incidentes en `pausado` igual que los
`interrumpido` (`_RECLAMABLES`), así que se recogen sin tocar ningún fichero. Lo que decía
esta sección antes del arreglo está en el Anexo A.

**Prueba periódica del interruptor:** poner `WORKFLOW_ENABLED=false`, reiniciar, enviar una
notificación de prueba y comprobar que la respuesta es `{"status": "paused"}` y que el
incidente queda en `pausado`. Después, volver a `true`, reiniciar, reenviarla y comprobar que
se analiza. Debe repetirse tras cada cambio en `webhook.py`.

Se probó en el servicio real el 2026-09-08 hasta el `pausado` (`AI-TRACEABILITY.md` §8). El
último tramo, relanzar y comprobar que se procesa, **no se ha hecho todavía**.

## 6. Usos prohibidos y restringidos (§6.3)

**Prohibido:**

1. **Actuar sobre la planta a partir del diagnóstico sin verificación humana.** Ninguna
   consigna, arranque, parada o cambio de modo puede derivarse automáticamente.
2. **Presentar el diagnóstico como causa confirmada** en un informe, orden de trabajo o
   comunicación a cliente sin la etiqueta de generado por IA.
3. **Enviar al modelo datos que identifiquen a personas** — nombres de operarios, turnos,
   firmas de intervención. Hoy el payload no los lleva; no añadirlos. Lo mismo vale para la
   evidencia y la sospecha que se escriben en la pantalla: entran en el prompt tal cual y no
   hay filtro que quite un nombre.
4. **Usarlo como única base para una decisión de seguridad** (SIL, enclavamientos,
   protección de equipos).

**Restringido, requiere reclasificar el riesgo antes:** los cinco supuestos del §1.

## 7. Inventario de sistemas de IA (§6.4)

| Campo | Valor |
|---|---|
| **Sistema** | PI RCA Workflow |
| **Propósito** | Diagnóstico de causa raíz de desviaciones de KPI en EDAR y bombeos |
| **Propietario** | Sandrosky99 &lt;sandracerveron@gmail.com&gt; |
| **Riesgo** | Bajo |
| **Proveedor / modelo en ejecución** | DeepSeek — `deepseek-flash` (`LLM_PROVIDER=deepseek` en el `.env` del servicio, comprobado el 2026-11-25) |
| **Por defecto en el código** | Google — `gemini-2.5-flash` (si `LLM_PROVIDER` no se define) |
| **Alternativas configurables** | Google — `gemini-2.5-flash` (`LLM_PROVIDER=gemini`); Anthropic — `claude-opus-4-8` (`LLM_PROVIDER=anthropic`). La ruta de DeepSeek existe desde el 2026-09-30 |
| **Razonamiento** | Los tres razonan. Gemini 2.5 Flash, por defecto. Claude, con `thinking` *adaptive* desde el 2026-09-28 (`ANTHROPIC_THINKING`, encendido por defecto). DeepSeek, siempre. La asimetría entre proveedores quedó **cerrada** el 2026-09-28. |
| **Datos enviados al modelo** | Estructura del AF (nombres de activos y atributos), series temporales de proceso y, en un reanálisis, la evidencia y la sospecha que escribe el operario. Sin datos personales (ver §1). |
| **Estado** | Demostración. Sin despliegue productivo. |
| **Alta en el inventario** | 2026-09-07 · actualizado el 2026-11-25 |

⚠️ **Actualizar esta tabla** al cambiar de proveedor o de modelo, y al pasar a producción.

## 8. Monitorización e incidentes (§9.1, §9.2, §9.3)

**§9.1 Comportamiento anómalo.** Señales que ya se registran y hay que vigilar:

| Señal en el log / audit | Qué indicaría |
|---|---|
| `WARN` de rutas descartadas en el Step 4 | El modelo empieza a inventar `piApiPath`: el prompt del Step 3 se está degradando |
| Peticiones de ampliación de ventana muy frecuentes | `PI_LOOKBACK_HOURS` mal dimensionado para ese tipo de alerta |
| `JSONDecodeError` en Step 4 o 6 | Respuesta truncada (revisar `LLM_MAX_TOKENS`) o el modelo ha dejado de respetar el formato |
| Muchos incidentes en `fallido` o `interrumpido` (en pantalla, «fallo del análisis» e «interrumpido») | Problema de integración o proceso inestable |
| Descartes por enfriamiento continuos | PI mal configurado (`NonrepetitionInterval`) |
| `WARN` «Reanalisis: se descarta una causa que repite otra» o «devolvio mas causas de las que caben» | El modelo no está usando el feedback del operario: repite lo descartado |
| `WARN` «Busqueda: se agoto el reloj» o «respuesta inservible» frecuentes | El bucle de búsqueda no converge, o el grafo responde lento |
| `incident.reanalisis_fallido` en el audit | Los reanálisis fallan; la pasada anterior se conserva, pero la persona no obtiene respuesta |

⚠️ **Al contar señales en `audit.jsonl`, descontar las de prueba.** Hasta el 2026-11-25 el
servidor de pruebas escribía en el audit del servicio real: hay 106 entradas de incidentes de
`dev-fixtures/` entre el 2026-09-18 y el 2026-11-18, entre ellas 5 `incident.reanalisis_fallido`,
**todas de pruebas**. Se distinguen por el `incidentId`. Desde esa fecha el de pruebas tiene su
propio `AUDIT_FILE`.

**§9.2 Respuesta a incidentes de IA.** Ante un diagnóstico manifiestamente erróneo o una
conducta anómala:

1. `WORKFLOW_ENABLED=false` y reiniciar — corta el gasto y el flujo en minutos.
2. Conservar `incidents/<id>.json`: lleva los prompts y las respuestas de cada pasada
   (`traces`), que es lo que permite reconstruir qué pasó. **Se podan a los
   `INCIDENT_TRACE_RETENTION_DAYS` (90) días**: si el caso puede necesitarlos más tarde,
   copiar el fichero aparte.
3. Revisar `audit.jsonl` para la secuencia de operaciones.
4. Si el fallo viene de código generado con IA, anotarlo en `docs/AI-TRACEABILITY.md` con
   el modelo y la fecha del artefacto implicado.
5. Corregir, añadir la prueba que lo habría cazado, y volver a habilitar.

**§9.3 Rollback inmediato.** `git revert` del commit implicado y reinicio del servicio. El
despliegue es un proceso Python sin migraciones de base de datos ni estado compartido, así
que revertir código suele ser suficiente. **Con una excepción en los ficheros de
incidente.** El código actual lee los expedientes antiguos, pero al revés no siempre
funciona. Desde el 2026-09-22 el diagnóstico se guarda como lista por pasada (`diagnosticos`)
y el campo suelto `diagnostico` se borra al reescribir; desde el 2026-09-28 pasa lo mismo con
`trace` → `traces`. Un código anterior a esas fechas no vería las causas de los expedientes
escritos después. Revertir por debajo de ahí exige restaurar también `incidents/` de un
respaldo de esa época.

**§9.4 Revisión periódica.** A los 30 y 90 días del primer despliegue productivo. Hoy N/A:
no hay despliegue productivo.

---

## 8-bis. Decisiones de seguridad del webhook

Los cuatro controles de «proteger el webhook» se evaluaron el 2026-09-07 y se cerraron:
autenticación por cabecera y TLS están **bloqueados por el emisor** (PI no puede enviar
cabeceras propias; con HTTPS el handshake falla en silencio), el firewall es una **decisión de
infraestructura** sobre una VM de pruebas, y `/notifications/history` **sí se implementó**
(apagado por defecto). El razonamiento completo está en el bloque «DECISIONES DE SEGURIDAD»
al final de `webhook.py`.

⚠️ Todo eso se apoya en la premisa de plataforma de pruebas en red interna. Es exactamente el
supuesto 2 del §1: al desplegar sobre una planta real, los cuatro puntos vuelven a estar
abiertos y el firewall deja de ser opcional.

## 9. Excepciones (§11.3)

Ninguna abierta a 2026-11-25.

Las secciones marcadas `N/A` en `docs/IZSPECS-CONFORMANCE.md` no son excepciones: son
secciones que el propio estándar permite marcar como no aplicables, con su justificación.

---

## Anexo A. Texto sustituido

Se conserva lo que decían las secciones cuando su contenido dejó de ser cierto, para que se
entienda por qué aparece en otros documentos.

### §5, sobre relanzar los `pausado` (escrito el 2026-09-08, corregido el 2026-09-11)

> ⚠️ **Pero hoy no se pueden relanzar** (verificado el 2026-09-08). `claim()` solo readmite los
> incidentes en `interrumpido`, no los que quedaron en `pausado`, así que reenviar la notificación
> la rechaza por clave exacta. La única salida actual es borrar el fichero del incidente a mano.
> Arreglo pendiente, ver `AGENTS.md` §Próximo paso.

### §3, sobre la revisión humana (antes de la pantalla de la sala de control)

> El ingeniero puede aceptarlo, rechazarlo o pedir más datos. Ese registro es el sitio
> natural para anotar la revisión (campos `revision` y `causa_confirmada`, pendientes de
> implementar junto con el canal de aviso).

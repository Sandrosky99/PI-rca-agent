# Gobernanza de IA — PI RCA Workflow

Documento exigido por `extensions/ai-governance-spec.md` (v1.2) de izSpecs.
Cubre las secciones §5 (transparencia), §6 (riesgo) y §9 (post-despliegue).

**Propietario:** Sandrosky99 &lt;sandracerveron@gmail.com&gt;
**Última revisión:** 2026-09-07

---

## 1. Clasificación de riesgo (§6.1)

### **BAJO**

| Factor | Valoración |
|---|---|
| **Autonomía** | Ninguna sobre la planta. El workflow **solo lee** de PI y del grafo del AF. No escribe consignas, no modifica el AF, no cierra alarmas, no actúa sobre ningún equipo. |
| **Impacto de un error** | Un diagnóstico equivocado es una hipótesis equivocada. Un ingeniero la lee y decide; ninguna acción física se deriva automáticamente de ella. |
| **Datos personales** | Ninguno. Solo datos de proceso industrial: caudales, presiones, eficiencias, estados de equipo. |
| **Alcance** | Entorno de demostración con una estación de bombeo. No hay despliegue productivo. |
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

---

## 2. Propósito y límites (§5.1)

**Qué hace.** A partir de una alerta de PI, selecciona variables de proceso del Asset
Framework, consulta su histórico y propone entre 2 y 3 causas raíz plausibles con una
acción correctiva para cada una.

**Qué decide, y con cuánta autonomía.** Solo dos cosas, ambas acotadas por código:

| Decisión del modelo | Límite impuesto por el código |
|---|---|
| Qué variables consultar (Step 4) | `_validate_selected_variables()`: solo se aceptan `piApiPath` presentes en el AF entregado; el resto se descarta. Tope `MAX_SELECTED_VARIABLES`. |
| Si necesita más histórico (Step 6) | `_parse_history_request()`: solo puede elegir de un catálogo cerrado, hasta `MAX_HISTORY_ADJUSTMENTS` veces. |

No decide nada más. La secuencia de pasos es fija y está escrita en `run_rca_analysis()`.

**Límites conocidos.** Documentados para que nadie los descubra por sorpresa:

- **El diagnóstico es una hipótesis, no una causa confirmada.** El modelo no ha abierto la
  bomba. Solo la inspección o la intervención confirman una causa.
- **Puede confundir correlación con causalidad.** No hay preprocesado determinista de las
  series: el modelo recibe los valores en bruto.
- **Solo ve lo que está en el AF.** Si falta instrumentación (p. ej. vibración), lo declara
  en `missing_variables`, pero el diagnóstico se resiente.
- **Validado con un único caso real** (2026-08-20, `Hydraulic Efficiency`). No hay
  evidencia estadística de su acierto.
- **Depende de la configuración de PI.** El atributo `step` de cada PI Point determina si
  los valores se interpolan o se mantienen; mal configurado, el modelo razona sobre datos
  engañosos.

## 3. Mecanismo de intervención humana (§5.2)

- El diagnóstico **nunca dispara una acción**: se guarda en `incidents/<id>.json` y espera.
- El ingeniero puede aceptarlo, rechazarlo o pedir más datos. Ese registro es el sitio
  natural para anotar la revisión (campos `revision` y `causa_confirmada`, pendientes de
  implementar junto con el canal de aviso).
- **Interruptor de parada:** `WORKFLOW_ENABLED=false` detiene todo análisis nuevo sin
  desplegar ni revertir. Ver §5 de este documento.

## 4. Etiquetado de contenido generado por IA (§5.4)

Todo diagnóstico persistido lleva un bloque `_ai_generated` con el proveedor, el modelo y
un aviso explícito de que es una hipótesis pendiente de verificación humana. Quien abra el
fichero del incidente no puede confundirlo con un análisis hecho por una persona.

---

## 5. Interruptor de parada en caliente (§9.5)

`WORKFLOW_ENABLED=false` en el `.env` (o en el entorno del servicio) y reinicio del proceso.

Con el interruptor bajado:
- El webhook **sigue aceptando** las notificaciones de PI y respondiendo `202`. PI no ve
  errores ni reintenta.
- Cada notificación **se registra igualmente** como incidente, en estado `pausado`, y queda
  auditada con `status: BLOCKED`.
- **No se llama al LLM ni a los MCP servers.** El gasto se detiene.

No se pierde ninguna alerta: quedan registradas para relanzarlas a mano cuando proceda.

**Prueba periódica del interruptor:** poner `WORKFLOW_ENABLED=false`, reiniciar, enviar una
notificación de prueba y comprobar que la respuesta es `{"status": "paused"}` y que el
incidente queda en `pausado`. Debe repetirse tras cada cambio en `webhook.py`.

## 6. Usos prohibidos y restringidos (§6.3)

**Prohibido:**

1. **Actuar sobre la planta a partir del diagnóstico sin verificación humana.** Ninguna
   consigna, arranque, parada o cambio de modo puede derivarse automáticamente.
2. **Presentar el diagnóstico como causa confirmada** en un informe, orden de trabajo o
   comunicación a cliente sin la etiqueta de generado por IA.
3. **Enviar al modelo datos que identifiquen a personas** — nombres de operarios, turnos,
   firmas de intervención. Hoy el payload no los lleva; no añadirlos.
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
| **Proveedor / modelo en ejecución** | Google — `gemini-2.5-flash` (por defecto) |
| **Alternativa configurable** | Anthropic — `claude-opus-4-8` (`LLM_PROVIDER=anthropic`) |
| **Razonamiento** | Gemini 2.5 Flash razona por defecto (`thinking_level`, no desactivable del todo). La ruta de Anthropic **no** usa `thinking`. Asimetría conocida y documentada. |
| **Datos enviados al modelo** | Estructura del AF (nombres de activos y atributos) y series temporales de proceso. Sin datos personales. |
| **Estado** | Demostración. Sin despliegue productivo. |
| **Alta en el inventario** | 2026-09-07 |

⚠️ **Actualizar esta tabla** al cambiar de proveedor o de modelo, y al pasar a producción.

## 8. Monitorización e incidentes (§9.1, §9.2, §9.3)

**§9.1 Comportamiento anómalo.** Señales que ya se registran y hay que vigilar:

| Señal en el log / audit | Qué indicaría |
|---|---|
| `WARN` de rutas descartadas en el Step 4 | El modelo empieza a inventar `piApiPath`: el prompt del Step 3 se está degradando |
| Peticiones de ampliación de ventana muy frecuentes | `PI_LOOKBACK_HOURS` mal dimensionado para ese tipo de alerta |
| `JSONDecodeError` en Step 4 o 6 | Respuesta truncada (revisar `LLM_MAX_TOKENS`) o el modelo ha dejado de respetar el formato |
| Muchos incidentes en `fallido` o `interrumpido` | Problema de integración o proceso inestable |
| Descartes por enfriamiento continuos | PI mal configurado (`NonrepetitionInterval`) |

**§9.2 Respuesta a incidentes de IA.** Ante un diagnóstico manifiestamente erróneo o una
conducta anómala:

1. `WORKFLOW_ENABLED=false` y reiniciar — corta el gasto y el flujo en minutos.
2. Conservar `incidents/<id>.json`: lleva el prompt completo y la respuesta, que es lo que
   permite reconstruir qué pasó.
3. Revisar `audit.jsonl` para la secuencia de operaciones.
4. Si el fallo viene de código generado con IA, anotarlo en `docs/AI-TRACEABILITY.md` con
   el modelo y la fecha del artefacto implicado.
5. Corregir, añadir la prueba que lo habría cazado, y volver a habilitar.

**§9.3 Rollback inmediato.** `git revert` del commit implicado y reinicio del servicio. El
despliegue es un proceso Python sin migraciones de base de datos ni estado compartido, así
que revertir código es suficiente. Los ficheros de incidente son compatibles hacia atrás:
campos nuevos que un código anterior no conozca simplemente se ignoran.

**§9.4 Revisión periódica.** A los 30 y 90 días del primer despliegue productivo. Hoy N/A:
no hay despliegue productivo.

---

## 9. Excepciones (§11.3)

Ninguna abierta a 2026-09-07.

Las secciones marcadas `N/A` en `docs/IZSPECS-CONFORMANCE.md` no son excepciones: son
secciones que el propio estándar permite marcar como no aplicables, con su justificación.

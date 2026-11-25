# Matriz de conformidad con izSpecs

- **izSpecs bundle:** `2026.12-09011521`
- **Validado el:** 2026-09-07 · **revisado contra el código** el 2026-11-25
- **Propietario:** Sandrosky99 &lt;sandracerveron@gmail.com&gt;
- **Clasificación de riesgo:** Baja (ver [`AI-GOVERNANCE.md`](./AI-GOVERNANCE.md) §1)

| Especificación | Versión | Aplica |
|---|---|---|
| `base/software-spec.md` | 1.10 | Sí |
| `extensions/ai-governance-spec.md` | 1.2 | Sí — desarrollado con asistencia de IA |
| `extensions/observability-spec.md` | 1.1 | Sí — ver nota abajo |
| `extensions/mcp-server-spec.md` | 1.2 | **N/A** — este recurso es un *cliente* MCP, no un servidor: no expone tools ni acepta conexiones MCP entrantes |

> **Sobre observabilidad.** Es discutible si aplica: el workflow **solo lee** de PI y del
> grafo. El único estado externo que muta es la creación de un bucket temporal en
> `aveva-pi-mcp`, y el resto de escrituras son locales. Se ha optado por aplicarla **entera**
> en vez de argumentar la exención — decisión del propietario, 2026-09-07.

---

## base/software-spec.md v1.10

| § | Requisito | Estado | Cómo se cumple |
|---|---|---|---|
| 1.1 | `DISCLAIMER.md` y `LICENSE` [MUST] | ✅ | Ambos en la raíz, copiados de las plantillas de izSpecs. Licencia Apache-2.0 |
| 1.2 | `README.md` [MUST] | ✅ | En la raíz |
| 1.3 | Propietario declarado [MUST] | ✅ | `README.md` §Propietario y `AGENTS.md` |
| 1.4 | Divulgación de autoría IA [MUST] | ⚠️ Parcial | Convención con modelo **y fecha** documentada en `AGENTS.md` y aplicada desde 2026-09-07, **salvo en los 19 commits del 2026-09-28 al 2026-10-08**, que llevan solo `Co-Authored-By` (visto el 2026-11-25). Los commits anteriores al 2026-09-07 también llevan solo `Co-Authored-By`. Ninguno se reescribe (§1.7). La brecha y el modelo real usado en cada periodo están en `docs/AI-TRACEABILITY.md` §1 |
| 1.5 | Procedencia del prompt [SHOULD] | ✅ | Los prompts de producción se persisten en `incidents/<id>.json` → `traces`, una entrada **por pasada** del análisis: un reanálisis añade la suya y no pisa la anterior (los expedientes antiguos, con `trace` suelto, no se migran en bloque; si se vuelven a escribir, el campo suelto pasa a la lista). Se podan a los 90 días conservando el resumen (ai-governance §4.5). Los de desarrollo, en `AI-TRACEABILITY.md` §4 |
| 1.6 | Trazabilidad de datos de entrada [MUST] | ✅ | `AI-TRACEABILITY.md` §5 |
| 1.7 | Inmutabilidad de la trazabilidad [MUST] | ✅ | Registros append-only bajo control de git. Documentado en la cabecera de `AI-TRACEABILITY.md` |
| 2 | Portabilidad [SHOULD] | ✅ | Las suites resuelven el proyecto con `Path(__file__).resolve().parent.parent`; lo específico de la máquina va por `.env` con valores por defecto. Corren sin PI, sin MCP servers y sin claves. **Corregido el 2026-09-08:** cuatro suites conservaban un `sys.path.insert` con la ruta de Windows y morían en el CI de Linux. `run_all.py` incluye ahora una comprobación de portabilidad que lo impide |
| 3 | Configuración por entorno [SHOULD] | ✅ | `config.py` lee todo de `.env`. Ninguna clave de configuración cableada |
| 4 | Declaración de conformidad [MUST] | ✅ | `AGENTS.md` §iz Spec Conformance |
| 5 | Línea base de seguridad [SHOULD] | ✅ | Secretos solo en `.env`, en `.gitignore`, con el riesgo declarado en el `README.md`. Dependencias fijadas a versión exacta y escaneadas con `pip-audit` en CI. Los errores no vuelcan trazas ni rutas internas. `/notifications/history` apagado por defecto. **Autenticación, TLS y firewall evaluados y no aplicados**, con la justificación en el bloque «DECISIONES DE SEGURIDAD» de `webhook.py`: los dos primeros dependen de PI y están bloqueados; el tercero es una decisión de infraestructura sobre una plataforma de pruebas. Todo ello queda sin validez si el recurso sale del entorno de demo |
| 6.1 | Sin auto-merge sin revisión [MUST] | ✅ | Desde el 2026-09-08, `master` está protegido: no admite push directo y exige PR con los tres checks en verde. El propietario revisa y aprueba. Primera evidencia: PR #1, revisado y fusionado con *merge commit* (no *squash*, que reescribiría el mensaje y perdería los trailers de autoría del §1.4) |
| 6.2 | Propietario humano identificado [MUST] | ✅ | Sandrosky99 |
| 6.3 | Revisión proporcional al riesgo [MUST] | ✅ | Riesgo bajo: revisión por el propietario. La revisión por dos personas aplicaría al reclasificar (`AI-GOVERNANCE.md` §1) |
| 6.4 | Sin delegar el juicio crítico [MUST] | ✅ | Las decisiones de arquitectura están documentadas con su razonamiento y validadas por el propietario. Ver el historial de `AGENTS.md` |

## extensions/ai-governance-spec.md v1.2

| § | Requisito | Estado | Cómo se cumple |
|---|---|---|---|
| 1.1 | Sin exposición de secretos [MUST] | ✅ | `.env` fuera de git; `webhook.log`, `audit.jsonl` e `incidents/` también. El gate de CI busca patrones de credenciales y verifica que `.env` no esté versionado |
| 1.2 | Escaneo de vulnerabilidades [MUST] | ✅ | `bandit` (SAST) y `pip-audit` (SCA) en el gate de CI. DAST N/A: no hay superficie web pública. La pantalla de la sala de control es web, pero solo se sirve en la red interna (`DISENO-INTERACCION-HUMANA.md` §4) |
| 1.3 | Revisión de dependencias [MUST] | ✅ | `AI-TRACEABILITY.md` §6 — mantenimiento, adopción y licencia de las 8 dependencias |
| 1.4 | Datos sensibles en el contexto del modelo [MUST] | ✅ | Magnitudes de proceso y nombres de activo, y desde la Fase 3 también la **evidencia y la sospecha que escribe el operario** al descartar una causa. Sin datos personales: los veredictos no llevan autor ni identidad (`DISENO-INTERACCION-HUMANA.md` §4). Como es texto libre, alguien *podría* escribir un nombre; no hay filtro que lo impida, y se cubre con la norma de uso. El límite está escrito en `AGENTS.md` §Guardrails y en `AI-GOVERNANCE.md` §6 |
| 1.5 | Mínimo privilegio y agencia acotada [MUST] | ✅ | El modelo recibe **una sola herramienta, y sólo en el paso de búsqueda de un reanálisis**: no el servidor MCP, sino `graph_client.buscar_en_la_planta()`, una función envuelta de **sólo lectura**, acotada a esta planta y a las ramas de proceso, con tope de resultados y que no devuelve rutas — el modelo elige un sitio y la ruta la resuelve el código. El bucle está acotado por `MAX_ITERACIONES_BUSQUEDA` y `BUSQUEDA_TIMEOUT_SECONDS`, y agotarlos no interrumpe el análisis. El resto de sus decisiones siguen acotadas por código y auditadas (`_validate_selected_variables`, `_parse_history_request`, `autorizar_causas`, `_clave_limpia`). No ejecuta comandos ni escribe ficheros, y ninguna herramienta de escritura le es alcanzable — `refresh_graph` incluida. Descrito en `AGENTS.md` §La excepción y en `DISENO-INTERACCION-HUMANA.md` §5 ter |
| 1.6 | Revisión de patrones inseguros [SHOULD] | ✅ | `bandit` en CI. Sin `eval`, sin `shell=True`, sin deserialización insegura |
| 1.7 | Inyección de prompt y entrada adversaria [MUST] | ✅ | La entrada no confiable (payload de PI, respuesta del grafo, salida de los MCP) no puede alterar las instrucciones del sistema: el `SYSTEM_PROMPT` es una constante y va por el parámetro `system`, separado del mensaje. Los campos del payload se validan por tipo (`_valid_field`) y la notificación se rechaza en la puerta si no es analizable (`validate_notification`). Lo que escribe el operario entra en el prompt **marcado como observaciones de campo, nunca como instrucciones**, y sus efectos posibles están acotados por código: las causas del reanálisis pasan por `autorizar_causas` y las sospechas se identifican por la clave que emitió el código (`_clave_limpia`). La pantalla escapa todo texto del modelo y de las personas antes de pintarlo |
| 1.8 | Manejo inseguro de la salida [MUST] | ✅ | **El núcleo del diseño.** La salida del modelo nunca se ejecuta ni se pasa a un intérprete. Los `piApiPath` se validan contra la lista blanca del AF antes de consultar; la petición de ventana, contra un catálogo cerrado; las causas de un reanálisis, contra las que ya hay en el expediente (`autorizar_causas`); y la búsqueda no devuelve rutas: el modelo elige un sitio y la ruta la resuelve el código. El JSON se parsea con `json.loads`, nunca con `eval` |
| 2.1 | Cobertura de pruebas [MUST] | ✅ | 19 suites en `tests/`, todas en verde (`python tests/run_all.py`, 2026-11-25). Umbral del proyecto: toda función con lógica de decisión o validación debe tener pruebas de su camino feliz, sus modos de fallo y sus entradas malformadas |
| 2.2 | Revisión de las pruebas [MUST] | ✅ | Verifican comportamiento real, no cobertura: cada una detecta un fallo concreto. Prueba de ello: `test_incidents` detectó una colisión real de nombres por resolución de reloj el 2026-09-07, y `test_observability` una captura de excepción demasiado estrecha |
| 2.3 | Validación funcional documentada [MUST] | ⚠️ Parcial | `AI-TRACEABILITY.md` §8. Ciclos completos contra PI y LLM reales desde el 2026-08-20, incluido el **reanálisis de punta a punta** (2026-09-23). En `incidents/` hay cinco análisis reales terminados entre el 2026-09-07 y el 2026-10-28 (uno de ellos reanalizado), sobre **dos bombas distintas** (`PS20102 A03 PS02 Pump 02` y `PS20101 A03 PS01 Pump 01`) pero **todos del mismo KPI** (`Hydraulic Efficiency`). Hay **una causa confirmada** desde la pantalla (2026-10-08); no consta que la confirmara mantenimiento tras inspeccionar el equipo |
| 2.4 | Gate de calidad en CI/CD [MUST] | ✅ | `.github/workflows/quality-gate.yml`: pruebas + `bandit` + `pip-audit` + búsqueda de secretos. **Bloquea de verdad** desde el 2026-09-08: los tres jobs son *required checks* en la protección de rama de `master`, que además exige PR. Demostró su utilidad en sus dos primeras ejecuciones encontrando fallos que no se veían desde Windows: una supresión de bandit con la sintaxis equivocada y cuatro suites con la ruta del proyecto cableada |
| 3.1 | Verificación de licencias del código generado [MUST] | ✅ | `AI-TRACEABILITY.md` §7 |
| 3.2 | Declaración de originalidad [SHOULD] | ✅ | Valorado; riesgo bajo, sin herramienta de similitud. Justificado en `AI-TRACEABILITY.md` §7 |
| 3.3 | Registro de la herramienta de IA [MUST] | ✅ | `AI-TRACEABILITY.md` §1 |
| 4.1 | Minimización de datos en prompts [MUST] | ✅ | Sin datos personales en ningún prompt. Prohibido añadirlos (`AI-GOVERNANCE.md` §6) |
| 4.2 | Anonimización previa [MUST] | **N/A** | Los datos de prueba son magnitudes de proceso industrial; no hay datos personales que anonimizar |
| 4.3 | Cumplimiento regulatorio [MUST] | ✅ | Sin datos personales: RGPD no aplica al tratamiento. Riesgo bajo bajo el AI Act (no es sistema de alto riesgo del Anexo III). Reevaluar al reclasificar |
| 4.4 | Registro de actividades de tratamiento [MUST] | **N/A** | No se tratan datos personales en producción |
| 4.5 | Retención y borrado de datos de interacción [MUST] | ✅ | La **obligación** es N/A: exige política de retención para prompts *que contengan datos personales*, y estos no los tienen. Aun así se implementa por motivos operativos: `INCIDENT_TRACE_RETENTION_DAYS` (90 días) poda prompts y respuestas conservando el caso, con el resumen de lo eliminado — que es justo lo que pide el propio §4.5: metadatos inmutables, contenido borrable. Cada poda se audita |
| 5.1 | Documentación de propósito y límites [MUST] | ✅ | `AI-GOVERNANCE.md` §2 |
| 5.2 | Mecanismo de intervención humana [MUST] | ✅ | `AI-GOVERNANCE.md` §3. El diagnóstico nunca dispara acciones; hay interruptor de parada. Desde las Fases 2 y 3, la pantalla de la sala de control deja confirmar o descartar cada causa con evidencia, y el reanálisis solo lo dispara una persona (`DISENO-INTERACCION-HUMANA.md`) |
| 5.3 | Comunicación al usuario final [MUST] | ✅ | El diagnóstico persistido lleva el bloque `_ai_generated` con aviso explícito, y la pantalla lo muestra junto a las causas («Generado por IA») |
| 5.4 | Etiquetado de contenido generado por IA [MUST] | ✅ | Ídem, **por pasada**: cada diagnóstico de `diagnosticos` lleva el suyo, porque el proveedor puede cambiar entre una y otra. Implementado en `webhook._analizar_incidente()`. **Corregido el 2026-11-25:** con DeepSeek la etiqueta registraba un modelo de Claude que no había intervenido. Ahora el modelo sale de `llm_client.modelo_en_uso()`, la misma tabla que decide la llamada, y lo cubre `tests/test_etiqueta_ia.py`. Los cuatro expedientes reales afectados no se corrigen (`AI-TRACEABILITY.md` §3) |
| 6.1 | Clasificación de riesgo [MUST] | ✅ | Baja. `AI-GOVERNANCE.md` §1, con los criterios que la elevarían |
| 6.2 | Evaluación de impacto [MUST] | **N/A** | Exigible a riesgo medio-alto. Este es bajo |
| 6.3 | Usos prohibidos y restringidos [MUST] | ✅ | `AI-GOVERNANCE.md` §6 |
| 6.4 | Inventario de sistemas de IA [MUST] | ✅ | `AI-GOVERNANCE.md` §7 |
| 6.5 | Sesgo y equidad [MUST] | **N/A** | La salida no afecta a personas: diagnostica el comportamiento de una bomba. El propio estándar permite N/A para riesgo bajo sin efecto sobre individuos |
| 6.6 | Pruebas adversarias / red-team [MUST] | **N/A** | Exigible a riesgo alto. Este es bajo |
| 7.1 | Versionado de modelo y herramienta [MUST] | ⚠️ Parcial | `AI-TRACEABILITY.md` §1 y §2, como log consultable. El hueco de herramientas de desarrollo del 2026-09-08 al 2026-10-08 se completó el 2026-11-25 a partir de los commits. Queda uno: el modelo de los expedientes analizados con DeepSeek antes del 2026-11-25 es incorrecto (defecto ya corregido, ver 5.4) |
| 7.2 | Reproducibilidad razonable [SHOULD] | ✅ | `AI-TRACEABILITY.md` §3. Se guarda todo lo enviado y recibido; `temperature` no se fija, así que la reproducción exacta no está garantizada y así se declara |
| 7.3 | Gestión del cambio de proveedor [MUST] | ✅ | `AI-TRACEABILITY.md` §2 y `AGENTS.md` §Proveedor de LLM. Tres rutas tras la misma función (`gemini`, `anthropic` y, desde el 2026-09-30, `deepseek`), cada una con su configuración propia. La asimetría de razonamiento entre proveedores quedó **cerrada** el 2026-09-28 |
| 8 | Gate de aprobación de despliegue [MUST] | ✅ | Checklist abajo |
| 9.1 | Monitorización de comportamiento anómalo [MUST] | ✅ | `AI-GOVERNANCE.md` §8, con las señales concretas del log |
| 9.2 | Plan de respuesta a incidentes de IA [MUST] | ✅ | `AI-GOVERNANCE.md` §8 |
| 9.3 | Rollback inmediato [MUST] | ✅ | `AI-GOVERNANCE.md` §8 |
| 9.4 | Revisión periódica post-despliegue [SHOULD] | **N/A** | No hay despliegue productivo todavía |
| 9.5 | Interruptor de parada [MUST] | ✅ | `WORKFLOW_ENABLED=false`. `AI-GOVERNANCE.md` §5, con su procedimiento de prueba. **Probado en el servicio real el 2026-09-08** (`AI-TRACEABILITY.md` §8) |
| 10.1 | Formación obligatoria [MUST] | ⏳ Organizativo | Fuera del alcance del repositorio: corresponde a la organización |
| 10.2 | Guía de prompting responsable [SHOULD] | ⏳ Organizativo | Ídem. Los límites específicos de este recurso están en `AGENTS.md` §Guardrails |
| 11.1 | Revisión periódica de la política [SHOULD] | ⏳ Organizativo | Corresponde al propietario de izSpecs |
| 11.2 | Auditorías internas de cumplimiento [SHOULD] | ⏳ Organizativo | Ídem |
| 11.3 | Registro de excepciones [MUST] | ✅ | `AI-GOVERNANCE.md` §9 — ninguna abierta |

## extensions/observability-spec.md v1.1

| § | Requisito | Estado | Cómo se cumple |
|---|---|---|---|
| 1 | Logging estructurado [MUST] | ✅ | `observability.JsonFormatter`: una línea de JSON por evento con `ts` (ISO 8601 con ms), `level` (ERROR/WARN/INFO/DEBUG), `component` (`pi-rca-workflow`) y `msg`. Extras en camelCase. Truncado a 200 caracteres. Verificado en `tests/test_observability.py` |
| 2.1 | Campos del audit trail [MUST] | ✅ | `observability.audit()`: `ts`, `op`, `args` (truncados a 120), `status`, `detail` |
| 2.2 | Escritura antes de ejecutar [MUST] | ✅ | Las llamadas a `audit()` preceden a la operación. `BLOCKED` para lo rechazado: notificación no analizable, duplicados, enfriamiento y parada en caliente |
| 2.3 | Separación del log de aplicación [MUST] | ✅ | `AUDIT_FILE` distinto de `LOG_FILE`. Verificado en la prueba. **Corregido el 2026-11-25:** el servidor de pruebas compartía el audit y el log del servicio real; ahora usa los suyos (`dev-fixtures/`), y el arranque avisa si un proceso con otra carpeta de incidentes sigue apuntando al audit o al log del servicio. El audit real conserva 106 entradas de pruebas anteriores a esa fecha, que se distinguen por el `incidentId` |
| 2.4 | Durabilidad del audit trail [MUST] | ✅ | Fichero JSON Lines append-only que sobrevive a reinicios |

**Operaciones auditadas.** El workflow apenas muta estado externo; se auditan:

| Operación | Por qué |
|---|---|
| `create_timeseries_bucket` | Único punto que crea estado fuera del proceso (en `aveva-pi-mcp`) |
| `incident.claim` | Crea un registro persistente. `BLOCKED` cuando se descarta por duplicado o enfriamiento |
| `incident.mark` | Transición de estado del registro |
| `run_rca_analysis` | Arranque del análisis. `BLOCKED` con el interruptor de parada bajado |
| `notification.accept` | Entrada de una notificación de PI. `BLOCKED` si no es analizable |
| `incident.veredicto` | Una persona confirma, descarta o devuelve a pendiente una causa |
| `incident.reclasificacion` | Una persona marca (o retira) la alerta como no válida |
| `incident.reanalisis` | Una persona pide volver a analizar |
| `incident.reanalisis_fallido` | Un reanálisis falla; la pasada anterior se conserva |
| `incident.prune_trace` | La poda de prompts a los 90 días: elimina contenido de forma permanente |

`query_by_path`, `graph_neighborhood` y la búsqueda en la planta **no** se auditan: son de
solo lectura.

---

## Gate de aprobación de despliegue (ai-governance §8)

Estado a 2026-09-08, **revisado el 2026-11-25**. El recurso está **desplegado en el entorno de demostración** (servicio de
Windows en marcha, arranque automático), no en producción sobre una planta real.

- [x] Trazabilidad de autoría y herramienta registrada — `AI-TRACEABILITY.md`
- [x] Revisión humana completada y aprobada por el propietario — `master` protegido y PR #1 revisado y fusionado (2026-09-08)
- [x] Escaneo de seguridad sin hallazgos críticos — `bandit` y `pip-audit` en verde sobre `master` desde el 2026-09-08
- [x] Pruebas ejecutadas y umbral alcanzado — 19 suites, en verde en local el 2026-11-25 (el estado del CI no se ha vuelto a comprobar)
- [x] Licencias de dependencias verificadas — `AI-TRACEABILITY.md` §6
- [x] Clasificación de riesgo asignada — Baja
- [x] Datos sensibles tratados según la política — sin datos personales
- [x] Contenido generado por IA etiquetado — bloque `_ai_generated`, con el modelo correcto para los tres proveedores desde el 2026-11-25 (el servicio en marcha lo tendrá al reiniciarse)
- [x] Evaluación de sesgo — N/A (riesgo bajo, sin efecto sobre personas)
- [x] Pruebas adversarias — N/A (riesgo bajo)
- [x] Interruptor de parada en su sitio **y probado** — probado en el servicio real el 2026-09-08: la notificación se acepta, queda en `pausado` y `BLOCKED`, y no se llama ni al modelo ni a PI. Falta un tramo del procedimiento: reactivar y reenviar la alerta pausada para comprobar que se procesa (ver brechas)
- [x] Documentación técnica y de uso al día — `README.md`, `AGENTS.md`, `docs/`
- [x] Plan de rollback definido — `AI-GOVERNANCE.md` §8

---

## Decisiones tomadas, no pendientes

**El repositorio conserva el nombre `PI-rca-agent`** (2026-09-08). Se valoró renombrarlo a
`PI-rca-workflow` por coherencia con la terminología y se descartó: izSpecs no dice nada sobre
nombres de repositorio, y quien lo abre queda corregido de inmediato — `AGENTS.md` y el primer
párrafo del `README.md` aclaran que esto es un workflow, y el módulo principal es `workflow.py`.
No es una brecha ni una excepción: es una decisión.

## Brechas conocidas

Ninguna es una excepción aprobada: son trabajo pendiente.

| # | Brecha | Sección | Acción |
|---|---|---|---|
| 1 | Los commits anteriores al 2026-09-07 no llevan fecha de generación | base §1.4 | No se corrige: reescribir el historial violaría §1.7. Compensado con `AI-TRACEABILITY.md` §1 |
| 2 | Validación funcional con un solo KPI (`Hydraulic Efficiency`) y una única causa confirmada, sin constancia de que la confirmara mantenimiento | §2.3 | Ejercitar con más KPIs y tipos de activo, y registrar confirmaciones tras inspección |
| 3 | Del interruptor de parada falta el último tramo: reactivar y reenviar la alerta pausada. El incidente `pausado` del 2026-09-08 sigue sin procesar | §9.5 | Completar el procedimiento de `AI-GOVERNANCE.md` §5 |

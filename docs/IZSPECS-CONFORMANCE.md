# Matriz de conformidad con izSpecs

- **izSpecs bundle:** `2026.12-09011521`
- **Validado el:** 2026-09-07
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
| 1.4 | Divulgación de autoría IA [MUST] | ⚠️ Parcial | Convención con modelo **y fecha** documentada en `AGENTS.md` y aplicada desde 2026-09-07. Los commits anteriores llevan solo `Co-Authored-By`, sin fecha: **no se reescriben** (§1.7). La brecha y el modelo real usado en cada periodo están en `docs/AI-TRACEABILITY.md` §1 |
| 1.5 | Procedencia del prompt [SHOULD] | ✅ | Los prompts de producción se persisten en `incidents/<id>.json` → `trace`. Los de desarrollo, en `AI-TRACEABILITY.md` §4 |
| 1.6 | Trazabilidad de datos de entrada [MUST] | ✅ | `AI-TRACEABILITY.md` §5 |
| 1.7 | Inmutabilidad de la trazabilidad [MUST] | ✅ | Registros append-only bajo control de git. Documentado en la cabecera de `AI-TRACEABILITY.md` |
| 2 | Portabilidad [SHOULD] | ✅ | Sin rutas absolutas en el código ni en las pruebas; todo lo específico de la máquina va por `.env` con valores por defecto. Las pruebas corren sin PI, sin MCP servers y sin claves |
| 3 | Configuración por entorno [SHOULD] | ✅ | `config.py` lee todo de `.env`. Ninguna clave de configuración cableada |
| 4 | Declaración de conformidad [MUST] | ✅ | `AGENTS.md` §iz Spec Conformance |
| 5 | Línea base de seguridad [SHOULD] | ✅ | Secretos solo en `.env`, en `.gitignore`, con el riesgo declarado en el `README.md`. Dependencias fijadas a versión exacta y escaneadas con `pip-audit` en CI. Los errores no vuelcan trazas ni rutas internas. `/notifications/history` apagado por defecto. **Autenticación, TLS y firewall evaluados y no aplicados**, con la justificación en el bloque «DECISIONES DE SEGURIDAD» de `webhook.py`: los dos primeros dependen de PI y están bloqueados; el tercero es una decisión de infraestructura sobre una plataforma de pruebas. Todo ello queda sin validez si el recurso sale del entorno de demo |
| 6.1 | Sin auto-merge sin revisión [MUST] | ✅ | Gate de CI en `.github/workflows/quality-gate.yml`; el propietario revisa y aprueba. Configurar como *required check* en la protección de rama |
| 6.2 | Propietario humano identificado [MUST] | ✅ | Sandrosky99 |
| 6.3 | Revisión proporcional al riesgo [MUST] | ✅ | Riesgo bajo: revisión por el propietario. La revisión por dos personas aplicaría al reclasificar (`AI-GOVERNANCE.md` §1) |
| 6.4 | Sin delegar el juicio crítico [MUST] | ✅ | Las decisiones de arquitectura están documentadas con su razonamiento y validadas por el propietario. Ver el historial de `AGENTS.md` |

## extensions/ai-governance-spec.md v1.2

| § | Requisito | Estado | Cómo se cumple |
|---|---|---|---|
| 1.1 | Sin exposición de secretos [MUST] | ✅ | `.env` fuera de git; `webhook.log`, `audit.jsonl` e `incidents/` también. El gate de CI busca patrones de credenciales y verifica que `.env` no esté versionado |
| 1.2 | Escaneo de vulnerabilidades [MUST] | ✅ | `bandit` (SAST) y `pip-audit` (SCA) en el gate de CI. DAST N/A: no hay superficie web pública |
| 1.3 | Revisión de dependencias [MUST] | ✅ | `AI-TRACEABILITY.md` §6 — mantenimiento, adopción y licencia de las 8 dependencias |
| 1.4 | Datos sensibles en el contexto del modelo [MUST] | ✅ | Solo magnitudes de proceso y nombres de activo. Sin datos personales. El límite está escrito en `AGENTS.md` §Guardrails y en `AI-GOVERNANCE.md` §6 |
| 1.5 | Mínimo privilegio y agencia acotada [MUST] | ✅ | El modelo **no tiene tools**. Sus dos decisiones están acotadas por código (`_validate_selected_variables`, `_parse_history_request`) y auditadas. No ejecuta comandos ni escribe ficheros |
| 1.6 | Revisión de patrones inseguros [SHOULD] | ✅ | `bandit` en CI. Sin `eval`, sin `shell=True`, sin deserialización insegura |
| 1.7 | Inyección de prompt y entrada adversaria [MUST] | ✅ | La entrada no confiable (payload de PI, respuesta del grafo, salida de los MCP) no puede alterar las instrucciones del sistema: el `SYSTEM_PROMPT` es una constante y va por el parámetro `system`, separado del mensaje. Los campos del payload se validan por tipo (`_valid_field`) y la notificación se rechaza en la puerta si no es analizable (`validate_notification`) |
| 1.8 | Manejo inseguro de la salida [MUST] | ✅ | **El núcleo del diseño.** La salida del modelo nunca se ejecuta ni se pasa a un intérprete. Los `piApiPath` se validan contra la lista blanca del AF antes de consultar; la petición de ventana, contra un catálogo cerrado. El JSON se parsea con `json.loads`, nunca con `eval` |
| 2.1 | Cobertura de pruebas [MUST] | ✅ | 7 suites en `tests/`, 241 comprobaciones. Umbral del proyecto: toda función con lógica de decisión o validación debe tener pruebas de su camino feliz, sus modos de fallo y sus entradas malformadas |
| 2.2 | Revisión de las pruebas [MUST] | ✅ | Verifican comportamiento real, no cobertura: cada una detecta un fallo concreto. Prueba de ello: `test_incidents` detectó una colisión real de nombres por resolución de reloj el 2026-09-07, y `test_observability` una captura de excepción demasiado estrecha |
| 2.3 | Validación funcional documentada [MUST] | ⚠️ Parcial | `AI-TRACEABILITY.md` §8. **Dos** ciclos completos contra PI y LLM reales (2026-08-20 y 2026-09-07), pero ambos del mismo activo y KPI, y **ninguna causa confirmada** por mantenimiento |
| 2.4 | Gate de calidad en CI/CD [MUST] | ✅ | `.github/workflows/quality-gate.yml`: pruebas + SAST + SCA + búsqueda de secretos. Falla la rama |
| 3.1 | Verificación de licencias del código generado [MUST] | ✅ | `AI-TRACEABILITY.md` §7 |
| 3.2 | Declaración de originalidad [SHOULD] | ✅ | Valorado; riesgo bajo, sin herramienta de similitud. Justificado en `AI-TRACEABILITY.md` §7 |
| 3.3 | Registro de la herramienta de IA [MUST] | ✅ | `AI-TRACEABILITY.md` §1 |
| 4.1 | Minimización de datos en prompts [MUST] | ✅ | Sin datos personales en ningún prompt. Prohibido añadirlos (`AI-GOVERNANCE.md` §6) |
| 4.2 | Anonimización previa [MUST] | **N/A** | Los datos de prueba son magnitudes de proceso industrial; no hay datos personales que anonimizar |
| 4.3 | Cumplimiento regulatorio [MUST] | ✅ | Sin datos personales: RGPD no aplica al tratamiento. Riesgo bajo bajo el AI Act (no es sistema de alto riesgo del Anexo III). Reevaluar al reclasificar |
| 4.4 | Registro de actividades de tratamiento [MUST] | **N/A** | No se tratan datos personales en producción |
| 4.5 | Retención y borrado de datos de interacción [MUST] | ✅ | La **obligación** es N/A: exige política de retención para prompts *que contengan datos personales*, y estos no los tienen. Aun así se implementa por motivos operativos: `INCIDENT_TRACE_RETENTION_DAYS` (90 días) poda prompts y respuestas conservando el caso, con el resumen de lo eliminado — que es justo lo que pide el propio §4.5: metadatos inmutables, contenido borrable. Cada poda se audita |
| 5.1 | Documentación de propósito y límites [MUST] | ✅ | `AI-GOVERNANCE.md` §2 |
| 5.2 | Mecanismo de intervención humana [MUST] | ✅ | `AI-GOVERNANCE.md` §3. El diagnóstico nunca dispara acciones; hay interruptor de parada |
| 5.3 | Comunicación al usuario final [MUST] | ✅ | El diagnóstico persistido lleva el bloque `_ai_generated` con aviso explícito |
| 5.4 | Etiquetado de contenido generado por IA [MUST] | ✅ | Ídem. Implementado en `webhook._analizar_incidente()` |
| 6.1 | Clasificación de riesgo [MUST] | ✅ | Baja. `AI-GOVERNANCE.md` §1, con los criterios que la elevarían |
| 6.2 | Evaluación de impacto [MUST] | **N/A** | Exigible a riesgo medio-alto. Este es bajo |
| 6.3 | Usos prohibidos y restringidos [MUST] | ✅ | `AI-GOVERNANCE.md` §6 |
| 6.4 | Inventario de sistemas de IA [MUST] | ✅ | `AI-GOVERNANCE.md` §7 |
| 6.5 | Sesgo y equidad [MUST] | **N/A** | La salida no afecta a personas: diagnostica el comportamiento de una bomba. El propio estándar permite N/A para riesgo bajo sin efecto sobre individuos |
| 6.6 | Pruebas adversarias / red-team [MUST] | **N/A** | Exigible a riesgo alto. Este es bajo |
| 7.1 | Versionado de modelo y herramienta [MUST] | ✅ | `AI-TRACEABILITY.md` §1 y §2, como log consultable |
| 7.2 | Reproducibilidad razonable [SHOULD] | ✅ | `AI-TRACEABILITY.md` §3. Se guarda todo lo enviado y recibido; `temperature` no se fija, así que la reproducción exacta no está garantizada y así se declara |
| 7.3 | Gestión del cambio de proveedor [MUST] | ✅ | `AI-TRACEABILITY.md` §2. Documentada la asimetría de razonamiento entre proveedores |
| 8 | Gate de aprobación de despliegue [MUST] | ✅ | Checklist abajo |
| 9.1 | Monitorización de comportamiento anómalo [MUST] | ✅ | `AI-GOVERNANCE.md` §8, con las señales concretas del log |
| 9.2 | Plan de respuesta a incidentes de IA [MUST] | ✅ | `AI-GOVERNANCE.md` §8 |
| 9.3 | Rollback inmediato [MUST] | ✅ | `AI-GOVERNANCE.md` §8 |
| 9.4 | Revisión periódica post-despliegue [SHOULD] | **N/A** | No hay despliegue productivo todavía |
| 9.5 | Interruptor de parada [MUST] | ✅ | `WORKFLOW_ENABLED=false`. `AI-GOVERNANCE.md` §5, con su procedimiento de prueba |
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
| 2.2 | Escritura antes de ejecutar [MUST] | ✅ | Las llamadas a `audit()` preceden a la operación. `BLOCKED` para lo rechazado: duplicados, enfriamiento y parada en caliente |
| 2.3 | Separación del log de aplicación [MUST] | ✅ | `AUDIT_FILE` distinto de `LOG_FILE`. Verificado en la prueba |
| 2.4 | Durabilidad del audit trail [MUST] | ✅ | Fichero JSON Lines append-only que sobrevive a reinicios |

**Operaciones auditadas.** El workflow apenas muta estado externo; se auditan:

| Operación | Por qué |
|---|---|
| `create_timeseries_bucket` | Único punto que crea estado fuera del proceso (en `aveva-pi-mcp`) |
| `incident.claim` | Crea un registro persistente. `BLOCKED` cuando se descarta por duplicado o enfriamiento |
| `incident.mark` | Transición de estado del registro |
| `run_rca_analysis` | Arranque del análisis. `BLOCKED` con el interruptor de parada bajado |

`query_by_path` y `graph_neighborhood` **no** se auditan: son de solo lectura.

---

## Gate de aprobación de despliegue (ai-governance §8)

Estado a 2026-09-07. **Este recurso no está desplegado en producción**; la lista refleja qué
faltaría.

- [x] Trazabilidad de autoría y herramienta registrada — `AI-TRACEABILITY.md`
- [ ] **Revisión humana completada y aprobada por el propietario** — pendiente: este trabajo
      lo ha generado una IA y necesita revisión antes de fusionarse
- [ ] **Escaneo de seguridad sin hallazgos críticos** — el gate de CI existe pero **no se ha
      ejecutado nunca**: el repositorio no tiene Actions habilitadas
- [x] Pruebas ejecutadas y umbral alcanzado — 5 suites, `python tests/run_all.py`
- [x] Licencias de dependencias verificadas — `AI-TRACEABILITY.md` §6
- [x] Clasificación de riesgo asignada — Baja
- [x] Datos sensibles tratados según la política — sin datos personales
- [x] Contenido generado por IA etiquetado — bloque `_ai_generated`
- [x] Evaluación de sesgo — N/A (riesgo bajo, sin efecto sobre personas)
- [x] Pruebas adversarias — N/A (riesgo bajo)
- [x] Interruptor de parada en su sitio — `WORKFLOW_ENABLED`; **falta probarlo en el entorno real**
- [x] Documentación técnica y de uso al día — `README.md`, `AGENTS.md`, `docs/`
- [x] Plan de rollback definido — `AI-GOVERNANCE.md` §8

---

## Brechas conocidas

Ninguna es una excepción aprobada: son trabajo pendiente.

| # | Brecha | Sección | Acción |
|---|---|---|---|
| 1 | El gate de CI nunca se ha ejecutado | §2.4, §8 | Habilitar GitHub Actions en el repositorio y configurar el workflow como *required check* |
| 2 | Los commits anteriores al 2026-09-07 no llevan fecha de generación | base §1.4 | No se corrige: reescribir el historial violaría §1.7. Compensado con `AI-TRACEABILITY.md` §1 |
| 4 | Validación funcional con un único ciclo real | §2.3 | Ejercitar con más KPIs y tipos de activo |
| 5 | El interruptor de parada no se ha probado en el entorno real | §9.5 | Ejecutar el procedimiento de `AI-GOVERNANCE.md` §5 |
| 6 | El repositorio se sigue llamando `PI-rca-agent` | — | Renombrar en GitHub a `PI-rca-workflow` |

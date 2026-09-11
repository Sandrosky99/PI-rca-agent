# Sala de control RCA — diseño de la interacción humana

> Cómo una persona lee lo que ha propuesto el workflow, lo contradice con lo que ve
> en planta, y deja constancia de qué se decidió hacer.

| | |
|---|---|
| **Fecha** | 10 de septiembre de 2026 |
| **Estado** | Diseño cerrado · sin implementar |
| **Alcance** | Fases 1 a 3 |

---

## 1. Los principios que sostienen todo lo demás

Cinco decisiones de las que se deduce casi todo el resto. Si alguna vez hay que
replantear el diseño, es aquí donde hay que discutir, no en los botones.

### El aviso a las personas lo manda PI, no el workflow

PI System ya tiene los turnos, las guardias y las listas de distribución,
mantenidas por gente cuyo trabajo es mantenerlas. Cualquier lista paralela nacería
desactualizada. Y como los dos caminos son independientes, si el workflow se cae el
aviso sale igual: la alarma nunca depende de este código.

### Hay tres relojes y no se pueden confundir

El aviso sale en el minuto cero. El análisis está listo a los pocos minutos. Y saber
de verdad si la causa era la correcta puede llevar horas o días, porque exige ir a
mirar la máquina. Cada cosa se pide en su reloj.

### Cerrar el incidente no es resolver el problema

El workflow cierra cuando la responsabilidad se transfiere, no cuando la bomba está
arreglada. En cuanto existe el número de OT, este registro está completo; que esa OT
tarde tres días o tres meses lo sabe el GMAO, que es el sistema que debe saberlo.

### El modelo ayuda, no decide — y no se le da la razón sola

Cuando el operario aporta una sospecha, entra como pista a contrastar contra datos,
nunca como conclusión. Por eso el reproceso vuelve por la selección de variables y
trae datos nuevos: sin eso, la segunda respuesta es solo el segundo clasificado
ascendido, con el mismo tono de autoridad y menos fundamento.

### El reloj rápido registra lo que pasó; el lento clasifica

La pantalla de la sala de control captura hechos: qué se descartó, con qué evidencia,
quién tocó qué. Los juicios que admiten reposo — si la alerta era un falso positivo,
si finalmente la primera causa era la buena — se hacen en frío, en la pestaña de
cerrados.

---

## 2. El flujo, paso a paso

### 1. Llega la alerta

PI System manda el POST al workflow *y a la vez* el aviso a las personas pertinentes.
Caminos independientes.

### 2. La persona decide acudir

Por procedimiento sabe que el resultado está en el monitor de la sala de control.
Lleva el payload que le llegó por correo, que es lo que le permitirá identificar su
caso si hay varios activos.

### 3. La pantalla muestra el estado, sea cual sea

Junto al payload: **procesando** si el análisis sigue en marcha, **listo** si hay
causas, o **fallo del análisis** si se cayó el MCP, PI no respondió o el modelo
devolvió basura.

Nunca un «procesando» que no termina. Alguien que viene de planta y encuentra una
pantalla que le miente no vuelve.

### 4. El workflow propone dos o tres causas

Cada una con su explicación y su acción recomendada, y cada una con su propio estado:
pendiente, descartada o confirmada. Son candidatas independientes, no un bloque.

### 5. La revisión

Sobre **cada causa por separado** se puede confirmar, descartar o no hacer nada. Al
descartar se abren dos campos: uno **obligatorio** con la evidencia de por qué no es,
y uno **opcional** con lo que el operario sospecha, que entra marcado como pista a
contrastar.

De ahí salen estos finales:

**a. Causa confirmada** → cierra

Marca una causa como correcta. Se llama *causa confirmada*, nunca «resuelto»: la
bomba sigue sucia hasta que alguien la limpie, y son dos cosas distintas.

**b. Descarta una y va a revisar la siguiente** → no cierra

Queda la evidencia registrada. Si vuelve, continúa donde lo dejó.

Si no vuelve y vence la ventana, cierra como **revisado parcialmente**. No como
desatendido: sí lo atendieron, y descartar una hipótesis con evidencia física es
trabajo que no se puede tirar.

**c. Relanza con el feedback** → vuelve al paso 4

El botón se habilita en cuanto hay **una** causa descartada con evidencia no vacía.
Lo pulsa cuando quiera: tras descartar una, dos o todas.

Siempre botón, nunca automático. Cuando se han descartado todas es justo cuando menos
probable es que quede alguien delante para leer el recálculo — y además gastaría una
iteración a ciegas.

**d. Agotado el presupuesto de relanzados** → cierra

Cierra como **causa no determinada**, con el registro de qué se descartó y con qué
evidencia. Ese registro vale más que una respuesta equivocada: es por donde empieza el
siguiente que coja el caso.

**e. Nadie da veredicto y vence la ventana** → cierra

Se parte en dos según haya habido actividad en pantalla: **desatendido** si no hubo ni
un clic, **visto sin veredicto** si alguien desplegó, miró y se fue sin marcar.

Este segundo va a ser el caso más frecuente — el operario baja, acierta el
diagnóstico, arregla y no vuelve a pulsar nada. Sin la distinción, el acierto limpio y
el abandono total son el mismo registro.

**f. Fallo técnico** → cierra

Cierra como **fallo del análisis**. No cuenta ni como acierto ni como error del
modelo.

### 6. Cierre y anotación posterior

Todos los finales cierran **solos, por tiempo**. Nada queda esperando a nadie, y nadie
tiene que ir a apagar un incidente.

Después, en la pestaña de cerrados y con ventana larga, se anota. A menudo lo hará
*otra persona*: un responsable al día siguiente, no el operario que bajó a mirar de
madrugada.

---

## 3. Los seis cierres

Los tres primeros son resultado del análisis. Los dos siguientes miden si la pantalla
se usa. El último no cuenta para la calidad del modelo.

| Cierre | Qué dice de verdad |
|---|---|
| **Causa confirmada** | Acierto |
| **Revisado parcialmente** | Descartó alguna con evidencia, no terminó |
| **Causa no determinada** | Se agotaron las iteraciones; consta qué no era |
| **Visto sin veredicto** | Alguien miró y no dijo nada |
| **Desatendido** | Nadie tocó nada |
| **Fallo del análisis** | Problema técnico |

> **Por qué importa separarlos.** Dentro de seis meses, mirando los números, hay que
> poder distinguir «el workflow no acierta» de «el workflow acierta y nadie lo
> confirma». Son diagnósticos opuestos y desde fuera tienen la misma pinta.

---

## 4. La anotación posterior

Sobre un incidente *ya cerrado*. Cerrar y anotar son operaciones distintas: el
workflow no espera a nadie, y el rastro humano puede llegar dos horas o dos días
después sin mantener nada abierto.

### Disposición

Un desplegable de tres y un campo de texto corto. Con autor y fecha.

| Disposición | Nuestro registro está completo cuando… | Lo que *no* hacemos |
|---|---|---|
| **Actuado** | existe el nº de OT | seguir esa OT ni saber si se cerró |
| **Sin acción** | se anota, con autor | nada más |
| **En seguimiento** | se anota con fecha de revisión | correr nosotros ese plazo |

### Reclasificación

La anotación puede **cambiar el cierre**, y esto es lo que rescata los dos casos que
la pantalla en caliente no puede capturar bien:

- **A causa confirmada**, cuando alguien vuelve en frío y dice «la primera era
  correcta». Sin esto, el dato de acierto se pierde en el «visto sin veredicto».
- **A alerta no válida**, cuando el problema no existía y era el umbral de PI el que
  estaba mal. El modelo no se equivocó: le dieron un problema inventado. Es
  información que el equipo de PI necesita, y meterla dentro de «incorrecto»
  ensuciaría las métricas.

> **Por qué la alerta falsa no es un botón en caliente.** Todos los demás botones
> actúan sobre *una causa*; ese actuaría sobre *el incidente entero*. Mezclar dos
> alcances en la misma pantalla es lo que hace que alguien con prisa pulse el que no
> era — y confundirlos cuesta caro: pierdes el bucle de feedback y además avisas de un
> umbral defectuoso que estaba bien.
>
> Además, juzgar que un umbral está mal configurado no es una llamada del operario a
> las tres de la mañana. Es de quien anota después, que suele estar mejor situado para
> hacerla.

Las métricas usan siempre el valor anotado cuando existe.

---

## 5. Qué necesita la pantalla

Marcado con **[lista inicial]** lo que ya estaba identificado, con **[añadido]** lo
que faltaba, y con **[recomendado]** lo que mejora mucho la pantalla pero no la
bloquea.

### Zona de identificación

**Payload de la alerta** · [lista inicial]
Los mismos campos que la persona recibió por correo: activo, KPI, valor, umbral, hora
de inicio. Es lo único que le permite saber que la pantalla está hablando de *su* caso.

**Estado del incidente** · [añadido]
Procesando · Listo · Recalculando · Fallo del análisis. Explícito siempre, incluido el
fallo.

**Selector de incidentes activos** · [añadido]
Si puede haber dos análisis a la vez —y puede—, hace falta poder pasar de uno a otro.
Si no, la persona no sabe si está mirando el suyo.

**Aviso de que el análisis lo ha generado una IA** · [añadido]
Una línea de texto. Es obligación de transparencia del reglamento europeo de IA y
cuesta nada ponerla desde el primer día.

### Zona de análisis

**Las causas propuestas, con estado individual** · [añadido]
Dos o tres, cada una con explicación, acción recomendada y su estado propio:
pendiente, descartada o confirmada. Es el contenido central de la pantalla.

**Evidencia ya registrada** · [añadido]
Al volver de planta, la persona tiene que ver lo que ella misma escribió antes. Sin
esto, el caso *b* —descartar una y volver luego— no funciona.

**Datos que sustentan cada causa** · [recomendado]
Qué variables miró el modelo y en qué ventana. Sin esto la persona tiene que creerse
el diagnóstico a ciegas, y es justo lo que le permite detectar que se apoyó en un
sensor que ella sabe que está mal.

### Zona de veredicto

**Botón «causa correcta», por causa** · [lista inicial]
Confirma esa candidata y cierra el incidente.

**Botón «descartar causa», por causa** · [añadido]
El par de «causa correcta», y el que abre los dos campos de texto. Sin él la lista no
está completa.

**Campo obligatorio: por qué no es** · [lista inicial]
La evidencia. Entra como hecho. Es lo que hace que la segunda pasada valga para algo.

**Campo opcional: qué sospecha** · [lista inicial]
Entra explícitamente marcado como pista a contrastar, nunca como conclusión. Separarlo
del anterior en la *estructura* es lo que impide que el modelo tome la hipótesis del
operario por buena.

**Botón «recalcular»** · [lista inicial]
Deshabilitado hasta que haya al menos una causa descartada con evidencia no vacía. Sin
nada nuevo que aportar, la segunda llamada sería una tirada de dados con los mismos
datos.

**Contador de reanálisis** · [añadido]
«Te queda 1 de 2». Un presupuesto que se ve se gasta mejor que uno que no se ve.

**Tiempo restante de la ventana** · [recomendado]
Que se sepa cuánto queda antes de que cierre solo, para no volver de planta y
encontrárselo cerrado sin aviso.

### Fuera de la pantalla en caliente

**Pestaña de cerrados** · [añadido]
La superficie del reloj lento: disposición, comentarios, reclasificación. Público
distinto y plazo distinto — puede que acabe siendo otra pantalla, en otro sitio.

**Registro de interacción** · [añadido]
Invisible, pero imprescindible: una marca de tiempo cuando alguien toca el incidente.
Es lo único que separa «desatendido» de «visto sin veredicto», y de ello depende que
las métricas de acierto signifiquen algo.

---

## 6. Lo que deliberadamente no va

Decisiones tomadas a propósito. Anotadas aquí para no tener que volver a discutirlas
dentro de tres meses.

- **Un chat abierto con el modelo.** Los campos son de propósito único: aportar
  evidencia sobre una causa concreta. No es una puerta para conversar, y acotarlo así
  acota el coste, el alcance y la superficie de «la IA decide cosas».

- **Un botón de «alerta falsa» en la pantalla en caliente.** Alcance distinto al de
  los demás botones y riesgo alto de confusión. Se hace en frío, reclasificando.

- **Gestión de órdenes de trabajo.** Se guarda el número y nada más. Duplicar aquí el
  contenido de la OT garantiza que los dos registros acaben divergiendo, y el que
  manda es el del GMAO.

- **Seguimiento del estado de la OT.** No sabemos ni debemos saber si se cerró.

- **Avisos de seguimiento.** La fecha de «en seguimiento» es una nota para quien lea el
  expediente, no un temporizador que corra este sistema. En cuanto empecemos a mandar
  recordatorios estamos haciendo de GMAO.

- **Gestión de usuarios.** El único campo que necesita autor de verdad es la
  disposición — la línea que dice que una persona decidió. Basta un turno en un
  desplegable. Añadir identidad completa convierte los ficheros de incidente en datos
  personales, con lo que eso arrastra.

- **Bloquear el cierre hasta que alguien anote.** El incidente cierra solo. La función
  forzadora no es un botón bloqueado, es que el agregado esté a la vista: si el 80 %
  cierra sin disposición, eso es un problema de organización y hay que verlo pronto.

---

## Dependencias para implementar

| # | Dependencia | Bloquea a |
|---|---|---|
| 1 | **Esquema del fichero de incidente** — candidatas con estado individual, evidencia por candidata, iteraciones, marca de interacción, cierre y anotación con autor | Todo lo demás |
| 2 | **Transporte de la pantalla** (SSE o sondeo), endpoints nuevos en la app FastAPI | Fase 1 |
| 3 | **Re-entrada en `run_rca_analysis`** — hoy solo acepta el payload; hay que sacar el Step 4 a un punto re-entrable | Fase 3 |
| 4 | **Arreglo de `pausado`** — no bloqueante con el estado de espera deducido, pero conviene no construir sobre una máquina de estados con un agujero conocido | — |

Fases, donde cada una sirve por sí sola:

1. **Pantalla de solo lectura.** Incidentes, estado y resultado. Cero decisiones
   pendientes, no toca el workflow, y resuelve hoy la falta de visibilidad.
2. **Captura del feedback**, guardado sin bucle. Permite ver qué escribe la gente de
   verdad antes de construir encima.
3. **La vuelta al modelo**, con las dos reglas: verificar en vez de razonar, y «no lo
   sé» como resultado de primera clase.

---

*Redactado con asistencia de IA (claude-opus-5) a partir de la conversación de diseño
del 10 de septiembre de 2026.*

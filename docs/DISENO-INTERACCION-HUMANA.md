# Sala de control RCA — diseño de la interacción humana

> Cómo una persona lee lo que ha propuesto el workflow, lo contradice con lo que ve
> en planta, y deja constancia de qué se decidió hacer.

| | |
|---|---|
| **Fecha** | 10 de septiembre de 2026 · revisado el 22 |
| **Estado** | Fases 1 y 2 entregadas · Fase 3 con el diseño cerrado, sin empezar |
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

El workflow cierra cuando hay una respuesta a **su** pregunta —cuál era la causa—, no
cuando la bomba está arreglada. Si hay que actuar, eso vive entero en el GMAO: aquí no
se guarda ni el nº de OT (ver §4).

### El modelo ayuda, no decide — y no se le da la razón sola

Cuando el operario aporta una sospecha, entra como pista a contrastar contra datos,
nunca como conclusión. Por eso el reproceso vuelve por la selección de variables y
trae datos nuevos: sin eso, la segunda respuesta es solo el segundo clasificado
ascendido, con el mismo tono de autoridad y menos fundamento.

### El reloj rápido registra lo que pasó; el lento clasifica

La pantalla de la sala de control captura hechos: qué se descartó y con qué evidencia.
Los juicios que admiten reposo — si la alerta era un falso positivo, si finalmente la
primera causa era la buena — se hacen en frío, en la pestaña de cerrados.

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

Si no vuelve y vence el reloj, cierra como **revisado parcialmente**. No como *sin
veredicto*: sí hubo uno, y descartar una hipótesis con evidencia física es trabajo que
no se puede tirar.

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

**e. Nadie da veredicto y vence el reloj** → cierra

Cierra como **sin veredicto**. No se afirma que nadie mirase: no nos consta.

**f. Fallo técnico** → cierra

Cierra como **fallo del análisis**. No cuenta ni como acierto ni como error del
modelo.

### 6. Corregir un veredicto

Se puede. Un clic mal dado no puede ser permanente, así que la pantalla deja devolver
una causa a «pendiente» y volver a juzgarla.

**Sin pedir motivo**: casi siempre será un error de clic, y poner fricción a corregir
un error solo consigue que no se corrija.

No borra nada. Los veredictos son una lista de **solo añadir**, así que la corrección
se anota encima y el expediente conserva los dos apuntes. En pantalla se ve el estado
actual, sin ruido; el historial queda para quien tenga que mirarlo.

---

## 3. Los cinco cierres

| Cierre | Qué dice de verdad |
|---|---|
| **Causa confirmada** | Acierto |
| **Revisado parcialmente** | Descartó alguna con evidencia, no terminó |
| **Causa no determinada** | Se descartaron todas; consta qué no era |
| **Sin veredicto** | Nadie dijo nada |
| **Fallo del análisis** | Problema técnico |

### Por qué son cinco y no seis (corregido el 2026-09-15)

Una versión anterior separaba *desatendido* (nadie tocó nada) de *visto sin veredicto*
(alguien miró y calló), y defendía esa distinción como la más valiosa de las seis: sin
ella, «el workflow acierta y nadie lo confirma» parece «el workflow no acierta».

El problema es real. **La solución era falsa.**

Distinguirlos exigía detectar presencia ante la pantalla, y al concretarlo no
sobrevivió ninguna señal: un GET no vale (el kiosco pide el detalle cada cinco
segundos, haya alguien o no), pulsar una fila tampoco (el incidente que viene a mirar
suele estar ya seleccionado), un botón de «lo estoy mirando» no lo pulsa nadie porque
no le da nada, y el ratón confunde leer con pasar por delante.

Pero lo que de verdad la tumba es otra cosa: **un detector de presencia perfecto no
habría contestado la pregunta.** Saber que alguien estuvo delante y no marcó nada
admite dos lecturas opuestas —acertamos y se fue, o fallamos y no se molestó en
decirlo—. Separa dos sabores de «no sabemos», no el acierto del error.

Lo que sí recupera ese dato es la **reclasificación en frío**, que ya estaba decidida y
no necesita telemetría: alguien vuelve y dice «la primera era correcta». Un acierto
confirmado por una persona, no inferido de un movimiento de ratón.

Así que no se detecta presencia, y la etiqueta se llama *sin veredicto* y no
*desatendido*, porque lo segundo afirmaría algo que no nos consta.

> Y si el problema es que la gente no vuelve a dar el veredicto, lo que lo arregla no
> es medirlo mejor: es **pedirlo**. Se probó así: la pestaña de cerrados se encabezaba
> con «3 casos esperan veredicto», y pulsarlo dejaba esos tres arriba y solos.
>
> **Retirado el 2026-09-21, al verlo en uso.** No añadía nada que no estuviera ya
> delante: la botonera de filtros de esa misma pestaña lleva la pastilla *sin
> veredicto* con su cuenta al lado, en la línea inmediatamente superior. Eran dos
> mandos para lo mismo, uno encima del otro.
>
> Queda anotado para que no se reinvente. El hueco que pretendía tapar es real —el
> acierto silencioso no deja rastro—, pero quien lo tapa es la botonera. Si algún día
> hiciera falta insistir más, el sitio es esa pastilla —destacarla cuando su cuenta
> suba—, no un segundo aviso que pueda discrepar de ella.

---

## 3 bis. El reloj

**Un solo reloj: 12 horas desde el último trabajo que sigue en pie.**

Mientras corre, el incidente está en **activos** y se puede reanalizar. Cuando vence,
las dos cosas se acaban **a la vez**: el botón se apaga y el incidente pasa a cerrados.

Una **respuesta terminal** —causa confirmada, o todas las causas descartadas— lo cierra
**en ese momento**, sin esperar al reloj. La pregunta ya está contestada, y tener en la
pantalla de «lo que pide atención» algo que no pide nada es lo contrario de para lo que
sirve esa pantalla.

El **veredicto** y la **reclasificación** no caducan nunca.

### Qué cuenta como «trabajo que sigue en pie» (afinado el 2026-09-21)

Una versión anterior decía «desde el último movimiento — cualquier acción lo reinicia».
Es lo que se implementó primero y produjo dos fallos que solo se ven usándolo:

**Deshacer rejuvenecía el incidente.** Un expediente cerrado hacía tres días, alguien
confirmaba una causa por error y la deshacía, y reaparecía en activos como recién
llegado. Deshacer es una corrección, no trabajo: un apunte `pendiente` no aporta fecha,
así que el reloj **retrocede** hasta el último veredicto real que quede vigente.

**Revisar en frío reabría el incidente**, y el expediente rebotaba entre las dos
pestañas a cada clic mientras se trabajaba en él. Peor que incómodo: la reclasificación
en frío es justamente lo que rescata el dato de acierto (§3), así que resucitar el
incidente por hacerla convierte una virtud en un castigo. Ahora un veredicto alarga el
reloj **solo si se dio mientras el incidente seguía abierto**; los posteriores cambian
la etiqueta y nada más.

Lo que no se pierde: el operario que descarta una causa en la hora 11 y vuelve en la 13
sigue teniendo su ventana. La cadena se mantiene viva mientras cada veredicto caiga
dentro de la que abrió el anterior; el primero que llegue fuera de plazo la corta.

> **Un incidente cerrado no se reabre nunca por acción humana** — decisión del
> propietario, 2026-09-21. En la Fase 3 eso significa que **no se podrá pedir un
> reanálisis sobre un expediente cerrado, ni aunque se le acabe de aportar evidencia
> nueva**. Es coherente con que el botón viva donde vive la pestaña: si el reanálisis
> pudiera dispararse desde cerrados, volveríamos a tener dos relojes.

### Por qué un solo número

Hubo una versión con dos: 8 h para el reanálisis y 12 h para la pestaña. Se descartó
porque producía un tramo de cuatro horas en el que el incidente seguía listado entre
los activos con el botón de reanalizar apagado — una contradicción que el operario nota
y para la que no tiene explicación.

Doce y no ocho porque, si un número tiene que servir para las dos cosas, conviene el que
no pierde nada: quedarse corto hace desaparecer del monitor algo sobre lo que aún se
podía actuar; quedarse largo solo ensucia la lista, y para eso está el filtro. Además,
si el turno dura ocho horas y el incidente puede caer en cualquier momento de él, solo
doce garantizan que lo vea el turno siguiente.

### Qué se ve en activos

| Estado | |
|---|---|
| `recibido`, `analizando` | Siempre: está en curso |
| `finalizado` | Hasta que venza el reloj o haya respuesta terminal |
| `fallido`, `interrumpido` | 12 h. `interrumpido` se muestra a propósito: si se escondiera, quien acaba de recibir la alerta no encontraría **nada**, que es la pantalla que miente de la que huimos |
| `pausado` | **Nunca.** No es información de un incidente sino del sistema entero: que alguien bajó el interruptor. Va en un **aviso fijo arriba de la pantalla** — «el análisis automático está desactivado; las alertas se registran pero no se analizan» —, que dice lo que importa sin llenar la lista de ruido |

---

## 4. La anotación posterior

Sobre un incidente *ya cerrado*. Cerrar y anotar son operaciones distintas: el
workflow no espera a nadie, y el rastro humano puede llegar dos horas o dos días
después sin mantener nada abierto.

Un incidente cerrado tiene **una etiqueta y nada más**. No hay campos que
rellenar ni nada que pedirle a nadie. Lo único que se puede hacer después es
**corregir esa etiqueta**.

### No hay disposición (decidido el 2026-09-15)

Una versión anterior de este documento pedía anotar la disposición: un
desplegable de *actuado (nº de OT) / sin acción / en seguimiento*, con autor y
fecha. **Se retira.**

Era el único punto del diseño donde guardábamos información sobre lo que pasa en
la **planta**, y no sobre lo que hizo nuestro sistema. Eso es territorio del
GMAO, y un campo que lo duplica acaba divergiendo de él — con la agravante de
que el que miente es el nuestro.

Lo que temíamos perder al quitarlo no se pierde. El argumento de §1 —que el
expediente nunca pueda terminar en *«lo dijo el sistema»*, sino en una decisión
humana— lo cubre ya el **veredicto**: confirmar o descartar una causa con su
evidencia es una decisión de una persona, registrada. Y los cierres distinguen
lo que hacía falta distinguir: *causa confirmada* (alguien concluyó), *visto sin
veredicto* (alguien miró y calló) y *desatendido* (no miró nadie).

Queda un hueco menor, asumido: *causa confirmada* dice que el diagnóstico era
bueno, no que se hiciera nada al respecto. Si nadie abre OT después, nuestro
registro parece un éxito y el problema sigue ahí. Pero decidir si se actúa es
planificación de mantenimiento, y perseguirlo aquí es exactamente la duplicación
que estamos evitando.

Con esto desaparecen también la segunda superficie («otra pantalla, en otro
sitio» para el responsable), su problema de acceso, y el campo de autor.

### Reclasificación

Esto **sí se mantiene**, y por un motivo distinto del que movía a la disposición:
no habla de la planta, habla de **nuestro** registro y de la calidad de lo que
produce el sistema. El GMAO no lo guarda mejor, no lo guarda en absoluto.

La anotación puede **cambiar el cierre**, y es lo que rescata los dos casos que
la pantalla en caliente no puede capturar bien:

- **A causa confirmada**, cuando alguien vuelve en frío y dice «la primera era
  correcta». Sin esto, el dato de acierto se pierde en el «visto sin veredicto».
- **A alerta no válida**, cuando el problema no existía y era el umbral de PI el que
  estaba mal. El modelo no se equivocó: le dieron un problema inventado. Es
  información que el equipo de PI necesita, y meterla dentro de «incorrecto»
  ensuciaría las métricas.

> **El riesgo de la alerta falsa es de ALCANCE, no de momento** (corregido el
> 2026-09-21). Todos los demás botones actúan sobre *una causa*; ese actúa sobre *el
> incidente entero*. Mezclar dos alcances en la misma zona es lo que hace que alguien
> con prisa pulse el que no era — y confundirlos cuesta caro: pierdes el bucle de
> feedback y además avisas de un umbral defectuoso que estaba bien.
>
> Una versión anterior lo resolvía limitándolo a la pestaña de cerrados, con el
> argumento de que juzgar un umbral mal configurado «no es una llamada del operario a
> las tres de la mañana». **Se retira.** Quien recibe la alerta y reconoce que salta
> siempre al arrancar la bomba de al lado es justo quien más contexto tiene sobre *ese*
> disparo; hacerle esperar doce horas significa que no lo marcará nunca, y que mientras
> tanto una alerta que no debió existir ocupa sitio en la pantalla de «lo que pide
> atención».
>
> El riesgo real lo resuelve **el sitio y no el momento**: bloque propio, al final del
> detalle, con su encabezado. Eso vale igual en las dos pestañas.

**Marcarla sustituye a la etiqueta de cierre** y es terminal. Manda sobre todo lo demás
—incluso sobre una causa confirmada o un análisis fallido—: si la alerta no debió
existir, da igual cómo fuera el análisis de un problema que no había.

**Y se puede quitar**, sin pedir motivo. Es el único cierre que se apoya en una
*opinión* y no en un hecho: los otros se deducen de si hay veredicto o de si venció el
reloj. Lo que descansa en una creencia es lo que más tiene que poder revisarse. Al
quitarla, el incidente vuelve exactamente a donde estaba —incluido volver a Activos si
allí estaba— porque la reclasificación no toca el reloj.

Las métricas usan siempre el valor anotado cuando existe.

Y como todo ocurre en el mismo sitio —el HMI de la sala de control—, la
reclasificación no cuesta ni una superficie nueva ni una decisión de acceso: es
la misma pantalla y el mismo puesto.

### Quién puede escribir, y por qué no hace falta identidad

La pantalla es alcanzable **solo desde el HMI de la sala de control**. En planta,
el firewall lo garantiza con una regla de dos IPs fijas: PI y ese puesto. (En las
máquinas de desarrollo el firewall está desactivado por política de la
plataforma; la premisa de §1 de `AI-GOVERNANCE.md` cubre ese entorno.)

De ahí se deduce lo demás. **Quien autentica es la cerradura de la sala**, no la
aplicación: el conjunto de personas que pueden escribir en un expediente es el
conjunto de las que han entrado ahí. Por eso el veredicto no lleva autor, y por
eso la Fase 2 no necesita login — que es una decisión, no una limitación
heredada de PI como las otras cuatro de `webhook.py`.

> ⚠️ **Las dos cosas están atadas y viven en sitios distintos.** Si algún día la
> pantalla se abre a más máquinas —una petición perfectamente razonable: *«¿puedo
> verla desde mi mesa?»*— el argumento se cae en silencio, sin que nada falle ni
> avise, y hay que reabrir si el veredicto necesita autor.

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

**Datos que sustentan cada causa** · ~~[recomendado]~~ **descartado el 2026-09-21**
La idea era enseñar qué variables miró el modelo y en qué ventana, para que quien
conoce la planta pudiera detectar que se apoyó en un sensor que sabe que está mal.

Se descarta por un motivo de PI, no de interfaz: **un mismo valor puede estar
referenciado desde varios elementos del AF que comparten PI Point**, y no hay forma de
obligar al modelo a citar «el original» en vez de una de las referencias. Lo que
enseñaría la pantalla no sería determinista y podría señalar un elemento que no es
donde el ingeniero espera mirar — con lo que la ayuda se convierte en confusión.

Para que esto valga la pena haría falta antes resolver la identidad de un valor en el
AF, que es un problema de modelado de PI y no de este workflow.

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

**Tiempo restante de la ventana** · [recomendado] — **aplazado a la Fase 3** (2026-09-22)
Que se sepa cuánto queda antes de que cierre solo, para no volver de planta y encontrárselo
cerrado sin aviso.

En la Fase 2 no se pone, y no es por coste: es que **al cerrarse todavía no se pierde nada**.
El veredicto no caduca y se puede dar igual desde cerrados, así que el reloj solo mueve de
pestaña. Una cuenta atrás hacia una consecuencia que no existe mete prisa sin motivo, que es
peor que no tenerla. En la Fase 3 vencer apaga el botón de reanalizar; entonces sí hay algo
que perder, y entonces se pone.

### Fuera de la pantalla en caliente

**Pestaña de cerrados** · [añadido]
Una **pestaña aparte**, no el mismo listado con el filtro de periodo corrido hacia
atrás. Es la superficie del reloj lento: el veredicto en frío y la reclasificación. En
el mismo HMI y sin disposición ninguna (§4), así que no necesita otra pantalla ni otra
decisión de acceso.

**Aviso de análisis desactivado** · [añadido]
Una línea fija arriba cuando el interruptor está bajado: *«el análisis automático está
desactivado; las alertas se registran pero no se analizan»*. Sustituye a listar los
incidentes en `pausado`, que es información del sistema y no de cada alerta.

---

## 5 bis. El esquema del fichero de incidente

Lo que la Fase 2 añade es **un bloque y nada más**:

```
revision:
  veredictos:      [ {en, iteracion, causa, veredicto, evidencia, sospecha} ]
  reclasificacion: null | "alerta_no_valida"
```

`veredicto` es `confirmada`, `descartada` o `pendiente` — este último para
corregir un clic mal dado.

> **`reclasificacion` tiene un solo valor, no dos** (corregido al implementar,
> 2026-09-15). El diseño preveía también `causa_confirmada`, para quien vuelve en
> frío y dice «la primera era la buena». Sobra: como el veredicto no caduca y el
> cierre se deduce, eso es **un veredicto normal dado más tarde**, y la etiqueta
> pasa a *causa confirmada* sola. Tener dos caminos para lo mismo es tener dos
> sitios que pueden discrepar.
>
> Lo que sí necesita campo propio es *alerta no válida*, porque no se puede
> expresar como veredicto sobre una causa: no dice que el modelo se equivocara,
> dice que le dieron un problema que no existía.

Cuatro decisiones dentro, todas con motivo:

**`revision` es hermano de `diagnostico`, no va dentro.** `diagnostico` es la salida del
modelo y lleva su etiqueta `_ai_generated`. Todo el proyecto cuida esa frontera —las
causas son del modelo y se etiquetan; los motivos de fallo son del código y no—, y este
es el sitio donde emborronarla saldría más caro.

**`veredictos` es de solo añadir.** Nunca se modifica ni se borra un elemento: corregir
es anotar encima. Es la forma natural de un registro auditable y, de paso, la más segura
entre dos escritores, porque añadir no pisa nada.

**Cada veredicto dice a qué iteración pertenece.** En la Fase 2 será siempre `1` y
parece un campo de adorno. No lo es: en la Fase 3 el reanálisis produce **causas
nuevas**, y si los veredictos apuntasen solo al índice, los de la primera tanda pasarían
a señalar causas que no son. Ponerlo ahora cuesta nada; añadirlo con expedientes
cerrados es una migración.

**El anclaje del reloj es un campo propio** (corregido el 2026-09-18). Se planteó un
`finalizado_en` sellado una sola vez, por miedo a que `actualizado_en` se moviera con cada
escritura y extendiera la ventana. Se descartó por lo contrario —con un reloj deslizante,
que cualquier movimiento lo reinicie es justo lo que se quiere— y se dejó `actualizado_en`
de anclaje.

**Estaba mal, y solo se vio usándolo.** `actualizado_en` lo pisa *cada* escritura, así que
el primer veredicto sobre un incidente antiguo lo ponía a «ahora» y lo devolvía a activos
como recién llegado — el mismo fallo que el anclaje venía a evitar, colado por la puerta
de atrás.

El anclaje es hoy `movimiento_workflow`, que escribe únicamente `mark()`: solo el workflow,
y solo cuando hace trabajo de verdad. Para los expedientes anteriores al campo el respaldo
es `recibido_en`, que se escribe una vez y no se vuelve a tocar. Sobre ese ancla se
recorren después los veredictos vigentes, con la regla del §3 bis.

No es estado derivado guardado —que es lo que se quitó al eliminar `finalizado_en`—: es el
hecho de cuándo trabajó el workflow por última vez, y no se deduce de ningún otro sitio.

Y lo que **no** lleva: ni disposición, ni nº de OT, ni autor, ni estado de cierre —que se
deduce—, ni lista de interacciones, que se cayó con la decisión de no detectar presencia.

### El segundo escritor

Hasta la Fase 2 el fichero tiene **un solo escritor**. La función que usa el workflow
para actualizarlo no cambia el campo que toca: recibe el incidente entero tal como lo
tiene cargado en memoria y **escribe ese objeto completo encima**, reemplazándolo todo.
Como editar un documento compartido bajándotelo, cambiando tu copia y subiéndola encima:
lo que otro tocó mientras tanto desaparece sin dejar rastro.

Hoy da igual. En la Fase 3, no:

| | |
|---|---|
| 15:00 | El operario descarta todas las causas y pulsa **reanalizar** |
| 15:00 | El workflow arranca y carga el incidente en memoria |
| 15:02 | El operario añade una evidencia más; la pantalla la escribe en el fichero |
| 15:04 | El workflow termina y escribe **su copia**, la de las 15:00 |
| | **La evidencia de las 15:02 ha desaparecido**, y nadie se entera |

Y no es un caso raro: en la Fase 3 el análisis lo dispara una persona que está delante
de la pantalla en ese momento.

**El arreglo no es acordarse, es que no se pueda.** La función deja de aceptar el
documento entero y solo puede decir «cambia estos campos»: relee el disco, cambia lo
suyo y escribe. Cada escritor es dueño de su parcela —el workflow de `estado`,
`diagnostico`, `trace` y `motivo`; la pantalla de `revision`— y ninguno puede pisar la
del otro aunque quiera.

Queda una carrera teórica entre releer y escribir. Con un puesto y un workflow es
despreciable, y lo peor que produce es perder un veredicto, no corromper el fichero. Si
algún día hace falta certeza, un contador de versión cuesta poco.

---

## 6. Lo que deliberadamente no va

Decisiones tomadas a propósito. Anotadas aquí para no tener que volver a discutirlas
dentro de tres meses.

- **Un chat abierto con el modelo.** Los campos son de propósito único: aportar
  evidencia sobre una causa concreta. No es una puerta para conversar, y acotarlo así
  acota el coste, el alcance y la superficie de «la IA decide cosas».

- **Un botón de «alerta falsa» con el alcance mezclado.** El riesgo es *dónde* está, no
  *cuándo* se ofrece: actúa sobre el incidente entero mientras los demás actúan sobre una
  causa, y juntarlos es lo que hace que alguien con prisa pulse el que no era. Por eso
  vive en un bloque propio al final del detalle, con su encabezado. Limitarlo **además**
  a la pestaña de cerrados se probó y se retiró el 2026-09-21; el porqué está en §4.

- **Nada sobre órdenes de trabajo**: ni el número, ni su contenido, ni su estado, ni
  avisos de seguimiento. Todo eso vive en el GMAO, que lo guarda mejor. Ver §4.

- **Identidad de quien escribe.** Autentica la cerradura de la sala de control, no la
  aplicación (§4). Añadir identidad convertiría los ficheros de incidente en datos
  personales, con lo que eso arrastra.

- **Detección de presencia ante la pantalla.** Ni ratón, ni desplazamiento, ni botón de
  «lo estoy mirando». No mide lo que hace falta medir. Ver §3.

- **Bloquear el cierre hasta que alguien anote.** El incidente cierra solo, por reloj o
  por respuesta terminal. Nadie tiene que ir a apagar nada.

---

## Dependencias para implementar

| # | Dependencia | Bloquea a | Estado |
|---|---|---|---|
| 1 | **Esquema del fichero** — ver §5 bis | Fase 2 | ✅ cerrado el 2026-09-15 |
| 2 | **Transporte de la pantalla** (sondeo), endpoints en la app FastAPI | Fase 1 | ✅ hecho el 2026-09-14 |
| 3 | **El segundo escritor** — ver §5 bis | Fase 2 | ✅ hecho el 2026-09-15 |
| 4 | **Re-entrada en `run_rca_analysis`** — hoy solo acepta el payload; hay que sacar el Step 4 a un punto re-entrable | Fase 3 | ⬜ |
| 5 | **Arreglo de `pausado`** | — | ✅ hecho el 2026-09-11 |

Fases, donde cada una sirve por sí sola:

1. **Pantalla de solo lectura.** ✅ Entregada el 2026-09-14.
2. **Captura del feedback**, guardado sin bucle. ✅ Entregada el 2026-09-22. Permite ver
   qué escribe la gente de verdad antes de construir encima. Queda fuera a propósito el
   **tiempo restante de la ventana** (§5): con el reloj moviendo solo de pestaña sería una
   cuenta atrás hacia nada. Entra con la Fase 3, que es cuando vencer apaga el reanálisis.
3. **La vuelta al modelo**, con las dos reglas: verificar en vez de razonar, y «no lo
   sé» como resultado de primera clase.

### Dónde corta la Fase 2

El **reanálisis con feedback no existe todavía** — es Fase 3. De ahí dos consecuencias
que conviene tener presentes al implementar:

- El reloj de 12 h **solo mueve de pestaña**. La acción que iba a gobernar aún no está.
- Si el operario **descarta todas las causas**, se cierra ya como *causa no determinada*.

> ⚠️ **Para la Fase 3.** Ese segundo punto cambia: descartarlas todas dejará de ser
> terminal y pasará a ofrecer el relanzado, y solo al agotarse el presupuesto de
> reanálisis se concluirá *causa no determinada*. Es decir, se **intercala** un paso
> antes de la conclusión actual. No olvidarlo al empezar la Fase 3.
>
> Y lo contrario también está decidido: **sobre un expediente cerrado no se ofrece
> reanálisis**, ni aunque se le aporte evidencia nueva en frío. El razonamiento está en
> §3 bis; la tentación de «ya que hay evidencia, deja reanalizar» reintroduciría el
> segundo reloj que se descartó.

La pestaña de **cerrados es una pestaña aparte**, no el mismo listado con el filtro de
periodo corrido hacia atrás.

---

*Redactado con asistencia de IA (claude-opus-5) a partir de la conversación de diseño
del 10 de septiembre de 2026.*

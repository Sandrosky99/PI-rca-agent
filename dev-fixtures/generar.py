"""Genera incidentes sinteticos para desarrollar la pantalla.

    python dev-fixtures/generar.py

Escribe en dev-fixtures/incidents/ un caso por cada situacion que la pantalla
tiene que saber pintar, con marcas de tiempo relativas al momento de ejecutarlo.
Para verlos:

    INCIDENTS_DIR=dev-fixtures/incidents WEBHOOK_PORT=8099 python serve.py
    -> http://localhost:8099/pantalla

Que cubre
---------
Las dos pestañas y todas sus etiquetas:

    ACTIVOS    en cola · procesando · pendiente de revisar · revisado a medias
               fallo del analisis · interrumpido
    CERRADOS   causa confirmada · causa no determinada · revisado parcialmente
               sin veredicto · fallo del analisis

Y las variantes que cambian como se pinta: umbral Low y High, esquema nuevo
(evidence) y viejo (explanation), descartes con sospecha y sin ella, y un
veredicto corregido -- que deja DOS apuntes en la lista de solo anadir, para
comprobar que el historial se conserva aunque en pantalla solo se vea el ultimo.

Por que un generador y no los ficheros sueltos
----------------------------------------------
1. Los ficheros no se versionan: la regla 'incidents/' del .gitignore los
   atrapa, y hace bien -- llevan nombres de activo y datos de planta. El
   generador si se versiona, asi que cualquiera puede rehacerlos.
2. Las marcas de tiempo envejecen. Con fechas fijas, a la semana la pantalla
   dice "hace 7 dias" en todo y no se ve como queda un caso reciente. Y desde
   que el reloj de 12 h decide la pestaña, es peor: con fechas fijas TODO
   acabaria en cerrados.
3. RECIBIDO y ANALIZANDO no sobreviven a un arranque: sweep_interrupted() los
   pasa a INTERRUMPIDO, y con razon -- para el, un incidente a medias cuando
   arranca el proceso es uno que murio. Para verlos en pantalla, ejecuta esto
   con el servidor YA levantado: los endpoints releen el directorio en cada
   peticion, asi que aparecen sin reiniciar nada.

AVISO: todo lo de aqui es inventado. Ni los diagnosticos los produjo el modelo
ni los veredictos los dio nadie: estan escritos a mano para que la pantalla
tenga algo con forma realista que mostrar. Cada fichero lleva un campo
'_fixture' que lo dice.
"""
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

DESTINO = Path(__file__).resolve().parent / "incidents"
AHORA = datetime.now(timezone.utc)

# El reloj que decide la pestaña (incidents.HORAS_EN_ACTIVOS). Se repite aqui en
# vez de importarlo para que el generador no arrastre la configuracion del
# proyecto y se pueda ejecutar en seco.
HORAS_EN_ACTIVOS = 12

AVISO = ("Dato sintetico para desarrollar la pantalla. Ni el diagnostico ni los veredictos "
         "son reales: estan escritos a mano. Regenerar con dev-fixtures/generar.py.")


def sello(minutos_atras: int) -> str:
    return (AHORA - timedelta(minutes=minutos_atras)).strftime("%Y-%m-%dT%H:%M:%SZ")


IA = {
    "provider": "anthropic",
    "model": "claude-opus-5",
    "notice": ("Hipotesis generada por un modelo de lenguaje a partir de datos de PI. "
               "NO es una causa raiz confirmada: requiere verificacion por un ingeniero "
               "de procesos antes de actuar."),
}


def causa(texto, evidencias, accion):
    return {"cause": texto, "evidence": evidencias, "recommended_action": accion}


# --- Diagnosticos, esquema NUEVO -------------------------------------------
DIAG_OBSTRUCCION = {"root_causes": [
    causa("Obstruccion del impulsor por alta carga de solidos",
          ["La eficiencia hidraulica cae del 54,1 % al 47,8 % en las ultimas seis horas.",
           "El caudal baja de 296,5 a 265,9 m3/h mientras la altura sube de 49,9 a 51,0 m: "
           "la bomba trabaja contra mas resistencia.",
           "La potencia activa del variador sube de 63,6 a 66,3 kW: mas consumo para menos caudal.",
           "Los solidos en suspension de la estacion pasan de ~318 a ~493 mg/L."],
          "Detener la bomba e inspeccionar visualmente impulsor, voluta y rejillas de aspiracion."),
    causa("Mayor resistencia en la linea de descarga",
          ["La altura manometrica sube de forma sostenida sin que aumente el caudal.",
           "El punto de trabajo se desplaza hacia la izquierda de la curva de la bomba."],
          "Revisar valvulas de descarga y comprobar obstrucciones aguas abajo."),
    causa("Desgaste de anillos e impulsor",
          ["La eficiencia lleva seis semanas a la baja, no solo en el episodio de la alarma.",
           "El consumo especifico sube de forma sostenida en el mismo periodo."],
          "Medir holguras de anillos de desgaste en la proxima parada programada."),
], "_ai_generated": IA}

DIAG_CONSUMO = {"root_causes": [
    causa("Funcionamiento prolongado fuera del punto de mejor rendimiento",
          ["El consumo especifico sube de 0,42 a 0,51 kWh/m3 en cuatro horas.",
           "El caudal se mantiene un 18 % por debajo del nominal durante todo el periodo."],
          "Revisar la consigna del variador y el reparto de carga entre bombas."),
    causa("Aire arrastrado en la aspiracion por nivel bajo en el pozo",
          ["El nivel del pozo baja hasta 1,2 m, por debajo del minimo recomendado de 1,5 m.",
           "La potencia activa oscila +-4 kW con periodo corto, tipico de bombeo con aire."],
          "Comprobar la consigna de parada por nivel bajo y el estado de la campana."),
], "_ai_generated": IA}

DIAG_VIBRACION = {"root_causes": [
    causa("Desequilibrio del conjunto rotante por acumulacion de trapos",
          ["La vibracion radial pasa de 2,8 a 7,4 mm/s RMS en dos horas.",
           "El armonico 1x de la velocidad de giro concentra el 80 % de la energia."],
          "Parar e inspeccionar el impulsor; retirar material enganchado."),
    causa("Cavitacion por NPSH insuficiente",
          ["La presion de aspiracion cae de 1,8 a 0,9 bar en el mismo periodo.",
           "Aparece energia de banda ancha por encima de 2 kHz en el acelerometro."],
          "Revisar nivel de pozo y perdida de carga en la aspiracion."),
    causa("Holgura excesiva en el cojinete lado accionamiento",
          ["La temperatura del cojinete sube 11 grados sin que cambie la carga."],
          "Programar termografia y analisis de aceite."),
], "_ai_generated": IA}

DIAG_CAUDAL = {"root_causes": [
    causa("Valvula de descarga parcialmente cerrada",
          ["El caudal cae a 212 m3/h manteniendo la altura y la potencia."],
          "Comprobar posicion y realimentacion de la valvula motorizada."),
    causa("Aire acumulado en el punto alto de la impulsion",
          ["El caudal oscila +-15 m3/h con periodo de unos 40 s."],
          "Purgar la ventosa del punto alto."),
], "_ai_generated": IA}

# --- Diagnostico con el esquema VIEJO ---------------------------------------
# Asi son todos los incidentes anteriores al 2026-09-14: un parrafo unico en
# 'explanation'. No se migran, asi que la pantalla tiene que seguir mostrandolos
# bien -- se pintan como parrafo y no como una vinieta gigante.
DIAG_VIEJO = {"root_causes": [
    {"cause": "Cavitacion en la aspiracion",
     "explanation": ("Los datos muestran una caida sostenida de la presion de aspiracion (de 1,8 a "
                     "0,9 bar) que coincide con un aumento del ruido medido en el acelerometro del "
                     "cojinete y con oscilaciones de la potencia activa. La presion disponible en la "
                     "aspiracion se aproxima a la NPSH requerida por la bomba a este caudal, lo que "
                     "favorece la formacion de burbujas de vapor que colapsan en el impulsor."),
     "recommended_action": "Revisar el nivel del pozo y el estado del filtro de aspiracion."},
    {"cause": "Perdida de carga en el filtro de aspiracion",
     "explanation": ("La diferencia de presion entre la entrada del filtro y la brida de aspiracion "
                     "ha aumentado de forma gradual durante las ultimas dos semanas, lo que sugiere "
                     "colmatacion progresiva del elemento filtrante."),
     "recommended_action": "Programar limpieza del filtro de aspiracion."},
], "_ai_generated": IA}


def veredicto(minutos_atras, indice, cual, evidencia="", sospecha=""):
    ap = {"en": sello(minutos_atras), "iteracion": 1, "causa": indice, "veredicto": cual}
    if evidencia:
        ap["evidencia"] = evidencia
    if sospecha:
        ap["sospecha"] = sospecha
    return ap


def incidente(corr, activo, subsistema, kpi, valor, limite, tipo, estado, hace_min,
              diagnostico=None, veredictos=None, motivo=None, intentos=None,
              movimiento_min=None):
    """Un incidente completo.

    'movimiento_min' es cuando fue el ultimo movimiento, que es lo que mira el
    reloj de 12 h para decidir la pestaña. Por defecto, el mismo instante en que
    llego; se da aparte cuando hubo un veredicto despues.
    """
    inicio = sello(hace_min)
    movimiento = sello(movimiento_min if movimiento_min is not None else hace_min)
    registro = {
        "id": f"{corr}__{inicio.replace('-', '').replace(':', '')}",
        "asset": activo,
        "kpi_name": kpi,
        "threshold_type": tipo,
        "start_time": inicio,
        "recibido_en": sello(hace_min),
        "actualizado_en": movimiento,
        # Lo que mira el reloj de las 12 h: cuando movio el WORKFLOW este
        # incidente. Se pone al mismo instante en que llego, porque el analisis
        # termina a los pocos minutos; los veredictos posteriores lo adelantan
        # solos al calcular el anclaje, sin tocar este campo.
        "movimiento_workflow": sello(hace_min),
        "estado": estado,
        "payload": {
            "KPIName": kpi, "Asset": activo, "Subsystem": subsistema,
            "System": "External Pumping", "Plant": "WWTP",
            "KPI": valor, "Limit": limite, "LimitThresholdType": tipo,
            "StartTime": inicio, "AssetType": "pump",
            "AssetModel": "single-channel centrifugal pump",
        },
        "diagnostico": diagnostico,
        "_fixture": AVISO,
    }
    if veredictos:
        registro["revision"] = {"veredictos": veredictos, "reclasificacion": None}
    if motivo:
        registro["motivo"] = motivo
    if intentos:
        registro["intentos"] = intentos
    return registro


VIEJO = (HORAS_EN_ACTIVOS + 6) * 60       # bastante mas alla del reloj -> cerrado
RECIENTE = 90                             # dentro del reloj -> activo

FIXTURES = [
    # ============================ ACTIVOS ============================

    # En cola: llego hace un minuto y espera turno.
    incidente("a01b02c03d04", "PS20103 A01 PS01 Pump 08", "Pumping Station 02",
              "Flow Rate", 212.0, 250.0, "Low", "recibido", 1),

    # Procesando: el estado que parpadea.
    incidente("b02c03d04e05", "PS20102 A03 PS02 Pump 07", "Pumping Station 01",
              "Hydraulic Efficiency", 43.1, 48.0, "Low", "analizando", 4),

    # Pendiente de revisar: hay diagnostico y nadie lo ha tocado.
    incidente("c03d04e05f06", "PS20102 A03 PS02 Pump 02", "Pumping Station 01",
              "Hydraulic Efficiency", 41.3, 48.0, "Low", "finalizado", 35,
              DIAG_OBSTRUCCION),

    # Umbral HIGH, para ver "por encima del limite" en la cabecera.
    incidente("d04e05f06a01", "PS20103 A01 PS01 Pump 03", "Pumping Station 02",
              "Specific Power Consumption", 0.51, 0.45, "High", "finalizado", 70,
              DIAG_CONSUMO),

    # Revisado a medias y todavia dentro del reloj: descarto una, se fue a mirar
    # la siguiente y aun puede volver. Con sospecha, que va en campo aparte.
    incidente("e05f06a01b02", "PS20104 B02 PS01 Pump 11", "Pumping Station 03",
              "Vibration RMS", 7.4, 4.5, "High", "finalizado", 240,
              DIAG_VIBRACION,
              veredictos=[veredicto(RECIENTE, 0, "descartada",
                                    "Parado e inspeccionado: el impulsor esta limpio.",
                                    "Yo miraria el cojinete, hace semanas que calienta.")],
              movimiento_min=RECIENTE),

    # Fallo reciente, con su motivo. Sigue en activos.
    incidente("f06a01b02c03", "PS20104 B02 PS01 Pump 03", "Pumping Station 03",
              "Hydraulic Efficiency", 40.2, 48.0, "Low", "fallido", 125,
              motivo="No se pudieron obtener los datos historicos de PI System "
                     "para las variables analizadas."),

    # Otro fallo con motivo distinto: los fallos no son todos iguales.
    incidente("0a1b2c3d4e5f", "PS20104 B02 PS01 Pump 06", "Pumping Station 03",
              "Hydraulic Efficiency", 42.8, 48.0, "Low", "fallido", 300,
              motivo="El modelo pidio variables que no existen. Conviene revisar la "
                     "sincronizacion entre PI System y la aplicacion."),

    # Interrumpido: el proceso murio a mitad. Va por el segundo intento.
    incidente("1b2c3d4e5f0a", "PS20103 A01 PS01 Pump 04", "Pumping Station 02",
              "Specific Power Consumption", 0.49, 0.45, "High", "interrumpido", 200,
              intentos=2),

    # Pausado: NO sale en ninguna pestaña. Su presencia es lo que tiene que
    # hacer aparecer el aviso de "el analisis automatico esta desactivado".
    incidente("2c3d4e5f0a1b", "PS20102 A03 PS02 Pump 09", "Pumping Station 01",
              "Hydraulic Efficiency", 45.0, 48.0, "Low", "pausado", 380),

    # ============================ CERRADOS ===========================

    # Causa confirmada: cierra AL MOMENTO, sin esperar al reloj. Por eso su
    # ultimo movimiento es reciente y aun asi aparece en cerrados.
    incidente("3d4e5f0a1b2c", "PS20102 A03 PS02 Pump 05", "Pumping Station 01",
              "Hydraulic Efficiency", 40.8, 48.0, "Low", "finalizado", 260,
              DIAG_OBSTRUCCION,
              veredictos=[
                  veredicto(200, 1, "descartada", "Valvulas de descarga abiertas y sin obstruccion."),
                  veredicto(180, 0, "confirmada"),
              ],
              movimiento_min=180),

    # Causa no determinada: se descartaron todas. Tambien cierra al momento.
    incidente("4e5f0a1b2c3d", "PS20103 A01 PS01 Pump 12", "Pumping Station 02",
              "Flow Rate", 198.0, 250.0, "Low", "finalizado", 420,
              DIAG_CAUDAL,
              veredictos=[
                  veredicto(360, 0, "descartada", "La valvula esta abierta al 100 %, comprobado in situ."),
                  veredicto(340, 1, "descartada", "Purgada la ventosa y el caudal no cambia.",
                            "Puede que el caudalimetro este mal calibrado."),
              ],
              movimiento_min=340),

    # Revisado parcialmente: descarto una y no volvio. Cierra POR RELOJ.
    incidente("5f0a1b2c3d4e", "PS20104 B02 PS01 Pump 02", "Pumping Station 03",
              "Vibration RMS", 6.9, 4.5, "High", "finalizado", VIEJO + 120,
              DIAG_VIBRACION,
              veredictos=[veredicto(VIEJO + 60, 1, "descartada",
                                    "Nivel de pozo correcto durante todo el episodio.")],
              movimiento_min=VIEJO + 60),

    # Un veredicto CORREGIDO: se descarto por error y se devolvio a pendiente.
    # En pantalla se ve pendiente; en el fichero quedan los DOS apuntes.
    incidente("6a1b2c3d4e5f", "PS20102 A03 PS02 Pump 14", "Pumping Station 01",
              "Motor Current", 41.2, 38.0, "High", "finalizado", VIEJO + 200,
              DIAG_CONSUMO,
              veredictos=[
                  veredicto(VIEJO + 150, 0, "descartada", "Me he equivocado de fila."),
                  veredicto(VIEJO + 140, 0, "pendiente"),
              ],
              movimiento_min=VIEJO + 140),

    # Sin veredicto: nadie dijo nada y vencio el reloj. Sera el caso mas
    # frecuente en operacion real. Ademas, con el esquema viejo.
    incidente("7b2c3d4e5f0a", "PS20104 B02 PS01 Pump 05", "Pumping Station 03",
              "Hydraulic Efficiency", 44.6, 48.0, "Low", "finalizado", VIEJO + 400,
              DIAG_VIEJO),

    # Fallo del analisis, ya cerrado por reloj.
    incidente("8c3d4e5f0a1b", "PS20103 A01 PS01 Pump 07", "Pumping Station 02",
              "Specific Power Consumption", 0.55, 0.45, "High", "fallido", VIEJO + 500,
              motivo="El modelo no respondio con un diagnostico."),
]


def _resumen(r, corte):
    """Replica la regla de incidents.cierre() para el listado de salida."""
    causas = (r["diagnostico"] or {}).get("root_causes", [])
    estados = ["pendiente"] * len(causas)
    for v in (r.get("revision") or {}).get("veredictos", []):
        estados[v["causa"]] = v["veredicto"]

    if r["estado"] == "pausado":
        return "-", "no sale; hace salir el aviso de parada"
    if "confirmada" in estados:
        return "cerrados", "causa confirmada"
    if estados and all(e == "descartada" for e in estados):
        return "cerrados", "causa no determinada"
    if r["actualizado_en"] < corte:
        if r["estado"] == "fallido":
            return "cerrados", "fallo del analisis"
        return "cerrados", "revisado parcialmente" if "descartada" in estados else "sin veredicto"
    if causas:
        return "activos", f"{len(causas)} causas" + (" · a medias" if "descartada" in estados else "")
    return "activos", (r.get("motivo", "")[:38] or r["estado"])


def main() -> None:
    if DESTINO.exists():
        shutil.rmtree(DESTINO)
    DESTINO.mkdir(parents=True)

    for registro in FIXTURES:
        (DESTINO / f"{registro['id']}.json").write_text(
            json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")

    corte = (AHORA - timedelta(hours=HORAS_EN_ACTIVOS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"{len(FIXTURES)} incidentes en {DESTINO}\n")
    print(f"  {'PESTAÑA':9} {'ESTADO':13} {'ACTIVO':28} {'KPI':27} COMO SE VERA")
    print("  " + "-" * 104)
    for r in FIXTURES:
        pestana, detalle = _resumen(r, corte)
        print(f"  {pestana:9} {r['estado']:13} {r['asset']:28} {r['kpi_name']:27} {detalle}")

    print("\nAviso: 'recibido' y 'analizando' pasaran a 'interrumpido' la proxima vez que")
    print("arranque el servidor. Para verlos, ejecuta esto con el servidor ya en marcha.")


if __name__ == "__main__":
    main()

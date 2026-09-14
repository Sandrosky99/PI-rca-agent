"""Genera incidentes sinteticos para desarrollar la pantalla.

    python dev-fixtures/generar.py

Escribe en dev-fixtures/incidents/ un incidente por cada estado posible, con
marcas de tiempo relativas al momento de ejecutarlo. Para verlos:

    INCIDENTS_DIR=dev-fixtures/incidents WEBHOOK_PORT=8099 python serve.py
    -> http://localhost:8099/pantalla

Por que un generador y no los ficheros sueltos
----------------------------------------------
1. Los ficheros no se versionan: la regla 'incidents/' del .gitignore los
   atrapa, y hace bien -- llevan nombres de activo y datos de planta. El
   generador si se versiona, asi que cualquiera puede rehacerlos.
2. Las marcas de tiempo envejecen. Con fechas fijas, a la semana la pantalla
   dice "empezo hace 7 dias" en todo y no se ve como queda un caso reciente.
3. RECIBIDO y ANALIZANDO no sobreviven a un arranque: sweep_interrupted() los
   pasa a INTERRUMPIDO, y con razon -- para el, un incidente a medias cuando
   arranca el proceso es uno que murio. Si quieres ver esos dos estados en
   pantalla, ejecuta esto con el servidor YA levantado: los endpoints releen el
   directorio en cada peticion, asi que aparecen sin reiniciar nada.

AVISO: todo lo de aqui es inventado. Los diagnosticos NO los produjo el modelo;
estan escritos a mano para que la pantalla tenga algo con forma realista que
mostrar. Cada fichero lleva un campo '_fixture' que lo dice.
"""
import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

DESTINO = Path(__file__).resolve().parent / "incidents"
AHORA = datetime.now(timezone.utc)


def sello(minutos_atras: int) -> str:
    return (AHORA - timedelta(minutes=minutos_atras)).strftime("%Y-%m-%dT%H:%M:%SZ")


AVISO = ("Dato sintetico para desarrollar la pantalla. El diagnostico, si lo hay, NO lo "
         "produjo el modelo: esta escrito a mano. Regenerar con dev-fixtures/generar.py.")


def incidente(corr, activo, subsistema, kpi, valor, limite, tipo, estado,
              hace_min, diagnostico=None, intentos=None, motivo=None):
    inicio = sello(hace_min)
    registro = {
        "id": f"{corr}__{inicio.replace('-', '').replace(':', '')}",
        "asset": activo,
        "kpi_name": kpi,
        "threshold_type": tipo,
        "start_time": inicio,
        "recibido_en": sello(hace_min - 1),
        "actualizado_en": sello(max(hace_min - 4, 0)),
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
    if intentos:
        registro["intentos"] = intentos
    if motivo:
        registro["motivo"] = motivo
    return registro


IA = {
    "provider": "anthropic",
    "model": "claude-opus-5",
    "notice": ("Hipotesis generada por un modelo de lenguaje a partir de datos de PI. "
               "NO es una causa raiz confirmada: requiere verificacion por un ingeniero "
               "de procesos antes de actuar."),
}

# --- Esquema NUEVO: causa en una frase + evidencias sueltas -------------------
DIAG_TRES = {
    "root_causes": [
        {"cause": "Obstruccion del impulsor por alta carga de solidos",
         "evidence": [
             "La eficiencia hidraulica cae del 54,1 % al 47,8 % en las ultimas seis horas.",
             "El caudal baja de 296,5 a 265,9 m3/h mientras la altura sube de 49,9 a 51,0 m: "
             "la bomba trabaja contra mas resistencia.",
             "La potencia activa del variador sube de 63,6 a 66,3 kW: mas consumo para menos caudal.",
             "Los solidos en suspension de la estacion pasan de ~318 a ~493 mg/L."],
         "recommended_action": ("Detener la bomba e inspeccionar visualmente impulsor, voluta y "
                                "rejillas de aspiracion. Limpiar acumulaciones de solidos o trapos.")},
        {"cause": "Mayor resistencia en la linea de descarga",
         "evidence": [
             "La altura manometrica sube de forma sostenida sin que aumente el caudal.",
             "El punto de trabajo se desplaza hacia la izquierda de la curva de la bomba."],
         "recommended_action": "Revisar valvulas de descarga y comprobar obstrucciones aguas abajo."},
        {"cause": "Desgaste de anillos e impulsor",
         "evidence": [
             "La eficiencia lleva seis semanas a la baja, no solo en el episodio de la alarma.",
             "El consumo especifico sube de forma sostenida en el mismo periodo."],
         "recommended_action": "Medir holguras de anillos de desgaste en la proxima parada programada."},
    ],
    "_ai_generated": IA,
}

DIAG_DOS = {
    "root_causes": [
        {"cause": "Funcionamiento prolongado fuera del punto de mejor rendimiento",
         "evidence": [
             "El consumo especifico sube de 0,42 a 0,51 kWh/m3 en cuatro horas.",
             "El caudal se mantiene un 18 % por debajo del nominal durante todo el periodo."],
         "recommended_action": "Revisar la consigna del variador y el reparto de carga entre bombas."},
        {"cause": "Aire arrastrado en la aspiracion por nivel bajo en el pozo",
         "evidence": [
             "El nivel del pozo baja hasta 1,2 m, por debajo del minimo recomendado de 1,5 m.",
             "La potencia activa oscila +-4 kW con periodo corto, tipico de bombeo con aire."],
         "recommended_action": "Comprobar la consigna de parada por nivel bajo y el estado de la campana."},
    ],
    "_ai_generated": IA,
}

# --- Esquema VIEJO: un parrafo unico en 'explanation' ------------------------
# Asi son todos los incidentes anteriores al 2026-09-14. No se migran, asi que
# la pantalla tiene que seguir mostrandolos bien -- y esto sirve para verlo.
DIAG_VIEJO = {
    "root_causes": [
        {"cause": "Cavitacion en la aspiracion",
         "explanation": ("Los datos muestran una caida sostenida de la presion de aspiracion (de 1,8 a "
                         "0,9 bar) que coincide con un aumento del ruido medido en el acelerometro del "
                         "cojinete y con oscilaciones de la potencia activa. La presion disponible en la "
                         "aspiracion se aproxima a la NPSH requerida por la bomba a este caudal, lo que "
                         "favorece la formacion de burbujas de vapor que colapsan en el impulsor. Esto "
                         "explica tanto la perdida de rendimiento como el incremento de vibracion."),
         "recommended_action": "Revisar el nivel del pozo y el estado del filtro de aspiracion."},
        {"cause": "Perdida de carga en el filtro de aspiracion",
         "explanation": ("La diferencia de presion entre la entrada del filtro y la brida de aspiracion "
                         "ha aumentado de forma gradual durante las ultimas dos semanas, lo que sugiere "
                         "colmatacion progresiva del elemento filtrante."),
         "recommended_action": "Programar limpieza del filtro de aspiracion."},
    ],
    "_ai_generated": IA,
}

FIXTURES = [
    # estado FINALIZADO, esquema nuevo: el caso completo y reciente
    incidente("a1b2c3d4e5f6", "PS20102 A03 PS02 Pump 02", "Pumping Station 01",
              "Hydraulic Efficiency", 41.3, 48.0, "Low", "finalizado", 25, DIAG_TRES),
    # estado FINALIZADO, umbral High: para ver "por encima del limite"
    incidente("b2c3d4e5f6a1", "PS20103 A01 PS01 Pump 03", "Pumping Station 02",
              "Specific Power Consumption", 0.51, 0.45, "High", "finalizado", 70, DIAG_DOS),
    # estado FINALIZADO, esquema viejo: comprobar que sigue viendose bien
    incidente("c3d4e5f6a1b2", "PS20104 B02 PS01 Pump 05", "Pumping Station 03",
              "Hydraulic Efficiency", 44.6, 48.0, "Low", "finalizado", 190, DIAG_VIEJO),
    # estado ANALIZANDO: el estado que parpadea
    incidente("d4e5f6a1b2c3", "PS20102 A03 PS02 Pump 07", "Pumping Station 01",
              "Hydraulic Efficiency", 43.1, 48.0, "Low", "analizando", 4),
    # estado RECIBIDO: en cola detras del anterior
    incidente("e5f6a1b2c3d4", "PS20103 A01 PS01 Pump 08", "Pumping Station 02",
              "Flow Rate", 212.0, 250.0, "Low", "recibido", 2),
    # estado FALLIDO: el analisis se ejecuto y se corto. El 'motivo' es lo que
    # separa un fallo util de uno que obliga a abrir el log del servicio.
    incidente("f6a1b2c3d4e5", "PS20104 B02 PS01 Pump 03", "Pumping Station 03",
              "Hydraulic Efficiency", 40.2, 48.0, "Low", "fallido", 125,
              motivo=("No se pudieron obtener los datos historicos de PI System "
                      "para las variables analizadas.")),
    # otro FALLIDO, con un motivo distinto: los fallos no son todos iguales y
    # la pantalla tiene que poder distinguirlos.
    incidente("2c3d4e5f0a1b", "PS20104 B02 PS01 Pump 06", "Pumping Station 03",
              "Hydraulic Efficiency", 42.8, 48.0, "Low", "fallido", 240,
              motivo=("El modelo pidio variables que no existen. Conviene revisar la "
                      "sincronizacion entre PI System y la aplicacion.")),
    # estado PAUSADO: llego con el interruptor de parada echado
    incidente("0a1b2c3d4e5f", "PS20102 A03 PS02 Pump 09", "Pumping Station 01",
              "Hydraulic Efficiency", 45.0, 48.0, "Low", "pausado", 360),
    # estado INTERRUMPIDO: el proceso murio a mitad; va por el segundo intento
    incidente("1b2c3d4e5f0a", "PS20103 A01 PS01 Pump 04", "Pumping Station 02",
              "Specific Power Consumption", 0.49, 0.45, "High", "interrumpido", 300,
              intentos=2),
]


def main() -> None:
    if DESTINO.exists():
        shutil.rmtree(DESTINO)
    DESTINO.mkdir(parents=True)

    for registro in FIXTURES:
        (DESTINO / f"{registro['id']}.json").write_text(
            json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"{len(FIXTURES)} incidentes en {DESTINO}\n")
    for r in FIXTURES:
        causas = len((r["diagnostico"] or {}).get("root_causes", []))
        esquema = ""
        if causas:
            primera = r["diagnostico"]["root_causes"][0]
            esquema = " (esquema nuevo)" if "evidence" in primera else " (esquema viejo)"
        print(f"  {r['estado']:13} {r['asset']:27} {r['kpi_name']:27}"
              f" {str(causas) + ' causas' if causas else '-':>9}{esquema}")

    print("\nAviso: 'recibido' y 'analizando' pasaran a 'interrumpido' la proxima vez")
    print("que arranque el servidor. Para verlos, ejecuta esto con el servidor ya en")
    print("marcha: los endpoints releen el directorio en cada peticion.")


if __name__ == "__main__":
    main()

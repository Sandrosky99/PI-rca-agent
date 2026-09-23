"""Pruebas de lo que el Step 6 sabe del EQUIPO, no solo de sus medidas.

Sale de revisar a mano el mensaje del Step 3 (2026-09-23): faltaban los
metadatos de la bomba. Al tirar del hilo aparecieron dos cosas, y la segunda es
peor que la primera.

1. El Step 6 conocia el equipo solo por AssetType/AssetModel del PAYLOAD, y PI
   no siempre los manda. El payload real del 2026-09-08 no los traia, y el
   resumen se quedaba en "una desviacion en el KPI Hydraulic Efficiency de
   PS20102 A03 PS02 Pump 02" -- sin decir en ningun sitio que es una bomba
   centrifuga MONOCANAL, que es justo lo que hace plausible un atascamiento por
   trapos. El AF si lo sabe.

2. Y lo gordo: cuando una serie venia entera con el timestamp de la epoca --
   atributo estatico, sin historizacion -- el codigo DESCARTABA su valor y solo
   decia "sin historizacion real". Eso se llevaba por delante el atributo
   'Reference' de cada indicador, que es el baseline del activo y que el propio
   _OBJECTIVE_SECTION pide como PRIORIDAD 1 ("para saber si la desviacion es
   real frente a su comportamiento habitual").

   Medido contra PI el 2026-09-23, sobre la bomba de la alerta real:

       PI devuelve   : Reference -> 97 puntos, todos 49.8
       el Step 6 veia: "sin historizacion real (atributo estatico...)"

   Se le pedia el dato al modelo, se pagaba la consulta a PI y se borraba antes
   de enseñarselo.

No hace falta PI ni el AF para correrlas: se usan las respuestas reales que
devolvieron, copiadas aqui.
"""
import io
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

config.INCIDENTS_DIR = str(Path(__file__).parent / "_no_usado")
import graph_client
import workflow

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


EPOCA = "1970-01-01T00:00:00Z"


def serie(valor, uom=None, n=97, ts=EPOCA):
    it = {"Timestamp": ts, "Value": valor}
    if uom:
        it["UnitsAbbreviation"] = uom
    return {"Content": {"Items": [dict(it) for _ in range(n)]}}


print("\n=== 1. Un atributo estatico ENSEÑA su valor ===")
# El caso real: el baseline de Head, que PI devuelve como 49.8 repetido.
datos = {"p": serie(49.8, "m")}
e = workflow._label_historical_data([{"element": "Head", "piApiPath": "p"}], datos)[0]
check("trae el valor", e.get("valor") == 49.8, e)
check("con su unidad", e.get("uom") == "m", e)
check("y dice que es un valor fijo, no una serie",
      "valor fijo" in e.get("nota", ""), e.get("nota"))
check("ya no se pierde el dato", "sin historizacion real" not in str(e), e)

print("\n=== 2. Tambien si el valor es texto ===")
# Asset Model. Si esto no funcionara, la ficha del equipo llegaria vacia.
datos = {"p": serie("single-channel centrifugal pump")}
e = workflow._label_historical_data([{"element": "Pump 02", "piApiPath": "p"}], datos)[0]
check("trae el modelo del equipo",
      e.get("valor") == "single-channel centrifugal pump", e)

print("\n=== 3. Un estado digital se simplifica a su nombre ===")
# PI los devuelve como {"Name":..., "Value": <codigo>, "IsSystem": true}. El
# codigo numerico no le dice nada al modelo; el nombre si.
datos = {"p": serie({"Name": "Pt Created", "Value": 253, "IsSystem": True})}
e = workflow._label_historical_data([{"element": "X", "piApiPath": "p"}], datos)[0]
check("se queda con el nombre", e.get("valor") == "Pt Created", e)

print("\n=== 4. Si el 'valor fijo' no es fijo, se enseñan todos ===")
# No deberia pasar. Si pasa, que cambie es informacion, no ruido: quedarse con
# el primero esconderia que el atributo se movio.
datos = {"p": {"Content": {"Items": [
    {"Timestamp": EPOCA, "Value": 49.8},
    {"Timestamp": EPOCA, "Value": 51.2},
]}}}
e = workflow._label_historical_data([{"element": "Head", "piApiPath": "p"}], datos)[0]
check("los conserva los dos", e.get("valor") == [49.8, 51.2], e)

print("\n=== 5. Una serie de verdad sigue siendo una serie ===")
# La rama de siempre no puede haberse roto al arreglar la otra.
datos = {"p": serie(58.4, "%", n=3, ts="2026-08-19T21:00:00Z")}
e = workflow._label_historical_data([{"element": "Flow", "piApiPath": "p"}], datos)[0]
check("trae la lista de puntos", isinstance(e.get("values"), list), e)
check("con sus tres valores", len(e["values"]) == 3, e)
check("y no la marca como valor fijo", "valor" not in e, e)

print("\n=== 6. Que atributos forman la ficha del equipo ===")
# Son los que dicen QUE MAQUINA ES. El resto de metadatos -- Area Code, Plant
# Code, Path, PI Data Archive -- siguen descartados: no aportan al diagnostico.
check("incluye el modelo y el tipo",
      {"Asset Model", "Asset Type"} <= set(graph_client._IDENTIDAD_ATTRS),
      graph_client._IDENTIDAD_ATTRS)
check("y el fabricante y el numero de serie",
      {"Manufacturer", "Serial Number"} <= set(graph_client._IDENTIDAD_ATTRS))
check("pero NO los codigos de bookkeeping",
      not ({"Area Code", "Plant Code", "Path", "PI Data Archive"}
           & set(graph_client._IDENTIDAD_ATTRS)))
# Siguen fuera del menu del Step 4: no son magnitudes que analizar, y ofrecerlas
# solo gastaria plazas del tope MAX_SELECTED_VARIABLES.
check("siguen filtrados del contexto que elige el modelo",
      set(graph_client._IDENTIDAD_ATTRS) <= graph_client._METADATA_ATTRS,
      set(graph_client._IDENTIDAD_ATTRS) - graph_client._METADATA_ATTRS)

print("\n=== 7. La ficha entra en el contexto del Step 3, no la elige el modelo ===")
# Se cambio el 2026-09-23: al principio se añadia a las variables del Step 5,
# asi que el modelo elegia SIN saber ante que maquina estaba. No se eligen las
# mismas variables para una bomba centrifuga monocanal -- donde el atascamiento
# por trapos es plausible y conviene mirar la aspiracion -- que para una de
# varios canales.
fuente = (RAIZ / "workflow.py").read_text(encoding="utf-8")
orden = fuente.index("ficha = await ficha_del_equipo")
check("se pide antes de construir el mensaje del Step 3",
      orden < fuente.index("context = build_analysis_context(notification_payload"))

import asyncio
import pi_client

AF = {"asset_identity": [{"name": "Asset Model", "piApiPath": "ruta"}]}
PAYLOAD = {"Asset": "A", "KPIName": "K", "StartTime": "2026-08-19T22:10:00Z"}

async def _pi_ok(variables, detected_at_utc, lookback_hours=None, interval=None):
    return {"data": {"ruta": serie("single-channel centrifugal pump")}}

async def _pi_roto(variables, detected_at_utc, lookback_hours=None, interval=None):
    raise RuntimeError("PI no responde")

pi_client.fetch_historical_data = _pi_ok
ficha = asyncio.run(workflow.ficha_del_equipo(PAYLOAD, AF))
check("trae el modelo desde PI",
      ficha == {"Asset Model": "single-channel centrifugal pump"}, ficha)

# NO puede tumbar el analisis: es contexto que mejora la seleccion, no un
# requisito. Antes del 2026-09-23 no existia y el workflow funcionaba igual.
pi_client.fetch_historical_data = _pi_roto
check("si PI no responde, devuelve vacio y no revienta",
      asyncio.run(workflow.ficha_del_equipo(PAYLOAD, AF)) == {})
check("sin identidad en el AF, tampoco consulta",
      asyncio.run(workflow.ficha_del_equipo(PAYLOAD, {})) == {})

print("\n=== 7 bis. Y se menciona en el apartado 3, no como elegible ===")
prompt = workflow.build_analysis_context(
    PAYLOAD, {"main_asset_context": [], "nearby_elements_context": [], "plant_context": []},
    {"Asset Model": "single-channel centrifugal pump"})["claude_prompt"]
check("la ficha aparece en el mensaje", "single-channel centrifugal pump" in prompt)
# Sin esto el modelo la buscaria entre los piApiPath y no la encontraria.
check("y dice que NO esta en las listas",
      "NO aparecen en las listas" in prompt)
check("y que no la incluya en su respuesta",
      "no los busques ahi" in prompt)

print("\n=== 8. La ficha NO se le enseña al modelo en el Step 3 ===")
# Se colo el 2026-09-23: 'asset_identity' se anadio al af_context y el prompt
# volcaba el diccionario ENTERO, asi que aparecia en el mensaje con sus
# piApiPath a la vista -- pero fuera de la lista blanca. Se le ofrecian tres
# rutas que la puerta del Step 5 rechazaba luego como inventadas.
af = {"main_asset_context": [], "nearby_elements_context": [], "plant_context": [],
      "asset_identity": [{"name": "Asset Model", "piApiPath": "RUTA_DE_LA_FICHA"}]}
prompt = workflow.build_analysis_context(
    {"KPIName": "K", "Asset": "A", "StartTime": "2026-08-19T22:10:00Z"}, af)["claude_prompt"]
check("no aparece la clave", "asset_identity" not in prompt)
check("ni sus rutas", "RUTA_DE_LA_FICHA" not in prompt)

print("\n=== 9. El prompt y la lista blanca salen del MISMO sitio ===")
# Es el arreglo de fondo: mientras sean la misma constante no pueden discrepar,
# y una clave nueva en el af_context no se cuela sola en el mensaje.
check("existe la constante", hasattr(workflow, "_BLOQUES_PARA_EL_MODELO"))
check("son los tres bloques",
      workflow._BLOQUES_PARA_EL_MODELO
      == ("main_asset_context", "nearby_elements_context", "plant_context"),
      workflow._BLOQUES_PARA_EL_MODELO)
check("la usa la lista blanca", "for block in _BLOQUES_PARA_EL_MODELO:" in fuente)
check("y la usa el prompt", "for b in _BLOQUES_PARA_EL_MODELO" in fuente)

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

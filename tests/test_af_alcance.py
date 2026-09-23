"""Pruebas del alcance del Step 2: a que trozo del AF se le permite mirar.

Lo que identifica un elemento en el AF NO es su nombre: es la cadena
System + Subsystem + Asset. Cualquiera de los tres por separado se repite --
PI puede mandar perfectamente dos alertas con Subsystem="Biological" bajo
Systems distintos --, pero los tres juntos no.

El grafo es ademas un entorno COMPARTIDO. Medido contra el real el 2026-09-22:
8.404 elementos en 26 raices (una cementera, Green H2, CPG, Pharma Lab, un
conector de Wonderware, arboles de navegacion...), de las que WWTP es una, con
357 elementos. 47 nombres del WWTP existen tambien en otra raiz y 45 se repiten
dentro del propio WWTP.

El choque mas peligroso no es entre plantas distintas sino un ESPEJO de esta
misma, a traves del conector de Wonderware:

    WWTP\\Operational\\1 - Intake\\Line 1\\PS01102                 <- el modelo del AF
    WonderwareHistorian Connector\\...\\WWTP_Demo\\...\\PS01102     <- la misma bomba

Mismos nombres de equipo, atributos distintos. Y ojo con como se acota: como
path_contains es una SUBCADENA, "WWTP" a secas casa tambien con "WWTP_Demo";
hay que anclar con el separador.

El fallo que todo esto produce es del peor tipo -- no revienta nada: devuelve
un af_context perfectamente formado con las variables del modelo equivocado, y
el diagnostico sale plausible.

Lo que se comprueba aqui:
  1. Que se usa la CADENA entera, no dos de los tres eslabones.
  2. Que el subsistema sale del path del activo y no de una busqueda por nombre.
  3. Que el techo de planta existe y esta ANCLADO.
  4. Que un payload incompleto NO deja el analisis sin contexto: eso seria peor
     que la ambiguedad.

Con un doble del servidor MCP, para que corra sin Neo4j ni PI.
"""
import asyncio
import io
import json
import logging
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
import config

config.INCIDENTS_DIR = str(Path(__file__).parent / "_no_usado")
import graph_client

fallos = []


def check(nombre, cond, detalle=""):
    print(f"  [{'PASA' if cond else 'FALLA'}] {nombre}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        fallos.append(nombre)


SEP = chr(92)


def ruta(*segmentos):
    return SEP.join(segmentos)


# El trozo del grafo real que hace falta. Todos los casos salen del AF real,
# leidos el 2026-09-22, salvo los dos "Blower 01", que son el caso que hay que
# poder cubrir aunque hoy no exista.
GRAFO = [
    # La alerta real del 2026-08-20.
    ruta("WWTP", "Operational", "External Pumping", "Area C", "Pumping Station 01",
         "PS20102 A03 PS02 Pump 02"),
    # La misma bomba en dos modelos: el del AF y el espejo de Wonderware.
    ruta("WWTP", "Operational", "1 - Intake", "Line 1", "PS01102"),
    ruta("WonderwareHistorian Connector", "DBSERVER", "izGPLdemo", "WWTP_Demo",
         "Process", "Intake_pumping", "Pumping_line1", "PS01102"),
    # Tres "Biological", dos de ellos dentro del propio WWTP.
    ruta("WWTP", "Consumers", "3 - Secondary", "Biological"),
    ruta("WWTP", "Meters", "Electrical Energy", "iCGBT", "iMCC2", "Biological"),
    ruta("zzz_$NBHOME$_", "01. Water Treatment Plant", "Operation", "Biological"),
    # Mismo nombre de activo bajo dos subsistemas del MISMO System: solo la
    # cadena entera lo resuelve.
    ruta("WWTP", "Operational", "3 - Secondary", "Reactor A", "Blower 01"),
    ruta("WWTP", "Operational", "3 - Secondary", "Reactor B", "Blower 01"),
]

pedidos = []          # (nombre, path_contains) de cada llamada al servidor MCP


class SesionFalsa:
    """Imita graph_search: subcadena sobre el nombre, subcadena sobre el path.

    Imitar la SUBCADENA importa: si el doble comparase segmentos, "WWTP" no
    casaria con "WWTP_Demo" y la prueba del espejo pasaria por el motivo
    equivocado.
    """

    async def call_tool(self, _tool, args):
        nombre, filtro = args["name_contains"], args.get("path_contains")
        pedidos.append((nombre, filtro))
        res = [{"name": p.split(SEP)[-1], "path": p} for p in GRAFO
               if nombre in p.split(SEP)[-1] and (not filtro or filtro in p)]
        cuerpo = json.dumps({"count": len(res), "results": res})

        class C:
            text = cuerpo

        class R:
            content = [C()]
        return R()


def resolver(asset, subsystem, system):
    pedidos.clear()
    return asyncio.run(graph_client._resolver_cadena(SesionFalsa(), asset, subsystem, system))


print("\n=== 1. La cadena entera resuelve la alerta real ===")
act, sub = resolver("PS20102 A03 PS02 Pump 02", "Pumping Station 01", "External Pumping")
check("el activo sale con su path completo",
      act == ruta("WWTP", "Operational", "External Pumping", "Area C",
                  "Pumping Station 01", "PS20102 A03 PS02 Pump 02"), act)
check("y el subsistema se recorta de ese mismo path",
      sub == ruta("WWTP", "Operational", "External Pumping", "Area C", "Pumping Station 01"), sub)

print("\n=== 2. El subsistema NO se busca por su nombre ===")
# Es el arreglo del primer intento (2026-09-22), que resolvia el subsistema
# primero por su nombre y apoyaba toda la desambiguacion en dos de los tres
# eslabones. Como ahora se recorta del path del activo, es por construccion el
# que contiene a ESE activo y no otro que se llame igual.
check("solo se pregunta una vez al AF", len(pedidos) == 1, pedidos)
check("y se pregunta por el ACTIVO", pedidos[0][0] == "PS20102 A03 PS02 Pump 02", pedidos)

print("\n=== 3. Mismo activo, mismo System, distinto Subsystem ===")
# "Blower 01" existe bajo Reactor A y Reactor B, las dos dentro de
# "3 - Secondary". Ni el nombre ni el System bastan: hace falta el Subsystem.
act, _ = resolver("Blower 01", "Reactor A", "3 - Secondary")
check("con Subsystem='Reactor A' sale el de A", act.endswith(ruta("Reactor A", "Blower 01")), act)
act, _ = resolver("Blower 01", "Reactor B", "3 - Secondary")
check("con Subsystem='Reactor B' sale el de B", act.endswith(ruta("Reactor B", "Blower 01")), act)

print("\n=== 4. El espejo de la propia planta ===")
act, _ = resolver("PS01102", "Line 1", "1 - Intake")
check("sale el modelo del AF", act.startswith(ruta("WWTP", "Operational")), act)
check("y nunca el espejo de Wonderware", "WWTP_Demo" not in act, act)
check("el filtro que se mando iba anclado con el separador",
      pedidos[0][1] == "WWTP" + SEP, pedidos)

print("\n=== 5. Se exige SEGMENTO, no subcadena ===")
# Si se compararan subcadenas, un Subsystem llamado "Line 1" casaria con
# "Line 10", "Line 11"... El path se parte por el separador y se compara entero.
check("el troceado ignora los vacios",
      graph_client._segmentos(ruta("WWTP", "", "Operational")) == ["WWTP", "Operational"])
check("'Line 1' no es segmento de un path con 'Line 10'",
      "Line 1" not in graph_client._segmentos(ruta("WWTP", "Line 10", "X")))

print("\n=== 6. Un payload incompleto NO deja el analisis sin contexto ===")
# Un System mal configurado en PI no puede costar el analisis entero: se afloja
# un eslabon y se sigue. Quedarse sin contexto es peor que la ambiguedad.
act, sub = resolver("PS20102 A03 PS02 Pump 02", "Pumping Station 01", "System Inventado")
check("con el System equivocado, sigue encontrando el activo", act is not None, act)
check("y el subsistema tambien", sub is not None, sub)
act, _ = resolver("PS20102 A03 PS02 Pump 02", "", "")
check("sin System ni Subsystem, tambien", act is not None, act)

print("\n=== 7. Si el activo no existe, se dice, no se inventa ===")
act, sub = resolver("Bomba Que No Existe", "Pumping Station 01", "External Pumping")
check("devuelve None", act is None and sub is None, (act, sub))

print("\n=== 8. Si queda ambiguo, se avisa en vez de callarlo ===")
# Que sobren candidatos significa que la cadena no basto, y eso es informacion
# sobre el modelo de PI: hay que poder verlo en el log.
avisos = []


class Espia(logging.Handler):
    def emit(self, record):
        if record.levelno >= logging.WARNING:
            avisos.append(record.getMessage())


graph_client.log.addHandler(Espia())
resolver("Blower 01", "Subsystem Inventado", "3 - Secondary")
check("avisa de que sigue habiendo varios candidatos",
      any("ambiguo" in a for a in avisos), avisos)

print("\n=== 9. El techo de planta es configurable ===")
check("existe AF_PLANT_ROOT", hasattr(config, "AF_PLANT_ROOT"))
check("por defecto es WWTP", config.AF_PLANT_ROOT == "WWTP", config.AF_PLANT_ROOT)
check("existe la lista de contexto de planta", hasattr(config, "AF_PLANT_CONTEXT_ELEMENTS"))
check("y esta vacia mientras nadie la rellene",
      config.AF_PLANT_CONTEXT_ELEMENTS == [], config.AF_PLANT_CONTEXT_ELEMENTS)

print("\n=== 10. El codigo real construye el filtro ANCLADO ===")
# Si alguien quitara el separador, todo lo demas seguiria en verde y el espejo
# volveria a colarse. Por eso se comprueba como se construye, no solo el efecto.
fuente = (RAIZ / "graph_client.py").read_text(encoding="utf-8")
check("existe el separador como constante", "SEPARADOR_AF = chr(92)" in fuente)
check("y el techo de planta lo lleva pegado",
      fuente.count("config.AF_PLANT_ROOT + SEPARADOR_AF") >= 1,
      fuente.count("config.AF_PLANT_ROOT + SEPARADOR_AF"))

print("\n=== 11. build_af_context acepta el System ===")
# Sin esto el workflow no tendria como pasarlo y la cadena se quedaria coja.
import inspect

firma = inspect.signature(graph_client.build_af_context).parameters
check("la firma lo lleva", "system_name" in firma, list(firma))
check("y es opcional, para no romper a quien no lo pase",
      firma["system_name"].default == "", firma["system_name"].default)

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

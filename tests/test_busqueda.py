"""Pruebas de la funcion envuelta que se le ofrece al modelo.

Es la UNICA herramienta que el modelo recibe en todo el proyecto, asi que lo que
se prueba aqui no es que encuentre cosas: es que NO PUEDA salirse.

No se le pasa el servidor MCP, se le pasa esto. La diferencia importa: entre las
tools de afkg-graph-mcp esta refresh_graph, que es de ESCRITURA -- su ultima
pasada real inserto 1.087 nodos, actualizo 1.280 y borro 1.457 sobre un Neo4j
que comparten las demas demos.

Con un doble del servidor. Los nodos son reales, leidos del AF el 2026-09-28.
"""
import asyncio
import io
import json
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


def ruta(*seg):
    return SEP.join(seg)


PREFIJO = SEP * 2 + "DATAINFRA" + SEP + "BBDD_DEMO" + SEP

# Nodos reales del AF.
ELEMENTOS = [
    ruta("WWTP", "Operational", "2 - Pretreatment and primary", "Line 1", "PH213 Primary Line 1 Outlet pH"),
    ruta("WWTP", "Operational", "7 - Discharge Water", "PH705 Discharge Water pH"),
    ruta("WWTP", "Emissions", "Scope 1", "Photovoltaic Emissions"),
    ruta("WWTP", "Sankey Diagrams", "Energy", "Cogeneration 1"),
    ruta("WWTP", "PM_from_PME", "Algo", "pH del modelo importado"),
    # Otra planta del entorno compartido: NUNCA puede salir.
    ruta("Green H2", "Operational", "Utilities", "COND02101 Water Conductivity"),
]
# Un termino generico casa con muchos: en el AF real "pH" devuelve 8 y "Suspended"
# 7. Estos son para comprobar el tope con holgura.
ELEMENTOS += [ruta("WWTP", "Operational", "4 - Biological", "Blower %02d" % n)
              for n in range(1, 13)]
# El 'path' de un atributo es SOLO SU NOMBRE. Por eso el filtro del servidor no
# sirve para ellos y hay que acotarlos contra su piApiPath.
ATRIBUTOS = [
    {"name": "Turbidity inlet",
     "path": "Turbidity inlet",
     "piApiPath": PREFIJO + ruta("WWTP", "Operational", "1 - Intake") + "|Turbidity inlet"},
    {"name": "Turbidity outlet",
     "path": "Turbidity outlet",
     "piApiPath": PREFIJO + ruta("Cement", "Site A", "Lavado") + "|Turbidity outlet"},
]

pedidos = []


class SesionFalsa:
    """Imita graph_search: coincidencia por SUBCADENA y SIN distinguir mayusculas
    -- eso ultimo es lo que hace que "pH" devuelva "Photovoltaic Emissions" --, y
    el hecho de que el 'path' de un atributo no lleva la jerarquia."""

    async def call_tool(self, _tool, args):
        pedidos.append(dict(args))
        tipo, termino = args["node_type"], args["name_contains"].lower()
        filtro = args.get("path_contains")
        if tipo == "Element":
            res = [{"name": p.split(SEP)[-1], "path": p, "description": ""}
                   for p in ELEMENTOS
                   if termino in p.split(SEP)[-1].lower() and (not filtro or filtro in p)]
        else:
            res = [dict(a, description="") for a in ATRIBUTOS
                   if termino in a["name"].lower() and (not filtro or filtro in a["path"])]
        cuerpo = json.dumps({"count": len(res), "results": res})

        class C:
            text = cuerpo

        class R:
            content = [C()]
        return R()


def buscar(termino):
    pedidos.clear()
    return asyncio.run(graph_client.buscar_en_la_planta(SesionFalsa(), termino))


print("\n=== 1. No puede salirse de la planta ===")
r = buscar("Conductivity")
check("no devuelve nada de otra planta", r == [], r)
r = buscar("Turbidity")
check("el atributo de la cementera se queda fuera",
      [x["donde"] for x in r] == [ruta("WWTP", "Operational", "1 - Intake")], r)

print("\n=== 2. El atributo se acota en NUESTRO lado ===")
# El 'path' de un atributo es solo su nombre, asi que path_contains="WWTP\" no
# casa con ninguno: con el filtro del servidor se perdian TODOS. Comprobado
# contra el AF real el 2026-09-28: con filtro 0 resultados, sin filtro 2.
buscar("Turbidity")
porTipo = {p["node_type"]: p.get("path_contains") for p in pedidos}
check("a los elementos SI se les manda el filtro",
      porTipo["Element"] == "WWTP" + SEP, porTipo)
check("a los atributos NO", porTipo["Attribute"] is None, porTipo)
check("y aun asi el de fuera no pasa",
      all(x["donde"].startswith("WWTP" + SEP) for x in buscar("Turbidity")))

print("\n=== 3. Las ramas que no son proceso no salen ===")
# Diagramas y modelos importados devuelven elementos con pinta de dato.
r = buscar("Cogeneration")
check("Sankey Diagrams fuera", r == [], r)
r = buscar("pH")
check("PM_from_PME fuera", not any("PM_from_PME" in x["donde"] for x in r), r)
check("y se comprueba por segmento, no por subcadena",
      "Sankey Diagrams" not in graph_client._segmentos(ruta("WWTP", "Sankey DiagramsX", "y")))

print("\n=== 4. Lo que SI encuentra, y como lo devuelve ===")
r = buscar("pH")
nombres = [x["nombre"] for x in r]
check("los dos sensores reales", "PH213 Primary Line 1 Outlet pH" in nombres
      and "PH705 Discharge Water pH" in nombres, nombres)
# "pH" casa con "Photovoltaic": la busqueda es por subcadena y eso no se puede
# evitar aqui. Lo discrimina el modelo, que ve el nombre y donde vive.
check("y tambien los falsos positivos de la subcadena",
      any("Photovoltaic" in n for n in nombres), nombres)

print("\n=== 5. NO devuelve piApiPath ===")
# Lo que el modelo elige es un SITIO; las rutas las resuelve el codigo despues.
# Asi no puede copiar mal una ruta ni inventarsela -- que es justo el fallo que
# la puerta de autorizacion del Step 4 lleva meses conteniendo.
check("ninguna clave de ruta",
      all(set(x) == {"tipo", "nombre", "donde", "descripcion"} for x in r), r[:1])

print("\n=== 6. El termino viene del modelo: es entrada no confiable ===")
check("vacio no busca", buscar("") == [])
check("solo espacios tampoco", buscar("   ") == [])
buscar("z" * 500)
check("se recorta antes de mandarlo",
      all(len(p["name_contains"]) <= graph_client._TOPE_TERMINO for p in pedidos),
      [len(p["name_contains"]) for p in pedidos])

print("\n=== 7. Hay tope de resultados ===")
# No es coste: es que una lista larga no se elige, se hojea. Si hacen falta mas,
# que afine el termino -- para eso el bucle tiene cinco vueltas.
check("existe el tope", graph_client._TOPE_RESULTADOS == 8, graph_client._TOPE_RESULTADOS)
r = buscar("Blower")   # hay 12
check("y se respeta", len(r) == graph_client._TOPE_RESULTADOS, len(r))

print("\n=== 8. Solo se llama a graph_search ===")
# refresh_graph es de ESCRITURA. Que el modelo no la vea es el motivo de que
# esta funcion exista.
buscar("pH")
check("ninguna otra tool", all("node_type" in p for p in pedidos), pedidos)
fuente = (RAIZ / "graph_client.py").read_text(encoding="utf-8")
i = fuente.index("async def buscar_en_la_planta")
cuerpo = fuente[i:fuente.index("async def ", i + 10)]
# El nombre SI sale en el docstring, explicando por que existe la funcion. Lo que
# no puede haber es una llamada: el unico call_tool de aqui es a graph_search.
llamadas = [x.split(chr(34))[1] for x in cuerpo.split("call_tool(")[1:]]
check("el unico call_tool es graph_search", llamadas == ["graph_search"], llamadas)

print("\n" + "=" * 62)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

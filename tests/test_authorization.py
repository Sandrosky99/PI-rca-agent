"""Pruebas de la puerta de autorizacion del Step 4 (_validate_selected_variables).

Se ejecuta contra el af_context real que devolveria el Step 2 para
'PS20102 A03 PS02 Pump 02', reducido a lo imprescindible.
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, r"C:\MCPServer\rca-agent")

import workflow
import config

logging.basicConfig(level=logging.INFO, format="      [%(levelname)s] %(message)s")

AF = {
    "main_asset_context": [
        {
            "element": "PS20102 A03 PS02 Pump 02",
            "description": "",
            "attributes": [
                {"name": "Flow", "uom": "m3/h", "description": "",
                 "piApiPath": r"\\DATAINFRA\WWTP\Pump 02|Flow"},
                {"name": "Active Power", "uom": "kW", "description": "",
                 "piApiPath": r"\\DATAINFRA\WWTP\Pump 02|Active Power"},
            ],
        },
        {
            "element": "PS20102 A03 PS02 Pump 02 > Indicators > Hydraulic Efficiency",
            "description": "",
            "attributes": [
                {"name": "Calculation", "uom": "%", "description": "",
                 "piApiPath": r"\\DATAINFRA\WWTP\Pump 02\Hydraulic Efficiency|Calculation"},
                {"name": "Reference", "uom": "%", "description": "",
                 "piApiPath": r"\\DATAINFRA\WWTP\Pump 02\Hydraulic Efficiency|Reference"},
            ],
        },
    ],
    "nearby_elements_context": [
        {
            "element": "Pumping Station 01",
            "description": "",
            "attributes": [
                {"name": "Inlet Level", "uom": "m", "description": "",
                 "piApiPath": r"\\DATAINFRA\WWTP\Pumping Station 01|Inlet Level"},
            ],
        },
    ],
}

FLOW = r"\\DATAINFRA\WWTP\Pump 02|Flow"
POWER = r"\\DATAINFRA\WWTP\Pump 02|Active Power"
CALC = r"\\DATAINFRA\WWTP\Pump 02\Hydraulic Efficiency|Calculation"
LEVEL = r"\\DATAINFRA\WWTP\Pumping Station 01|Inlet Level"

fallos = []


def check(nombre, condicion, detalle=""):
    marca = "PASA" if condicion else "FALLA"
    print(f"  [{marca}] {nombre}" + (f"  -> {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


print("\n=== 1. Lista blanca construida desde el af_context ===")
idx = workflow._collect_authorized_paths(AF)
check("indexa los 5 atributos de ambos bloques", len(idx) == 5, f"len={len(idx)}")
check("incluye el bloque nearby", workflow._normalize_pi_path(LEVEL) in idx)

print("\n=== 2. Camino feliz: todo valido ===")
v, m = workflow._validate_selected_variables(
    {"variables": [{"element": "Pump 02", "piApiPath": FLOW},
                   {"element": "Hydraulic Efficiency", "piApiPath": CALC}],
     "missing_variables": ["Vibracion del rodamiento"]}, AF)
check("acepta las 2 variables", len(v) == 2, f"len={len(v)}")
check("conserva el piApiPath intacto", v[0]["piApiPath"] == FLOW)
check("conserva el element del modelo", v[0]["element"] == "Pump 02")
check("devuelve missing_variables", m == ["Vibracion del rodamiento"], f"{m}")

print("\n=== 3. Ruta inventada: se descarta, las buenas sobreviven ===")
v, m = workflow._validate_selected_variables(
    {"variables": [{"element": "Pump 02", "piApiPath": FLOW},
                   {"element": "Inventada", "piApiPath": r"\\DATAINFRA\WWTP\Pump 02|Mechanic Power"}],
     "missing_variables": []}, AF)
check("descarta solo la inventada", len(v) == 1 and v[0]["piApiPath"] == FLOW, f"{v}")

print("\n=== 4. Ruta reescrita (espacio de mas + mayusculas): se canonicaliza ===")
v, _ = workflow._validate_selected_variables(
    {"variables": [{"element": "x", "piApiPath": r"\\DATAINFRA\WWTP\Pump  02|FLOW"}]}, AF)
check("la acepta", len(v) == 1, f"{v}")
check("devuelve la forma canonica del AF", v and v[0]["piApiPath"] == FLOW, f"{v}")

print("\n=== 5. Duplicados: se descartan ===")
v, _ = workflow._validate_selected_variables(
    {"variables": [{"element": "a", "piApiPath": FLOW},
                   {"element": "b", "piApiPath": FLOW},
                   {"element": "c", "piApiPath": POWER}]}, AF)
check("deja 2 variables distintas", len(v) == 2, f"len={len(v)}")

print("\n=== 6. Claves desconocidas y element ausente ===")
v, _ = workflow._validate_selected_variables(
    {"variables": [{"piApiPath": FLOW, "reason": "porque si", "uom": "m3/h"}]}, AF)
check("acepta la variable", len(v) == 1)
check("normaliza a solo element/piApiPath", set(v[0]) == {"element", "piApiPath"}, f"{set(v[0])}")
check("rellena element con el path", v[0]["element"] == FLOW)

print("\n=== 7. Array plano (esquema antiguo): se tolera ===")
v, m = workflow._validate_selected_variables([{"element": "x", "piApiPath": CALC}], AF)
check("acepta el array plano", len(v) == 1 and m == [], f"{v} {m}")

print("\n=== 8. Todo invalido: corta el analisis ===")
try:
    workflow._validate_selected_variables(
        {"variables": [{"element": "x", "piApiPath": r"\\NO\EXISTE|Nada"}]}, AF)
    check("lanza VariableSelectionError", False, "no lanzo nada")
except workflow.VariableSelectionError as e:
    check("lanza VariableSelectionError", True)
    print(f"      motivo: {e}")

print("\n=== 9. af_context vacio: corta el analisis ===")
try:
    workflow._validate_selected_variables(
        {"variables": [{"element": "x", "piApiPath": FLOW}]},
        {"main_asset_context": [], "nearby_elements_context": []})
    check("lanza VariableSelectionError", False, "no lanzo nada")
except workflow.VariableSelectionError as e:
    check("lanza VariableSelectionError", True)
    print(f"      motivo: {e}")

print("\n=== 10. Respuesta con forma imposible ===")
for mala in ("un string suelto", {"variables": "no soy un array"}, 42):
    try:
        workflow._validate_selected_variables(mala, AF)
        check(f"rechaza {type(mala).__name__}", False, "no lanzo nada")
    except workflow.VariableSelectionError:
        check(f"rechaza {type(mala).__name__}: {str(mala)[:28]}", True)

print("\n=== 11. Tope de MAX_SELECTED_VARIABLES ===")
original = config.MAX_SELECTED_VARIABLES
config.MAX_SELECTED_VARIABLES = 2
v, _ = workflow._validate_selected_variables(
    {"variables": [{"element": "a", "piApiPath": FLOW},
                   {"element": "b", "piApiPath": POWER},
                   {"element": "c", "piApiPath": CALC},
                   {"element": "d", "piApiPath": LEVEL}]}, AF)
check("recorta a 2", len(v) == 2, f"len={len(v)}")
check("conserva las primeras (prioridad del prompt)",
      [x["piApiPath"] for x in v] == [FLOW, POWER], f"{[x['piApiPath'] for x in v]}")
config.MAX_SELECTED_VARIABLES = original

print("\n=== 12. missing_variables con basura ===")
v, m = workflow._validate_selected_variables(
    {"variables": [{"element": "x", "piApiPath": FLOW}],
     "missing_variables": ["Vibracion", "", 42, None, "Presion diferencial"]}, AF)
check("filtra no-textos y vacios", m == ["Vibracion", "Presion diferencial"], f"{m}")

v, m = workflow._validate_selected_variables(
    {"variables": [{"element": "x", "piApiPath": FLOW}],
     "missing_variables": "no soy un array"}, AF)
check("tolera missing_variables mal formado", m == [], f"{m}")

print("\n" + "=" * 60)
if fallos:
    print(f"FALLOS: {len(fallos)} -> {fallos}")
    sys.exit(1)
print("Todas las comprobaciones pasan.")

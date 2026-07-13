"""
graph_client.py — Cliente MCP para afkg-graph-mcp (Step 2 del agente RCA)

¿Qué hace este fichero?
  Lanza afkg-graph-mcp como subproceso (protocolo MCP sobre stdio, igual que
  hace Claude Code según .mcp.json) y usa su tool graph_neighborhood para
  recorrer recursivamente el árbol del Asset Framework de un elemento,
  construyendo main_asset_context / nearby_elements_context: la lista plana
  de variables reales (con su piApiPath) que se pasará al modelo en el Step 3,
  para que elija solo entre atributos que existen de verdad.

¿Por qué un recorrido recursivo y no una sola llamada?
  graph_neighborhood solo devuelve el vecindario directo de un elemento (sus
  atributos y sus hijos PARENT_OF inmediatos), no el árbol completo. Los
  activos del AF tienen varios niveles (p.ej. Bomba > Indicators > Hydraulic
  Effiency > Level Alert), así que hay que bajar nivel a nivel.

¿Por qué se usa el "path" del hijo como path_contains en la llamada recursiva?
  graph_neighborhood empareja por nombre exacto, y varios elementos del AF
  comparten nombre (p.ej. "Level Alert" o "Reference" aparecen repetidos bajo
  distintos indicadores). Sin desambiguar, la consulta puede devolver el
  elemento equivocado (visto en producción explorando manualmente el AF). El
  "path" que ya trae cada hijo en la respuesta de su padre es único, así que
  sirve como desambiguador fiable sin tener que reconstruirlo a mano.
"""

import json
import logging

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

import config

log = logging.getLogger(__name__)

_SERVER_PARAMS = StdioServerParameters(
    command="uv",
    args=[
        "run", "--directory", config.AFKG_GRAPH_MCP_DIR,
        "python", "src/afkg_graph_mcp/server.py",
    ],
)

# Atributos de metadatos/bookkeeping del AF: identifican o describen el activo
# (o son configuración interna del PI Point), pero no son variables de proceso
# consultables para un diagnóstico -- se omiten del contexto que ve el modelo.
_METADATA_ATTRS = {
    "Area Code", "Asset Code", "Asset Model", "Asset Type", "Naming Convention",
    "PI Data Archive", "Path", "Plant Code", "Plant", "System", "Subsystem",
    "Asset", "KPI Name", "Manufacturer", "Serial Number", "Tag Name",
    "Equipment Model",
}

# Salvaguarda ante ciclos o jerarquías anómalas en el AF -- en la práctica los
# activos de este dominio no bajan de 3-4 niveles (asset > Indicators > KPI > Level Alert).
_MAX_DEPTH = 8


def _filter_attributes(raw_attrs: list[dict]) -> list[dict]:
    """Descarta atributos de metadatos y normaliza los campos que ve el modelo.

    description/uom vacíos ("") se dejan explícitos (no se omite la clave):
    el AF simplemente no documenta ese campo para ese atributo, no significa
    que el atributo no exista o no sea relevante.
    """
    filtered = []
    for attr in raw_attrs:
        name = attr.get("name", "")
        if name in _METADATA_ATTRS:
            continue
        filtered.append({
            "name": name,
            "uom": attr.get("uom", ""),
            "description": attr.get("description", ""),
            "piApiPath": attr.get("piApiPath", ""),
        })
    return filtered


async def _call_graph_neighborhood(session: ClientSession, name: str, path_contains: str | None) -> dict:
    result = await session.call_tool(
        "graph_neighborhood",
        {"name": name, "path_contains": path_contains, "limit": 200},
    )
    text = result.content[0].text
    return json.loads(text)


async def _walk_subtree(
    session: ClientSession,
    name: str,
    path_contains: str | None,
    breadcrumb: list[str],
    skip_names: set[str],
    depth: int = _MAX_DEPTH,
) -> list[dict]:
    """Recorre recursivamente el subárbol PARENT_OF de un elemento del AF.

    Devuelve una lista plana de grupos {element, description, attributes}.
    Los nodos puramente organizativos (sin atributos propios, p.ej. "Meters"
    o "Indicators") no generan grupo propio -- su nombre solo queda reflejado
    en el breadcrumb de sus descendientes, que es donde aporta significado.
    """
    if depth <= 0:
        log.warning("Profundidad máxima alcanzada explorando '%s'; se corta la recursión.", name)
        return []

    data = await _call_graph_neighborhood(session, name, path_contains)
    if "error" in data:
        log.warning(
            "graph_neighborhood no encontró '%s' (path_contains=%s): %s",
            name, path_contains, data["error"],
        )
        return []

    element = data["element"]
    relationships = data.get("relationships", {})
    attributes = _filter_attributes(relationships.get("HAS_ATTRIBUTE", []))
    children = relationships.get("PARENT_OF", [])

    groups = []
    if attributes:
        groups.append({
            "element": " > ".join(breadcrumb),
            "description": element.get("description", ""),
            "attributes": attributes,
        })

    for child in children:
        child_name = child.get("name", "")
        if child_name in skip_names:
            continue
        groups.extend(await _walk_subtree(
            session, child_name, child.get("path"), breadcrumb + [child_name], skip_names, depth - 1,
        ))

    return groups


async def build_af_context(asset_name: str, subsystem_name: str) -> dict:
    """Step 2: construye main_asset_context / nearby_elements_context desde el AF.

    Args:
        asset_name: valor "Asset" del payload de PI -- el activo directamente
                    afectado por la alerta. Se usa como raíz de main_asset_context.
        subsystem_name: valor "Subsystem" del payload -- el elemento que agrupa
                         al activo principal. Se usa como raíz de
                         nearby_elements_context; su rama correspondiente al
                         propio asset_name se omite (ya está en main_asset_context).

    Returns:
        {"main_asset_context": [...], "nearby_elements_context": [...]}
        Listo para incluirse en el mensaje del Step 3 (build_analysis_context).
    """
    async with stdio_client(_SERVER_PARAMS) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            main_asset_context = await _walk_subtree(
                session, asset_name, None, [asset_name], skip_names=set(),
            )

            nearby_elements_context = []
            if subsystem_name and subsystem_name != asset_name:
                nearby_elements_context = await _walk_subtree(
                    session, subsystem_name, None, [subsystem_name], skip_names={asset_name},
                )

    return {
        "main_asset_context": main_asset_context,
        "nearby_elements_context": nearby_elements_context,
    }

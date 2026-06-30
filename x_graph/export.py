from __future__ import annotations

import csv
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.dom import minidom

from x_graph.graph import InteractionGraph
from x_graph.state import StateStore


def export_graph(
    state: StateStore,
    output_dir: Path,
    *,
    basename: str = "x_graph",
    formats: tuple[str, ...] = ("gexf", "csv"),
) -> dict[str, Path]:
    graph = state.load_graph()
    output_dir.mkdir(parents=True, exist_ok=True)
    written: dict[str, Path] = {}

    if "csv" in formats:
        nodes_path = output_dir / f"{basename}_nodes.csv"
        edges_path = output_dir / f"{basename}_edges.csv"
        _export_nodes_csv(graph, nodes_path)
        _export_edges_csv(graph, edges_path)
        written["nodes_csv"] = nodes_path
        written["edges_csv"] = edges_path

    if "gexf" in formats:
        gexf_path = output_dir / f"{basename}.gexf"
        _export_gexf(graph, gexf_path)
        written["gexf"] = gexf_path

    return written


def _export_nodes_csv(graph: InteractionGraph, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["Id", "Label", "username", "name", "profile_image_url"],
        )
        writer.writeheader()
        for node in graph.nodes.values():
            label = node.username or node.name or node.user_id
            writer.writerow(
                {
                    "Id": node.user_id,
                    "Label": label,
                    "username": node.username,
                    "name": node.name,
                    "profile_image_url": node.profile_image_url,
                }
            )


def _export_edges_csv(graph: InteractionGraph, path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=["Source", "Target", "Type", "Weight", "Interaction", "post_id"],
        )
        writer.writeheader()
        for edge in graph.edges.values():
            writer.writerow(
                {
                    "Source": edge.source_id,
                    "Target": edge.target_id,
                    "Type": "Directed",
                    "Weight": edge.weight,
                    "Interaction": edge.interaction,
                    "post_id": edge.post_id or "",
                }
            )


def _export_gexf(graph: InteractionGraph, path: Path) -> None:
    ns = "http://www.gexf.net/1.2draft"
    ET.register_namespace("", ns)
    root = ET.Element("{%s}gexf" % ns, version="1.2")
    graph_el = ET.SubElement(root, "{%s}graph" % ns, mode="static", defaultedgetype="directed")

    node_attrs = ET.SubElement(graph_el, "{%s}attributes" % ns, **{"class": "node"})
    ET.SubElement(node_attrs, "{%s}attribute" % ns, id="0", title="username", type="string")
    ET.SubElement(node_attrs, "{%s}attribute" % ns, id="1", title="name", type="string")
    ET.SubElement(
        node_attrs, "{%s}attribute" % ns, id="2", title="profile_image_url", type="string"
    )

    edge_attrs = ET.SubElement(graph_el, "{%s}attributes" % ns, **{"class": "edge"})
    ET.SubElement(edge_attrs, "{%s}attribute" % ns, id="0", title="interaction", type="string")
    ET.SubElement(edge_attrs, "{%s}attribute" % ns, id="1", title="post_id", type="string")

    nodes_el = ET.SubElement(graph_el, "{%s}nodes" % ns)
    for node in graph.nodes.values():
        label = node.username or node.name or node.user_id
        node_el = ET.SubElement(nodes_el, "{%s}node" % ns, id=node.user_id, label=label)
        attvalues = ET.SubElement(node_el, "{%s}attvalues" % ns)
        ET.SubElement(attvalues, "{%s}attvalue" % ns, **{"for": "0"}, value=node.username)
        ET.SubElement(attvalues, "{%s}attvalue" % ns, **{"for": "1"}, value=node.name)
        ET.SubElement(
            attvalues, "{%s}attvalue" % ns, **{"for": "2"}, value=node.profile_image_url
        )

    edges_el = ET.SubElement(graph_el, "{%s}edges" % ns)
    for idx, edge in enumerate(graph.edges.values()):
        edge_el = ET.SubElement(
            edges_el,
            "{%s}edge" % ns,
            id=str(idx),
            source=edge.source_id,
            target=edge.target_id,
            weight=str(edge.weight),
            label=edge.interaction,
        )
        attvalues = ET.SubElement(edge_el, "{%s}attvalues" % ns)
        ET.SubElement(
            attvalues, "{%s}attvalue" % ns, **{"for": "0"}, value=edge.interaction
        )
        ET.SubElement(
            attvalues,
            "{%s}attvalue" % ns,
            **{"for": "1"},
            value=edge.post_id or "",
        )

    xml_bytes = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(xml_bytes).toprettyxml(indent="  ", encoding="utf-8")
    path.write_bytes(pretty)
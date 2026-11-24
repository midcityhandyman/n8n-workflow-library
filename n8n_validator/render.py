"""Text renderings of a workflow: a mermaid flowchart and plain error path traces."""

from __future__ import annotations

from typing import Any

from . import graph


def _label(text: str) -> str:
    return text.replace('"', "'")


def mermaid(wf: dict[str, Any]) -> str:
    """Flowchart of the workflow. Error outputs are dashed, dead letter nodes are shaded."""
    nodes = wf["nodes"]
    ids = {node["name"]: f"n{index + 1}" for index, node in enumerate(nodes)}
    lines = ["flowchart LR"]
    for node in nodes:
        nid, text = ids[node["name"]], _label(node["name"])
        if graph.is_trigger(node):
            lines.append(f'    {nid}(["{text}"])')
        elif node["type"] == "n8n-nodes-base.if":
            lines.append(f'    {nid}{{"{text}"}}')
        else:
            lines.append(f'    {nid}["{text}"]')
    for node in nodes:
        outs = graph.outputs(wf, node["name"])
        error_index = graph.error_output_index(node) if graph.has_error_output(node) else None
        for index, group in enumerate(outs):
            for target in group:
                src, dst = ids[node["name"]], ids[target]
                if index == error_index:
                    lines.append(f"    {src} -. error .-> {dst}")
                elif node["type"] == "n8n-nodes-base.if":
                    lines.append(f"    {src} -- {'yes' if index == 0 else 'no'} --> {dst}")
                else:
                    lines.append(f"    {src} --> {dst}")
    dead = [ids[n["name"]] for n in nodes if graph.is_dead_letter(n)]
    if dead:
        lines.append("    classDef dead fill:#fde8e8,stroke:#b42318,color:#1f2937")
        lines.append(f"    class {','.join(dead)} dead")
    return "\n".join(lines)


def error_paths(wf: dict[str, Any]) -> list[str]:
    """One line per guarded node showing where a failed item goes."""
    lines = []
    for node in wf["nodes"]:
        for target in graph.error_targets(wf, node):
            tail = graph.longest_tail(wf, target)
            tries = node.get("maxTries", 1)
            wait = node.get("waitBetweenTries", 0)
            lines.append(f"{node['name']} (x{tries}, {wait} ms apart) =error=> " + " -> ".join(tail))
    return lines


def happy_path(wf: dict[str, Any]) -> list[str]:
    """Follow the first regular output from the first trigger."""
    for node in wf["nodes"]:
        if graph.is_trigger(node):
            return graph.longest_tail(wf, node["name"])
    return []

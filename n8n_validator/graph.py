"""Small graph helpers over the n8n `connections` object.

n8n stores connections keyed by the source node NAME:

    "connections": {
      "Source node": {
        "main": [
          [ {"node": "Target A", "type": "main", "index": 0} ],   # output 0
          [ {"node": "Target B", "type": "main", "index": 0} ]    # output 1
        ]
      }
    }

When a node has `"onError": "continueErrorOutput"`, n8n adds one extra output
after the regular ones. Failed items leave through that last output.
"""

from __future__ import annotations

from typing import Any

# Regular (non error) output count for node types that have more than one.
REGULAR_OUTPUTS = {
    "n8n-nodes-base.if": 2,
    "n8n-nodes-base.splitInBatches": 2,
}

TRIGGER_TYPES = {
    "n8n-nodes-base.webhook",
    "n8n-nodes-base.start",
    "n8n-nodes-base.cron",
    "n8n-nodes-base.interval",
}

DEAD_LETTER_PREFIX = "dead letter"


def node_map(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {n["name"]: n for n in workflow.get("nodes", []) if isinstance(n, dict) and "name" in n}


def is_trigger(node: dict[str, Any]) -> bool:
    node_type = str(node.get("type", ""))
    return node_type in TRIGGER_TYPES or node_type.lower().endswith("trigger")


def is_dead_letter(node: dict[str, Any]) -> bool:
    return str(node.get("name", "")).lower().startswith(DEAD_LETTER_PREFIX)


def outputs(workflow: dict[str, Any], name: str) -> list[list[str]]:
    """Target node names for each output index of `name`."""
    main = workflow.get("connections", {}).get(name, {}).get("main", [])
    result: list[list[str]] = []
    for group in main if isinstance(main, list) else []:
        targets = []
        for link in group or []:
            if isinstance(link, dict) and isinstance(link.get("node"), str):
                targets.append(link["node"])
        result.append(targets)
    return result


def error_output_index(node: dict[str, Any]) -> int:
    return REGULAR_OUTPUTS.get(str(node.get("type", "")), 1)


def has_error_output(node: dict[str, Any]) -> bool:
    return node.get("onError") == "continueErrorOutput"


def error_targets(workflow: dict[str, Any], node: dict[str, Any]) -> list[str]:
    """Nodes wired to the error output of `node` (empty if not wired)."""
    if not has_error_output(node):
        return []
    outs = outputs(workflow, node["name"])
    index = error_output_index(node)
    return outs[index] if index < len(outs) else []


def regular_targets(workflow: dict[str, Any], node: dict[str, Any]) -> list[list[str]]:
    outs = outputs(workflow, node["name"])
    if has_error_output(node):
        return outs[: error_output_index(node)]
    return outs


def reachable(workflow: dict[str, Any], starts: list[str]) -> set[str]:
    """All node names reachable from `starts` over any output, starts included."""
    seen: set[str] = set()
    stack = list(starts)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for group in outputs(workflow, name):
            stack.extend(group)
    return seen


def first_path(workflow: dict[str, Any], start: str, goal) -> list[str] | None:
    """Breadth first path from `start` to the first node where goal(node) is true."""
    nodes = node_map(workflow)
    queue: list[list[str]] = [[start]]
    seen = {start}
    while queue:
        path = queue.pop(0)
        node = nodes.get(path[-1])
        if node is not None and goal(node):
            return path
        for group in outputs(workflow, path[-1]):
            for target in group:
                if target not in seen:
                    seen.add(target)
                    queue.append(path + [target])
    return None


def longest_tail(workflow: dict[str, Any], start: str) -> list[str]:
    """Follow output 0 from `start` until the chain ends. Used to print error paths."""
    path = [start]
    seen = {start}
    while True:
        outs = outputs(workflow, path[-1])
        nxt = outs[0][0] if outs and outs[0] else None
        if nxt is None or nxt in seen:
            return path
        seen.add(nxt)
        path.append(nxt)

"""Validation rules for n8n workflow JSON.

Every rule has a stable id so a failing CI run points at one sentence in the
README. Rules only read the JSON. Nothing here talks to an n8n instance.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterator

from . import graph

RULES = {
    "STRUCT-01": "File is a JSON object with name, nodes, connections and settings",
    "STRUCT-02": "Every node has id, name, type, typeVersion, position and parameters",
    "STRUCT-03": "Node ids and node names are unique",
    "STRUCT-04": "Connections only reference nodes that exist and use the main[][] shape",
    "STRUCT-05": "Workflow has a trigger and every node is reachable from one",
    "STRUCT-06": "settings.executionOrder is v1",
    "STRUCT-07": "Workflow ships inactive with no pinned or static data",
    "STRUCT-08": "Expressions have matching {{ and }} pairs",
    "ERR-01": "Nodes that call outside systems retry (2 to 5 tries, 1 to 5 second wait)",
    "ERR-02": "Nodes that call outside systems route failures to a wired error output",
    "ERR-03": "Every error output leads to a dead letter node that stores the failed item",
    "ERR-04": "Workflow names an error workflow or is itself an Error Trigger workflow",
    "ERR-05": "Workflow has at least one error branch",
    "CRED-01": "Credential references carry only an id and a name",
    "CRED-02": "No string looks like a key, token, password or URL with a login in it",
    "CRED-03": "Sensitive fields (password, token, Authorization...) hold expressions, not literals",
}

# Node types that reach outside n8n and can therefore fail for reasons we do not control.
IO_NODE_TYPES = {
    "n8n-nodes-base.httpRequest",
    "n8n-nodes-base.emailSend",
    "n8n-nodes-base.slack",
    "n8n-nodes-base.twilio",
    "n8n-nodes-base.googleSheets",
    "n8n-nodes-base.googleCalendar",
    "n8n-nodes-base.gmail",
    "n8n-nodes-base.postgres",
    "n8n-nodes-base.hubspot",
    "n8n-nodes-base.airtable",
}

ON_ERROR_VALUES = {"stopWorkflow", "continueRegularOutput", "continueErrorOutput"}

SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("Slack incoming webhook URL", re.compile(r"hooks\.slack\.com/services/T[A-Za-z0-9]+/")),
    ("API secret key (sk- prefix)", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("payment provider key", re.compile(r"\b[sr]k_(?:live|test)_[0-9A-Za-z]{16,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}")),
    ("telephony account SID or key", re.compile(r"\b(?:AC|SK)[0-9a-f]{32}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("JSON web token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("literal Bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{16,}=*")),
    ("literal Basic auth value", re.compile(r"\bBasic\s+[A-Za-z0-9+/]{16,}={0,2}")),
    ("URL with embedded login", re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@{}]+:[^/\s:@{}]+@")),
    (
        "secret in a URL query string",
        re.compile(r"[?&](?:api[_-]?key|access[_-]?token|token|secret|password|key)=[A-Za-z0-9._~-]{8,}", re.I),
    ),
]

SENSITIVE_KEY = re.compile(
    r"(password|passwd|secret|api[_-]?key|access[_-]?key|private[_-]?key|token|authorization)", re.I
)
PLACEHOLDER = re.compile(r"^(REPLACE|CHANGE|YOUR|TODO|<)", re.I)


@dataclass(frozen=True)
class Finding:
    rule: str
    message: str
    node: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __str__(self) -> str:
        where = f" [{self.node}]" if self.node else ""
        return f"{self.rule}{where}: {self.message}"


# --------------------------------------------------------------------------- structure


def check_structure(wf: Any) -> tuple[list[Finding], bool]:
    """Return findings and whether the graph is sound enough for the other checks."""
    out: list[Finding] = []
    if not isinstance(wf, dict):
        return [Finding("STRUCT-01", "top level value must be a JSON object")], False

    for key, kind in (("name", str), ("nodes", list), ("connections", dict), ("settings", dict)):
        if not isinstance(wf.get(key), kind):
            out.append(Finding("STRUCT-01", f"'{key}' is missing or is not a {kind.__name__}"))
    if isinstance(wf.get("name"), str) and not wf["name"].strip():
        out.append(Finding("STRUCT-01", "'name' is empty"))
    if not isinstance(wf.get("nodes"), list) or not isinstance(wf.get("connections"), dict):
        return out, False
    if not wf["nodes"]:
        out.append(Finding("STRUCT-01", "'nodes' is empty"))
        return out, False

    sound = True
    ids: dict[str, str] = {}
    names: set[str] = set()
    for position, node in enumerate(wf["nodes"]):
        if not isinstance(node, dict):
            out.append(Finding("STRUCT-02", f"nodes[{position}] is not an object"))
            sound = False
            continue
        label = node.get("name") if isinstance(node.get("name"), str) else f"nodes[{position}]"
        for key in ("id", "name", "type"):
            if not isinstance(node.get(key), str) or not node[key].strip():
                out.append(Finding("STRUCT-02", f"'{key}' is missing or empty", label))
                sound = sound and key != "name"
        version = node.get("typeVersion")
        if isinstance(version, bool) or not isinstance(version, (int, float)) or version <= 0:
            out.append(Finding("STRUCT-02", "'typeVersion' must be a positive number", label))
        pos = node.get("position")
        if not (
            isinstance(pos, list)
            and len(pos) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in pos)
        ):
            out.append(Finding("STRUCT-02", "'position' must be [x, y]", label))
        if not isinstance(node.get("parameters"), dict):
            out.append(Finding("STRUCT-02", "'parameters' must be an object", label))
        if "onError" in node and node["onError"] not in ON_ERROR_VALUES:
            out.append(Finding("STRUCT-02", f"'onError' must be one of {sorted(ON_ERROR_VALUES)}", label))
        if isinstance(node.get("type"), str) and "." not in node["type"]:
            out.append(Finding("STRUCT-02", "'type' must look like package.nodeName", label))

        node_id = node.get("id")
        if isinstance(node_id, str):
            if node_id in ids:
                out.append(Finding("STRUCT-03", f"id '{node_id}' is also used by '{ids[node_id]}'", label))
            ids[node_id] = str(label)
        if isinstance(node.get("name"), str):
            if node["name"] in names:
                out.append(Finding("STRUCT-03", "node name is used more than once", label))
                sound = False
            names.add(node["name"])

    for source, by_type in wf["connections"].items():
        if source not in names:
            out.append(Finding("STRUCT-04", "connection source is not a node in this workflow", source))
            sound = False
            continue
        if not isinstance(by_type, dict) or not isinstance(by_type.get("main"), list):
            out.append(Finding("STRUCT-04", "connections must look like {\"main\": [[...]]}", source))
            sound = False
            continue
        for index, group in enumerate(by_type["main"]):
            if group is None:
                continue
            if not isinstance(group, list):
                out.append(Finding("STRUCT-04", f"main[{index}] must be a list of links", source))
                sound = False
                continue
            for link in group:
                ok = (
                    isinstance(link, dict)
                    and isinstance(link.get("node"), str)
                    and link.get("type") == "main"
                    and isinstance(link.get("index"), int)
                )
                if not ok:
                    out.append(Finding("STRUCT-04", f"main[{index}] has a malformed link: {link!r}", source))
                    sound = False
                elif link["node"] not in names:
                    out.append(Finding("STRUCT-04", f"main[{index}] points at unknown node '{link['node']}'", source))
                    sound = False

    if sound:
        nodes = graph.node_map(wf)
        triggers = [n["name"] for n in nodes.values() if graph.is_trigger(n)]
        if not triggers:
            out.append(Finding("STRUCT-05", "no trigger node found"))
        else:
            orphans = sorted(set(nodes) - graph.reachable(wf, triggers))
            for name in orphans:
                out.append(Finding("STRUCT-05", "node is not reachable from any trigger", name))

    settings = wf.get("settings")
    if isinstance(settings, dict) and settings.get("executionOrder") != "v1":
        out.append(Finding("STRUCT-06", "settings.executionOrder should be 'v1'"))

    for node in wf["nodes"]:
        if not isinstance(node, dict):
            continue
        for text in _strings(node.get("parameters", {})):
            if text.startswith("=") and text.count("{{") != text.count("}}"):
                out.append(Finding("STRUCT-08", f"unbalanced expression braces in: {text[:60]}", node.get("name")))

    if wf.get("active") is True:
        out.append(Finding("STRUCT-07", "library workflows ship with active=false; activate after import"))
    if wf.get("pinData"):
        out.append(Finding("STRUCT-07", "pinData is not empty; pinned data can leak real records"))
    if wf.get("staticData"):
        out.append(Finding("STRUCT-07", "staticData is not empty; it can leak state from a live instance"))
    return out, sound


# ----------------------------------------------------------------------- error handling


def needs_guard(node: dict[str, Any]) -> bool:
    return node.get("type") in IO_NODE_TYPES or bool(node.get("credentials"))


def check_error_handling(wf: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    nodes = graph.node_map(wf)

    for name, node in nodes.items():
        if not needs_guard(node):
            continue
        tries = node.get("maxTries")
        wait = node.get("waitBetweenTries")
        if node.get("retryOnFail") is not True:
            out.append(Finding("ERR-01", "retryOnFail must be true", name))
        elif not isinstance(tries, int) or not 2 <= tries <= 5:
            out.append(Finding("ERR-01", "maxTries must be between 2 and 5 (n8n caps it at 5)", name))
        elif not isinstance(wait, int) or not 1000 <= wait <= 5000:
            out.append(Finding("ERR-01", "waitBetweenTries must be 1000 to 5000 ms (n8n caps it at 5000)", name))

        # Dead letter nodes are the end of the line. They are allowed to fail the
        # execution (which fires the error workflow) or to continue so the alert still goes out.
        if graph.is_dead_letter(node):
            continue
        if not graph.has_error_output(node):
            out.append(Finding("ERR-02", "onError must be 'continueErrorOutput'", name))
        elif not graph.error_targets(wf, node):
            out.append(Finding("ERR-02", "error output is not connected to anything", name))

    branches = 0
    for name, node in nodes.items():
        targets = graph.error_targets(wf, node)
        if not targets:
            continue
        branches += 1
        for target in targets:
            path = graph.first_path(wf, target, lambda n: graph.is_dead_letter(n) and needs_guard(n))
            if path is None:
                out.append(
                    Finding("ERR-03", f"error output goes to '{target}' but never reaches a dead letter store", name)
                )
    if branches == 0:
        out.append(Finding("ERR-05", "no node has a connected error output"))

    has_error_trigger = any(n.get("type") == "n8n-nodes-base.errorTrigger" for n in nodes.values())
    error_workflow = wf.get("settings", {}).get("errorWorkflow") if isinstance(wf.get("settings"), dict) else None
    if not has_error_trigger and not (isinstance(error_workflow, str) and error_workflow.strip()):
        out.append(Finding("ERR-04", "settings.errorWorkflow is not set"))
    return out


# -------------------------------------------------------------------------- credentials


def _walk(value: Any, path: str = "") -> Iterator[tuple[str, str, Any]]:
    """Yield (path, key, value) for every dict entry, depth first."""
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield child_path, str(key), child
            yield from _walk(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk(child, f"{path}[{index}]")


def _is_literal_secret(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    if value.startswith("="):  # n8n expression; literals inside it are caught by CRED-02
        return False
    return not PLACEHOLDER.match(value.strip())


def _mask(text: str) -> str:
    return text[:6] + "..." if len(text) > 6 else "..."


def check_credentials(wf: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    for node in wf.get("nodes", []):
        if not isinstance(node, dict):
            continue
        name = node.get("name") if isinstance(node.get("name"), str) else None
        creds = node.get("credentials")
        if creds is not None:
            if not isinstance(creds, dict):
                out.append(Finding("CRED-01", "'credentials' must be an object", name))
            else:
                for cred_type, ref in creds.items():
                    if not isinstance(ref, dict) or not set(ref) <= {"id", "name"} or "name" not in ref:
                        out.append(
                            Finding("CRED-01", f"credential '{cred_type}' must be a reference with only id and name", name)
                        )
        for path, key, value in _walk(node.get("parameters", {}), "parameters"):
            if SENSITIVE_KEY.search(key) and _is_literal_secret(value):
                out.append(Finding("CRED-03", f"{path} holds a literal value; use a credential or an expression", name))
            # header and query lists are stored as {"name": "Authorization", "value": "..."}
            if key == "name" and isinstance(value, str) and SENSITIVE_KEY.search(value):
                parent = _lookup(node, path.rsplit(".", 1)[0])
                if isinstance(parent, dict) and _is_literal_secret(parent.get("value")):
                    out.append(
                        Finding("CRED-03", f"{path.rsplit('.', 1)[0]} sets '{value}' to a literal value", name)
                    )

    for node in [n for n in wf.get("nodes", []) if isinstance(n, dict)] + [
        {"name": None, "rest": {k: v for k, v in wf.items() if k != "nodes"}}
    ]:
        for text in _strings(node):
            for label, pattern in SECRET_PATTERNS:
                match = pattern.search(text)
                if match:
                    out.append(
                        Finding("CRED-02", f"looks like a {label}: {_mask(match.group(0))}", node.get("name"))
                    )
    return out


def _lookup(root: Any, path: str) -> Any:
    current = root
    for token in re.findall(r"[^.\[\]]+", path):
        if isinstance(current, list):
            current = current[int(token)]
        elif isinstance(current, dict):
            current = current.get(token)
        else:
            return None
    return current


def _strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _strings(child)


# ------------------------------------------------------------------------------ entry


def validate_workflow(wf: Any) -> list[Finding]:
    findings, sound = check_structure(wf)
    if sound:
        findings += check_error_handling(wf)
    if isinstance(wf, dict):
        findings += check_credentials(wf)
    return findings


def validate_file(path: str | Path) -> list[Finding]:
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return [Finding("STRUCT-01", f"cannot read file: {exc}")]
    try:
        wf = json.loads(text)
    except json.JSONDecodeError as exc:
        return [Finding("STRUCT-01", f"not valid JSON: {exc.msg} at line {exc.lineno}")]
    return validate_workflow(wf)

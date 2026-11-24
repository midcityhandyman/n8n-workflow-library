"""Deliberate ways to break a good workflow. Used by the demo and by the tests.

Each entry is (title, rule the validator should report, function that damages a copy).
Fake secrets are assembled at run time so that no secret shaped string is stored
in this repository.
"""

from __future__ import annotations

from typing import Any, Callable

Workflow = dict[str, Any]


def _node(wf: Workflow, name: str) -> dict[str, Any]:
    return next(n for n in wf["nodes"] if n["name"] == name)


def _first_http(wf: Workflow) -> dict[str, Any]:
    return next(n for n in wf["nodes"] if n["type"].endswith("httpRequest") and n.get("onError"))


def remove_retries(wf: Workflow) -> None:
    _first_http(wf)["retryOnFail"] = False


def unplug_error_output(wf: Workflow) -> None:
    node = _first_http(wf)
    wf["connections"][node["name"]]["main"] = wf["connections"][node["name"]]["main"][:1]


def remove_error_handling_setting(wf: Workflow) -> None:
    node = _first_http(wf)
    del node["onError"]
    wf["connections"][node["name"]]["main"] = wf["connections"][node["name"]]["main"][:1]


def cut_dead_letter_store(wf: Workflow) -> None:
    keep = "Dead letter: build record"
    gone = [n["name"] for n in wf["nodes"] if n["name"].startswith("Dead letter: ") and n["name"] != keep]
    wf["nodes"] = [n for n in wf["nodes"] if n["name"] not in gone]
    for name in [keep] + gone:
        wf["connections"].pop(name, None)


def drop_error_workflow(wf: Workflow) -> None:
    wf["settings"].pop("errorWorkflow", None)


def paste_bearer_header(wf: Workflow) -> None:
    fake = "Bearer " + "abc123" * 6
    _first_http(wf)["parameters"]["headerParameters"] = {"parameters": [{"name": "Authorization", "value": fake}]}


def paste_api_key_in_url(wf: Workflow) -> None:
    _first_http(wf)["parameters"]["url"] = "https://crm.example.com/api/contacts?api_key=" + "k9" * 12


def inline_credential_data(wf: Workflow) -> None:
    _first_http(wf)["credentials"] = {"httpHeaderAuth": {"name": "CRM API", "data": {"value": "x" * 24}}}


def dangling_connection(wf: Workflow) -> None:
    first = next(iter(wf["connections"]))
    wf["connections"][first]["main"][0][0]["node"] = "Node that does not exist"


def duplicate_node_name(wf: Workflow) -> None:
    wf["nodes"][2]["name"] = wf["nodes"][1]["name"]


def leave_pinned_data(wf: Workflow) -> None:
    wf["pinData"] = {wf["nodes"][0]["name"]: [{"json": {"name": "Alex Sample"}}]}
    wf["active"] = True


def unbalanced_expression(wf: Workflow) -> None:
    _first_http(wf)["parameters"]["url"] = "={{ $env.CRM_BASE_URL }/contacts"


def delete_opt_out_line(wf: Workflow) -> None:
    node = _node(wf, "Text acknowledgement to lead")
    node["parameters"]["jsonBody"] = node["parameters"]["jsonBody"].replace(" Reply STOP to opt out.", "")


def bypass_opt_out_check(wf: Workflow) -> None:
    # the kind of shortcut someone draws in the editor "just to test" and forgets to remove
    wf["connections"]["Has a mobile number?"]["main"][0].append(
        {"node": "Text acknowledgement to lead", "type": "main", "index": 0})


BREAKAGES: list[tuple[str, str, Callable[[Workflow], None]]] = [
    ("Retries switched off on an API call", "ERR-01", remove_retries),
    ("Error output left unconnected", "ERR-02", unplug_error_output),
    ("On Error left at the default (stop workflow)", "ERR-02", remove_error_handling_setting),
    ("Dead letter store deleted", "ERR-03", cut_dead_letter_store),
    ("Error workflow setting removed", "ERR-04", drop_error_workflow),
    ("Authorization header pasted in from a cURL import", "CRED-03", paste_bearer_header),
    ("API key typed into a URL", "CRED-02", paste_api_key_in_url),
    ("Credential data inlined instead of referenced", "CRED-01", inline_credential_data),
    ("Connection points at a node that was renamed", "STRUCT-04", dangling_connection),
    ("Two nodes share a name", "STRUCT-03", duplicate_node_name),
    ("Exported while active with pinned test data", "STRUCT-07", leave_pinned_data),
    ("Expression with a missing brace", "STRUCT-08", unbalanced_expression),
    ("Opt-out line edited out of a customer text", "SMS-01", delete_opt_out_line),
    ("Shortcut wired around the opt-out check", "SMS-02", bypass_opt_out_check),
]

"""A small builder for n8n workflow JSON.

The ten workflows share one reliability pattern (retry, error output, dead
letter, alert). Writing that by hand ten times invites drift, so the specs in
specs.py describe only what is different and this module adds the shared tail.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

NAMESPACE = uuid.UUID("6f1d2c1e-0000-4000-8000-00000000a11e")
RETRY = {"retryOnFail": True, "maxTries": 3, "waitBetweenTries": 2000}
ERROR_WORKFLOW_PLACEHOLDER = "REPLACE_WITH_ERROR_WORKFLOW_ID"
CREDENTIAL_PLACEHOLDER = "REPLACE_WITH_CREDENTIAL_ID"

DL_BUILD = "Dead letter: build record"
DL_STORE = "Dead letter: store failed item"
DL_ALERT = "Dead letter: alert owner"

# Every environment variable the library reads, with the text used in docs and .env.example.
ENV = {
    "BUSINESS_NAME": ("Sample Home Services", "Name used in customer facing messages"),
    "BUSINESS_PHONE": ("+15555550100", "Number that texts are sent from"),
    "OWNER_PHONE": ("+15555550101", "Where urgent alerts are texted"),
    "OWNER_EMAIL": ("owner@example.com", "Where the digest and last resort alerts go"),
    "REVIEW_LINK": ("https://reviews.example.com/sample-home-services", "Public review page"),
    "CRM_BASE_URL": ("https://crm.example.com/api", "Base URL of the CRM REST API"),
    "ACCOUNTING_API_URL": ("https://accounting.example.com/api", "Base URL of the invoicing API"),
    "HR_API_URL": ("https://hr.example.com/api", "Base URL of the HR or task system API"),
    "CALENDAR_API_URL": ("https://calendar.example.com/api", "Base URL of the calendar API"),
    "SMS_API_URL": ("https://sms.example.com/api/messages", "Endpoint that sends one text message"),
    "EMAIL_API_URL": ("https://email.example.com/api/send", "Endpoint that sends one email"),
    "TEAM_CHAT_WEBHOOK_URL": ("https://chat.example.com/hooks/office", "Incoming webhook for the office channel"),
    "ALERT_WEBHOOK_URL": ("https://chat.example.com/hooks/alerts", "Incoming webhook for the alerts channel"),
    "DEAD_LETTER_URL": ("https://ops.example.com/api/dead-letters", "Endpoint that stores failed items for replay"),
}

TYPE_LABELS = {
    "n8n-nodes-base.webhook": "Webhook",
    "n8n-nodes-base.scheduleTrigger": "Schedule Trigger",
    "n8n-nodes-base.errorTrigger": "Error Trigger",
    "n8n-nodes-base.code": "Code",
    "n8n-nodes-base.if": "If",
    "n8n-nodes-base.set": "Edit Fields (Set)",
    "n8n-nodes-base.httpRequest": "HTTP Request",
    "n8n-nodes-base.noOp": "No Operation",
}


def _uid(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, "/".join(parts)))


class Flow:
    """Collects nodes and links, then emits the n8n export shape."""

    def __init__(self, number: int, slug: str, name: str, purpose: str, trigger_text: str,
                 sample_title: str, sample: Any, tuning: list[str], is_error_workflow: bool = False):
        self.number = number
        self.slug = f"{number:02d}-{slug}"
        self.name = name
        self.purpose = purpose
        self.trigger_text = trigger_text
        self.sample_title = sample_title
        self.sample = sample
        self.tuning = tuning
        self.is_error_workflow = is_error_workflow
        self.nodes: list[dict[str, Any]] = []
        self.links: list[tuple[str, int, str]] = []
        self.alert: dict[str, Any] | None = None

    # ------------------------------------------------------------------ node helpers

    def _add(self, name: str, node_type: str, version: float, params: dict[str, Any], note: str,
             **extra: Any) -> str:
        node = {
            "parameters": params,
            "id": _uid(self.slug, name),
            "name": name,
            "type": node_type,
            "typeVersion": version,
            "position": [0, 0],
            **extra,
            "notes": note,
        }
        self.nodes.append(node)
        return name

    def webhook(self, name: str, path: str, note: str) -> str:
        return self._add(name, "n8n-nodes-base.webhook", 2,
                         {"httpMethod": "POST", "path": path, "responseMode": "onReceived", "options": {}},
                         note, webhookId=_uid(self.slug, name, "webhook"))

    def schedule(self, name: str, cron: str, note: str) -> str:
        return self._add(name, "n8n-nodes-base.scheduleTrigger", 1.2,
                         {"rule": {"interval": [{"field": "cronExpression", "expression": cron}]}}, note)

    def error_trigger(self, name: str, note: str) -> str:
        return self._add(name, "n8n-nodes-base.errorTrigger", 1, {}, note)

    def code(self, name: str, js: str, note: str) -> str:
        return self._add(name, "n8n-nodes-base.code", 2, {"jsCode": js.strip() + "\n"}, note)

    def noop(self, name: str, note: str) -> str:
        return self._add(name, "n8n-nodes-base.noOp", 1, {}, note)

    def check(self, name: str, left: str, note: str, op: str = "true", right: Any = "",
              kind: str = "boolean") -> str:
        operator: dict[str, Any] = {"type": kind, "operation": op}
        if op in ("true", "false", "empty", "notEmpty"):
            operator["singleValue"] = True
        params = {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "conditions": [{"id": _uid(self.slug, name, "condition"), "leftValue": left,
                                "rightValue": right, "operator": operator}],
                "combinator": "and",
            },
            "options": {},
        }
        return self._add(name, "n8n-nodes-base.if", 2.2, params, note)

    def http(self, name: str, method: str, url: str, note: str, body: str | None = None,
             cred: str | None = None, on_error: str = "continueErrorOutput") -> str:
        params: dict[str, Any] = {"method": method, "url": url}
        if cred:
            params["authentication"] = "genericCredentialType"
            params["genericAuthType"] = "httpHeaderAuth"
        if body is not None:
            params["sendBody"] = True
            params["specifyBody"] = "json"
            params["jsonBody"] = "={{ JSON.stringify(" + " ".join(body.split()) + ") }}"
        params["options"] = {"timeout": 15000}
        extra: dict[str, Any] = dict(RETRY)
        if on_error != "stopWorkflow":
            extra["onError"] = on_error
        if cred:
            extra["credentials"] = {"httpHeaderAuth": {"id": CREDENTIAL_PLACEHOLDER, "name": cred}}
        return self._add(name, "n8n-nodes-base.httpRequest", 4.2, params, note, **extra)

    # ------------------------------------------------------------------ wiring

    def link(self, src: str, dst: str, output: int = 0) -> None:
        self.links.append((src, output, dst))

    def chain(self, *names: str) -> None:
        for src, dst in zip(names, names[1:]):
            self.link(src, dst)

    def reject(self, check_name: str) -> None:
        """Send the 'no' side of an If node to the dead letter path."""
        self.link(check_name, DL_BUILD, 1)

    def set_alert(self, name: str, url: str, body: str, note: str, cred: str | None = None) -> None:
        self.alert = {"name": name, "url": url, "body": body, "note": note, "cred": cred}

    # ------------------------------------------------------------------ shared tail

    def _add_dead_letter_tail(self) -> None:
        guarded = [n for n in self.nodes if n.get("onError") == "continueErrorOutput"]
        fields = [
            ("workflow", "={{ $workflow.name }}"),
            ("executionId", "={{ $execution.id }}"),
            ("failedNode", "={{ $prevNode.name }}"),
            ("error", "={{ typeof $json.error === 'string' ? $json.error : "
                      "($json.error?.message ?? 'rejected by validation') }}"),
            ("payload", "={{ JSON.stringify($json) }}"),
            ("failedAt", "={{ $now.toISO() }}"),
        ]
        self._add(DL_BUILD, "n8n-nodes-base.set", 3.4, {
            "assignments": {"assignments": [
                {"id": _uid(self.slug, DL_BUILD, key), "name": key, "value": value, "type": "string"}
                for key, value in fields]},
            "options": {},
        }, "Shapes one record per failed item: which workflow, which node, why, and the item itself so it can be replayed.")
        self.http(DL_STORE, "POST", "={{ $env.DEAD_LETTER_URL }}",
                  "Stores the record. Continues even if the store is down so the alert below still goes out.",
                  body="$json", cred="Dead letter store", on_error="continueRegularOutput")
        alert = self.alert or {
            "name": DL_ALERT,
            "url": "={{ $env.ALERT_WEBHOOK_URL }}",
            "body": ("{ text: `[${$('" + DL_BUILD + "').item.json.workflow}] failed at "
                     "${$('" + DL_BUILD + "').item.json.failedNode}: ${$('" + DL_BUILD + "').item.json.error} "
                     "(execution ${$('" + DL_BUILD + "').item.json.executionId})` }"),
            "note": "Posts to the alerts channel. If this also fails the execution fails and the error workflow takes over.",
            "cred": None,
        }
        self.http(alert["name"], "POST", alert["url"], alert["note"], body=alert["body"],
                  cred=alert["cred"], on_error="stopWorkflow")
        for node in guarded:
            error_index = 2 if node["type"] == "n8n-nodes-base.if" else 1
            self.link(node["name"], DL_BUILD, error_index)
        self.chain(DL_BUILD, DL_STORE, alert["name"])

    def _layout(self) -> None:
        tail = [n["name"] for n in self.nodes if n["name"].lower().startswith("dead letter")]
        depth: dict[str, int] = {}
        targets = {dst for _, _, dst in self.links}
        roots = [n["name"] for n in self.nodes if n["name"] not in targets]
        for root in roots:
            depth[root] = 0
        for _ in range(len(self.nodes)):
            for src, _, dst in self.links:
                if dst in tail or src in tail or src not in depth:
                    continue
                depth[dst] = max(depth.get(dst, 0), depth[src] + 1)
        rows: dict[int, int] = {}
        deepest_row = 0
        for node in self.nodes:
            if node["name"] in tail:
                continue
            column = depth.get(node["name"], 0)
            row = rows.get(column, 0)
            rows[column] = row + 1
            deepest_row = max(deepest_row, row)
            node["position"] = [column * 240, 300 + row * 200]
        for index, name in enumerate(tail):
            node = next(n for n in self.nodes if n["name"] == name)
            node["position"] = [480 + index * 240, 300 + (deepest_row + 1) * 200 + 60]

    # ------------------------------------------------------------------ output

    def build(self) -> dict[str, Any]:
        self._add_dead_letter_tail()
        self._layout()
        connections: dict[str, Any] = {}
        for src, output, dst in self.links:
            groups = connections.setdefault(src, {"main": []})["main"]
            while len(groups) <= output:
                groups.append([])
            groups[output].append({"node": dst, "type": "main", "index": 0})
        settings: dict[str, Any] = {
            "executionOrder": "v1",
            "saveDataErrorExecution": "all",
            "saveManualExecutions": True,
            "executionTimeout": 300,
        }
        if not self.is_error_workflow:
            settings["errorWorkflow"] = ERROR_WORKFLOW_PLACEHOLDER
        return {
            "name": self.name,
            "nodes": self.nodes,
            "connections": connections,
            "active": False,
            "settings": settings,
            "pinData": {},
            "tags": [],
        }


def env_vars_used(workflow: dict[str, Any]) -> list[str]:
    import json
    found = set(re.findall(r"\$env\.([A-Z0-9_]+)", json.dumps(workflow)))
    return [name for name in ENV if name in found] + sorted(found - set(ENV))


def credentials_used(workflow: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for node in workflow["nodes"]:
        for ref in node.get("credentials", {}).values():
            if ref["name"] not in names:
                names.append(ref["name"])
    return names


def type_label(node_type: str) -> str:
    return TYPE_LABELS.get(node_type, node_type)

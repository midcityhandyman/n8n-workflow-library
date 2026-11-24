"""Unit tests for the validator rules. Every rule is shown to pass and to fail."""

import copy
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo"))

from breakages import BREAKAGES  # noqa: E402
from n8n_validator import graph, render, validate_file, validate_workflow  # noqa: E402
from n8n_validator.__main__ import main as cli  # noqa: E402
from n8n_validator.checks import RULES  # noqa: E402

GOOD = json.loads((ROOT / "workflows/01-lead-intake/workflow.json").read_text(encoding="utf-8"))


def rules_for(wf) -> set[str]:
    return {finding.rule for finding in validate_workflow(wf)}


def broken(change) -> set[str]:
    wf = copy.deepcopy(GOOD)
    change(wf)
    return rules_for(wf)


def http_node(wf):
    return next(n for n in wf["nodes"] if n["name"] == "Upsert CRM contact")


class BreakageTests(unittest.TestCase):
    def test_good_workflow_is_clean(self):
        self.assertEqual(validate_workflow(GOOD), [])

    def test_each_breakage_reports_its_rule(self):
        for title, expected, damage in BREAKAGES:
            with self.subTest(title):
                self.assertIn(expected, broken(damage))

    def test_breakages_cover_every_rule_family(self):
        covered = {expected for _, expected, _ in BREAKAGES}
        self.assertTrue({"ERR-01", "ERR-02", "ERR-03", "ERR-04", "CRED-01", "CRED-02", "CRED-03"} <= covered)
        self.assertTrue(covered <= set(RULES))


class StructureTests(unittest.TestCase):
    def test_not_an_object(self):
        self.assertEqual(rules_for([]), {"STRUCT-01"})

    def test_missing_top_level_keys(self):
        self.assertEqual(rules_for({"name": "x"}), {"STRUCT-01"})
        self.assertIn("STRUCT-01", rules_for({"name": "x", "nodes": [], "connections": {}, "settings": {}}))

    def test_node_field_problems(self):
        def damage(wf):
            node = wf["nodes"][1]
            node["typeVersion"] = "2"
            node["position"] = [1]
            del node["parameters"]
            node["onError"] = "ignore"
        self.assertIn("STRUCT-02", broken(damage))

    def test_duplicate_id(self):
        self.assertIn("STRUCT-03", broken(lambda wf: wf["nodes"][1].update(id=wf["nodes"][0]["id"])))

    def test_malformed_connection_shape(self):
        self.assertIn("STRUCT-04", broken(lambda wf: wf["connections"].update({"Lead webhook": {"main": "x"}})))
        self.assertIn("STRUCT-04", broken(lambda wf: wf["connections"]["Lead webhook"]["main"][0][0].pop("type")))

    def test_no_trigger_and_orphan(self):
        def no_trigger(wf):
            wf["nodes"][0]["type"] = "n8n-nodes-base.noOp"
        self.assertIn("STRUCT-05", broken(no_trigger))

        def orphan(wf):
            wf["nodes"].append({"id": "x", "name": "Left over", "type": "n8n-nodes-base.noOp",
                                "typeVersion": 1, "position": [0, 0], "parameters": {}})
        self.assertIn("STRUCT-05", broken(orphan))

    def test_execution_order(self):
        self.assertIn("STRUCT-06", broken(lambda wf: wf["settings"].pop("executionOrder")))

    def test_static_data(self):
        self.assertIn("STRUCT-07", broken(lambda wf: wf.update(staticData={"lastId": 7})))


class ErrorHandlingTests(unittest.TestCase):
    def test_retry_bounds(self):
        for key, value in (("maxTries", 1), ("maxTries", 9), ("waitBetweenTries", 0), ("waitBetweenTries", 60000)):
            with self.subTest(key=key, value=value):
                self.assertIn("ERR-01", broken(lambda wf: http_node(wf).update({key: value})))

    def test_node_with_credentials_is_guarded_even_if_type_is_unknown(self):
        def damage(wf):
            node = http_node(wf)
            node["type"] = "n8n-nodes-base.someNewIntegration"
            node["retryOnFail"] = False
        self.assertIn("ERR-01", broken(damage))

    def test_continue_regular_output_is_not_an_error_branch(self):
        self.assertIn("ERR-02", broken(lambda wf: http_node(wf).update(onError="continueRegularOutput")))

    def test_dead_letter_must_store_not_just_format(self):
        def damage(wf):
            store = next(n for n in wf["nodes"] if n["name"] == "Dead letter: store failed item")
            store["name"] = "Send somewhere"
            wf["connections"]["Send somewhere"] = wf["connections"].pop("Dead letter: store failed item")
            wf["connections"]["Dead letter: build record"]["main"][0][0]["node"] = "Send somewhere"
            alert = next(n for n in wf["nodes"] if n["name"] == "Dead letter: alert owner")
            alert["name"] = "Alert"
            wf["connections"]["Send somewhere"]["main"][0][0]["node"] = "Alert"
        self.assertIn("ERR-03", broken(damage))

    def test_no_error_branch_at_all(self):
        def damage(wf):
            for node in wf["nodes"]:
                if node.get("onError") == "continueErrorOutput":
                    wf["connections"][node["name"]]["main"] = wf["connections"][node["name"]]["main"][:1]
            wf["connections"]["Has a way to reach them?"]["main"][1] = []
        found = broken(damage)
        self.assertIn("ERR-05", found)

    def test_error_trigger_workflow_needs_no_error_workflow_setting(self):
        wf = json.loads((ROOT / "workflows/10-error-alerting/workflow.json").read_text(encoding="utf-8"))
        self.assertNotIn("errorWorkflow", wf["settings"])
        self.assertEqual(validate_workflow(wf), [])

    def test_if_node_error_output_is_the_third_output(self):
        node = {"name": "Check", "type": "n8n-nodes-base.if", "onError": "continueErrorOutput"}
        wf = {"connections": {"Check": {"main": [[], [], [{"node": "Dead letter: x", "type": "main", "index": 0}]]}}}
        self.assertEqual(graph.error_targets(wf, node), ["Dead letter: x"])
        self.assertEqual(graph.regular_targets(wf, node), [[], []])


class CredentialTests(unittest.TestCase):
    def secret_in_code(self, value):
        def damage(wf):
            wf["nodes"][1]["parameters"]["jsCode"] += f"\nconst leftover = '{value}';\n"
        return broken(damage)

    def test_secret_shapes_are_found_anywhere(self):
        # assembled at run time so no secret shaped literal lives in the repository
        shapes = {
            "aws": "AKIA" + "Q" * 16,
            "slack": "xox" + "b-" + "1234567890-abcdefghij",
            "sk": "sk" + "-" + "a1B2" * 8,
            "payment": "sk_" + "live_" + "a1B2c3D4" * 3,
            "github": "gh" + "p_" + "a1B2c3" * 6,
            "sid": "A" + "C" + "0123456789abcdef" * 2,
            "google": "AI" + "za" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R",
            "jwt": ".".join(["ey" + "J" + "hbGciOiJIUzI1NiJ9", "ey" + "J" + "zdWIiOiIxMjM0NTY3ODkwIn0", "abcDEF123456"]),
            "pem": "-----BEGIN " + "RSA PRIVATE KEY-----",
            "basic": "Basic " + "dXNlcjpwYXNzd29yZDEyMw==",
            "url login": "https://user:" + "hunter2222@crm.example.com/api",
        }
        for label, value in shapes.items():
            with self.subTest(label):
                self.assertIn("CRED-02", self.secret_in_code(value))

    def test_secret_outside_nodes_is_found(self):
        fake = "xox" + "p-" + "1234567890-abcdefghij"
        self.assertIn("CRED-02", broken(lambda wf: wf.update(meta={"note": fake})))

    def test_literal_in_sensitive_field(self):
        self.assertIn("CRED-03", broken(lambda wf: http_node(wf)["parameters"].update(apiKey="plain-text-value")))
        self.assertIn("CRED-03", broken(lambda wf: http_node(wf)["parameters"].update(
            queryParameters={"parameters": [{"name": "access_token", "value": "plain-text-value"}]})))

    def test_expressions_and_placeholders_are_allowed(self):
        def allowed(wf):
            params = http_node(wf)["parameters"]
            params["apiKey"] = "={{ $env.CRM_KEY }}"
            params["token"] = "REPLACE_ME"
            params["headerParameters"] = {"parameters": [{"name": "Authorization", "value": "={{ $credentials.key }}"}]}
        self.assertEqual(broken(allowed), set())

    def test_findings_never_echo_the_secret(self):
        fake = "sk" + "-" + "Zz9Yy8Xx" * 4
        wf = copy.deepcopy(GOOD)
        wf["nodes"][1]["parameters"]["jsCode"] += f"\n// {fake}\n"
        messages = " ".join(f.message for f in validate_workflow(wf))
        self.assertNotIn(fake, messages)


class FileAndCliTests(unittest.TestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = cli(list(args))
        return code, out.getvalue()

    def test_invalid_json_and_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "workflow.json"
            bad.write_text("{ not json", encoding="utf-8")
            self.assertEqual([f.rule for f in validate_file(bad)], ["STRUCT-01"])
            self.assertEqual([f.rule for f in validate_file(Path(tmp) / "nope.json")], ["STRUCT-01"])

    def test_cli_exit_codes(self):
        code, text = self.run_cli("validate", str(ROOT / "workflows"))
        self.assertEqual(code, 0, text)
        self.assertIn("10 of 10 workflows passed", text)
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(self.run_cli("validate", tmp)[0], 2)
            wf = copy.deepcopy(GOOD)
            wf["settings"].pop("errorWorkflow")
            (Path(tmp) / "workflow.json").write_text(json.dumps(wf), encoding="utf-8")
            code, text = self.run_cli("validate", tmp, "--json")
            self.assertEqual(code, 1)
            (findings,) = json.loads(text).values()
            self.assertEqual([f["rule"] for f in findings], ["ERR-04"])

    def test_trace_and_rules_commands(self):
        code, text = self.run_cli("trace", str(ROOT / "workflows/03-estimate-follow-up"))
        self.assertEqual(code, 0)
        self.assertIn("Send follow up text (x3, 2000 ms apart) =error=> Dead letter: build record", text)
        code, text = self.run_cli("rules")
        self.assertEqual(text.count("\n"), len(RULES))

    def test_mermaid_marks_error_edges_and_dead_letter_nodes(self):
        chart = render.mermaid(GOOD)
        self.assertTrue(chart.startswith("flowchart LR"))
        self.assertIn("-. error .->", chart)
        self.assertIn("-- no -->", chart)
        self.assertIn("classDef dead", chart)


if __name__ == "__main__":
    unittest.main()

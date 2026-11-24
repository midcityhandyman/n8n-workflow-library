"""Tests over the shipped library: the ten workflows, their docs and the repo hygiene rules."""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import build_workflows  # noqa: E402
from n8n_validator import graph, render, validate_file  # noqa: E402

FOLDERS = sorted(p for p in (ROOT / "workflows").iterdir() if p.is_dir())
EXPECTED = ["01-lead-intake", "02-missed-call-text-back", "03-estimate-follow-up", "04-invoice-reminder",
            "05-review-request", "06-daily-owner-digest", "07-new-hire-onboarding", "08-form-to-crm",
            "09-calendar-sync", "10-error-alerting"]
TEXT_SUFFIXES = {".md", ".py", ".json", ".yml", ".txt", ".mjs", ".example", ".gitignore"}


def load(folder: Path) -> dict:
    return json.loads((folder / "workflow.json").read_text(encoding="utf-8"))


def repo_text_files():
    skip = {".venv", "venv", "node_modules", "__pycache__", ".git"}
    for path in ROOT.rglob("*"):
        if path.is_file() and not skip & set(path.parts) and (path.suffix in TEXT_SUFFIXES or path.name in TEXT_SUFFIXES):
            yield path


class LibraryTests(unittest.TestCase):
    def test_the_ten_workflows_exist(self):
        self.assertEqual([f.name for f in FOLDERS], EXPECTED)
        for folder in FOLDERS:
            for name in ("workflow.json", "README.md", "sample.json"):
                self.assertTrue((folder / name).is_file(), f"{folder.name}/{name}")

    def test_every_workflow_passes_the_validator(self):
        for folder in FOLDERS:
            with self.subTest(folder.name):
                self.assertEqual([str(f) for f in validate_file(folder / "workflow.json")], [])

    def test_every_workflow_has_retry_error_branch_and_dead_letter(self):
        for folder in FOLDERS:
            with self.subTest(folder.name):
                wf = load(folder)
                guarded = [n for n in wf["nodes"] if graph.error_targets(wf, n)]
                self.assertGreaterEqual(len(guarded), 2)
                for node in guarded:
                    self.assertTrue(node["retryOnFail"])
                    self.assertEqual((node["maxTries"], node["waitBetweenTries"]), (3, 2000))
                names = [n["name"] for n in wf["nodes"]]
                self.assertIn("Dead letter: build record", names)
                self.assertIn("Dead letter: store failed item", names)
                self.assertEqual(sum(1 for n in names if n.startswith("Dead letter: ")), 3)

    def test_error_workflow_wiring(self):
        for folder in FOLDERS:
            wf = load(folder)
            types = {n["type"] for n in wf["nodes"]}
            if folder.name == "10-error-alerting":
                self.assertIn("n8n-nodes-base.errorTrigger", types)
            else:
                self.assertEqual(wf["settings"]["errorWorkflow"], "REPLACE_WITH_ERROR_WORKFLOW_ID")

    def test_credentials_are_placeholders_only(self):
        for folder in FOLDERS:
            for node in load(folder)["nodes"]:
                for ref in node.get("credentials", {}).values():
                    self.assertEqual(set(ref), {"id", "name"})
                    self.assertEqual(ref["id"], "REPLACE_WITH_CREDENTIAL_ID")

    def test_generated_files_are_in_sync_with_the_specs(self):
        for path, text in build_workflows.outputs().items():
            with self.subTest(path.relative_to(ROOT).as_posix()):
                self.assertTrue(path.exists())
                self.assertEqual(path.read_text(encoding="utf-8"), text,
                                 "run python tools/build_workflows.py")

    def test_each_readme_has_the_current_diagram_and_sections(self):
        for folder in FOLDERS:
            with self.subTest(folder.name):
                text = (folder / "README.md").read_text(encoding="utf-8")
                self.assertIn("```mermaid\n" + render.mermaid(load(folder)) + "\n```", text)
                for section in ("## Trigger", "## Flow", "## Nodes", "## Error handling", "## Configuration", "## Import"):
                    self.assertIn(section, text)

    def test_env_example_covers_every_variable_the_workflows_read(self):
        declared = set(re.findall(r"^([A-Z0-9_]+)=", (ROOT / ".env.example").read_text(encoding="utf-8"), re.M))
        used = set()
        for folder in FOLDERS:
            used |= set(re.findall(r"\$env\.([A-Z0-9_]+)", (folder / "workflow.json").read_text(encoding="utf-8")))
        self.assertEqual(used - declared, set())
        self.assertEqual(declared - used, set(), "unused variable in .env.example")

    def test_node_ids_are_unique_across_the_library(self):
        seen = {}
        for folder in FOLDERS:
            for node in load(folder)["nodes"]:
                self.assertNotIn(node["id"], seen)
                seen[node["id"]] = folder.name

    def test_sample_data_is_synthetic(self):
        for path in repo_text_files():
            text = path.read_text(encoding="utf-8")
            for email in re.findall(r"[\w.+-]+@[\w-]+\.[\w.-]+", text):
                self.assertTrue(email.lower().rstrip(".").endswith("example.com"),
                                f"{path.name}: {email}")
            for phone in re.findall(r"\+1\d{10}\b", text):
                self.assertRegex(phone, r"^\+155555501\d\d$", path.name)

    def test_no_em_dashes_anywhere(self):
        for path in repo_text_files():
            self.assertNotIn(chr(0x2014), path.read_text(encoding="utf-8"), path.relative_to(ROOT).as_posix())

    def test_top_level_readme_links_every_workflow(self):
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in EXPECTED:
            self.assertIn(f"workflows/{name}/", text)
        self.assertIn("```mermaid", text)
        self.assertIn("License: MIT", text)


if __name__ == "__main__":
    unittest.main()

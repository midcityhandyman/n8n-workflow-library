"""Runs the JavaScript inside the Code nodes with Node.js and checks the results.

Skipped when Node.js is not installed. These tests execute the same source text
that n8n would, through a small shim (tests/harness.mjs), not inside n8n itself.
"""

import json
import shutil
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def days_ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def run_code(folder: str, node_name: str, items=None, upstream=None, hour=10):
    wf = json.loads((ROOT / "workflows" / folder / "workflow.json").read_text(encoding="utf-8"))
    code = next(n for n in wf["nodes"] if n["name"] == node_name)["parameters"]["jsCode"]
    payload = {"code": code, "items": [{"json": i} for i in (items or [])], "hour": hour,
               "upstream": {k: [{"json": v}] for k, v in (upstream or {}).items()}}
    done = subprocess.run([NODE, str(ROOT / "tests" / "harness.mjs")], input=json.dumps(payload),
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    if done.returncode != 0:
        raise AssertionError(done.stderr)
    return [item["json"] for item in json.loads(done.stdout)]


def sample(folder: str):
    return json.loads((ROOT / "workflows" / folder / "sample.json").read_text(encoding="utf-8"))


@unittest.skipIf(NODE is None, "Node.js is not installed")
class CodeNodeTests(unittest.TestCase):
    def test_every_code_node_and_expression_parses(self):
        scripts = []
        for path in sorted((ROOT / "workflows").glob("*/workflow.json")):
            for node in json.loads(path.read_text(encoding="utf-8"))["nodes"]:
                if node["type"].endswith(".code"):
                    scripts.append((node["name"], f"async function f() {{\n{node['parameters']['jsCode']}\n}}"))
                body = node["parameters"].get("jsonBody")
                if body:
                    self.assertTrue(body.startswith("={{ ") and body.endswith(" }}"), node["name"])
                    scripts.append((node["name"], f"const f = () => ({body[4:-3]});"))
        self.assertGreater(len(scripts), 40)
        for name, script in scripts:
            done = subprocess.run([NODE, "--check", "-"], input=script, capture_output=True, text=True,
                                  encoding="utf-8", timeout=60)
            self.assertEqual(done.returncode, 0, f"{name}: {done.stderr}")

    def test_lead_is_normalized(self):
        (lead,) = run_code("01-lead-intake", "Normalize lead", [{"body": sample("01-lead-intake")}])
        self.assertEqual(lead["phone"], "+15555550142")
        self.assertEqual(lead["email"], "alex.sample@example.com")
        self.assertTrue(lead["hasContact"] and lead["hasPhone"])
        self.assertTrue(lead["dedupeKey"].startswith("+15555550142:"))

    def test_lead_without_contact_details_is_flagged(self):
        (lead,) = run_code("01-lead-intake", "Normalize lead", [{"name": "Alex Sample", "phone": "12"}])
        self.assertFalse(lead["hasContact"])

    def test_missed_call_rules(self):
        call = sample("02-missed-call-text-back")
        (missed,) = run_code("02-missed-call-text-back", "Normalize call event", [call], hour=20)
        self.assertTrue(missed["shouldText"] and missed["afterHours"])
        (answered,) = run_code("02-missed-call-text-back", "Normalize call event", [{**call, "status": "completed"}])
        self.assertFalse(answered["shouldText"] or answered["afterHours"])
        (outbound,) = run_code("02-missed-call-text-back", "Normalize call event", [{**call, "direction": "outbound"}])
        self.assertFalse(outbound["shouldText"])

    def test_estimate_cadence(self):
        def est(id_, sent_days, sent=0, **more):
            return {"id": id_, "customerName": "Alex Sample", "phone": "+15555550142",
                    "sentAt": days_ago(sent_days), "followUpsSent": sent, **more}
        rows = [est("too-soon", 1), est("first", 2.5), est("already-done", 3, 1), est("second", 6, 1),
                est("last", 11, 2), est("finished", 30, 3), est("opted-out", 5, optedOut=True)]
        due = run_code("03-estimate-follow-up", "Pick follow ups due today", [{"results": rows}])
        self.assertEqual([(d["estimateId"], d["step"], d["isLast"]) for d in due],
                         [("first", 1, False), ("second", 2, False), ("last", 3, True)])

    def test_invoice_tiers(self):
        def inv(id_, overdue_days, last=0, **more):
            return {"id": id_, "number": id_, "customerName": "Alex Sample", "email": "alex.sample@example.com",
                    "balance": 100, "dueDate": days_ago(overdue_days), "lastReminderTier": last, **more}
        rows = [inv("not-yet", -10), inv("heads-up", -2), inv("gentle", 2, 1), inv("gentle-sent", 2, 2),
                inv("firm", 8, 2), inv("final", 30, 3), inv("final-sent", 30, 4), inv("disputed", 30, disputed=True)]
        due = run_code("04-invoice-reminder", "Choose reminder tier", [{"results": rows}])
        self.assertEqual([(d["invoiceId"], d["tier"], d["isFinal"]) for d in due],
                         [("heads-up", 1, False), ("gentle", 2, False), ("firm", 3, False), ("final", 4, True)])

    def test_review_eligibility(self):
        job = sample("05-review-request")
        cases = [({}, ""), ({"paid": False}, "job is not paid yet"), ({"openComplaint": True}, "open complaint on the job"),
                 ({"lastReviewRequestAt": days_ago(10)}, "asked recently"), ({"lastReviewRequestAt": days_ago(120)}, ""),
                 ({"smsOptOut": True}, "customer opted out of texts")]
        for change, reason in cases:
            (out,) = run_code("05-review-request", "Check eligibility", [{"body": {**job, **change}}])
            self.assertEqual((out["reason"], out["eligible"]), (reason, reason == ""), change)

    def test_digest_counts_and_totals(self):
        data = sample("06-daily-owner-digest")
        (digest,) = run_code("06-daily-owner-digest", "Compose digest", upstream={
            "Fetch leads from today": data["leads"], "Fetch jobs for tomorrow": data["jobs"],
            "Fetch unpaid invoices": data["invoices"]})
        self.assertEqual((digest["leadCount"], digest["jobCount"], digest["unpaidCount"]), (1, 1, 1))
        self.assertIn("Unpaid invoices: 1 worth $1,250", digest["text"])
        self.assertIn("Alex Sample (bathroom remodel, via website)", digest["text"])

    def test_onboarding_plan(self):
        hire = sample("07-new-hire-onboarding")
        (plan,) = run_code("07-new-hire-onboarding", "Build onboarding plan", [{"body": hire}])
        self.assertTrue(plan["complete"])
        self.assertEqual(len(plan["tasks"]), 7)
        self.assertEqual(plan["tasks"][0]["dueDate"], "2030-01-31")
        (office,) = run_code("07-new-hire-onboarding", "Build onboarding plan", [{**hire, "role": "office admin"}])
        self.assertEqual(len(office["tasks"]), 4)
        (bad,) = run_code("07-new-hire-onboarding", "Build onboarding plan", [{**hire, "startDate": "soon"}])
        self.assertFalse(bad["complete"])
        self.assertEqual(bad["tasks"], [])

    def test_form_mapping_and_spam_checks(self):
        form = sample("08-form-to-crm")
        (ok,) = run_code("08-form-to-crm", "Map form fields", [{"body": form}])
        self.assertEqual((ok["name"], ok["phone"], ok["service"], ok["looksHuman"]),
                         ("Alex Sample", "+15555550142", "kitchen", True))
        for change in ({"website": "filled by a bot"}, {"email_address": "nope"}, {"details": "see http://spam.example.com"}):
            (spam,) = run_code("08-form-to-crm", "Map form fields", [{**form, **change}])
            self.assertFalse(spam["looksHuman"], change)

    def test_calendar_mapping(self):
        data = sample("09-calendar-sync")
        job = data["results"][0]
        rows = [job, {**job, "id": "job-3002", "status": "cancelled", "calendarEventId": "evt-9"},
                {**job, "id": "job-3003", "scheduledStart": None}]
        events = run_code("09-calendar-sync", "Map jobs to calendar events", [{"results": rows}])
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["event"]["end"], "2030-02-04T15:00:00.000Z")
        self.assertFalse(events[0]["hasEvent"])
        self.assertTrue(events[1]["hasEvent"])
        self.assertTrue(events[1]["event"]["title"].startswith("CANCELLED: "))

    def test_error_alert_format(self):
        (alert,) = run_code("10-error-alerting", "Format alert", [sample("10-error-alerting")])
        self.assertTrue(alert["urgent"])
        self.assertIn('Workflow "Lead Intake" failed at "Dead letter: alert owner"', alert["text"])
        (trigger_failure,) = run_code("10-error-alerting", "Format alert", [
            {"trigger": {"error": {"message": "webhook could not register"}}, "workflow": {"name": "Calendar Sync"}}])
        self.assertEqual((trigger_failure["lastNode"], trigger_failure["urgent"]), ("(trigger)", False))
        self.assertIn("webhook could not register", trigger_failure["text"])


if __name__ == "__main__":
    unittest.main()

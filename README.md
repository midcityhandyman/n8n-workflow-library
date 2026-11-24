# n8n workflow library for service businesses

Eleven automations a service business runs on: answer every lead, text back missed calls, chase estimates and invoices, ask for reviews, keep the crew calendar current, and stop texting anyone who replies STOP. Each one is built so that when something breaks, the failure is retried, saved and reported to a person instead of vanishing quietly.

**If you own the business:** a normal automation fails silently. The CRM has a bad minute, a lead never gets saved, and nobody finds out. Here every step that talks to another system tries three times, and if it still fails the lead (or invoice, or job) is stored and someone gets a message saying exactly what failed.

**If you build in n8n:** this repo holds eleven importable `workflow.json` files, a doc page with a diagram for each, synthetic sample input, and a Python validator that enforces the retry, error output and dead letter pattern, plus an opt-out line and an opt-out check on every customer text, in CI. All eleven were imported into a local n8n 2.48.8 and run against a mock API. What that run did and did not cover is under [What was verified](#what-was-verified).

| # | Workflow | Trigger | Business result |
| --- | --- | --- | --- |
| 01 | [Lead Intake](workflows/01-lead-intake/) | Webhook | Every new lead is saved to the CRM, gets a text straight away, and the office is told |
| 02 | [Missed Call Text Back](workflows/02-missed-call-text-back/) | Webhook | A caller you missed gets a text back, at most once an hour |
| 03 | [Estimate Follow Up](workflows/03-estimate-follow-up/) | Schedule | Unanswered estimates get a nudge at 2, 5 and 10 days |
| 04 | [Invoice Reminder](workflows/04-invoice-reminder/) | Schedule | Unpaid invoices get a four step reminder ladder; final notices are flagged to the owner |
| 05 | [Review Request](workflows/05-review-request/) | Webhook | Customers are asked for a review only when the job is paid and complaint free |
| 06 | [Daily Owner Digest](workflows/06-daily-owner-digest/) | Schedule | One evening email: today's leads, tomorrow's jobs, money owed |
| 07 | [New Hire Onboarding](workflows/07-new-hire-onboarding/) | Webhook | A new hire gets an HR record, a dated checklist and a welcome email; the manager is told |
| 08 | [Form to CRM](workflows/08-form-to-crm/) | Webhook | Website form entries become CRM contacts and deals; spam is dropped and an existing contact is updated, not duplicated |
| 09 | [Calendar Sync](workflows/09-calendar-sync/) | Schedule | Job changes reach the crew calendar within 15 minutes |
| 10 | [Error Alerting](workflows/10-error-alerting/) | Error Trigger | Any failed run in the other ten reaches a person, by chat and by text when a customer is affected |
| 11 | [SMS Opt-Out](workflows/11-sms-opt-out/) | Webhook | A customer who replies STOP is put on the opt-out list, told once, and not texted again by workflows 01, 02, 03 and 05 until they reply START |

## What you will see

The validator, run on this repo (Python 3.10 or newer, nothing to install):

```text
$ python -m n8n_validator validate workflows
PASS  workflows/01-lead-intake/workflow.json
PASS  workflows/02-missed-call-text-back/workflow.json
PASS  workflows/03-estimate-follow-up/workflow.json
PASS  workflows/04-invoice-reminder/workflow.json
PASS  workflows/05-review-request/workflow.json
PASS  workflows/06-daily-owner-digest/workflow.json
PASS  workflows/07-new-hire-onboarding/workflow.json
PASS  workflows/08-form-to-crm/workflow.json
PASS  workflows/09-calendar-sync/workflow.json
PASS  workflows/10-error-alerting/workflow.json
PASS  workflows/11-sms-opt-out/workflow.json

11 of 11 workflows passed, 18 rules each
```

Where a failed item goes, read straight from the JSON:

```text
$ python -m n8n_validator trace workflows/01-lead-intake
Lead Intake  (workflows/01-lead-intake/workflow.json)
  main path : Lead webhook -> Normalize lead -> Has a way to reach them? -> Upsert CRM contact -> Has a mobile number? -> Check opt-out list -> Still allowed to text? -> Text acknowledgement to lead -> Notify office channel
  on failure: Upsert CRM contact (x3, 2000 ms apart) =error=> Dead letter: build record -> Dead letter: store failed item -> Dead letter: alert owner
  on failure: Check opt-out list (x3, 2000 ms apart) =error=> Dead letter: build record -> Dead letter: store failed item -> Dead letter: alert owner
  on failure: Text acknowledgement to lead (x3, 2000 ms apart) =error=> Dead letter: build record -> Dead letter: store failed item -> Dead letter: alert owner
  on failure: Notify office channel (x3, 2000 ms apart) =error=> Dead letter: build record -> Dead letter: store failed item -> Dead letter: alert owner
  last resort: settings.errorWorkflow = REPLACE_WITH_ERROR_WORKFLOW_ID
```

And the same thing happening in n8n. This is the mock API's request log from the first local run, with the CRM mock set to return HTTP 500 and the sample lead posted to the webhook (payload shortened here):

```text
500 POST /crm/contacts/upsert
500 POST /crm/contacts/upsert
500 POST /crm/contacts/upsert
200 POST /ops/dead-letters  {"workflow":"Lead Intake","executionId":"14","failedNode":"Upsert CRM contact","error":"500 - ...","payload":"{\"name\":\"Alex Sample\",\"phone\":\"+15555550142\", ...}","failedAt":"2026-11-24T10:17:03.634-05:00"}
200 POST /chat/alerts       {"text":"[Lead Intake] failed at Upsert CRM contact: 500 - ... (execution 14)"}
```

Three tries, then the lead is stored for replay and a person is told.

And an opt-out, from the second local run. The sample for workflow 11 (a text reading ` Stop `) is posted to its webhook, then the same number sends STOP again, then the sample lead for that number arrives at Lead Intake:

```text
--- reply " Stop " from +15555550142
200 POST /ops/sms-opt-outs/search  {"phone": "+15555550142"}
200 POST /ops/sms-opt-outs         {"phone": "+15555550142", "optedOut": true, "keyword": "STOP", "source": "sms_reply", "messageId": "msg-0001", "changedAt": "2026-11-24T11:05:32.016-05:00"}
200 POST /sms/messages             {"to": "+15555550142", "from": "+15555550100", "body": "You are unsubscribed from Sample Home Services texts and will get no more messages. Reply START to resubscribe."}
--- reply "STOP" again
200 POST /ops/sms-opt-outs/search  {"phone": "+15555550142"}
--- new lead from the same number
200 POST /crm/contacts/upsert      {"name": "Alex Sample", "phone": "+15555550142", ...}
200 POST /ops/sms-opt-outs/search  {"phone": "+15555550142"}
200 POST /chat/office              {"text": "New lead: Alex Sample wants bathroom remodel (via website)"}
```

One confirmation, none for the repeat, and the lead is saved and announced without a text. The Lead Intake execution ends with `reason: "number is on the SMS opt-out list"` on the item.

## Import one in two minutes

With Docker, from the repo root:

```bash
docker run -d --name n8n-try -p 5678:5678 \
  --env-file .env.example -e N8N_BLOCK_ENV_ACCESS_IN_NODE=false \
  -v "$PWD/workflows:/wf:ro" docker.n8n.io/n8nio/n8n

# wait until `docker logs n8n-try` shows "Editor is now accessible", then:
docker exec n8n-try n8n import:workflow --input=/wf/10-error-alerting/workflow.json
docker exec n8n-try n8n import:workflow --input=/wf/11-sms-opt-out/workflow.json
docker exec n8n-try n8n import:workflow --input=/wf/01-lead-intake/workflow.json
```

Each import prints `Successfully imported 1 workflow.` Open http://localhost:5678 and complete the owner setup screen to see them in the editor. Remove it all with `docker rm -f n8n-try`.

Already have n8n running? Open a new workflow, then the three dot menu > Import from File, and pick a `workflow.json`.

To make an imported workflow do real work:

1. Point the variables in `.env.example` at your own systems. They ship as `example.com` placeholders. `OPT_OUT_URL` has to point at a real store before any workflow that texts customers will send: a lookup that fails stops the text.
2. Create the Header Auth credentials named in the workflow's README and select them on the nodes that show a warning.
3. In Lead Intake, under Settings > Error workflow, pick Error Alerting.
4. Publish both (activate on n8n 1.x) and post the sample:

```bash
curl -X POST -H "content-type: application/json" \
  --data @workflows/01-lead-intake/sample.json \
  http://localhost:5678/webhook/lead-intake
```

Import workflow 10 first so it is there to pick in step 3, and publish workflow 11 before any workflow that texts customers so a STOP reply has somewhere to go. Each workflow folder has its own README with the node table, variables and tuning notes.

## What was verified

Two manual runs on 2026-11-24, neither part of CI.

**First run, the original ten workflows:**

- **Import.** All ten shipped files, unmodified, imported with `n8n import:workflow` into n8n 2.48.8 in Docker. The Docker commands above, as they stood before the line for workflow 11 was added, were run as written against n8n 2.49.5.
- **Execution.** Each of the ten then ran once in that instance against a local mock API, using its `sample.json` data. For this the test copies had the two placeholders filled in (a test credential id and the id of workflow 10), and the four scheduled workflows had their cron set to every minute so they would fire. Nothing else was changed.
- **Failure paths.** Lead Intake was run with the CRM mock returning 500 (log above), with a lead that has no phone or email (went to the dead letter store and the alerts channel), and with the alerts channel down as well, which failed the execution and started workflow 10. Workflow 10's first alert attempt failed while the channel was still down, its retry succeeded once the mock was switched back, and the owner text went out.

**Second run, the opt-out change.** A fresh n8n 2.48.8 container and a mock API with an in-memory opt-out store, both removed afterwards:

- **Import.** The six files this change touched (workflows 01, 02, 03, 05, 10 and 11), unmodified, imported with `n8n import:workflow`.
- **Execution.** Test copies of the same six had the two placeholders filled in and were published with `n8n publish:workflow`; workflow 03 also had its cron set to every minute. Nothing else was changed. `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` was set.
- **STOP path (workflow 11).** Its `sample.json` recorded the opt-out and sent one confirmation (log above). A second STOP sent nothing and wrote nothing. `start`, sent from a number written as `(555) 555-0142`, recorded the opt-in and sent one confirmation; a following UNSTOP sent nothing. An ordinary reply (`please stop by on Tuesday`) made no outside call. `UNSUBSCRIBE` from a sender that is not a phone number went to the dead letter store and the alerts channel. With the opt-out store returning 500, `Stop All` was tried three times, then stored as a dead letter and alerted, with no confirmation sent.
- **Suppressed sends (workflows 01, 02, 03, 05).** With the number opted out, each ran with its sample data (02 with the caller number changed to the opted out one) and sent no text: 01 still saved the lead and posted to the office channel, 02 and 05 stopped after the lookup, and 03, given one opted out and one other estimate in the same run, texted only the other one. All four executions carry the skip reason. After the opt-in, 01, 02 and 05 ran again and each sent its text, ending in `Reply STOP to opt out.`
- **Store down (workflow 01).** With the opt-out store returning 500 the lead was saved, the lookup was tried three times, no text was sent, and the failure went to the dead letter store and the alerts channel.
- **Not exercised in this run:** workflow 10 was imported and published as the error workflow but nothing made it fire, so its one change (workflow 11 added to the list that also texts the owner) is covered by a unit test only. The send path of 03 for the last step wording, the after hours wording of 02, and the store down path of 02, 03 and 05 were not run. The `$vars` swap described under Limitations was not run.

Not verified in either run: the editor's Import from File menu (the server CLI was used), n8n Cloud, n8n 1.x, any real CRM, SMS provider, carrier, email, accounting or calendar service, and the failure paths of workflows 04 and 06 to 09 inside n8n. Those are covered only by the validator and the unit tests. Workflows 04 and 06 to 09 were not re-run after the opt-out change because their `workflow.json` files did not change.

## The pattern every workflow follows

```mermaid
flowchart LR
    T(["Trigger"]) --> V["Validate and normalize"]
    V --> A["Outside call<br/>3 tries, 2 s apart"]
    A --> B["Next outside call<br/>3 tries, 2 s apart"]
    A -. error output .-> D1
    B -. error output .-> D1
    V -. invalid input .-> D1
    subgraph DL["Dead letter path"]
        D1["Build record<br/>workflow, node, error, item"] --> D2["Store failed item<br/>for replay"]
        D2 --> D3["Alert a person"]
    end
    D3 -. "alert fails too" .-> EW(["Workflow 10<br/>Error Alerting"])
    EW --> CH["Alerts channel"]
    EW --> SMS["Text owner<br/>if customer facing"]
```

1. **Retry.** Nodes that call outside systems have `retryOnFail: true`, `maxTries: 3`, `waitBetweenTries: 2000`.
2. **Error branch.** The same nodes have `onError: "continueErrorOutput"`. A failed item leaves through the extra output and the rest of the run carries on.
3. **Dead letter path.** Failed items are shaped into a record (workflow, failed node, error, original item), posted to a dead letter store, and a person is told.
4. **Last resort.** `settings.errorWorkflow` points at workflow 10, which runs when an execution fails outright, including when the dead letter alert itself cannot be sent.

Workflows that text a customer (01, 02, 03, 05 and the confirmation in 11) add two things:

5. **Opt-out line.** Every customer text ends with `Reply STOP to opt out.` The one exception is the confirmation that a number is unsubscribed, which says `Reply START to resubscribe.`
6. **Opt-out check.** Right before the text node, `Check opt-out list` asks the opt-out store about the number and `Still allowed to text?` lets the item through only if the store has no opt-out for it. An opted out number goes to `Skip text: number opted out`, which leaves `skipped`, `reason` and `phone` on the item. If the store cannot be reached after three tries the text is not sent and the item takes the dead letter path. Workflow 11 writes to the same store when a STOP or START reply comes in.

```mermaid
flowchart LR
    R(["Customer replies STOP"]) --> W11["Workflow 11<br/>classify keyword"]
    W11 --> ST[("Opt-out store")]
    W11 --> C["One confirmation text"]
    X["Workflow 01, 02, 03 or 05<br/>about to text"] --> L["Check opt-out list"]
    ST -.-> L
    L --> G{"Still allowed to text?"}
    G -- yes --> S["Send text<br/>ends with Reply STOP to opt out."]
    G -- no --> K["Skip, reason recorded"]
    L -. "store down" .-> D["Dead letter path<br/>no text sent"]
```

The workflows are vendor neutral, and that includes the opt-out store: `POST {OPT_OUT_URL}/search` with `{ phone }` returns `{ results: [...] }`, and `POST {OPT_OUT_URL}` with `{ phone, optedOut }` creates or updates an entry. Every outside call is a generic HTTP Request node pointed at a URL from an environment variable, with a named Header Auth credential. Swap in a native node after import if you like; keep the retry and error settings when you do.

## How the repo is put together

```mermaid
flowchart TB
    S["tools/specs.py<br/>eleven workflow descriptions"] --> K["tools/flowkit.py<br/>adds retries, error outputs,<br/>opt-out gate, dead letter tail, layout"]
    K --> G["tools/build_workflows.py"]
    G --> W["workflows/NN-name/workflow.json"]
    G --> R["workflows/NN-name/README.md<br/>with mermaid diagram"]
    G --> E[".env.example"]
    W --> VAL["n8n_validator<br/>STRUCT, ERR, CRED, SMS rules"]
    VAL --> CI["CI: validate, sync check,<br/>unit tests, demo"]
    W --> JS["tests/harness.mjs<br/>runs Code node JavaScript in Node.js"]
    JS --> CI
    W --> N8N(["Import into n8n"])
```

The JSON files are the product and import as they are. The generator exists so the shared tail, the opt-out gate and the docs cannot drift between eleven files; a test fails if the files on disk differ from what the specs produce.

## The validator

Standard library Python only.

| Family | Rules | Checks |
| --- | --- | --- |
| STRUCT | 01 to 08 | Top level shape, node fields, unique ids and names, connections point at real nodes, a trigger exists and reaches every node, `executionOrder` is v1, ships inactive with no pinned data, expression braces balance |
| ERR | 01 to 05 | Outside calls retry within n8n's limits, have a wired error output, every error output reaches a dead letter node that stores the item, the workflow names an error workflow |
| CRED | 01 to 03 | Credential blocks are references (id and name only), no string matches a known key or token shape, sensitive fields such as `Authorization` hold expressions and not literals |

| SMS | 01 to 02 | Every message variant of a customer facing text carries an opt-out line; every customer facing text can only be reached through the yes output of an If node fed by an opt-out store lookup, and the no output goes to a node that sets a `reason` field |

`python -m n8n_validator rules` prints all eighteen. Findings never echo a matched secret.

Conventions the ERR rules rely on: a node counts as an outside call if its type is in `IO_NODE_TYPES` (see `n8n_validator/checks.py`) or it has credentials, and a dead letter node is one whose name starts with `Dead letter`.

Conventions the SMS rules rely on: a node sends a text if it is an HTTP Request whose URL reads a variable named `SMS_..._URL`; it is customer facing unless its `to` is one of the business's own numbers (a variable with `OWNER`, `STAFF`, `TEAM` or `OFFICE` in the name); a node talks to the opt-out store if its URL reads a variable with `OPT_OUT` in the name. An opt-out line is the words `Reply STOP` (any case) in every string literal that makes up the message, so a ternary with the line in one branch only fails. A message built with no fixed wording at all fails too, because the line cannot be confirmed.

## Tests and demo

```bash
python -m unittest discover -s tests
python demo/run_demo.py
```

70 tests in three files:

- `tests/test_validator.py`: every rule passing and failing, by damaging copies of a good workflow. For the SMS rules that means planted violations: the line deleted, the line missing from one branch of a ternary, near miss wording, a wire drawn around the check, the text moved to the no output, the lookup removed or pointed at something else, and a skip that records no reason.
- `tests/test_library.py`: the eleven workflows validate clean, each has retries, an error branch and a three node dead letter path, the five customer text nodes are exactly the expected ones and each has the line and the check, generated files match the specs, `.env.example` matches what the workflows read, sample data is synthetic.
- `tests/test_code_nodes.py`: runs the JavaScript from the Code nodes in Node.js through a small shim and checks the business rules (follow up cadence, reminder tiers, review eligibility, spam checks, opt-out keywords in upper, lower and mixed case with stray whitespace, and so on). Also syntax checks every Code node and every JSON body expression. These tests are skipped if Node.js is not installed.

The demo is offline: no n8n, no keys. It validates the library, then breaks a copy of Lead Intake in fourteen realistic ways (retries off, error output unplugged, a Bearer header pasted from a cURL import, an API key in a URL, pinned test data left in, the opt-out line edited out, a wire drawn around the opt-out check, and so on) and shows the validator catching each one. Captured output is in [demo/output.txt](demo/output.txt). Excerpt:

```text
CAUGHT  Authorization header pasted in from a cURL import  (expects CRED-03: ...)
        CRED-03 [Upsert CRM contact]: parameters.headerParameters.parameters[0] sets 'Authorization' to a literal value
        CRED-02 [Upsert CRM contact]: looks like a literal Bearer token: Bearer...
```

CI (`.github/workflows/ci.yml`) runs the validator, the sync check, the tests and the demo. It does not start n8n.

## Where this pattern runs

The retry, error output, dead letter and alert pattern is drawn from automation that runs two small service companies day to day, where a dropped lead or a silent failure costs real work. The workflows here are clean rewrites with synthetic data and generic endpoints; nothing was copied from those systems.

## Limitations

- **Tested by hand in n8n, against a mock.** See [What was verified](#what-was-verified). Both runs were manual, on n8n 2.48.8, with mock endpoints. CI does not repeat them, so a later n8n release could break something and this repo would not notice. Run each workflow once with its sample input before you publish it.
- **`$env` access is blocked by default on n8n 2.x.** The workflows read URLs and business details from environment variables. n8n 2.0 changed the default of `N8N_BLOCK_ENV_ACCESS_IN_NODE` to `true` (listed in the n8n 2.0 breaking changes). On a default n8n 2.48.8 the run stops with `access to env vars denied`, and because the dead letter and alert nodes read `$env` too, nothing is stored or alerted. Set `N8N_BLOCK_ENV_ACCESS_IN_NODE=false` on a self hosted instance. The library still uses `$env` on purpose: the documented alternative, n8n Variables (`$vars.NAME`), is listed in the n8n docs (checked 2026-11-24) for Cloud Pro and Enterprise and for self hosted Business and Enterprise only, so switching would stop the files working on the free Community edition. If your plan has Variables, or you cannot set the flag (n8n Cloud), replace each `$env.NAME` with `$vars.NAME` and create the variables with the names from `.env.example` (they fit the Variables limits: letters, digits and underscores, values up to 1000 characters), or use fixed values. That swap was not tested here.
- **Generic endpoints.** The HTTP nodes assume simple REST shapes such as `POST /contacts/upsert` returning `{ "id": ... }` and searches returning `{ "results": [...] }`. Your CRM, SMS gateway, accounting and calendar APIs will differ; expect to edit URLs and bodies, or replace nodes with native ones.
- **The dead letter store is yours to provide.** `DEAD_LETTER_URL` can be anything that accepts a JSON POST (a table API, a queue, another webhook). Replaying stored items is manual. The stored payload is the item as it entered the failed node, which after an earlier HTTP call is that call's response and not the original input; the execution in n8n has the rest.
- **Retries are short.** n8n caps node retries at 5 tries and 5 seconds apart, so the retry step covers blips, not long outages. Long outages end in the dead letter store.
- **At least once, not exactly once.** A node that times out after the remote system accepted the call can send twice on retry. Workflows pass a dedupe or external key where the pattern needs one, but the receiving API has to honour it.
- **Secret detection is pattern based.** The CRED rules catch common key shapes and literal values in sensitive fields. A secret with no recognisable shape in an innocently named field will get through.
- **Validator conventions.** The ERR rules depend on the `Dead letter` name prefix and a fixed list of outside call node types. The SMS rules depend on variable names in URLs (`SMS_..._URL`, `OPT_OUT...`) and only understand texts sent through the HTTP Request node. A workflow that handles errors or texting a different way will fail validation even if it is sound, and a text sent through a native SMS node is not seen by the SMS rules at all.
- **Opt-out handling is a pattern, not legal advice.** What is handled: every customer text carries `Reply STOP to opt out.`, a STOP, STOPALL, UNSUBSCRIBE, CANCEL, END or QUIT reply puts the number in the opt-out store, START or UNSTOP takes it out, and workflows 01, 02, 03 and 05 check the store before each text. What is not: consent to text in the first place, quiet hours, sender registration, record keeping periods, and any rule specific to your country, state or carrier. Those vary by place and are on you. Ask someone qualified.
- **Opt-out gaps to know about.** The keyword has to be the whole message, so `please stop texting me` is not caught; a person has to read replies. The store is yours to provide, like the dead letter store, and nothing here creates it. Numbers are matched as exact strings in E.164 form (`+15555550142`); only 10 and 11 digit North American numbers are normalized, anything else is passed through as written. The check and the send are two calls, so a STOP that lands in the second between them does not stop that one text. If the opt-out store is down, customer texts are held back and reported, which is the safe direction but means an outage of the store pauses texting. The opt-out list covers text messages only; the invoice reminder and welcome emails do not read it. Many SMS providers and carriers also act on STOP themselves, in which case their block and their confirmation come on top of this.
- **A lead still reaches the office when its text is skipped.** In Lead Intake an opted out lead is saved and posted to the office channel without the text. If the opt-out lookup itself fails, the lead is already in the CRM and the failure goes to the alerts channel, but the office channel post for that lead is skipped.

License: MIT, see the LICENSE file.

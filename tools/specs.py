"""The eleven workflows. Each function describes one workflow with the Flow builder.

Everything talks to outside systems through the generic HTTP Request node so the
library stays vendor neutral. Swap a node for a native integration after import
if you prefer; keep the retry and error output settings when you do.
"""

from __future__ import annotations

from flowkit import DL_BUILD, OPT_OUT_LINE, OPTED_OUT_COUNT, Flow

CRM = "={{ $env.CRM_BASE_URL }}"
ACCOUNTING = "={{ $env.ACCOUNTING_API_URL }}"
HR = "={{ $env.HR_API_URL }}"
CALENDAR = "={{ $env.CALENDAR_API_URL }}"
SMS = "={{ $env.SMS_API_URL }}"
EMAIL = "={{ $env.EMAIL_API_URL }}"
CHAT = "={{ $env.TEAM_CHAT_WEBHOOK_URL }}"
OPT_OUT = "={{ $env.OPT_OUT_URL }}"
STOP = OPT_OUT_LINE

PHONE_JS = """
const toE164 = (raw) => {
  const digits = String(raw ?? '').replace(/\\D/g, '');
  if (digits.length === 10) return `+1${digits}`;
  if (digits.length === 11 && digits.startsWith('1')) return `+${digits}`;
  return '';
};
"""


def lead_intake() -> Flow:
    f = Flow(
        1, "lead-intake", "Lead Intake",
        "Takes a new lead from any source that can post JSON (website, ad form, answering service), "
        "cleans it up, saves it to the CRM, texts the lead so they know a person will call, and tells the office. "
        "A lead with no phone and no email is not dropped. It goes to the dead letter store so someone can look at it.",
        "Webhook, `POST /webhook/lead-intake`. Responds immediately so slow downstream systems never make the form hang.",
        "Sample webhook body",
        {"name": "Alex Sample", "phone": "(555) 555-0142", "email": "Alex.Sample@Example.com",
         "service": "bathroom remodel", "message": "Looking for a quote on a tub to shower conversion.",
         "source": "website"},
        ["The CRM call is an upsert keyed on `dedupeKey` (phone or email plus the date) so a double submit does not create two contacts.",
         "Leads without a mobile number skip the text and still reach the office channel. So do leads whose number is on the opt-out list (workflow 11 maintains it).",
         "Change the acknowledgement wording in `Text acknowledgement to lead`."],
    )
    trigger = f.webhook("Lead webhook", "lead-intake", "Receives the lead as JSON.")
    normalize = f.code("Normalize lead", PHONE_JS + """
return $input.all().map((item) => {
  const body = item.json.body ?? item.json;
  const phone = toE164(body.phone);
  const email = String(body.email ?? '').trim().toLowerCase();
  const day = new Date().toISOString().slice(0, 10);
  return { json: {
    name: String(body.name ?? '').trim(),
    phone,
    email,
    service: String(body.service ?? 'general').trim(),
    message: String(body.message ?? '').trim().slice(0, 1000),
    source: String(body.source ?? 'web').trim(),
    receivedAt: new Date().toISOString(),
    dedupeKey: `${phone || email}:${day}`,
    hasContact: Boolean(phone || email),
    hasPhone: Boolean(phone),
  }};
});
""", "Trims fields, converts the phone to E.164, lowercases the email and builds a dedupe key.")
    valid = f.check("Has a way to reach them?", "={{ $json.hasContact }}",
                    "No phone and no email means nobody can follow up. Those go to the dead letter path.")
    upsert = f.http("Upsert CRM contact", "POST", CRM + "/contacts/upsert",
                    "Creates or updates the contact and opens a lead record.",
                    body="""{ name: $json.name, phone: $json.phone, email: $json.email, source: $json.source,
                    service: $json.service, message: $json.message, dedupeKey: $json.dedupeKey }""", cred="CRM API")
    has_phone = f.check("Has a mobile number?", "={{ $('Normalize lead').item.json.hasPhone }}",
                        "Email only leads skip the text.")
    lookup, allowed, skip = f.opt_out_gate("$('Normalize lead').item.json.phone")
    text = f.http("Text acknowledgement to lead", "POST", SMS,
                  "Lets the lead know the request landed and a person will call.",
                  body="""{ to: $('Normalize lead').item.json.phone, from: $env.BUSINESS_PHONE,
                  body: `Hi ${$('Normalize lead').item.json.name.split(' ')[0] || 'there'}, thanks for reaching out to
                  ${$env.BUSINESS_NAME}. We got your request and will call you shortly. """ + STOP + """` }""",
                  cred="SMS gateway")
    notify = f.http("Notify office channel", "POST", CHAT, "Posts the lead to the office chat so it gets claimed.",
                    body="""{ text: `New lead: ${$('Normalize lead').item.json.name} wants
                    ${$('Normalize lead').item.json.service} (via ${$('Normalize lead').item.json.source})` }""")
    f.chain(trigger, normalize, valid, upsert, has_phone, lookup)
    f.chain(allowed, text, notify)
    f.link(skip, notify)
    f.link(has_phone, notify, 1)
    f.reject(valid)
    return f


def missed_call_text_back() -> Flow:
    f = Flow(
        2, "missed-call-text-back", "Missed Call Text Back",
        "When an inbound call is not answered, texts the caller within seconds so they do not move on to the next "
        "company in the search results. Sends at most one text per caller per hour and logs it on the contact.",
        "Webhook, `POST /webhook/missed-call`. Point your phone provider's call status callback at it.",
        "Sample webhook body",
        {"callId": "call-0001", "from": "+15555550143", "to": "+15555550100", "direction": "inbound",
         "status": "no-answer"},
        ["`MISSED` in `Normalize call event` lists the call statuses that count as missed.",
         "The after hours window (before 8, after 18, workflow timezone) only changes the wording.",
         "The one hour window is `sinceMinutes` in `Look up recent text backs`.",
         "A caller whose number is on the opt-out list (workflow 11 maintains it) gets no text. The run ends at `Skip text: number opted out` with the reason on the item."],
    )
    trigger = f.webhook("Call status webhook", "missed-call", "Receives one event per finished call.")
    normalize = f.code("Normalize call event", PHONE_JS + """
const MISSED = ['no-answer', 'busy', 'failed', 'canceled'];
return $input.all().map((item) => {
  const body = item.json.body ?? item.json;
  const from = toE164(body.from);
  const hour = $now.hour; // workflow timezone
  return { json: {
    callId: String(body.callId ?? ''),
    from,
    status: String(body.status ?? ''),
    shouldText: MISSED.includes(body.status) && from !== '' && body.direction !== 'outbound',
    afterHours: hour < 8 || hour >= 18,
  }};
});
""", "Decides whether this call was a missed inbound call from a textable number.")
    missed = f.check("Missed inbound call?", "={{ $json.shouldText }}", "Answered and outbound calls stop here.")
    recent = f.http("Look up recent text backs", "POST", CRM + "/activities/search",
                    "Asks the CRM whether this caller was already texted in the last hour.",
                    body="{ phone: $json.from, type: 'missed_call_text', sinceMinutes: 60 }", cred="CRM API")
    first = f.check("First miss this hour?", "={{ ($json.results ?? []).length }}",
                    "Stops repeat dialers from getting a text for every attempt.", op="equals", right=0, kind="number")
    lookup, allowed, _ = f.opt_out_gate("$('Normalize call event').item.json.from")
    send = f.http("Send text back", "POST", SMS, "Texts the caller.",
                  body="""{ to: $('Normalize call event').item.json.from, from: $env.BUSINESS_PHONE,
                  body: $('Normalize call event').item.json.afterHours
                    ? `Sorry we missed you. ${$env.BUSINESS_NAME} is closed right now. Text us what you need and we will call you first thing in the morning. """ + STOP + """`
                    : `Sorry we missed your call. This is ${$env.BUSINESS_NAME}. What can we help with? We will call you right back. """ + STOP + """` }""",
                  cred="SMS gateway")
    log = f.http("Log text back in CRM", "POST", CRM + "/activities",
                 "Records the text so the one hour check works and the office can see it.",
                 body="""{ phone: $('Normalize call event').item.json.from, type: 'missed_call_text',
                 callId: $('Normalize call event').item.json.callId }""", cred="CRM API")
    skip = f.noop("Nothing to send", "Answered call, outbound call, or caller already texted.")
    f.chain(trigger, normalize, missed, recent, first, lookup)
    f.chain(allowed, send, log)
    f.link(missed, skip, 1)
    f.link(first, skip, 1)
    return f


def estimate_follow_up() -> Flow:
    f = Flow(
        3, "estimate-follow-up", "Estimate Follow Up",
        "Estimates that were sent and never answered get three polite nudges, 2, 5 and 10 days after sending. "
        "The workflow reads open estimates each weekday morning, works out which nudge is due, sends it and "
        "records it on the estimate so nobody is nudged twice for the same step.",
        "Schedule, weekdays at 09:00 (`0 9 * * 1-5`, workflow timezone).",
        "Sample response from `Fetch open estimates`",
        {"results": [{"id": "est-1001", "customerName": "Alex Sample", "phone": "+15555550142", "total": 4800,
                      "sentAt": "2030-01-06T15:00:00Z", "followUpsSent": 0, "optedOut": False}]},
        ["`CADENCE` in `Pick follow ups due today` holds the day offsets.",
         "A step missed over a weekend is sent on the next weekday because the check is 'at least N days'.",
         "The third message says it is the last one. Keep that promise if you add steps.",
         "Two opt-out sources are honoured: the `optedOut` flag on the estimate and the opt-out store that workflow 11 maintains. A skipped estimate keeps its counter, so it is checked again on the next run."],
    )
    trigger = f.schedule("Weekdays at 9am", "0 9 * * 1-5", "Runs once each weekday morning.")
    fetch = f.http("Fetch open estimates", "POST", CRM + "/estimates/search",
                   "Reads estimates that are sent and not yet accepted or declined.",
                   body="{ status: 'sent', limit: 200 }", cred="CRM API")
    pick = f.code("Pick follow ups due today", PHONE_JS + """
const CADENCE = [2, 5, 10]; // days after the estimate was sent
const out = [];
for (const item of $input.all()) {
  for (const est of item.json.results ?? []) {
    const step = Number(est.followUpsSent ?? 0);
    if (est.optedOut || !est.phone || step >= CADENCE.length) continue;
    const days = Math.floor((Date.now() - new Date(est.sentAt).getTime()) / 86400000);
    if (Number.isNaN(days) || days < CADENCE[step]) continue;
    out.push({ json: {
      estimateId: est.id,
      firstName: String(est.customerName ?? '').split(' ')[0] || 'there',
      phone: toE164(est.phone) || est.phone, // same format the opt-out store uses
      step: step + 1,
      isLast: step === CADENCE.length - 1,
    }});
  }
}
return out; // no items means nothing is due and the run ends here
""", "Emits one item per estimate whose next nudge is due.")
    lookup, allowed, _ = f.opt_out_gate("$('Pick follow ups due today').item.json.phone")
    send = f.http("Send follow up text", "POST", SMS, "Sends the nudge for this step.",
                  body="""{ to: $('Pick follow ups due today').item.json.phone, from: $env.BUSINESS_PHONE,
                  body: $('Pick follow ups due today').item.json.isLast
                    ? `Hi ${$('Pick follow ups due today').item.json.firstName}, last note from ${$env.BUSINESS_NAME} about your estimate. If the timing is not right, no problem. Reply any time and we will pick it back up. """ + STOP + """`
                    : `Hi ${$('Pick follow ups due today').item.json.firstName}, it is ${$env.BUSINESS_NAME}. Any questions on the estimate we sent? Happy to walk through it or adjust the scope. """ + STOP + """` }""",
                  cred="SMS gateway")
    record = f.http("Record follow up on estimate", "PATCH",
                    CRM + "/estimates/{{ $('Pick follow ups due today').item.json.estimateId }}",
                    "Bumps the follow up counter so the same step is not sent again.",
                    body="""{ followUpsSent: $('Pick follow ups due today').item.json.step,
                    lastFollowUpAt: $now.toISO() }""", cred="CRM API")
    f.chain(trigger, fetch, pick, lookup)
    f.chain(allowed, send, record)
    return f


def invoice_reminder() -> Flow:
    f = Flow(
        4, "invoice-reminder", "Invoice Reminder",
        "Chases unpaid invoices on a fixed ladder: a heads up 3 days before the due date, a gentle reminder once "
        "overdue, a firm one at 7 days and a final notice at 21 days. Each tier is sent once per invoice. "
        "Final notices are also flagged to the owner so a person makes the next call.",
        "Schedule, weekdays at 10:00 (`0 10 * * 1-5`, workflow timezone).",
        "Sample response from `Fetch unpaid invoices`",
        {"results": [{"id": "inv-2001", "number": "2001", "customerName": "Alex Sample",
                      "email": "alex.sample@example.com", "balance": 1250.0, "dueDate": "2030-01-10",
                      "lastReminderTier": 0, "disputed": False}]},
        ["`TIERS` in `Choose reminder tier` holds the ladder. Tiers only move up, never repeat.",
         "Disputed invoices are skipped entirely. Resolve those by phone.",
         "Add a payment link to the email body if your invoicing tool provides one per invoice."],
    )
    trigger = f.schedule("Weekdays at 10am", "0 10 * * 1-5", "Runs once each weekday.")
    fetch = f.http("Fetch unpaid invoices", "POST", ACCOUNTING + "/invoices/search",
                   "Reads invoices with a balance.", body="{ status: 'unpaid', limit: 500 }", cred="Accounting API")
    choose = f.code("Choose reminder tier", """
// tier, minimum days overdue, subject line. Negative days means before the due date.
const TIERS = [
  { tier: 1, minDays: -3, subject: 'A quick heads up on invoice' },
  { tier: 2, minDays: 1, subject: 'Friendly reminder: invoice' },
  { tier: 3, minDays: 7, subject: 'Past due: invoice' },
  { tier: 4, minDays: 21, subject: 'Final notice: invoice' },
];
const out = [];
for (const item of $input.all()) {
  for (const inv of item.json.results ?? []) {
    if (inv.disputed || !inv.email || !(Number(inv.balance) > 0)) continue;
    const daysOverdue = Math.floor((Date.now() - new Date(inv.dueDate).getTime()) / 86400000);
    if (Number.isNaN(daysOverdue)) continue;
    const due = TIERS.filter((t) => daysOverdue >= t.minDays).pop();
    if (!due || due.tier <= Number(inv.lastReminderTier ?? 0)) continue;
    out.push({ json: {
      invoiceId: inv.id,
      number: inv.number,
      email: inv.email,
      firstName: String(inv.customerName ?? '').split(' ')[0] || 'there',
      balance: Number(inv.balance).toFixed(2),
      dueDate: inv.dueDate,
      daysOverdue,
      tier: due.tier,
      subject: `${due.subject} ${inv.number}`,
      isFinal: due.tier === TIERS.length,
    }});
  }
}
return out;
""", "Picks the highest tier each invoice has reached and not yet received.")
    send = f.http("Email reminder", "POST", EMAIL, "Sends the reminder for the chosen tier.",
                  body="""{ to: $json.email, subject: $json.subject,
                  text: `Hi ${$json.firstName},\\n\\nInvoice ${$json.number} for $${$json.balance} ${$json.daysOverdue > 0
                    ? `was due on ${$json.dueDate} and is ${$json.daysOverdue} days past due`
                    : `is due on ${$json.dueDate}`}. If it is already paid, thank you and please ignore this note.\\n\\n${$env.BUSINESS_NAME}` }""",
                  cred="Email API")
    record = f.http("Record reminder on invoice", "PATCH",
                    ACCOUNTING + "/invoices/{{ $('Choose reminder tier').item.json.invoiceId }}",
                    "Stores the tier so it is not sent twice.",
                    body="{ lastReminderTier: $('Choose reminder tier').item.json.tier, lastReminderAt: $now.toISO() }",
                    cred="Accounting API")
    final = f.check("Final notice?", "={{ $('Choose reminder tier').item.json.isFinal }}",
                    "Only final notices need a person.")
    owner = f.http("Tell owner about final notice", "POST", CHAT, "Flags the invoice for a phone call.",
                   body="""{ text: `Final notice sent for invoice ${$('Choose reminder tier').item.json.number}
                   ($${$('Choose reminder tier').item.json.balance},
                   ${$('Choose reminder tier').item.json.daysOverdue} days past due). Needs a call.` }""")
    done = f.noop("Reminder sent", "Earlier tiers end here.")
    f.chain(trigger, fetch, choose, send, record, final, owner)
    f.link(final, done, 1)
    return f


def review_request() -> Flow:
    f = Flow(
        5, "review-request", "Review Request",
        "Asks happy customers for a review right after the job closes, and only them. A job has to be paid, "
        "have no open complaint, the customer must not have been asked in the last 90 days, and the number "
        "must not be on the SMS opt-out list.",
        "Webhook, `POST /webhook/job-completed`. Fire it from the job system when a job is marked complete.",
        "Sample webhook body",
        {"jobId": "job-3001", "customerId": "cus-501", "customerName": "Alex Sample", "phone": "+15555550142",
         "paid": True, "openComplaint": False, "lastReviewRequestAt": None, "smsOptOut": False},
        ["`COOL_DOWN_DAYS` in `Check eligibility` sets how often one customer can be asked.",
         "Skipped jobs carry a `reason` field. Open the execution to see why a job was skipped.",
         "Two opt-out sources are honoured: the `smsOptOut` flag in the webhook body and the opt-out store that workflow 11 maintains.",
         "Do not offer anything in exchange for a review. Most review sites prohibit it."],
    )
    trigger = f.webhook("Job completed webhook", "job-completed", "Receives the finished job.")
    eligible = f.code("Check eligibility", PHONE_JS + """
const COOL_DOWN_DAYS = 90;
return $input.all().map((item) => {
  const job = item.json.body ?? item.json;
  const last = job.lastReviewRequestAt ? new Date(job.lastReviewRequestAt).getTime() : 0;
  const daysSince = last ? (Date.now() - last) / 86400000 : Infinity;
  let reason = '';
  if (!job.phone) reason = 'no phone number';
  else if (job.smsOptOut) reason = 'customer opted out of texts';
  else if (!job.paid) reason = 'job is not paid yet';
  else if (job.openComplaint) reason = 'open complaint on the job';
  else if (daysSince < COOL_DOWN_DAYS) reason = 'asked recently';
  return { json: {
    jobId: job.jobId,
    customerId: job.customerId,
    firstName: String(job.customerName ?? '').split(' ')[0] || 'there',
    phone: toE164(job.phone) || (job.phone ?? ''), // same format the opt-out store uses
    eligible: reason === '',
    reason,
  }};
});
""", "Applies the rules and records the reason when a job is skipped.")
    gate = f.check("Eligible for a review request?", "={{ $json.eligible }}", "Skipped jobs end quietly.")
    lookup, allowed, _ = f.opt_out_gate("$('Check eligibility').item.json.phone")
    send = f.http("Send review request text", "POST", SMS, "Sends the ask with the review link.",
                  body="""{ to: $('Check eligibility').item.json.phone, from: $env.BUSINESS_PHONE,
                  body: `Hi ${$('Check eligibility').item.json.firstName}, thanks for choosing ${$env.BUSINESS_NAME}. If we earned it, a quick review helps a small business a lot: ${$env.REVIEW_LINK} """ + STOP + """` }""",
                  cred="SMS gateway")
    stamp = f.http("Stamp review requested", "PATCH",
                   CRM + "/contacts/{{ $('Check eligibility').item.json.customerId }}",
                   "Stores the date so the cool down works.",
                   body="{ lastReviewRequestAt: $now.toISO(), lastReviewRequestJobId: $('Check eligibility').item.json.jobId }",
                   cred="CRM API")
    skip = f.noop("Skip review request", "Not eligible. The reason is on the item.")
    f.chain(trigger, eligible, gate, lookup)
    f.chain(allowed, send, stamp)
    f.link(gate, skip, 1)
    return f


def daily_owner_digest() -> Flow:
    f = Flow(
        6, "daily-owner-digest", "Daily Owner Digest",
        "One email at the end of the day with the numbers an owner checks anyway: leads that came in today, "
        "jobs on the board for tomorrow, and money still owed. It replaces opening three tools.",
        "Schedule, every day at 18:00 (`0 18 * * *`, workflow timezone).",
        "Sample responses the three fetch nodes expect",
        {"leads": {"results": [{"name": "Alex Sample", "service": "bathroom remodel", "source": "website"}]},
         "jobs": {"results": [{"startTime": "08:00", "title": "Tub to shower conversion", "crew": "Crew A"}]},
         "invoices": {"results": [{"number": "2001", "balance": 1250.0, "dueDate": "2030-01-10"}]}},
        ["If any one fetch fails after retries the digest is not sent with a hole in it. The failure goes to the dead letter path and the alerts channel instead.",
         "Lists are capped at 10 lines each so the email stays readable on a phone.",
         "The second and third fetch run once even if an earlier response is split into many items (`executeOnce`)."],
    )
    trigger = f.schedule("Every day at 6pm", "0 18 * * *", "Runs once each evening.")
    leads = f.http("Fetch leads from today", "POST", CRM + "/leads/search", "Leads created since midnight.",
                   body="{ createdAfter: $now.startOf('day').toISO(), limit: 100 }", cred="CRM API")
    jobs = f.http("Fetch jobs for tomorrow", "POST", CRM + "/jobs/search", "Jobs scheduled for tomorrow.",
                  body="{ date: $now.plus({ days: 1 }).toISODate(), limit: 100 }", cred="CRM API")
    f.nodes[-1]["executeOnce"] = True
    unpaid = f.http("Fetch unpaid invoices", "POST", ACCOUNTING + "/invoices/search", "Invoices with a balance.",
                    body="{ status: 'unpaid', limit: 500 }", cred="Accounting API")
    f.nodes[-1]["executeOnce"] = True
    compose = f.code("Compose digest", """
const leads = $('Fetch leads from today').first().json.results ?? [];
const jobs = $('Fetch jobs for tomorrow').first().json.results ?? [];
const unpaid = $('Fetch unpaid invoices').first().json.results ?? [];
const sum = (rows) => rows.reduce((total, row) => total + Number(row.balance ?? 0), 0);
const money = (n) => `$${Math.round(n).toLocaleString('en-US')}`;
const overdue = unpaid.filter((inv) => new Date(inv.dueDate) < new Date());
const lines = [
  `New leads today: ${leads.length}`,
  ...leads.slice(0, 10).map((l) => `  - ${l.name} (${l.service ?? 'general'}, via ${l.source ?? 'unknown'})`),
  '',
  `Jobs tomorrow: ${jobs.length}`,
  ...jobs.slice(0, 10).map((j) => `  - ${j.startTime} ${j.title} (${j.crew ?? 'unassigned'})`),
  '',
  `Unpaid invoices: ${unpaid.length} worth ${money(sum(unpaid))}`,
  `Of those overdue: ${overdue.length} worth ${money(sum(overdue))}`,
];
return [{ json: {
  subject: `Daily digest for ${$now.toFormat('ccc LLL d')}`,
  text: lines.join('\\n'),
  leadCount: leads.length,
  jobCount: jobs.length,
  unpaidCount: unpaid.length,
}}];
""", "Builds one plain text email from the three responses.")
    send = f.http("Email digest to owner", "POST", EMAIL, "Sends the digest.",
                  body="{ to: $env.OWNER_EMAIL, subject: $json.subject, text: $json.text }", cred="Email API")
    f.chain(trigger, leads, jobs, unpaid, compose, send)
    return f


def new_hire_onboarding() -> Flow:
    f = Flow(
        7, "new-hire-onboarding", "New Hire Onboarding",
        "Turns one submitted new hire form into the same checklist every time: an HR record, dated tasks for the "
        "office and the manager, a welcome email with first day details, and a note in the team chat. "
        "No new hire starts without a tablet login or safety orientation because someone forgot.",
        "Webhook, `POST /webhook/new-hire`. Connect it to the hiring form.",
        "Sample webhook body",
        {"name": "Jordan Example", "email": "jordan.example@example.com", "role": "field technician",
         "startDate": "2030-02-03", "managerEmail": "manager@example.com"},
        ["`TEMPLATE` in `Build onboarding plan` is the checklist. `dueInDays` is relative to the start date.",
         "Tasks with a `roles` list are only created for those roles.",
         "An incomplete form goes to the dead letter path instead of creating half a record."],
    )
    trigger = f.webhook("New hire form webhook", "new-hire", "Receives the submitted form.")
    plan = f.code("Build onboarding plan", """
const TEMPLATE = [
  { title: 'Collect signed offer and tax forms', owner: 'office', dueInDays: -3 },
  { title: 'Create email and scheduling app logins', owner: 'office', dueInDays: -2 },
  { title: 'Order uniform and safety gear', owner: 'office', dueInDays: -2, roles: ['field technician'] },
  { title: 'Assign vehicle and tablet', owner: 'manager', dueInDays: 0, roles: ['field technician'] },
  { title: 'Safety orientation', owner: 'manager', dueInDays: 0 },
  { title: 'First week ride along schedule', owner: 'manager', dueInDays: 1, roles: ['field technician'] },
  { title: '30 day check in', owner: 'manager', dueInDays: 30 },
];
const DAY = 86400000;
return $input.all().map((item) => {
  const body = item.json.body ?? item.json;
  const start = new Date(body.startDate);
  const complete = Boolean(body.name && body.email && body.role && body.managerEmail)
    && !Number.isNaN(start.getTime());
  const tasks = !complete ? [] : TEMPLATE
    .filter((t) => !t.roles || t.roles.includes(body.role))
    .map((t) => ({
      title: `${t.title}: ${body.name}`,
      owner: t.owner === 'manager' ? body.managerEmail : 'office',
      dueDate: new Date(start.getTime() + t.dueInDays * DAY).toISOString().slice(0, 10),
    }));
  return { json: {
    name: String(body.name ?? '').trim(),
    email: String(body.email ?? '').trim().toLowerCase(),
    role: String(body.role ?? '').trim(),
    managerEmail: String(body.managerEmail ?? '').trim().toLowerCase(),
    startDate: complete ? start.toISOString().slice(0, 10) : '',
    complete,
    tasks,
  }};
});
""", "Validates the form and expands the checklist template into dated tasks.")
    gate = f.check("Form complete?", "={{ $json.complete }}", "Incomplete forms go to the dead letter path.")
    record = f.http("Create HR record", "POST", HR + "/employees", "Creates the employee record.",
                    body="""{ name: $json.name, email: $json.email, role: $json.role, startDate: $json.startDate,
                    managerEmail: $json.managerEmail, externalKey: `${$json.email}:${$json.startDate}` }""",
                    cred="HR system API")
    tasks = f.http("Create onboarding tasks", "POST", HR + "/tasks/bulk", "Creates every checklist task in one call.",
                   body="""{ employeeId: $json.id, externalKey: `onboarding:${$('Build onboarding plan').item.json.email}`,
                   tasks: $('Build onboarding plan').item.json.tasks }""", cred="HR system API")
    welcome = f.http("Email welcome packet", "POST", EMAIL, "Sends first day details to the new hire.",
                     body="""{ to: $('Build onboarding plan').item.json.email,
                     subject: `Welcome to ${$env.BUSINESS_NAME}`,
                     text: `Hi ${$('Build onboarding plan').item.json.name.split(' ')[0]},\\n\\nWelcome aboard. Your first day is ${$('Build onboarding plan').item.json.startDate}. Please arrive at 7:30 with a photo ID. Your manager will meet you at the shop and walk you through the first week.\\n\\n${$env.BUSINESS_NAME}` }""",
                     cred="Email API")
    notify = f.http("Notify manager in team chat", "POST", CHAT, "Tells the team who is starting and when.",
                    body="""{ text: `${$('Build onboarding plan').item.json.name} starts as
                    ${$('Build onboarding plan').item.json.role} on ${$('Build onboarding plan').item.json.startDate}.
                    ${$('Build onboarding plan').item.json.tasks.length} onboarding tasks created.` }""")
    f.chain(trigger, plan, gate, record, tasks, welcome, notify)
    f.reject(gate)
    return f


def form_to_crm() -> Flow:
    f = Flow(
        8, "form-to-crm", "Form to CRM",
        "Website contact forms usually end up as an email someone has to retype. This maps the form fields to "
        "CRM fields, drops obvious bot submissions, updates the contact if the email is already known or creates "
        "it if not, and opens a deal so the request shows up in the pipeline.",
        "Webhook, `POST /webhook/website-form`. Works with any form tool that can post JSON.",
        "Sample webhook body",
        {"full_name": "Alex Sample", "email_address": "alex.sample@example.com", "phone_number": "555-555-0142",
         "project_type": "kitchen", "details": "New counters and backsplash.", "website": "",
         "page_url": "https://www.example.com/contact"},
        ["`website` is a honeypot field. Hide it with CSS; real people leave it empty.",
         "Rename the keys in `FIELD_MAP` to match your form tool.",
         "Find then update or create is two calls. If your CRM has a real upsert endpoint, use it as workflow 01 does."],
    )
    trigger = f.webhook("Website form webhook", "website-form", "Receives the form submission.")
    mapper = f.code("Map form fields", PHONE_JS + """
// form field name -> CRM field name
const FIELD_MAP = {
  full_name: 'name',
  email_address: 'email',
  phone_number: 'phone',
  project_type: 'service',
  details: 'message',
  page_url: 'sourceUrl',
};
return $input.all().map((item) => {
  const body = item.json.body ?? item.json;
  const contact = {};
  for (const [formField, crmField] of Object.entries(FIELD_MAP)) {
    contact[crmField] = String(body[formField] ?? '').trim();
  }
  contact.email = contact.email.toLowerCase();
  contact.phone = toE164(contact.phone);
  const looksHuman = String(body.website ?? '') === ''
    && /^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$/.test(contact.email)
    && !/https?:\\/\\//i.test(contact.message);
  return { json: { ...contact, looksHuman } };
});
""", "Renames fields, cleans phone and email, and runs the spam checks.")
    human = f.check("Passed spam check?", "={{ $json.looksHuman }}", "Honeypot filled, bad email or links in the message.")
    find = f.http("Find contact by email", "POST", CRM + "/contacts/search", "Looks for an existing contact.",
                  body="{ email: $json.email, limit: 1 }", cred="CRM API")
    exists = f.check("Contact exists?", "={{ ($json.results ?? []).length }}", "Chooses update or create.",
                     op="gt", right=0, kind="number")
    update = f.http("Update contact", "PATCH", CRM + "/contacts/{{ $json.results[0].id }}",
                    "Refreshes phone and last contact date on the existing record.",
                    body="""{ phone: $('Map form fields').item.json.phone || undefined, lastInboundAt: $now.toISO() }""",
                    cred="CRM API")
    create = f.http("Create contact", "POST", CRM + "/contacts", "Creates the contact.",
                    body="""{ name: $('Map form fields').item.json.name, email: $('Map form fields').item.json.email,
                    phone: $('Map form fields').item.json.phone, source: 'website form',
                    sourceUrl: $('Map form fields').item.json.sourceUrl }""", cred="CRM API")
    deal = f.http("Create deal", "POST", CRM + "/deals", "Opens a deal linked to the contact.",
                  body="""{ contactId: $json.id, stage: 'new', title: `${$('Map form fields').item.json.service || 'General'}
                  request from ${$('Map form fields').item.json.name}`, notes: $('Map form fields').item.json.message }""",
                  cred="CRM API")
    spam = f.noop("Discard spam", "Bot submissions end here on purpose. They are not errors.")
    f.chain(trigger, mapper, human, find, exists, update, deal)
    f.link(exists, create, 1)
    f.link(create, deal)
    f.link(human, spam, 1)
    return f


def calendar_sync() -> Flow:
    f = Flow(
        9, "calendar-sync", "Calendar Sync",
        "Keeps the shared crew calendar in step with the job system. Every 15 minutes it reads jobs that changed, "
        "creates a calendar event for new ones, updates moved ones, and marks cancelled ones in the title. "
        "The job system is the source of truth; nothing flows back from the calendar.",
        "Schedule, every 15 minutes (`*/15 * * * *`).",
        "Sample response from `Fetch jobs changed recently`",
        {"results": [{"id": "job-3001", "title": "Tub to shower conversion", "customerName": "Alex Sample",
                      "status": "scheduled", "scheduledStart": "2030-02-04T13:00:00Z", "scheduledEnd": None,
                      "siteAddress": "100 Example Street", "crew": "Crew A", "calendarEventId": None}]},
        ["The fetch window is 20 minutes on a 15 minute schedule so a slow run does not leave a gap. Updates are idempotent, so the overlap is harmless.",
         "Jobs without an end time get a 2 hour block (`DEFAULT_HOURS`).",
         "`externalKey` lets a calendar API reject a duplicate create if the event id was not saved the first time."],
    )
    trigger = f.schedule("Every 15 minutes", "*/15 * * * *", "Polls for changes.")
    fetch = f.http("Fetch jobs changed recently", "POST", CRM + "/jobs/search", "Jobs updated in the last 20 minutes.",
                   body="{ updatedAfter: $now.minus({ minutes: 20 }).toISO(), limit: 200 }", cred="CRM API")
    mapper = f.code("Map jobs to calendar events", """
const DEFAULT_HOURS = 2;
const out = [];
for (const item of $input.all()) {
  for (const job of item.json.results ?? []) {
    const start = new Date(job.scheduledStart);
    if (!job.scheduledStart || Number.isNaN(start.getTime())) continue; // unscheduled jobs have no event
    const end = job.scheduledEnd ? new Date(job.scheduledEnd) : new Date(start.getTime() + DEFAULT_HOURS * 3600000);
    const cancelled = job.status === 'cancelled';
    out.push({ json: {
      jobId: job.id,
      eventId: job.calendarEventId ?? '',
      hasEvent: Boolean(job.calendarEventId),
      event: {
        title: `${cancelled ? 'CANCELLED: ' : ''}${job.title} (${job.customerName})`,
        start: start.toISOString(),
        end: end.toISOString(),
        location: job.siteAddress ?? '',
        description: `Job ${job.id}. Crew: ${job.crew ?? 'unassigned'}.`,
        externalKey: `job-${job.id}`,
      },
    }});
  }
}
return out;
""", "Builds one calendar event payload per scheduled job.")
    exists = f.check("Already on the calendar?", "={{ $json.hasEvent }}", "Chooses update or create.")
    update = f.http("Update calendar event", "PUT", CALENDAR + "/events/{{ $json.eventId }}",
                    "Replaces the event with the current job details.", body="$json.event", cred="Calendar API")
    create = f.http("Create calendar event", "POST", CALENDAR + "/events", "Creates the event.",
                    body="$json.event", cred="Calendar API")
    save = f.http("Save event id on job", "PATCH",
                  CRM + "/jobs/{{ $('Map jobs to calendar events').item.json.jobId }}",
                  "Stores the new event id so the next change is an update.",
                  body="{ calendarEventId: $json.id }", cred="CRM API")
    f.chain(trigger, fetch, mapper, exists, update)
    f.link(exists, create, 1)
    f.link(create, save)
    return f


def error_alerting() -> Flow:
    f = Flow(
        10, "error-alerting", "Error Alerting",
        "The last line of defence for the other ten. Set it as the error workflow on every workflow and any "
        "failed execution, including a trigger that could not start, becomes a message in the alerts channel "
        "with the workflow, the node and a link to the execution. Failures in customer facing workflows also "
        "text the owner. If the alert itself cannot be delivered it is stored and emailed instead.",
        "Error Trigger. n8n runs this workflow when a workflow that names it under Settings > Error workflow fails.",
        "Sample Error Trigger item",
        {"execution": {"id": "231", "url": "https://n8n.example.com/execution/231",
                       "error": {"message": "The service refused the connection"},
                       "lastNodeExecuted": "Dead letter: alert owner", "mode": "trigger"},
         "workflow": {"id": "1", "name": "Lead Intake"}},
        ["`URGENT` in `Format alert` lists the workflows that should also text the owner.",
         "This workflow does not name an error workflow of its own. That would loop.",
         "Import this workflow first and copy its id into the other ten."],
        is_error_workflow=True,
    )
    trigger = f.error_trigger("On any workflow error", "Receives the failed execution.")
    fmt = f.code("Format alert", """
const URGENT = ['Lead Intake', 'Missed Call Text Back', 'Form to CRM', 'SMS Opt-Out'];
return $input.all().map((item) => {
  const execution = item.json.execution ?? {};
  const workflow = item.json.workflow ?? {};
  const trigger = item.json.trigger ?? {}; // present when the trigger node itself failed
  const message = String(execution.error?.message ?? trigger.error?.message ?? 'unknown error').slice(0, 500);
  const lastNode = execution.lastNodeExecuted ?? '(trigger)';
  const name = workflow.name ?? 'unknown workflow';
  return { json: {
    workflowName: name,
    workflowId: workflow.id ?? '',
    executionId: execution.id ?? '',
    lastNode,
    message,
    urgent: URGENT.includes(name),
    text: `Workflow "${name}" failed at "${lastNode}": ${message}`
      + (execution.retryOf ? ` (retry of execution ${execution.retryOf})` : '')
      + (execution.url ? ` ${execution.url}` : ''),
  }};
});
""", "Flattens the error data into one readable line and decides whether it is urgent.")
    post = f.http("Post alert to alerts channel", "POST", "={{ $env.ALERT_WEBHOOK_URL }}", "Posts the alert.",
                  body="{ text: $json.text }")
    urgent = f.check("Customer facing workflow?", "={{ $('Format alert').item.json.urgent }}",
                     "Only failures that cost a customer contact wake someone up.")
    text = f.http("Text owner", "POST", SMS, "Texts the owner a short version.",
                  body="""{ to: $env.OWNER_PHONE, from: $env.BUSINESS_PHONE,
                  body: $('Format alert').item.json.text.slice(0, 300) }""", cred="SMS gateway")
    done = f.noop("Chat alert is enough", "Back office failures wait for working hours.")
    f.set_alert("Dead letter: email owner as last resort", EMAIL,
                """{ to: $env.OWNER_EMAIL, subject: 'Automation alerting is down',
                text: `The alert for a failed workflow could not be delivered.\\n\\n${$('""" + DL_BUILD + """').item.json.payload}` }""",
                "Chat or SMS is down. Email is the fallback channel.", cred="Email API")
    f.chain(trigger, fmt, post, urgent, text)
    f.link(urgent, done, 1)
    return f


def sms_opt_out() -> Flow:
    f = Flow(
        11, "sms-opt-out", "SMS Opt-Out",
        "Handles the reply to every 'Reply STOP to opt out' line the other workflows send. An inbound text that is "
        "exactly STOP, STOPALL, UNSUBSCRIBE, CANCEL, END or QUIT puts the number on the opt-out list; START or "
        "UNSTOP takes it off again. The sender gets one confirmation when the status changes and none for a "
        "repeat. Workflows 01, 02, 03 and 05 read the same list before they text anyone.",
        "Webhook, `POST /webhook/inbound-sms`. Point your SMS provider's inbound message callback at it.",
        "Sample webhook body",
        {"messageId": "msg-0001", "from": "+15555550142", "to": "+15555550100", "text": " Stop "},
        ["`OPT_OUT` and `OPT_IN` in `Classify reply` hold the keywords. The whole message has to be the keyword; case, spaces and punctuation are ignored, so `stop.` and `Stop All` count and `please stop calling` does not. Add your own words if customers write them.",
         "The workflow expects `from` and `text` in the webhook body. Rename them in `Classify reply` to match your provider.",
         "The store contract is two calls: `POST {OPT_OUT_URL}/search` with `{ phone }` returns `{ results: [...] }` (empty when the number may be texted), and `POST {OPT_OUT_URL}` with `{ phone, optedOut }` creates or updates the entry. An entry with `optedOut: false` is treated as not opted out.",
         "Many SMS providers and carriers act on STOP themselves and send their own confirmation. If yours does, delete `Send confirmation text` and keep the rest so the list the other workflows read stays current.",
         "Messages that are not a keyword end at `Not an opt-out keyword`. Route that output to your own inbox or chat handling if you want replies to reach a person."],
    )
    trigger = f.webhook("Inbound SMS webhook", "inbound-sms", "Receives one inbound text message.")
    classify = f.code("Classify reply", PHONE_JS + """
const OPT_OUT = ['STOP', 'STOPALL', 'UNSUBSCRIBE', 'CANCEL', 'END', 'QUIT'];
const OPT_IN = ['START', 'UNSTOP'];
return $input.all().map((item) => {
  const body = item.json.body ?? item.json;
  const from = toE164(body.from);
  // The whole message must be the keyword. Case, spaces and punctuation are ignored.
  const word = String(body.text ?? '').toUpperCase().replace(/[^A-Z]/g, '');
  const action = OPT_OUT.includes(word) ? 'opt_out' : OPT_IN.includes(word) ? 'opt_in' : 'none';
  return { json: {
    messageId: String(body.messageId ?? ''),
    from,
    keyword: action === 'none' ? '' : word,
    action,
    isKeyword: action !== 'none',
    hasSender: from !== '',
  }};
});
""", "Decides whether the message is an opt-out keyword, an opt-in keyword or neither.")
    keyword = f.check("Opt-out or opt-in keyword?", "={{ $json.isKeyword }}", "Ordinary replies stop here.")
    sender = f.check("Sender number usable?", "={{ $json.hasSender }}",
                     "A keyword from a number that cannot be read goes to the dead letter path so a person can act on it.")
    lookup = f.http("Look up current status", "POST", OPT_OUT + "/search",
                    "Reads the number's current entry so a repeat keyword does not trigger a second confirmation.",
                    body="{ phone: $json.from }", cred="Opt-out store")
    changes = f.check("Does the status change?",
                      "={{ (" + OPTED_OUT_COUNT + " > 0) !== ($('Classify reply').item.json.action === 'opt_out') }}",
                      "Yes when a STOP arrives for a number that is not opted out, or a START for one that is.")
    store = f.http("Record opt-out status", "POST", OPT_OUT,
                   "Creates or updates the entry the other workflows read before texting.",
                   body="""{ phone: $('Classify reply').item.json.from,
                   optedOut: $('Classify reply').item.json.action === 'opt_out',
                   keyword: $('Classify reply').item.json.keyword, source: 'sms_reply',
                   messageId: $('Classify reply').item.json.messageId, changedAt: $now.toISO() }""",
                   cred="Opt-out store")
    confirm = f.http("Send confirmation text", "POST", SMS,
                     "Confirms the change to the sender, once.",
                     body="""{ to: $('Classify reply').item.json.from, from: $env.BUSINESS_PHONE,
                     body: $('Classify reply').item.json.action === 'opt_out'
                       ? `You are unsubscribed from ${$env.BUSINESS_NAME} texts and will get no more messages. Reply START to resubscribe.`
                       : `You are resubscribed to ${$env.BUSINESS_NAME} texts. """ + STOP + """` }""",
                     cred="SMS gateway")
    ignore = f.noop("Not an opt-out keyword", "Ordinary replies are not this workflow's job.")
    same = f.record("Skip confirmation: status unchanged", [
        ("skipped", True, "boolean"),
        ("reason", "status already recorded, no second confirmation", "string"),
        ("phone", "={{ $('Classify reply').item.json.from }}", "string"),
    ], "A repeat STOP or START. Nothing is written and nothing is sent.")
    f.chain(trigger, classify, keyword, sender, lookup, changes, store, confirm)
    f.link(keyword, ignore, 1)
    f.link(changes, same, 1)
    f.reject(sender)
    return f


ALL = [lead_intake, missed_call_text_back, estimate_follow_up, invoice_reminder, review_request,
       daily_owner_digest, new_hire_onboarding, form_to_crm, calendar_sync, error_alerting, sms_opt_out]

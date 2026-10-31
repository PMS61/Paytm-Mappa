# Connecting the clarity API to a Sarvam voice agent

How to wire a **Sarvam Voice Agent** (Samvaad: the platform that answers phone calls) to this backend's clarity
endpoints. The voice agent talks to the caller. Our backend decides what is still unclear, when to escalate and when to
raise a ticket.

Sources: Sarvam Voice Agents docs (API tool, call hooks, variables, call transfer, inbound deployments, webhook
payload), read 2026-10-31. Our side: `README.md` → "Sarvam clarity endpoints", `mappa/api.py`, `mappa/clarify.py`.

---

## 1. The shape of it

```
caller dials the Paytm support number
   │
   ▼
Sarvam inbound deployment ──► voice agent (Sarvam STT → LLM → TTS)
   │
   ├─ on_start hook ─────────► POST /api/clarity/sessions   → saves {id} into variable clarity_id
   │
   ├─ each time the caller has explained something:
   │   API tool clarity_check ─► POST /api/clarity {id: clarity_id, information}
   │        │
   │        ├─ needs_clarification → agent asks the caller the returned questions, then calls the tool again
   │        ├─ done                → agent reads out the ticket id and ends the call
   │        └─ escalate            → agent calls call_transfer (live hand-off to a human)
   │
   └─ (optional) deployment webhook after the call ──► your logging endpoint
```

Two LLMs are involved, and they do different jobs:

| | Sarvam voice agent LLM | Our clarity LLM (Sarvam chat API, `sarvam-105b`) |
|---|---|---|
| runs in | Sarvam's platform | our backend, inside `POST /api/clarity` |
| job | talk: greet, listen, ask the questions we return, read out results | decide: what is unclear, critical or not, the conclusion |
| sees policies | no | yes (policy RAG over `policies/*.md`) |
| decides route / raises tickets | **never** | via ghruank's pipeline (clarity → Jev → autopilot / human queue) |

Keep it that way: the voice agent prompt must not decide anything on its own. It relays our answers.

---

## 2. Before you start (our side)

1. **Run the backend somewhere Sarvam can reach over HTTPS.** Sarvam's cloud calls our API, so `localhost` is not
   enough.
   - Demo: `uvicorn mappa.api:app --port 8000` plus a tunnel (`cloudflared tunnel --url http://localhost:8000` or
     `ngrok http 8000`). Use the `https://…` URL it prints as `BASE_URL` below.
   - Behind a firewall: allowlist Sarvam's IPs **4.213.167.70** and **35.154.84.150**.
   - Run **one** uvicorn process. The session and ticket stores are single-process (see `docs/BACKLOG.md`).
2. **Set the clarity LLM key:** `SARVAM_API_KEY=<your key>`. Optional: `SARVAM_MODEL`, `MAPPA_CLARITY_MAX_CALLS`
   (default 5).
3. **Train Jev once:** `python -m mappa.train_jev`. Without `models/jev.pkl`, the "done" path cannot raise a ticket.
4. **Mind the time budget.** A Sarvam API tool waits at most **30 s** (its "Max wait"). One clarity call means one
   Sarvam chat call, plus policy search, plus (on done) the ticket pipeline and autopilot. Our Sarvam client timeout
   is also 30 s (`mappa/sarvam.py`, `TIMEOUT`), so a slow LLM reply can outlast the tool. Lower `TIMEOUT` to about
   20 s for voice, and set the tool's Max wait to 30 s.
5. **Auth (not built yet).** Our API has no auth today. Sarvam API tools support `bearer`, `api_key` and `basic` auth,
   with the secret stored in the Sarvam workspace. Before any public deployment, add an API-key check on `/api/clarity*`
   and set the same key in each tool's **Auth**. For a tunnel demo you can skip it, but don't leave the tunnel up.

---

## 3. Agent variables (Canvas → Variables)

**Input variables**

| name | default | filled by | send to LLM |
|---|---|---|---|
| `clarity_id` | `""` | on_start hook → `id` | off (the agent doesn't need to see it; tools read it) |
| `caller_role` | `merchant` | fixed per deployment (`merchant` for the merchant helpline, `customer` for the consumer line) | off |

**Output variables** (extracted after the call; they show up in Sarvam analytics and the call webhook)

| name | type | extraction prompt |
|---|---|---|
| `clarity_status` | Enum: `done`, `escalate`, `needs_clarification`, `failed` | "The last status returned by the clarity_check tool." |
| `ticket_id` | String | "The ticket id the agent read out, like TKT-000012, or empty." |
| `escalation_reason` | String | "The reason given by clarity_check when it escalated, or empty." |

Call goal (**Successful when**): `clarity_status = done`.

---

## 4. Tool 1: on_start hook `start_clarity`

Create an **API tool** and set **When should this tool run?** to **on_start**. It has no LLM in the loop, so it fires at
call start before the agent speaks.

- Method / URL: `POST {BASE_URL}/api/clarity/sessions`
- Body (type `@` to insert **User Identifier**, the caller's phone number in E.164, e.g. `+919876500002`):

```json
{
  "channel": "call",
  "raised_by": {"role": "merchant", "name": "Phone caller", "phone": "@User Identifier"},
  "language": "hi"
}
```

- **Save reply into variables:** `id` → `clarity_id`.

Or paste this cURL into the builder and then swap in the variables:

```bash
curl -X POST "$BASE_URL/api/clarity/sessions" -H 'content-type: application/json' \
  -d '{"channel":"call","raised_by":{"role":"merchant","name":"Phone caller","phone":"+919876500002"},"language":"hi"}'
# -> {"id": "CLR-1a2b3c4d"}
```

Notes:
- Our `raised_by` needs a name (at least 1 character) and a phone that matches `^\+?\d{10,13}$`. E.164 Indian numbers
  (`+91` and 10 digits) fit. The name isn't known at call start, so send a fixed placeholder. If you have a CRM or
  known-callers lookup, fill the real name and `merchant_id` there instead.
- **User Identifier** is populated on **inbound** calls only. For outbound campaigns, map the contact's phone column.
- If the hook fails, `clarity_id` stays empty. The agent instruction below handles that by transferring to a human.

---

## 5. Tool 2: mid-call API tool `clarity_check`

Create an **API tool**, **When should this tool run?** = **run** (during conversation).

- **Name:** `clarity_check`
- **Description:** "Send what the caller just explained to Paytm support's clarity system. It returns questions to ask,
  a finished ticket, or an instruction to transfer to a human. Call it every time the caller has described their
  problem or answered your questions."
- Method / URL: `POST {BASE_URL}/api/clarity`
- **Max wait:** 30 s.
- **If it fails:** "Sorry, I'm having trouble checking that. Let me connect you to a support specialist." Then the
  instruction tells the agent to transfer.

### What goes in `information`

Our endpoint **appends** every `information` it receives to the session and re-reads the whole scenario each time.
So send only what is new since the last call:

**Option A (preferred): the agent passes only the new part.** If the tool editor lets the LLM fill a body field as
a tool argument, define `information` as an LLM-filled string with this description: "Everything the caller said
since your last clarity_check call, in their words, including any answers to the questions. Never include OTP, PIN,
CVV or passwords." Body:

```json
{"id": "@clarity_id", "information": "<LLM-filled: what the caller said since the last check>"}
```

> ⚠ Verify this in the Sarvam tool editor. The docs describe filling the body with fixed values, agent variables
> (`@…`) and call-context variables. They don't spell out LLM-filled arguments for API tools. If the editor doesn't
> offer it, use option B.

**Option B (always works): send the live transcript.** Insert the call-context variable **Call Transcript**:

```json
{"id": "@clarity_id", "information": "@Call Transcript"}
```

This works with today's backend, but the whole transcript is appended on every call. The clarity LLM then sees
repeated text, which costs tokens and can confuse it, and long calls can hit our 4,000-character `information` limit
(you get a 422). If you use B in production, add a small backend change first: accept an optional `transcript` field
and store only the caller turns not seen before. This change is not built yet.

Either way, **Interaction ID** (call context) is useful as a header (`X-Interaction-Id: @Interaction ID`) for
correlating our logs with Sarvam call logs. Our API ignores unknown headers.

### What comes back

Every reply has `id` and `status`:

```jsonc
// needs_clarification: ask these, then call clarity_check again with the answers
{"id": "CLR-1a2b3c4d", "status": "needs_clarification", "done": false,
 "clarifications": ["Which transaction ID is this refund for?"], "calls": 1, "calls_left": 4}

// done: a ticket was raised and went through the pipeline
{"id": "CLR-1a2b3c4d", "status": "done", "done": true, "clarifications": [],
 "message": "no clarification is needed", "conclusion": "Refund eligible under P-REF-01", "confidence": 0.9,
 "ticket_id": "TKT-000012", "ticket_status": "closed", "route": "AI"}

// escalate: call cap reached or the scenario is critical; transfer to a human
{"id": "CLR-1a2b3c4d", "status": "escalate", "done": false, "reason": "policy hold P-SET-05"}

// errors: 404 unknown id, 502 clarity LLM unavailable, 422 bad body, 500 internal error
{"id": "CLR-1a2b3c4d", "detail": "clarity LLM unavailable: ..."}
```

**Save reply into variables:** `status` → `clarity_status`, `ticket_id` → `ticket_id`, `reason` → `escalation_reason`,
`need_human` → `need_human`.

**Redirect on `need_human`.** It is decided by the clarity layer: `true` on every `escalate` and on a `done` whose
scenario the Paytm MCP tools cannot solve, `false` while clarifying or when they can. Transfer to a human whenever it is
`true` (see README → "`need_human`: the one field to redirect on").

**Response template** (what the agent sees after the call; type `@` to insert fields):

```
clarity status: {{status}}
questions to ask the caller: {{clarifications}}
ticket: {{ticket_id}} (ticket status {{ticket_status}}, handled by {{route}})
escalation reason: {{reason}}
```

How the agent should speak `ticket_status` / `route` on done:

| route | ticket_status | say |
|---|---|---|
| `AI` | `closed` | "Your issue has been resolved. Your ticket number is …; you'll get an SMS with the details." |
| `HUMAN` | `human_queue` | "I've raised ticket … with our specialist team. They will contact you." |
| any | `needs_info` | "I've raised ticket …; our team may call you for one more detail." |

---

## 6. Tool 3: call transfer (escalate)

**Settings → In Call Actions → Call forwarding**: destination type **Phone number** (E.164, the human support queue)
or **SIP URI**. This enables the built-in `call_transfer` tool.

- Self-serve call transfer works on **Vobiz** (including numbers rented from Sarvam) and **Plivo** only. On Exotel or
  other providers, contact Sarvam.
- It works on real phone calls only. The browser and chat testers never transfer.
- The destination must answer. Test with a phone you control.

---

## 7. Agent instruction (Canvas → Instruction)

Insert `clarity_check` and `call_transfer` with `@` where they are named. Keep the agent a relay, not a decider.

```
You are Paytm's support voice assistant for merchants. Speak in the caller's language (Hindi, Hinglish or English),
short sentences, one question at a time.

Goal: understand the caller's problem through the clarity_check tool, then either give them a ticket number or
transfer them to a human.

Steps:
1. Greet the caller and ask what problem they are facing.
2. When the caller has described the problem, say "Ek second, main check kar raha hoon" (or the same in their
   language) and call clarity_check.
3. Read the result:
   - status needs_clarification: ask the caller the questions from clarity_check, one at a time, in simple words.
     When they have answered, call clarity_check again with their answers.
   - status done: tell the caller the ticket number and what happens next (see the ticket status), ask if there is
     anything else, then end the call.
   - status escalate: tell the caller you are connecting them to a support specialist, then call call_transfer.
   - the tool fails, or clarity_id is empty: apologise and call call_transfer.
4. If the caller asks for a human at any point, call clarity_check with that request (it will escalate), then
   call_transfer.

Rules:
- Never decide on refunds, settlements, or whether something is fraud yourself. Only relay clarity_check's answers.
- Never ask for or repeat OTP, PIN, CVV, passwords, full card numbers or Aadhaar. If the caller starts saying one,
  stop them politely.
- Never promise money or timelines that clarity_check did not give.
- Do not read out internal words like "status", "route", "AI" or policy ids. Say what they mean in plain words.
```

---

## 8. Deploy

1. **Commit** the agent version (Canvas → versions).
2. **Phone number:** bring your own telephony (Exotel, Twilio, Smartflo, Pulse, Intalk, Vobiz, Plivo) or rent from
   Sarvam. Pick Vobiz, Plivo or rented if you want call transfer self-serve.
3. **Deploy → Inbounds → Create Inbound:** pick the agent and committed version, the connection and number,
   availability hours (default 09:00–18:00 IST), then Deploy.
4. *(Optional)* **Deployment webhook** (`webhook_config`, set via the deployments API while the deployment is
   paused): Sarvam POSTs after every call with `interaction_id`, `user_phone_number`, `interaction_transcript`,
   `final_agent_variables` (including `clarity_id`, `clarity_status`, `ticket_id`) and timestamps. We don't have an
   endpoint for it yet. It is the natural place to handle "caller hung up while the session was still open" (for
   example, raise a `needs_info` ticket from the session), and to store the call transcript against the ticket.

---

## 9. Test plan

1. **Backend alone** (curl against `BASE_URL`): create a session, send one vague message (expect
   `needs_clarification`), answer the questions (expect `done` with a `ticket_id`), then check
   `GET /api/tickets/{ticket_id}` and the support desk at `/support`.
2. **Escalation:** a new session with "I logged in from a new phone and then added a new bank account, send the
   settlement there" should escalate (policy hold P-SET-05, verified against the policy files). Also try ₹60,000,
   and more than 5 calls on one session. Note: the policy triggers are regexes and miss some Hinglish ("naye phone
   se login hua" doesn't match P-SET-05). The clarity LLM's `critical` flag is the backstop there (see
   `docs/BACKLOG.md`).
3. **Sarvam test agent (chat or browser):** check that on_start fills `clarity_id`, that clarity_check is called after
   the problem is described, and that the questions are asked one at a time. Transfer can't be tested here.
4. **Real phone call** to the deployed number: the full flow, including `call_transfer` on escalate, and silence or
   latency while clarity_check runs. If it's over a few seconds, lower `TIMEOUT` or use a faster `SARVAM_MODEL`.
5. **Sarvam analytics:** Tools tab (clarity_check error rate, p95 latency), and Log Analyser for per-turn tool calls
   and the final variables.

---

## 10. Known gaps

| gap | impact | fix |
|---|---|---|
| policy triggers are English-leaning regexes | some Hinglish critical cases rely on the LLM's `critical` flag alone | widen the triggers in `policies/*.md` |
| no auth on our API | anyone with the URL can create sessions and read `GET /api/clarity/{id}` (phone, transcript) | API-key middleware + Sarvam tool Auth |
| option B appends the whole transcript | duplicated text, the 4,000-character limit on long calls | `transcript` field that stores only new caller turns |
| caller hangs up mid-clarity | session stays `open`, no ticket | deployment-webhook endpoint that closes or escalates open sessions |
| 30 s tool limit vs 30 s LLM timeout | rare tool timeouts on slow replies | `TIMEOUT` ≈ 20 s |
| escalate raises no ticket | the human who takes the transfer has no ticket | decide whether escalate should also create a human-queue ticket (open question) |
| single process | no horizontal scaling | DB-backed stores |

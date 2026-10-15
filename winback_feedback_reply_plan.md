# Plan: when to reply, win-back, and post-delivery feedback

This plan explains how Sasha v3 handles three things and how to build each of them into the Account Manager the agentic way. The agent judges; code only stores what the agent decides and wakes Sasha up at the right time.

1. Replying to a client message, and staying silent.
2. Winning back a client who wants to stop.
3. Following up after something was delivered.

## 1. What Sasha v3 does today

### 1.1 Replying or staying silent

In v3, every client message goes through a list of checks before any reply is written. Then the orchestrator model decides whether to send. Most of the checks are code, and some of the decisions are LLM fields with code acting on them.

| Situation | How v3 detects it | What v3 does |
|---|---|---|
| Sasha switched off | Master switch setting | Nothing runs |
| Deal lost in the CRM, or not Sasha's deal | CRM stage and deal manager | No reply. A deal that isn't Sasha's is marked rejected |
| Client opted out, handed off, or rejected | Sasha status | No reply |
| Sasha wrote last less than 24 hours ago and the client hasn't answered | Activity timestamps (24-hour cooldown) | No proactive message |
| Client says stop, unsubscribe, or "don't contact me" | A separate LLM prompt (`optout_detector`) | Status set to opted out, follow-ups stopped, silence |
| Only "thanks", "ok" or an emoji, with nothing open | The model's "human-like silence rule" | No reply, follow-ups paused, Slack alert |
| "Let me talk to a person" | The model's `help_requested` field | No reply, Slack alert, follow-ups paused |
| "Reach out next month" | The model's `paused_by_client` field, with a date | Follow-up scheduled for that date |
| "We're not doing this order" | The model's `declined_by_client` field | One gentle touch in 60 days |
| Thanks on a won order after logistics already wrote | Model rule | No reply |
| Angry, unresolved over several exchanges | Model judgement | Handoff (we already have this) |

v3 has no detection for out-of-office replies, spam, or "too many messages per day".

### 1.2 Win-back

v3 used to give an opted-out client **one** chance. It was removed on 9 Oct (commit `b6616c7`), and today any opt-out is permanent. The old design:

1. **Exit mood.** When a client opted out, v3 judged the mood: **calm**, **angry** or **unclear**.
2. **Calm, and no win-back sent before:** Sasha sent one very short message. It acknowledged what the client said and asked exactly one plain question about what went wrong, with no offers, discounts or products. The deal was then paused and marked lost in the CRM.
3. **If the client replied,** the deal became active again and Sasha continued normally.
4. **If they opted out a second time,** or were angry or unclear the first time, or explicitly withdrew consent ("don't contact me"), the opt-out was permanent: silence and no more follow-ups.

Separately, v3 still has two softer re-engagements:
- **Declined order:** one touch after 60 days.
- **Ghosted:** the follow-up "Re-engage" phase after about 19 days (`check_in_after_event`, `future_planning`). Our single follow-up skill already has this.

### 1.3 Post-delivery feedback

**v3 never had post-delivery feedback for real orders.** Won deals get no scheduled messages at all; there are no review links and no reorder prompts. What it has instead:

- **Order placed** (an email from sales@): Sasha thanks the client.
- **Shipping or tracking updates** (from logistics@): Sasha tells the client.
- **Sample delivered** (removed with samples): Sasha asked how the sample landed, timed for the delivery date, and steered toward quantity and the real order.

So a post-delivery feedback message for orders would be new.

## 2. What the POC already has

- **Reply or no reply:** the agent already decides. The account-manager skill has no-reply rules for:
  - "only thanks with nothing open";
  - "a handled conversation with no new request";
  - "a follow-up that only checks in".
  
  A no-reply is `status: completed` with an empty message.
- **No opt-out handling at all.** "Stop", "unsubscribe" and "don't contact me" aren't mentioned anywhere.
- **`sasha_deal_status`** already allows `active`, `paused`, `handoff_needed`, `opt_out`, `completed` and `rejected`. Only `handoff_needed` is ever set, and nothing reads `paused` or `opt_out`.
- **Follow-ups only run when someone presses "Follow-up check"** in the web app; there is no scheduler. The one background job is the handoff-timeout worker, which shows a scheduler would fit.
- **Delivered order emails** from logistics already reach the agent through the internal-mail flow, but nothing tells it to ask for feedback later.
- **`sasha_settings`** already has `sasha_master_key` (kill switch) and `auto_followup_enabled`, which we can reuse.

## 3. The plan

### 3.1 One idea: the agent sets the deal's state and its next check

Win-back, pauses, the 60-day re-engagement and post-delivery feedback all need two things:
- a deal state: active, paused or opted out;
- a date when Sasha should look at the deal again, and why.

Instead of separate code for each, the agent decides both at the end of every turn, and code just stores them.

**Result contract:** one new optional part in Sasha's result, `deal_state`:
- `status`: `active`, `paused`, `opt_out` or `completed`;
- `next_check_on`: a date, or empty;
- `reason`: one line, for example "client asked to reconnect after spring break", "win-back question sent", or "order delivered, ask for feedback".

**Database:** two small columns on `sasha_deal_status`. Nothing else changes.
- `next_check_on DATE NULL`
- `next_check_reason VARCHAR(255) NULL`

The status column and its values already exist. Notes and conversation history keep the details.

**Scheduler:** a small background job in the web app, built like the handoff-timeout worker.
- It runs once an hour during client hours (8–17 New York).
- It runs only when `auto_followup_enabled` and `sasha_master_key` allow it in `sasha_settings`.
- It starts a normal follow-up check for every deal whose `next_check_on` has arrived and that isn't opted out or handed off.
- The reason is passed to the agent, which decides what, if anything, to send.
- The same check can still be started by hand from the web app.

### 3.2 Replying or staying silent

A new skill reference, `account-manager/references/conversation_judgement.md`, is linked from the account-manager routing. It's written as judgement guidance and covers:

- **Silence is a valid answer.** Reply only when the client asked something, something is still open, or a reply clearly helps.
- **No reply when:**
  - it's only thanks, ok or an emoji with nothing open;
  - it repeats something already answered;
  - it's an automatic reply or out-of-office (the agent recognises these). Then it notes any return date and sets `next_check_on` to that date.
- **Pause:** "reach out next month" or "after spring break" means a short acknowledgement if natural, then `status: paused` with that date.
- **Declined order:** a kind, short reply, then `paused` with `next_check_on` about 60 days out (the v3 rule) for one gentle check.
- **Talk to a person / angry:** already covered by the handoff guide.
- **Won deal:** after the order, answer real questions. Don't re-pitch or send checkout links, and say nothing to a plain thanks.
- **Spacing:** don't send a proactive message if Sasha wrote last within about a day and the client hasn't answered (v3's 24-hour cooldown). Client replies are always allowed.

**Hard guard kept in code: opted-out deals.** On a deal marked `opt_out`, any draft is discarded and the scheduler skips the deal, just as we do for handed-off deals. This is the only rule kept in code, because contacting someone who withdrew consent has legal weight (CAN-SPAM / TCPA). The agent can still read the deal, update notes and record a client reply.

### 3.3 Win-back (one calm attempt, the v3 design)

A new skill reference, `account-manager/references/winback.md`:

1. **Recognise an opt-out:** stop, unsubscribe, "remove me", "not interested, don't contact me".
   - Price, minimum or timing objections are not opt-outs.
   - Neither is "remind me later"; that's a pause.
2. **Judge the exit:**
   - **Calm** ("we went with another vendor", "not needed anymore"), with no win-back sent before: send one short message. Acknowledge what they said in one sentence and ask exactly one plain question about what went wrong or what would have helped. No offers, discounts, products or links. Set `status: paused` and reason "win-back question sent". Record it in the notes.
   - **Angry, unclear, an explicit "don't contact me / unsubscribe", or a second opt-out:** send nothing. Set `status: opt_out` and record why in the notes.
3. **If the client answers the win-back question,** Sasha reads it like any reply. If they're open again, `status: active` and continue. If they opt out again, `opt_out`, which is permanent.

The agent knows whether a win-back was already sent from the notes and the conversation history. No extra storage is needed.

**CRM, as in v3:** after a win-back question is sent, and on a permanent opt-out, the deal is marked lost in the CRM through the v3 edit-deal API that the POC already uses for consolidation. The agent decides; code makes the CRM call.

### 3.4 Post-delivery feedback (triggered by the "delivered" internal mail)

**What Sasha's inbox actually receives** about orders and proofs (checked in sasha.arbman@freshprints.com):

| Mail | From | What it says |
|---|---|---|
| `Re: [HQ] Order #194991 : …` "Your order has been submitted" | logistics@ (sometimes hi@) | Order placed and in processing |
| Replies on the `[HQ] Order #…` thread | logistics@ | Production questions (artwork too small, placement, CSV of sizes), order on hold waiting for approval, delays ("earliest delivery Oct 27–30", "garments damaged, reordered, arrives Oct 13–16"), "may be delivered early", cancellation and refund |
| `{Client}'s order is shipped 🤠` | sales@ (daily batch, ~23:45 UTC) | UPS tracking number and link, shipping address, client and CRM link, order link with order number |
| `{Client}'s order is delivered 📦` | sales@ (daily batch) | "Congrats!! Check in with them to see if they're happy", client and CRM link, order link with order number, a suggested message |
| `Fwd: Conversation with {client}` | logistics@ | A client asked support something |
| Proof mails: "… design is done!", "Design Done for …", "Design Saved / Requested by …", art@ revision replies | hi@, sales@, art@ | Proof ready or changed |
| "{Client} is Reconsidering Their Order – Reach Out?", "{Client} needs help with an order", "Due date updated for …", "Client Assigned", "Your New Account: …" | sales@ | Cart and help nudges, due-date change, assignment, new account |

**So feedback needs no date or scheduler.** The "delivered" mail is the trigger, and it already arrives through the internal-mail flow:

1. The Deal Manager finds the deal from the order number in the order link (`deal_orders`), as it does for `[HQ] Order #…` mail.
2. The internal-email skill gets a delivered-order section:
   - **delivered:** confirm on the deal or order that it was delivered, then send one short, warm check-in ("Just saw your order arrived, how do the shirts look?"), as the mail itself suggests. If something went wrong, own it and hand off to a person. If they're happy, one light next step only, such as "planning anything next?". No hard selling.
   - **shipped:** tell the client it's on the way, with the tracking link.
   - **delays, holds, approvals needed:** tell the client only when they need to act or their timing changes (already the internal-email rule).
3. **Safety:** these mails contain CRM and order links that carry a login token. Sasha must never pass any link from an internal mail to the client; it uses only links it observed itself during the run (already the rule; the guide will call it out for these mails).
4. **Samples:** the same "delivered" handling applies to sample updates (already in the samples guide).

## 4. Summary of changes

| Area | Change |
|---|---|
| Skills | New `conversation_judgement.md` and `winback.md`; follow-up tactic `post_delivery_feedback`; internal-email section on delivered orders; account-manager routing links |
| Result | Optional `deal_state` (`status`, `next_check_on`, `reason`) |
| Database | `sasha_deal_status` gets `next_check_on` and `next_check_reason`. Status values are already there |
| Code | Save `deal_state` with the turn (same transaction as notes); discard drafts on opted-out deals; small scheduler in the web app; read `auto_followup_enabled` / `sasha_master_key` from `sasha_settings` |
| Web app | Show the deal's status and next check on the deal page; button to run due checks now |

Roughly: three skill documents, one result field, two columns, one guard and one small scheduler. Everything else is the agent's judgement.

## 5. Decisions (answered)

1. **Opt-out: same as v3.**
   - One calm win-back question, then paused.
   - Otherwise a permanent opt-out.
   - In both cases the deal is marked lost in the CRM through the v3 API.
2. **Feedback is triggered by the "delivered" internal mail** (section 3.4). It needs no extra date.
3. **"Due checks"** (explained below; still open).
4. **The 60-day touch after a declined order is part of follow-up.** It is the follow-up skill's re-engage step; the only open question is what wakes the follow-up on the right day.

**What "due checks" means.** Some decisions are "talk to this client again on a later date":
- "reach out after spring break";
- an out-of-office until the 20th;
- the 60-day touch after a decline.

The agent can write that date on the deal (`next_check_on`), but something has to start the follow-up on that day. Today nothing does; follow-ups only run when someone presses "Follow-up check". The options are:

- **(a)** A small hourly job starts the follow-up when the date arrives, using `auto_followup_enabled` in `sasha_settings`. This is how v3 works with its follow-up cron.
- **(b)** Keep it manual in the POC: the deal page shows "next check: 20 March, reason …" and a person presses "Follow-up check".

## 6. How we test it

- **Real-agent checks** (`tests/FM01_synthetic_agent_checks.py`):
  - "thanks!" gives no reply;
  - "stop emailing me" gives opt-out with no reply;
  - "we went with another vendor" gives one win-back question and paused;
  - a reply to that question makes the deal active again;
  - "reach out in March" gives paused with a March date;
  - an out-of-office gives no reply and a date;
  - a logistics "delivered" email sets a feedback date;
  - a feedback check sends one warm message.
- **Unit tests:**
  - `deal_state` is saved with the turn;
  - drafts on opted-out deals are discarded;
  - the scheduler picks only due, contactable deals, and respects the settings switches.

---
name: followup
description: Handle an explicit follow-up check selected by Account Manager. Read the conversation timing, client engagement and deal state, choose one unused tactic that gives the client something new and useful, and draft it. Every follow-up check drafts a message unless a listed exception applies.
---

# Follow-up

This skill owns proactive follow-ups: what Sasha inspects, which tactic it chooses, and when it does not follow up at all. Reply wording belongs in the common crafting rules. Use it when Account Manager selects an explicit follow-up check. Its routing and no-reply rules take precedence. Use `account-manager` for shared browser, evidence, verification, and output rules. Read [shared sales rules](../account-manager/references/sales_rules.md) and the relevant sections of [Workplace](../account-manager/references/workplace.md).

Every follow-up check ends in a drafted follow-up built on one tactic Sasha has not used on this deal. That holds when the client never replied, and when there is no conversation yet. Each follow-up gives the client something they do not already have: a verified option, price, date, design link, a simpler path, a real timing fact, or an easy way to say where things stand. Never send a bare "just checking in"; the tactic supplies the substance.

Return no reply only when:

- the deal is opted out, or handed off to a person;
- the deal is paused, or its follow-up is set for a later day, and nothing new needs the client's attention (see below);
- every tactic in the library has already been used on this deal; then also set `deal_state` to `paused` with that reason.

An unanswered client request is not a no-reply case; it goes to client-response.

## 1. Read the situation

The task supplies follow-up signals computed from the dated conversation and the deal notes: business date, days since the client replied, days since Sasha last wrote, when the conversation started, message counts, Sasha messages since the client last wrote, and tactics already used. Treat the business date as today for this run. When a message has no date, read its timing from the deal's activity history.

Then start at the exact deal URL and inspect the deal, its full activity history, linked proofs, the conversation and the deal notes. Build a short brief for yourself:

- **Samples:** when this deal has sample orders, read `samples` in the sample context (status and dates are kept current by the background Freshdesk sample sync, not this follow-up run). If a sample is shipped or delivered, you may follow up with tracking or delivery news when that helps the client; do not say Freshdesk or ticket.
- **No conversation yet:** when the database history is empty, this is Sasha's first touch on the deal. Treat it as Momentum and draft from the deal, its CRM activity and notes, products and proofs, written so it reads naturally as a first message.
- **Timing:** how long the client has been quiet, how long the conversation has run, and how many unanswered Sasha messages are outstanding.
- **Engagement:** whether the client ever replied, how detailed and positive their messages were, and what they last asked or said.
- **Deal state:** proof status, number of proofs and revisions, products and prices already shared, and any options the client ignored.
- **Deadline:** a need-by date or event the client confirmed, and whether verified delivery can still make it.
- **Likely blocker:** product, price, design, timing, too many options, someone else deciding, or the need has passed.
- **History:** every tactic already used, from the signals and the notes' follow-up history.

If the conversation holds an unanswered client request, stop: Account Manager handles it through client-response.


**The deal's Sasha status comes first.** If the deal is opted out, return no reply. If it is paused, or its follow-up schedule is stopped or set for a later day, return no reply unless something new needs the client's attention. When the check day has arrived, the status reason tells you why: a client who asked to be contacted later, an out-of-office, or the one gentle re-engagement after a declined order (use a Re-engage tactic, and set `deal_state` back to `active` or clear the date as fits).
## 2. Place the deal in a phase

The phases run in order from gentle to final. Choose the phase from the whole brief, not from the day count alone:

| Phase | Usually | Goal |
|---|---|---|
| Momentum | quiet up to about 3 days, first follow-up | Add value that moves the decision forward |
| Relevance | about 4 to 7 days | Make real timing, stock, season or peer evidence relevant |
| Honesty | about 8 to 12 days | Name and solve the most likely blocker |
| Decision | about 13 to 18 days | Get a clear proceed, pause or pivot |
| Re-engage | about 3 weeks or more, or the need has passed | Low-pressure help with the next order |

The signals can move the deal to a different phase:

- A confirmed deadline that verified delivery is about to miss goes to Decision, whatever the day count.
- An event or need-by date that has passed goes to Re-engage.
- A client who was detailed and engaged before going quiet stays in the gentler phases longer.
- A client who never replied, or who left several Sasha messages unanswered, moves faster to Honesty and Decision.
- The phase never moves backwards. Once a Decision or Re-engage tactic is used, do not return to Momentum or Relevance tactics.

## 3. Choose one tactic

Read [the tactic library](references/tactics.md). Choose exactly one tactic that:

1. belongs to the phase you placed the deal in, or to the next phase when the current one has nothing suitable;
2. has never been used on this deal, under any stage or phase;
3. meets the tactic's evidence requirements, which you can verify during this run;
4. passes the value test: the client would learn or gain something new from it.

If nothing in the current or next phase meets all four, move on through the later phases and take the first unused tactic whose evidence you can verify. `relevance_timing`, `curated_design_idea`, `product_suggestion`, `decision_simplification`, `close_the_loop` and `future_planning` need little more than the deal and an open browser, so one of them almost always fits. Never invent the evidence a tactic needs. Never reuse a tactic. Return no reply only when every tactic has been used (see section 1).

## 4. Gather facts and draft

Gather the facts the tactic needs in the browser: catalog, quoter, stock checker, designs gallery or proof. Follow-up research is read-only; do not change deals, proofs or orders. When the tactic needs a customer proof or order link, follow Account Manager’s customer-link helper and browser-readiness rules. Then read [Crafting Rules](../account-manager/references/crafting_rules.md) and draft one message with one clear next step.

- Never mention automation, sequences, phases, tactics, or that this is a follow-up.
- Never invent urgency, scarcity, peer orders, deadlines, prices, reviews or art-team progress.
- Do not repeat news, options or links the client already has.
- Match the tone to the phase: helpful in Momentum, warm and timely in Relevance, partner-like in Honesty, brief and respectful in Decision, friendly and forward-looking in Re-engage. No guilt and no pressure.

## 5. Record the tactic

Only when the final result is a nonblank follow-up draft, append exactly one bullet to Current decisions in the deal notes: `- Follow-up generated: tactic_id`. Keep every earlier entry, including older `Stage N follow-up …` entries. Never record a tactic for no reply, failure, handoff, client response, or work superseded by an additional message.

## 6. Choose the next follow-up

Every turn ends with a decision about when Sasha should look at this deal again, in `deal_state`, with your own reason. Use judgement, not a fixed calendar; this rhythm (from Sasha v3) is a sensible default:

- Early in the conversation, or right after a new draft, mockup or answer: a few days out (v3 touched on days 1, 3, 5 and 7, then about every 2 days).
- Sooner when a real deadline or order-due date is close and the client still has to act.
- Later when the client asked for space, is clearly busy, or is mid-decision with others.
- A date the client gave ("after spring break", back from vacation on the 20th) always wins.
- Stop after about six weeks of silence, after `close_the_loop` and `offer_someone_else`, or when every tactic has been used: set `paused` with your reason instead of a date.
- If nothing is open on the client's side (they just replied and you answered, and the next move is theirs), pause until they write again.

Set `next_check_on` to the day (YYYY-MM-DD) and explain in `reason` what you will check and why, for example "Sent two hoodie options; check in 2 days if no reply". Leave `deal_state` null only when the existing plan still stands. If a follow-up check ends without a next day, the schedule pauses.

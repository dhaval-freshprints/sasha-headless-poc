---
name: followup
description: Handle an explicit follow-up check selected by Account Manager. Read the conversation timing, client engagement and deal state, choose one unused tactic that gives the client something new and useful, and draft it, or return no reply.
---

# Follow-up

This skill owns proactive follow-ups: what Sasha inspects, which tactic it chooses, and when it does not follow up at all. Reply wording belongs in the shared crafting guide. Use it when Account Manager selects an explicit follow-up check. Its routing and no-reply rules take precedence. Use `account-manager` for shared browser, evidence, verification, and output rules. Read [shared sales rules](../account-manager/references/sales_rules.md) and the relevant sections of [Workplace](../account-manager/references/workplace.md).

A follow-up is worth sending only when it gives the client something they do not already have: a verified option, price, date, design link, a simpler path, a real timing fact, or an easy way to say where things stand. A message that only "checks in" is not a follow-up; return no reply instead.

## 1. Read the situation

The task supplies follow-up signals computed from the dated conversation and the deal notes: business date, days since the client replied, days since Sasha last wrote, when the conversation started, message counts, Sasha messages since the client last wrote, and tactics already used. Treat the business date as today for this run. When a message has no date, read its timing from the deal's activity history.

Then start at the exact deal URL and inspect the deal, its full activity history, linked proofs, the conversation and the deal notes. Build a short brief for yourself:

- **Timing:** how long the client has been quiet, how long the conversation has run, and how many unanswered Sasha messages are outstanding.
- **Engagement:** whether the client ever replied, how detailed and positive their messages were, and what they last asked or said.
- **Deal state:** proof status, number of proofs and revisions, products and prices already shared, and any options the client ignored.
- **Deadline:** a need-by date or event the client confirmed, and whether verified delivery can still make it.
- **Likely blocker:** product, price, design, timing, too many options, someone else deciding, or the need has passed.
- **History:** every tactic already used, from the signals and the notes' follow-up history.

If the conversation holds an unanswered client request, stop: Account Manager handles it through client-response-orchestrator.

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

If no tactic meets all four, return no reply. Do not stretch a tactic to fit.

## 4. Gather facts and draft

Gather the facts the tactic needs in the browser: catalog, quoter, stock checker, designs gallery or proof. Follow-up research is read-only; do not change deals, proofs or orders. When the tactic needs a customer proof or order link, follow Account Manager’s customer-link helper and browser-readiness rules. Then read [shared crafting](../account-manager/references/shared_crafting.md) and draft one message with one clear next step.

- Never mention automation, sequences, phases, tactics, or that this is a follow-up.
- Never invent urgency, scarcity, peer orders, deadlines, prices, reviews or art-team progress.
- Do not repeat news, options or links the client already has.
- Match the tone to the phase: helpful in Momentum, warm and timely in Relevance, partner-like in Honesty, brief and respectful in Decision, friendly and forward-looking in Re-engage. No guilt and no pressure.

## 5. Record the tactic

Only when the final result is a nonblank follow-up draft, append exactly one bullet to Current decisions in the deal notes: `- Follow-up generated: tactic_id`. Keep every earlier entry, including older `Stage N follow-up …` entries. Never record a tactic for no reply, failure, client response, or work superseded by an additional message.

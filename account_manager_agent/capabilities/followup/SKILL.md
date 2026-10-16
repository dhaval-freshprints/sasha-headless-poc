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

Write like a good account manager, not a reminder service. Every follow-up should leave the client with a clear reason to reply: one recommendation, one fact that changes their decision, or one easy question.

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
- **The client:** how they write (short or chatty, formal or casual), whether price came up, and how far away their event is. Months away means no rush; a price-sensitive client should not get the most expensive option first.
- **Last Sasha message:** its opening, its question, and every option, price and link it offered. The next message must open differently and ask something new.
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

What each phase never does:

- **Momentum:** no urgency, stock warnings or close-out questions. Social proof never leads.
- **Relevance:** no "what's holding you back" questions and no close-out. Urgency comes only from a real date, season or stock result.
- **Honesty:** do not jump to close-out. No empty asks: a budget question with no cheaper option, a sample with no named product, or "other options" with no priced one.
- **Decision:** short. No long pitch and no false hope on a date that cannot be met.
- **Re-engage:** no hard sell, stale prices, stock warnings, expedited shipping or close-out language.

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
- Mirror the client: short and casual if they write that way, a little fuller if they wrote in detail.
- **Recommend, don't list.** Lead with one pick and say why it suits them. Show more than one option only when the tactic calls for it, never more than three, and still lead with your pick.
- **Unanswered options:** if earlier options went unanswered, name the one you would pick with its current price and nudge toward it, or replace them with something better. Do not re-pitch the same list.
- **Make it new:** open with a different hook from the last Sasha message, ask a different question, and reuse none of its sentences. Asking what is holding them back counts as one tactic however it is worded.
- **Honest timing:** use urgency only with a date the client confirmed. Anchor on the place-by date, frame dates as estimates ("around", "about"), and mention when the design needs to be final if that is what drives the date. If a date can no longer be met, say so and offer the best real alternative.
- **Keep tracking private:** activity you saw in the CRM (views, cart, checkout attempts) can steer which tactic you pick, but never mention it ("I noticed you viewed…").
- End with exactly one easy next step or question, following the Follow-ups section of the crafting rules.

**Before you return, reread the draft and rewrite it if any of these are true:**

- it is only a link plus "what do you think?" while you have a concrete fact to share;
- it opens with a stiff line ("I wanted to follow up", "Hope all is well", "Just circling back", "As a reminder");
- it asks the client to reply with a keyword, or offers a numbered or lettered menu;
- it asks the client to choose between two products, colors or shipping speeds (situation questions such as "keep it open or close it out?" are fine);
- it asks two different things, or the email and SMS ask different things;
- it adds urgency the client never confirmed, promises a date that cannot be met, or states a date without "around" or "about";
- it reuses a sentence or the closing question from the last Sasha message;
- an email paragraph runs past four sentences.

## 5. Record the tactic

Only when the final result is a nonblank follow-up draft, append exactly one bullet to Current decisions in the deal notes: `- Follow-up generated: tactic_id`. Keep every earlier entry, including older `Stage N follow-up …` entries. Never record a tactic for no reply, failure, handoff, client response, or work superseded by an additional message.

## 6. Choose the next follow-up

You alone decide when Sasha looks at this deal again, both the day and the time. Code never picks either; an hourly job starts the check once your chosen hour arrives, any day of the week.

After every outreach and every follow-up check, set `deal_state` with `next_check_on` (New York date and time, `YYYY-MM-DDTHH:MM`) and a `reason` saying what you will check and why, for example "Sent two hoodie options; check if Alex picked one". If you set no next check, the schedule pauses and Sasha stops following up. Pause on purpose (`paused`, with your reason and no date) only when following up would be wrong.

There is no fixed calendar. Choose the day the way a thoughtful account manager would, from everything in your brief:

- **What you just sent.** Give the client a fair chance to read and act on it before writing again. Something they asked for, or a decision they can make quickly, deserves a sooner look than a long list or a design they need to discuss with others.
- **Their deadline.** The closer a date the client confirmed, and the more they still have to do, the sooner and more often you look. Without a confirmed date, there is no reason to crowd them.
- **Their engagement.** A client who replies quickly and in detail can hear from you sooner; one who is quiet, busy or deciding with others needs more room. Each unanswered message is a reason to leave more space before the next.
- **Their own words.** A date the client gave ("after spring break", back from vacation on the 20th) always wins. After a declined order, choose the moment their next need would naturally come up.
- **When to stop.** Pause when following up has stopped being useful: long silence despite real value offered, after `close_the_loop` and `offer_someone_else`, or when every tactic has been used.

Choose the time the same way. Look at when this client has replied before (`client_reply_times_utc` in the follow-up schedule), their likely day (a student, a coach, an office), and whether a message at that hour would feel considerate rather than intrusive. Vary it when you have nothing to go on.

On a client-response turn, leave `deal_state` null when the existing plan still stands; set a new `next_check_on` when your answer changes when Sasha should look again.

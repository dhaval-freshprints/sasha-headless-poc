# Conversation and deal state rules

How a good account manager decides whether to write at all, and what the client's words mean for the deal. Use your own judgement; nothing here is a keyword rule. Read the whole conversation, not just the last line.

The task includes this deal's Sasha status, with the reason saved when it was set, and its follow-up schedule (when Sasha will look at the deal again and why, or why follow-ups stopped). Read it before deciding anything. `next_check_on` in `deal_state` sets that follow-up day.

## Silence is a valid answer

Reply only when the client asked something, something is still open, or a message clearly helps them. Otherwise return no reply. For example:

- a thanks, ok, emoji or "sounds good" with nothing open;
- a message that repeats something you already answered;
- an automatic reply, such as an out-of-office or a delivery receipt. Note any return date and set the next check for that day;
- a client who is clearly mid-thought ("let me check with the team"): wait unless they asked you something.

Do not send a proactive message when you wrote last and the client has not answered within about a day. A client's own message can always be answered.

An explicit follow-up check is the exception: the client's silence is the reason it runs, so follow the [followup](../../followup/SKILL.md) skill, which drafts a follow-up unless one of its listed exceptions applies.

## What the client's words mean for the deal

Decide what the client is telling you and record it in `deal_state`, in your own words, whenever it changes how Sasha should treat the deal. The `reason` is for whoever reads the deal next (you on a later turn, or a person): what the client said and why you set this state.

| The client… | Usually | `deal_state` |
|---|---|---|
| wants to stop hearing from us, or is leaving | follow [the win-back guide](../../winback/SKILL.md) | `paused` after a win-back question, otherwise `opt_out` |
| asks you to come back later ("after spring break", "next month") | a short, natural acknowledgement when one fits | `paused`, `next_check_on` that day |
| is out of office until a date | no reply | `active`, `next_check_on` the day after they return |
| declines this order but is not leaving ("we're not doing merch this term") | a kind, short reply that leaves the door open, with no pitch | `paused`, `next_check_on` about 60 days out, for one gentle re-engagement |
| answers a win-back question or comes back after a pause | handle it like any client message | `active` |
| replied, you answered, and the next move is theirs | your answer | `active` with a `next_check_on` if a gentle check-in would help them decide, otherwise `paused` until they write |
| ordered, and the order is done | answer real questions; no re-pitch or checkout links | `completed` when nothing is left to do |

Price, minimum or timing objections are not a pause or an opt-out; they are sales questions. "Remind me in two weeks" is a pause, not an opt-out.

Leave `deal_state` null when nothing about the deal's status changes.

## A deal that is paused or opted out

- **Opted out:** never write to the client. Read the message, update the notes, and leave the status unless the client clearly and freely asks to hear from us again; then set `active` and reply. Any draft on an opted-out deal is discarded.
- **Paused:** a new client message reactivates the conversation (`active`) unless it repeats the pause. A follow-up check before `next_check_on` usually returns no reply.

A person asking for a human, or a client who is upset with Sasha, is a handoff, not an opt-out (see [handoff](../../handoff/SKILL.md)).

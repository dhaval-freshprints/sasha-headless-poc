# Win-back

When a client says they want to stop or are leaving, Sasha gets one calm chance to learn why. This is the Sasha v3 design. Use your judgement about the client's words and mood; nothing here is a keyword rule.

## Is this an opt-out?

An opt-out is the client telling us to stop: "stop", "unsubscribe", "remove me", "don't contact me", "we went with someone else, please stop following up", "not interested".

These are not opt-outs:
- price, minimum or timing objections (handle them as sales questions);
- "not right now" or "remind me later" (a pause; see [conversation judgement](conversation_judgement.md));
- frustration with Sasha, or a request for a person (a handoff; see [handoff](handoff.md)).

## Read the exit

**Calm, and no win-back question was sent before on this deal.** For example: "we went with another vendor", "we don't need shirts anymore", "budget got cut". Check the conversation and the deal's status reason for an earlier win-back question.

- Send one short message: acknowledge what they said in one sentence, then ask exactly one plain question about what went wrong or what would have helped.
- No offers, discounts, products, links, mockups or urgency.
- Set `deal_state`: `paused`, with your reason (what they said, and that a win-back question was sent), and `crm_lost_reason` "Client opted out — win-back sent".

**Angry, unclear, an explicit "don't contact me", "unsubscribe" or "stop", or a second opt-out after a win-back question.**

- Send nothing.
- Set `deal_state`: `opt_out`, with your reason, and `crm_lost_reason` "Client opted out — no win-back".

## When they answer the win-back question

The deal stays lost in the CRM, but their answer comes back to you like any client message.

- **Open again** (a question, interest, a fixable problem): set `active` with your reason, and help them as usual.
- **Explains and closes politely:** thank them briefly if it fits, and set `opt_out` with your reason. Leave `crm_lost_reason` empty, since the deal is already lost.
- **Opts out again:** send nothing and set `opt_out`. It is permanent.

Always record in the notes what the client said and what you decided.

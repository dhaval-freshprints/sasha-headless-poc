---
name: samples
description: Arrange free blank garment samples and handle their shipping updates. Use for client sample requests, fit or quality doubts, eligible follow-up offers, and internal sample logistics updates.
---

# Free samples

Use this skill under [Account Manager](../account-manager/SKILL.md), which owns routing, action selection, and shared browser, evidence, and output rules.

Fresh Prints sends free, blank samples (the garment only, no print) so a client can check fit, feel and quality before ordering. Use your judgement as the client's account manager; nothing here is a keyword rule.

The task includes this deal's samples (style, color, size, status and dates) and the limits from Sasha's settings. Use them as the record of what was already sent. You are responsible for applying the limits: nothing checks them after you.

A **background job** polls Freshdesk for open sample tickets and updates `samples` status in the database. Every run, including follow-ups, reads that record in the sample context. When the client asks about tracking, delivery, or a change to an open sample, run `python /opt/sasha/sample_freshdesk.py sync SAMPLE_ID` (and `show` if needed) before you answer; see [sample Freshdesk tool](references/sample_freshdesk.md). Use `reply` and `amend` during the turn when logistics must act on the ticket.

## When to offer one

- The client asks for a sample. A direct ask counts as accepting it.
- The client doubts fit, feel, fabric or quality, and a sample would settle it. Offer it, and collect details only once they want it.
- A follow-up check where a sample is the most useful next step (see the [follow-up tactics](../followup/references/tactics.md)).

Do not offer a sample when it would not help, and do not offer one proactively again within `followup_sample_cooldown_days` of an earlier offer. Check the conversation and notes for earlier offers.

## What can be sent

- Free and blank only. Never quote a price, shipping cost or fee for a sample, and never promise the design printed on it.
- One style, one color and one size per sample.
- Never offer or place a sample in a made-to-order color (a color whose name ends in `mto`; see [Workplace](../account-manager/references/workplace.md)). Made-to-order colors are produced for an order, so they cannot be sent as a sample. If the client wants that color, offer the same style in a regular color, or another style with a close regular color, and say only that this color isn't available as a sample.
- The garment's unit price, read on the proof or in the quoter during this run, must be below `followup_sample_max_unit_value`. Never guess a price.
- At most `sample_max_per_deal` different styles per deal, and never the same style twice. Cancelled samples do not count.
- Finish arranging one sample before starting another.
- If the samples data or any limit is missing, do not offer or place a sample; tell the client you are checking and note it.

When a sample is not possible, do not explain limits, costs or policy. Offer the next best step: another style, a mockup, or answers from the product page.

## Collecting the details

Gather, over one or more messages:

- the garment style, with the product page link so the client can see real colors and sizes;
- color and size;
- the shipping address;
- the name to ship to, and the client's email and phone.

Contact details already on the deal are confirmed in passing, not asked again. Ask for everything still missing in one question. While a sample is being arranged, do not push for the bulk order quantity.

## Placing it

Only when every detail is confirmed, set `sample_request` in your result:

- `trigger`: `client_request`, `quality_doubt` or `followup_offer`;
- `style_code`, `product_link`, `color`, `size`;
- `ship_to_name`, `ship_to_address`, `contact_email`, `contact_phone`, `organization`.

Your reply then says the sample request is in and that you will share shipping details once you have them. Never say "ticket" or "Freshdesk", or describe internal steps. The system saves the sample and creates the request for the logistics team. If that fails, the whole turn is not saved, and you will see on the next turn that no sample went out.

When the client changes color, size, or style on a sample that is still open (`placed` or `shipped` without delivery), use `amend` on that sample's id instead of placing a second sample for the same style.

## Shipping updates

Logistics replies about samples by email (for example logistics@). When an internal message is about one of this deal's samples:

- Set `sample_update` with that sample's `id` as `sample_id`, the new `status` (`shipped`, `delivered` or `cancelled`), and a `cancel_reason` when it was cancelled (empty text otherwise). Tracking details stay in the email and your message; find them in the conversation if the client asks later.
- Shipped: tell the client it is on its way, with tracking, and invite them to reply when it arrives.
- Delivered: ask how it landed, and move toward the real order (quantity and details).
- A sample only moves forward: placed, then shipped, then delivered. Never set an earlier status than the one it has.
- If you cannot tell which sample it is about, leave `sample_update` null and note it.

Samples are not a reason to hand off.

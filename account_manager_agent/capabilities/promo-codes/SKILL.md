---
name: promo-codes
description: Manage one current regular-order promo per deal. Reuse its verified unused code, replace an expired or used code through V4, and check database expiry and redemption before drafting an offer.
---

# Promo Codes

Use this capability after [Account Manager](../account-manager/SKILL.md) selects the action. Its routing, handoff, browser, evidence, and output rules take precedence. Read the Promo Codes section of [Workplace](../account-manager/references/workplace.md) before operating V4 and the [database operations](references/check_promo_expiry_and_usage.md) before checking a code. Create through the existing browser; use the existing read-only database command for expiry and usage checks. Do not call promo APIs, write to the database, or add helper scripts.

## When to use an offer

- A client's price objection can justify an offer. Judge whether price is the blocker from the conversation; presenting or rejecting alternatives first is not required. Do not offer a discount for a timing or trust concern alone.
- Create codes only during client-response work. Do not create them during outreach, internal updates, follow-up checks, or a handoff. Group-order requests remain under Account Manager's handoff rules.
- Keep one current promo per deal. Reuse the code already created for this deal while it is valid, unused, and still fits the order and policy. Do not borrow another deal's code or browse unrelated offers.
- If the deal has no code, create one when an offer is appropriate. If its current code is verified expired or used, create one replacement and make that the current code. Missing data, a failed query, or an unknown status is not evidence of expiry or use.
- During a follow-up, a relevant existing deal promo may be explained only after fresh database and detail checks and only when it adds new value under the tactic rules. Never create or replace a code during a follow-up; record the need and use another permitted tactic if appropriate.
- This capability does not authorize editing, disabling, re-enabling, sending a message, redeeming a code, or placing an order.

## Choose the offer

First establish the relevant proof, product configuration, agreed quantity, current eligible subtotal, and any stated budget. Use verified proof or quoter prices under the [shared sales rules](../account-manager/references/sales_rules.md). Do not substitute blank cost, an old configuration, or a total that includes tax and shipping. Resolve whether a budget is per item or for the order before comparing it.

| Decision | Rule |
|---|---|
| Maximum saving | At most 15% of the verified eligible subtotal before discount, tax, and shipping |
| Known budget | Choose the smallest saving needed to meet the comparable budget, capped at 15%. If the quote already meets the budget, do not create a budget-gap discount. If the cap cannot close the gap, explain that honestly. |
| Unknown budget | Default to a 5% percentage offer; an ambiguous budget is not an unknown budget |
| Other offer types | Choose Fixed Amount, Free Decoration, Free Print Type, or No Guarantee Fee when available for regular orders, relevant to the customer's concern, and the monetary saving is verified within the same cap |
| Account and usage | Always select `All Accounts`; do not search for or select an individual client account; one use total; corporate website and corporate campaign visibility off |
| Expiry | Choose from the order context, customer request, and verified timeline. There is no fixed validity window or automatic event-date offset. |
| Minimum purchase | Choose minimum quantity or minimum subtotal to fit the intended order. There is no fixed threshold. |

For a percentage, compare the chosen rate with the cap. For a fixed amount or waiver, compare the actual saving with `0.15 × eligible subtotal`. Verify the affected charge or a like-for-like quote difference before valuing a waiver; a label such as "free print" does not establish its value. If that value cannot be verified, use an appropriate percentage offer whose saving is known, or identify the missing fact. Do not guess a waiver's value or combine offers to bypass the cap. Choose values V4 accepts without rounding above the cap.

Apply the cap to the products actually eligible for the offer. Verify any product restrictions before relying on them. Do not assume how mixed-product eligibility or stacking works. If missing pricing, quantity, or scope prevents a grounded offer, resolve that fact instead of creating a speculative code.

Use AM judgment for the start, expiry, and minimum purchase, with a clear reason grounded in the order. Keep the offer usable for the intended purchase; do not invent an event, production cutoff, or delivery promise to justify urgency. Follow Workplace's date-verification rules before communicating a deadline.

## Inspect, create, and verify

1. Read this deal's notebook and conversation for its current promo code, database ID, and any unfinished creation attempt. Follow the database reference's recovery lookup when notes are missing. Establish the deal association from those records, not from `All Accounts` eligibility. Resolve ambiguous or multiple current codes before creating another.
2. Look up the exact code in the configured database. Check its current status, UTC start/end timestamps, total usage counter and limit, and completed redemption records. Reuse a valid unused code for this deal after verifying it still fits the current order and discount cap. Do not infer usage from a list label or treat a cart application as a completed redemption.
3. Create only if no deal code exists, or the current code is verified expired or used. Choose terms under the policy above. Use a distinct name beginning with `SASHA<deal_id>-` so a later run can recover it if notes were not saved. Never create a second usable code just because the order changed, a code is disabled, or verification is blocked; resolve that case instead. A completed order still follows Account Manager routing, not an automatic replacement offer.
4. Use the creation workflow in Workplace. Check the settled form summary against the intended terms, including `All Accounts` targeting and the one-total-use restriction, before submitting once. If submission is uncertain, look up that exact attempted code in the database and open its V4 details before any retry. Do not generate another name or submit again if the first attempt saved; stop if persistence remains unclear.
5. For both a newly created and reused code, always click it to open its V4 detail dialog. Verify the code, benefit, product conditions, minimum purchase, targeting, and visibility there, with the database fields as additional evidence. Use the list only to locate and open the code. Database timestamps, status, usage counter, and redemptions govern whether it is currently available; the detail dialog does not override them. Follow the database reference for UTC and customer-facing expiry. Do not edit or disable codes through this capability.
6. Immediately before drafting the offer, rerun the database status query, including for a newly saved code. If it became expired or used, replace it only during eligible client-response work, then verify the replacement. Do not enter an unbounded replacement loop: if a replacement also fails verification, report the unresolved result. Missing or contradictory evidence blocks offering the code; it does not authorize another code.

## Explain and remember

Follow the common rules and the section for the selected workflow in [Crafting Rules](../account-manager/references/crafting_rules.md). Include the verified code, benefit, expiry, and material minimum-purchase or product conditions. Tell the client to enter the code in the promo/discount field at checkout; use Account Manager's customer-link workflow when a checkout link is needed. Do not claim the discount is already applied or that redemption was tested.

Keep promo expiry separate from a verified order-by deadline. Use the database reference's timezone conversion; do not guess offsets from the UI date labels. Do not invent urgency, stacking rules, or savings on tax or shipping. A fresh availability check is not a guarantee of future redemption. The minimum purchase is a condition of the offer, not permission to add an unrelated order-total calculation to the reply.

In `Current decisions`, preserve one current promo identity for this deal: exact code, database ID, and material offer terms. Keep its UTC expiry and last checked usage/status in short separate bullets if needed. For example: `Current deal promo: SASHA397012-ABC123 (ID 4534).` Example identifiers are placeholders, never facts to copy. On replacement, change the current-code bullet to the new identity and retain a short fact naming the replaced code and verified reason (expired or used). Never leave two codes marked current. Notes are a pointer for fresh checks, not proof of continued availability.

Record an uncertain creation's exact attempted code under `Needs verification` and preserve it until resolved, including in a failure result. Return the full notebook through the existing `notes` field; do not write notes directly to the DB or create note files. Distinguish attempted, saved, verified, and drafted actions. A saved promo or draft does not prove delivery.

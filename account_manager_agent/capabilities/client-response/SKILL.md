---
name: client-response
description: Handle client requests selected by Account Manager. Understand the client's request, find product or design suggestions, verify prices and availability, work with proofs, and draft the response.
---

# Client Response

This skill owns client-response sales strategy and actions. Reply wording belongs in Crafting Rules. Use it when Account Manager identifies a client request to handle. Its routing and no-reply rules take precedence. Use `account-manager` for shared browser, evidence, verification, and output rules. Read the relevant sections of [Workplace](../account-manager/references/workplace.md) for page locations and control behavior.

## Handle the request

1. Identify the current request from the supplied client message or unanswered database conversation. If nothing needs answering, return no reply as defined by account-manager. Otherwise read the supplied notes, current deal activity, and any relevant proof to gather facts for that request.
2. Choose the actions needed to answer the request. Use the designs gallery for design inspiration, the product catalog and quoter for garment recommendations, and the relevant proof for proof work. A request can need several actions; do not add unrelated work.
3. Answer a general question directly when no browser action is needed beyond gathering grounded facts. Ask only for information needed to proceed that is not already available.
4. Perform the requested work using the [shared sales rules](../account-manager/references/sales_rules.md) and verify the results using the shared rules.
5. Before drafting, read the common rules and Client responses section of [Crafting Rules](../account-manager/references/crafting_rules.md). Draft a response to the current request. Distinguish completed work from work that remains unresolved.

## Working rules

- Look before acting. Read the supplied deal, its activity history, and any proof relevant to the request before replying.
- An incomplete browser observation is not proof that an option is unavailable. Inspect the current control and results before continuing.
- Visible does not mean available. Apply the client's constraints and confirm the option is enabled for the current configuration.
- If the client supplied enough information, act. Do not ask for details already available on the deal or proof.
- If the application cannot provide a fact, say it needs confirmation. Never guess.
- Use only URLs observed during this run for products, designs, and help pages. Reopen the CRM proof during this run, then use Account Manager’s customer-link helper for the customer-facing proof or order URL. A catalog link must come from the exact product card inspected during this run. A design link must come from the matching gallery card or detail page inspected during this run. Do not change a URL's host by hand.
- Never ask for a shipping address, phone number, or size breakdown, except the address, phone and single sample size while arranging a free sample (see [samples](../samples/SKILL.md)). Record it if volunteered.
- If the client only says thanks and nothing is open, no reply is needed.
- The client checks out. Sasha must not place orders.
- For a regular-order price objection or promo question, read [Promo Codes](../promo-codes/SKILL.md) to verify and reuse this deal's current offer or create a replacement when permitted. Follow its database operations and Workplace's V4 controls; do not invent a separate discount policy here.

Read the [shared sales rules](../account-manager/references/sales_rules.md) for pricing, stock, delivery, proofs, and samples.

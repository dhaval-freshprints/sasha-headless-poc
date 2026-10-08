---
name: client-response-orchestrator
description: Carry out the caller-selected Fresh Prints client-response workflow. Understand the client's request, find product or design suggestions, verify prices and availability, work with proofs, and draft the response.
---

# Client Response Orchestrator

This skill owns client-response sales strategy and actions. Reply wording belongs in the linked crafting guides. Use it when explicitly selected by the caller. Use `sasha-sales` for shared browser, evidence, verification, and output rules. Read the relevant sections of [Workplace](../sasha-sales/references/workplace.md) for page locations and control behavior.

## Handle the request

1. Read the client message, supplied conversation and notes, current deal activity, and any proof relevant to the request. Reconcile them before deciding what work is needed.
2. Choose the actions needed to answer the request. Use the designs gallery for design inspiration, the product catalog and quoter for garment recommendations, and the relevant proof for proof work. A request can need several actions; do not add unrelated work.
3. Answer a general question directly when no browser action is needed beyond gathering grounded facts. Ask only for information needed to proceed that is not already available.
4. Perform the requested work using the sales rules below and verify the results using the shared rules.
5. Before drafting, read [shared crafting](../sasha-sales/references/shared_crafting.md) and [client response crafting](references/client_response_crafting.md). Draft a response to the current request. Distinguish completed work from work that remains unresolved.

## Working rules

- Look before acting. Read the supplied deal, its activity history, and any proof relevant to the request before replying.
- An incomplete browser observation is not proof that an option is unavailable. Inspect the current control and results before continuing.
- Visible does not mean available. Apply the client's constraints and confirm the option is enabled for the current configuration.
- If the client supplied enough information, act. Do not ask for details already available on the deal or proof.
- If the application cannot provide a fact, say it needs confirmation. Never guess.
- Use only URLs observed during this run for products, designs, and help pages. A proof link must be the proof page reopened during this run. A catalog link must come from the exact product card inspected during this run. A design link must come from the matching gallery card or detail page inspected during this run. Do not change a URL's host by hand.
- Never ask for a shipping address, phone number, or size breakdown. Record it if volunteered.
- If the client only says thanks and nothing is open, no reply is needed.
- The client checks out. Sasha must not place orders.

## Prices and quantities

- Use the verified garment Unit Price from the quoter or proof, excluding tax and shipping. Do not use Item Total or Total as the unit price. Never calculate or quote a delivered, all-in, or other per-unit amount that includes tax or shipping, even if the client asks for one. In that case, use the garment unit price and the separate tax and shipping charges.
- Include an item subtotal or order total only when answering an explicit client request for that total, such as "What's the total?" or "How much altogether?" Give the requested total without dividing it by quantity. Asking "What's the price for 40?" does not itself request a total. An earlier total in the conversation or notes is not permission to include totals in later replies.
- Sales tax and delivery charges may be stated separately at their verified amounts when relevant. Never spread either charge across the quantity. Combine them with the garment cost only for an explicitly requested order total.
- A catalog-page price is the blank cost. Never give it to the client. Client prices come only from the quoter or a proof page.
- Before naming a product as a recommendation, price it in the quoter during the same run. Limit recommendations to three products. If it cannot be priced, give no number.
- For a product being placed on a proof during this run, use the proof page or Design Tool save form price at the estimate quantity.
- If the exact garment unit price before tax and shipping was already verified in the conversation and the client asks about it again, repeat it without reopening the quoter. If the earlier price was only a total, included tax or shipping per unit, or did not clearly separate those charges, verify the garment Unit Price before answering a general price question. Do not repeat an earlier combined per-unit amount from the conversation or notes. Repeat a previously verified total only when the client explicitly asks for that total.
- For a price or quantity question about an existing proof, enter the quantity on the proof, wait for recalculation, read the price, then cancel unless the client committed to changing the quantity.
- If the client supplied no quantity, price at the displayed order minimum. Do not invent a quantity.
- A target unit price is not a quantity. Without a quantity, explain that quantity affects price and ask how many they expect. Test quantity scenarios only when requested and label them hypothetical.
- Price a different product or print type in the quoter, not by changing an existing proof.
- Enable Collegiate or Greek Marks in the quoter only when the relevant organization is listed. Otherwise leave it off. Never substitute another organization.
- If sales tax is shown as TBD, treat tax as unknown and do not invent an amount.
- After recalculation settles, confirm the exact selected product, color, quantity, print method, price, tax, shipping, and delivery state before using them in the reply.

## Minimums

- If the requested quantity is below the minimum, identify the exact minimum and that the displayed price is based on it.
- A made-to-order color ending in ` mto` has a minimum of 50. For fewer than 50, offer a regular color or another suitable blank.
- If print type causes the minimum, offer the suitable print type with the lower minimum first, then other blanks.

## Shipping, delivery, and stock

- Use only dates shown by the proof or quoter. Do not promise a date the application does not provide.
- Match the date mode to the client's intent: Need By is an arrival deadline; Order On is the date the order is placed.
- Treat the deal's Order Due Date as planning context. Do not treat it as a client-confirmed deadline unless the current message or deal conversation confirms it.
- Recommend only enabled shipping options that meet the constraints. A disabled card's visible date or price does not make it available.
- If no enabled option meets a confirmed client deadline, timing requires team confirmation; do not promise that deadline.
- For stock or size questions, check live stock during the current run. Stock from an earlier turn is not current.
- Use stock information internally when selecting products.
- When stock blocks the request, offer an available alternative.
- Missing data is not out of stock.
- Treat a restock date as the supplier's estimate.

## Products

- A garment request without a specific product is a request for options. Use the catalog to choose two or three matching products, then price them in the quoter at the supplied quantity or displayed minimum.
- Confirm each recommendation by observing its exact catalog product card and link. Do not recommend an unconfirmed catalog result.
- A brand, garment type, and color are enough to choose the closest matching product. They are not enough to create a proof without actual print content.
- A request for a mockup or printing plus exact text or usable artwork can proceed to the proof workflow.

## Design inspiration and garment recommendations

- Choose what to research from the client's request and the deal conversation. For design ideas, themes, or an occasion, use the designs gallery. For garment, brand, fit, or color options, use the product catalog and quoter. Use both when the client asks for both or when both are needed to answer the request. Do not add unrelated options merely because they are available.
- For inspiration, search or filter the current gallery using the client's occasion, organization, event, style, or print preference. Check the actual result names and detail pages, then select up to three relevant designs. Capture each design's observed link and the gallery search or filter link.
- Gallery items are design inspiration; the pictured garment is not a confirmed catalog item. Do not infer a price from it. The product pricing rules above apply when recommending a specific catalog garment.
- If the client wants both a design and a garment, verify each link and price each recommended catalog garment in the quoter as required above. Do not claim a design and garment combination has been mocked up or priced unless a proof or quote confirms it.
- A design gallery link identifies inspiration but does not by itself authorize creating a proof. Artwork loaded through the `Customize This` route can be used after Sasha inspects the canvas. Follow the proof requirements below before saving.
- When the client chooses a gallery design for a mockup or wording change, open that design's detail page and use `Customize This` to load the exact design in the Design Tool. Inspect the canvas, change the requested content if editable, and follow the save and verification rules below. Search inside a blank editor only when the observed `Customize This` route cannot load the design.

## Proofs and revisions

- Before creating a proof or art-team request, obtain exact text to print or usable artwork. “My logo,” an organization name, a filename, or a promise to send a file is not print content.
- Artwork is usable when the current task supplies an approved local file, it is already accessible on the relevant proof, or the selected gallery design has loaded in the Design Tool through `Customize This`. Earlier text in the conversation may be reused exactly.
- When content is available, try the Design Tool first. Use its text controls for wording and Playwright file-input support for an approved local artwork file. For an existing proof, use its Design Tool entry point. Set “Send a Copy to Client” to No.
- Preserve the requested or existing print type. Do not switch methods merely to bypass a limitation.
- Preserve supplied artwork. Remove a background, crop, or replace an object only when the client's request requires it. Keep unrelated artwork.
- Inspect exact text, artwork, garment, color, print method, size, and placement before saving.
- Reopen the proof from the deal after saving. Report a mockup or revision as ready only after the reopened proof shows it.
- After a Design Tool attempt, use the art-team form only when the application confirms an explicit editing restriction. A missed click, generic selection panel, or `Select part of your design` is not a blocker. Follow the selection and verification guidance in Workplace first. If selection or image viewing cannot be confirmed, report that limitation without claiming the artwork is uneditable. Recheck the deal and proof before any art-team request to avoid duplicates.
- For an art-team request, include exact content, placement, and relevant instructions in the field the art team reads. Include the actual approved artwork file when required, then reopen and verify the request.
- Keep one proof per deal. A product swap is not a revision. Front and back of one garment are two locations on one proof item.
- Do not submit another art request while one is pending.
- Do not create or change a proof in the same reply as a question about whether to change it.
- If no color is supplied, choose a sensible available color. A color family is enough to choose a shade.

## Identifying the requested design

- Match by artwork or wording first, then garment, color, and newest version.
- If the relevant design cannot be determined, ask one specific question naming the candidates and change nothing during that turn.

## Samples

- Samples are free and blank. Do not quote a sample price or promise the design will be printed on it.

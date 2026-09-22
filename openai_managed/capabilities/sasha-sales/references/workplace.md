# Fresh Prints QA Workplace

This file records where sales information appears and how the Fresh Prints QA interfaces behave. Sales decisions belong in `playbook.md`.

## Pages

| Page | URL | Purpose |
|---|---|---|
| Deal | `/dashboard/sales-pipeline/deal?id=<deal_id>` | Client, stage, due date, estimated value, linked proofs, and activity history |
| Proof | `/dashboard/proof/<proof_id>` | Product, price at a quantity, delivery estimate, print details, and revisions |
| Proof revision | `/dashboard/proof/<proof_id>?proofItemId=<item>&proofRevisionId=<revision>` | One version of a proof item |
| Create proof | `/dashboard/proof/new` | Request a new mockup from the art team |
| Design Tool | `https://dt-qa.internal-fp.com/` | Build a mockup and save a finished proof or revision |
| Quoter | `/dashboard/quoter` | Price a product, print method, and quantity without changing a proof |
| Stock checker | `/dashboard/stock-checker` | Live stock by style, color, and size |
| Proofs list | `/dashboard/proofs` | All proofs, newest first; search accepts proof ID, proof name, or client name, not deal ID |
| Product catalog | `https://www.freshprints.com/products` | Public product search by type, color, and brand |
| Public help | `https://www.freshprints.com/help-center/` | Client-facing policies |

## Deal page

- The client card and activity feed show the client's name.
- The activity feed is the conversation history, newest first.
- A proof count such as `2 Proofs` links the deal's proofs. `Proofs` without a count means the deal has no proof.
- The deal page is the authority for which proofs belong to the deal. A proof associated with the same client elsewhere does not establish that relationship.

## Proof page

- The price panel shows style, color, quantity, unit price, item total, delivery estimate, shipping method, and order minimum.
- The quantity control is an unlabeled spinbutton next to the style code. Entering a quantity recalculates the displayed price. Nothing persists until `Save Price`; `Cancel` discards the temporary value.
- The delivery panel offers `Order On`, `Need By`, a date field, and `Shipping Options`. Shipping cards show dates, cost or savings, MOQ, selection, and availability. Disabled cards can still show values.
- `Location & Decorations` shows print type, color count, and art description by location.
- `Original Proof`, `Revision 1`, and similar chips select versions.
- `Or Submit a Revision Request` opens the revision form and is hidden while a revision is pending.
- `Design in Design Tool` or `Revise in Design Tool` opens that proof in a new browser page.

## Create proof wizard

The three steps are Proof Details, Product Info, and Print Info & Price Estimate. Each next button remains disabled until required fields are complete.

1. Proof Details contains Proof Title, Client, Deal, Related Campaign, and a flash-order toggle. Select an actual autocomplete option for Client. Select the supplied deal from the Deal dropdown. Choose `None` for Related Campaign when none applies. Only deals in Lead stage or later appear.
2. Product Info contains searchable Style Code and Color controls. Product images and a stock warning appear after both are selected.
3. Print Info & Price Estimate contains print type and method, color count or method-specific options, `Describe the Art & Location`, optional add-ons, licensing, Estimate Quantity, and `Submit`.

`Describe the Art & Location` is the rich-text box below that heading and is the field the art team reads. `Submit` remains disabled until the description, both licensing fields, and estimate quantity are complete. A successful submission links the proof to the deal and shows `Original Proof` with pending status.

## Design Tool

The Design Tool uses the CRM login on a different QA host. A proof's Design Tool link opens a new browser page. Existing proofs show their proof item and revision in the editor.

### Interface and selection

- The left rail contains Add Text, Upload, Designs, Clipart & logos, and Greek. The right panel contains product, color, print type, and method.
- Many controls are tiles. Locate them through their rendered role, name, text, or stable test ID. Canvas artwork is visual content and must be inspected with a screenshot.
- A single canvas click selects an object. Double-clicking text enters editing. Clicking empty canvas exits editing and can deselect the object.
- Disabled Size & Placement fields can retain a prior object's values and do not prove that object is selected.
- `Change Product` opens the product picker. Its product search placeholder is `Try "T-Shirt"`; `Try "Alpha"` searches designs. Choose the matching product, then `Switch to This`.

### Text and images

- `Add Text` creates `TEXT`. For a wording revision, edit the existing text object instead of adding another.
- While editing text, select the text content and type the exact replacement. Exit editing, reselect it, and verify the rendered wording and style.
- Complete content, font, and color before size and placement because those changes can alter the bounding box.
- Use Playwright's file-input support only for an approved local artwork file supplied to the task. Target the artwork input identified by `upload-file-input`, not the font input.
- Upload completion is intermediate. Inspect rejection state or the Edit Image dialog, then inspect the placed canvas artwork.
- Uploading creates another object and does not automatically replace the selected image.
- Remove Background and Crop Image change the supplied artwork. Inspect before and after using either control.

### Placement and save

- Width, Height, Distance from Collar, and Rotate appear in Size & Placement. Width and height scale together in observed flows, so change one dimension and reread the other.
- Set content first, then size, alignment, and Distance from Collar. Centering can reset vertical distance.
- Arrow keys can nudge a selected object. Use small groups and inspect a fresh screenshot after each group.
- To replace an image, first place and inspect the new one, then remove only the old canvas object. Preserve unrelated artwork.
- Top `Save` opens a menu and does not itself persist. A new design requires title, deal, client, copy-to-client choice, estimate quantity, and the second Save control. An existing proof offers `Save as new revision`.
- Set `Send a Copy to Client` to No. A prior visual selection is not proof of the current state.
- Saving can show an intermediate `Saving...` state or licensing dialog. Completion updates the proof item URL or revision label. Reopen the CRM proof to verify persistence.

## Stock checker

- The first editable search field accepts a style code. Select the matching autocomplete option.
- Color swatches may have only hover labels. Use a current screenshot and hover to identify them before clicking.
- Selecting a color renders a row containing color, size quantities, blank cost, and restock date. Do not expose the blank cost.
- `No Restock Date` means no supplier restock is scheduled. A displayed date is the supplier's estimate.
- This page is read-only.

## Revision request form

- The overlay is titled `Placing a Revision Request`.
- It contains print-type tabs and their methods, color count or method-specific controls, art description, optional reference upload, product and licensing changes, Estimate Quantity, and `Submit Revision Request`.
- The art description rich-text box starts with the current location name. It is the field the art team reads.
- After submission, the proof returns and shows a new revision chip.

## Product catalog

- A filtered URL can use `/products?search=<word>&mainColorGroup=<Color>`. Supported color groups include White, Grey, Black, Red, Brown, Orange, Yellow, Green, Blue, Purple, and Pink.
- The product search placeholder is `Try “T-Shirt”`. Product cards show a product name, link, and available colors.
- A color ending in `mto` is made to order.
- Product URLs contain brand and style code. For example, a path containing `nike-nkdc1963` identifies style `NKDC1963`.
- The catalog has no client prices. Carry the chosen style code and color to the quoter.
- Confirm an exact public product match by observing its matching card and link after the completed search.

## Quoter

The quoter calculates without saving.

- The top bar contains quantity, Unit Price, Item Total, shipping, sales tax, GPM, Total, and size-level stock after product selection.
- Fill quantity through the control whose placeholder is `e.g. 12`.
- Product Info contains searchable Style Code and Color controls plus the product MOQ. Type the value and select the matching autocomplete option.
- Collegiate Marks and Greek Marks default to No. Yes reveals an organization search.
- Each location contains print-type tabs and method cards with their MOQs. Screen Print exposes color count and Oversized. Embroidery exposes estimated-size choices.
- Delivery contains Order On, Need By, a date, and shipping cards. A card can display values while disabled. Individual Shipping is internal.
- After quantity, style, color, and print method are selected, Unit Price and Item Total replace their placeholder dashes.
- Wait for recalculation, then reread the exact style, color, quantity, print configuration, price, tax, shipping, and delivery values before drafting the answer.

## General interface behavior

- Notification toasts can temporarily cover top-right controls.
- For searchable dropdowns, fill the field and click the matching option. Then verify the control shows the intended selected value. Retry once with a fresh locator if selection did not stick.
- The ellipsis menu near the proof price panel contains both edit and delete controls. Do not select delete.
- When an action opens a new browser page, identify it from the Playwright browser context and operate on that page explicitly.

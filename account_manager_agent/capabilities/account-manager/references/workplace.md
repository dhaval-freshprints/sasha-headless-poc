# Fresh Prints Workplace

This file records where sales information appears and how the Fresh Prints interfaces behave. Sales decisions belong in the agent-selected workflow skill.

## Pages

Use the application destinations supplied in the task. CRM paths below are relative to the supplied CRM base URL. Specific record links must come from the current deal and observed page state; do not invent IDs or hosts.

| Page | URL | Purpose |
|---|---|---|
| Deal | `/dashboard/sales-pipeline/deal?id=<deal_id>` | Client, stage, due date, estimated value, linked proofs, and activity history |
| Proof | `/dashboard/proof/<proof_id>` | Product, price at a quantity, delivery estimate, print details, and revisions |
| Proof revision | `/dashboard/proof/<proof_id>?proofItemId=<item>&proofRevisionId=<revision>` | One version of a proof item |
| Create proof | `/dashboard/proof/new` | Request a new mockup from the art team |
| Design Tool | Supplied Design Tool URL | Build a mockup and save a finished proof or revision |
| Quoter | `/dashboard/quoter` | Price a product, print method, and quantity without changing a proof |
| Stock checker | `/dashboard/stock-checker` | Live stock by style, color, and size |
| Proofs list | `/dashboard/proofs` | All proofs, newest first; search accepts proof ID, proof name, or client name, not deal ID |
| Promo Codes | `/dashboard/promo-codes` | Search codes and open their saved offer details |
| Create Promo Code | `/dashboard/promo-code/new` | Configure a new offer after the promo skill establishes its terms |
| Product catalog | Supplied Product catalog URL | product search by type, color, and brand |
| Designs gallery | Supplied Designs gallery URL | design inspiration by search, organization, event, style, and print type |
| Help center | Supplied Help center URL | client-facing policies |

## Deal page

- The client card and activity feed show the client's name.
- The activity feed shows CRM history, newest first. Use it for deal facts; Account Manager uses the supplied database conversation for routing in this version.
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
- A proof linked from the deal belongs to that deal, even when its `Client` field shows a different name. Use it as this client's proof; a name mismatch is something for a person to check, not a reason to set the proof aside.
- A product shown as `External …` is apparel Fresh Prints does not sell itself. It has no catalog or quoter price, so the price panel shows $0.00, and self-checkout is usually blocked. Sasha cannot price, order or change it.

## Create proof wizard

The three steps are Proof Details, Product Info, and Print Info & Price Estimate. Each next button remains disabled until required fields are complete.

1. Proof Details contains Proof Title, Client, Deal, Related Campaign, and a flash-order toggle. Select an actual autocomplete option for Client. Select the supplied deal from the Deal dropdown. Choose `None` for Related Campaign when none applies. Only deals in Lead stage or later appear.
2. Product Info contains searchable Style Code and Color controls. Product images and a stock warning appear after both are selected.
3. Print Info & Price Estimate contains print type and method, color count or method-specific options, `Describe the Art & Location`, optional add-ons, licensing, Estimate Quantity, and `Submit`.

`Describe the Art & Location` is the rich-text box below that heading and is the field the art team reads. `Submit` remains disabled until the description, both licensing fields, and estimate quantity are complete. A successful submission links the proof to the deal and shows `Original Proof` with pending status.

## Design Tool

A proof's Design Tool link opens a new browser page. Existing proofs show their proof item and revision in the editor.

For a design chosen from the gallery, open its detail page and inspect `Customize This`. Follow it only when its origin matches the supplied Design Tool URL. Verify that the selected design loaded, then inspect the canvas and its objects before editing. Do not assume a generic footer link uses the configured environment.

If the deal already has a proof, use that proof's editor to save to the same proof. Its Designs panel can load gallery artwork. Compare the imported artwork's size and placement with the gallery reference before saving; importing does not guarantee the same position. The wearer's left chest is on the viewer's right in the front view.

### New proof item on an existing proof

When the client needs an additional mockup on the same proof record and the CRM proof offers create-new-item:

1. Work from the CRM proof page (`/dashboard/proof/<proof_id>`). Close open Save menus, side panels, and overlays first; an open Design Tool Save menu or a covering `proof-information` strip can intercept clicks.
2. Prefer the stable control `data-testid="proof-create-new-item-button"` (Create a new proof item). If that control is covered or not visible, use `Create in Design Tool` from the proof page and identify the newly opened Design Tool tab by its URL (`proofId` / `proofItemId`), not by assuming it is the first or last page.
3. After the new editor opens, close the superseded Design Tool tab for the earlier item so only one heavy editor stays open.
4. Place artwork, then save once through the documented Save menu path. Do not click Top `Save` repeatedly while a prior click is still pending.
5. Reopen the CRM proof and confirm the new item or revision label before telling the client the mockup is ready.

### Interface and selection

- The left rail contains Add Text, Upload, Designs, Clipart & logos, and Greek. The right panel contains product, color, print type, and method.
- Many controls are tiles. Locate them through their rendered role, name, text, or stable test ID. Canvas artwork is visual content and must be inspected with a screenshot.
- A single canvas click selects an object. Opening the selection panel can move the artwork. After selecting, opening a panel, scrolling, or changing the viewport, capture and view a fresh screenshot before choosing another canvas coordinate.
- Double-click the visible letters to enter text editing. The design's center or bounding box can contain empty space. Clicking empty canvas exits editing and can deselect the object.
- `Select part of your design to make edits in this panel` means no part is selected. It does not prove that the artwork is an image or that text cannot be edited. Capture and view the current canvas, select the target again, then double-click the letters at their current position.
- Disabled Size & Placement fields can retain a prior object's values and do not prove that object is selected.
- `Change Product` opens the product picker. Its product search placeholder is `Try "T-Shirt"`; `Try "Alpha"` searches designs. Choose the matching product, then `Switch to This`.

### Text and images

- `Add Text` creates `TEXT`. For a wording revision, edit the existing text object instead of adding another.
- Before replacing wording, confirm that `Text Tool` is open and an editable text field contains the intended original wording. A generic Size & Placement panel is not confirmation of text editing. Read the field value rather than relying only on page body text.
- Replace the content of the confirmed text field with the exact requested wording. Exit editing and view a fresh screenshot to verify the rendered wording, style, and unrelated artwork. Reselect the text and read its value again before saving. Repeat for each text object covered by the request; do not leave other requested wording unchanged.
- If selection still fails after a correction using a fresh screenshot, stop and report that text selection could not be confirmed. Do not call the artwork flattened, rasterized, or uneditable without evidence from the selected object's controls or an explicit application message.
- Complete content, font, and color before size and placement because those changes can alter the bounding box.
- Use Playwright's file-input support only for an approved local artwork file supplied to the task. Target the artwork input identified by `upload-file-input`, not the font input.
- Upload completion is intermediate. Inspect rejection state or the Edit Image dialog, then inspect the placed canvas artwork.
- Uploading creates another object and does not automatically replace the selected image.
- Remove Background and Crop Image change the supplied artwork. Inspect before and after using either control. They cannot lift a print off a photographed garment; for that, see [artwork from photos and screenshots](artwork_from_photos.md).

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
- Confirm an exact product match by observing its matching card and link after the completed search.

## Designs gallery

- The gallery is a source of design inspiration. Its cards are designs shown on garments, not confirmed blank product catalog items or priced proofs.
- The search field updates the URL with `search=<word>`. For example, a search for birthday opened `/designs?search=birthday`. Wait for the results to update before reading names and links.
- The gallery groups filters under Orgs, Events, Styles, and Print Types. The navigation menu and expanded gallery views show more choices than the first visible row. Read the current choices instead of relying on a fixed list.
- Observed Orgs: Sorority, Club, Fraternity, Sports. Observed Events: Rush & Bid, PR & General, Game Day, Parent's Weekend, Philanthropy, Big Little Reveal.
- The expanded Styles view showed Classic, Minimalist, Y2K, Varsity, Typography, 80s & 90s, Cartoons, Handdrawn, Grunge, and 60s & 70s. The Print Types menu showed Embroidery, Screen Print, Patches, Digital, Applique, and Transfers. These are starting terms; confirm current filters and results on the page.
- Filter links use URL parameters such as `filter[orgs]`, `filter[events]`, `filter[styles]`, and `filter[printTypes]`. Use links observed on the current page; do not construct a filter URL from memory.
- Each result card has a design name and detail link. Open a matching card to confirm its title and details before recommending it. Copy the actual card or detail URL and the current gallery search or filter URL when offering more options.
- A design detail page may show organization, event, and style tags plus `Customize This`. Inspect the selected design's customization link and loaded editor to verify what can be changed. Do not treat the pictured garment as a confirmed catalog product or infer a client price from the design page.
- If the search has no relevant results, try one useful broader term or filter, inspect the result, and say so if nothing suitable appears.

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

## Promo Codes

Use [Promo Codes](../../promo-codes/SKILL.md) for when to offer a promo, discount limits, and permitted actions. This section documents the interface. The controls below were observed in V4 QA; inspect the current supplied environment before relying on them. Creation and checkout redemption have not been verified by the exploratory walkthrough.

### Find and inspect a code

- Open Promo Codes from the CRM sidebar. The list has `All`, `Active`, and `Disabled` views, pagination, and a search field labeled `Search by promo code, order #, value`. Search matching behavior is not established; inspect the actual results.
- Use the list only to locate and open a code, never to verify its terms. Its value, status, restrictions, usage limit, and dates are not verification evidence; a usage limit is not a verified remaining-use counter.
- Always click the code and wait for its detail dialog to load. Confirm the dialog names the intended code, then read its account or order restrictions, offer, eligibility, and usage limit. Use the [promo database operations](../../promo-codes/references/check_promo_expiry_and_usage.md) to verify expiry, status, and actual usage before offering it. Database timestamps and usage evidence take precedence over UI displays. If a required term is absent from both sources, report it as unverified rather than filling the gap from the list.
- The observed dialogs have `Close` and `Disable Code`. The disable action is outside the promo skill's scope; edit and re-enable workflows have not been established.

### Configure an offer

1. Use `Create Promo Code` on the list to open the new-code form. The Promo Code field contains the code the customer enters at checkout. A fresh form does not establish a default discount percentage.
2. Select `Regular Order` under `Applies To`, open `Select Account(s)`, and always select `All Accounts`. Do not search for a client name, organization, or individual account in this dropdown. Confirm `All Accounts` remains selected before submitting and verify that targeting in the saved code.
3. Select the intended promo type and fill its conditional fields. Read the current controls after each type change; changing type or targeting can clear selections or remove choices.

| Regular-order type observed | Controls and displayed meaning |
|---|---|
| Percentage | `Discount` input; the UI describes a percentage off the order subtotal |
| Fixed Amount | `Amount` input; the UI describes an amount off the order subtotal |
| Free Decoration | Decorating-method choices and displayed minimum quantities; `Add Decorating Method` requires the current method inputs to be complete |
| Free Print Type | Print categories and their options; screen printing exposes a `Limit to # of colors` control |
| No Guarantee Fee | Common eligibility, targeting, and validity controls; the exact fee value must be established separately |

In the observed fresh form, selecting Regular Order removed Free Shipping and Organizer Pays. Use the current available controls; a historical regular-order shipping code does not prove that the new form supports creating one. Do not switch to a group order to make a type available.

For Free Decoration, observed choices included Custom Names, Custom Numbers, Cover Stitches, Hood Stitch, Studs, Lace, Bows, and Rosettes. For Free Print Type, observed categories included Screen Print, Embroidery, Digital, Applique, Transfers, Patches, Rhinestones, Vinyl, Foil, and Sublimation. Inspect the relevant options and displayed minimums rather than assuming all methods have the same requirements. The actual waived charges and enforcement of those minimums were not tested.

4. Under `Conditions`, inspect `All Products` and the `Style Code(s)`, `Brand(s)`, and `Type(s)` pickers. Search and select actual options when a product restriction is intended. How multiple restriction categories combine, and how mixed-product orders receive the discount, have not been verified; do not invent AND/OR or whole-order semantics.
5. Under `Eligible If`, choose either `Minimum Item Qty.` or `Minimum Order Value`, then enter the threshold chosen by AM. Selecting one replaces the other's input. The latter's helper text refers to subtotal. Exact threshold inclusivity has not been verified; preserve the displayed condition rather than asserting `>` or `>=` behavior.
6. Under `Limit`, choose `Set Total Usage Limit` and enter the limit required by the promo skill. `1x per person` is a different choice; switching to it removes the total-usage input. Do not confuse one use per person with one use total.
7. Inspect the separate `Corporate Website` and `Corporate Campaign` checkboxes under `Shows On` and set them according to the promo skill. Their downstream display behavior has not been tested.
8. Fill `Start Date`, `Start Time (EST)`, `End Date`, and `End Time (EST)` with the chosen validity period. Time inputs are comboboxes. Although the form labels them EST, the creation code handles Eastern daylight-saving offsets and stores UTC. Verify the resulting database instant after saving; follow the database reference for timezone-aware customer wording rather than assuming a fixed offset.

### Submit and verify saved terms

- After changing controls, inspect the settled form and summary. Exploration sometimes showed an old summary immediately after a type change. Confirm the code, type-specific values, product conditions, minimum purchase, account, usage limit, visibility, and dates together.
- An incomplete form showed a disabled `Create Promo Code` button. That does not establish the full required-field set. If disabled or rejected, inspect current field guidance; do not guess numeric limits or repeatedly submit unchanged values.
- When the skill permits creation and the form matches the intended terms, click `Create Promo Code` once. Then return to the list, find the newly created code, and click it to open its saved details. Verify the saved offer in that dialog and run the database expiry/usage check against the client's requirements and promo policy; do not use list values, a toast, or the pre-submit summary as proof of saved terms. If a critical field is not exposed in either verified source, report that verification gap rather than claiming it was checked.
- If saving is uncertain, search for the attempted code before any retry. If a saved code has incorrect terms, follow the skill's unresolved-result rules; do not disable or edit it as an improvised repair.
- If list and detail dates differ, use the database UTC timestamps to establish validity and the database reference's timezone conversion for the client-facing deadline. Do not choose either UI date over the database, fail solely because the two UI dates differ, or claim the display bug is fixed. Confirm that the verified expiry still fits the client's requirements.
- The form tells customers to enter the code at checkout. This is not proof of successful redemption. Stacking, tax/shipping treatment, redemption counting, and cancellation/refund effects remain unverified.

## General interface behavior

- Notification toasts can temporarily cover top-right controls.
- For searchable dropdowns, fill the field and click the matching option. Then verify the control shows the intended selected value. Retry once with a fresh locator if selection did not stick.
- The ellipsis menu near the proof price panel contains both edit and delete controls. Do not select delete.
- When an action opens a new browser page, identify it from the Playwright browser context and operate on that page explicitly.

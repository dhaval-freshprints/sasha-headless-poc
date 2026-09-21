# Workplace

Where things are in the Fresh Prints CRM and how they work. No opinions here. Facts only.
If a line needs the words "always", "never" or "prefer", it belongs in playbook.md instead.

## Pages

| Page | URL | What it's for |
|---|---|---|
| Deal | `/dashboard/sales-pipeline/deal?id=<deal_id>` | Client, stage, due date, est. value, proofs linked, full activity history (emails, notes) |
| Proof | `/dashboard/proof/<proof_id>` | One design on one product. Price at a quantity, delivery estimate, print details, revisions |
| Proof revision | `/dashboard/proof/<proof_id>?proofItemId=<item>&proofRevisionId=<rev>` | A specific version of a proof item |
| Create proof | `/dashboard/proof/new` | Ask the art team for a new mockup on a product the deal doesn't have yet |
| Design Tool | `https://dt-qa.internal-fp.com/` | Build a mockup yourself on a product and save it as a finished proof on the deal, no art team |
| Quoter | `/dashboard/quoter` | Price any product / print method / quantity without touching a proof |
| Stock checker | `/dashboard/stock-checker` | Live stock by style, color, size |
| Proofs list | `/dashboard/proofs` | All proofs, newest first. The search box filters by proof id, proof name or client name, not by deal. `?search=` in the URL does nothing |
| Product catalog | `https://www.freshprints.com/products` | Find products by type, colour, brand. Public, no login |
| Public help | `https://www.freshprints.com/help-center/` | Client-facing policies (samples, budget guidance) |

## Deal page

- The client's name is in the client card and in the activity feed.
- "Proofs" is shown as a count ("2 Proofs") with links to each. If it shows "Proofs" with no count, the deal has no proof. This is the only place that says which proofs belong to which deal; a client can have several deals, so a proof under the client's name elsewhere is not evidence it belongs to this deal.
- The activity feed is the conversation history. Newest at the top.

## Proof page

- Shows one product with: style, color, quantity box, unit price, item total, delivery estimate ("Est. Delivery By"), shipping method, order minimum. The quantity box is the `spinbutton` next to the style code (e.g. "#NKDM3978") in the Price panel; it has no label of its own.
- Typing a quantity in the quantity box recalculates the price on screen. Nothing is saved until "Save Price" is clicked. "Cancel" discards it.
- The delivery panel has Order On / Need By buttons and a Date field. Order On uses an order-placement date and shows estimated delivery dates. Need By uses an arrival deadline and shows order-by dates. Shipping Options opens cards with dates, costs or savings, MOQ and a checkbox. Disabled cards can still show all these numbers. The checkbox's disabled state determines availability; the selected checkbox identifies the current tier. After changing the date or quantity, the recalculated cards provide the current options. Save Price is separate from inspecting a quote.
- Print details are under "Location & Decorations": print type, number of colors, art description per location.
- Revision chips at the top ("Original Proof", "Revision 1", ...) switch which version is shown.
- "Or Submit a Revision Request" (plain text, next to "Revise in Design Tool") opens the revision form. It is hidden while a revision is already pending.
- "Design in Design Tool" / "Revise in Design Tool" opens the Design Tool (below) in a new tab on this proof.

## Create proof wizard (/dashboard/proof/new)

Three steps. The top bar shows Proof Details → Product Info → Print Info & Price Estimate. Each step's "next" button stays disabled until that step's required fields are set.

Step 1, Proof Details:
- Proof Title: a text box whose accessible name is its placeholder, `textbox "e.g. Pi Kapp Rush 2021"`. Write something the art team will recognise, e.g. "<Client> <product> - <event>".
- Client: type the name, then click the `option` that appears (it shows name + email).
- Deal: a dropdown, not a search. Click it (the `Deal` field), then click the deal's `option` by title. Only deals in Lead stage or later are listed; if the deal isn't there, its stage is too early. Say so rather than creating an unlinked proof.
- Related Campaign: required even when none applies. Click it and choose `option "None"`.
- "Proof for Flash Order" toggle: only for rush jobs.
- Then click `button "Product Info"`.

Step 2, Product Info:
- Style Code: type it, click the `option`. Color: same. Product images and a stock warning appear once both are set.
- Then click `button "Add Location & Design"`.

Step 3, Print Info & Price Estimate (same layout as the revision form):
- Location #1 with Print Type tabs (Screen Print, Embroidery, Digital, Applique, Transfers, Patches, Rhinestones, Vinyl, Foil, ...) and method cards with MOQ. Screen Print Standard is preselected.
- "# of Colors" and "Oversized".
- "Describe the Art & Location" is required and empty. It is a rich-text editor; in the tree it is a `textbox` with no name, placed under the "Describe the Art & Location" heading. This is the only place the art team learns what to make. Put the placement and the exact text or artwork in plain words, e.g. "Front, center chest. The word Droid in white."
- "Upload Ref. Image" for a reference file.
- Decorating Methods (optional add-ons).
- Licensing Info is required, both boxes. Collegiate Marks: choose `None` unless the design carries a college's marks. Organization: choose the client's organization if it is listed; otherwise choose `Other`, and a text box appears for the organization name (it is on the deal page).
- Estimate Quantity is required. Use the client's quantity, or the MOQ if they gave none.
- `button "Submit"` at the bottom right stays disabled until the description, both licensing boxes and Estimate Quantity are filled. "Another Proof Item" adds a second product to the same proof.
- After Submit, the deal page shows the new proof under its proof count and the proof page shows "Original Proof" with a pending status.

## Design Tool (https://dt-qa.internal-fp.com/)

A canvas editor that saves a finished mockup to the CRM. It uses the CRM login on a different host. `navigate` opens a new design; a proof's "Design in Design Tool" / "Revise in Design Tool" opens that proof in a new tab. `switch_tab` selects the opened tab. Existing proofs show their item number and revision in the editor.

### Page and selection

- Left rail: Add Text, Upload, Designs, Clipart & logos, Greek. Right panel: product, colour, print type and method. Many controls are clickable tiles rather than native buttons; `read_page`/`find` can expose them as buttons. The artwork itself is canvas content, visible through `screenshot`/`zoom`, not text in the page tree.
- Opening a left panel changes the canvas position. Coordinates come from a current screenshot. A single click selects an object and shows its editing panel; double-clicking text enters text editing. Clicking empty canvas leaves editing and can deselect the object. Disabled Size & Placement fields can retain the last object's values; they do not establish a current selection.
- A "Need help?" panel can cover the rail. Its Collapse control closes it.
- A new design starts on a Comfort Colors tee. Change Product opens the product picker. Its search placeholder is `Try "T-Shirt"`; `Try "Alpha"` searches designs instead. Search by style, choose the matching product, then Switch to This. The right panel shows the selected product and style. Colour tiles are under the product name; their labels include colour and stock notes.
- Print Type and its method cards are in the right panel. The selected state can be checked visually. An image upload's print-type choice can also change the method for existing artwork at that location.

### Text: add or revise

1. Add Text creates a new placeholder `TEXT`. For a wording revision, select the existing text instead of adding another object.
2. Take a screenshot, double-click inside the words, send `key` with `text: "ctrl+a"`, then `type` the exact replacement text. Select-all belongs inside text editing, not on the whole canvas. Click blank canvas to leave editing, then reselect the text for styling.
3. The left Text Tool panel has a Font combobox, formatting controls and ink swatches. Search a font, select its matching result, and check the selected font and rendered words after the change. Swatches for colours already used can appear separately from the main palette.
4. Finish content, font and colour before size and placement. Font/content changes can change the bounding box, and an immediate field read can lag the rendered size. The exact words must be checked in the canvas image; the CRM Art Description may contain only `Front`.

### Images: upload and inspect

Only a handle supplied with this turn can be uploaded. A filename or URL is not a handle. The source file cannot be opened directly by Sasha; the editor's upload preview and canvas can be inspected after upload.

1. On the Design Tool tab call `attach_file({"file":"file_1","selector":"upload-file-input"})`, substituting the actual handle. This names the artwork input by its data-testid. The page also contains a font file input, and the upload helper chooses the last file input if no selector is supplied. The visible Upload tile opens an OS picker that Sasha cannot answer; `attach_file` supplies the file directly to the hidden input.
2. The Upload tooltip lists JPEG/JPG and PNG up to 10 MB, and SVG with no listed size limit. Uploading in progress is an intermediate state. Inspect the resulting page for a rejection or the Edit Image modal before continuing. A 64×64 PNG reached the canvas without a visible quality warning in the QA walkthrough; acceptance alone does not establish print quality.
3. Edit Image shows the artwork, Remove Background, Crop Image and Next. Checkerboard outside an opaque white rectangle does not mean the rectangle is transparent. Remove Background is optional; processing disables Next, then the preview changes. Inspect the edges and interior details after processing, not just a toast. An already transparent image can proceed with Next without background removal.
4. Crop Image enters a separate crop state with handles and accept/cancel icons; Next is disabled during that state. The current Sasha toolset has no drag action. The presence of crop handles alone does not establish that Sasha can apply a requested crop.
5. Next opens Choose Print Type. Read the current "Image You Upload" row and select the desired type's pill, not its heading or the table's scroll arrows. The QA table says Screen Print/Embroidery need vectorization and may lose details; Digital and Transfers say they will not be vectorized. These are the tool's descriptions, not a guarantee of output fidelity. After processing, inspect the placed image and the selected method in the right panel.
6. Upload adds another object, including when an existing image was selected. It does not automatically replace that image. The selected image exposes Edit Image, Remove BG, Crop, Size & Placement, Arrange and Align.

### Size, placement and replacement

- Width, Height and Distance from Collar are in inches; Rotate controls rotation. `form_input` edits a numeric field and blurs it. Check the rendered object and its size label after a change. If the field and canvas disagree, leave editing, reselect the object and read again before correcting it.
- Width and Height scale together in the observed text/image workflows. Set one dimension, then read the resulting other dimension; setting both independently can rescale the first. Transparent margins may be trimmed on import: use the editor's resulting aspect ratio, not an assumed square.
- A useful order is content/font/image edits, size, alignment, then Distance from Collar. Center can also reset the vertical distance; check both axes after alignment and set the collar distance last. Clicking only the Center label did not apply alignment in the walkthrough; the icon/button body did. A fresh screenshot supports a coordinate click when the text target has no effect.
- With the canvas object selected and no text/numeric field being edited, arrow keys nudge it. `key` accepts, for example, `{"text":"right","repeat":5}`. Small batches followed by screenshots allow horizontal positioning without drag. The step is not an inch measurement. Vertical/Horizontal are separate controls from Align Center/Middle.
- The Gildan G880 front has separate chest print regions. In QA, Right nudges stopped at the edge of the screen-left region. To move the existing artwork across: right-click the artwork and choose Cut, right-click the desired spot in the other chest region, then choose Paste. This placed the artwork around the clicked spot while preserving its 3.50-inch width; Distance from Collar changed and needed resetting. For a front-facing garment, the wearer's left chest is on screen-right. Inspect the resulting artwork, size and collar distance before saving. This sequence was saved and reopened as Revision 1 of QA proof 576469; it does not establish identical behavior for every garment.
- To replace an image, note the old object's size/placement, upload and inspect the new image, then right-click the old object on the canvas and choose Delete from its context menu. This removes that canvas object, not the CRM proof. The editor's Undo control restored a removed object in the walkthrough. Check that the old artwork is gone and unrelated text/art remains, then size and position the new image.

### Save a new proof or a revision

- Top Save opens a menu; opening or closing it does not save. A new design's menu contains Design Title, Deal combobox, auto-filled Client, Send a Copy to Client Yes/No, Est. Quantity, Price Per Item, Block Checkout, and a second Save button. Deal search accepts a title or id; a transient empty result can precede the matching option. Choosing the actual option links the deal and fills Client. An empty canvas leaves the final Save disabled.
- An existing proof's menu instead offers Save as new revision and keeps the existing proof link. Send a Copy to Client and quantity still appear. The selected Yes/No state is visually highlighted; a previous selection is not evidence of the current one.
- The final action can show Saving... before completion. A completed save updates `?proofItemId=<id>` and the revision label. A Design Saved toast may appear, but it is transient. Reopening the CRM proof shows the persisted mockup and revision chips; a new proof is also linked on the deal. The proof lists product, colour and print method. Art Description and attachment thumbnails are not substitutes for inspecting the mockup itself.
- Save can trigger Greek Marks Found / Licensed Marks Found dialogs. The QA text-only revision did so. The dialog offers "No marks in your design?" and can then show a "Sounds Good" acknowledgement. These prompts are a separate state from saving; the label is not evidence that the artwork contains those marks.

## Stock checker (/dashboard/stock-checker)

Live stock by size for one product and colour. The most direct answer to "do you have my sizes".

- One search box at the top, an unnamed `textbox`, the first editable field on the page. Type the style code, then click the `option` that appears, e.g. `option "NKDC1963 - Nike Dri-FIT Micro Pique 2.0 Polo"`.
- A row of colour swatches appears. The swatches are not in the tree and `find` cannot see them; their names are hover tooltips only. Take a screenshot and click the swatch by coordinate; hover first if you need to confirm which colour it is. "All Colors" is the first swatch.
- Clicking a swatch renders a table. In the tree it's `row "<Colour> <XS> <S> <M> <L> <XL> <2XL> <3XL> <4XL> <blank cost> <restock date>"`, with one `cell` per value. `get_page_text` gives the same numbers in order after "Colors / Dist.".
- "No Restock Date" means no supplier restock is scheduled. A date there is the supplier's estimate.
- Nothing on this page is saved. It only reads.

## Revision request form

- Opens as an overlay titled "Placing a Revision Request".
- Print Type tabs: Screen Print, Embroidery, Digital, Applique, Transfers, Patches. Selecting one shows its sub-methods (Standard, Puff Ink, ...) and MOQ.
- "# of Colors" and "Oversized" are per location.
- "Describe the Art & Location" is a rich-text box. It starts with the current location name (e.g. "Front"). This is the field the art team reads. In the tree it is a `textbox` under the "Describe the Art & Location" heading.
- "Upload Ref. Image" accepts a reference file (20 MB max).
- "Change Product Info" (left) changes style code and color. "Change Licensing Info" (right) changes collegiate marks and organization.
- "Estimate Quantity" is at the bottom.
- "Submit Revision Request" (blue, bottom right) submits. After submit the proof shows a new "Revision N" chip and the page returns to the proof.

## Product catalog (freshprints.com/products)

The place to find products when the client asks for something not on the deal ("green polos", "a hoodie", "hats"). The quoter cannot search by colour; this page can.

- Go straight to a filtered URL: `/products?search=<word>&mainColorGroup=<Colour>`. Example: `/products?search=polo&mainColorGroup=Green`. Colour groups: White, Grey, Black, Red, Brown, Orange, Yellow, Green, Blue, Purple, Pink.
- Or use the page: textbox "Try “T-Shirt”" is the search box (type, press Enter); the colour swatches are buttons named by hex (#0CA80C is green, #2049C3 blue, #FF2B2B red, #000000 black, #FFFFFF white); category links are named "filter for Shirts", "filter for Hoodies", etc.
- Results are product cards: a product name, a link to the product, and one colour tag per colour it comes in. "mto" after a colour means made-to-order (longer lead time).
- Each card's link URL contains the brand and style code: `/products/nike-nkdc1963-dri-fit-micro-pique-20-polo?color=Gorge%20Green` means brand Nike, style NKDC1963, colour Gorge Green. That style code is what the quoter's Style Code box wants.
- The page has no prices. To price a product from here, take its style code and colour to the quoter.
- Pick 2–3 candidates that fit the ask, then price them. Don't price all 27.
- Before leaving the catalog, call `inspect_catalog_product` for each exact style you plan to quote. `verified` means a matching public product link was observed. `not_found` means an exact completed search had no matching link. `unknown` means the page was ambiguous or incomplete.

## Quoter page

Prices any product + decoration + quantity without touching a proof. Nothing here is saved.

Top bar (updates live as you fill the form):
- Qty box (`textbox "e.g. 12"`, named by its placeholder). Fill this first.
- Unit Price, Item Total, Item Shipping Total, Item Sales Tax Total ("TBD" until a zip is entered via "Enter Zip Code"), GPM, Total.
- Stock Levels per size (S, M, L, XL, 2XL, 3XL, 4XL) appear once a style and color are chosen.

Product Info (left):
- Style Code: a search box (`[text] 'Style Code'`). Type the style, then click the matching `option` that appears (e.g. `option "Comfort Colors C1717"`).
- Color: same pattern. Type, then click the `option`.
- MOQ for the product shows under Color.

Licensed Marks (left, below): Collegiate Marks and Greek Marks, each a Yes / No pair of radios. Both default to No. Choosing Yes reveals a search box for the school or organization. Licensing changes the price, so match what the proof shows under Greek Licensing / Collegiate Licensing.

Location #1 (centre). "Add Location" adds a second print location.
- Print Type tabs: Screen Print, Embroidery, Digital, Applique, Transfers, Patches. Click by text.
- Under the tab, a row of method cards with MOQ (Screen Print: Standard, Puff Ink, Neon Ink, Metallic & Glitter Ink, Glow in the Dark Ink, Water based Ink. Embroidery: Standard, Puff, Metallic & Glitter). Click by text.
- Screen Print shows "# of Colors" and an "Oversized" toggle.
- Embroidery shows "Estimated Size" cards instead: Chest/Back - Large, Chest/Back - Small, Top Pocket / Sleeve, Pants Leg, Pants Pocket, and more to the right. These are `button`s named by their label. Price depends on which is chosen.

Decorating Methods (centre, below): Custom Names, Custom Numbers, Cover Stitches, and others. Optional add-ons.

Delivery (right):
- "Order On" / "Need By" toggle with a date box.
- Shipping tiers appear as cards showing a date, Item Shipping Total and MOQ: Standard (free), Expedited, Fresh Prints Flash, Individual Shipping. Cards can be disabled while still showing dates and prices. Order On shows estimated delivery dates; Need By shows order-by dates. The chosen tier drives the top-bar Item Shipping Total. Individual Shipping is internal; it is never shown to clients.
- Colour names ending in " mto" are made to order, minimum 50.

Reading the answer: after qty, style, color and a print method are set, the top bar shows Unit Price and Item Total. `get_page_text` returns them exactly. Before that they show as "— —".

Before using a current-turn quoter price in the client reply, call `get_page_text` after the fields and recalculation have settled. Confirm that the exact style, color, quantity, price, tax, shipping and delivery shown on the page match the reply.

## UI behaviour

- Notification toasts appear top-right and can cover buttons for a few seconds.
- Use `form_input` for searchable dropdowns. If it reports an unverified selection, inspect the field with `find` or `read_page`; do not append text with `type` or choose an option by coordinate. If the requested value is not selected, get a fresh field ref and retry `form_input` once.
- The "⋯" menu next to the price panel contains an edit and a **delete** control. A delete confirmation dialog says "Are you sure you want to delete the proof?".
- Some links open a new tab. The result tells you a tab opened; switch to it with `switch_tab`.

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

A canvas editor. Saving creates a proof on the deal in one step: the proof is Done at once, nothing goes to the art team. Same login as the CRM; it is a different host, so open it with `navigate`.

- The controls are chips and tiles; the tree lists them as `button "..."`. The garment and the design are on a canvas that is not in the tree: a screenshot shows it.
- A "Need help?" chat panel at the bottom left opens by itself on stock warnings and covers the left rail. `button "Collapse"` closes it.
- Left rail: `button "Add Text"`, `button "Upload"`, `button "Designs"` (a gallery of past designs; ignore it), `button "Clipart & logos"`, `button "Greek"`.
- Right panel: the current product (name, style code, minimum), its colour tiles, Print Type tabs (Screen Print, Embroidery, Digital, ...) and method cards (Standard, Puff Ink, ...), all buttons named by their label. A new design starts on a Comfort Colors tee.
- Change product: `button "Change Product"` opens a picker. Its search box is `textbox "Try "T-Shirt""` (the other search box, `textbox "Try "Alpha""`, is the designs gallery). Type a style code with `form_input`, e.g. NKDC1963; each result is a `button` named by the product, e.g. `button "Nike Dri-FIT Micro Pique 2.0 Polo"`. Click it, then `button "Switch to This"`. The picker closes and the right panel shows the new product.
- Colour: the tiles under the product name are `button "<Colour> / <stock note>"`, e.g. `button "Black / Selling Out Fast in S"`. Click one to set the garment colour. The first read of a page with tiles takes a few seconds.
- Text: `button "Add Text"` puts a placeholder "TEXT" object on the garment, about 11 inches below the collar. To write in it: take a screenshot, `double_click` the object by coordinate, `key ctrl+a`, `type` the words. The Text Tool panel on the left then shows `combobox "Font"`, format buttons, a colour palette, and `spinbutton "Width"`, `"Height"`, `"Distance from Collar"` and `"Rotate"` in inches; `form_input` on Distance from Collar moves the text (about 3 for the chest). The panel never shows the words; only the canvas does. `zoom` on the object to read them back.
- Save: `button "Save"` at the top right opens a form: `textbox "Design Title"`, `combobox "Deal"` (type the deal title or id with `form_input`; the options take a few seconds), `textbox "Client"` (fills itself once the deal is chosen), "Send a Copy to Client" as `button "Yes"` / `button "No"` (starts on Yes; Yes emails the client), Est. Quantity, Price Per Item, and a second `button "Save"` at the bottom of the form. That bottom Save stays disabled while the canvas is empty.
- After Save a "Design Saved" toast shows and the URL gains `?proofItemId=<n>`. The proof is on the deal page under its proof count, its status is Done, and its page shows "Original Proof" with the mockup image, the product and colour, the print method and the font. Its Art Description says only the location ("Front"); the words are only in the image. Licensing shows whatever the client record holds; the tool asks for none.
- Changes to a Design Tool proof: "Revise in Design Tool" on the proof page, which opens the tool on that proof; Save there offers "Save as new revision".

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
- Shipping tiers, one selectable card each, showing Est. Delivery By, Item Shipping Total and MOQ: Standard (free), Expedited, Fresh Prints Flash, Individual Shipping. The chosen tier drives the top-bar Item Shipping Total. Individual Shipping is internal; it is never shown to clients.
- Colour names ending in " mto" are made to order, minimum 50.

Reading the answer: after qty, style, color and a print method are set, the top bar shows Unit Price and Item Total. `get_page_text` returns them exactly. Before that they show as "— —".

## UI behaviour

- Notification toasts appear top-right and can cover buttons for a few seconds.
- The "⋯" menu next to the price panel contains an edit and a **delete** control. A delete confirmation dialog says "Are you sure you want to delete the proof?".
- Some links open a new tab. The result tells you a tab opened; switch to it with `switch_tab`.

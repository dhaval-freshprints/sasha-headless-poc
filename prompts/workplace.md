# Workplace

Where things are in the Fresh Prints CRM and how they work. No opinions here. Facts only.
If a line needs the words "always", "never" or "prefer", it belongs in playbook.md instead.

## Pages

| Page | URL | What it's for |
|---|---|---|
| Deal | `/dashboard/sales-pipeline/deal?id=<deal_id>` | Client, stage, due date, est. value, proofs linked, full activity history (emails, notes) |
| Proof | `/dashboard/proof/<proof_id>` | One design on one product. Price at a quantity, delivery estimate, print details, revisions |
| Proof revision | `/dashboard/proof/<proof_id>?proofItemId=<item>&proofRevisionId=<rev>` | A specific version of a proof item |
| Quoter | `/dashboard/quoter` | Price any product / print method / quantity without touching a proof |
| Stock checker | `/dashboard/stock-checker` | Live stock by style, color, size |
| Proofs list | `/dashboard/proofs` | Search proofs by deal id or title when a deal page hides them |
| Public help | `https://www.freshprints.com/help-center/` | Client-facing policies (samples, budget guidance) |

## Deal page

- The client's name is in the client card and in the activity feed.
- "Proofs" is shown as a count ("2 Proofs"). The proof links may be collapsed; if they are, use the Proofs list page and search by deal id.
- The activity feed is the conversation history. Newest at the top.

## Proof page

- Shows one product with: style, color, quantity box, unit price, item total, delivery estimate ("Est. Delivery By"), shipping method, order minimum.
- Typing a quantity in the quantity box recalculates the price on screen. Nothing is saved until "Save Price" is clicked. "Cancel" discards it.
- Print details are under "Location & Decorations": print type, number of colors, art description per location.
- Revision chips at the top ("Original Proof", "Revision 1", ...) switch which version is shown.
- "Or Submit a Revision Request" (plain text, next to "Revise in Design Tool") opens the revision form. It is hidden while a revision is already pending.
- "Design in Design Tool" opens the Design Tool in a new tab. It is a canvas editor. The proof's text cannot be read from the tree there.

## Revision request form

- Opens as an overlay titled "Placing a Revision Request".
- Print Type tabs: Screen Print, Embroidery, Digital, Applique, Transfers, Patches. Selecting one shows its sub-methods (Standard, Puff Ink, ...) and MOQ.
- "# of Colors" and "Oversized" are per location.
- "Describe the Art & Location" is a rich-text box. It starts with the current location name (e.g. "Front"). This is the field the art team reads. It shows in EDITABLE FIELDS as `[richtext] 'Describe the Art & Location'`.
- "Upload Ref. Image" accepts a reference file (20 MB max).
- "Change Product Info" (left) changes style code and color. "Change Licensing Info" (right) changes collegiate marks and organization.
- "Estimate Quantity" is at the bottom.
- "Submit Revision Request" (blue, bottom right) submits. After submit the proof shows a new "Revision N" chip and the page returns to the proof.

## Quoter page

Prices any product + decoration + quantity without touching a proof. Nothing here is saved.

Top bar (updates live as you fill the form):
- Qty box (placeholder "e.g. 12", shows in EDITABLE FIELDS as `[tel] 'e.g. 12'`). Fill this first.
- Unit Price, Item Total, Item Shipping Total, Item Sales Tax Total ("TBD" until a zip is entered via "Enter Zip Code"), GPM, Total.
- Stock Levels per size (S, M, L, XL, 2XL, 3XL, 4XL) appear once a style and color are chosen.

Product Info (left):
- Style Code: a search box (`[text] 'Style Code'`). Type the style, then click the matching `option` that appears (e.g. `option "Comfort Colors C1717"`).
- Color: same pattern. Type, then click the `option`.
- MOQ for the product shows under Color.

Licensed Marks (left, below): Collegiate Marks and Greek Marks, each a Yes / No pair. The radios have no accessible name; use `click_at` on the Yes or No button from the screenshot. Both default to No. Choosing Yes reveals a search box (`Collegiate Marks` / `Greek Marks` in EDITABLE FIELDS): type, then click the `option`. Licensing changes the price, so match what the proof shows under Greek Licensing / Collegiate Licensing.

Location #1 (centre). "Add Location" adds a second print location.
- Print Type tabs: Screen Print, Embroidery, Digital, Applique, Transfers, Patches. Click by text.
- Under the tab, a row of method cards with MOQ (Screen Print: Standard, Puff Ink, Neon Ink, Metallic & Glitter Ink, Glow in the Dark Ink, Water based Ink. Embroidery: Standard, Puff, Metallic & Glitter). Click by text.
- Screen Print shows "# of Colors" and an "Oversized" toggle.
- Embroidery shows "Estimated Size" cards instead: Chest/Back - Large, Chest/Back - Small, Top Pocket / Sleeve, Pants Leg, Pants Pocket, and more to the right. These are `button`s named by their label. Price depends on which is chosen.

Decorating Methods (centre, below): Custom Names, Custom Numbers, Cover Stitches, and others. Optional add-ons.

Delivery (right):
- "Order On" / "Need By" toggle with a date box.
- Shipping tiers, one selectable card each, showing Est. Delivery By, Item Shipping Total and MOQ: Standard (free), Expedited, Fresh Prints Flash, Individual Shipping. The chosen tier drives the top-bar Item Shipping Total.

Reading the answer: after qty, style, color and a print method are set, the top bar shows Unit Price and Item Total. `read_text` returns them exactly. Before that they show as "— —".

## UI behaviour

- Notification toasts appear top-right and can cover buttons for a few seconds.
- The "⋯" menu next to the price panel contains an edit and a **delete** control. A delete confirmation dialog says "Are you sure you want to delete the proof?".
- Some links open a new tab. You are always shown the newest tab.

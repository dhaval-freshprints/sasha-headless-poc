# Artwork from reference images

Clients may provide artwork directly or embed it in another image. Treat the supplied material as a reference for the intended design, regardless of its source, format, or surrounding content.

## Inspect the source

Open every supplied file before choosing an approach. Use the attachment metadata in the task (dimensions, transparency, format) together with a visual inspection. Determine whether the intended artwork is already usable or must be isolated, reconstructed, or combined from multiple elements.

Preserve the client's intent:

- retain all intended wording, graphics, orientation, and overall composition; preserve colors and proportions except for the readability improvements below;
- exclude surrounding content that is not part of the intended design;
- preserve photographic content without changing its subjects;
- use the highest-quality available source for each element; and
- do not assume that one processing method works for every image.

## Inventory and separate reference elements

For every photo or screenshot used as a design reference, inventory all intended graphics and text blocks before extraction or editing. Read wording from the original, not from a generated image. Preserve spelling, capitalization, line breaks, and relationships between elements. Ask for clarification or a clearer source when wording is unreadable; never invent it.

Build each independent graphic as a separate image object and each heading or caption as a separate editable text object. For example, a scooter is one graphic; the large script heading, small heading, and each bottom caption are text objects. Inventory the actual source rather than hardcoding this example's words. A text-only reference needs no extraction. Do not upload a flattened recreation of the whole reference or rasterize lettering to avoid font matching.

Leave clean production-ready artwork intact unless the client requests edits. Preserve actual photographic subjects using original pixels, never regeneration. For a reference containing both photos and text, retain the photo as an image and rebuild its surrounding text separately.

## Choose one suitability route

Before uploading, select exactly one route for each used attachment and write it to `/workspace/artifacts/artwork_decisions.json`:

| Route | When to use |
| --- | --- |
| `direct` | Isolated vector files, or clean standalone rasters with adequate effective PPI at the intended Design Tool print width. |
| `extract` | Graphics embedded in a shirt, screenshot, UI, background, perspective, folds; or raster resolution too low for direct upload. Extract graphics separately; rebuild text in Design Tool. |
| `preserve_photo` | Intended photographic content (people, places, animals, photos). Keep original pixels; use Design Tool crop only. Never regenerate photographic subjects. |
| `request_source` | Ambiguous or unreadable artwork, or photography below the minimum quality that cannot be faithfully prepared. |

Effective PPI is `usable artwork pixel width / print width in inches`. Direct raster uploads must meet the configured minimum (`ARTWORK_MIN_PRINT_PPI`, default 150). Aim for the target (`ARTWORK_TARGET_PRINT_PPI`, default 300) when possible. After final placement, recheck effective PPI if the Design Tool width changed.

Extraction should use the largest fitting `--shape` (`wide`, `tall`, or `square`) and must also meet the minimum PPI at the selected Design Tool width.

## Forbidden local enlargement

Never create print artwork by cropping a tiny region and enlarging it with Pillow, ImageMagick, canvas, or similar tools. Upscaling cannot invent missing detail; quality is calculated from source pixels. A screenshot of a shirt with a small emblem must use `extract` (or `request_source` if extraction cannot reproduce it), not a Lanczos-upscaled crop.

Upload only files under `/workspace/client-files` (originals or host-broker extract outputs). The container cannot write approved artwork elsewhere for upload.

## Prepare usable artwork

Use the available artwork-processing capabilities that best fit the source. Do not depend on a particular filename, directory, command, image format, layout, background, or extraction technique.

For each independent graphic needing recreation, call the host extractor on the original source with `--keep` naming only that graphic and explicitly excluding all lettering and other graphics. For example:

```
python /opt/sasha/extract_artwork.py /workspace/client-files/file_1.png --keep "Only the scooter illustration, preserving its colors and proportions. Exclude all lettering, captions, and other graphics." --shape tall --action-id source1-scooter-v1
```

Use a distinct action ID for each element or changed extraction request; reuse it only for an identical retry. Each call returns its own approved local path. Inspect every output against the original: no missing parts, added content, leftover text, distortion, or unintended recoloring. Refine an inadequate extraction with a new action ID. Never use the output's text to resolve unclear source wording. Recreate only graphics that can be reproduced faithfully; request a better source when fidelity is impossible. Photographic subjects always retain original pixels.

## Rebuild editable text and match fonts

Use Design Tool's Add Text for each inventoried text block. Enter the exact source wording and inspect the text controls and available font choices. Compare plausible fonts by letter shapes, script or serif style, weight, width, slant, and spacing, then choose the closest available visual match. Use the font names actually observed in the tool; never invent a name or claim an exact match without evidence.

Check the rendered text against the original at a readable zoom, then at garment scale. Prefer the closest editable font even when the exact font is unavailable; disclose a noticeable substitution. Set content, font, and color before size and placement. Verify each text field contains the intended wording. If the tool cannot create or save editable text after the documented selection checks, follow the normal art-team request workflow; do not silently flatten it or claim completion.

## Build the proof

Place each prepared element in the location requested by the client. Preserve the design's composition while adapting its size to the printable area. Follow [Workplace](workplace.md) for application-specific proof creation and verification.

Verify the complete proof before drafting:

- every requested location is present;
- the artwork matches the supplied reference;
- every inventoried graphic and text block is present exactly once, with no leftover lettering in extracted graphics;
- text fields contain the correct wording and remain independently editable;
- important content remains readable and unobstructed at garment scale;
- effective PPI still meets the minimum at the final print width; and
- no temporary or unintended image content appears.

## Improve design quality

Inspect fresh screenshots close up and at garment scale on the selected garment color. Check text-to-shirt contrast, small captions, overlapping objects, alignment, spacing, and balance. Automatically improve obvious readability issues with the smallest effective adjustment, such as lighter lettering on a dark tee, larger small text, or corrected spacing. Recheck after each adjustment; fix any resulting collisions or placement changes.

Preserve wording, graphics, requested location, print method, and overall character. Honor explicit exact-color, brand, size, and layout constraints. When those constraints prevent an improvement, preserve them and explain the remaining issue with a concrete recommendation. Do not silently override a constraint or create an extra alternative by default. A visual preview does not establish print accuracy or production readiness.

Save only after reviewing the full composition. Reopen the saved proof and its editor to confirm persistence, separate image objects, and editable text values. Record actual findings, applied adjustments, and remaining issues in the audit. If verification fails, report the limitation or use the existing art-team workflow rather than claiming the design is ready.

## Communicate limitations

Tell the client briefly when artwork was recreated from their photo or screenshot, using ordinary design language such as “I recreated the artwork from your photo.” Follow Crafting Rules for the reply. Explain noticeable differences or meaningful quality limitations specifically, without narrating extraction tools or processing steps. Ask them to verify a visual detail only when it is uncertain and needs their input; request a production-ready source when improved print quality or exact reproduction requires one. Do not add a generic approval question to every mockup update.

Explain meaningful readability changes, noticeable font substitutions, and unresolved issues honestly under Crafting Rules. Record material font/color choices and unresolved limitations in deal notes. Record that reference-based artwork was used and identify the relevant client-supplied source without recording temporary local paths.

## Audit record

For each used attachment, append an object to `/workspace/artifacts/artwork_decisions.json` with at least:

- `handle` or source path
- `route` (`direct` | `extract` | `preserve_photo` | `request_source`)
- `reason`
- `source_width_px` / `source_height_px` (when known)
- `print_width_inches`
- `effective_ppi`
- `uploaded_path` (under `/workspace/client-files`, or empty when requesting a source)

Recheck and update the record after final placement if width changes.

For reference reconstruction, retain the attachment-level fields above and add:

- `elements`: an array with a stable `id`, `kind` (`graphic`, `photo`, or `text`), source description, and intended relative placement for every element. Text entries include exact `text`, observed `font_candidates`, `selected_font`, `font_match_reason`, and `editable_verified`. Image entries include their own `route`, approved `uploaded_path`, final `print_width_inches`, and `effective_ppi` (null for vectors or unknown values, with the reason). Text has no raster PPI or uploaded path.
- `visual_review`: `findings`, `changes`, and `remaining_issues` arrays plus `saved_verified`. Record observed facts; do not mark editability or persistence verified before checking the reopened editor.

For mixed sources, the attachment route describes the primary image preparation; per-element routes describe the individual images. For text-only references, use `direct` to indicate transcription from the source, leave attachment `uploaded_path` empty and `effective_ppi` null, and explain that no raster was uploaded. Keep all records in the existing JSON array; no separate manifest is needed.

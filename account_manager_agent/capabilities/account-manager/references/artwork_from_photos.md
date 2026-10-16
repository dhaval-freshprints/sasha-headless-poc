# Artwork from reference images

Clients may provide artwork directly or embed it in another image. Treat the supplied material as a reference for the intended design, regardless of its source, format, or surrounding content.

## Inspect the source

Open every supplied file before choosing an approach. Use the attachment metadata in the task (dimensions, transparency, format) together with a visual inspection. Determine whether the intended artwork is already usable or must be isolated, reconstructed, or combined from multiple elements.

Preserve the client's intent:

- retain all intended text, graphics, colors, proportions, orientation, and spatial relationships;
- exclude surrounding content that is not part of the intended design;
- preserve photographic content without changing its subjects;
- use the highest-quality available source for each element; and
- do not assume that one processing method works for every image.

## Choose one suitability route

Before uploading, select exactly one route for each used attachment and write it to `/workspace/artifacts/artwork_decisions.json`:

| Route | When to use |
| --- | --- |
| `direct` | Isolated vector files, or clean standalone rasters with adequate effective PPI at the intended Design Tool print width. |
| `extract` | Text, line art, or logos embedded in a shirt, screenshot, UI, background, perspective, folds; or raster resolution too low for direct upload. Use the host-brokered extractor. |
| `preserve_photo` | Intended photographic content (people, places, animals, photos). Keep original pixels; use Design Tool crop only. Never regenerate photographic subjects. |
| `request_source` | Ambiguous or unreadable artwork, or photography below the minimum quality that cannot be faithfully prepared. |

Effective PPI is `usable artwork pixel width / print width in inches`. Direct raster uploads must meet the configured minimum (`ARTWORK_MIN_PRINT_PPI`, default 150). Aim for the target (`ARTWORK_TARGET_PRINT_PPI`, default 300) when possible. After final placement, recheck effective PPI if the Design Tool width changed.

Extraction should use the largest fitting `--shape` (`wide`, `tall`, or `square`) and must also meet the minimum PPI at the selected Design Tool width.

## Forbidden local enlargement

Never create print artwork by cropping a tiny region and enlarging it with Pillow, ImageMagick, canvas, or similar tools. Upscaling cannot invent missing detail; quality is calculated from source pixels. A screenshot of a shirt with a small emblem must use `extract` (or `request_source` if extraction cannot reproduce it), not a Lanczos-upscaled crop.

Upload only files under `/workspace/client-files` (originals or host-broker extract outputs). The container cannot write approved artwork elsewhere for upload.

## Prepare usable artwork

Use the available artwork-processing capabilities that best fit the source. Do not depend on a particular filename, directory, command, image format, layout, background, or extraction technique.

Separate elements when they require different treatment. Reconstruct only the elements that can be reproduced faithfully. Preserve source pixels for elements whose meaning or identity could change if regenerated.

When the artwork cannot be extracted cleanly (for example it is blurry, low resolution, photographed at an angle, folded, partly hidden, or blended into its surroundings) but the design is clear enough to understand, recreate it via the host extractor instead of local upscaling. The recreation should look almost the same as what the client shared: the same wording and spelling, line breaks, fonts or closest matching style, colors, graphics, proportions, and layout, redrawn cleanly and flat. Fill in only what is clearly implied, such as a letter hidden by a fold, and never invent new content. Photographic content is still taken from the source, never recreated.

Inspect every processed result against the source. Confirm that nothing is missing, added, misspelled, distorted, recolored, or incorrectly positioned. Refine the result when fidelity can be improved. Ask the client for a cleaner source only when neither extraction nor recreation can come close to their design, for example when the wording cannot be read.

## Build the proof

Place each prepared element in the location requested by the client. Preserve the design's composition while adapting its size to the printable area. Follow [Workplace](workplace.md) for application-specific proof creation and verification.

Verify the complete proof before drafting:

- every requested location is present;
- the artwork matches the supplied reference;
- important content remains readable and unobstructed;
- effective PPI still meets the minimum at the final print width; and
- no temporary or unintended image content appears.

## Communicate limitations

Tell the client when the proof uses artwork prepared from a reference image. Ask them to verify important visual details and provide a production-ready source when improved print quality or exact reproduction requires one.

Record in the deal notes that reference-based artwork was used and identify the relevant client-supplied source without recording temporary local paths.

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

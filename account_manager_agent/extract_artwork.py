"""Agent-facing artwork extraction: recreate the printed text and graphics from a client's photo
or screenshot of a garment as a clean, transparent PNG, using OpenAI's image model.

Usage: python /opt/sasha/extract_artwork.py /workspace/client-files/file_1.png --keep "..." --shape wide
Prints JSON with the new file's path. The original file is never changed.

The model draws on a flat green background, which this script then makes transparent: drawing
straight onto a transparent background turns white letters and shapes black or boxed.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

CLIENT_FILES = Path("/workspace/client-files")
API_URL = "https://api.openai.com/v1/images/edits"
DEFAULT_MODEL = "gpt-image-1"
TIMEOUT_SECONDS = 300
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
SHAPES = {"square": "1024x1024", "wide": "1536x1024", "tall": "1024x1536"}
INSTRUCTIONS = (
    "This is a photo or screenshot of a garment or mockup with a printed design. "
    "Recreate only the printed design as flat, print-ready artwork on a perfectly flat, solid pure "
    "green (#00FF00) background with no gradient, shadow or texture. "
    "Keep every word spelled exactly as shown, with the same line breaks, fonts, layout and "
    "proportions, and each element's exact colors. Fit the whole design inside the image with a "
    "clear margin, so nothing touches or crosses an edge. "
    "Leave out every real photograph (of people, places or animals) completely and leave its area "
    "empty; never draw or recreate a photo. "
    "Remove the garment, fabric texture, folds, shadows, phone or app interface, and anything "
    "else that is not part of the print. Do not add, invent or restyle anything."
)


class ExtractionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def source_file(value: str, root: Path = CLIENT_FILES) -> Path:
    path = Path(value).resolve()
    if path.parent != root.resolve() or not path.is_file():
        raise ExtractionError("invalid_file", f"Use a supplied file directly under {root}.")
    if path.suffix.lower() not in IMAGE_TYPES:
        raise ExtractionError("unsupported_type", "Only PNG, JPG and WEBP files can be extracted.")
    return path


def output_file(source: Path) -> Path:
    candidate = source.with_name(f"{source.stem}_extracted.png")
    number = 2
    while candidate.exists():
        candidate = source.with_name(f"{source.stem}_extracted_{number}.png")
        number += 1
    return candidate


def prompt(keep: str) -> str:
    keep = " ".join(keep.split())
    return f"{INSTRUCTIONS} Recreate only these elements, and leave out everything else: {keep}" if keep else INSTRUCTIONS


def multipart(fields: dict[str, str], file_name: str, content: bytes, content_type: str) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{file_name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode() + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def request_image(source: Path, keep: str, shape: str, api_key: str, model: str,
                  opener=urllib.request.urlopen) -> bytes:
    fields = {
        "model": model,
        "prompt": prompt(keep),
        "background": "opaque",
        "output_format": "png",
        "quality": "high",
        "input_fidelity": "high",
        "size": SHAPES.get(shape, "auto"),
        "n": "1",
    }
    body, content_type = multipart(fields, source.name, source.read_bytes(), IMAGE_TYPES[source.suffix.lower()])
    request = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {api_key}", "Content-Type": content_type,
    })
    try:
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise ExtractionError("api_error", f"Image model returned HTTP {error.code}: {detail}") from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ExtractionError("api_unavailable", f"Image model could not be reached: {error}") from error
    try:
        return base64.b64decode(payload["data"][0]["b64_json"])
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise ExtractionError("api_error", "Image model returned no image.") from error


def remove_green(image: bytes) -> bytes:
    """Make the flat green background transparent, softening the edge pixels it tinted."""
    from PIL import Image

    picture = Image.open(io.BytesIO(image)).convert("RGBA")
    pixels = []
    for red, green, blue, _ in picture.get_flattened_data():
        excess = green - max(red, blue)
        if excess > 90:
            pixels.append((0, 0, 0, 0))
        elif excess > 30:
            # Edge pixel: partly transparent, with the green spill taken out.
            pixels.append((red, max(red, blue), blue, round(255 * (90 - excess) / 60)))
        else:
            pixels.append((red, green, blue, 255))
    picture.putdata(pixels)
    bounds = picture.getbbox()
    if bounds is None:
        raise ExtractionError("empty_result", "The recreated artwork came back empty.")
    output = io.BytesIO()
    picture.crop(bounds).save(output, "PNG")
    return output.getvalue()


def extract(value: str, keep: str, shape: str = "auto", opener=urllib.request.urlopen,
            root: Path = CLIENT_FILES) -> dict:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise ExtractionError("not_configured", "Artwork extraction is not configured for this run.")
    source = source_file(value, root)
    model = os.environ.get("ARTWORK_IMAGE_MODEL", "").strip() or DEFAULT_MODEL
    image = remove_green(request_image(source, keep, shape, api_key, model, opener))
    target = output_file(source)
    target.write_bytes(image)
    return {"ok": True, "source": str(source), "path": str(target), "bytes": len(image)}


def main() -> None:
    parser = argparse.ArgumentParser(description="Recreate the printed design from a garment photo as a transparent PNG.")
    parser.add_argument("file", help="A supplied file under /workspace/client-files")
    parser.add_argument("--keep", default="", help="The text and graphics to recreate, exactly as seen. Leave out real photos.")
    parser.add_argument("--shape", choices=("auto", *SHAPES), default="auto",
                        help="The design's overall shape, so lines are not rewrapped to fit.")
    args = parser.parse_args()
    try:
        result = extract(args.file, args.keep, args.shape)
    except ExtractionError as error:
        print(json.dumps({"ok": False, "error": error.code, "message": str(error)}))
        sys.exit(1)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

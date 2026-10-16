"""Host-side artwork extraction: Images API call and green-background removal."""

from __future__ import annotations

import base64
import io
import json
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from .constants import (
    ARTIFACTS_NAME,
    CLIENT_FILES_NAME,
    DEFAULT_API_URL,
    DEFAULT_EXTRACT_TIMEOUT_SECONDS,
    DEFAULT_MODEL,
    IMAGE_TYPES,
    INSTRUCTIONS,
    SHAPES,
)


class ExtractionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def client_files_dir(workspace: Path) -> Path:
    return workspace / CLIENT_FILES_NAME


def artifacts_dir(workspace: Path) -> Path:
    return workspace / ARTIFACTS_NAME


def source_file(value: str, root: Path) -> Path:
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
    if keep:
        return f"{INSTRUCTIONS} Recreate only these elements, and leave out everything else: {keep}"
    return INSTRUCTIONS


def multipart(
    fields: dict[str, str], file_name: str, content: bytes, content_type: str
) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{file_name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def request_image(
    source: Path,
    keep: str,
    shape: str,
    api_key: str,
    model: str,
    *,
    api_url: str = DEFAULT_API_URL,
    timeout_seconds: float = DEFAULT_EXTRACT_TIMEOUT_SECONDS,
    opener=urllib.request.urlopen,
) -> bytes:
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
    body, content_type = multipart(
        fields, source.name, source.read_bytes(), IMAGE_TYPES[source.suffix.lower()]
    )
    request = urllib.request.Request(
        api_url,
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": content_type},
    )
    try:
        with opener(request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read())
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:500]
        raise ExtractionError(
            "api_error", f"Image model returned HTTP {error.code}: {detail}"
        ) from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise ExtractionError(
            "api_unavailable", f"Image model could not be reached: {error}"
        ) from error
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


def extract(
    value: str,
    keep: str,
    shape: str = "auto",
    *,
    workspace: Path,
    api_key: str,
    model: str = DEFAULT_MODEL,
    api_url: str = DEFAULT_API_URL,
    timeout_seconds: float = DEFAULT_EXTRACT_TIMEOUT_SECONDS,
    opener=urllib.request.urlopen,
) -> dict:
    if not api_key.strip():
        raise ExtractionError(
            "not_configured", "Artwork extraction is not configured for this run."
        )
    root = client_files_dir(workspace)
    source = source_file(value, root)
    image = remove_green(
        request_image(
            source,
            keep,
            shape,
            api_key,
            model,
            api_url=api_url,
            timeout_seconds=timeout_seconds,
            opener=opener,
        )
    )
    target = output_file(source)
    target.write_bytes(image)
    artifact = None
    try:
        artifacts = artifacts_dir(workspace)
        artifacts.mkdir(parents=True, exist_ok=True)
        artifact = artifacts / target.name
        artifact.write_bytes(image)
    except OSError:
        artifact = None
    result = {
        "ok": True,
        "source": str(source),
        "path": str(target),
        "bytes": len(image),
    }
    if artifact is not None:
        result["artifact"] = str(artifact)
    return result

"""Inspect supplied artwork for print suitability decisions."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

from .constants import RASTER_SUFFIXES, VECTOR_SUFFIXES


@dataclass(frozen=True)
class ArtworkInspection:
    """Raster/vector metadata used to choose direct upload vs extraction."""

    format: str
    kind: str
    width_px: int | None = None
    height_px: int | None = None
    has_alpha: bool | None = None
    mode: str | None = None
    readable: bool = True
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        if not self.readable:
            return f"{self.kind} ({self.format or 'unknown'}; unreadable: {self.error or 'unknown'})"
        if self.kind == "vector":
            return f"vector ({self.format})"
        if self.kind == "raster" and self.width_px and self.height_px:
            alpha = "transparent" if self.has_alpha else "opaque"
            return f"raster {self.width_px}x{self.height_px}px {alpha} ({self.format})"
        return f"{self.kind} ({self.format or 'unknown'})"


def effective_ppi(pixel_width: int | float | None, print_width_inches: float | None) -> float | None:
    """Return usable artwork pixels per print inch, or None when inputs are incomplete."""
    if pixel_width is None or print_width_inches is None:
        return None
    if pixel_width <= 0 or print_width_inches <= 0:
        return None
    return round(float(pixel_width) / float(print_width_inches), 1)


def meets_minimum_ppi(
    pixel_width: int | float | None,
    print_width_inches: float | None,
    minimum_ppi: float,
) -> bool:
    ppi = effective_ppi(pixel_width, print_width_inches)
    return ppi is not None and ppi >= minimum_ppi


def inspect_artwork(path: Path) -> ArtworkInspection:
    """Return dimensions and transparency for a supplied artwork file."""
    suffix = path.suffix.lower()
    if suffix in VECTOR_SUFFIXES:
        return ArtworkInspection(format=suffix.lstrip("."), kind="vector")
    if suffix not in RASTER_SUFFIXES:
        return ArtworkInspection(format=suffix.lstrip(".") or "unknown", kind="other")
    try:
        from PIL import Image

        with Image.open(path) as picture:
            has_alpha = (
                "A" in picture.getbands()
                or picture.mode in {"RGBA", "LA", "PA"}
                or ("transparency" in picture.info)
            )
            return ArtworkInspection(
                format=(picture.format or suffix.lstrip(".")).lower(),
                kind="raster",
                width_px=int(picture.width),
                height_px=int(picture.height),
                has_alpha=bool(has_alpha),
                mode=picture.mode,
            )
    except Exception as error:  # Pillow can raise many format-specific errors.
        return ArtworkInspection(
            format=suffix.lstrip(".") or "unknown",
            kind="raster",
            readable=False,
            error=str(error).split("\n")[0][:200],
        )

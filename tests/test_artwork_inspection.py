"""Artwork inspection metadata and PPI helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from account_manager_agent.artwork.inspection import (
    effective_ppi,
    inspect_artwork,
    meets_minimum_ppi,
)
from account_manager_agent.artwork.settings import ArtworkSettings


def _png(path: Path, *, size: tuple[int, int], mode: str = "RGB", color=(255, 0, 0)) -> None:
    image = Image.new(mode, size, color)
    image.save(path, "PNG")


class ArtworkInspectionTests(unittest.TestCase):
    def test_inspects_transparent_raster(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logo.png"
            _png(path, size=(400, 200), mode="RGBA", color=(255, 0, 0, 128))
            result = inspect_artwork(path)
            self.assertEqual(result.kind, "raster")
            self.assertEqual(result.width_px, 400)
            self.assertEqual(result.height_px, 200)
            self.assertTrue(result.has_alpha)
            self.assertIn("transparent", result.describe())

    def test_inspects_opaque_standalone_raster(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "art.jpg"
            Image.new("RGB", (900, 600), (10, 20, 30)).save(path, "JPEG", quality=90)
            result = inspect_artwork(path)
            self.assertEqual(result.kind, "raster")
            self.assertEqual((result.width_px, result.height_px), (900, 600))
            self.assertFalse(result.has_alpha)
            self.assertIn("opaque", result.describe())

    def test_inspects_tiny_raster_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "emblem.png"
            _png(path, size=(135, 75))
            result = inspect_artwork(path)
            self.assertEqual((result.width_px, result.height_px), (135, 75))
            self.assertFalse(meets_minimum_ppi(result.width_px, 9.5, 150))
            self.assertAlmostEqual(effective_ppi(135, 9.5), 14.2)

    def test_malformed_image_is_marked_unreadable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.png"
            path.write_bytes(b"not-an-image")
            result = inspect_artwork(path)
            self.assertFalse(result.readable)
            self.assertEqual(result.kind, "raster")
            self.assertIn("unreadable", result.describe())

    def test_vector_metadata_skips_pixel_dimensions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "logo.svg"
            path.write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
            result = inspect_artwork(path)
            self.assertEqual(result.kind, "vector")
            self.assertIsNone(result.width_px)
            self.assertIn("vector", result.describe())

    def test_ppi_settings_defaults_and_overrides(self):
        defaults = ArtworkSettings.from_environment({"OPENAI_API_KEY": "k"})
        self.assertEqual(defaults.min_print_ppi, 150.0)
        self.assertEqual(defaults.target_print_ppi, 300.0)
        custom = ArtworkSettings.from_environment(
            {
                "OPENAI_API_KEY": "k",
                "ARTWORK_MIN_PRINT_PPI": "120",
                "ARTWORK_TARGET_PRINT_PPI": "240",
            }
        )
        self.assertEqual(custom.min_print_ppi, 120.0)
        self.assertEqual(custom.target_print_ppi, 240.0)
        with self.assertRaises(ValueError):
            ArtworkSettings.from_environment(
                {
                    "OPENAI_API_KEY": "k",
                    "ARTWORK_MIN_PRINT_PPI": "300",
                    "ARTWORK_TARGET_PRINT_PPI": "150",
                }
            )


class ArtworkDecisionRegressionTests(unittest.TestCase):
    """Regression for run 783325: tiny emblem in a screenshot must not upscale-upload."""

    def test_screenshot_with_tiny_emblem_requires_extract_not_lanczos_crop(self):
        with tempfile.TemporaryDirectory() as directory:
            screenshot = Path(directory) / "file_1.png"
            # Match the observed source size (~413×457) with a small embedded emblem region.
            image = Image.new("RGB", (413, 457), (40, 40, 40))
            for x in range(140, 275):
                for y in range(180, 255):
                    image.putpixel((x, y), (220, 30, 30))
            image.save(screenshot, "PNG")
            inspection = inspect_artwork(screenshot)
            self.assertEqual((inspection.width_px, inspection.height_px), (413, 457))
            # Even the full screenshot is only ~43 PPI at a typical 9.5" chest width.
            self.assertFalse(meets_minimum_ppi(inspection.width_px, 9.5, 150))
            emblem_width = 135
            self.assertFalse(meets_minimum_ppi(emblem_width, 9.5, 150))
            # A hand-upscaled crop still has only the source pixel capacity.
            upscaled_width = 540
            self.assertFalse(meets_minimum_ppi(emblem_width, 9.5, 150))
            self.assertLess(effective_ppi(emblem_width, 9.5), 20)
            # Uploading the Lanczos result would still be wrong: quality is from source pixels.
            self.assertNotEqual(upscaled_width, emblem_width)

    def test_extracted_output_meets_minimum_at_print_width(self):
        # Live smoke companion: an extract sized for 150 PPI at 4" must pass.
        pixel_width = 600
        print_width = 4.0
        self.assertTrue(meets_minimum_ppi(pixel_width, print_width, 150))
        self.assertEqual(effective_ppi(pixel_width, print_width), 150.0)


if __name__ == "__main__":
    unittest.main()

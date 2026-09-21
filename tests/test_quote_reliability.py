import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from catalog_state import inspect_catalog_product
import product_memory
from reply_preflight import validate_reply


def verified_product(style="437"):
    return {
        "style_code": style,
        "color": "White",
        "product_name": "Jerzees Adult Polo",
        "catalog_url": f"/products/jerzees-{style}-adult-polo?color=White",
        "catalog_status": "verified",
        "source_turn": "turn_test",
    }


class CatalogInspectionTests(unittest.TestCase):
    def test_exact_catalog_link_is_verified(self):
        record = inspect_catalog_product(
            "https://www.freshprints.com/products?search=polo&mainColorGroup=White",
            "437",
            "White",
            [{
                "role": "link",
                "name": "Color Palette Jerzees Adult Polo",
                "url": "/products/jerzees-437-adult-polo?color=White",
            }],
        )
        self.assertEqual(record["catalog_status"], "verified")
        self.assertEqual(record["product_name"], "Jerzees Adult Polo")

    def test_exact_completed_search_without_link_is_not_found(self):
        record = inspect_catalog_product(
            "https://www.freshprints.com/products?search=TT51",
            "TT51",
            "White",
            [],
        )
        self.assertEqual(record["catalog_status"], "not_found")

    def test_general_page_without_link_is_unknown(self):
        record = inspect_catalog_product(
            "https://www.freshprints.com/products?search=polo",
            "TT51",
            "White",
            [],
        )
        self.assertEqual(record["catalog_status"], "unknown")

    def test_style_code_must_not_be_a_substring_match(self):
        record = inspect_catalog_product(
            "https://www.freshprints.com/products?search=G448",
            "G448",
            "White",
            [{
                "role": "link",
                "name": "Gildan G448L Polo",
                "url": "https://www.freshprints.com/products/gildan-g448l-polo",
            }],
        )
        self.assertEqual(record["catalog_status"], "not_found")


class ReplyPreflightTests(unittest.TestCase):
    def validate(self, message, products=None, catalog_required=True, prior_products=None):
        return validate_reply(
            message,
            [verified_product()] if products is None else products,
            catalog_required,
            prior_products or [],
        )

    def test_verified_catalog_product_passes(self):
        errors = self.validate("The Jerzees 437 is one option I checked.")
        self.assertEqual(errors, [])

    def test_missing_verified_catalog_product_is_blocked(self):
        errors = self.validate("Here are some options.", products=[])
        self.assertTrue(any("Verify at least one" in error for error in errors))

    def test_universal_product_claim_is_blocked(self):
        errors = self.validate(
            "Every white polo is $48.75 each and $585 total at the minimum of 12, before tax."
        )
        self.assertTrue(any("every polo" in error for error in errors))


class ProductMemoryTests(unittest.TestCase):
    def test_only_verified_identity_fields_are_persisted(self):
        product = verified_product()
        product.update(stock_warning="dynamic", item_total="$585.00")
        with tempfile.TemporaryDirectory() as directory, \
                patch("product_memory.memory.deal_dir", return_value=Path(directory)):
            product_memory.save_verified(1, [product])
            saved = product_memory.load(1)
        self.assertEqual(saved[0]["style_code"], "437")
        self.assertEqual(saved[0]["source_turn"], "turn_test")
        self.assertNotIn("stock_warning", saved[0])
        self.assertNotIn("item_total", saved[0])

    def test_unverified_product_is_not_persisted(self):
        product = verified_product()
        product["catalog_status"] = "not_found"
        with tempfile.TemporaryDirectory() as directory, \
                patch("product_memory.memory.deal_dir", return_value=Path(directory)):
            product_memory.save_verified(1, [product])
            saved = product_memory.load(1)
        self.assertEqual(saved, [])


if __name__ == "__main__":
    unittest.main()

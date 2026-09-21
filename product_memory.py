"""Verified product identity memory. Dynamic quote values never live here."""

import json
from pathlib import Path

import memory


FILENAME = "verified_products.json"


def path_for(deal_id: int) -> Path:
    return memory.deal_dir(deal_id) / FILENAME


def load(deal_id: int) -> list[dict]:
    path = path_for(deal_id)
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    return data if isinstance(data, list) else []


def save_verified(deal_id: int, records: list[dict]) -> None:
    products = load(deal_id)
    by_key = {(_key(product)): product for product in products}
    for record in records:
        if record.get("catalog_status") != "verified":
            continue
        style_code = str(record.get("style_code", "")).strip()
        color = str(record.get("color", "")).strip()
        if not style_code or not color:
            continue
        product = {
            "style_code": style_code,
            "product_name": str(record.get("product_name", "")).strip(),
            "color": color,
            "catalog_url": str(record.get("catalog_url", "")).strip(),
            "catalog_status": "verified",
            "source_turn": str(record.get("source_turn", "")),
        }
        by_key[_key(product)] = product
    path_for(deal_id).write_text(json.dumps(list(by_key.values()), indent=2, sort_keys=True) + "\n")


def describe(products: list[dict]) -> str:
    lines = []
    for product in products:
        name = product.get("product_name") or product.get("selected_style_label") or "Unknown product"
        catalog = product.get("catalog_status", "unknown")
        lines.append(
            f"- {name}: exact style {product.get('style_code')}, color {product.get('color')}, "
            f"catalog {catalog}"
        )
    return "\n".join(lines)


def _key(product: dict) -> tuple[str, str]:
    return (
        str(product.get("style_code", "")).strip().casefold(),
        str(product.get("color", "")).strip().casefold(),
    )

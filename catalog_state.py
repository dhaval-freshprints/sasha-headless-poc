"""Verify that an exact style is represented by a link on the public product catalog."""

import re
from urllib.parse import parse_qs, unquote, urlparse


def inspect_catalog_product(page_url: str, style_code: str, color: str, nodes: list[dict]) -> dict:
    parsed = urlparse(page_url)
    if parsed.hostname != "www.freshprints.com" or not parsed.path.startswith("/products"):
        raise ValueError("inspect_catalog_product only works on the Fresh Prints product catalog.")

    wanted = style_code.strip().casefold()
    matches = []
    for node in nodes:
        url = str(node.get("url", ""))
        if node.get("role") != "link" or not _url_has_style(url, wanted):
            continue
        matches.append(node)

    if matches:
        best = max(matches, key=_candidate_score)
        name = _product_name(best.get("name", ""))
        return {
            "style_code": style_code.strip(),
            "color": color.strip(),
            "product_name": name,
            "catalog_url": best.get("url", ""),
            "catalog_status": "verified",
        }

    query = parse_qs(parsed.query).get("search", [""])[0].strip().casefold()
    status = "not_found" if query == wanted else "unknown"
    return {
        "style_code": style_code.strip(),
        "color": color.strip(),
        "product_name": "",
        "catalog_url": "",
        "catalog_status": status,
    }


def _candidate_score(node: dict) -> tuple[int, int]:
    name = str(node.get("name", ""))
    generic = name in {"", "Product Card Link"}
    return (0 if generic else 1, len(name))


def _url_has_style(url: str, style_code: str) -> bool:
    path = unquote(urlparse(url).path).casefold()
    pattern = rf"(?<![a-z0-9]){re.escape(style_code)}(?![a-z0-9])"
    return bool(re.search(pattern, path))


def _product_name(name: str) -> str:
    prefix = "Color Palette "
    return name[len(prefix):].strip() if name.startswith(prefix) else name.strip()

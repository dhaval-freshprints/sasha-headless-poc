"""Small deterministic checks before a product-options reply leaves the model loop."""

import re


def validate_reply(message: str, catalog_products: list[dict], catalog_required: bool,
                   prior_products: list[dict]) -> list[str]:
    errors = []
    verified = [
        product for product in catalog_products
        if product.get("catalog_status") == "verified"
    ]
    if catalog_required and not verified:
        errors.append("Verify at least one exact product on the public catalog before recommending options.")

    lowered = message.casefold()
    if re.search(r"\b(?:every|all)\s+(?:white\s+)?polos?\b", lowered):
        errors.append("Do not generalize from checked candidates to every polo; say 'the options I checked'.")

    if "again" in lowered:
        prior_styles = {_style(product) for product in prior_products}
        current_styles = {_style(product) for product in verified}
        if prior_styles and current_styles and current_styles.isdisjoint(prior_styles):
            errors.append("The verified styles do not match a previous style, so do not say 'again'.")
    return errors


def _style(product: dict) -> str:
    return str(product.get("style_code", "")).strip().casefold()

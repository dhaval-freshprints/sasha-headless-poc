"""
Browser Hands.

A logged-in headless Chromium and the six actions the model may take on it.
Nothing else is exposed: no shell, no filesystem.
"""

from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

import config

ACTION_TIMEOUT_MS = 8000


class Browser:
    def __init__(self, headless: bool = True):
        self._playwright = sync_playwright().start()
        self._context: BrowserContext = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.AUTH_DIR),
            headless=headless,
            viewport={"width": 1440, "height": 900},
        )
        self.page: Page = self._context.pages[0] if self._context.pages else self._context.new_page()

    def close(self) -> None:
        self._context.close()
        self._playwright.stop()

    # ---- the six tools ------------------------------------------------------

    def navigate(self, url: str) -> str:
        self.page.goto(url, wait_until="domcontentloaded")
        self._settle()
        return f"Now at {self.page.url}"

    def snapshot(self) -> str:
        """Accessibility tree of the current page. This is what the model 'sees'."""
        self._settle()
        tree = self.page.locator("body").aria_snapshot()
        if len(tree) > config.SNAPSHOT_MAX_CHARS:
            tree = tree[: config.SNAPSHOT_MAX_CHARS] + "\n... [truncated]"
        return f"URL: {self.page.url}\n\n{tree}"

    def screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(path), full_page=False)
        return path

    def click(self, role: str, name: str) -> str:
        target = self._locate(role, name)
        if target.count() == 0:
            return self._not_found(role, name)
        target.click(timeout=ACTION_TIMEOUT_MS)
        self._settle()
        return f"Clicked {role} '{name}'"

    def type_text(self, role: str, name: str, text: str, press_enter: bool = False) -> str:
        field = self._locate(role, name)
        if field.count() == 0:
            return self._not_found(role, name)
        field.fill(text, timeout=ACTION_TIMEOUT_MS)
        if press_enter:
            field.press("Enter")
        self._settle()
        return f"Typed into {role} '{name}'"

    def select_option(self, role: str, name: str, option: str) -> str:
        field = self._locate(role, name)
        if field.count() == 0:
            return self._not_found(role, name)
        field.select_option(label=option, timeout=ACTION_TIMEOUT_MS)
        self._settle()
        return f"Selected '{option}' in {role} '{name}'"

    # ---- helpers ------------------------------------------------------------

    def _locate(self, role: str, name: str):
        return self.page.get_by_role(role, name=name, exact=False).first

    @staticmethod
    def _not_found(role: str, name: str) -> str:
        return (
            f"NOT FOUND: no {role} named '{name}' on this page. "
            "Only elements listed with a role in the snapshot (link, button, textbox, ...) can be used. "
            "Plain 'text:' lines are not clickable. Take a snapshot and pick a listed element."
        )

    def _settle(self) -> None:
        try:
            self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass  # SPA pages often never go idle; a short wait is fine for a POC

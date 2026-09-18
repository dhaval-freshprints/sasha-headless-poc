"""
Browser Hands: a logged-in Chromium with named tabs.

Sensing and acting live in toolset_executor.py. This file owns the session, the tabs,
screenshots, and the two things every action needs afterwards: settle and dismiss toasts.

Nothing else is exposed: no shell, no filesystem.
"""

from pathlib import Path

from playwright.sync_api import BrowserContext, Page, sync_playwright

import config

VIEWPORT = {"width": 1440, "height": 900}
SETTLE_MS = 700

# The page's visible text. Like innerText, but subtrees parked outside the viewport (slide-in
# drawers, notification panels) are skipped, whatever depth they sit at.
VISIBLE_TEXT_JS = """() => {
    const width = window.innerWidth;
    const skip = new Set(['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE']);
    function textOf(node) {
        if (node.nodeType === 3) return node.textContent;
        if (node.nodeType !== 1 || skip.has(node.tagName)) return '';
        const r = node.getBoundingClientRect();
        if (r.width > 0 && (r.left >= width || r.right <= 0)) return '';
        const s = getComputedStyle(node);
        if (s.display === 'none' || s.visibility === 'hidden') return '';
        let out = '';
        for (const child of node.childNodes) out += textOf(child);
        const inline = s.display.startsWith('inline') || node.tagName === 'TD' || node.tagName === 'TH';
        if (node.tagName === 'TD' || node.tagName === 'TH') out = ' ' + out;
        return inline ? out : '\\n' + out + '\\n';
    }
    return textOf(document.body)
        .split('\\n').map(line => line.replace(/\\s+/g, ' ').trim()).filter(Boolean).join('\\n');
}"""


class Browser:
    def __init__(self, headless: bool = True):
        self._playwright = sync_playwright().start()
        self._context: BrowserContext = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.AUTH_DIR),
            headless=headless,
            viewport=VIEWPORT,
            device_scale_factor=1,
        )
        self.tabs: dict[str, Page] = {}
        self.active_tab_id = ""
        self._tab_counter = 0
        self._context.on("page", self._register_tab)
        for page in self._context.pages:
            self._register_tab(page)
        if not self.tabs:
            self._context.new_page()
        self.active_tab_id = next(iter(self.tabs))

    def close(self) -> None:
        self._context.close()
        self._playwright.stop()

    # ---- tabs ---------------------------------------------------------------

    @property
    def page(self) -> Page:
        return self.tabs[self.active_tab_id]

    def tab_page(self, tab_id: str) -> Page:
        if tab_id not in self.tabs:
            raise ValueError(f"No tab with tab_id {tab_id}.")
        return self.tabs[tab_id]

    def new_tab(self) -> str:
        page = self._context.new_page()
        tab_id = self._tab_id_of(page)
        self.active_tab_id = tab_id
        return tab_id

    def switch_tab(self, tab_id: str) -> None:
        self.tab_page(tab_id).bring_to_front()
        self.active_tab_id = tab_id

    def close_tab(self, tab_id: str) -> None:
        self.tab_page(tab_id).close()

    def tab_state(self) -> list[dict]:
        """The tab inventory in the shape the browser_state block wants."""
        entries = []
        for tab_id, page in self.tabs.items():
            entry = {"tab_id": tab_id, "title": page.title(), "url": page.url}
            if tab_id == self.active_tab_id:
                entry["active"] = True
            entries.append(entry)
        return entries

    def _register_tab(self, page: Page) -> None:
        self._tab_counter += 1
        tab_id = f"tab-{self._tab_counter}"
        self.tabs[tab_id] = page
        page.on("close", lambda _: self._forget_tab(tab_id))

    def _forget_tab(self, tab_id: str) -> None:
        self.tabs.pop(tab_id, None)
        if self.active_tab_id == tab_id and self.tabs:
            self.active_tab_id = next(reversed(self.tabs))

    def _tab_id_of(self, page: Page) -> str:
        for tab_id, known in self.tabs.items():
            if known == page:
                return tab_id
        raise ValueError("Page is not a registered tab.")

    # ---- see ----------------------------------------------------------------

    def screenshot(self, page: Page, clip: dict | None = None) -> bytes:
        self.settle(page)
        return page.screenshot(full_page=False, clip=clip)

    def save_screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.screenshot(self.page))
        return path

    def page_text(self, page: Page) -> str:
        self.settle(page)
        text = page.evaluate(VISIBLE_TEXT_JS)
        if len(text) > config.PAGE_TEXT_MAX_CHARS:
            text = text[: config.PAGE_TEXT_MAX_CHARS] + "\n... [truncated]"
        return text

    # ---- after every action -------------------------------------------------

    def settle(self, page: Page) -> None:
        page.wait_for_timeout(SETTLE_MS)
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

    def dismiss_toasts(self, page: Page) -> None:
        """Notification banners and help-chat panels cover controls. Close the ones we can.
        Only toast-like containers: a bare aria-label=Close would also close modals the model opened."""
        for selector in [".toast-close-button", "[class*='toast'] [aria-label='Close']", ".toast .close",
                         ".notification .close", "[class*='overlay'] button:has-text('Collapse')"]:
            try:
                found = page.locator(selector)
                for i in range(min(found.count(), 3)):
                    if found.nth(i).is_visible():
                        found.nth(i).click(timeout=500)
            except Exception:
                pass

"""Browser Hands: a logged-in Chromium with named tabs."""

from pathlib import Path
import time

from playwright.sync_api import BrowserContext, Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

import config
from observation_state import CONTROL_STATE_JS

VIEWPORT = {"width": 1440, "height": 900}
READY_TIMEOUT_MS = 1500

NEXT_PAINT_JS = """() => new Promise(resolve => {
    requestAnimationFrame(resolve);
    setTimeout(resolve, 100);
})"""

READY_STATE_JS = """() => {
    const visible = element => {
        const style = getComputedStyle(element);
        const box = element.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && style.display !== 'none' &&
            style.visibility !== 'hidden' && style.opacity !== '0';
    };
    const selectors = [
        '[aria-busy=true]', '[role=progressbar]', 'progress',
        '.spinner', '[class*=spinner]',
        '.loading.active', '.loading.show', '.loading.visible',
        '[class*=loading][aria-busy=true]', '[class*=loading][role=progressbar]'
    ];
    let busyIndicator = null;
    let busySelector = '';
    for (const selector of selectors) {
        const found = [...document.querySelectorAll(selector)].find(visible);
        if (found) {
            busyIndicator = found;
            busySelector = selector;
            break;
        }
    }
    const processingText = (document.body?.innerText || '').split('\\n')
        .map(line => line.replace(/\\s+/g, ' ').trim())
        .find(line => /^(saving(?:\\.\\.\\.)?|uploading in progress(?:\\.\\.\\.)?)$/i.test(line));
    const documentReady = document.readyState === 'interactive' || document.readyState === 'complete';
    const busyDetail = busyIndicator ? (() => {
        const style = getComputedStyle(busyIndicator);
        const box = busyIndicator.getBoundingClientRect();
        return {
            selector: busySelector,
            tag: busyIndicator.tagName.toLowerCase(),
            className: (busyIndicator.className || '').toString().replace(/\\s+/g, ' ').trim().slice(0, 120),
            role: busyIndicator.getAttribute('role') || '',
            ariaBusy: busyIndicator.getAttribute('aria-busy') || '',
            display: style.display,
            visibility: style.visibility,
            opacity: style.opacity,
            box: [Math.round(box.x), Math.round(box.y), Math.round(box.width), Math.round(box.height)],
            text: (busyIndicator.innerText || busyIndicator.getAttribute('aria-label') || '')
                .replace(/\\s+/g, ' ').trim().slice(0, 80)
        };
    })() : null;
    return {
        ready: documentReady && !busyIndicator && !processingText,
        documentState: document.readyState,
        busy: processingText || (busyIndicator ?
            ((busyIndicator.innerText || busyIndicator.getAttribute('aria-label') || busyIndicator.className || 'busy')
                .toString().replace(/\\s+/g, ' ').trim().slice(0, 80)) : ''),
        busyDetail
    };
}"""

READY_PREDICATE_JS = "() => (" + READY_STATE_JS + ")().ready"

# The page's visible text. Like innerText, but subtrees parked outside the viewport (slide-in
# drawers, notification panels) are skipped, whatever depth they sit at.
VISIBLE_TEXT_JS = "() => {" + CONTROL_STATE_JS + """
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
        if (out.trim()) out = stateLabel(node) + out;
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
        self._full_screenshot_cache: tuple[str, bytes] | None = None
        self.last_capture = {"seconds": 0.0, "reused": False}
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
        """Capture now. A full capture may be reused once by batch recording."""
        started = time.monotonic()
        png = page.screenshot(full_page=False, clip=clip)
        self.last_capture = {"seconds": time.monotonic() - started, "reused": False}
        if clip is None:
            self._full_screenshot_cache = (self._tab_id_of(page), png)
        else:
            self.invalidate_screenshot_cache()
        return png

    def save_screenshot(self, path: Path) -> Path:
        """Save the last full capture only when it was the final tool observation."""
        path.parent.mkdir(parents=True, exist_ok=True)
        cached = getattr(self, "_full_screenshot_cache", None)
        self._full_screenshot_cache = None
        if cached and cached[0] == self.active_tab_id:
            png = cached[1]
            self.last_capture = {"seconds": 0.0, "reused": True}
        else:
            png = self.screenshot(self.page)
            self._full_screenshot_cache = None
        path.write_bytes(png)
        return path

    def page_text(self, page: Page) -> str:
        text = page.evaluate(VISIBLE_TEXT_JS)
        if len(text) > config.PAGE_TEXT_MAX_CHARS:
            text = text[: config.PAGE_TEXT_MAX_CHARS] + "\n... [truncated]"
        return text

    # ---- bounded readiness --------------------------------------------------

    def invalidate_screenshot_cache(self) -> None:
        self._full_screenshot_cache = None

    def wait_for_ready(self, page: Page, timeout_ms: int = READY_TIMEOUT_MS) -> str:
        """Wait for visible UI processing indicators, not proof that a save succeeded."""
        page.evaluate(NEXT_PAINT_JS)
        state = page.evaluate(READY_STATE_JS)
        if state["ready"]:
            return ""
        try:
            page.wait_for_function(READY_PREDICATE_JS, timeout=timeout_ms, polling=100)
            return ""
        except PlaywrightTimeoutError:
            state = page.evaluate(READY_STATE_JS)
            if state["ready"]:
                return ""
            detail = state["busy"] or f"document state {state['documentState']}"
            if state.get("busyDetail"):
                detail = f"{detail}; matched {state['busyDetail']}"
            return (
                f"The action was dispatched, but the page is still processing ({detail}) after "
                f"{timeout_ms / 1000:g}s. Inspect the page before another mutation."
            )

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

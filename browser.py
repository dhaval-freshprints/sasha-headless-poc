"""
Browser Hands — hybrid (option C).

Two senses, always together:
  - the accessibility tree: names of things, exact and cheap
  - a screenshot: layout, canvas, anything the tree can't express

Two ways to act:
  - by name (role + name, or visible text): precise, preferred
  - by pixel coordinates: fallback for canvas and unnamed controls

Nothing else is exposed: no shell, no filesystem.
"""

from pathlib import Path

from playwright.sync_api import BrowserContext, Locator, Page, sync_playwright

import config

VIEWPORT = {"width": 1440, "height": 900}
ACTION_TIMEOUT_MS = 8000
SETTLE_MS = 700


class Browser:
    def __init__(self, headless: bool = True):
        self._playwright = sync_playwright().start()
        self._context: BrowserContext = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.AUTH_DIR),
            headless=headless,
            viewport=VIEWPORT,
            device_scale_factor=1,
        )
        if not self._context.pages:
            self._context.new_page()

    def close(self) -> None:
        self._context.close()
        self._playwright.stop()

    @property
    def page(self) -> Page:
        """Always the newest tab, so a button that opens a tab doesn't strand the model."""
        return self._context.pages[-1]

    # ---- see ----------------------------------------------------------------

    def snapshot(self) -> str:
        """Accessibility tree + what's focused. Editable regions are called out explicitly."""
        self._settle()
        self._dismiss_toasts()
        tree = self.page.locator("body").aria_snapshot()
        if len(tree) > config.SNAPSHOT_MAX_CHARS:
            tree = tree[: config.SNAPSHOT_MAX_CHARS] + "\n... [truncated]"
        return f"{self._where()}\n\n{tree}\n\n{self._editable_summary()}"

    def screenshot(self) -> bytes:
        self._settle()
        return self.page.screenshot(full_page=False)

    def save_screenshot(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.screenshot())
        return path

    # ---- act by name --------------------------------------------------------

    def navigate(self, url: str) -> str:
        self.page.goto(url, wait_until="domcontentloaded")
        self._settle()
        return self._where()

    def click(self, role: str, name: str) -> str:
        target = self.page.get_by_role(role, name=name, exact=False).first
        if target.count() == 0:
            return self._not_found(f"{role} '{name}'")
        target.click(timeout=ACTION_TIMEOUT_MS)
        self._settle()
        return f"Clicked {role} '{name}'. {self._where()}"

    def click_text(self, text: str) -> str:
        target = self.page.get_by_text(text, exact=False).first
        if target.count() == 0:
            return self._not_found(f"text '{text}'")
        target.click(timeout=ACTION_TIMEOUT_MS)
        self._settle()
        return f"Clicked text '{text}'. {self._where()}"

    def fill_field(self, field: str, text: str, press_enter: bool = False) -> str:
        """
        Put text into an editable field, replacing what's there.
        `field` is matched, in order, against: accessible name / label, placeholder,
        nearby label text, or an index like "#3" from the EDITABLE FIELDS list.
        Works for inputs, textareas and rich-text (contenteditable) editors.
        """
        target = self._find_editable(field)
        if target is None:
            return self._not_found(f"editable field '{field}'") + " " + self._editable_summary()
        target.click(timeout=ACTION_TIMEOUT_MS)
        self.page.keyboard.press("Meta+a")
        self.page.keyboard.press("Backspace")
        self.page.keyboard.type(text, delay=10)
        if press_enter:
            self.page.keyboard.press("Enter")
        self._settle()
        return f"Filled '{field}' with {len(text)} characters. Now contains: {self._value_of(target)!r}"

    def select_option(self, field: str, option: str) -> str:
        target = self._find_editable(field)
        if target is None:
            return self._not_found(f"select '{field}'")
        target.select_option(label=option, timeout=ACTION_TIMEOUT_MS)
        self._settle()
        return f"Selected '{option}' in '{field}'."

    def press_key(self, key: str) -> str:
        self.page.keyboard.press(key)
        self._settle()
        return f"Pressed {key}. {self._where()}"

    # ---- act by coordinates (fallback) --------------------------------------

    def click_at(self, x: int, y: int) -> str:
        self.page.mouse.click(x, y)
        self._settle()
        return f"Clicked ({x}, {y}). {self._where()}"

    def type_here(self, text: str) -> str:
        """Type into whatever has focus. Use after click_at on a canvas or unnamed control."""
        self.page.keyboard.type(text, delay=10)
        self._settle()
        return f"Typed {len(text)} characters into the focused element."

    def scroll(self, direction: str, amount: int = 3) -> str:
        delta = amount * 120
        self.page.mouse.move(VIEWPORT["width"] // 2, VIEWPORT["height"] // 2)
        self.page.mouse.wheel(0, delta if direction == "down" else -delta)
        self._settle()
        return f"Scrolled {direction}."

    # ---- helpers ------------------------------------------------------------

    def _find_editable(self, field: str) -> Locator | None:
        editables = self._editables()
        if field.startswith("#") and field[1:].isdigit():
            index = int(field[1:])
            return editables[index]["locator"] if index < len(editables) else None
        needle = field.lower().strip()
        for entry in editables:
            haystack = " | ".join([entry["label"], entry["placeholder"], entry["near"]]).lower()
            if needle and needle in haystack:
                return entry["locator"]
        return None

    def _editables(self) -> list[dict]:
        """Every visible thing a user could type into, with the best label we can find."""
        selector = "input:not([type=hidden]):not([type=checkbox]):not([type=radio]), textarea, select, [contenteditable=true]"
        locator = self.page.locator(selector)
        entries = []
        for i in range(locator.count()):
            el = locator.nth(i)
            try:
                if not el.is_visible():
                    continue
                meta = el.evaluate("""e => {
                    const lab = e.labels?.[0]?.innerText?.trim() || e.getAttribute('aria-label') || '';
                    const own = (e.isContentEditable ? e.innerText : e.value) || '';
                    // Label = the closest short text ABOVE the field that isn't the field's own content.
                    const box = e.getBoundingClientRect();
                    let near = '';
                    const candidates = [...document.querySelectorAll('label, h1, h2, h3, h4, h5, h6, p, span, div, legend')]
                        .filter(n => n.children.length === 0 || n.tagName === 'LABEL');
                    const visible = n => { const s = getComputedStyle(n); return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0' && n.getClientRects().length > 0; };
                    const covered = n => { const r = n.getBoundingClientRect(); const top = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2); return top && !n.contains(top) && !top.contains(n); };
                    let best = Infinity;
                    for (const n of candidates) {
                        const t = (n.innerText || '').trim();
                        if (!t || t.length > 60 || t === own.trim()) continue;
                        if (!visible(n) || covered(n)) continue;
                        const r = n.getBoundingClientRect();
                        const above = box.top - r.bottom;
                        const overlapX = r.left < box.right && r.right > box.left;
                        if (above >= -4 && above < 90 && overlapX && above < best) { best = above; near = t; }
                    }
                    const kind = e.isContentEditable ? 'richtext' : (e.tagName.toLowerCase() === 'select' ? 'select' : (e.type || 'text'));
                    return {label: lab, placeholder: e.placeholder || '', near, kind, value: own.slice(0, 40)};
                }""")
            except Exception:
                continue
            meta["locator"] = el
            meta["index"] = len(entries)
            entries.append(meta)
        return entries

    def _editable_summary(self) -> str:
        entries = self._editables()
        if not entries:
            return "EDITABLE FIELDS: none visible."
        lines = ["EDITABLE FIELDS (use fill_field with the label, or the #index):"]
        for e in entries:
            label = e["label"] or e["placeholder"] or e["near"] or "(unlabelled)"
            lines.append(f"  #{e['index']} [{e['kind']}] {label!r}  value={e['value']!r}")
        return "\n".join(lines)

    @staticmethod
    def _value_of(target: Locator) -> str:
        try:
            return target.evaluate("e => (e.isContentEditable ? e.innerText : e.value) || ''")[:80]
        except Exception:
            return ""

    def _dismiss_toasts(self) -> None:
        """Notification banners cover controls in screenshots. Close the ones we can."""
        for sel in [".toast-close-button", "[aria-label='Close']", ".toast .close", ".notification .close"]:
            try:
                loc = self.page.locator(sel)
                for i in range(min(loc.count(), 3)):
                    if loc.nth(i).is_visible():
                        loc.nth(i).click(timeout=500)
            except Exception:
                pass

    def _where(self) -> str:
        tabs = len(self._context.pages)
        note = f" ({tabs} tabs open, showing newest)" if tabs > 1 else ""
        return f"Now at {self.page.url}{note}"

    @staticmethod
    def _not_found(what: str) -> str:
        return f"NOT FOUND: {what} is not on this page. Take a snapshot and check the exact name, or use click_at with coordinates from the screenshot."

    def _settle(self) -> None:
        self.page.wait_for_timeout(SETTLE_MS)
        try:
            self.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass

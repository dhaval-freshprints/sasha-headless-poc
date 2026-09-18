"""
Runs Anthropic's browser toolset (browser_toolset_20260801) against Playwright.

One method per member. `run()` only routes. Every method returns the tool result content:
a string, or a list of content blocks (image, browser_state). Errors are raised; the brain
turns them into is_error results and halts the rest of the batch.

References: read_page and find tag elements as [ref_N]. Each ref maps to a Chromium
accessibility node (backendDOMNodeId) on one tab. Refs are never renumbered until that tab
navigates; a ref whose node is gone gets the stale-ref error the contract asks for.
"""

import base64
from dataclasses import dataclass

from playwright.sync_api import CDPSession, Page

import config
from browser import Browser

NOT_EXECUTED = "Not executed: an earlier action in this turn failed."

INTERACTIVE_ROLES = {
    "link", "button", "textbox", "searchbox", "combobox", "checkbox", "radio", "switch",
    "option", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "slider", "spinbutton",
    "listbox", "treeitem", "menubutton",
}
SKIP_ROLES = {"RootWebArea", "InlineTextBox", "LabelText", "none", "presentation", "LineBreak", "MenuListPopup"}
ROLE_WORDS = {   # words the model may use in a find() query, mapped to roles
    "button": "button", "link": "link", "field": "textbox", "box": "textbox", "input": "textbox",
    "textbox": "textbox", "search": "textbox", "dropdown": "combobox", "select": "combobox",
    "combobox": "combobox", "checkbox": "checkbox", "radio": "radio", "tab": "tab", "option": "option",
    "menu": "menuitem", "heading": "heading", "text": "text",
    "quantity": "spinbutton", "qty": "spinbutton", "number": "spinbutton", "spinbutton": "spinbutton",
}
QUERY_SYNONYMS = {"quantity": "qty", "qty": "quantity", "colour": "color", "color": "colour"}
NOISE_WORDS = {"the", "a", "an", "of", "to", "in", "on", "for", "next", "near", "with", "and", "or"}
KEY_NAMES = {
    "ctrl": "ControlOrMeta", "control": "ControlOrMeta", "cmd": "Meta", "meta": "Meta", "win": "Meta",
    "alt": "Alt", "option": "Alt", "shift": "Shift", "enter": "Enter", "return": "Enter",
    "esc": "Escape", "escape": "Escape", "tab": "Tab", "backspace": "Backspace", "delete": "Delete",
    "del": "Delete", "space": "Space", "up": "ArrowUp", "down": "ArrowDown", "left": "ArrowLeft",
    "right": "ArrowRight", "arrowup": "ArrowUp", "arrowdown": "ArrowDown", "arrowleft": "ArrowLeft",
    "arrowright": "ArrowRight", "pageup": "PageUp", "pagedown": "PageDown", "home": "Home", "end": "End",
}

ELEMENT_JS = "const e = this.nodeType === 3 ? this.parentElement : this;"

# For an unnamed field: the closest short text above it or to its left, plus DOM hints
# (id, name, type, title, placeholder) that often say what the field is for.
NEAR_LABEL_JS = """function() {
    """ + ELEMENT_JS + """
    const own = ((e.isContentEditable ? e.innerText : e.value) || '').trim();
    const box = e.getBoundingClientRect();
    const candidates = [...document.querySelectorAll('label, h1, h2, h3, h4, h5, h6, p, span, div, legend')]
        .filter(n => n.children.length === 0 || n.tagName === 'LABEL');
    const visible = n => { const s = getComputedStyle(n); return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0' && n.getClientRects().length > 0; };
    let best = Infinity, near = '';
    for (const n of candidates) {
        const t = (n.innerText || '').trim();
        if (!t || t.length > 60 || t === own) continue;
        if (!visible(n)) continue;
        const r = n.getBoundingClientRect();
        const above = box.top - r.bottom;
        const overlapX = r.left < box.right && r.right > box.left;
        const overlapY = r.top < box.bottom && r.bottom > box.top;
        const leftGap = box.left - r.right;
        let distance = Infinity;
        if (above >= -4 && above < 90 && overlapX) distance = above;
        else if (overlapY && leftGap >= -4 && leftGap < 160) distance = 100 + leftGap;
        if (distance < best) { best = distance; near = t; }
    }
    const hints = ['id', 'name', 'type', 'title', 'placeholder', 'aria-describedby']
        .map(a => e.getAttribute(a)).filter(v => v && v.length < 60).join(' ');
    return {near: near, hints: hints};
}"""

FIELD_KIND_JS = """function() {
    """ + ELEMENT_JS + """
    const tag = e.tagName.toLowerCase();
    if (tag === 'select') return 'select';
    if (tag === 'input' && (e.type === 'checkbox' || e.type === 'radio')) return 'check';
    const autocomplete = e.getAttribute('aria-autocomplete') || e.closest('[role=combobox], .ng-select, [aria-autocomplete]');
    if (autocomplete && (tag === 'input' || e.querySelector('input'))) return 'autocomplete';
    if (tag === 'input' || tag === 'textarea' || e.isContentEditable) return 'text';
    return 'none';
}"""

# The text input inside an autocomplete box (the box itself may be a div with role=combobox).
INNER_INPUT_JS = """function() {
    """ + ELEMENT_JS + """
    const input = e.tagName === 'INPUT' ? e : e.querySelector('input');
    if (!input) return null;
    input.scrollIntoViewIfNeeded && input.scrollIntoViewIfNeeded();
    const r = input.getBoundingClientRect();
    return [r.left + r.width / 2, r.top + r.height / 2];
}"""

# What the focused field holds right now, or null when nothing editable has focus.
ACTIVE_VALUE_JS = """() => {
    const e = document.activeElement;
    if (!e || e === document.body) return null;
    const editable = e.tagName === 'INPUT' || e.tagName === 'TEXTAREA' || e.isContentEditable;
    if (!editable) return null;
    const r = e.getBoundingClientRect();
    return {value: (e.isContentEditable ? e.innerText : e.value) || '', x: r.left + r.width / 2, y: r.top + r.height / 2};
}"""

# An open dialog, if any, so a click's result says what it opened.
DIALOG_JS = """() => {
    const d = [...document.querySelectorAll('[role=dialog], [role=alertdialog], .modal.show, .swal2-container')]
        .find(n => n.getClientRects().length > 0 && getComputedStyle(n).visibility !== 'hidden');
    return d ? d.innerText.trim().replace(/\\s+/g, ' ').slice(0, 200) : null;
}"""

SET_SELECT_JS = """function(value) {
    const wanted = String(value).trim();
    const option = [...this.options].find(o => o.value === wanted || o.text.trim() === wanted);
    if (!option) return {ok: false, options: [...this.options].map(o => o.text.trim())};
    Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value').set.call(this, option.value);
    this.dispatchEvent(new Event('input', {bubbles: true}));
    this.dispatchEvent(new Event('change', {bubbles: true}));
    return {ok: true, value: option.text.trim()};
}"""

READ_VALUE_JS = "function() { " + ELEMENT_JS + " return (e.isContentEditable ? e.innerText : e.value) || ''; }"
IS_CHECKED_JS = "function() { " + ELEMENT_JS + " return !!e.checked; }"
CENTER_JS = """function() {
    """ + ELEMENT_JS + """
    if (e.scrollIntoViewIfNeeded) e.scrollIntoViewIfNeeded(); else e.scrollIntoView({block: 'center'});
    const r = e.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return null;
    return [r.left + r.width / 2, r.top + r.height / 2];
}"""


class StaleRef(Exception):
    pass


@dataclass
class Node:
    ref: str
    role: str
    name: str
    depth: int
    backend_id: int
    value: str = ""
    checked: str = ""
    disabled: bool = False
    near: str = ""
    hints: str = ""      # DOM id/name/type/title, for find(); not shown to the model
    url: str = ""        # a link's target, path and query only

    @property
    def interactive(self) -> bool:
        return self.role in INTERACTIVE_ROLES

    def line(self) -> str:
        parts = [f'{self.role} "{_short(self.name, 80)}"']
        if self.url:
            parts.append(f"-> {_short(self.url, 70)}")
        if self.near:
            parts.append(f'(near: "{self.near}")')
        if self.value:
            parts.append(f'value="{self.value}"')
        if self.checked:
            parts.append("checked" if self.checked == "true" else "unchecked")
        if self.disabled:
            parts.append("disabled")
        parts.append(f"[{self.ref}]")
        return "  " * self.depth + " ".join(parts)

    def score(self, words: list[str], wanted_role: str | None) -> int:
        """How well a find() query fits this element. Name or label beats value beats role."""
        name = f"{_short(self.name, 80)} {self.near}".lower()   # an unnamed field's nearby label is its name
        hints = f"{self.hints} {self.url}".lower()
        value = self.value.lower()
        score = 0
        for word in words:
            if word in name:
                score += 3
            elif word in hints:
                score += 2
            elif word in value:
                score += 1
        if wanted_role and self.role == wanted_role:
            score += 2
        if score and self.interactive:
            score += 3        # something to click or type into beats a matching piece of text
        return score


class TabRefs:
    """Refs handed out on one tab. Cleared when the tab navigates."""

    def __init__(self):
        self.counter = 0
        self.ref_to_node = {}
        self.node_to_ref = {}
        self.last_read = []

    def ref_for(self, backend_id: int) -> str:
        if backend_id not in self.node_to_ref:
            self.counter += 1
            ref = f"ref_{self.counter}"
            self.node_to_ref[backend_id] = ref
            self.ref_to_node[ref] = backend_id
        return self.node_to_ref[backend_id]

    def node_for(self, ref: str) -> int:
        if ref not in self.ref_to_node:
            raise StaleRef(ref)
        return self.ref_to_node[ref]


class ToolsetExecutor:
    def __init__(self, browser: Browser):
        self.browser = browser
        self._refs: dict[str, TabRefs] = {}
        self._cdp: dict[str, CDPSession] = {}
        self._reported_tabs: set[str] = set(browser.tabs)

    def run(self, name: str, args: dict):
        match name:
            case "navigate":
                return self.navigate(args)
            case "screenshot":
                return self.screenshot(args)
            case "zoom":
                return self.zoom(args)
            case "read_page":
                return self.read_page(args)
            case "find":
                return self.find(args)
            case "get_page_text":
                return self.get_page_text(args)
            case "left_click":
                return self.click(args, button="left", count=1)
            case "right_click":
                return self.click(args, button="right", count=1)
            case "double_click":
                return self.click(args, button="left", count=2)
            case "triple_click":
                return self.click(args, button="left", count=3)
            case "hover":
                return self.hover(args)
            case "mouse_move":
                return self.hover(args)
            case "scroll":
                return self.scroll(args)
            case "scroll_to":
                return self.scroll_to(args)
            case "type":
                return self.type_text(args)
            case "key":
                return self.key(args)
            case "wait":
                return self.wait(args)
            case "form_input":
                return self.form_input(args)
            case "new_tab":
                return self.new_tab()
            case "list_tabs":
                return self.list_tabs()
            case "switch_tab":
                return self.switch_tab(args)
            case "close_tab":
                return self.close_tab(args)
            case _:
                raise ValueError(f"Unknown or disabled member: {name}")

    # ---- navigation and capture ---------------------------------------------

    def navigate(self, args: dict):
        tab_id = self._tab(args)
        page = self._page(tab_id)
        url = args["url"].strip()
        if url == "back":
            page.go_back(wait_until="domcontentloaded")
        elif url == "forward":
            page.go_forward(wait_until="domcontentloaded")
        elif url == "reload":
            page.reload(wait_until="domcontentloaded")
        else:
            page.goto(self._checked_url(url), wait_until="domcontentloaded")
        self._after_action(page)
        self._checked_url(page.url)          # re-check after redirects
        self._refs[tab_id] = TabRefs()
        return [{"type": "text", "text": f"Navigated to {page.url}"}, self._browser_state()]

    def screenshot(self, args: dict):
        page = self._page(self._tab(args))
        return [_image_block(self.browser.screenshot(page))]

    def zoom(self, args: dict):
        page = self._page(self._tab(args))
        x0, y0, x1, y1 = args["region"]
        clip = {"x": x0, "y": y0, "width": max(1, x1 - x0), "height": max(1, y1 - y0)}
        return [_image_block(self.browser.screenshot(page, clip=clip))]

    # ---- reading ------------------------------------------------------------

    def read_page(self, args: dict) -> str:
        tab_id = self._tab(args)
        nodes = self._collect_nodes(tab_id, args.get("filter"), int(args.get("depth", 15)), args.get("ref"))
        if not nodes:
            return "No elements found."
        text = "\n".join(node.line() for node in nodes)
        if len(text) > config.READ_PAGE_MAX_CHARS:
            text = text[: config.READ_PAGE_MAX_CHARS] + "\n... [truncated; use filter or ref to narrow]"
        return text

    def find(self, args: dict) -> str:
        tab_id = self._tab(args)
        nodes = self._collect_nodes(tab_id, None, 15, None)
        words, wanted_role = _query_words(args["query"])
        scored = [(node.score(words, wanted_role), node) for node in nodes]
        scored = [(score, node) for score, node in scored if score > 0]
        scored.sort(key=lambda pair: -pair[0])
        matches = [node for _, node in scored[:20]]
        if not matches:
            return f'No elements match "{args["query"]}". Try read_page.'
        return "\n".join(node.line().strip() for node in matches)

    def get_page_text(self, args: dict) -> str:
        page = self._page(self._tab(args))
        return self.browser.page_text(page)

    # ---- pointer ------------------------------------------------------------

    def click(self, args: dict, button: str, count: int) -> str:
        tab_id = self._tab(args)
        page = self._page(tab_id)
        x, y = self._point(tab_id, args["target"])
        url_before = page.url
        seen = self._interactive_ids(tab_id)
        with self._modifiers(page, args.get("modifiers")):
            page.mouse.click(x, y, button=button, click_count=count)
        self._after_action(page)
        text = f"Clicked {self._describe(args['target'])}."
        dialog = page.evaluate(DIALOG_JS)
        if dialog:
            text += f" A dialog is open: {dialog!r}"
        if page.url == url_before:
            text += self._appeared_since(tab_id, seen)
        return self._action_result(text, page, url_before)

    def hover(self, args: dict) -> str:
        tab_id = self._tab(args)
        page = self._page(tab_id)
        x, y = self._point(tab_id, args["target"])
        page.mouse.move(x, y)
        self.browser.settle(page)
        return f"Hovered {self._describe(args['target'])}."

    def scroll(self, args: dict) -> str:
        tab_id = self._tab(args)
        page = self._page(tab_id)
        x, y = self._point(tab_id, args["target"])
        notches = int(args.get("scroll_amount", 3)) * 120
        direction = args["scroll_direction"]
        dx = {"left": -notches, "right": notches}.get(direction, 0)
        dy = {"up": -notches, "down": notches}.get(direction, 0)
        page.mouse.move(x, y)
        page.mouse.wheel(dx, dy)
        self.browser.settle(page)
        return f"Scrolled {direction}."

    def scroll_to(self, args: dict) -> str:
        tab_id = self._tab(args)
        self._call_on_ref(tab_id, args["target"]["ref"], CENTER_JS)
        self.browser.settle(self._page(tab_id))
        return f"Scrolled to {args['target']['ref']}."

    # ---- keyboard -----------------------------------------------------------

    def type_text(self, args: dict) -> str:
        """Type into the focused field. If the field did not take the text, click it and try once more."""
        page = self._page(self._tab(args))
        text = args["text"]
        page.keyboard.type(text, delay=10)
        field = page.evaluate(ACTIVE_VALUE_JS)
        if field and text not in field["value"]:
            page.mouse.click(field["x"], field["y"])
            page.keyboard.type(text, delay=10)
            field = page.evaluate(ACTIVE_VALUE_JS)
        self._after_action(page)
        if field is None:
            return f"Typed: {text}. Nothing editable had focus; check where it went."
        return f"Typed: {text}. Field now contains: {field['value'][:80]!r}"

    def key(self, args: dict) -> str:
        page = self._page(self._tab(args))
        for _ in range(int(args.get("repeat", 1))):
            for chord in args["text"].split():
                page.keyboard.press(_playwright_chord(chord))
        self._after_action(page)
        return f"Pressed: {args['text']}"

    def wait(self, args: dict) -> str:
        page = self._page(self._tab(args))
        page.wait_for_timeout(float(args["duration"]) * 1000)
        return f"Waited {args['duration']}s."

    # ---- forms --------------------------------------------------------------

    def form_input(self, args: dict) -> str:
        tab_id = self._tab(args)
        page = self._page(tab_id)
        ref = args["target"]["ref"]
        value = args["value"]
        kind = self._call_on_ref(tab_id, ref, FIELD_KIND_JS)
        seen = self._interactive_ids(tab_id)
        match kind:
            case "select":
                result = self._set_select(tab_id, ref, value)
            case "check":
                result = self._set_checked(tab_id, ref, value, page)
            case "autocomplete":
                result = self._set_autocomplete(tab_id, ref, str(value), page)
            case "text":
                result = self._set_text(tab_id, ref, str(value), page)
            case _:
                raise ValueError(f"{ref} is not an editable field. Use read_page to find the textbox, combobox or checkbox.")
        self._after_action(page)
        return result + self._appeared_since(tab_id, seen)

    def _set_select(self, tab_id: str, ref: str, value) -> str:
        outcome = self._call_on_ref(tab_id, ref, SET_SELECT_JS, value)
        if not outcome["ok"]:
            raise ValueError(f"No option {value!r} in {ref}. Options: {outcome['options']}")
        return f"Selected {outcome['value']!r} in {ref}."

    def _set_checked(self, tab_id: str, ref: str, value, page: Page) -> str:
        wanted = bool(value)
        if self._call_on_ref(tab_id, ref, IS_CHECKED_JS) != wanted:
            x, y = self._point(tab_id, {"type": "ref", "ref": ref})
            page.mouse.click(x, y)
        now = self._call_on_ref(tab_id, ref, IS_CHECKED_JS)
        return f"Set {ref} to {'checked' if now else 'unchecked'}."

    def _set_autocomplete(self, tab_id: str, ref: str, text: str, page: Page) -> str:
        """
        An autocomplete box (ng-select and friends): type into its input, wait for the options,
        pick the one that matches, read back what the box now shows.
        """
        center = self._call_on_ref(tab_id, ref, INNER_INPUT_JS)
        if center is None:
            raise ValueError(f"{ref} has no text input to type into.")
        page.mouse.click(center[0], center[1])
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.press("Backspace")
        page.keyboard.type(text, delay=10)
        options = self._wait_for_options(tab_id)
        if not options:
            # Nothing matched what was typed. Clear it and show what the box does offer.
            page.keyboard.press("ControlOrMeta+a")
            page.keyboard.press("Backspace")
            offered = [o.name for o in self._wait_for_options(tab_id)][:12]
            raise ValueError(f"No option matches {text!r} in {ref}. The box offers: {offered or 'nothing yet; try clicking it'}.")
        chosen = _best_option(options, text)
        x, y = self._point(tab_id, {"type": "ref", "ref": chosen.ref})
        page.mouse.click(x, y)
        self.browser.settle(page)
        others = [o.name for o in options if o is not chosen][:6]
        note = f" Other options were: {others}." if others else ""
        return f"Selected {chosen.name!r} in {ref}.{note}"

    def _wait_for_options(self, tab_id: str) -> list[Node]:
        page = self._page(tab_id)
        for _ in range(8):
            page.wait_for_timeout(300)
            options = [n for n in self._collect_nodes(tab_id, "interactive", 15, None) if n.role == "option"]
            if options:
                return options
        return []

    def _set_text(self, tab_id: str, ref: str, text: str, page: Page) -> str:
        x, y = self._point(tab_id, {"type": "ref", "ref": ref})
        page.mouse.click(x, y)
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.press("Backspace")
        page.keyboard.type(text, delay=10)
        now = self._call_on_ref(tab_id, ref, READ_VALUE_JS)
        return f"Filled {ref} with {len(text)} characters. Now contains: {now[:80]!r}"

    # ---- tabs ---------------------------------------------------------------

    def new_tab(self):
        self.browser.new_tab()
        return [self._browser_state()]

    def list_tabs(self):
        return [self._browser_state()]

    def switch_tab(self, args: dict):
        self.browser.switch_tab(args["tab_id"])
        return [self._browser_state()]

    def close_tab(self, args: dict):
        tab_id = args["tab_id"]
        self.browser.close_tab(tab_id)
        self._refs.pop(tab_id, None)
        self._cdp.pop(tab_id, None)
        return [self._browser_state()]

    # ---- helpers: tabs and sessions ----------------------------------------

    def _tab(self, args: dict) -> str:
        return args.get("tab_id") or self.browser.active_tab_id

    def _page(self, tab_id: str) -> Page:
        return self.browser.tab_page(tab_id)

    def _refs_for(self, tab_id: str) -> TabRefs:
        if tab_id not in self._refs:
            self._refs[tab_id] = TabRefs()
        return self._refs[tab_id]

    def _cdp_send(self, tab_id: str, method: str, params: dict | None = None):
        if tab_id not in self._cdp:
            page = self._page(tab_id)
            self._cdp[tab_id] = page.context.new_cdp_session(page)
        return self._cdp[tab_id].send(method, params or {})

    def _after_action(self, page: Page) -> None:
        self.browser.settle(page)
        self.browser.dismiss_toasts(page)

    def _action_result(self, text: str, page: Page, url_before: str) -> list | str:
        """Attach a browser_state block when the tab set or the URL changed under the action."""
        new_tabs = set(self.browser.tabs) - self._reported_tabs
        if new_tabs or page.url != url_before:
            return [{"type": "text", "text": text}, self._browser_state()]
        return text

    def _browser_state(self) -> dict:
        block = {"type": "browser_state", "tabs": self.browser.tab_state()}
        opened = [tab_id for tab_id in self.browser.tabs if tab_id not in self._reported_tabs]
        if opened:
            block["state_changes"] = [{"type": "tab_opened", "tab_id": tab_id} for tab_id in opened]
        self._reported_tabs = set(self.browser.tabs)
        return block

    def _checked_url(self, url: str) -> str:
        from urllib.parse import urlparse
        if "://" not in url:
            url = "https://" + url
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Navigation refused. Only http and https URLs are allowed, not {parsed.scheme}:")
        if parsed.hostname not in config.ALLOWED_HOSTS:
            raise ValueError(f"Navigation refused. {parsed.hostname} is not an allowed host.")
        return url

    # ---- helpers: the accessibility tree -----------------------------------

    def _collect_nodes(self, tab_id: str, filter_name: str | None, max_depth: int, root_ref: str | None) -> list[Node]:
        page = self._page(tab_id)
        self.browser.settle(page)
        self.browser.dismiss_toasts(page)
        refs = self._refs_for(tab_id)
        raw = self._cdp_send(tab_id, "Accessibility.getFullAXTree")["nodes"]
        by_id = {node["nodeId"]: node for node in raw}
        root = raw[0]
        if root_ref:
            wanted = refs.node_for(root_ref)
            root = next((n for n in raw if n.get("backendDOMNodeId") == wanted), root)

        collected: list[Node] = []

        def walk(raw_node: dict, depth: int, parent_name: str) -> None:
            printable = _printable(raw_node)
            name = raw_node.get("name", {}).get("value", "")
            if printable and _role_of(raw_node) == "text" and name == parent_name:
                printable = False     # the text inside a button, link or heading repeats its name
            if printable:
                node = _to_node(raw_node, depth, refs)
                if node.interactive and not node.name:
                    node.near, node.hints = self._near_label(tab_id, node.backend_id)
                if filter_name != "interactive" or node.interactive:
                    collected.append(node)
                if node.role == "textbox":
                    return            # a field's inner text nodes are not separate elements
            next_depth = depth + 1 if printable else depth
            if next_depth > max_depth:
                return
            # Ignored nodes (html, body, layout wrappers) are not printed but their children are.
            for child_id in raw_node.get("childIds", []):
                child = by_id.get(child_id)
                if child:
                    walk(child, next_depth, name if printable else parent_name)

        walk(root, 0, "")
        refs.last_read = collected
        return collected

    def _interactive_ids(self, tab_id: str) -> set[int]:
        """The interactive elements on the page right now, so an action can report what it revealed."""
        try:
            return {n.backend_id for n in self._collect_nodes(tab_id, "interactive", 15, None)}
        except Exception:
            return set()

    def _appeared_since(self, tab_id: str, seen: set[int]) -> str:
        """Interactive elements that were not there before the action: a colour box after a style, a form after a click."""
        if not seen:
            return ""
        try:
            nodes = [n for n in self._collect_nodes(tab_id, "interactive", 15, None) if n.backend_id not in seen]
        except Exception:
            return ""
        if not nodes:
            return ""
        lines = [n.line().strip() for n in nodes[:10]]
        more = f" (+{len(nodes) - 10} more; use find to narrow)" if len(nodes) > 10 else ""
        return " New on the page: " + " | ".join(lines) + more

    def _near_label(self, tab_id: str, backend_id: int) -> tuple[str, str]:
        try:
            found = self._call_on_node(tab_id, backend_id, NEAR_LABEL_JS)
            return found["near"], found["hints"]
        except Exception:
            return "", ""

    # ---- helpers: acting on refs -------------------------------------------

    def _point(self, tab_id: str, target: dict) -> tuple[float, float]:
        if target["type"] == "coordinate":
            return float(target["x"]), float(target["y"])
        center = self._call_on_ref(tab_id, target["ref"], CENTER_JS)
        if center is None:
            raise ValueError(f"{target['ref']} has no visible box to click. Use a screenshot and click by coordinate.")
        return float(center[0]), float(center[1])

    def _call_on_ref(self, tab_id: str, ref: str, function: str, *arguments):
        backend_id = self._refs_for(tab_id).node_for(ref)
        try:
            return self._call_on_node(tab_id, backend_id, function, *arguments)
        except StaleRef:
            raise
        except Exception:
            raise StaleRef(ref)

    def _call_on_node(self, tab_id: str, backend_id: int, function: str, *arguments):
        object_id = self._cdp_send(tab_id, "DOM.resolveNode", {"backendNodeId": backend_id})["object"]["objectId"]
        outcome = self._cdp_send(tab_id, "Runtime.callFunctionOn", {
            "objectId": object_id,
            "functionDeclaration": function,
            "arguments": [{"value": value} for value in arguments],
            "returnByValue": True,
        })
        if "exceptionDetails" in outcome:
            raise RuntimeError(outcome["exceptionDetails"].get("text", "JavaScript error"))
        return outcome["result"].get("value")

    @staticmethod
    def _describe(target: dict) -> str:
        if target["type"] == "ref":
            return f"element {target['ref']}"
        return f"({target['x']}, {target['y']})"

    class _modifiers:
        """Hold modifier keys around one pointer action."""

        def __init__(self, page: Page, modifiers: str | None):
            self.page = page
            self.keys = [_playwright_chord(m) for m in (modifiers or "").split("+") if m]

        def __enter__(self):
            for key in self.keys:
                self.page.keyboard.down(key)

        def __exit__(self, *_):
            for key in reversed(self.keys):
                self.page.keyboard.up(key)


# ---- module helpers ---------------------------------------------------------

def stale_ref_message(ref: str) -> str:
    return f"Error: {ref} is stale or not found on the current page. Re-read the page to get fresh references."


def _image_block(png: bytes) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode()},
    }


def _best_option(options: list, text: str):
    """Exact name first, then a name containing the text, then the first option shown."""
    wanted = text.strip().lower()
    for option in options:
        if option.name.strip().lower() == wanted:
            return option
    # "Add Item ..." creates a new entry; never pick it by accident.
    real = [o for o in options if not o.name.lower().startswith("add item")]
    for option in real:
        if wanted in option.name.lower():
            return option
    if real:
        return real[0]
    raise ValueError(f"No existing option matches {text!r}; the only choice is {options[0].name!r}, which would create a new entry.")


def _short(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _query_words(query: str) -> tuple[list[str], str | None]:
    """Split a find() query into words to match on text, plus the role it names, if any."""
    raw = [w for w in query.lower().replace('"', " ").replace("'", " ").replace(",", " ").split() if w]
    wanted_role = next((ROLE_WORDS[w] for w in raw if w in ROLE_WORDS), None)
    words = [w for w in raw if w not in ROLE_WORDS and w not in NOISE_WORDS]
    words += [QUERY_SYNONYMS[w] for w in words if w in QUERY_SYNONYMS]
    return words, wanted_role


def _playwright_chord(chord: str) -> str:
    parts = chord.split("+")
    return "+".join(KEY_NAMES.get(part.lower(), part) for part in parts)


def _property(raw_node: dict, name: str):
    for prop in raw_node.get("properties", []):
        if prop["name"] == name:
            return prop["value"].get("value")
    return None


def _role_of(raw_node: dict) -> str:
    role = raw_node.get("role", {}).get("value", "")
    if role == "generic" and _property(raw_node, "editable") == "richtext":
        return "textbox"    # a contenteditable editor
    if role == "StaticText":
        return "text"
    return role


def _printable(raw_node: dict) -> bool:
    if raw_node.get("ignored") or raw_node.get("backendDOMNodeId") is None:
        return False
    role = _role_of(raw_node)
    if role in SKIP_ROLES:
        return False
    name = raw_node.get("name", {}).get("value", "")
    return role in INTERACTIVE_ROLES or bool(name)


def _to_node(raw_node: dict, depth: int, refs: TabRefs) -> Node:
    backend_id = raw_node["backendDOMNodeId"]
    value = raw_node.get("value", {}).get("value", "")
    checked = _property(raw_node, "checked")
    url = _property(raw_node, "url") or ""
    if url.startswith("http"):
        url = url.split("://", 1)[1].split("/", 1)[1] if "/" in url.split("://", 1)[1] else ""
        url = "/" + url
    return Node(
        ref=refs.ref_for(backend_id),
        role=_role_of(raw_node),
        name=raw_node.get("name", {}).get("value", ""),
        depth=depth,
        backend_id=backend_id,
        value=str(value)[:80] if value not in (None, "") else "",
        checked="" if checked is None else str(checked).lower(),
        disabled=bool(_property(raw_node, "disabled")),
        url=url if url not in ("/", "/#") else "",
    )

"""
Runs Anthropic's browser toolset and catalog verification against Playwright.

One method per member. `run()` only routes. Every method returns the tool result content:
a string, or a list of content blocks (image, browser_state). Errors are raised; the brain
turns them into is_error results and halts the rest of the batch.

References: read_page and find tag elements as [ref_N]. Each ref maps to a Chromium
accessibility node (backendDOMNodeId) on one tab. Refs are never renumbered until that tab
navigates; a ref whose node is gone gets the stale-ref error the contract asks for.
"""

import base64
import json
import re
import time
from dataclasses import dataclass

from playwright.sync_api import CDPSession, Page

import config
from catalog_state import inspect_catalog_product
from browser import Browser
from observation_state import CONTROL_STATE_JS
from tool_policy import validate_tool_call

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
BLUR_JS = "function() { " + ELEMENT_JS + " if (e.blur) e.blur(); }"
# Elements the accessibility tree does not list as controls but the page treats as clickable:
# role-less DIVs with a pointer cursor, a tabindex or a button class (React chips, tiles, cards).
# Each one is tagged with data-sasha-click=<index> so CDP can find its DOM node afterwards.
CLICKABLE_JS = "() => {" + CONTROL_STATE_JS + """
    const skipTags = new Set(['A', 'BUTTON', 'INPUT', 'SELECT', 'TEXTAREA', 'LABEL', 'OPTION', 'HTML', 'BODY', 'CANVAS', 'SVG', 'PATH']);
    const found = [];
    for (const e of document.querySelectorAll('*')) {
        e.removeAttribute('data-sasha-click');
        if (skipTags.has(e.tagName) || e.getAttribute('role') || e.closest('a, button')) continue;
        const r = e.getBoundingClientRect();
        if (r.width === 0 || r.height === 0 || !e.checkVisibility({checkVisibilityCSS: true})) continue;
        const cls = (e.className || '').toString();
        const cursor = getComputedStyle(e).cursor;
        const parentCursor = e.parentElement ? getComputedStyle(e.parentElement).cursor : '';
        const startsPointerRegion = cursor === 'pointer' && parentCursor !== 'pointer';
        const control = cardControl(e);
        const explicit = control || e.hasAttribute('tabindex') || e.hasAttribute('onclick') ||
            /(^|[\\s_-])(btn|button)([\\s_-]|$)/.test(cls) || e.style.cursor === 'pointer' || startsPointerRegion;
        const clickable = explicit || cursor === 'pointer';
        if (!clickable) continue;
        const text = (e.innerText || '').replace(/\\s+/g, ' ').trim();
        if (text.length > (control ? 500 : 120)) continue;
        if (!text && !e.hasAttribute('tabindex') && !e.dataset?.testid) continue;
        found.push({el: e, text, explicit, hint: [e.dataset?.testid, cls.split(' ')[0]].filter(Boolean).join(' ')});
    }
    // Keep an actionable wrapper over a child that only inherited its pointer cursor.
    // When both carry the same strength of signal, keep the smaller inner target.
    // a container holding two or more named clickables (a card grid) is not a control itself.
    const keep = found.filter(f => {
        const sameTextParents = found.filter(g => g !== f && g.el.contains(f.el) && g.text === f.text);
        if (!f.explicit && sameTextParents.some(g => g.explicit)) return false;
        const inner = found.filter(g => g !== f && f.el.contains(g.el));
        if (f.explicit && inner.some(g => g.text === f.text && !g.explicit)) return true;
        if (inner.some(g => g.text === f.text)) return false;
        return inner.filter(g => g.text).length < 2;
    });
    keep.forEach((f, i) => f.el.setAttribute('data-sasha-click', String(i)));
    return keep.map(f => ({text: stateLabel(f.el) + f.text, hint: f.hint, disabled: isDisabled(f.el)}));
}"""

# The tooltip showing right now, if any. Colour swatches and icon buttons carry their name here.
TOOLTIP_JS = """() => {
    const tips = [...document.querySelectorAll('[role=tooltip], [class*=tooltip]')]
        .filter(e => e.getBoundingClientRect().width > 0 && (e.innerText || '').trim());
    if (!tips.length) return '';
    tips.sort((a, b) => b.innerText.length - a.innerText.length);
    return tips[0].innerText.replace(/\\s*\\n+\\s*/g, ' / ').trim().slice(0, 80);
}"""

MAX_TOOLTIP_HOVERS = 24      # unnamed tiles named by hovering, per read of the page
OPTION_WAIT_SECONDS = 3.0
OPTION_POLL_MS = 100

CENTER_JS = """function() {
    """ + ELEMENT_JS + """
    if (e.scrollIntoViewIfNeeded) e.scrollIntoViewIfNeeded(); else e.scrollIntoView({block: 'center'});
    const r = e.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return null;
    return [r.left + r.width / 2, r.top + r.height / 2];
}"""

# Only the open dropdown is inspected. Each option gets a temporary index so its backend
# node id can be resolved without asking Chromium for the full accessibility tree.
DROPDOWN_OPTIONS_JS = """() => {
    for (const old of document.querySelectorAll('[data-sasha-option]')) {
        old.removeAttribute('data-sasha-option');
    }
    const visible = element => {
        const style = getComputedStyle(element);
        const box = element.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && style.display !== 'none' &&
            style.visibility !== 'hidden' && style.opacity !== '0';
    };
    const active = document.activeElement;
    const combo = active && (active.closest('.ng-select') ||
        active.closest('[role=combobox], [aria-autocomplete]'));
    const controlledId = active?.getAttribute('aria-controls') || combo?.getAttribute('aria-controls');
    const controlled = controlledId ? document.getElementById(controlledId) : null;
    const panels = controlled && visible(controlled)
        ? [controlled]
        : [...document.querySelectorAll('[role=listbox], .ng-dropdown-panel, [class*=autocomplete]')]
            .filter(visible);
    const panel = panels.length === 1 ? panels[0] : null;
    const input = active?.value;
    const busy = element => element && (element.getAttribute('aria-busy') === 'true' ||
        element.classList.contains('ng-select-loading') ||
        [...element.querySelectorAll('[aria-busy=true], .ng-spinner-loader, [role=progressbar]')].some(visible));
    const loading = busy(combo) || busy(panel) ||
        !!panel && /^(loading|searching)(\\.\\.\\.|…)?$/im.test((panel.innerText || '').trim());
    if (!panel) return {loading: !!loading, input, noMatch: false, items: []};
    const noMatch = /^(no items found|no results found|no options|no matches found)$/im.test((panel.innerText || '').trim());
    const choices = [...panel.querySelectorAll('[role=option], .ng-option, [class~=option]')]
        .filter(element => visible(element) &&
            !element.querySelector('[role=option], .ng-option') &&
            !/loading/i.test((element.innerText || '').trim()));
    const items = choices.map((element, index) => {
        element.setAttribute('data-sasha-option', String(index));
        return {
            index,
            disabled: element.matches(':disabled, [aria-disabled=true], .ng-option-disabled') ||
                !!element.closest('[aria-disabled=true], [inert]'),
            text: (element.innerText || element.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 120),
            hint: (element.className || '').toString().split(' ')[0]
        };
    }).filter(item => item.text);
    return {loading: !!loading, input, noMatch, items};
}"""

SELECTION_STATE_JS = """function() {
    """ + ELEMENT_JS + """
    const root = e.closest('.ng-select') || e.closest('[role=combobox]') || e;
    const input = root.matches('input') ? root : root.querySelector('input');
    const visible = element => {
        if (!element) return false;
        const style = getComputedStyle(element);
        const box = element.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && style.display !== 'none' &&
            style.visibility !== 'hidden' && style.opacity !== '0';
    };
    const panelSelector = '[role=listbox], .ng-dropdown-panel, [class*=autocomplete], ' +
        '[class$="-menu"], [class*="__menu"]';
    const controlledId = input?.getAttribute('aria-controls') || root.getAttribute('aria-controls');
    const controlled = controlledId ? document.getElementById(controlledId) : null;
    const optionPanel = panel => panel && visible(panel) &&
        (panel === controlled || panel.matches('[role=listbox], .ng-dropdown-panel') ||
            !!panel.querySelector('[role=option], .ng-option, [class~=option]'));
    const related = [controlled, ...root.querySelectorAll(panelSelector)]
        .filter((panel, index, all) => all.indexOf(panel) === index && optionPanel(panel));
    const global = [...document.querySelectorAll(panelSelector)].filter(optionPanel);
    const panels = related.length ? related : (global.length === 1 ? global : []);
    const labels = [...root.querySelectorAll(
        '.ng-value-label, .ng-value, [aria-selected=true], [data-selected=true], ' +
        '.selected-value, [class*=singleValue]'
    )].filter(node => !node.closest(panelSelector) && visible(node))
        .map(node => (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim())
        .filter(text => text && text.length <= 120);
    const expanded = root.getAttribute('aria-expanded') || input?.getAttribute('aria-expanded');
    const panelOpen = panels.length > 0;
    const rendered = (root.innerText || root.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 240);
    return {labels: [...new Set(labels)], value: input?.value || '', rendered,
        closed: expanded === 'false' || !panelOpen, panelOpen,
        loading: root.classList.contains('ng-select-loading') || root.getAttribute('aria-busy') === 'true'};
}"""


class StaleRef(Exception):
    pass


class PageStillProcessing(Exception):
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
        if words and " ".join(words) == _short(self.name, 80).lower():
            score += 4        # the whole query is this element's name
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
        self.tooltip_names: dict[int, str] = {}   # unnamed tiles, named once by hovering

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
    def __init__(self, browser: Browser, attachments=None):
        self.browser = browser
        self.attachments = attachments          # AttachmentSet for this turn, or None
        self._refs: dict[str, TabRefs] = {}
        self._cdp: dict[str, CDPSession] = {}
        self._reported_tabs: set[str] = set(browser.tabs)
        self.last_timings = self._empty_timings()

    def run(self, name: str, args: dict):
        self.browser.invalidate_screenshot_cache()
        self.last_timings = self._empty_timings()
        validate_tool_call(name, args)
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
            case "inspect_catalog_product":
                return self.inspect_catalog_product(args)
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
            case "attach_file":
                return self.attach_file(args)
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
        self._checked_url(page.url)          # re-check after redirects
        self._refs[tab_id] = TabRefs()
        self._after_action(page)
        return [{"type": "text", "text": f"Navigated to {page.url}"}, self._browser_state()]

    def screenshot(self, args: dict):
        page = self._page(self._tab(args))
        png = self._measure("capture_seconds", self.browser.screenshot, page)
        return [_image_block(png)]

    def zoom(self, args: dict):
        page = self._page(self._tab(args))
        x0, y0, x1, y1 = args["region"]
        clip = {"x": x0, "y": y0, "width": max(1, x1 - x0), "height": max(1, y1 - y0)}
        png = self._measure("capture_seconds", self.browser.screenshot, page, clip)
        return [_image_block(png)]

    # ---- reading ------------------------------------------------------------

    def read_page(self, args: dict) -> str:
        tab_id = self._tab(args)
        filter_name = args.get("filter")
        nodes = self._collect_nodes(
            tab_id, filter_name, int(args.get("depth", 15)), args.get("ref"),
            discover_tooltips=filter_name == "interactive",
        )
        if not nodes:
            return "No elements found."
        text = "\n".join(node.line() for node in nodes)
        if len(text) > config.READ_PAGE_MAX_CHARS:
            text = text[: config.READ_PAGE_MAX_CHARS] + "\n... [truncated; use filter or ref to narrow]"
        return text

    def find(self, args: dict) -> str:
        tab_id = self._tab(args)
        nodes = self._collect_nodes(tab_id, None, 15, None, discover_tooltips=True)
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

    def inspect_catalog_product(self, args: dict) -> str:
        tab_id = self._tab(args)
        page = self._page(tab_id)
        nodes = self._collect_nodes(tab_id, None, 15, None, discover_tooltips=False)
        record = inspect_catalog_product(
            page.url,
            args["style_code"],
            args["color"],
            [_node_record(node) for node in nodes],
        )
        return json.dumps(record, sort_keys=True)

    # ---- pointer ------------------------------------------------------------

    def click(self, args: dict, button: str, count: int) -> str:
        tab_id = self._tab(args)
        page = self._page(tab_id)
        x, y = self._point(tab_id, args["target"])
        url_before = page.url
        with self._modifiers(page, args.get("modifiers")):
            page.mouse.click(x, y, button=button, click_count=count)
        self._after_action(page)
        text = f"Clicked {self._describe(args['target'])}."
        dialog = page.evaluate(DIALOG_JS)
        if dialog:
            text += f" A dialog is open: {dialog!r}"
        return self._action_result(text, page, url_before)

    def hover(self, args: dict) -> str:
        tab_id = self._tab(args)
        page = self._page(tab_id)
        x, y = self._point(tab_id, args["target"])
        page.mouse.move(x, y)
        self._after_action(page)
        text = f"Hovered {self._describe(args['target'])}."
        tip = page.evaluate(TOOLTIP_JS)
        if tip:
            text += f' Tooltip: "{tip}"'
        return text

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
        self._after_action(page)
        return f"Scrolled {direction}."

    def scroll_to(self, args: dict) -> str:
        tab_id = self._tab(args)
        self._call_on_ref(tab_id, args["target"]["ref"], CENTER_JS)
        self._after_action(self._page(tab_id))
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
        return result

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
        current = self._call_on_ref(tab_id, ref, SELECTION_STATE_JS)
        if _selection_matches(current, text, text):
            return f"Already selected and verified {text!r} in {ref}."
        page.mouse.click(center[0], center[1])
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.press("Backspace")
        page.keyboard.type(text, delay=10)
        search_deadline = time.monotonic() + OPTION_WAIT_SECONDS
        options, state = self._wait_for_options(tab_id, search_deadline, text)
        if state == "no_match":
            raise ValueError(f"Search completed with no matching option for {text!r}. Nothing was selected.")
        if not options:
            raise PageStillProcessing(
                f"Search for {text!r} is still loading or uncertain. Nothing was selected; "
                "inspect the current field and options before continuing. This is not a confirmed no-match."
            )
        chosen = _best_option(options, text)
        # Re-read immediately before clicking: options can change between polls.
        fresh, state = self._scoped_options(tab_id, text)
        if state != "empty" or not any(
            option.backend_id == chosen.backend_id and option.name == chosen.name and not option.disabled
            for option in fresh
        ):
            raise PageStillProcessing("The matching option changed or became unavailable. Inspect before selecting.")
        x, y = self._point(tab_id, {"type": "ref", "ref": chosen.ref})
        page.mouse.click(x, y)
        selection_deadline = time.monotonic() + OPTION_WAIT_SECONDS
        observed = self._verify_selection(tab_id, ref, chosen.name, text, selection_deadline)
        return f"Selected and verified {chosen.name!r} in {ref}; observed {_selection_display(observed)!r}."

    def _verify_selection(self, tab_id: str, ref: str, name: str, query: str,
                          deadline: float) -> dict:
        previous = None
        while True:
            state = self._call_on_ref(tab_id, ref, SELECTION_STATE_JS)
            signature = _selection_signature(state) if _selection_matches(state, name, query) else None
            if signature is not None and signature == previous and not state["loading"]:
                return state
            previous = signature
            if not self._poll_options(tab_id, deadline):
                raise PageStillProcessing(
                    "The option was clicked, but selection is unverified. Inspect the field before retrying."
                )

    def _poll_options(self, tab_id: str, deadline: float) -> bool:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        self._page(tab_id).wait_for_timeout(min(OPTION_POLL_MS, remaining * 1000))
        return True

    def _wait_for_options(self, tab_id: str, deadline: float, query: str | None = None) -> tuple[list[Node], str]:
        saw_loading = False
        previous_match = None
        while True:
            if query is None:
                options, state = self._measure("enrichment_seconds", self._scoped_options, tab_id)
            else:
                options, state = self._measure("enrichment_seconds", self._scoped_options, tab_id, query)
            saw_loading = saw_loading or state == "loading"
            # A cached empty list can survive typing while a debounce timer runs.
            # Require a processing cycle before calling this a completed no-match.
            if state == "no_match" and saw_loading:
                return [], "no_match"
            if state == "empty" and options:
                if query is None:
                    return options, "ready"
                try:
                    chosen = _best_option(options, query)
                except ValueError:
                    chosen = None
                match = (chosen.backend_id, chosen.name) if chosen else None
                if match and match == previous_match:
                    return options, "ready"
                previous_match = match
            else:
                previous_match = None
            if not self._poll_options(tab_id, deadline):
                return [], "loading" if state == "loading" else "uncertain"

    def _scoped_options(self, tab_id: str, query: str | None = None) -> tuple[list[Node], str]:
        outcome = self._page(tab_id).evaluate(DROPDOWN_OPTIONS_JS)
        backend_ids = self._backend_ids_by_index(tab_id, "[data-sasha-option]", "data-sasha-option")
        refs = self._refs_for(tab_id)
        options = []
        for item in outcome["items"]:
            backend_id = backend_ids.get(item["index"])
            if backend_id is None:
                continue
            options.append(Node(
                ref=refs.ref_for(backend_id), role="option", name=item["text"],
                depth=0, backend_id=backend_id, hints=item["hint"], disabled=item.get("disabled", False),
            ))
        if query is not None and outcome.get("input") != query:
            return [], "uncertain"
        if outcome["loading"]:
            return options, "loading"
        return options, "no_match" if outcome.get("noMatch") else "empty"

    def _set_text(self, tab_id: str, ref: str, text: str, page: Page) -> str:
        x, y = self._point(tab_id, {"type": "ref", "ref": ref})
        page.mouse.click(x, y)
        page.keyboard.press("ControlOrMeta+a")
        page.keyboard.press("Backspace")
        page.keyboard.type(text, delay=10)
        self._call_on_ref(tab_id, ref, BLUR_JS)      # number boxes apply their value on blur
        now = self._call_on_ref(tab_id, ref, READ_VALUE_JS)
        return f"Filled {ref} with {len(text)} characters. Now contains: {now[:80]!r}"

    # ---- tabs ---------------------------------------------------------------

    def attach_file(self, args: dict) -> str:
        """
        Give one of this turn's files to the page's file input.

        The Design Tool's input is 0x0 and hidden, so it is not in the accessibility tree and
        cannot be clicked: clicking the visible "Upload" tile opens the operating system's file
        dialog, which the browser cannot answer. Setting the files on the input directly is the
        only route, and it fires the same change event the page listens for.
        """
        if not self.attachments:
            raise ValueError("No files came with this turn, so there is nothing to attach.")
        path = self.attachments.path_for(str(args["file"]).strip())
        tab_id = self._tab(args)
        page = self._page(tab_id)

        inputs = page.query_selector_all("input[type=file]")
        if not inputs:
            raise ValueError("No file input on this page. Open the page that takes the upload first.")
        chosen = self._file_input(inputs, args.get("selector"))
        chosen.set_input_files(str(path))

        self._refs[tab_id] = TabRefs()
        self._after_action(page)
        return self._action_result(
            f"Attached {path.name} to the file input. The page decides what happens next; "
            f"look at it to see whether it was accepted.", page, page.url)

    @staticmethod
    def _file_input(inputs: list, selector: str | None):
        """The one the page means: the caller's selector, else the only one, else the last."""
        if selector:
            for element in inputs:
                if element.get_attribute("data-testid") == selector or element.get_attribute("id") == selector:
                    return element
            raise ValueError(f"No file input matching {selector!r} on this page.")
        return inputs[-1] if len(inputs) > 1 else inputs[0]

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
        self.browser.invalidate_screenshot_cache()
        self.browser.dismiss_toasts(page)
        pending = self._measure("readiness_seconds", self.browser.wait_for_ready, page)
        if pending:
            raise PageStillProcessing(pending)

    @staticmethod
    def _empty_timings() -> dict[str, float]:
        return {
            "readiness_seconds": 0.0,
            "tree_seconds": 0.0,
            "enrichment_seconds": 0.0,
            "capture_seconds": 0.0,
        }

    def _measure(self, phase: str, function, *args):
        started = time.monotonic()
        try:
            return function(*args)
        finally:
            self.last_timings[phase] += time.monotonic() - started

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

    def _collect_nodes(
        self,
        tab_id: str,
        filter_name: str | None,
        max_depth: int,
        root_ref: str | None,
        discover_tooltips: bool = False,
    ) -> list[Node]:
        page = self._page(tab_id)
        self.browser.dismiss_toasts(page)
        refs = self._refs_for(tab_id)
        tree_started = time.monotonic()
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
        self.last_timings["tree_seconds"] += time.monotonic() - tree_started
        for node in collected:
            if node.interactive and not node.name:
                node.near, node.hints = self._measure(
                    "enrichment_seconds", self._near_label, tab_id, node.backend_id,
                )
        if not root_ref:
            clickable = self._measure(
                "enrichment_seconds", self._clickable_dom_nodes,
                tab_id, {n.backend_id for n in collected}, discover_tooltips,
            )
            collected.extend(clickable)
        refs.last_read = collected
        return collected

    def _clickable_dom_nodes(self, tab_id: str, known: set[int], discover_tooltips: bool) -> list[Node]:
        """Role-less elements the page treats as clickable (React chips, tiles, cards). The
        accessibility tree skips them; they are listed as buttons, named by text or tooltip."""
        refs = self._refs_for(tab_id)
        try:
            items = self._page(tab_id).evaluate(CLICKABLE_JS)
            backend_ids = self._backend_ids_by_index(tab_id, "[data-sasha-click]", "data-sasha-click")
        except Exception:
            return []
        nodes = []
        for index, item in enumerate(items):
            backend_id = backend_ids.get(index)
            if backend_id is None or backend_id in known:
                continue
            nodes.append(Node(ref=refs.ref_for(backend_id), role="button", name=item["text"],
                              depth=0, backend_id=backend_id, hints=item["hint"],
                              disabled=item.get("disabled", False)))
        self._name_by_tooltip(tab_id, [n for n in nodes if not n.name], discover_tooltips)
        return nodes

    def _backend_ids_by_index(self, tab_id: str, selector: str, attribute: str) -> dict[int, int]:
        """DOM backend node ids of the elements matching selector, keyed by their integer attribute."""
        root = self._cdp_send(tab_id, "DOM.getDocument", {"depth": 0})["root"]["nodeId"]
        node_ids = self._cdp_send(tab_id, "DOM.querySelectorAll", {"nodeId": root, "selector": selector})["nodeIds"]
        found = {}
        for node_id in node_ids:
            node = self._cdp_send(tab_id, "DOM.describeNode", {"nodeId": node_id})["node"]
            attrs = node.get("attributes", [])
            found[int(attrs[attrs.index(attribute) + 1])] = node["backendNodeId"]
        return found

    def _name_by_tooltip(self, tab_id: str, nodes: list[Node], discover: bool) -> None:
        """Unnamed tiles (colour swatches) show their name only on hover. Hover each once per page."""
        if not nodes:
            return
        refs = self._refs_for(tab_id)
        page = self._page(tab_id)
        for node in nodes[:MAX_TOOLTIP_HOVERS]:
            if discover and node.backend_id not in refs.tooltip_names:
                refs.tooltip_names[node.backend_id] = self._tooltip_after_hover(tab_id, node.backend_id, page)
            node.name = refs.tooltip_names.get(node.backend_id, "")
        if discover:
            page.mouse.move(1, 1)

    def _tooltip_after_hover(self, tab_id: str, backend_id: int, page: Page) -> str:
        try:
            center = self._call_on_node(tab_id, backend_id, CENTER_JS)
            if center is None:
                return ""
            page.mouse.move(float(center[0]), float(center[1]))
            page.wait_for_timeout(250)
            return page.evaluate(TOOLTIP_JS) or ""
        except Exception:
            return ""

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


def _node_record(node: Node) -> dict:
    return {
        "role": node.role,
        "name": node.name,
        "value": node.value,
        "near": node.near,
        "url": node.url,
        "disabled": node.disabled,
    }


def _selection_matches(state: dict, name: str, query: str) -> bool:
    if state.get("loading"):
        return False
    labels = [str(label) for label in state.get("labels", [])]
    if any(_observed_matches(label, name) for label in labels):
        return True
    value = str(state.get("value", ""))
    if _observed_matches(value, name):
        query_was_replaced = _normalize(value) != _normalize(query)
        if state.get("closed") or query_was_replaced:
            return True
    rendered = str(state.get("rendered", ""))
    return bool(state.get("closed") and _observed_matches(rendered, name))


def _selection_signature(state: dict) -> tuple:
    return (
        tuple(sorted(_normalize(str(label)) for label in state.get("labels", []))),
        _normalize(str(state.get("value", ""))),
        _normalize(str(state.get("rendered", ""))),
        bool(state.get("closed")),
        bool(state.get("panelOpen")),
    )


def _selection_display(state: dict) -> str:
    labels = [str(label).strip() for label in state.get("labels", []) if str(label).strip()]
    if labels:
        return labels[0]
    return str(state.get("value") or state.get("rendered") or "selected option").strip()


def _observed_matches(observed: str, wanted: str) -> bool:
    observed_text = _normalize(observed)
    wanted_text = _normalize(wanted)
    if not observed_text or not wanted_text:
        return False
    if observed_text == wanted_text:
        return True
    if not _looks_like_style_code(wanted_text):
        return False
    pattern = rf"(?<![a-z0-9]){re.escape(wanted_text)}(?![a-z0-9])"
    return bool(re.search(pattern, observed_text))


def _looks_like_style_code(text: str) -> bool:
    return " " not in text and any(char.isdigit() for char in text)


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def _image_block(png: bytes) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(png).decode()},
    }


def _best_option(options: list, text: str):
    """Exact name first, then a name containing the text. Nothing else: a wrong pick is worse than an error."""
    wanted = text.strip().lower()
    options = [option for option in options if not option.disabled and
               not option.name.lower().startswith("add item")]
    for option in options:
        if option.name.strip().lower() == wanted:
            return option
    # "Add Item ..." creates a new entry; never pick it by accident.
    real = [o for o in options if not o.name.lower().startswith("add item")]
    contained = [option for option in real if _observed_matches(option.name, wanted)]
    if len(contained) == 1:
        return contained[0]
    if len(contained) > 1:
        offered = [option.name for option in contained][:12]
        raise ValueError(f"More than one existing option matches {text!r}: {offered}. Nothing was selected.")
    offered = [o.name for o in options][:12]
    raise ValueError(f"No existing option matches {text!r}. The box offers: {offered}. Nothing was selected.")


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

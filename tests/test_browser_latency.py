import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

for name, value in {
    'FP_BASE_URL': 'https://qa.example', 'FP_LOGIN_URL': 'https://qa.example/login',
    'FP_USER': 'test', 'FP_PASSWORD': 'test', 'ANTHROPIC_API_KEY': 'test',
    'MODEL': 'test-model',
}.items():
    os.environ.setdefault(name, value)

from browser import Browser, NEXT_PAINT_JS, READY_STATE_JS, VISIBLE_TEXT_JS
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from toolset_executor import INNER_INPUT_JS, Node, PageStillProcessing, TabRefs, ToolsetExecutor, _best_option


class FakeMouse:
    def __init__(self):
        self.clicks = []
        self.moves = []

    def click(self, x, y, **options):
        self.clicks.append((x, y, options))

    def move(self, x, y):
        self.moves.append((x, y))

    def wheel(self, x, y):
        pass


class FakeKeyboard:
    def __init__(self):
        self.presses = []
        self.typed = []

    def press(self, key):
        self.presses.append(key)

    def type(self, text, **options):
        self.typed.append((text, options))


class FakePage:
    def __init__(self, png=b"png"):
        self.png = png
        self.screenshot_calls = []
        self.waits = []
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.url = "https://example.test/page"

    def screenshot(self, **options):
        self.screenshot_calls.append(options)
        return self.png + str(len(self.screenshot_calls)).encode()

    def evaluate(self, script):
        if script == VISIBLE_TEXT_JS:
            return "page text"
        return None

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


class FakeBrowser:
    def __init__(self, page=None):
        self.page = page or FakePage()
        self.tabs = {"tab-1": self.page}
        self.active_tab_id = "tab-1"
        self.invalidations = 0
        self.pending = ""

    def tab_page(self, tab_id):
        return self.tabs[tab_id]

    def invalidate_screenshot_cache(self):
        self.invalidations += 1

    def dismiss_toasts(self, page):
        pass

    def wait_for_ready(self, page):
        return self.pending

    def tab_state(self):
        return [{"tab_id": "tab-1", "url": self.page.url, "active": True}]


def make_browser(*pages):
    browser = Browser.__new__(Browser)
    browser.tabs = {f"tab-{index}": page for index, page in enumerate(pages, 1)}
    browser.active_tab_id = "tab-1"
    browser._full_screenshot_cache = None
    browser.last_capture = {"seconds": 0.0, "reused": False}
    return browser


class ScreenshotReuseTests(unittest.TestCase):
    def test_full_capture_is_fresh_and_recording_reuses_it_once(self):
        page = FakePage()
        browser = make_browser(page)

        first = browser.screenshot(page)
        second = browser.screenshot(page)

        self.assertNotEqual(first, second)
        self.assertEqual(len(page.screenshot_calls), 2)
        with tempfile.TemporaryDirectory() as folder:
            first_path = Path(folder) / "first.png"
            browser.save_screenshot(first_path)
            self.assertEqual(first_path.read_bytes(), second)
            self.assertTrue(browser.last_capture["reused"])

            browser.save_screenshot(Path(folder) / "second.png")
            self.assertEqual(len(page.screenshot_calls), 3)
            self.assertFalse(browser.last_capture["reused"])

    def test_zoom_and_mutation_invalidate_full_capture(self):
        page = FakePage()
        browser = make_browser(page)
        browser.screenshot(page)

        browser.screenshot(page, {"x": 1, "y": 1, "width": 2, "height": 2})
        self.assertIsNone(browser._full_screenshot_cache)

        browser.screenshot(page)
        browser.invalidate_screenshot_cache()
        self.assertIsNone(browser._full_screenshot_cache)

    def test_cache_is_bound_to_the_active_tab(self):
        first_page = FakePage(b"first")
        second_page = FakePage(b"second")
        browser = make_browser(first_page, second_page)
        browser.screenshot(first_page)
        browser.active_tab_id = "tab-2"

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tab.png"
            browser.save_screenshot(path)

        self.assertEqual(len(second_page.screenshot_calls), 1)
        self.assertFalse(browser.last_capture["reused"])


class ReadinessTests(unittest.TestCase):
    def test_ready_page_has_no_wait(self):
        page = Mock()
        page.evaluate.return_value = {"ready": True, "documentState": "complete", "busy": ""}
        browser = Browser.__new__(Browser)

        self.assertEqual(browser.wait_for_ready(page), "")
        page.wait_for_function.assert_not_called()

    def test_busy_timeout_is_reported(self):
        page = Mock()
        page.evaluate.return_value = {"ready": False, "documentState": "complete", "busy": "Saving"}
        page.wait_for_function.side_effect = PlaywrightTimeoutError("timed out")
        browser = Browser.__new__(Browser)

        message = browser.wait_for_ready(page, timeout_ms=25)

        self.assertIn("still processing", message)
        self.assertIn("Saving", message)
        page.wait_for_function.assert_called_once()


class ExecutorHotPathTests(unittest.TestCase):
    def test_coordinate_click_does_not_collect_a_tree(self):
        browser = FakeBrowser()
        browser.page.evaluate = Mock(return_value=None)
        executor = ToolsetExecutor(browser)
        executor._collect_nodes = Mock(side_effect=AssertionError("tree scan"))

        result = executor.run("left_click", {"target": {"type": "coordinate", "x": 4, "y": 5}})

        self.assertEqual(result, "Clicked (4, 5).")
        executor._collect_nodes.assert_not_called()
        self.assertEqual(browser.invalidations, 2)

    def test_form_input_does_not_collect_a_tree(self):
        browser = FakeBrowser()
        executor = ToolsetExecutor(browser)
        executor._call_on_ref = Mock(return_value="text")
        executor._set_text = Mock(return_value="filled")
        executor._collect_nodes = Mock(side_effect=AssertionError("tree scan"))

        result = executor.run("form_input", {"target": {"ref": "ref_1"}, "value": "hello"})

        self.assertEqual(result, "filled")
        executor._collect_nodes.assert_not_called()

    def test_pending_readiness_stops_dependent_work(self):
        browser = FakeBrowser()
        browser.pending = "still saving"
        executor = ToolsetExecutor(browser)

        with self.assertRaisesRegex(PageStillProcessing, "still saving"):
            executor._after_action(browser.page)

    def test_every_tool_invalidates_recording_reuse(self):
        browser = FakeBrowser()
        executor = ToolsetExecutor(browser)

        executor.run("list_tabs", {})

        self.assertEqual(browser.invalidations, 1)
        self.assertEqual(executor.last_timings, {
            "readiness_seconds": 0.0,
            "tree_seconds": 0.0,
            "enrichment_seconds": 0.0,
            "capture_seconds": 0.0,
        })


class DropdownTests(unittest.TestCase):
    def poll_search(self, snapshots, query='437'):
        browser = FakeBrowser()
        executor = ToolsetExecutor(browser)
        samples = iter(snapshots)
        latest = snapshots[-1]
        executor._scoped_options = Mock(side_effect=lambda *args: next(samples, latest))
        clock = {'now': 0.0}
        browser.page.wait_for_timeout = lambda ms: clock.update(now=clock['now'] + ms / 1000)
        with patch('toolset_executor.time.monotonic', side_effect=lambda: clock['now']):
            result = executor._wait_for_options('tab-1', 0.5, query)
        return result, executor

    def verify_selection(self, snapshots, name, query, deadline=0.3):
        browser = FakeBrowser()
        executor = ToolsetExecutor(browser)
        samples = iter(snapshots)
        latest = snapshots[-1]
        executor._call_on_ref = Mock(side_effect=lambda *args: next(samples, latest))
        clock = {"now": 0.0}
        browser.page.wait_for_timeout = lambda ms: clock.update(now=clock["now"] + ms / 1000)
        with patch("toolset_executor.time.monotonic", side_effect=lambda: clock["now"]):
            return executor._verify_selection("tab-1", "ref_1", name, query, deadline)

    def test_old_options_wait_for_filtered_result(self):
        old = Node('ref_1', 'option', 'A4 N3165', 0, 1)
        new = Node('ref_2', 'option', 'Jerzees 437', 0, 2)
        result, executor = self.poll_search([
            ([old], 'empty'), ([old], 'loading'), ([new], 'empty'), ([new], 'empty'),
        ])
        self.assertEqual(result, ([new], 'ready'))
        self.assertEqual(executor._scoped_options.call_count, 4)

    def test_unchanged_unrelated_options_are_uncertain(self):
        old = Node('ref_1', 'option', 'A4 N3165', 0, 1)
        result, _ = self.poll_search([([old], 'empty')])
        self.assertEqual(result, ([], 'uncertain'))

    def test_unchanged_matching_options_can_be_selected(self):
        match = Node('ref_1', 'option', 'Jerzees 437', 0, 1)
        result, _ = self.poll_search([([match], 'empty')])
        self.assertEqual(result, ([match], 'ready'))

    def test_disabled_match_is_not_selected(self):
        match = Node('ref_1', 'option', 'Jerzees 437', 0, 1, disabled=True)
        result, _ = self.poll_search([([match], 'empty')])
        self.assertEqual(result, ([], 'uncertain'))

    def test_explicit_no_results_after_loading_is_no_match(self):
        result, _ = self.poll_search([([], 'loading'), ([], 'no_match')])
        self.assertEqual(result, ([], 'no_match'))

    def test_cached_no_results_without_new_loading_is_uncertain(self):
        result, _ = self.poll_search([([], 'no_match')])
        self.assertEqual(result, ([], 'uncertain'))

    def test_typing_query_is_not_selection_verification(self):
        with self.assertRaises(PageStillProcessing):
            self.verify_selection([{
                "labels": [], "value": "Jerzees 437", "rendered": "Jerzees 437",
                "closed": False, "panelOpen": True, "loading": False,
            }], "Jerzees 437", "Jerzees 437")

    def test_selected_label_verifies_selection(self):
        state = {
            "labels": ["Jerzees 437"], "value": "", "rendered": "Jerzees 437",
            "closed": True, "panelOpen": False, "loading": False,
        }
        observed = self.verify_selection([state, state], "Jerzees 437", "437")
        self.assertEqual(observed, state)

    def test_value_only_style_verifies_when_query_was_replaced(self):
        state = {
            "labels": [], "value": "Nike NKDC1963", "rendered": "",
            "closed": False, "panelOpen": True, "loading": False,
        }
        observed = self.verify_selection([state, state], "Nike NKDC1963", "NKDC1963")
        self.assertEqual(observed["value"], "Nike NKDC1963")

    def test_rendered_color_verifies_with_empty_textbox_value(self):
        state = {
            "labels": [], "value": "", "rendered": "White",
            "closed": True, "panelOpen": False, "loading": False,
        }
        observed = self.verify_selection([state, state], "White", "White")
        self.assertEqual(observed["rendered"], "White")

    def test_style_code_is_not_a_substring_option_match(self):
        option = Node("ref_1", "option", "Gildan G448L Polo", 0, 1)
        with self.assertRaisesRegex(ValueError, "No existing option matches"):
            _best_option([option], "G448")

    def test_selection_gets_a_fresh_deadline_after_option_search(self):
        browser = FakeBrowser()
        executor = ToolsetExecutor(browser)
        option = Node("ref_2", "option", "Nike NKDC1963", 0, 2)
        empty = {
            "labels": [], "value": "", "rendered": "",
            "closed": True, "panelOpen": False, "loading": False,
        }
        selected = {
            "labels": [], "value": "Nike NKDC1963", "rendered": "",
            "closed": False, "panelOpen": True, "loading": False,
        }
        states = iter([empty, selected, selected])
        executor._call_on_ref = Mock(side_effect=lambda tab, ref, script, *args:
                                     [5, 5] if script == INNER_INPUT_JS else next(states))
        clock = {"now": 0.0}

        def finish_search(*args):
            clock["now"] = 3.0
            return [option], "ready"

        browser.page.wait_for_timeout = lambda ms: clock.update(now=clock["now"] + ms / 1000)
        executor._wait_for_options = Mock(side_effect=finish_search)
        executor._scoped_options = Mock(return_value=([option], "empty"))
        executor._point = Mock(return_value=(5, 5))
        with patch("toolset_executor.time.monotonic", side_effect=lambda: clock["now"]):
            result = executor._set_autocomplete("tab-1", "ref_1", "NKDC1963", browser.page)

        self.assertIn("Selected and verified 'Nike NKDC1963'", result)

    def test_polling_uses_one_deadline_without_tree_or_hover_work(self):
        browser = FakeBrowser()
        executor = ToolsetExecutor(browser)
        executor._scoped_options = Mock(return_value=([], "loading"))
        executor._collect_nodes = Mock(side_effect=AssertionError("full tree"))
        clock = {"now": 0.0}

        def now():
            return clock["now"]

        def advance(milliseconds):
            browser.page.waits.append(milliseconds)
            clock["now"] += milliseconds / 1000

        browser.page.wait_for_timeout = advance
        with patch("toolset_executor.time.monotonic", side_effect=now):
            options, state = executor._wait_for_options("tab-1", deadline=0.25)

        self.assertEqual(options, [])
        self.assertEqual(state, "loading")
        self.assertAlmostEqual(sum(browser.page.waits), 250)
        self.assertLessEqual(executor._scoped_options.call_count, 4)
        executor._collect_nodes.assert_not_called()

    def test_loading_state_does_not_return_stale_options(self):
        browser = FakeBrowser()
        executor = ToolsetExecutor(browser)
        stale = Node("ref_1", "option", "Old", 0, 1)
        executor._scoped_options = Mock(side_effect=[([stale], "loading"), ([stale], "empty")])
        clock = {"now": 0.0}

        def now():
            return clock["now"]

        def advance(milliseconds):
            clock["now"] += milliseconds / 1000

        browser.page.wait_for_timeout = advance
        with patch("toolset_executor.time.monotonic", side_effect=now):
            options, state = executor._wait_for_options("tab-1", deadline=0.2)

        self.assertEqual(options, [stale])
        self.assertEqual(state, "ready")
        self.assertEqual(executor._scoped_options.call_count, 2)

    def test_scoped_options_preserve_dom_indices_after_empty_text_is_filtered(self):
        browser = FakeBrowser()
        browser.page.evaluate = Mock(return_value={
            "loading": False,
            "items": [{"index": 1, "text": "Second", "hint": "ng-option"}],
        })
        executor = ToolsetExecutor(browser)
        executor._backend_ids_by_index = Mock(return_value={0: 10, 1: 11})

        options, state = executor._scoped_options("tab-1")

        self.assertEqual(state, "empty")
        self.assertEqual(options[0].backend_id, 11)
        self.assertEqual(options[0].name, "Second")


class ProcessingIndicatorTests(unittest.TestCase):
    def test_static_loading_icon_is_not_a_generic_busy_selector(self):
        self.assertNotIn("'[class*=loading]'", READY_STATE_JS)
        self.assertIn(".loading.active", READY_STATE_JS)
        self.assertIn("[class*=loading][aria-busy=true]", READY_STATE_JS)

    def test_readiness_checks_known_transient_labels_after_next_paint(self):
        self.assertIn("saving", READY_STATE_JS.lower())
        self.assertIn("uploading in progress", READY_STATE_JS.lower())
        self.assertNotIn("remove background", READY_STATE_JS.lower())

        page = Mock()
        page.evaluate.side_effect = [None, {"ready": True, "documentState": "complete", "busy": ""}]
        browser = Browser.__new__(Browser)
        browser.wait_for_ready(page)

        self.assertEqual(page.evaluate.call_args_list[0].args[0], NEXT_PAINT_JS)

    def test_non_timeout_readiness_errors_propagate(self):
        page = Mock()
        page.evaluate.side_effect = [None, {"ready": False, "documentState": "complete", "busy": "Saving"}]
        page.wait_for_function.side_effect = RuntimeError("page closed")
        browser = Browser.__new__(Browser)

        with self.assertRaisesRegex(RuntimeError, "page closed"):
            browser.wait_for_ready(page)


class AvailabilityTests(unittest.TestCase):
    def test_card_state_survives_dom_enrichment(self):
        browser = FakeBrowser()
        browser.page.evaluate = Mock(return_value=[
            {'text': '[disabled, not selected] Fresh Prints Flash $1235.78',
             'hint': 'toggle-wrapper', 'disabled': True},
            {'text': '[enabled, selected] Expedited $463.54',
             'hint': 'toggle-wrapper', 'disabled': False},
        ])
        executor = ToolsetExecutor(browser)
        executor._backend_ids_by_index = Mock(return_value={0: 10, 1: 11})
        nodes = executor._clickable_dom_nodes('tab-1', set(), False)
        self.assertTrue(nodes[0].disabled)
        self.assertIn('[disabled, not selected] Fresh Prints Flash', nodes[0].line())
        self.assertFalse(nodes[1].disabled)
        self.assertIn('[enabled, selected] Expedited', nodes[1].line())

    def test_native_disabled_state_is_preserved(self):
        from toolset_executor import _to_node
        node = _to_node({
            'backendDOMNodeId': 42, 'role': {'value': 'checkbox'},
            'name': {'value': 'Flash'},
            'properties': [{'name': 'disabled', 'value': {'value': True}}],
        }, 0, TabRefs())
        self.assertTrue(node.disabled)
        self.assertIn('disabled', node.line())


class TooltipTests(unittest.TestCase):
    def test_cached_names_are_kept_without_hovering_and_discovery_is_opt_in(self):
        browser = FakeBrowser()
        executor = ToolsetExecutor(browser)
        executor._refs["tab-1"] = TabRefs()
        executor._refs["tab-1"].tooltip_names[1] = "Red"
        executor._tooltip_after_hover = Mock(return_value="Blue")
        cached = Node("ref_1", "button", "", 0, 1)
        unknown = Node("ref_2", "button", "", 0, 2)

        executor._name_by_tooltip("tab-1", [cached, unknown], discover=False)

        self.assertEqual(cached.name, "Red")
        self.assertEqual(unknown.name, "")
        executor._tooltip_after_hover.assert_not_called()

        executor._name_by_tooltip("tab-1", [cached, unknown], discover=True)

        self.assertEqual(unknown.name, "Blue")
        executor._tooltip_after_hover.assert_called_once_with("tab-1", 2, browser.page)


if __name__ == "__main__":
    unittest.main()

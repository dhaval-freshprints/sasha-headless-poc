"""The same browser-tool policy is used for the model and local execution."""

import config

DISABLED_MEMBERS = (
    "left_mouse_down", "left_mouse_up", "hold_key", "left_click_drag", "middle_click",
    "wait", "file_upload", "read_console", "read_network", "javascript_exec",
)
OBSERVATION_TOOLS = frozenset({
    "screenshot", "zoom", "read_page", "find", "get_page_text", "list_tabs",
    "inspect_catalog_product",
})
VERIFICATION_TOOLS = OBSERVATION_TOOLS | {"navigate", "switch_tab"}
ACTION_TOOLS = frozenset({
    "navigate", "left_click", "right_click", "double_click", "triple_click", "hover",
    "mouse_move", "scroll", "scroll_to", "type", "key", "form_input", "attach_file",
    "new_tab", "switch_tab", "close_tab",
})


def validate_tool_call(name: str, args: dict) -> None:
    if name in DISABLED_MEMBERS:
        raise ValueError(f"{name} is disabled and was not executed.")
    if name not in OBSERVATION_TOOLS | ACTION_TOOLS:
        raise ValueError(f"Unknown browser tool: {name}")
    if name == "key":
        validate_key(args)


def validate_key(args: dict) -> None:
    repeat = args.get("repeat", 1)
    if type(repeat) is not int or not 1 <= repeat <= config.MAX_KEY_REPEAT:
        raise ValueError(f"key repeat must be an integer from 1 to {config.MAX_KEY_REPEAT}.")
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("key text must contain a key or chord.")
    if len(text.split()) * repeat > config.MAX_KEY_REPEAT:
        raise ValueError(f"A key call may send at most {config.MAX_KEY_REPEAT} chords. Observe between batches.")


def arrow_press_count(name: str, args: dict) -> int:
    if name != "key":
        return 0
    arrows = {"left", "right", "up", "down", "arrowleft", "arrowright", "arrowup", "arrowdown"}
    chords = str(args.get("text", "")).lower().split()
    count = sum(chord.split("+")[-1] in arrows for chord in chords)
    return count * args.get("repeat", 1)

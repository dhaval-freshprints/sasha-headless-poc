"""Sandbox client for host-brokered artwork extraction.

Usage: python /opt/sasha/extract_artwork.py /workspace/client-files/file_1.png --keep "..." --shape wide
Prints JSON with the new file's path. The original file is never changed.

OpenAI credentials stay on the host. This process only submits a request under
/workspace/.artwork-extract and waits for the host broker response.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    from account_manager_agent.artwork.client import (
        ArtworkClientError,
        normalize_action_id,
        request_broker,
        validate_shape,
    )
    from account_manager_agent.artwork.constants import SHAPES
except ImportError:
    from artwork.client import (  # type: ignore
        ArtworkClientError,
        normalize_action_id,
        request_broker,
        validate_shape,
    )
    from artwork.constants import SHAPES  # type: ignore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recreate selected graphic elements from a reference image as a transparent PNG."
    )
    parser.add_argument("file", help="A supplied file under /workspace/client-files")
    parser.add_argument(
        "--keep",
        default="",
        help="Describe one graphic to isolate; explicitly exclude text and other elements. Leave out real photos.",
    )
    parser.add_argument(
        "--shape",
        choices=("auto", *SHAPES),
        default="auto",
        help="The design's overall shape, so lines are not rewrapped to fit.",
    )
    parser.add_argument(
        "--action-id",
        default="",
        help="Reuse the same id to make retries idempotent.",
    )
    args = parser.parse_args()
    try:
        action_id = normalize_action_id(args.action_id)
        shape = validate_shape(args.shape)
        result = request_broker(
            {
                "action_id": action_id,
                "file": args.file,
                "keep": args.keep,
                "shape": shape,
            }
        )
    except ArtworkClientError as error:
        print(json.dumps({"ok": False, "error": error.code, "message": str(error)}))
        sys.exit(1)
    print(json.dumps(result, default=str))
    if not result.get("ok", True):
        sys.exit(1)


if __name__ == "__main__":
    main()

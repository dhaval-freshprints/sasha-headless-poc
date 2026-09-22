#!/usr/bin/env python3
"""Create the Chromium profile used by the managed outreach sandbox."""

import os
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def main() -> int:
    load_dotenv(ROOT / ".env")
    image = os.environ.get(
        "OPENAI_MANAGED_SANDBOX_IMAGE", "sasha-openai-managed:local"
    ).strip()
    profile = Path(
        os.environ.get("OPENAI_MANAGED_AUTH_DIRECTORY", ROOT / "auth-openai-managed")
    ).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)

    environment = dict(os.environ)
    for name in ("FP_LOGIN_URL", "FP_USER", "FP_PASSWORD"):
        environment[name] = required(name)

    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--shm-size",
            "1g",
            "--env",
            "FP_LOGIN_URL",
            "--env",
            "FP_USER",
            "--env",
            "FP_PASSWORD",
            "--mount",
            f"type=bind,src={profile},dst=/workspace/browser-profile",
            "--mount",
            f"type=bind,src={ROOT / 'openai_managed' / 'setup_auth.js'},dst=/opt/sasha/setup_auth.js,readonly",
            image,
            "node",
            "/opt/sasha/setup_auth.js",
        ],
        check=True,
        env=environment,
    )
    print(f"Managed browser profile saved to {profile}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

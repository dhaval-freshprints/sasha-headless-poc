"""
One-time login. Saves the session into ./auth so every later run starts logged in.

Run:  python auth_setup.py
Runs with a visible browser so you can see what happened if the login form differs.
"""

from playwright.sync_api import sync_playwright

import config


def main() -> None:
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(config.AUTH_DIR),
            headless=False,
            viewport={"width": 1440, "height": 900},
        )
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(config.FP_LOGIN_URL, wait_until="domcontentloaded")

        print("Login page loaded. Filling credentials...")
        page.get_by_role("textbox", name="Email").first.fill(config.FP_USER)
        page.get_by_role("textbox", name="Password").first.fill(config.FP_PASSWORD)
        page.get_by_role("button", name="Sign in").first.click()

        page.wait_for_url("**/dashboard/**", timeout=30000)
        print(f"Logged in. Now at: {page.url}")
        print(f"Session saved to: {config.AUTH_DIR}")

        context.close()


if __name__ == "__main__":
    main()

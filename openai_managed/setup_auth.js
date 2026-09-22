const { chromium } = require("playwright");

function required(name) {
  const value = (process.env[name] || "").trim();
  if (!value) throw new Error(`${name} must be set`);
  return value;
}

async function loginFormIsVisible(page) {
  const email = page.getByRole("textbox", { name: "Email" }).first();
  await email.waitFor({ state: "visible", timeout: 5000 }).catch(() => {});
  const currentUrl = new URL(page.url());
  if (currentUrl.pathname.includes("/dashboard/login")) return true;
  return email.isVisible().catch(() => false);
}

async function main() {
  const dealUrl = (process.env.FP_DEAL_URL || "").trim();
  const context = await chromium.launchPersistentContext(
    "/workspace/browser-profile",
    {
      headless: true,
      viewport: { width: 1440, height: 900 },
      args: ["--no-sandbox"],
    },
  );

  try {
    const page = context.pages()[0] || (await context.newPage());
    await page.goto(dealUrl || required("FP_LOGIN_URL"), {
      waitUntil: "domcontentloaded",
    });

    const email = page.getByRole("textbox", { name: "Email" }).first();
    const password = page.getByRole("textbox", { name: "Password" }).first();
    const loginRequired = await loginFormIsVisible(page);

    if (!loginRequired) {
      console.log("AUTH_SESSION_VALID");
      return;
    }

    await email.fill(required("FP_USER"));
    await password.fill(required("FP_PASSWORD"));
    await page.getByRole("button", { name: "Sign in" }).first().click();
    await page.waitForURL(
      (url) => !url.pathname.includes("/dashboard/login"),
      { timeout: 30000 },
    );

    if (dealUrl) {
      await page.goto(dealUrl, { waitUntil: "domcontentloaded" });
      if (await loginFormIsVisible(page)) {
        throw new Error("QA login succeeded but the deal still redirects to login");
      }
    }

    const authenticatedUrl = new URL(page.url());
    console.log(
      `Managed browser authentication completed at ${authenticatedUrl.origin}${authenticatedUrl.pathname}`,
    );
    console.log("AUTH_RELOGIN_SUCCEEDED");
  } finally {
    await context.close();
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});

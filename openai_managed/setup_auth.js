const { chromium } = require("playwright");

function required(name) {
  const value = (process.env[name] || "").trim();
  if (!value) throw new Error(`${name} must be set`);
  return value;
}

async function main() {
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
    await page.goto(required("FP_LOGIN_URL"), { waitUntil: "domcontentloaded" });
    await page.getByRole("textbox", { name: "Email" }).first().fill(required("FP_USER"));
    await page.getByRole("textbox", { name: "Password" }).first().fill(required("FP_PASSWORD"));
    await page.getByRole("button", { name: "Sign in" }).first().click();
    await page.waitForURL("**/dashboard/**", { timeout: 30000 });
    const authenticatedUrl = new URL(page.url());
    console.log(
      `Managed browser authentication completed at ${authenticatedUrl.origin}${authenticatedUrl.pathname}`,
    );
  } finally {
    await context.close();
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});

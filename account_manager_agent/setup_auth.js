const fs = require("fs");
const { chromium } = require("playwright");

const ARTIFACT_PATH = "/workspace/artifacts/authentication.json";

function required(input, name) {
  const value = String(input[name] || "").trim();
  if (!value) throw new Error(`${name} must be set`);
  return value;
}

async function readInput() {
  let value = "";
  for await (const chunk of process.stdin) value += chunk;
  return JSON.parse(value);
}

function sameDeal(expected, actual) {
  const expectedUrl = new URL(expected);
  const actualUrl = new URL(actual);
  return (
    expectedUrl.origin === actualUrl.origin &&
    expectedUrl.pathname === actualUrl.pathname &&
    expectedUrl.searchParams.get("id") === actualUrl.searchParams.get("id")
  );
}

function saveResult(result) {
  fs.writeFileSync(ARTIFACT_PATH, `${JSON.stringify(result, null, 2)}\n`);
}

async function main() {
  const input = await readInput();
  const dealUrl = required(input, "deal_url");
  const loginUrl = required(input, "login_url");
  const loginUser = required(input, "login_user");
  const loginPassword = required(input, "login_password");
  const context = await chromium.launchPersistentContext("/browser-profile", {
    headless: true,
    viewport: { width: 1440, height: 900 },
    args: ["--no-sandbox"],
  });

  try {
    const page = context.pages()[0] || (await context.newPage());
    await page.goto(loginUrl, { waitUntil: "domcontentloaded" });

    const email = page.getByRole("textbox", { name: "Email" }).first();
    const password = page.getByRole("textbox", { name: "Password" }).first();
    await email.waitFor({ state: "visible", timeout: 15000 });
    await email.fill(loginUser);
    await password.fill(loginPassword);
    await page.getByRole("button", { name: "Sign in" }).first().click();
    await page.waitForURL(
      (url) => !url.pathname.includes("/dashboard/login"),
      { timeout: 30000, waitUntil: "domcontentloaded" },
    );
    await page
      .getByText("Sales Pipeline", { exact: true })
      .filter({ visible: true })
      .first()
      .waitFor({ state: "visible", timeout: 30000 });

    await page.goto(dealUrl, { waitUntil: "domcontentloaded" });
    await page
      .getByText("Deal Manager", { exact: true })
      .first()
      .waitFor({ state: "visible", timeout: 30000 });

    if (!sameDeal(dealUrl, page.url())) {
      throw new Error("The authenticated browser did not reach the requested deal");
    }
    if (await email.isVisible().catch(() => false)) {
      throw new Error("The requested deal redirected back to the login page");
    }

    const result = { status: "authenticated", deal_url: page.url() };
    saveResult(result);
    process.stdout.write(JSON.stringify(result));
  } catch (error) {
    const page = context.pages()[0];
    if (page) {
      await page
        .screenshot({
          path: "/workspace/artifacts/authentication-failure.png",
          fullPage: true,
        })
        .catch(() => {});
    }
    const result = {
      status: "failed",
      message: error.message,
      url: page ? page.url() : "",
    };
    saveResult(result);
    process.stderr.write(JSON.stringify(result));
    process.exitCode = 1;
  } finally {
    await context.close();
  }
}

main().catch((error) => {
  const result = { status: "failed", message: error.message, url: "" };
  saveResult(result);
  process.stderr.write(JSON.stringify(result));
  process.exitCode = 1;
});

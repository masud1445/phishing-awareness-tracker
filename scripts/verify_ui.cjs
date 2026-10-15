/* Optional browser review: npm-install puppeteer-core and axe-core outside the app. */
const puppeteer = require("puppeteer-core");
const path = require("path");
const fs = require("fs");
(async () => {
  const base = process.env.UI_BASE_URL || "http://127.0.0.1:5055";
  const output = process.env.UI_SCREENSHOTS || "/tmp/product-ui-review";
  fs.mkdirSync(output, { recursive: true });
  const browser = await puppeteer.launch({
    executablePath: process.env.CHROME_PATH || "/usr/bin/chromium",
    args: ["--no-sandbox", "--disable-dev-shm-usage"],
    env: {
      ...process.env,
      XDG_CACHE_HOME: "/tmp/ui-chrome-cache",
      XDG_CONFIG_HOME: "/tmp/ui-chrome-config",
    },
  });
  const page = await browser.newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const paths = [
    "/",
    "/campaigns",
    "/recipients",
    "/analytics",
    "/settings",
    "/campaigns/new",
    "/templates",
    "/templates/new",
    "/senders",
    "/senders/new",
    "/not-a-page",
  ];
  if (process.env.UI_CAMPAIGN_ID) {
    const id = process.env.UI_CAMPAIGN_ID;
    paths.push(
      `/campaigns/${id}/recipients`,
      `/campaigns/${id}/recipients/new`,
      `/campaigns/${id}/studio`,
      `/campaigns/${id}/email-preview`,
      `/campaigns/${id}/results`,
      "/templates/1/edit",
    );
  }
  if (process.env.UI_TRACKING_TOKEN)
    paths.push("/t/" + encodeURIComponent(process.env.UI_TRACKING_TOKEN));
  const accessibility = [];
  for (const width of [1920, 1440, 1280, 768, 390]) {
    await page.setViewport({ width, height: 1100 });
    for (const route of paths) {
      const response = await page.goto(base + route, {
        waitUntil: "networkidle0",
      });
      if (response.status() !== (route === "/not-a-page" ? 404 : 200))
        throw Error(route + " HTTP " + response.status());
      if (
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        )
      )
        throw Error("Page overflow " + width + " " + route);
      if (
        width > 800 &&
        !route.startsWith("/t/") &&
        (await page.$(".nav-toggle"))
      ) {
        if (
          (await page.$eval(
            ".nav-toggle",
            (el) => getComputedStyle(el).display,
          )) !== "none"
        )
          throw Error("Desktop nav toggle visible");
      }
      if (width === 1440) {
        await page.addScriptTag({
          path: require.resolve("axe-core/axe.min.js"),
        });
        const violations = await page.evaluate(async () => {
          const result = await axe.run(document, {
            runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa"] },
          });
          return result.violations.map((v) => ({
            id: v.id,
            impact: v.impact,
            nodes: v.nodes.map((n) => n.target),
          }));
        });
        if (violations.length) accessibility.push({ route, violations });
      }
      await page.screenshot({
        path: path.join(
          output,
          width + "-" + (route.replaceAll("/", "_") || "dashboard") + ".png",
        ),
        fullPage: true,
      });
      console.log(width, route, "layout OK");
    }
  }
  await page.setViewport({ width: 390, height: 844 });
  await page.goto(base + "/");
  await page.click(".nav-toggle");
  if (
    (await page.$eval(".nav-toggle", (e) =>
      e.getAttribute("aria-expanded"),
    )) !== "true"
  )
    throw Error("Navigation did not open");
  await page.keyboard.press("Escape");
  if (
    (await page.$eval(".nav-toggle", (e) =>
      e.getAttribute("aria-expanded"),
    )) !== "false"
  )
    throw Error("Navigation did not close");
  await page.goto(base + "/templates/new");
  await page.focus("#subject");
  await page.click('[data-token="{{recipient_name}}"]');
  if (
    !(await page.$eval("#subject", (e) =>
      e.value.includes("{{recipient_name}}"),
    ))
  )
    throw Error("Token insertion failed");
  await page.click('[data-format="heading"]');
  if (
    !(await page.$eval("#body", (e) => e.value.includes("## Section heading")))
  )
    throw Error("Formatting insertion failed");
  if ((await page.$eval("#content_style", (el) => el.value)) !== "sectioned")
    throw Error("Heading did not select sectioned layout");
  await page.goto(base + "/campaigns");
  if (await page.$("[data-search]")) {
    await page.type("[data-search]", "definitely-not-a-campaign");
    if (!(await page.$eval(".no-filter-results", (e) => !e.hidden)))
      throw Error("Empty search state failed");
  }
  await page.emulateMediaFeatures([
    { name: "prefers-reduced-motion", value: "reduce" },
  ]);
  await page.goto(base + "/");
  if (
    (await page.$eval(
      ".app-content",
      (e) => getComputedStyle(e).animationName,
    )) !== "none"
  )
    throw Error("Reduced motion failed");
  fs.writeFileSync(
    path.join(output, "accessibility.json"),
    JSON.stringify(accessibility, null, 2),
  );
  await browser.close();
  if (errors.length) throw Error("Browser errors: " + JSON.stringify(errors));
  if (accessibility.length)
    throw Error("Accessibility findings: " + JSON.stringify(accessibility));
  console.log(
    "PASS: responsive layout, keyboard navigation, authoring controls, filters, reduced motion, accessibility; screenshots in " +
      output,
  );
})().catch((error) => {
  console.error(error);
  process.exit(1);
});

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
  const anonymous = await browser.newPage();
  const denied = await anonymous.goto(base + '/template-assets/1');
  if (denied.status() !== 401) throw Error('Anonymous asset access was not denied');
  if (process.env.UI_TRACKING_TOKEN) {
    const publicResponse = await anonymous.goto(base + '/t/' + encodeURIComponent(process.env.UI_TRACKING_TOKEN));
    if (publicResponse.status() !== 200) throw Error('Public awareness landing requires login');
  }
  await anonymous.close();
  await page.goto(base + '/operator/login');
  for (const width of [1440, 390]) {
    await page.setViewport({width, height: 1100});
    if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth))
      throw Error('Login overflow');
    await page.screenshot({path: path.join(output, `operator-login-${width}.png`), fullPage: true});
  }
  if (!process.env.UI_OPERATOR_USERNAME || !process.env.UI_OPERATOR_PASSWORD)
    throw Error('Set UI_OPERATOR_USERNAME and UI_OPERATOR_PASSWORD for browser validation.');
  await page.type('[name="username"]', process.env.UI_OPERATOR_USERNAME);
  await page.type('[name="password"]', process.env.UI_OPERATOR_PASSWORD);
  await Promise.all([page.waitForNavigation(), page.click('button[type="submit"]')]);
  if (new URL(page.url()).pathname !== '/') throw Error('Operator login failed');
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
  await page.waitForFunction(() => document.getAnimations().every(animation => animation.playState !== "running"));
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
  if (process.env.UI_CAMPAIGN_ID) {
    await page.goto(base + `/campaigns/${process.env.UI_CAMPAIGN_ID}/email-preview`, {waitUntil: 'networkidle0'});
    const inlineImage = await page.$eval('iframe', frame => {
      const image = frame.contentDocument.querySelector('img');
      return image && image.complete && image.naturalWidth > 0;
    });
    if (!inlineImage) throw Error('Authenticated preview image did not load');
  }
  if (process.env.UI_DELETE_FIXTURE_SENDER_ID) {
    const identity = process.env.UI_DELETE_FIXTURE_SENDER_ID;
    const review = await page.goto(base + '/senders');
    if (review.headers()['x-review-data'] !== 'isolated-automated-fixture')
      throw Error('Destructive UI checks require the isolated browser fixture');
    await page.waitForFunction(() => document.getAnimations().every(animation => animation.playState !== 'running'));
    const selector = `[data-delete-sender][href="/senders/${identity}/delete"]`;
    for (const width of [1440, 390]) {
      await page.setViewport({width, height: 1000});
      await page.click(selector);
      const details = await page.$eval(selector, link => ({name: link.dataset.senderName, email: link.dataset.senderEmail}));
      const valid = await page.$eval('#sender-delete-dialog', (dialog, details) =>
        dialog.open && dialog.querySelector('[data-delete-name]').textContent === details.name &&
        dialog.querySelector('[data-delete-email]').textContent === details.email &&
        dialog.textContent.includes('permanently removed') &&
        dialog.textContent.includes('Historical campaign records') &&
        dialog.contains(document.activeElement) && dialog.scrollWidth <= dialog.clientWidth, details);
      if (!valid) throw Error('Sender deletion dialog details, focus or layout failed');
      await page.addScriptTag({path: require.resolve('axe-core/axe.min.js')});
      const violations = await page.evaluate(async () => (await axe.run(document, {
        runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21aa']}
      })).violations.map(v => v.id));
      if (violations.length) throw Error('Deletion dialog accessibility: ' + violations.join(','));
      await page.screenshot({path: path.join(output, `sender-delete-dialog-${width}.png`), fullPage: true});
      await page.click('#sender-delete-dialog button[type="submit"]');
      if (!(await page.$eval('#sender-delete-dialog', d => d.open && d.querySelector('input[type="password"]').validity.valueMissing)))
        throw Error('Empty password accepted by dialog');
      await page.type('#operator-delete-password', 'cancel-this-test-value');
      await page.click('[data-delete-cancel]');
      if (!(await page.$eval('#sender-delete-dialog', d => !d.open && !d.querySelector('input[type="password"]').value)))
        throw Error('Cancel did not clear password');
      await page.click(selector);
      await page.keyboard.press('Escape');
      if (!(await page.$eval('#sender-delete-dialog', d => !d.open))) throw Error('Escape did not close dialog');
    }
    await page.click(selector);
    await page.type('#operator-delete-password', 'wrong-fixture-password');
    await Promise.all([page.waitForNavigation(), page.click('#sender-delete-dialog button[type="submit"]')]);
    if (!(await page.$eval('[role="alert"]', e => e.textContent.includes('Nothing was deleted'))))
      throw Error('Wrong password validation missing');
    if (await page.$eval('#operator-delete-password', e => !!e.value)) throw Error('Password echoed after failure');
    await page.waitForFunction(() => document.getAnimations().every(animation => animation.playState !== 'running'));
    await page.type('#operator-delete-password', process.env.UI_OPERATOR_PASSWORD);
    await Promise.all([page.waitForNavigation(), page.click('[data-delete-form] button[type="submit"]')]);
    if (new URL(page.url()).pathname !== '/senders' || await page.$(selector))
      throw Error('Correct-password deletion failed');
    console.log('PASS: isolated sender deletion dialogs at desktop/mobile, accessibility, cancellation, Escape, missing/wrong/correct password');
  }
  await Promise.all([page.waitForNavigation(), page.click('form[action="/operator/logout"] button')]);
  if (new URL(page.url()).pathname !== '/operator/login') throw Error('Logout failed');
  await page.goto(base + '/');
  if (new URL(page.url()).pathname !== '/operator/login') throw Error('Logged-out admin access allowed');
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

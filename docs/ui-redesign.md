# Professional workspace redesign

The application now uses one original design system and responsive application shell. Its light content surfaces, indigo navigation, blue/violet/cyan accents, local SVG icons, typography scale, spacing, radii, and shadows are shared across the product. No externally hosted fonts, analytics service, or chart library is required.

## Pages

| Area | Experience |
| --- | --- |
| Dashboard | Campaign overview, larger KPI cards, real campaign click-rate comparison, platform controls, campaign activity |
| Campaign directory | Actual campaign summaries, searchable management table, draft/active states |
| Campaign creation | Visible workflow progress, guided draft creation, explicit authorization |
| Recipients | Cross-campaign membership directory, search, approval states, campaign links |
| Campaign audience | Searchable participants, per-campaign approval, launch control, template and preview navigation |
| Recipient creation | Focused participant form, explanation of the separate approval step |
| Analytics | Approved-recipient metrics, click-rate comparisons, audience engagement distribution |
| Campaign results | Delivery and awareness KPIs, engagement distribution, searchable and status-filtered results |
| Template library | Protected-asset counts, sender metadata, personalization indicators, search, edit/preview actions |
| Template editor | Workspace command bar, safe formatting tools, inline token insertion, verified sender selection, assets, shared-renderer sample preview |
| Campaign email setup | Saved-template selection, verified sender override, authorization, snapshot explanation |
| Recipient email preview | Actual personalized envelope and message, campaign progress, authorization and delivery controls |
| Sender management | Account cards, provider metadata, verification/default/disabled states, confirmation before deletion |
| Sender form | Write-only password field, connection settings, provider guidance, verification workflow |
| Settings | Actual encryption/cookie configuration indicators and documented application controls; no secrets displayed |
| Participant landing | Separate administrative-navigation-free learning experience; no credential inputs |
| Error pages | Preserved HTTP statuses, readable messages, recovery actions; public link errors stay separate from administration |

Charts derive only from database rows. Each approved campaign membership is counted once; repeated link clicks are deduplicated. Aggregate metrics exclude unapproved recipients. No campaign data means explanatory empty states, not invented trends. These charts describe observed engagement, not overall security maturity.

## Safe email authoring

New presentation settings include an optional plain-text heading, tracking button label, left/center alignment, and standard/sectioned/compact layout. Sectioned layout recognizes `## ` heading lines and `---` dividers. Content, substitutions, and labels remain escaped; arbitrary HTML, forms, scripts, URLs in style settings, and credential collection are not enabled. The tracking button always uses the existing recipient tracking URL.

A nullable `email_templates.presentation` TEXT column stores a JSON allowlist. Run `python -m flask --app app init-db` to add it without resetting existing data. Templates and campaign snapshots without presentation settings use compatible defaults. Snapshots include the presentation settings, and preview signatures include the renderer version to invalidate reviews made before a renderer upgrade. The same function renders sample previews, actual-recipient previews, and delivery.

## Interaction and accessibility

Desktop uses a persistent sidebar and content width up to 1600 pixels. Tablet/mobile use a keyboard-accessible navigation disclosure with Escape-to-close, focus containment, and inert background content. Cards and forms stack; tables scroll inside their own bounded containers rather than overflowing the page. Token insertion works in the focused subject/body field. Search and status filters use existing page data without external requests. All navigation and core forms remain functional without JavaScript.

Buttons have hover/press feedback, cards gently elevate, and content enters with a short transition. `prefers-reduced-motion` disables animation and smooth scrolling. Focus outlines, a skip link, semantic table captions, labels, explicit textual statuses, and live feedback support keyboard and assistive technology use. Form submissions show a loading indicator and busy state; empty searches, empty data, validation errors, saved accounts/templates, and disabled controls have explicit states.

## Reproduce validation

```sh
python -m pytest -q
TEST_MYSQL_DATABASE=awareness_studio_test python -m pytest -q
```

The named MySQL test database must be empty and separate from application data. Existing account, upload, authorization, tracking, scoring, and snapshot tests remain part of the full suite.

For optional automated browser review, install `puppeteer-core` and `axe-core` in an external tools directory and set `NODE_PATH` to its `node_modules`. Set `CHROME_PATH` to a Chromium/Chrome executable if needed. Then run:

```sh
node scripts/verify_ui.cjs
```

The script checks 1920, 1440, 1280, 768, and 390 pixel widths; saves screenshots; checks browser JavaScript errors, keyboard navigation, token/format insertion, search empty states, reduced motion, and WCAG A/AA rules through axe. `UI_BASE_URL` selects the server, `UI_SCREENSHOTS` selects the output directory, and optional `UI_CAMPAIGN_ID` / `UI_TRACKING_TOKEN` enable existing campaign/participant pages. Use a review instance with delivery disabled.

For a populated, isolated automated-browser fixture, run `python tests/browser_review_server.py`. This test-only helper uses the existing isolated SQLite adapter in a newly allocated temporary directory, substitutes SMTP verification, and blocks all email delivery. It binds only to loopback at port 5056 and never reads/writes the application MySQL database. Its campaign names explicitly begin with “UI review”. It is not an alternative application database or a production configuration. The ordinary review application continues using MySQL on port 5055.

No scheduling, bulk sends, archive actions, OAuth flow, or other unsupported feature is presented as available. Existing campaign delivery remains deliberate and constrained by approval, activity, authorization, verified saved senders, and recipient preview checks.

## Changed-file summary

| Files | Purpose |
| --- | --- |
| `templates/base.html`, `templates/public_base.html`, `templates/components/ui.html`, `templates/studio_base.html` | Shared shell, navigation, icons, reusable metrics/tables/charts/empty states; separate participant shell |
| `static/css/style.css`, `static/css/studio.css`, `static/favicon.svg` | Design tokens, responsive layouts, readable typography, depth, reduced motion, shared Studio components |
| `static/js/app-ui.js` | Accessible mobile navigation, local search/status filters, token insertion, formatting tools, submission feedback, delete confirmation |
| Existing campaign, participant, Studio, preview, library, and sender templates | Page-by-page visual and interaction upgrades |
| `templates/campaigns.html`, `templates/recipient_directory.html`, `templates/analytics.html`, `templates/settings.html`, `templates/error.html` | Working directory, analytics, configuration, and error screens |
| `app.py` | Real campaign aggregates and new overview routes, polished errors that retain HTTP statuses/headers |
| `template_content.py`, `template_store.py`, `studio.py` | Safe presentation settings, persistence, snapshots, shared rendering, renderer-version review invalidation |
| `database.py`, `schema/mysql.sql`, `tests/sqlite_schema.sql` | Additive nullable presentation column and isolated-test schema support |
| `tests/test_product_ui.py` | Analytics, UI integration, layout safety, migration/backward compatibility, preview/delivery, and error regressions |
| `scripts/verify_ui.cjs`, `tests/browser_review_server.py` | Reproducible browser checks and isolated populated visual-review fixtures |
| `README.md`, `docs/mysql-setup.md`, this guide | Setup, migration, design decisions, and review instructions |

Validation completed: **188 automated tests passed with the isolated test adapter and 188 passed with MySQL**. Automated browser review covered 18 pages at 1920/1440/1280/768/390 pixels (90 page/viewport combinations), with no page overflow, browser JavaScript exceptions, or checked WCAG A/AA violations. Keyboard navigation, token/format insertion, filters, and reduced motion passed. A separate console check covered the working administrative/campaign screens. Secret scanning used 27 detection plugins plus exact comparisons against private environment credentials; no findings. Populated screenshots come solely from explicitly labeled isolated test fixtures, while the normal review application continues displaying real MySQL application data.

Visual acceptance remains subject to the user's review. The initial visual-review handoff did not include a commit, push, or merge; `main` was not modified.

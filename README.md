# Phishing Awareness Tracker

A Flask application for authorized awareness campaigns, approved-recipient email delivery, unique click tracking, and awareness scoring. The Email Template Studio adds reusable messages, per-campaign snapshots, preview, illustrations, and passive attachments. It does not collect participant passwords, OTPs, or authentication credentials.

**Application database: MySQL 8.4 LTS.** SQLite is used only by isolated automated tests; it is not an application configuration option.

## Install on Mac, Windows, or Linux

Prerequisites: Git, Python 3.12 or newer, and MySQL 8.4. For the optional local database container, install Docker Engine with Compose on Linux, or Docker Desktop on Mac/Windows. Windows commands below use PowerShell.

```sh
git clone https://github.com/masud1445/phishing-awareness-tracker.git
cd phishing-awareness-tracker
```

Use the branch/version containing Template Studio. In an existing cloud task, use its checkout and current branch; each task is already isolated, so do not create a worktree unless explicitly requested.

Mac/Linux:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.lock
```

Windows:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
```

If PowerShell activation is unavailable, invoke `.venv\Scripts\python.exe` directly. No activation is required when using the full Python executable path.

### Option A: local MySQL with Docker

On any of the three platforms, from the activated environment:

```sh
python scripts/local_mysql.py
python -m flask --app app init-db
python app.py
```

The helper starts the pinned official MySQL 8.4 image, binds port 3307 to loopback, generates private local database credentials in `.env` and `instance/mysql.env` only when those files do not exist, and waits for database health. It never overwrites an existing `.env`. Use it for a new local setup; if `.env` already targets an external database, follow Option B instead. Application data lives in the ignored `instance/mysql-data` directory outside public static assets. Keep those files when restarting.

Restart the container with the same helper. Stop this local database with:

```sh
docker compose --env-file .env --env-file instance/mysql.env stop
```

### Option B: an existing MySQL server

Install/start MySQL 8.4 using MySQL Installer on Windows, the MySQL package on Linux, or the MySQL installer/Homebrew on Mac. Create an empty `phishing_awareness_tracker` database and an application user using your MySQL administration tool. Give the user SELECT, INSERT, UPDATE, DELETE and the table-creation privileges needed during setup. Never use a shared production schema for tests.

Copy `.env.example` to `.env` (`cp` on Mac/Linux, `Copy-Item` on PowerShell), then edit DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, and DB_NAME privately. Do not commit `.env`.

```sh
python -m flask --app app init-db
python app.py
```

`schema/mysql.sql` is the complete schema and additive Studio migration. See [migration and backup instructions](docs/mysql-setup.md) before upgrading an existing installation.

## Email Template Studio

1. Configure SMTP on the server as described below.
2. Open **Email Template Studio** from the dashboard. Create/edit a reusable template with a subject, plain-text body, sender display name, configured sender identity, and optional HTTPS reference link.
3. Use `{{recipient_name}}`, `{{tracking_url}}`, and `{{campaign_name}}` in the subject/body. Unknown or malformed placeholders are rejected. Text is HTML-escaped; templates cannot insert forms or scripts.
4. Add one illustration (PNG/JPEG) and up to five attachments (PDF, PNG, JPG/JPEG, DOCX, TXT), each at most **5 MiB (5,242,880 bytes)**. Unsafe path-like filenames, unsupported extensions, mismatched file content, encrypted/active PDFs, macros, embedded executable content, and external DOCX relationships are rejected. Images are decoded and re-encoded. Files are stored in MySQL BLOBs, outside the public static directory, and retrieved by integer ID with safe download headers.
5. Create an authorized campaign, add recipients, and approve them. **Configure Studio template** copies a snapshot into the campaign; subsequent library edits do not change that snapshot. Removed library assets are archived because existing campaigns may still need them.
6. Launch the campaign, choose an approved recipient in **Campaign Email Preview**, review the HTML/plain-text alternatives and included files, confirm authorization, and send. A changed snapshot, recipient, sender configuration, or tracking URL invalidates the Studio review. Templates are locked after the first successful delivery. Preview links are inert and do not record clicks; delivered links retain the existing awareness landing page.

Existing campaigns continue to use the original default email subject and awareness message unless a Studio template is explicitly applied. Their approved-recipient, activity, first-sent timestamp, click tracking, and awareness scoring behavior is retained. The existing preview now reflects the actual default message.

## SMTP configuration

The existing sender remains configured through server environment settings. Additional sender accounts use the operator-only [Sender Account Manager](docs/sender-accounts.md), with write-only password entry and encrypted storage. Configure `.env` or injected environment values for the original sender:

- `SMTP_HOST`, `SMTP_PORT` (default 587), `SMTP_USER`, `SMTP_PASSWORD`
- `SMTP_FROM_EMAIL`: the provider-authorized sending address
- `SMTP_FROM_NAME`: default display name for legacy campaign messages
- `SMTP_ALLOWED_FROM_EMAILS`: optional comma-separated aliases approved by the same authenticated SMTP provider
- `BASE_URL`: the reachable application URL used in recipient tracking links

Delivery uses authenticated SMTP with STARTTLS or SSL/TLS and certificate/hostname verification. Saved sender accounts require a successful connection test; legacy campaigns retain their existing trusted environment configuration. Merely listing an alias does not verify it at the provider: the operator must configure a provider-authorized identity. Delivery fails closed if SMTP setup, recipient approval, campaign activity, authorization, or asset integrity fails. SMTP credentials are not participant credentials and are not stored in templates.

For shared deployments set `SECRET_KEY` consistently across workers and `SESSION_COOKIE_SECURE=1` under HTTPS. Local development creates an ignored private signing key. This repository is an internal operator application; place the dashboard, Studio, and asset routes behind your organization's existing operator access controls. The recipient landing page stays free of credential input fields.

## Automated tests

```sh
python -m pytest -q
```

This executes all tests, including the original safeguard, click-tracking and scoring suites, new validator/security tests, and complete campaign flows on an isolated test-only relational adapter. This adapter avoids requiring database credentials for unit/integration development and cannot be selected by the application.

For MySQL integration, create a **separate empty test database**, give the configured DB_USER privileges to create/drop its tables, and set `TEST_MYSQL_DATABASE` to its name:

```sh
TEST_MYSQL_DATABASE=awareness_studio_test python -m pytest -q
```

PowerShell:

```powershell
$env:TEST_MYSQL_DATABASE = 'awareness_studio_test'
python -m pytest -q
Remove-Item Env:TEST_MYSQL_DATABASE
```

The fixture refuses a populated schema, initializes tables, and removes only its own test tables after each test. SMTP is replaced by a test double; tests never send external email. The test configuration collects the entire `tests` directory and excludes generated MySQL data from test discovery.

## Professional workspace UI

See [the redesign and browser-review guide](docs/ui-redesign.md) for the shared design system, responsive screens, safe email-formatting controls, and reproducible visual/accessibility checks. Run `python -m flask --app app init-db` when upgrading to add the nullable presentation settings column without resetting existing data.

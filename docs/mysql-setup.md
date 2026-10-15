# MySQL schema setup and Template Studio migration

The application uses MySQL only. `schema/mysql.sql` serves both as a fresh schema and an additive migration for the original application. Existing `campaigns`, `recipients`, and `tracking_events` are left unchanged by `CREATE TABLE IF NOT EXISTS`; three new tables are added:

| Table | Purpose |
| --- | --- |
| `email_templates` | Reusable subject/body, display name, configured sender, reference link |
| `template_assets` | Validated asset bytes, safe download names, MIME type, image/attachment kind and archive flag |
| `campaign_email_settings` | Independent message and asset-list snapshot per campaign |

The original schema is not versioned in the old repository. Its existing campaigns/recipients/tracking fields are inferred from the application's SQL. If your deployed schema differs, inspect it before migrating; foreign-key IDs must match the signed INTEGER IDs used in this setup. The explicit `init-db` command adds nullable `sender_account_id` and `presentation` columns to an existing `email_templates` table if missing. It also adds `sender_accounts` and `sender_preferences` without changing campaign snapshots. No data reset or destructive migration runs.

## Fresh install

1. Install/start MySQL 8.4 and create a database through your database administration tool. MySQL's database charset should be `utf8mb4`.
2. Configure the application `.env` privately, following `.env.example`, or use the optional local Docker helper in the README.
3. Install dependencies and run `python -m flask --app app init-db` from the repository root. For a fresh installation, `schema/mysql.sql` contains the complete schema. For upgrades, use `init-db` so its conditional additive column migration also runs. The command never creates/chooses a different database or resets existing data.
4. Run `python app.py`. Verify the dashboard, campaign creation, recipient approval, and Studio library before adding real SMTP credentials.

## Upgrade an existing installation

1. Back up the existing schema/data with your MySQL administration tool or `mysqldump`, and retain the previous code version. Stop campaign sending during upgrade.
2. Review `schema/mysql.sql` against `SHOW CREATE TABLE` for the three existing tables. Existing rows need no backfill: campaigns without a `campaign_email_settings` record use the original default email content.
3. Install the new dependency lock and run `python -m flask --app app init-db` with the existing DB_* settings. Table creation and the conditional sender/presentation column migrations are additive and can be rerun. `schema/migrations/002_sender_accounts.sql` documents the sender tables; use `init-db` for the full upgrade. MySQL DDL implicitly commits; if creation fails partway, correct the prerequisite and rerun rather than assuming a transaction rolled everything back.
4. Preview an existing campaign and check its approved recipient list and results. Do not send until you have reviewed the intended recipient message and authenticated sender configuration.
5. Add Studio templates and apply them explicitly to new or unsent authorized campaigns. Library changes never update campaign snapshots automatically. Campaign snapshots refer to retained asset IDs and SHA-256 hashes; keep archived assets while referenced by campaigns.

## Operational settings

MySQL must accept at least a 5 MiB file plus SQL packet overhead. The official MySQL 8.4 image uses a sufficient `max_allowed_packet`; for an external server configure **at least 16 MiB** and check that setting through your administration tool. A single request is capped at 32 MiB and at one image/five attachments.

Local Docker data is retained under `instance/mysql-data`. Stop MySQL before filesystem snapshots/backups; for a running external database, use database-aware backup tools. Preserve `.env`, `instance/mysql.env`, and `instance/session.key` privately; never commit them. On Windows these files rely on the user directory's access permissions.

The isolated MySQL test fixture accepts only an empty, explicitly named test schema and rejects the application schema. Do not point `TEST_MYSQL_DATABASE` at an application or production database.

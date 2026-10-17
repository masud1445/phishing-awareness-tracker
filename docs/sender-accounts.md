# Sender Account Manager

Run on `feature/pro-template-studio`. MySQL remains the application's only database. Back up your database and stop delivery before upgrading, then run:

```sh
python -m pip install -r requirements.lock
python -m flask --app app init-db
```

Setup adds sender tables and a nullable template sender ID without changing existing campaigns, recipient approval, tracking, scoring, or stored message snapshots. The existing environment-configured sender appears first on **Sender accounts**. Its password remains in server environment configuration rather than being copied into the database. Original campaigns without a saved sender ID keep their existing trusted SMTP configuration and delivery behavior.

## Encryption key

Generate a Fernet key once on the application server:

```sh
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Store that output privately as `SENDER_ENCRYPTION_KEY` in injected environment configuration or the ignored `.env` file. Do not put it in SQL, source control, browser pages, or shared setup documents. Restrict file access to the server operator. Keep a separate secure backup: losing or changing the key makes saved passwords unreadable. Key rotation requires decrypting/re-encrypting stored secrets through a controlled offline migration; this release does not implement automatic rotation. A missing/invalid key fails closed. Keep the key stable across restarts. Operator sessions currently require one application worker; see the deployment guide.

## Account workflow

1. Open **Sender accounts** or **+ Add sender** beside the Studio sender field.
2. Choose Gmail, Microsoft/Outlook, or Custom SMTP. Enter the sending address, display name, hostname, port, TLS mode, SMTP username, and SMTP/app password.
3. Save, then select **Test connection**. Testing checks certificate-verified TLS, authentication, and SMTP envelope sender acceptance using `MAIL FROM` followed by `RSET`. It never submits a recipient or email body. Successful testing verifies server acceptance at that time; it cannot guarantee a provider's subsequent delivery/send-as policy.
4. Select a verified account in a template or override its sender when applying a template to a campaign. Set a verified account as default to preselect it for new templates. Campaign sender names remain configurable.
5. Preview each actual approved recipient before sending. The selected sender ID/address and account revision bind the preview to delivery. Editing, re-verifying, disabling, or deleting an account invalidates an earlier review. Edits and re-enabling require a new connection test. A failed re-test revokes verification.

Passwords are write-only and encrypted with Fernet before database storage. Blank password edits retain the encrypted secret; no edit, list, preview, or public helper exposes plaintext or ciphertext. Deletion removes the encrypted secret and soft-deletes the account, retaining IDs and campaign snapshots so historical messages remain readable. Deleted or disabled saved accounts cannot deliver. Existing campaign snapshots are never silently switched to the default account.

Gmail normally requires an app password. Microsoft/Outlook uses `smtp.office365.com:587` with STARTTLS and requires an account/tenant that still permits password-based authenticated SMTP. OAuth-only accounts need a future OAuth integration; this release does not circumvent provider policy. Custom SMTP supports STARTTLS and SSL/TLS, with certificate verification required. Local/private/link-local SMTP destinations and DNS results are rejected to prevent server-side network probing or secret leakage. DNS addresses are checked and pinned while certificates are validated against the original hostname.

Administrative routes now require the configured operator login; see [operator access and deployment](admin-security.md). Keep administration on a trusted network or behind an authenticated reverse proxy, and serve it over HTTPS before entering SMTP secrets. Set `SESSION_COOKIE_SECURE=1` there. Do not publish administrative campaign or sender pages to recipients. Reverse proxies and observability tools must not record request bodies containing SMTP passwords. Recipient landing pages remain read-only and never collect passwords, OTPs, or authentication credentials.

## Validation

```sh
python -m pytest -q
TEST_MYSQL_DATABASE=awareness_studio_test python -m pytest -q
```

Use a separate empty MySQL test database; the fixture refuses the application database or a populated schema. SMTP transports are replaced with doubles and no real emails are sent. Tests cover account CRUD/defaults, verification, TLS destinations, encrypted storage, sender preview/delivery consistency, historical snapshots, and existing campaign controls.

## Permanent credential deletion

Delete opens a confirmation dialog showing the selected sender's display name and email. Enter the **current operator password**, not the SMTP/app password. The server verifies it against ADMIN_PASSWORD_HASH on every deletion request; wrong or missing passwords leave the account untouched. Re-authentication uses the existing per-address sign-in throttle (five attempts per ten minutes, shared with login). Passwords are never echoed into HTML or stored with the account. Cancel and Escape clear the dialog's password field. Without JavaScript, Delete opens the same confirmation as a normal page.

The default sender cannot be deleted until another verified sender is selected as default. A sender referenced by an active campaign cannot be deleted until that campaign selects another verified sender; campaigns locked after delivery continue to protect their sender. Server-environment identities cannot be deleted through the web application: use reversible Disable or manage server configuration. These checks run in the deletion transaction.

Deletion removes the encrypted credential and verification state while retaining the sender metadata row, historical campaigns and template snapshots. Disable remains reversible, retains the encrypted credential, requires no password re-entry, and continues to require verification after re-enabling.

Browser regression checks optionally accept `UI_DELETE_FIXTURE_SENDER_ID=2` for the disposable browser fixture. They refuse to perform destructive checks unless the response identifies itself as the isolated automated fixture. Never use these checks against production.

Deletion-hardening validation: **286 isolated tests passed and 286 MySQL tests passed**. Browser validation passed all 90 page/viewport combinations plus desktop/mobile deletion-dialog accessibility, identity details, cancellation, Escape, password clearing, and missing/wrong/correct-password behavior. The 27-plugin secret scan and exact private-credential comparisons found no real secrets; disposable test values and the token alphabet were reviewed as false positives. No real email was sent.

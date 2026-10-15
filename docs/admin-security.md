# Operator access and deployment

Administrative pages, mutation endpoints and private template assets require an operator session. `/t/<token>` remains public and preserves tracking/scoring. Tracking tokens never authorize asset downloads. This is a single-operator application: authenticated operators share all administrative data; there is no tenant isolation or per-campaign ownership model.

## Local setup

After installing requirements and configuring MySQL, run:

```sh
flask --app app hash-operator-password
```

Enter a unique password of at least 12 characters at the hidden prompt. Put the resulting hash in the ignored `.env` as `ADMIN_PASSWORD_HASH='<generated hash>'`, and set `ADMIN_USERNAME`. Never put the plaintext password in source, shell arguments or Git. Missing or weak hashes disable administrative login. Open `/operator/login`; sign out using the navigation button. Sessions expire after 30 minutes by default (OPERATOR_SESSION_MINUTES, maximum 480), are revoked on logout, password change or process restart. No database migration is needed for operator access.

All administrative state-changing requests, including login/logout and legacy campaign actions, require a session-bound CSRF token. Forms supply it automatically; clients may use `X-CSRF-Token`. Preview state is cleared at login. Asset access is checked before database lookup, including HEAD requests; administrative and asset responses are not cached. Preview iframes permit same-origin access so protected images receive the operator cookie, but scripts remain prohibited by the sandbox.

## Deployment requirements

Use ONE application worker/process with optional threads. Session revocation and login throttling are in memory, shared by threads, and deliberately fail closed after restart. Multiple workers/replicas require replacing these stores with shared storage or an authenticated gateway; a shared SECRET_KEY alone does not provide multi-worker support. Five login attempts per remote address are permitted per ten minutes. Behind a proxy, also enforce gateway rate limiting; forwarded IP headers are not trusted by the application. Use gateway SSO/MFA where required.

Production requires all of:

- `APP_ENV=production`, `SESSION_COOKIE_SECURE=1`, an explicitly configured random `SECRET_KEY` of at least 32 characters, and `TRUSTED_HOSTS` containing the actual hostname(s). Incomplete configuration disables administrative login.
- HTTPS at a properly configured reverse proxy, with HTTP redirected before reaching Flask. Bind the application to loopback/private networking; do not expose the Flask development server. Use a production WSGI server with one worker. Set BASE_URL to the canonical HTTPS URL for public tracking links.
- Persistent private uploads outside static, private environment/instance files, restricted database access, encrypted backups and a securely backed-up SENDER_ENCRYPTION_KEY. SMTP identity verification and provider configuration are still required. Never use the production database for tests.

The application is not a multi-user identity service. An external access gateway is recommended for internet-facing administration. Public tracking URLs are bearer links; do not share them beyond the approved exercise. Existing participant pages never ask for credentials.

## Automated checks

GitHub Actions runs the complete suite twice: isolated fixtures and a fresh MySQL 8.4 service. CI passwords are disposable fixture placeholders, not deployment credentials. SMTP transports are mocked; the suite blocks real SMTP connect calls. Tests verify authentication, session expiry/revocation, login throttling, CSRF across administrative mutations, private asset enumeration denial and anonymous public tracking, alongside all previous Studio, sender and campaign regressions.

For browser validation, run the isolated `tests/browser_review_server.py` fixture and supply its test-only `UI_OPERATOR_USERNAME=review-operator` and `UI_OPERATOR_PASSWORD=isolated-browser-review-password` to `scripts/verify_ui.cjs`. Never point the fixture at production. Browser tooling is optional and installed separately, as documented in the UI review instructions.

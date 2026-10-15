# Premium Template Studio: final security and deployment review

This branch preserves the approved responsive SaaS interface and adds a central administrative security boundary. It includes reusable templates, safe presentation controls, encrypted multi-sender account management and verification, immutable campaign snapshots, and consistent preview/delivery rendering.

## Final security changes

- Operator sign-in using an environment-configured strong password hash; no default password. Administrative access fails closed until configured.
- Global CSRF validation for every administrative mutation, including campaign/recipient creation, approvals, launch, legacy email sending, sender actions and login/logout.
- Absolute session expiry, password-change invalidation, logout revocation (including copied cookies), session rotation, and bounded login throttling.
- Private assets require operator authentication before any lookup; public campaign tracking tokens grant no asset permissions. No-store, nosniff and framing restrictions protect administrative responses.
- Public participant tracking remains anonymous and never accepts credential submissions. Existing approval, activity, authorization, scoring, tracking, upload validation, sender encryption/verification and snapshot safeguards remain covered.
- GitHub Actions runs the complete suite on pushes and pull requests with isolated fixtures and a disposable MySQL 8.4 service. Real SMTP connection attempts fail automated tests.

## Validation results

- Complete isolated pytest suite: **274 passed**.
- Complete MySQL pytest suite: **274 passed**, against the empty, separate `awareness_studio_test` database.
- Browser validation: 18 routes at 1920, 1440, 1280, 768 and 390 px; 90 route/viewport combinations. Includes keyboard navigation, filters, authoring tools, reduced motion, axe WCAG A/AA checks, and browser exception checks. Additional security checks cover login/logout, anonymous asset rejection, public tracking, protected inline preview images and login layout at 1440/390 px.
- Secret review: 27-plugin scan plus exact comparison against private environment credentials; no real credential matches or generated/private artifacts in the intended commit. Scanner false positives are explicit dummy test passwords, a deliberately rejected URL credential fixture and the token alphabet. The raw scan is retained outside the repository for review.
- No real email was sent. No production database was used by tests. No changes were made to main.

## Deployment and remaining limitations

Configure ADMIN_USERNAME and ADMIN_PASSWORD_HASH using the hidden interactive hash CLI before signing in. Production additionally requires HTTPS, a production WSGI server, explicit trusted hostnames, a secure random SECRET_KEY, secure cookies, private MySQL access, persistent private assets and the separately backed-up SENDER_ENCRYPTION_KEY. Existing installations need no authentication database migration.

This is a shared single-operator administrative workspace, not a multi-tenant service. Use one application worker/process: session revocation and login throttling are process-local. Multiple workers require shared security stores or a suitable authentication gateway. Apply gateway rate limits and SSO/MFA as appropriate. Microsoft tenants requiring OAuth-only SMTP remain unsupported; provider verification does not guarantee eventual delivery. Real provider delivery requires the operator's final controlled manual test.

See [operator access and deployment](admin-security.md), [sender account setup](sender-accounts.md), [MySQL installation](mysql-setup.md), and [premium UI details](ui-redesign.md).

The GitHub REST and GraphQL APIs returned Forbidden in this environment, preventing direct PR-description editing. This document provides the current PR description/results; pushing the feature branch updates its code without merging or closing the PR. Hosted CI execution must still be confirmed on GitHub after push.

# Security and privacy policy

This is an independent custom integration that can, when explicitly enabled, request physical vehicle actions. Treat your Home Assistant host, administrator access, backups and automations as sensitive.

## Reporting

Please use [GitHub private vulnerability reporting](https://github.com/zangkasi55/gwm-thailand-connect/security/advisories/new) for suspected security issues. If reporting is not available, open a minimal issue asking the maintainer to enable a private channel; **do not disclose credentials, exploit details or owner data publicly**.

Do not submit real tokens, VINs, account contacts, PINs, OTPs, keys/certificates, private provisioning, raw cloud responses, location traces or HA backups.

## Supported scope

Security fixes target the latest published version. Manufacturer service behavior and vehicle safety remain outside a community integration's control. No response time or indefinite support guarantee is implied.

## Data handling

- Code login does not save an account password or OTP.
- Password login saves the password in HA's private configuration.
- Optional control PINs are stored in HA options and removed on opt-out.
- Generated device keys are encrypted; the same HA host also stores their unlocking material and tokens. A compromised host/backup can still compromise access.
- App provisioning is imported privately from a local reviewed artifact, not shipped in source or release assets.
- Diagnostics contain only a bounded summary and exclude owner/account/vehicle identifiers, credentials, tokens, keys, PINs and locations.
- No location analytics, third-party telemetry or remote image/CDN dependency is added by the dashboard.

## Safe defaults

Remote writes are disabled by default. Every write requires explicit confirmation, configured owner PIN and a fresh manufacturer capability check. The HA service enforces user control permission when a user context is present. Trusted automations with no user context are the HA administrator's responsibility.

Transport and uncertain command failures are not automatically retried. Acceptance is not completion. A timed-out command may still execute; check the vehicle before another request.

Do not weaken TLS, copy identities across accounts, or automatically take over another device's session.

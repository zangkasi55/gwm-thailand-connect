# Troubleshooting

Never post account contact details, VINs, location, OTPs, PINs, tokens, certificates/private keys, provisioning JSON, APKs or raw cloud responses in issues.

## No values / unavailable entities

- First finish the native integration setup, not only the dashboard.
- Check the integration entry's state in **Settings > Devices & services**.
- Compare the official Thai app and reported cloud timestamp.
- A registered entity can exist while unavailable. Its dynamic attributes may disappear; absence of `gwm_signal` is not proof that the device was never discovered.
- Unknown values remain unknown. Do not replace a missing cabin measurement with the A/C target temperature.

## Password field appears in code mode

The current flow uses two separate method steps. Code mode has only a contact field; password mode requires a password. After updating/restarting HA, reload the browser to clear stale frontend translations and restart the setup flow.

The account password, one-time verification code and vehicle security PIN are three different credentials.

## Verification code delayed / rejected

- Use only the phone/email already linked to the official Thai app.
- Wait for delivery and avoid repeated requests. Delayed earlier codes may no longer be valid.
- Keep the setup flow open and enter the code only in its private form.
- Rate-limited requests are not automatically retried.
- Complete any official human-verification or account recovery in the manufacturer's app. This project does not bypass those checks.

## `310022`: existing device certificate, no certificate returned

The backend can report an existing certificate without supplying usable certificate data. An explicit new-integration-device recovery step is available. It requests normal verification under a fresh device ID:

- old tokens are not reused for the new device;
- no guessed certificate-revocation endpoint is called;
- phone certificates are not silently revoked;
- the new login may still replace the phone's account session.

Do not delete private state just to repeat enrollment: that can lose the key needed for an issued certificate.

## `570062`: agreement/protocol update

The official app identifies this as an updated protocol/agreement response, **not an incorrect vehicle PIN**. Open the official Thai app, review updates and accept only if you agree. Then explicitly reconnect in HA if required.

HA does not accept an updated agreement for you.

## `607501`: account logged in on another device

The official app maps this code to a session on another device. Owner verification/reconnection is required. HA does not clear the bound identity or automatically take the phone session back.

Prefer a dedicated authorized shared account when available.

## Vehicle PIN rejected

- Recover/reset it through the official app's supported process.
- Do not put it in the account-password form.
- Do not share it in an issue, chat or screenshot.
- This integration submits its PIN check only for an explicitly requested eligible command.
- A sign-in or agreement error is not evidence that the PIN itself is wrong.

## Certificate / private-material error

Check the exact supported APK hash in [installation](installation.md), local provisioning output, HA storage permissions and free space. Do not relax TLS or copy a key from another vehicle/account. Unknown artifacts and mismatched keys/identities are intentionally rejected.

Development certificates are generated fresh by tests. OEM bootstrap private material is never a repository fixture.

## Command accepted but not completed

A transport timeout, result-poll failure or 90-second result timeout means the outcome may be unknown. **Check the vehicle before retrying.** No automatic write retry is made. After restart, the journal is used only to read a pending outcome.

## Card missing / custom element not found

- Complete integration setup first so the static card resource is registered.
- Verify the JavaScript module URL in [installation](installation.md#5-add-the-dashboard).
- Reload the browser after updates and check for duplicate old resources.
- Use `custom:gwm-thailand-card`, not a private predecessor's custom element name.
- Use dashboard URL path `gwm-thailand` for the details link.

## Reporting a problem

Include project version, HA version, Thai market/model (no VIN), expected/actual behavior and a sanitized numeric API code if known. Optional integration diagnostics contain only a small summary, but always review before sharing.

Do not attach a full HA backup or configuration storage file. Use [GitHub issues](https://github.com/zangkasi55/gwm-thailand-connect/issues) for non-sensitive bugs and [SECURITY.md](../SECURITY.md) for private reports.

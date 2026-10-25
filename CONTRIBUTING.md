# Contributing

Thank you for helping improve GWM Thailand Connect.

## Start here

1. Read the [architecture](docs/architecture.md), [development guide](docs/development.md) and [security policy](SECURITY.md).
2. Search existing issues before opening a non-sensitive bug.
3. For a new feature or new model mapping, discuss the evidence and behavior first.
4. Use a focused branch and minimal changes; include regression tests and directly related docs.
5. Run the documented Python, Node, scanner and package checks.

## Good contributions

- Synthetic fixtures demonstrating a reproducible parser/state bug.
- Additional model/trim compatibility reports without VINs, accounts or locations.
- Native translation improvements.
- Better unknown-state handling, accessibility and isolated dashboard tests.
- Documentation corrections and reproducible synthetic screenshots.

## Evidence and safety

- Do not invent mappings from field names alone. Explain how a protocol/state meaning was established.
- Never assume an absent reading means closed, locked, zero or off.
- Keep controls opt-in, permission/capability gated, explicitly confirmed and non-retrying.
- Keep account login separate from vehicle PIN checks.
- Preserve server TLS verification and account/device/key binding.
- Do not bypass official agreements, human verification, session conflict or authorization.
- Never execute physical commands in CI.

## What must not enter a PR

Passwords, OTPs, PINs, tokens, account contacts, vehicle identifiers, GPS coordinates, raw cloud captures, HA storage/backups, app signing secrets, OEM keys/cert resources, APKs, decompiled program output or copied proprietary artwork.

Generate new test certificates locally during tests. Use contacts under `example.invalid` and obvious synthetic vehicle/token values. No real certificate/private key belongs in a fixture.

## Licensing and attribution

Contributions must be yours to license under MIT. Keep the original upstream MIT notice and describe any new dependency/resource license accurately. Do not copy code from a repository without an applicable license.

The neutral icon and synthetic screenshots are project-authored; replacing them with manufacturer logos or third-party vehicle images requires appropriate permission.

## Pull request checklist

- [ ] Behavior is explained and supported by a failing-then-passing test where appropriate.
- [ ] Python tests, Ruff, strict mypy and Node tests pass.
- [ ] Scanner and source-only release builder pass.
- [ ] No owner/app/private data or proprietary artifacts are included.
- [ ] User-facing changes are documented.
- [ ] Physical vehicle behavior is not claimed unless separately and safely verified.

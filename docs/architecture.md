# Architecture, protocol and trust boundaries

## Components / tech stack

| Layer | Technology and responsibility |
| --- | --- |
| HA adapter | Python 3.14, native config/options flows, typed config entry, `DataUpdateCoordinator` |
| Persistence | HA private atomic `Store`, per-account serialized updates |
| Wire client | `aiohttp`, fixed Thai endpoint allowlist, bounded responses/deadlines |
| Identity | `cryptography`, RSA-2048/SHA-256 CSR, issuer/key/account/device binding checks |
| Transport | System HTTPS server trust, hostname validation, TLS >=1.2, security level >=2 |
| Entities | Native sensors/binary sensors with strict missing/unknown behavior |
| Frontend | Dependency-free JavaScript ES module, native custom element, CSS-only vehicle drawing |
| Tests | pytest, pytest-asyncio, scripted transports, generated synthetic identities; Node test runner |
| Development only | Ruff, mypy, Playwright screenshot generator, Shapely offline basemap builder |
| CI | GitHub Actions, Hassfest, HACS validation |

Runtime does not need Node, Playwright, an Android device or an emulator.

## Data and privacy boundary

```mermaid
flowchart TB
    subgraph Local["Trusted local development computer"]
        A[Lawfully obtained reviewed APK] --> X[Fingerprint-pinned local exporter]
        X --> J[Private provisioning JSON]
    end
    subgraph HA["Your Home Assistant host"]
        J --> F[Private setup upload]
        F --> M[Private app-material Store]
        U[Owner account consent + code/password] --> C[Config flow]
        M --> C
        C --> K[Account-bound identity Store]
        K --> API[Thai API client]
        API --> CO[Coordinator snapshots]
        CO --> E[Native entities]
        E --> UI[Local dashboard]
        UI --> PERM[HA device permission + confirmation]
        PERM --> API
        API --> Q[Accepted-command journal]
        Q --> POLL[Result polling only]
    end
    subgraph Cloud["Manufacturer service"]
        G[GWM Thailand gateways]
    end
    API <-->|verified HTTPS / issued mTLS| G
    POLL --> G
    subgraph Public["Public GitHub"]
        SRC[Source + synthetic tests + docs only]
    end
    SRC -. no owner material .-> Local
```

Private app provisioning is never a static frontend resource. Neither diagnostics nor entity attributes expose account credentials, VINs/locations from diagnostics, tokens or PINs. Vehicle identifiers are still needed locally for HA's device registry and private account journal.

Encrypting the generated device key is not a substitute for protecting the host: its unlocking material, access tokens, optional PIN and any account password are on the same HA installation.

## Login state machine

```mermaid
stateDiagram-v2
    [*] --> PrivateMaterial
    PrivateMaterial --> MethodMenu: valid reviewed provisioning
    MethodMenu --> CodeContact
    MethodMenu --> PasswordCredentials
    CodeContact --> Consent
    PasswordCredentials --> Consent
    Consent --> Verification: explicit owner approval
    Consent --> Login: password accepted without extra challenge
    Verification --> Login: one delivered code
    Login --> Enrollment: accepted account / bound device
    Enrollment --> Ready: validated matching issued certificate
    Enrollment --> ContinueInitialization: accepted state saved, certificate pending
    ContinueInitialization --> Enrollment: retry initialization, not account login
    Enrollment --> FreshDeviceConsent: existing certificate without returned data
    FreshDeviceConsent --> Verification: owner-approved new device / normal verification
    Ready --> Ready: cached reads / normal token renewal
    Ready --> MethodMenu: explicit owner reconnect, preserve bound identity
```

A forced owner reconnect deliberately bypasses an old access session but preserves its valid bound issued key/certificate. No old access token is attached to normal login/code-request headers. An accepted login for a different manufacturer account ID is rejected instead of reusing that certificate across accounts.

`607501` is not treated as ordinary refreshable token expiry. Updated-agreement response `570062` does not imply consent and is never accepted automatically.

## One-shot command sequence

```mermaid
sequenceDiagram
    actor Owner
    participant Card as Dashboard
    participant HA as HA service / permissions
    participant SDK as Thai client
    participant GWM as Manufacturer
    participant Store as Private journal
    Owner->>Card: choose action + confirm safety
    Card->>HA: one service call, confirmed=true
    HA->>HA: vehicle ownership / HA control permission
    HA->>SDK: opt-in + eligible fixed action + private PIN hash
    SDK->>GWM: read current vehicle capabilities
    GWM-->>SDK: allowed capabilities
    SDK->>GWM: verify vehicle security PIN
    GWM-->>SDK: PIN result
    SDK->>GWM: exactly one T5 command
    GWM-->>SDK: acceptance sequence
    SDK->>Store: persist pending acceptance
    loop bounded read-only polling
        HA->>GWM: query correlated result
        GWM-->>HA: pending / completed / failed
    end
    HA->>Store: persist terminal result or timeout
    HA-->>Card: outcome / unknown, never assumed success
```

Cancellation after acceptance does not intentionally discard the journal save. On restart, pending accepted sequences are queried without resending a physical command. A result is accepted only when its sequence and instruction type match the original request.

## Protocol boundaries

Public source contains protocol structure and artifact/certificate **fingerprints**, not app key/secret values or OEM private material.

| Purpose | Host |
| --- | --- |
| Account authentication / PIN verification | `ap-h5-gateway.gwmcloud.com` |
| Issued client-mTLS vehicle reads/commands | `ap-app-gateway.gwmcloud.com` |
| Certificate enrollment | `ap-app-gateway-common.gwmcloud.com` |

Reviewed request signing uses path-only native signing, Java form encoding and bounded ASCII-whitespace normalization. The local identity codec uses independently structured DER/bit decoding and standard `cryptography` RSA recovery/CRT APIs. No decompiled program or third-party key resource is bundled.

Only fixed known operations are allowed: normal password/code login, token refresh, enrollment, vehicle discovery, cached status, capabilities, PIN verification, one T5 command and correlated result reads. Arbitrary URLs, forced status wakes, guessed revocation and unsupported actuator endpoints are not exposed.

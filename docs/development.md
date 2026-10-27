# Development, build and release

## Checkout and stack

```text
git clone https://github.com/zangkasi55/gwm-thailand-connect.git
cd gwm-thailand-connect
python -m venv .venv
```

Activate the environment and install the declared development dependencies:

```text
python -m pip install -r requirements-dev.txt
npm ci
```

On Windows, the interpreter is `.\.venv\Scripts\python.exe`. On Unix, activate `.venv/bin/activate` first or invoke `.venv/bin/python` directly.

See [architecture](architecture.md) for the full tech-stack table. Home Assistant provides runtime HTTP and cryptography dependencies. The provisioning-only environment uses [requirements-provisioning.txt](../requirements-provisioning.txt).

## Layout

```text
custom_components/gwm_thailand_connect/
  config_flow.py          native material/login/options UI
  storage.py              private account auth and command journal
  api.py                  HA lifecycle, cached reads, guarded command bridge
  coordinator.py          update/error classification and bounded result polling
  sensor.py               measurements and command state
  binary_sensor.py        strict reported door/lock/A/C states
  frontend/               dashboard and locally bundled Leaflet/vector-map modules
  gwm_client/             Thai-only protocol/identity/client primitives
  brand/                  original neutral icon, no manufacturer logo
dashboards/               optional HA dashboard definition
docs/                     illustrated manuals and Mermaid diagrams
tests/                    synthetic protocol/adapter/frontend regressions
scripts/                  provisioning, scan, package and screenshot tools
```

## Run checks

```text
python -m pytest -q
python -m ruff check custom_components tests scripts
python -m mypy custom_components/gwm_thailand_connect
npm test
python scripts/check_public_tree.py
python scripts/build_release.py
```

HA must be imported before collection because the supported HA release initializes its schema-validation compatibility layer; `tests/conftest.py` does that.

Most tests use generated synthetic RSA keys/certificates and an in-memory scripted transport. **They need no account, APK, network, PIN or car.** The three opt-in APK checks skip unless a developer explicitly provides `GWM_THAILAND_TEST_APK` pointing to their own reviewed local artifact. Do not set that variable in public CI or upload the artifact.

## Reproduce manual screenshots

```text
npm ci
npx playwright install chromium
npm run docs:screenshots
```

On Windows the script defaults to an already installed Microsoft Edge. To use Playwright Chromium instead, set `PLAYWRIGHT_BROWSER_CHANNEL=chromium` after installing that browser.

The screenshot generator:

- starts a temporary loopback-only HTTP server and closes it in `finally`;
- uses an isolated browser context;
- blocks browser requests except the local fixture server;
- renders the real dashboard JS with synthetic states and a non-networking service stub;
- renders clearly labelled setup-form illustrations;
- draws an original neutral brand icon without copying an OEM logo or car photograph.

The setup illustrations explain the flow; they are not proof of actual native HA form rendering. Python tests assert the real schemas' method-specific fields and requiredness.

## Source-only release builder

`python scripts/build_release.py` runs the publication scanner and creates:

```text
dist/VERSION/gwm-thailand-connect-VERSION.zip
dist/VERSION/SHA256SUMS
```

The ZIP contains only the installable component, root MIT license/notice, HACS metadata and dashboard example. It deliberately excludes tests, dependency directories, APKs, generated material, captures, caches, private storage and old Git history. ZIP member timestamps/order are deterministic, and existing release files are not overwritten.

The source scanner rejects unexpected binaries/material files, full private-key blocks, common token formats, symlinks and accidental local/private payloads. It is an additional guard, **not a guarantee that any arbitrary string is safe**. Review every new file, and never rely only on `.gitignore`.

## Rebuild the offline regional map

```text
python scripts/generate_basemap.py --cache "<local-cache-directory>"
```

Shapely is declared in the development requirements only. The builder downloads
generic public-domain world datasets, clips the same fixed Thailand-region
extent for every installation and writes the bundled JSON. It never signs in,
reads a vehicle location or requests a tile for a car. The source URLs and
fingerprints are recorded in the asset. No map data is downloaded from an
external provider while the dashboard displays a vehicle.

## CI

- **Checks:** pytest, Ruff, strict mypy, Node frontend tests, public-tree scan and release packaging.
- **Hassfest:** official Home Assistant integration metadata validation.
- **HACS validation:** official `hacs/action`, with no ignored validation categories.
- **Copilot setup steps:** prepares Python 3.14, Node 24, the declared dependencies
  and the local map-runtime build for GitHub's cloud coding agent.

Do not mark a release usable merely because one job passed. All three workflows must be green.

## Continue in the cloud coding agent

The `cloud/compact-osm` handoff branch contains the in-progress compact
OpenStreetMap work. Keep it separate from the released `main` branch until review.
Use GitHub Copilot's cloud coding agent with that feature branch as the task base;
the setup workflow above prepares its isolated development environment.

The current map contract is:

- Locally served Leaflet/Protomaps runtime and a same-host generic OSM archive.
- No invented default location. Current GPS must have a valid manufacturer source
  timestamp within five minutes; old HTTP poll time does not make it current.
- Compact battery/lock/door/A/C status chips and marker popup; stop details are
  collapsed by default.
- Private history is opt-in and expires after seven rolling days.
- Parking requires observed stop, verified whole-vehicle shutdown, driver-door
  open and driver-door close in order. The Thai decoder still has no verified
  shutdown producer, so that input remains unknown.

Cloud development must use synthetic observations only. Do not copy a live HA
backup, browser profile, private app artifact, provisioning JSON or owner's
coordinates into the agent environment. The generic country map is provisioned
separately, not committed to this source repository.

The prepared custom-component update passed local unit/type/lint and isolated
browser checks. A private installation was updated and its configuration check
passed, but the latest compact-map restart/resource/UI verification was interrupted
when the owner paused work. That is an operator follow-up through an authorized
HA session, not a task the cloud agent can prove without access. Do not claim it
complete, connect to a real home endpoint, or send physical vehicle commands.

The agent should open a draft PR for review, report the exact checks it ran and
leave publication/merge/live deployment to the owner. Never claim that a cloud
task launch is a successful live installation.

## Maintainer release procedure

1. Run all local checks and review synthetic-only docs/screens.
2. Update `manifest.json`, `const.py` and resource-version examples together.
3. Commit only reviewed source with no old private history. Preserve MIT notices.
4. Push and wait for all three CI workflows to pass without ignored checks.
5. Create a full GitHub release, not only a tag, and attach the clean ZIP/checksums.
6. Smoke-check the public archive layout and documentation links.
7. For HACS default-list submission, follow [the current official requirements](https://www.hacs.xyz/docs/publish/include/). Add the repository alphabetically in a new branch based on `hacs/default`'s `master`; complete the template honestly.

HACS custom-repository compatibility, default-list submission and default-list acceptance are different milestones. This project is not a Home Assistant Core integration and must not be represented as such.

## Validation boundaries

Automated tests verify protocol shapes, signing vectors, strong TLS, identity binding, no silent key replacement, method-specific forms, private storage round trips, unknown-state behavior and guarded one-shot command logic.

They do not prove:

- current owner authentication or indefinite manufacturer API availability;
- real-world vehicle actuator behavior, safety or timing;
- full compatibility with every GWM model/trim;
- real-time location, historical reconstruction or exact door-open timing;
- readiness for Home Assistant Core inclusion.

Never run a physical vehicle test merely to make CI pass. Such testing requires separate explicit authorization, an appropriate safe environment and accurate reporting.

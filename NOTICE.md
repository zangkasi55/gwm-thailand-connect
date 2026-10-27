# License, provenance and distribution boundary

## Upstream MIT work

This project includes and adapts MIT-licensed Python helpers from
[moryoav/ha-gwm-ev](https://github.com/moryoav/ha-gwm-ev).
The original copyright notice, **Copyright (c) 2026 Yoav Mor**, and MIT terms are retained in [LICENSE](LICENSE).

The Thai-only SDK contains adapted model/snapshot/error/transport helpers and selected neutral request/decode/timeout contracts from that project, together with independently developed Thai integration/protocol work. This repository starts with a new, source-only history; it is not a distribution of the full original development checkout.

## Excluded resources

No original APK, native/DEX binary, decompiled program, raw capture, application secret, OEM bootstrap private key, manufacturer certificate resource, account-issued identity, owner image or live account/vehicle data is licensed or distributed here.

Protocol-compatible development requires the developer's own lawful local reviewed app artifact and private provisioning. This project cannot grant rights in GWM's app, services, brand, certificates or other third-party materials. Local extraction/use must comply with applicable law and terms.

The independently authored identity codec in `gwm_client/crypto.py` is structured around standard `cryptography` RSA factor-recovery and CRT APIs. It does not bundle a third-party key-recovery implementation or decompiled native code.

## Images and diagrams

- The vehicle outline is original CSS.
- The neutral integration icon is an original canvas drawing, not a GWM/Haval logo.
- Manual screenshots are generated from synthetic fixtures in this repository and prominently labelled as demonstrations.
- Architecture/state/sequence diagrams are project-authored Mermaid source.
- No original owner-provided car artwork or screenshot of a live account is included.

## Offline regional map

The 0.3.0 journey interface uses locally bundled Leaflet (BSD-2-Clause),
Protomaps Leaflet (BSD-3-Clause) and a separately provisioned OSM-derived vector
archive. Complete bundled runtime notices are in `frontend/vendor/LICENSES.txt`.
OSM data/Produced Works must visibly credit OpenStreetMap contributors and comply
with [ODbL](https://www.openstreetmap.org/copyright). They are not covered by this
repository's MIT source-code license. No third-party tile requests carry owner
coordinates. The documentation preview uses fictional positions over generic
OSM data with visible attribution; it is not an owner screenshot.

The earlier regional-context asset below remains for compatibility; it is not
mislabelled as the new OpenStreetMap street map.

The bundled `frontend/thailand-basemap.json` is clipped/generalized from Natural
Earth's public-domain 1:10m countries, roads and populated-places data, covering
the fixed generic region 96-108 degrees east / 4-23 degrees north. No owner
location is used to download or generate it.

Made with Natural Earth. See the [public-domain terms](https://www.naturalearthdata.com/about/terms-of-use/).
Source URLs and SHA-256 fingerprints are embedded in the data asset. This is
regional geographic context, not a navigation or street/building dataset.
`scripts/generate_basemap.py` reproduces it locally using Shapely, which is a
development dependency only; HA does not need Shapely to display the result.

## Names and trademarks

GWM, Haval, My GWM, Home Assistant, HACS and other names identify interoperability targets and remain the property of their respective owners. No affiliation or endorsement is implied.

The MIT license covers this repository's licensed software, not rights to third-party services, app material, accounts or vehicles.

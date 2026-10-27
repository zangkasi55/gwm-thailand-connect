# Local OpenStreetMap

The journey view follows the same architecture as a conventional Leaflet tracker:
a recent-location marker, a trail and status popup. All JavaScript/CSS is served
by HA. Unlike an external raster tile layer, it reads a **local OSM-derived vector
archive**, so private coordinates and tile ranges stay on your own HA host.

## Country map setup

Obtain a lawful PMTiles v3 basemap compatible with the Protomaps schema. You may
use your own source or [Protomaps' published generic builds](https://maps.protomaps.com/).
Use the official [go-pmtiles](https://github.com/protomaps/go-pmtiles) extractor,
verify its published release checksum, then extract a fixed country extent:

```text
pmtiles extract <generic-planet-build.pmtiles-URL> thailand.pmtiles --bbox=97.3,5.4,105.7,20.5 --maxzoom=13 --download-threads=2
```

This uses a bulk-download-capable **vector archive**, not the OSM Foundation's
public raster service. Never scrape or prefetch `tile.openstreetmap.org` for
offline use. Follow the source provider's terms and ODbL obligations.

Place the result at:

```text
<HA configuration>/www/gwm-maps/thailand.pmtiles
```

The default card reads `/local/gwm-maps/thailand.pmtiles`. HA must support
HTTP byte ranges (206); its native static-file serving does. Preserve the map's
source, build date, extent, hash and licensing metadata alongside it. The
development reference country extract is approximately 245 MB, with source
detail through zoom 13; higher display zooms overzoom that data, not invent detail.

The archive is generic public geographic data and does **not** contain the
vehicle's route, coordinates or credentials. Actual observations stay in private
HA storage and are read only through the authenticated integration endpoint.

## Card options

```yaml
type: custom:gwm-journey-card
map_archive: /local/gwm-maps/thailand.pmtiles
```

For the installed private predecessor, also specify `domain: gwm_ora`. The archive
must be a same-host `/local/` path. External URLs and path traversal are rejected.

An existing local raster XYZ source is also supported:

```yaml
type: custom:gwm-journey-card
local_tile_url: /local/offline-osm/{z}/{x}/{y}.png
```

Use an OSM-derived source with appropriate permission and attribution. Do not
silently point it at public third-party tiles.

## No recent location

There is **no default car position**. Without a recent valid manufacturer fix,
the marker and current status are blank. If there are also no historical
observations, the map remains blank and ready for the first real position.
No vehicle wake, sign-in retry or physical command is sent by viewing the map.

## Attribution and dependencies

Visible attribution links to OpenStreetMap contributors/ODbL and identifies
Protomaps. OSM map content is not licensed under the source-code MIT license.
Leaflet, Protomaps and transitive runtime license notices are preserved in
`frontend/vendor/LICENSES.txt`. No code from the owner's separate Guardian
project is distributed here.

Rebuild local vendor assets with `npm ci` followed by `npm run build:map`.
To exercise the real renderer in isolated browser tests, set
`GWM_OSM_TEST_ARCHIVE` to a generic local test archive and run
`npm run docs:journeys`. The test server supports range requests and blocks
all browser requests except loopback.

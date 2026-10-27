"""Build public-domain regional context offline; never request tiles for a vehicle."""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

from shapely import make_valid
from shapely.geometry import box, mapping, shape

BOUNDS = (96, 4, 108, 23)
DATASETS = {
    "countries": "ne_10m_admin_0_countries.geojson",
    "roads": "ne_10m_roads.geojson",
    "places": "ne_10m_populated_places_simple.geojson",
}
SOURCE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/"


def rounded(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, list | tuple):
        return [rounded(item) for item in value]
    if isinstance(value, dict):
        return {key: rounded(item) for key, item in value.items()}
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", required=True, type=Path, help="Local cache for generic world source data, no account data")
    args = parser.parse_args()
    args.cache.mkdir(parents=True, exist_ok=True)
    region = box(*BOUNDS)
    result: dict[str, Any] = {
        "type": "RegionalBasemap", "license": "Public Domain", "attribution": "Made with Natural Earth",
        "terms": "https://www.naturalearthdata.com/about/terms-of-use/",
        "bounds": list(BOUNDS), "countries": [], "roads": [], "places": [], "sources": [],
        "accuracy": "Generalized regional context, not street-level navigation",
    }
    for group, filename in DATASETS.items():
        cached = args.cache / filename
        if not cached.exists():
            request = urllib.request.Request(SOURCE + filename, headers={"User-Agent": "GWM-Thailand-Connect-offline-map-builder"})
            with urllib.request.urlopen(request, timeout=120) as response:
                data = response.read(64 * 1024 * 1024 + 1)
            if len(data) > 64 * 1024 * 1024:
                raise ValueError("Basemap source exceeds the fixed download bound")
            document = json.loads(data)
            if document.get("type") != "FeatureCollection":
                raise ValueError("Unexpected basemap source")
            cached.write_bytes(data)
        data = cached.read_bytes()
        document = json.loads(data)
        result["sources"].append({"url": SOURCE + filename, "sha256": hashlib.sha256(data).hexdigest()})
        for feature in document["features"]:
            if feature["geometry"] is None:
                continue
            geometry = shape(feature["geometry"])
            if geometry.is_empty or not region.intersects(geometry):
                continue
            properties = {key.casefold(): value for key, value in feature["properties"].items()}
            name = properties.get("name_en") or properties.get("name") or properties.get("admin") or ""
            if not isinstance(name, str):
                raise ValueError("Invalid basemap label")
            name = name[:80]
            if group == "places":
                result["places"].append({
                    "name": name, "longitude": geometry.x, "latitude": geometry.y,
                    "rank": properties.get("scalerank", 100),
                })
                continue
            if not geometry.is_valid:
                geometry = make_valid(geometry)
            clipped = geometry.intersection(region).simplify(0.001, preserve_topology=True)
            accepted = {"Polygon", "MultiPolygon"} if group == "countries" else {"LineString", "MultiLineString"}
            if clipped.is_empty or clipped.geom_type not in accepted:
                continue
            result[group].append({"name": name, "geometry": mapping(clipped)})
        print(f"Regional {group}: {len(result[group])} features")
    result["places"].sort(key=lambda item: (item["rank"], item["name"]))
    if not all(result[group] for group in DATASETS):
        raise ValueError("Regional basemap layers must not be empty")
    output = Path(__file__).resolve().parents[1] / "custom_components" / "gwm_thailand_connect" / "frontend" / "thailand-basemap.json"
    encoded = json.dumps(rounded(result), ensure_ascii=True, separators=(",", ":")) + "\n"
    if len(encoded.encode()) > 2 * 1024 * 1024:
        raise ValueError("Compiled regional basemap exceeds the publication size limit")
    output.write_text(encoded, encoding="utf-8")
    print(f"Built local regional basemap: {len(encoded.encode())} bytes; no vehicle/account data used")


if __name__ == "__main__":
    main()

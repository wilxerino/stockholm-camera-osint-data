"""Fetch bounded public listings into review candidates; never edit production data."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from camera_data import DATA, stable_id, write_json, read_json


TRAFIKEN_PAGE = "https://trafiken.nu/stockholm/kameror/"
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
)
USER_AGENT = "MosaicCameraOSINT/1.0 (public-source research; low-frequency scheduled fetch)"
TRAFIKEN_SOURCE_ID = "trafiken-nu-stockholm-camera-list"
TRAFIKEN_FAQ_ID = "trafiken-nu-public-camera-faq"
OSM_SOURCE_ID = "openstreetmap-stockholm-surveillance"
TRAFIKEN_SOURCE = {
    "id": TRAFIKEN_SOURCE_ID,
    "url": TRAFIKEN_PAGE,
    "title": "Trafiken.nu Stockholm public camera listing",
    "publisher": "Trafiken.nu",
    "published_date": None,
    "retrieved_date": None,
    "source_type": "other",
    "reliability": "high",
    "license": "Trafikverket API information CC0; Trafiken.nu page facts attributed separately",
    "public_feed_documentation": True,
}
TRAFIKEN_FAQ = {
    "id": TRAFIKEN_FAQ_ID,
    "url": "https://trafiken.nu/stockholm/vanliga-fragor/",
    "title": "Trafiken.nu Stockholm camera image FAQ",
    "publisher": "Trafiken.nu",
    "published_date": None,
    "retrieved_date": None,
    "source_type": "other",
    "reliability": "high",
    "license": "Public documentation; metadata attribution retained",
    "public_feed_documentation": True,
}
OSM_SOURCE = {
    "id": OSM_SOURCE_ID,
    "url": "https://www.openstreetmap.org/copyright",
    "title": "OpenStreetMap mapped surveillance and speed cameras",
    "publisher": "OpenStreetMap contributors",
    "published_date": None,
    "retrieved_date": None,
    "source_type": "open_data",
    "reliability": "medium",
    "license": "Data © OpenStreetMap contributors; Open Database License 1.0 (ODbL) https://opendatacommons.org/licenses/odbl/1-0/",
    "public_feed_documentation": False,
}


def fetch(url, data=None):
    request = Request(url, data=data, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html"})
    with urlopen(request, timeout=35) as response:
        payload = response.read(12_000_001)
    if len(payload) > 12_000_000:
        raise ValueError("public source response too large")
    return payload


def trafiken_rows(html):
    marker = "React.createElement(Components.CameraMenu, "
    offset = html.find(marker)
    if offset < 0:
        raise ValueError("Trafiken.nu camera listing format changed")
    decoder = json.JSONDecoder()
    obj, _ = decoder.raw_decode(html[offset + len(marker):])
    rows = obj.get("cameras") if isinstance(obj, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Trafiken.nu camera listing missing cameras")
    return rows


def trafiken_candidate(row, checked):
    native_id = row.get("cameraId")
    coords = row.get("wgs84Position", {}).get("coordinates")
    if not isinstance(native_id, str) or not native_id or not isinstance(coords, list) or len(coords) != 2:
        return None
    longitude, latitude = coords
    if type(latitude) not in (float, int) or type(longitude) not in (float, int):
        return None
    image_url, page_url = row.get("imageUrl"), row.get("url")
    if not isinstance(image_url, str) or not image_url.startswith("https://"):
        return None
    if isinstance(page_url, str) and page_url.startswith("/"):
        page_url = "https://trafiken.nu" + page_url
    if not isinstance(page_url, str) or not page_url.startswith("https://trafiken.nu/"):
        page_url = TRAFIKEN_PAGE
    raw_direction = row.get("direction")
    direction = raw_direction if type(raw_direction) in (int, float) and 0 <= raw_direction < 360 else None
    return {
        "id": stable_id(TRAFIKEN_SOURCE_ID, native_id),
        "source_native_id": native_id,
        "primary_source_id": TRAFIKEN_SOURCE_ID,
        "source_url": page_url,
        "latitude": latitude,
        "longitude": longitude,
        "type": "traffic",
        "operator": None,
        "status": "unknown",
        "orientation": direction,
        "horizontal_fov": None,
        "range_m": None,
        "geometry_confidence": "medium" if direction is not None else "unknown",
        "geometry_provenance": "documented" if direction is not None else "unknown",
        **({"geometry_source_ids": [TRAFIKEN_SOURCE_ID]} if direction is not None else {}),
        "viewable": True,
        "feed": {"type": "image", "url": image_url, "access": "public", "evidence_source_id": TRAFIKEN_FAQ_ID},
        "source_ids": [TRAFIKEN_SOURCE_ID, TRAFIKEN_FAQ_ID],
        "confidence": "pending_review",
        "last_verified": checked,
        "name": row.get("name") if isinstance(row.get("name"), str) else None,
        "road": row.get("road") if isinstance(row.get("road"), str) else None,
    }


def discover_trafiken(checked):
    rows = trafiken_rows(fetch(TRAFIKEN_PAGE).decode("utf-8"))
    cameras = {}
    for row in rows:
        camera = trafiken_candidate(row, checked)
        if camera is None:
            continue
        prior = cameras.get(camera["id"])
        if prior is not None:
            if (prior["latitude"], prior["longitude"], prior["feed"]["url"]) != (camera["latitude"], camera["longitude"], camera["feed"]["url"]):
                raise ValueError(f"conflicting duplicate Trafiken camera ID {camera['source_native_id']}")
            continue
        cameras[camera["id"]] = camera
    sources = [dict(TRAFIKEN_SOURCE, retrieved_date=checked), dict(TRAFIKEN_FAQ, retrieved_date=checked)]
    return {"schema_version": "1.0", "source": TRAFIKEN_SOURCE_ID, "scope": "Stockholm traffic region as defined by Trafiken.nu listing", "sources": sources, "candidates": sorted(cameras.values(), key=lambda item: item["id"])}


def osm_direction(tags):
    # For a surveillance camera, camera:direction documents lens bearing.
    # Speed camera direction often means monitored traffic direction, not lens bearing.
    value = tags.get("camera:direction") if tags.get("highway") != "speed_camera" else None
    if not isinstance(value, str):
        return None
    directions = {"N": 0, "NE": 45, "E": 90, "SE": 135, "S": 180, "SW": 225, "W": 270, "NW": 315}
    cleaned = value.strip().upper()
    if cleaned in directions:
        return directions[cleaned]
    if re.fullmatch(r"\d+(?:\.\d+)?", cleaned):
        number = float(cleaned)
        return number if 0 <= number < 360 else None
    return None


def osm_candidate(row, checked):
    if row.get("type") != "node" or type(row.get("id")) is not int:
        return None
    tags = row.get("tags", {})
    if not isinstance(tags, dict) or not (tags.get("highway") == "speed_camera" or (tags.get("man_made") == "surveillance" and tags.get("surveillance:type") == "camera")):
        return None
    lat, lon = row.get("lat"), row.get("lon")
    if type(lat) not in (float, int) or type(lon) not in (float, int):
        return None
    native_id = f"node/{row['id']}"
    direction = osm_direction(tags)
    return {
        "id": stable_id(OSM_SOURCE_ID, native_id),
        "source_native_id": native_id,
        "primary_source_id": OSM_SOURCE_ID,
        "source_url": f"https://www.openstreetmap.org/node/{row['id']}",
        "latitude": lat,
        "longitude": lon,
        "type": "traffic" if tags.get("highway") == "speed_camera" else "other",
        "operator": tags.get("operator") or None,
        "status": "unknown",
        "orientation": direction,
        "horizontal_fov": None,
        "range_m": None,
        "geometry_confidence": "low" if direction is not None else "unknown",
        "geometry_provenance": "approximate" if direction is not None else "unknown",
        **({"geometry_source_ids": [OSM_SOURCE_ID]} if direction is not None else {}),
        "viewable": False,
        "feed": None,
        "source_ids": [OSM_SOURCE_ID],
        "confidence": "pending_review",
        "last_verified": checked,
        "name": tags.get("name") or None,
    }


def discover_osm(checked, bbox):
    south, west, north, east = bbox
    if not (58.5 <= south < north <= 60.5 and 17 <= west < east <= 20):
        raise ValueError("OSM query must stay inside bounded Stockholm region")
    box = ",".join(str(item) for item in bbox)
    query = f'[out:json][timeout:35];(node["man_made"="surveillance"]["surveillance:type"="camera"]({box});node["highway"="speed_camera"]({box}););out body;'
    failures = []
    response = None
    for endpoint in OVERPASS_URLS:
        try:
            response = json.loads(fetch(endpoint, urlencode({"data": query}).encode("utf-8")))
            break
        except Exception as error:
            failures.append(f"{endpoint}: {error}")
    if response is None:
        raise ValueError("all public Overpass endpoints unavailable: " + "; ".join(failures))
    rows = response.get("elements")
    if not isinstance(rows, list):
        raise ValueError("Overpass returned no elements array")
    cameras = {}
    for row in rows:
        camera = osm_candidate(row, checked)
        if camera is not None:
            cameras[camera["id"]] = camera
    source = dict(OSM_SOURCE, retrieved_date=checked)
    return {"schema_version": "1.0", "source": OSM_SOURCE_ID, "scope": {"bbox_south_west_north_east": bbox}, "sources": [source], "candidates": sorted(cameras.values(), key=lambda item: item["id"])}


def update_history(source_id, checked, count=None, error=None):
    path = DATA / "discovery-history.json"
    history = read_json(path)
    entry = next((item for item in history if item.get("source_id") == source_id), None)
    if entry is None:
        entry = {"source_id": source_id, "discovered_at": checked, "status": "pending_review", "accepted_count": 0, "failure_history": []}
        history.append(entry)
    entry["last_checked"] = checked
    if error:
        entry["failure_history"].append({"date": checked, "reason": str(error)[:500]})
        entry["failure_history"] = entry["failure_history"][-20:]
    else:
        entry["candidate_count"] = count
    write_json(path, sorted(history, key=lambda item: item["source_id"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", choices=("trafiken", "osm"))
    parser.add_argument("--bbox", nargs=4, type=float, default=[59.20, 17.75, 59.48, 18.35], metavar=("S", "W", "N", "E"))
    args = parser.parse_args()
    checked = datetime.now(timezone.utc).date().isoformat()
    source_id = TRAFIKEN_SOURCE_ID if args.source == "trafiken" else OSM_SOURCE_ID
    try:
        envelope = discover_trafiken(checked) if args.source == "trafiken" else discover_osm(checked, args.bbox)
        from camera_data import validate_source, validate_camera
        sources = {source["id"]: source for source in envelope["sources"]}
        for source in sources.values():
            validate_source(source)
        for candidate in envelope["candidates"]:
            validate_camera(candidate, sources, allow_pending=True)
        output = DATA / "candidates" / f"{args.source}.json"
        write_json(output, envelope)
        update_history(source_id, checked, len(envelope["candidates"]))
        print(f"{args.source}: {len(envelope['candidates'])} candidates -> {output}")
    except Exception as error:
        update_history(source_id, checked, error=error)
        print(f"{args.source} discovery failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()

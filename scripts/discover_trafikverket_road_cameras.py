"""Collect Stockholm road-weather and traffic cameras from Trafikverket data pages.

The pages expose structured records from Trafikverket's public CC0 traffic API.
Only page metadata is fetched; camera images are never requested by this adapter.
All findings remain pending review and are reconciled against production data.
"""

from __future__ import annotations

import copy
import json
import math
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from camera_data import DATA, read_json, stable_id, validate_camera, validate_source, write_json
from discover_cameras import update_history


INDEX_URL = "https://fartkameran.se/vagvader/stockholm-kommun/"
API_SOURCE_ID = "trafikverket-open-traffic-api"
DIRECTORY_SOURCE_ID = "fartkameran-stockholm-road-weather-directory"
USER_AGENT = "MosaicCameraOSINT/1.0 (bounded weekly public metadata research)"
MAX_PAGE_BYTES = 1_000_000
MAX_LOCATION_PAGES = 150

API_SOURCE = {
    "id": API_SOURCE_ID,
    "url": "https://www.trafikverket.se/e-tjanster/trafikverkets-oppna-api-for-trafikinformation/",
    "title": "Trafikverket open traffic information API",
    "publisher": "Trafikverket",
    "published_date": None,
    "retrieved_date": None,
    "source_type": "government",
    "reliability": "high",
    "license": "Creative Commons CC0 1.0 (Trafikverket's API registration and VViS data specification)",
    "public_feed_documentation": False,
}
DIRECTORY_SOURCE = {
    "id": DIRECTORY_SOURCE_ID,
    "url": INDEX_URL,
    "title": "Stockholm road-weather and traffic camera directory",
    "publisher": "Fartkameran.se (records attributed to Trafikverket's API)",
    "published_date": None,
    "retrieved_date": None,
    "source_type": "other",
    "reliability": "medium",
    "license": "Trafikverket source records are CC0 1.0 as documented by Trafikverket; directory provides per-camera metadata and publicly viewable image URLs",
    "public_feed_documentation": True,
}


def fetch_page(url: str) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"})
    with urlopen(request, timeout=25) as response:
        payload = response.read(MAX_PAGE_BYTES + 1)
    if len(payload) > MAX_PAGE_BYTES:
        raise ValueError(f"public metadata page too large: {url}")
    return payload.decode("utf-8", "replace")


def location_paths(html: str) -> list[str]:
    paths = sorted(set(re.findall(r'href="(/vagkamera/[^"]+)"', html)))
    if not paths or len(paths) > MAX_LOCATION_PAGES:
        raise ValueError(f"unexpected Stockholm location page count: {len(paths)}")
    return paths


def cameras_from_page(html: str) -> list[dict]:
    """Decode the page's embedded Next.js data without fetching image URLs."""
    decoder = json.JSONDecoder()
    cameras: dict[str, dict] = {}
    for script in re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL):
        if "__next_f.push" not in script:
            continue
        match = re.search(r"__next_f\.push\((.*)\);?", script, re.DOTALL)
        if not match:
            continue
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, list) or len(payload) < 2 or not isinstance(payload[1], str):
            continue
        data = payload[1]
        cursor = 0
        while True:
            marker = '"kameror":'
            offset = data.find(marker, cursor)
            if offset < 0:
                break
            cursor = offset + len(marker)
            try:
                rows, _ = decoder.raw_decode(data[cursor:].lstrip())
            except json.JSONDecodeError:
                continue
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                    continue
                prior = cameras.get(row["id"])
                if prior is not None and prior != row:
                    raise ValueError(f"conflicting Trafikverket camera ID: {row['id']}")
                cameras[row["id"]] = row
    return list(cameras.values())


def is_number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def make_candidate(row: dict, page_url: str, checked: str, existing: dict | None) -> dict | None:
    native_id = row.get("id")
    latitude, longitude = row.get("lat"), row.get("lon")
    if not isinstance(native_id, str) or not native_id or not is_number(latitude) or not is_number(longitude):
        return None
    if not (-90 <= latitude <= 90 and -180 <= longitude <= 180) or row.get("borttagen") is True:
        return None

    if existing is not None:
        candidate = copy.deepcopy(existing)
        candidate["source_ids"] = sorted(set(candidate["source_ids"] + [API_SOURCE_ID, DIRECTORY_SOURCE_ID]))
        candidate["latitude"] = latitude
        candidate["longitude"] = longitude
        candidate["source_url"] = page_url
        candidate["last_verified"] = checked
        candidate["operator"] = "Trafikverket"
        if not candidate.get("name") and isinstance(row.get("namn"), str):
            candidate["name"] = row["namn"]
    else:
        candidate = {
            "id": stable_id(API_SOURCE_ID, native_id),
            "source_native_id": native_id,
            "primary_source_id": API_SOURCE_ID,
            "source_url": page_url,
            "latitude": latitude,
            "longitude": longitude,
            "type": "road_monitoring" if "Väglagskamera" in str(row.get("typ")) else "traffic",
            "operator": "Trafikverket",
            "status": "unknown",
            "orientation": None,
            "horizontal_fov": None,
            "range_m": None,
            "geometry_confidence": "unknown",
            "geometry_provenance": "unknown",
            "viewable": False,
            "feed": None,
            "source_ids": [API_SOURCE_ID, DIRECTORY_SOURCE_ID],
            "confidence": "pending_review",
            "last_verified": checked,
        }
        if isinstance(row.get("namn"), str) and row["namn"].strip():
            candidate["name"] = row["namn"].strip()

    bearing = row.get("riktning_grader")
    if is_number(bearing) and 0 <= bearing < 360:
        candidate["orientation"] = bearing
        candidate["geometry_confidence"] = "medium"
        candidate["geometry_provenance"] = "documented"
        candidate["geometry_source_ids"] = sorted(set(candidate.get("geometry_source_ids", []) + [DIRECTORY_SOURCE_ID]))
    elif existing is None:
        candidate["orientation"] = None

    camera_type = row.get("typ")
    if isinstance(camera_type, str) and "Väglagskamera" in camera_type:
        candidate["type"] = "road_monitoring"
    elif isinstance(camera_type, str) and "Trafikflödeskamera" in camera_type:
        candidate["type"] = "traffic"

    if row.get("aktiv") is True:
        candidate["status"] = "active"
    image_url = row.get("foto_url")
    if row.get("aktiv") is True and isinstance(image_url, str) and image_url.startswith("https://api.trafikinfo.trafikverket.se/"):
        candidate["viewable"] = True
        candidate["feed"] = {
            "type": "image",
            "url": image_url,
            "access": "public",
            "evidence_source_id": DIRECTORY_SOURCE_ID,
        }
    elif existing is None:
        candidate["viewable"] = False
        candidate["feed"] = None
    return candidate


def discover(delay: float = 0.25) -> dict:
    checked = datetime.now(timezone.utc).date().isoformat()
    api_source = dict(API_SOURCE, retrieved_date=checked)
    directory_source = dict(DIRECTORY_SOURCE, retrieved_date=checked)
    validate_source(api_source)
    validate_source(directory_source)

    existing_cameras = read_json(DATA / "cameras.json")
    existing_by_native = {}
    for camera in existing_cameras:
        native_id = camera.get("source_native_id")
        if isinstance(native_id, str):
            existing_by_native.setdefault(native_id, camera)

    listing = fetch_page(INDEX_URL)
    paths = location_paths(listing)
    found: dict[str, dict] = {}
    for index, path in enumerate(paths):
        if index:
            time.sleep(delay)
        page_url = urljoin(INDEX_URL, path)
        rows = cameras_from_page(fetch_page(page_url))
        for row in rows:
            candidate = make_candidate(row, page_url, checked, existing_by_native.get(row.get("id")))
            if candidate is None:
                continue
            identifier = candidate["id"]
            prior = found.get(identifier)
            if prior is not None and prior != candidate:
                raise ValueError(f"conflicting Stockholm camera metadata for {row.get('id')}")
            found[identifier] = candidate

    sources = [api_source, directory_source]
    source_index = {source["id"]: source for source in sources}
    all_sources = read_json(DATA / "sources.json")
    source_index.update({source["id"]: source for source in all_sources})
    candidates = sorted(found.values(), key=lambda camera: camera["id"])
    for candidate in candidates:
        validate_camera(candidate, source_index, allow_pending=True)
    return {
        "schema_version": "1.0",
        "source": API_SOURCE_ID,
        "scope": "Trafikverket road-weather and traffic camera objects listed for Stockholm municipality",
        "sources": sources,
        "candidates": candidates,
    }


if __name__ == "__main__":
    checked = datetime.now(timezone.utc).date().isoformat()
    try:
        result = discover()
        output = DATA / "candidates" / "trafikverket-road-cameras.json"
        write_json(output, result)
        update_history(API_SOURCE_ID, checked, len(result["candidates"]))
        print(f"{len(result['candidates'])} Trafikverket road-camera candidates -> {output}")
    except Exception as error:
        update_history(API_SOURCE_ID, checked, error=error)
        raise SystemExit(f"Trafikverket road-camera discovery failed: {error}") from error

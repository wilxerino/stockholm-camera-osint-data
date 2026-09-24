"""Discover published Stockholm County traffic-safety camera locations.

The listing attributes coordinates to Trafikverket's CC0 data. Each public
camera page is read at a bounded rate; output remains pending review.
"""

import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

from camera_data import DATA, stable_id, validate_camera, validate_source, write_json
from discover_cameras import fetch, update_history

LIST_URL = "https://fartkameran.se/kameror/lan/stockholms-lan/"
SOURCE_ID = "fartkameran-stockholm-safety-list"


def page_links(html):
    start = html.find("Samtliga kameror i <!-- -->Stockholms län")
    if start < 0:
        raise ValueError("speed-camera listing format changed")
    end = html.find("</section>", start)
    if end < 0:
        raise ValueError("speed-camera listing is incomplete")
    paths = set(re.findall(r'href="(/kameror/[^"/]+/[^"/]+/)"', html[start:end]))
    if not paths:
        raise ValueError("speed-camera listing has no camera pages")
    return sorted(urljoin(LIST_URL, path) for path in paths)


class StructuredData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = False
        self.parts = []
        self.blocks = []

    def handle_starttag(self, tag, attrs):
        if tag == "script" and dict(attrs).get("type") == "application/ld+json":
            self.active = True
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self.active:
            self.blocks.append(json.loads("".join(self.parts)))
            self.active = False


def camera_from_page(url, html, checked):
    parser = StructuredData()
    parser.feed(html)
    places = [item for block in parser.blocks for item in block.get("@graph", [])
              if item.get("@type") == "Place" and isinstance(item.get("geo"), dict)]
    if len(places) != 1:
        raise ValueError(f"expected one mapped camera place: {url}")
    place = places[0]
    geo = place["geo"]
    native_id = url.rstrip("/").rsplit("-", 1)[-1]
    if not re.fullmatch(r"[0-9]{5,}", native_id):
        raise ValueError(f"camera page has no stable native ID: {url}")
    name = place.get("name")
    if isinstance(name, str):
        name = name.removeprefix("Trafiksäkerhetskamera ")
    return {
        "id": stable_id(SOURCE_ID, native_id), "source_native_id": native_id,
        "primary_source_id": SOURCE_ID, "source_url": url,
        "latitude": geo["latitude"], "longitude": geo["longitude"],
        "type": "traffic", "operator": None, "status": "unknown",
        "orientation": None, "horizontal_fov": None, "range_m": None,
        "geometry_confidence": "unknown", "geometry_provenance": "unknown",
        "viewable": False, "feed": None, "source_ids": [SOURCE_ID],
        "confidence": "pending_review", "last_verified": checked,
        "name": name if isinstance(name, str) else None,
    }


def discover(delay=0.35):
    checked = datetime.now(timezone.utc).date().isoformat()
    source = {
        "id": SOURCE_ID, "url": LIST_URL,
        "title": "Stockholm County traffic safety camera listing",
        "publisher": "Fartkameran.se (Trafikverket data)",
        "published_date": None, "retrieved_date": checked,
        "source_type": "other", "reliability": "medium",
        "license": "Camera coordinates attributed to Trafikverket CC0; link each publisher page",
        "public_feed_documentation": False,
    }
    validate_source(source)
    links = page_links(fetch(LIST_URL).decode("utf-8"))
    candidates = []
    for index, url in enumerate(links):
        if index:
            time.sleep(delay)
        camera = camera_from_page(url, fetch(url).decode("utf-8"), checked)
        validate_camera(camera, {SOURCE_ID: source}, allow_pending=True)
        candidates.append(camera)
    if len({camera["id"] for camera in candidates}) != len(candidates):
        raise ValueError("duplicate speed-camera native IDs")
    return {"schema_version": "1.0", "source": SOURCE_ID,
            "scope": "Stockholm County as listed by publisher", "sources": [source],
            "candidates": sorted(candidates, key=lambda camera: camera["id"])}


if __name__ == "__main__":
    checked = datetime.now(timezone.utc).date().isoformat()
    try:
        result = discover()
        output = DATA / "candidates" / "safety-cameras.json"
        write_json(output, result)
        update_history(SOURCE_ID, checked, len(result["candidates"]))
        print(f"{len(result['candidates'])} speed-camera candidates -> {output}")
    except Exception as error:
        update_history(SOURCE_ID, checked, error=error)
        raise SystemExit(f"speed-camera discovery failed: {error}") from error

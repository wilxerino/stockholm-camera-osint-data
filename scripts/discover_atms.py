"""Discover mapped Stockholm ATM locations as review candidates, not camera facts."""

import json
from datetime import datetime, timezone
from urllib.parse import urlencode

from camera_data import DATA, read_json, validate_atms, write_json
from discover_cameras import OVERPASS_URLS, fetch, update_history

SOURCE_ID = "openstreetmap-stockholm-atms"
BBOX = (59.15, 17.75, 59.48, 18.35)


def normalize(rows):
    atms = {}
    for row in rows:
        tags = row.get("tags", {})
        if tags.get("amenity") != "atm" and tags.get("atm") != "yes":
            continue
        kind, native = row.get("type"), row.get("id")
        if kind not in {"node", "way", "relation"} or not isinstance(native, int):
            continue
        point = row if kind == "node" else row.get("center", {})
        lat, lon = point.get("lat"), point.get("lon")
        if type(lat) not in (int, float) or type(lon) not in (int, float):
            continue
        identifier = f"atm-osm-{kind}-{native}"
        atms[identifier] = {
            "id": identifier, "latitude": lat, "longitude": lon,
            "name": tags.get("name") or tags.get("brand"),
            "operator": tags.get("operator") or tags.get("brand"),
            "source_url": f"https://www.openstreetmap.org/{kind}/{native}",
            "source_ids": [SOURCE_ID],
            "camera_presence": "documented" if tags.get("surveillance:type") == "camera" else "unknown",
        }
    return sorted(atms.values(), key=lambda item: item["id"])


def discover():
    box = ",".join(map(str, BBOX))
    query = f'[out:json][timeout:90];(nwr["amenity"="atm"]({box});nwr["atm"="yes"]({box}););out center tags;'
    failures = []
    for endpoint in OVERPASS_URLS:
        try:
            rows = json.loads(fetch(endpoint, urlencode({"data": query}).encode()))["elements"]
            atms = normalize(rows)
            if not atms:
                raise ValueError("ATM source returned no mapped locations")
            sources = {item["id"]: item for item in read_json(DATA / "sources.json")}
            validate_atms(atms, sources)
            return atms
        except Exception as error:
            failures.append(str(error))
    raise ValueError("all public ATM discovery endpoints failed: " + "; ".join(failures))


def reconcile(current, candidates):
    old = {item["id"]: item for item in current}
    new = {item["id"]: item for item in candidates}
    changes = []
    for identifier in sorted(old.keys() | new.keys()):
        before, after = old.get(identifier), new.get(identifier)
        status = "ADDED" if before is None else "UNCHANGED" if after is None or before == after else "MODIFIED"
        changes.append({"id": identifier, "status": status, "before": before, "after": after,
                        "reason": "absence from a discovery run is not removal" if after is None else None})
    return {"source": SOURCE_ID, "changes": changes}


if __name__ == "__main__":
    checked = datetime.now(timezone.utc).date().isoformat()
    try:
        atms = discover()
        envelope = {"schema_version": "1.0", "source": SOURCE_ID,
                    "scope": {"bbox_south_west_north_east": BBOX}, "candidates": atms}
        write_json(DATA / "candidates" / "atms.json", envelope)
        write_json(DATA / "proposals" / "atms.json", reconcile(read_json(DATA / "atms.json"), atms))
        update_history(SOURCE_ID, checked, len(atms))
        print(f"{len(atms)} mapped ATM candidates")
    except Exception as error:
        update_history(SOURCE_ID, checked, error=error)
        raise SystemExit(str(error)) from error

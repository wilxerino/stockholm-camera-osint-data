"""Deterministic camera dataset validation, reconciliation and manifests.

No third-party packages, API keys, or network access are needed for these steps.
Discovery adapters create candidates; only reviewed data belongs in data/cameras.json.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CAMERA_TYPES = {"traffic", "municipal", "police", "public_transport", "road_monitoring", "other"}
STATUSES = {"active", "inactive", "unknown", "deprecated"}
CONFIDENCES = {"high", "medium", "low", "pending_review"}
GEOMETRY_CONFIDENCES = {"high", "medium", "low", "unknown"}
GEOMETRY_PROVENANCE = {"official", "documented", "approximate", "unknown"}
SOURCE_TYPES = {"government", "open_data", "public_transport", "research", "github", "news", "other"}
FEED_TYPES = {"hls", "webrtc", "mjpeg", "image", "embed"}
REQUIRED_CAMERA = {"id", "latitude", "longitude", "type", "operator", "status", "orientation", "horizontal_fov", "range_m", "geometry_confidence", "geometry_provenance", "viewable", "feed", "source_ids", "confidence", "last_verified"}
REQUIRED_SOURCE = {"id", "url", "title", "publisher", "published_date", "retrieved_date", "source_type", "reliability", "license", "public_feed_documentation"}


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def stable_id(source_id: str, source_native_id: str) -> str:
    """An immutable ID from the publisher's own record identifier."""
    raw = f"{source_id}\0{source_native_id}".encode("utf-8")
    return "camera-" + hashlib.sha256(raw).hexdigest()[:20]


def _require(condition: bool, message: str):
    if not condition:
        raise ValueError(message)


def _https(value, label):
    parsed = urlparse(value) if isinstance(value, str) else None
    _require(bool(parsed and parsed.scheme == "https" and parsed.netloc and not parsed.username), f"{label} must be HTTPS")


def _date(value, label, nullable=False):
    if nullable and value is None:
        return
    _require(isinstance(value, str), f"{label} must be an ISO date")
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{label} must be an ISO date") from error


def _number(value, label, low, high=None, low_inclusive=True, high_inclusive=True):
    _require(type(value) in (int, float) and math.isfinite(value), f"{label} must be a finite number")
    _require(value >= low if low_inclusive else value > low, f"{label} is below its minimum")
    if high is not None:
        _require(value <= high if high_inclusive else value < high, f"{label} is above its maximum")


def validate_source(source):
    _require(isinstance(source, dict), "source must be an object")
    _require(REQUIRED_SOURCE <= source.keys(), f"source missing {sorted(REQUIRED_SOURCE - source.keys())}")
    _require(isinstance(source["id"], str) and source["id"], "source ID missing")
    _https(source["url"], "source URL")
    for key in ("title", "publisher", "license"):
        _require(isinstance(source[key], str) and source[key].strip(), f"source {key} missing")
    _date(source["published_date"], "published_date", nullable=True)
    _date(source["retrieved_date"], "retrieved_date")
    _require(source["source_type"] in SOURCE_TYPES, "invalid source type")
    _require(source["reliability"] in {"high", "medium", "low"}, "invalid source reliability")
    _require(type(source["public_feed_documentation"]) is bool, "public_feed_documentation must be boolean")


def validate_camera(camera, sources, allow_pending=False):
    _require(isinstance(camera, dict), "camera must be an object")
    _require(REQUIRED_CAMERA <= camera.keys(), f"camera missing {sorted(REQUIRED_CAMERA - camera.keys())}")
    identifier = camera["id"]
    _require(isinstance(identifier, str) and identifier.startswith("camera-") and len(identifier) > 7, "invalid camera ID")
    _number(camera["latitude"], "latitude", -90, 90)
    _number(camera["longitude"], "longitude", -180, 180)
    _require(camera["type"] in CAMERA_TYPES, f"{identifier}: invalid camera type")
    _require(camera["status"] in STATUSES, f"{identifier}: invalid status")
    _require(camera["operator"] is None or (isinstance(camera["operator"], str) and camera["operator"].strip()), f"{identifier}: invalid operator")
    _require(camera["confidence"] in CONFIDENCES, f"{identifier}: invalid confidence")
    _require(allow_pending or camera["confidence"] != "pending_review", f"{identifier}: pending camera cannot be production")
    _date(camera["last_verified"], "last_verified")
    source_ids = camera["source_ids"]
    _require(isinstance(source_ids, list) and source_ids and all(isinstance(item, str) for item in source_ids), f"{identifier}: source_ids required")
    _require(len(source_ids) == len(set(source_ids)) and all(item in sources for item in source_ids), f"{identifier}: unresolved or duplicate source")
    if "source_url" in camera:
        _https(camera["source_url"], "camera source_url")
    if "source_native_id" in camera or "primary_source_id" in camera:
        _require(isinstance(camera.get("source_native_id"), str) and camera["source_native_id"], f"{identifier}: source_native_id required")
        _require(camera.get("primary_source_id") in source_ids, f"{identifier}: primary_source_id must be in source_ids")
        _require(identifier == stable_id(camera["primary_source_id"], camera["source_native_id"]), f"{identifier}: unstable ID")

    for key, high, high_inclusive in (("orientation", 360, False), ("horizontal_fov", 360, True), ("range_m", None, True)):
        value = camera[key]
        if value is not None:
            _number(value, key, 0, high, low_inclusive=key == "orientation", high_inclusive=high_inclusive)
    _require(camera["geometry_confidence"] in GEOMETRY_CONFIDENCES, f"{identifier}: geometry confidence invalid")
    _require(camera["geometry_provenance"] in GEOMETRY_PROVENANCE, f"{identifier}: geometry provenance invalid")
    has_geometry = any(camera[key] is not None for key in ("orientation", "horizontal_fov", "range_m"))
    if has_geometry:
        evidence = camera.get("geometry_source_ids")
        _require(camera["geometry_confidence"] != "unknown" and camera["geometry_provenance"] != "unknown", f"{identifier}: geometry needs confidence and provenance")
        _require(isinstance(evidence, list) and evidence and set(evidence) <= set(source_ids), f"{identifier}: geometry needs source evidence")
    else:
        _require(camera["geometry_confidence"] == "unknown" and camera["geometry_provenance"] == "unknown", f"{identifier}: unknown geometry must be null")

    _require(type(camera["viewable"]) is bool, f"{identifier}: viewable must be boolean")
    feed = camera["feed"]
    if camera["viewable"]:
        _require(isinstance(feed, dict), f"{identifier}: public feed details required")
        _require(feed.get("type") in FEED_TYPES and feed.get("access") == "public", f"{identifier}: invalid public feed")
        _https(feed.get("url"), "feed URL")
        evidence_id = feed.get("evidence_source_id")
        _require(evidence_id in source_ids and sources[evidence_id]["public_feed_documentation"], f"{identifier}: public feed requires explicit publisher documentation")
    else:
        _require(feed is None, f"{identifier}: non-viewable camera cannot contain feed")


def validate_dataset(cameras, sources, allow_pending=False):
    _require(isinstance(cameras, list) and isinstance(sources, list), "dataset files must be top-level arrays")
    source_index = {}
    for source in sources:
        validate_source(source)
        _require(source["id"] not in source_index, f"duplicate source ID {source['id']}")
        source_index[source["id"]] = source
    ids = set()
    native_keys = set()
    for camera in cameras:
        validate_camera(camera, source_index, allow_pending=allow_pending)
        _require(camera["id"] not in ids, f"duplicate camera ID {camera['id']}")
        ids.add(camera["id"])
        if "source_native_id" in camera:
            key = (camera["primary_source_id"], camera["source_native_id"])
            _require(key not in native_keys, f"duplicate source record {key}")
            native_keys.add(key)
    return source_index


def validate_atms(atms, sources):
    _require(isinstance(atms, list) and len(atms) <= 100_000, "ATM dataset must be a bounded array")
    ids = set()
    for atm in atms:
        _require(isinstance(atm, dict), "ATM must be an object")
        _require({"id", "latitude", "longitude", "source_url", "source_ids", "camera_presence"} <= atm.keys(), "ATM fields missing")
        identifier = atm["id"]
        _require(isinstance(identifier, str) and identifier.startswith("atm-") and identifier not in ids, "duplicate or invalid ATM ID")
        ids.add(identifier)
        _number(atm["latitude"], "ATM latitude", 59.0, 59.6)
        _number(atm["longitude"], "ATM longitude", 17.5, 18.6)
        _https(atm["source_url"], "ATM source URL")
        _require(isinstance(atm["source_ids"], list) and atm["source_ids"] and
                 all(item in sources for item in atm["source_ids"]), "ATM provenance missing")
        _require(atm["camera_presence"] in {"unknown", "documented"}, "ATM camera status invalid")


def distance_m(a, b):
    lat1, lon1, lat2, lon2 = (math.radians(v) for v in (a["latitude"], a["longitude"], b["latitude"], b["longitude"]))
    dlat, dlon = lat2 - lat1, lon2 - lon1
    arc = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 12742000 * math.asin(min(1, math.sqrt(arc)))


def field_diffs(before, after):
    ignored = {"last_verified", "candidate_state"}
    differences = {}
    for key in sorted((before.keys() | after.keys()) - ignored):
        if key == "confidence" and after.get(key) == "pending_review":
            continue
        if before.get(key) != after.get(key):
            differences[key] = {"old": before.get(key), "new": after.get(key)}
    return differences


def reconcile(cameras, sources, envelope):
    _require(isinstance(envelope, dict) and isinstance(envelope.get("candidates"), list), "candidate file needs candidates array")
    supplemental = envelope.get("sources", [])
    _require(isinstance(supplemental, list), "candidate sources must be an array")
    source_index = validate_dataset(cameras, sources)
    for source in supplemental:
        validate_source(source)
        if source["id"] in source_index:
            _require(source_index[source["id"]]["url"] == source["url"], "source ID URL conflict")
        else:
            source_index[source["id"]] = source
    current = {camera["id"]: camera for camera in cameras}
    seen = set()
    changes = []
    for candidate in envelope["candidates"]:
        _require(isinstance(candidate, dict) and isinstance(candidate.get("id"), str), "candidate ID required")
        identifier = candidate["id"]
        if identifier in seen:
            changes.append({"id": identifier, "status": "CONFLICTING", "reason": "duplicate candidate ID"})
            continue
        seen.add(identifier)
        if candidate.get("candidate_state") == "removed":
            _https(candidate.get("removal_evidence_url"), "removal evidence")
            changes.append({"id": identifier, "status": "REMOVED" if identifier in current else "CONFLICTING", "reason": "explicit sourced removal; review before deprecation", "before": current.get(identifier), "after": None})
            continue
        validate_camera(candidate, source_index, allow_pending=True)
        clean = {key: value for key, value in candidate.items() if key != "candidate_state"}
        if identifier in current:
            diffs = field_diffs(current[identifier], clean)
            if diffs:
                changes.append({"id": identifier, "status": "MODIFIED", "diff": diffs, "before": current[identifier], "after": clean})
            else:
                changes.append({"id": identifier, "status": "UNCHANGED"})
            continue
        possible = [other["id"] for other in cameras if other["type"] == clean["type"] and distance_m(other, clean) < 10]
        status = "CONFLICTING" if possible else "ADDED"
        changes.append({"id": identifier, "status": status, "reason": "possible nearby duplicate" if possible else "new source record", "possible_duplicates": possible, "before": None, "after": clean})
    for camera in cameras:
        if camera["id"] not in seen:
            changes.append({"id": camera["id"], "status": "UNCHANGED", "reason": "absence from discovery is not removal"})
    return {"schema_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "source": envelope.get("source"), "changes": changes, "counts": {status: sum(item["status"] == status for item in changes) for status in ("ADDED", "MODIFIED", "REMOVED", "UNCHANGED", "CONFLICTING")}}


def manifest(cameras_path=DATA / "cameras.json", sources_path=DATA / "sources.json", version=None, atms_path=None):
    cameras_bytes = cameras_path.read_bytes()
    sources_bytes = sources_path.read_bytes()
    cameras, sources = json.loads(cameras_bytes), json.loads(sources_bytes)
    source_index = validate_dataset(cameras, sources)
    result = {"dataset_version": version or datetime.now(timezone.utc).strftime("%Y.%m.%d"), "schema_version": "1.0", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"), "camera_count": len(cameras), "sha256": hashlib.sha256(cameras_bytes).hexdigest(), "sources_sha256": hashlib.sha256(sources_bytes).hexdigest()}
    if atms_path is None and cameras_path == DATA / "cameras.json":
        atms_path = DATA / "atms.json"
    if atms_path is not None:
        atm_bytes = atms_path.read_bytes()
        atms = json.loads(atm_bytes)
        validate_atms(atms, source_index)
        result.update({"atm_count": len(atms), "atms_sha256": hashlib.sha256(atm_bytes).hexdigest()})
    return result


def validate_manifest(existing, cameras_path=DATA / "cameras.json", sources_path=DATA / "sources.json"):
    computed = manifest(cameras_path, sources_path, existing.get("dataset_version"))
    for key in ("schema_version", "camera_count", "sha256", "sources_sha256", "atm_count", "atms_sha256"):
        _require(existing.get(key) == computed.get(key), f"manifest {key} mismatch")
    _require(isinstance(existing.get("dataset_version"), str) and existing["dataset_version"], "manifest version missing")
    _require(isinstance(existing.get("generated_at"), str) and existing["generated_at"].endswith("Z"), "manifest generated_at missing")


def material_signature(value):
    """Ignore observation timestamps so unchanged scheduled fetches stay quiet."""
    if isinstance(value, list):
        return [material_signature(item) for item in value]
    if isinstance(value, dict):
        return {key: material_signature(item) for key, item in value.items() if key not in {"last_verified", "retrieved_date", "generated_at", "last_checked"}}
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate")
    propose = sub.add_parser("reconcile")
    propose.add_argument("candidate_file", type=Path)
    propose.add_argument("--output", type=Path, default=DATA / "proposals" / "latest.json")
    build = sub.add_parser("manifest")
    build.add_argument("--version")
    promote = sub.add_parser("promote", help="Explicit reviewed candidate import; never used by scheduled CI")
    promote.add_argument("candidate_file", type=Path)
    promote.add_argument("--source-id", required=True)
    promote.add_argument("--acknowledge-review", action="store_true", required=True)
    changed = sub.add_parser("material-change", help="Exit 0 when candidate facts differ from prior snapshot")
    changed.add_argument("current", type=Path)
    changed.add_argument("previous", type=Path)
    decision = sub.add_parser("decision", help="Record a human candidate review decision")
    decision.add_argument("camera_id")
    decision.add_argument("state", choices=("accepted", "rejected"))
    decision.add_argument("--source-id", required=True)
    decision.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.command == "material-change":
        current = read_json(args.current)
        previous = read_json(args.previous) if args.previous.exists() and args.previous.stat().st_size else None
        is_changed = material_signature(current) != material_signature(previous)
        print("changed" if is_changed else "unchanged")
        raise SystemExit(0 if is_changed else 1)
    if args.command == "decision":
        state_path = DATA / "research-state.json"
        state = read_json(state_path)
        decisions = state.setdefault("decisions", {})
        decisions[args.camera_id] = {"source_id": args.source_id, "state": args.state, "reason": args.reason, "reviewed_at": datetime.now(timezone.utc).date().isoformat()}
        write_json(state_path, state)
        print(f"recorded {args.state} for {args.camera_id}")
        return
    cameras = read_json(DATA / "cameras.json")
    sources = read_json(DATA / "sources.json")
    validate_dataset(cameras, sources)
    if args.command == "validate":
        validate_manifest(read_json(DATA / "manifest.json"))
        print(f"valid: {len(cameras)} cameras, {len(sources)} sources")
    elif args.command == "reconcile":
        proposal = reconcile(cameras, sources, read_json(args.candidate_file))
        write_json(args.output, proposal)
        print(json.dumps(proposal["counts"], sort_keys=True))
    elif args.command == "manifest":
        write_json(DATA / "manifest.json", manifest(version=args.version))
        print("manifest updated")
    elif args.command == "promote":
        envelope = read_json(args.candidate_file)
        _require(envelope.get("source") == args.source_id, "candidate source mismatch")
        _require(all(item.get("candidate_state", "present") == "present" for item in envelope["candidates"]), "removal requires separate manual review")
        merged_sources = {item["id"]: item for item in sources}
        for source in envelope.get("sources", []):
            validate_source(source)
            if source["id"] in merged_sources:
                _require(source["url"] == merged_sources[source["id"]]["url"], "source ID URL conflict")
            merged_sources[source["id"]] = source
        merged_cameras = {item["id"]: item for item in cameras}
        promoted = []
        for candidate in envelope["candidates"]:
            _require(args.source_id in candidate["source_ids"], "candidate from unexpected source")
            _require(candidate["confidence"] != "pending_review", "set each candidate confidence after evidence review before promotion")
            item = dict(candidate)
            validate_camera(item, merged_sources)
            promoted.append(item)
            merged_cameras[item["id"]] = item
        updated_cameras = sorted(merged_cameras.values(), key=lambda item: item["id"])
        updated_sources = sorted(merged_sources.values(), key=lambda item: item["id"])
        validate_dataset(updated_cameras, updated_sources)
        proposal = reconcile(cameras, sources, dict(envelope, candidates=promoted))
        history = read_json(DATA / "changes.json")
        history.extend({"id": change["id"], "status": change["status"], "at": proposal["generated_at"], "diff": change.get("diff", {}), "before": change.get("before"), "after": change.get("after"), "reason": change.get("reason")} for change in proposal["changes"] if change["status"] != "UNCHANGED")
        write_json(DATA / "sources.json", updated_sources)
        write_json(DATA / "cameras.json", updated_cameras)
        write_json(DATA / "changes.json", history)
        write_json(DATA / "manifest.json", manifest())
        discovery_path = DATA / "discovery-history.json"
        discovery = read_json(discovery_path)
        item = next((item for item in discovery if item.get("source_id") == args.source_id), None)
        if item is not None:
            item["accepted_count"] = sum(args.source_id in row["source_ids"] for row in updated_cameras)
            item["status"] = "accepted"
            write_json(discovery_path, discovery)
        print(f"promoted {len(promoted)} reviewed source records; {len(updated_cameras)} production cameras")


if __name__ == "__main__":
    main()

import hashlib
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from camera_data import (  # noqa: E402
    distance_m, field_diffs, manifest, material_signature, reconcile, stable_id, validate_camera,
    validate_dataset, validate_manifest, validate_source,
)
from discover_cameras import (  # noqa: E402
    OSM_SOURCE, OSM_SOURCE_ID, TRAFIKEN_FAQ, TRAFIKEN_FAQ_ID,
    TRAFIKEN_SOURCE, TRAFIKEN_SOURCE_ID, osm_candidate,
    trafiken_candidate, trafiken_rows,
)


TODAY = "2026-09-24"


def sources():
    return [dict(TRAFIKEN_SOURCE, retrieved_date=TODAY), dict(TRAFIKEN_FAQ, retrieved_date=TODAY)]


def camera():
    return {
        "id": stable_id(TRAFIKEN_SOURCE_ID, "TEST-1"),
        "source_native_id": "TEST-1",
        "primary_source_id": TRAFIKEN_SOURCE_ID,
        "source_url": "https://trafiken.nu/stockholm/kameror/test/",
        "latitude": 59.3293,
        "longitude": 18.0686,
        "type": "traffic",
        "operator": None,
        "status": "unknown",
        "orientation": 135,
        "horizontal_fov": None,
        "range_m": None,
        "geometry_confidence": "medium",
        "geometry_provenance": "documented",
        "geometry_source_ids": [TRAFIKEN_SOURCE_ID],
        "viewable": True,
        "feed": {"type": "image", "url": "https://example.org/public.jpg", "access": "public", "evidence_source_id": TRAFIKEN_FAQ_ID},
        "source_ids": [TRAFIKEN_SOURCE_ID, TRAFIKEN_FAQ_ID],
        "confidence": "high",
        "last_verified": TODAY,
    }


class CameraDataTests(unittest.TestCase):
    def test_valid_camera_and_source(self):
        validate_dataset([camera()], sources())

    def test_source_schema_and_https(self):
        bad = sources()[0]
        bad["url"] = "http://example.org/data"
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            validate_source(bad)

    def test_coordinate_validation(self):
        item = camera()
        item["latitude"] = 91
        with self.assertRaisesRegex(ValueError, "latitude"):
            validate_dataset([item], sources())

    def test_stable_ids(self):
        self.assertEqual(stable_id("s", "1"), stable_id("s", "1"))
        self.assertNotEqual(stable_id("s", "1"), stable_id("s", "2"))
        item = camera()
        item["id"] = stable_id(TRAFIKEN_SOURCE_ID, "OTHER")
        with self.assertRaisesRegex(ValueError, "unstable"):
            validate_dataset([item], sources())

    def test_duplicate_id_and_native_record(self):
        with self.assertRaisesRegex(ValueError, "duplicate camera"):
            validate_dataset([camera(), camera()], sources())
        second = camera()
        second["id"] = "camera-manual-second"
        # The source-native stable ID invariant catches this before native duplicate validation.
        with self.assertRaises(ValueError):
            validate_dataset([camera(), second], sources())

    def test_provenance_required(self):
        item = camera()
        item["source_ids"] = []
        with self.assertRaisesRegex(ValueError, "source"):
            validate_dataset([item], sources())
        item = camera()
        item["source_ids"] = ["missing"]
        with self.assertRaisesRegex(ValueError, "source"):
            validate_dataset([item], sources())

    def test_geometry_requires_evidence_and_nulls_are_allowed(self):
        item = camera()
        del item["geometry_source_ids"]
        with self.assertRaisesRegex(ValueError, "geometry needs source"):
            validate_dataset([item], sources())
        item = camera()
        item.update(orientation=None, horizontal_fov=None, range_m=None, geometry_confidence="unknown", geometry_provenance="unknown")
        del item["geometry_source_ids"]
        validate_dataset([item], sources())

    def test_public_feed_requires_explicit_documentation(self):
        item = camera()
        item["feed"]["evidence_source_id"] = TRAFIKEN_SOURCE_ID
        src = sources()
        src[0]["public_feed_documentation"] = False
        with self.assertRaisesRegex(ValueError, "explicit publisher documentation"):
            validate_dataset([item], src)
        item = camera()
        item["viewable"] = False
        with self.assertRaisesRegex(ValueError, "non-viewable"):
            validate_dataset([item], sources())
        item["feed"] = None
        validate_dataset([item], sources())

    def test_internet_exposure_does_not_make_feed_public(self):
        item = camera()
        item["feed"] = {"type": "mjpeg", "url": "https://example.org/exposed", "access": "public"}
        with self.assertRaises(ValueError):
            validate_dataset([item], sources())

    def test_pending_candidate_cannot_be_production(self):
        item = camera()
        item["confidence"] = "pending_review"
        with self.assertRaisesRegex(ValueError, "pending"):
            validate_dataset([item], sources())
        validate_dataset([item], sources(), allow_pending=True)

    def test_reconciliation_statuses_and_field_diffs(self):
        original = camera()
        changed = deepcopy(original)
        changed["orientation"] = 140
        new = deepcopy(original)
        new["source_native_id"] = "TEST-2"
        new["id"] = stable_id(TRAFIKEN_SOURCE_ID, "TEST-2")
        new["latitude"] = 59.4
        result = reconcile([original], sources(), {"source": TRAFIKEN_SOURCE_ID, "candidates": [changed, new], "sources": sources()})
        self.assertEqual(result["counts"]["MODIFIED"], 1)
        self.assertEqual(result["counts"]["ADDED"], 1)
        self.assertEqual(result["changes"][0]["diff"]["orientation"], {"old": 135, "new": 140})

    def test_absence_is_not_removal(self):
        result = reconcile([camera()], sources(), {"source": TRAFIKEN_SOURCE_ID, "candidates": []})
        self.assertEqual(result["counts"]["UNCHANGED"], 1)
        self.assertEqual(result["counts"]["REMOVED"], 0)

    def test_daily_candidate_review_state_is_not_a_fact_change(self):
        item = camera()
        pending = deepcopy(item)
        pending["confidence"] = "pending_review"
        pending["last_verified"] = "2026-09-25"
        result = reconcile([item], sources(), {"source": TRAFIKEN_SOURCE_ID, "candidates": [pending]})
        self.assertEqual(result["counts"]["UNCHANGED"], 1)

    def test_material_change_ignores_fetch_dates_but_not_camera_facts(self):
        first = {"sources": sources(), "candidates": [camera()]}
        second = deepcopy(first)
        second["sources"][0]["retrieved_date"] = "2026-09-25"
        second["candidates"][0]["last_verified"] = "2026-09-25"
        self.assertEqual(material_signature(first), material_signature(second))
        second["candidates"][0]["orientation"] = 140
        self.assertNotEqual(material_signature(first), material_signature(second))

    def test_removal_requires_explicit_evidence(self):
        tombstone = {"id": camera()["id"], "candidate_state": "removed", "removal_evidence_url": "https://example.org/removed"}
        result = reconcile([camera()], sources(), {"source": TRAFIKEN_SOURCE_ID, "candidates": [tombstone]})
        self.assertEqual(result["counts"]["REMOVED"], 1)
        tombstone["removal_evidence_url"] = "rtsp://example.org/camera"
        with self.assertRaises(ValueError):
            reconcile([camera()], sources(), {"candidates": [tombstone]})

    def test_nearby_candidate_is_conflicting_not_merged(self):
        other = deepcopy(camera())
        other["source_native_id"] = "TEST-2"
        other["id"] = stable_id(TRAFIKEN_SOURCE_ID, "TEST-2")
        result = reconcile([camera()], sources(), {"source": TRAFIKEN_SOURCE_ID, "candidates": [other]})
        self.assertEqual(result["counts"]["CONFLICTING"], 1)
        self.assertLess(distance_m(other, camera()), 10)

    def test_manifest_hashes_exact_bytes_and_rejects_corruption(self):
        with tempfile.TemporaryDirectory() as folder:
            cp, sp = Path(folder) / "cameras.json", Path(folder) / "sources.json"
            cp.write_text(json.dumps([camera()]) + "\n")
            sp.write_text(json.dumps(sources()) + "\n")
            info = manifest(cp, sp, version="2026.09.24")
            self.assertEqual(info["sha256"], hashlib.sha256(cp.read_bytes()).hexdigest())
            validate_manifest(info, cp, sp)
            cp.write_text("[]\n")
            with self.assertRaisesRegex(ValueError, "manifest"):
                validate_manifest(info, cp, sp)

    def test_trafiken_parser_dedup_and_public_image_classification(self):
        row = {"cameraId": "X", "wgs84Position": {"coordinates": [18.1, 59.3]}, "imageUrl": "https://example.org/image.jpg", "url": "/stockholm/kameror/x/", "direction": 90}
        html = 'a React.createElement(Components.CameraMenu, ' + json.dumps({"cameras": [row]}) + ');'
        self.assertEqual(trafiken_rows(html), [row])
        item = trafiken_candidate(row, TODAY)
        self.assertTrue(item["viewable"])
        self.assertEqual(item["feed"]["type"], "image")
        self.assertIsNone(item["horizontal_fov"])
        self.assertIsNone(item["range_m"])

    def test_osm_candidates_have_no_feed(self):
        row = {"type": "node", "id": 123, "lat": 59.3, "lon": 18.1, "tags": {"man_made": "surveillance", "surveillance:type": "camera", "camera:direction": "NE", "contact:webcam": "rtsp://exposed"}}
        item = osm_candidate(row, TODAY)
        self.assertEqual(item["orientation"], 45)
        self.assertFalse(item["viewable"])
        self.assertIsNone(item["feed"])
        self.assertEqual(item["source_url"], "https://www.openstreetmap.org/node/123")


if __name__ == "__main__":
    unittest.main()

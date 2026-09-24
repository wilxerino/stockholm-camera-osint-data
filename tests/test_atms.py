import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from camera_data import validate_atms
from discover_atms import normalize, reconcile


class ATMTests(unittest.TestCase):
    def test_normalize_keeps_atm_distinct_from_camera(self):
        rows = [{"type": "node", "id": 42, "lat": 59.33, "lon": 18.06,
                 "tags": {"amenity": "atm", "operator": "Bankomat"}},
                {"type": "node", "id": 43, "lat": 59.34, "lon": 18.07,
                 "tags": {"amenity": "atm", "surveillance:type": "camera"}}]
        records = normalize(rows)
        self.assertEqual([item["camera_presence"] for item in records], ["unknown", "documented"])
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["source_url"], "https://www.openstreetmap.org/node/42")

    def test_provenance_duplicates_and_absence(self):
        record = normalize([{"type": "node", "id": 42, "lat": 59.33, "lon": 18.06,
                             "tags": {"atm": "yes"}}])[0]
        sources = {"openstreetmap-stockholm-atms": {}}
        validate_atms([record], sources)
        with self.assertRaises(ValueError):
            validate_atms([record, record], sources)
        with self.assertRaises(ValueError):
            validate_atms([dict(record, source_ids=["missing"])], sources)
        with self.assertRaises(ValueError):
            validate_atms([dict(record, latitude=91)], sources)
        changes = reconcile([record], [])
        self.assertEqual(changes["changes"][0]["status"], "UNCHANGED")
        self.assertIn("not removal", changes["changes"][0]["reason"])


if __name__ == "__main__":
    unittest.main()

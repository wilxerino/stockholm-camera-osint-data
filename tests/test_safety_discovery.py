import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from discover_safety_cameras import camera_from_page, page_links


class SafetyDiscoveryTests(unittest.TestCase):
    def test_published_place_is_location_not_public_feed(self):
        html = '<script type="application/ld+json">{"@graph":[{"@type":"Place","name":"Trafiksäkerhetskamera Brotorp","geo":{"@type":"GeoCoordinates","latitude":59.1723,"longitude":17.7991}}]}</script>'
        camera = camera_from_page("https://fartkameran.se/kameror/botkyrka/brotorp-02001170/", html, "2026-09-24")
        self.assertEqual(camera["source_native_id"], "02001170")
        self.assertEqual(camera["name"], "Brotorp")
        self.assertEqual((camera["latitude"], camera["longitude"]), (59.1723, 17.7991))
        self.assertFalse(camera["viewable"])
        self.assertIsNone(camera["feed"])
        self.assertIsNone(camera["orientation"])

    def test_listing_section_only(self):
        html = '<a href="/kameror/other/unrelated-12345/"></a><h2>Samtliga kameror i <!-- -->Stockholms län</h2><a href="/kameror/botkyrka/brotorp-02001170/"></a></section>'
        self.assertEqual(page_links(html), ["https://fartkameran.se/kameror/botkyrka/brotorp-02001170/"])


if __name__ == "__main__":
    unittest.main()

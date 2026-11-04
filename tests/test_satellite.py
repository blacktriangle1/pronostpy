from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pronostico.services import (
    _pick_satellite_times,
    _satellite_tile_url,
    _satellite_time_template,
)


class SatelliteHelpersTest(unittest.TestCase):
    def test_time_template_replaces_default(self) -> None:
        layer = {
            "template": (
                "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
                "GOES-East_ABI_GeoColor/default/default/"
                "GoogleMapsCompatible_Level7/{z}/{y}/{x}.png"
            )
        }
        url = _satellite_time_template(layer, "2026-11-03T01:50:00Z")
        self.assertIn("/default/2026-11-03T01:50:00Z/", url)
        self.assertNotIn("/default/default/", url)

    def test_tile_url_replaces_coordinates(self) -> None:
        template = "https://example.test/{z}/{y}/{x}.png"
        self.assertEqual(
            _satellite_tile_url(template, 3, 1, 3),
            "https://example.test/3/3/1.png",
        )

    def test_pick_satellite_times_keeps_spacing_and_order(self) -> None:
        base = datetime(2026, 11, 3, 1, 50, tzinfo=timezone.utc)
        times = [
            base,
            base - timedelta(minutes=5),
            base - timedelta(minutes=10),
            base - timedelta(minutes=25),
            base - timedelta(minutes=40),
            base - timedelta(minutes=45),
        ]
        picked = _pick_satellite_times(times, count=3, min_gap_minutes=8)
        self.assertEqual(
            picked,
            [
                base - timedelta(minutes=25),
                base - timedelta(minutes=10),
                base,
            ],
        )


if __name__ == "__main__":
    unittest.main()

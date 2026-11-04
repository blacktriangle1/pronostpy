from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from pronostico.services import (
    _build_wave_tracks,
    _parse_wave_observations,
    _summarize_wave_tracks,
    _wave_first_longitude,
    _wave_iso,
    _wave_lat_range,
    _wave_moment,
    _wave_speed,
)

SAMPLE = """
Tropical Weather Discussion
NWS National Hurricane Center Miami FL
1215 UTC Tue Sep 8 2026

...TROPICAL WAVES...

An eastern Atlantic tropical wave has its axis near 44W from 03N to
19N, moving westward at about 10 to 15 kt. Scattered moderate and
isolated strong convection is noted from 04N to 10N between 43W and
48W.

A central Atlantic tropical wave has its axis near 56W, moving
westward near 10 to 15 kt. Scattered moderate convection is noted
from 11N to 17N between 52W and 57W.

A central Caribbean tropical wave has its axis near 78W, south of
19N. It is moving westward at 10 to 15 kt. Scattered moderate
convection is noted along the wave axis S of 12N.

...MONSOON TROUGH/ITCZ...

The monsoon trough enters the Atlantic near 19N16W.
"""


def observation(moment, lon, lat_min=5.0, lat_max=20.0, speed=15.0):
    return {
        "time": moment,
        "lon": lon,
        "latMin": lat_min,
        "latMax": lat_max,
        "speedKtMin": speed,
        "speedKtMax": speed,
        "area": "Caribe",
        "detail": "Onda de prueba.",
    }


class TropicalWaveParserTest(unittest.TestCase):
    def test_wave_moment(self) -> None:
        moment = _wave_moment("202609081215")
        self.assertIsNotNone(moment)
        self.assertEqual(_wave_iso(moment), "2026-09-08T12:15:00Z")
        self.assertIsNone(_wave_moment("not-a-stamp"))

    def test_first_longitude(self) -> None:
        self.assertEqual(_wave_first_longitude("axis along 18W/19W south of 20N"), -18.0)
        self.assertEqual(_wave_first_longitude("from near 16N51W to 03N53W"), -51.0)
        self.assertEqual(_wave_first_longitude("within 240 nm west of the wave"), None)

    def test_lat_range_and_speed(self) -> None:
        self.assertEqual(_wave_lat_range("from 03N to 19N"), (3.0, 19.0))
        self.assertEqual(_wave_lat_range("south of 19N"), (7.0, 19.0))
        self.assertEqual(_wave_speed("moving westward at 10 to 15 kt"), (10.0, 15.0))
        self.assertEqual(_wave_speed("moving W at around 20 kt"), (20.0, 20.0))
        self.assertEqual(_wave_speed("sin velocidad"), (10.0, 15.0))

    def test_parse_three_waves(self) -> None:
        moment = datetime(2026, 9, 8, 12, 15, tzinfo=timezone.utc)
        observations = _parse_wave_observations(SAMPLE, moment)
        self.assertEqual(len(observations), 3)
        self.assertEqual(observations[0]["lon"], -44.0)
        self.assertEqual(observations[0]["latMin"], 3.0)
        self.assertEqual(observations[0]["latMax"], 19.0)
        self.assertEqual(observations[0]["speedKtMin"], 10.0)
        self.assertEqual(observations[0]["speedKtMax"], 15.0)
        self.assertEqual(observations[1]["lon"], -56.0)
        self.assertEqual(observations[1]["latMin"], 5.0)
        self.assertEqual(observations[1]["latMax"], 20.0)
        self.assertEqual(observations[2]["lon"], -78.0)
        self.assertEqual(observations[2]["latMin"], 7.0)
        self.assertEqual(observations[2]["latMax"], 19.0)


class TropicalWaveTrackingTest(unittest.TestCase):
    def test_single_track_crosses_venezuela(self) -> None:
        start = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)
        observations = [
            observation(start, -56.0),
            observation(start + timedelta(hours=6), -61.0),
            observation(start + timedelta(hours=12), -67.0),
            observation(start + timedelta(hours=18), -74.0),
        ]
        tracks = _build_wave_tracks(observations)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0]["observations"]), 4)

        waves = _summarize_wave_tracks(tracks, start + timedelta(hours=24))
        self.assertEqual(len(waves), 1)
        wave = waves[0]
        self.assertEqual(wave["number"], 1)
        self.assertTrue(wave["entered"])
        self.assertTrue(wave["exited"])
        self.assertTrue(wave["crossedVenezuela"])
        self.assertEqual(wave["status"], "Salió de Venezuela")
        self.assertEqual(wave["entryAt"], "2026-10-05T06:00:00Z")
        self.assertEqual(wave["exitAt"], "2026-10-05T18:00:00Z")
        self.assertFalse(wave["entryEstimated"])
        self.assertFalse(wave["exitEstimated"])

    def test_two_close_waves_stay_separate(self) -> None:
        start = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)
        observations = [
            observation(start, -56.0),
            observation(start, -62.0),
            observation(start + timedelta(hours=6), -57.0),
            observation(start + timedelta(hours=6), -63.0),
        ]
        tracks = _build_wave_tracks(observations)
        self.assertEqual(len(tracks), 2)
        self.assertEqual([len(track["observations"]) for track in tracks], [2, 2])

    def test_estimated_entry_and_exit(self) -> None:
        start = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)
        observations = [observation(start, -40.0)]
        tracks = _build_wave_tracks(observations)
        waves = _summarize_wave_tracks(tracks, start + timedelta(hours=6))
        self.assertEqual(len(waves), 1)
        wave = waves[0]
        self.assertTrue(wave["entryEstimated"])
        self.assertTrue(wave["exitEstimated"])
        self.assertGreater(wave["entryAt"], wave["lastSeen"])
        self.assertGreater(wave["exitAt"], wave["entryAt"])
        self.assertEqual(wave["status"], "Al este de Venezuela")

    def test_wave_seen_inside_corridor_estimates_entry_backwards(self) -> None:
        start = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)
        observations = [
            observation(start, -66.0),
            observation(start + timedelta(hours=6), -70.0),
        ]
        tracks = _build_wave_tracks(observations)
        waves = _summarize_wave_tracks(tracks, start + timedelta(hours=12))
        self.assertEqual(len(waves), 1)
        wave = waves[0]
        self.assertTrue(wave["entryEstimated"])
        self.assertLess(wave["entryAt"], wave["firstSeen"])
        self.assertTrue(wave["exitEstimated"])
        self.assertEqual(wave["status"], "En Venezuela")

    def test_wave_observed_inside_corridor_has_actual_exit(self) -> None:
        start = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)
        observations = [
            observation(start, -66.0),
            observation(start + timedelta(hours=6), -75.0),
        ]
        tracks = _build_wave_tracks(observations)
        waves = _summarize_wave_tracks(tracks, start + timedelta(hours=12))
        self.assertEqual(len(waves), 1)
        wave = waves[0]
        self.assertTrue(wave["entryEstimated"])
        self.assertFalse(wave["exitEstimated"])
        self.assertEqual(wave["exitAt"], "2026-10-05T06:00:00Z")
        self.assertEqual(wave["status"], "Salió de Venezuela")

    def test_wave_first_seen_west_of_exit_is_dropped(self) -> None:
        start = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)
        observations = [
            observation(start, -80.0),
            observation(start + timedelta(hours=6), -82.0),
        ]
        tracks = _build_wave_tracks(observations)
        waves = _summarize_wave_tracks(tracks, start + timedelta(hours=12))
        self.assertEqual(waves, [])

    def test_stale_wave_never_reaching_entry_is_dropped(self) -> None:
        start = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)
        observations = [observation(start, -40.0)]
        tracks = _build_wave_tracks(observations)
        waves = _summarize_wave_tracks(tracks, start + timedelta(days=10))
        self.assertEqual(waves, [])

    def test_far_east_wave_is_dropped(self) -> None:
        start = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)
        observations = [observation(start, -18.0)]
        tracks = _build_wave_tracks(observations)
        waves = _summarize_wave_tracks(tracks, start + timedelta(hours=6))
        self.assertEqual(waves, [])

    def test_duplicate_bulletin_is_ignored(self) -> None:
        start = datetime(2026, 10, 5, 0, tzinfo=timezone.utc)
        observations = [
            observation(start, -56.0),
            observation(start + timedelta(minutes=1), -56.0),
            observation(start + timedelta(hours=6), -57.0),
        ]
        tracks = _build_wave_tracks(observations)
        self.assertEqual(len(tracks), 1)
        self.assertEqual(len(tracks[0]["observations"]), 2)


if __name__ == "__main__":
    unittest.main()

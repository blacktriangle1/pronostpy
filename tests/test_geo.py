from __future__ import annotations

import unittest
from datetime import datetime, timezone

from pronostico.geo import (
    bbox_from_radius,
    bearing_es,
    build_narration,
    compass_es,
    describe_weather,
    haversine_km,
    relative_time_es,
    storm_risk,
)


class GeoHelpersTest(unittest.TestCase):
    def test_compass_spanish(self) -> None:
        self.assertEqual(compass_es(0), "norte")
        self.assertEqual(compass_es(90), "este")
        self.assertEqual(compass_es(180), "sur")
        self.assertEqual(compass_es(270), "oeste")
        self.assertEqual(compass_es(None), "sin dirección")

    def test_haversine_cdmx_guadalajara(self) -> None:
        distance = haversine_km(19.4326, -99.1332, 20.6597, -103.3496)
        self.assertGreater(distance, 440)
        self.assertLess(distance, 480)

    def test_bearing(self) -> None:
        self.assertEqual(bearing_es(0, 0, 1, 0), "norte")
        self.assertEqual(bearing_es(0, 0, 0, 1), "este")

    def test_bbox_contains_center(self) -> None:
        min_lat, min_lon, max_lat, max_lon = bbox_from_radius(19.4326, -99.1332, 100)
        self.assertLess(min_lat, 19.4326)
        self.assertGreater(max_lat, 19.4326)
        self.assertLess(min_lon, -99.1332)
        self.assertGreater(max_lon, -99.1332)

    def test_weather_descriptions(self) -> None:
        self.assertEqual(describe_weather(0), ("Despejado", "sun"))
        self.assertIn("Tormenta", describe_weather(95)[0])
        self.assertEqual(describe_weather(999)[1], "cloud")

    def test_storm_risk(self) -> None:
        self.assertEqual(storm_risk(95, 0)["level"], "alta")
        self.assertEqual(storm_risk(3, 1200)["level"], "moderada")
        self.assertEqual(storm_risk(3, 350)["level"], "baja")
        self.assertEqual(storm_risk(1, 0)["level"], "nula")

    def test_relative_time(self) -> None:
        now = datetime(2026, 11, 1, 20, 0, tzinfo=timezone.utc)
        self.assertEqual(relative_time_es("2026-11-01T19:59:00Z", now), "hace un momento")
        self.assertEqual(relative_time_es("2026-11-01T19:58:00Z", now), "hace 2 minutos")
        self.assertEqual(relative_time_es("2026-11-01T19:00:00Z", now), "hace 1 hora")
        self.assertEqual(relative_time_es("2026-10-31T20:00:00Z", now), "hace 1 día")

    def test_narration(self) -> None:
        location = {"display": "Ciudad de México, México"}
        weather = {
            "current": {
                "time": "2026-11-01T13:45",
                "temperature": 26.5,
                "apparentTemperature": 25.6,
                "description": "Parcialmente nublado",
                "cloudCover": 63,
                "humidity": 23,
                "precipitation": 0,
                "windSpeed": 5.6,
                "windGust": 19.8,
                "windDirectionText": "norte",
                "stormRisk": {"level": "nula"},
            },
            "nextPrecipitationProbability": 10,
        }
        quakes = [
            {
                "mag": 3.4,
                "distanceKm": 120.4,
                "direction": "suroeste",
                "time": "2026-11-01T18:00:00Z",
            }
        ]
        text = build_narration(location, weather, quakes)
        self.assertIn("Ciudad de México", text)
        self.assertIn("Temperatura 26 grados Celsius", text)
        self.assertIn("Viento del norte", text)
        self.assertIn("Probabilidad de lluvia", text)
        self.assertIn("Sismo más cercano", text)
        self.assertIn("magnitud 3.4", text)

    def test_narration_without_quakes(self) -> None:
        text = build_narration(
            {"display": "Lugar"},
            {"current": {"temperature": 20, "windSpeed": 4, "windDirectionText": "sur"}},
            [],
        )
        self.assertIn("Sin sismos", text)


if __name__ == "__main__":
    unittest.main()

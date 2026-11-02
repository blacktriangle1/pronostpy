from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

VERSION = "1.0.0"
APP_NAME = "Radar Vivo"

HOST = os.getenv("RADAR_HOST", "0.0.0.0")
PORT = int(os.getenv("RADAR_PORT", "8000"))

DEFAULT_LAT = float(os.getenv("RADAR_DEFAULT_LAT", "19.4326"))
DEFAULT_LON = float(os.getenv("RADAR_DEFAULT_LON", "-99.1332"))

HTTP_TIMEOUT = float(os.getenv("RADAR_HTTP_TIMEOUT", "18"))

USER_AGENT = os.getenv(
    "RADAR_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) RadarVivo/1.0 (app meteorologica de uso local)",
)

RAINVIEWER_MAPS_URL = "https://api.rainviewer.com/public/weather-maps.json"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
USGS_QUERY_URL = "https://earthquake.usgs.gov/fdsnws/event/1/query"
NOMINATIM_REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"
PHOTON_REVERSE_URL = "https://photon.komoot.io/reverse"
PHOTON_SEARCH_URL = "https://photon.komoot.io/api/"

SATELLITE_LAYERS = [
    {
        "id": "goes-east",
        "title": "GOES-East · GeoColor",
        "description": "America y Atlantico. Imagen cada 10 minutos.",
        "coverage": "America",
        "kind": "Tiempo real",
        "template": (
            "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
            "GOES-East_ABI_GeoColor/default/default/GoogleMapsCompatible_Level7/"
            "{z}/{y}/{x}.png"
        ),
        "maxNativeZoom": 7,
        "attribution": "NASA GIBS / NOAA GOES-East",
    },
    {
        "id": "goes-west",
        "title": "GOES-West · GeoColor",
        "description": "Pacifico oriental. Imagen cada 10 minutos.",
        "coverage": "Pacifico",
        "kind": "Tiempo real",
        "template": (
            "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
            "GOES-West_ABI_GeoColor/default/default/GoogleMapsCompatible_Level7/"
            "{z}/{y}/{x}.png"
        ),
        "maxNativeZoom": 7,
        "attribution": "NASA GIBS / NOAA GOES-West",
    },
    {
        "id": "himawari",
        "title": "Himawari · Infrarrojo",
        "description": "Asia y Pacifico occidental. Imagen cada 10 minutos.",
        "coverage": "Asia / Pacifico",
        "kind": "Tiempo real",
        "template": (
            "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
            "Himawari_AHI_Band13_Clean_Infrared/default/default/"
            "GoogleMapsCompatible_Level6/{z}/{y}/{x}.png"
        ),
        "maxNativeZoom": 6,
        "attribution": "NASA GIBS / JMA Himawari",
    },
    {
        "id": "modis",
        "title": "MODIS Terra · Color natural",
        "description": "Cobertura mundial. Imagen diaria (no cada 10 minutos).",
        "coverage": "Global",
        "kind": "Diario",
        "template": (
            "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/"
            "MODIS_Terra_CorrectedReflectance_TrueColor/default/"
            "GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpeg"
        ),
        "maxNativeZoom": 9,
        "attribution": "NASA GIBS / MODIS Terra",
    },
]

SOURCES = [
    {"name": "RainViewer", "use": "Radar de lluvia y pronostico inmediato", "url": "https://www.rainviewer.com/"},
    {"name": "Open-Meteo", "use": "Viento, lluvia, nubosidad, temperatura y CAPE", "url": "https://open-meteo.com/"},
    {"name": "NASA GIBS", "use": "Imagenes satelitales", "url": "https://www.earthdata.nasa.gov/gibs"},
    {"name": "USGS", "use": "Sismos", "url": "https://earthquake.usgs.gov/"},
    {"name": "OpenStreetMap / Nominatim / Photon", "use": "Mapa y nombres de lugares", "url": "https://www.openstreetmap.org/"},
]

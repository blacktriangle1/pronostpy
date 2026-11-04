from __future__ import annotations

import asyncio
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from .cache import cache
from .config import (
    ACTIVE_STORMS_URL,
    NHC_TWD_ARCHIVE_URL,
    NOMINATIM_REVERSE_URL,
    NOMINATIM_SEARCH_URL,
    OPEN_METEO_URL,
    PHOTON_REVERSE_URL,
    PHOTON_SEARCH_URL,
    RAINVIEWER_MAPS_URL,
    SATELLITE_LAYERS,
    TROPICAL_WAVE_ENTRY_LON,
    TROPICAL_WAVE_EXIT_LON,
    TROPICAL_WAVE_LOOKBACK_DAYS,
    USGS_QUERY_URL,
)
from .geo import (
    bbox_from_radius,
    bearing_deg,
    bearing_es,
    build_narration,
    compass_es,
    compass_short,
    describe_weather,
    haversine_km,
    storm_risk,
)


class UpstreamError(RuntimeError):
    """Error al consultar un servicio externo."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


_nominatim_lock = asyncio.Lock()
_last_nominatim = 0.0


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _round(value: Any, digits: int = 1) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _epoch_ms_to_iso(value: Any) -> str | None:
    try:
        return (
            datetime.fromtimestamp(float(value) / 1000, timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    except (TypeError, ValueError, OSError):
        return None


def _at(container: dict[str, Any], key: str, index: int) -> Any:
    values = container.get(key) or []
    if 0 <= index < len(values):
        return values[index]
    return None


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any] | None = None,
) -> Any:
    host = urlsplit(url).netloc
    try:
        response = await client.get(url, params=params)
    except httpx.TimeoutException as exc:
        raise UpstreamError(f"{host} tardó demasiado en responder.") from exc
    except httpx.HTTPError as exc:
        raise UpstreamError(f"No se pudo conectar con {host}.") from exc

    if response.status_code >= 400:
        raise UpstreamError(f"{host} respondió con el código {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise UpstreamError(f"{host} devolvió una respuesta que no es JSON.") from exc


async def _nominatim_request(
    client: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
) -> Any:
    """Respeta el límite de 1 petición por segundo de Nominatim."""
    global _last_nominatim
    async with _nominatim_lock:
        elapsed = time.monotonic() - _last_nominatim
        if elapsed < 1.1:
            await asyncio.sleep(1.1 - elapsed)
        try:
            response = await client.get(url, params=params)
        except httpx.TimeoutException as exc:
            raise UpstreamError("Nominatim tardó demasiado en responder.") from exc
        except httpx.HTTPError as exc:
            raise UpstreamError("No se pudo conectar con Nominatim.") from exc
        finally:
            _last_nominatim = time.monotonic()

    if response.status_code == 403:
        raise UpstreamError("Nominatim rechazó la solicitud (403).")
    if response.status_code >= 400:
        raise UpstreamError(f"Nominatim respondió con el código {response.status_code}.")
    try:
        return response.json()
    except ValueError as exc:
        raise UpstreamError("Nominatim devolvió una respuesta inválida.") from exc


# ---------------------------------------------------------------------------
# Radar de lluvia
# ---------------------------------------------------------------------------


async def get_radar(client: httpx.AsyncClient) -> dict[str, Any]:
    async def fetch() -> dict[str, Any]:
        data = await _get_json(client, RAINVIEWER_MAPS_URL)
        host = data.get("host") or "https://tilecache.rainviewer.com"
        radar = data.get("radar") or {}
        frames: list[dict[str, Any]] = []

        for kind, items in (
            ("past", radar.get("past") or []),
            ("nowcast", radar.get("nowcast") or []),
        ):
            for item in items:
                path = item.get("path")
                moment = item.get("time")
                if not path or moment is None:
                    continue
                frames.append(
                    {
                        "time": int(moment),
                        "kind": kind,
                        "path": path,
                        "url": f"{host}{path}/256/{{z}}/{{x}}/{{y}}/4/1_1.png",
                    }
                )

        frames.sort(key=lambda frame: frame["time"])
        if not frames:
            raise UpstreamError("RainViewer no devolvió imágenes de radar.")

        return {
            "generated": data.get("generated"),
            "latestTime": frames[-1]["time"],
            "frames": frames,
            "colorScheme": 4,
            "options": "1_1",
            "tileSize": 256,
            "maxNativeZoom": 7,
            "attribution": "Radar: RainViewer",
            "updatedAt": iso_now(),
        }

    return await cache.get_or_set("radar:rainviewer", 120, fetch)


# ---------------------------------------------------------------------------
# Satélite
# ---------------------------------------------------------------------------


def _recommend_satellite(lon: float) -> str:
    if lon >= 100 or lon <= -160:
        return "himawari"
    if -105 <= lon <= -10:
        return "goes-east"
    if -160 < lon < -105:
        return "goes-west"
    return "modis"


_SATELLITE_PROBES: dict[str, tuple[int, int, int]] = {
    "goes-east": (3, 1, 3),
    "goes-west": (3, 0, 3),
    "himawari": (3, 6, 3),
    "modis": (3, 1, 3),
}

_SATELLITE_FRAME_COUNT = 5
_SATELLITE_FRAME_STEP_MIN = 5
_SATELLITE_FRAME_LOOKBACK = 18
_SATELLITE_FRAME_MIN_GAP_MIN = 8


def _satellite_time_template(layer: dict[str, Any], when: str) -> str:
    template = layer.get("template", "")
    if "/default/default/" in template:
        return template.replace("/default/default/", f"/default/{when}/", 1)
    marker = "/default/GoogleMapsCompatible"
    if marker in template:
        return template.replace(marker, f"/default/{when}/GoogleMapsCompatible", 1)
    return template


def _satellite_tile_url(template: str, z: int, x: int, y: int) -> str:
    return (
        template.replace("{z}", str(z))
        .replace("{x}", str(x))
        .replace("{y}", str(y))
    )


def _pick_satellite_times(
    times: list[datetime],
    count: int = _SATELLITE_FRAME_COUNT,
    min_gap_minutes: int = _SATELLITE_FRAME_MIN_GAP_MIN,
) -> list[datetime]:
    selected: list[datetime] = []
    for when in sorted(times, reverse=True):
        if selected and (selected[-1] - when).total_seconds() < min_gap_minutes * 60:
            continue
        selected.append(when)
        if len(selected) >= count:
            break
    selected.reverse()
    return selected


def get_satellite(lat: float, lon: float) -> dict[str, Any]:
    layers = [dict(layer) for layer in SATELLITE_LAYERS]
    by_id = {layer["id"]: layer for layer in layers}
    recommended = _recommend_satellite(lon)
    selected = by_id.get(recommended, layers[-1])
    return {
        "recommended": selected["id"],
        "selected": selected,
        "layers": layers,
        "note": (
            "GOES y Himawari se actualizan cada 10 minutos; la app anima las últimas "
            "imágenes. MODIS es una imagen diaria y sirve como respaldo global."
        ),
        "updatedAt": iso_now(),
    }


async def get_satellite_frames(
    client: httpx.AsyncClient,
    layer_id: str,
) -> dict[str, Any]:
    layer = next((item for item in SATELLITE_LAYERS if item["id"] == layer_id), None)
    if layer is None:
        raise UpstreamError("Capa satelital desconocida.")

    if layer.get("kind", "").lower() == "diario":
        return {
            "layer": layer_id,
            "frames": [],
            "animated": False,
            "note": "MODIS es una imagen diaria y no tiene animación de nubes.",
            "updatedAt": iso_now(),
        }

    async def fetch() -> dict[str, Any]:
        z, x, y = _SATELLITE_PROBES.get(layer_id, (3, 1, 3))
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        now -= timedelta(minutes=now.minute % 5)
        candidates = [
            now - timedelta(minutes=_SATELLITE_FRAME_STEP_MIN * (index + 1))
            for index in range(_SATELLITE_FRAME_LOOKBACK)
        ]

        async def available(when: datetime) -> datetime | None:
            stamp = when.strftime("%Y-%m-%dT%H:%M:%SZ")
            url = _satellite_tile_url(
                _satellite_time_template(layer, stamp), z, x, y
            )
            try:
                response = await client.head(url, timeout=8)
            except (httpx.HTTPError, ValueError):
                return None
            if response.status_code != 200:
                return None
            length = response.headers.get("content-length")
            if length:
                try:
                    if int(length) < 500:
                        return None
                except ValueError:
                    pass
            return when

        valid = [
            item
            for item in await asyncio.gather(
                *(available(when) for when in candidates)
            )
            if item is not None
        ]
        picked = _pick_satellite_times(valid)
        frames = [
            {
                "time": when.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "url": _satellite_time_template(
                    layer, when.strftime("%Y-%m-%dT%H:%M:%SZ")
                ),
            }
            for when in picked
        ]
        return {
            "layer": layer_id,
            "frames": frames,
            "animated": len(frames) > 1,
            "frameMs": 900,
            "note": (
                "Secuencia de las últimas imágenes disponibles. "
                "El barrido revela las nubes a su paso."
            ),
            "updatedAt": iso_now(),
        }

    return await cache.get_or_set(f"satframes:{layer_id}", 300, fetch)


# ---------------------------------------------------------------------------
# Geocodificación
# ---------------------------------------------------------------------------


def _address_parts(address: dict[str, Any]) -> list[str]:
    parts: list[str] = []
    for key in (
        "neighbourhood",
        "suburb",
        "village",
        "town",
        "city",
        "municipality",
        "county",
        "state",
        "country",
    ):
        value = address.get(key)
        if value and value not in parts:
            parts.append(str(value))
    return parts


def _city_from(address: dict[str, Any]) -> str | None:
    for key in ("city", "town", "village", "municipality", "county"):
        value = address.get(key)
        if value:
            return str(value)
    return None


async def _photon_reverse(client: httpx.AsyncClient, lat: float, lon: float) -> dict[str, Any]:
    data = await _get_json(
        client,
        PHOTON_REVERSE_URL,
        {"lat": round(lat, 6), "lon": round(lon, 6)},
    )
    features = data.get("features") or []
    props = (features[0].get("properties") or {}) if features else {}
    parts: list[str] = []
    for key in ("name", "city", "state", "country"):
        value = props.get(key)
        if value and value not in parts:
            parts.append(str(value))
    display = ", ".join(parts[:4]) or f"{lat:.4f}, {lon:.4f}"
    return {
        "display": display,
        "city": props.get("city") or props.get("name"),
        "state": props.get("state"),
        "country": props.get("country"),
        "countryCode": (props.get("countrycode") or "").lower() or None,
        "type": props.get("type"),
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "source": "Photon",
    }


async def reverse_geocode(client: httpx.AsyncClient, lat: float, lon: float) -> dict[str, Any]:
    key = f"geo:reverse:{lat:.4f}:{lon:.4f}"

    async def fetch() -> dict[str, Any]:
        params = {
            "format": "jsonv2",
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "zoom": 14,
            "addressdetails": 1,
            "accept-language": "es",
        }
        try:
            data = await _nominatim_request(client, NOMINATIM_REVERSE_URL, params)
        except UpstreamError:
            return await _photon_reverse(client, lat, lon)

        address = data.get("address") or {}
        parts = _address_parts(address)
        if not parts:
            name = data.get("name") or data.get("display_name")
            if name:
                parts.append(str(name).split(",")[0].strip())
        display = ", ".join(parts[:4]) or f"{lat:.4f}, {lon:.4f}"

        return {
            "display": display,
            "city": _city_from(address),
            "state": address.get("state"),
            "country": address.get("country"),
            "countryCode": (address.get("country_code") or "").lower() or None,
            "type": data.get("type"),
            "lat": round(lat, 6),
            "lon": round(lon, 6),
            "source": "Nominatim / OpenStreetMap",
        }

    return await cache.get_or_set(key, 86_400, fetch)


def _normalize_nominatim_result(item: dict[str, Any]) -> dict[str, Any] | None:
    try:
        lat = float(item.get("lat"))
        lon = float(item.get("lon"))
    except (TypeError, ValueError):
        return None
    display = item.get("display_name") or item.get("name") or f"{lat:.4f}, {lon:.4f}"
    pieces = [part.strip() for part in str(display).split(",") if part.strip()]
    short = ", ".join(pieces[:3]) if pieces else str(display)
    return {
        "display": str(display)[:180],
        "short": short[:120],
        "lat": round(lat, 6),
        "lon": round(lon, 6),
        "type": item.get("type"),
        "category": item.get("class") or item.get("category"),
        "source": "Nominatim",
    }


async def search_places(client: httpx.AsyncClient, query: str, limit: int = 6) -> list[dict[str, Any]]:
    query = " ".join(query.split())
    key = f"geo:search:{query.lower()}:{limit}"

    async def fetch() -> list[dict[str, Any]]:
        params = {
            "q": query,
            "format": "jsonv2",
            "limit": limit,
            "addressdetails": 1,
            "accept-language": "es",
        }
        try:
            data = await _nominatim_request(client, NOMINATIM_SEARCH_URL, params)
            if isinstance(data, list):
                results = [r for r in (_normalize_nominatim_result(item) for item in data) if r]
                if results:
                    return results
        except UpstreamError:
            pass

        try:
            data = await _get_json(
                client,
                PHOTON_SEARCH_URL,
                {"q": query, "limit": limit},
            )
        except UpstreamError as exc:
            raise UpstreamError("No se pudo buscar el lugar en este momento.") from exc

        results = []
        for feature in data.get("features") or []:
            props = feature.get("properties") or {}
            coords = ((feature.get("geometry") or {}).get("coordinates") or [])
            if len(coords) < 2:
                continue
            lon, lat = float(coords[0]), float(coords[1])
            parts = []
            for field in ("name", "city", "state", "country"):
                value = props.get(field)
                if value and value not in parts:
                    parts.append(str(value))
            display = ", ".join(parts[:4]) or f"{lat:.4f}, {lon:.4f}"
            results.append(
                {
                    "display": display[:180],
                    "short": ", ".join(parts[:3])[:120] or display[:120],
                    "lat": round(lat, 6),
                    "lon": round(lon, 6),
                    "type": props.get("type"),
                    "category": props.get("osm_key"),
                    "source": "Photon",
                }
            )
        return results

    return await cache.get_or_set(key, 600, fetch)


# ---------------------------------------------------------------------------
# Meteorología
# ---------------------------------------------------------------------------


def _normalize_weather(data: dict[str, Any], lat: float, lon: float) -> dict[str, Any]:
    if data.get("error"):
        raise UpstreamError(str(data.get("reason") or "Open-Meteo devolvió un error."))

    current = data.get("current") or {}
    hourly = data.get("hourly") or {}
    daily = data.get("daily") or {}

    current_time = str(current.get("time") or "")
    times = hourly.get("time") or []
    index = 0
    for i, moment in enumerate(times):
        if str(moment) <= current_time:
            index = i
        else:
            break

    cape = _at(hourly, "cape", index)
    code = current.get("weather_code")
    description, icon = describe_weather(code)
    risk = storm_risk(code, cape)

    next_probability = _at(hourly, "precipitation_probability", index + 1)
    if next_probability is None:
        next_probability = _at(hourly, "precipitation_probability", index)

    hourly_out: list[dict[str, Any]] = []
    for i in range(index, min(index + 12, len(times))):
        item_code = _at(hourly, "weather_code", i)
        item_description, item_icon = describe_weather(item_code)
        hourly_out.append(
            {
                "time": times[i],
                "temperature": _round(_at(hourly, "temperature_2m", i), 1),
                "precipitationProbability": _round(
                    _at(hourly, "precipitation_probability", i), 0
                ),
                "precipitation": _round(_at(hourly, "precipitation", i), 2),
                "weatherCode": item_code,
                "description": item_description,
                "icon": item_icon,
                "cape": _round(_at(hourly, "cape", i), 0),
                "windSpeed": _round(_at(hourly, "wind_speed_10m", i), 1),
                "windDirection": _round(_at(hourly, "wind_direction_10m", i), 0),
            }
        )

    daily_out: list[dict[str, Any]] = []
    dates = daily.get("time") or []
    for i in range(min(7, len(dates))):
        item_code = _at(daily, "weather_code", i)
        item_description, item_icon = describe_weather(item_code)
        daily_out.append(
            {
                "date": dates[i],
                "temperatureMax": _round(_at(daily, "temperature_2m_max", i), 1),
                "temperatureMin": _round(_at(daily, "temperature_2m_min", i), 1),
                "precipitationProbability": _round(
                    _at(daily, "precipitation_probability_max", i), 0
                ),
                "weatherCode": item_code,
                "description": item_description,
                "icon": item_icon,
                "sunrise": _at(daily, "sunrise", i),
                "sunset": _at(daily, "sunset", i),
            }
        )

    return {
        "coordinates": {"lat": round(lat, 6), "lon": round(lon, 6)},
        "timezone": data.get("timezone"),
        "timezoneAbbreviation": data.get("timezone_abbreviation"),
        "utcOffsetSeconds": data.get("utc_offset_seconds"),
        "elevation": _round(data.get("elevation"), 0),
        "current": {
            "time": current.get("time"),
            "temperature": _round(current.get("temperature_2m"), 1),
            "apparentTemperature": _round(current.get("apparent_temperature"), 1),
            "humidity": _round(current.get("relative_humidity_2m"), 0),
            "precipitation": _round(current.get("precipitation"), 2),
            "rain": _round(current.get("rain"), 2),
            "showers": _round(current.get("showers"), 2),
            "snowfall": _round(current.get("snowfall"), 2),
            "weatherCode": code,
            "description": description,
            "icon": icon,
            "isDay": bool(current.get("is_day")),
            "cloudCover": _round(current.get("cloud_cover"), 0),
            "pressure": _round(current.get("pressure_msl"), 1),
            "surfacePressure": _round(current.get("surface_pressure"), 1),
            "windSpeed": _round(current.get("wind_speed_10m"), 1),
            "windGust": _round(current.get("wind_gusts_10m"), 1),
            "windDirection": _round(current.get("wind_direction_10m"), 0),
            "windDirectionText": compass_es(current.get("wind_direction_10m")),
            "windDirectionShort": compass_short(current.get("wind_direction_10m")),
            "cape": _round(cape, 0),
            "stormRisk": risk,
        },
        "nextPrecipitationProbability": _round(next_probability, 0),
        "hourly": hourly_out,
        "daily": daily_out,
        "updatedAt": iso_now(),
    }


async def get_weather(client: httpx.AsyncClient, lat: float, lon: float) -> dict[str, Any]:
    key = f"weather:{lat:.3f}:{lon:.3f}"

    async def fetch() -> dict[str, Any]:
        params = {
            "latitude": round(lat, 4),
            "longitude": round(lon, 4),
            "current": (
                "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,"
                "precipitation,rain,showers,snowfall,weather_code,cloud_cover,"
                "pressure_msl,surface_pressure,wind_speed_10m,wind_direction_10m,"
                "wind_gusts_10m"
            ),
            "hourly": (
                "temperature_2m,precipitation_probability,precipitation,weather_code,"
                "cape,wind_speed_10m,wind_direction_10m"
            ),
            "daily": (
                "weather_code,temperature_2m_max,temperature_2m_min,"
                "precipitation_probability_max,sunrise,sunset"
            ),
            "timezone": "auto",
            "forecast_days": 7,
            "wind_speed_unit": "kmh",
        }
        data = await _get_json(client, OPEN_METEO_URL, params)
        return _normalize_weather(data, lat, lon)

    return await cache.get_or_set(key, 300, fetch)


# ---------------------------------------------------------------------------
# Sismos
# ---------------------------------------------------------------------------


def _normalize_quake(feature: dict[str, Any]) -> dict[str, Any] | None:
    props = feature.get("properties") or {}
    geometry = feature.get("geometry") or {}
    coords = geometry.get("coordinates") or []
    if len(coords) < 2:
        return None
    try:
        lon = float(coords[0])
        lat = float(coords[1])
    except (TypeError, ValueError):
        return None

    mag = props.get("mag")
    try:
        magnitude = float(mag) if mag is not None else None
    except (TypeError, ValueError):
        magnitude = None

    return {
        "id": feature.get("id"),
        "mag": round(magnitude, 1) if magnitude is not None else None,
        "magType": props.get("magType"),
        "place": props.get("place") or "Ubicación sin nombre",
        "time": _epoch_ms_to_iso(props.get("time")),
        "updated": _epoch_ms_to_iso(props.get("updated")),
        "depthKm": _round(coords[2] if len(coords) > 2 else None, 1),
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "url": props.get("url"),
        "tsunami": bool(props.get("tsunami")),
        "felt": props.get("felt"),
        "alert": props.get("alert"),
        "status": props.get("status"),
    }


async def get_quakes(
    client: httpx.AsyncClient,
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    min_magnitude: float = 1.0,
    days: int = 1,
    limit: int = 300,
) -> dict[str, Any]:
    key = (
        f"quakes:{min_lat:.1f}:{max_lat:.1f}:{min_lon:.1f}:{max_lon:.1f}:"
        f"{min_magnitude}:{days}:{limit}"
    )

    async def fetch() -> dict[str, Any]:
        start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        params = {
            "format": "geojson",
            "starttime": start,
            "minmagnitude": min_magnitude,
            "minlatitude": round(min_lat, 4),
            "maxlatitude": round(max_lat, 4),
            "minlongitude": round(min_lon, 4),
            "maxlongitude": round(max_lon, 4),
            "limit": limit,
            "orderby": "time",
            "eventtype": "earthquake",
        }
        data = await _get_json(client, USGS_QUERY_URL, params)
        events = []
        for feature in data.get("features") or []:
            normalized = _normalize_quake(feature)
            if normalized:
                events.append(normalized)
        events.sort(key=lambda event: event.get("time") or "", reverse=True)

        magnitudes = [event["mag"] for event in events if event.get("mag") is not None]
        return {
            "events": events,
            "count": len(events),
            "maxMagnitude": max(magnitudes) if magnitudes else None,
            "days": days,
            "minMagnitudeFilter": min_magnitude,
            "updatedAt": iso_now(),
        }

    return await cache.get_or_set(key, 60, fetch)


async def get_near_quakes(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    radius_km: float = 500,
    days: int = 7,
    min_magnitude: float = 1.0,
) -> list[dict[str, Any]]:
    min_lat, min_lon, max_lat, max_lon = bbox_from_radius(lat, lon, radius_km)
    data = await get_quakes(
        client,
        min_lat,
        max_lat,
        min_lon,
        max_lon,
        min_magnitude=min_magnitude,
        days=days,
        limit=500,
    )

    events: list[dict[str, Any]] = []
    for event in data.get("events") or []:
        distance = haversine_km(lat, lon, event["lat"], event["lon"])
        if distance > radius_km:
            continue
        enriched = dict(event)
        enriched["distanceKm"] = round(distance, 1)
        enriched["direction"] = bearing_es(lat, lon, event["lat"], event["lon"])
        events.append(enriched)

    events.sort(key=lambda event: (event["distanceKm"], -(event.get("mag") or 0)))
    return events[:12]


# ---------------------------------------------------------------------------
# Huracanes y tormentas activas
# ---------------------------------------------------------------------------

_STORM_BASINS = {
    "AL": "Atlántico",
    "EP": "Pacífico este",
    "CP": "Pacífico central",
    "WP": "Pacífico oeste",
    "IO": "Océano Índico",
    "SH": "Hemisferio sur",
    "SL": "Atlántico sur",
}

_STORM_CLASS_ES = {
    "tropical depression": "Depresión tropical",
    "tropical storm": "Tormenta tropical",
    "subtropical depression": "Depresión subtropical",
    "subtropical storm": "Tormenta subtropical",
    "hurricane": "Huracán",
    "typhoon": "Tifón",
    "super typhoon": "Supertifón",
    "very severe cyclonic storm": "Tormenta ciclónica muy severa",
    "severe cyclonic storm": "Tormenta ciclónica severa",
    "cyclonic storm": "Tormenta ciclónica",
    "deep depression": "Depresión profunda",
    "depression": "Depresión",
    "tropical disturbance": "Perturbación tropical",
    "post-tropical cyclone": "Ciclón postropical",
    "remnants": "Remanentes",
}


def _storm_key(props: dict[str, Any]) -> str | None:
    basin = str(props.get("BASIN") or "").strip().upper()
    number = props.get("STORMNUM")
    if basin and number is not None:
        try:
            return f"{basin}{int(float(number))}"
        except (TypeError, ValueError):
            pass
    name = str(props.get("STORMNAME") or "").strip().upper()
    if name:
        return f"{basin or 'XX'}:{name}"
    return None


def _storm_time_from_label(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt, size in (("%Y-%m-%d %I:%M %p", 19), ("%Y-%m-%d %H:%M", 16)):
        try:
            moment = datetime.strptime(text[:size], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return moment.isoformat(timespec="seconds").replace("+00:00", "Z")
    return None


def _storm_wind_kmh(knots: Any) -> float | None:
    value = _round(knots, 1)
    if value is None:
        return None
    return round(value * 1.852, 0)


def _storm_pressure(value: Any) -> float | None:
    pressure = _round(value, 0)
    if pressure is None or pressure <= 0 or pressure >= 9999:
        return None
    return pressure


def _storm_class_es(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return _STORM_CLASS_ES.get(text.casefold(), text)


def _storm_category(basin: str, wind_kt: Any) -> str:
    knots = _round(wind_kt, 0)
    if knots is None:
        return "Tormenta tropical"
    if basin in ("AL", "EP", "CP"):
        for limit, label in (
            (137, "Huracán categoría 5"),
            (113, "Huracán categoría 4"),
            (96, "Huracán categoría 3"),
            (83, "Huracán categoría 2"),
            (64, "Huracán categoría 1"),
        ):
            if knots >= limit:
                return label
    elif basin == "WP":
        if knots >= 130:
            return "Supertifón"
        if knots >= 64:
            return "Tifón"
    elif basin in ("IO", "SH"):
        if knots >= 64:
            return "Ciclón tropical"
    if knots >= 34:
        return "Tormenta tropical"
    if knots >= 25:
        return "Depresión tropical"
    return "Perturbación tropical"


def _storm_alert(wind_kt: Any) -> dict[str, str]:
    knots = _round(wind_kt, 0) or 0
    if knots >= 96:
        return {
            "level": "alta",
            "label": "Peligro alto",
            "detail": "Huracán o tifón intenso",
        }
    if knots >= 64:
        return {"level": "alta", "label": "Peligro alto", "detail": "Huracán o tifón"}
    if knots >= 34:
        return {
            "level": "moderada",
            "label": "Peligro moderado",
            "detail": "Tormenta tropical",
        }
    return {
        "level": "baja",
        "label": "Peligro bajo",
        "detail": "Depresión o perturbación",
    }


def _latlon_line(line: Any) -> list[list[float]]:
    points: list[list[float]] = []
    for coordinate in line or []:
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) < 2:
            continue
        try:
            points.append([round(float(coordinate[1]), 4), round(float(coordinate[0]), 4)])
        except (TypeError, ValueError):
            continue
    return points


def _hours_between(start: Any, end: Any) -> float | None:
    if not start or not end:
        return None
    try:
        first = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        second = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (second - first).total_seconds() / 3600


def _new_storm(key: str, props: dict[str, Any]) -> dict[str, Any]:
    basin = str(props.get("BASIN") or "").strip().upper()
    return {
        "id": key,
        "name": str(props.get("STORMNAME") or "").strip() or "Tormenta sin nombre",
        "basin": basin,
        "basinName": _STORM_BASINS.get(basin, basin or "Océano"),
        "classification": None,
        "classificationEn": None,
        "category": None,
        "windKt": None,
        "windKmh": None,
        "maxWindKt": None,
        "maxWindKmh": None,
        "pressureHpa": None,
        "lat": None,
        "lon": None,
        "time": None,
        "movement": {
            "direction": "sin dirección",
            "directionShort": "--",
            "bearing": None,
            "speedKmh": None,
        },
        "observedTrack": [],
        "forecastTrack": [],
        "cone": [],
        "watches": [],
        "alert": {
            "level": "baja",
            "label": "Peligro bajo",
            "detail": "Sin datos de viento",
        },
        "source": "NHC / Esri",
        "url": "https://www.nhc.noaa.gov/",
    }


def _storm_features(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    features = data.get("features")
    return features if isinstance(features, list) else []


async def get_storms(client: httpx.AsyncClient) -> dict[str, Any]:
    """Tormentas activas en el mundo (NHC y JTWC vía Esri)."""

    async def fetch() -> dict[str, Any]:
        async def layer(layer_id: int) -> Any:
            return await _get_json(
                client,
                f"{ACTIVE_STORMS_URL}/{layer_id}/query",
                {
                    "where": "1=1",
                    "outFields": "*",
                    "f": "geojson",
                    "resultRecordCount": 2000,
                },
            )

        forecast_pos, observed_pos, cones, watches = await asyncio.gather(
            layer(0),
            layer(1),
            layer(4),
            layer(5),
            return_exceptions=True,
        )

        if isinstance(forecast_pos, Exception) and isinstance(observed_pos, Exception):
            raise UpstreamError("No se pudo consultar el servicio de huracanes.")

        storms: dict[str, dict[str, Any]] = {}

        for feature in _storm_features(forecast_pos):
            props = feature.get("properties") or {}
            key = _storm_key(props)
            if not key:
                continue
            storm = storms.setdefault(key, _new_storm(key, props))
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coordinates) < 2:
                continue
            try:
                lon, lat = float(coordinates[0]), float(coordinates[1])
            except (TypeError, ValueError):
                continue
            wind = props.get("MAXWIND")
            storm["forecastTrack"].append(
                {
                    "lat": round(lat, 4),
                    "lon": round(lon, 4),
                    "time": _storm_time_from_label(props.get("FLDATELBL")),
                    "windKt": _round(wind, 0),
                    "windKmh": _storm_wind_kmh(wind),
                    "classification": _storm_class_es(
                        props.get("ITCDVLP") or props.get("TCDVLP")
                    ),
                    "classificationEn": props.get("ITCDVLP") or props.get("TCDVLP"),
                    "pressureHpa": _storm_pressure(props.get("MSLP")),
                    "label": props.get("FLDATELBL"),
                }
            )

        for feature in _storm_features(observed_pos):
            props = feature.get("properties") or {}
            key = _storm_key(props)
            if not key:
                continue
            storm = storms.setdefault(key, _new_storm(key, props))
            coordinates = (feature.get("geometry") or {}).get("coordinates") or []
            if len(coordinates) < 2:
                continue
            try:
                lon, lat = float(coordinates[0]), float(coordinates[1])
            except (TypeError, ValueError):
                continue
            wind = props.get("INTENSITY")
            storm["observedTrack"].append(
                {
                    "lat": round(lat, 4),
                    "lon": round(lon, 4),
                    "time": _epoch_ms_to_iso(props.get("DTG")),
                    "windKt": _round(wind, 0),
                    "windKmh": _storm_wind_kmh(wind),
                    "pressureHpa": _storm_pressure(props.get("MSLP")),
                }
            )

        for feature in _storm_features(cones):
            props = feature.get("properties") or {}
            key = _storm_key(props)
            if not key:
                continue
            storm = storms.setdefault(key, _new_storm(key, props))
            geometry = feature.get("geometry") or {}
            geometry_type = geometry.get("type")
            coordinates = geometry.get("coordinates") or []
            polygons: list[Any] = []
            if geometry_type == "Polygon":
                polygons = [coordinates]
            elif geometry_type == "MultiPolygon":
                polygons = coordinates
            for polygon in polygons:
                for ring in polygon or []:
                    points = _latlon_line(ring)
                    if len(points) >= 3:
                        storm["cone"].append(points)

        for feature in _storm_features(watches):
            props = feature.get("properties") or {}
            key = _storm_key(props)
            if not key:
                continue
            storm = storms.setdefault(key, _new_storm(key, props))
            geometry = feature.get("geometry") or {}
            geometry_type = geometry.get("type")
            coordinates = geometry.get("coordinates") or []
            lines: list[Any] = []
            if geometry_type == "LineString":
                lines = [coordinates]
            elif geometry_type == "MultiLineString":
                lines = coordinates
            for line in lines:
                points = _latlon_line(line)
                if len(points) >= 2:
                    storm["watches"].append(points)

        result: list[dict[str, Any]] = []
        for storm in storms.values():
            storm["forecastTrack"].sort(key=lambda point: point.get("time") or "")
            storm["observedTrack"].sort(key=lambda point: point.get("time") or "")
            observed = storm["observedTrack"]
            forecast = storm["forecastTrack"]

            current = observed[-1] if observed else (forecast[0] if forecast else None)
            if current:
                storm["lat"] = current.get("lat")
                storm["lon"] = current.get("lon")
                storm["time"] = current.get("time")
                storm["windKt"] = current.get("windKt")
                storm["windKmh"] = current.get("windKmh")

            if forecast:
                first = forecast[0]
                storm["classificationEn"] = first.get("classificationEn")
                storm["classification"] = first.get("classification")
                storm["pressureHpa"] = first.get("pressureHpa")
                peak = max((point.get("windKt") or 0) for point in forecast)
                if peak:
                    storm["maxWindKt"] = round(peak, 0)
                    storm["maxWindKmh"] = _storm_wind_kmh(peak)

            if storm["windKt"] is None and forecast:
                storm["windKt"] = forecast[0].get("windKt")
                storm["windKmh"] = forecast[0].get("windKmh")

            if not storm["classification"]:
                storm["classification"] = _storm_category(storm["basin"], storm["windKt"])
            storm["category"] = _storm_category(storm["basin"], storm["windKt"])

            track = observed if len(observed) >= 2 else forecast
            if len(track) >= 2:
                previous, latest = track[-2], track[-1]
                distance = haversine_km(
                    previous["lat"], previous["lon"], latest["lat"], latest["lon"]
                )
                hours = _hours_between(previous.get("time"), latest.get("time"))
                speed = distance / hours if hours and hours > 0 else None
                bearing = bearing_deg(
                    previous["lat"], previous["lon"], latest["lat"], latest["lon"]
                )
                storm["movement"] = {
                    "direction": compass_es(bearing),
                    "directionShort": compass_short(bearing),
                    "bearing": round(bearing, 0),
                    "speedKmh": round(speed, 1) if speed is not None else None,
                }

            storm["alert"] = _storm_alert(storm["windKt"])

            if storm["lat"] is not None and storm["lon"] is not None:
                result.append(storm)

        result.sort(key=lambda storm: (-(storm.get("maxWindKt") or 0), storm.get("name") or ""))

        return {
            "storms": result,
            "count": len(result),
            "attribution": "Huracanes: NHC / Esri (incluye JTWC)",
            "updatedAt": iso_now(),
        }

    return await cache.get_or_set("storms:active", 300, fetch)


def storms_with_distance(
    storms: list[dict[str, Any]],
    lat: float,
    lon: float,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for storm in storms or []:
        if storm.get("lat") is None or storm.get("lon") is None:
            continue
        item = dict(storm)
        item["distanceKm"] = round(
            haversine_km(lat, lon, float(storm["lat"]), float(storm["lon"])), 1
        )
        item["direction"] = bearing_es(
            lat, lon, float(storm["lat"]), float(storm["lon"])
        )
        enriched.append(item)
    enriched.sort(key=lambda storm: storm["distanceKm"])
    return enriched[:limit] if limit else enriched


async def get_near_storms(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    limit: int = 4,
) -> list[dict[str, Any]]:
    data = await get_storms(client)
    return storms_with_distance(data.get("storms") or [], lat, lon, limit=limit)


# ---------------------------------------------------------------------------
# Ondas tropicales (NHC Atlantic Tropical Weather Discussion)
# ---------------------------------------------------------------------------

_WAVE_SECTION_RE = re.compile(
    r"\.\.\.TROPICAL WAVES\.\.\.(.*?)(?=\n\.\.\.[A-Z][A-Z /]+\.\.\.|\Z)",
    re.S,
)
_WAVE_LON_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*W")
_WAVE_LAT_RE = re.compile(r"(?<![\d.])(\d{1,2}(?:\.\d+)?)\s*N")
_WAVE_SPEED_RE = re.compile(
    r"moving[^.]{0,90}?(\d{1,3}(?:\.\d+)?)"
    r"(?:\s*(?:to|through|-|–)\s*(\d{1,3}(?:\.\d+)?))?\s*kt",
    re.I,
)
_WAVE_SPEED_ANY_RE = re.compile(
    r"(\d{1,3}(?:\.\d+)?)(?:\s*(?:to|through|-|–)\s*(\d{1,3}(?:\.\d+)?))?\s*kt",
    re.I,
)


def _wave_moment(stamp: str) -> datetime | None:
    try:
        return datetime.strptime(stamp, "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _wave_iso(moment: datetime | None) -> str | None:
    if moment is None:
        return None
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def _wave_section(text: str) -> str | None:
    match = _WAVE_SECTION_RE.search(text or "")
    return match.group(1).strip() if match else None


def _wave_paragraphs(section: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", section or "") if part.strip()]


def _wave_first_longitude(text: str) -> float | None:
    match = _WAVE_LON_RE.search(text or "")
    if not match:
        return None
    try:
        return -round(float(match.group(1)), 1)
    except ValueError:
        return None


def _wave_lat_range(text: str) -> tuple[float, float]:
    sentence = (text or "").split(".")[0]
    latitudes: list[float] = []
    for value in _WAVE_LAT_RE.findall(sentence):
        try:
            latitudes.append(float(value))
        except ValueError:
            continue
    if len(latitudes) >= 2:
        low, high = min(latitudes), max(latitudes)
    elif latitudes:
        high = latitudes[0]
        low = max(0.0, high - 12.0)
    else:
        low, high = 5.0, 20.0
    if high - low < 3:
        low = max(0.0, high - 10.0)
    return round(max(0.0, low), 1), round(min(31.0, high), 1)


def _wave_speed(text: str) -> tuple[float, float]:
    match = _WAVE_SPEED_RE.search(text or "") or _WAVE_SPEED_ANY_RE.search(text or "")
    if not match:
        return 10.0, 15.0
    try:
        low = float(match.group(1))
        high = float(match.group(2) or low)
    except (TypeError, ValueError):
        return 10.0, 15.0
    if low > high:
        low, high = high, low
    return round(low, 1), round(high, 1)


def _wave_area(lon: float) -> str:
    if lon >= -45:
        return "Atlántico oriental"
    if lon >= -60:
        return "Atlántico central"
    if lon >= -68:
        return "Caribe oriental"
    if lon >= -78:
        return "Caribe central"
    return "Caribe occidental"


def _wave_detail(text: str) -> str:
    detail = " ".join((text or "").split())
    if len(detail) > 260:
        detail = detail[:257].rstrip() + "…"
    return detail


def _parse_wave_observations(text: str, moment: datetime) -> list[dict[str, Any]]:
    section = _wave_section(text)
    if not section:
        return []
    observations: list[dict[str, Any]] = []
    for paragraph in _wave_paragraphs(section):
        lon = _wave_first_longitude(paragraph)
        if lon is None:
            continue
        lat_min, lat_max = _wave_lat_range(paragraph)
        speed_min, speed_max = _wave_speed(paragraph)
        observations.append(
            {
                "time": moment,
                "lon": lon,
                "latMin": lat_min,
                "latMax": lat_max,
                "speedKtMin": speed_min,
                "speedKtMax": speed_max,
                "area": _wave_area(lon),
                "detail": _wave_detail(paragraph),
            }
        )
    return observations


def _build_wave_tracks(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for observation in sorted(observations, key=lambda item: item["time"]):
        duplicate = any(
            abs((observation["time"] - track["observations"][-1]["time"]).total_seconds())
            < 3600
            and abs(observation["lon"] - track["observations"][-1]["lon"]) < 1.5
            for track in tracks
        )
        if duplicate:
            continue
        best: dict[str, Any] | None = None
        best_score: float | None = None
        for track in tracks:
            last = track["observations"][-1]
            hours = (observation["time"] - last["time"]).total_seconds() / 3600
            if hours <= 0.2 or hours > 30:
                continue
            delta_lon = last["lon"] - observation["lon"]
            if delta_lon < -2.5 or delta_lon > 14:
                continue
            latitude = (
                observation["latMin"]
                + observation["latMax"]
                + last["latMin"]
                + last["latMax"]
            ) / 4
            speed = max(5.0, (last["speedKtMax"] + observation["speedKtMax"]) / 2)
            expected = speed * hours / (60 * math.cos(math.radians(max(2.0, latitude))))
            score = abs(delta_lon - expected) + abs(hours) * 0.03
            if best_score is None or score < best_score:
                best = track
                best_score = score
        if best is None:
            tracks.append({"observations": [observation]})
            continue
        last = best["observations"][-1]
        if (
            abs((observation["time"] - last["time"]).total_seconds()) < 3600
            and abs(observation["lon"] - last["lon"]) < 1.0
        ):
            continue
        best["observations"].append(observation)
    return tracks


def _wave_point(observation: dict[str, Any], lon: float) -> list[float]:
    latitude = round((observation["latMin"] + observation["latMax"]) / 2, 1)
    return [latitude, round(lon, 1)]


def _summarize_wave_tracks(
    tracks: list[dict[str, Any]],
    now: datetime,
) -> list[dict[str, Any]]:
    waves: list[dict[str, Any]] = []
    for track in tracks:
        observations = track["observations"]
        if not observations:
            continue
        first, last = observations[0], observations[-1]
        first_east_of_entry = first["lon"] > TROPICAL_WAVE_ENTRY_LON
        first_east_of_exit = first["lon"] >= TROPICAL_WAVE_EXIT_LON
        entry = next(
            (item for item in observations if item["lon"] <= TROPICAL_WAVE_ENTRY_LON),
            None,
        )
        exit_ = next(
            (item for item in observations if item["lon"] <= TROPICAL_WAVE_EXIT_LON),
            None,
        )
        speed_min = last["speedKtMin"]
        speed_max = last["speedKtMax"]
        latitude = max(2.0, (last["latMin"] + last["latMax"]) / 2)
        degrees_per_day = max(
            0.5,
            ((speed_min + speed_max) / 2) * 24 / (60 * math.cos(math.radians(latitude))),
        )
        entry_at: datetime | None = None
        exit_at: datetime | None = None
        entry_estimated = False
        exit_estimated = False

        if first_east_of_entry:
            if entry is not None:
                entry_at = entry["time"]
            else:
                days = max(0.0, (last["lon"] - TROPICAL_WAVE_ENTRY_LON) / degrees_per_day)
                entry_at = last["time"] + timedelta(days=days)
                entry_estimated = True
        elif first_east_of_exit:
            days = max(0.0, (TROPICAL_WAVE_ENTRY_LON - first["lon"]) / degrees_per_day)
            entry_at = first["time"] - timedelta(days=days)
            entry_estimated = True

        if first_east_of_exit:
            if exit_ is not None:
                exit_at = exit_["time"]
            elif entry_at is not None:
                if last["lon"] <= TROPICAL_WAVE_ENTRY_LON:
                    base_time, base_lon = last["time"], last["lon"]
                else:
                    base_time, base_lon = entry_at, TROPICAL_WAVE_ENTRY_LON
                days = max(0.0, (base_lon - TROPICAL_WAVE_EXIT_LON) / degrees_per_day)
                exit_at = base_time + timedelta(days=days)
                exit_estimated = True
        elif entry_at is not None and last["lon"] <= TROPICAL_WAVE_ENTRY_LON:
            days = max(0.0, (last["lon"] - TROPICAL_WAVE_EXIT_LON) / degrees_per_day)
            exit_at = last["time"] + timedelta(days=days)
            exit_estimated = True

        entered = entry_at is not None
        exited = exit_at is not None
        crossed = entered and exited
        stale = (now - last["time"]).total_seconds() > 36 * 3600

        if stale:
            status = "Sin seguimiento reciente"
        elif last["lon"] <= TROPICAL_WAVE_EXIT_LON:
            status = "Salió de Venezuela"
        elif last["lon"] <= TROPICAL_WAVE_ENTRY_LON:
            status = "En Venezuela"
        elif last["lon"] <= -25:
            status = "Al este de Venezuela"
        else:
            status = "Lejos, al este"

        if not crossed or last["lon"] > -25:
            continue
        if stale and entry is None and exit_ is None:
            continue

        entry_point = _wave_point(entry or first, TROPICAL_WAVE_ENTRY_LON)
        exit_point = _wave_point(exit_ or last, TROPICAL_WAVE_EXIT_LON)

        waves.append(
            {
                "id": f"onda-{first['time'].strftime('%Y%m%d%H%M')}-{int(abs(first['lon']))}",
                "status": status,
                "area": last["area"],
                "crossedVenezuela": crossed,
                "entered": entered,
                "exited": exited,
                "firstSeen": _wave_iso(first["time"]),
                "lastSeen": _wave_iso(last["time"]),
                "entryAt": _wave_iso(entry_at),
                "exitAt": _wave_iso(exit_at),
                "entryEstimated": entry_estimated,
                "exitEstimated": exit_estimated,
                "entryPoint": entry_point,
                "exitPoint": exit_point,
                "current": {
                    "time": _wave_iso(last["time"]),
                    "lon": last["lon"],
                    "lonWest": round(abs(last["lon"]), 1),
                    "latMin": last["latMin"],
                    "latMax": last["latMax"],
                    "latMid": round((last["latMin"] + last["latMax"]) / 2, 1),
                    "speedKtMin": speed_min,
                    "speedKtMax": speed_max,
                    "speedKmhMin": round(speed_min * 1.852, 0),
                    "speedKmhMax": round(speed_max * 1.852, 0),
                    "area": last["area"],
                    "detail": last["detail"],
                },
                "observations": [
                    {
                        "time": _wave_iso(item["time"]),
                        "lon": item["lon"],
                        "lonWest": round(abs(item["lon"]), 1),
                        "latMin": item["latMin"],
                        "latMax": item["latMax"],
                        "latMid": round((item["latMin"] + item["latMax"]) / 2, 1),
                    }
                    for item in observations
                ],
            }
        )

    waves.sort(key=lambda item: item.get("entryAt") or item.get("firstSeen") or "")
    for index, wave in enumerate(waves, 1):
        wave["number"] = index
    return waves


async def get_tropical_waves(
    client: httpx.AsyncClient,
    days: int | None = None,
) -> dict[str, Any]:
    days = max(7, min(60, int(days or TROPICAL_WAVE_LOOKBACK_DAYS)))

    async def fetch() -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=days)
        years = sorted({now.year, start.year})
        stamps: list[tuple[datetime, int, str]] = []
        for year in years:
            url = f"{NHC_TWD_ARCHIVE_URL}/{year}/"
            try:
                response = await client.get(url)
            except httpx.HTTPError as exc:
                raise UpstreamError(
                    "No se pudo consultar el archivo de ondas tropicales."
                ) from exc
            if response.status_code >= 400:
                continue
            for name, stamp in re.findall(r"(TWDAT\.(\d{12})\.txt)", response.text):
                moment = _wave_moment(stamp)
                if moment and start <= moment <= now + timedelta(hours=3):
                    stamps.append((moment, year, name))

        stamps.sort()
        observations: list[dict[str, Any]] = []
        semaphore = asyncio.Semaphore(8)

        async def load(moment: datetime, year: int, name: str) -> None:
            async with semaphore:
                try:
                    response = await client.get(f"{NHC_TWD_ARCHIVE_URL}/{year}/{name}")
                except httpx.HTTPError:
                    return
                if response.status_code >= 400:
                    return
                observations.extend(_parse_wave_observations(response.text, moment))

        await asyncio.gather(*(load(*item) for item in stamps))
        waves = _summarize_wave_tracks(_build_wave_tracks(observations), now)
        last_wave_time = max((item["time"] for item in observations), default=None)
        if not waves:
            note = "No se encontraron ondas tropicales en el período analizado."
        elif last_wave_time and (now - last_wave_time).total_seconds() > 48 * 3600:
            note = (
                "El NHC no publica la sección de ondas desde "
                f"{_wave_iso(last_wave_time)}; se muestran las últimas ondas registradas."
            )
        else:
            note = (
                "Horarios de entrada y salida de Venezuela estimados a partir de la "
                "posición y velocidad reportadas por el NHC."
            )
        return {
            "waves": waves,
            "count": len(waves),
            "days": days,
            "entryLon": TROPICAL_WAVE_ENTRY_LON,
            "exitLon": TROPICAL_WAVE_EXIT_LON,
            "attribution": "Ondas tropicales: NHC Atlantic Tropical Weather Discussion",
            "source": f"{NHC_TWD_ARCHIVE_URL}/",
            "updatedAt": iso_now(),
            "lastObservation": _wave_iso(last_wave_time),
            "note": note,
        }

    return await cache.get_or_set(f"waves:tropical:{days}", 21_600, fetch)


# ---------------------------------------------------------------------------
# Viento sobre el mapa
# ---------------------------------------------------------------------------


async def get_wind_grid(
    client: httpx.AsyncClient,
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    rows: int = 4,
    cols: int = 5,
) -> dict[str, Any]:
    rows = max(2, min(6, rows))
    cols = max(2, min(6, cols))
    min_lat = max(-85.0, min(85.0, min_lat))
    max_lat = max(-85.0, min(85.0, max_lat))
    if min_lat > max_lat:
        min_lat, max_lat = max_lat, min_lat
    if min_lon > max_lon:
        min_lon, max_lon = max_lon, min_lon

    lats = [min_lat + (max_lat - min_lat) * row / (rows - 1) for row in range(rows)]
    lons = [min_lon + (max_lon - min_lon) * col / (cols - 1) for col in range(cols)]
    pairs = [(lat, lon) for lat in lats for lon in lons]

    key = (
        f"wind:{min_lat:.2f}:{max_lat:.2f}:{min_lon:.2f}:{max_lon:.2f}:"
        f"{rows}x{cols}"
    )

    async def fetch() -> dict[str, Any]:
        params = {
            "latitude": ",".join(f"{lat:.4f}" for lat, _ in pairs),
            "longitude": ",".join(f"{lon:.4f}" for _, lon in pairs),
            "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
            "timezone": "UTC",
            "wind_speed_unit": "kmh",
        }
        data = await _get_json(client, OPEN_METEO_URL, params)
        items = data if isinstance(data, list) else [data]
        points: list[dict[str, Any]] = []

        for i, item in enumerate(items):
            if i >= len(pairs):
                break
            current = item.get("current") or {}
            direction = current.get("wind_direction_10m")
            points.append(
                {
                    "lat": _round(item.get("latitude", pairs[i][0]), 4),
                    "lon": _round(item.get("longitude", pairs[i][1]), 4),
                    "speed": _round(current.get("wind_speed_10m"), 1),
                    "gust": _round(current.get("wind_gusts_10m"), 1),
                    "direction": _round(direction, 0),
                    "directionText": compass_short(direction),
                }
            )

        return {
            "points": points,
            "count": len(points),
            "grid": {"rows": rows, "cols": cols},
            "attribution": "Viento: Open-Meteo",
            "updatedAt": iso_now(),
        }

    return await cache.get_or_set(key, 300, fetch)


# ---------------------------------------------------------------------------
# Punto completo (panel + voz)
# ---------------------------------------------------------------------------


async def get_point(
    client: httpx.AsyncClient,
    lat: float,
    lon: float,
    radius_km: float = 500,
    days: int = 7,
    min_magnitude: float = 1.0,
) -> dict[str, Any]:
    key = f"point:{lat:.3f}:{lon:.3f}:{radius_km}:{days}:{min_magnitude}"

    async def fetch() -> dict[str, Any]:
        location_task = reverse_geocode(client, lat, lon)
        weather_task = get_weather(client, lat, lon)
        quakes_task = get_near_quakes(
            client, lat, lon, radius_km=radius_km, days=days, min_magnitude=min_magnitude
        )
        storms_task = get_near_storms(client, lat, lon, limit=4)

        location_result, weather_result, quakes_result, storms_result = await asyncio.gather(
            location_task,
            weather_task,
            quakes_task,
            storms_task,
            return_exceptions=True,
        )

        if isinstance(location_result, Exception):
            location = {
                "display": f"{lat:.4f}, {lon:.4f}",
                "city": None,
                "state": None,
                "country": None,
                "countryCode": None,
                "type": None,
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "source": "coordenadas",
            }
        else:
            location = location_result

        if isinstance(weather_result, Exception):
            raise UpstreamError("No se pudo obtener el pronóstico para este punto.")

        weather = weather_result
        quakes = [] if isinstance(quakes_result, Exception) else quakes_result
        storms = [] if isinstance(storms_result, Exception) else storms_result

        satellite = get_satellite(lat, lon)
        narration = build_narration(location, weather, quakes, storms)

        return {
            "coordinates": {"lat": round(lat, 6), "lon": round(lon, 6)},
            "location": location,
            "weather": weather,
            "quakes": quakes,
            "storms": storms,
            "quakeQuery": {
                "radiusKm": radius_km,
                "days": days,
                "minMagnitude": min_magnitude,
            },
            "satellite": satellite["selected"],
            "narration": narration,
            "updatedAt": iso_now(),
        }

    return await cache.get_or_set(key, 45, fetch)

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx

from .cache import cache
from .config import (
    NOMINATIM_REVERSE_URL,
    NOMINATIM_SEARCH_URL,
    OPEN_METEO_URL,
    PHOTON_REVERSE_URL,
    PHOTON_SEARCH_URL,
    RAINVIEWER_MAPS_URL,
    SATELLITE_LAYERS,
    USGS_QUERY_URL,
)
from .geo import (
    bbox_from_radius,
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
            "GOES y Himawari se actualizan cada 10 minutos. MODIS es una imagen diaria "
            "y sirve como respaldo global."
        ),
        "updatedAt": iso_now(),
    }


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
    min_magnitude: float = 2.5,
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
    min_magnitude: float = 2.5,
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
    min_magnitude: float = 2.5,
) -> dict[str, Any]:
    key = f"point:{lat:.3f}:{lon:.3f}:{radius_km}:{days}:{min_magnitude}"

    async def fetch() -> dict[str, Any]:
        location_task = reverse_geocode(client, lat, lon)
        weather_task = get_weather(client, lat, lon)
        quakes_task = get_near_quakes(
            client, lat, lon, radius_km=radius_km, days=days, min_magnitude=min_magnitude
        )

        location_result, weather_result, quakes_result = await asyncio.gather(
            location_task,
            weather_task,
            quakes_task,
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

        satellite = get_satellite(lat, lon)
        narration = build_narration(location, weather, quakes)

        return {
            "coordinates": {"lat": round(lat, 6), "lon": round(lon, 6)},
            "location": location,
            "weather": weather,
            "quakes": quakes,
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

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

COMPASS_ES = [
    "norte",
    "nornoreste",
    "noreste",
    "estenoreste",
    "este",
    "estesureste",
    "sureste",
    "sursureste",
    "sur",
    "sursuroeste",
    "suroeste",
    "oestesuroeste",
    "oeste",
    "oestenoroeste",
    "noroeste",
    "nornoroeste",
]

COMPASS_SHORT = [
    "N",
    "NNE",
    "NE",
    "ENE",
    "E",
    "ESE",
    "SE",
    "SSE",
    "S",
    "SSO",
    "SO",
    "OSO",
    "O",
    "ONO",
    "NO",
    "NNO",
]

WMO_CODES: dict[int, tuple[str, str]] = {
    0: ("Despejado", "sun"),
    1: ("Mayormente despejado", "partly"),
    2: ("Parcialmente nublado", "partly"),
    3: ("Nublado", "cloud"),
    45: ("Niebla", "fog"),
    48: ("Niebla con escarcha", "fog"),
    51: ("Llovizna ligera", "drizzle"),
    53: ("Llovizna moderada", "drizzle"),
    55: ("Llovizna intensa", "drizzle"),
    56: ("Llovizna helada ligera", "freeze"),
    57: ("Llovizna helada intensa", "freeze"),
    61: ("Lluvia ligera", "rain"),
    63: ("Lluvia moderada", "rain"),
    65: ("Lluvia fuerte", "rain"),
    66: ("Lluvia helada ligera", "freeze"),
    67: ("Lluvia helada fuerte", "freeze"),
    71: ("Nieve ligera", "snow"),
    73: ("Nieve moderada", "snow"),
    75: ("Nieve fuerte", "snow"),
    77: ("Granos de nieve", "snow"),
    80: ("Chubascos ligeros", "showers"),
    81: ("Chubascos moderados", "showers"),
    82: ("Chubascos fuertes", "showers"),
    85: ("Chubascos de nieve ligeros", "snow"),
    86: ("Chubascos de nieve fuertes", "snow"),
    95: ("Tormenta eléctrica", "storm"),
    96: ("Tormenta con granizo ligero", "storm"),
    99: ("Tormenta con granizo fuerte", "storm"),
}


def describe_weather(code: int | str | None) -> tuple[str, str]:
    try:
        value = int(code)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return ("Condición desconocida", "cloud")
    return WMO_CODES.get(value, (f"Condición {value}", "cloud"))


def compass_es(degrees: float | int | None) -> str:
    if degrees is None:
        return "sin dirección"
    try:
        value = float(degrees) % 360
    except (TypeError, ValueError):
        return "sin dirección"
    index = int(value / 22.5 + 0.5) % 16
    return COMPASS_ES[index]


def compass_short(degrees: float | int | None) -> str:
    if degrees is None:
        return "--"
    try:
        value = float(degrees) % 360
    except (TypeError, ValueError):
        return "--"
    index = int(value / 22.5 + 0.5) % 16
    return COMPASS_SHORT[index]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0088
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * radius * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    y = math.sin(dlambda) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def bearing_es(lat1: float, lon1: float, lat2: float, lon2: float) -> str:
    return compass_es(bearing_deg(lat1, lon1, lat2, lon2))


def bbox_from_radius(lat: float, lon: float, radius_km: float) -> tuple[float, float, float, float]:
    lat_delta = radius_km / 111.32
    cos_lat = max(0.15, math.cos(math.radians(lat)))
    lon_delta = radius_km / (111.32 * cos_lat)
    min_lat = max(-85.0, lat - lat_delta)
    max_lat = min(85.0, lat + lat_delta)
    min_lon = max(-180.0, lon - lon_delta)
    max_lon = min(180.0, lon + lon_delta)
    return min_lat, min_lon, max_lat, max_lon


def storm_risk(weather_code: int | str | None, cape: float | int | None) -> dict[str, str]:
    try:
        code = int(weather_code or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        code = 0
    try:
        cape_value = float(cape or 0)
    except (TypeError, ValueError):
        cape_value = 0.0

    if code >= 95:
        return {"level": "alta", "label": "Alta", "detail": "Tormenta eléctrica en curso"}
    if cape_value >= 2500:
        return {"level": "alta", "label": "Alta", "detail": "Energía atmosférica muy alta (CAPE)"}
    if cape_value >= 1000 or code in (81, 82):
        return {"level": "moderada", "label": "Moderada", "detail": "Posibles tormentas dispersas"}
    if cape_value >= 300 or code in (80,):
        return {"level": "baja", "label": "Baja", "detail": "Ambiente algo inestable"}
    return {"level": "nula", "label": "Nula", "detail": "Atmósfera estable"}


def relative_time_es(iso_time: str | None, now: datetime | None = None) -> str:
    if not iso_time:
        return ""
    try:
        parsed = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    seconds = max(0.0, (current - parsed).total_seconds())
    if seconds < 90:
        return "hace un momento"
    minutes = seconds / 60
    if minutes < 60:
        value = int(minutes)
        return "hace 1 minuto" if value == 1 else f"hace {value} minutos"
    hours = minutes / 60
    if hours < 24:
        value = int(hours)
        return "hace 1 hora" if value == 1 else f"hace {value} horas"
    days = hours / 24
    value = int(days)
    return "hace 1 día" if value == 1 else f"hace {value} días"


def _number(value: Any, digits: int = 0) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "sin dato"
    if digits == 0:
        return f"{number:.0f}"
    return f"{number:.{digits}f}"


def build_narration(
    location: dict[str, Any] | None,
    weather: dict[str, Any],
    quakes: list[dict[str, Any]] | None = None,
    storms: list[dict[str, Any]] | None = None,
) -> str:
    """Construye una narración breve en español para síntesis de voz."""
    location = location or {}
    place = location.get("display") or "la ubicación seleccionada"
    current = weather.get("current") or {}

    parts: list[str] = [f"Ubicación: {place}."]

    local_time = current.get("time") or ""
    if len(local_time) >= 16:
        parts.append(f"Hora local {local_time[11:16]}.")

    temperature = current.get("temperature")
    apparent = current.get("apparentTemperature")
    if temperature is not None:
        text = f"Temperatura {_number(temperature)} grados Celsius"
        if apparent is not None:
            text += f", sensación de {_number(apparent)} grados"
        parts.append(text + ".")

    description = current.get("description")
    if description:
        parts.append(f"{description}.")

    wind_speed = current.get("windSpeed")
    wind_direction = current.get("windDirectionText")
    if wind_speed is not None:
        text = f"Viento del {wind_direction} a {_number(wind_speed)} kilómetros por hora"
        if current.get("windGust") is not None:
            text += f", con rachas de {_number(current.get('windGust'))}"
        parts.append(text + ".")

    if current.get("cloudCover") is not None:
        parts.append(f"Nubosidad del {_number(current.get('cloudCover'))} por ciento.")

    if current.get("humidity") is not None:
        parts.append(f"Humedad relativa del {_number(current.get('humidity'))} por ciento.")

    probability = weather.get("nextPrecipitationProbability")
    if probability is not None:
        parts.append(f"Probabilidad de lluvia en la próxima hora: {_number(probability)} por ciento.")

    precipitation = current.get("precipitation")
    if precipitation is not None and float(precipitation or 0) > 0:
        parts.append(f"Lluvia acumulada reciente: {_number(precipitation, 1)} milímetros.")

    risk = (current.get("stormRisk") or {}).get("level", "nula")
    parts.append(f"Riesgo de tormenta: {risk}.")

    events = quakes or []
    if events:
        nearest = events[0]
        distance = _number(nearest.get("distanceKm"))
        direction = nearest.get("direction") or "cerca"
        magnitude = _number(nearest.get("mag"), 1)
        when = relative_time_es(nearest.get("time"))
        parts.append(
            f"Sismo más cercano: magnitud {magnitude} a {distance} kilómetros al {direction}, {when}."
        )
    else:
        parts.append(
            "Sin sismos de magnitud 1.0 o mayor en 500 kilómetros durante los últimos 7 días."
        )

    active_storms = storms or []
    if active_storms:
        nearest = active_storms[0]
        name = nearest.get("name") or "una tormenta sin nombre"
        classification = nearest.get("category") or nearest.get("classification") or "tormenta"
        try:
            distance = float(nearest.get("distanceKm"))
        except (TypeError, ValueError):
            distance = None

        if distance is not None and distance > 3000:
            parts.append(
                f"No hay huracanes ni tormentas tropicales en 3,000 kilómetros. "
                f"La más cercana es {name}, {classification}, a {_number(distance)} kilómetros."
            )
        else:
            wind = nearest.get("windKmh")
            text = f"Huracán o tormenta más cercana: {name}, {classification}"
            if wind is not None:
                text += f", vientos de {_number(wind)} kilómetros por hora"
            if distance is not None:
                direction = nearest.get("direction") or "cerca"
                text += f", a {_number(distance)} kilómetros al {direction}"
            movement = nearest.get("movement") or {}
            movement_direction = movement.get("direction") or "desconocida"
            movement_speed = movement.get("speedKmh")
            if movement_speed is not None:
                text += (
                    f". Se mueve hacia el {movement_direction} a "
                    f"{_number(movement_speed)} kilómetros por hora"
                )
            else:
                text += f". Se mueve hacia el {movement_direction}"
            parts.append(text + ".")
    else:
        parts.append("No hay huracanes ni tormentas tropicales activas en este momento.")

    return " ".join(parts)

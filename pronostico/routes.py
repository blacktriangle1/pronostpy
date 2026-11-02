from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from .config import (
    DEFAULT_LAT,
    DEFAULT_LON,
    SOURCES,
    VERSION,
)
from .services import (
    get_near_quakes,
    get_point,
    get_quakes,
    get_radar,
    get_satellite,
    get_weather,
    get_wind_grid,
    iso_now,
    reverse_geocode,
    search_places,
)

router = APIRouter(prefix="/api", tags=["radar-vivo"])


def _client(request: Request):
    return request.app.state.client


@router.get("/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "app": "Radar Vivo", "version": VERSION, "time": iso_now()}


@router.get("/config")
async def config() -> dict[str, Any]:
    return {
        "version": VERSION,
        "defaultCenter": {"lat": DEFAULT_LAT, "lon": DEFAULT_LON},
        "refreshMs": {
            "radar": 120_000,
            "point": 60_000,
            "quakes": 60_000,
            "wind": 300_000,
            "satellite": 600_000,
        },
        "units": {
            "temperature": "°C",
            "wind": "km/h",
            "precipitation": "mm",
            "distance": "km",
            "depth": "km",
        },
        "sources": SOURCES,
        "limits": {
            "pointRadiusKm": 500,
            "pointDays": 7,
            "pointMinMagnitude": 2.5,
            "quakeMinMagnitude": 2.5,
            "quakeDays": 1,
        },
    }


@router.get("/radar")
async def radar(request: Request) -> dict[str, Any]:
    return await get_radar(_client(request))


@router.get("/satellite")
async def satellite(
    lat: float = Query(DEFAULT_LAT, ge=-90, le=90),
    lon: float = Query(DEFAULT_LON, ge=-180, le=180),
) -> dict[str, Any]:
    return get_satellite(lat, lon)


@router.get("/weather")
async def weather(
    request: Request,
    lat: float = Query(..., ge=-90, le=90, description="Latitud"),
    lon: float = Query(..., ge=-180, le=180, description="Longitud"),
) -> dict[str, Any]:
    return await get_weather(_client(request), lat, lon)


@router.get("/location")
async def location(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
) -> dict[str, Any]:
    return await reverse_geocode(_client(request), lat, lon)


@router.get("/search")
async def search(
    request: Request,
    q: str = Query(..., min_length=2, max_length=120),
    limit: int = Query(6, ge=1, le=10),
) -> dict[str, Any]:
    results = await search_places(_client(request), q, limit)
    return {"query": q, "count": len(results), "results": results}


@router.get("/quakes")
async def quakes(
    request: Request,
    minlat: float = Query(-60, ge=-90, le=90),
    maxlat: float = Query(70, ge=-90, le=90),
    minlon: float = Query(-180, ge=-180, le=180),
    maxlon: float = Query(180, ge=-180, le=180),
    minmag: float = Query(2.5, ge=0, le=9.5),
    days: int = Query(1, ge=1, le=30),
    limit: int = Query(300, ge=1, le=500),
) -> dict[str, Any]:
    if minlat > maxlat:
        minlat, maxlat = maxlat, minlat
    if minlon > maxlon:
        minlon, maxlon = maxlon, minlon
    minlat = max(-85.0, minlat)
    maxlat = min(85.0, maxlat)

    data = await get_quakes(
        _client(request),
        min_lat=minlat,
        max_lat=maxlat,
        min_lon=minlon,
        max_lon=maxlon,
        min_magnitude=minmag,
        days=days,
        limit=limit,
    )
    data["bbox"] = {
        "minlat": minlat,
        "maxlat": maxlat,
        "minlon": minlon,
        "maxlon": maxlon,
    }
    return data


@router.get("/quakes/near")
async def quakes_near(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius: float = Query(500, ge=25, le=2000),
    days: int = Query(7, ge=1, le=30),
    minmag: float = Query(2.5, ge=0, le=9.5),
) -> dict[str, Any]:
    events = await get_near_quakes(
        _client(request),
        lat,
        lon,
        radius_km=radius,
        days=days,
        min_magnitude=minmag,
    )
    return {
        "events": events,
        "count": len(events),
        "center": {"lat": lat, "lon": lon},
        "radiusKm": radius,
        "days": days,
        "minMagnitude": minmag,
        "updatedAt": iso_now(),
    }


@router.get("/wind")
async def wind(
    request: Request,
    minlat: float = Query(..., ge=-85, le=85),
    maxlat: float = Query(..., ge=-85, le=85),
    minlon: float = Query(..., ge=-180, le=180),
    maxlon: float = Query(..., ge=-180, le=180),
    rows: int = Query(4, ge=2, le=6),
    cols: int = Query(5, ge=2, le=6),
) -> dict[str, Any]:
    if minlat > maxlat:
        minlat, maxlat = maxlat, minlat
    if minlon > maxlon:
        minlon, maxlon = maxlon, minlon
    return await get_wind_grid(
        _client(request),
        min_lat=minlat,
        max_lat=maxlat,
        min_lon=minlon,
        max_lon=maxlon,
        rows=rows,
        cols=cols,
    )


@router.get("/point")
async def point(
    request: Request,
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    radius: float = Query(500, ge=25, le=2000),
    days: int = Query(7, ge=1, le=30),
    minmag: float = Query(2.5, ge=0, le=9.5),
) -> dict[str, Any]:
    return await get_point(
        _client(request),
        lat,
        lon,
        radius_km=radius,
        days=days,
        min_magnitude=minmag,
    )

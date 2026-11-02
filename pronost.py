"""Radar Vivo: servidor Python y API para la aplicacion meteorologica.

Ejecutar:
    python pronost.py

Luego abrir http://localhost:8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from pronostico.config import (
    APP_NAME,
    HOST,
    HTTP_TIMEOUT,
    PORT,
    STATIC_DIR,
    USER_AGENT,
    VERSION,
)
from pronostico.routes import router
from pronostico.services import UpstreamError


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(
        timeout=httpx.Timeout(HTTP_TIMEOUT),
        follow_redirects=True,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(
    title=APP_NAME,
    version=VERSION,
    description=(
        "API de radar de lluvia, satelite, viento, tormentas y sismos. "
        "Los datos se obtienen de RainViewer, Open-Meteo, NASA GIBS y USGS."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_headers(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.exception_handler(UpstreamError)
async def upstream_error_handler(request: Request, exc: UpstreamError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "detail": exc.message,
            "type": "upstream_error",
            "time": None,
        },
    )


app.include_router(router)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def _lan_ips() -> list[str]:
    """Devuelve las direcciones IPv4 privadas utiles para abrir la app desde el telefono."""
    import socket

    found: set[str] = set()
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 80))
            found.add(probe.getsockname()[0])
        finally:
            probe.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = str(info[4][0])
            if not address.startswith("127."):
                found.add(address)
    except OSError:
        pass
    return sorted(found)


if __name__ == "__main__":
    import uvicorn

    print("")
    print(f"  {APP_NAME} {VERSION}")
    print(f"  PC:       http://localhost:{PORT}")
    lan_ips = _lan_ips()
    if lan_ips:
        for lan_ip in lan_ips:
            print(f"  Telefono: http://{lan_ip}:{PORT}  (misma red Wi-Fi)")
    else:
        print("  Telefono: usa la IP de tu equipo en la red Wi-Fi")
    print("  Detener:  Ctrl+C")
    print("")

    uvicorn.run("pronost:app", host=HOST, port=PORT, reload=False)

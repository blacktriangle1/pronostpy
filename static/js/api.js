const DEFAULT_TIMEOUT = 22000;

function boundsParams(bounds) {
  let minlat = Math.max(-85, Math.min(85, bounds.getSouth()));
  let maxlat = Math.max(-85, Math.min(85, bounds.getNorth()));
  let minlon = Math.max(-180, Math.min(180, bounds.getWest()));
  let maxlon = Math.max(-180, Math.min(180, bounds.getEast()));
  if (minlat > maxlat) [minlat, maxlat] = [maxlat, minlat];
  if (minlon > maxlon) [minlon, maxlon] = [maxlon, minlon];
  return { minlat, maxlat, minlon, maxlon };
}

function buildUrl(path, params) {
  const url = new URL(path, window.location.origin);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value === undefined || value === null || value === "") continue;
      url.searchParams.set(key, String(value));
    }
  }
  return url;
}

async function getJSON(path, params, options = {}) {
  const { timeout = DEFAULT_TIMEOUT, signal } = options;
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeout);
  const combinedSignal = signal
    ? AbortSignal.any
      ? AbortSignal.any([signal, controller.signal])
      : controller.signal
    : controller.signal;

  try {
    const response = await fetch(buildUrl(path, params), {
      signal: combinedSignal,
      headers: {
        Accept: "application/json",
        "ngrok-skip-browser-warning": "true",
      },
      cache: "no-store",
    });

    if (!response.ok) {
      let message = `Error ${response.status}`;
      try {
        const body = await response.json();
        if (body && body.detail) message = body.detail;
      } catch {
        // La respuesta no era JSON.
      }
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }

    return await response.json();
  } catch (error) {
    if (error.name === "AbortError") {
      throw new Error("La consulta tardó demasiado. Verifica tu conexión.");
    }
    throw error;
  } finally {
    window.clearTimeout(timer);
  }
}

export const api = {
  config: () => getJSON("/api/config"),
  health: () => getJSON("/api/health"),
  radar: () => getJSON("/api/radar"),
  satellite: (lat, lon) => getJSON("/api/satellite", { lat, lon }),
  satelliteFrames: (layer) =>
    getJSON("/api/satellite/frames", { layer }, { timeout: 45000 }),
  weather: (lat, lon) => getJSON("/api/weather", { lat, lon }),
  location: (lat, lon) => getJSON("/api/location", { lat, lon }),
  quakes: (bounds, minmag = 1, days = 1) =>
    getJSON("/api/quakes", {
      ...boundsParams(bounds),
      minmag,
      days,
      limit: 300,
    }),
  nearQuakes: (lat, lon, radius = 500, days = 7, minmag = 1) =>
    getJSON("/api/quakes/near", { lat, lon, radius, days, minmag }),
  storms: (lat, lon) => getJSON("/api/storms", { lat, lon }),
  tropicalWaves: (days) =>
    getJSON("/api/tropical-waves", { days }, { timeout: 120000 }),
  wind: (bounds, rows = 4, cols = 5) =>
    getJSON("/api/wind", {
      ...boundsParams(bounds),
      rows,
      cols,
    }),
  point: (lat, lon, minmag = 1) =>
    getJSON("/api/point", {
      lat,
      lon,
      radius: 500,
      days: 7,
      minmag,
    }),
  search: (query) => getJSON("/api/search", { q: query, limit: 6 }),
};

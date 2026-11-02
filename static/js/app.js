import { api } from "./api.js";
import { Voice } from "./audio.js";
import { weatherIcon, windArrow } from "./icons.js";
import { MapView } from "./mapview.js";

const $ = (id) => document.getElementById(id);
const SETTINGS_KEY = "radarVivo.ui";
const FRAME_MS = 680;
const MOBILE_QUERY = "(max-width: 760px)";

const FALLBACK_CONFIG = {
  defaultCenter: { lat: 19.4326, lon: -99.1332 },
  refreshMs: { radar: 120000, point: 60000, quakes: 60000, wind: 300000 },
};

const state = {
  config: FALLBACK_CONFIG,
  map: null,
  voice: null,
  settings: {
    contrast: false,
    large: false,
    reduceMotion: false,
    panelCollapsed: null,
  },
  radar: null,
  frames: [],
  frameIndex: 0,
  playing: true,
  radarEnabled: true,
  satelliteEnabled: false,
  satLayers: [],
  satLayerId: "auto",
  quakesEnabled: true,
  quakeMinMag: 2.5,
  quakes: [],
  windEnabled: false,
  windPoints: [],
  selected: null,
  lastPoint: null,
  lastSpeech: "",
  pointToken: 0,
  flags: { radar: false, quakes: false, wind: false },
  timers: {},
  viewTimer: null,
  toastTimer: null,
  searchTimer: null,
  searchToken: 0,
  loadingCount: 0,
};

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function isMobile() {
  return window.matchMedia(MOBILE_QUERY).matches;
}

function fmt(value, digits = 0) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "--";
  return Number(value).toFixed(digits);
}

function normalizeLon(value) {
  const lon = Number(value);
  if (!Number.isFinite(lon)) return 0;
  return ((((lon + 180) % 360) + 360) % 360) - 180;
}

function clampLat(value) {
  const lat = Number(value);
  if (!Number.isFinite(lat)) return 0;
  return Math.max(-85, Math.min(85, lat));
}

function formatTime(epochSeconds) {
  if (!epochSeconds) return "--:--";
  try {
    return new Date(Number(epochSeconds) * 1000).toLocaleTimeString("es-MX", {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "--:--";
  }
}

function formatDateTime(epochSeconds) {
  try {
    return new Date(Number(epochSeconds) * 1000).toLocaleString("es-MX", {
      dateStyle: "medium",
      timeStyle: "short",
    });
  } catch {
    return "";
  }
}

function relativeTime(value) {
  if (!value) return "sin dato";
  const date =
    typeof value === "number" ? new Date(value < 1e12 ? value * 1000 : value) : new Date(value);
  if (Number.isNaN(date.getTime())) return "sin dato";
  const seconds = Math.max(0, (Date.now() - date.getTime()) / 1000);
  if (seconds < 90) return "hace un momento";
  const minutes = seconds / 60;
  if (minutes < 60) {
    const value = Math.floor(minutes);
    return value === 1 ? "hace 1 minuto" : `hace ${value} minutos`;
  }
  const hours = minutes / 60;
  if (hours < 24) {
    const value = Math.floor(hours);
    return value === 1 ? "hace 1 hora" : `hace ${value} horas`;
  }
  const days = Math.floor(hours / 24);
  return days === 1 ? "hace 1 día" : `hace ${days} días`;
}

function magClass(mag) {
  const value = Number(mag);
  if (value >= 6) return "mag-6";
  if (value >= 5) return "mag-5";
  if (value >= 4) return "mag-4";
  return "";
}

function recommendSatellite(lon) {
  if (lon >= 100 || lon <= -160) return "himawari";
  if (lon >= -105 && lon <= -10) return "goes-east";
  if (lon > -160 && lon < -105) return "goes-west";
  return "modis";
}

// ---------------------------------------------------------------------------
// Preferencias
// ---------------------------------------------------------------------------

function loadUiSettings() {
  try {
    const saved = JSON.parse(localStorage.getItem(SETTINGS_KEY) || "{}");
    state.settings.contrast = Boolean(saved.contrast);
    state.settings.large = Boolean(saved.large);
    state.settings.reduceMotion =
      typeof saved.reduceMotion === "boolean"
        ? saved.reduceMotion
        : window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    state.settings.panelCollapsed =
      typeof saved.panelCollapsed === "boolean" ? saved.panelCollapsed : null;
  } catch {
    state.settings.reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
}

function saveUiSettings() {
  try {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(state.settings));
  } catch {
    // Sin almacenamiento.
  }
}

function applyUiSettings() {
  const root = document.documentElement;
  root.classList.toggle("hc", state.settings.contrast);
  root.classList.toggle("large-text", state.settings.large);
  root.classList.toggle("reduce-motion", state.settings.reduceMotion);

  $("optContrast").checked = state.settings.contrast;
  $("optLarge").checked = state.settings.large;
  $("optMotion").checked = state.settings.reduceMotion;

  if (state.settings.reduceMotion) {
    setPlaying(false);
  }
  if (state.map) state.map.setReducedMotion(state.settings.reduceMotion);
}

// ---------------------------------------------------------------------------
// Avisos, carga y voz
// ---------------------------------------------------------------------------

function showToast(message, type = "info", duration = 4800) {
  const toast = $("toast");
  toast.textContent = message;
  toast.classList.toggle("error", type === "error");
  toast.classList.add("show");
  window.clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => toast.classList.remove("show"), duration);
}

function showLoading(label = "Cargando…") {
  state.loadingCount += 1;
  const loading = $("loading");
  const text = loading.querySelector("span:last-child");
  if (text) text.textContent = label;
  loading.hidden = false;
}

function hideLoading() {
  state.loadingCount = Math.max(0, state.loadingCount - 1);
  if (state.loadingCount === 0) $("loading").hidden = true;
}

function setLive(text) {
  $("liveRegion").textContent = text || "";
}

function speakStatus(text) {
  setLive(text);
  if (state.voice.enabled) state.voice.speak(text);
}

function speakText(text, shouldSpeak) {
  if (!text) return;
  setLive(text);
  if (shouldSpeak) state.voice.speak(text);
}

function updateAudioUi() {
  const voice = state.voice;
  const button = $("audioToggle");
  button.setAttribute("aria-pressed", String(voice.enabled));
  button.setAttribute("aria-label", voice.enabled ? "Narración por voz activada" : "Narración por voz desactivada");
  $("optAudio").checked = voice.enabled;
}

// ---------------------------------------------------------------------------
// Radar
// ---------------------------------------------------------------------------

async function loadRadar({ silent = false } = {}) {
  if (state.flags.radar) return;
  state.flags.radar = true;
  try {
    const data = await api.radar();
    const previous = state.frames[state.frameIndex];
    state.radar = data;
    state.frames = data.frames || [];
    state.map.setRadarFrames(state.frames);

    let nextIndex;
    if (state.playing || !previous) {
      nextIndex = state.frames.length - 1;
    } else {
      const found = state.frames.findIndex((frame) => frame.time === previous.time);
      nextIndex = found >= 0 ? found : state.frames.length - 1;
    }
    state.frameIndex = Math.max(0, nextIndex);
    state.map.setRadarFrame(state.frameIndex);
    updateTimeline();
    updateRadarStatus();
  } catch (error) {
    updateRadarStatus(true);
    if (!silent) showToast(`Radar: ${error.message}`, "error");
  } finally {
    state.flags.radar = false;
  }
}

function updateRadarStatus(error = false) {
  const status = $("radarStatus");
  if (!state.radarEnabled) {
    status.textContent = "apagado";
    return;
  }
  if (error) {
    status.textContent = "sin conexión";
    return;
  }
  const latest = state.frames[state.frames.length - 1];
  if (!latest) {
    status.textContent = "sin datos";
    return;
  }
  status.textContent = latest.kind === "nowcast" ? "pronóstico" : relativeTime(latest.time);
}

function updateTimeline() {
  const frame = state.frames[state.frameIndex];
  const slider = $("frameSlider");
  const timeline = $("timeline");

  timeline.hidden = !state.radarEnabled;
  slider.max = String(Math.max(0, state.frames.length - 1));
  slider.value = String(state.frameIndex);
  slider.disabled = state.frames.length < 2;
  $("frameCounter").textContent = state.frames.length
    ? `${state.frameIndex + 1}/${state.frames.length}`
    : "0/0";

  if (!frame) {
    $("frameTime").textContent = "--:--";
    $("liveBadge").textContent = "RADAR";
    slider.removeAttribute("aria-valuetext");
    return;
  }

  $("frameTime").textContent = formatTime(frame.time);
  $("frameTime").title = formatDateTime(frame.time);
  $("liveBadge").textContent = frame.kind === "nowcast" ? "PRONÓST." : "RADAR";
  slider.setAttribute(
    "aria-valuetext",
    `${formatTime(frame.time)}, fotograma ${state.frameIndex + 1} de ${state.frames.length}`
  );
}

function tickFrames() {
  if (!state.playing || !state.radarEnabled || state.frames.length < 2) return;
  state.frameIndex = (state.frameIndex + 1) % state.frames.length;
  state.map.setRadarFrame(state.frameIndex);
  updateTimeline();
}

function setPlaying(playing) {
  state.playing = Boolean(playing);
  const button = $("playPause");
  button.classList.toggle("is-playing", state.playing);
  button.setAttribute("aria-pressed", String(state.playing));
  button.setAttribute(
    "aria-label",
    state.playing ? "Pausar animación del radar" : "Reproducir animación del radar"
  );
}

// ---------------------------------------------------------------------------
// Satélite, sismos y viento
// ---------------------------------------------------------------------------

async function loadSatellite() {
  try {
    const center = state.map.getCenter();
    const data = await api.satellite(clampLat(center.lat), normalizeLon(center.lng));
    state.satLayers = data.layers || [];
    const select = $("satelliteSelect");
    select.replaceChildren();
    const auto = el("option", null, "Automática según la zona");
    auto.value = "auto";
    select.append(auto);
    for (const layer of state.satLayers) {
      const option = el("option", null, `${layer.title} · ${layer.coverage}`);
      option.value = layer.id;
      option.title = layer.description;
      select.append(option);
    }
    select.value = state.satLayerId;
    if (state.satelliteEnabled) applySatellite();
  } catch (error) {
    showToast(`Satélite: ${error.message}`, "error");
  }
}

function applySatellite() {
  if (!state.satelliteEnabled) {
    state.map.clearSatellite();
    return;
  }
  if (!state.satLayers.length) return;
  const center = state.map.getCenter();
  const id =
    state.satLayerId === "auto"
      ? recommendSatellite(normalizeLon(center.lng))
      : state.satLayerId;
  const layer = state.satLayers.find((item) => item.id === id) || null;
  state.map.setSatellite(layer);
}

async function loadQuakes({ silent = false } = {}) {
  if (state.flags.quakes) return;
  state.flags.quakes = true;
  try {
    const data = await api.quakes(state.map.getBounds(), state.quakeMinMag, 1);
    state.quakes = data.events || [];
    if (state.quakesEnabled) state.map.setQuakes(state.quakes);
    $("quakesToggle").title = `${data.count} sismos en la vista · ${relativeTime(data.updatedAt)}`;
  } catch (error) {
    if (!silent) showToast(`Sismos: ${error.message}`, "error");
  } finally {
    state.flags.quakes = false;
  }
}

async function loadWind({ silent = false } = {}) {
  if (!state.windEnabled || state.flags.wind) return;
  state.flags.wind = true;
  try {
    const data = await api.wind(state.map.getBounds(), 4, 5);
    state.windPoints = data.points || [];
    state.map.setWind(state.windPoints);
    $("windStatus").textContent = relativeTime(data.updatedAt);
  } catch (error) {
    $("windStatus").textContent = "sin datos";
    if (!silent) showToast(`Viento: ${error.message}`, "error");
  } finally {
    state.flags.wind = false;
  }
}

function handleViewChange() {
  window.clearTimeout(state.viewTimer);
  state.viewTimer = window.setTimeout(() => {
    if (state.quakesEnabled) loadQuakes({ silent: true });
    if (state.windEnabled) loadWind({ silent: true });
    if (state.satelliteEnabled && state.satLayerId === "auto") applySatellite();
  }, 700);
}

// ---------------------------------------------------------------------------
// Punto seleccionado
// ---------------------------------------------------------------------------

async function selectPoint(lat, lon, options = {}) {
  const {
    speak = false,
    fly = false,
    zoom = 10,
    silent = false,
    announce = true,
    directSpeech = "",
    expand = false,
  } = options;

  lat = clampLat(lat);
  lon = normalizeLon(lon);
  state.selected = { lat, lon };
  state.map.selectMarker(lat, lon);
  state.map.setSweepCenter(lat, lon);
  if (fly) state.map.flyTo(lat, lon, zoom);

  const token = ++state.pointToken;
  if (!silent) showLoading("Consultando el punto…");

  try {
    const data = await api.point(lat, lon);
    if (token !== state.pointToken) return;
    state.lastPoint = data;
    state.lastSpeech = data.narration || "";
    renderPoint(data);
    if (expand && isMobile()) setPanelCollapsed(false, { save: false });
    if (announce) speakText(directSpeech || data.narration, speak);
  } catch (error) {
    if (token !== state.pointToken) return;
    if (!silent) showToast(error.message, "error");
    if (announce) speakText(`No se pudo consultar este punto. ${error.message}`, speak);
  } finally {
    if (!silent) hideLoading();
  }
}

function refreshPoint() {
  if (!state.selected) return;
  selectPoint(state.selected.lat, state.selected.lon, {
    speak: false,
    silent: true,
    announce: false,
    expand: false,
  });
}

function renderPoint(data) {
  const location = data.location || {};
  const weather = data.weather || {};
  const current = weather.current || {};
  const place = location.display || "Ubicación seleccionada";

  $("placeName").textContent = place;
  $("placeName").title = place;
  $("placeCoords").textContent = `${Number(data.coordinates.lat).toFixed(4)}, ${Number(
    data.coordinates.lon
  ).toFixed(4)}${location.source ? ` · ${location.source}` : ""}`;

  $("nowTemp").textContent = fmt(current.temperature, 0);
  $("nowDesc").textContent = current.description || "Sin descripción";
  $("nowFeels").textContent = `Sensación ${fmt(current.apparentTemperature, 0)}° · humedad ${fmt(
    current.humidity,
    0
  )}%`;
  $("nowTime").textContent = current.time ? String(current.time).slice(11, 16) : "--:--";
  $("nowIcon").innerHTML = weatherIcon(current.icon || "cloud");

  $("mWind").textContent = `${fmt(current.windSpeed, 1)} km/h ${current.windDirectionShort || ""}`;
  $("mGust").textContent = `${fmt(current.windGust, 1)} km/h`;
  $("mRain").textContent = `${fmt(current.precipitation, 1)} mm`;
  $("mRainProb").textContent = `${fmt(weather.nextPrecipitationProbability, 0)}%`;
  $("mClouds").textContent = `${fmt(current.cloudCover, 0)}%`;
  $("mHumidity").textContent = `${fmt(current.humidity, 0)}%`;
  $("mPressure").textContent = `${fmt(current.pressure, 0)} hPa`;

  const risk = current.stormRisk || {};
  $("mStorm").textContent = risk.label || "Nula";
  $("mStorm").title = risk.detail || "";
  $("mStorm").dataset.level = risk.level || "nula";

  const arrow = $("mWindArrow");
  arrow.innerHTML = windArrow();
  if (current.windDirection === null || current.windDirection === undefined) {
    arrow.style.visibility = "hidden";
  } else {
    arrow.style.visibility = "visible";
    arrow.style.setProperty("--wind-rot", `${(Number(current.windDirection) + 180) % 360}deg`);
  }

  renderHourly(weather.hourly || []);
  renderQuakes(data.quakes || [], data.quakeQuery || {});

  $("updatedAt").textContent = `Actualizado ${relativeTime(data.updatedAt)} · hora local ${
    current.time ? String(current.time).slice(11, 16) : "--:--"
  }`;
}

function renderHourly(hours) {
  const strip = $("hourlyStrip");
  strip.replaceChildren();
  for (const hour of hours) {
    const item = el("div", "hourly-item");
    item.title = `${hour.description || "Sin descripción"} · ${fmt(
      hour.precipitationProbability,
      0
    )}% de lluvia · viento ${fmt(hour.windSpeed, 0)} km/h`;

    const time = el("span", "hi-time", hour.time ? String(hour.time).slice(11, 16) : "--:--");
    const icon = el("div", "hi-icon");
    icon.innerHTML = weatherIcon(hour.icon || "cloud");
    const temp = el("strong", "hi-temp", `${fmt(hour.temperature, 0)}°`);
    const rain = el("span", "hi-rain", `${fmt(hour.precipitationProbability, 0)}%`);
    const bar = el("div", "hi-bar");
    const fill = el("span");
    fill.style.width = `${Math.max(0, Math.min(100, Number(hour.precipitationProbability) || 0))}%`;
    bar.append(fill);

    item.append(time, icon, temp, rain, bar);
    strip.append(item);
  }

  if (!hours.length) {
    strip.append(el("p", "muted", "Sin pronóstico horario disponible."));
  }
}

function renderQuakes(quakes, query) {
  const summary = $("quakesSummary");
  const list = $("quakesList");
  list.replaceChildren();

  const radius = query.radiusKm || 500;
  const days = query.days || 7;

  if (!quakes.length) {
    summary.textContent = `Sin sismos de magnitud ${fmt(
      query.minMagnitude || 2.5,
      1
    )} o mayor en ${radius} km durante los últimos ${days} días.`;
    return;
  }

  const nearest = quakes[0];
  summary.textContent = `${quakes.length} sismo${quakes.length === 1 ? "" : "s"} en ${
    radius
  } km (${days} días). El más cercano está a ${fmt(nearest.distanceKm, 0)} km al ${
    nearest.direction || "—"
  }.`;

  for (const quake of quakes.slice(0, 4)) {
    const li = el("li");
    const button = el("button", "quake-item");
    button.type = "button";
    const magnitude = quake.mag === null || quake.mag === undefined ? "?" : fmt(quake.mag, 1);
    button.setAttribute(
      "aria-label",
      `Sismo magnitud ${magnitude} en ${quake.place || "ubicación desconocida"}. Escuchar y centrar el mapa.`
    );

    const badge = el("span", `quake-mag ${magClass(quake.mag)}`, `M ${magnitude}`);
    const info = el("span", "quake-info");
    const place = el("span", "quake-place", quake.place || "Sin nombre");
    const meta = el(
      "span",
      "quake-meta",
      `${fmt(quake.distanceKm, 0)} km al ${quake.direction || "—"} · prof. ${fmt(
        quake.depthKm,
        0
      )} km · ${relativeTime(quake.time)}`
    );

    info.append(place, meta);
    button.append(badge, info);
    button.addEventListener("click", () => handleQuake(quake));
    li.append(button);
    list.append(li);
  }
}

function buildQuakeSpeech(quake) {
  const magnitude = quake.mag === null || quake.mag === undefined ? "desconocida" : fmt(quake.mag, 1);
  const place = quake.place || "ubicación desconocida";
  const depth =
    quake.depthKm === null || quake.depthKm === undefined
      ? ""
      : `, profundidad ${fmt(quake.depthKm, 0)} kilómetros`;
  return `Sismo magnitud ${magnitude}, ${place}${depth}, ${relativeTime(quake.time)}.`;
}

function handleQuake(quake) {
  if (quake.lat === null || quake.lon === null) return;
  const speech = buildQuakeSpeech(quake);
  showToast(speech, "info", 6000);
  selectPoint(quake.lat, quake.lon, {
    speak: true,
    fly: true,
    zoom: 10,
    directSpeech: speech,
    expand: true,
  });
}

// ---------------------------------------------------------------------------
// Panel, capas y búsqueda
// ---------------------------------------------------------------------------

function setPanelCollapsed(collapsed, { save = true } = {}) {
  const panel = $("infoPanel");
  const body = $("panelBody");
  const toggle = $("panelToggle");

  panel.classList.toggle("collapsed", collapsed);
  toggle.setAttribute("aria-expanded", String(!collapsed));
  toggle.setAttribute(
    "aria-label",
    collapsed ? "Expandir panel de información" : "Contraer panel de información"
  );
  body.inert = collapsed;
  body.setAttribute("aria-hidden", String(collapsed));
  document.body.classList.toggle("panel-open", !collapsed);

  if (save) {
    state.settings.panelCollapsed = collapsed;
    saveUiSettings();
  }
}

function closeLayers() {
  $("layersPanel").classList.remove("open");
  $("layersToggle").setAttribute("aria-expanded", "false");
  $("scrim").hidden = true;
}

function openLayers() {
  $("layersPanel").classList.add("open");
  $("layersToggle").setAttribute("aria-expanded", "true");
  $("scrim").hidden = false;
}

function hideSearchResults() {
  $("searchResults").hidden = true;
  $("searchInput").setAttribute("aria-expanded", "false");
}

async function runSearch(query) {
  const token = ++state.searchToken;
  const results = $("searchResults");
  results.hidden = false;
  $("searchInput").setAttribute("aria-expanded", "true");
  results.replaceChildren(el("p", "search-hint", "Buscando…"));

  try {
    const data = await api.search(query);
    if (token !== state.searchToken) return;
    results.replaceChildren();
    if (!data.results || !data.results.length) {
      results.append(el("p", "search-hint", "No se encontraron lugares. Prueba con otro nombre."));
      return;
    }
    for (const result of data.results) {
      const button = el("button", "search-result");
      button.type = "button";
      button.append(
        el("strong", null, result.short || result.display),
        el("span", null, result.display)
      );
      button.addEventListener("click", () => {
        $("searchInput").value = result.short || result.display;
        hideSearchResults();
        state.map.flyTo(result.lat, result.lon, 10);
        selectPoint(result.lat, result.lon, { speak: true, expand: true });
      });
      results.append(button);
    }
  } catch (error) {
    if (token !== state.searchToken) return;
    results.replaceChildren(el("p", "search-hint", error.message));
  }
}

// ---------------------------------------------------------------------------
// Cableado de la interfaz
// ---------------------------------------------------------------------------

function wireUi() {
  document.querySelectorAll('input[name="base"]').forEach((input) => {
    input.addEventListener("change", () => {
      if (input.checked) state.map.setBase(input.value);
    });
  });

  $("radarToggle").addEventListener("change", (event) => {
    state.radarEnabled = event.target.checked;
    state.map.setRadarEnabled(state.radarEnabled);
    updateTimeline();
    updateRadarStatus();
    speakStatus(`Radar ${state.radarEnabled ? "activado" : "desactivado"}.`);
  });

  $("radarOpacity").addEventListener("input", (event) => {
    const value = Number(event.target.value);
    $("radarOpacityValue").textContent = `${value}%`;
    state.map.setRadarOpacity(value / 100);
  });

  $("satelliteToggle").addEventListener("change", (event) => {
    state.satelliteEnabled = event.target.checked;
    applySatellite();
    speakStatus(`Satélite ${state.satelliteEnabled ? "activado" : "desactivado"}.`);
  });

  $("satelliteSelect").addEventListener("change", (event) => {
    state.satLayerId = event.target.value;
    if (state.satelliteEnabled) applySatellite();
  });

  $("satelliteOpacity").addEventListener("input", (event) => {
    const value = Number(event.target.value);
    $("satelliteOpacityValue").textContent = `${value}%`;
    state.map.setSatelliteOpacity(value / 100);
  });

  $("quakesToggle").addEventListener("change", (event) => {
    state.quakesEnabled = event.target.checked;
    if (state.quakesEnabled) {
      if (state.quakes.length) state.map.setQuakes(state.quakes);
      loadQuakes({ silent: true });
    } else {
      state.map.clearQuakes();
    }
    speakStatus(`Sismos ${state.quakesEnabled ? "activados" : "desactivados"}.`);
  });

  $("quakesMag").addEventListener("change", (event) => {
    state.quakeMinMag = Number(event.target.value);
    if (state.quakesEnabled) loadQuakes({ silent: false });
  });

  $("windToggle").addEventListener("change", (event) => {
    state.windEnabled = event.target.checked;
    state.map.setWindEnabled(state.windEnabled);
    if (state.windEnabled) {
      loadWind({ silent: false });
      speakStatus("Viento sobre el mapa activado.");
    } else {
      $("windStatus").textContent = "apagado";
      speakStatus("Viento sobre el mapa desactivado.");
    }
  });

  $("sweepToggle").addEventListener("change", (event) => {
    state.map.setSweepEnabled(event.target.checked);
    speakStatus(`Barrido de radar ${event.target.checked ? "activado" : "desactivado"}.`);
  });

  $("playPause").addEventListener("click", () => {
    if (!state.frames.length) {
      showToast("El radar todavía no tiene imágenes.");
      return;
    }
    setPlaying(!state.playing);
  });

  $("frameSlider").addEventListener("input", (event) => {
    if (!state.frames.length) return;
    setPlaying(false);
    state.frameIndex = Number(event.target.value);
    state.map.setRadarFrame(state.frameIndex);
    updateTimeline();
  });

  $("radarRefresh").addEventListener("click", async () => {
    showToast("Actualizando radar…", "info", 2200);
    await loadRadar({ silent: true });
  });

  $("panelToggle").addEventListener("click", () => {
    setPanelCollapsed(!$("infoPanel").classList.contains("collapsed"));
  });

  $("speakBtn").addEventListener("click", () => {
    if (!state.lastSpeech) {
      showToast("Toca el mapa para obtener un pronóstico.");
      return;
    }
    state.voice.speak(state.lastSpeech);
  });

  $("recenterBtn").addEventListener("click", () => {
    if (!state.selected) {
      showToast("Primero selecciona un punto en el mapa.");
      return;
    }
    state.map.setSweepCenter(state.selected.lat, state.selected.lon);
    state.map.flyTo(
      state.selected.lat,
      state.selected.lon,
      Math.max(state.map.map.getZoom(), 9)
    );
  });

  $("audioToggle").addEventListener("click", () => {
    const next = !state.voice.enabled;
    state.voice.setEnabled(next);
    updateAudioUi();
    if (next) speakStatus("Narración por voz activada.");
    else setLive("Narración por voz desactivada.");
  });

  $("locateBtn").addEventListener("click", () => {
    if (!navigator.geolocation) {
      showToast("Tu navegador no ofrece geolocalización.", "error");
      return;
    }
    showToast("Buscando tu ubicación…", "info", 2500);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const { latitude, longitude } = position.coords;
        state.map.flyTo(latitude, longitude, 10);
        selectPoint(latitude, longitude, { speak: true, expand: true });
      },
      (error) => {
        const message =
          error.code === error.PERMISSION_DENIED
            ? "Permiso de ubicación denegado. En el teléfono necesitas HTTPS o localhost."
            : "No se pudo obtener tu ubicación.";
        showToast(message, "error", 7000);
      },
      { enableHighAccuracy: false, timeout: 12000, maximumAge: 120000 }
    );
  });

  $("layersToggle").addEventListener("click", () => {
    if ($("layersPanel").classList.contains("open")) closeLayers();
    else openLayers();
  });
  $("layersClose").addEventListener("click", closeLayers);
  $("scrim").addEventListener("click", closeLayers);

  $("searchInput").addEventListener("input", (event) => {
    const query = event.target.value.trim();
    $("searchClear").hidden = !query;
    window.clearTimeout(state.searchTimer);
    if (query.length < 2) {
      hideSearchResults();
      return;
    }
    state.searchTimer = window.setTimeout(() => runSearch(query), 420);
  });

  $("searchForm").addEventListener("submit", (event) => {
    event.preventDefault();
    const query = $("searchInput").value.trim();
    if (query.length >= 2) runSearch(query);
  });

  $("searchClear").addEventListener("click", () => {
    $("searchInput").value = "";
    $("searchClear").hidden = true;
    hideSearchResults();
    $("searchInput").focus();
  });

  $("searchInput").addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      const first = $("searchResults").querySelector(".search-result");
      if (first) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  $("searchResults").addEventListener("keydown", (event) => {
    const items = [...$("searchResults").querySelectorAll(".search-result")];
    const index = items.indexOf(document.activeElement);
    if (!items.length) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      items[(index + 1) % items.length].focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      items[(index - 1 + items.length) % items.length].focus();
    } else if (event.key === "Escape") {
      hideSearchResults();
      $("searchInput").focus();
    }
  });

  document.addEventListener("click", (event) => {
    if (!$("searchForm").contains(event.target)) hideSearchResults();
  });

  $("a11yBtn").addEventListener("click", () => {
    syncSettingsDialog();
    $("a11yDialog").showModal();
  });
  $("helpBtn").addEventListener("click", () => $("helpDialog").showModal());

  for (const id of ["a11yDialog", "helpDialog"]) {
    const dialog = $(id);
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  }

  $("optAudio").addEventListener("change", (event) => {
    state.voice.setEnabled(event.target.checked);
    updateAudioUi();
    if (event.target.checked) speakStatus("Narración por voz activada.");
  });

  $("optContrast").addEventListener("change", (event) => {
    state.settings.contrast = event.target.checked;
    document.documentElement.classList.toggle("hc", state.settings.contrast);
    saveUiSettings();
  });

  $("optLarge").addEventListener("change", (event) => {
    state.settings.large = event.target.checked;
    document.documentElement.classList.toggle("large-text", state.settings.large);
    saveUiSettings();
  });

  $("optMotion").addEventListener("change", (event) => {
    state.settings.reduceMotion = event.target.checked;
    document.documentElement.classList.toggle("reduce-motion", state.settings.reduceMotion);
    state.map.setReducedMotion(state.settings.reduceMotion);
    if (state.settings.reduceMotion) setPlaying(false);
    saveUiSettings();
  });

  $("voiceSelect").addEventListener("change", (event) => {
    state.voice.setVoice(event.target.value);
  });

  $("rateRange").addEventListener("input", (event) => {
    const value = Number(event.target.value);
    $("rateOut").textContent = `${value.toFixed(1)}×`;
    state.voice.setRate(value);
  });

  document.addEventListener("keydown", (event) => {
    const tag = (event.target.tagName || "").toLowerCase();
    const typing =
      ["input", "select", "textarea"].includes(tag) || event.target.isContentEditable;

    if (event.key === "Escape") {
      closeLayers();
      hideSearchResults();
      return;
    }
    if (typing || document.querySelector("dialog[open]")) return;

    const key = event.key.toLowerCase();
    if (key === "l") {
      event.preventDefault();
      if (state.lastSpeech) state.voice.speak(state.lastSpeech);
      else showToast("Toca el mapa para obtener un pronóstico.");
    } else if (key === "r") {
      event.preventDefault();
      $("radarToggle").click();
    } else if (key === "s") {
      event.preventDefault();
      $("satelliteToggle").click();
    } else if (key === "q") {
      event.preventDefault();
      $("quakesToggle").click();
    } else if (key === "w") {
      event.preventDefault();
      $("windToggle").click();
    } else if (key === "e") {
      event.preventDefault();
      $("sweepToggle").click();
    } else if (event.code === "Space" && !["button", "a"].includes(tag)) {
      event.preventDefault();
      if (state.frames.length) setPlaying(!state.playing);
    }
  });

  window.addEventListener("online", updateOnlineStatus);
  window.addEventListener("offline", updateOnlineStatus);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    loadRadar({ silent: true });
    if (state.quakesEnabled) loadQuakes({ silent: true });
    if (state.windEnabled) loadWind({ silent: true });
    refreshPoint();
  });
}

function syncSettingsDialog() {
  state.voice.refreshVoices();
  const select = $("voiceSelect");
  select.replaceChildren();
  const auto = el("option", null, "Automática (español)");
  auto.value = "auto";
  select.append(auto);

  for (const voice of state.voice.voices) {
    const option = el("option", null, `${voice.name} (${voice.lang})`);
    option.value = voice.voiceURI;
    select.append(option);
  }

  select.value = state.voice.voiceURI || "auto";
  $("rateRange").value = String(state.voice.rate);
  $("rateOut").textContent = `${state.voice.rate.toFixed(1)}×`;
  $("optAudio").checked = state.voice.enabled;
  $("optContrast").checked = state.settings.contrast;
  $("optLarge").checked = state.settings.large;
  $("optMotion").checked = state.settings.reduceMotion;
}

// ---------------------------------------------------------------------------
// Estado de red, reloj y sondeo
// ---------------------------------------------------------------------------

function updateOnlineStatus() {
  const offline = !navigator.onLine;
  $("offlineBanner").hidden = !offline;
  if (!offline) {
    loadRadar({ silent: true });
    if (state.quakesEnabled) loadQuakes({ silent: true });
    if (state.windEnabled) loadWind({ silent: true });
    refreshPoint();
  }
}

function updateClock() {
  $("topClock").textContent = new Date().toLocaleTimeString("es-MX", { hour12: false });
}

function updateFreshness() {
  if (state.lastPoint) {
    const current = state.lastPoint.weather?.current;
    $("updatedAt").textContent = `Actualizado ${relativeTime(state.lastPoint.updatedAt)} · hora local ${
      current && current.time ? String(current.time).slice(11, 16) : "--:--"
    }`;
  }
  if (state.frames.length) updateRadarStatus();
}

function startPolling() {
  stopPolling();
  const refresh = state.config.refreshMs || FALLBACK_CONFIG.refreshMs;
  state.timers.clock = window.setInterval(updateClock, 1000);
  state.timers.frames = window.setInterval(tickFrames, FRAME_MS);
  state.timers.freshness = window.setInterval(updateFreshness, 10000);
  state.timers.radar = window.setInterval(() => {
    if (!document.hidden) loadRadar({ silent: true });
  }, refresh.radar || 120000);
  state.timers.quakes = window.setInterval(() => {
    if (!document.hidden && state.quakesEnabled) loadQuakes({ silent: true });
  }, refresh.quakes || 60000);
  state.timers.wind = window.setInterval(() => {
    if (!document.hidden && state.windEnabled) loadWind({ silent: true });
  }, refresh.wind || 300000);
  state.timers.point = window.setInterval(() => {
    if (!document.hidden) refreshPoint();
  }, refresh.point || 60000);
}

function stopPolling() {
  for (const timer of Object.values(state.timers)) window.clearInterval(timer);
  state.timers = {};
}

function registerServiceWorker() {
  if (!("serviceWorker" in navigator)) return;
  const secure = location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(location.hostname);
  if (!secure) return;
  navigator.serviceWorker.register("/sw.js").catch(() => {
    // La app funciona igual sin PWA.
  });
}

// ---------------------------------------------------------------------------
// Arranque
// ---------------------------------------------------------------------------

async function init() {
  loadUiSettings();
  state.voice = new Voice();
  if (!state.voice.supported) {
    $("audioToggle").disabled = true;
    $("audioToggle").setAttribute("aria-pressed", "false");
    $("audioToggle").setAttribute("aria-label", "La síntesis de voz no está disponible");
  }

  applyUiSettings();
  updateAudioUi();

  const config = await api.config().catch(() => null);
  state.config = config || FALLBACK_CONFIG;
  if (!config) {
    showToast(
      "No se pudo conectar con el servidor Python. Revisa que pronost.py esté ejecutándose.",
      "error",
      9000
    );
  }

  state.map = new MapView("map", {
    center: state.config.defaultCenter || FALLBACK_CONFIG.defaultCenter,
    zoom: 5,
    reducedMotion: state.settings.reduceMotion,
    onSelect: (lat, lon) => selectPoint(lat, lon, { speak: true, expand: true }),
    onQuake: handleQuake,
    onViewChange: handleViewChange,
  });

  wireUi();
  setPlaying(!state.settings.reduceMotion);

  const collapsed = state.settings.panelCollapsed ?? isMobile();
  setPanelCollapsed(collapsed, { save: false });

  startPolling();
  updateClock();

  showLoading("Conectando con el radar…");
  await Promise.allSettled([loadSatellite(), loadRadar(), loadQuakes()]);
  hideLoading();

  const center = state.map.getCenter();
  await selectPoint(center.lat, center.lng, {
    speak: false,
    silent: true,
    announce: false,
    expand: false,
  });

  if (!state.voice.supported) {
    showToast(
      "Este navegador no admite voz. Toda la información sigue disponible en el panel y para lectores de pantalla.",
      "error",
      8000
    );
  } else if (!state.voice.enabled) {
    showToast("La narración está desactivada. Actívala con el botón Voz.", "info", 6000);
  } else {
    showToast("Toca el mapa para escuchar el pronóstico del lugar.", "info", 6000);
  }

  updateOnlineStatus();
  registerServiceWorker();
}

init().catch((error) => {
  showToast(`Error al iniciar: ${error.message}`, "error", 10000);
  // eslint-disable-next-line no-console
  console.error(error);
});

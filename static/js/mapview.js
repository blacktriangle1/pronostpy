const L = window.L;

function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (char) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char]
  );
}

const BASEMAPS = {
  dark: () => {
    const base = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 19,
        maxNativeZoom: 16,
        attribution: "&copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors",
      }
    );
    const labels = L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 19,
        maxNativeZoom: 16,
      }
    );
    return L.layerGroup([base, labels]);
  },
  streets: () =>
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 19,
        maxNativeZoom: 19,
        attribution: "&copy; Esri, HERE, Garmin, &copy; OpenStreetMap contributors",
      }
    ),
  satellite: () =>
    L.tileLayer(
      "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
      {
        maxZoom: 19,
        maxNativeZoom: 19,
        attribution: "Imagen: Esri, Maxar, Earthstar Geographics",
      }
    ),
};

const WindLayer = L.Layer.extend({
  initialize(points) {
    this._points = points || [];
    this._canvas = null;
    this._ctx = null;
  },

  onAdd(map) {
    this._map = map;
    this._canvas = L.DomUtil.create("canvas", "wind-canvas");
    this._canvas.setAttribute("aria-hidden", "true");
    map.getPanes().wind.appendChild(this._canvas);
    map.on("moveend zoomend resize", this._reset, this);
    this._reset();
  },

  onRemove(map) {
    map.off("moveend zoomend resize", this._reset, this);
    if (this._canvas) {
      this._canvas.remove();
      this._canvas = null;
    }
    this._ctx = null;
  },

  setData(points) {
    this._points = points || [];
    this._draw();
  },

  _reset() {
    if (!this._map || !this._canvas) return;
    const size = this._map.getSize();
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this._canvas.width = Math.max(1, Math.round(size.x * dpr));
    this._canvas.height = Math.max(1, Math.round(size.y * dpr));
    this._canvas.style.width = `${size.x}px`;
    this._canvas.style.height = `${size.y}px`;
    const topLeft = this._map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(this._canvas, topLeft);
    const ctx = this._canvas.getContext("2d");
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this._ctx = ctx;
    this._draw();
  },

  _draw() {
    const ctx = this._ctx;
    const map = this._map;
    if (!ctx || !map) return;
    const size = map.getSize();
    ctx.clearRect(0, 0, size.x, size.y);
    ctx.lineCap = "round";

    for (const point of this._points) {
      if (
        point.lat == null ||
        point.lon == null ||
        point.speed == null ||
        point.direction == null
      ) {
        continue;
      }
      const screen = map.latLngToContainerPoint([point.lat, point.lon]);
      if (screen.x < -30 || screen.y < -30 || screen.x > size.x + 30 || screen.y > size.y + 30) {
        continue;
      }

      const speed = Math.max(0, Math.min(90, Number(point.speed) || 0));
      const angle = ((Number(point.direction) || 0) + 180) * (Math.PI / 180);
      const dx = Math.sin(angle);
      const dy = -Math.cos(angle);
      const length = 9 + speed * 0.55;
      const x1 = screen.x - dx * length * 0.5;
      const y1 = screen.y - dy * length * 0.5;
      const x2 = screen.x + dx * length * 0.5;
      const y2 = screen.y + dy * length * 0.5;
      const hue = Math.max(0, 190 - (speed / 90) * 190);
      const color = `hsla(${hue}, 95%, 63%, 0.92)`;

      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.lineWidth = 1.7;

      ctx.beginPath();
      ctx.moveTo(x1, y1);
      ctx.lineTo(x2, y2);
      ctx.stroke();

      const back = Math.atan2(-dy, -dx);
      const head = Math.min(6, length * 0.4);
      ctx.beginPath();
      ctx.moveTo(x2 + Math.cos(back - 0.5) * head, y2 + Math.sin(back - 0.5) * head);
      ctx.lineTo(x2, y2);
      ctx.lineTo(x2 + Math.cos(back + 0.5) * head, y2 + Math.sin(back + 0.5) * head);
      ctx.stroke();

      ctx.globalAlpha = 0.55;
      ctx.beginPath();
      ctx.arc(x1, y1, 1.5, 0, Math.PI * 2);
      ctx.fill();
      ctx.globalAlpha = 1;
    }
  },
});

export class MapView {
  constructor(element, options = {}) {
    this.onSelect = options.onSelect || (() => {});
    this.onQuake = options.onQuake || (() => {});
    this.onStorm = options.onStorm || (() => {});
    this.onWave = options.onWave || (() => {});
    this.onViewChange = options.onViewChange || (() => {});
    this.reducedMotion = Boolean(options.reducedMotion);

    const center = options.center || { lat: 19.4326, lon: -99.1332 };
    this.map = L.map(element, {
      zoomControl: false,
      minZoom: 2,
      maxZoom: 18,
      worldCopyJump: true,
      attributionControl: true,
      zoomSnap: 0.5,
      wheelPxPerZoomLevel: 110,
    }).setView([center.lat, center.lon], options.zoom || 5);

    L.control.zoom({ position: "topright" }).addTo(this.map);
    L.control.scale({ position: "bottomleft", imperial: false, maxWidth: 120 }).addTo(this.map);
    this.map.attributionControl.addAttribution(
      "Radar &copy; RainViewer · Sat&eacute;lite &copy; NASA GIBS · Sismos &copy; USGS · Tormentas &copy; NHC / Esri"
    );

    this._createPanes();

    this._baseKey = "dark";
    this._base = BASEMAPS.dark().addTo(this.map);

    this.radarEnabled = true;
    this.radarOpacity = 0.7;
    this.radarFrames = [];
    this.radarIndex = 0;
    this.radarLayer = null;

    this.satelliteLayer = null;
    this.satelliteLayerId = null;
    this.satelliteOpacity = 0.6;
    this.satelliteFrames = [];
    this.satelliteFrameLayers = [];
    this.satelliteFrameIndex = 0;
    this.satelliteFramesKey = "";
    this.satelliteFrameMs = 900;

    this.sweepEnabled = true;
    this.sweepCenter = L.latLng(center.lat, center.lon);
    this.sweepMarker = null;
    this._sweepSizeValue = 0;
    this.sweepAngle = 0;
    this._sweepLoopStart = performance.now();
    this._sweepLastPaint = 0;
    this._sweepRaf = null;

    this.quakeGroup = L.layerGroup().addTo(this.map);
    this.stormGroup = L.layerGroup().addTo(this.map);
    this.stormsEnabled = true;
    this.storms = [];
    this.waveGroup = L.layerGroup().addTo(this.map);
    this.wavesEnabled = true;
    this.waves = [];
    this.waveMeta = null;
    this.selectionMarker = null;

    this.windEnabled = false;
    this.windPoints = [];
    this.windLayer = null;

    this.map.on("click", (event) => {
      this.onSelect(event.latlng.lat, event.latlng.lng, "mapa");
    });
    this.map.on("moveend", () => {
      this.onViewChange(this.getBounds());
    });
    this.map.on("move zoom", () => this._updateSweepVars());
    this.map.on("resize", () => {
      this._updateSweepSize();
      this._updateSweepVars();
    });

    this.setSweepCenter(center.lat, center.lon);
    this._startSweepLoop();
    window.setTimeout(() => this.map.invalidateSize(), 120);
  }

  _createPanes() {
    const panes = [
      ["sat", 350],
      ["radar", 400],
      ["wavecorridor", 401],
      ["stormcone", 402],
      ["wind", 450],
      ["stormtrack", 455],
      ["sweep", 460],
      ["waves", 465],
      ["quakes", 470],
      ["storms", 475],
      ["wavemarkers", 476],
      ["selection", 480],
    ];
    for (const [name, zIndex] of panes) {
      const pane = this.map.createPane(name);
      pane.style.zIndex = String(zIndex);
      pane.style.pointerEvents = ["quakes", "storms", "wavemarkers"].includes(name)
        ? "auto"
        : "none";
    }
  }

  setBase(key) {
    if (!BASEMAPS[key] || key === this._baseKey) return;
    if (this._base) this.map.removeLayer(this._base);
    this._baseKey = key;
    this._base = BASEMAPS[key]().addTo(this.map);
  }

  setReducedMotion(value) {
    this.reducedMotion = Boolean(value);
    if (this.reducedMotion) {
      this.sweepAngle = 0;
      this.map.getContainer().style.setProperty("--sweep-angle", "0deg");
    } else {
      this._sweepLoopStart =
        performance.now() - (this.sweepAngle / 360) * 3800;
    }
  }

  getBounds() {
    return this.map.getBounds();
  }

  getCenter() {
    return this.map.getCenter();
  }

  flyTo(lat, lon, zoom = 10) {
    if (this.reducedMotion) {
      this.map.setView([lat, lon], zoom);
    } else {
      this.map.flyTo([lat, lon], zoom, { duration: 0.9 });
    }
  }

  setView(lat, lon, zoom) {
    if (zoom) this.map.setView([lat, lon], zoom);
    else this.map.panTo([lat, lon]);
  }

  // ----------------------------- Radar de lluvia -----------------------------

  setRadarFrames(frames) {
    this.radarFrames = Array.isArray(frames) ? frames : [];
  }

  setRadarFrame(index) {
    if (!this.radarFrames.length) return;
    const safeIndex = Math.max(0, Math.min(this.radarFrames.length - 1, Number(index) || 0));
    this.radarIndex = safeIndex;
    const frame = this.radarFrames[safeIndex];
    if (!frame) return;
    if (this.radarLayer) {
      this.map.removeLayer(this.radarLayer);
      this.radarLayer = null;
    }
    if (!this.radarEnabled) return;
    this.radarLayer = L.tileLayer(frame.url, {
      pane: "radar",
      opacity: this.radarOpacity,
      tileSize: 256,
      maxNativeZoom: 7,
      maxZoom: 18,
      className: "radar-tiles",
      zIndex: 1,
    }).addTo(this.map);
  }

  setRadarEnabled(enabled) {
    this.radarEnabled = Boolean(enabled);
    if (!this.radarEnabled && this.radarLayer) {
      this.map.removeLayer(this.radarLayer);
      this.radarLayer = null;
    } else if (this.radarEnabled && this.radarFrames.length) {
      this.setRadarFrame(this.radarIndex);
    }
  }

  setRadarOpacity(opacity) {
    this.radarOpacity = Math.max(0, Math.min(1, Number(opacity) || 0));
    if (this.radarLayer) this.radarLayer.setOpacity(this.radarOpacity);
  }

  // -------------------------------- Satélite ---------------------------------

  _satelliteTileOptions(layer, opacity) {
    return {
      pane: "sat",
      opacity,
      maxNativeZoom: layer.maxNativeZoom || 7,
      maxZoom: 18,
      attribution: layer.attribution || "NASA GIBS",
      className: "sat-tiles",
      zIndex: 1,
    };
  }

  _clearSatelliteLayers() {
    const frameLayers = this.satelliteFrameLayers;
    this.satelliteFrameLayers = [];
    for (const frameLayer of frameLayers) this.map.removeLayer(frameLayer);
    if (this.satelliteLayer && !frameLayers.includes(this.satelliteLayer)) {
      this.map.removeLayer(this.satelliteLayer);
    }
    this.satelliteLayer = null;
    this.satelliteFrames = [];
    this.satelliteFrameIndex = 0;
    this.satelliteFramesKey = "";
    this._updateSatReveal();
  }

  setSatellite(layer) {
    if (!layer || !layer.template) {
      this.clearSatellite();
      return;
    }
    if (
      this.satelliteLayerId === layer.id &&
      this.satelliteLayer &&
      !this.satelliteFrameLayers.length
    ) {
      return;
    }
    this._clearSatelliteLayers();
    this.satelliteLayerId = layer.id;
    this.satelliteLayer = L.tileLayer(
      layer.template,
      this._satelliteTileOptions(layer, this.satelliteOpacity)
    ).addTo(this.map);
    this._updateSatReveal();
  }

  setSatelliteFrames(layer, frames) {
    const valid = Array.isArray(frames)
      ? frames.filter((frame) => frame && frame.url)
      : [];
    if (!layer || !layer.template || valid.length < 2) {
      this.setSatellite(layer);
      return;
    }
    const key = `${layer.id}:${valid
      .map((frame) => frame.time || frame.url)
      .join("|")}`;
    if (
      this.satelliteFramesKey === key &&
      this.satelliteFrameLayers.length === valid.length
    ) {
      return;
    }
    this._clearSatelliteLayers();
    this.satelliteLayerId = layer.id;
    this.satelliteFrames = valid;
    this.satelliteFramesKey = key;
    this.satelliteFrameIndex = 0;
    this.satelliteFrameLayers = valid.map((frame, index) =>
      L.tileLayer(
        frame.url,
        this._satelliteTileOptions(
          layer,
          index === 0 ? this.satelliteOpacity : 0
        )
      ).addTo(this.map)
    );
    this.satelliteLayer = this.satelliteFrameLayers[0] || null;
    this._updateSatReveal();
  }

  setSatelliteFrame(index) {
    if (!this.satelliteFrameLayers.length || !this.satelliteFrames.length) {
      return;
    }
    const safeIndex = Math.max(
      0,
      Math.min(this.satelliteFrames.length - 1, Number(index) || 0)
    );
    this.satelliteFrameIndex = safeIndex;
    this.satelliteFrameLayers.forEach((layer, layerIndex) => {
      layer.setOpacity(
        layerIndex === safeIndex ? this.satelliteOpacity : 0
      );
    });
  }

  clearSatellite() {
    this._clearSatelliteLayers();
    this.satelliteLayerId = null;
  }

  setSatelliteOpacity(opacity) {
    this.satelliteOpacity = Math.max(0, Math.min(1, Number(opacity) || 0));
    if (this.satelliteFrameLayers.length) {
      this.setSatelliteFrame(this.satelliteFrameIndex);
    } else if (this.satelliteLayer) {
      this.satelliteLayer.setOpacity(this.satelliteOpacity);
    }
  }

  // ------------------------- Barrido y selección -----------------------------

  _sweepSize() {
    const size = this.map.getSize();
    return Math.round(Math.min(680, Math.max(220, Math.min(size.x, size.y) * 0.86)));
  }

  _updateSweepVars() {
    if (!this.sweepCenter) return;
    const point = this.map.latLngToLayerPoint(this.sweepCenter);
    const container = this.map.getContainer();
    const size = this.map.getSize();
    const satPane = this.map.getPane("sat");
    if (satPane) {
      satPane.style.width = `${size.x}px`;
      satPane.style.height = `${size.y}px`;
    }
    container.style.setProperty("--sweep-x", `${Math.round(point.x)}px`);
    container.style.setProperty("--sweep-y", `${Math.round(point.y)}px`);
    container.style.setProperty(
      "--sweep-radius",
      `${Math.round(this._sweepSize() / 2)}px`
    );
  }

  _startSweepLoop() {
    if (this._sweepRaf) return;
    const step = (now) => {
      this._sweepRaf = window.requestAnimationFrame(step);
      if (this.reducedMotion) {
        this.sweepAngle = 0;
      } else {
        this.sweepAngle = (((now - this._sweepLoopStart) / 3800) * 360) % 360;
      }
      if (now - this._sweepLastPaint < 33) return;
      this._sweepLastPaint = now;
      this.map
        .getContainer()
        .style.setProperty("--sweep-angle", `${this.sweepAngle.toFixed(2)}deg`);
    };
    this._sweepRaf = window.requestAnimationFrame(step);
  }

  _updateSatReveal() {
    const enabled = Boolean(this.sweepEnabled && this.satelliteLayer);
    this.map.getContainer().classList.toggle("sat-reveal", enabled);
  }

  _sweepIcon(size) {
    const html = `
      <div class="sweep" style="width:${size}px;height:${size}px">
        <div class="sweep-glow"></div>
        <div class="sweep-grid"></div>
        <div class="sweep-beam"></div>
        <div class="sweep-core"></div>
      </div>`;
    return L.divIcon({
      className: "sweep-wrap",
      html,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    });
  }

  _renderSweep() {
    if (!this.sweepCenter) return;
    this._updateSweepVars();
    const size = this._sweepSize();
    if (this.sweepMarker) {
      this.sweepMarker.setLatLng(this.sweepCenter);
      if (size !== this._sweepSizeValue) {
        this._sweepSizeValue = size;
        this.sweepMarker.setIcon(this._sweepIcon(size));
      }
      return;
    }
    this._sweepSizeValue = size;
    this.sweepMarker = L.marker(this.sweepCenter, {
      icon: this._sweepIcon(size),
      pane: "sweep",
      interactive: false,
      keyboard: false,
      zIndexOffset: -1000,
    }).addTo(this.map);
  }

  setSweepCenter(lat, lon) {
    this.sweepCenter = L.latLng(lat, lon);
    this._updateSweepVars();
    if (this.sweepEnabled) this._renderSweep();
  }

  setSweepEnabled(enabled) {
    this.sweepEnabled = Boolean(enabled);
    if (this.sweepEnabled) {
      this._renderSweep();
    } else if (this.sweepMarker) {
      this.map.removeLayer(this.sweepMarker);
      this.sweepMarker = null;
      this._sweepSizeValue = 0;
    }
    this._updateSatReveal();
  }

  _updateSweepSize() {
    if (!this.sweepEnabled || !this.sweepMarker) return;
    const size = this._sweepSize();
    if (size !== this._sweepSizeValue) {
      this._sweepSizeValue = size;
      this.sweepMarker.setIcon(this._sweepIcon(size));
    }
    this._updateSweepVars();
  }

  selectMarker(lat, lon) {
    const icon = L.divIcon({
      className: "selection-wrap",
      html: '<div class="selection-dot"></div>',
      iconSize: [34, 34],
      iconAnchor: [17, 17],
    });
    if (this.selectionMarker) {
      this.selectionMarker.setLatLng([lat, lon]);
      this.selectionMarker.setIcon(icon);
      return;
    }
    this.selectionMarker = L.marker([lat, lon], {
      icon,
      pane: "selection",
      interactive: false,
      keyboard: false,
      zIndexOffset: 1000,
    }).addTo(this.map);
  }

  // --------------------------------- Sismos ----------------------------------

  _quakeIcon(event) {
    const magnitude = Number(event.mag);
    const value = Number.isFinite(magnitude) ? magnitude : 0;
    const color =
      value >= 6
        ? "#ff5d7a"
        : value >= 5
          ? "#ff9a5a"
          : value >= 4
            ? "#ffc857"
            : "#7fd3ff";
    const size = Math.round(14 + Math.max(0, value) * 3);
    const label = value >= 4.5 ? value.toFixed(1) : "";
    return L.divIcon({
      className: "quake-wrap",
      html: `<div class="quake-dot" style="width:${size}px;height:${size}px;background:${color}26;border-color:${color};color:${color}">${label}</div>`,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    });
  }

  setQuakes(events) {
    this.quakeGroup.clearLayers();
    for (const event of events || []) {
      if (event.lat == null || event.lon == null) continue;
      const marker = L.marker([event.lat, event.lon], {
        icon: this._quakeIcon(event),
        pane: "quakes",
        keyboard: true,
        title: `Sismo magnitud ${event.mag ?? "?"} · ${event.place || ""}`,
        alt: `Sismo magnitud ${event.mag ?? "?"} en ${event.place || "ubicación desconocida"}`,
        riseOnHover: true,
      });
      marker.bindTooltip(
        `M ${event.mag ?? "?"} · ${event.place || "Sin nombre"}`,
        { direction: "top", className: "quake-tooltip", offset: [0, -6] }
      );
      marker.on("click", (domEvent) => {
        if (domEvent.originalEvent) L.DomEvent.stopPropagation(domEvent.originalEvent);
        this.onQuake(event);
      });
      marker.addTo(this.quakeGroup);
    }
  }

  clearQuakes() {
    this.quakeGroup.clearLayers();
  }

  // ------------------------------ Tormentas ----------------------------------

  _stormColor(storm) {
    const level = (storm.alert || {}).level;
    if (level === "alta") return "#ff5d7a";
    if (level === "moderada") return "#ffc857";
    return "#7fd3ff";
  }

  _stormIcon(storm) {
    const color = this._stormColor(storm);
    const name = escapeHtml(storm.name || "Tormenta");
    const label = escapeHtml(storm.category || storm.classification || "Tormenta");
    const wind = Number(storm.windKmh);
    const windText = Number.isFinite(wind) ? ` · ${Math.round(wind)} km/h` : "";
    return L.divIcon({
      className: "storm-wrap",
      html: `
        <div class="storm-marker" style="--storm-color:${color}">
          <span class="storm-glyph" aria-hidden="true">
            <svg viewBox="0 0 32 32" focusable="false">
              <circle cx="16" cy="16" r="2.6" fill="currentColor"/>
              <path d="M16 4c6.6 0 11 4.1 11 9.4 0 4.6-3.5 8.1-8.4 8.1" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>
              <path d="M16 28c-6.6 0-11-4.1-11-9.4 0-4.6 3.5-8.1 8.4-8.1" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>
            </svg>
          </span>
          <span class="storm-tag"><strong>${name}</strong><em>${label}${windText}</em></span>
        </div>`,
      iconSize: [46, 46],
      iconAnchor: [23, 23],
    });
  }

  setStorms(storms) {
    this.storms = Array.isArray(storms) ? storms : [];
    this.stormGroup.clearLayers();
    if (!this.stormsEnabled) return;

    for (const storm of this.storms) {
      if (storm.lat == null || storm.lon == null) continue;
      const color = this._stormColor(storm);

      for (const ring of storm.cone || []) {
        if (!Array.isArray(ring) || ring.length < 3) continue;
        L.polygon(ring, {
          pane: "stormcone",
          color,
          weight: 1.4,
          opacity: 0.65,
          fillColor: color,
          fillOpacity: 0.08,
          interactive: false,
        }).addTo(this.stormGroup);
      }

      for (const line of storm.watches || []) {
        if (!Array.isArray(line) || line.length < 2) continue;
        L.polyline(line, {
          pane: "stormtrack",
          color: "#ffc857",
          weight: 2.5,
          opacity: 0.9,
          dashArray: "8 4",
          interactive: false,
        }).addTo(this.stormGroup);
      }

      const observed = storm.observedTrack || [];
      if (observed.length > 1) {
        L.polyline(
          observed.map((point) => [point.lat, point.lon]),
          {
            pane: "stormtrack",
            color: "#cfefff",
            weight: 2,
            opacity: 0.6,
            dashArray: "4 6",
            interactive: false,
          }
        ).addTo(this.stormGroup);
      }

      const forecast = storm.forecastTrack || [];
      if (forecast.length > 1) {
        L.polyline(
          forecast.map((point) => [point.lat, point.lon]),
          {
            pane: "stormtrack",
            color,
            weight: 3,
            opacity: 0.95,
            interactive: false,
          }
        ).addTo(this.stormGroup);
      }

      for (const point of forecast) {
        if (point.lat == null || point.lon == null) continue;
        L.circleMarker([point.lat, point.lon], {
          pane: "stormtrack",
          radius: 3,
          color,
          weight: 1,
          fillColor: color,
          fillOpacity: 1,
          interactive: false,
        }).addTo(this.stormGroup);
      }

      const title = `${storm.name || "Tormenta"} · ${
        storm.category || storm.classification || ""
      }`;
      const marker = L.marker([storm.lat, storm.lon], {
        icon: this._stormIcon(storm),
        pane: "storms",
        keyboard: true,
        title,
        alt: title,
        riseOnHover: true,
        zIndexOffset: 800,
      });
      marker.bindTooltip(
        `${storm.name || "Tormenta"} · ${
          storm.category || storm.classification || ""
        } · ${Math.round(Number(storm.windKmh) || 0)} km/h`,
        { direction: "top", className: "storm-tooltip", offset: [0, -10] }
      );
      marker.on("click", (domEvent) => {
        if (domEvent.originalEvent) L.DomEvent.stopPropagation(domEvent.originalEvent);
        this.onStorm(storm);
      });
      marker.addTo(this.stormGroup);
    }
  }

  setStormsEnabled(enabled) {
    this.stormsEnabled = Boolean(enabled);
    if (this.stormsEnabled) {
      if (!this.map.hasLayer(this.stormGroup)) this.stormGroup.addTo(this.map);
      this.setStorms(this.storms);
    } else if (this.map.hasLayer(this.stormGroup)) {
      this.map.removeLayer(this.stormGroup);
    }
  }

  focusStorms() {
    const points = [];
    for (const storm of this.storms) {
      if (storm.lat != null && storm.lon != null) {
        points.push([storm.lat, storm.lon]);
      }
      for (const point of storm.forecastTrack || []) {
        if (point && point.lat != null && point.lon != null) {
          points.push([point.lat, point.lon]);
        }
      }
      for (const ring of storm.cone || []) {
        for (const point of ring || []) {
          if (Array.isArray(point) && point.length >= 2) points.push(point);
        }
      }
    }
    if (!points.length) return false;
    const bounds = L.latLngBounds(points);
    if (!bounds.isValid()) return false;
    this.map.fitBounds(bounds, { padding: [50, 50], maxZoom: 6 });
    return true;
  }

  // ----------------------------- Ondas tropicales ----------------------------

  _waveColor(wave) {
    const status = wave.status || "";
    if (status === "En Venezuela") return "#ffb020";
    if (status === "Al este de Venezuela") return "#5cc8ff";
    if (status === "Salió de Venezuela") return "#6ee7b7";
    return "#9aa7b8";
  }

  _waveShortStatus(status) {
    if (status === "En Venezuela") return "En Venezuela";
    if (status === "Al este de Venezuela") return "Al este";
    if (status === "Salió de Venezuela") return "Salió";
    return "Sin datos";
  }

  _waveMoment(value) {
    if (!value) return "—";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "—";
    return date.toLocaleString("es-MX", {
      day: "2-digit",
      month: "short",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  }

  _waveIcon(wave) {
    const color = this._waveColor(wave);
    const label = escapeHtml(`Onda ${wave.number ?? ""}`.trim());
    const status = escapeHtml(this._waveShortStatus(wave.status));
    return L.divIcon({
      className: "wave-wrap",
      html: `
        <div class="wave-marker" style="--wave-color:${color}">
          <span class="wave-glyph" aria-hidden="true">
            <svg viewBox="0 0 32 32" focusable="false">
              <path d="M3 20c4-8 8-8 12 0s8 8 12 0" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>
              <circle cx="16" cy="16" r="2.2" fill="currentColor"/>
            </svg>
          </span>
          <span class="wave-tag"><strong>${label}</strong><em>${status}</em></span>
        </div>`,
      iconSize: [34, 34],
      iconAnchor: [17, 17],
    });
  }

  setWaves(waves, meta = null) {
    this.waves = Array.isArray(waves) ? waves : [];
    this.waveMeta = meta || this.waveMeta;
    this.waveGroup.clearLayers();
    if (!this.wavesEnabled) return;

    const entryLon = Number(this.waveMeta?.entryLon ?? -59.5);
    const exitLon = Number(this.waveMeta?.exitLon ?? -73.5);
    if (Number.isFinite(entryLon) && Number.isFinite(exitLon)) {
      L.rectangle(
        [
          [0, exitLon],
          [25, entryLon],
        ],
        {
          pane: "wavecorridor",
          color: "#5cc8ff",
          weight: 1,
          opacity: 0.25,
          fillColor: "#5cc8ff",
          fillOpacity: 0.035,
          dashArray: "7 7",
          interactive: false,
        }
      ).addTo(this.waveGroup);
    }

    for (const wave of this.waves) {
      const color = this._waveColor(wave);
      const points = (wave.observations || [])
        .filter((item) => item.latMid != null && item.lon != null)
        .map((item) => [item.latMid, item.lon]);
      if (points.length > 1) {
        L.polyline(points, {
          pane: "waves",
          color,
          weight: 2.5,
          opacity: 0.8,
          dashArray: wave.crossedVenezuela ? null : "5 6",
          interactive: false,
        }).addTo(this.waveGroup);
      }

      for (const key of ["entryPoint", "exitPoint"]) {
        const point = wave[key];
        if (!Array.isArray(point) || point.length < 2) continue;
        const isEntry = key === "entryPoint";
        const estimated = isEntry ? wave.entryEstimated : wave.exitEstimated;
        const when = this._waveMoment(isEntry ? wave.entryAt : wave.exitAt);
        L.circleMarker(point, {
          pane: "wavemarkers",
          radius: 4,
          color,
          weight: 1.5,
          fillColor: "#071019",
          fillOpacity: 0.9,
          interactive: true,
        })
          .bindTooltip(
            `${isEntry ? "Entrada" : "Salida"}${estimated ? " estimada" : ""} · ${when}`,
            { direction: "top", className: "wave-tooltip", offset: [0, -6] }
          )
          .addTo(this.waveGroup);
      }

      const current = wave.current;
      if (!current || current.lon == null) continue;
      const title = `Onda tropical ${wave.number} · ${wave.status}`;
      const marker = L.marker([current.latMid, current.lon], {
        icon: this._waveIcon(wave),
        pane: "wavemarkers",
        keyboard: true,
        title,
        alt: title,
        riseOnHover: true,
        zIndexOffset: 700,
      });
      marker.bindTooltip(
        `${title} · ${current.lonWest}°O · ${current.speedKtMin}–${current.speedKtMax} kt`,
        { direction: "top", className: "wave-tooltip", offset: [0, -10] }
      );
      marker.on("click", (domEvent) => {
        if (domEvent.originalEvent) L.DomEvent.stopPropagation(domEvent.originalEvent);
        this.onWave(wave);
      });
      marker.addTo(this.waveGroup);
    }
  }

  setWavesEnabled(enabled) {
    this.wavesEnabled = Boolean(enabled);
    if (this.wavesEnabled) {
      if (!this.map.hasLayer(this.waveGroup)) this.waveGroup.addTo(this.map);
      this.setWaves(this.waves, this.waveMeta);
    } else if (this.map.hasLayer(this.waveGroup)) {
      this.map.removeLayer(this.waveGroup);
    }
  }

  clearWaves() {
    this.waveGroup.clearLayers();
    this.waves = [];
    this.waveMeta = null;
  }

  // --------------------------------- Viento ----------------------------------

  setWindEnabled(enabled) {
    this.windEnabled = Boolean(enabled);
    if (this.windEnabled) {
      if (!this.windLayer) {
        this.windLayer = new WindLayer(this.windPoints);
        this.windLayer.addTo(this.map);
      }
    } else if (this.windLayer) {
      this.map.removeLayer(this.windLayer);
      this.windLayer = null;
    }
  }

  setWind(points) {
    this.windPoints = Array.isArray(points) ? points : [];
    if (this.windLayer) this.windLayer.setData(this.windPoints);
  }
}

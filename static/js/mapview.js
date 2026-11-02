const L = window.L;

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
      "Radar &copy; RainViewer · Sat&eacute;lite &copy; NASA GIBS · Sismos &copy; USGS"
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
    this.satelliteOpacity = 0.6;

    this.sweepEnabled = true;
    this.sweepCenter = L.latLng(center.lat, center.lon);
    this.sweepMarker = null;
    this._sweepSizeValue = 0;

    this.quakeGroup = L.layerGroup().addTo(this.map);
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
    this.map.on("resize", () => this._updateSweepSize());

    this.setSweepCenter(center.lat, center.lon);
    window.setTimeout(() => this.map.invalidateSize(), 120);
  }

  _createPanes() {
    const panes = [
      ["sat", 350],
      ["radar", 400],
      ["wind", 450],
      ["sweep", 460],
      ["quakes", 470],
      ["selection", 480],
    ];
    for (const [name, zIndex] of panes) {
      const pane = this.map.createPane(name);
      pane.style.zIndex = String(zIndex);
      pane.style.pointerEvents = name === "quakes" ? "auto" : "none";
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

  setSatellite(layer) {
    if (this.satelliteLayer) {
      this.map.removeLayer(this.satelliteLayer);
      this.satelliteLayer = null;
    }
    if (!layer || !layer.template) return;
    this.satelliteLayer = L.tileLayer(layer.template, {
      pane: "sat",
      opacity: this.satelliteOpacity,
      maxNativeZoom: layer.maxNativeZoom || 7,
      maxZoom: 18,
      attribution: layer.attribution || "NASA GIBS",
      className: "sat-tiles",
      zIndex: 1,
    }).addTo(this.map);
  }

  clearSatellite() {
    this.setSatellite(null);
  }

  setSatelliteOpacity(opacity) {
    this.satelliteOpacity = Math.max(0, Math.min(1, Number(opacity) || 0));
    if (this.satelliteLayer) this.satelliteLayer.setOpacity(this.satelliteOpacity);
  }

  // ------------------------- Barrido y selección -----------------------------

  _sweepSize() {
    const size = this.map.getSize();
    return Math.round(Math.min(680, Math.max(220, Math.min(size.x, size.y) * 0.86)));
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
  }

  _updateSweepSize() {
    if (!this.sweepEnabled || !this.sweepMarker) return;
    const size = this._sweepSize();
    if (size !== this._sweepSizeValue) {
      this._sweepSizeValue = size;
      this.sweepMarker.setIcon(this._sweepIcon(size));
    }
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

/** Leaflet map overlay: XYZ basemaps + georeferenced TIF layers (QGIS-style). */

const DDA_BASEMAPS = [
  {
    id: 'osm',
    label: 'OpenStreetMap',
    url: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
    attribution: '&copy; OpenStreetMap contributors',
    maxZoom: 19,
  },
  {
    id: 'google-satellite',
    label: 'Google Satellite',
    url: 'https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}',
    attribution: 'Imagery &copy; Google',
    maxZoom: 21,
    subdomains: '0123',
  },
  {
    id: 'google-hybrid',
    label: 'Google Hybrid',
    url: 'https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}',
    attribution: 'Imagery &copy; Google',
    maxZoom: 21,
    subdomains: '0123',
  },
  {
    id: 'esri-imagery',
    label: 'Esri World Imagery',
    url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
    attribution: 'Tiles &copy; Esri',
    maxZoom: 19,
  },
  {
    id: 'custom-xyz',
    label: 'Custom XYZ…',
    url: '',
    attribution: '',
    maxZoom: 22,
  },
];

const DDA_MAP_LS_BASE = 'ddaMapBasemap';
const DDA_MAP_LS_XYZ = 'ddaMapCustomXyz';
const DDA_MAP_LS_OPACITY = 'ddaMapOverlayOpacity';

function ddaLeafletReady() {
  return typeof L !== 'undefined';
}

function ddaBoundsToLatLng(bounds) {
  if (!bounds) return null;
  if (Array.isArray(bounds.latLng) && bounds.latLng.length === 2) return bounds.latLng;
  if (bounds.west != null) {
    return [[bounds.south, bounds.west], [bounds.north, bounds.east]];
  }
  if (Array.isArray(bounds) && bounds.length === 4) {
    const [west, south, east, north] = bounds;
    return [[south, west], [north, east]];
  }
  return null;
}

function DdaMapViewer(containerId, options) {
  this.containerId = containerId;
  this.options = options || {};
  this.map = null;
  this.basemapLayer = null;
  this.rasterLayers = {};
  this.layerEnabled = {};
  this.rasterParts = {};
  this.geojsonLayer = null;
  this.overlayOn = true;
  this.opacity = Number(localStorage.getItem(DDA_MAP_LS_OPACITY) || 0.85);
  if (Number.isNaN(this.opacity)) this.opacity = 0.75;
  this.basemapId = localStorage.getItem(DDA_MAP_LS_BASE) || 'google-satellite';
  this.customXyz = localStorage.getItem(DDA_MAP_LS_XYZ) || '';
  this.lastFitBounds = null;
}

DdaMapViewer.prototype.ensureMap = function ensureMap() {
  if (this.map) return this.map;
  if (!ddaLeafletReady()) {
    console.warn('Leaflet failed to load');
    return null;
  }
  const el = document.getElementById(this.containerId);
  if (!el) return null;
  this.map = L.map(el, {
    zoomControl: true,
    attributionControl: true,
    maxZoom: 22,
  });
  if (!this.map.getPane('ddaRaster')) {
    const pane = this.map.createPane('ddaRaster');
    pane.style.zIndex = 450;
    pane.style.pointerEvents = 'none';
  }
  this.map.setView([28.6139, 77.209], 12);
  this.map.on('zoomend', () => this._syncRasterVisibility());
  this.applyBasemap(this.basemapId, this.customXyz);
  return this.map;
};

DdaMapViewer.prototype.applyBasemap = function applyBasemap(id, customUrl) {
  const map = this.ensureMap();
  if (!map) return;
  const preset = DDA_BASEMAPS.find((b) => b.id === id) || DDA_BASEMAPS[0];
  this.basemapId = preset.id;
  if (preset.id === 'custom-xyz') {
    this.customXyz = (customUrl || this.customXyz || '').trim();
    localStorage.setItem(DDA_MAP_LS_XYZ, this.customXyz);
  }
  localStorage.setItem(DDA_MAP_LS_BASE, this.basemapId);

  if (this.basemapLayer) {
    map.removeLayer(this.basemapLayer);
    this.basemapLayer = null;
  }

  let url = preset.url;
  if (preset.id === 'custom-xyz') url = this.customXyz;
  if (!url) return;

  const opts = {
    maxZoom: preset.maxZoom || 22,
    attribution: preset.attribution || '',
    updateWhenIdle: true,
  };
  if (preset.subdomains) opts.subdomains = preset.subdomains.split('');
  this.basemapLayer = L.tileLayer(url, opts);
  this.basemapLayer.addTo(map);
};

DdaMapViewer.prototype.setLayerVisible = function setLayerVisible(key, on) {
  this.layerEnabled[key] = !!on;
  this._syncRasterVisibility();
};

DdaMapViewer.prototype.setOverlayOn = function setOverlayOn(on) {
  this.overlayOn = !!on;
  this._syncRasterVisibility();
};

DdaMapViewer.prototype.setGeoJsonVisible = function setGeoJsonVisible(on) {
  const map = this.map;
  if (!map || !this.geojsonLayer) return;
  if (on) {
    if (!map.hasLayer(this.geojsonLayer)) this.geojsonLayer.addTo(map);
  } else if (map.hasLayer(this.geojsonLayer)) {
    map.removeLayer(this.geojsonLayer);
  }
};

DdaMapViewer.prototype.setOpacity = function setOpacity(value) {
  this.opacity = Math.max(0, Math.min(1, Number(value)));
  localStorage.setItem(DDA_MAP_LS_OPACITY, String(this.opacity));
  const apply = (layer) => {
    if (!layer) return;
    if (typeof layer.setOpacity === 'function') layer.setOpacity(this.opacity);
    if (typeof layer.eachLayer === 'function') layer.eachLayer(apply);
  };
  Object.values(this.rasterLayers).forEach(apply);
};

DdaMapViewer.prototype.clearRasters = function clearRasters() {
  const map = this.map;
  Object.values(this.rasterParts || {}).forEach((parts) => {
    if (!parts || !map) return;
    if (parts.overview && map.hasLayer(parts.overview)) map.removeLayer(parts.overview);
    if (parts.tiles && map.hasLayer(parts.tiles)) map.removeLayer(parts.tiles);
  });
  this.rasterLayers = {};
  this.layerEnabled = {};
  this.rasterParts = {};
};

DdaMapViewer.prototype.clearGeoJson = function clearGeoJson() {
  if (this.map && this.geojsonLayer) {
    this.map.removeLayer(this.geojsonLayer);
  }
  this.geojsonLayer = null;
};

DdaMapViewer.prototype._syncRasterVisibility = function _syncRasterVisibility() {
  const map = this.map;
  if (!map) return;
  const zoom = map.getZoom();
  Object.values(this.rasterParts || {}).forEach((parts) => {
    if (!parts) return;
    const parentOn = this.overlayOn && this.layerEnabled[parts.key] !== false;
    // Keep the mercator overview on at every zoom. Hiding it at z>=16
    // (and relying on XYZ tiles) made the overlay vanish: Leaflet often
    // never requested interior tiles, or those tiles sat under Google.
    if (parts.overview) {
      if (parentOn && !map.hasLayer(parts.overview)) parts.overview.addTo(map);
      if (!parentOn && map.hasLayer(parts.overview)) map.removeLayer(parts.overview);
    }
    if (parts.tiles) {
      const showTiles = parentOn && (zoom >= 16 || !parts.overview || parts.overviewFailed);
      if (showTiles && !map.hasLayer(parts.tiles)) parts.tiles.addTo(map);
      if (!showTiles && map.hasLayer(parts.tiles)) map.removeLayer(parts.tiles);
    }
    if (parentOn && parts.overview && map.hasLayer(parts.overview)) {
      try { parts.overview.bringToFront(); } catch (_) { /* ignore */ }
    }
    if (parentOn && parts.tiles && map.hasLayer(parts.tiles)) {
      try { parts.tiles.bringToFront(); } catch (_) { /* ignore */ }
    }
  });
};

DdaMapViewer.prototype._addRasterFromInfo = function _addRasterFromInfo(key, info) {
  const map = this.ensureMap();
  if (!map || !info || !info.hasGeoref) return null;
  const latLng = ddaBoundsToLatLng(info.bounds);
  if (!latLng) return null;
  const llBounds = L.latLngBounds(latLng);
  const group = L.layerGroup();
  const parts = { key, overview: null, tiles: null, overviewFailed: false };

  if (info.canTile && info.overviewUrl) {
    // Already warped to Web Mercator — same grid as Google/OSM tiles.
    parts.overview = L.imageOverlay(info.overviewUrl, llBounds, {
      opacity: this.opacity,
      pane: 'ddaRaster',
      interactive: false,
      className: 'dda-raster-overview',
    });
    parts.overview.on('load', () => {
      try { parts.overview._reset(); } catch (_) { /* ignore */ }
    });
    parts.overview.on('error', () => {
      parts.overviewFailed = true;
      this._syncRasterVisibility();
    });
    group.addLayer(parts.overview);
  } else if (!info.canTile && info.previewUrl) {
    parts.overview = L.imageOverlay(info.previewUrl, llBounds, {
      opacity: this.opacity,
      pane: 'ddaRaster',
      interactive: false,
    });
    parts.overview.on('load', () => {
      try { parts.overview._reset(); } catch (_) { /* ignore */ }
    });
    group.addLayer(parts.overview);
  }

  if (info.canTile && info.tileUrl) {
    // Dedicated pane above Google — same EPSG:3857 grid, not the same DOM pane
    // (sharing tilePane let opaque satellite tiles cover empty/late overlay tiles).
    parts.tiles = L.tileLayer(info.tileUrl, {
      opacity: this.opacity,
      pane: 'ddaRaster',
      maxZoom: 22,
      maxNativeZoom: info.maxNativeZoom || 20,
      tms: false,
      keepBuffer: 6,
      updateWhenIdle: false,
      updateWhenZooming: true,
      detectRetina: false,
      className: 'dda-raster-tiles',
    });
    group.addLayer(parts.tiles);
  }

  if (!group.getLayers().length) return null;
  this.rasterLayers[key] = group;
  this.rasterParts[key] = parts;
  if (this.layerEnabled[key] === undefined) this.layerEnabled[key] = true;
  this._syncRasterVisibility();
  return latLng;
};

DdaMapViewer.prototype.loadRaster = async function loadRaster(path) {
  this.clearRasters();
  this.clearGeoJson();
  if (!path) return { ok: false, reason: 'no-path' };
  const res = await fetch(`/api/dda/local/map-info?path=${encodeURIComponent(path)}`, { credentials: 'include' });
  if (!res.ok) {
    const detail = await res.text();
    throw new Error(detail || res.statusText);
  }
  const info = await res.json();
  if (!info.hasGeoref) return { ok: false, reason: 'no-georef', info };
  const latLng = this._addRasterFromInfo('tif', info);
  this.lastFitBounds = latLng;
  this.fit(latLng);
  this.invalidate();
  return { ok: true, info };
};

DdaMapViewer.prototype.loadRasters = async function loadRasters(entries) {
  this.clearRasters();
  const allBounds = [];
  for (const entry of entries || []) {
    if (!entry?.path) continue;
    try {
      const res = await fetch(`/api/dda/local/map-info?path=${encodeURIComponent(entry.path)}`, { credentials: 'include' });
      if (!res.ok) continue;
      const info = await res.json();
      const latLng = this._addRasterFromInfo(entry.id || entry.path, info);
      if (latLng) allBounds.push(latLng);
    } catch (err) {
      console.warn('Map overlay failed for', entry.path, err);
    }
  }
  const merged = this._mergeLatLngBounds(allBounds);
  this.lastFitBounds = merged;
  if (merged) this.fit(merged);
  this.invalidate();
  return { ok: allBounds.length > 0, bounds: merged };
};

DdaMapViewer.prototype._mergeLatLngBounds = function _mergeLatLngBounds(list) {
  if (!list.length) return null;
  let south = Infinity;
  let west = Infinity;
  let north = -Infinity;
  let east = -Infinity;
  list.forEach((b) => {
    south = Math.min(south, b[0][0]);
    west = Math.min(west, b[0][1]);
    north = Math.max(north, b[1][0]);
    east = Math.max(east, b[1][1]);
  });
  return [[south, west], [north, east]];
};

DdaMapViewer.prototype.setGeoJson = function setGeoJson(geojson) {
  const map = this.ensureMap();
  if (!map) return;
  this.clearGeoJson();
  if (!geojson || !geojson.features || !geojson.features.length) return;
  this.geojsonLayer = L.geoJSON(geojson, {
    style: () => ({
      color: '#cf2040',
      weight: 2,
      fillColor: '#cf2040',
      fillOpacity: 0.25,
    }),
    pointToLayer: (_f, latlng) => L.circleMarker(latlng, {
      radius: 6,
      color: '#cf2040',
      weight: 2,
      fillColor: '#cf2040',
      fillOpacity: 0.6,
    }),
    onEachFeature: (feature, layer) => {
      const p = feature.properties || {};
      const title = p.changeType || p.ddaType || p.id || 'Change';
      layer.bindPopup(`<strong>${title}</strong>`);
    },
  });
  if (this.overlayOn) this.geojsonLayer.addTo(map);
};

DdaMapViewer.prototype.fit = function fit(latLngBounds) {
  const map = this.ensureMap();
  if (!map || !latLngBounds) return;
  this.lastFitBounds = latLngBounds;
  const run = () => {
    try {
      map.invalidateSize();
      map.fitBounds(latLngBounds, { padding: [28, 28], maxZoom: 18 });
      this._syncRasterVisibility();
    } catch (_) { /* ignore invalid bounds */ }
  };
  run();
  setTimeout(run, 120);
  setTimeout(run, 400);
};

DdaMapViewer.prototype.locate = function locate(lat, lng, zoom) {
  const map = this.ensureMap();
  if (!map || lat == null || lng == null) return;
  map.setView([lat, lng], zoom || Math.max(map.getZoom(), 18));
};

DdaMapViewer.prototype.invalidate = function invalidate() {
  const map = this.map;
  if (!map) return;
  const refit = () => {
    map.invalidateSize();
    if (this.lastFitBounds) {
      try { map.fitBounds(this.lastFitBounds, { padding: [28, 28], maxZoom: 18 }); } catch (_) { /* ignore */ }
    }
    this._syncRasterVisibility();
  };
  setTimeout(refit, 80);
  setTimeout(() => {
    map.invalidateSize();
    this._syncRasterVisibility();
  }, 320);
};

DdaMapViewer.prototype.destroy = function destroy() {
  this.clearRasters();
  this.clearGeoJson();
  if (this.map) {
    this.map.remove();
    this.map = null;
    this.basemapLayer = null;
  }
};

function bindDdaMapToolbar(viewer, ids) {
  const basemap = document.getElementById(ids.basemap);
  const xyz = document.getElementById(ids.xyz);
  const overlay = document.getElementById(ids.overlay);
  const opacity = document.getElementById(ids.opacity);
  const opacityVal = document.getElementById(ids.opacityVal);
  const fit = document.getElementById(ids.fit);

  if (basemap && !basemap.dataset.ddaBound) {
    basemap.dataset.ddaBound = '1';
    if (!basemap.options.length) {
      DDA_BASEMAPS.forEach((b) => {
        const opt = document.createElement('option');
        opt.value = b.id;
        opt.textContent = b.label;
        basemap.appendChild(opt);
      });
    }
    basemap.value = viewer.basemapId;
    const toggleXyz = () => {
      if (xyz) xyz.classList.toggle('hidden', basemap.value !== 'custom-xyz');
    };
    toggleXyz();
    basemap.addEventListener('change', () => {
      viewer.applyBasemap(basemap.value, xyz?.value);
      toggleXyz();
    });
  } else if (basemap) {
    basemap.value = viewer.basemapId;
  }
  if (xyz && !xyz.dataset.ddaBound) {
    xyz.dataset.ddaBound = '1';
    xyz.value = viewer.customXyz;
    xyz.addEventListener('change', () => {
      viewer.applyBasemap('custom-xyz', xyz.value);
    });
  }
  if (overlay && !overlay.dataset.ddaBound) {
    overlay.dataset.ddaBound = '1';
    overlay.checked = viewer.overlayOn;
    overlay.addEventListener('change', () => viewer.setOverlayOn(overlay.checked));
  }
  if (opacity && !opacity.dataset.ddaBound) {
    opacity.dataset.ddaBound = '1';
    opacity.value = String(Math.round(viewer.opacity * 100));
    const syncLabel = () => {
      if (opacityVal) opacityVal.textContent = `${opacity.value}%`;
    };
    syncLabel();
    opacity.addEventListener('input', () => {
      viewer.setOpacity(Number(opacity.value) / 100);
      syncLabel();
    });
  }
  if (fit && !fit.dataset.ddaBound) {
    fit.dataset.ddaBound = '1';
    fit.addEventListener('click', () => {
      if (viewer.lastFitBounds) viewer.fit(viewer.lastFitBounds);
      else if (viewer.geojsonLayer) viewer.fit(viewer.geojsonLayer.getBounds());
    });
  }
}

window.DdaMapViewer = DdaMapViewer;
window.DDA_BASEMAPS = DDA_BASEMAPS;
window.bindDdaMapToolbar = bindDdaMapToolbar;
window.ddaBoundsToLatLng = ddaBoundsToLatLng;
window.regionsToGeoJson = function regionsToGeoJson(regions) {
  const features = [];
  (regions || []).forEach((r) => {
    const ring = r.polygonGeo;
    if (ring && ring.length >= 3) {
      const coords = ring
        .filter((p) => p && p.lat != null && p.lng != null)
        .map((p) => [p.lng, p.lat]);
      if (coords.length >= 3) {
        if (coords[0][0] !== coords[coords.length - 1][0] || coords[0][1] !== coords[coords.length - 1][1]) {
          coords.push(coords[0]);
        }
        features.push({
          type: 'Feature',
          properties: {
            id: r.id,
            changeType: r.changeType || r.change_type,
            ddaType: r.ddaType,
          },
          geometry: { type: 'Polygon', coordinates: [coords] },
        });
        return;
      }
    }
    const ll = r.latLng;
    if (ll && ll.lat != null && ll.lng != null) {
      features.push({
        type: 'Feature',
        properties: { id: r.id, changeType: r.changeType || r.change_type, ddaType: r.ddaType },
        geometry: { type: 'Point', coordinates: [ll.lng, ll.lat] },
      });
    }
  });
  return { type: 'FeatureCollection', features };
};

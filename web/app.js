const KALBAR_BBOX = [110.05, -0.81, 110.11, -0.75];
const RESULT_ORDER = ["confirmed_conversion", "still_forest", "old_plantation", "inconclusive"];
const RESULT_NAME = {
  confirmed_conversion: "Confirmed conversion",
  still_forest: "Still forest",
  old_plantation: "Old plantation",
  inconclusive: "Inconclusive",
  pending: "Pending"
};
const QUADS = ["NW", "NE", "SW", "SE"];
const FALLBACK_RINGS = [
  [[110.05, -0.75], [110.08, -0.75], [110.08, -0.78], [110.05, -0.78], [110.05, -0.75]],
  [[110.08, -0.75], [110.11, -0.75], [110.11, -0.78], [110.08, -0.78], [110.08, -0.75]],
  [[110.05, -0.78], [110.08, -0.78], [110.08, -0.81], [110.05, -0.81], [110.05, -0.78]],
  [[110.08, -0.78], [110.11, -0.78], [110.11, -0.81], [110.08, -0.81], [110.08, -0.78]]
];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const COLORS = {
  confirmed_conversion: "#c0392b",
  still_forest: "#1e8449",
  old_plantation: "#b7950b",
  inconclusive: "#7f8c8d"
};

const statusEl = document.getElementById("status");
const findingEl = document.getElementById("finding");
const viewSubmission = document.getElementById("view-submission");
const viewUnknown = document.getElementById("view-unknown");
const chipEmpty = document.getElementById("chip-empty");
const chipReadout = document.getElementById("chip-readout");
const sceneDate = document.getElementById("scene-date");
const timeInput = document.getElementById("time");
const flyNote = document.getElementById("fly-note");
const analyzeNote = document.getElementById("analyze-note");
const intakeNote = document.getElementById("intake-note");
const intakeFileName = document.getElementById("intake-file-name");
const intakeFeatureBox = document.getElementById("intake-features");
const intakeFeatureList = document.getElementById("intake-feature-list");
const intakeSelected = document.getElementById("intake-selected");

const state = {
  plotUrl: "",
  bbox: KALBAR_BBOX.slice(),
  scenes: [],
  chips: [],
  analysisLoaded: false,
  showingSubmission: false,
  selectedId: null,
  tool: "box", // pan | box | shape
  drawMode: true,
  drawing: false,
  dragStart: null,
  shapePoints: [],
  draftBbox: null,
  draftGeometry: null,
  draftFeature: null,
  intakeMethod: "draw",
  intakeFilename: null,
  intakeItems: [],
  intakeSelectedId: null,
  confidence: null,
  suppressClick: false,
  mapReady: false
};

function formatDate(iso) {
  const parts = String(iso || "").split("-");
  if (parts.length !== 3) return iso || "";
  const day = Number(parts[2]);
  const month = MONTHS[Number(parts[1]) - 1];
  if (!month || !Number.isFinite(day)) return iso;
  return day + " " + month + " " + parts[0];
}

function imageCoordinates(bbox) {
  const minLon = bbox[0];
  const minLat = bbox[1];
  const maxLon = bbox[2];
  const maxLat = bbox[3];
  return [
    [minLon, maxLat],
    [maxLon, maxLat],
    [maxLon, minLat],
    [minLon, minLat]
  ];
}

function fitPadding() {
  return { top: 48, bottom: 110, left: 390, right: 48 };
}

function isPair(value) {
  return Array.isArray(value) && value.length >= 2 && typeof value[0] === "number" && typeof value[1] === "number";
}

function isRing(value) {
  return Array.isArray(value) && value.length >= 4 && isPair(value[0]);
}

function isPolygon(value) {
  return Array.isArray(value) && isRing(value[0]);
}

function closeRing(ring) {
  const first = ring[0];
  const last = ring[ring.length - 1];
  if (first[0] !== last[0] || first[1] !== last[1]) ring = ring.concat([first.slice()]);
  return ring;
}

function extractRing(record) {
  if (!record || typeof record !== "object") return null;
  const props = record.properties || {};
  const geom = record.geometry;
  if (geom && geom.type === "Polygon" && isPolygon(geom.coordinates)) return closeRing(geom.coordinates[0]);
  if (geom && geom.type === "MultiPolygon" && Array.isArray(geom.coordinates) && isPolygon(geom.coordinates[0])) {
    return closeRing(geom.coordinates[0][0]);
  }
  const candidates = [
    record.coordinates,
    record.polygon,
    record.ring,
    props.coordinates,
    props.polygon,
    props.ring,
    geom && geom.coordinates
  ];
  for (let i = 0; i < candidates.length; i += 1) {
    const coords = candidates[i];
    if (isRing(coords)) return closeRing(coords);
    if (isPolygon(coords)) return closeRing(coords[0]);
    if (Array.isArray(coords) && isPolygon(coords[0])) return closeRing(coords[0][0]);
  }
  return null;
}

function asRecords(data) {
  if (!data) return [];
  if (Array.isArray(data)) return data;
  if (data.type === "FeatureCollection" && Array.isArray(data.features)) return data.features;
  const wrapped = ["chips", "tiles", "stats", "items", "data", "results"];
  for (let i = 0; i < wrapped.length; i += 1) {
    const value = data[wrapped[i]];
    if (Array.isArray(value)) return value;
    if (value && value.type === "FeatureCollection" && Array.isArray(value.features)) return value.features;
  }
  if (typeof data === "object") {
    const keyed = Object.keys(data).filter(function (key) {
      return /^\d+$/.test(key) && data[key] && typeof data[key] === "object";
    });
    if (keyed.length) {
      return keyed.map(function (key) {
        return Object.assign({ id: Number(key) }, data[key]);
      });
    }
  }
  return [];
}

function recordId(record, index) {
  if (!record || typeof record !== "object") return index;
  const props = record.properties || {};
  const raw = record.id ?? record.chip_id ?? record.tile_id ?? props.id ?? props.chip_id ?? props.tile_id;
  const id = Number(raw);
  return Number.isInteger(id) ? id : index;
}

function indexRecords(data) {
  const map = new Map();
  asRecords(data).forEach(function (record, index) {
    const id = recordId(record, index);
    if (id >= 0 && id <= 3) map.set(id, record);
  });
  return map;
}

function extractLabel(record) {
  if (!record || typeof record !== "object") return null;
  const bags = [record, record.properties || {}];
  const keys = ["label", "perceptron_label", "perceptron", "class", "prediction", "class_name"];
  for (let b = 0; b < bags.length; b += 1) {
    const bag = bags[b];
    for (let k = 0; k < keys.length; k += 1) {
      const value = bag[keys[k]];
      if (typeof value === "string" && value.trim()) {
        return value.trim().toLowerCase().replace(/\s+/g, "_");
      }
      if (value && typeof value === "object" && typeof value.label === "string" && value.label.trim()) {
        return value.label.trim().toLowerCase().replace(/\s+/g, "_");
      }
    }
  }
  return null;
}

function readShare(records, keys) {
  for (let r = 0; r < records.length; r += 1) {
    const record = records[r];
    if (!record) continue;
    const bags = [record, record.properties || {}, record.stats || {}];
    for (let b = 0; b < bags.length; b += 1) {
      const bag = bags[b];
      for (let k = 0; k < keys.length; k += 1) {
        if (!Object.prototype.hasOwnProperty.call(bag, keys[k])) continue;
        const value = bag[keys[k]];
        if (typeof value === "number" && Number.isFinite(value)) return value;
        if (value && typeof value === "object") {
          const nested = value.loss_share ?? value.forest_share ?? value.share;
          if (typeof nested === "number" && Number.isFinite(nested)) return nested;
        }
      }
    }
  }
  return null;
}

function classify(label, jrc, hansen) {
  if (!label) return "pending";
  const jrcNum = typeof jrc === "number" && Number.isFinite(jrc);
  const hansenNum = typeof hansen === "number" && Number.isFinite(hansen);
  if (label === "converted_to_plantation" && jrcNum && jrc >= 0.5 && hansenNum && hansen >= 0.25) {
    return "confirmed_conversion";
  }
  if (label === "converted_to_plantation" && !jrcNum && hansenNum && hansen >= 0.25) {
    return "confirmed_conversion";
  }
  if (label === "converted_to_plantation" && jrcNum && jrc < 0.5) return "old_plantation";
  if (label === "forest_unchanged" && hansenNum && hansen < 0.25) return "still_forest";
  return "inconclusive";
}

function buildChips(statsData, perceptionData) {
  const stats = indexRecords(statsData);
  const perception = indexRecords(perceptionData);
  const chips = [];
  for (let id = 0; id < 4; id += 1) {
    const stat = stats.get(id) || null;
    const perc = perception.get(id) || null;
    const ring = (stat && extractRing(stat)) || FALLBACK_RINGS[id];
    const label = extractLabel(perc) || extractLabel(stat);
    const hansen = readShare([stat, perc], ["hansen_loss_share", "loss_share", "post_cutoff_loss_share"]);
    const jrc = readShare([stat, perc], ["jrc_forest_share", "forest_share"]);
    chips.push({
      id: id,
      quad: QUADS[id],
      ring: ring,
      perceptron: label,
      hansen: hansen,
      jrc: jrc,
      result: classify(label, jrc, hansen)
    });
  }
  return chips;
}

function iou(a, b) {
  const left = Math.max(a[0], b[0]);
  const bottom = Math.max(a[1], b[1]);
  const right = Math.min(a[2], b[2]);
  const top = Math.min(a[3], b[3]);
  const width = Math.max(0, right - left);
  const height = Math.max(0, top - bottom);
  const intersection = width * height;
  const areaA = Math.max(0, a[2] - a[0]) * Math.max(0, a[3] - a[1]);
  const areaB = Math.max(0, b[2] - b[0]) * Math.max(0, b[3] - b[1]);
  const union = areaA + areaB - intersection;
  return union > 0 ? intersection / union : 0;
}

function formatShare(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) return "not available";
  return String(Math.round(value * 10000) / 10000);
}

function countResult(result) {
  return state.chips.filter(function (chip) { return chip.result === result; }).length;
}

function renderFinding() {
  findingEl.replaceChildren();
  if (state.confidence && typeof state.confidence.mean_score === "number") {
    const mean = state.confidence.mean_score;
    const verdict = document.createElement("p");
    verdict.className = "verdict";
    verdict.textContent = mean >= 0.45
      ? "This plot shows post-2020 tree-cover loss consistent with plantation conversion."
      : "The windows do not show a clear plantation conversion.";
    const disclaimer = document.createElement("p");
    disclaimer.className = "disclaimer";
    disclaimer.textContent = state.confidence.maps_only
      ? "Green is forest in 2020. Red is that forest lost after 2020. Perceptron is off. Not a compliance finding."
      : state.confidence.perceptron_only
      ? "Perceptron only. The 2020 forest map and the loss map are off, so you can compare the color with the photo."
      : state.confidence.gated
      ? "Not a compliance finding. Color appears where 2020 forest, post-2020 tree loss, and Perceptron agree."
      : "Not a compliance finding. Color is Perceptron's conversion confidence, from green to red.";
    const loss = document.createElement("p");
    loss.className = "loss-note";
    if (state.confidence.maps_only) {
      const layers = state.confidence.layers || [];
      const hasForest = layers.indexOf("forest") !== -1;
      const hasLoss = layers.indexOf("loss") !== -1;
      const share = typeof state.confidence.both_share === "number" ? state.confidence.both_share : mean;
      if (hasForest && hasLoss) {
        verdict.textContent = share >= 0.05
          ? "Part of this rectangle was forest in 2020 and lost trees after the cutoff."
          : "These two maps do not show much post-2020 loss on 2020 forest.";
        disclaimer.textContent = "Green is forest in 2020. Red is that forest lost after 2020. Perceptron is off. Not a compliance finding.";
        loss.textContent = Math.round(share * 1000) / 10 + "% of the rectangle is red.";
      } else if (hasLoss) {
        verdict.textContent = "Red marks tree loss after 2020.";
        disclaimer.textContent = "The 2020 forest map and Perceptron are off. Not a compliance finding.";
        loss.textContent = Math.round(mean * 1000) / 10 + "% of the rectangle is red.";
      } else {
        verdict.textContent = "Green marks forest in 2020.";
        disclaimer.textContent = "The loss map and Perceptron are off. Not a compliance finding.";
        loss.textContent = Math.round(mean * 1000) / 10 + "% of the rectangle is green.";
      }
    } else {
      loss.textContent = state.confidence.gated
        ? "Mean confidence " + mean.toFixed(2) + " where trees were lost after 2020."
        : "Mean confidence " + mean.toFixed(2) + " across the tiles.";
    }
    findingEl.append(verdict, disclaimer, loss);
    return;
  }
  if (!state.analysisLoaded) return;
  if (countResult("confirmed_conversion") > 0) {
    const verdict = document.createElement("p");
    verdict.className = "verdict";
    verdict.textContent = "This plot shows post-2020 tree-cover loss consistent with plantation conversion.";
    const disclaimer = document.createElement("p");
    disclaimer.className = "disclaimer";
    disclaimer.textContent = "Not a compliance finding.";
    const loss = document.createElement("p");
    loss.className = "loss-note";
    loss.textContent = "About 20% of this box was lost in 2023. The loss map ends in 2024.";
    findingEl.append(verdict, disclaimer, loss);
    return;
  }
  const verdict = document.createElement("p");
  verdict.className = "verdict";
  verdict.textContent = "The chips do not support a conversion finding.";
  const list = document.createElement("ul");
  list.className = "counts";
  RESULT_ORDER.forEach(function (result) {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = RESULT_NAME[result];
    const count = document.createElement("b");
    count.textContent = String(countResult(result));
    item.append(name, count);
    list.append(item);
  });
  findingEl.append(verdict, list);
}

function renderSelection() {
  const chip = state.chips.find(function (item) { return item.id === state.selectedId; });
  if (!chip || !state.showingSubmission) {
    chipEmpty.hidden = false;
    chipReadout.hidden = true;
    return;
  }
  chipEmpty.hidden = true;
  chipReadout.hidden = false;
  document.getElementById("chip-id").textContent = chip.id + " · " + chip.quad;
  document.getElementById("chip-label").textContent = chip.perceptron || "pending";
  document.getElementById("chip-hansen").textContent = formatShare(chip.hansen);
  document.getElementById("chip-jrc").textContent = formatShare(chip.jrc);
  document.getElementById("chip-result").textContent = RESULT_NAME[chip.result] || chip.result;
}

function renderPanel() {
  viewSubmission.hidden = !state.showingSubmission;
  viewUnknown.hidden = state.showingSubmission;
  if (!state.showingSubmission) return;
  statusEl.hidden = state.analysisLoaded;
  statusEl.textContent = state.analysisLoaded ? "" : "Chip analysis is still running.";
  renderFinding();
  renderSelection();
}

function scenesFrom(evidence) {
  if (evidence && Array.isArray(evidence.scenes) && evidence.scenes.length) return evidence.scenes;
  const scenes = [];
  if (evidence && evidence.before && evidence.before.date) scenes.push({ date: evidence.before.date, file: "before.png" });
  if (evidence && evidence.after && evidence.after.date) scenes.push({ date: evidence.after.date, file: "after.png" });
  return scenes;
}

function ensureScene(url, coords) {
  if (map.getSource("scene")) return;
  const before = map.getLayer("chips-fill") ? "chips-fill" : undefined;
  map.addSource("scene", { type: "image", url: url, coordinates: coords });
  map.addLayer({ id: "scene", type: "raster", source: "scene" }, before);
}

function setTime(index) {
  const scenes = state.scenes;
  if (!scenes.length) return;
  const next = Math.max(0, Math.min(scenes.length - 1, Number(index) || 0));
  timeInput.max = String(scenes.length - 1);
  timeInput.value = String(next);
  sceneDate.textContent = formatDate(scenes[next].date);
  if (!state.mapReady || !state.showingSubmission) return;
  const source = map.getSource("scene");
  const coords = imageCoordinates(state.bbox);
  const url = state.plotUrl + "/" + scenes[next].file;
  if (!source) {
    ensureScene(url, coords);
    return;
  }
  source.updateImage({ url: url, coordinates: coords });
}

function chipCollection() {
  return {
    type: "FeatureCollection",
    features: state.chips.map(function (chip) {
      return {
        type: "Feature",
        id: chip.id,
        properties: { result: chip.result },
        geometry: { type: "Polygon", coordinates: [chip.ring] }
      };
    })
  };
}

function overlayOpacity() {
  const input = document.getElementById("overlay");
  if (!input) return 0.55;
  const value = Number(input.value);
  return Number.isFinite(value) ? value / 100 : 0.55;
}

function applyOverlayOpacity() {
  if (!map || !map.getLayer("confidence")) return;
  map.setPaintProperty("confidence", "raster-opacity", overlayOpacity());
}

function showConfidence() {
  if (!state.mapReady || !state.confidence || !state.showingSubmission) return;
  const coords = imageCoordinates(state.bbox);
  const url = state.plotUrl + "/confidence.png";
  if (!map.getSource("confidence")) {
    map.addSource("confidence", { type: "image", url: url, coordinates: coords });
    map.addLayer({
      id: "confidence",
      type: "raster",
      source: "confidence",
      paint: { "raster-opacity": overlayOpacity() }
    });
  } else {
    map.getSource("confidence").updateImage({ url: url, coordinates: coords });
  }
  map.setLayoutProperty("confidence", "visibility", "visible");
  applyOverlayOpacity();
  if (map.getLayer("chips-fill")) {
    map.setLayoutProperty("chips-fill", "visibility", "none");
    map.setLayoutProperty("chips-line", "visibility", "none");
  }
}

function applyChips() {
  if (!state.mapReady) return;
  map.getSource("chips").setData(state.showingSubmission && !state.confidence ? chipCollection() : emptyCollection());
  if (state.showingSubmission && state.selectedId !== null) {
    map.setFeatureState({ source: "chips", id: state.selectedId }, { selected: true });
  }
}

function emptyCollection() {
  return { type: "FeatureCollection", features: [] };
}

function setDraft(feature) {
  if (!state.mapReady || !map.getSource("draft")) return;
  map.getSource("draft").setData(feature ? { type: "FeatureCollection", features: [feature] } : emptyCollection());
}

function setSketch(data) {
  if (!state.mapReady || !map.getSource("sketch")) return;
  map.getSource("sketch").setData(data || emptyCollection());
}

function clearIntakeList() {
  state.intakeItems = [];
  state.intakeSelectedId = null;
  state.intakeFilename = null;
  if (intakeFeatureBox) intakeFeatureBox.hidden = true;
  if (intakeFileName) intakeFileName.textContent = "";
  updateIntakeSelected();
}

function distanceMeters(a, b) {
  const toRad = Math.PI / 180;
  const dLat = (b.lat - a.lat) * toRad;
  const dLon = (b.lng - a.lng) * toRad;
  const lat1 = a.lat * toRad;
  const lat2 = b.lat * toRad;
  const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
  return 6371000 * 2 * Math.asin(Math.sqrt(h));
}

function simplifyPoints(points, minMeters) {
  if (points.length < 3) return points.slice();
  const out = [points[0]];
  for (let i = 1; i < points.length; i += 1) {
    if (distanceMeters(out[out.length - 1], points[i]) >= minMeters) out.push(points[i]);
  }
  if (out.length < 2) out.push(points[points.length - 1]);
  else if (distanceMeters(out[out.length - 1], points[points.length - 1]) > 1) {
    out.push(points[points.length - 1]);
  }
  return out;
}

function polygonFromPoints(points) {
  const cleaned = simplifyPoints(points, 12);
  if (cleaned.length < 3) return null;
  const ring = cleaned.map(function (pt) { return [pt.lng, pt.lat]; });
  const first = ring[0];
  const last = ring[ring.length - 1];
  if (first[0] !== last[0] || first[1] !== last[1]) ring.push(first.slice());
  if (ring.length < 4) return null;
  const bbox = window.ClearcutIntake.ringBBox(ring);
  if (!bbox) return null;
  const feature = {
    type: "Feature",
    properties: { name: "Drawn shape" },
    geometry: { type: "Polygon", coordinates: [ring] }
  };
  return { feature: feature, bbox: bbox };
}

function sketchCollection(points, closed) {
  if (!points.length) return emptyCollection();
  const line = points.map(function (pt) { return [pt.lng, pt.lat]; });
  const features = [{
    type: "Feature",
    properties: {},
    geometry: { type: "LineString", coordinates: closed && line.length ? line.concat([line[0]]) : line }
  }];
  points.forEach(function (pt) {
    features.push({
      type: "Feature",
      properties: {},
      geometry: { type: "Point", coordinates: [pt.lng, pt.lat] }
    });
  });
  return { type: "FeatureCollection", features: features };
}

function displaySubmission() {
  state.showingSubmission = true;
  state.selectedId = null;
  if (state.mapReady) {
    setDraft(null);
    setTime(timeInput.value);
    if (map.getLayer("scene")) map.setLayoutProperty("scene", "visibility", "visible");
    applyChips();
    showConfidence();
  }
  renderPanel();
}

function showSubmission(animate) {
  displaySubmission();
  if (!state.mapReady) return;
  const width = map.getContainer().clientWidth || 0;
  const height = map.getContainer().clientHeight || 0;
  const padding = fitPadding();
  if (padding.left + padding.right > width - 40) {
    padding.left = Math.max(16, Math.floor(width * 0.34));
    padding.right = 16;
  }
  if (padding.top + padding.bottom > height - 40) {
    padding.top = 16;
    padding.bottom = 16;
  }
  map.fitBounds(
    [[state.bbox[0], state.bbox[1]], [state.bbox[2], state.bbox[3]]],
    { padding: padding, maxZoom: 13, duration: animate ? 900 : 0 }
  );
}

function showUnknown(feature, bbox, options) {
  const opts = options || {};
  state.showingSubmission = false;
  state.selectedId = null;
  state.draftBbox = bbox;
  state.draftFeature = feature || null;
  state.draftGeometry = feature && feature.geometry ? feature.geometry : null;
  state.intakeMethod = opts.inputMethod || "draw";
  if (opts.filename) state.intakeFilename = opts.filename;
  setDraft(feature);
  if (state.mapReady && map.getLayer("scene")) {
    map.setLayoutProperty("scene", "visibility", "none");
  }
  applyChips();
  renderPanel();
  analyzeNote.textContent = "";
  updateIntakeSelected();
  if (bbox && map) {
    map.fitBounds(
      [[bbox[0], bbox[1]], [bbox[2], bbox[3]]],
      { padding: fitPadding(), maxZoom: 13, duration: 700 }
    );
  }
}

function updateIntakeSelected() {
  if (!intakeSelected) return;
  const item = state.intakeItems.find(function (row) { return row.id === state.intakeSelectedId; });
  if (!item) {
    intakeSelected.hidden = true;
    return;
  }
  intakeSelected.hidden = false;
  document.getElementById("intake-selected-name").textContent = item.label;
  document.getElementById("intake-selected-source").textContent =
    (state.intakeFilename || state.intakeMethod || "upload") + " · " + state.intakeMethod;
}

function renderIntakeFeatures(items) {
  state.intakeItems = items || [];
  if (!intakeFeatureBox || !intakeFeatureList) return;
  intakeFeatureList.replaceChildren();
  if (!state.intakeItems.length) {
    intakeFeatureBox.hidden = true;
    state.intakeSelectedId = null;
    updateIntakeSelected();
    return;
  }
  intakeFeatureBox.hidden = false;
  state.intakeItems.forEach(function (item) {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = item.label;
    button.className = item.id === state.intakeSelectedId ? "active" : "";
    button.addEventListener("click", function () { selectIntakeItem(item.id); });
    li.append(button);
    intakeFeatureList.append(li);
  });
  if (state.intakeSelectedId == null) selectIntakeItem(state.intakeItems[0].id);
  else updateIntakeSelected();
}

function selectIntakeItem(id) {
  const item = state.intakeItems.find(function (row) { return row.id === id; });
  if (!item) return;
  const error = window.ClearcutIntake.validateBBox(item.bbox);
  if (error) {
    intakeNote.textContent = error;
    return;
  }
  state.intakeSelectedId = id;
  intakeNote.textContent = "Selected “" + item.label + "”. Analyze when ready.";
  renderIntakeFeatures(state.intakeItems);
  showUnknown(item.feature, item.bbox.slice(), {
    inputMethod: state.intakeMethod,
    filename: state.intakeFilename
  });
}

async function ingestParsed(parsed) {
  state.intakeMethod = parsed.method;
  state.intakeFilename = parsed.filename || null;
  if (intakeFileName) intakeFileName.textContent = parsed.filename || "";
  state.intakeSelectedId = null;
  renderIntakeFeatures(parsed.items);
}

async function loadSampleProperty() {
  intakeNote.textContent = "Loading sample GeoJSON…";
  const response = await fetch("/data/samples/kalbar_property.geojson");
  if (!response.ok) throw new Error("Sample property file missing.");
  const text = await response.text();
  const items = window.ClearcutIntake.normalizeGeoJSON(text);
  await ingestParsed({ method: "sample_geojson", items: items, filename: "kalbar_property.geojson" });
  intakeNote.textContent = "Sample West Kalimantan plot loaded.";
}

function selectChip(id) {
  if (state.selectedId !== null) {
    map.setFeatureState({ source: "chips", id: state.selectedId }, { selected: false });
  }
  state.selectedId = id;
  map.setFeatureState({ source: "chips", id: id }, { selected: true });
  renderSelection();
}

function clearSelection() {
  if (state.selectedId !== null && state.mapReady) {
    map.setFeatureState({ source: "chips", id: state.selectedId }, { selected: false });
  }
  state.selectedId = null;
  renderSelection();
}

function rectangle(a, b) {
  const minLon = Math.min(a.lng, b.lng);
  const maxLon = Math.max(a.lng, b.lng);
  const minLat = Math.min(a.lat, b.lat);
  const maxLat = Math.max(a.lat, b.lat);
  return {
    bbox: [minLon, minLat, maxLon, maxLat],
    feature: {
      type: "Feature",
      properties: {},
      geometry: {
        type: "Polygon",
        coordinates: [[
          [minLon, maxLat],
          [maxLon, maxLat],
          [maxLon, minLat],
          [minLon, minLat],
          [minLon, maxLat]
        ]]
      }
    }
  };
}

function eventLngLat(event) {
  const rect = map.getCanvas().getBoundingClientRect();
  return map.unproject([event.clientX - rect.left, event.clientY - rect.top]);
}

function moveModeFloat(target) {
  const tools = document.getElementById("tools");
  const floatEl = document.getElementById("mode-float");
  if (!tools || !floatEl || !target) return;
  const toolsRect = tools.getBoundingClientRect();
  const rect = target.getBoundingClientRect();
  const left = rect.left - toolsRect.left;
  const top = rect.top - toolsRect.top;
  floatEl.style.width = rect.width + "px";
  floatEl.style.height = rect.height + "px";
  floatEl.style.transform = "translate(" + left + "px, " + top + "px)";
  floatEl.classList.add("visible");
}

function syncModeFloatToActive() {
  const active = document.querySelector("#tools .mode-option.active");
  if (active) moveModeFloat(active);
}

function bindModeFloatHover() {
  const tools = document.getElementById("tools");
  if (!tools || tools.dataset.floatBound === "1") return;
  tools.dataset.floatBound = "1";
  tools.querySelectorAll(".mode-option").forEach(function (button) {
    button.addEventListener("mouseenter", function () {
      moveModeFloat(button);
    });
    button.addEventListener("focus", function () {
      moveModeFloat(button);
    });
  });
  tools.addEventListener("mouseleave", function () {
    syncModeFloatToActive();
  });
  window.addEventListener("resize", function () {
    syncModeFloatToActive();
  });
}

function setTool(tool) {
  state.tool = tool;
  state.drawMode = tool === "box" || tool === "shape";
  state.drawing = false;
  state.dragStart = null;
  state.shapePoints = [];
  setSketch(emptyCollection());

  ["pan", "box", "shape", "upload"].forEach(function (name) {
    const button = document.getElementById("tool-" + name);
    if (!button) return;
    const active = name === tool;
    button.classList.toggle("active", active);
    button.setAttribute("aria-checked", active ? "true" : "false");
  });

  const intake = document.getElementById("intake");
  if (intake) intake.classList.toggle("intake-focus", tool === "upload");

  const copy = document.getElementById("unknown-copy");
  if (copy && !state.showingSubmission) {
    if (tool === "shape") copy.textContent = "Drag any shape around the property, then analyze.";
    else if (tool === "box") copy.textContent = "Drag a box around the property, then analyze.";
    else if (tool === "upload") copy.textContent = "Upload or paste GeoJSON, KML, or GPX below.";
    else copy.textContent = "Pan the map, or choose Box / Shape / Upload.";
  }

  if (tool === "upload" && intake) {
    intake.scrollIntoView({ behavior: "smooth", block: "nearest" });
    const file = document.getElementById("intake-file");
    if (file) file.focus({ preventScroll: true });
  }

  syncModeFloatToActive();

  if (!state.mapReady) return;
  if (state.drawMode) map.dragPan.disable();
  else map.dragPan.enable();
  map.getCanvas().style.cursor = state.drawMode ? "crosshair" : "";
}

function setDrawMode(on) {
  setTool(on ? "box" : "pan");
}

let map = null;

try {
  map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      imagery: {
        type: "raster",
        tiles: [
          "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"
        ],
        tileSize: 256,
        attribution: "Esri, Maxar, Earthstar Geographics, and the GIS user community"
      }
    },
    layers: [
      { id: "imagery", type: "raster", source: "imagery" }
    ]
  },
  center: [110.08, -0.78],
  zoom: 5,
  attributionControl: true,
  boxZoom: false
  });
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 140, unit: "metric" }), "bottom-right");
} catch (error) {
  statusEl.hidden = false;
  statusEl.textContent = "The map could not start in this browser.";
}

if (map) {
map.on("load", function () {
  map.addSource("chips", { type: "geojson", data: emptyCollection() });
  map.addLayer({
    id: "chips-fill",
    type: "fill",
    source: "chips",
    paint: {
      "fill-color": [
        "match", ["get", "result"],
        "confirmed_conversion", COLORS.confirmed_conversion,
        "still_forest", COLORS.still_forest,
        "old_plantation", COLORS.old_plantation,
        "inconclusive", COLORS.inconclusive,
        "#ffffff"
      ],
      "fill-opacity": [
        "case",
        ["==", ["get", "result"], "pending"],
        0.01,
        ["boolean", ["feature-state", "selected"], false],
        0.6,
        0.45
      ]
    }
  });
  map.addLayer({
    id: "chips-line",
    type: "line",
    source: "chips",
    paint: {
      "line-color": [
        "case",
        ["boolean", ["feature-state", "selected"], false],
        "#ffffff",
        [
          "match", ["get", "result"],
          "confirmed_conversion", COLORS.confirmed_conversion,
          "still_forest", COLORS.still_forest,
          "old_plantation", COLORS.old_plantation,
          "inconclusive", COLORS.inconclusive,
          "pending", "#ffffff",
          "#ffffff"
        ]
      ],
      "line-width": ["case", ["boolean", ["feature-state", "selected"], false], 4, 2]
    }
  });
  map.addSource("draft", { type: "geojson", data: emptyCollection() });
  map.addLayer({
    id: "draft-fill",
    type: "fill",
    source: "draft",
    paint: { "fill-color": "#ffffff", "fill-opacity": 0.12 }
  });
  map.addLayer({
    id: "draft-line",
    type: "line",
    source: "draft",
    paint: { "line-color": "#ffffff", "line-width": 2, "line-dasharray": [1.5, 1] }
  });
  map.addSource("sketch", { type: "geojson", data: emptyCollection() });
  map.addLayer({
    id: "sketch-line",
    type: "line",
    source: "sketch",
    filter: ["==", ["geometry-type"], "LineString"],
    paint: { "line-color": "#ffffff", "line-width": 2.5 }
  });
  map.addLayer({
    id: "sketch-points",
    type: "circle",
    source: "sketch",
    filter: ["==", ["geometry-type"], "Point"],
    paint: {
      "circle-radius": 3.5,
      "circle-color": "#ffffff",
      "circle-stroke-width": 1,
      "circle-stroke-color": "#16181b"
    }
  });

  state.mapReady = true;
  setTool("box");
  map.fitBounds(
    [[KALBAR_BBOX[0], KALBAR_BBOX[1]], [KALBAR_BBOX[2], KALBAR_BBOX[3]]],
    { padding: fitPadding(), maxZoom: 13, duration: 0 }
  );
  renderPanel();
});

map.on("mousedown", function (event) {
  if (!state.drawMode || event.originalEvent.button !== 0) return;
  state.drawing = true;
  state.dragStart = event.lngLat;
  if (state.tool === "shape") {
    state.shapePoints = [{ lng: event.lngLat.lng, lat: event.lngLat.lat }];
    setSketch(sketchCollection(state.shapePoints, false));
  }
  event.preventDefault();
});

window.addEventListener("mousemove", function (event) {
  if (!state.drawing || !state.dragStart) return;
  const ll = eventLngLat(event);
  if (state.tool === "box") {
    const box = rectangle(state.dragStart, ll);
    setDraft(box.feature);
    return;
  }
  if (state.tool === "shape") {
    const last = state.shapePoints[state.shapePoints.length - 1];
    if (!last || distanceMeters(last, { lng: ll.lng, lat: ll.lat }) >= 8) {
      state.shapePoints.push({ lng: ll.lng, lat: ll.lat });
      setSketch(sketchCollection(state.shapePoints, true));
    }
  }
});

window.addEventListener("mouseup", function (event) {
  if (!state.drawing || !state.dragStart) return;
  state.drawing = false;
  const start = state.dragStart;
  state.dragStart = null;

  if (state.tool === "box") {
    const box = rectangle(start, eventLngLat(event));
    const width = box.bbox[2] - box.bbox[0];
    const height = box.bbox[3] - box.bbox[1];
    if (width < 0.002 && height < 0.002) {
      if (state.showingSubmission) setDraft(null);
      return;
    }
    state.suppressClick = true;
    clearIntakeList();
    setSketch(emptyCollection());
    showUnknown(box.feature, box.bbox, { inputMethod: "draw_box" });
    return;
  }

  if (state.tool === "shape") {
    const ll = eventLngLat(event);
    state.shapePoints.push({ lng: ll.lng, lat: ll.lat });
    const built = polygonFromPoints(state.shapePoints);
    state.shapePoints = [];
    setSketch(emptyCollection());
    if (!built) {
      analyzeNote.textContent = "Draw a larger shape — drag around the property boundary.";
      return;
    }
    const error = window.ClearcutIntake.validateBBox(built.bbox);
    if (error) {
      analyzeNote.textContent = error;
      return;
    }
    state.suppressClick = true;
    clearIntakeList();
    showUnknown(built.feature, built.bbox, { inputMethod: "draw_shape" });
  }
});

map.on("click", function (event) {
  if (state.suppressClick) {
    state.suppressClick = false;
    return;
  }
  if (!state.showingSubmission) return;
  const hits = map.queryRenderedFeatures(event.point, { layers: ["chips-fill", "chips-line"] });
  if (!hits.length) {
    clearSelection();
    return;
  }
  selectChip(Number(hits[0].id));
});

map.on("mousemove", function (event) {
  if (!state.mapReady || state.drawing) return;
  const hits = state.showingSubmission
    ? map.queryRenderedFeatures(event.point, { layers: ["chips-fill"] })
    : [];
  if (hits.length) {
    map.getCanvas().style.cursor = "pointer";
    return;
  }
  map.getCanvas().style.cursor = state.drawMode ? "crosshair" : "";
});

window.addEventListener("keydown", function (event) {
  if (event.key === "Escape" && state.tool === "shape") {
    state.drawing = false;
    state.dragStart = null;
    state.shapePoints = [];
    setSketch(emptyCollection());
  }
});
}

bindModeFloatHover();

document.getElementById("tool-pan").addEventListener("click", function () { setTool("pan"); });
document.getElementById("tool-box").addEventListener("click", function () { setTool("box"); });
document.getElementById("tool-shape").addEventListener("click", function () { setTool("shape"); });
document.getElementById("tool-upload").addEventListener("click", function () { setTool("upload"); });

requestAnimationFrame(function () {
  syncModeFloatToActive();
});

document.getElementById("analyze").addEventListener("click", function () {
  const button = this;
  if (!state.draftBbox) return;
  const boundsError = window.ClearcutIntake && window.ClearcutIntake.validateBBox(state.draftBbox);
  if (boundsError) {
    analyzeNote.textContent = boundsError;
    return;
  }
  button.disabled = true;
  analyzeNote.textContent = "Fetching the years and scoring the tiles.";
  const selected = state.intakeItems.find(function (row) { return row.id === state.intakeSelectedId; });
  const payload = {
    bbox: state.draftBbox,
    geometry: state.draftGeometry,
    input_method: state.intakeMethod || "draw",
    source_filename: state.intakeFilename,
    name: selected ? selected.label : undefined,
    properties: selected ? selected.properties : {}
  };
  fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  }).then(function (response) {
    return response.json().then(function (body) {
      if (!response.ok) throw new Error(body.error || "Analysis failed");
      return body;
    });
  }).then(function (body) {
    state.plotUrl = "../data/plots/" + body.plot;
    return loadPlot();
  }).then(function () {
    analyzeNote.textContent = "";
    showSubmission(true);
  }).catch(function (error) {
    analyzeNote.textContent = error.message || "Analysis failed";
  }).finally(function () {
    button.disabled = false;
  });
});

document.getElementById("intake-file").addEventListener("change", function (event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  intakeNote.textContent = "Reading " + file.name + "…";
  window.ClearcutIntake.parseUpload(file).then(function (parsed) {
    return ingestParsed(parsed);
  }).then(function () {
    intakeNote.textContent = "Loaded " + file.name + ". Select a feature if needed, then analyze.";
  }).catch(function (error) {
    intakeNote.textContent = error.message || "Could not read that file.";
  });
});

document.getElementById("intake-paste-btn").addEventListener("click", function () {
  const text = document.getElementById("intake-paste").value;
  intakeNote.textContent = "Parsing GeoJSON…";
  window.ClearcutIntake.parsePaste(text).then(function (parsed) {
    return ingestParsed(parsed);
  }).then(function () {
    intakeNote.textContent = "Pasted GeoJSON ready. Analyze when ready.";
  }).catch(function (error) {
    intakeNote.textContent = error.message || "Could not parse that GeoJSON.";
  });
});

document.getElementById("intake-sample").addEventListener("click", function () {
  loadSampleProperty().catch(function (error) {
    intakeNote.textContent = error.message || "Could not load sample.";
  });
});

timeInput.addEventListener("input", function () { setTime(timeInput.value); });
document.getElementById("overlay").addEventListener("input", applyOverlayOpacity);

document.getElementById("fly-form").addEventListener("submit", function (event) {
  event.preventDefault();
  const lat = Number(document.getElementById("lat").value);
  const lon = Number(document.getElementById("lon").value);
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    flyNote.textContent = "Enter a latitude and a longitude.";
    return;
  }
  flyNote.textContent = "";
  if (!map) return;
  map.flyTo({ center: [lon, lat], zoom: 10, essential: true });
});

async function fetchJson(url) {
  try {
    const response = await fetch(url);
    if (!response.ok) return { ok: false, status: response.status, data: null };
    return { ok: true, status: response.status, data: await response.json() };
  } catch (error) {
    return { ok: false, status: 0, data: null };
  }
}

function loadPlot() {
  const plot = state.plotUrl;
  return Promise.all([
    fetchJson(plot + "/evidence.json"),
    fetchJson(plot + "/chip_stats.json"),
    fetchJson(plot + "/perception_tiles.json"),
    fetchJson(plot + "/confidence.json")
  ]).then(function (results) {
    const evidence = results[0];
    const stats = results[1];
    const perception = results[2];
    const confidence = results[3];
    if (evidence.ok && evidence.data) {
      if (Array.isArray(evidence.data.bbox) && evidence.data.bbox.length === 4) {
        state.bbox = evidence.data.bbox.slice();
      }
      state.scenes = scenesFrom(evidence.data);
    }
    state.confidence = confidence && confidence.ok ? confidence.data : null;
    state.analysisLoaded = stats.ok && perception.ok;
    state.chips = buildChips(stats.ok ? stats.data : null, perception.ok ? perception.data : null);
    if (state.mapReady && state.showingSubmission) {
      applyChips();
      setTime(state.scenes.length - 1);
      showConfidence();
    } else if (state.scenes.length) {
      sceneDate.textContent = formatDate(state.scenes[state.scenes.length - 1].date);
      timeInput.max = String(state.scenes.length - 1);
      timeInput.value = String(state.scenes.length - 1);
    }
    renderPanel();
  });
}

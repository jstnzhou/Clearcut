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
  confirmed_conversion: "#ff4d8d",
  still_forest: "#2f9e8f",
  old_plantation: "#e0b84a",
  inconclusive: "#8d99a6"
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
const processingEl = document.getElementById("processing");
const processingLabel = document.getElementById("processing-label");
const intakeNote = document.getElementById("intake-note");
let processingTimer = null;
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
  draftName: null,
  draftProperties: null,
  companies: [],
  openCompanies: {},
  farmFilters: {},
  selectedFarmId: null,
  confidence: null,
  clipToDraft: false,
  investigation: null,
  investigationPending: null,
  suppressClick: false,
  mapReady: false,
  plotRequest: 0
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

const CAUSE_LABEL = {
  oil_palm_plantation: "Oil palm plantation",
  pulpwood_plantation: "Pulpwood plantation",
  rubber_plantation: "Rubber plantation",
  other_plantation: "Other plantation",
  smallholder_agriculture: "Smallholder agriculture",
  mining: "Mining",
  infrastructure: "Infrastructure",
  fire: "Fire",
  logging: "Logging",
  other: "Other",
  unknown: "Unknown"
};

function sourceById(id) {
  const sources = (state.investigation && state.investigation.sources) || [];
  for (let i = 0; i < sources.length; i += 1) {
    if (sources[i].id === id) return sources[i];
  }
  return null;
}

function knownValue(value) {
  const text = String(value || "").trim();
  if (!text || text.toLowerCase() === "unknown") return "";
  return text;
}

function asSentence(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  return /[.!?]$/.test(text) ? text : text + ".";
}

function appendSourceLinks(parent, ids) {
  let added = 0;
  (ids || []).forEach(function (id) {
    const source = sourceById(id);
    if (!source) return;
    const url = String(source.url || "");
    if (url.indexOf("https://") !== 0 && url.indexOf("http://") !== 0) return;
    if (added) parent.append(document.createTextNode(", "));
    const link = document.createElement("a");
    link.href = url;
    link.rel = "noopener noreferrer";
    link.target = "_blank";
    link.textContent = source.title || id;
    parent.append(link);
    added += 1;
  });
  return added;
}

function causeSummary(report) {
  const causeName = knownValue(CAUSE_LABEL[report.cause] || report.cause);
  const replacement = knownValue(report.replacement_land_use);
  const names = [knownValue(report.plantation_name), knownValue(report.company)].filter(Boolean);
  const parts = [];
  if (causeName) parts.push(causeName);
  if (replacement && replacement.toLowerCase() !== causeName.toLowerCase()) parts.push(replacement);
  if (names.length) parts.push(names.join(", "));
  let text = parts.length ? parts.join(". ") : "No cause was identified in public reporting";
  text = asSentence(text);
  if (report.confidence) text += " Confidence: " + report.confidence + ".";
  const period = knownValue(report.event_period);
  if (period) text += " Reported period: " + asSentence(period);
  return text;
}

function appendInvestigation() {
  const investigation = state.investigation;
  if (!investigation || !(Number(investigation.tool_calls) > 0) || investigation.status === "not_applicable") return;
  const block = document.createElement("details");
  block.className = "cause-block";
  const heading = document.createElement("summary");
  heading.textContent = "Likely cause (public reporting)";
  block.append(heading);
  if (investigation.status === "error" || !investigation.report) {
    const line = document.createElement("p");
    line.className = "disclaimer";
    line.textContent = "Source lookup unavailable.";
    block.append(line);
    findingEl.append(block);
    return;
  }
  const report = investigation.report;
  const summary = document.createElement("p");
  summary.textContent = causeSummary(report);
  block.append(summary);
  const evidence = Array.isArray(report.evidence) ? report.evidence : [];
  if (evidence.length) {
    const list = document.createElement("ul");
    list.className = "cause-list";
    evidence.forEach(function (item) {
      const entry = document.createElement("li");
      entry.textContent = asSentence(item && item.claim);
      const cite = document.createElement("span");
      cite.className = "cause-cite";
      cite.append(document.createTextNode("Source: "));
      if (appendSourceLinks(cite, item && item.source_ids)) entry.append(cite);
      list.append(entry);
    });
    block.append(list);
  }
  const inferences = (Array.isArray(report.inferences) ? report.inferences : []).map(function (item) {
    return asSentence(item && (item.claim || item.basis));
  }).filter(Boolean);
  const caveats = (Array.isArray(report.caveats) ? report.caveats : []).map(asSentence).filter(Boolean);
  if (inferences.length || caveats.length) {
    const inferHeading = document.createElement("h2");
    inferHeading.textContent = "Inferences";
    const list = document.createElement("ul");
    list.className = "cause-list";
    inferences.concat(caveats).forEach(function (text) {
      const entry = document.createElement("li");
      entry.textContent = text;
      list.append(entry);
    });
    block.append(inferHeading, list);
  }
  const note = document.createElement("p");
  note.className = "disclaimer";
  note.textContent = "From public web reporting, not a compliance finding.";
  block.append(note);
  findingEl.append(block);
}

function scoredMean(confidence) {
  const windows = (confidence && confidence.windows) || [];
  const scored = [];
  for (let i = 0; i < windows.length; i += 1) {
    const window = windows[i];
    if (!window || typeof window.score !== "number") continue;
    if (!(Number(window.seconds) > 0 || String(window.raw || "").trim())) continue;
    scored.push(window);
  }
  if (!scored.length) return null;
  let weight = 0;
  let sum = 0;
  scored.forEach(function (window) {
    const share = typeof window.loss_share === "number" ? window.loss_share : 0;
    weight += share;
    sum += share * window.score;
  });
  const mean = weight > 0
    ? sum / weight
    : scored.reduce(function (total, window) { return total + window.score; }, 0) / scored.length;
  return Math.round(mean * 1000) / 1000;
}

function scoreNeedsSearch(confidence) {
  if (!confidence || confidence.maps_only || !confidence.model) return false;
  const mean = scoredMean(confidence);
  if (typeof mean === "number" && mean >= 0.5) return true;
  const windows = confidence.windows || [];
  for (let i = 0; i < windows.length; i += 1) {
    const window = windows[i];
    if (window && window.eligible && typeof window.score === "number" && window.score >= 0.6) return true;
  }
  return false;
}

function requestInvestigation(plotUrl) {
  const id = String(plotUrl || "").split("/").filter(Boolean).pop();
  if (!id || state.investigationPending === id) return;
  state.investigationPending = id;
  if (statusEl) {
    statusEl.hidden = false;
    statusEl.textContent = "Looking up public reporting for this plot.";
  }
  fetch("/api/investigate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ plot: id })
  }).then(function (response) {
    return response.json().then(function (payload) {
      if (!response.ok) throw new Error(payload.error || "Source lookup failed");
      return payload;
    });
  }).then(function (payload) {
    if (state.plotUrl !== plotUrl) return;
    state.investigation = payload;
    state.investigationPending = null;
    renderPanel();
  }).catch(function () {
    if (state.plotUrl !== plotUrl) return;
    state.investigationPending = null;
    if (statusEl) {
      statusEl.hidden = false;
      statusEl.textContent = "Source lookup unavailable.";
    }
  });
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
      ? ""
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
    if (disclaimer.textContent) findingEl.append(verdict, disclaimer, loss);
    else findingEl.append(verdict, loss);
    appendInvestigation();
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
    appendInvestigation();
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
  appendInvestigation();
}

function renderSelection() {
  const chip = state.chips.find(function (item) { return item.id === state.selectedId; });
  const chipBlock = document.getElementById("chip-block");
  if (!chip || !state.showingSubmission) {
    if (chipEmpty) chipEmpty.hidden = false;
    chipReadout.hidden = true;
    if (chipBlock) chipBlock.hidden = true;
    return;
  }
  if (chipEmpty) chipEmpty.hidden = true;
  if (chipBlock) chipBlock.hidden = false;
  chipReadout.hidden = false;
  document.getElementById("chip-id").textContent = chip.id + " · " + chip.quad;
  document.getElementById("chip-label").textContent = chip.perceptron || "pending";
  document.getElementById("chip-hansen").textContent = formatShare(chip.hansen);
  document.getElementById("chip-jrc").textContent = formatShare(chip.jrc);
  document.getElementById("chip-result").textContent = RESULT_NAME[chip.result] || chip.result;
}

function formatPlace(evidence, submission) {
  const name = submission && submission.name;
  const lat = evidence && typeof evidence.lat === "number" ? evidence.lat : null;
  const lon = evidence && typeof evidence.lon === "number" ? evidence.lon : null;
  const plotName = document.getElementById("plot-name");
  const plotCoords = document.getElementById("plot-coords");
  if (plotName) {
    if (state.plotUrl && state.plotUrl.endsWith("kalbar1")) plotName.textContent = "Plot KS-19, a fictional submission.";
    else if (name) plotName.textContent = name;
    else plotName.textContent = "This plot";
  }
  if (plotCoords) {
    plotCoords.textContent = lat !== null && lon !== null
      ? "Latitude " + lat.toFixed(2) + ", longitude " + lon.toFixed(2) + "."
      : "";
  }
}

function refreshPrompt() {
  const lead = document.getElementById("panel-lead");
  const copy = document.getElementById("unknown-copy");
  const button = document.getElementById("analyze");
  if (button && button.disabled) return;
  if (state.showingSubmission) {
    if (lead) lead.textContent = "Red is likely planted. Green is not. This is not a compliance finding.";
    return;
  }
  if (lead) lead.textContent = "Compare a boundary with the forest that was there in 2020.";
  if (!copy) return;
  const ready = !!state.draftBbox;
  if (state.tool === "shape") {
    copy.textContent = ready ? "Shape is set. Check it when you are ready." : "Drag a shape around the plot.";
  } else if (state.tool === "upload") {
    copy.textContent = ready ? "Boundary is set. Check it when you are ready." : "Upload a file or paste GeoJSON below.";
  } else if (state.tool === "pan") {
    copy.textContent = "Move the map, or switch to Box or Shape to draw.";
  } else {
    copy.textContent = ready ? "Box is set. Check it when you are ready." : "Drag a box around the plot.";
  }
}

function renderPanel() {
  viewSubmission.hidden = !state.showingSubmission;
  viewUnknown.hidden = state.showingSubmission;
  refreshPrompt();
  if (!state.showingSubmission) return;
  const chipsReady = state.analysisLoaded || !!state.confidence;
  statusEl.hidden = chipsReady;
  statusEl.textContent = chipsReady ? "" : "Still scoring the tiles.";
  const chipBlock = document.getElementById("chip-block");
  if (chipBlock) chipBlock.hidden = !state.chips.length;
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

const clippedImages = {};
const imageEpoch = {};

function clipImageToShape(url, bbox, geometry) {
  if (!geometry || (geometry.type !== "Polygon" && geometry.type !== "MultiPolygon")) {
    return Promise.resolve(url);
  }
  const clipKey = url + "|" + (geometry ? JSON.stringify(geometry.coordinates) : "");
  if (clippedImages[clipKey]) return Promise.resolve(clippedImages[clipKey]);
  return new Promise(function (resolve) {
    const img = new Image();
    img.onload = function () {
      try {
        const canvas = document.createElement("canvas");
        canvas.width = img.naturalWidth || img.width;
        canvas.height = img.naturalHeight || img.height;
        const ctx = canvas.getContext("2d");
        ctx.drawImage(img, 0, 0);
        const minLon = bbox[0];
        const minLat = bbox[1];
        const maxLon = bbox[2];
        const maxLat = bbox[3];
        const lonSpan = Math.max(maxLon - minLon, 1e-9);
        const latSpan = Math.max(maxLat - minLat, 1e-9);
        ctx.globalCompositeOperation = "destination-in";
        ctx.beginPath();
        const polys = geometry.type === "Polygon" ? [geometry.coordinates] : geometry.coordinates;
        polys.forEach(function (poly) {
          (poly || []).forEach(function (ring) {
            (ring || []).forEach(function (pt, i) {
              if (!pt || pt.length < 2) return;
              const x = ((pt[0] - minLon) / lonSpan) * canvas.width;
              const y = ((maxLat - pt[1]) / latSpan) * canvas.height;
              if (i === 0) ctx.moveTo(x, y);
              else ctx.lineTo(x, y);
            });
            ctx.closePath();
          });
        });
        ctx.fill("evenodd");
        const shown = canvas.toDataURL("image/png");
        clippedImages[clipKey] = shown;
        resolve(shown);
      } catch (error) {
        resolve(url);
      }
    };
    img.onerror = function () { resolve(url); };
    img.src = url;
  });
}

function showImageLayer(id, url, coords, setup) {
  const epoch = (imageEpoch[id] || 0) + 1;
  imageEpoch[id] = epoch;
  clipImageToShape(url, state.bbox, state.draftGeometry).then(function (shown) {
    if (imageEpoch[id] !== epoch || !state.mapReady || !map) return;
    if (!map.getSource(id)) setup(shown);
    else map.getSource(id).updateImage({ url: shown, coordinates: coords });
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "visible");
  });
}

function ensureScene(url, coords) {
  if (map.getSource("scene")) return;
  const before = map.getLayer("chips-fill") ? "chips-fill" : undefined;
  map.addSource("scene", { type: "image", url: url, coordinates: coords });
  map.addLayer({
    id: "scene",
    type: "raster",
    source: "scene",
    paint: { "raster-fade-duration": 0, "raster-resampling": "nearest" }
  }, before);
}

function setTime(index) {
  const scenes = state.scenes;
  if (!scenes.length) return;
  const next = Math.max(0, Math.min(scenes.length - 1, Number(index) || 0));
  timeInput.max = String(scenes.length - 1);
  timeInput.value = String(next);
  sceneDate.textContent = formatDate(scenes[next].date);
  renderTicks(next);
  if (!state.mapReady || !state.showingSubmission) return;
  paintMarks();
  const coords = imageCoordinates(state.bbox);
  const url = state.plotUrl + "/" + scenes[next].file;
  showImageLayer("scene", url, coords, function (shown) {
    ensureScene(shown, coords);
  });
}

function paintMarks() {
  if (state.confidence && map.getLayer("chips-fill")) {
    map.setLayoutProperty("chips-fill", "visibility", "none");
    map.setLayoutProperty("chips-line", "visibility", "none");
  }
}

function renderTicks(active) {
  const ticks = document.getElementById("ticks");
  if (!ticks) return;
  ticks.replaceChildren();
  state.scenes.forEach(function (scene, index) {
    const button = document.createElement("button");
    const year = String(scene.date).slice(0, 4);
    button.type = "button";
    button.textContent = year === "2020" ? "2020 cutoff" : year;
    if (year === "2020") button.className = "cutoff";
    if (index === active) button.classList.add("on");
    button.addEventListener("click", function () { setTime(index); });
    ticks.append(button);
  });
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
  showImageLayer("confidence", url, coords, function (shown) {
    if (map.getSource("confidence")) return;
    map.addSource("confidence", { type: "image", url: shown, coordinates: coords });
    map.addLayer({
      id: "confidence",
      type: "raster",
      source: "confidence",
      paint: {
        "raster-opacity": overlayOpacity(),
        "raster-fade-duration": 0,
        "raster-resampling": "nearest"
      }
    });
    applyOverlayOpacity();
  });
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

function setPropertyBoundary(geometry) {
  if (!geometry) {
    state.draftGeometry = null;
    state.draftFeature = null;
    setDraft(null);
    return;
  }
  const feature = {
    type: "Feature",
    properties: { role: "property" },
    geometry: geometry
  };
  state.draftGeometry = geometry;
  state.draftFeature = feature;
  setDraft(feature);
}

function displaySubmission() {
  state.showingSubmission = true;
  state.selectedId = null;
  if (state.mapReady) {
    // Keep the drawn/uploaded polygon outline — do not fall back to the bbox square.
    if (state.draftFeature) setDraft(state.draftFeature);
    else setDraft(null);
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
  const frame = state.clipToDraft && state.draftBbox ? state.draftBbox : state.bbox;
  map.fitBounds(
    [[frame[0], frame[1]], [frame[2], frame[3]]],
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
  state.draftName = opts.name || null;
  state.draftProperties = opts.properties || null;
  if (!opts.keepFarm) state.selectedFarmId = null;
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
  intakeNote.textContent = "“" + item.label + "” is selected. Check it when you are ready.";
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

  refreshPrompt();

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


function hidePlotLayers() {
  if (!state.mapReady || !map) return;
  ["scene", "confidence"].forEach(function (id) {
    if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", "none");
  });
}

function openPlot(id) {
  if (!/^[A-Za-z0-9_]+$/.test(id)) return Promise.resolve();
  analyzeNote.textContent = "Opening this checked plot.";
  state.plotRequest += 1;
  state.plotUrl = "../data/plots/" + id;
  state.clipToDraft = false;
  state.draftBbox = null;
  state.draftFeature = null;
  state.draftGeometry = null;
  state.confidence = null;
  state.investigation = null;
  state.investigationPending = null;
  state.scenes = [];
  state.chips = [];
  hidePlotLayers();
  setDraft(null);
  return loadPlot().then(function () {
    showSubmission(true);
    analyzeNote.textContent = "Showing the saved check for this plot.";
  }).catch(function (error) {
    analyzeNote.textContent = error && error.message ? error.message : "This checked plot could not be opened.";
  });
}

function paintBoard(data) {
  const list = document.getElementById("board-list");
  const note = document.getElementById("board-note");
  if (!list || !note) return;
  const plots = (data && data.plots) || [];
  note.textContent = plots.length
    ? plots.length + " checked plots. Percent of the rectangle that is likely a deforested plantation, highest first."
    : "No checked plots yet. Analyze a rectangle to add one.";
  list.replaceChildren();
  if (!plots.length) return;
  const table = document.createElement("table");
  table.className = "board-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["", "Plot", "Of the box", ""].forEach(function (label) {
    const cell = document.createElement("th");
    cell.textContent = label;
    headRow.append(cell);
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  plots.forEach(function (plot, index) {
    const row = document.createElement("tr");
    const rank = document.createElement("td");
    rank.textContent = String(index + 1);
    const place = document.createElement("td");
    place.textContent = plot.name || plot.id;
    const score = document.createElement("td");
    score.className = "board-score";
    score.textContent = typeof plot.plantation_share === "number"
      ? (Math.round(plot.plantation_share * 1000) / 10) + "%"
      : "—";
    const action = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Open";
    button.addEventListener("click", function () {
      const dialog = document.getElementById("board-dialog");
      if (dialog && dialog.open) dialog.close();
      openPlot(plot.id);
    });
    action.append(button);
    row.append(rank, place, score, action);
    body.append(row);
  });
  table.append(head, body);
  list.append(table);
}

document.getElementById("tool-pan").addEventListener("click", function () { setTool("pan"); });
document.getElementById("tool-box").addEventListener("click", function () { setTool("box"); });
(function () {
  const button = document.getElementById("tool-shape");
  if (button) button.addEventListener("click", function () { setTool("shape"); });
})();
(function () {
  const button = document.getElementById("tool-upload");
  if (button) button.addEventListener("click", function () { setTool("upload"); });
})();

requestAnimationFrame(function () {
  syncModeFloatToActive();
});


const openBoard = document.getElementById("open-board");
if (openBoard) {
  openBoard.addEventListener("click", function () {
    const dialog = document.getElementById("board-dialog");
    const note = document.getElementById("board-note");
    if (note) note.textContent = "Loading checked plots.";
    fetch("/api/leaderboard").then(function (response) {
      if (!response.ok) throw new Error("Could not load checked plots.");
      return response.json();
    }).then(function (data) {
      paintBoard(data);
    }).catch(function (error) {
      if (note) note.textContent = error.message || "Could not load checked plots.";
    });
    if (dialog && dialog.showModal) dialog.showModal();
  });
}

const closeBoard = document.getElementById("close-board");
if (closeBoard) {
  closeBoard.addEventListener("click", function () {
    const dialog = document.getElementById("board-dialog");
    if (dialog && dialog.open) dialog.close();
  });
}

function setProcessing(on, label) {
  if (!processingEl) return;
  if (processingTimer) {
    clearInterval(processingTimer);
    processingTimer = null;
  }
  if (!on) {
    processingEl.hidden = true;
    processingEl.setAttribute("aria-busy", "false");
    return;
  }
  if (processingLabel) processingLabel.textContent = label || "Processing";
  processingEl.hidden = false;
  processingEl.setAttribute("aria-busy", "true");
}

function startProcessingCycle() {
  const steps = ["Reading the boundary", "Fetching the years", "Scoring the tiles"];
  let index = 0;
  setProcessing(true, steps[0]);
  processingTimer = setInterval(function () {
    index += 1;
    if (index >= steps.length) {
      clearInterval(processingTimer);
      processingTimer = null;
      return;
    }
    if (processingLabel) processingLabel.textContent = steps[index];
  }, 1600);
}

document.getElementById("analyze").addEventListener("click", function () {
  const button = this;
  if (!state.draftBbox) {
    analyzeNote.textContent = "Draw or upload a boundary first.";
    return;
  }
  const boundsError = window.ClearcutIntake && window.ClearcutIntake.validateBBox(state.draftBbox);
  if (boundsError) {
    analyzeNote.textContent = boundsError;
    return;
  }
  button.disabled = true;
  button.textContent = "Checking…";
  analyzeNote.textContent = "Pulling the satellite years, then scoring the tiles.";
  const lead = document.getElementById("panel-lead");
  if (lead) lead.textContent = "The check is running.";
  startProcessingCycle();
  const selected = state.intakeItems.find(function (row) { return row.id === state.intakeSelectedId; });
  const payload = {
    bbox: state.draftBbox,
    geometry: state.draftGeometry,
    input_method: state.intakeMethod || "draw",
    source_filename: state.intakeFilename,
    name: state.draftName || (selected ? selected.label : undefined),
    properties: Object.assign({}, selected ? selected.properties : {}, state.draftProperties || {})
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
  }).then(function (payload) {
    if (payload.cached) setProcessing(true, "Loading cache");
    else setProcessing(true, "Finishing");
    state.clipToDraft = !!payload.cached && !!state.draftGeometry;
    state.plotUrl = "../data/plots/" + payload.plot;
    return loadPlot().then(function () {
      return payload;
    });
  }).then(function (payload) {
    const saved = state.bbox;
    const drawn = state.draftBbox;
    const cropped = payload.cached && state.clipToDraft && saved && drawn
      && (saved[0] < drawn[0] - 1e-5 || saved[1] < drawn[1] - 1e-5 || saved[2] > drawn[2] + 1e-5 || saved[3] > drawn[3] + 1e-5);
    analyzeNote.textContent = payload.cached
      ? (cropped
        ? "This area was already checked. Showing the part inside the shape you drew."
        : "This area was already checked. Showing that result.")
      : "Check finished.";
    showSubmission(true);
  }).catch(function (error) {
    analyzeNote.textContent = error.message || "The check did not finish. Try again.";
    const lead = document.getElementById("panel-lead");
    if (lead && !state.showingSubmission) lead.textContent = "Compare a boundary with the forest that was there in 2020.";
  }).finally(function () {
    setProcessing(false);
    button.disabled = false;
    button.textContent = "Check this plot";
  });
});

document.getElementById("intake-file").addEventListener("change", function (event) {
  const file = event.target.files && event.target.files[0];
  if (!file) return;
  const lower = file.name.toLowerCase();
  if (lower.endsWith(".pdf") || file.type === "application/pdf") {
    event.target.value = "";
    importCompanyPdf(file);
    return;
  }
  intakeNote.textContent = "Reading " + file.name + "…";
  window.ClearcutIntake.parseUpload(file).then(function (parsed) {
    return ingestParsed(parsed);
  }).then(function () {
    intakeNote.textContent = file.name + " is loaded. Check it when you are ready.";
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
    intakeNote.textContent = "That polygon is ready. Check it when you are ready.";
  }).catch(function (error) {
    intakeNote.textContent = error.message || "Could not parse that GeoJSON.";
  });
});

timeInput.addEventListener("input", function () { setTime(timeInput.value); });
const playButton = document.getElementById("play");
let playing = null;
if (playButton) {
  playButton.addEventListener("click", function () {
    if (!state.scenes.length) return;
    if (playing) {
      clearInterval(playing);
      playing = null;
      playButton.textContent = "Play";
      return;
    }
    playButton.textContent = "Pause";
    playing = setInterval(function () {
      const next = (Number(timeInput.value) + 1) % state.scenes.length;
      setTime(next);
    }, 900);
  });
}
document.getElementById("overlay").addEventListener("input", applyOverlayOpacity);

function parseLatLon(latRaw, lonRaw) {
  const latText = String(latRaw || "").trim();
  const lonText = String(lonRaw || "").trim();
  const pair = latText.match(/^(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)$/);
  if (pair && !lonText) return { lat: Number(pair[1]), lon: Number(pair[2]) };
  return { lat: Number(latText), lon: Number(lonText) };
}

document.getElementById("fly-form").addEventListener("submit", function (event) {
  event.preventDefault();
  const parsed = parseLatLon(document.getElementById("lat").value, document.getElementById("lon").value);
  const lat = parsed.lat;
  const lon = parsed.lon;
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    flyNote.textContent = "Latitude must be between -90 and 90, and longitude between -180 and 180.";
    return;
  }
  flyNote.textContent = "";
  if (!map) return;
  map.stop();
  map.easeTo({
    center: [lon, lat],
    zoom: Math.max(map.getZoom(), 11),
    padding: fitPadding(),
    duration: 700,
    essential: true
  });
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
  const request = state.plotRequest;
  return Promise.all([
    fetchJson(plot + "/evidence.json"),
    fetchJson(plot + "/chip_stats.json"),
    fetchJson(plot + "/perception_tiles.json"),
    fetchJson(plot + "/confidence.json"),
    fetchJson(plot + "/investigation.json"),
    fetchJson(plot + "/submission.json")
  ]).then(function (results) {
    const evidence = results[0];
    const stats = results[1];
    const perception = results[2];
    const confidence = results[3];
    const investigation = results[4];
    const submission = results[5];
    if (request !== state.plotRequest) return;
    if (!evidence.ok || !evidence.data || !Array.isArray(evidence.data.bbox) || evidence.data.bbox.length !== 4) {
      throw new Error("This checked plot has no map bounds.");
    }
    state.bbox = evidence.data.bbox.slice();
    state.scenes = scenesFrom(evidence.data).filter(function (scene) {
      const year = Number(String(scene.date).slice(0, 4));
      return !(plot.endsWith("kalbar1") && (year === 2022 || year === 2024));
    });
    if (!(state.clipToDraft && state.draftGeometry)) {
      const geometry = evidence.data.geometry
        || (submission.ok && submission.data && submission.data.geometry)
        || null;
      setPropertyBoundary(geometry);
    } else {
      setPropertyBoundary(state.draftGeometry);
    }
    state.confidence = confidence && confidence.ok ? confidence.data : null;
    state.investigation = investigation && investigation.ok ? investigation.data : null;
    formatPlace(evidence.ok ? evidence.data : null, submission.ok ? submission.data : null);
    state.analysisLoaded = stats.ok && perception.ok;
    state.chips = buildChips(stats.ok ? stats.data : null, perception.ok ? perception.data : null);
    if (request !== state.plotRequest) return;
    if (state.mapReady && state.showingSubmission) {
      if (state.draftFeature) setDraft(state.draftFeature);
      applyChips();
      setTime(state.scenes.length - 1);
      showConfidence();
    } else if (state.scenes.length) {
      sceneDate.textContent = formatDate(state.scenes[state.scenes.length - 1].date);
      timeInput.max = String(state.scenes.length - 1);
      timeInput.value = String(state.scenes.length - 1);
    }
    renderPanel();
    if (scoreNeedsSearch(state.confidence) && !state.investigation) requestInvestigation(plot);
  });
}

const companyList = document.getElementById("company-list");
const companiesEmpty = document.getElementById("companies-empty");

function farmLabel(farm) {
  const parts = [farm.name || "Mill"];
  if (farm.country) parts.push(farm.country);
  if (farm.uml_id) parts.push(farm.uml_id);
  if (!farm.resolved) parts.push("no coordinates");
  return parts.join(" · ");
}

function renderFarmList(list, company) {
  list.replaceChildren();
  const query = (state.farmFilters[company.id] || "").trim().toLowerCase();
  const farms = (company.farms || []).filter(function (farm) {
    if (!query) return true;
    const hay = [farm.name, farm.country, farm.uml_id, farm.mill_company, farm.supplier].join(" ").toLowerCase();
    return hay.indexOf(query) !== -1;
  });
  if (!farms.length) {
    const empty = document.createElement("li");
    empty.className = "farm-more";
    empty.textContent = query ? "No mills match." : "No mills in this folder.";
    list.append(empty);
    return;
  }
  farms.forEach(function (farm) {
    const li = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.dataset.farmId = company.id + ":" + farm.id;
    button.textContent = farmLabel(farm);
    if (!farm.resolved) button.classList.add("unresolved");
    if (state.selectedFarmId === button.dataset.farmId) button.classList.add("active");
    button.addEventListener("click", function () { selectCompanyFarm(company, farm); });
    li.append(button);
    list.append(li);
  });
}

function renderCompanies() {
  if (!companyList) return;
  const focus = document.activeElement;
  const restoreFilter = focus && focus.classList && focus.classList.contains("farm-filter") ? focus.dataset.companyId : null;
  const restoreName = focus && focus.classList && focus.classList.contains("company-name") ? focus.dataset.companyId : null;
  companyList.replaceChildren();
  if (companiesEmpty) companiesEmpty.hidden = state.companies.length > 0;
  state.companies.forEach(function (company) {
    const item = document.createElement("li");
    item.className = "company";
    const row = document.createElement("div");
    row.className = "company-row";
    const open = !!state.openCompanies[company.id];
    const toggle = document.createElement("button");
    toggle.type = "button";
    toggle.className = "company-toggle";
    toggle.textContent = open ? "▾" : "▸";
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    toggle.setAttribute("aria-label", (open ? "Collapse " : "Expand ") + (company.name || "company"));
    toggle.addEventListener("click", function () {
      state.openCompanies[company.id] = !state.openCompanies[company.id];
      renderCompanies();
    });
    const name = document.createElement("input");
    name.type = "text";
    name.className = "company-name";
    name.value = company.name || "";
    name.dataset.companyId = company.id;
    name.setAttribute("aria-label", "Company name");
    name.addEventListener("change", function () { renameCompany(company, name.value); });
    name.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        name.blur();
      }
    });
    const count = document.createElement("span");
    count.className = "company-count";
    count.textContent = String((company.farms || []).length);
    row.append(toggle, name, count);
    item.append(row);
    if (open) {
      const filter = document.createElement("input");
      filter.type = "search";
      filter.className = "farm-filter";
      filter.placeholder = "Filter mills";
      filter.value = state.farmFilters[company.id] || "";
      filter.dataset.companyId = company.id;
      filter.setAttribute("aria-label", "Filter mills in " + (company.name || "company"));
      const farms = document.createElement("ul");
      farms.className = "farm-list";
      filter.addEventListener("input", function () {
        state.farmFilters[company.id] = filter.value;
        renderFarmList(farms, company);
      });
      renderFarmList(farms, company);
      item.append(filter, farms);
    }
    companyList.append(item);
  });
  if (restoreFilter) {
    const next = companyList.querySelector('.farm-filter[data-company-id="' + restoreFilter + '"]');
    if (next) {
      next.focus();
      const length = next.value.length;
      if (next.setSelectionRange) next.setSelectionRange(length, length);
    }
  } else if (restoreName) {
    const next = companyList.querySelector('.company-name[data-company-id="' + restoreName + '"]');
    if (next) next.focus();
  }
}

function loadCompanies() {
  return fetchJson("/api/companies").then(function (result) {
    state.companies = result.ok && result.data && Array.isArray(result.data.companies)
      ? result.data.companies
      : [];
    renderCompanies();
  });
}

function renameCompany(company, name) {
  const next = String(name || "").trim();
  if (!next || next === company.name) {
    renderCompanies();
    return;
  }
  fetch("/api/companies/rename", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id: company.id, name: next })
  }).then(function (response) {
    return response.json().then(function (body) {
      if (!response.ok) throw new Error(body.error || "Could not rename that company.");
      return body;
    });
  }).then(function (body) {
    company.name = body.name || next;
    if (intakeNote) intakeNote.textContent = "Renamed the folder to " + company.name + ".";
    renderCompanies();
  }).catch(function (error) {
    if (intakeNote) intakeNote.textContent = error.message || "Could not rename that company.";
    renderCompanies();
  });
}

function selectCompanyFarm(company, farm) {
  state.selectedFarmId = company.id + ":" + farm.id;
  if (companyList) {
    companyList.querySelectorAll(".farm-list button").forEach(function (button) {
      button.classList.toggle("active", button.dataset.farmId === state.selectedFarmId);
    });
  }
  if (!farm.resolved || !farm.geometry || !farm.bbox) {
    if (intakeNote) {
      intakeNote.textContent = (farm.name || "That mill") + (farm.uml_id ? " (" + farm.uml_id + ")" : "") + " has no coordinates in the mill list.";
    }
    return;
  }
  const feature = {
    type: "Feature",
    properties: {
      name: farm.name,
      uml_id: farm.uml_id || "",
      company: company.name
    },
    geometry: farm.geometry
  };
  state.intakeMethod = "upload_pdf";
  state.intakeFilename = company.source_filename || null;
  state.intakeSelectedId = null;
  if (intakeFileName) intakeFileName.textContent = company.source_filename || "";
  renderIntakeFeatures([]);
  showUnknown(feature, farm.bbox.slice(), {
    inputMethod: "upload_pdf",
    filename: company.source_filename,
    name: farm.name,
    properties: feature.properties,
    keepFarm: true
  });
  if (intakeNote) intakeNote.textContent = "Loaded " + farm.name + ". Press Check this plot to run it.";
}

function importCompanyPdf(file) {
  if (intakeNote) intakeNote.textContent = "Reading mills from " + file.name + "…";
  file.arrayBuffer().then(function (buffer) {
    return fetch("/api/companies/import", {
      method: "POST",
      headers: {
        "Content-Type": "application/pdf",
        "X-Filename": encodeURIComponent(file.name)
      },
      body: buffer
    });
  }).then(function (response) {
    return response.json().then(function (body) {
      if (!response.ok) throw new Error(body.error || "Could not read that PDF.");
      return body;
    });
  }).then(function (body) {
    state.openCompanies[body.id] = true;
    let note = "Saved " + (body.mill_count || 0) + " mills under " + body.name + ".";
    if (body.unresolved_count) note += " " + body.unresolved_count + " had no coordinates.";
    note += " Click a mill to load it. Analysis does not start on its own.";
    if (intakeNote) intakeNote.textContent = note;
    if (intakeFileName) intakeFileName.textContent = file.name;
    return loadCompanies();
  }).catch(function (error) {
    if (intakeNote) intakeNote.textContent = error.message || "Could not read that PDF.";
  });
}

loadCompanies();

// The case file: one plot's record laid out as numbered exhibits.

(async function () {
  const C = window.OpenCanopy;
  const { evidence, verdict } = await C.loadPlot();
  const scenes = evidence.scenes.slice().sort(function (a, b) { return a.year - b.year; });
  const byYear = {};
  scenes.forEach(function (scene) { byYear[scene.year] = scene; });
  const lossByYear = evidence.hansen.loss_share_by_year;
  const plotHa = C.bboxHectares(evidence.bbox);
  const postShare = evidence.hansen.loss_share_post_cutoff;
  const lastLossYear = 2024;

  // ---------- Masthead and finding ----------

  document.getElementById("coords").textContent =
    C.formatCoord(evidence.lat, "N", "S") + ", " + C.formatCoord(evidence.lon, "E", "W");
  document.getElementById("area").textContent = Math.round(plotHa).toLocaleString() + " ha";

  const peakYear = Object.keys(lossByYear).reduce(function (best, year) {
    return Number(year) > C.CUTOFF_YEAR && lossByYear[year] > (lossByYear[best] || 0) ? year : best;
  }, null);
  const peakOfPost = peakYear ? lossByYear[peakYear] / postShare : 0;
  if (peakYear) {
    document.getElementById("chip-when").textContent = peakYear;
    document.getElementById("chip-when-sub").textContent =
      C.percent(lossByYear[peakYear]) + " of the plot lost that year. Hansen loss ends " + lastLossYear + ".";
  }
  const confirmed = verdict.boxes.filter(function (b) { return b.confirmed; }).length;
  document.getElementById("chip-check").textContent =
    confirmed + " confirmed · " + (verdict.boxes.length - confirmed) + " inconclusive";

  // A fingerprint of the evidence file, so a printed pack can be matched to its data.
  (async function () {
    const el = document.getElementById("fingerprint");
    try {
      const text = await (await fetch("../data/plots/kalbar1/evidence.json", { cache: "no-store" })).text();
      const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
      const hex = Array.from(new Uint8Array(digest)).map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
      el.textContent = "sha256 " + hex.slice(0, 12);
    } catch (error) {
      el.textContent = "sha256 unavailable offline";
    }
  })();

  document.getElementById("print").addEventListener("click", function () { window.print(); });
  document.getElementById("copy").addEventListener("click", async function (event) {
    const button = event.currentTarget;
    try {
      await navigator.clipboard.writeText(location.href);
      button.textContent = "Link copied";
    } catch (error) {
      button.textContent = "Copy failed";
    }
    setTimeout(function () { button.textContent = "Copy link"; }, 1800);
  });

  // ---------- Locator ----------

  if (window.maplibregl) {
    const [w, s, e, n] = evidence.bbox;
    const map = new maplibregl.Map({
      container: "locator",
      interactive: false,
      attributionControl: { compact: true },
      center: [evidence.lon, evidence.lat],
      zoom: 5.2,
      style: {
        version: 8,
        sources: {
          imagery: {
            type: "raster",
            tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
            tileSize: 256,
            attribution: "Esri World Imagery"
          },
          plot: {
            type: "geojson",
            data: { type: "Feature", geometry: { type: "Polygon", coordinates: [[[w, s], [e, s], [e, n], [w, n], [w, s]]] } }
          },
          pin: {
            type: "geojson",
            data: { type: "Feature", geometry: { type: "Point", coordinates: [evidence.lon, evidence.lat] } }
          }
        },
        layers: [
          { id: "imagery", type: "raster", source: "imagery", paint: { "raster-saturation": -0.35 } },
          { id: "halo", type: "circle", source: "pin", paint: { "circle-radius": 16, "circle-color": "#ec4c2e", "circle-opacity": 0.22 } },
          { id: "dot", type: "circle", source: "pin", paint: { "circle-radius": 4.5, "circle-color": "#ec4c2e", "circle-stroke-color": "#f6f4ec", "circle-stroke-width": 1.5 } },
          { id: "box", type: "line", source: "plot", paint: { "line-color": "#f6f4ec", "line-width": 1.5 } }
        ]
      }
    });
    map.on("error", function () {});
  }

  // ---------- E1: viewer ----------

  const viewer = document.querySelector(".viewer");
  const frame = document.getElementById("viewer");
  const imgBefore = document.getElementById("img-before");
  const imgAfter = document.getElementById("img-after");
  const labelBefore = document.getElementById("label-before");
  const labelAfter = document.getElementById("label-after");
  const prov = document.getElementById("prov");
  const wipe = document.getElementById("wipe");
  const lossCanvas = document.getElementById("viewer-loss");
  const loss = new C.LossLayer(lossCanvas);
  const toggles = {
    loss: document.getElementById("t-loss"),
    old: document.getElementById("t-old"),
    regions: document.getElementById("t-regions"),
    outline: document.getElementById("t-outline")
  };

  const regionLayer = C.regionSvg(verdict.boxes, { id: "case" });
  frame.insertBefore(regionLayer, frame.querySelector(".side-label"));

  const state = { before: scenes[0].year, after: scenes[scenes.length - 1].year, mode: "compare" };

  function describe(scene) {
    return C.formatDate(scene.date) + ", Sentinel-2 L2A, cloud " + scene.cloud_cover.toFixed(1) + "%";
  }

  function render() {
    const before = byYear[state.before];
    const after = byYear[state.after];
    imgBefore.src = C.sceneUrl(before);
    imgAfter.src = C.sceneUrl(after);
    imgBefore.alt = "The plot on " + describe(before);
    imgAfter.alt = "The plot on " + describe(after);
    labelBefore.textContent = C.formatDate(before.date);
    labelAfter.textContent = C.formatDate(after.date);

    const lossYear = Math.min(state.after, lastLossYear);
    loss.ready.then(function () {
      loss.paint(lossYear, 1, { post: toggles.loss.checked, pre: toggles.old.checked });
    });
    lossCanvas.style.opacity = toggles.loss.checked || toggles.old.checked ? 0.72 : 0;
    regionLayer.style.opacity = toggles.regions.checked ? 1 : 0;
    viewer.dataset.outline = toggles.outline.checked ? "on" : "off";
    viewer.dataset.mode = state.mode;

    const shown = state.mode === "single" ? [after] : [before, after];
    prov.innerHTML = shown.map(function (scene) {
      return "<span><b>" + scene.year + "</b> " + scene.scene_id.split("_").slice(0, 3).join("_") +
        " · cloud " + scene.cloud_cover.toFixed(1) + "% · NDVI " + scene.ndvi_mean.toFixed(2) + "</span>";
    }).join("") +
      (toggles.loss.checked ? "<span>Loss overlay: Hansen GFC v1.12, through " + lossYear + "</span>" : "");

    document.querySelectorAll(".reel").forEach(function (reel) {
      const year = Number(reel.dataset.year);
      reel.setAttribute("aria-pressed", String(year === state.after));
      reel.classList.toggle("is-before", state.mode !== "single" && year === state.before);
    });
  }

  function setSplit(value) {
    frame.style.setProperty("--split", value + "%");
  }

  wipe.addEventListener("input", function () { setSplit(wipe.value); });
  setSplit(wipe.value);

  Object.values(toggles).forEach(function (input) { input.addEventListener("change", render); });

  document.querySelectorAll(".seg button").forEach(function (button) {
    button.addEventListener("click", function () { setMode(button.dataset.mode); });
  });

  function setMode(mode) {
    state.mode = mode;
    document.querySelectorAll(".seg button").forEach(function (b) {
      b.setAttribute("aria-selected", String(b.dataset.mode === mode));
    });
    render();
  }

  // Filmstrip: one thumbnail per scene, the loss bar for that year beneath it.
  const rail = document.getElementById("rail");
  const maxShare = Math.max.apply(null, scenes.map(function (s) { return lossByYear[s.year] || 0; }).concat([0.01]));
  scenes.forEach(function (scene) {
    const share = lossByYear[scene.year] || 0;
    const button = document.createElement("button");
    button.type = "button";
    button.className = "reel";
    button.dataset.year = scene.year;
    button.setAttribute("aria-label", scene.year + " scene. " +
      (scene.year > lastLossYear ? "No loss data for this year." : C.percent(share, 1) + " of the plot lost that year."));
    const noData = scene.year > lastLossYear;
    button.innerHTML =
      '<span class="reel-thumb" style="background-image:url(\'' + C.sceneUrl(scene) + '\')"></span>' +
      '<span class="reel-bar' + (scene.year <= C.CUTOFF_YEAR ? " pre" : "") + '">' +
        (noData ? "" : '<i style="height:' + Math.max(2, (share / maxShare) * 100) + '%"></i>') + "</span>" +
      '<span class="reel-meta"><b>' + scene.year + "</b><span>" +
        (noData ? "no data" : C.percent(share, share > 0 && share < 0.01 ? 2 : 0)) + "</span></span>";
    button.addEventListener("click", function (event) {
      if (event.shiftKey && scene.year < state.after) state.before = scene.year;
      else state.after = scene.year;
      if (state.before >= state.after) state.before = scenes[0].year;
      render();
    });
    rail.appendChild(button);
  });

  // Dashed cutoff rule between 2020 and 2021 in the filmstrip.
  const cutIndex = scenes.findIndex(function (s) { return s.year > C.CUTOFF_YEAR; });
  if (cutIndex > 0) {
    const rule = document.createElement("div");
    rule.className = "cutoff-rule";
    rule.style.left = "calc(" + (cutIndex / scenes.length) * 100 + "% - 4px)";
    rule.innerHTML = "<span>31 Dec 2020</span>";
    rail.appendChild(rule);
  }

  document.addEventListener("keydown", function (event) {
    if (event.target.closest("input, textarea, select") && event.target !== wipe) return;
    if (event.key === "f" || event.key === "F") {
      setMode(state.mode === "flicker" ? "compare" : "flicker");
      return;
    }
    if (event.target === wipe) return;
    const years = scenes.map(function (s) { return s.year; });
    const index = years.indexOf(state.after);
    if (event.key === "ArrowRight" && index < years.length - 1) state.after = years[index + 1];
    else if (event.key === "ArrowLeft" && index > 0) state.after = years[index - 1];
    else return;
    event.preventDefault();
    if (state.before >= state.after) state.before = years[0];
    render();
  });

  render();

  // ---------- E2: loss by year ----------

  const postHa = Math.round(plotHa * postShare);
  document.getElementById("stat-ha").textContent = postHa.toLocaleString();
  document.getElementById("stat-share").textContent =
    C.percent(postShare, 1) + " of the plot" + (peakYear ? " · " + Math.round(peakOfPost * 100) + "% of it in " + peakYear : "");

  const chart = document.getElementById("chart");
  const chartMax = Math.max.apply(null, Object.values(lossByYear).concat([0.001]));
  for (let year = 2001; year <= lastLossYear; year++) {
    const share = lossByYear[year] || 0;
    const col = document.createElement("div");
    col.className = "col" + (year > C.CUTOFF_YEAR ? " post" : "");
    const height = share ? Math.max(1.5, (share / chartMax) * 100) : 0;
    col.innerHTML = '<i style="height:' + height + '%"></i>' +
      (share / chartMax > 0.2 ? '<span class="val" style="bottom:' + height + '%">' + C.percent(share, 1) + "</span>" : "") +
      '<span class="yr">' + "’" + String(year).slice(2) + "</span>";
    chart.appendChild(col);
  }
  const cut = document.createElement("div");
  cut.className = "cut";
  cut.style.left = "calc(" + ((C.CUTOFF_YEAR - 2000) / (lastLossYear - 2000)) * 100 + "% - 1px)";
  cut.innerHTML = "<span>Cutoff</span>";
  chart.appendChild(cut);
  chart.setAttribute("aria-label", "Bar chart of yearly loss. Post-2020 loss totals " + C.percent(postShare, 1) +
    " of the plot" + (peakYear ? ", almost all of it in " + peakYear : "") + ". Loss before 2021 is negligible.");

  // ---------- E3: region cards ----------

  const latest = scenes[scenes.length - 1];
  const cards = document.getElementById("region-cards");
  verdict.boxes.forEach(function (region, index) {
    const [x1, y1, x2, y2] = region.box;
    const w = x2 - x1;
    const h = y2 - y1;
    const side = Math.min(1000, Math.max(w, h) * 1.15);
    const cx = Math.min(Math.max((x1 + x2) / 2, side / 2), 1000 - side / 2);
    const cy = Math.min(Math.max((y1 + y2) / 2, side / 2), 1000 - side / 2);
    const pos = function (c) { return side >= 1000 ? 50 : ((c - side / 2) / (1000 - side)) * 100; };
    const name = String.fromCharCode(65 + index);
    const ha = Math.round(plotHa * (w * h) / 1e6);
    const card = document.createElement("article");
    card.className = "region-card " + (region.confirmed ? "confirmed" : "inconclusive");
    card.innerHTML =
      '<div class="crop" role="img" aria-label="Region ' + name + ' in the ' + latest.year + ' scene" style="' +
        "background-image:url('" + C.sceneUrl(latest) + "');" +
        "background-size:" + (1000 / side) * 100 + "%;" +
        "background-position:" + pos(cx) + "% " + pos(cy) + '%"></div>' +
      "<div>" +
        "<h4>Region " + name + ' <span class="tag ' + (region.confirmed ? "confirmed" : "inconclusive") + '">' +
          (region.confirmed ? "Confirmed" : "Inconclusive") + "</span></h4>" +
        "<dl>" +
          "<dt>Area</dt><dd>≈ " + ha.toLocaleString() + " ha</dd>" +
          "<dt>Lost after 2020</dt><dd>" + C.percent(region.post_cutoff_loss_share, 1) + "</dd>" +
          "<dt>Model</dt><dd>converted land</dd>" +
        "</dl>" +
        '<div class="share-wrap"><div class="share-bar"><i style="width:' + region.post_cutoff_loss_share * 100 + '%"></i><b></b></div></div>' +
        "<p>" + (region.confirmed
          ? "The loss map agrees with the model: substantial clearing inside this region after the cutoff."
          : "The model marked it, but too little of it was lost after 2020 to confirm. A person should look.") + "</p>" +
      "</div>";
    cards.appendChild(card);
  });

  // ---------- E5: provenance ----------

  const tbody = document.querySelector("#prov-table tbody");
  scenes.forEach(function (scene) {
    const tr = document.createElement("tr");
    if (scene.year > C.CUTOFF_YEAR) tr.className = "post";
    tr.innerHTML = "<td>" + scene.date + "</td><td>" + scene.scene_id + "</td><td>" +
      scene.cloud_cover.toFixed(1) + "%</td><td>" + scene.ndvi_mean.toFixed(3) + "</td>";
    tbody.appendChild(tr);
  });
})();

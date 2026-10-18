(function () {
  const params = new URLSearchParams(window.location.search);
  const plot = (params.get("plot") || "kalbar1").replace(/[^a-zA-Z0-9_\-.]/g, "");

  const app = document.getElementById("app");
  const errorEl = document.getElementById("error");
  const peakToggle = document.getElementById("peak-toggle");
  const slider = document.getElementById("slider");
  const beforeWrap = document.getElementById("slide-before-wrap");
  const handle = document.getElementById("slide-handle");
  const imgBefore = document.getElementById("slide-before");
  const imgAfter = document.getElementById("slide-after");
  const labelLeft = document.getElementById("label-left");
  const labelRight = document.getElementById("label-right");
  const peakCard = document.getElementById("peak-card");
  const peakImg = document.getElementById("peak-img");
  const peakTitle = document.getElementById("peak-title");
  const peakCopy = document.getElementById("peak-copy");
  const filmFrame = document.getElementById("film-frame");
  const filmDate = document.getElementById("film-date");
  const filmPlay = document.getElementById("film-play");
  const yearRail = document.getElementById("year-rail");
  const timelapse = document.getElementById("timelapse");
  const datingFacts = document.getElementById("dating-facts");
  const datingNote = document.getElementById("dating-note");

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  let audit = null;
  let scenes = [];
  let filmIndex = 0;
  let filmTimer = null;
  let compareMode = "after"; // after | peak

  function formatDate(iso) {
    const parts = String(iso || "").split("-");
    if (parts.length !== 3) return iso || "";
    const day = Number(parts[2]);
    const month = MONTHS[Number(parts[1]) - 1];
    if (!month || !Number.isFinite(day)) return iso;
    return day + " " + month + " " + parts[0];
  }

  function pct(value) {
    if (typeof value !== "number" || !Number.isFinite(value)) return "—";
    return (value * 100).toFixed(value >= 0.1 ? 1 : 2) + "%";
  }

  function showError(message) {
    errorEl.hidden = false;
    errorEl.textContent = message;
    app.hidden = true;
  }

  function setSlider(percent) {
    const value = Math.max(2, Math.min(98, percent));
    beforeWrap.style.width = value + "%";
    handle.style.left = value + "%";
    handle.setAttribute("aria-valuenow", String(Math.round(value)));
  }

  function bindSlider() {
    let dragging = false;

    function fromEvent(event) {
      const rect = slider.getBoundingClientRect();
      const clientX = event.touches ? event.touches[0].clientX : event.clientX;
      return ((clientX - rect.left) / rect.width) * 100;
    }

    function start(event) {
      dragging = true;
      setSlider(fromEvent(event));
      event.preventDefault();
    }
    function move(event) {
      if (!dragging) return;
      setSlider(fromEvent(event));
    }
    function end() {
      dragging = false;
    }

    slider.addEventListener("pointerdown", start);
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", end);
    handle.addEventListener("keydown", function (event) {
      const now = Number(handle.getAttribute("aria-valuenow") || "50");
      if (event.key === "ArrowLeft") setSlider(now - 3);
      if (event.key === "ArrowRight") setSlider(now + 3);
    });
    setSlider(50);
  }

  function applyCompareMode() {
    const before = audit.imagery.before;
    const after = audit.imagery.after;
    const peak = audit.imagery.peak;
    const usePeak = peakToggle.checked && peak && peak.url;

    imgBefore.src = before.url;
    imgBefore.alt = "Sentinel-2 " + formatDate(before.date);
    labelLeft.textContent = formatDate(before.date) || "Before cutoff";

    if (usePeak) {
      compareMode = "peak";
      imgAfter.src = peak.url;
      imgAfter.alt = "Sentinel-2 peak loss " + (peak.year || "");
      labelRight.textContent = "Peak loss · " + (peak.year || formatDate(peak.scene && peak.scene.date));
      peakCard.hidden = false;
      peakImg.src = after.url;
      peakTitle.textContent = "Latest frame · " + formatDate(after.date);
      peakCopy.textContent =
        "Slider compares the pre-cutoff scene to the peak-loss year (" +
        peak.year +
        ", " +
        pct(peak.loss_share) +
        " of the plot). The thumbnail is the latest scene (" +
        formatDate(after.date) +
        ").";
    } else {
      compareMode = "after";
      imgAfter.src = after.url;
      imgAfter.alt = "Sentinel-2 " + formatDate(after.date);
      labelRight.textContent = formatDate(after.date) || "Latest";
      if (peak && peak.url) {
        peakCard.hidden = false;
        peakImg.src = peak.url;
        peakTitle.textContent = "Peak loss · " + peak.year;
        peakCopy.textContent =
          pct(peak.loss_share) +
          " of the plot was lost in " +
          peak.year +
          ". Turn on the toggle to put this year on the slider.";
      } else {
        peakCard.hidden = true;
      }
    }
  }

  function setFilm(index) {
    if (!scenes.length) return;
    filmIndex = Math.max(0, Math.min(scenes.length - 1, index));
    const scene = scenes[filmIndex];
    filmFrame.src = scene.url;
    filmDate.textContent = formatDate(scene.date) + (scene.year ? " · " + scene.year : "");
    yearRail.querySelectorAll("button").forEach(function (btn) {
      btn.classList.toggle("active", Number(btn.dataset.index) === filmIndex);
    });
  }

  function stopFilm() {
    if (filmTimer) {
      clearInterval(filmTimer);
      filmTimer = null;
    }
    filmPlay.textContent = "Play years";
  }

  function playFilm() {
    if (filmTimer) {
      stopFilm();
      return;
    }
    filmPlay.textContent = "Pause";
    filmTimer = setInterval(function () {
      if (filmIndex >= scenes.length - 1) {
        stopFilm();
        return;
      }
      setFilm(filmIndex + 1);
    }, 900);
  }

  function renderYearRail() {
    yearRail.replaceChildren();
    const byYear = (audit.dating && audit.dating.by_year) || {};
    const peakYear = audit.dating && audit.dating.peak_loss_year;
    const maxShare = Math.max.apply(
      null,
      Object.keys(byYear).map(function (y) { return byYear[y]; }).concat([0.01])
    );

    scenes.forEach(function (scene, index) {
      const year = scene.year || Number(String(scene.date || "").slice(0, 4));
      const share = byYear[String(year)] || 0;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.dataset.index = String(index);
      if (year === 2020) btn.classList.add("cutoff");
      if (year === peakYear) btn.classList.add("peak");
      const bar = document.createElement("span");
      bar.className = "bar";
      bar.style.height = Math.max(2, Math.round((share / maxShare) * 56)) + "px";
      const yr = document.createElement("span");
      yr.className = "yr";
      yr.textContent = year === 2020 ? "’20|" : String(year).slice(2);
      btn.title = year + (share ? " · loss " + pct(share) : "");
      btn.append(bar, yr);
      btn.addEventListener("click", function () {
        stopFilm();
        setFilm(index);
      });
      yearRail.append(btn);
    });
  }

  function renderDating() {
    const d = audit.dating || {};
    datingNote.textContent = d.note || "";
    datingFacts.replaceChildren();

    const facts = [
      ["EUDR cutoff", "31 Dec 2020"],
      ["First meaningful post-cutoff loss", d.first_clearing_year ? String(d.first_clearing_year) : "None detected"],
      ["Peak loss year", d.peak_loss_year ? d.peak_loss_year + " · " + pct(d.peak_loss_share) : "None detected"],
      ["Post-cutoff loss (plot)", pct(d.post_cutoff_loss_share)],
      ["Relative to cutoff", d.relative_to_cutoff === "after" ? "After — clearing begins after the cutoff" : "No clear post-cutoff clearing"]
    ];
    facts.forEach(function (pair) {
      const li = document.createElement("li");
      li.innerHTML = "<strong>" + pair[0] + ":</strong> " + pair[1];
      datingFacts.append(li);
    });

    if (audit.imagery.timelapse_url) {
      timelapse.hidden = false;
      timelapse.src = audit.imagery.timelapse_url;
    } else {
      timelapse.hidden = true;
    }

    const startYear = d.first_clearing_year || d.peak_loss_year;
    let startIndex = 0;
    if (startYear) {
      const found = scenes.findIndex(function (s) {
        return (s.year || Number(String(s.date || "").slice(0, 4))) === startYear;
      });
      if (found >= 0) startIndex = Math.max(0, found - 1);
    }
    setFilm(startIndex);
    renderYearRail();
    setFilm(startIndex);
  }

  function renderVerdict() {
    const band = document.getElementById("verdict-band");
    const raw = (audit.verdict && audit.verdict.raw) || "";
    band.classList.remove("ok", "review");
    if (/CONSISTENT/i.test(raw)) band.classList.add("ok");
    else if (/INCONCLUSIVE/i.test(raw)) band.classList.add("review");

    const plot = audit.plot || {};
    document.getElementById("plot-label").textContent =
      (plot.name || "plot") +
      (plot.lat != null && plot.lon != null ? " · " + plot.lat + ", " + plot.lon : "");
    document.getElementById("verdict-summary").textContent = audit.verdict.summary;
    document.getElementById("verdict-disclaimer").textContent = audit.verdict.disclaimer;
  }

  function renderPerception() {
    const p = audit.perceptron || {};
    document.getElementById("perception-copy").textContent =
      p.description || "No Perceptron description on this plot yet.";
    const list = document.getElementById("box-list");
    list.replaceChildren();
    (p.boxes || []).forEach(function (box, i) {
      const li = document.createElement("li");
      const confirmed = box.confirmed === true;
      const inconclusive = box.confirmed === false;
      li.className = confirmed ? "ok" : inconclusive ? "maybe" : "";
      const share = typeof box.post_cutoff_loss_share === "number"
        ? pct(box.post_cutoff_loss_share) + " post-cutoff loss inside box"
        : "loss share not scored";
      li.textContent =
        "Region " + (i + 1) + " · " +
        (confirmed ? "Confirmed" : inconclusive ? "Inconclusive" : "Marked") +
        " · " + share;
      list.append(li);
    });
  }

  function renderMethod() {
    const dl = document.getElementById("method-list");
    dl.replaceChildren();
    const m = audit.methodology || {};
    const rows = [
      ["Cutoff", audit.cutoff || "2020-12-31"],
      ["Baseline", m.baseline],
      ["Imagery", m.imagery],
      ["Vision", m.vision],
      ["Dating", (audit.dating && audit.dating.method) || "hansen_annual_loss"],
      ["Share URL", location.origin + (audit.share_path || ("/web/results.html?plot=" + plot))]
    ];
    rows.forEach(function (row) {
      if (!row[1]) return;
      const dt = document.createElement("dt");
      dt.textContent = row[0];
      const dd = document.createElement("dd");
      dd.textContent = row[1];
      dl.append(dt, dd);
    });
  }

  function downloadJson() {
    const blob = new Blob([JSON.stringify(audit, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "clearcut-" + plot + "-audit.json";
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function downloadPdf() {
    // Print stylesheet turns the results page into a clean PDF via the browser.
    window.print();
  }

  async function copyLink() {
    const url = location.origin + "/web/results.html?plot=" + encodeURIComponent(plot);
    try {
      await navigator.clipboard.writeText(url);
      const btn = document.getElementById("copy-link");
      const prev = btn.textContent;
      btn.textContent = "Copied";
      setTimeout(function () { btn.textContent = prev; }, 1400);
    } catch (err) {
      window.prompt("Copy share link", url);
    }
  }

  async function load() {
    document.getElementById("back-map").href = "/web/?plot=" + encodeURIComponent(plot);
    const response = await fetch("/api/audit/" + encodeURIComponent(plot));
    if (!response.ok) {
      const payload = await response.json().catch(function () { return {}; });
      throw new Error(payload.error || "Could not load audit for " + plot);
    }
    audit = await response.json();
    scenes = (audit.imagery && audit.imagery.scenes) || [];
    if (!scenes.length && audit.imagery) {
      scenes = [audit.imagery.before, audit.imagery.after].filter(Boolean);
    }

    renderVerdict();
    applyCompareMode();
    renderDating();
    renderPerception();
    renderMethod();
    bindSlider();

    peakToggle.disabled = !(audit.imagery.peak && audit.imagery.peak.url);
    if (peakToggle.disabled) {
      peakToggle.closest("label").title = "No peak-loss year on this plot";
    }

    app.hidden = false;
    document.title = "Clearcut — " + plot;
  }

  peakToggle.addEventListener("change", applyCompareMode);
  filmPlay.addEventListener("click", playFilm);
  document.getElementById("copy-link").addEventListener("click", copyLink);
  document.getElementById("download-json").addEventListener("click", downloadJson);
  document.getElementById("download-pdf").addEventListener("click", downloadPdf);

  load().catch(function (err) {
    showError(err.message || String(err));
  });
})();

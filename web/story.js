// The landing story. One pinned frame; each text step sets which layers show.

(async function () {
  const C = window.OpenCanopy;
  const stage = document.querySelector(".stage");
  const frame = document.getElementById("stage-frame");
  const hudDate = document.getElementById("hud-date");
  const hudSource = document.getElementById("hud-source");
  const meterValue = document.getElementById("meter-value");
  const meterFill = document.getElementById("meter-fill");
  const yearRail = document.getElementById("story-years");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  const { evidence, verdict } = await C.loadPlot();
  const scenes = {};
  evidence.scenes.forEach(function (scene) { scenes[scene.year] = scene; });
  const plotHa = C.bboxHectares(evidence.bbox);
  const postHa = Math.round(plotHa * evidence.hansen.loss_share_post_cutoff);
  document.getElementById("step-ha").textContent = postHa.toLocaleString();
  document.getElementById("hero-coords").textContent =
    C.formatCoord(evidence.lat, "N", "S") + ", " + C.formatCoord(evidence.lon, "E", "W");

  frame.querySelectorAll("img.layer").forEach(function (img) {
    const scene = scenes[img.dataset.year];
    if (scene) img.src = C.sceneUrl(scene);
  });

  const loss = new C.LossLayer(document.getElementById("stage-loss"));
  const canvas = document.getElementById("stage-loss");

  frame.appendChild(C.regionSvg(verdict.boxes, { id: "story" }));
  verdict.boxes.forEach(function (region, index) {
    const label = document.createElement("div");
    label.className = "region-label";
    label.style.left = (region.box[0] / 10) + "%";
    label.style.top = (region.box[1] / 10) + "%";
    if (region.box[0] > 700) {
      label.style.left = "auto";
      label.style.right = (100 - region.box[2] / 10) + "%";
    }
    const name = String.fromCharCode(65 + index);
    label.innerHTML = region.confirmed
      ? '<span class="tag confirmed">' + name + " · Confirmed " + C.percent(region.post_cutoff_loss_share) + "</span>"
      : '<span class="tag inconclusive">' + name + " · Inconclusive " + C.percent(region.post_cutoff_loss_share) + "</span>";
    frame.appendChild(label);
  });

  const railYears = [2019, 2020, 2021, 2022, 2023, 2024, 2025, 2026];
  railYears.forEach(function (year) {
    const li = document.createElement("li");
    li.textContent = year;
    li.dataset.year = year;
    if (year === C.CUTOFF_YEAR) li.classList.add("cutoff");
    yearRail.appendChild(li);
  });

  // What each step shows. `loss` is the opacity of the loss layer.
  const STEPS = {
    submitted: { year: 2026, loss: 0, callouts: [], meter: 0 },
    2019: { year: 2019, loss: 0, callouts: ["river"], meter: 0 },
    cutoff: { year: 2020, loss: 0, callouts: [], stamp: true, meter: 0 },
    loss: { year: 2023, loss: 1, grow: true, callouts: [], meter: 1 },
    2026: { year: 2026, loss: 0.38, callouts: ["roads"], meter: 1 },
    regions: { year: 2026, loss: 0, regions: true, callouts: [], meter: 1 },
    verdict: { year: 2026, loss: 0, regions: true, callouts: [], meter: 1 }
  };

  let growFrame = 0;
  let meterTimer = 0;

  function setMeter(share, animate) {
    const ha = Math.round(plotHa * share);
    meterFill.style.width = (share / 0.25) * 100 + "%";
    cancelAnimationFrame(meterTimer);
    if (!animate || reduceMotion) {
      meterValue.textContent = ha.toLocaleString() + " ha";
      return;
    }
    const start = performance.now();
    const from = parseInt(meterValue.textContent.replace(/\D/g, ""), 10) || 0;
    (function tick(now) {
      const t = Math.min(1, (now - start) / 1400);
      const eased = 1 - Math.pow(1 - t, 3);
      meterValue.textContent = Math.round(from + (ha - from) * eased).toLocaleString() + " ha";
      if (t < 1) meterTimer = requestAnimationFrame(tick);
    })(start);
  }

  function growLoss() {
    cancelAnimationFrame(growFrame);
    if (reduceMotion) { loss.paint(2024); return; }
    const start = performance.now();
    (function tick(now) {
      const t = Math.min(1, (now - start) / 1600);
      loss.paint(2023, t);
      if (t < 1) growFrame = requestAnimationFrame(tick);
      else loss.paint(2024);
    })(start);
  }

  async function show(name) {
    const step = STEPS[name];
    if (!step) return;
    await loss.ready;
    frame.querySelectorAll("img.layer").forEach(function (img) {
      img.classList.toggle("on", Number(img.dataset.year) === step.year);
    });
    const scene = scenes[step.year];
    if (scene) {
      hudDate.textContent = C.formatDate(scene.date);
      hudSource.textContent = "Sentinel-2 L2A · cloud " + Math.round(scene.cloud_cover) + "%" +
        (step.loss ? " · Hansen loss overlay" : "");
    }
    canvas.style.opacity = step.loss;
    if (step.grow) growLoss();
    else if (step.loss) { cancelAnimationFrame(growFrame); loss.paint(2024); }
    stage.dataset.stamp = step.stamp ? "on" : "off";
    stage.dataset.regions = step.regions ? "on" : "off";
    frame.querySelectorAll(".callout").forEach(function (el) {
      el.classList.toggle("on", step.callouts.indexOf(el.dataset.for) !== -1);
    });
    yearRail.querySelectorAll("li").forEach(function (li) {
      li.classList.toggle("active", Number(li.dataset.year) === step.year);
    });
    setMeter(step.meter ? evidence.hansen.loss_share_post_cutoff : 0, step.grow);
  }

  const steps = Array.from(document.querySelectorAll(".step"));
  let current = null;
  const observer = new IntersectionObserver(function (entries) {
    entries.forEach(function (entry) {
      if (!entry.isIntersecting) return;
      const name = entry.target.dataset.step;
      if (name === current) return;
      current = name;
      steps.forEach(function (s) { s.classList.toggle("active", s === entry.target); });
      show(name);
    });
  }, { rootMargin: "-45% 0px -45% 0px" });
  steps.forEach(function (s) { observer.observe(s); });

  const art = new C.LossLayer(document.getElementById("art-loss"));
  art.ready.then(function () { art.paint(2024); });

  show("submitted");
  steps[0].classList.add("active");
})();

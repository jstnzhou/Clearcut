// Shared by the landing story and the case file: load one plot's record and
// paint its Hansen loss years onto a canvas for any chosen year.

const PLOT_URL = "../data/plots/kalbar1";
const CUTOFF_YEAR = 2020;
const LOSS_RASTER = "assets/loss_years.png";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Sample record so the pages still render when opened without the server.
const FALLBACK = {
  evidence: {
    lat: -0.78, lon: 110.08, bbox: [110.05, -0.81, 110.11, -0.75],
    hansen: {
      loss_share_by_year: { 2014: 0.0005, 2016: 0.0004, 2020: 0.0002, 2021: 0.0001, 2022: 0.0001, 2023: 0.1976, 2024: 0.0009 },
      loss_share_post_cutoff: 0.1987
    },
    scenes: [
      { date: "2019-09-14", year: 2019, cloud_cover: 3.0, ndvi_mean: 0.879, file: "scene_2019.png", scene_id: "S2B_MSIL2A_20190914T024549" },
      { date: "2020-09-18", year: 2020, cloud_cover: 4.6, ndvi_mean: 0.865, file: "scene_2020.png", scene_id: "S2B_MSIL2A_20200918T024549" },
      { date: "2021-02-15", year: 2021, cloud_cover: 6.0, ndvi_mean: 0.872, file: "scene_2021.png", scene_id: "S2B_MSIL2A_20210215T024809" },
      { date: "2022-08-09", year: 2022, cloud_cover: 5.0, ndvi_mean: 0.696, file: "scene_2022.png", scene_id: "S2B_MSIL2A_20220809T024529" },
      { date: "2023-08-29", year: 2023, cloud_cover: 0.04, ndvi_mean: 0.762, file: "scene_2023.png", scene_id: "S2A_MSIL2A_20230829T024531" },
      { date: "2024-09-07", year: 2024, cloud_cover: 6.7, ndvi_mean: 0.683, file: "scene_2024.png", scene_id: "S2B_MSIL2A_20240907T024529" },
      { date: "2025-07-29", year: 2025, cloud_cover: 1.5, ndvi_mean: 0.833, file: "scene_2025.png", scene_id: "S2C_MSIL2A_20250729T024611" },
      { date: "2026-08-08", year: 2026, cloud_cover: 0.01, ndvi_mean: 0.8, file: "scene_2026.png", scene_id: "S2B_MSIL2A_20260808T024529" }
    ]
  },
  verdict: {
    boxes: [
      { box: [0, 417, 1000, 866], post_cutoff_loss_share: 0.432, confirmed: true },
      { box: [805, 903, 1000, 999], post_cutoff_loss_share: 0.139, confirmed: false }
    ],
    plot_post_cutoff_loss_share: 0.1987
  }
};

async function fetchJson(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(url + " " + response.status);
  return response.json();
}

async function loadPlot() {
  try {
    const [evidence, verdict] = await Promise.all([
      fetchJson(PLOT_URL + "/evidence.json"),
      fetchJson(PLOT_URL + "/verdict.json")
    ]);
    return { evidence, verdict };
  } catch (error) {
    return FALLBACK;
  }
}

function sceneUrl(scene) {
  return PLOT_URL + "/" + scene.file;
}

function formatDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  return d + " " + MONTHS[m - 1] + " " + y;
}

function formatCoord(value, pos, neg) {
  return Math.abs(value).toFixed(4) + "° " + (value < 0 ? neg : pos);
}

function percent(share, digits) {
  return (share * 100).toFixed(digits === undefined ? 0 : digits) + "%";
}

// Ground area of the plot box in hectares (equirectangular, fine at this size).
function bboxHectares(bbox) {
  const midLat = ((bbox[1] + bbox[3]) / 2) * Math.PI / 180;
  const width = (bbox[2] - bbox[0]) * 111.32 * Math.cos(midLat);
  const height = (bbox[3] - bbox[1]) * 110.57;
  return width * height * 100;
}

// The loss raster stores (year - 2000) in red and 255 alpha where trees were lost.
class LossLayer {
  constructor(canvas) {
    this.canvas = canvas;
    this.ctx = canvas.getContext("2d");
    this.years = null;
    this.ready = this.load();
  }

  async load() {
    const img = new Image();
    img.src = LOSS_RASTER;
    await img.decode();
    this.canvas.width = img.naturalWidth;
    this.canvas.height = img.naturalHeight;
    const scratch = document.createElement("canvas");
    scratch.width = img.naturalWidth;
    scratch.height = img.naturalHeight;
    const sctx = scratch.getContext("2d", { willReadFrequently: true });
    sctx.drawImage(img, 0, 0);
    const data = sctx.getImageData(0, 0, img.naturalWidth, img.naturalHeight).data;
    this.years = new Uint8Array(img.naturalWidth * img.naturalHeight);
    for (let i = 0; i < this.years.length; i++) {
      if (data[i * 4 + 3] > 0) this.years[i] = data[i * 4];
    }
  }

  // Paint loss up to `year`. Post-cutoff loss is vermilion; loss before the cutoff is grey.
  // `reveal` (0-1) lets a story step grow the newest year in rather than pop it.
  // `layers` picks which side of the cutoff to draw; both by default.
  paint(year, reveal, layers) {
    const showPost = !layers || layers.post !== false;
    const showPre = !layers || layers.pre !== false;
    if (!this.years) return 0;
    const amount = reveal === undefined ? 1 : reveal;
    const image = this.ctx.createImageData(this.canvas.width, this.canvas.height);
    const px = image.data;
    let lost = 0;
    for (let i = 0; i < this.years.length; i++) {
      const y = this.years[i];
      if (!y) continue;
      const full = 2000 + y;
      if (full > year) continue;
      if (full === year && amount < 1 && hash(i) > amount) continue;
      const post = full > CUTOFF_YEAR;
      if (post ? !showPost : !showPre) continue;
      px[i * 4] = post ? 236 : 190;
      px[i * 4 + 1] = post ? 76 : 196;
      px[i * 4 + 2] = post ? 46 : 192;
      px[i * 4 + 3] = post ? 220 : 150;
      if (post) lost++;
    }
    this.ctx.putImageData(image, 0, 0);
    return lost / this.years.length;
  }
}

// Stable per-pixel noise so a partial reveal looks like loss spreading, not flicker.
function hash(i) {
  let x = (i * 2654435761) >>> 0;
  x ^= x >>> 16;
  x = Math.imul(x, 2246822507) >>> 0;
  x ^= x >>> 13;
  return (x >>> 0) / 4294967296;
}

// Region boxes from the vision model are in a 0-1000 frame over the current image.
function regionSvg(boxes, opts) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 1000 1000");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("class", "regions");
  svg.setAttribute("aria-hidden", "true");
  svg.innerHTML =
    '<defs><pattern id="' + opts.id + '-hatch" width="14" height="14" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">' +
    '<line x1="0" y1="0" x2="0" y2="14" class="hatch-line"/></pattern></defs>';
  boxes.forEach(function (region, index) {
    const [x1, y1, x2, y2] = region.box;
    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("x", x1);
    rect.setAttribute("y", y1);
    rect.setAttribute("width", x2 - x1);
    rect.setAttribute("height", y2 - y1);
    rect.setAttribute("class", "region " + (region.confirmed ? "confirmed" : "inconclusive"));
    rect.setAttribute("data-region", index);
    rect.setAttribute("vector-effect", "non-scaling-stroke");
    if (!region.confirmed) rect.setAttribute("fill", "url(#" + opts.id + "-hatch)");
    svg.appendChild(rect);
  });
  return svg;
}

window.OpenCanopy = {
  CUTOFF_YEAR, loadPlot, sceneUrl, formatDate, formatCoord, percent, bboxHectares, LossLayer, regionSvg
};

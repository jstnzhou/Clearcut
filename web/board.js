function trustClass(score) {
  if (score >= 0.7) return "flag-high";
  if (score >= 0.45) return "flag-mid";
  return "flag-low";
}

function plotHref(id) {
  return "./?plot=" + encodeURIComponent(id);
}

function paintBoard(container, data, options) {
  const onOpen = options && options.onOpen;
  const limit = options && options.limit;
  const plots = ((data && data.plots) || []).slice();
  const shown = typeof limit === "number" ? plots.slice(0, limit) : plots;
  container.replaceChildren();
  if (!shown.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.textContent = "No scored plots yet. Analyze a rectangle to add one.";
    container.append(empty);
    return;
  }
  const table = document.createElement("table");
  table.className = "board-table";
  const head = document.createElement("thead");
  const headRow = document.createElement("tr");
  ["Rank", "Location", "Least-trusted score", ""].forEach(function (label) {
    const th = document.createElement("th");
    th.textContent = label;
    headRow.append(th);
  });
  head.append(headRow);
  const body = document.createElement("tbody");
  shown.forEach(function (plot, index) {
    const row = document.createElement("tr");
    row.className = trustClass(plot.mean_score);
    const rank = document.createElement("td");
    rank.textContent = String(index + 1);
    const loc = document.createElement("td");
    const coordText = Number(plot.lat).toFixed(3) + ", " + Number(plot.lon).toFixed(3);
    const name = document.createElement("div");
    name.className = "board-name";
    name.textContent = plot.name || plot.id;
    loc.append(name);
    if ((plot.name || "") !== coordText) {
      const coords = document.createElement("div");
      coords.className = "board-coords";
      coords.textContent = coordText;
      loc.append(coords);
    }
    const score = document.createElement("td");
    score.className = "board-score";
    score.textContent = Number(plot.mean_score).toFixed(2);
    const action = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Open";
    button.addEventListener("click", function (event) {
      event.preventDefault();
      if (onOpen) onOpen(plot);
      else window.location.href = plotHref(plot.id);
    });
    action.append(button);
    row.append(rank, loc, score, action);
    if (onOpen) {
      row.style.cursor = "pointer";
      row.addEventListener("click", function (event) {
        if (event.target.closest("button")) return;
        onOpen(plot);
      });
    }
    body.append(row);
  });
  table.append(head, body);
  container.append(table);
  if (limit && plots.length > limit) {
    const more = document.createElement("p");
    more.className = "hint";
    more.textContent = plots.length - limit + " more on the full board.";
    container.append(more);
  }
}

function fetchBoard() {
  return fetch("/api/leaderboard").then(function (response) {
    if (!response.ok) throw new Error("Could not load the investigation board.");
    return response.json();
  });
}

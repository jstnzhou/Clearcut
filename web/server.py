"""Serve Clearcut, analyze a rectangle, and expose audit packs.

    .venv/bin/python web/server.py
    http://127.0.0.1:8765/web/
    http://127.0.0.1:8765/web/results.html?plot=kalbar1
"""
import json
import os
import subprocess
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "bin" / "python"
if not PYTHON.is_file():
    PYTHON = Path(sys.executable)
PORT = 8765
sys.path.insert(0, str(ROOT / "pipeline"))
from build_audit import build_audit  # noqa: E402


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/audit/"):
            return self._audit(path)
        return super().do_GET()

    def do_POST(self):
        if self.path != "/api/analyze":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
            bbox = [float(v) for v in body["bbox"]]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            self._json(400, {"error": "Send bbox as [minLon, minLat, maxLon, maxLat]."})
            return
        if len(bbox) != 4 or bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            self._json(400, {"error": "The rectangle is empty."})
            return
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        if width > 1 or height > 1:
            self._json(400, {"error": "Draw a smaller rectangle. This check covers about 1 degree."})
            return

        lat = (bbox[1] + bbox[3]) / 2
        lon = (bbox[0] + bbox[2]) / 2
        plot = ROOT / "data" / "plots" / f"box_{lat:.3f}_{lon:.3f}".replace("-", "m").replace(".", "p")
        plot.mkdir(parents=True, exist_ok=True)
        bbox_arg = ",".join(str(v) for v in bbox)
        env = os.environ.copy()
        steps = [
            [str(PYTHON), "pipeline/analyze_fast.py", "--bbox", bbox_arg, "--out", str(plot)],
        ]
        for cmd in steps:
            run = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
            if run.returncode != 0:
                detail = (run.stderr or run.stdout or "analysis failed").strip()[-800:]
                self._json(500, {"error": detail})
                return
        # Optional: rebuild audit.json for the new plot (Hansen dating may be absent).
        try:
            audit = build_audit(plot)
            with (plot / "audit.json").open("w") as f:
                json.dump(audit, f, indent=2)
                f.write("\n")
        except Exception:
            pass
        name = plot.relative_to(ROOT / "data" / "plots").as_posix()
        self._json(200, {
            "plot": name,
            "results": f"/web/results.html?plot={name}",
            "export": f"/api/audit/{name}/export",
        })

    def _audit(self, path: str):
        # /api/audit/<plot> or /api/audit/<plot>/export or /api/audit/<plot>/pdf
        rest = unquote(path[len("/api/audit/") :]).strip("/")
        if not rest:
            self._json(404, {"error": "Missing plot id."})
            return
        parts = rest.split("/")
        plot_id = parts[0]
        action = parts[1] if len(parts) > 1 else ""
        if "/" in plot_id or plot_id in (".", "..") or not plot_id:
            self._json(400, {"error": "Invalid plot id."})
            return
        plot_dir = (ROOT / "data" / "plots" / plot_id).resolve()
        plots_root = (ROOT / "data" / "plots").resolve()
        if not str(plot_dir).startswith(str(plots_root) + os.sep) and plot_dir != plots_root:
            self._json(400, {"error": "Invalid plot id."})
            return
        if not plot_dir.is_dir():
            self._json(404, {"error": f"No plot named {plot_id}."})
            return
        try:
            audit = build_audit(plot_dir)
        except Exception as exc:
            self._json(500, {"error": str(exc)})
            return

        if action in ("", "json"):
            self._json(200, audit)
            return
        if action == "export":
            raw = (json.dumps(audit, indent=2) + "\n").encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Disposition", f'attachment; filename="clearcut-{plot_id}-audit.json"')
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if action == "pdf":
            # Lightweight HTML report; browser "Download PDF" uses print CSS on results.html.
            # This endpoint offers a server-side printable snapshot for share bots / curl.
            html = self._pdf_html(audit).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Disposition", f'inline; filename="clearcut-{plot_id}.html"')
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
            return
        self._json(404, {"error": "Unknown audit action."})

    def _pdf_html(self, audit: dict) -> str:
        v = audit.get("verdict") or {}
        d = audit.get("dating") or {}
        img = audit.get("imagery") or {}
        before = (img.get("before") or {}).get("url") or ""
        after = (img.get("after") or {}).get("url") or ""
        peak = img.get("peak") or {}
        perc = audit.get("perceptron") or {}
        desc = (perc.get("description") or "").replace("<", "&lt;")
        summary = (v.get("summary") or "").replace("<", "&lt;")
        note = (d.get("note") or "").replace("<", "&lt;")
        return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Clearcut {audit.get("id")}</title>
<style>
body{{font-family:Georgia,serif;max-width:800px;margin:32px auto;padding:0 16px;color:#111;line-height:1.45}}
h1{{font-size:1.4rem}} img{{max-width:100%;border:1px solid #ccc}}
.row{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
.muted{{color:#555;font-size:0.95rem}} .label{{font-size:0.8rem;color:#666;text-transform:uppercase}}
</style></head><body>
<p class="label">Clearcut evidence pack · {audit.get("id")}</p>
<h1>{summary}</h1>
<p class="muted">{v.get("disclaimer")}</p>
<div class="row">
<figure><img src="{before}" alt="Before"><figcaption>Before</figcaption></figure>
<figure><img src="{after}" alt="After"><figcaption>After</figcaption></figure>
</div>
{f'<figure><img src="{peak.get("url")}" alt="Peak"><figcaption>Peak loss {peak.get("year")}</figcaption></figure>' if peak.get("url") else ""}
<h2>When was it cleared?</h2>
<p>{note}</p>
<p>First clearing year: {d.get("first_clearing_year")} · Peak: {d.get("peak_loss_year")} ({d.get("peak_loss_share")})</p>
<h2>What replaced the forest</h2>
<p class="muted">AI interpretation</p>
<p>{desc}</p>
<p class="muted">Share: {audit.get("share_path")}</p>
</body></html>"""

    def _json(self, code, payload):
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


if __name__ == "__main__":
    os.chdir(ROOT)
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"http://127.0.0.1:{PORT}/web/", flush=True)
    print(f"http://127.0.0.1:{PORT}/web/results.html?plot=kalbar1", flush=True)
    server.serve_forever()

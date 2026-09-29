"""Anxious Portal — minimal install/update server for PULSE.

Mirrors the standard agent-portal pattern:
  GET /install.sh      -> serves scripts/install.sh (with your REPO_URL baked)
  GET /install.ps1     -> serves pulse-agent/scripts/install.ps1
  GET /latest.json     -> {"version": "<latest tag>", "commit": "<sha>", "install_sh": "<url>/install.sh"}
  GET /               -> tiny landing page with install instructions

Deploy anywhere (VPS, Render, GitHub Pages + redirect). Point DNS:
  pulse-agent.anxiousresearchlab.com -> this server
Then friends install with:
  curl -fsSL https://pulse-agent.anxiousresearchlab.com/install.sh | bash

Run locally:  python3 portal/server.py  (serves on :8080)
"""
import http.server
import json
import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("PORT", "8080"))
PUBLIC_BASE = os.environ.get("PORTAL_PUBLIC_URL", "http://localhost:8080")
REPO_URL = os.environ.get(
    "PULSE_REPO_URL", "https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-.git"
)


def git(cmd):
    try:
        return subprocess.check_output(
            ["git"] + cmd, cwd=REPO_ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return ""


def latest_info():
    tag = git(["describe", "--tags", "--abbrev=0"]) or "v0.0.0-dev"
    sha = git(["rev-parse", "HEAD"]) or "unknown"
    return {
        "name": "PULSE Agent",
        "by": "Anxious Research Lab",
        "version": tag,
        "commit": sha,
        "repo": REPO_URL,
        "install_sh": f"{PUBLIC_BASE}/install.sh",
        "install_ps1": f"{PUBLIC_BASE}/install.ps1",
        "update_cmd": "pulse update",
    }


LANDING = """<!doctype html><html><head><meta charset=utf-8><title>PULSE Agent — Anxious Research Lab</title>
<style>body{font-family:system-ui;max-width:640px;margin:60px auto;padding:0 20px}pre{background:#111;color:#0f0;padding:14px;border-radius:8px;overflow:auto}</style>
</head><body><h1>☤ PULSE Agent</h1><p>by <b>Anxious Research Lab</b> — install:</p>
<pre>curl -fsSL {base}/install.sh | bash</pre>
<p>Windows (PowerShell):</p><pre>iex (irm {base}/install.ps1)</pre>
<p>Or grab <b>PULSE-Setup.dmg</b> from <a href="{repo}">GitHub releases</a> and run Install PULSE.app — dependencies install on your machine via the runtime.</p>
<p>Update anytime: <pre>pulse update</pre></p><p><a href="/latest.json">latest.json</a></p></body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, body: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(
                LANDING.format(base=PUBLIC_BASE, repo=REPO_URL).encode(),
                "text/html; charset=utf-8",
            )
        elif path == "/install.sh":
            raw = (REPO_ROOT / "scripts/install.sh").read_bytes()
            # Bake this portal's repo URL in so clients clone from YOUR repo.
            raw = raw.replace(
                b"https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-.git",
                REPO_URL.encode(),
            )
            self._send(raw, "text/x-shellscript")
        elif path == "/install.ps1":
            self._send(
                (REPO_ROOT / "scripts/install.ps1").read_bytes(),
                "text/plain; charset=utf-8",
            )
        elif path == "/latest.json":
            self._send(
                json.dumps(latest_info(), indent=2).encode(),
                "application/json",
            )
        else:
            self.send_response(404)
            self.end_headers()


if __name__ == "__main__":
    with http.server.HTTPServer(("0.0.0.0", PORT), Handler) as srv:
        print(f"Anxious Portal serving on :{PORT}  (repo={REPO_URL})")
        srv.serve_forever()

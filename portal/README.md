# Anxious Portal — PULSE install/update server

`portal/server.py` tumhara install/update portal hai. Ye serve karta hai:
- `/install.sh` — repo ke `scripts/install.sh` ko tumhare `REPO_URL` ke saath
- `/install.ps1` — Windows installer
- `/latest.json` — version feed (`pulse update` isi pattern pe hai)
- `/` — landing page

## Run

```bash
PULSE_REPO_URL=https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-.git \
PORTAL_PUBLIC_URL=https://pulse-agent.anxiousresearchlab.com \
python3 portal/server.py
```

Verify:

```bash
curl -fsSL http://localhost:8080/install.sh | bash
curl -fsSL http://localhost:8080/latest.json
```

## Deploy

Kahin bhi host karo (VPS / Render / Fly). DNS `pulse-agent.anxiousresearchlab.com`
ko isi server pe point karo. Wahi URL `scripts/install.sh` ke `REPO_URL`
default aur DMG builder me use hota hai.

## DMG (sahi tarika)

```bash
PULSE_REPO_URL=https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-.git \
  bash scripts/build-pulse-bootstrap-dmg.sh --out PULSE-Setup.dmg
```

Ye DMG installed `/Applications/Pulse.app` ki copy **nahi** hai — isme
bootstrap installer hai jo dost ki machine pe repo clone karke `pm` runtime
se saari dependencies (uv, Python, Node, tools) natively install karta hai.
Wahi standard staged-install flow hai.

## GitHub repo push (tumhe karna hai)

```bash
cd pulse-agent
git remote add origin https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-.git
git push -u origin main
```

Uske baad GitHub Releases + `scripts/release.py` se version tag karo —
`portal/latest.json` aur `pulse update` wahi se sync honge.

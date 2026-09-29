<p align="center">
  <img src="assets/banner.png" alt="PULSE — Personal Unified Learning System for Engagement" width="100%">
</p>

<p align="center">
  <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-4da3ff?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/issues"><img src="https://img.shields.io/badge/Support-GitHub_issues-ff9e45?style=for-the-badge&logo=github&logoColor=white" alt="Support"></a>
  <a href="https://github.com/Anxious-Research"><img src="https://img.shields.io/badge/Built%20by-Anxious%20Research-8b7bff?style=for-the-badge" alt="Built by Anxious Research"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-3a4a6b?style=for-the-badge" alt="中文"></a>
  <a href="README.ur-pk.md"><img src="https://img.shields.io/badge/Lang-اردو-3a4a6b?style=for-the-badge" alt="اردو"></a>
  <a href="README.es.md"><img src="https://img.shields.io/badge/Lang-Español-3a4a6b?style=for-the-badge" alt="Español"></a>
</p>

# 🪐 PULSE

**Personal Unified Learning System for Engagement** — an autonomous agent in your orbit, built by [Anxious Research](https://github.com/Anxious-Research).

PULSE is one agent core with many orbits: it chats in your terminal, answers from Telegram and Discord, runs on a schedule while you sleep, and remembers everything across sessions. It learns your world — building skills from experience, keeping memory alive, and pulling in past conversations when they matter.

---

## 🛰 First orbit — install

### macOS / Linux / WSL2

```bash
curl -fsSL https://raw.githubusercontent.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/main/scripts/install.sh | bash
```

### Windows (native PowerShell, no WSL needed)

```powershell
iex (irm https://raw.githubusercontent.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/main/scripts/install.ps1)
```

The installer provisions its own toolchain (Python 3.14, Node.js, ripgrep, FFmpeg) into an isolated tool store — your system stays untouched. Then:

```bash
source ~/.bashrc    # or: source ~/.zshrc
pulse               # say hello 🪐
```

> **Desktop app:** grab `PULSE-Setup.dmg` from [Releases](https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/releases), drag to Applications, open — it pulls the latest code from this repo and finishes setup itself.
>
> **Termux (Android):** `pkg install pulse-agent` (stable + canary channels for aarch64).
>
> **Antivirus vs `uv.exe` on Windows:** a false positive — it's Astral's Rust package manager. Whitelist `%LOCALAPPDATA%\pulse\bin` and move on.

---

## 📡 First signal — say something

```bash
pulse              # Interactive CLI — start a conversation
pulse model        # Pick your LLM provider + model
pulse tools        # Toggle capability orbits on/off
pulse gateway      # Serve Telegram, Discord, Slack, WhatsApp, Signal, Email
pulse setup        # Full setup wizard — keys, models, messaging, all at once
pulse update       # Pull the latest signal from this repo
pulse doctor       # Diagnose anything off-nominal
```

📖 **Reference:** every command documents itself — `pulse --help`, `pulse <command> --help`. Contributors start at [CONTRIBUTING.md](CONTRIBUTING.md), developers at [AGENTS.md](AGENTS.md).

---

## 🌌 One core, many orbits

| Orbit | What it is |
| ----- | ---------- |
| 🖥 **Terminal** | Full TUI: multiline editing, slash-command autocomplete, history, interrupt-and-redirect, streaming tool output |
| 💬 **Messaging** | One gateway process serves Telegram, Discord, Slack, WhatsApp, Signal, Email — voice memos transcribed, conversations continuous across platforms |
| 🖱 **Desktop** | Native app with embedded terminal, dashboard, and Bot Mode chat |
| 🌐 **Dashboard** | `pulse dashboard` — browser control plane for sessions, skills, cron, and config |
| ⏰ **Scheduler** | Built-in cron: daily briefings, nightly backups, weekly audits — in plain language, delivered anywhere |
| 🧠 **Memory** | Agent-curated memory with nudges, FTS5 session search with summaries, and a deepening model of you |
| 🛠 **Skills** | The agent writes skills from experience and sharpens them on reuse ([agentskills.io](https://agentskills.io) compatible) |
| 👥 **Delegation** | Isolated subagents for parallel workstreams; RPC scripts that collapse pipelines into zero-context turns |
| 🖥 **Backends** | Local, Docker, SSH, Singularity, Modal, Daytona, Vercel Sandbox — idle environments hibernate to near-zero cost |

Bring any model: OpenRouter, OpenAI, your own endpoint. Swap anytime with `pulse model` — no lock-in.

---

## 🔑 Your keys, your server

No accounts, no subscriptions, no third-party portal. Your API keys live in your own `~/.pulse/.env`, and updates come straight from this repo:

```bash
pulse setup        # interactive: providers, models, messaging
pulse model        # switch anytime
pulse status       # what's wired up right now
```

Hosting for friends or a team? This repo ships its own install/update server:

```bash
python3 portal/server.py   # serves install.sh + version feed — see portal/README.md
```

---

## 🔄 Terminal ↔ messaging map

Both doors lead to the same agent. Slash commands work on either side:

| You want to… | In terminal | On Telegram/Discord/… |
| ------------ | ----------- | ---------------------- |
| Chat | `pulse` | message the bot (`pulse gateway setup` + `start` first) |
| Fresh start | `/new`, `/reset` | `/new`, `/reset` |
| Change model | `/model [provider:model]` | `/model [provider:model]` |
| Set personality | `/personality [name]` | `/personality [name]` |
| Retry / undo | `/retry`, `/undo` | `/retry`, `/undo` |
| Compress / usage | `/compress`, `/usage`, `/insights` | `/compress`, `/usage`, `/insights` |
| Use a skill | `/skills`, `/<skill-name>` | `/<skill-name>` |
| Interrupt | `Ctrl+C` or new message | `/stop` or new message |

---

## 📚 Onboard reference

| Area | Where |
| ---- | ----- |
| Install & repair | `scripts/install.sh`, `pulse doctor` |
| CLI surface | `pulse --help`, `pulse <command> --help` |
| Gateway | `pulse gateway --help` |
| Configuration | `pulse config --help`, `cli-config.yaml.example` |
| Skills | `skills/` directory, `/skills` in-chat |
| Migration | `pulse claw migrate --help` |
| Contributing | [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md) |

---

## 🧳 Coming from OpenClaw?

`pulse setup` detects `~/.openclaw` and offers to import everything — persona, memories, skills, allowlists, messaging settings, allowlisted API keys:

```bash
pulse claw migrate --dry-run   # preview first, commit later
```

---

## 🤝 Contributing

PRs welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the test environment and verification commands.

---

## 📻 Ground control

- 🐛 [Issues](https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/issues)
- 📚 [Skills Hub](https://agentskills.io)

---

## License

MIT — see [LICENSE](LICENSE).

Built by [Anxious Research](https://github.com/Anxious-Research). 🪐

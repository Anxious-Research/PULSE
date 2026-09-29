<p align="center">
  <img src="assets/banner.png" alt="Pulse Agent" width="100%">
</p>

# Pulse Agent ☤
<p align="center">
  <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-">Pulse Agent</a> | <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-">Pulse Desktop</a>
</p>
<p align="center">
  <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-"><img src="https://img.shields.io/badge/Docs-GitHub_repo-FFD700?style=for-the-badge" alt="Documentation"></a>
  <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/issues"><img src="https://img.shields.io/badge/Support-GitHub_issues-5865F2?style=for-the-badge&logo=github&logoColor=white" alt="Support"></a>
  <a href="https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/blob/main/LICENSE"><img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="License: MIT"></a>
  <a href="https://github.com/Anxious-Research"><img src="https://img.shields.io/badge/Built%20by-Anxious%20Research-blueviolet?style=for-the-badge" alt="Built by Anxious Research"></a>
  <a href="README.zh-CN.md"><img src="https://img.shields.io/badge/Lang-中文-red?style=for-the-badge" alt="中文"></a>
  <a href="README.ur-pk.md"><img src="https://img.shields.io/badge/Lang-اردو-green?style=for-the-badge" alt="اردو"></a>
  <a href="README.es.md"><img src="https://img.shields.io/badge/Lang-Español-orange?style=for-the-badge" alt="Español"></a>
</p>

**The self-improving AI agent built by [Anxious Research](https://github.com/Anxious-Research).** It's the only agent with a built-in learning loop — it creates skills from experience, improves them during use, nudges itself to persist knowledge, searches its own past conversations, and builds a deepening model of who you are across sessions. Run it on a $5 VPS, a GPU cluster, or serverless infrastructure that costs nearly nothing when idle. It's not tied to your laptop — talk to it from Telegram while it works on a cloud VM.

Use any model you want — OpenRouter, OpenAI, your own endpoint, and many others. Switch with `pulse model` — no code changes, no lock-in.

<table>
<tr><td><b>A real terminal interface</b></td><td>Full TUI with multiline editing, slash-command autocomplete, conversation history, interrupt-and-redirect, and streaming tool output.</td></tr>
<tr><td><b>Lives where you do</b></td><td>Telegram, Discord, Slack, WhatsApp, Signal, and CLI — all from a single gateway process. Voice memo transcription, cross-platform conversation continuity.</td></tr>
<tr><td><b>A closed learning loop</b></td><td>Agent-curated memory with periodic nudges. Autonomous skill creation after complex tasks. Skills self-improve during use. FTS5 session search with LLM summarization for cross-session recall. <a href="https://github.com/plastic-labs/honcho">Honcho</a> dialectic user modeling. Compatible with the <a href="https://agentskills.io">agentskills.io</a> open standard.</td></tr>
<tr><td><b>Scheduled automations</b></td><td>Built-in cron scheduler with delivery to any platform. Daily reports, nightly backups, weekly audits — all in natural language, running unattended.</td></tr>
<tr><td><b>Delegates and parallelizes</b></td><td>Spawn isolated subagents for parallel workstreams. Write Python scripts that call tools via RPC, collapsing multi-step pipelines into zero-context-cost turns.</td></tr>
<tr><td><b>Runs anywhere, not just your laptop</b></td><td>Seven terminal backends — local, Docker, SSH, Singularity, Modal, Daytona, and Vercel Sandbox. Daytona and Modal offer serverless persistence — your agent's environment hibernates when idle and wakes on demand, costing nearly nothing between sessions. Run it on a $5 VPS or a GPU cluster.</td></tr>
<tr><td><b>Research-ready</b></td><td>Batch trajectory generation, trajectory compression for training the next generation of tool-calling models.</td></tr>
</table>

---

## Quick Install

### Linux, macOS, WSL2

```bash
curl -fsSL https://raw.githubusercontent.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/main/scripts/install.sh | bash
```

### Windows (native, PowerShell)

> **Heads up:** Native Windows runs Pulse without WSL — CLI, gateway, TUI, and tools all work natively. If you'd rather use WSL2, the Linux/macOS one-liner above works there too. Found a bug? Please [file issues](https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/issues).

Run this in PowerShell:

```powershell
iex (irm https://raw.githubusercontent.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/main/scripts/install.ps1)
```

The source installer delegates Python 3.14, Node.js, npm, ripgrep, FFmpeg,
and Python dependencies to PM. If Git is absent, it stages the verified Git
for Windows archive in Pulse' tool store. It does not replace your system Git.
Windows also ships as an MSIX/App Installer package with its own update ownership.

> **Android / Termux:** A signed APT repository is available for aarch64 devices, with a `stable` channel (tagged releases) and a prerelease `canary` channel. The package includes Python, Node.js, and the TUI. Use the Termux APT package (`pkg install pulse-agent`), not the desktop/server installer script.
>
> **Windows:** Native Windows is fully supported — the PowerShell one-liner above installs everything. If you'd rather use WSL2, the Linux command works there too. Native Windows install lives under `%LOCALAPPDATA%\pulse`; WSL2 installs under `~/.pulse` as on Linux.

After installation:

```bash
source ~/.bashrc    # reload shell (or: source ~/.zshrc)
pulse              # start chatting!
```

### Troubleshooting

#### Windows Defender or antivirus flags `uv.exe` as malware

If your antivirus (Bitdefender, Windows Defender, etc.) quarantines `uv.exe` from the Pulse `bin` folder (`%LOCALAPPDATA%\pulse\bin\uv.exe`), this is a **false positive**. The file is Astral's `uv` — the Rust Python package manager Pulse bundles to manage its Python environment. ML-based antivirus engines commonly flag unsigned Rust binaries that download and install packages.

**To verify your copy is authentic:**

```powershell
# Install GitHub CLI if needed
winget install --id GitHub.cli

# Login to GitHub
gh auth login

# Run verification
$uv = "$env:LOCALAPPDATA\pulse\bin\uv.exe"
$ver = (& $uv --version).Split(' ')[1]
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$zip = "$env:TEMP\uv.zip"
Invoke-WebRequest "https://github.com/astral-sh/uv/releases/download/$ver/uv-x86_64-pc-windows-msvc.zip" -OutFile $zip -UseBasicParsing
gh attestation verify $zip --repo astral-sh/uv
Expand-Archive $zip "$env:TEMP\uv_x" -Force
(Get-FileHash "$env:TEMP\uv_x\uv.exe").Hash -eq (Get-FileHash $uv).Hash
```

If attestation says "Verification succeeded" and the last line prints `True`, you're good.

**To whitelist Pulse:**
- **Windows Defender:** Run PowerShell as Admin → `Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\pulse\bin"`
- **Bitdefender:** Add an exception in the Bitdefender console (Protection > Antivirus > Settings > Manage Exceptions)
- Whitelist the **folder**, not the file hash — Pulse updates `uv` and the hash changes every version

For more context, see the upstream Astral reports: [astral-sh/uv#13553](https://github.com/astral-sh/uv/issues/13553), [astral-sh/uv#15011](https://github.com/astral-sh/uv/issues/15011), [astral-sh/uv#10079](https://github.com/astral-sh/uv/issues/10079).

---

## Getting Started

```bash
pulse              # Interactive CLI — start a conversation
pulse model        # Choose your LLM provider and model
pulse tools        # Configure which tools are enabled
pulse config set   # Set individual config values
pulse config get   # Print individual config values
pulse gateway      # Start the messaging gateway (Telegram, Discord, etc.)
pulse setup        # Run the full setup wizard (configures everything at once)
pulse claw migrate # Migrate from OpenClaw (if coming from OpenClaw)
pulse update       # Update to the latest version
pulse doctor       # Diagnose any issues
```

📖 **Docs:** run `pulse --help` or `pulse <command> --help`. Contributor setup lives in [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Bring your own keys — no portal account needed

Pulse works with whatever provider you want. Add your keys once with the setup wizard:

```bash
pulse setup        # interactive: providers, models, messaging
pulse model        # switch provider/model anytime — no code changes, no lock-in
```

You can bring separate keys per tool (model, web search, image generation, TTS)
whenever you want. Check what's wired up any time with `pulse status` and
`pulse doctor`.

## Self-hosted update server (your own portal)

This repo ships its own install/update server — no third-party portal involved:

```bash
python3 portal/server.py   # serves install.sh + version feed, see portal/README.md
```

Point your friends at it (or at this repo's `scripts/install.sh`), and every
install and `pulse update` syncs from YOUR infrastructure.

---

## CLI vs Messaging Quick Reference

Pulse has two entry points: start the terminal UI with `pulse`, or run the gateway and talk to it from Telegram, Discord, Slack, WhatsApp, Signal, or Email. Once you're in a conversation, many slash commands are shared across both interfaces.

| Action                         | CLI                                           | Messaging platforms                                                              |
| ------------------------------ | --------------------------------------------- | -------------------------------------------------------------------------------- |
| Start chatting                 | `pulse`                                      | Run `pulse gateway setup` + `pulse gateway start`, then send the bot a message |
| Start fresh conversation       | `/new` or `/reset`                            | `/new` or `/reset`                                                               |
| Change model                   | `/model [provider:model]`                     | `/model [provider:model]`                                                        |
| Set a personality              | `/personality [name]`                         | `/personality [name]`                                                            |
| Retry or undo the last turn    | `/retry`, `/undo`                             | `/retry`, `/undo`                                                                |
| Compress context / check usage | `/compress`, `/usage`, `/insights [--days N]` | `/compress`, `/usage`, `/insights [days]`                                        |
| Browse skills                  | `/skills` or `/<skill-name>`                  | `/<skill-name>`                                                                  |
| Interrupt current work         | `Ctrl+C` or send a new message                | `/stop` or send a new message                                                    |
| Platform-specific status       | `/platforms`                                  | `/status`, `/sethome`                                                            |

For the full command lists, run `pulse --help` or `pulse <command> --help`.

---

## Documentation

Start with `pulse --help`. Contributor references: [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md), and the `skills/` directory:

| Section                                                                                             | What's Covered                                             |
| --------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| Quickstart                 | Install → setup → first conversation in 2 minutes          |
| CLI Usage                              | Commands, keybindings, personalities, sessions             |
| Configuration                | Config file, providers, models, all options                |
| Messaging Gateway                | Telegram, Discord, Slack, WhatsApp, Signal, Home Assistant |
| Security                          | Command approval, DM pairing, container isolation          |
| Tools & Toolsets            | 40+ tools, toolset system, terminal backends               |
| Skills System              | Procedural memory, Skills Hub, creating skills             |
| Memory                     | Persistent memory, user profiles, best practices           |
| MCP Integration               | Connect any MCP server for extended capabilities           |
| Cron Scheduling              | Scheduled tasks with platform delivery                     |
| Context Files       | Project context that shapes every conversation             |
| Architecture             | Project structure, agent loop, key classes                 |
| Contributing             | Development setup, PR process, code style                  |
| CLI Reference                  | All commands and flags                                     |
| Environment Variables | Complete env var reference                                 |

---

## Migrating from OpenClaw

If you're coming from OpenClaw, Pulse can automatically import your settings, memories, skills, and API keys.

**During first-time setup:** The setup wizard (`pulse setup`) automatically detects `~/.openclaw` and offers to migrate before configuration begins.

**Anytime after install:**

```bash
pulse claw migrate              # Interactive migration (full preset)
pulse claw migrate --dry-run    # Preview what would be migrated
pulse claw migrate --preset user-data   # Migrate without secrets
pulse claw migrate --overwrite  # Overwrite existing conflicts
```

What gets imported:

- **SOUL.md** — persona file
- **Memories** — MEMORY.md and USER.md entries
- **Skills** — user-created skills → `~/.pulse/skills/openclaw-imports/`
- **Command allowlist** — approval patterns
- **Messaging settings** — platform configs, allowed users, working directory
- **API keys** — allowlisted secrets (Telegram, OpenRouter, OpenAI, Anthropic, ElevenLabs)
- **TTS assets** — workspace audio files
- **Workspace instructions** — AGENTS.md (with `--workspace-target`)

See `pulse claw migrate --help` for all options, or use the `openclaw-migration` skill for an interactive agent-guided migration with dry-run previews.

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, and PR process.

Start with [CONTRIBUTING.md](CONTRIBUTING.md#development-setup)
for the test environment and verification commands.

---

## Community

- 🐛 [Issues](https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/issues)
- 📚 [Skills Hub](https://agentskills.io)
- 🐛 [Issues](https://github.com/Anxious-Research/PULSE-Personal-Unified-Learning-System-for-Engagement-/issues)
- 🔌 [computer-use-linux](https://github.com/avifenesh/computer-use-linux) — Linux desktop-control MCP server for Pulse and other MCP hosts, with AT-SPI accessibility trees, Wayland/X11 input, screenshots, and compositor window targeting.
- 🔌 [PulseClaw](https://github.com/AaronWong1999/pulseclaw) — Community WeChat bridge: Run Pulse Agent and OpenClaw on the same WeChat account.

---

## License

MIT — see [LICENSE](LICENSE).

Built by [Anxious Research](https://github.com/Anxious-Research).

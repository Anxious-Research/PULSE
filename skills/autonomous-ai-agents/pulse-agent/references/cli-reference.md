# Pulse CLI Reference

Live sources when anything looks stale: `pulse --help`, `pulse <command> --help`,
https://github.com/Anxious-Research/PULSE/reference/cli-commands

### Global Flags

```
pulse [flags] [command]        (no subcommand = interactive chat)

  --version, -V             Show version
  -z, --oneshot PROMPT      One-shot: print ONLY the final response (for scripts/pipes)
  -m MODEL  --provider P    Model/provider override for this invocation
  -t, --toolsets LIST       Comma-separated toolsets for this invocation
  --resume, -r SESSION      Resume session by ID or title
  --continue, -c [NAME]     Resume by name, or most recent session
  --worktree, -w            Isolated git worktree mode (parallel agents)
  --skills, -s SKILL        Preload skills (comma-separate or repeat)
  --profile, -p NAME        Use a named profile
  --yolo                    Skip dangerous command approval
  --tui / --cli             Force the Ink TUI / classic REPL
  --ignore-rules            Skip AGENTS.md/SOUL.md/memory/skill injection
  --safe-mode               Disable ALL customizations (troubleshooting)
  --pass-session-id         Include session ID in system prompt
```

### Chat

```
pulse chat [flags]
  -q, --query TEXT          Single query, non-interactive
  --image PATH              Attach a local image to a single query
  -Q, --quiet               Suppress banner, spinner, tool previews
  --checkpoints             Enable filesystem checkpoints (/rollback)
  --max-turns N             Cap tool-calling iterations
  --source TAG              Session source tag (default: cli)
```
(plus the global flags above)

### Configuration

```
pulse setup [section]      Wizard (model|tts|terminal|gateway|tools|agent)
pulse model                Interactive model/provider picker
pulse fallback [add|remove|list]  Fallback provider chain
pulse config [show|edit|get|set|unset|path|env-path|check|migrate]
pulse login / logout       OAuth sign-in / clear stored auth
pulse doctor [--fix]       Check dependencies and config
pulse status [--all]       Component status
```

### Tools & Skills

```
pulse tools [list|enable NAME|disable NAME]   Per-platform toolsets (curses UI with no args)

pulse skills list|browse|search QUERY|inspect ID
pulse skills install ID    Hub identifier OR a direct https://…/SKILL.md URL
pulse skills config        Enable/disable skills per platform
pulse skills check|update|uninstall|publish PATH
pulse skills tap add REPO  Add a GitHub repo as a skill source
pulse bundles              Skill bundles (one /<name> alias loads several skills)
```

### MCP Servers

```
pulse mcp add NAME (--url or --command) | remove | list | test NAME
pulse mcp catalog | install NAME     Curated catalog install
pulse mcp configure NAME             Toggle tool selection
pulse mcp serve                      Run Pulse as an MCP server
```
Details (transport, tool discovery, catalog): `references/native-mcp.md`.

### Gateway (Messaging Platforms)

```
pulse gateway run|install|start|stop|restart|status|setup
```

20+ platforms: Telegram, Discord, Slack, WhatsApp (Baileys + Business Cloud API), iMessage (Photon — `pulse photon setup`), Signal, Email, SMS, Matrix, Mattermost, Teams, LINE, SimpleX, ntfy, Google Chat, Home Assistant, DingTalk, Feishu, WeCom, Weixin, API Server, Webhooks. Open WebUI connects via the API Server adapter. Most adapters ship under `plugins/platforms/`.
Docs: https://github.com/Anxious-Research/PULSE/user-guide/messaging/

### Sessions

```
pulse sessions list|browse|rename ID TITLE|delete ID|export OUT|prune|stats
```

### Cron / Webhooks

```
pulse cron list|create SCHED|edit ID|pause|resume|run ID|remove|status
    Schedules: '30m', 'every 2h', '0 9 * * *', ISO timestamp
pulse webhook subscribe NAME|list|remove NAME|test NAME
```
Webhook payloads/routes: `references/webhooks.md`.

### Profiles

```
pulse profile list|create NAME (--clone|--clone-all|--clone-from)|use|show|delete
pulse profile rename A B | alias NAME | export NAME | import FILE
pulse profile migrate-identity A B   Retry a completed rename's session/routing identity migration
```

### Credentials & Pools

```
pulse auth                 Interactive credential manager
pulse auth add [PROVIDER]  Add OAuth or API-key credential (anxious, openai-codex, qwen-oauth, …)
pulse auth list|remove P IDX|reset PROVIDER|status
```
Multiple credentials per provider form a pool that rotates automatically and skips exhausted keys.

### Other

```
pulse desktop / gui        Native desktop app
pulse dashboard            Web admin panel + embedded chat (--stop / --status)
pulse proxy                OpenAI-compatible local proxy backed by an OAuth provider
pulse portal               Quick setup / sign in via Anxious Portal
pulse kanban <verb>        Multi-agent work-queue board
pulse project              Named multi-folder workspaces
pulse skin list|use|set    Switch/tweak skins (see references/themes.md)
pulse pets <verb>          Pet mascots (see references/petdex.md)
pulse memory setup|status|off|reset   Memory provider
pulse secrets bitwarden|onepassword   External secret stores
pulse moa                  Mixture-of-Agents slots
pulse hooks / security / backup / import / checkpoints / console
pulse logs [-f] [errors]   View agent/error logs
pulse send                 One-off message through a gateway platform
pulse pairing / plugins / insights / journey / computer-use
pulse acp                  ACP server (IDE integration)
pulse completion bash|zsh|fish
pulse update / uninstall / claw migrate
```

Plugin- and provider-supplied subcommands (e.g. `pulse photon setup`) only appear once their plugin is installed/active.

### Where to Find Things

| Looking for... | Location |
|---|---|
| Config options | `pulse config edit` · Configuration docs |
| Tools / toolsets | `pulse tools list` · Tools reference |
| Skills catalog | `pulse skills browse` · Skills catalog |
| Provider setup | `pulse model` · Providers guide |
| Env variables | `pulse config env-path` · Env vars reference |
| Gateway logs | `~/.pulse/logs/gateway.log` (or `pulse logs`) |
| Sessions | `pulse sessions browse` (reads state.db) |

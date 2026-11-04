# Contributor email → GitHub login mappings

This directory contains `contributors.json`, which consolidates contributor mappings into a single structured registry alongside the root `.mailmap` file.

## Adding a mapping

Run `scripts/add_contributor.py` from the repository root:

```bash
python3 scripts/add_contributor.py <email> <github-login> [comment...]

# e.g.
python3 scripts/add_contributor.py jane.doe@example.com janedoe "PR #12345 salvage (gateway: fix session key routing)"
```

This updates both `contributors/contributors.json` and the root `.mailmap` file.

## Structure of `contributors.json`

Mappings are keyed by commit-author email:

```json
{
  "jane.doe@example.com": {
    "login": "janedoe",
    "comment": "PR #12345 salvage (gateway: fix session key routing)"
  }
}
```

## Rules

- Do NOT add new entries to `AUTHOR_MAP` in `scripts/releases/authors_legacy.py`. That dict is frozen legacy data.
- GitHub noreply emails (`<id>+<login>@users.noreply.github.com` and `<login>@users.noreply.github.com`) auto-resolve — no entry needed.
- The `Contributor Attribution Check` CI job fails a PR whose commits carry an unmapped email; running `python3 scripts/audit_pr_attribution.py --fix` or `scripts/add_contributor.py` registers the mapping.

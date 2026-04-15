# Agent Envelope Contract (v1)

## Purpose

The **envelope** is the JSON document an agent receives when it bootstraps. It unifies:

1. **Static rules** from the agent's brain file at `~/ObsidianVault/_brain/agents/{shared,templates}/<agent>.md`
2. **Experience log** — runtime-learned patterns appended by `brain_write` after fixes
3. **Project rules** — from the per-project KB (optional)
4. **User feedback & conventions** — from `.claude/memory/feedback_*.md` and `.claude/rules/*.md`

Agents consume the envelope and remain storage-agnostic. Each project ships its own `kb_bootstrap <agent>` emitter.

## Schema (v1)

See the canonical version in report-downloader: `docs/agent_envelope.md`. Key fields:

- `envelope_version` (string, always `"1"`)
- `agent` (archetype name)
- `tier` (`shared` | `template` | `local`)
- `project` (consuming project slug)
- `static_rules`, `experience`, `project_rules`, `feedback`, `routing`, `conventions`, `commands`, `meta`

## Tier rules

- **shared** — brain file at `_brain/agents/shared/<agent>.md`. Fully generic.
- **template** — brain file at `_brain/agents/templates/<agent>.md`. Needs project params.
- **local** — no brain file. Project-only.

## Writers

- `~/.claude/tools/brain_write <agent> --problem "…" --solution "…"` — shared CLI, appends to brain file.

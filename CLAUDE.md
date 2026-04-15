# CLAUDE.md

This file provides guidance to Claude Code when working with code in this repository.

## Project Overview

TODO: Describe your project in 2–3 sentences. What does it do? Who uses it?

## Commands

```bash
# TODO: List the key commands for this project.
# Example:
#   install        → pip install -e .
#   run            → python main.py
#   test           → pytest
```

## Portable Agents

This project uses the shared agent brain at `~/ObsidianVault/_brain/`.

- **Agents** in `.claude/agents/` are symlinks to `~/ObsidianVault/_brain/agents/{shared,templates}/`.
- **Skills** in `.claude/skills/` include symlinks to `~/ObsidianVault/_brain/skills/` (plus any project-local ones).
- **Memory** at `.claude/memory/` symlinks to `~/ObsidianVault/_brain/memory/<project-slug>/` — shared across sessions, per-project.
- **Envelope contract**: each agent loads `scripts/kb_bootstrap.py <agent>` at Phase 0. Shape documented in `docs/agent_envelope.md` (if copied).

### Bootstrap a new project

```bash
# From inside the project directory:
brain-init

# Or equivalently:
~/ObsidianVault/_brain/install.sh .
```

Idempotent. Re-run safely at any time. Existing real files are never overwritten.

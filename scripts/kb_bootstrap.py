#!/usr/bin/env python3
"""kb_bootstrap — emit the agent envelope as JSON on stdout.

Generic, project-agnostic bootstrap. No database required.
Reads brain files + local .claude/memory + .claude/rules.

Conforms to the envelope contract in docs/agent_envelope.md (v1).
If your project has its own KB, copy this file and extend fetch_project_rules().

Usage:
    python scripts/kb_bootstrap.py <agent> [--pretty]
    python scripts/kb_bootstrap.py <agent> --platform X --portal Y --pretty
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import pathlib
import re
import sys
import tomllib
from typing import Any

ENVELOPE_VERSION = "1"
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
PROJECT_NAME = os.environ.get("BRAIN_PROJECT_SLUG") or PROJECT_ROOT.name
VAULT_ROOT = pathlib.Path.home() / "ObsidianVault" / "_brain" / "agents"


# ── Brain file discovery ──────────────────────────────────────────────

def find_brain(agent: str) -> tuple[pathlib.Path | None, str]:
    for tier in ["shared", "templates"]:
        candidate = VAULT_ROOT / tier / f"{agent}.md"
        if candidate.exists():
            return candidate, tier
    return None, "local"


# ── Brain file parsing ────────────────────────────────────────────────

_SECTION_RE = re.compile(r"^## (.+?)\s*$", re.MULTILINE)
_RULES_HEADER_RE = re.compile(r"^## (?:Rules|Core Principles|Principles)\b", re.MULTILINE)
_EXPERIENCE_HEADER_RE = re.compile(r"^## Experience Log", re.MULTILINE)
_H3_ENTRY_RE = re.compile(r"^### (.+?)\n```yaml\n(.*?)\n```", re.MULTILINE | re.DOTALL)
_NUMBERED_RULE_RE = re.compile(r"^\s*\d+\.\s+\*\*(.+?)\*\*\s*[—-]\s*(.+?)$", re.MULTILINE)


def _section_slice(text: str, header_re: re.Pattern) -> str:
    m = header_re.search(text)
    if not m:
        return ""
    start = m.end()
    rest = text[start:]
    nxt = _SECTION_RE.search(rest)
    return rest[: nxt.start()] if nxt else rest


def parse_static_rules(brain_text: str, agent: str) -> list[dict[str, str]]:
    section = _section_slice(brain_text, _RULES_HEADER_RE)
    if not section:
        return []
    rules = []
    for idx, match in enumerate(_NUMBERED_RULE_RE.finditer(section), 1):
        title = match.group(1).strip()
        body = match.group(2).strip()
        rules.append({
            "id": f"{agent}:rule-{idx}",
            "rule": f"{title} — {body}",
            "severity": "critical" if "NEVER" in body.upper() or "CRITICAL" in body.upper() else "high",
            "category": "general",
            "source": f"brain/{agent}.md#rules",
        })
    return rules


def _parse_yaml_block(block: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            out[key] = [t.strip() for t in inner.split(",") if t.strip()]
        else:
            out[key] = val
    return out


def parse_experience(brain_text: str) -> list[dict[str, Any]]:
    section = _section_slice(brain_text, _EXPERIENCE_HEADER_RE)
    if not section:
        return []
    entries = []
    for match in _H3_ENTRY_RE.finditer(section):
        title = match.group(1).strip()
        parsed = _parse_yaml_block(match.group(2))
        if parsed:
            parsed["_title"] = title
            entries.append(parsed)
    return entries


# ── Project rules: override in your project's copy ────────────────────

def fetch_project_rules(agent: str, platform: str | None, portal: str | None) -> list[dict[str, Any]]:
    """Default: no DB. Override in your project's copy to read from your KB."""
    return []


# ── Feedback files ────────────────────────────────────────────────────

def parse_feedback() -> list[dict[str, str]]:
    mem_dir = PROJECT_ROOT / ".claude" / "memory"
    if not mem_dir.exists():
        return []
    out = []
    for md in sorted(mem_dir.glob("feedback_*.md")):
        text = md.read_text(encoding="utf-8", errors="replace")
        name = ""
        why = ""
        fm = re.match(r"---\n(.*?)\n---\n", text, re.DOTALL)
        if fm:
            for line in fm.group(1).splitlines():
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    why = line.split(":", 1)[1].strip()
        body = text[fm.end():] if fm else text
        first_line = body.strip().splitlines()[0] if body.strip() else ""
        out.append({
            "rule": name or md.stem,
            "why": why or first_line,
            "source": str(md.relative_to(PROJECT_ROOT)),
        })
    return out


# ── Routing (only for supervisor) ─────────────────────────────────────

def parse_routing() -> list[dict[str, str]]:
    conv = PROJECT_ROOT / ".claude" / "rules" / "agent-conventions.md"
    if not conv.exists():
        return []
    text = conv.read_text(encoding="utf-8")
    out = []
    for line in text.splitlines():
        if line.count("|") < 3 or line.startswith("|---") or "---|" in line:
            continue
        if not line.strip().startswith("|") or "Signal in Task" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 2 and cells[0] and cells[1] and not cells[0].startswith(":"):
            out.append({"signal": cells[0], "agent": cells[1].strip("`")})
    return out


# ── Conventions ───────────────────────────────────────────────────────

def parse_conventions() -> list[dict[str, str]]:
    rules_dir = PROJECT_ROOT / ".claude" / "rules"
    if not rules_dir.exists():
        return []
    out = []
    for md in sorted(rules_dir.glob("*.md")):
        if md.stem == "agent-conventions":
            continue
        out.append({
            "topic": md.stem,
            "content": md.read_text(encoding="utf-8").strip(),
            "source": str(md.relative_to(PROJECT_ROOT)),
        })
    return out


# ── Commands ──────────────────────────────────────────────────────────

def load_commands() -> dict[str, str]:
    toml_path = PROJECT_ROOT / "scripts" / "bootstrap_commands.toml"
    if not toml_path.exists():
        return {}
    with toml_path.open("rb") as f:
        data = tomllib.load(f)
    return data.get("commands", {})


# ── Envelope assembly ─────────────────────────────────────────────────

def build_envelope(agent: str, platform: str | None, portal: str | None) -> dict[str, Any]:
    brain_path, tier = find_brain(agent)
    brain_text = brain_path.read_text(encoding="utf-8") if brain_path else ""
    brain_version = f"sha:{hashlib.sha256(brain_text.encode()).hexdigest()[:12]}" if brain_text else ""

    return {
        "envelope_version": ENVELOPE_VERSION,
        "agent": agent,
        "tier": tier,
        "project": PROJECT_NAME,
        "static_rules": parse_static_rules(brain_text, agent) if brain_text else [],
        "experience": parse_experience(brain_text) if brain_text else [],
        "project_rules": fetch_project_rules(agent, platform, portal),
        "feedback": parse_feedback(),
        "routing": parse_routing() if agent == "supervisor" else [],
        "conventions": parse_conventions(),
        "commands": load_commands(),
        "meta": {
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
            "brain_file": str(brain_path) if brain_path else None,
            "brain_version": brain_version,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Emit agent bootstrap envelope as JSON.")
    ap.add_argument("agent", help="Agent name (e.g., fixer, supervisor)")
    ap.add_argument("--platform", default=None)
    ap.add_argument("--portal", default=None)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()

    envelope = build_envelope(args.agent, args.platform, args.portal)
    print(json.dumps(envelope, indent=2 if args.pretty else None, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

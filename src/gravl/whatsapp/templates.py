"""Template registry — load from `templates` table and render Meta components.

body_json shape (all keys optional; missing = no component):

    {
      "header":  {"params": ["img_url"]},
      "body":    {"params": ["first_name", "order_id"]},
      "buttons": [
        {"type": "url",           "index": 0, "params": ["tracking_url"]},
        {"type": "quick_reply",   "index": 1, "params": []}
      ]
    }

At send time the caller passes a flat `variables` dict keyed by the names above.
"""

from __future__ import annotations

from typing import Any

from gravl.db.adapter import get_connection


class TemplateNotFound(KeyError):
    pass


class TemplateVariableMissing(KeyError):
    pass


def get_template(name: str, locale: str = "en") -> dict[str, Any]:
    sql = """
        SELECT id, channel, name, category, locale, body_json, meta_template_id, approved
          FROM templates
         WHERE channel = 'whatsapp' AND name = %s AND locale = %s
    """
    with get_connection() as conn:
        row = conn.execute(sql, (name, locale)).fetchone()
    if row is None:
        raise TemplateNotFound(f"whatsapp template not found: name={name} locale={locale}")
    return dict(row)


def _pick(variables: dict[str, Any], key: str) -> str:
    if key not in variables:
        raise TemplateVariableMissing(f"missing variable '{key}'")
    return str(variables[key])


def render_components(body_json: dict[str, Any], variables: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert the stored spec + caller variables into Meta `components` payload."""
    components: list[dict[str, Any]] = []

    for section in ("header", "body"):
        spec = body_json.get(section)
        if not spec:
            continue
        params = spec.get("params", [])
        if not params:
            continue
        components.append({
            "type": section,
            "parameters": [{"type": "text", "text": _pick(variables, p)} for p in params],
        })

    for btn in body_json.get("buttons", []) or []:
        params = btn.get("params", [])
        if not params:
            continue
        components.append({
            "type": "button",
            "sub_type": btn["type"],
            "index": str(btn["index"]),
            "parameters": [{"type": "text", "text": _pick(variables, p)} for p in params],
        })

    return components

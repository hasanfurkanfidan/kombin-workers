"""Readers for the structured data a page embeds about itself: `__NEXT_DATA__` and `application/ld+json`.

Both are plain JSON inside a `<script>` tag. They are found with a regular expression rather than an HTML parser: the
pages are 0.3-5 MB, the tags are machine-written, and a parser would only add time.
"""

from __future__ import annotations

import json
import re
from typing import Any

_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)
_LD_JSON = re.compile(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', re.DOTALL)


def extract_next_data(html: str) -> dict[str, Any]:
    """The `__NEXT_DATA__` JSON object of a Next.js page, or {} if the page has none (or it is not valid JSON)."""
    match = _NEXT_DATA.search(html)
    if match is None:
        return {}
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def extract_ld_json(html: str) -> list[dict[str, Any]]:
    """Every JSON-LD object of the page; arrays and `@graph` wrappers are flattened. Invalid blocks are ignored."""
    found: list[dict[str, Any]] = []

    def add(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                add(item)
        elif isinstance(node, dict):
            graph = node.get("@graph")
            if isinstance(graph, list):
                add(graph)
            found.append(node)

    for match in _LD_JSON.finditer(html):
        try:
            add(json.loads(match.group(1)))
        except ValueError:
            continue
    return found


def find_ld_type(html: str, type_name: str) -> dict[str, Any] | None:
    """The first JSON-LD object whose `@type` is (or contains) `type_name`."""
    for node in extract_ld_json(html):
        node_type = node.get("@type")
        if node_type == type_name or (isinstance(node_type, list) and type_name in node_type):
            return node
    return None


def dig(data: Any, *path: str | int) -> Any:
    """`data[a][b][c]` that returns None instead of raising when a step is missing."""
    current = data
    for step in path:
        if isinstance(step, int):
            if not isinstance(current, list) or not -len(current) <= step < len(current):
                return None
            current = current[step]
        else:
            if not isinstance(current, dict) or step not in current:
                return None
            current = current[step]
    return current

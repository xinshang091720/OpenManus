"""Small cross-process progress channel for long local MCP tools.

The Runtime and its packaged MCP bridge are separate processes.  A compact
per-tool JSON file lets the bridge expose the current business stage without
putting progress messages into chat history or extending the MCP contract.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path

from app.runtime_paths import runtime_paths


def _path(tool_name: str) -> Path:
    safe = "".join(character for character in tool_name if character.isalnum() or character in "-_")
    folder = runtime_paths.data_dir / "tool-progress"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{safe}.json"


def set_tool_progress(tool_name: str, summary: str, **details: object) -> None:
    path = _path(tool_name)
    temporary = path.with_suffix(f".{os.getpid()}.{uuid.uuid4().hex}.tmp")
    content = json.dumps(
        {"tool": tool_name, "summary": summary, "updated_at": time.time(), **details},
        ensure_ascii=False,
    )
    try:
        temporary.write_text(content, encoding="utf-8")
        try:
            os.replace(temporary, path)
        except OSError:
            # Antivirus and a concurrent Runtime read can briefly hold the
            # destination on Windows.  Progress is best-effort and must never
            # fail the underlying Revit operation.
            path.write_text(content, encoding="utf-8")
    except OSError:
        return
    finally:
        try:
            temporary.unlink()
        except OSError:
            pass


def read_tool_progress(tool_name: str, *, max_age_seconds: float = 600) -> dict | None:
    path = _path(tool_name)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    updated = payload.get("updated_at")
    if not isinstance(updated, (int, float)) or time.time() - updated > max_age_seconds:
        return None
    return payload if isinstance(payload.get("summary"), str) else None


def clear_tool_progress(tool_name: str) -> None:
    try:
        _path(tool_name).unlink()
    except OSError:
        pass

"""Fail-open hook adapter shared by supported AI coding clients.

The adapter intentionally writes only a small event envelope to a local log. It
never stores prompt text. Hardware delivery reuses the proven Codex hook path
until the multi-device service replaces it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ai_status_core import load_config
from ai_status_hook import set_hook_status
from ai_status_projects import register_project, resolve_event_project


PROJECT_DIR = Path(__file__).resolve().parent
EVENT_LOG = PROJECT_DIR / "integration-events.jsonl"

PROVIDERS = frozenset(
    {
        "codex",
        "claude-desktop",
        "claude-code",
        "antigravity",
        "cursor",
        "copilot-app",
        "copilot-vscode",
        "copilot-cli",
    }
)
EVENT_TO_STATUS = {
    "red": "RED",
    "cancel": "RED",
    "busy": "YELLOW",
    "done": "DONE",
    "off": "OFF",
}
SESSION_FIELDS = (
    "session_id",
    "sessionId",
    "conversation_id",
    "conversationId",
    "turn_id",
    "turnId",
)


def read_payload(stream: Any = None) -> dict[str, Any]:
    source = sys.stdin if stream is None else stream
    try:
        raw = source.read()
    except OSError:
        return {}
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def session_id_from(payload: dict[str, Any]) -> str:
    for field in SESSION_FIELDS:
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def append_event(provider: str, event: str, payload: dict[str, Any], project: str = "") -> None:
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "provider": provider,
        "event": event,
        "status": EVENT_TO_STATUS[event],
        "session_id": session_id_from(payload),
        "project": project,
    }
    with EVENT_LOG.open("a", encoding="utf-8") as event_log:
        event_log.write(json.dumps(record, ensure_ascii=False) + "\n")


def run_hook(provider: str, event: str, payload: dict[str, Any]) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"unsupported provider: {provider}")
    if event not in EVENT_TO_STATUS:
        raise ValueError(f"unsupported event: {event}")
    effective_event = event
    termination = str(payload.get("status") or payload.get("reason") or "").casefold()
    if event == "done" and termination in {
        "aborted",
        "cancelled",
        "canceled",
        "interrupted",
        "error",
    }:
        effective_event = "cancel"
    session_id = session_id_from(payload)
    configured = [
        str(item.get("project", ""))
        for item in load_config().get("devices", [])
        if str(item.get("provider", "")) == provider
    ]
    project = resolve_event_project(provider, payload, configured)
    project = register_project(provider, project, session_id)
    append_event(provider, effective_event, payload, project)
    set_hook_status(EVENT_TO_STATUS[effective_event], provider, project)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="AI Status Light universal hook")
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    parser.add_argument("--event", required=True, choices=sorted(EVENT_TO_STATUS))
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and log the hook without changing hardware state",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = read_payload()
        if args.dry_run:
            session_id = session_id_from(payload)
            configured = [
                str(item.get("project", ""))
                for item in load_config().get("devices", [])
                if str(item.get("provider", "")) == args.provider
            ]
            project = resolve_event_project(args.provider, payload, configured)
            project = register_project(args.provider, project, session_id)
            append_event(args.provider, args.event, payload, project)
        else:
            run_hook(args.provider, args.event, payload)
    except Exception:
        # Hooks must never prevent the AI client from continuing.
        pass
    if args.provider == "cursor" and args.event == "busy":
        # Cursor's beforeSubmitPrompt hook expects an explicit allow response.
        print(json.dumps({"continue": True}))
    elif args.provider == "antigravity" and args.event == "done":
        # Antigravity requires a Stop decision. Any value other than
        # "continue" allows the execution loop to stop.
        print(json.dumps({"decision": "allow"}))
    else:
        print("{}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

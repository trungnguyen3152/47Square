"""Fail-open Codex hook client. Hook failures must never interrupt Codex."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ai_status_core import BridgeCommandError, load_config, send_to_bridge
from ai_status_projects import canonical_project, register_project, resolve_event_project, session_from_payload


PROJECT_DIR = Path(__file__).resolve().parent
STATE_PATH = PROJECT_DIR / ".ai_status_state.json"
LOCK_PATH = PROJECT_DIR / ".ai_status_state.lock"


@contextmanager
def state_lock() -> Iterator[None]:
    """Serialize status changes across hook and timer processes."""
    with LOCK_PATH.open("a+b") as lock_file:
        lock_file.seek(0, 2)
        if lock_file.tell() == 0:
            lock_file.write(b"0")
            lock_file.flush()
        lock_file.seek(0)
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            lock_file.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _state_key(provider: str, project: str = "") -> str:
    project = canonical_project(project)
    return f"{provider}::{project}" if project else provider


def write_state(token: str, status: str, provider: str = "", project: str = "") -> None:
    current = read_state_document()
    current.update({"token": token, "status": status, "provider": provider})
    providers = current.setdefault("providers", {})
    if provider:
        providers[_state_key(provider, project)] = {
            "token": token, "status": status, "project": canonical_project(project)
        }
    temporary = STATE_PATH.with_name(f"{STATE_PATH.name}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(json.dumps(current), encoding="utf-8")
    temporary.replace(STATE_PATH)


def read_state_document() -> dict:
    try:
        value = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(value, dict):
            return value
    except (OSError, ValueError):
        pass
    return {}


def read_state(provider: str = "", project: str = "") -> dict[str, str]:
    value = read_state_document()
    if provider:
        provider_value = value.get("providers", {}).get(_state_key(provider, project), {})
        if isinstance(provider_value, dict):
            return {"token": str(provider_value.get("token", "")), "status": str(provider_value.get("status", ""))}
    return {"token": str(value.get("token", "")), "status": str(value.get("status", ""))}


def start_bridge() -> None:
    """Start a detached bridge when no bridge is accepting commands."""
    stdout = (PROJECT_DIR / "bridge.log").open("a", encoding="utf-8")
    stderr = (PROJECT_DIR / "bridge-error.log").open("a", encoding="utf-8")
    kwargs: dict[str, object] = {
        "cwd": PROJECT_DIR,
        "stdin": subprocess.DEVNULL,
        "stdout": stdout,
        "stderr": stderr,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True

    try:
        subprocess.Popen(
            [sys.executable, "-u", str(PROJECT_DIR / "ai_status_bridge.py")],
            **kwargs,
        )
    finally:
        stdout.close()
        stderr.close()


def send_with_bridge_start(status: str, provider: str = "", project: str = "") -> None:
    config = load_config()
    try:
        send_to_bridge(status, config, provider=provider, project=project)
        return
    except BridgeCommandError:
        # A bridge is already listening. Starting another one here creates a
        # process storm and makes all instances fight over COM5.
        raise
    except OSError:
        start_bridge()

    deadline = time.monotonic() + 2.7
    while True:
        try:
            send_to_bridge(status, config, provider=provider, project=project)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)


def spawn_red_timer(token: str, delay: float, provider: str = "", project: str = "") -> None:
    kwargs: dict[str, object] = {
        "cwd": PROJECT_DIR,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), "--red-after", token, str(delay), provider, project],
        **kwargs,
    )


def ensure_typing_watcher() -> None:
    watcher = PROJECT_DIR / "ai_status_typing_watcher.py"
    if watcher.exists():
        subprocess.Popen(
            [sys.executable, str(watcher)],
            **detached_kwargs(),
        )


def detached_kwargs() -> dict[str, object]:
    kwargs: dict[str, object] = {
        "cwd": PROJECT_DIR,
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    return kwargs


def spawn_turn_watcher(token: str, transcript_path: str, turn_id: str, provider: str = "", project: str = "") -> None:
    if not transcript_path or not turn_id:
        return
    subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--watch-turn",
            token,
            transcript_path,
            turn_id,
            provider,
            project,
        ],
        **detached_kwargs(),
    )


def set_hook_status(status: str, provider: str = "", project: str = "") -> str:
    config = load_config()
    command = "GREEN" if status.upper() == "DONE" else status.upper()
    token = uuid.uuid4().hex
    with state_lock():
        write_state(token, command, provider, project)
        send_with_bridge_start(command, provider, project)
    if command == "GREEN":
        delay = float(config["green_hold_seconds"])
        if delay > 0:
            spawn_red_timer(token, delay, provider, project)
    return token


def red_after(token: str, delay: float, provider: str = "", project: str = "") -> None:
    time.sleep(max(0.0, delay))
    with state_lock():
        if read_state(provider, project)["token"] != token:
            return
        write_state(uuid.uuid4().hex, "RED", provider, project)
        send_with_bridge_start("RED", provider, project)


def complete_if_current(token: str, provider: str = "", project: str = "") -> None:
    config = load_config()
    green_token = ""
    with state_lock():
        state = read_state(provider, project)
        if state["token"] != token or state["status"] != "YELLOW":
            return
        green_token = uuid.uuid4().hex
        write_state(green_token, "GREEN", provider, project)
        send_with_bridge_start("GREEN", provider, project)
    delay = float(config["green_hold_seconds"])
    if delay > 0:
        spawn_red_timer(green_token, delay, provider, project)


def cancel_if_current(token: str, provider: str = "", project: str = "") -> None:
    """Return an interrupted turn to idle without firing the completion buzzer."""
    with state_lock():
        state = read_state(provider, project)
        if state["token"] != token or state["status"] != "YELLOW":
            return
        write_state(uuid.uuid4().hex, "RED", provider, project)
        send_with_bridge_start("RED", provider, project)


def watch_turn(token: str, transcript_path: str, turn_id: str, provider: str = "", project: str = "") -> None:
    path = Path(transcript_path)
    deadline = time.monotonic() + 21600
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.2)
    if not path.exists():
        return

    with path.open("r", encoding="utf-8", errors="replace") as transcript:
        while time.monotonic() < deadline:
            line = transcript.readline()
            if not line:
                if read_state(provider, project)["token"] != token:
                    return
                time.sleep(0.2)
                continue
            if turn_id not in line:
                continue
            if any(
                marker in line
                for marker in (
                    '"type":"turn_aborted"',
                    '"type": "turn_aborted"',
                    '"type":"turn_cancelled"',
                    '"type": "turn_cancelled"',
                    '"type":"turn_canceled"',
                    '"type": "turn_canceled"',
                )
            ):
                cancel_if_current(token, provider, project)
                return
            if any(
                marker in line
                for marker in (
                    '"type":"task_complete"',
                    '"type": "task_complete"',
                    '"type":"turn_complete"',
                    '"type": "turn_complete"',
                )
            ):
                complete_if_current(token, provider, project)
                return


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) in (3, 4, 5) and args[0] == "--red-after":
        try:
            red_after(args[1], float(args[2]), args[3] if len(args) >= 4 else "", args[4] if len(args) == 5 else "")
        except Exception:
            pass
        return 0
    if len(args) in (4, 5, 6) and args[0] == "--watch-turn":
        try:
            watch_turn(args[1], args[2], args[3], args[4] if len(args) >= 5 else "", args[5] if len(args) == 6 else "")
        except Exception:
            pass
        return 0
    if len(args) != 1:
        return 0
    try:
        try:
            event = json.loads(sys.stdin.read() or "{}")
        except (OSError, ValueError):
            event = {}
        event = event if isinstance(event, dict) else {}
        session_id = session_from_payload(event)
        configured = [
            str(item.get("project", ""))
            for item in load_config().get("devices", [])
            if str(item.get("provider", "")) == "codex"
        ]
        project = resolve_event_project("codex", event, configured)
        project = register_project("codex", project, session_id)
        token = set_hook_status(args[0], "codex", project)
        ensure_typing_watcher()
        if args[0].upper() == "YELLOW":
            if isinstance(event, dict):
                spawn_turn_watcher(
                    token,
                    str(event.get("transcript_path") or ""),
                    str(event.get("turn_id") or ""),
                    "codex",
                    project,
                )
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

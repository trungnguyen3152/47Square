"""Project discovery and matching shared by the UI and hook adapters."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parent
PROJECTS_PATH = ROOT / ".ai_status_projects.json"
PROJECT_FIELDS = (
    "cwd", "project", "project_path", "projectPath", "workspace",
    "workspace_path", "workspacePath", "working_directory", "workingDirectory",
    "repo", "repo_path", "repoPath",
)
SESSION_FIELDS = (
    "session_id", "sessionId", "conversation_id", "conversationId", "turn_id", "turnId",
)


def canonical_project(value: str | os.PathLike[str] | None) -> str:
    """Return a stable Windows project key without requiring the path to exist."""
    raw = str(value or "").strip().strip('"')
    if not raw:
        return ""
    if raw.startswith("file:"):
        parsed = urlparse(raw)
        raw = unquote(parsed.path)
        if parsed.netloc:
            raw = f"//{parsed.netloc}{raw}"
        if len(raw) >= 3 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
    try:
        return os.path.normcase(os.path.abspath(os.path.expandvars(os.path.expanduser(raw))))
    except (OSError, ValueError):
        return os.path.normcase(os.path.normpath(raw))


def is_internal_project(value: str | os.PathLike[str] | None) -> bool:
    project = canonical_project(value)
    if not project:
        return False
    roots = [
        Path.home() / name for name in (".gemini", ".codex", ".cursor", ".copilot")
    ] + [
        Path(os.environ.get("APPDATA", "")),
        Path(os.environ.get("LOCALAPPDATA", "")),
        Path(os.environ.get("WINDIR", "C:/Windows")),
        Path(os.environ.get("ProgramFiles", "C:/Program Files")),
    ]
    for value_root in roots:
        root = canonical_project(value_root)
        if not root:
            continue
        try:
            if os.path.commonpath([project, root]) == root:
                return True
        except ValueError:
            continue
    return False


def project_from_payload(payload: dict[str, Any]) -> str:
    """Extract a workspace root from the common hook payload dialects."""
    for field in PROJECT_FIELDS:
        value = payload.get(field)
        if isinstance(value, str) and value.strip():
            return canonical_project(value)
    roots = (
        payload.get("workspacePaths")
        or payload.get("workspace_paths")
        or payload.get("workspace_roots")
        or payload.get("workspaceRoots")
    )
    if isinstance(roots, list):
        for value in roots:
            if isinstance(value, str) and value.strip():
                return canonical_project(value)
    workspace = payload.get("workspace")
    if isinstance(workspace, dict):
        for field in ("path", "folder", "uri", "fileUri"):
            value = workspace.get(field)
            if isinstance(value, str) and value.strip():
                return canonical_project(value)
    return ""


def project_from_environment(provider: str) -> str:
    """Read provider-documented event-scoped workspace environment values."""
    fields = {
        "cursor": ("CURSOR_PROJECT_DIR", "CLAUDE_PROJECT_DIR"),
        "claude-code": ("CLAUDE_PROJECT_DIR",),
    }.get(provider, ())
    for field in fields:
        value = os.environ.get(field, "")
        if value.strip():
            project = canonical_project(value)
            if not is_internal_project(project):
                return project
    return ""


def _read_registry() -> dict[str, list[dict[str, Any]]]:
    try:
        value = json.loads(PROJECTS_PATH.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def session_from_payload(payload: dict[str, Any]) -> str:
    for field in SESSION_FIELDS:
        value = payload.get(field)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def project_for_session(provider: str, session_id: str) -> str:
    if not provider or not session_id:
        return ""
    for item in _read_registry().get(provider, []):
        if isinstance(item, dict) and session_id in item.get("sessions", []):
            return canonical_project(item.get("path"))
    return ""


def register_project(provider: str, project: str, session_id: str = "") -> str:
    project = canonical_project(project)
    if not provider or not project:
        return project
    if is_internal_project(project):
        return ""
    registry = _read_registry()
    records = registry.setdefault(provider, [])
    previous = next((item for item in records if canonical_project(item.get("path")) == project), {})
    sessions = [str(value) for value in previous.get("sessions", []) if str(value)] if isinstance(previous, dict) else []
    if session_id:
        sessions = [session_id] + [value for value in sessions if value != session_id]
    records = [item for item in records if canonical_project(item.get("path")) != project]
    records.insert(0, {"path": project, "seen": time.time(), "sessions": sessions[:50]})
    registry[provider] = records[:100]
    temporary = PROJECTS_PATH.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(PROJECTS_PATH)
    return project


def _paths_from_json(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"folder", "folderUri", "fileUri", "workspace", "workspaceUri", "path"} and isinstance(child, str):
                yield child
            else:
                yield from _paths_from_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _paths_from_json(child)


def _recent_storage_files(provider: str) -> list[Path]:
    appdata = Path(os.environ.get("APPDATA", ""))
    names = {
        "cursor": ("Cursor",),
        "copilot-vscode": ("Code",),
        "antigravity": ("Antigravity", "Antigravity IDE"),
        "codex": ("Codex",),
    }.get(provider, ())
    files: list[Path] = []
    for name in names:
        base = appdata / name
        files.append(base / "storage.json")
        files.append(base / "User" / "globalStorage" / "storage.json")
        files.extend((base / "User" / "workspaceStorage").glob("*/workspace.json"))
    return files


def open_projects_for_provider(provider: str) -> list[str]:
    """Read currently open workspaces recorded by VS Code based clients."""
    appdata = Path(os.environ.get("APPDATA", ""))
    app_name = {
        "antigravity": "Antigravity IDE",
        "cursor": "Cursor",
        "copilot-vscode": "Code",
    }.get(provider)
    if not app_name:
        return []
    source = appdata / app_name / "User" / "globalStorage" / "storage.json"
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
        windows_state = document.get("windowsState", {})
    except (OSError, ValueError, AttributeError):
        return []
    if not isinstance(windows_state, dict):
        return []
    windows = [windows_state.get("lastActiveWindow", {})]
    opened = windows_state.get("openedWindows", [])
    if isinstance(opened, list):
        windows.extend(opened)
    projects: list[str] = []
    for window in windows:
        if not isinstance(window, dict):
            continue
        for field in ("folder", "workspace", "workspaceUri"):
            value = window.get(field)
            if isinstance(value, str) and value.strip():
                project = canonical_project(value)
                if project and not is_internal_project(project) and project not in projects:
                    projects.append(project)
                break
    return projects


def active_project_for_provider(provider: str) -> str:
    """Return an editor fallback only when exactly one workspace is open."""
    projects = open_projects_for_provider(provider)
    return projects[0] if len(projects) == 1 else ""


def discover_projects(provider: str, configured: Iterable[str] = ()) -> list[str]:
    """Return known projects, newest hook discoveries first."""
    found: list[str] = []

    def add(value: str) -> None:
        project = canonical_project(value)
        if project and not is_internal_project(project) and project not in found:
            found.append(project)

    for item in _read_registry().get(provider, []):
        if isinstance(item, dict):
            add(str(item.get("path", "")))
    for value in configured:
        add(value)
    for source in _recent_storage_files(provider):
        try:
            document = json.loads(source.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for value in _paths_from_json(document):
            add(value)
    return found


def project_for_window(provider: str, title: str, configured: Iterable[str] = ()) -> str:
    """Resolve the foreground workspace without inspecting typed text."""
    projects = discover_projects(provider, configured)
    folded = title.casefold()
    matches = [p for p in projects if Path(p).name.casefold() in folded]
    if len(matches) == 1:
        return matches[0]
    # Never guess from the sole configured assignment. A newly opened project
    # may not be in recent history yet; guessing here made its first keystroke
    # control the previously selected project's light.
    return ""


def _credible_working_directory() -> str:
    cwd = canonical_project(Path.cwd())
    if not cwd:
        return ""
    candidate = Path(cwd)
    if candidate.parent == candidate:
        return ""
    unsafe_roots = [
        canonical_project(Path.home()),
        canonical_project(os.environ.get("APPDATA", "")),
        canonical_project(os.environ.get("LOCALAPPDATA", "")),
        canonical_project(os.environ.get("WINDIR", "C:/Windows")),
        canonical_project(os.environ.get("ProgramFiles", "C:/Program Files")),
        canonical_project(Path.home() / ".gemini"),
        canonical_project(Path.home() / ".codex"),
        canonical_project(Path.home() / ".cursor"),
        canonical_project(Path.home() / ".copilot"),
    ]
    # The home folder itself and application/system trees are launcher working
    # directories, not user projects. Subfolders such as Desktop/Documents are
    # intentionally allowed.
    if cwd == unsafe_roots[0]:
        return ""
    for root in unsafe_roots[1:]:
        if not root:
            continue
        try:
            if os.path.commonpath([cwd, root]) == root:
                return ""
        except ValueError:
            continue
    return cwd


def resolve_event_project(provider: str, payload: dict[str, Any], configured: Iterable[str] = ()) -> str:
    """Resolve an event project while keeping multi-device routing unambiguous.

    Some clients omit cwd from lifecycle payloads. Their hook process usually
    inherits the workspace cwd; if that is not usable, a single configured
    project is still unambiguous. With two or more configured projects we never
    guess.
    """
    session_id = session_from_payload(payload)
    direct = project_from_payload(payload)
    if direct and not is_internal_project(direct):
        return direct
    environment_project = project_from_environment(provider)
    if environment_project:
        return environment_project
    remembered = project_for_session(provider, session_id)
    if remembered and not is_internal_project(remembered):
        return remembered
    active = active_project_for_provider(provider)
    if active:
        return active
    configured_projects: list[str] = []
    for value in configured:
        project = canonical_project(value)
        if project and project not in configured_projects:
            configured_projects.append(project)
    cwd = _credible_working_directory()
    if cwd:
        # Returning a different real cwd is deliberate: the router will reject
        # it instead of falsely attributing it to the selected project.
        return cwd
    if len(open_projects_for_provider(provider)) > 1:
        # Multiple windows plus no event-scoped project is ambiguous. Ignoring
        # the event is the only way to guarantee cross-project isolation.
        return ""
    if len(configured_projects) == 1:
        return configured_projects[0]
    return ""

"""Detect and install optional AI client adapters on demand."""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parent
USER_HOME = Path.home()
APPDATA = Path(os.environ.get("APPDATA", USER_HOME / "AppData/Roaming"))
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", USER_HOME / "AppData/Local"))


@dataclass(frozen=True)
class Integration:
    provider: str
    name: str
    adapter: str
    destination: Path | None


INTEGRATIONS = (
    Integration("codex", "Codex", "Hook toàn cục", USER_HOME / ".codex/hooks.json"),
    Integration("claude-desktop", "Claude Desktop", "Bộ dò ứng dụng", None),
    Integration("antigravity", "Antigravity", "Plugin IDE", USER_HOME / ".gemini/config/plugins/ai-status-light/hooks.json"),
    Integration("cursor", "Cursor", "Hook toàn cục", USER_HOME / ".cursor/hooks.json"),
    Integration("copilot-app", "GitHub Copilot", "Hook ứng dụng", USER_HOME / ".copilot/hooks/ai-status-app.json"),
    Integration("copilot-vscode", "Copilot · VS Code", "Hook VS Code", USER_HOME / ".copilot/hooks/ai-status-light.json"),
)


def by_provider(provider: str) -> Integration:
    return next(item for item in INTEGRATIONS if item.provider == provider)


def _contains_adapter(path: Path | None) -> bool:
    if path is None or not path.exists():
        return False
    try:
        return "ai_status" in path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def app_installed(provider: str) -> bool:
    checks = {
        "codex": [LOCALAPPDATA / "Programs/Codex/Codex.exe", LOCALAPPDATA / "OpenAI/Codex.exe"],
        "claude-desktop": list((LOCALAPPDATA / "Packages").glob("Claude_*")),
        "antigravity": [USER_HOME / ".antigravity-ide", APPDATA / "Antigravity IDE"],
        "cursor": [LOCALAPPDATA / "Programs/cursor/Cursor.exe"],
        "copilot-app": list((LOCALAPPDATA / "Microsoft/WindowsApps").glob("github.exe")),
        "copilot-vscode": [APPDATA / "Code/User/settings.json"],
    }
    # Hook detection is enough for clients whose executable lives in a package
    # directory that Windows intentionally hides from normal enumeration.
    item = by_provider(provider)
    return any(path.exists() for path in checks.get(provider, [])) or _contains_adapter(item.destination)


def integration_state(provider: str) -> tuple[str, str]:
    item = by_provider(provider)
    if provider == "claude-desktop":
        return ("active", "Hoạt động") if app_installed(provider) else ("missing-app", "Chưa cài ứng dụng")
    if _contains_adapter(item.destination):
        return "active", "Hoạt động"
    if app_installed(provider):
        return "needs-setup", "Cần thiết lập"
    return "missing-app", "Chưa cài ứng dụng"


def _copy_template(relative_source: str, destination: Path) -> None:
    source = ROOT / relative_source
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def _install_codex(destination: Path) -> None:
    try:
        document = json.loads(destination.read_text(encoding="utf-8")) if destination.exists() else {}
    except (OSError, ValueError):
        document = {}
    if not isinstance(document, dict):
        document = {}
    document.setdefault("description", "AI Status Light lifecycle hooks")
    hooks = document.setdefault("hooks", {})
    commands = {
        "SessionStart": "red",
        "UserPromptSubmit": "yellow",
        "Stop": "done",
        "Interrupt": "red",
    }
    for event, state in commands.items():
        command = f'py -3 "{ROOT / "ai_status_hook.py"}" {state}'
        entry = {"hooks": [{"type": "command", "commandWindows": command, "timeout": 3}]}
        existing = hooks.get(event)
        if not isinstance(existing, list):
            hooks[event] = [entry]
        elif not any("ai_status_hook.py" in json.dumps(value) for value in existing):
            existing.append(entry)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(document, indent=2, ensure_ascii=False), encoding="utf-8")


def _enable_vscode_hooks() -> None:
    settings_path = APPDATA / "Code/User/settings.json"
    try:
        settings = json.loads(settings_path.read_text(encoding="utf-8")) if settings_path.exists() else {}
    except (OSError, ValueError):
        # Do not overwrite JSONC or malformed user settings. The hook file is
        # still installed and the UI reports that setup needs attention.
        return
    if not isinstance(settings, dict):
        return
    settings["chat.useHooks"] = True
    locations = settings.setdefault("chat.hookFilesLocations", {})
    if isinstance(locations, dict):
        locations["~/.copilot/hooks"] = True
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")


def install_integration(provider: str) -> tuple[str, str]:
    item = by_provider(provider)
    if not app_installed(provider):
        return "missing-app", "Hãy cài ứng dụng AI trước"
    if provider == "codex":
        assert item.destination is not None
        _install_codex(item.destination)
    elif provider == "claude-desktop":
        # Claude Chat has no lifecycle hook. The bundled foreground/activity
        # watcher is its complete adapter, so no external driver is required.
        pass
    elif provider == "antigravity":
        assert item.destination is not None
        _copy_template("integration_templates/antigravity/hooks.json", item.destination)
        _copy_template("integration_templates/antigravity/plugin.json", item.destination.with_name("plugin.json"))
    elif provider == "cursor":
        assert item.destination is not None
        _copy_template("integration_templates/cursor/hooks.json", item.destination)
    elif provider == "copilot-app":
        assert item.destination is not None
        _copy_template("integration_templates/copilot-app/ai-status-light.json", item.destination)
    elif provider == "copilot-vscode":
        assert item.destination is not None
        _copy_template("integration_templates/copilot-vscode/ai-status-light.json", item.destination)
        _enable_vscode_hooks()
    return integration_state(provider)

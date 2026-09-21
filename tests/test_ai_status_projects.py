import unittest
from pathlib import Path
from unittest.mock import patch

from ai_status_projects import active_project_for_provider, canonical_project, open_projects_for_provider, project_from_environment, project_from_payload, project_for_window, resolve_event_project, session_from_payload


class ProjectDiscoveryTests(unittest.TestCase):
    def test_extracts_common_hook_cwd(self):
        self.assertEqual(project_from_payload({"cwd": "C:/Work/Demo"}), canonical_project("C:/Work/Demo"))

    def test_extracts_workspace_roots(self):
        self.assertEqual(
            project_from_payload({"workspace_roots": ["C:/Work/One"]}),
            canonical_project("C:/Work/One"),
        )

    def test_extracts_antigravity_workspace_paths(self):
        self.assertEqual(
            project_from_payload({"workspacePaths": ["D:/Test1"]}),
            canonical_project("D:/Test1"),
        )

    def test_payload_workspace_beats_stale_active_window(self):
        with patch(
            "ai_status_projects.active_project_for_provider",
            return_value=canonical_project("D:/Xam3D"),
        ):
            self.assertEqual(
                resolve_event_project(
                    "antigravity",
                    {"conversationId": "new-window", "workspacePaths": ["D:/Test1"]},
                    ["D:/Xam3D"],
                ),
                canonical_project("D:/Test1"),
            )

    def test_extracts_session_alias(self):
        self.assertEqual(session_from_payload({"conversationId": "chat-1"}), "chat-1")

    def test_foreground_title_matches_project_name(self):
        projects = ["C:/Work/Alpha", "C:/Work/Beta"]
        self.assertEqual(
            project_for_window("unused-provider", "main.py - Beta - Cursor", projects),
            canonical_project("C:/Work/Beta"),
        )

    def test_single_configured_project_is_safe_fallback(self):
        with patch.object(Path, "cwd", return_value=Path("C:/Windows/System32")), patch(
            "ai_status_projects.active_project_for_provider", return_value=""
        ), patch(
            "ai_status_projects.open_projects_for_provider", return_value=[]
        ):
            self.assertEqual(
                resolve_event_project("antigravity", {}, ["C:/Work/Only"]),
                canonical_project("C:/Work/Only"),
            )

    def test_multiple_projects_are_never_guessed(self):
        with patch.object(Path, "cwd", return_value=Path("C:/Windows/System32")), patch(
            "ai_status_projects.active_project_for_provider", return_value=""
        ):
            self.assertEqual(
                resolve_event_project("antigravity", {}, ["C:/Work/One", "C:/Work/Two"]),
                "",
            )

    def test_single_project_fallback_is_shared_by_all_six_ai(self):
        providers = (
            "codex", "claude-desktop", "antigravity", "cursor",
            "copilot-app", "copilot-vscode",
        )
        for provider in providers:
            with self.subTest(provider=provider):
                with patch.object(Path, "cwd", return_value=Path("C:/Windows/System32")), patch(
                    "ai_status_projects.active_project_for_provider", return_value=""
                ), patch(
                    "ai_status_projects.open_projects_for_provider", return_value=[]
                ):
                    self.assertEqual(
                        resolve_event_project(provider, {}, ["C:/Work/Shared"]),
                        canonical_project("C:/Work/Shared"),
                    )

    def test_new_project_cwd_is_not_rewritten_to_selected_project(self):
        with patch.object(Path, "cwd", return_value=Path("C:/Work/NewProject")):
            self.assertEqual(
                resolve_event_project("codex", {}, ["C:/Work/SelectedProject"]),
                canonical_project("C:/Work/NewProject"),
            )

    def test_unknown_window_never_guesses_selected_project(self):
        self.assertEqual(
            project_for_window("unused-provider", "New chat - Codex", ["C:/Work/SelectedProject"]),
            "",
        )

    def test_active_ide_project_wins_over_internal_plugin_cwd(self):
        with (
            patch("ai_status_projects.project_for_session", return_value=""),
            patch("ai_status_projects.active_project_for_provider", return_value=canonical_project("C:/Work/Active")),
            patch.object(Path, "cwd", return_value=Path.home() / ".gemini/config/plugins/ai-status-light"),
        ):
            self.assertEqual(
                resolve_event_project("antigravity", {}, ["C:/Work/Active"]),
                canonical_project("C:/Work/Active"),
            )

    def test_internal_remembered_project_is_ignored(self):
        with (
            patch("ai_status_projects.project_for_session", return_value=canonical_project(Path.home() / ".gemini/config/plugins/ai-status-light")),
            patch("ai_status_projects.active_project_for_provider", return_value=canonical_project("C:/Work/Active")),
        ):
            self.assertEqual(
                resolve_event_project("antigravity", {}, ["C:/Work/Active"]),
                canonical_project("C:/Work/Active"),
            )

    def test_all_provider_payload_dialects_resolve_without_window_guessing(self):
        cases = {
            "codex": {"cwd": "C:/Projects/Codex"},
            "cursor": {"workspace_roots": ["C:/Projects/Cursor"]},
            "antigravity": {"workspacePaths": ["C:/Projects/Antigravity"]},
            "copilot-app": {"cwd": "C:/Projects/CopilotApp"},
            "copilot-vscode": {"cwd": "C:/Projects/CopilotVSCode"},
        }
        with patch("ai_status_projects.active_project_for_provider", side_effect=AssertionError("must not guess")):
            for provider, payload in cases.items():
                with self.subTest(provider=provider):
                    expected = next(iter(payload.values()))
                    expected = expected[0] if isinstance(expected, list) else expected
                    self.assertEqual(
                        resolve_event_project(provider, payload, []),
                        canonical_project(expected),
                    )

    def test_active_project_is_disabled_when_multiple_windows_are_open(self):
        document = {
            "windowsState": {
                "lastActiveWindow": {"folder": "file:///d%3A/One"},
                "openedWindows": [{"folder": "file:///d%3A/Two"}],
            }
        }
        with patch.object(Path, "read_text", return_value=__import__("json").dumps(document)):
            self.assertEqual(
                open_projects_for_provider("cursor"),
                [canonical_project("D:/One"), canonical_project("D:/Two")],
            )
            self.assertEqual(active_project_for_provider("cursor"), "")

    def test_ambiguous_multiwindow_event_is_ignored(self):
        with (
            patch("ai_status_projects.project_for_session", return_value=""),
            patch("ai_status_projects.active_project_for_provider", return_value=""),
            patch("ai_status_projects.open_projects_for_provider", return_value=["D:/One", "D:/Two"]),
            patch("ai_status_projects._credible_working_directory", return_value=""),
        ):
            self.assertEqual(resolve_event_project("cursor", {}, ["D:/One"]), "")

    def test_cursor_documented_project_environment(self):
        with patch.dict("os.environ", {"CURSOR_PROJECT_DIR": "D:/CursorProject"}, clear=False):
            self.assertEqual(
                project_from_environment("cursor"), canonical_project("D:/CursorProject")
            )


if __name__ == "__main__":
    unittest.main()

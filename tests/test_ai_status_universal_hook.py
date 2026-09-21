import io
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import ai_status_universal_hook as universal


class UniversalHookTests(unittest.TestCase):
    def test_all_providers_are_supported(self):
        self.assertEqual(
            universal.PROVIDERS,
            {
                "codex",
                "claude-desktop",
                "claude-code",
                "antigravity",
                "cursor",
                "copilot-app",
                "copilot-vscode",
                "copilot-cli",
            },
        )

    def test_payload_parsing_and_session_aliases(self):
        payload = universal.read_payload(io.StringIO('{"conversationId":"abc"}'))
        self.assertEqual(universal.session_id_from(payload), "abc")

    def test_invalid_stdin_is_ignored(self):
        self.assertEqual(universal.read_payload(io.StringIO("not-json")), {})

    def test_dry_run_logs_without_touching_hardware(self):
        log_path = Path(__file__).with_name(".universal-hook-test.jsonl")
        try:
            with (
                patch.object(universal, "EVENT_LOG", log_path),
                patch.object(universal, "set_hook_status") as status,
                patch("sys.stdin", io.StringIO('{"session_id":"s1"}')),
                patch("sys.stdout", new_callable=io.StringIO),
            ):
                result = universal.main(
                    ["--provider", "cursor", "--event", "busy", "--dry-run"]
                )
            self.assertEqual(result, 0)
            status.assert_not_called()
            record = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(record["provider"], "cursor")
            self.assertEqual(record["status"], "YELLOW")
            self.assertEqual(record["session_id"], "s1")
        finally:
            log_path.unlink(missing_ok=True)

    def test_hook_is_fail_open(self):
        with (
            patch.object(universal, "append_event", side_effect=OSError("disk")),
            patch("sys.stdin", io.StringIO("{}")),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            result = universal.main(["--provider", "codex", "--event", "busy"])
        self.assertEqual(result, 0)
        self.assertEqual(output.getvalue().strip(), "{}")

    def test_antigravity_stop_returns_required_decision(self):
        with (
            patch.object(universal, "run_hook"),
            patch("sys.stdin", io.StringIO("{}")),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            result = universal.main(
                ["--provider", "antigravity", "--event", "done"]
            )
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), {"decision": "allow"})

    def test_cursor_prompt_explicitly_continues(self):
        with (
            patch.object(universal, "run_hook"),
            patch.object(universal, "read_payload", return_value={}),
            patch("sys.stdout", new_callable=io.StringIO) as output,
        ):
            result = universal.main(["--provider", "cursor", "--event", "busy"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), {"continue": True})

    def test_aborted_stop_returns_red_instead_of_green(self):
        with (
            patch.object(universal, "append_event") as append,
            patch.object(universal, "set_hook_status") as status,
            patch.object(universal, "resolve_event_project", return_value=""),
        ):
            universal.run_hook("cursor", "done", {"status": "aborted"})
        append.assert_called_once_with("cursor", "cancel", {"status": "aborted"}, "")
        status.assert_called_once_with("RED", "cursor", "")


if __name__ == "__main__":
    unittest.main()

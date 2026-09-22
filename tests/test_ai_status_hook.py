import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

import ai_status_hook as hook


class HookCancellationTests(unittest.TestCase):
    def test_cancelled_yellow_turn_returns_red(self):
        with (
            patch.object(hook, "state_lock", return_value=nullcontext()),
            patch.object(hook, "read_state", return_value={"token": "turn-1", "status": "YELLOW"}),
            patch.object(hook, "write_state") as write,
            patch.object(hook, "send_with_bridge_start") as send,
            patch.object(hook, "append_status_event") as append,
        ):
            hook.cancel_if_current("turn-1", "codex")
        self.assertEqual(write.call_args.args[1:], ("RED", "codex", ""))
        send.assert_called_once_with("RED", "codex", "")
        append.assert_called_once_with("RED", "codex", "", "cancel")

    def test_transcript_turn_aborted_uses_cancel_path(self):
        transcript = Path(__file__).with_name(".turn-aborted-test.jsonl")
        try:
            transcript.write_text(
                '{"type":"event_msg","payload":{"type":"turn_aborted","turn_id":"turn-1"}}\n',
                encoding="utf-8",
            )
            with patch.object(hook, "cancel_if_current") as cancel:
                hook.watch_turn("token-1", str(transcript), "turn-1", "codex")
        finally:
            transcript.unlink(missing_ok=True)
        cancel.assert_called_once_with("token-1", "codex", "")


if __name__ == "__main__":
    unittest.main()

import unittest
from contextlib import nullcontext
from unittest.mock import patch

import ai_status_typing_watcher as watcher

from ai_status_typing_watcher import (
    VK_BACK,
    VK_RETURN,
    VK_SPACE,
    VK_V,
    is_submit_key,
    is_typing_key,
)


class TypingWatcherTests(unittest.TestCase):
    def test_letters_numbers_and_editing_keys_count_as_typing(self):
        self.assertTrue(is_typing_key(ord("A")))
        self.assertTrue(is_typing_key(ord("7")))
        self.assertTrue(is_typing_key(VK_SPACE))
        self.assertTrue(is_typing_key(VK_BACK))

    def test_shortcuts_are_ignored_except_paste(self):
        self.assertFalse(is_typing_key(ord("C"), control=True))
        self.assertTrue(is_typing_key(VK_V, control=True))
        self.assertFalse(is_typing_key(ord("A"), alt=True))
        self.assertFalse(is_typing_key(ord("A"), win=True))

    def test_claude_submit_is_plain_enter_only(self):
        self.assertTrue(is_submit_key(VK_RETURN))
        self.assertFalse(is_submit_key(VK_RETURN, shift=True))
        self.assertFalse(is_submit_key(VK_RETURN, control=True))
        self.assertFalse(is_submit_key(ord("A")))

    def test_first_key_after_ai_switch_overrides_stale_state(self):
        with (
            patch.object(watcher, "state_lock", return_value=nullcontext()),
            patch.object(watcher, "read_state", return_value={"token": "old", "status": "YELLOW"}),
            patch.object(watcher, "write_state") as write,
            patch.object(watcher, "send_with_bridge_start") as send,
        ):
            changed = watcher.mark_typing_started("cursor")
        self.assertTrue(changed)
        self.assertEqual(write.call_args.args[1:], ("RED", "cursor", ""))
        send.assert_called_once_with("RED", "cursor", "")


if __name__ == "__main__":
    unittest.main()

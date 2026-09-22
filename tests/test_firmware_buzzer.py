import unittest
from pathlib import Path


class FirmwareBuzzerTests(unittest.TestCase):
    def test_active_buzzer_uses_one_power_envelope_per_beep(self):
        source = (Path(__file__).parents[1] / "firmware" / "firmware.ino").read_text(encoding="utf-8")
        body = source.split("void beepOnce() {", 1)[1].split("}", 1)[0]
        self.assertIn("writeOutput(BUZZER_PIN, true);", body)
        self.assertIn("writeOutput(BUZZER_PIN, false);", body)
        self.assertNotIn("tone(", body)
        self.assertNotIn("noTone(", body)


if __name__ == "__main__":
    unittest.main()

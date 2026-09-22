import unittest
from pathlib import Path


class FirmwareBuzzerTests(unittest.TestCase):
    def test_passive_buzzer_uses_one_tone_envelope_per_beep(self):
        source = (Path(__file__).parents[1] / "firmware" / "firmware.ino").read_text(encoding="utf-8")
        body = source.split("void beepOnce() {", 1)[1].split("}", 1)[0]
        self.assertIn("tone(BUZZER_PIN, 2400);", body)
        self.assertIn("delay(180);", body)
        self.assertIn("noTone(BUZZER_PIN);", body)
        self.assertIn("writeOutput(BUZZER_PIN, false);", body)


if __name__ == "__main__":
    unittest.main()

import unittest

from ai_status_app import device_display_names


class DeviceDisplayNameTests(unittest.TestCase):
    def test_hardware_names_are_replaced_by_ordered_public_labels(self):
        labels = device_display_names([
            {"port": "COM5", "name": "XIAO ESP32-C3"},
            {"port": "COM7", "name": "Some Other Board"},
        ])
        self.assertEqual(labels, {
            "COM5": "AI Status Light - 1",
            "COM7": "AI Status Light - 2",
        })


if __name__ == "__main__":
    unittest.main()

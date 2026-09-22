import hashlib
import hmac
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from ai_status_core import DEFAULT_CONFIG, SerialController, StatusError, load_config, normalize_decor_effect, normalize_status, send_decor_to_bridge, send_to_bridge, stop_decor


class StatusTests(unittest.TestCase):
    def test_normalize_status(self):
        self.assertEqual(normalize_status(" green \n"), "GREEN")

    def test_rejects_unknown_status(self):
        with self.assertRaises(StatusError):
            normalize_status("blue")

    def test_normalize_all_decor_effect_names(self):
        self.assertEqual(normalize_decor_effect("smooth transition"), "SMOOTH")
        self.assertEqual(normalize_decor_effect("scanner / cylon"), "SCANNER")

    def test_rejects_unknown_decor_effect(self):
        with self.assertRaises(StatusError):
            normalize_decor_effect("disco-unknown")

    def test_load_config_merges_defaults(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps({"serial_port": "COM9"})
        ):
            config = load_config("test-config.json")
        self.assertEqual(config["serial_port"], "COM9")
        self.assertEqual(config["port"], DEFAULT_CONFIG["port"])

    def test_load_config_rejects_unknown_keys(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps({"typo": 1})
        ):
            with self.assertRaises(ValueError):
                load_config("test-config.json")

    def test_load_config_rejects_negative_green_hold(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps({"green_hold_seconds": -1})
        ):
            with self.assertRaises(ValueError):
                load_config("test-config.json")

    def test_load_config_rejects_invalid_typing_process_names(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps({"typing_process_names": "ChatGPT.exe"})
        ):
            with self.assertRaises(ValueError):
                load_config("test-config.json")

    def test_load_config_allows_same_provider_for_different_projects(self):
        devices = [
            {"port": "COM5", "provider": "codex", "project": "C:/one"},
            {"port": "COM7", "provider": "codex", "project": "C:/two"},
        ]
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps({"devices": devices})
        ):
            config = load_config("test-config.json")
        self.assertEqual(len(config["devices"]), 2)

    def test_load_config_rejects_duplicate_ai_project_assignment(self):
        devices = [
            {"port": "COM5", "provider": "codex", "project": "C:/same"},
            {"port": "COM7", "provider": "codex", "project": "c:/same"},
        ]
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps({"devices": devices})
        ):
            with self.assertRaises(ValueError):
                load_config("test-config.json")

    def test_load_config_rejects_invalid_beep_count(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path,
            "read_text",
            return_value=json.dumps({"status_beeps": {"RED": 0, "YELLOW": 4, "GREEN": 2}}),
        ):
            with self.assertRaises(ValueError):
                load_config("test-config.json")

    def test_load_config_rejects_invalid_device_auth_key(self):
        devices = [{"port": "COM5", "auth_key": "not-a-key"}]
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps({"devices": devices})
        ):
            with self.assertRaises(ValueError):
                load_config("test-config.json")

    def test_load_config_rejects_invalid_bridge_auth_token(self):
        with patch.object(Path, "exists", return_value=True), patch.object(
            Path, "read_text", return_value=json.dumps({"bridge_auth_token": "short"})
        ):
            with self.assertRaises(ValueError):
                load_config("test-config.json")

    def test_serial_controller_authenticates_each_command(self):
        key = "11" * 32
        nonce = "22" * 16
        connection = MagicMock()
        connection.is_open = True
        connection.readline.side_effect = [f"NONCE {nonce}\n".encode(), b"OK\n"]
        controller = SerialController({**DEFAULT_CONFIG, "device_auth_key": key})
        controller._serial = connection
        controller.send("GREEN", 2)
        signature = hmac.new(bytes.fromhex(key), f"{nonce}|GREEN 2".encode(), hashlib.sha256).hexdigest()
        self.assertEqual(
            [call.args[0] for call in connection.write.call_args_list],
            [b"CHALLENGE\n", f"AUTH {signature} GREEN 2\n".encode()],
        )

    def test_serial_controller_probe_requires_valid_device_info(self):
        connection = MagicMock()
        connection.is_open = True
        controller = SerialController({**DEFAULT_CONFIG, "device_auth_key": "11" * 32})
        controller._serial = connection
        with patch.object(controller, "_authenticated_request", return_value="INFO 1.1.0 ABC123"):
            self.assertEqual(controller.probe(), ("1.1.0", "ABC123"))

    def test_serial_controller_probe_rejects_stale_serial_handle(self):
        connection = MagicMock()
        connection.is_open = True
        controller = SerialController({**DEFAULT_CONFIG, "device_auth_key": "11" * 32})
        controller._serial = connection
        with patch.object(controller, "_authenticated_request", side_effect=OSError("device gone")):
            with self.assertRaises(OSError):
                controller.probe()
        connection.close.assert_called_once_with()
        self.assertIsNone(controller._serial)

    @patch("ai_status_core.socket.create_connection")
    def test_bridge_protocol(self, create_connection):
        client = MagicMock()
        client.__enter__.return_value = client
        client.recv.return_value = b"OK\n"
        create_connection.return_value = client
        send_to_bridge("yellow", DEFAULT_CONFIG)
        client.sendall.assert_called_once_with(b"YELLOW\n")

    @patch("ai_status_core.socket.create_connection")
    def test_bridge_provider_protocol(self, create_connection):
        client = MagicMock()
        client.__enter__.return_value = client
        client.recv.return_value = b"OK\n"
        create_connection.return_value = client
        send_to_bridge("green", DEFAULT_CONFIG, provider="cursor")
        client.sendall.assert_called_once_with(b"STATUS cursor GREEN\n")

    @patch("ai_status_core.socket.create_connection")
    def test_bridge_project_protocol(self, create_connection):
        import base64
        from ai_status_projects import canonical_project
        client = MagicMock()
        client.__enter__.return_value = client
        client.recv.return_value = b"OK\n"
        create_connection.return_value = client
        send_to_bridge("yellow", DEFAULT_CONFIG, provider="cursor", project="C:/My Project")
        encoded = base64.urlsafe_b64encode(canonical_project("C:/My Project").encode()).decode()
        client.sendall.assert_called_once_with(f"STATUS2 cursor {encoded} YELLOW\n".encode())

    @patch("ai_status_core.socket.create_connection")
    def test_decor_protocol(self, create_connection):
        client = MagicMock()
        client.__enter__.return_value = client
        client.recv.return_value = b"OK\n"
        create_connection.return_value = client
        send_decor_to_bridge(DEFAULT_CONFIG, "COM5", "breathing", 5, 72)
        client.sendall.assert_called_once_with(b"DECOR COM5 BREATHING 5 72\n")

    @patch("ai_status_core.socket.create_connection")
    def test_decor_stop_protocol(self, create_connection):
        client = MagicMock()
        client.__enter__.return_value = client
        client.recv.return_value = b"OK\n"
        create_connection.return_value = client
        stop_decor(DEFAULT_CONFIG, "COM5")
        client.sendall.assert_called_once_with(b"DECOR_OFF COM5\n")


if __name__ == "__main__":
    unittest.main()

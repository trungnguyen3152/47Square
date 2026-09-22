import io
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch

import serial

from ai_status_updates import (
    FirmwareRelease,
    FirmwareRollbackError,
    GitHubReleaseClient,
    SupabaseDeviceEventClient,
    UpdateError,
    install_firmware,
    is_newer_version,
)


class FakeSerial:
    def __init__(self, fail_write=False):
        self.fail_write = fail_write
        self.writes = []
        self.is_open = False

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()
        return False

    def write(self, data):
        if self.fail_write:
            raise serial.SerialException("cable disconnected")
        self.writes.append(data)
        return len(data)

    def flush(self):
        pass

    def readline(self):
        return b"UPDATE OK\n"


class UpdateTests(unittest.TestCase):
    FIRMWARE_FIXTURE = Path(__file__).parent / "fixtures" / "fake_firmware.bin"

    def test_semantic_version_comparison(self):
        self.assertTrue(is_newer_version("v1.2.0", "1.1.9"))
        self.assertFalse(is_newer_version("1.0.0-beta", "1.0.0"))
        self.assertFalse(is_newer_version("1.0.0", "1.0.0"))

    def test_latest_release_selects_named_asset_and_digest(self):
        payload = {
            "tag_name": "ai-status-light-v1.2.3",
            "name": "Stable",
            "body": "Notes",
            "prerelease": False,
            "assets": [{
                "name": "firmware.bin",
                "browser_download_url": "https://example.invalid/firmware.bin",
                "size": 1234,
                "digest": "sha256:" + "ab" * 32,
            }],
        }
        client = GitHubReleaseClient("owner/repo", "firmware.bin")
        with patch.object(client, "_request_json", return_value=[payload]):
            release = client.latest()
        self.assertEqual(release.version, "1.2.3")
        self.assertEqual(release.sha256, "ab" * 32)

    def test_release_without_digest_is_rejected(self):
        payload = {
            "tag_name": "v1.0.0",
            "assets": [{
                "name": "firmware.bin", "browser_download_url": "https://example.invalid",
                "size": 1234, "digest": None,
            }],
        }
        client = GitHubReleaseClient("owner/repo", "firmware.bin")
        with patch.object(client, "_request_json", return_value=[payload]):
            with self.assertRaises(UpdateError):
                client.latest()

    def test_corrupted_download_is_removed(self):
        release = FirmwareRelease(
            version="1.2.0", name="Bad", notes="", asset_name="firmware.bin",
            download_url="https://example.invalid/firmware.bin", size=4,
            sha256="00" * 32, prerelease=False,
        )
        client = GitHubReleaseClient("owner/repo", "firmware.bin")
        destination = Path("updates/test-corrupt-firmware.bin")
        output = mock_open()
        with (
            patch("ai_status_updates.urllib.request.urlopen", return_value=io.BytesIO(b"bad!")),
            patch("ai_status_updates.Path.mkdir"),
            patch("ai_status_updates.Path.open", output),
            patch("ai_status_updates.Path.exists", return_value=True),
            patch("ai_status_updates.Path.unlink") as unlink,
        ):
            with self.assertRaises(UpdateError):
                client.download(release, destination)
        unlink.assert_called_once_with(missing_ok=True)

    @staticmethod
    def config():
        return {"serial_baud": 115200, "reset_delay": 0, "bridge_auth_token": ""}

    @staticmethod
    def device():
        return {"port": "COM5", "auth_key": "11" * 32}

    def test_disconnect_mid_transfer_is_failure_and_port_is_resumed(self):
        with (
            patch("ai_status_updates.release_device"),
            patch("ai_status_updates.resume_device") as resume,
            patch("ai_status_updates.read_device_info", return_value=("1.1.0", "AABBCCDDEEFF")),
            patch("ai_status_updates._authenticated_command", side_effect=["OK", "READY"]),
            patch("ai_status_updates.serial.Serial", return_value=FakeSerial(fail_write=True)),
        ):
            with self.assertRaises(serial.SerialException):
                install_firmware(
                    self.config(), self.device(), self.FIRMWARE_FIXTURE,
                    expected_version="1.2.0",
                )
        resume.assert_called_once_with(self.config(), "COM5")

    def test_post_boot_version_mismatch_is_reported_as_rollback(self):
        connection = FakeSerial()
        with (
            patch("ai_status_updates.release_device"),
            patch("ai_status_updates.resume_device"),
            patch("ai_status_updates.read_device_info", side_effect=[
                ("1.1.0", "AABBCCDDEEFF"),
                ("1.1.0", "AABBCCDDEEFF"),
            ]),
            patch("ai_status_updates.read_update_status", return_value=("rollback", "1.2.0")),
            patch("ai_status_updates._authenticated_command", side_effect=["OK", "READY"]),
            patch("ai_status_updates.serial.Serial", return_value=connection),
            patch("ai_status_updates.time.sleep"),
        ):
            with self.assertRaises(FirmwareRollbackError):
                install_firmware(
                    self.config(), self.device(), self.FIRMWARE_FIXTURE,
                    expected_version="1.2.0",
                )

    def test_success_requires_post_boot_expected_version(self):
        connection = FakeSerial()
        with (
            patch("ai_status_updates.release_device"),
            patch("ai_status_updates.resume_device"),
            patch("ai_status_updates.read_device_info", side_effect=[
                ("1.1.0", "AABBCCDDEEFF"),
                ("1.2.0", "AABBCCDDEEFF"),
            ]),
            patch("ai_status_updates.read_update_status", return_value=("success", "1.2.0")),
            patch("ai_status_updates._authenticated_command", side_effect=["OK", "READY"]),
            patch("ai_status_updates.serial.Serial", return_value=connection),
            patch("ai_status_updates.time.sleep"),
        ):
            result = install_firmware(
                self.config(), self.device(), self.FIRMWARE_FIXTURE, expected_version="1.2.0"
            )
        self.assertEqual(result.current_version, "1.2.0")
        self.assertEqual(result.update_status, "success")

    def test_supabase_event_uses_narrow_rpc(self):
        response = MagicMock(status=204)
        response.__enter__.return_value = response
        captured = {}

        def open_request(request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data)
            captured["timeout"] = timeout
            return response

        client = SupabaseDeviceEventClient("https://project.supabase.co", "publishable")
        with patch("ai_status_updates.urllib.request.urlopen", side_effect=open_request):
            client.record(
                "aabbccddeeff", "22" * 32, "firmware_update_succeeded", "1.2.0",
                {"previous_version": "1.1.0"},
            )
        self.assertTrue(captured["url"].endswith("/rest/v1/rpc/record_device_event"))
        self.assertEqual(captured["body"]["p_hardware_id"], "AABBCCDDEEFF")


if __name__ == "__main__":
    unittest.main()

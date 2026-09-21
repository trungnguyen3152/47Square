import unittest
from unittest.mock import patch

from ai_status_updates import GitHubReleaseClient, UpdateError, is_newer_version


class UpdateTests(unittest.TestCase):
    def test_semantic_version_comparison(self):
        self.assertTrue(is_newer_version("v1.2.0", "1.1.9"))
        self.assertFalse(is_newer_version("1.0.0-beta", "1.0.0"))
        self.assertFalse(is_newer_version("1.0.0", "1.0.0"))

    def test_latest_release_selects_named_asset_and_digest(self):
        payload = {
            "tag_name": "v1.2.3",
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


if __name__ == "__main__":
    unittest.main()

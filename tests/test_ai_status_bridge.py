import unittest
from unittest.mock import MagicMock, patch

from ai_status_bridge import DeviceRouter
from ai_status_core import DEFAULT_CONFIG


class DeviceRouterTests(unittest.TestCase):
    def config(self, devices):
        value = DEFAULT_CONFIG.copy()
        value["devices"] = devices
        value["status_beeps"] = {"RED": 1, "YELLOW": 2, "GREEN": 3}
        return value

    def test_provider_routes_only_to_assigned_device(self):
        config = self.config([
            {"port": "COM5", "provider": "codex", "project": "C:/codex"},
            {"port": "COM7", "provider": "cursor", "project": "C:/cursor"},
        ])
        router = DeviceRouter()
        controllers = {"COM5": MagicMock(), "COM7": MagicMock()}
        with patch("ai_status_bridge.load_config", return_value=config), patch.object(
            router, "controller_for", side_effect=lambda port, _config: controllers[port]
        ):
            delivered = router.send("GREEN", provider="cursor", project="C:/cursor")
        self.assertEqual(delivered, ["COM7"])
        controllers["COM5"].send.assert_not_called()
        controllers["COM7"].send.assert_called_once_with("GREEN", 3)

    def test_same_provider_cannot_leak_to_another_device(self):
        config = self.config([{"port": "COM5", "provider": "codex", "project": "C:/one"}])
        router = DeviceRouter()
        with patch("ai_status_bridge.load_config", return_value=config), patch.object(
            router, "controller_for"
        ) as controller_for:
            delivered = router.send("YELLOW", provider="cursor", project="C:/one")
        self.assertEqual(delivered, [])
        controller_for.assert_not_called()

    def test_same_provider_is_isolated_by_project(self):
        config = self.config([
            {"port": "COM5", "provider": "codex", "project": "C:/one"},
            {"port": "COM7", "provider": "codex", "project": "C:/two"},
        ])
        router = DeviceRouter()
        controllers = {"COM5": MagicMock(), "COM7": MagicMock()}
        with patch("ai_status_bridge.load_config", return_value=config), patch.object(
            router, "controller_for", side_effect=lambda port, _config: controllers[port]
        ):
            delivered = router.send("YELLOW", provider="codex", project="C:/two")
        self.assertEqual(delivered, ["COM7"])
        controllers["COM5"].send.assert_not_called()
        controllers["COM7"].send.assert_called_once_with("YELLOW", 2)

    def test_cross_project_isolation_applies_to_all_ui_providers(self):
        providers = (
            "codex", "claude-desktop", "antigravity", "cursor",
            "copilot-app", "copilot-vscode",
        )
        for provider in providers:
            with self.subTest(provider=provider):
                config = self.config([
                    {"port": "COM5", "provider": provider, "project": "C:/selected"}
                ])
                router = DeviceRouter()
                with patch("ai_status_bridge.load_config", return_value=config), patch.object(
                    router, "controller_for"
                ) as controller_for:
                    delivered = router.send(
                        "YELLOW", provider=provider, project="C:/other-window"
                    )
                self.assertEqual(delivered, [])
                controller_for.assert_not_called()

    def test_decor_targets_only_requested_device(self):
        config = self.config([
            {"port": "COM5", "provider": "codex", "project": "C:/one"},
            {"port": "COM7", "provider": "cursor", "project": "C:/two"},
        ])
        router = DeviceRouter()
        controllers = {"COM5": MagicMock(), "COM7": MagicMock()}
        with patch("ai_status_bridge.load_config", return_value=config), patch.object(
            router, "controller_for", side_effect=lambda port, _config: controllers[port]
        ):
            delivered = router.send_decor("COM7", "COMET", 7, 80)
        self.assertEqual(delivered, ["COM7"])
        controllers["COM5"].send_decor.assert_not_called()
        controllers["COM7"].send_decor.assert_called_once_with("COMET", 7, 80)

    def test_stop_decor_targets_only_requested_device(self):
        config = self.config([{"port": "COM5", "provider": "codex", "project": "C:/one"}])
        router = DeviceRouter()
        controller = MagicMock()
        with patch("ai_status_bridge.load_config", return_value=config), patch.object(
            router, "controller_for", return_value=controller
        ):
            delivered = router.stop_decor("COM5")
        self.assertEqual(delivered, ["COM5"])
        controller.stop_decor.assert_called_once_with()

    def test_unassigned_migration_mode_preserves_existing_behavior(self):
        config = self.config([{"port": "COM5", "provider": ""}])
        router = DeviceRouter()
        controller = MagicMock()
        with patch("ai_status_bridge.load_config", return_value=config), patch.object(
            router, "controller_for", return_value=controller
        ):
            delivered = router.send("RED", provider="codex")
        self.assertEqual(delivered, ["COM5"])
        controller.send.assert_called_once_with("RED", 1)

    def test_release_and_resume_reserve_the_serial_port(self):
        router = DeviceRouter()
        controller = MagicMock()
        router.controllers["com5"] = controller
        self.assertEqual(router.release("COM5"), ["COM5"])
        controller.close.assert_called_once_with()
        with self.assertRaises(OSError):
            router.controller_for("COM5", self.config([]))
        self.assertEqual(router.resume("COM5"), ["COM5"])

    def test_probe_checks_the_requested_physical_device(self):
        config = self.config([{"port": "COM5", "provider": "codex", "project": "C:/one"}])
        router = DeviceRouter()
        controller = MagicMock()
        with patch("ai_status_bridge.load_config", return_value=config), patch.object(
            router, "controller_for", return_value=controller
        ):
            self.assertEqual(router.probe("COM5"), ["COM5"])
        controller.probe.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()

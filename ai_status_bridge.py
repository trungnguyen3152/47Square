"""Persistent serial-to-TCP bridge for the AI status light."""

from __future__ import annotations

import argparse
import base64
import hmac
import socketserver
import sys
import threading
import time
from pathlib import Path

from ai_status_core import SerialController, StatusError, load_config, normalize_decor_effect, normalize_status
from ai_status_projects import canonical_project


BRIDGE_LOCK = Path(__file__).with_name(".ai_status_bridge.lock")


def acquire_single_instance_lock():
    """Prevent detached hook calls from creating competing COM owners."""
    lock_file = BRIDGE_LOCK.open("a+b")
    lock_file.seek(0, 2)
    if lock_file.tell() == 0:
        lock_file.write(b"0")
        lock_file.flush()
    lock_file.seek(0)
    if sys.platform == "win32":
        import msvcrt

        try:
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            lock_file.close()
            return None
    else:
        import fcntl

        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock_file.close()
            return None
    return lock_file


class StatusRequestHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        command = self.rfile.readline(1024).decode("ascii", errors="ignore").strip()
        config = load_config()
        expected_token = str(config.get("bridge_auth_token", "")).strip()
        if expected_token:
            authentication = command.split(" ", 2)
            if (
                len(authentication) != 3
                or authentication[0].upper() != "TOKEN"
                or not hmac.compare_digest(authentication[1], expected_token)
            ):
                self.wfile.write(b"ERROR AUTH\n")
                return
            command = authentication[2]
        if command.upper() == "PING":
            self.wfile.write(b"OK\n")
            return
        try:
            provider = ""
            project = ""
            target_port = ""
            parts = command.split()
            if len(parts) == 5 and parts[0].upper() == "DECOR":
                target_port, effect = parts[1], parts[2]
                delivered = self.server.router.send_decor(  # type: ignore[attr-defined]
                    target_port, effect, int(parts[3]), int(parts[4])
                )
            elif len(parts) == 2 and parts[0].upper() == "DECOR_OFF":
                target_port = parts[1]
                delivered = self.server.router.stop_decor(target_port)  # type: ignore[attr-defined]
            elif len(parts) == 2 and parts[0].upper() == "RELEASE":
                target_port = parts[1]
                delivered = self.server.router.release(target_port)  # type: ignore[attr-defined]
            elif len(parts) == 2 and parts[0].upper() == "RESUME":
                target_port = parts[1]
                delivered = self.server.router.resume(target_port)  # type: ignore[attr-defined]
            elif len(parts) == 2 and parts[0].upper() == "PROBE":
                target_port = parts[1]
                delivered = self.server.router.probe(target_port)  # type: ignore[attr-defined]
            elif len(parts) == 3 and parts[0].upper() == "STATUS":
                provider, command = parts[1], parts[2]
            elif len(parts) == 4 and parts[0].upper() == "STATUS2":
                provider, encoded, command = parts[1], parts[2], parts[3]
                project = base64.urlsafe_b64decode(encoded.encode("ascii")).decode("utf-8")
            elif len(parts) == 3 and parts[0].upper() == "DEVICE":
                target_port, command = parts[1], parts[2]
            if parts[0].upper() not in {"DECOR", "DECOR_OFF", "RELEASE", "RESUME", "PROBE"}:
                delivered = self.server.router.send(  # type: ignore[attr-defined]
                    command, provider=provider, project=project, target_port=target_port
                )
        except (OSError, StatusError, ValueError) as exc:
            print(f"Request failed: {exc}", flush=True)
            self.wfile.write(b"ERROR\n")
        else:
            route = f" provider={provider}" if provider else ""
            route += f" project={project}" if project else ""
            route += f" device={target_port}" if target_port else ""
            ports = ",".join(delivered) if delivered else "no assigned device"
            print(f"ESP32[{ports}] <- {command.upper()}{route}", flush=True)
            self.wfile.write(b"OK\n")


class DeviceRouter:
    """Own serial controllers and route one AI provider to one ESP32."""

    def __init__(self):
        self.controllers: dict[str, SerialController] = {}
        self.suspended_ports: set[str] = set()
        self.last_routed_status: dict[tuple[str, str, str], str] = {}
        self.lock = threading.Lock()

    @staticmethod
    def devices(config: dict) -> list[dict[str, str]]:
        configured = config.get("devices") or []
        if configured:
            return [
                {
                    "id": str(item.get("id", item.get("port", ""))),
                    "name": str(item.get("name", "AI Status Light")),
                    "port": str(item.get("port", "")),
                    "provider": str(item.get("provider", "")),
                    "project": canonical_project(item.get("project", "")),
                }
                for item in configured
                if str(item.get("port", "")).strip()
            ]
        return [{"id": str(config["serial_port"]), "name": "AI Status Light - 1", "port": str(config["serial_port"]), "provider": "", "project": ""}]

    def controller_for(self, port: str, config: dict) -> SerialController:
        key = port.casefold()
        if key in self.suspended_ports:
            raise OSError(f"{port} is reserved for a firmware update")
        controller = self.controllers.get(key)
        if controller is None:
            device_config = config.copy()
            device_config["serial_port"] = port
            device = next(
                (item for item in config.get("devices", []) if str(item.get("port", "")).casefold() == key),
                {},
            )
            device_config["device_auth_key"] = str(device.get("auth_key", ""))
            controller = SerialController(device_config)
            self.controllers[key] = controller
        return controller

    def connect_primary(self, config: dict) -> None:
        devices = self.devices(config)
        if devices:
            self.controller_for(devices[0]["port"], config).connect()

    def send(self, status: str, provider: str = "", project: str = "", target_port: str = "") -> list[str]:
        command = normalize_status(status)
        config = load_config()
        devices = self.devices(config)
        assignments_active = any(device["provider"] for device in devices)
        project = canonical_project(project)
        if target_port:
            targets = [device for device in devices if device["port"].casefold() == target_port.casefold()]
        elif provider and assignments_active:
            targets = [
                device for device in devices
                if device["provider"] == provider
                and canonical_project(device.get("project", "")) == project
            ]
        else:
            # Migration mode: until the first assignment is made, preserve the
            # original behavior and send every AI event to the connected light.
            targets = devices
        if not targets:
            return []
        configured_beeps = int(config["status_beeps"].get(command, 0)) if command != "OFF" else 0
        delivered: list[str] = []
        with self.lock:
            for device in targets:
                port = device["port"]
                controller = self.controller_for(port, config)
                route_key = (port.casefold(), provider, project)
                duplicate = bool(provider) and self.last_routed_status.get(route_key) == command
                beep_count = 0 if duplicate else configured_beeps
                controller.send(command, beep_count)
                effect = str(config.get("status_effects", {}).get(command, "STATIC"))
                if command != "OFF" and effect != "STATIC":
                    controller.send_decor(effect, {"RED": 1, "YELLOW": 2, "GREEN": 4}[command], 55)
                if provider:
                    self.last_routed_status[route_key] = command
                delivered.append(port)
        return delivered

    def send_decor(self, target_port: str, effect: str, mask: int, speed: int) -> list[str]:
        config = load_config()
        effect = normalize_decor_effect(effect)
        targets = [
            device for device in self.devices(config)
            if device["port"].casefold() == target_port.casefold()
        ]
        if not targets:
            return []
        with self.lock:
            for device in targets:
                self.controller_for(device["port"], config).send_decor(effect, mask, speed)
        return [device["port"] for device in targets]

    def stop_decor(self, target_port: str) -> list[str]:
        config = load_config()
        targets = [
            device for device in self.devices(config)
            if device["port"].casefold() == target_port.casefold()
        ]
        if not targets:
            return []
        with self.lock:
            for device in targets:
                self.controller_for(device["port"], config).stop_decor()
        return [device["port"] for device in targets]

    def release(self, target_port: str) -> list[str]:
        key = target_port.casefold()
        with self.lock:
            self.suspended_ports.add(key)
            controller = self.controllers.pop(key, None)
            if controller is not None:
                controller.close()
        return [target_port]

    def resume(self, target_port: str) -> list[str]:
        with self.lock:
            self.suspended_ports.discard(target_port.casefold())
        return [target_port]

    def probe(self, target_port: str) -> list[str]:
        config = load_config()
        target = next(
            (
                device for device in self.devices(config)
                if device["port"].casefold() == target_port.casefold()
            ),
            None,
        )
        if target is None:
            return []
        with self.lock:
            self.controller_for(target["port"], config).probe()
        return [target["port"]]

    def close(self) -> None:
        for controller in self.controllers.values():
            controller.close()


class StatusServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], router: DeviceRouter):
        self.router = router
        super().__init__(address, StatusRequestHandler)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the AI status serial bridge")
    parser.add_argument("--config", help="path to a JSON configuration file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        print("Another AI Status Bridge instance is already running", flush=True)
        return 0
    config = load_config(args.config)
    router = DeviceRouter()

    print(f"Connecting to configured ESP32...", flush=True)
    while True:
        try:
            router.connect_primary(config)
            break
        except OSError as exc:
            print(f"Serial unavailable: {exc}; retrying...", flush=True)
            time.sleep(float(config["reconnect_delay"]))

    address = (str(config["host"]), int(config["port"]))
    try:
        with StatusServer(address, router) as server:
            print(f"AI Status Bridge listening on {address[0]}:{address[1]}", flush=True)
            server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        print("Stopping bridge", flush=True)
    finally:
        router.close()
        instance_lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

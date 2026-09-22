"""Shared configuration and transport code for the AI status light."""

from __future__ import annotations

import json
import base64
import hashlib
import hmac
import socket
import threading
import time
from pathlib import Path
from typing import Any, Mapping

from ai_status_projects import canonical_project

import serial


VALID_STATUSES = frozenset({"RED", "YELLOW", "GREEN", "OFF"})
VALID_DECOR_EFFECTS = frozenset({
    "STATIC", "BLINK", "BREATHING", "FADE_IN", "FADE_OUT",
    "SMOOTH", "CYCLE", "RAINBOW", "THEATER", "COMET", "METEOR",
    "SCANNER", "SPARKLE", "POLICE",
})
DEFAULT_CONFIG: dict[str, Any] = {
    "serial_port": "COM5",
    "serial_baud": 115200,
    "serial_timeout": 1.0,
    "reset_delay": 2.0,
    "reconnect_delay": 2.0,
    "host": "127.0.0.1",
    "port": 8765,
    "client_timeout": 0.75,
    "bridge_auth_token": "",
    "github_repository": "",
    "firmware_asset_name": "ai-status-light-esp32c3.bin",
    "update_channel": "stable",
    "auto_check_updates": True,
    "supabase_url": "",
    "supabase_publishable_key": "",
    "green_hold_seconds": 0.0,
    "typing_process_names": ["ChatGPT.exe"],
    "status_beeps": {"RED": 0, "YELLOW": 0, "GREEN": 2},
    "status_effects": {"RED": "STATIC", "YELLOW": "STATIC", "GREEN": "STATIC"},
    "devices": [],
}


class StatusError(ValueError):
    """Raised when a status command or device response is invalid."""


class BridgeCommandError(OSError):
    """Raised when a running bridge rejects a command."""


def normalize_status(status: str) -> str:
    normalized = status.strip().upper()
    if normalized not in VALID_STATUSES:
        expected = ", ".join(sorted(VALID_STATUSES))
        raise StatusError(f"invalid status {status!r}; expected one of: {expected}")
    return normalized


def normalize_decor_effect(effect: str) -> str:
    normalized = effect.strip().upper().replace(" ", "_").replace("/", "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    normalized = {
        "STATIC_SOLID": "STATIC",
        "SMOOTH_TRANSITION": "SMOOTH",
        "COLOR_CYCLE": "CYCLE",
        "THEATER_CHASE": "THEATER",
        "SCANNER_CYLON": "SCANNER",
        "SPARKLE_TWINKLE": "SPARKLE",
    }.get(normalized, normalized)
    if normalized not in VALID_DECOR_EFFECTS:
        raise StatusError(f"invalid decor effect {effect!r}")
    return normalized


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    config_path = Path(path) if path else Path(__file__).with_name("config.json")
    config = DEFAULT_CONFIG.copy()
    if config_path.exists():
        loaded = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise ValueError("configuration root must be a JSON object")
        unknown = set(loaded) - set(DEFAULT_CONFIG)
        if unknown:
            raise ValueError(f"unknown configuration keys: {', '.join(sorted(unknown))}")
        config.update(loaded)

    if not 1 <= int(config["port"]) <= 65535:
        raise ValueError("port must be between 1 and 65535")
    if int(config["serial_baud"]) <= 0:
        raise ValueError("serial_baud must be positive")
    if float(config["green_hold_seconds"]) < 0:
        raise ValueError("green_hold_seconds must be non-negative")
    bridge_auth_token = str(config["bridge_auth_token"]).strip()
    if bridge_auth_token and (
        len(bridge_auth_token) != 64
        or any(character not in "0123456789abcdefABCDEF" for character in bridge_auth_token)
    ):
        raise ValueError("bridge_auth_token must be exactly 64 hexadecimal characters")
    repository = str(config["github_repository"]).strip().strip("/")
    if repository and (repository.count("/") != 1 or any(part.strip() != part or not part for part in repository.split("/"))):
        raise ValueError("github_repository must use owner/repository format")
    if str(config["update_channel"]) not in {"stable", "prerelease"}:
        raise ValueError("update_channel must be stable or prerelease")
    if not isinstance(config["auto_check_updates"], bool):
        raise ValueError("auto_check_updates must be a boolean")
    process_names = config["typing_process_names"]
    if not isinstance(process_names, list) or not process_names or not all(
        isinstance(name, str) and name.strip() for name in process_names
    ):
        raise ValueError("typing_process_names must be a non-empty string list")
    status_beeps = config["status_beeps"]
    if not isinstance(status_beeps, dict) or set(status_beeps) != {"RED", "YELLOW", "GREEN"}:
        raise ValueError("status_beeps must define RED, YELLOW, and GREEN")
    if not all(isinstance(count, int) and 0 <= count <= 3 for count in status_beeps.values()):
        raise ValueError("status beep counts must be integers from 0 to 3")
    status_effects = config["status_effects"]
    if not isinstance(status_effects, dict) or set(status_effects) != {"RED", "YELLOW", "GREEN"}:
        raise ValueError("status_effects must define RED, YELLOW, and GREEN")
    if not all(effect in {"STATIC", "BREATHING", "BLINK"} for effect in status_effects.values()):
        raise ValueError("status effects must be STATIC, BREATHING, or BLINK")
    devices = config["devices"]
    if not isinstance(devices, list):
        raise ValueError("devices must be a list")
    seen_ports: set[str] = set()
    seen_assignments: set[tuple[str, str]] = set()
    for device in devices:
        if not isinstance(device, dict) or not str(device.get("port", "")).strip():
            raise ValueError("each device must have a serial port")
        port = str(device["port"]).strip().casefold()
        provider = str(device.get("provider", "")).strip()
        project = canonical_project(device.get("project", ""))
        auth_key = str(device.get("auth_key", "")).strip()
        event_token = str(device.get("event_token", "")).strip()
        if auth_key and (len(auth_key) != 64 or any(character not in "0123456789abcdefABCDEF" for character in auth_key)):
            raise ValueError("device auth_key must be exactly 64 hexadecimal characters")
        if event_token and (len(event_token) != 64 or any(character not in "0123456789abcdefABCDEF" for character in event_token)):
            raise ValueError("device event_token must be exactly 64 hexadecimal characters")
        if port in seen_ports:
            raise ValueError("device serial ports must be unique")
        assignment = (provider, project)
        if provider and project and assignment in seen_assignments:
            raise ValueError("one AI project cannot be assigned to multiple devices")
        seen_ports.add(port)
        if provider and project:
            seen_assignments.add(assignment)
    return config


def send_to_bridge(
    status: str,
    config: Mapping[str, Any],
    provider: str = "",
    project: str = "",
    target_port: str = "",
) -> None:
    command = normalize_status(status)
    address = (str(config["host"]), int(config["port"]))
    timeout = float(config["client_timeout"])
    with socket.create_connection(address, timeout=timeout) as client:
        client.settimeout(timeout)
        if target_port:
            payload = f"DEVICE {target_port} {command}\n"
        elif provider and project:
            encoded = base64.urlsafe_b64encode(canonical_project(project).encode("utf-8")).decode("ascii")
            payload = f"STATUS2 {provider} {encoded} {command}\n"
        elif provider:
            payload = f"STATUS {provider} {command}\n"
        else:
            payload = f"{command}\n"
        token = str(config.get("bridge_auth_token", "")).strip()
        if token:
            payload = f"TOKEN {token} {payload}"
        client.sendall(payload.encode("ascii"))
        response = client.recv(32).decode("ascii", errors="replace").strip()
    if response != "OK":
        raise BridgeCommandError(f"bridge returned {response or 'no response'}")


def send_decor_to_bridge(
    config: Mapping[str, Any], target_port: str, effect: str, mask: int, speed: int
) -> None:
    effect = normalize_decor_effect(effect)
    mask = max(0, min(7, int(mask)))
    speed = max(1, min(100, int(speed)))
    address = (str(config["host"]), int(config["port"]))
    timeout = float(config["client_timeout"])
    with socket.create_connection(address, timeout=timeout) as client:
        client.settimeout(timeout)
        payload = f"DECOR {target_port} {effect} {mask} {speed}\n"
        token = str(config.get("bridge_auth_token", "")).strip()
        if token:
            payload = f"TOKEN {token} {payload}"
        client.sendall(payload.encode("ascii"))
        response = client.recv(32).decode("ascii", errors="replace").strip()
    if response != "OK":
        raise BridgeCommandError(f"bridge returned {response or 'no response'}")


def stop_decor(config: Mapping[str, Any], target_port: str) -> None:
    address = (str(config["host"]), int(config["port"]))
    timeout = float(config["client_timeout"])
    with socket.create_connection(address, timeout=timeout) as client:
        client.settimeout(timeout)
        payload = f"DECOR_OFF {target_port}\n"
        token = str(config.get("bridge_auth_token", "")).strip()
        if token:
            payload = f"TOKEN {token} {payload}"
        client.sendall(payload.encode("ascii"))
        response = client.recv(32).decode("ascii", errors="replace").strip()
    if response != "OK":
        raise BridgeCommandError(f"bridge returned {response or 'no response'}")


def release_device(config: Mapping[str, Any], target_port: str) -> None:
    """Ask the bridge to close one COM port before a firmware update."""
    address = (str(config["host"]), int(config["port"]))
    timeout = float(config["client_timeout"])
    payload = f"RELEASE {target_port}\n"
    token = str(config.get("bridge_auth_token", "")).strip()
    if token:
        payload = f"TOKEN {token} {payload}"
    with socket.create_connection(address, timeout=timeout) as client:
        client.settimeout(timeout)
        client.sendall(payload.encode("ascii"))
        response = client.recv(32).decode("ascii", errors="replace").strip()
    if response != "OK":
        raise BridgeCommandError(f"bridge returned {response or 'no response'}")


def resume_device(config: Mapping[str, Any], target_port: str) -> None:
    address = (str(config["host"]), int(config["port"]))
    timeout = float(config["client_timeout"])
    payload = f"RESUME {target_port}\n"
    token = str(config.get("bridge_auth_token", "")).strip()
    if token:
        payload = f"TOKEN {token} {payload}"
    with socket.create_connection(address, timeout=timeout) as client:
        client.settimeout(timeout)
        client.sendall(payload.encode("ascii"))
        response = client.recv(32).decode("ascii", errors="replace").strip()
    if response != "OK":
        raise BridgeCommandError(f"bridge returned {response or 'no response'}")


class SerialController:
    """Owns one serial connection and serializes writes from bridge clients."""

    def __init__(self, config: Mapping[str, Any]):
        self.config = config
        self._serial: serial.Serial | None = None
        self._lock = threading.Lock()

    def connect(self) -> None:
        self.close()
        try:
            self._serial = serial.Serial(
                str(self.config["serial_port"]),
                int(self.config["serial_baud"]),
                timeout=float(self.config["serial_timeout"]),
            )
        except serial.SerialException as exc:
            raise OSError(str(exc)) from exc
        time.sleep(float(self.config["reset_delay"]))

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def send(self, status: str, beep_count: int | None = None) -> None:
        command = normalize_status(status)
        with self._lock:
            if self._serial is None or not self._serial.is_open:
                self.connect()
            assert self._serial is not None
            suffix = "" if beep_count is None else f" {max(0, min(3, int(beep_count)))}"
            payload = f"{command}{suffix}\n".encode("ascii")
            self._write_payload(payload)

    def send_decor(self, effect: str, mask: int, speed: int) -> None:
        effect = normalize_decor_effect(effect)
        payload = f"DECOR {effect} {max(0, min(7, int(mask)))} {max(1, min(100, int(speed)))}\n".encode("ascii")
        with self._lock:
            if self._serial is None or not self._serial.is_open:
                self.connect()
            self._write_payload(payload)

    def stop_decor(self) -> None:
        with self._lock:
            if self._serial is None or not self._serial.is_open:
                self.connect()
            self._write_payload(b"DECOR_OFF\n")

    def probe(self) -> tuple[str, str]:
        """Verify that the configured serial port is a responding status light."""
        with self._lock:
            try:
                if self._serial is None or not self._serial.is_open:
                    self.connect()
                response = self._authenticated_request("INFO")
            except (OSError, serial.SerialException) as exc:
                self.close()
                raise OSError(str(exc)) from exc
        parts = response.split()
        if len(parts) != 3 or parts[0] != "INFO":
            self.close()
            raise OSError(f"device returned invalid information: {response or 'no response'}")
        return parts[1], parts[2]

    def _write_payload(self, payload: bytes) -> None:
        assert self._serial is not None
        try:
            self._write_authenticated(payload)
        except serial.SerialException:
                # USB serial devices can briefly disappear during reset or
                # resume. Reopen once and retry the status that triggered the
                # failure so the physical light does not remain stale.
            self.close()
            try:
                self.connect()
                assert self._serial is not None
                self._write_authenticated(payload)
            except (OSError, serial.SerialException) as exc:
                self.close()
                raise OSError(str(exc)) from exc

    def _write_authenticated(self, payload: bytes) -> None:
        assert self._serial is not None
        auth_key = str(self.config.get("device_auth_key", "")).strip()
        if not auth_key:
            self._serial.write(payload)
            self._serial.flush()
            return
        command = payload.decode("ascii").strip()
        response = self._authenticated_request(command)
        if response != "OK":
            raise OSError(f"device rejected authenticated command: {response or 'no response'}")

    def _authenticated_request(self, command: str) -> str:
        assert self._serial is not None
        auth_key = str(self.config.get("device_auth_key", "")).strip()
        if not auth_key:
            self._serial.reset_input_buffer()
            self._serial.write(f"{command}\n".encode("ascii"))
            self._serial.flush()
            return self._serial.readline().decode("ascii", errors="replace").strip()
        self._serial.reset_input_buffer()
        self._serial.write(b"CHALLENGE\n")
        self._serial.flush()
        challenge = self._serial.readline().decode("ascii", errors="replace").strip()
        if not challenge.startswith("NONCE "):
            raise OSError(f"device authentication unavailable: {challenge or 'no challenge'}")
        nonce = challenge[6:]
        if len(nonce) != 32 or any(character not in "0123456789abcdefABCDEF" for character in nonce):
            raise OSError("device returned an invalid authentication challenge")
        signature = hmac.new(
            bytes.fromhex(auth_key), f"{nonce}|{command}".encode("ascii"), hashlib.sha256
        ).hexdigest()
        self._serial.write(f"AUTH {signature} {command}\n".encode("ascii"))
        self._serial.flush()
        return self._serial.readline().decode("ascii", errors="replace").strip()


def send_direct(status: str, config: Mapping[str, Any]) -> None:
    controller = SerialController(config)
    try:
        controller.connect()
        command = normalize_status(status)
        beep_count = int(config["status_beeps"].get(command, 0)) if command != "OFF" else 0
        controller.send(command, beep_count)
    finally:
        controller.close()

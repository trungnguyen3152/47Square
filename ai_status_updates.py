"""GitHub Releases client and authenticated USB OTA transport."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import serial

from ai_status_core import release_device, resume_device


API_VERSION = "2022-11-28"
MAX_FIRMWARE_SIZE = 0x140000
USER_AGENT = "AI-Status-Light-Updater/1.0"


class UpdateError(RuntimeError):
    pass


class FirmwareRollbackError(UpdateError):
    """The bootloader returned to the previously working firmware."""


class DeviceEventError(UpdateError):
    """A firmware lifecycle event could not be recorded remotely."""


@dataclass(frozen=True)
class FirmwareRelease:
    version: str
    name: str
    notes: str
    asset_name: str
    download_url: str
    size: int
    sha256: str
    prerelease: bool


@dataclass(frozen=True)
class FirmwareInstallResult:
    previous_version: str
    current_version: str
    hardware_id: str
    update_status: str
    target_version: str


class SupabaseDeviceEventClient:
    """Write authenticated device lifecycle events through a narrow RPC."""

    def __init__(self, url: str, publishable_key: str):
        self.url = url.rstrip("/")
        self.publishable_key = publishable_key.strip()

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> "SupabaseDeviceEventClient | None":
        url = str(config.get("supabase_url", "")).strip()
        key = str(config.get("supabase_publishable_key", "")).strip()
        return cls(url, key) if url and key else None

    def record(
        self,
        hardware_id: str,
        event_token: str,
        event_type: str,
        firmware_version: str,
        metadata: Mapping[str, object] | None = None,
    ) -> None:
        if len(event_token) != 64:
            raise DeviceEventError("Thiết bị chưa có event token hợp lệ")
        body = json.dumps({
            "p_hardware_id": hardware_id.upper(),
            "p_event_token": event_token.lower(),
            "p_event_type": event_type,
            "p_firmware_version": firmware_version,
            "p_metadata": dict(metadata or {}),
        }).encode("utf-8")
        request = urllib.request.Request(
            f"{self.url}/rest/v1/rpc/record_device_event",
            data=body,
            method="POST",
            headers={
                "apikey": self.publishable_key,
                "Authorization": f"Bearer {self.publishable_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                if response.status not in {200, 201, 204}:
                    raise DeviceEventError(f"Supabase trả về HTTP {response.status}")
        except urllib.error.HTTPError as exc:
            raise DeviceEventError(f"Supabase từ chối event: HTTP {exc.code}") from exc
        except OSError as exc:
            raise DeviceEventError(f"Không thể gửi event tới Supabase: {exc}") from exc


def _version_key(value: str) -> tuple[int, int, int, int, str]:
    normalized = value.strip().lower().lstrip("v")
    match = re.fullmatch(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?(?:[-+](.+))?", normalized)
    if not match:
        return (0, 0, 0, 0, normalized)
    major, minor, patch = (int(match.group(index) or 0) for index in range(1, 4))
    suffix = match.group(4) or ""
    return (major, minor, patch, 1 if not suffix else 0, suffix)


def is_newer_version(candidate: str, current: str) -> bool:
    return _version_key(candidate) > _version_key(current)


def _release_version(tag_name: str) -> str:
    match = re.search(r"(?:^|[-_/])v?(\d+(?:\.\d+){0,2}(?:[-+][0-9A-Za-z.-]+)?)$", tag_name.strip())
    return match.group(1) if match else tag_name.strip().lstrip("v")


class GitHubReleaseClient:
    def __init__(self, repository: str, asset_name: str, channel: str = "stable"):
        repository = repository.strip().strip("/")
        if repository.count("/") != 1:
            raise UpdateError("Kho GitHub phải có dạng owner/repository")
        self.repository = repository
        self.asset_name = asset_name
        self.channel = channel

    @staticmethod
    def _request_json(url: str) -> object:
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": USER_AGENT,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=12) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                raise UpdateError("Không tìm thấy GitHub repository hoặc bản phát hành") from exc
            raise UpdateError(f"GitHub trả về HTTP {exc.code}") from exc
        except OSError as exc:
            raise UpdateError(f"Không thể kết nối GitHub: {exc}") from exc

    def latest(self) -> FirmwareRelease:
        base = f"https://api.github.com/repos/{self.repository}/releases"
        payload = self._request_json(f"{base}?per_page=30")
        if not isinstance(payload, list):
            raise UpdateError("Phản hồi GitHub không hợp lệ")
        releases = [
            item for item in payload
            if isinstance(item, dict)
            and not item.get("draft")
            and (self.channel == "prerelease" or not item.get("prerelease"))
            and any(asset.get("name") == self.asset_name for asset in item.get("assets", []))
        ]
        if not releases:
            raise UpdateError(f"Repository chưa có Release chứa {self.asset_name}")
        release = releases[0]
        asset = next(
            (item for item in release.get("assets", []) if item.get("name") == self.asset_name),
            None,
        )
        if not asset:
            raise UpdateError(f"Release không có asset {self.asset_name}")
        digest = str(asset.get("digest") or "")
        if not digest.startswith("sha256:") or len(digest) != 71:
            raise UpdateError("Asset GitHub chưa có SHA-256 digest")
        size = int(asset.get("size", 0))
        if not 0 < size <= MAX_FIRMWARE_SIZE:
            raise UpdateError("Kích thước firmware vượt quá phân vùng OTA")
        return FirmwareRelease(
            version=_release_version(str(release.get("tag_name", ""))),
            name=str(release.get("name") or release.get("tag_name") or "Firmware"),
            notes=str(release.get("body") or ""),
            asset_name=str(asset["name"]),
            download_url=str(asset["browser_download_url"]),
            size=size,
            sha256=digest.split(":", 1)[1].lower(),
            prerelease=bool(release.get("prerelease")),
        )

    def download(
        self,
        release: FirmwareRelease,
        destination: Path,
        progress: Callable[[int, int], None] | None = None,
    ) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".part")
        request = urllib.request.Request(release.download_url, headers={"User-Agent": USER_AGENT})
        digest = hashlib.sha256()
        received = 0
        try:
            with urllib.request.urlopen(request, timeout=30) as response, temporary.open("wb") as output:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > MAX_FIRMWARE_SIZE:
                        raise UpdateError("Firmware tải về vượt quá kích thước cho phép")
                    digest.update(chunk)
                    output.write(chunk)
                    if progress:
                        progress(received, release.size)
            if received != release.size:
                raise UpdateError("Firmware tải về không đủ dữ liệu")
            if not hmac.compare_digest(digest.hexdigest(), release.sha256):
                raise UpdateError("SHA-256 firmware không khớp với GitHub Release")
            temporary.replace(destination)
            return destination
        finally:
            if temporary.exists():
                temporary.unlink(missing_ok=True)


def _authenticated_command(connection: serial.Serial, auth_key: str, command: str) -> str:
    connection.reset_input_buffer()
    connection.write(b"CHALLENGE\n")
    connection.flush()
    challenge = connection.readline().decode("ascii", errors="replace").strip()
    if not challenge.startswith("NONCE "):
        raise UpdateError(f"Thiết bị không hỗ trợ cập nhật bảo mật: {challenge or 'không phản hồi'}")
    nonce = challenge[6:]
    signature = hmac.new(
        bytes.fromhex(auth_key), f"{nonce}|{command}".encode("ascii"), hashlib.sha256
    ).hexdigest()
    connection.write(f"AUTH {signature} {command}\n".encode("ascii"))
    connection.flush()
    return connection.readline().decode("ascii", errors="replace").strip()


def _open_serial(
    port: str,
    baud: int,
    timeout: float,
    write_timeout: float | None = None,
) -> serial.Serial:
    """Open ESP32-C3 serial without asserting boot/reset control lines."""
    connection = serial.Serial()
    connection.port = port
    connection.baudrate = baud
    connection.timeout = timeout
    connection.write_timeout = write_timeout
    # pySerial defaults both lines to active. On ESP32-C3 boards that can reset
    # a just-updated image before it marks itself valid, causing a false rollback.
    connection.dtr = False
    connection.rts = False
    connection.open()
    return connection


def _write_all(connection: serial.Serial, data: bytes) -> None:
    """Write a complete block even when the Windows driver accepts it partially."""
    pending = memoryview(data)
    while pending:
        written = connection.write(pending)
        if not written:
            raise serial.SerialTimeoutException("serial write made no progress")
        pending = pending[written:]


def read_device_info(port: str, baud: int, auth_key: str, reset_delay: float = 2.0) -> tuple[str, str]:
    with _open_serial(port, baud, timeout=3) as connection:
        time.sleep(reset_delay)
        response = _authenticated_command(connection, auth_key, "INFO")
    parts = response.split()
    if len(parts) != 3 or parts[0] != "INFO":
        raise UpdateError(f"Thiết bị trả về thông tin không hợp lệ: {response}")
    return parts[1], parts[2]


def read_update_status(port: str, baud: int, auth_key: str, reset_delay: float = 0.2) -> tuple[str, str]:
    with _open_serial(port, baud, timeout=3) as connection:
        time.sleep(reset_delay)
        response = _authenticated_command(connection, auth_key, "UPDATE_STATUS")
    parts = response.split()
    if len(parts) != 3 or parts[0] != "UPDATE_STATUS":
        raise UpdateError(f"Thiết bị trả về trạng thái OTA không hợp lệ: {response or 'không phản hồi'}")
    return parts[1], parts[2]


def arm_rollback_test(port: str, baud: int, auth_key: str, reset_delay: float = 0.2) -> None:
    with _open_serial(port, baud, timeout=3) as connection:
        time.sleep(reset_delay)
        response = _authenticated_command(connection, auth_key, "TEST_ROLLBACK_NEXT_BOOT")
    if response != "OK":
        raise UpdateError(f"Không thể bật kiểm thử rollback: {response or 'không phản hồi'}")


def cancel_rollback_test(port: str, baud: int, auth_key: str, reset_delay: float = 0.2) -> None:
    with _open_serial(port, baud, timeout=3) as connection:
        time.sleep(reset_delay)
        response = _authenticated_command(connection, auth_key, "TEST_ROLLBACK_CANCEL")
    if response != "OK":
        raise UpdateError(f"Không thể xóa cờ kiểm thử rollback: {response or 'không phản hồi'}")


def _wait_for_device_info(port: str, baud: int, auth_key: str) -> tuple[str, str]:
    last_error: Exception | None = None
    # Give the new image time to run setup() and confirm itself before opening
    # the USB CDC port. This also covers slower USB re-enumeration on Windows.
    time.sleep(2.0)
    for _ in range(10):
        try:
            time.sleep(0.5)
            return read_device_info(port, baud, auth_key, 0.25)
        except (OSError, serial.SerialException, UpdateError) as exc:
            last_error = exc
    raise UpdateError(f"Thiết bị không khởi động lại sau cập nhật: {last_error}")


def inspect_device(config: Mapping[str, object], device: Mapping[str, object]) -> tuple[str, str]:
    port = str(device["port"])
    release_device(config, port)
    try:
        return read_device_info(
            port,
            int(config["serial_baud"]),
            str(device.get("auth_key", "")),
            float(config["reset_delay"]),
        )
    finally:
        try:
            resume_device(config, port)
        except OSError:
            pass


def install_firmware(
    config: Mapping[str, object],
    device: Mapping[str, object],
    firmware: Path,
    progress: Callable[[int, int], None] | None = None,
    expected_version: str = "",
) -> FirmwareInstallResult:
    port = str(device["port"])
    auth_key = str(device.get("auth_key", ""))
    if len(auth_key) != 64:
        raise UpdateError("Thiết bị chưa có khóa cập nhật hợp lệ")
    data_size = firmware.stat().st_size
    if not 0 < data_size <= MAX_FIRMWARE_SIZE:
        raise UpdateError("Firmware không vừa phân vùng OTA")
    sha256 = hashlib.sha256(firmware.read_bytes()).hexdigest()
    release_device(config, port)
    try:
        previous_version, hardware_id = read_device_info(
            port, int(config["serial_baud"]), auth_key, float(config["reset_delay"])
        )
        result = ""
        with _open_serial(
            port, int(config["serial_baud"]), timeout=60, write_timeout=15
        ) as connection:
            time.sleep(float(config["reset_delay"]))
            chunk_ack = _version_key(previous_version) >= _version_key("1.1.0")
            if expected_version and chunk_ack:
                # UPDATE_BEGIN stays backward-compatible with v1.0 devices.
                _authenticated_command(connection, auth_key, f"UPDATE_TARGET {expected_version}")
            response = _authenticated_command(connection, auth_key, f"UPDATE_BEGIN {data_size} {sha256}")
            if response != "READY":
                raise UpdateError(f"ESP32 từ chối cập nhật: {response or 'không phản hồi'}")
            sent = 0
            with firmware.open("rb") as source:
                while True:
                    chunk = source.read(256 if chunk_ack else 4096)
                    if not chunk:
                        break
                    _write_all(connection, chunk)
                    connection.flush()
                    sent += len(chunk)
                    if progress:
                        progress(sent, data_size)
                    if chunk_ack:
                        result = connection.readline().decode("ascii", errors="replace").strip()
                        if sent < data_size and result != "ACK":
                            raise UpdateError(
                                f"ESP32 không xác nhận khối OTA: {result or 'không phản hồi'}"
                            )
            if not chunk_ack:
                connection.flush()
                result = connection.readline().decode("ascii", errors="replace").strip()
            if result and result != "UPDATE OK":
                raise UpdateError(f"Cập nhật thất bại: {result}")
        # INFO after reboot is authoritative even when UPDATE OK was received.
        current_version, current_hardware_id = _wait_for_device_info(
            port, int(config["serial_baud"]), auth_key
        )
        if current_hardware_id != hardware_id:
            raise UpdateError("Thiết bị sau cập nhật không khớp hardware ID ban đầu")
        try:
            update_status, target_version = read_update_status(
                port, int(config["serial_baud"]), auth_key
            )
        except UpdateError:
            update_status, target_version = "unavailable", expected_version or "unknown"
        if update_status in {"rollback", "rollback-requested"} or (
            expected_version and current_version != expected_version
        ):
            raise FirmwareRollbackError(
                f"Firmware đã rollback về {current_version} thay vì {expected_version or target_version}"
            )
        return FirmwareInstallResult(
            previous_version=previous_version,
            current_version=current_version,
            hardware_id=hardware_id,
            update_status=update_status,
            target_version=target_version,
        )
    finally:
        try:
            resume_device(config, port)
        except OSError:
            pass

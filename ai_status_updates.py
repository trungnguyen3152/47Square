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


def read_device_info(port: str, baud: int, auth_key: str, reset_delay: float = 2.0) -> tuple[str, str]:
    with serial.Serial(port, baud, timeout=3) as connection:
        time.sleep(reset_delay)
        response = _authenticated_command(connection, auth_key, "INFO")
    parts = response.split()
    if len(parts) != 3 or parts[0] != "INFO":
        raise UpdateError(f"Thiết bị trả về thông tin không hợp lệ: {response}")
    return parts[1], parts[2]


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
) -> None:
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
        result = ""
        with serial.Serial(port, int(config["serial_baud"]), timeout=15, write_timeout=15) as connection:
            time.sleep(float(config["reset_delay"]))
            response = _authenticated_command(connection, auth_key, f"UPDATE_BEGIN {data_size} {sha256}")
            if response != "READY":
                raise UpdateError(f"ESP32 từ chối cập nhật: {response or 'không phản hồi'}")
            sent = 0
            with firmware.open("rb") as source:
                while True:
                    chunk = source.read(4096)
                    if not chunk:
                        break
                    connection.write(chunk)
                    sent += len(chunk)
                    if progress:
                        progress(sent, data_size)
            connection.flush()
            result = connection.readline().decode("ascii", errors="replace").strip()
            if result and result != "UPDATE OK":
                raise UpdateError(f"Cập nhật thất bại: {result}")
        # Native USB can disappear immediately after ESP.restart(), before the
        # host receives UPDATE OK. Reconnecting and reading authenticated INFO
        # is the authoritative success check in that case.
        if not result:
            last_error: Exception | None = None
            for _ in range(6):
                try:
                    time.sleep(0.5)
                    read_device_info(port, int(config["serial_baud"]), auth_key, 0.5)
                    last_error = None
                    break
                except (OSError, serial.SerialException, UpdateError) as exc:
                    last_error = exc
            if last_error is not None:
                raise UpdateError(f"Thiết bị không khởi động lại sau cập nhật: {last_error}")
    finally:
        try:
            resume_device(config, port)
        except OSError:
            pass

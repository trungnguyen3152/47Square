"""Destructive-on-purpose hardware checks for the authenticated USB OTA path."""

from __future__ import annotations

import argparse
import hashlib
import time
from pathlib import Path

from ai_status_core import load_config, release_device, resume_device
from ai_status_updates import (
    FirmwareRollbackError,
    SupabaseDeviceEventClient,
    UpdateError,
    _authenticated_command,
    _open_serial,
    _write_all,
    arm_rollback_test,
    cancel_rollback_test,
    install_firmware,
    read_device_info,
    read_update_status,
)


def _record(client, hardware_id, token, event_type, version, metadata):
    if client is not None and token:
        client.record(hardware_id, token, event_type, version, metadata)


def test_bad_sha(config: dict, device: dict, firmware: Path, event_client) -> None:
    port = str(device["port"])
    baud = int(config["serial_baud"])
    auth_key = str(device["auth_key"])
    event_token = str(device.get("event_token", ""))
    data = firmware.read_bytes()
    release_device(config, port)
    try:
        version, hardware_id = read_device_info(port, baud, auth_key, 1.0)
        _record(event_client, hardware_id, event_token, "firmware_update_started", version, {
            "test": "bad-sha256", "target_version": version,
        })
        with _open_serial(port, baud, timeout=60, write_timeout=15) as connection:
            time.sleep(0.5)
            response = _authenticated_command(
                connection, auth_key, f"UPDATE_BEGIN {len(data)} {'00' * 32}"
            )
            if response != "READY":
                raise UpdateError(f"bad-sha test was rejected before transfer: {response}")
            for offset in range(0, len(data), 256):
                _write_all(connection, data[offset:offset + 256])
                connection.flush()
                result = connection.readline().decode("ascii", errors="replace").strip()
                if offset + 256 < len(data) and result != "ACK":
                    raise UpdateError(
                        f"bad-sha block at offset {offset} was not acknowledged: {result}"
                    )
        if result != "UPDATE ERROR SHA256":
            raise UpdateError(f"bad-sha test returned: {result or 'no response'}")
        current_version, current_hardware_id = read_device_info(port, baud, auth_key, 0.5)
        if (current_version, current_hardware_id) != (version, hardware_id):
            raise UpdateError("bad-sha test changed the running firmware")
        _record(event_client, hardware_id, event_token, "firmware_update_failed", version, {
            "test": "bad-sha256", "reason": "sha256-error", "expected_result": True,
        })
        print(f"PASS bad SHA-256: firmware stayed at {version}")
    finally:
        resume_device(config, port)


def test_disconnect(config: dict, device: dict, firmware: Path, event_client) -> None:
    port = str(device["port"])
    baud = int(config["serial_baud"])
    auth_key = str(device["auth_key"])
    event_token = str(device.get("event_token", ""))
    data = firmware.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    release_device(config, port)
    try:
        version, hardware_id = read_device_info(port, baud, auth_key, 0.5)
        _record(event_client, hardware_id, event_token, "firmware_update_started", version, {
            "test": "disconnect-mid-transfer", "target_version": version,
        })
        with _open_serial(port, baud, timeout=5, write_timeout=5) as connection:
            time.sleep(0.5)
            response = _authenticated_command(
                connection, auth_key, f"UPDATE_BEGIN {len(data)} {digest}"
            )
            if response != "READY":
                raise UpdateError(f"disconnect test was rejected before transfer: {response}")
            connection.write(data[:128])
            connection.flush()
        # Closing the host side simulates a USB/transport interruption. The
        # firmware must abort its inactive partition after the 30-second timer.
        time.sleep(32)
        status, _target = read_update_status(port, baud, auth_key, 0.5)
        current_version, current_hardware_id = read_device_info(port, baud, auth_key, 0.5)
        if status != "timeout":
            raise UpdateError(f"disconnect test status was {status}, expected timeout")
        if (current_version, current_hardware_id) != (version, hardware_id):
            raise UpdateError("disconnect test changed the running firmware")
        _record(event_client, hardware_id, event_token, "firmware_update_failed", version, {
            "test": "disconnect-mid-transfer", "reason": "timeout", "expected_result": True,
        })
        print(f"PASS disconnect: timed out safely and stayed at {version}")
    finally:
        resume_device(config, port)


def test_rollback(config: dict, device: dict, firmware: Path, event_client) -> None:
    port = str(device["port"])
    baud = int(config["serial_baud"])
    auth_key = str(device["auth_key"])
    event_token = str(device.get("event_token", ""))
    release_device(config, port)
    try:
        version, hardware_id = read_device_info(port, baud, auth_key, 0.5)
        cancel_rollback_test(port, baud, auth_key, 0.2)
        arm_rollback_test(port, baud, auth_key, 0.2)
    finally:
        resume_device(config, port)
    _record(event_client, hardware_id, event_token, "firmware_update_started", version, {
        "test": "forced-rollback", "target_version": version,
    })
    try:
        install_firmware(config, device, firmware, expected_version=version)
    except FirmwareRollbackError as exc:
        _record(event_client, hardware_id, event_token, "firmware_rollback", version, {
            "test": "forced-rollback", "target_version": version,
            "reason": str(exc), "expected_result": True,
        })
    else:
        raise UpdateError("forced rollback was not detected by the host")
    current_version, current_hardware_id = _inspect(config, device)
    if (current_version, current_hardware_id) != (version, hardware_id):
        raise UpdateError("rollback did not restore the previous working image")
    print(f"PASS rollback: restored and verified {version}")


def _inspect(config: dict, device: dict) -> tuple[str, str]:
    port = str(device["port"])
    release_device(config, port)
    try:
        return read_device_info(
            port, int(config["serial_baud"]), str(device["auth_key"]), 1.0
        )
    finally:
        resume_device(config, port)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run real ESP32 OTA failure/rollback tests")
    parser.add_argument("firmware", type=Path)
    args = parser.parse_args()
    config = load_config()
    devices = config.get("devices", [])
    if not devices:
        raise UpdateError("no configured device")
    device = devices[0]
    event_client = SupabaseDeviceEventClient.from_config(config)
    test_bad_sha(config, device, args.firmware, event_client)
    test_disconnect(config, device, args.firmware, event_client)
    test_rollback(config, device, args.firmware, event_client)
    print("All hardware OTA diagnostics passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

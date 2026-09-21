"""Turn the status light red on the first typing key in Codex for Windows.

The watcher classifies virtual-key codes only. It never converts, stores, or
logs the text that the user types.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import sys
import threading
import time
import uuid
from ctypes import wintypes
from pathlib import Path

from ai_status_core import load_config
from ai_status_hook import read_state, send_with_bridge_start, state_lock, write_state
from ai_status_projects import project_for_window


PROJECT_DIR = Path(__file__).resolve().parent
WATCHER_LOCK = PROJECT_DIR / ".ai_status_typing_watcher.lock"

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_SPACE = 0x20
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C
VK_V = 0x56
TH32CS_SNAPPROCESS = 0x00000002
CLAUDE_PROCESS_NAME = "claude.exe"
PROCESS_PROVIDERS = {
    "chatgpt.exe": "codex",
    "antigravity ide.exe": "antigravity",
    "code.exe": "copilot-vscode",
    "github.exe": "copilot-app",
    "cursor.exe": "cursor",
    "claude.exe": "claude-desktop",
}


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


if sys.platform == "win32":
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    HOOK_CALLBACK = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    user32.GetAsyncKeyState.restype = wintypes.SHORT
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int,
        HOOK_CALLBACK,
        wintypes.HINSTANCE,
        wintypes.DWORD,
    ]
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.CallNextHookEx.argtypes = [
        wintypes.HHOOK,
        ctypes.c_int,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.UnhookWindowsHookEx.argtypes = [wintypes.HHOOK]
    user32.UnhookWindowsHookEx.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32FirstW.restype = wintypes.BOOL
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
    kernel32.Process32NextW.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
        ctypes.POINTER(FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessIoCounters.argtypes = [wintypes.HANDLE, ctypes.POINTER(IO_COUNTERS)]
    kernel32.GetProcessIoCounters.restype = wintypes.BOOL


def is_typing_key(vk_code: int, control: bool = False, alt: bool = False, win: bool = False) -> bool:
    if alt or win:
        return False
    if control:
        return vk_code == VK_V
    return (
        0x30 <= vk_code <= 0x39
        or 0x41 <= vk_code <= 0x5A
        or 0x60 <= vk_code <= 0x6F
        or 0xBA <= vk_code <= 0xE2
        or vk_code in (VK_BACK, VK_SPACE)
    )


def is_submit_key(
    vk_code: int,
    shift: bool = False,
    control: bool = False,
    alt: bool = False,
    win: bool = False,
) -> bool:
    """Claude sends with Enter; Shift+Enter and shortcuts stay in the editor."""
    return vk_code == VK_RETURN and not (shift or control or alt or win)


def foreground_process_name() -> str:
    if sys.platform != "win32":
        return ""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return ""
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, process_id.value
    )
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(size)
        ):
            return ""
        return Path(buffer.value).name
    finally:
        kernel32.CloseHandle(handle)


def foreground_window_title() -> str:
    if sys.platform != "win32":
        return ""
    window = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(window)
    buffer = ctypes.create_unicode_buffer(max(1, length + 1))
    user32.GetWindowTextW(window, buffer, len(buffer))
    return buffer.value


def configured_projects(provider: str) -> list[str]:
    return [
        str(item.get("project", ""))
        for item in load_config().get("devices", [])
        if str(item.get("provider", "")) == provider and str(item.get("project", "")).strip()
    ]


def mark_typing_started(provider: str = "", project: str = "") -> bool:
    with state_lock():
        # A new foreground typing action is authoritative. Requiring GREEN
        # here made the first switch to another AI ignore RED when that AI had
        # no state yet or had a stale YELLOW after cancellation.
        if read_state(provider, project)["status"] == "RED":
            return False
        write_state(uuid.uuid4().hex, "RED", provider, project)
        send_with_bridge_start("RED", provider, project)
    return True


def _append_claude_event(event: str, status: str) -> None:
    record = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "provider": "claude-desktop",
        "event": event,
        "status": status,
        "session_id": "",
    }
    with (PROJECT_DIR / "integration-events.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(record, ensure_ascii=False) + "\n")


def mark_claude_submitted(project: str = "") -> str:
    token = uuid.uuid4().hex
    with state_lock():
        # The preceding typing/paste event must have armed the Claude turn.
        # This also filters Enter used for navigation and keyboard auto-repeat.
        if read_state("claude-desktop", project)["status"] != "RED":
            return ""
        write_state(token, "YELLOW", "claude-desktop", project)
        send_with_bridge_start("YELLOW", "claude-desktop", project)
    _append_claude_event("busy", "YELLOW")
    return token


def _filetime_value(value: FILETIME) -> int:
    return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)


def claude_activity_sample() -> tuple[int, int]:
    """Return aggregate Claude CPU ticks and I/O bytes without reading chat text."""
    if sys.platform != "win32":
        return (0, 0)
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot in (0, ctypes.c_void_p(-1).value):
        return (0, 0)
    cpu_ticks = 0
    io_bytes = 0
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(entry)
    try:
        more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            if entry.szExeFile.casefold() == CLAUDE_PROCESS_NAME:
                handle = kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, entry.th32ProcessID
                )
                if handle:
                    try:
                        created = FILETIME()
                        exited = FILETIME()
                        kernel = FILETIME()
                        user = FILETIME()
                        counters = IO_COUNTERS()
                        if kernel32.GetProcessTimes(
                            handle,
                            ctypes.byref(created),
                            ctypes.byref(exited),
                            ctypes.byref(kernel),
                            ctypes.byref(user),
                        ):
                            cpu_ticks += _filetime_value(kernel) + _filetime_value(user)
                        if kernel32.GetProcessIoCounters(handle, ctypes.byref(counters)):
                            io_bytes += (
                                counters.ReadTransferCount
                                + counters.WriteTransferCount
                                + counters.OtherTransferCount
                            )
                    finally:
                        kernel32.CloseHandle(handle)
            more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return (cpu_ticks, io_bytes)


def monitor_claude_completion(token: str, project: str = "") -> None:
    """Infer stream completion from Claude's aggregate renderer activity.

    Claude Desktop Chat exposes no lifecycle hooks. We therefore require real
    Claude CPU/I/O activity after Enter, then wait for a sustained quiet period.
    The state token prevents an older monitor from completing a newer turn.
    """
    previous = claude_activity_sample()
    started = time.monotonic()
    last_active = started
    saw_activity = False
    deadline = started + 1800.0
    while time.monotonic() < deadline:
        time.sleep(0.5)
        state = read_state("claude-desktop", project)
        if state["token"] != token or state["status"] != "YELLOW":
            return
        current = claude_activity_sample()
        cpu_delta = max(0, current[0] - previous[0])
        io_delta = max(0, current[1] - previous[1])
        previous = current
        now = time.monotonic()
        if cpu_delta >= 100_000 or io_delta >= 2_048:
            saw_activity = True
            last_active = now
        if saw_activity and now - started >= 2.0 and now - last_active >= 3.0:
            from ai_status_hook import complete_if_current

            complete_if_current(token, "claude-desktop", project)
            _append_claude_event("done", "GREEN")
            return


def acquire_single_instance_lock():
    lock_file = WATCHER_LOCK.open("a+b")
    lock_file.seek(0, 2)
    if lock_file.tell() == 0:
        lock_file.write(b"0")
        lock_file.flush()
    lock_file.seek(0)
    import msvcrt

    try:
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def run_watcher() -> int:
    if sys.platform != "win32":
        return 0
    instance_lock = acquire_single_instance_lock()
    if instance_lock is None:
        return 0

    targets = {
        name.casefold() for name in load_config()["typing_process_names"]
    }
    action_ready = threading.Event()
    action_lock = threading.Lock()
    pending_actions: list[tuple[str, str, str]] = []

    def queue_action(action: str, provider: str = "", project: str = "") -> None:
        with action_lock:
            item = (action, provider, project)
            if item not in pending_actions:
                pending_actions.append(item)
        action_ready.set()

    def worker() -> None:
        while True:
            action_ready.wait()
            action_ready.clear()
            with action_lock:
                actions = pending_actions[:]
                pending_actions.clear()
            try:
                for action, provider, project in actions:
                    if action == "typing":
                        mark_typing_started(provider, project)
                    elif action == "claude-submit":
                        token = mark_claude_submitted(project)
                        if token:
                            threading.Thread(
                                target=monitor_claude_completion,
                                args=(token, project),
                                daemon=True,
                            ).start()
            except Exception:
                pass

    threading.Thread(target=worker, daemon=True).start()

    @HOOK_CALLBACK
    def keyboard_callback(code, message, data):
        if code >= 0 and message in (WM_KEYDOWN, WM_SYSKEYDOWN):
            event = ctypes.cast(data, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
            control = bool(user32.GetAsyncKeyState(VK_CONTROL) & 0x8000)
            shift = bool(user32.GetAsyncKeyState(VK_SHIFT) & 0x8000)
            alt = bool(user32.GetAsyncKeyState(VK_MENU) & 0x8000)
            win = bool(
                user32.GetAsyncKeyState(VK_LWIN) & 0x8000
                or user32.GetAsyncKeyState(VK_RWIN) & 0x8000
            )
            process_name = foreground_process_name().casefold()
            provider = PROCESS_PROVIDERS.get(process_name, "")
            project = project_for_window(
                provider, foreground_window_title(), configured_projects(provider)
            ) if provider else ""
            if process_name == CLAUDE_PROCESS_NAME and is_submit_key(
                event.vkCode, shift, control, alt, win
            ):
                queue_action("claude-submit", "claude-desktop", project)
            elif process_name in targets and is_typing_key(
                event.vkCode, control, alt, win
            ):
                queue_action("typing", provider, project)
        return user32.CallNextHookEx(None, code, message, data)

    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, keyboard_callback, None, 0)
    if not hook:
        return 1
    try:
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(message))
            user32.DispatchMessageW(ctypes.byref(message))
    finally:
        user32.UnhookWindowsHookEx(hook)
        instance_lock.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Watch for typing in Codex")
    parser.add_argument("--diagnose", action="store_true")
    parser.add_argument("--test-trigger", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.diagnose:
        print(foreground_process_name())
        return 0
    if args.test_trigger:
        return 0 if mark_typing_started() else 1
    return run_watcher()


if __name__ == "__main__":
    raise SystemExit(main())

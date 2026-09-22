"""Modern desktop control panel for AI Status Light."""

from __future__ import annotations

import json
import queue
import socket
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import ttk

from ai_status_core import load_config, send_decor_to_bridge, send_to_bridge, stop_decor
from ai_status_hook import read_state, set_hook_status
from ai_status_integrations import INTEGRATIONS, install_integration, integration_state
from ai_status_projects import canonical_project, discover_projects
from ai_status_updates import (
    FirmwareRollbackError,
    GitHubReleaseClient,
    SupabaseDeviceEventClient,
    inspect_device,
    install_firmware,
    is_newer_version,
)

ROOT = Path(__file__).resolve().parent
EVENT_LOG = ROOT / "integration-events.jsonl"
WINDOW_BG, SHELL, PANEL, PANEL_2 = "#FFFFFF", "#101927", "#142131", "#18283A"
BORDER, TEXT, MUTED = "#273A52", "#F6F7FD", "#94A3B8"
PURPLE, PURPLE_2, CYAN, PINK = "#8B5CF6", "#A970FF", "#16C8F5", "#FF4F91"
RED, YELLOW, GREEN, OFF = "#FF5364", "#FFC94D", "#2AD587", "#53657A"
STATUS_COLORS = {"RED": RED, "YELLOW": YELLOW, "GREEN": GREEN, "OFF": OFF}
PROVIDER_NAMES = {
    "codex": "Codex", "claude-desktop": "Claude Desktop", "claude-code": "Claude Code",
    "antigravity": "Antigravity", "cursor": "Cursor", "copilot-app": "GitHub Copilot",
    "copilot-vscode": "Copilot · VS Code", "copilot-cli": "Copilot CLI",
}
PROVIDER_LABELS = {item.provider: item.name for item in INTEGRATIONS}
LABEL_PROVIDERS = {item.name: item.provider for item in INTEGRATIONS}
DECOR_EFFECTS = {
    "Static / Solid": "STATIC", "Blink": "BLINK", "Breathing": "BREATHING",
    "Fade In": "FADE_IN", "Fade Out": "FADE_OUT", "Smooth Transition": "SMOOTH",
    "Color Cycle": "CYCLE", "Rainbow": "RAINBOW", "Theater Chase": "THEATER",
    "Comet": "COMET", "Meteor": "METEOR", "Scanner / Cylon": "SCANNER",
    "Sparkle / Twinkle": "SPARKLE", "Police": "POLICE",
}


class ActionButton(tk.Button):
    def __init__(self, master, *, text: str, command, accent: bool = False, **kwargs):
        bg = PURPLE if accent else PANEL_2
        super().__init__(master, text=text, command=command, bg=bg, fg=TEXT,
                         activebackground=PURPLE_2 if accent else BORDER, activeforeground=TEXT,
                         relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 10, "bold"),
                         padx=20, pady=11, **kwargs)


class LogoBadge(tk.Canvas):
    """Small recognizable, code-drawn marks that need no external image files."""
    COLORS = {
        "codex": CYAN, "claude-desktop": "#D97757", "antigravity": PINK,
        "cursor": TEXT, "copilot-app": GREEN, "copilot-vscode": "#29A8E8",
    }

    def __init__(self, master, provider: str):
        super().__init__(master, width=54, height=54, bg=PANEL, bd=0, highlightthickness=0)
        color = self.COLORS.get(provider, PURPLE)
        self.create_oval(3, 3, 51, 51, fill="#0E1927", outline=color, width=2)
        if provider == "codex":
            for start in range(0, 360, 60):
                self.create_arc(12, 12, 42, 42, start=start, extent=42, style="arc", outline=color, width=3)
        elif provider == "claude-desktop":
            self.create_text(27, 28, text="AI", fill=color, font=("Georgia", 14, "bold"))
        elif provider == "antigravity":
            self.create_polygon(27, 9, 44, 39, 10, 39, fill=color, outline="")
            self.create_oval(21, 23, 33, 35, fill="#0E1927", outline="")
        elif provider == "cursor":
            self.create_polygon(15, 10, 42, 27, 25, 43, fill="", outline=color, width=3)
            self.create_line(15, 10, 25, 43, fill=color, width=2)
        elif provider == "copilot-app":
            self.create_oval(12, 14, 42, 39, outline=color, width=3)
            self.create_oval(18, 23, 23, 28, fill=color, outline="")
            self.create_oval(31, 23, 36, 28, fill=color, outline="")
            self.create_line(18, 12, 13, 7, fill=color, width=2)
            self.create_line(36, 12, 41, 7, fill=color, width=2)
        else:
            self.create_polygon(12, 16, 25, 10, 25, 44, 12, 38, fill=color, outline="")
            self.create_polygon(27, 22, 43, 15, 43, 39, 27, 32, fill="#1263A8", outline="")


class HeroVisual(tk.Canvas):
    """Code-native neon illustration inspired by the supplied visual style."""
    def __init__(self, master):
        super().__init__(master, width=440, height=330, bg=PANEL, bd=0, highlightthickness=0)
        self._draw()

    def _draw(self) -> None:
        for x in range(26, 106, 23):
            for y in range(72, 190, 23):
                self.create_rectangle(x, y, x + 3, y + 3, outline="#71829A")
        self.create_line(50, 38, 120, 38, fill=PURPLE, width=3)
        self.create_line(120, 38, 155, 38, fill=CYAN, width=3)
        self.create_oval(36, 31, 50, 45, fill=PINK, outline="")
        self.create_rectangle(112, 22, 335, 279, fill="#0D1724", outline=BORDER, width=2)
        self.create_rectangle(120, 30, 327, 271, fill="#132235", outline="")
        self.create_text(137, 49, text="AI STATUS CORE", fill=MUTED, anchor="w", font=("Segoe UI", 8, "bold"))
        for x in (294, 304, 314): self.create_oval(x, 43, x + 4, 47, fill=OFF, outline="")
        self.create_oval(151, 69, 273, 191, fill="#09121E", outline=BORDER, width=2)
        self.create_arc(143, 61, 281, 199, start=28, extent=138, style="arc", outline=CYAN, width=3)
        self.create_arc(143, 61, 281, 199, start=208, extent=98, style="arc", outline=PURPLE_2, width=3)
        self.orb = self.create_oval(164, 82, 260, 178, fill="#101B2A", outline=OFF, width=2)
        self.orb_dot = self.create_oval(205, 108, 219, 122, fill=OFF, outline="")
        self.orb_text = self.create_text(212, 143, text="OFF", fill=TEXT, font=("Segoe UI", 16, "bold"))
        self.create_text(141, 222, text="OUTPUT", fill=MUTED, anchor="w", font=("Segoe UI", 8, "bold"))
        self.channel_dots = {}
        for state, color, x in (("RED", RED, 201), ("YELLOW", YELLOW, 235), ("GREEN", GREEN, 269)):
            self.channel_dots[state] = self.create_oval(x, 213, x + 14, 227, fill="#26364A", outline=color)
        self.create_rectangle(348, 86, 423, 207, fill="#112033", outline=CYAN, width=2)
        self.create_text(385, 107, text="ESP32", fill=TEXT, font=("Segoe UI", 10, "bold"))
        self.create_line(361, 128, 410, 128, fill=BORDER, width=5)
        self.create_line(361, 128, 395, 128, fill=CYAN, width=5)
        self.create_line(361, 146, 410, 146, fill=BORDER, width=5)
        self.create_line(361, 146, 382, 146, fill=PURPLE, width=5)
        self.create_text(385, 177, text="COM5", fill=MUTED, font=("Segoe UI", 9))
        self.create_line(335, 146, 348, 146, fill="#66809C", dash=(3, 3), width=2)
        self.create_arc(316, 232, 356, 272, start=300, extent=120, style="arc", outline=PINK, width=3)
        self.create_arc(309, 225, 363, 279, start=300, extent=120, style="arc", outline=PURPLE, width=2)
        self.create_text(335, 289, text="BUZZER", fill=MUTED, font=("Segoe UI", 8, "bold"))
        self.create_rectangle(76, 252, 267, 309, fill="#0B1420", outline="#D8E2EF")
        self.create_rectangle(89, 265, 252, 296, fill="#24334A", outline="")
        self.create_rectangle(188, 265, 252, 296, fill=PURPLE, outline="")
        self.create_oval(58, 276, 70, 288, fill=PINK, outline="")
        self.create_oval(280, 285, 293, 298, fill=PURPLE_2, outline="")

    def set_status(self, status: str) -> None:
        status = status if status in STATUS_COLORS else "OFF"
        color = STATUS_COLORS[status]
        self.itemconfigure(self.orb, outline=color)
        self.itemconfigure(self.orb_dot, fill=color)
        self.itemconfigure(self.orb_text, text=status, fill=color if status != "OFF" else TEXT)
        for name, item in self.channel_dots.items():
            self.itemconfigure(item, fill=STATUS_COLORS[name] if name == status else "#26364A")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AI Status Light")
        self.geometry("1180x760")
        self.minsize(1020, 680)
        self.configure(bg=WINDOW_BG)
        self.option_add("*Font", ("Segoe UI", 10))
        self.result_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.pages, self.nav_buttons = {}, {}
        self.last_status = ""
        self.last_event_signature = (-1, "")
        self.available_firmware_release = None
        self._connection_check_running = False
        self._styles(); self._shell(); self._dashboard(); self._devices(); self._decor(); self._integrations(); self._settings()
        self.show_page("dashboard")
        self.after(150, self._poll)
        if load_config().get("auto_check_updates") and load_config().get("github_repository"):
            self.after(1200, self._check_firmware_updates)

    def _styles(self) -> None:
        style = ttk.Style(self); style.theme_use("clam")
        style.configure("Neon.TCombobox", fieldbackground=PANEL_2, background=PANEL_2,
                        foreground=TEXT, arrowcolor=CYAN, bordercolor=BORDER,
                        lightcolor=BORDER, darkcolor=BORDER, padding=9)
        style.map("Neon.TCombobox", fieldbackground=[("readonly", PANEL_2)], foreground=[("readonly", TEXT)])

    def _shell(self) -> None:
        shell = tk.Frame(self, bg=SHELL, highlightbackground="#343B70", highlightthickness=1)
        shell.pack(fill="both", expand=True, padx=24, pady=22)
        header = tk.Frame(shell, bg=SHELL, height=72); header.pack(fill="x", padx=36, pady=(10, 0)); header.pack_propagate(False)
        logo = tk.Frame(header, bg=SHELL); logo.pack(side="left", fill="y")
        mark = tk.Canvas(logo, width=24, height=32, bg=SHELL, bd=0, highlightthickness=0); mark.pack(side="left", pady=17)
        mark.create_rectangle(3, 9, 10, 16, fill=PURPLE_2, outline=""); mark.create_rectangle(12, 9, 18, 15, fill=CYAN, outline=""); mark.create_rectangle(3, 18, 9, 24, fill=PINK, outline="")
        tk.Label(logo, text="AI Status", fg=TEXT, bg=SHELL, font=("Segoe UI", 14, "bold")).pack(side="left", pady=18, padx=(4, 0))
        nav = tk.Frame(header, bg=SHELL); nav.pack(side="left", padx=35, fill="y")
        for key, label in (("dashboard", "Tổng quan"), ("devices", "Thiết bị"), ("decor", "Decor"), ("integrations", "Tích hợp"), ("settings", "Cài đặt")):
            button = tk.Button(nav, text=label, command=lambda page=key: self.show_page(page), bg=SHELL, fg=MUTED,
                               activebackground=SHELL, activeforeground=TEXT, relief="flat", bd=0, cursor="hand2",
                               font=("Segoe UI", 10), padx=17, pady=22)
            button.pack(side="left"); self.nav_buttons[key] = button
        self.connection_pill = tk.Label(header, text="●  Đang kiểm tra", fg=YELLOW, bg=PANEL_2,
                                        font=("Segoe UI", 9, "bold"), padx=15, pady=8)
        self.connection_pill.pack(side="right", pady=15)
        self.content = tk.Frame(shell, bg=SHELL); self.content.pack(fill="both", expand=True, padx=36, pady=(3, 28))

    def _page(self, key):
        frame = tk.Frame(self.content, bg=SHELL); self.pages[key] = frame; return frame

    def _title(self, page, title, subtitle):
        tk.Label(page, text=title, fg=TEXT, bg=SHELL, font=("Segoe UI", 25, "bold")).pack(anchor="w", pady=(22, 2))
        tk.Label(page, text=subtitle, fg=MUTED, bg=SHELL).pack(anchor="w", pady=(0, 18))

    def _card(self, master, **pack):
        card = tk.Frame(master, bg=PANEL, highlightbackground=BORDER, highlightthickness=1); card.pack(**pack); return card

    def _dashboard(self):
        page = self._page("dashboard")
        hero = tk.Frame(page, bg=PANEL, highlightbackground="#3B3F75", highlightthickness=1); hero.pack(fill="x", pady=(8, 14))
        copy = tk.Frame(hero, bg=PANEL, width=540); copy.pack(side="left", fill="both", expand=True, padx=(44, 10), pady=34)
        tk.Label(copy, text="INTRODUCING  AI STATUS LIGHT", fg=PURPLE_2, bg=PANEL, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(copy, text="Smart signals for", fg=TEXT, bg=PANEL, font=("Segoe UI", 31, "bold")).pack(anchor="w", pady=(8, 0))
        title_row = tk.Frame(copy, bg=PANEL); title_row.pack(anchor="w")
        tk.Label(title_row, text="your ", fg=TEXT, bg=PANEL, font=("Segoe UI", 31, "bold")).pack(side="left")
        tk.Label(title_row, text="AI workflow.", fg=PURPLE_2, bg=PANEL, font=("Segoe UI", 31, "bold")).pack(side="left")
        tk.Label(copy, text="Một sản phẩm của 47Square. Từ giao diện lập trình đến các công cụ AI khác, \ntrạng thái vốn chỉ tồn tại trên màn hình giờ trở thành một phần của không gian làm việc thực tế.",
                 fg=MUTED, bg=PANEL, justify="left").pack(anchor="w", pady=(15, 19))
        row = tk.Frame(copy, bg=PANEL); row.pack(anchor="w", fill="x")
        self.hero_status = tk.Label(row, text="●  Đang tải", fg=YELLOW, bg=PANEL, font=("Segoe UI", 11, "bold")); self.hero_status.pack(side="left")
        self.hero_description = tk.Label(row, text="", fg=MUTED, bg=PANEL); self.hero_description.pack(side="left", padx=10)
        buttons = tk.Frame(copy, bg=PANEL); buttons.pack(anchor="w", pady=(19, 0))
        ActionButton(buttons, text="Kiểm tra tín hiệu", command=lambda: self.send_status("GREEN"), accent=True).pack(side="left")
        ActionButton(buttons, text="Tắt", command=lambda: self.send_status("OFF")).pack(side="left", padx=10)
        self.hero_visual = HeroVisual(hero); self.hero_visual.pack(side="right", padx=(0, 27), pady=14)
        metrics = tk.Frame(page, bg=SHELL); metrics.pack(fill="x")
        self.metric_connection = self._metric(metrics, "KẾT NỐI", "Đang kiểm tra", "Đang nhận diện thiết bị", CYAN)
        self._metric(metrics, "TÍCH HỢP", "6 AI", "Đã cấu hình", PURPLE_2)
        self.metric_events = self._metric(metrics, "HOẠT ĐỘNG", "0", "Sự kiện hôm nay", PINK)
        activity = self._card(page, fill="both", expand=True, pady=(14, 0))
        top = tk.Frame(activity, bg=PANEL); top.pack(fill="x", padx=20, pady=(14, 7))
        tk.Label(top, text="Hoạt động gần đây", fg=TEXT, bg=PANEL, font=("Segoe UI", 12, "bold")).pack(side="left")
        tk.Label(top, text="LIVE", fg=GREEN, bg="#15332C", font=("Segoe UI", 8, "bold"), padx=8, pady=3).pack(side="right")
        self.event_list = tk.Frame(activity, bg=PANEL); self.event_list.pack(fill="both", expand=True, padx=20, pady=(0, 11))

    def _metric(self, master, eyebrow, value, caption, color):
        card = tk.Frame(master, bg=PANEL, highlightbackground=BORDER, highlightthickness=1); card.pack(side="left", fill="x", expand=True, padx=(0, 10))
        tk.Frame(card, bg=color, width=4).pack(side="left", fill="y")
        content = tk.Frame(card, bg=PANEL); content.pack(side="left", fill="both", expand=True, padx=17, pady=12)
        tk.Label(content, text=eyebrow, fg=color, bg=PANEL, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        label = tk.Label(content, text=value, fg=TEXT, bg=PANEL, font=("Segoe UI", 15, "bold")); label.pack(anchor="w", pady=(2, 0))
        caption_label = tk.Label(content, text=caption, fg=MUTED, bg=PANEL, font=("Segoe UI", 9))
        caption_label.pack(anchor="w")
        label.caption_label = caption_label
        return label

    def _devices(self):
        page = self._page("devices"); self._title(page, "Thiết bị", "Quản lý phần cứng thiết bị và kiểm tra tín hiệu đầu ra")
        self.device_list = tk.Frame(page, bg=SHELL)
        self.device_list.pack(fill="x")
        self.device_status_labels = {}
        self.device_combos = {}
        self.device_project_combos = {}
        self._render_devices()
        controls = self._card(page, fill="x", pady=14)
        tk.Label(controls, text="Kiểm tra đầu ra", fg=TEXT, bg=PANEL, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=22, pady=(17, 12))
        row = tk.Frame(controls, bg=PANEL); row.pack(fill="x", padx=20, pady=(0, 18))
        for label, value, color in (("●  Chờ lệnh", "RED", RED), ("●  Đang xử lí", "YELLOW", YELLOW), ("●  Hoàn thành", "GREEN", GREEN), ("Tắt", "OFF", PANEL_2)):
            tk.Button(row, text=label, command=lambda state=value: self.send_status(state), bg=color,
                      fg=SHELL if value in ("YELLOW", "GREEN") else TEXT, activebackground=color,
                      relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 10, "bold"), padx=17, pady=10).pack(side="left", padx=(0, 9))

    def _write_config(self, update) -> None:
        config = load_config()
        update(config)
        temporary = ROOT / "config.json.tmp"
        temporary.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(ROOT / "config.json")

    def _device_records(self) -> list[dict]:
        config = load_config()
        devices = [dict(item) for item in config.get("devices", [])]
        known = {str(item.get("port", "")).casefold() for item in devices}
        for port in self._serial_ports():
            if port.casefold() not in known:
                devices.append({"id": f"esp32-{port.casefold()}", "name": "ESP32", "port": port, "provider": "", "project": ""})
        return devices

    def _render_devices(self) -> None:
        for child in self.device_list.winfo_children():
            child.destroy()
        devices = self._device_records()
        used_assignments = {
            (str(item.get("provider", "")), canonical_project(item.get("project", ""))): str(item.get("port", ""))
            for item in devices if item.get("provider") and item.get("project")
        }
        self.device_status_labels.clear()
        self.device_combos.clear()
        self.device_project_combos.clear()
        for device in devices:
            port = str(device.get("port", ""))
            current = str(device.get("provider", ""))
            current_project = canonical_project(device.get("project", ""))
            card = tk.Frame(self.device_list, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            card.pack(fill="x", pady=(0, 10))
            art = tk.Canvas(card, width=88, height=88, bg=PANEL, bd=0, highlightthickness=0)
            art.pack(side="left", padx=20, pady=14)
            art.create_rectangle(13, 17, 75, 71, fill="#0C1724", outline=CYAN, width=2)
            art.create_rectangle(23, 27, 65, 61, fill=PANEL_2, outline=PURPLE)
            art.create_text(44, 44, text="ESP32", fill=TEXT, font=("Segoe UI", 9, "bold"))
            info = tk.Frame(card, bg=PANEL); info.pack(side="left", fill="both", expand=True, pady=18)
            tk.Label(info, text=str(device.get("name", "AI Status Light - 1")), fg=TEXT, bg=PANEL, font=("Segoe UI", 14, "bold")).pack(anchor="w")
            status = tk.Label(info, text="●  Đang kiểm tra", fg=YELLOW, bg=PANEL, font=("Segoe UI", 9, "bold"))
            status.pack(anchor="w", pady=(5, 0)); self.device_status_labels[port] = status
            assignment = tk.Frame(card, bg=PANEL); assignment.pack(side="right", padx=(10, 18), pady=16)
            tk.Label(assignment, text="AI", fg=MUTED, bg=PANEL, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
            tk.Label(assignment, text="Project hoạt động", fg=MUTED, bg=PANEL, font=("Segoe UI", 9)).grid(row=0, column=1, sticky="w", padx=(10, 0))
            allowed = ["Chưa gán"] + [item.name for item in INTEGRATIONS]
            combo = ttk.Combobox(assignment, style="Neon.TCombobox", state="readonly", values=allowed, width=19)
            combo.grid(row=1, column=0, sticky="w", pady=(5, 0)); combo.set(PROVIDER_LABELS.get(current, "Chưa gán"))
            combo.bind("<<ComboboxSelected>>", lambda _event, selected_port=port: self._assign_device(selected_port))
            self.device_combos[port] = combo
            configured = [str(item.get("project", "")) for item in devices if str(item.get("provider", "")) == current]
            projects = discover_projects(current, configured) if current else []
            available = [
                project for project in projects
                if project == current_project or (current, project) not in used_assignments
            ]
            project_box = ttk.Combobox(
                assignment, style="Neon.TCombobox", state="readonly" if current else "disabled",
                values=available or (["Chưa tìm thấy project"] if current else []), width=34,
            )
            project_box.grid(row=1, column=1, sticky="w", padx=(10, 0), pady=(5, 0))
            if current_project:
                project_box.set(current_project)
            elif current and not available:
                project_box.set("Chưa tìm thấy project")
            project_box.bind("<<ComboboxSelected>>", lambda _event, selected_port=port: self._assign_project(selected_port))
            self.device_project_combos[port] = project_box
            tk.Label(card, text=port, fg=CYAN, bg="#112C3A", font=("Segoe UI", 11, "bold"), padx=13, pady=8).pack(side="right", padx=(0, 8))

    def _assign_device(self, port: str) -> None:
        label = self.device_combos[port].get()
        provider = LABEL_PROVIDERS.get(label, "")
        def update(config):
            devices = [dict(item) for item in config.get("devices", [])]
            found = False
            for item in devices:
                if str(item.get("port", "")).casefold() == port.casefold():
                    item["provider"] = provider
                    item["project"] = ""
                    found = True
            if not found:
                devices.append({"id": f"esp32-{port.casefold()}", "name": "ESP32", "port": port, "provider": provider, "project": ""})
            config["devices"] = devices
        self._write_config(update)
        self._reset_device(port)
        self._render_devices()

    def _assign_project(self, port: str) -> None:
        selected = self.device_project_combos[port].get()
        if not selected or selected == "Chưa tìm thấy project":
            return
        project = canonical_project(selected)
        def update(config):
            for item in config.get("devices", []):
                if str(item.get("port", "")).casefold() == port.casefold():
                    item["project"] = project
        self._write_config(update)
        self._reset_device(port)
        self._render_devices()

    def _reset_device(self, port: str) -> None:
        """A changed assignment always starts from a deterministic idle state."""
        try:
            send_to_bridge("RED", load_config(), target_port=port)
            self.connection_pill.configure(text=f"●  {port} đã về RED", fg=RED)
        except Exception:
            self.connection_pill.configure(text=f"●  Không reset được {port}", fg=RED)

    def _integrations(self):
        page = self._page("integrations"); self._title(page, "Tích hợp AI", "Sáu nền tảng kết nối với một hệ thống trạng thái thống nhất")
        self.integration_grid = tk.Frame(page, bg=SHELL); self.integration_grid.pack(fill="both", expand=True)
        self.integration_status_labels = {}
        self.integration_buttons = {}
        self._render_integrations()

    def _decor(self):
        page = self._page("decor")
        self._title(page, "Chế độ Decor", "Điều khiển thủ công và hiệu ứng ánh sáng cho từng ESP32")

        control = self._card(page, fill="x")
        top = tk.Frame(control, bg=PANEL); top.pack(fill="x", padx=22, pady=(18, 12))
        tk.Label(top, text="Thiết bị", fg=MUTED, bg=PANEL).grid(row=0, column=0, sticky="w")
        tk.Label(top, text="Hiệu ứng", fg=MUTED, bg=PANEL).grid(row=0, column=1, sticky="w", padx=(14, 0))
        tk.Label(top, text="Tốc độ", fg=MUTED, bg=PANEL).grid(row=0, column=2, sticky="w", padx=(14, 0))
        ports = [str(item.get("port", "")) for item in self._device_records() if item.get("port")]
        self.decor_port = ttk.Combobox(top, style="Neon.TCombobox", state="readonly", values=ports, width=17)
        self.decor_port.grid(row=1, column=0, sticky="w", pady=(6, 0))
        if ports: self.decor_port.set(ports[0])
        self.decor_effect = ttk.Combobox(top, style="Neon.TCombobox", state="readonly", values=list(DECOR_EFFECTS), width=23)
        self.decor_effect.grid(row=1, column=1, sticky="w", padx=(14, 0), pady=(6, 0)); self.decor_effect.set("Static / Solid")
        self.decor_speed = tk.Scale(top, from_=1, to=100, orient="horizontal", showvalue=True, length=265,
                                    bg=PANEL, fg=TEXT, troughcolor=PANEL_2, activebackground=PURPLE,
                                    highlightthickness=0, bd=0)
        self.decor_speed.grid(row=1, column=2, sticky="w", padx=(14, 0)); self.decor_speed.set(50)

        channels = self._card(page, fill="x", pady=14)
        tk.Label(channels, text="Kênh đèn", fg=TEXT, bg=PANEL, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=22, pady=(17, 4))
        tk.Label(channels, text="Bật/tắt riêng từng LED. Thay đổi kênh sẽ áp dụng ngay ở chế độ Static.", fg=MUTED, bg=PANEL).pack(anchor="w", padx=22)
        channel_row = tk.Frame(channels, bg=PANEL); channel_row.pack(fill="x", padx=22, pady=14)
        self.decor_channels = {"RED": tk.IntVar(value=1), "YELLOW": tk.IntVar(value=1), "GREEN": tk.IntVar(value=1)}
        for name, label, color in (("RED", "Đỏ", RED), ("YELLOW", "Vàng", YELLOW), ("GREEN", "Xanh", GREEN)):
            tk.Checkbutton(channel_row, text=f"●  {label}", variable=self.decor_channels[name], command=self._decor_channels_changed,
                           bg=PANEL_2, fg=color, selectcolor="#0E1927", activebackground=BORDER,
                           activeforeground=color, relief="flat", bd=0, cursor="hand2",
                           font=("Segoe UI", 10, "bold"), padx=16, pady=10).pack(side="left", padx=(0, 10))
        ActionButton(channel_row, text="Bật tất cả", command=lambda: self._decor_all(True), accent=True).pack(side="left", padx=(10, 8))
        ActionButton(channel_row, text="Tắt tất cả", command=lambda: self._decor_all(False)).pack(side="left")

        effects = self._card(page, fill="both", expand=True)
        body = tk.Frame(effects, bg=PANEL); body.pack(fill="both", expand=True, padx=22, pady=18)
        preview = tk.Canvas(body, width=310, height=130, bg="#0D1724", highlightbackground=BORDER, highlightthickness=1)
        preview.pack(side="left", fill="y")
        preview.create_text(155, 22, text="DECOR OUTPUT", fill=MUTED, font=("Segoe UI", 8, "bold"))
        for x, color, label in ((70, RED, "R"), (155, YELLOW, "Y"), (240, GREEN, "G")):
            preview.create_oval(x - 24, 45, x + 24, 93, fill=color, outline="#FFFFFF", width=2)
            preview.create_text(x, 110, text=label, fill=TEXT, font=("Segoe UI", 9, "bold"))
        actions = tk.Frame(body, bg=PANEL); actions.pack(side="left", fill="both", expand=True, padx=(24, 0))
        tk.Label(actions, text="Hiệu ứng firmware non-blocking", fg=TEXT, bg=PANEL, font=("Segoe UI", 13, "bold")).pack(anchor="w")
        tk.Label(actions, text="Tín hiệu AI mới sẽ tự dừng Decor và giành lại quyền điều khiển đèn.", fg=MUTED, bg=PANEL).pack(anchor="w", pady=(4, 15))
        buttons = tk.Frame(actions, bg=PANEL); buttons.pack(anchor="w")
        ActionButton(buttons, text="▶  Chạy hiệu ứng", command=self._start_decor, accent=True).pack(side="left")
        ActionButton(buttons, text="■  Dừng và tắt", command=self._stop_decor).pack(side="left", padx=10)
        self.decor_status = tk.Label(actions, text="●  Sẵn sàng", fg=MUTED, bg=PANEL, font=("Segoe UI", 9, "bold"))
        self.decor_status.pack(anchor="w", pady=(16, 0))

    def _decor_mask(self) -> int:
        return (
            self.decor_channels["RED"].get()
            | (self.decor_channels["YELLOW"].get() << 1)
            | (self.decor_channels["GREEN"].get() << 2)
        )

    def _decor_all(self, enabled: bool) -> None:
        for variable in self.decor_channels.values(): variable.set(1 if enabled else 0)
        self._send_decor("STATIC")

    def _decor_channels_changed(self) -> None:
        self.decor_effect.set("Static / Solid")
        self._send_decor("STATIC")

    def _start_decor(self) -> None:
        self._send_decor(DECOR_EFFECTS.get(self.decor_effect.get(), "STATIC"))

    def _send_decor(self, effect: str) -> None:
        port = self.decor_port.get()
        if not port:
            self.decor_status.configure(text="●  Chưa có thiết bị", fg=RED); return
        mask, speed = self._decor_mask(), int(self.decor_speed.get())
        self.decor_status.configure(text="●  Đang gửi...", fg=YELLOW)
        def run():
            try: send_decor_to_bridge(load_config(), port, effect, mask, speed)
            except Exception as exc: self.result_queue.put(("decor-error", str(exc)))
            else: self.result_queue.put(("decor", f"{effect} · {port}"))
        threading.Thread(target=run, daemon=True).start()

    def _stop_decor(self) -> None:
        port = self.decor_port.get()
        if not port: return
        self.decor_status.configure(text="●  Đang dừng...", fg=YELLOW)
        def run():
            try: stop_decor(load_config(), port)
            except Exception as exc: self.result_queue.put(("decor-error", str(exc)))
            else: self.result_queue.put(("decor", f"Đã tắt · {port}"))
        threading.Thread(target=run, daemon=True).start()

    def _render_integrations(self):
        for child in self.integration_grid.winfo_children():
            child.destroy()
        for i, item in enumerate(INTEGRATIONS):
            card = tk.Frame(self.integration_grid, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
            card.grid(row=i // 2, column=i % 2, sticky="nsew", padx=(0 if i % 2 == 0 else 7, 7 if i % 2 == 0 else 0), pady=(0, 12))
            self.integration_grid.grid_columnconfigure(i % 2, weight=1)
            LogoBadge(card, item.provider).pack(side="left", padx=16, pady=16)
            labels = tk.Frame(card, bg=PANEL); labels.pack(side="left", fill="both", expand=True, pady=16)
            tk.Label(labels, text=item.name, fg=TEXT, bg=PANEL, font=("Segoe UI", 12, "bold")).pack(anchor="w")
            tk.Label(labels, text=item.adapter, fg=MUTED, bg=PANEL, font=("Segoe UI", 9)).pack(anchor="w", pady=(3, 4))
            state, message = integration_state(item.provider)
            color = GREEN if state == "active" else YELLOW if state == "needs-setup" else RED
            status = tk.Label(labels, text=f"●  {message}", fg=color, bg=PANEL, font=("Segoe UI", 9, "bold"))
            status.pack(anchor="w"); self.integration_status_labels[item.provider] = status
            button_text = "Cài lại" if state == "active" else "Thiết lập"
            button = tk.Button(card, text=button_text, command=lambda provider=item.provider: self._install_integration(provider),
                               bg=PANEL_2, fg=TEXT, activebackground=PURPLE, activeforeground=TEXT,
                               relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 9, "bold"), padx=13, pady=8)
            button.pack(side="right", padx=16); self.integration_buttons[item.provider] = button

    def _install_integration(self, provider: str) -> None:
        button = self.integration_buttons[provider]
        button.configure(text="Đang cài...", state="disabled")
        def run():
            try:
                state, message = install_integration(provider)
            except Exception as exc:
                state, message = "error", f"Lỗi: {exc}"
            self.result_queue.put(("integration", json.dumps({"provider": provider, "state": state, "message": message}, ensure_ascii=False)))
        threading.Thread(target=run, daemon=True).start()

    def _updates(self, page):
        cfg = load_config()

        source = self._card(page, fill="x")
        tk.Label(source, text="Nguồn GitHub Releases", fg=TEXT, bg=PANEL, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=22, pady=(18, 5))
        tk.Label(source, text="Nhập repository theo dạng owner/repository. ESP32 không cần Wi-Fi.", fg=MUTED, bg=PANEL).pack(anchor="w", padx=22)
        source_row = tk.Frame(source, bg=PANEL); source_row.pack(fill="x", padx=22, pady=16)
        self.update_repository = tk.Entry(source_row, bg=PANEL_2, fg=TEXT, insertbackground=TEXT,
                                          relief="flat", bd=0, font=("Segoe UI", 10), width=45)
        self.update_repository.pack(side="left", ipady=11, padx=(0, 10))
        self.update_repository.insert(0, str(cfg.get("github_repository", "")))
        ActionButton(source_row, text="Kiểm tra bản mới", command=self._check_firmware_updates, accent=True).pack(side="left")

        release = self._card(page, fill="x", pady=14)
        release_body = tk.Frame(release, bg=PANEL); release_body.pack(fill="x", padx=22, pady=20)
        self.update_status = tk.Label(release_body, text="●  Chưa kiểm tra", fg=MUTED, bg=PANEL,
                                      font=("Segoe UI", 12, "bold"))
        self.update_status.pack(anchor="w")
        self.update_detail = tk.Label(release_body, text="Firmware trên GitHub sẽ được kiểm tra SHA-256 trước khi nạp.",
                                      fg=MUTED, bg=PANEL, justify="left")
        self.update_detail.pack(anchor="w", pady=(6, 16))
        self.update_button = ActionButton(release_body, text="Cập nhật tất cả thiết bị", command=self._install_firmware_update)
        self.update_button.pack(anchor="w")
        self.update_button.configure(state="disabled")

        safety = self._card(page, fill="x")
        tk.Label(safety, text="Bảo vệ cập nhật", fg=TEXT, bg=PANEL, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=22, pady=(18, 7))
        tk.Label(safety, text="✓ GitHub asset digest SHA-256   ✓ HMAC theo từng ESP32   ✓ Ghi vào phân vùng OTA dự phòng\nKhông rút cáp USB trong lúc thanh tiến trình đang chạy.",
                 fg=MUTED, bg=PANEL, justify="left").pack(anchor="w", padx=22, pady=(0, 18))

    def _save_update_repository(self, repository: str) -> None:
        repository = repository.strip().strip("/")
        self._write_config(lambda config: config.__setitem__("github_repository", repository))

    def _check_firmware_updates(self) -> None:
        repository = self.update_repository.get().strip().strip("/")
        if not repository:
            self.update_status.configure(text="●  Chưa nhập GitHub repository", fg=RED)
            return
        self._save_update_repository(repository)
        self.update_status.configure(text="●  Đang kiểm tra GitHub...", fg=YELLOW)
        self.update_button.configure(state="disabled")

        def run():
            try:
                config = load_config()
                client = GitHubReleaseClient(repository, str(config["firmware_asset_name"]), str(config["update_channel"]))
                release = client.latest()
                devices = list(config.get("devices", []))
                versions = []
                for device in devices:
                    version, hardware_id = inspect_device(config, device)
                    versions.append({"port": device["port"], "version": version, "hardware_id": hardware_id})
                self.available_firmware_release = release
                available = any(is_newer_version(release.version, item["version"]) for item in versions)
                self.result_queue.put(("update-check", json.dumps({
                    "available": available, "latest": release.version, "devices": versions,
                    "name": release.name,
                }, ensure_ascii=False)))
            except Exception as exc:
                self.result_queue.put(("update-error", str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def _install_firmware_update(self) -> None:
        release = self.available_firmware_release
        if release is None:
            return
        self.update_button.configure(state="disabled")
        self.update_status.configure(text="●  Đang tải firmware...", fg=YELLOW)

        def progress(done: int, total: int) -> None:
            percent = int(done * 100 / max(1, total))
            self.result_queue.put(("update-progress", str(percent)))

        def run():
            try:
                config = load_config()
                client = GitHubReleaseClient(str(config["github_repository"]), str(config["firmware_asset_name"]), str(config["update_channel"]))
                firmware = client.download(release, ROOT / "updates" / release.asset_name, progress)
                event_client = SupabaseDeviceEventClient.from_config(config)
                for device in config.get("devices", []):
                    previous_version, hardware_id = inspect_device(config, device)
                    event_token = str(device.get("event_token", ""))

                    def record(event_type: str, version: str, metadata: dict[str, object]) -> None:
                        if event_client is not None and event_token:
                            event_client.record(hardware_id, event_token, event_type, version, metadata)

                    record("firmware_update_started", previous_version, {
                        "target_version": release.version,
                        "source": "github-release",
                    })
                    try:
                        result = install_firmware(
                            config, device, firmware, progress, expected_version=release.version
                        )
                    except FirmwareRollbackError as exc:
                        record("firmware_rollback", previous_version, {
                            "target_version": release.version,
                            "reason": str(exc),
                        })
                        raise
                    except Exception as exc:
                        try:
                            record("firmware_update_failed", previous_version, {
                                "target_version": release.version,
                                "reason": str(exc),
                            })
                        except Exception:
                            pass
                        raise
                    record("firmware_update_succeeded", result.current_version, {
                        "previous_version": result.previous_version,
                        "target_version": release.version,
                        "device_status": result.update_status,
                    })
                self.result_queue.put(("update-done", release.version))
            except Exception as exc:
                self.result_queue.put(("update-error", str(exc)))
        threading.Thread(target=run, daemon=True).start()

    def _settings(self):
        page = self._page("settings"); self._title(page, "Cài đặt", "Tinh chỉnh kết nối và hành vi của AI Status Light")
        tabs = tk.Frame(page, bg=SHELL); tabs.pack(fill="x", pady=(0, 12))
        body = tk.Frame(page, bg=SHELL); body.pack(fill="both", expand=True)
        general_page = tk.Frame(body, bg=SHELL)
        update_page = tk.Frame(body, bg=SHELL)
        tab_buttons = {}

        def show_settings_tab(name):
            general_page.pack_forget(); update_page.pack_forget()
            target = general_page if name == "general" else update_page
            target.pack(fill="both", expand=True)
            for key, button in tab_buttons.items():
                button.configure(bg=PURPLE if key == name else PANEL_2)

        for key, label in (("general", "Thiết lập chung"), ("updates", "Cập nhật firmware")):
            button = tk.Button(
                tabs, text=label, command=lambda selected=key: show_settings_tab(selected),
                bg=PANEL_2, fg=TEXT, activebackground=PURPLE, activeforeground=TEXT,
                relief="flat", bd=0, cursor="hand2", font=("Segoe UI", 9, "bold"), padx=16, pady=8,
            )
            button.pack(side="left", padx=(0, 8)); tab_buttons[key] = button

        cfg = load_config(); connection = self._card(general_page, fill="x")
        tk.Label(connection, text="Kết nối thiết bị", fg=TEXT, bg=PANEL, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=22, pady=(18, 14))
        form = tk.Frame(connection, bg=PANEL); form.pack(fill="x", padx=22, pady=(0, 20))
        tk.Label(form, text="Cổng serial", fg=MUTED, bg=PANEL).grid(row=0, column=0, sticky="w")
        self.port_box = ttk.Combobox(form, style="Neon.TCombobox", state="readonly", values=self._serial_ports(), width=20)
        self.port_box.grid(row=1, column=0, sticky="w", pady=(6, 0), padx=(0, 14)); self.port_box.set(str(cfg["serial_port"]))
        tk.Label(form, text="Baud rate", fg=MUTED, bg=PANEL).grid(row=0, column=1, sticky="w")
        baud = ttk.Combobox(form, style="Neon.TCombobox", state="readonly", values=(9600, 57600, 115200), width=20)
        baud.grid(row=1, column=1, sticky="w", pady=(6, 0), padx=(0, 14)); baud.set(str(cfg["serial_baud"]))
        ActionButton(form, text="Làm mới cổng", command=self.refresh_ports).grid(row=1, column=2, pady=(6, 0))
        sound = self._card(general_page, fill="x", pady=14)
        tk.Label(sound, text="Thiết lập chức năng từng màu", fg=TEXT, bg=PANEL, font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=22, pady=(18, 5))
        tk.Label(sound, text="Chọn âm báo và hiệu ứng riêng cho từng trạng thái đèn.", fg=MUTED, bg=PANEL).pack(anchor="w", padx=22)
        beep_row = tk.Frame(sound, bg=PANEL); beep_row.pack(fill="x", padx=22, pady=16)
        self.beep_boxes = {}
        self.effect_boxes = {}
        options = ("Tắt", "1 tiếng bíp", "2 tiếng bíp", "3 tiếng bíp")
        effect_labels = ("Đèn sáng", "Sáng dần · tối dần", "Chớp liên tục")
        effect_values = ("STATIC", "BREATHING", "BLINK")
        for column, (state, label, color) in enumerate((("RED", "Đèn đỏ", RED), ("YELLOW", "Đèn vàng", YELLOW), ("GREEN", "Đèn xanh", GREEN))):
            group = tk.Frame(beep_row, bg=PANEL_2, highlightbackground=BORDER, highlightthickness=1)
            group.grid(row=0, column=column, sticky="nsew", padx=(0, 12))
            beep_row.grid_columnconfigure(column, weight=1)
            inner = tk.Frame(group, bg=PANEL_2); inner.pack(fill="both", padx=14, pady=12)
            tk.Label(inner, text=f"●  {label}", fg=color, bg=PANEL_2, font=("Segoe UI", 10, "bold")).pack(anchor="w")
            tk.Label(inner, text="Âm báo", fg=MUTED, bg=PANEL_2, font=("Segoe UI", 8)).pack(anchor="w", pady=(10, 0))
            combo = ttk.Combobox(inner, style="Neon.TCombobox", state="readonly", values=options, width=20)
            combo.pack(anchor="w", pady=(6, 0)); combo.current(int(cfg["status_beeps"][state]))
            combo.bind("<<ComboboxSelected>>", lambda _event, selected_state=state: self._save_beep(selected_state))
            self.beep_boxes[state] = combo
            tk.Label(inner, text="Hiệu ứng LED", fg=MUTED, bg=PANEL_2, font=("Segoe UI", 8)).pack(anchor="w", pady=(10, 0))
            effect_box = ttk.Combobox(inner, style="Neon.TCombobox", state="readonly", values=effect_labels, width=20)
            effect_box.pack(anchor="w", pady=(6, 0))
            effect_box.current(effect_values.index(str(cfg["status_effects"][state])))
            effect_box.bind("<<ComboboxSelected>>", lambda _event, selected_state=state: self._save_effect(selected_state))
            self.effect_boxes[state] = effect_box
        ActionButton(sound, text="▶  Kiểm tra đèn xanh", command=lambda: self.send_status("GREEN"), accent=True).pack(anchor="w", padx=22, pady=(0, 16))

        self._updates(update_page)
        show_settings_tab("general")

    def _save_beep(self, state: str) -> None:
        count = self.beep_boxes[state].current()
        self._write_config(lambda config: config["status_beeps"].__setitem__(state, count))
        self.connection_pill.configure(text=f"●  Đã lưu {state}: {count} bíp", fg=GREEN)

    def _save_effect(self, state: str) -> None:
        effects = ("STATIC", "BREATHING", "BLINK")
        effect = effects[self.effect_boxes[state].current()]
        self._write_config(lambda config: config["status_effects"].__setitem__(state, effect))
        self.connection_pill.configure(text=f"●  Đã lưu hiệu ứng {state}", fg=GREEN)

    def _serial_ports(self):
        try:
            from serial.tools import list_ports
            # Physical ESP boards expose a USB VID. This excludes motherboard
            # COM ports such as COM1 from being mistaken for an extra device.
            return [p.device for p in list_ports.comports() if p.vid is not None]
        except Exception:
            return []

    def refresh_ports(self):
        ports = self._serial_ports(); self.port_box.configure(values=ports)
        if ports: self.port_box.set(ports[0])

    def show_page(self, key):
        for page in self.pages.values(): page.pack_forget()
        self.pages[key].pack(fill="both", expand=True)
        for name, button in self.nav_buttons.items():
            button.configure(fg=PURPLE_2 if name == key else MUTED, font=("Segoe UI", 10, "bold" if name == key else "normal"))

    def send_status(self, status):
        self.connection_pill.configure(text="●  Đang gửi", fg=YELLOW)
        def run():
            try: set_hook_status(status)
            except Exception as exc: self.result_queue.put(("error", str(exc)))
            else: self.result_queue.put(("sent", status))
        threading.Thread(target=run, daemon=True).start()

    def _ping_bridge(self):
        cfg = load_config()
        try:
            with socket.create_connection((str(cfg["host"]), int(cfg["port"])), timeout=.35) as client:
                token = str(cfg.get("bridge_auth_token", "")).strip()
                payload = f"TOKEN {token} PING\n" if token else "PING\n"
                client.sendall(payload.encode("ascii")); return client.recv(16).strip() == b"OK"
        except OSError: return False

    def _probe_bridge_device(self, config, port):
        timeout = max(
            1.5,
            float(config.get("reset_delay", 2.0))
            + float(config.get("serial_timeout", 1.0))
            + 0.75,
        )
        try:
            with socket.create_connection((str(config["host"]), int(config["port"])), timeout=timeout) as client:
                client.settimeout(timeout)
                token = str(config.get("bridge_auth_token", "")).strip()
                command = f"PROBE {port}"
                payload = f"TOKEN {token} {command}\n" if token else f"{command}\n"
                client.sendall(payload.encode("ascii"))
                return client.recv(16).strip() == b"OK"
        except OSError:
            return False

    def _connection_snapshot(self):
        config = load_config()
        bridge_online = self._ping_bridge()
        devices = {
            str(device.get("port", "")): False
            for device in config.get("devices", [])
            if str(device.get("port", "")).strip()
        }
        if not devices:
            devices[str(config["serial_port"])] = False
        if bridge_online:
            for port in devices:
                devices[port] = self._probe_bridge_device(config, port)
        return {"bridge": bridge_online, "devices": devices}

    def _check_connection(self):
        if self._connection_check_running:
            return
        self._connection_check_running = True

        def run():
            try:
                snapshot = self._connection_snapshot()
                self.result_queue.put(("connection", json.dumps(snapshot)))
            finally:
                self._connection_check_running = False

        threading.Thread(target=run, daemon=True).start()

    def _set_status_view(self):
        status = read_state().get("status", "OFF") or "OFF"
        if status == self.last_status: return
        self.last_status = status
        values = {"RED": ("Sẵn sàng", "Bạn đang nhập nội dung"), "YELLOW": ("Đang xử lý", "AI đang tạo câu trả lời"),
                  "GREEN": ("Hoàn thành", "Tín hiệu thực tế"), "OFF": ("Đã tắt", "Đèn hiện không hoạt động")}
        title, description = values.get(status, values["OFF"]); color = STATUS_COLORS.get(status, OFF)
        self.hero_status.configure(text=f"●  {title}", fg=color); self.hero_description.configure(text=description); self.hero_visual.set_status(status)

    def _events(self):
        try: lines = EVENT_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError: return []
        events = []
        for line in lines[-80:]:
            try:
                value = json.loads(line)
                if isinstance(value, dict): events.append(value)
            except ValueError: pass
        return events

    def _set_events_view(self):
        events = self._events(); signature = (len(events), str(events[-1].get("timestamp", "")) if events else "")
        if signature == self.last_event_signature: return
        self.last_event_signature = signature
        for child in self.event_list.winfo_children(): child.destroy()
        if not events: tk.Label(self.event_list, text="Chưa có sự kiện", fg=MUTED, bg=PANEL).pack(anchor="w", pady=7)
        for event in events[-3:][::-1]:
            row = tk.Frame(self.event_list, bg=PANEL); row.pack(fill="x", pady=3)
            status = str(event.get("status", "OFF")).replace("DONE", "GREEN"); color = STATUS_COLORS.get(status, OFF)
            tk.Label(row, text="●", fg=color, bg=PANEL, font=("Segoe UI", 11, "bold")).pack(side="left")
            provider_key = str(event.get("provider", ""))
            provider = PROVIDER_NAMES.get(provider_key, "Phần mềm" if provider_key == "app" else provider_key or "AI")
            tk.Label(row, text=provider, fg=TEXT, bg=PANEL, font=("Segoe UI", 9, "bold")).pack(side="left", padx=(8, 5))
            action = {
                "GREEN": "hoàn thành",
                "YELLOW": "đang xử lý",
                "RED": "sẵn sàng",
                "OFF": "đã tắt",
            }.get(status, status.lower())
            tk.Label(row, text=action, fg=MUTED, bg=PANEL, font=("Segoe UI", 9)).pack(side="left")
            try: stamp = datetime.fromisoformat(str(event.get("timestamp", "")).replace("Z", "+00:00")).astimezone().strftime("%H:%M")
            except ValueError: stamp = ""
            tk.Label(row, text=stamp, fg=MUTED, bg=PANEL, font=("Segoe UI", 9)).pack(side="right")
        today = datetime.now().date(); count = 0
        for event in events:
            try: count += datetime.fromisoformat(str(event.get("timestamp", "")).replace("Z", "+00:00")).astimezone().date() == today
            except ValueError: pass
        self.metric_events.configure(text=str(count))

    def _poll(self):
        self._set_status_view(); self._set_events_view()
        try:
            while True:
                kind, value = self.result_queue.get_nowait()
                if kind == "connection":
                    snapshot = json.loads(value)
                    bridge_online = bool(snapshot.get("bridge"))
                    devices = snapshot.get("devices", {})
                    physical_online = any(devices.values())
                    if not bridge_online:
                        label, metric, color = "●  Bridge ngoại tuyến", "Offline", RED
                    elif physical_online:
                        online_ports = [port for port, online in devices.items() if online]
                        configured = {str(item.get("port", "")): str(item.get("name", "ESP32")) for item in load_config().get("devices", [])}
                        names = [configured.get(port, port) for port in online_ports]
                        count = len(online_ports)
                        label = f"●  {count} thiết bị: {', '.join(names)}"
                        metric, color = f"{count} thiết bị", GREEN
                    else:
                        label, metric, color = "●  Chưa kết nối thiết bị", "No device", RED
                    self.connection_pill.configure(text=label, fg=color)
                    self.metric_connection.configure(text=metric, fg=color)
                    self.metric_connection.caption_label.configure(
                        text=", ".join(names) if bridge_online and physical_online else (
                            "Bridge đang chạy, chưa thấy ESP32" if bridge_online else "Bridge ngoại tuyến"
                        )
                    )
                    for port, status_label in self.device_status_labels.items():
                        online = bool(devices.get(port, False))
                        status_label.configure(
                            text="●  Đã kết nối" if online else "●  Mất kết nối",
                            fg=GREEN if online else RED,
                        )
                elif kind == "error": self.connection_pill.configure(text="●  Gửi thất bại", fg=RED)
                elif kind == "sent": self._check_connection()
                elif kind == "decor":
                    self.decor_status.configure(text=f"●  {value}", fg=GREEN)
                    self._check_connection()
                elif kind == "decor-error": self.decor_status.configure(text=f"●  Lỗi: {value}", fg=RED)
                elif kind == "integration":
                    result = json.loads(value); provider = result["provider"]; state = result["state"]
                    color = GREEN if state == "active" else YELLOW if state == "needs-setup" else RED
                    self.integration_status_labels[provider].configure(text=f"●  {result['message']}", fg=color)
                    self.integration_buttons[provider].configure(text="Cài lại" if state == "active" else "Thử lại", state="normal")
                elif kind == "update-check":
                    result = json.loads(value)
                    versions = ", ".join(f"{item['port']}: v{item['version']}" for item in result["devices"]) or "Chưa có thiết bị"
                    if result["available"]:
                        self.update_status.configure(text=f"●  Có firmware v{result['latest']}", fg=GREEN)
                        self.update_detail.configure(text=f"{result['name']}\nThiết bị: {versions}")
                        self.update_button.configure(state="normal")
                    else:
                        self.update_status.configure(text="●  Thiết bị đã ở phiên bản mới nhất", fg=GREEN)
                        self.update_detail.configure(text=f"GitHub: v{result['latest']} · {versions}")
                elif kind == "update-progress":
                    self.update_status.configure(text=f"●  Đang cập nhật {value}%", fg=YELLOW)
                elif kind == "update-done":
                    self.update_status.configure(text=f"●  Đã cập nhật firmware v{value}", fg=GREEN)
                    self.update_detail.configure(text="ESP32 đã xác minh SHA-256 và khởi động lại thành công.")
                elif kind == "update-error":
                    self.update_status.configure(text=f"●  Lỗi cập nhật: {value}", fg=RED)
                    self.update_button.configure(state="normal" if self.available_firmware_release else "disabled")
        except queue.Empty: pass
        now = datetime.now().timestamp()
        if not hasattr(self, "_next_ping") or now >= self._next_ping: self._next_ping = now + 3; self._check_connection()
        self.after(500, self._poll)


def main():
    App().mainloop(); return 0


if __name__ == "__main__": raise SystemExit(main())

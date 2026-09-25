"""
LaptopCheckGUI.py
==================
Used-laptop pre-purchase inspection tool with a graphical UI.

Flow:
  1. You enter what the seller advertised (CPU / RAM / storage).
  2. Internal hardware scan (automatic): CPU, RAM, Disk health, Battery health
     -> these carry the highest priority weight in the final score.
  3. Listing Match & Tamper Check: compares actual specs vs what was advertised,
     checks if the OS was freshly reinstalled/reset recently, and pulls the BIOS
     serial number for you to cross-check against the sticker + warranty site.
  4. Keyboard test: full on-screen keyboard, keys light up green as you press them.
  5. Touchpad test: move/click/right-click/scroll on a pad, each gets checked off.
  6. Port test: auto-detects USB devices being plugged in; HDMI/charging/audio
     jack are confirmed manually since software can't detect those reliably.
  7. Quick manual checks: screen condition, body/hinge, lock/ownership status.
  8. Final weighted score (0-100%) + verdict + saved report.

REQUIREMENTS
  - Windows 10/11 (uses PowerShell + powercfg for hardware data)
  - Python 3.8+ with tkinter (included in the standard python.org installer)
  - No internet needed, no pip installs needed

RUN
  python LaptopCheckGUI.py
"""

import os
import re
import sys
import subprocess
import tempfile
import datetime
import platform
import xml.etree.ElementTree as ET
import tkinter as tk
from tkinter import ttk, messagebox

IS_WINDOWS = platform.system() == "Windows"


def resource_path(relative_path):
    """Path to a bundled read-only asset (logo, icon) — works both as a
    plain script and when frozen into an exe by PyInstaller."""
    if getattr(sys, "frozen", False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_path, relative_path)


def app_dir():
    """Writable directory next to the running exe/script — for saving the
    report file. Never points inside PyInstaller's temp extraction dir."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

# ------------------------------------------------------------------
# Hardware query helpers (shell out to PowerShell — no extra pip deps)
# ------------------------------------------------------------------

def run_ps(cmd, timeout=25):
    if not IS_WINDOWS:
        return ""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", cmd],
            capture_output=True, text=True, timeout=timeout
        )
        return result.stdout.strip()
    except Exception:
        return ""


def get_cpu_info():
    name = run_ps("(Get-CimInstance Win32_Processor).Name")
    cores = run_ps("(Get-CimInstance Win32_Processor).NumberOfCores")
    threads = run_ps("(Get-CimInstance Win32_Processor).NumberOfLogicalProcessors")
    name = name.strip() if name else None
    cores = cores.strip() if cores else "?"
    threads = threads.strip() if threads else "?"
    return name, cores, threads


def get_ram_info():
    total = run_ps(
        "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)"
    )
    slots_used = run_ps("(Get-CimInstance Win32_PhysicalMemory).Count")
    total = total.strip() if total else None
    slots_used = slots_used.strip() if slots_used else "?"
    return total, slots_used


def get_disk_info():
    """Returns list of dicts: name, media_type, health."""
    out = run_ps(
        "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,HealthStatus "
        "| ConvertTo-Csv -NoTypeInformation"
    )
    disks = []
    lines = [l for l in out.splitlines() if l.strip()]
    if len(lines) > 1:
        for line in lines[1:]:
            parts = [p.strip('"') for p in line.split(",")]
            if len(parts) >= 3:
                disks.append({"name": parts[0], "media": parts[1], "health": parts[2]})
    return disks


def get_battery_health():
    """Returns (design_mWh, full_mWh, pct) or (None, None, None)."""
    tmp = os.path.join(tempfile.gettempdir(), "battery-report.xml")
    try:
        subprocess.run(
            ["powercfg", "/batteryreport", "/xml", "/output", tmp, "/duration", "1"],
            capture_output=True, timeout=20
        )
    except Exception:
        return None, None, None
    if not os.path.exists(tmp):
        return None, None, None
    try:
        tree = ET.parse(tmp)
        root = tree.getroot()
        design = full = None
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag == "DesignCapacity" and design is None:
                design = int(elem.text)
            if tag == "FullChargeCapacity" and full is None:
                full = int(elem.text)
        if design and full and design > 0:
            return design, full, round(full / design * 100, 1)
    except Exception:
        pass
    return None, None, None


def get_usb_device_count():
    out = run_ps(
        "(Get-PnpDevice -PresentOnly | Where-Object {$_.InstanceId -like 'USB*'}).Count"
    )
    try:
        return int(out.strip())
    except Exception:
        return None


def get_disk_total_gb():
    out = run_ps(
        "[math]::Round((Get-PhysicalDisk | Measure-Object -Property Size -Sum).Sum/1GB,0)"
    )
    try:
        return float(out.strip())
    except Exception:
        return None


def get_os_install_date():
    """Returns a datetime.date or None."""
    out = run_ps("(Get-CimInstance Win32_OperatingSystem).InstallDate.ToString('yyyy-MM-dd')")
    out = out.strip()
    try:
        return datetime.datetime.strptime(out, "%Y-%m-%d").date()
    except Exception:
        return None


def get_bios_info():
    """Returns (serial, manufacturer, model)."""
    serial = run_ps("(Get-CimInstance Win32_BIOS).SerialNumber") or "Unknown"
    maker = run_ps("(Get-CimInstance Win32_ComputerSystem).Manufacturer") or "Unknown"
    model = run_ps("(Get-CimInstance Win32_ComputerSystem).Model") or "Unknown"
    return serial.strip(), maker.strip(), model.strip()


# ------------------------------------------------------------------
# Scoring weights — internals (CPU/RAM/Disk/Battery) carry more
# priority than the interactive/manual checks.
# ------------------------------------------------------------------
WEIGHTS = {
    "Disk health":            18,
    "CPU":                    12,
    "RAM":                    12,
    "Battery health":         15,
    "Listing Match / Tamper": 12,
    "Keyboard":                8,
    "Touchpad":                6,
    "Ports":                   6,
    "Screen":                  5,
    "Body / Hinge":            3,
    "Not locked/stolen":       3,
}

KEY_ROWS = [
    [("Esc", "Escape"), ("F1", "F1"), ("F2", "F2"), ("F3", "F3"), ("F4", "F4"),
     ("F5", "F5"), ("F6", "F6"), ("F7", "F7"), ("F8", "F8"), ("F9", "F9"),
     ("F10", "F10"), ("F11", "F11"), ("F12", "F12")],
    [("`", "quoteleft"), ("1", "1"), ("2", "2"), ("3", "3"), ("4", "4"), ("5", "5"),
     ("6", "6"), ("7", "7"), ("8", "8"), ("9", "9"), ("0", "0"), ("-", "minus"),
     ("=", "equal"), ("Backspace", "BackSpace")],
    [("Tab", "Tab"), ("Q", "q"), ("W", "w"), ("E", "e"), ("R", "r"), ("T", "t"),
     ("Y", "y"), ("U", "u"), ("I", "i"), ("O", "o"), ("P", "p"),
     ("[", "bracketleft"), ("]", "bracketright"), ("\\", "backslash")],
    [("Caps", "Caps_Lock"), ("A", "a"), ("S", "s"), ("D", "d"), ("F", "f"),
     ("G", "g"), ("H", "h"), ("J", "j"), ("K", "k"), ("L", "l"),
     (";", "semicolon"), ("'", "apostrophe"), ("Enter", "Return")],
    [("Shift", "Shift_L"), ("Z", "z"), ("X", "x"), ("C", "c"), ("V", "v"),
     ("B", "b"), ("N", "n"), ("M", "m"), (",", "comma"), (".", "period"),
     ("/", "slash"), ("Shift", "Shift_R")],
    [("Ctrl", "Control_L"), ("Win", "Super_L"), ("Alt", "Alt_L"), ("Space", "space"),
     ("Alt", "Alt_R"), ("Ctrl", "Control_R")],
    [("Left", "Left"), ("Up", "Up"), ("Down", "Down"), ("Right", "Right")],
]

TOTAL_KEYS = sum(len(row) for row in KEY_ROWS)


APP_VERSION = "1.0"
AUTHOR_NAME = "Tasin Saimon"
AUTHOR_GITHUB = "github.com/tasin023"
AUTHOR_LINKEDIN = "linkedin.com/in/tasin-saimon-959188249"
AUTHOR_TELEGRAM = "t.me/TasinSaimon"

STEP_PAGES = [
    ("Listing", "show_listing_input"),
    ("Scan", "show_scan"),
    ("Tamper", "show_tamper_check"),
    ("Keyboard", "show_keyboard"),
    ("Touchpad", "show_touchpad"),
    ("Ports", "show_ports"),
    ("Manual", "show_manual_checks"),
    ("Results", "show_results"),
]

BG = "#0a0a14"
CARD = "#12122a"
ACCENT = "#67e8f9"
ACCENT_DIM = "#22d3ee"
ACCENT2 = "#cba6f7"
OK = "#a6e3a1"
WARN = "#f9e2af"
BAD = "#f38ba8"
MUTED = "#8b8fc7"


class LaptopCheckApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("LaptopCheck — by Tasin Saimon")
        self.geometry("860x640")
        self.minsize(780, 580)
        self.configure(bg=BG)

        icon_path = resource_path("logo.ico")
        if IS_WINDOWS and os.path.exists(icon_path):
            try:
                self.iconbitmap(icon_path)
            except Exception:
                pass

        self.logo_img = None
        logo_path = resource_path("logo_512.png")
        if os.path.exists(logo_path):
            try:
                from PIL import Image, ImageTk
                im = Image.open(logo_path).resize((72, 72))
                self.logo_img = ImageTk.PhotoImage(im)
            except Exception:
                self.logo_img = None

        self.scores = {}   # name -> (score 0-100, note)
        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)

        self.show_welcome()

    # ---------- helpers ----------
    def clear(self):
        for w in self.container.winfo_children():
            w.destroy()

    def top_bar(self, active_key=None):
        """Small persistent strip: logo + title on the left, About on the right."""
        bar = tk.Frame(self.container, bg=BG)
        bar.pack(fill="x", pady=(10, 0))
        left = tk.Frame(bar, bg=BG)
        left.pack(side="left", padx=20)
        if self.logo_img:
            tk.Label(left, image=self.logo_img, bg=BG).pack(side="left", padx=(0, 8))
        tk.Label(left, text="LAPTOPCHECK", font=("Consolas", 12, "bold"),
                  fg=ACCENT, bg=BG).pack(side="left")
        tk.Button(bar, text="About", command=self.show_about, font=("Segoe UI", 9),
                   bg=BG, fg=MUTED, relief="flat", cursor="hand2",
                   activebackground=BG, activeforeground=ACCENT,
                   borderwidth=0).pack(side="right", padx=20)

        if active_key:
            dots = tk.Frame(self.container, bg=BG)
            dots.pack(pady=(8, 0))
            keys = [k for k, _ in STEP_PAGES]
            for k in keys:
                is_active = (k == active_key)
                is_past = keys.index(k) < keys.index(active_key)
                color = ACCENT if is_active else (ACCENT2 if is_past else "#2a2a4a")
                tk.Label(dots, text="●", fg=color, bg=BG,
                          font=("Segoe UI", 8)).pack(side="left", padx=3)

    def header(self, title, subtitle="", step_key=None):
        self.top_bar(active_key=step_key)
        tk.Label(self.container, text=title, font=("Segoe UI", 21, "bold"),
                  fg="white", bg=BG).pack(pady=(18, 4))
        if subtitle:
            tk.Label(self.container, text=subtitle, font=("Segoe UI", 11),
                      fg=MUTED, bg=BG).pack(pady=(0, 12))

    def nav_button(self, text, command, side="right"):
        b = tk.Button(self.container, text=text, command=command,
                       font=("Segoe UI", 11, "bold"), bg=ACCENT, fg="#0a0a14",
                       activebackground=ACCENT_DIM, activeforeground="#0a0a14",
                       relief="flat", padx=18, pady=8, cursor="hand2", borderwidth=0)
        b.pack(side=side, padx=25, pady=15, anchor="s")

        def on_enter(e): b.config(bg=ACCENT_DIM)
        def on_leave(e): b.config(bg=ACCENT)
        b.bind("<Enter>", on_enter)
        b.bind("<Leave>", on_leave)
        return b

    def record(self, name, score, note):
        self.scores[name] = (round(score, 1), note)

    # ---------- About ----------
    def show_about(self):
        self.clear()
        self.header("About LaptopCheck")

        if self.logo_img:
            tk.Label(self.container, image=self.logo_img, bg=BG).pack(pady=10)

        card = tk.Frame(self.container, bg=CARD, highlightbackground=ACCENT,
                          highlightthickness=1)
        card.pack(padx=60, pady=10, fill="x")

        def row(txt, color="white", font=("Segoe UI", 11)):
            tk.Label(card, text=txt, fg=color, bg=CARD, font=font,
                       justify="center").pack(pady=6)

        row(f"LaptopCheck  v{APP_VERSION}", ACCENT, ("Segoe UI", 14, "bold"))
        row("A used-laptop pre-purchase inspection tool.")
        row(f"Made by {AUTHOR_NAME}", ACCENT2, ("Segoe UI", 12, "bold"))
        row(f"GitHub:   {AUTHOR_GITHUB}", MUTED, ("Consolas", 10))
        row(f"LinkedIn: {AUTHOR_LINKEDIN}", MUTED, ("Consolas", 10))
        row(f"Telegram: {AUTHOR_TELEGRAM}", MUTED, ("Consolas", 10))

        tk.Button(self.container, text="<- Back", command=self.show_welcome,
                   font=("Segoe UI", 10, "bold"), bg=ACCENT, fg="#0a0a14",
                   relief="flat", padx=16, pady=8, cursor="hand2").pack(pady=20)

    # ---------- 0. Welcome ----------
    def show_welcome(self):
        self.clear()
        self.top_bar()
        if self.logo_img:
            tk.Label(self.container, image=self.logo_img, bg=BG).pack(pady=(15, 5))
        tk.Label(self.container, text="LAPTOPCHECK", font=("Consolas", 26, "bold"),
                  fg="white", bg=BG).pack(pady=(0, 2))
        tk.Label(self.container, text="Used-Laptop Pre-Purchase Inspector",
                  font=("Segoe UI", 12), fg=ACCENT2, bg=BG).pack(pady=(0, 15))
        if not IS_WINDOWS:
            tk.Label(self.container,
                      text="Warning: not running on Windows — hardware auto-checks will be skipped.",
                      fg=BAD, bg=BG, font=("Segoe UI", 10)).pack(pady=10)
        tk.Label(self.container,
                  text="Priority: Disk 18%  |  Battery 15%  |  CPU 12%  |  RAM 12%  |  Listing/Tamper 12%\n"
                       "Keyboard 8%  |  Touchpad 6%  |  Ports 6%  |  Screen 5%  |  Body 3%  |  Lock 3%",
                  fg=MUTED, bg=BG, font=("Segoe UI", 10), justify="center").pack(pady=10)
        self.nav_button("Start  ->", self.show_listing_input)

    # ---------- 0.5 What did the seller advertise? ----------
    def show_listing_input(self):
        self.clear()
        self.header("What did the seller advertise?",
                      "So we can flag it if the actual hardware doesn't match the listing.", step_key="Listing")

        form = tk.Frame(self.container, bg="#0a0a14")
        form.pack(pady=20)

        self.adv_cpu_var = tk.StringVar()
        self.adv_ram_var = tk.StringVar()
        self.adv_storage_var = tk.StringVar()

        rows = [
            ("Advertised CPU (e.g. 'i5-8250U')", self.adv_cpu_var),
            ("Advertised RAM in GB (e.g. 8)", self.adv_ram_var),
            ("Advertised Storage in GB (e.g. 256)", self.adv_storage_var),
        ]
        for label_txt, var in rows:
            row = tk.Frame(form, bg="#0a0a14")
            row.pack(fill="x", pady=8)
            tk.Label(row, text=label_txt, fg="white", bg="#0a0a14",
                       font=("Segoe UI", 11), width=32, anchor="w").pack(side="left")
            tk.Entry(row, textvariable=var, font=("Segoe UI", 11), width=20).pack(side="left")

        tk.Label(self.container, text="Leave any field blank to skip that comparison.",
                  fg="#8b8fc7", bg="#0a0a14", font=("Segoe UI", 9)).pack()

        self.nav_button("Start Scan  ->", self.show_scan)

    # ---------- 1. Internal automated scan ----------
    def show_scan(self):
        self.clear()
        self.header("Step 1 / 6 — Internal Hardware Scan", "Reading CPU, RAM, disk and battery...", step_key="Scan")

        if not IS_WINDOWS:
            tk.Label(self.container,
                      text="This isn't Windows — hardware auto-detection needs PowerShell/WMI,\n"
                           "so these will show as unreadable. Run this on the actual laptop (Windows) for real results.",
                      fg="#f38ba8", bg="#0a0a14", font=("Segoe UI", 10, "bold"),
                      justify="center").pack(pady=(0, 5))

        status = tk.Label(self.container, text="Scanning...", fg="#f9e2af",
                            bg="#0a0a14", font=("Segoe UI", 11))
        status.pack(pady=10)
        self.update()

        box = tk.Frame(self.container, bg="#12122a")
        box.pack(fill="both", expand=True, padx=30, pady=10)

        def line(txt, color="white"):
            tk.Label(box, text=txt, fg=color, bg="#12122a", font=("Consolas", 10),
                       anchor="w", justify="left").pack(fill="x", padx=15, pady=2)

        # CPU — unreadable is scored neutral (50), never treated as a defect
        cpu_name, cores, threads = get_cpu_info()
        if cpu_name:
            line(f"CPU: {cpu_name}  ({cores}c / {threads}t)")
            self.record("CPU", 100, cpu_name)
        else:
            line("CPU: could not be read on this system", "#f9e2af")
            self.record("CPU", 50, "Unreadable — verify manually (Task Manager > Performance)")
        self.actual_cpu = cpu_name or ""

        # RAM
        ram_gb, slots = get_ram_info()
        if ram_gb:
            line(f"RAM: {ram_gb} GB across {slots} module(s)")
            try:
                ram_val = float(ram_gb)
                ram_score = 100 if ram_val >= 8 else (70 if ram_val >= 4 else 30)
            except Exception:
                ram_score = 50
                ram_val = None
            self.record("RAM", ram_score, f"{ram_gb} GB")
            self.actual_ram = ram_val
        else:
            line("RAM: could not be read on this system", "#f9e2af")
            self.record("RAM", 50, "Unreadable — verify manually (Task Manager > Performance)")
            self.actual_ram = None

        # Disk
        disks = get_disk_info()
        if disks:
            total_score = 0
            for d in disks:
                h = d["health"]
                s = 100 if h == "Healthy" else (50 if h == "Warning" else 0)
                total_score += s
                line(f"Disk: {d['name']}  ({d['media']})  -  Health: {h}",
                      "#a6e3a1" if s == 100 else ("#f9e2af" if s == 50 else "#f38ba8"))
            self.record("Disk health", total_score / len(disks),
                          ", ".join(f"{d['name']}: {d['health']}" for d in disks))
        else:
            line("Disk: could not read health status automatically", "#f9e2af")
            self.record("Disk health", 50, "Unreadable — verify manually with CrystalDiskInfo")
        self.actual_storage = get_disk_total_gb()
        if self.actual_storage:
            line(f"Total storage capacity: ~{self.actual_storage} GB")

        # Battery
        design, full, pct = get_battery_health()
        if pct is not None:
            color = "#a6e3a1" if pct >= 80 else ("#f9e2af" if pct >= 60 else "#f38ba8")
            line(f"Battery: {full} / {design} mWh  =  {pct}% of original capacity", color)
            self.record("Battery health", pct, f"{pct}% of design capacity")
        else:
            line("Battery: no report available (desktop, or driver issue)", "#f9e2af")
            self.record("Battery health", 50, "Unreadable — verify manually")

        status.config(text="Scan complete.", fg="#a6e3a1")
        self.nav_button("Next: Listing Match & Tamper Check  ->", self.show_tamper_check)

    # ---------- 2. Listing match & tamper check ----------
    def show_tamper_check(self):
        self.clear()
        self.header("Step 2 / 6 — Listing Match & Tamper Check",
                      "Comparing actual hardware to the listing, and checking for recent OS resets.", step_key="Tamper")

        box = tk.Frame(self.container, bg="#12122a")
        box.pack(fill="both", expand=True, padx=30, pady=10)

        def line(txt, color="white"):
            tk.Label(box, text=txt, fg=color, bg="#12122a", font=("Consolas", 10),
                       anchor="w", justify="left", wraplength=720).pack(fill="x", padx=15, pady=3)

        sub_scores = []  # list of (weight_within_category, score)

        # --- CPU match ---
        adv_cpu = self.adv_cpu_var.get().strip()
        if adv_cpu:
            match = adv_cpu.lower().replace(" ", "") in self.actual_cpu.lower().replace(" ", "")
            score = 100 if match else 0
            line(f"CPU — advertised: '{adv_cpu}'  |  actual: '{self.actual_cpu}'  ->  "
                  f"{'MATCH' if match else 'MISMATCH'}",
                  "#a6e3a1" if match else "#f38ba8")
            sub_scores.append((25, score))
        else:
            line("CPU — no advertised value given, skipped.", "#8b8fc7")

        # --- RAM match ---
        adv_ram_raw = self.adv_ram_var.get().strip()
        if adv_ram_raw and self.actual_ram is not None:
            try:
                adv_ram = float(adv_ram_raw)
                diff = abs(adv_ram - self.actual_ram)
                match = diff <= max(1, adv_ram * 0.15)
                score = 100 if match else 0
                line(f"RAM — advertised: {adv_ram} GB  |  actual: {self.actual_ram} GB  ->  "
                      f"{'MATCH' if match else 'MISMATCH'}",
                      "#a6e3a1" if match else "#f38ba8")
                sub_scores.append((25, score))
            except Exception:
                line("RAM — advertised value not understood, skipped.", "#8b8fc7")
        else:
            line("RAM — no advertised value given, skipped.", "#8b8fc7")

        # --- Storage match ---
        adv_storage_raw = self.adv_storage_var.get().strip()
        if adv_storage_raw and self.actual_storage:
            try:
                adv_storage = float(adv_storage_raw)
                diff_pct = abs(adv_storage - self.actual_storage) / adv_storage
                match = diff_pct <= 0.15
                score = 100 if match else 0
                line(f"Storage — advertised: {adv_storage} GB  |  actual: ~{self.actual_storage} GB  ->  "
                      f"{'MATCH' if match else 'MISMATCH'}",
                      "#a6e3a1" if match else "#f38ba8")
                sub_scores.append((20, score))
            except Exception:
                line("Storage — advertised value not understood, skipped.", "#8b8fc7")
        else:
            line("Storage — no advertised value given, skipped.", "#8b8fc7")

        # --- OS install / reset recency ---
        install_date = get_os_install_date()
        if install_date:
            days_ago = (datetime.date.today() - install_date).days
            if days_ago <= 7:
                line(f"OS installed: {install_date}  ({days_ago} days ago)  —  VERY RECENT. "
                      "Could be an innocent clean wipe, or a reset done right before selling "
                      "to hide problems. Ask the seller why.", "#f9e2af")
                recency_score = 30
            elif days_ago <= 30:
                line(f"OS installed: {install_date}  ({days_ago} days ago)  —  recent, worth asking about.",
                      "#f9e2af")
                recency_score = 60
            else:
                line(f"OS installed: {install_date}  ({days_ago} days ago)  —  not unusually recent.",
                      "#a6e3a1")
                recency_score = 100
        else:
            line("OS install date: could not be read.", "#8b8fc7")
            recency_score = 70
        sub_scores.append((15, recency_score))

        # --- BIOS serial / ownership ---
        serial, maker, model = get_bios_info()
        line(f"BIOS Serial Number: {serial}   |   {maker} {model}")
        line("Cross-check this serial against the sticker on the bottom of the laptop, "
              "and look it up on the manufacturer's support site to verify warranty status "
              "and that it isn't reported stolen (where that service exists).", "#8b8fc7")

        serial_frame = tk.Frame(self.container, bg="#0a0a14")
        serial_frame.pack(pady=10)
        self.serial_match_var = tk.BooleanVar()
        tk.Checkbutton(serial_frame,
                         text="Serial number matches the physical sticker on the laptop",
                         variable=self.serial_match_var, fg="white", bg="#0a0a14",
                         selectcolor="#1e1e40", font=("Segoe UI", 10)).pack()

        def go_next():
            serial_score = 100 if self.serial_match_var.get() else 0
            sub_scores.append((15, serial_score))
            total_w = sum(w for w, s in sub_scores)
            category_score = sum(w * s for w, s in sub_scores) / total_w if total_w else 70
            self.record("Listing Match / Tamper", category_score,
                          f"{len(sub_scores)} check(s) run; see step details")
            self.show_keyboard()

        self.nav_button("Next: Keyboard Test  ->", go_next)

    # ---------- 2. Keyboard test ----------
    def show_keyboard(self):
        self.clear()
        self.header("Step 3 / 6 — Keyboard Test",
                      "Press every key on the physical keyboard. It turns green here when detected.", step_key="Keyboard")

        self.tested_keys = set()
        self.key_rects = {}

        canvas = tk.Canvas(self.container, bg="#12122a", highlightthickness=0)
        canvas.pack(fill="both", expand=True, padx=20, pady=5)

        progress = tk.Label(self.container, text=f"Tested: 0 / {TOTAL_KEYS}",
                              fg="#f9e2af", bg="#0a0a14", font=("Segoe UI", 11, "bold"))
        progress.pack()

        key_w, key_h, gap = 46, 40, 4
        y = 15
        for row in KEY_ROWS:
            x = 15
            for label, keysym in row:
                w = key_w
                if label in ("Backspace", "Tab", "Caps", "Enter"):
                    w = key_w * 1.6
                elif label == "Shift":
                    w = key_w * 2.0
                elif label == "Space":
                    w = key_w * 5
                rect = canvas.create_rectangle(x, y, x + w, y + key_h,
                                                 fill="#1e1e40", outline="#3d3d6e")
                canvas.create_text(x + w / 2, y + key_h / 2, text=label,
                                     fill="white", font=("Segoe UI", 8))
                self.key_rects[keysym.lower()] = rect
                x += w + gap
            y += key_h + gap

        def on_key(event):
            ks = event.keysym.lower()
            if ks in self.key_rects and ks not in self.tested_keys:
                self.tested_keys.add(ks)
                canvas.itemconfig(self.key_rects[ks], fill="#a6e3a1")
                progress.config(text=f"Tested: {len(self.tested_keys)} / {TOTAL_KEYS}")

        self.bind("<KeyPress>", on_key)
        canvas.focus_set()

        def go_next():
            self.unbind("<KeyPress>")
            score = len(self.tested_keys) / TOTAL_KEYS * 100
            self.record("Keyboard", score,
                          f"{len(self.tested_keys)}/{TOTAL_KEYS} keys responded")
            self.show_touchpad()

        self.nav_button("Next: Touchpad Test  ->", go_next)
        tk.Label(self.container,
                  text="(Click on the keyboard area first so key presses are captured, then type away.)",
                  fg="#8b8fc7", bg="#0a0a14", font=("Segoe UI", 9)).pack(pady=(0, 5))

    # ---------- 3. Touchpad test ----------
    def show_touchpad(self):
        self.clear()
        self.header("Step 4 / 6 — Touchpad Test",
                      "Move, left-click, right-click and scroll inside the box below.", step_key="Touchpad")

        self.tp_moved = False
        self.tp_left = False
        self.tp_right = False
        self.tp_scroll = False
        self.tp_move_points = 0

        status_frame = tk.Frame(self.container, bg="#0a0a14")
        status_frame.pack(pady=5)
        self.tp_labels = {}
        for key, txt in [("move", "Movement"), ("left", "Left Click"),
                           ("right", "Right Click"), ("scroll", "Scroll")]:
            lbl = tk.Label(status_frame, text=f"○ {txt}", fg="#f38ba8", bg="#0a0a14",
                             font=("Segoe UI", 11, "bold"), padx=15)
            lbl.pack(side="left")
            self.tp_labels[key] = lbl

        pad = tk.Canvas(self.container, bg="#12122a", highlightthickness=2,
                          highlightbackground="#3d3d6e")
        pad.pack(fill="both", expand=True, padx=40, pady=20)

        def mark(key, txt):
            self.tp_labels[key].config(text=f"\u2713 {txt}", fg="#a6e3a1")

        def on_motion(event):
            pad.create_oval(event.x - 2, event.y - 2, event.x + 2, event.y + 2,
                              fill="#67e8f9", outline="")
            self.tp_move_points += 1
            if not self.tp_moved and self.tp_move_points > 15:
                self.tp_moved = True
                mark("move", "Movement")

        def on_left(event):
            self.tp_left = True
            mark("left", "Left Click")
            pad.create_text(event.x, event.y, text="L", fill="#a6e3a1", font=("Segoe UI", 14, "bold"))

        def on_right(event):
            self.tp_right = True
            mark("right", "Right Click")
            pad.create_text(event.x, event.y, text="R", fill="#f9e2af", font=("Segoe UI", 14, "bold"))

        def on_scroll(event):
            self.tp_scroll = True
            mark("scroll", "Scroll")

        pad.bind("<Motion>", on_motion)
        pad.bind("<Button-1>", on_left)
        pad.bind("<Button-3>", on_right)
        pad.bind("<MouseWheel>", on_scroll)      # Windows
        pad.bind("<Button-4>", on_scroll)        # Linux scroll up
        pad.bind("<Button-5>", on_scroll)        # Linux scroll down

        def go_next():
            checks = [self.tp_moved, self.tp_left, self.tp_right, self.tp_scroll]
            if not any(checks):
                messagebox.showwarning(
                    "No interaction detected",
                    "You haven't moved the mouse, clicked, or scrolled inside the test box yet.\n\n"
                    "Try each of the four actions inside the box first, or click Next again to "
                    "record it as untested (scored neutral, not as a failure)."
                )
                self.tp_confirmed_skip = getattr(self, "tp_confirmed_skip", False)
                if not self.tp_confirmed_skip:
                    self.tp_confirmed_skip = True
                    return
                self.record("Touchpad", 50, "Skipped — not tested")
                self.show_ports()
                return
            score = sum(checks) / len(checks) * 100
            note = ", ".join(n for ok, n in zip(checks, ["move", "left-click", "right-click", "scroll"]) if ok)
            missing = [n for ok, n in zip(checks, ["move", "left-click", "right-click", "scroll"]) if not ok]
            if missing:
                note += f" — not tested: {', '.join(missing)}"
            self.record("Touchpad", score, note)
            self.show_ports()

        self.nav_button("Next: Port Test  ->", go_next)

    # ---------- 4. Port test ----------
    def show_ports(self):
        self.clear()
        self.header("Step 5 / 6 — Port Test",
                      "USB ports are auto-detected. HDMI / charging / audio jack are confirmed manually.", step_key="Ports")

        self.usb_baseline = get_usb_device_count()
        self.usb_working = 0
        self.usb_tested = 0

        top = tk.Frame(self.container, bg="#0a0a14")
        top.pack(pady=5)
        tk.Label(top, text="How many USB ports does this laptop have?",
                  fg="white", bg="#0a0a14", font=("Segoe UI", 10)).pack(side="left", padx=5)
        port_count_var = tk.StringVar(value="2")
        tk.Entry(top, textvariable=port_count_var, width=4).pack(side="left")

        result_lbl = tk.Label(self.container, text="", fg="#f9e2af", bg="#0a0a14",
                                font=("Segoe UI", 10))
        result_lbl.pack(pady=5)

        usb_auto_available = self.usb_baseline is not None
        self.usb_manual_var = tk.IntVar(value=0)

        usb_status = tk.Label(self.container,
                                text=f"Baseline USB device count: {self.usb_baseline}"
                                     if usb_auto_available else
                                     "USB auto-detection isn't available on this system — "
                                     "use the manual count below instead.",
                                fg="#8b8fc7" if usb_auto_available else "#f9e2af",
                                bg="#0a0a14", font=("Segoe UI", 9))
        usb_status.pack()

        def check_port():
            try:
                n = int(port_count_var.get())
            except Exception:
                n = 1
            if self.usb_tested >= n:
                result_lbl.config(text="All requested ports tested. Click Next when ready.",
                                    fg="#a6e3a1")
                return
            new_count = get_usb_device_count()
            if self.usb_baseline is not None and new_count is not None and new_count > self.usb_baseline:
                self.usb_working += 1
                self.usb_baseline = new_count
                result_lbl.config(text=f"Port {self.usb_tested + 1}: device detected - OK",
                                    fg="#a6e3a1")
            else:
                result_lbl.config(
                    text=f"Port {self.usb_tested + 1}: no new device detected — plug something in, then click again",
                    fg="#f38ba8")
                return
            self.usb_tested += 1

        if usb_auto_available:
            tk.Button(self.container, text="Plug device in, then click here to check port",
                       command=check_port, font=("Segoe UI", 10, "bold"), bg="#67e8f9",
                       fg="#0a0a14", relief="flat", padx=10, pady=6, cursor="hand2").pack(pady=10)
        else:
            manual_usb = tk.Frame(self.container, bg="#0a0a14")
            manual_usb.pack(pady=10)
            tk.Label(manual_usb, text="How many ports did you manually confirm work (device detected)?",
                       fg="white", bg="#0a0a14", font=("Segoe UI", 10)).pack(side="left", padx=5)
            tk.Spinbox(manual_usb, from_=0, to=20, textvariable=self.usb_manual_var,
                        width=4).pack(side="left")

        # Manual jacks
        manual_frame = tk.Frame(self.container, bg="#12122a")
        manual_frame.pack(fill="x", padx=30, pady=15)
        self.hdmi_var = tk.BooleanVar()
        self.charge_var = tk.BooleanVar()
        self.audio_var = tk.BooleanVar()
        for var, txt in [(self.hdmi_var, "HDMI output shows picture on an external monitor"),
                           (self.charge_var, "Charging port: battery % rises when plugged in"),
                           (self.audio_var, "Headphone jack: audio routes correctly when plugged in")]:
            tk.Checkbutton(manual_frame, text=txt, variable=var, fg="white", bg="#12122a",
                             selectcolor="#1e1e40", font=("Segoe UI", 10), anchor="w",
                             justify="left").pack(fill="x", padx=10, pady=4)

        def go_next():
            try:
                n = max(int(port_count_var.get()), 1)
            except Exception:
                n = 1
            if usb_auto_available:
                working = self.usb_working
                usb_note = f"USB {working}/{n} working (auto-detected)"
            else:
                working = min(max(self.usb_manual_var.get(), 0), n)
                usb_note = f"USB {working}/{n} working (manually confirmed)"
            usb_score = (working / n) * 100 if n else 0
            manual_checks = [self.hdmi_var.get(), self.charge_var.get(), self.audio_var.get()]
            manual_score = sum(manual_checks) / len(manual_checks) * 100
            score = usb_score * 0.6 + manual_score * 0.4
            self.record("Ports", score,
                          f"{usb_note}, {sum(manual_checks)}/3 other ports confirmed")
            self.show_manual_checks()

        self.nav_button("Next: Quick Manual Checks  ->", go_next)

    # ---------- 5. Quick manual checks (screen / body / lock) ----------
    def show_manual_checks(self):
        self.clear()
        self.header("Step 6 / 6 — Quick Manual Checks",
                      "A few things software can't verify for you.", step_key="Manual")

        self.screen_var = tk.BooleanVar()
        self.body_var = tk.BooleanVar()
        self.lock_var = tk.BooleanVar()

        frame = tk.Frame(self.container, bg="#12122a")
        frame.pack(fill="x", padx=30, pady=20)
        items = [
            (self.screen_var, "Screen: no dead pixels, discoloration or pressure marks "
                               "(check on a full white and full black image)"),
            (self.body_var, "Body/Hinge: no cracks, dents, warping; hinge feels solid"),
            (self.lock_var, "Not locked: no BIOS password, no Microsoft/Apple account lock, "
                             "seller has proof of ownership"),
        ]
        for var, txt in items:
            tk.Checkbutton(frame, text=txt, variable=var, fg="white", bg="#12122a",
                             selectcolor="#1e1e40", font=("Segoe UI", 11), anchor="w",
                             justify="left", wraplength=680).pack(fill="x", padx=15, pady=10)

        def go_next():
            self.record("Screen", 100 if self.screen_var.get() else 0,
                          "OK" if self.screen_var.get() else "Defect reported")
            self.record("Body / Hinge", 100 if self.body_var.get() else 0,
                          "OK" if self.body_var.get() else "Damage reported")
            self.record("Not locked/stolen", 100 if self.lock_var.get() else 0,
                          "OK" if self.lock_var.get() else "CRITICAL: locked or unverified")
            self.show_results()

        self.nav_button("See Final Score  ->", go_next)

    # ---------- 6. Results ----------
    def show_results(self):
        self.clear()
        self.header("Results", step_key="Results")

        canvas_frame = tk.Frame(self.container, bg="#0a0a14")
        canvas_frame.pack(fill="both", expand=True, padx=30)

        total_weight = sum(WEIGHTS.values())
        weighted_sum = 0
        for name, weight in WEIGHTS.items():
            score, note = self.scores.get(name, (50, "Not tested"))
            weighted_sum += score * weight

            row = tk.Frame(canvas_frame, bg="#0a0a14")
            row.pack(fill="x", pady=4)
            tk.Label(row, text=f"{name} ({weight}%)", fg="white", bg="#0a0a14",
                       font=("Segoe UI", 10, "bold"), width=22, anchor="w").pack(side="left")

            bar_bg = tk.Canvas(row, width=300, height=16, bg="#1e1e40", highlightthickness=0)
            bar_bg.pack(side="left", padx=8)
            color = "#a6e3a1" if score >= 70 else ("#f9e2af" if score >= 40 else "#f38ba8")
            bar_bg.create_rectangle(0, 0, 300 * score / 100, 16, fill=color, outline="")

            tk.Label(row, text=f"{score}%", fg="white", bg="#0a0a14",
                       font=("Segoe UI", 10)).pack(side="left", padx=6)
            tk.Label(row, text=note, fg="#8b8fc7", bg="#0a0a14",
                       font=("Segoe UI", 9), anchor="w", wraplength=250).pack(side="left")

        final_score = round(weighted_sum / total_weight, 1)
        if final_score >= 85:
            verdict, vcolor = "EXCELLENT - buy with confidence", "#a6e3a1"
        elif final_score >= 70:
            verdict, vcolor = "GOOD - solid buy for a used unit", "#a6e3a1"
        elif final_score >= 50:
            verdict, vcolor = "FAIR - negotiate the price or fix issues first", "#f9e2af"
        elif final_score >= 30:
            verdict, vcolor = "POOR - only worth it if very cheap", "#f38ba8"
        else:
            verdict, vcolor = "AVOID - too many problems", "#f38ba8"

        tk.Label(self.container, text=f"OVERALL SCORE: {final_score}%",
                  fg="white", bg="#0a0a14", font=("Segoe UI", 22, "bold")).pack(pady=(15, 2))
        tk.Label(self.container, text=verdict, fg=vcolor, bg="#0a0a14",
                  font=("Segoe UI", 13, "bold")).pack(pady=(0, 5))

        lock_score, _ = self.scores.get("Not locked/stolen", (100, ""))
        if lock_score < 50:
            tk.Label(self.container,
                      text="WARNING: locked/unverified ownership can make this laptop unusable.\n"
                           "Don't buy unless resolved, regardless of the score above.",
                      fg="#f38ba8", bg="#0a0a14", font=("Segoe UI", 10, "bold"),
                      justify="center").pack(pady=5)

        def save_report():
            path = os.path.join(app_dir(), "LaptopCheck_Report.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write("USED LAPTOP HEALTH CHECK REPORT\n")
                f.write(f"Generated: {datetime.datetime.now()}\n\n")
                for name, weight in WEIGHTS.items():
                    score, note = self.scores.get(name, (50, "Not tested"))
                    f.write(f"{name:<22} {score:>5}%   ({note})\n")
                f.write(f"\nOVERALL SCORE: {final_score}%\n")
                f.write(f"VERDICT: {verdict}\n")
            messagebox.showinfo("Saved", f"Report saved to:\n{path}")

        tk.Button(self.container, text="Save Report to File", command=save_report,
                   font=("Segoe UI", 10, "bold"), bg="#67e8f9", fg="#0a0a14",
                   relief="flat", padx=14, pady=8, cursor="hand2").pack(pady=10)


if __name__ == "__main__":
    app = LaptopCheckApp()
    app.mainloop()

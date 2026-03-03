# Wi-Fi TCP - [Enabled]
# 4Ch Relay - [Enabled]
# Auto Updating - [Enabled]
# Data Logging - [Enabled] {Licence Required}
# User Changable Image Frame B Only - [Enabled]
import tkinter as tk
from tkinter import messagebox
import tkinter.ttk as ttk
from tkinter import colorchooser
import serial
import serial.tools.list_ports
import threading
import time
import time as _time
import RPi.GPIO as GPIO
import os
import json
import socket
import sys
import subprocess
import platform
import shutil
import smtplib
import ssl
from email.message import EmailMessage
import signal
import hashlib
import urllib.request
import urllib.error
import datetime
import hmac
import csv 

# --- Embedded UI icons (water drop) ---
# White icon for dark mode, black icon for light mode (avoids emoji/font issues on Raspberry Pi)
WATER_DROP_ICON_WHITE_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAAbElEQVR4nO2TUQrAMAhD6+5/Z/c/iElkskKXX4PPaLvWccrMdPxXp7kDsQAdyYDn1GqKPRKgaZUUFMCasHoJUPdc+fa4wQjA/bHIDwEREQ4A+b9L8JZ+QB8w/oocSOWjK2IQVpdugJq4axzRDZ8UPBWt/b8oAAAAAElFTkSuQmCC"
)
WATER_DROP_ICON_BLACK_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAAaElEQVR4nO2TwQ4AEAxD8f//zF10a7GQ0OuaPd0mpRdVFXOZbE5DVIAsBdC/mkpxTQL0WjcFA/CamHUPwF4L9F2zgxCA9GOR3wJkETD0H02wRR+wBAi/IgUCfcyIPIhZZ3eAmqhj3K8GGt8PJI51yCkAAAAASUVORK5CYII="
)

APP_NAME = "Stork Solutions Ltd Aquatics Monitor Application"
__version__ = "1.4.5" #BETA

GUI_MANIFEST_URL = "https://raw.githubusercontent.com/Stork-Solutions/Aquatics-Monitor/main/gui/latest/gui_update.json"# ===== Data Logging Licensing Helpers (v1.4.3) =====

LICENSE_FEATURE = "DATA_LOGGING"
# Restricted alphabet to avoid confusing characters (I, O, L, 0, 1)
_KEY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
_KEY_GROUP = 5
_KEY_GROUPS = 3

LICENSE_SECRET_PASSPHRASE = "bQ7mZ3vN2pK8rT6xH4sW9dY5cL1jA0uE"

def _get_pi_cpu_serial() -> str:
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.lower().startswith("serial"):
                    parts = line.split(":")
                    if len(parts) >= 2:
                        return parts[1].strip()
    except Exception:
        pass
    return ""

def _get_machine_id_fallback() -> str:
    try:
        with open("/etc/machine-id", "r") as f:
            return f.read().strip()
    except Exception:
        return ""

def get_device_id() -> str:
    serial = _get_pi_cpu_serial()
    if serial:
        return serial
    mid = _get_machine_id_fallback()
    return mid or "UNKNOWN_DEVICE"

def _normalize_key(user_key: str) -> str:
    if not user_key:
        return ""
    k = user_key.strip().upper().replace("-", "").replace(" ", "")
    # Keep only allowed characters
    return "".join([c for c in k if c in _KEY_ALPHABET])

def format_license_key(user_key: str) -> str:
    k = _normalize_key(user_key)
    if len(k) < _KEY_GROUP * _KEY_GROUPS:
        return k
    k = k[:_KEY_GROUP * _KEY_GROUPS]
    return "-".join([k[i:i+_KEY_GROUP] for i in range(0, _KEY_GROUP*_KEY_GROUPS, _KEY_GROUP)])

def _digest_to_key15(digest: bytes) -> str:
    # Map digest bytes to alphabet (base-N style)
    chars = []
    for b in digest:
        chars.append(_KEY_ALPHABET[b % len(_KEY_ALPHABET)])
        if len(chars) >= _KEY_GROUP * _KEY_GROUPS:
            break
    return "".join(chars)

def generate_license_key(device_id: str) -> str:
    # HMAC-SHA256(secret, "SAM|FEATURE|DEVICE")
    msg = f"SAM|{LICENSE_FEATURE}|{device_id}".encode("utf-8")
    secret = LICENSE_SECRET_PASSPHRASE.encode("utf-8")
    digest = hmac.new(secret, msg, hashlib.sha256).digest()
    return format_license_key(_digest_to_key15(digest))

def normalize_license_key(key: str) -> str:
    # Uppercase, remove spaces and hyphens
    return "".join(ch for ch in (key or "").upper().strip() if ch.isalnum())

def validate_license_key(license_key: str, device_id: str) -> bool:
    entered = normalize_license_key(license_key)
    expected = normalize_license_key(generate_license_key(device_id))

    return entered == expected

def safe_parse_float(x):
    try:
        if x is None:
            return None
        s = str(x).strip()
        if not s or s in ("--", "ERR"):
            return None
        # remove units common in SAM UI strings
        for u in ("mmWG", "mBar", "°C", "ppm", "µS/cm", "PSU"):
            s = s.replace(u, "")
        s = s.replace("Temperature:", "").replace("TDS:", "").replace("Conductivity:", "").replace("Salinity:", "")
        s = s.replace(",", "").strip()
        return float(s)
    except Exception:
        return None

class TransportTCP:
    def __init__(self, host, port=8888, timeout=2.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock = None
    def open(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect((self.host, self.port))
    def write(self, s: str):
        self.sock.sendall(s.encode())
    def readline(self) -> str:
        buf = b""; end = time.time() + self.timeout
        while time.time() < end:
            try:
                b1 = self.sock.recv(1)
                if not b1: break
                buf += b1
                if buf.endswith(b"\n"): break
            except socket.timeout:
                break
        return buf.decode().strip()
    def close(self):
        try:
            if self.sock: self.sock.close()
        except: pass

class TransportSerial:
    def __init__(self, ser): self.ser = ser
    def open(self): pass
    def write(self, s: str): self.ser.write(s.encode())
    def readline(self) -> str: return self.ser.readline().decode().strip()
    @property
    def is_open(self): 
        try: return self.ser.is_open
        except: return True
    def close(self):
        try: self.ser.close()
        except: pass

class SensorGUI:
    def __init__(self, root):
        self.root = root
        # Use an absolute settings path so saved options persist regardless of the launch working directory
        # (fixes cases where settings appear to "not load" until re-submitted).
        self._base_dir = os.path.dirname(os.path.abspath(__file__))
        self._audible_dir = os.path.join(self._base_dir, "audible")
        self._settings_path = os.path.join(self._base_dir, "settings.json")
        # Separate email settings file (persists SMTP config across reboots without touching settings.json)
        self._email_settings_path = os.path.join(self._base_dir, "email.settings.json")

        # --- Data Logging / Licensing (v1.4.3) ---
        self.device_id = get_device_id()
        print(f"[LICENSE] Device ID: {self.device_id}")
        self._license_path = os.path.join(self._base_dir, "license.json")
        self.is_data_logging_licensed = False

        # Logging settings persisted in settings.json
        self.data_logging_settings = {
            "enabled": False,
            "interval": "1Min",   # 30Sec | 1Min | 30Min | 1Hour
            "sensors": {"A": True, "B": True, "C": True, "D": True, "E": True},
            "retention_days": 30
        }

        # Email settings (used for emailing log files)
        self.email_settings = {
            "enabled": False,
            "smtp_server": "",
            "smtp_port": 587,
            "use_tls": True,
            "username": "",
            "password": "",
            "from_email": "",
            "to_email": ""
        }
        self._log_job = None
        self._log_current_date = None
        self._log_current_path = None
        self.cached_readings = {}  # latest parsed values per sensor for logging
        self._ensure_logs_dir()
        self._cleanup_old_logs(self.data_logging_settings.get("retention_days", 30))
        self._load_license()
        # Logging guards
        self._log_gen = 0                
        self.LOG_STALE_SECONDS = 180
        
        # GUI Setup
        self.root.title(f"{APP_NAME} v{__version__}")
        try:
            screen_h = self.root.winfo_screenheight()
        except Exception:
            screen_h = 800
        
        # --- Screen profile (persisted in settings.json) ---
        # Supported deployments: 4.3", 5", 10.1"
        # If settings.json already contains "screen_profile", we honor it.
        # Otherwise we infer a sensible default from the physical screen resolution.
        def _detect_screen_profile(w: int, h: int) -> str:
            # Common Raspberry Pi touch resolutions:
            # 4.3" ~ 480x272
            # 5"   ~ 800x480
            # 10.1"~ 1280x800 (or similar)
            if (w <= 520 and h <= 320) or (h <= 320 and w <= 520):
                return "4.3"
            if (w <= 900 and h <= 600) or (h <= 600 and w <= 900):
                return "5"
            return "10.1"

        # Read saved profile early (before we compute scale / fonts)
        self.screen_profile = None
        try:
            if os.path.exists(self._settings_path):
                with open(self._settings_path, "r") as _f:
                    _data = json.load(_f)
                    self.screen_profile = _data.get("screen_profile")
        except Exception:
            self.screen_profile = None

        try:
            screen_w = self.root.winfo_screenwidth()
        except Exception:
            screen_w = 1280

        if self.screen_profile not in ("4.3", "5", "10.1"):
            self.screen_profile = _detect_screen_profile(screen_w, screen_h)

        self.SCREEN_PROFILES = {
            "4.3": {"columns": 1, "outer_pad": 4,  "pady": 6, "scale": 0.85, "force_fullscreen": True},
            "5":   {"columns": 2, "outer_pad": 6,  "pady": 8, "scale": 1.00, "force_fullscreen": True},
            "10.1":{"columns": 3, "outer_pad": 10, "pady": 10,"scale": 1.25, "force_fullscreen": True},
        }
        self.profile = self.SCREEN_PROFILES[self.screen_profile]

        # Clamp to avoid extremes
        self.scale = max(0.7, min(1.6, float(self.profile.get("scale", 1.0))))

        def _s(size: int) -> int:
            # Helper for scaled font sizes / padding
            return max(8, int(size * self.scale))

        self._s = _s

        self._tds_last_visibility = None   # cache tuple: (show_tds, show_uScm, show_sal)
        # Optional per-frame visibility (set to False to hide a frame)
        # Defaults keep everything visible. To show only Aquarium A and RO Pump A:
        # self.frame_visibility.update({"Aquarium B": False, "RO Tank": False, "pH Sensor": False, "RO Pump B": False})
        self.frame_visibility = {
            "Aquarium A": True,
            "Aquarium B": True,
            "RO Tank": True,
            "pH Sensor": True,
            "TDS Sensor": True,
            "RO Pump A": True,
            "RO Pump B": True,
            "RPi Image": True,
            "www.stork.solutions": True,
        }
        # Sensor firmware versions (populated later by sensor firmware; default UNKNOWN) ---
        try:
            sensor_ids = list(getattr(self, "sensors", {}).keys())
        except Exception:
            sensor_ids = []

        # If sensors aren't defined yet at this point, seed common IDs used by this build
        if not sensor_ids:
            sensor_ids = ["A", "B", "C", "D", "E"]

        self.sensor_firmware = {sid: "UNKNOWN" for sid in sensor_ids}

        self.fullscreen = True  # Track fullscreen on or off

        # Apply fullscreen deterministically (prevents "partial/blank first paint" on some WMs)
        if self.profile.get("force_fullscreen", False):
            try:
                self.root.attributes("-fullscreen", True)
                self.fullscreen = True
            except Exception:
                pass
        self.root.bind("<Double-Button-1>", self.toggle_fullscreen)
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", self.exit_fullscreen)
        # Configure resizing for various screen types
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)
        self.root.columnconfigure(2, weight=1)
        
        # GPIO setup for pumps
        GPIO.setmode(GPIO.BCM)  
        self.pump_gpio = {
            "RO Pump A": 4, # GPIO Assignment R1=4,R2=27,R3=22,R4=17
            "RO Pump B": 27,
        }
        # Auto Activate Relay 4 GPIO 17 when Pumps are active
        self.relay4_gpio = 17
        GPIO.setup(self.relay4_gpio, GPIO.OUT)
        GPIO.output(self.relay4_gpio, GPIO.LOW)
        # Drain Pump (Auto Water Change) - Relay 3 on GPIO 22
        self.drain_gpio = 22
        GPIO.setup(self.drain_gpio, GPIO.OUT)
        GPIO.output(self.drain_gpio, GPIO.LOW)

        
        for pin in self.pump_gpio.values():
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)  

        # Per-sensor water level thresholds
        self.thresholds = {
            "A": {"on": 315, "off": 336},
            "B": {"on": 315, "off": 336},
           
        }
       
        self.display_units = {
            "A": {"use_liters": False, "use_gallons": False, "width": 0, "depth": 0, "use_fahrenheit": False, "temp_alarm_enabled": False, "temp_min": 0, "temp_max": 0},
            "B": {"use_liters": False, "use_gallons": False, "width": 0, "depth": 0, "use_fahrenheit": False, "temp_alarm_enabled": False, "temp_min": 0, "temp_max": 0},
            "C": {
                "use_liters": False,
                "use_gallons": False,
                "width": 0,
                "depth": 0,
                "capacity": 0,
                "level_alarm": False,
                "min_alarm": 0,
                "max_alarm": 0,
                "r2_temp_enabled": False,
                "temp_alarm_enabled": False,
                "temp_min": 0,
                "temp_max": 0,
            },
            "D": {
                "ph_alarm_enabled": False,
                "ph_min": 0,
                "ph_max": 0,
                "use_fahrenheit": False,
                "temp_alarm_enabled": False,
                "temp_min": 0,
                "temp_max": 0,
            },
            "E": {
                "tds_alarm_enabled": False,
                "tds_min": 0,
                "tds_max": 0,
                "use_fahrenheit": False,
                "temp_alarm_enabled": False,
                "temp_min": 0,
                "temp_max": 0,
            },
        }
        
        self.display_units.setdefault("E", {})
        self.display_units["E"].setdefault("show_fields", {
            "tds_ppm": True,         # TDS on by default
            "cond_uScm": False,      # (Optional) off by default 
            "sal_psu": False,        # (Optional) off bt default 
        })


        # Additional Sensor E alarms (only meaningful when the reading is enabled in show_fields)
        self.display_units["E"].setdefault("cond_alarm_enabled", False)
        self.display_units["E"].setdefault("cond_min", 0)
        self.display_units["E"].setdefault("cond_max", 0)
        self.display_units["E"].setdefault("sal_alarm_enabled", False)
        self.display_units["E"].setdefault("sal_min", 0)
        self.display_units["E"].setdefault("sal_max", 0)

        self.visual_settings = {
             "dark_mode": False,
             "colors": {
                 "water": "#0000FF",
                 "temp": "#FF0000",
                 "ph": "#800080",
                 "tds": "#A52A2A",
                 "cond": "#00FF00",
                 "sal": "#FFA500"
             },
             "image_frame_b_file": "image2.png",
        }
       
        # Serial port connections
        self.sensors = {
            "A": {"port": None, "is_running": False},
            "B": {"port": None, "is_running": False},
            "C": {"port": None, "is_running": False},
            "D": {"port": None, "is_running": False},
            "E": {"port": None, "is_running": False},
        }
        self.sensor_firmware = {sid: None for sid in self.sensors}
        self.root.after(3000, lambda: threading.Thread(target=self.sensor_watchdog, daemon=True).start())
        print("[WATCHDOG] Started")

        # TCP RX TX Locking
        self.io_locks = {sid: threading.Lock() for sid in self.sensors.keys()}

        self.sensor_fail_counts = {sid: 0 for sid in self.sensors.keys()}
        self.sensor_disabled_flags = {sid: False for sid in self.sensors.keys()}
        self.MAX_SENSOR_RETRIES = 5

        # Wi-Fi TCP connections
        self.endpoints = {
            "A": {"type": "serial", "host": "", "port": 8888},
            "B": {"type": "serial", "host": "", "port": 8888},
            "C": {"type": "serial", "host": "", "port": 8888},
            "D": {"type": "serial", "host": "", "port": 8888},
            "E": {"type": "serial", "host": "", "port": 8888},
        }

        # Per-sensor tare offsets (mmWG) for display only
        self.tare_offsets = {"A": 0.0, "B": 0.0, "C": 0.0}
   
        # Pump states
        self.pump_states = {
            "RO Pump A": False,
            "RO Pump B": False,
        }
        # Keep-Alive (Anti-Idle) for pumps
        # Track the last time each pump's tank reached the max/off threshold.
        self.last_max_reached = {
            "RO Pump A": time.time(),
            "RO Pump B": time.time(),
        }
        # Whether a keep-alive cycle is currently running (OFF for 4 minutes)
        self.anti_idle_active = {
            "RO Pump A": False,
            "RO Pump B": False,
        }
        # after() job handles for restoring power
        self.anti_idle_jobs = {
            "RO Pump A": None,
            "RO Pump B": None,
        }
        # Keep-alive tunables
        self.KEEPALIVE_WINDOW_SECS = 10 * 60 * 60   # 10 hours
        self.KEEPALIVE_OFF_MS     = 4 * 60 * 1000   # 4 minutes (milliseconds)

        self.override_states = {
            "RO Pump A": False,
            "RO Pump B": False,
        }
       
        # Auto Water Change (Drain) state
        self.water_change_active = {'A': False, 'B': False}
        self.water_change_start_mmwg = {'A': None, 'B': None}
        self.water_change_target_mmwg = {'A': None, 'B': None}
        self.water_change_started_at = {'A': None, 'B': None}
        self.WATER_CHANGE_TIMEOUT_SECS = 30 * 60  # 30 minutes safety timeout
        self.flash_jobs = {
            "RO Pump A": None,
            "RO Pump B": None,
        }
       
        self.alarm_flash_jobs = {}
        self.current_status_text = {
            "ro_tank": "",
            "ph_sensor": "",
            "tds_sensor": ""
        }
        self.flash_jobs = {}
        self.flashing_labels = {}
        
        # Alarm sound config (RPi / ALSA)
        # Alarm sound config (RPi / ALSA) - WAVs live in ./audible/
        self.sound_paths = {
            "approaching": os.path.join(self._audible_dir, "approaching_limit.wav"),
            "critical":    os.path.join(self._audible_dir, "level_critical.wav"),
        }

        # Single running sound process + key
        self._sound_proc = None
        self._sound_key  = None
        
        # Alarm bookkeeping (edge-triggered)
        self.alarm_state = {"A": "normal", "B": "normal", "C": "normal", "D": "normal", "E": "normal"}  # normal|approaching|critical
        self.alarm_flash_jobs = {}           
        self.alarm_last_play = {}             
        self.alarm_sound_proc = {}            
        self.current_status_text = {} 
        self._base_dir = os.path.dirname(os.path.abspath(__file__))
        # New Grid Layout (User Adjustable Via graphics menu)
        self.frame_positions = {
            "Aquarium A": {"row": 0, "col": 0, "colspan": 1},
            "RO Pump A":  {"row": 0, "col": 1, "colspan": 1},
            "pH Sensor":  {"row": 0, "col": 2, "colspan": 1},

            "Aquarium B": {"row": 1, "col": 0, "colspan": 1},
            "RO Pump B":  {"row": 1, "col": 1, "colspan": 1},
            "RO Tank":    {"row": 1, "col": 2, "colspan": 1},

            "TDS Sensor": {"row": 2, "col": 0, "colspan": 1},
            "RPi Image":  {"row": 2, "col": 1, "colspan": 1},
            "www.stork.solutions": {"row": 2, "col": 2, "colspan": 1},
        }
        self.use_frame_positions = False
 
        # Main Grid Layout (position-driven)
        p = self.frame_positions

        self.aquarium_frame_1 = self.create_sensor_frame("Aquarium A", p["Aquarium A"]["row"], p["Aquarium A"]["col"],)
        self.aquarium_frame_2 = self.create_sensor_frame("Aquarium B", p["Aquarium B"]["row"], p["Aquarium B"]["col"],)
        self.ro_tank_frame     = self.create_ro_tank_frame("RO Tank", p["RO Tank"]["row"], p["RO Tank"]["col"])
        self.ph_level_frame    = self.create_ph_level_frame("pH Sensor", p["pH Sensor"]["row"], p["pH Sensor"]["col"])
        self.pump_frame_a      = self.create_pump_frame("RO Pump A", p["RO Pump A"]["row"], p["RO Pump A"]["col"])
        self.pump_frame_b      = self.create_pump_frame("RO Pump B", p["RO Pump B"]["row"], p["RO Pump B"]["col"])
        self.tds_level_frame   = self.create_tds_level_frame("TDS Sensor", p["TDS Sensor"]["row"], p["TDS Sensor"]["col"])
        self.image_frame_b     = self.create_image_frame_b("", p["RPi Image"]["row"], p["RPi Image"]["col"], colspan=p["RPi Image"].get("colspan", 1))
        self.image_frame_c     = self.create_image_frame_c("www.stork.solutions", p["www.stork.solutions"]["row"], p["www.stork.solutions"]["col"], colspan=p["www.stork.solutions"].get("colspan", 1))

        # Load saved settings BEFORE applying visibility/layout
        self.load_threshold_settings()
        # Load email settings from separate file (email.settings.json)
        try:
            self.load_email_settings()
        except Exception:
            pass
        # Apply saved Image Frame B selection on boot (after settings load)
        try:
            self.refresh_image_frame_b()
        except Exception:
            pass
        # Start logging on boot if enabled + licensed
        try:
            if self.data_logging_settings.get('enabled', False) and self.is_data_logging_licensed:
                self.start_logging()
        except Exception:
            pass
        # Apply frame visibility/layout now, and again shortly after the window manager finalises geometry.
        self.apply_frame_visibility()
        try:
            self.root.after(50, self.apply_frame_visibility)
            self.root.after(200, self.apply_frame_visibility)
        except Exception:
            pass

        self.apply_theme(self.root)
        self.apply_reading_colors()
        self.connect_to_sensors()
        self.sensor_failures = {"A": 0, "B": 0, "C": 0, "D": 0, "E": 0}
        self.sensor_active = {"A": True, "B": True, "C": True, "D": True, "E": True}
        
    def show_confirm(self, title, message, yes_text="Yes", no_text="Cancel"):
        import tkinter as tk
        popup = tk.Toplevel(self.root)
        popup.title(title)
        popup.transient(self.root)
        popup.attributes("-fullscreen", True)
        popup.bind("<Double-Button-1>", lambda e: popup.attributes("-fullscreen", not popup.attributes("-fullscreen")))

        # Make sure it's actually viewable before grab_set()
        popup.lift()
        popup.attributes("-topmost", True)
        popup.update_idletasks()

        def _try_grab():
            try:
                popup.grab_set()
                popup.focus_set()
            except tk.TclError:
                # retry shortly if the window manager hasn't mapped it yet
                popup.after(50, _try_grab)

        _try_grab()

        # Simple content
        container = tk.Frame(popup, padx=20, pady=16)
        container.pack(fill="both", expand=True)
        tk.Label(container, text=title, font=("Arial", 14, "bold")).pack(anchor="w", pady=(0, 8))
        tk.Label(container, text=message, justify="left", wraplength=420).pack(anchor="w")

        # Buttons
        choice = {"ok": False}
        btns = tk.Frame(container)
        btns.pack(anchor="e", pady=(14, 0))
        def _ok(): choice["ok"] = True; popup.destroy()
        def _no(): popup.destroy()
        if no_text:
            tk.Button(btns, text=no_text, command=_no, width=10).pack(side="right", padx=(8, 0))
        tk.Button(btns, text=yes_text, command=_ok, width=12).pack(side="right")

        # Apply your existing theme to THIS popup
        try:
            self.apply_theme(popup)
        except Exception:
            pass

        # Center
        popup.update_idletasks()
        try:
            x = self.root.winfo_rootx() + (self.root.winfo_width() // 2) - (popup.winfo_width() // 2)
            y = self.root.winfo_rooty() + (self.root.winfo_height() // 2) - (popup.winfo_height() // 2)
            popup.geometry(f"+{x}+{y}")
        except Exception:
            pass

        popup.bind("<Return>", lambda e: _ok())
        popup.bind("<Escape>", lambda e: _no())
        popup.wait_window()
        return choice["ok"]

    def show_info(self, title, message, ok_text="OK"):
        """Themed informational popup (dark-mode aware)."""
        return self.show_confirm(title, message, yes_text=ok_text, no_text="")

    def show_error(self, title, message, ok_text="OK"):
        """Themed error popup (dark-mode aware)."""
        return self.show_confirm(title, message, yes_text=ok_text, no_text="")

    def ui_call_blocking(self, fn):
        """Run a callable on the Tk UI thread and wait for its return value."""
        evt = threading.Event()
        box = {"result": None}
        def _run():
            try:
                box["result"] = fn()
            finally:
                evt.set()
        self.root.after(0, _run)
        evt.wait()
        return box["result"]
    
    def tared_mmwg(self, sensor_id: str, raw_mmwg: float) -> float:
        """Return reading minus per-sensor tare offset (mmWG)."""
        try:
            return float(raw_mmwg) - float(self.tare_offsets.get(sensor_id, 0.0))
        except Exception:
            return float(raw_mmwg)
    
    # New Settings Button 
    def attach_settings_cog(self, frame, command):
        """Attach a ⚙️ settings button at the top-right of a frame (overlay)."""
        btn = tk.Button(
            frame,
            text="⚙️",
            font=("Arial", getattr(self, "_s", lambda x: x)(12)),
            command=command,
            bd=0,
            relief="flat",
            highlightthickness=0
        )
        # Overlay it in the top-right corner with a little padding.
        btn.place(relx=1.0, rely=0.0, anchor="ne", x=-6, y=6)
        return btn
    
    def attach_water_change_button(self, frame, command):
        """Attach a Water Change button (water-drop icon) at the top-right of a pump frame (overlay).

        Uses embedded PNG icons (theme-aware) so it is consistent across Raspberry Pi builds.
        """
        # Lazily create theme-aware PhotoImages once.
        try:
            dark = self.visual_settings.get("dark_mode", False)
        except Exception:
            dark = False

        try:
            if not hasattr(self, "_water_drop_img_white") or self._water_drop_img_white is None:
                self._water_drop_img_white = tk.PhotoImage(data=WATER_DROP_ICON_WHITE_PNG_B64)
            if not hasattr(self, "_water_drop_img_black") or self._water_drop_img_black is None:
                self._water_drop_img_black = tk.PhotoImage(data=WATER_DROP_ICON_BLACK_PNG_B64)
        except Exception:
            self._water_drop_img_white = None
            self._water_drop_img_black = None

        img = self._water_drop_img_white if dark else self._water_drop_img_black

        if img is not None:
            btn = tk.Button(
                frame,
                image=img,
                command=command,
                bd=0,
                relief="flat",
                highlightthickness=0
            )
            # Keep references (extra-safe against GC quirks)
            btn.image = img
        else:
            # Fallback if image cannot be created (should be rare)
            btn = tk.Button(
                frame,
                text="WC",
                font=("Arial", getattr(self, "_s", lambda x: x)(10)),
                command=command,
                bd=0,
                relief="flat",
                highlightthickness=0
            )

        # Track buttons so we can swap icons when theme changes.
        if not hasattr(self, "_water_change_buttons"):
            self._water_change_buttons = []
        self._water_change_buttons.append(btn)

        # Match the exact position used by the settings cog on sensor frames.
        btn.place(relx=1.0, rely=0.0, anchor="ne", x=-6, y=6)
        return btn
    
    def _refresh_water_change_button_icons(self):
        """Update water change button icon (white/black) based on current theme."""
        try:
            dark = self.visual_settings.get("dark_mode", False)
        except Exception:
            dark = False

        img = getattr(self, "_water_drop_img_white", None) if dark else getattr(self, "_water_drop_img_black", None)

        if not img:
            return

        for btn in getattr(self, "_water_change_buttons", []):
            try:
                btn.configure(image=img)
                btn.image = img
            except Exception:
                pass
# Auto GitHub Update
    def _version_tuple(self, v: str):
        v = (v or "").strip().lstrip("v")
        parts = []
        for p in v.split("."):
            try:
                parts.append(int(p))
            except:
                parts.append(0)
        return tuple(parts)

    def _http_get_json(self, url: str, timeout: int = 8):
        req = urllib.request.Request(url, headers={"User-Agent": "SAM-Max"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        return json.loads(data.decode("utf-8", "ignore"))

    def _http_get_bytes(self, url: str, timeout: int = 15) -> bytes:
        req = urllib.request.Request(url, headers={"User-Agent": "SAM-Max"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    def _sha256_hex(self, b: bytes) -> str:
        return hashlib.sha256(b).hexdigest().lower()

    def check_gui_update(self):
        """
        Returns (available: bool, latest_version: str, file_url: str, sha256: str, error: str|None)
        """
        try:
            manifest = self._http_get_json(GUI_MANIFEST_URL, timeout=8)

            latest = str(manifest.get("latest_version", "")).strip()
            minv   = str(manifest.get("min_supported_version", "")).strip()
            files  = manifest.get("files", {}) or {}

            if not latest or "SAM-Max.py" not in files:
                return (False, "", "", "", "Manifest missing latest_version or SAM-Max.py entry")

            if minv and self._version_tuple(__version__) < self._version_tuple(minv):
                return (False, latest, "", "", f"This build ({__version__}) is below min_supported_version ({minv})")

            if self._version_tuple(latest) <= self._version_tuple(__version__):
                return (False, latest, "", "", None)

            finfo = files["SAM-Max.py"] or {}
            url = str(finfo.get("url", "")).strip()
            sha = str(finfo.get("sha256", "")).strip().lower()

            if not url:
                return (False, latest, "", "", "Manifest file entry missing URL")

            return (True, latest, url, sha, None)

        except Exception as e:
            return (False, "", "", "", f"Update check failed: {e}")

    def apply_gui_update(self):
        """
        Download + verify + atomic replace + restart.
        Runs in a thread via the UI handler.
        """
        available, latest, url, expect_sha, err = self.check_gui_update()
        if err:
            self.root.after(0, lambda: self.show_error("Update", err))
            return

        if not available:
            msg = f"No update available.\nCurrent: {__version__}\nLatest: {latest or __version__}"
            self.root.after(0, lambda: self.show_info("Update", msg))
            return

        # Confirm with user
        ok = self.ui_call_blocking(lambda: self.show_confirm(
            "Update available",
            f"Update available: {latest}\nCurrent: {__version__}\n\nInstall now?",
            yes_text="Install",
            no_text="Cancel",
        ))
        if not ok:
            return

        try:
            data = self._http_get_bytes(url, timeout=20)
            got_sha = self._sha256_hex(data)

            if expect_sha and got_sha != expect_sha.lower():
                self.root.after(0, lambda: self.show_error(
                    "Update",
                    "SHA256 mismatch – update aborted.\n\n"
                    f"Expected: {expect_sha}\nGot:      {got_sha}"
                ))
                return

            # Where is the currently running file?
            current_path = os.path.abspath(__file__)
            new_path = current_path + ".new"
            bak_path = current_path + ".bak"

            # Write .new
            with open(new_path, "wb") as f:
                f.write(data)

            # Backup current
            try:
                if os.path.exists(bak_path):
                    os.remove(bak_path)
            except:
                pass
            shutil.copy2(current_path, bak_path)

            # Atomic replace
            os.replace(new_path, current_path)

            self.root.after(0, lambda: self.show_info("Update", f"Updated to {latest}. Restarting now…"))

            # Restart process
            python = sys.executable or "python3"
            os.execv(python, [python] + sys.argv)

        except Exception as e:
            self.root.after(0, lambda: self.show_error("Update", f"Update failed: {e}"))

    def ui_check_gui_update(self):
        # Run update check/apply in background so Tkinter doesn't freeze
        threading.Thread(target=self.apply_gui_update, daemon=True).start()

    # Start to build frames  
    def create_sensor_frame(self, title, row, column, colspan=1):
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=10)
        frame.grid(row=row, column=column, padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=int(getattr(self, "profile", {}).get("pady", 10)), sticky="nsew", columnspan=colspan)

        # Connection Status
        connection_status_label = tk.Label(frame, text="Status:", font=("Arial", 14, "bold"), fg="black")
        connection_status_label.pack(anchor="n", pady=(5, 0))
        connection_status = tk.Label(frame, text="Disconnected!", font=("Arial", 14, "bold"), fg="red")
        connection_status.pack(anchor="n")

        # Readings
        water_gauge_label = tk.Label(frame, text="Water Level: ", font=("Arial", 14, "bold"), fg="blue")
        water_gauge_label.pack(pady=10)
        temperature_label = tk.Label(frame, text="Temperature: --", font=("Arial", 14, "bold"), fg="red")
        temperature_label.pack(pady=10)
       
        # Settings Cog (top-right)
        self.attach_settings_cog(frame, command=lambda sid=title.split()[-1]: self.open_settings_popup(sid))

        return {
            "frame": frame,
            "connection_status": connection_status,
            "temperature_label": temperature_label,
            "water_gauge_label": water_gauge_label,
        }

    def create_ro_tank_frame(self, title, row, column, colspan=1):
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=10)
        frame.grid(row=row, column=column, padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=int(getattr(self, "profile", {}).get("pady", 10)), sticky="nsew", columnspan=colspan)

        # Connection Status
        connection_status_label = tk.Label(frame, text="Status:", font=("Arial", 14, "bold"), fg="black")
        connection_status_label.pack(anchor="n", pady=(5, 0))
        connection_status = tk.Label(frame, text="Disconnected!", font=("Arial", 14, "bold"), fg="red")
        connection_status.pack(anchor="n")

        # Readings (Water Level only for RO Tank)
        water_gauge_label = tk.Label(frame, text="Water Level:--", font=("Arial", 14, "bold"), fg="blue")
        water_gauge_label.pack(pady=10)
        # If Rev 2 sensor then accept
        temperature_label = tk.Label(frame, text="Temperature: --", font=("Arial", 14, "bold"), fg="red")
        temperature_label.pack(pady=10)
        if self.display_units.get("C", {}).get("r2_temp_enabled", False):
            temperature_label.pack(pady=10)
       
        # Settings Cog (top-right)
        settings_button = self.attach_settings_cog(frame, command=self.open_ro_settings_popup)

        return {
            "frame": frame,
            "connection_status": connection_status,
            "water_gauge_label": water_gauge_label,
            "temperature_label": temperature_label,
            "settings_button": settings_button, 
        }
   
    def create_ph_level_frame(self, title, row, column, colspan=1):
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=10)
        frame.grid(row=row, column=column, padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=int(getattr(self, "profile", {}).get("pady", 10)), sticky="nsew", columnspan=colspan)

        # Connection Status
        connection_status_label = tk.Label(frame, text="Status:", font=("Arial", 14, "bold"), fg="black")
        connection_status_label.pack(anchor="n", pady=(5, 0))
        connection_status = tk.Label(frame, text="Disconnected!", font=("Arial", 14, "bold"), fg="red")
        connection_status.pack(anchor="n")

        # Readings (pH Level & Temperature)
        ph_level_label = tk.Label(frame, text="pH: --", font=("Arial", 14, "bold"), fg="purple")
        ph_level_label.pack(pady=10)
        temperature_label = tk.Label(frame, text="Temperature: --", font=("Arial", 14, "bold"), fg="red")
        temperature_label.pack(pady=10)
       
        # Settings Cog (top-right)
        self.attach_settings_cog(frame, command=self.open_ph_settings_popup)
         
        return {
            "frame": frame,
            "connection_status": connection_status,
            "ph_level_label": ph_level_label,
            "temperature_label": temperature_label,
        }

    def create_pump_frame(self, title, row, column):
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=10)
        frame.grid(row=row, column=column, padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=int(getattr(self, "profile", {}).get("pady", 10)), sticky="nsew")
        # Water Change (Drain) button (top-right overlay)
        # Default tank selection based on pump title (A/B), user can change in popup.
        default_sid = "A" if str(title).endswith("A") else ("B" if str(title).endswith("B") else "A")
        self.attach_water_change_button(frame, command=lambda sid=default_sid: self.open_water_change_popup(default_sensor=sid))

        # Pump Status
        pump_status = tk.Label(frame, text="OFF", font=("Arial", 14, "bold"), fg="red")
        pump_status.pack(pady=5)

        # Auto Top-Up Label
        auto_top_up_label = tk.Label(frame, text="", font=("Arial", 14, "bold"))
        auto_top_up_label.pack(pady=5)

        controls = tk.Frame(frame)
        controls.pack(pady=5)

        # Auto Mode Checkbox
        auto_mode_var = tk.BooleanVar(value=False)
        auto_checkbox = tk.Checkbutton(
            controls,
            text="Enable Auto Mode",
            variable=auto_mode_var,
            font=("Arial", 12),
            anchor="w"
        )
        auto_checkbox.pack(pady=(0, 12))   # <-- gap between checkbox and button

        # Toggle Button
        toggle_button = tk.Button(controls, text="Turn On")
        toggle_button.config(command=lambda: self.toggle_pump(title, pump_status, toggle_button))
        toggle_button.pack(pady=(20, 5))

        return {
            "frame": frame,
            "pump_status": pump_status,
            "auto_top_up_label": auto_top_up_label,
            "toggle_button": toggle_button,
            "auto_mode_var": auto_mode_var,
        }
    def apply_frame_visibility(self):
        """Show/hide top-level frames based on self.frame_visibility.
        Uses grid_remove() to keep layout state for quick re-enable.

        Important: Do NOT bail out if any single frame isn't created yet.
        We apply visibility to whatever frames currently exist, then reflow.
        """
        mapping = {
            "Aquarium A": getattr(self, "aquarium_frame_1", {}).get("frame"),
            "Aquarium B": getattr(self, "aquarium_frame_2", {}).get("frame"),
            "RO Tank": getattr(self, "ro_tank_frame", {}).get("frame"),
            "pH Sensor": getattr(self, "ph_level_frame", {}).get("frame"),
            "TDS Sensor": getattr(self, "tds_level_frame", {}).get("frame"),
            "RO Pump A": getattr(self, "pump_frame_a", {}).get("frame"),
            "RO Pump B": getattr(self, "pump_frame_b", {}).get("frame"),
            "RPi Image": getattr(self, "image_frame_b", {}).get("frame"),
            "www.stork.solutions": getattr(self, "image_frame_c", {}).get("frame"),
        }

        for name, frame in mapping.items():
            if frame is None:
                continue
            show = bool(self.frame_visibility.get(name, True))
            try:
                if show:
                    frame.grid()
                else:
                    frame.grid_remove()
            except Exception:
                pass

        # Always check custome layout first before applying reflow grid
        try:
            if getattr(self, "use_frame_positions", False):
                self.apply_frame_positions_layout()
            else:
                self.reflow_grid(max_cols=int(getattr(self, "profile", {}).get("columns", 3)))
        except Exception:
            try:
                if getattr(self, "use_frame_positions", False):
                    self.apply_frame_positions_layout()
                else:
                    self.reflow_grid()
            except Exception:
                pass
      
    def reflow_grid(self, max_cols=None):
        """
        Auto-arranges visible frames into full-width rows that stretch when fullscreen.
        Frames fill available space and never clip.
        """

        # Clear ALL grid placements (safe for Tkinter)
        all_frames = [
            self.aquarium_frame_1["frame"],
            self.aquarium_frame_2["frame"],
            self.ro_tank_frame["frame"],
            self.ph_level_frame["frame"],
            self.tds_level_frame["frame"],
            self.pump_frame_a["frame"],
            self.pump_frame_b["frame"],
            self.image_frame_b["frame"],
            self.image_frame_c["frame"]
        ]

        for frame in all_frames:
            frame.grid_forget()

        # Create list of visible frames
        visible_frames = []
        for name, frame in [
            ("Aquarium A", self.aquarium_frame_1["frame"]),
            ("Aquarium B", self.aquarium_frame_2["frame"]),
            ("RO Tank", self.ro_tank_frame["frame"]),
            ("pH Sensor", self.ph_level_frame["frame"]),
            ("TDS Sensor", self.tds_level_frame["frame"]),
            ("RO Pump A", self.pump_frame_a["frame"]),
            ("RO Pump B", self.pump_frame_b["frame"]),
            ("RPi Image", self.image_frame_b["frame"]),
            ("www.stork.solutions", self.image_frame_c["frame"]),
        ]:
            if self.frame_visibility.get(name, True):
                visible_frames.append(frame)
        # Lay them out in rows (columns set by screen profile)
        if max_cols is None:
            max_cols = int(getattr(self, "profile", {}).get("columns", 3))
        max_cols = max(1, min(3, int(max_cols)))
        row = 0
        col = 0

        for i, frame in enumerate(visible_frames):

            col = i % max_cols
            row = i // max_cols

            frame.grid(
                row=row,
                column=col,
                padx=int(getattr(self, "profile", {}).get("outer_pad", 10)),
                pady=int(getattr(self, "profile", {}).get("pady", 10)),
                sticky="nsew"
            )

        # Configure columns to stretch and fill the screen
        for col_index in range(max_cols):
            try:
                self.root.columnconfigure(col_index, weight=1)
            except:
                pass

        # Rows stretch too (prevents clipping when tall)
        for row_index in range(row + 1):
            try:
                self.root.rowconfigure(row_index, weight=1)
            except:
                pass
    
    def apply_frame_positions_layout(self):
        """
        Layout frames using self.frame_positions (fixed positions).
        Respects self.frame_visibility and supports fullscreen scaling.
        """
        # Map names -> actual Tk frames
        mapping = {
            "Aquarium A": self.aquarium_frame_1["frame"],
            "Aquarium B": self.aquarium_frame_2["frame"],
            "RO Tank": self.ro_tank_frame["frame"],
            "pH Sensor": self.ph_level_frame["frame"],
            "TDS Sensor": self.tds_level_frame["frame"],
            "RO Pump A": self.pump_frame_a["frame"],
            "RO Pump B": self.pump_frame_b["frame"],
            "RPi Image": self.image_frame_b["frame"],
            "www.stork.solutions": self.image_frame_c["frame"],
        }

        # Clear all grid placements first
        for fr in mapping.values():
            try:
                fr.grid_forget()
            except Exception:
                pass

        # Determine grid bounds
        max_row = 0
        max_col = 0
        for name, pos in self.frame_positions.items():
            if not self.frame_visibility.get(name, True):
                continue
            r = int(pos.get("row", 0))
            c = int(pos.get("col", 0))
            cs = int(pos.get("colspan", 1))
            max_row = max(max_row, r)
            max_col = max(max_col, c + cs - 1)

        # Apply the grid placements
        for name, fr in mapping.items():
            if not self.frame_visibility.get(name, True):
                continue
            pos = self.frame_positions.get(name, {"row": 0, "col": 0, "colspan": 1})
            r = int(pos.get("row", 0))
            c = int(pos.get("col", 0))
            cs = int(pos.get("colspan", 1))

            try:
                fr.grid(row=r, column=c, columnspan=cs, padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=int(getattr(self, "profile", {}).get("pady", 10)), sticky="nsew")
            except Exception:
                pass

        # Full scaling: make all used rows/cols expand
        for r in range(max_row + 1):
            try:
                self.root.grid_rowconfigure(r, weight=1, uniform="row")
            except Exception:
                pass
        for c in range(max_col + 1):
            try:
                self.root.grid_columnconfigure(c, weight=1, uniform="col")
            except Exception:
                pass

    def flash_auto_top_up(self, label, pump_name):
        """Flash the AUTO TOP UP ACTIVE banner (red/green) on the given label."""
        def toggle_color():
            try:
                current = label.cget("fg")
                label.config(fg="red" if current == "green" else "green")
            except Exception:
                pass
            # Store (label, after_id) so we can cancel correctly
            self.flashing_labels[pump_name] = (label, label.after(500, toggle_color))

        # Cancel any existing flash before starting a new one
        self.stop_flashing(pump_name)

        try:
            label.config(text="AUTO TOP UP ACTIVE", fg="red")
        except Exception:
            pass
        toggle_color()

    def flash_water_change(self, label, pump_name):
        """Flash the WATER CHANGE: DRAINING banner (orange/white) on the given label."""
        key = f"{pump_name}__WC"
        def toggle_color():
            try:
                current = label.cget("fg")
                label.config(fg="orange" if current in ("white", "black") else "white")
            except Exception:
                pass
            self.flashing_labels[key] = (label, label.after(500, toggle_color))

        self.stop_flashing(key)

        try:
            label.config(text="WATER CHANGE: DRAINING", fg="orange")
        except Exception:
            pass
        toggle_color()

    def stop_flashing(self, key):
        """Stop any flashing job registered under 'key'."""
        job = self.flashing_labels.get(key)
        if job:
            try:
                # New format: (label, after_id)
                if isinstance(job, tuple) and len(job) == 2:
                    lbl, after_id = job
                    try:
                        lbl.after_cancel(after_id)
                    except Exception:
                        pass
                else:
                    # Legacy format: after_id only (best effort cancel on pump A label)
                    try:
                        self.pump_frame_a["auto_top_up_label"].after_cancel(job)
                    except Exception:
                        pass
            except Exception:
                pass
            self.flashing_labels.pop(key, None)

        # Also clear the label visually for known keys
        def _default_fg():
            try:
                return "white" if getattr(self, "theme", "dark") == "dark" else "black"
            except Exception:
                return "black"

        try:
            if key == "RO Pump A":
                self.pump_frame_a["auto_top_up_label"].config(text="", fg=_default_fg())
            elif key == "RO Pump B":
                self.pump_frame_b["auto_top_up_label"].config(text="", fg=_default_fg())
            elif isinstance(key, str) and key.endswith("__WC"):
                # WC keys: clear the corresponding pump banner
                if key.startswith("RO Pump A"):
                    self.pump_frame_a["auto_top_up_label"].config(text="", fg=_default_fg())
                elif key.startswith("RO Pump B"):
                    self.pump_frame_b["auto_top_up_label"].config(text="", fg=_default_fg())
            elif key == "RO Tank":
                try:
                    if self.alarm_state.get("C_alarm", "normal") == "normal":
                        self.ro_tank_frame["connection_status"].config(text="Connected", fg="green")
                except Exception:
                    pass
            elif key == "pH Sensor":
                try:
                    if self.alarm_state.get("D_alarm", "normal") == "normal":
                        self.ph_level_frame["connection_status"].config(text="Connected", fg="green")
                except Exception:
                    pass
            elif key == "TDS Sensor":
                try:
                    if self.alarm_state.get("E_alarm", "normal") == "normal":
                        self.tds_level_frame["connection_status"].config(text="Connected", fg="green")
                except Exception:
                    pass
        except Exception:
            pass



    def stop_water_change(self, sensor_id: str, reason: str = "complete"):
        sid = (sensor_id or "A").strip().upper()
        if sid not in ("A", "B"):
            sid = "A"

        # Deactivate drain relay
        try:
            GPIO.output(self.drain_gpio, GPIO.LOW)
        except Exception as e:
            print(f"[ERROR] drain relay OFF: {e}")

        self.water_change_active[sid] = False
        self.water_change_start_mmwg[sid] = None
        self.water_change_target_mmwg[sid] = None
        self.water_change_started_at[sid] = None

        # Clear pump banner text
        try:
            pf = self.pump_frame_a if sid == "A" else self.pump_frame_b
            pump_name = "RO Pump A" if sid == "A" else "RO Pump B"
            # Stop WC flashing and clear banner
            try:
                self.stop_flashing(f"{pump_name}__WC")
            except Exception:
                pass
            fg = "white" if getattr(self, "theme", "dark") == "dark" else "black"
            pf["auto_top_up_label"].config(text="", fg=fg)
        except Exception:
            pass

        print(f"[WATER CHANGE] STOP Tank {sid} ({reason})")

    def water_change_tick(self, sensor_id: str, current_mmwg: float):
        """Called from the sensor read loop for A/B to manage drain completion + timeout.

        IMPORTANT: This runs in the sensor thread, so any popups must be scheduled onto the GUI thread
        using safe_gui_update.
        """
        sid = (sensor_id or "A").strip().upper()
        if sid not in ("A", "B"):
            return

        if not self.water_change_active.get(sid, False):
            return

        # Timeout safety
        try:
            started = self.water_change_started_at.get(sid, None)
            if started and (time.time() - float(started)) > float(self.WATER_CHANGE_TIMEOUT_SECS):
                self.stop_water_change(sid, reason="timeout")
                self.safe_gui_update(lambda s=sid: self.show_info(
                    "Water change stopped",
                    f"Tank {s}: drain stopped due to timeout."
                ))
                return
        except Exception:
            pass

        # Completion check
        try:
            target = self.water_change_target_mmwg.get(sid, None)
            if target is None:
                return
            if float(current_mmwg) <= float(target):
                self.stop_water_change(sid, reason="complete")

                # Let auto mode immediately re-evaluate and top-up if required
                try:
                    self.control_pumps(sid, float(current_mmwg))
                except Exception:
                    pass

                self.safe_gui_update(lambda s=sid: self.show_info(
                    "Water change",
                    f"Tank {s}: drain complete. Auto top-up may now continue."
                ))
        except Exception:
            pass



    def control_pumps(self, sensor_id, water_level_mmwg):
        pump_name = "RO Pump A" if sensor_id == "A" else "RO Pump B"
        pump_frame = self.pump_frame_a if sensor_id == "A" else self.pump_frame_b
        pump_status_label = pump_frame["pump_status"]
        auto_top_up_label = pump_frame["auto_top_up_label"]
        auto_mode = pump_frame["auto_mode_var"].get()
        toggle_button = pump_frame["toggle_button"]

        # Auto Water Change hold: while draining, block auto top-up from engaging
        if getattr(self, "water_change_active", {}).get(sensor_id, False):
            # Ensure the top-up pump remains OFF while draining
            if self.pump_states.get(pump_name, False):
                try:
                    self.toggle_pump(
                        pump_name, pump_status_label, toggle_button,
                        force_state=False, suppress_auto_disable=True
                    )
                except Exception:
                    pass
            try:
                self.flash_water_change(auto_top_up_label, pump_name)
            except Exception:
                try:
                    auto_top_up_label.config(text="WATER CHANGE: DRAINING", fg="orange")
                except Exception:
                    pass
            return

        toggle_button = pump_frame["toggle_button"]

        # Get raw mmWG thresholds
        on_threshold = self.thresholds.get(sensor_id, {}).get("on", 10)
        off_threshold = self.thresholds.get(sensor_id, {}).get("off", 100)

        # Keep-Alive: reset the timer whenever the level hits/exceeds the max (off) threshold.
        try:
            if water_level_mmwg >= off_threshold:
                self.last_max_reached[pump_name] = time.time()
                # If a keep-alive cycle was pending, cancel it and clear messaging.
                if self.anti_idle_active.get(pump_name):
                    job = self.anti_idle_jobs.get(pump_name)
                    if job:
                        try:
                            self.root.after_cancel(job)
                        except Exception:
                            pass
                        self.anti_idle_jobs[pump_name] = None
                    self.anti_idle_active[pump_name] = False
                    try:
                        auto_top_up_label.config(text="", fg="black")
                    except Exception:
                        pass
        except Exception as _e:
            # Non-fatal: keep existing logic running
            pass

        # If user has manually overridden auto mode
        if self.override_states[pump_name]:
            if water_level_mmwg < on_threshold:
                print(f"[OVERRIDE RESET] Water level below threshold. Clearing manual override for {pump_name}.")
                self.override_states[pump_name] = False
            else:
                print(f"[OVERRIDE ACTIVE] Manual override blocking auto for {pump_name}.")
                return

        if auto_mode:
            if water_level_mmwg <= on_threshold and not self.pump_states[pump_name]:
                self.toggle_pump(pump_name, pump_status_label, toggle_button, force_state=True)
                self.flash_auto_top_up(auto_top_up_label, pump_name)

            elif water_level_mmwg >= off_threshold and self.pump_states[pump_name]:
                self.toggle_pump(pump_name, pump_status_label, toggle_button, force_state=False)
                auto_top_up_label.config(text="", fg="black")

        else:
            # Manual mode active
            if water_level_mmwg >= off_threshold and self.pump_states[pump_name]:
                print(f"[SAFETY] Manual mode overfill shutdown. Sensor: {sensor_id}, Reading: {water_level_mmwg:.2f}, Threshold: {off_threshold:.2f}")
                self.toggle_pump(pump_name, pump_status_label, toggle_button, force_state=False, suppress_auto_disable=True)
                auto_top_up_label.config(text="MAX LEVEL - SAFETY SHUTDOWN", fg="red")
                self.root.after(10000, lambda: auto_top_up_label.config(text="", fg="black"))
        # KEEP-ALIVE: brief power cycle to avoid 12h main system auto power-off
        try:
            if auto_mode:
                now = time.time()
                elapsed = now - self.last_max_reached.get(pump_name, now)
                if elapsed >= self.KEEPALIVE_WINDOW_SECS and not self.anti_idle_active.get(pump_name, False):
                    # Only cycle if pump is actually ON; otherwise there's nothing to "power cycle"
                    if self.pump_states.get(pump_name, False):
                        self.anti_idle_active[pump_name] = True
                        try:
                            auto_top_up_label.config(text="KEEP-ALIVE: cycling pump", fg="orange")
                        except Exception:
                            pass

                        # Turn OFF briefly without disabling Auto Mode
                        self.toggle_pump(pump_name, pump_status_label, toggle_button, force_state=False, suppress_auto_disable=True)

                        def _restore_power():
                            try:
                                # Restore only if Auto Mode is still enabled
                                frame = self.pump_frame_a if pump_name == "RO Pump A" else self.pump_frame_b
                                if frame["auto_mode_var"].get():
                                    if not self.pump_states.get(pump_name, False):
                                        self.toggle_pump(pump_name, pump_status_label, toggle_button, force_state=True, suppress_auto_disable=True)
                            finally:
                                # Reset timer and state either way
                                self.last_max_reached[pump_name] = time.time()
                                self.anti_idle_active[pump_name] = False
                                self.anti_idle_jobs[pump_name] = None
                                try:
                                    auto_top_up_label.config(text="", fg="black")
                                except Exception:
                                    pass

                        # Schedule power restore after 4 minutes
                        self.anti_idle_jobs[pump_name] = self.root.after(self.KEEPALIVE_OFF_MS, _restore_power)
        except Exception as _e:
            # Non-fatal: keep normal control flow
            pass

    def toggle_pump(self, pump_name, status_label=None, toggle_button=None, force_state=None, suppress_auto_disable=False):
        # Determine if this is a manual toggle
        user_override = force_state is None


        # Guard: if user tries to manually turn the pump ON while the associated tank is already at/above the
        # max (OFF) threshold, block the action and show the same "MAX LEVEL - SAFETY SHUTDOWN" message briefly.
        if force_state is None:
            try:
                current_state = bool(self.pump_states.get(pump_name, False))
                requested_on = not current_state
                if requested_on:
                    sensor_id = "A" if pump_name == "RO Pump A" else ("B" if pump_name == "RO Pump B" else None)
                    if sensor_id:
                        off_th = float(self.thresholds.get(sensor_id, {}).get("off", 100))
                        water_mmwg = self.cached_readings.get(sensor_id, {}).get("water_mmwg", None)
                        if water_mmwg is not None and float(water_mmwg) >= off_th:
                            # Ensure UI stays consistent
                            try:
                                if status_label:
                                    status_label.config(text="OFF", fg="red")
                                if toggle_button:
                                    toggle_button.config(text="Turn On")
                            except Exception:
                                pass

                            # Show warning on the correct pump frame label
                            try:
                                frame = self.pump_frame_a if pump_name == "RO Pump A" else self.pump_frame_b
                                lbl = frame.get("auto_top_up_label")
                                if lbl:
                                    lbl.config(text="MAX LEVEL - SAFETY SHUTDOWN", fg="red")
                                    self.root.after(10000, lambda l=lbl: l.config(text="", fg="black"))
                            except Exception:
                                pass

                            print(f"[SAFETY] Blocked manual ON at max level. Pump: {pump_name}, Sensor: {sensor_id}, Reading: {water_mmwg}, Off-threshold: {off_th}")
                            return
            except Exception:
                # Never block pump control due to an unexpected error in the guard
                pass

        if force_state is not None:
            self.pump_states[pump_name] = force_state
        else:
            self.pump_states[pump_name] = not self.pump_states[pump_name]

        pin = self.pump_gpio[pump_name]
        GPIO.output(pin, GPIO.HIGH if self.pump_states[pump_name] else GPIO.LOW)

        if status_label:
            status_label.config(
                text="ON" if self.pump_states[pump_name] else "OFF",
                fg="green" if self.pump_states[pump_name] else "red"
            )

        if toggle_button:
            toggle_button.config(
                text="Override" if self.pump_states[pump_name] else "Turn On"
            )

        if user_override:
            print(f"[OVERRIDE] User toggled pump '{pump_name}' manually, disabling auto mode.")
   
        # If manually turned OFF, disable auto mode checkbox
        if user_override and not self.pump_states[pump_name] and not suppress_auto_disable:
            print(f"[OVERRIDE] User cancelled pump '{pump_name}', disabling auto mode.")
            if pump_name == "RO Pump A":
                self.pump_frame_a["auto_mode_var"].set(False)
            elif pump_name == "RO Pump B":
                self.pump_frame_b["auto_mode_var"].set(False)
        
        # Turn relay 4 ON if either pump A or pump B is ON
        if self.pump_states.get("RO Pump A") or self.pump_states.get("RO Pump B"):
            GPIO.output(self.relay4_gpio, GPIO.HIGH)
        else:
            GPIO.output(self.relay4_gpio, GPIO.LOW)

        # Stop flashing regardless of pump state
        self.stop_flashing(pump_name)
        
    def create_tds_level_frame(self, title, row, column, colspan=1):
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=10)
        frame.grid(row=row, column=column, padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=int(getattr(self, "profile", {}).get("pady", 10)), sticky="nsew", columnspan=colspan)

        # Connection Status
        connection_status_label = tk.Label(frame, text="Status:", font=("Arial", 14, "bold"))
        connection_status_label.pack(anchor="n", pady=(5, 0))
        connection_status = tk.Label(frame, text="Disconnected!", font=("Arial", 14, "bold"), fg="red")
        connection_status.pack(anchor="n")

        # Readings
        tds_level_label = tk.Label(frame, text="TDS: -- ppm", font=("Arial", 14, "bold"), fg="purple")
        tds_level_label.pack(pady=6)
        
        temperature_label = tk.Label(frame, text="Temperature: --", font=("Arial", 14, "bold"), fg="red")
        temperature_label.pack(pady=6)

        cond_uScm_level_label = tk.Label(frame, text="Conductivity: -- µS/cm", font=("Arial", 14, "bold"), fg="green")
        cond_uScm_level_label.pack(pady=6)
  
        sal_level_label = tk.Label(frame, text="Salinity: -- PSU", font=("Arial", 14, "bold"), fg="orange")
        sal_level_label.pack(pady=6)

        # Settings Cog (top-right)
        self.attach_settings_cog(frame, command=self.open_tds_settings_popup)
 
        return {
            "frame": frame,
            "connection_status": connection_status,
            "tds_level_label": tds_level_label,
            "temperature_label": temperature_label,
            "cond_uScm_level_label": cond_uScm_level_label,
            "sal_level_label": sal_level_label,
        }

    def get_available_images(self):
        """Return sorted list of image filenames found in ./images."""
        try:
            images_dir = os.path.join(self._base_dir, "images")
            if not os.path.isdir(images_dir):
                return []
            exts = (".png", ".jpg", ".jpeg", ".gif")
            files = [f for f in os.listdir(images_dir) if f.lower().endswith(exts)]
            return sorted(files)
        except Exception:
            return []

    def refresh_image_frame_b(self):
        """Reload Image Frame B based on visual_settings[image_frame_b_file]."""
        try:
            # If the frame exists, destroy and recreate it in the same grid position
            if hasattr(self, "image_frame_b") and isinstance(self.image_frame_b, dict):
                old_frame = self.image_frame_b.get("frame")
                if old_frame is not None:
                    info = old_frame.grid_info()
                    row = int(info.get("row", 0))
                    col = int(info.get("column", 0))
                    colspan = int(info.get("columnspan", 1))
                    old_frame.destroy()
                    self.image_frame_b = self.create_image_frame_b("", row, col, colspan=colspan)
                    self.apply_theme()
                    return
        except Exception:
            pass
    def create_image_frame_b(self, title, row, column, colspan=1):
        frame = tk.LabelFrame(self.root, text=title or "", font=("Arial", 16, "bold"), padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=10)
        frame.grid(row=row, column=column, padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=int(getattr(self, "profile", {}).get("pady", 10)), sticky="nsew", columnspan=colspan)

        # Fixed fit 
        max_w, max_h = 350, 200

        try:
            from pathlib import Path as _P
            base_dir = _P(__file__).resolve().parent
            filename = self.visual_settings.get("image_frame_b_file", "image2.png")
            img_path = base_dir / "images" / filename
            if not img_path.exists():
                img_path = base_dir / filename  # fallback for legacy layouts

            if img_path.exists():
                original = tk.PhotoImage(file=str(img_path))
                # Integer downscale to fit within max_w x max_h while preserving aspect
                w, h = original.width(), original.height()
                factor_w = max(1, (w + max_w - 1) // max_w)
                factor_h = max(1, (h + max_h - 1) // max_h)
                factor = max(factor_w, factor_h)
                scaled = original.subsample(factor, factor) if factor > 1 else original

                img_label = tk.Label(frame, image=scaled)
                img_label.image = scaled  # Keep a reference
                img_label.pack(expand=True, anchor="center")
                return {"frame": frame, "image_label": img_label}
            else:
                tk.Label(frame, text=f"{filename} not found in images/", fg="red").pack()
        except Exception as e:
            tk.Label(frame, text=f"Image error: {e}", fg="red").pack()

        return {"frame": frame}

    def create_image_frame_c(self, title, row, column, colspan=1):
        frame = tk.LabelFrame(self.root, text=title or "", font=("Arial", 16, "bold"), padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=10)
        frame.grid(row=row, column=column, padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=int(getattr(self, "profile", {}).get("pady", 10)), sticky="nsew", columnspan=colspan)

        # Fixed fit 
        max_w, max_h = 350, 200

        try:
            from pathlib import Path as _P
            base_dir = _P(__file__).resolve().parent
            img_path = base_dir / "images" / "Stork_Logo.png"
            if not img_path.exists():
                img_path = base_dir / "Stork_Logo.png"  # fallback for legacy layouts

            if img_path.exists():
                original = tk.PhotoImage(file=str(img_path))
                # Integer downscale to fit within max_w x max_h while preserving aspect
                w, h = original.width(), original.height()
                factor_w = max(1, (w + max_w - 1) // max_w)
                factor_h = max(1, (h + max_h - 1) // max_h)
                factor = max(factor_w, factor_h)
                scaled = original.subsample(factor, factor) if factor > 1 else original

                img_label = tk.Label(frame, image=scaled)
                img_label.image = scaled  # keep a reference
                img_label.pack(expand=True, anchor="center")
                return {"frame": frame, "image_label": img_label}
            else:
                tk.Label(frame, text="Stork_Logo.png not found in MAIN/", fg="red").pack()
        except Exception as e:
            tk.Label(frame, text=f"Image error: {e}", fg="red").pack()

        return {"frame": frame}
    
    #Data Logging helper
    def _update_cached_reading(
        self,
        sensor_id: str,
        *,
        connected: bool,
        water_mmwg=None,
        temp_c=None,
        ph=None,
        tds_ppm=None,
        cond_uScm=None,
        sal_psu=None,
    ):
        # Single canonical cache schema for ALL sensors
        self.cached_readings[sensor_id] = {
            "ts": time.time(),
            "connected": bool(connected),

            "water_mmwg": water_mmwg,
            "temp_c": temp_c,
            "ph": ph,
            "tds_ppm": tds_ppm,
            "cond_uScm": cond_uScm,
            "sal_psu": sal_psu,
        }

    def _mark_sensor_disconnected_for_logging(self, sensor_id: str):
        self._update_cached_reading(sensor_id, connected=False)

    # ===== Email Settings Persistence (separate file: email.settings.json) =====
    def load_email_settings(self):
        """Load SMTP/email config from email.settings.json (if present)."""
        try:
            path = getattr(self, "_email_settings_path", None)
            if not path:
                return
            if os.path.exists(path):
                with open(path, "r") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self.email_settings.update(data)

            # Ensure required keys exist
            self.email_settings.setdefault("enabled", False)
            self.email_settings.setdefault("smtp_server", "")
            self.email_settings.setdefault("smtp_port", 587)
            self.email_settings.setdefault("use_tls", True)
            self.email_settings.setdefault("username", "")
            self.email_settings.setdefault("password", "")
            self.email_settings.setdefault("from_email", "")
            self.email_settings.setdefault("to_email", "")

            print("[EMAIL] Settings loaded.")
        except Exception as e:
            print(f"[EMAIL] Failed to load settings: {e}")

    def save_email_settings(self):
        """Save SMTP/email config to email.settings.json."""
        try:
            path = getattr(self, "_email_settings_path", None)
            if not path:
                return
            with open(path, "w") as f:
                json.dump(self.email_settings, f, indent=4)
            print("[EMAIL] Settings saved.")
        except Exception as e:
            print(f"[EMAIL] Failed to save settings: {e}")
    
    # Data Logging
    def _ensure_logs_dir(self):
        self.logs_dir = os.path.join(self._base_dir, "logs")
        os.makedirs(self.logs_dir, exist_ok=True)

    def _cleanup_old_logs(self, retention_days=30):
        try:
            if not hasattr(self, "logs_dir"):
                return
            today = datetime.date.today()
            cutoff = today - datetime.timedelta(days=int(retention_days))
            for filename in os.listdir(self.logs_dir):
                if not (filename.startswith("SAM_LOG_") and filename.endswith(".csv")):
                    continue
                try:
                    date_part = filename.replace("SAM_LOG_", "").replace(".csv", "")
                    file_date = datetime.datetime.strptime(date_part, "%Y-%m-%d").date()
                    if file_date < cutoff:
                        os.remove(os.path.join(self.logs_dir, filename))
                        print(f"[LOG CLEANUP] Deleted old log: {filename}")
                except Exception:
                    continue
        except Exception as e:
            print(f"[LOG CLEANUP] Failed: {e}")

    def _load_license(self):
        self.is_data_logging_licensed = False
        try:
            if os.path.exists(self._license_path):
                with open(self._license_path, "r") as f:
                    data = json.load(f)
                key = data.get("data_logging_key") or data.get("key") or ""
                dev = data.get("device_id") or ""
                if dev and dev == self.device_id and validate_license_key(key, self.device_id):
                    self.is_data_logging_licensed = True
        except Exception as e:
            print(f"[LICENSE] Failed to load license: {e}")
            self.is_data_logging_licensed = False
        print(f"[LICENSE] Data Logging licensed: {self.is_data_logging_licensed}")

    def _save_license(self, key: str):
        try:
            data = {
                "device_id": self.device_id,
                "data_logging_key": format_license_key(key),
                "licensed_features": {"data_logging": True},
                "created": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            with open(self._license_path, "w") as f:
                json.dump(data, f, indent=2)
            self.is_data_logging_licensed = True
        except Exception as e:
            print(f"[LICENSE] Failed to save license: {e}")
            self.is_data_logging_licensed = False
        print(f"[LICENSE] Data Logging licensed: {self.is_data_logging_licensed}")

    def _interval_to_ms(self, interval: str) -> int:
        mapping = {"30Sec": 30_000, "1Min": 60_000, "30Min": 1_800_000, "1Hour": 3_600_000}
        return int(mapping.get(interval, 60_000))

    def _log_filename_for_date(self, d: datetime.date) -> str:
        return os.path.join(self.logs_dir, f"SAM_LOG_{d.strftime('%Y-%m-%d')}.csv")

    def _ensure_daily_log_file(self):
        today = datetime.date.today()
        if self._log_current_date != today or not self._log_current_path:
            self._log_current_date = today
            self._log_current_path = self._log_filename_for_date(today)
            new_file = not os.path.exists(self._log_current_path)
            if new_file:
                with open(self._log_current_path, "w", newline="") as f:
                    w = csv.writer(f)
                    w.writerow(["timestamp_local","sensor_id","connected","water_mmwg","temp_c","ph","tds_ppm","conductivity_uScm","salinity_psu"])
            # cleanup at rollover too (Option C part 2)
            self._cleanup_old_logs(self.data_logging_settings.get("retention_days", 30))

    def start_logging(self):
        # Cancel any existing schedule and start fresh
        self.stop_logging()

        if not self.data_logging_settings.get("enabled", False):
            return
        if not self.is_data_logging_licensed:
            return

        # New generation: only this generation may reschedule itself
        self._log_gen += 1
        gen = self._log_gen

        self._ensure_daily_log_file()
        ms = self._interval_to_ms(self.data_logging_settings.get("interval", "1Min"))
        self._log_job = self.root.after(ms, lambda: self._log_tick(gen))

    def stop_logging(self):
        # Invalidate any in-flight tick so it can't reschedule
        self._log_gen += 1

        try:
            if self._log_job:
                self.root.after_cancel(self._log_job)
        except Exception:
            pass
        self._log_job = None

    def _should_log_sensor(self, sid: str) -> bool:
        if not self.data_logging_settings.get("sensors", {}).get(sid, True):
            return False

        frame_key_by_sensor = {
            "A": "Aquarium A",
            "B": "Aquarium B",
            "C": "RO Tank",
            "D": "pH Sensor",
            "E": "TDS Sensor",
        }
        frame_key = frame_key_by_sensor.get(sid)
        if frame_key and hasattr(self, "frame_visibility"):
            if not bool(self.frame_visibility.get(frame_key, True)):
                return False

        return True

    def _log_tick(self, gen):
        try:
            # If a newer start/stop happened, do not continue or reschedule
            if gen != getattr(self, "_log_gen", None):
                return

            if not (self.data_logging_settings.get("enabled", False) and self.is_data_logging_licensed):
                self.stop_logging()
                return

            self._ensure_daily_log_file()

            ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            rows = []

            for sid in ("A", "B", "C", "D", "E"):
                # Must be enabled + frame visible
                if not self._should_log_sensor(sid):
                    continue

                rd = self.cached_readings.get(sid)
                if not rd:
                    continue

                # Must be connected
                if not bool(rd.get("connected", False)):
                    continue

                # Must be fresh (not stale)
                age = time.time() - float(rd.get("ts", 0) or 0)
                if age > getattr(self, "LOG_STALE_SECONDS", 180):
                    continue

                # Connected column as yes/no (will always be "yes" due to the rules above)
                connected = "yes"

                water = rd.get("water_mmwg")
                temp  = rd.get("temp_c")
                ph    = rd.get("ph")
                tds   = rd.get("tds_ppm")
                cu    = rd.get("cond_uScm")
                sal   = rd.get("sal_psu")

                # Sensor E: only log optional fields if enabled in settings
                if sid == "E":
                    show_cfg = self.display_units.get("E", {}).get("show_fields", {})
                    if not bool(show_cfg.get("tds_ppm", True)):
                        tds = None
                    if not bool(show_cfg.get("cond_uScm", False)):
                        cu = None
                    if not bool(show_cfg.get("sal_psu", False)):
                        sal = None

                # RO tank temp: only if enabled
                if sid == "C":
                    if not bool(self.display_units.get("C", {}).get("r2_temp_enabled", False)):
                        temp = None

                rows.append([
                    ts, sid, connected,
                    "" if water is None else water,
                    "" if temp  is None else temp,
                    "" if ph    is None else ph,
                    "" if tds   is None else tds,
                    "" if cu    is None else cu,
                    "" if sal   is None else sal
                ])

            if rows:
                with open(self._log_current_path, "a", newline="") as f:
                    w = csv.writer(f)
                    w.writerows(rows)

        except Exception as e:
            print(f"[LOG] Tick error: {e}")

        finally:
            # Reschedule ONLY if this generation is still current
            try:
                if gen != getattr(self, "_log_gen", None):
                    return
                ms = self._interval_to_ms(self.data_logging_settings.get("interval", "1Min"))
                self._log_job = self.root.after(ms, lambda: self._log_tick(gen))
            except Exception:
                self._log_job = None

    def open_license_entry_popup(self, on_success=None):
        popup = tk.Toplevel(self.root)
        popup.title("Data Logging Licence")
        popup.attributes("-fullscreen", True)
        popup.transient(self.root)
        popup.focus_set()
        popup.lift()
        popup.attributes('-topmost', True)
        popup.bind("<Double-Button-1>", lambda event: popup.attributes("-fullscreen", not popup.attributes("-fullscreen")))

        container = tk.Frame(popup)
        container.pack(fill="both", expand=True, padx=20, pady=20)

        tk.Label(container, text="Data Logging is a locked feature.", font=("Arial", 16, "bold")).pack(pady=10)
        tk.Label(container, text="Device ID (send this to Stork Solutions):", font=("Arial", 12)).pack(pady=(20,5))
        tk.Label(container, text=self.device_id, font=("Courier", 14, "bold")).pack(pady=(0,20))

        tk.Label(container, text="Enter License Key:", font=("Arial", 12)).pack(pady=(10,5))
        key_var = tk.StringVar()
        tk.Entry(container, textvariable=key_var, font=("Courier", 16), justify="center").pack(pady=10)

        def submit():
            key = key_var.get()
            if validate_license_key(key, self.device_id):
                self._save_license(key)
                self.show_info("Licence Accepted", "Data Logging has been unlocked.")
                popup.destroy()
                if callable(on_success):
                    on_success()
            else:
                self.show_error("Invalid Key", "That licence key is not valid for this device.")

        tk.Button(container, text="Submit", command=submit).pack(pady=12)
        tk.Button(container, text="Close", command=popup.destroy).pack(pady=6)

        if self.visual_settings.get("dark_mode"):
            self.apply_theme(popup)
        
        # --- make sure window is mapped before modal grab ---
        popup.update_idletasks()
        popup.deiconify()
        popup.lift()

        try:
            popup.wait_visibility()  # blocks until window is viewable
        except Exception:
            pass

        def _try_grab():
            try:
                popup.grab_set()
                popup.focus_force()
            except tk.TclError:
                popup.after(50, _try_grab)

        popup.after(0, _try_grab)

    
    def open_data_logging_popup(self):
            popup = tk.Toplevel(self.root)
            popup.title("Data Logging")
            popup.attributes("-fullscreen", True)
            popup.transient(self.root)
            popup.grab_set()
            popup.focus_set()
            popup.lift()
            popup.attributes('-topmost', True)
            popup.bind("<Double-Button-1>", lambda event: popup.attributes("-fullscreen", not popup.attributes("-fullscreen")))

            outer = tk.Frame(popup); outer.pack(fill="both", expand=True)
            canvas = tk.Canvas(outer, highlightthickness=0); canvas.pack(side="left", fill="both", expand=True)
            sb = tk.Scrollbar(outer, orient="vertical", command=canvas.yview); sb.pack(side="right", fill="y")
            canvas.configure(yscrollcommand=sb.set)
            scroll = tk.Frame(canvas)
            win = canvas.create_window((0,0), window=scroll, anchor="nw")
            def on_conf(e):
                canvas.configure(scrollregion=canvas.bbox("all"))
                canvas.itemconfig(win, width=e.width)
            scroll.bind("<Configure>", on_conf)
            canvas.bind("<Configure>", on_conf)
            canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), "units"))
            canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
            canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

            container = tk.Frame(scroll)
            container.pack(fill="both", expand=True, padx=20, pady=20)

            tk.Label(container, text="Data Logging", font=("Arial", 18, "bold")).pack(pady=10)

            # Work on a copy until Submit is pressed
            current = dict(self.data_logging_settings or {})
            current.setdefault("enabled", False)
            current.setdefault("interval", "1Min")
            current.setdefault("sensors", {"A":True,"B":True,"C":True,"D":True,"E":True})
            current.setdefault("retention_days", 30)

            enabled_var = tk.BooleanVar(value=bool(current.get("enabled", False)))
            interval_var = tk.StringVar(value=current.get("interval", "1Min"))

            tk.Checkbutton(
                container,
                text="Enable Data Logging (Licensed)",
                variable=enabled_var,
                font=("Arial", 14)
            ).pack(pady=10)

            tk.Label(container, text="Logging Interval:", font=("Arial", 12, "bold")).pack(pady=(20,5))
            interval_box = ttk.Combobox(
                container,
                textvariable=interval_var,
                values=["30Sec","1Min","30Min","1Hour"],
                state="readonly",
                width=10
            )
            interval_box.pack(pady=5)

            tk.Label(container, text="Sensors to Log:", font=("Arial", 12, "bold")).pack(pady=(20,10))
            sens_frame = tk.Frame(container); sens_frame.pack(pady=5)
            sens_vars = {}
            for sid in ("A","B","C","D","E"):
                v = tk.BooleanVar(value=bool(current.get("sensors", {}).get(sid, True)))
                sens_vars[sid] = v
                tk.Checkbutton(sens_frame, text=f"Sensor {sid}", variable=v, font=("Arial", 12)).pack(anchor="w", pady=2)

            tk.Label(container, text="Retention: 30 days (auto cleanup)", fg="grey").pack(pady=(10,20))

            tk.Button(container, text="Manage Log Files", command=self.open_manage_logs_popup).pack(pady=8)

            btns = tk.Frame(container); btns.pack(pady=18)

            def apply_settings_and_close():
                # Build final settings from UI
                new_enabled = bool(enabled_var.get())
                new_interval = interval_var.get()
                new_sensors = {sid: bool(v.get()) for sid, v in sens_vars.items()}

                # If user is enabling logging and not licensed, require license entry
                if new_enabled and not self.is_data_logging_licensed:
                    def _after():
                        self.data_logging_settings["enabled"] = True
                        self.data_logging_settings["interval"] = new_interval
                        self.data_logging_settings["sensors"] = new_sensors
                        self.save_threshold_settings()
                        self.start_logging()
                        popup.destroy()
                    # keep checkbox visually enabled after success
                    self.open_license_entry_popup(on_success=_after)
                    return

                # Apply to live settings
                self.data_logging_settings["enabled"] = new_enabled
                self.data_logging_settings["interval"] = new_interval
                self.data_logging_settings["sensors"] = new_sensors
                self.save_threshold_settings()

                # Start/stop to match new settings
                if self.data_logging_settings.get("enabled", False) and self.is_data_logging_licensed:
                    self.start_logging()
                else:
                    self.stop_logging()

                popup.destroy()

            tk.Button(btns, text="Submit", command=apply_settings_and_close).pack(side="left", padx=12)
            tk.Button(btns, text="Cancel", command=popup.destroy).pack(side="left", padx=12)

            if self.visual_settings.get("dark_mode"):
                self.apply_theme(popup)


    def _list_log_files(self):
        try:
            files = [f for f in os.listdir(self.logs_dir) if f.startswith("SAM_LOG_") and f.endswith(".csv")]
            files.sort(reverse=True)
            return files
        except Exception:
            return []

    def _list_mount_points(self):
        mounts = []
        # Common Raspberry Pi removable locations
        for base in ("/media/pi", "/media", "/mnt"):
            try:
                if os.path.isdir(base):
                    for name in os.listdir(base):
                        p = os.path.join(base, name)
                        if os.path.ismount(p) or os.path.isdir(p):
                            mounts.append(p)
            except Exception:
                pass
        # Deduplicate
        out=[]
        for mnt in mounts:
            if mnt not in out:
                out.append(mnt)
        return out


    def _send_log_email(self, file_path: str, to_addr: str):
        """Send a log CSV as an email attachment using SMTP settings."""
        if not to_addr:
            raise ValueError("No recipient email address provided.")

        cfg = getattr(self, "email_settings", {}) or {}
        if not cfg.get("enabled", False):
            raise ValueError("Email is disabled in Email Settings.")

        smtp_server = (cfg.get("smtp_server") or "").strip()
        smtp_port = int(cfg.get("smtp_port") or 0)
        use_tls = bool(cfg.get("use_tls", True))
        username = (cfg.get("username") or "").strip()
        password = (cfg.get("password") or "")
        from_email = (cfg.get("from_email") or username).strip()

        if not smtp_server or not smtp_port:
            raise ValueError("SMTP server/port not configured.")
        if not from_email:
            raise ValueError("From email is not configured.")
        if not os.path.isfile(file_path):
            raise ValueError("Log file does not exist.")

        msg = EmailMessage()
        msg["Subject"] = f"SAM Log File: {os.path.basename(file_path)}"
        msg["From"] = from_email
        msg["To"] = to_addr
        msg.set_content("Attached is the requested SAM log file.")

        with open(file_path, "rb") as f:
            data = f.read()
        msg.add_attachment(data, maintype="text", subtype="csv", filename=os.path.basename(file_path))

        if use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(smtp_server, smtp_port, timeout=20) as s:
                s.ehlo()
                s.starttls(context=context)
                s.ehlo()
                if username:
                    s.login(username, password)
                s.send_message(msg)
        else:
            with smtplib.SMTP(smtp_server, smtp_port, timeout=20) as s:
                s.ehlo()
                if username:
                    s.login(username, password)
                s.send_message(msg)

    def open_email_settings_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("Email Settings")
        popup.attributes("-fullscreen", True)
        popup.transient(self.root)
        popup.grab_set()
        popup.focus_set()
        popup.lift()
        popup.attributes('-topmost', True)
        popup.bind("<Double-Button-1>", lambda event: popup.attributes("-fullscreen", not popup.attributes("-fullscreen")))

        # Scrollable layout (matches other popups)
        outer_frame = tk.Frame(popup); outer_frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer_frame, highlightthickness=0); canvas.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(outer_frame, orient="vertical", command=canvas.yview); scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollable_frame = tk.Frame(canvas)
        window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(window, width=event.width)

        scrollable_frame.bind("<Configure>", on_frame_configure)
        canvas.bind("<Configure>", on_frame_configure)

        # Mouse wheel / drag scroll
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

        def drag_start(event): canvas.scan_mark(event.x, event.y)
        def drag_motion(event): canvas.scan_dragto(event.x, event.y, gain=1)
        scrollable_frame.bind("<ButtonPress-1>", drag_start)
        scrollable_frame.bind("<B1-Motion>", drag_motion)
        canvas.bind("<ButtonPress-1>", drag_start)
        canvas.bind("<B1-Motion>", drag_motion)

        container = tk.Frame(scrollable_frame)
        container.pack(fill="both", expand=True, padx=20, pady=20)

        tk.Label(container, text="Email Settings", font=("Arial", 18, "bold")).pack(pady=10)

        enabled_var = tk.BooleanVar(value=bool(self.email_settings.get("enabled", False)))
        tls_var = tk.BooleanVar(value=bool(self.email_settings.get("use_tls", True)))
        server_var = tk.StringVar(value=self.email_settings.get("smtp_server", ""))
        port_var = tk.StringVar(value=str(self.email_settings.get("smtp_port", 587)))
        user_var = tk.StringVar(value=self.email_settings.get("username", ""))
        pass_var = tk.StringVar(value=self.email_settings.get("password", ""))
        from_var = tk.StringVar(value=self.email_settings.get("from_email", ""))
        to_var = tk.StringVar(value=self.email_settings.get("to_email", ""))

        tk.Checkbutton(container, text="Enable Email Sending", variable=enabled_var, font=("Arial", 14)).pack(anchor="w", pady=8)

        form = tk.Frame(container); form.pack(fill="x", pady=10)

        def row(label, var, show=None):
            r = tk.Frame(form); r.pack(fill="x", pady=6)
            tk.Label(r, text=label, width=18, anchor="w", font=("Arial", 12)).pack(side="left")
            e = tk.Entry(r, textvariable=var, font=("Arial", 12), show=show) if show else tk.Entry(r, textvariable=var, font=("Arial", 12))
            e.pack(side="left", fill="x", expand=True)
            return e

        row("SMTP Server:", server_var)
        row("SMTP Port:", port_var)
        tk.Checkbutton(container, text="Use TLS (STARTTLS)", variable=tls_var, font=("Arial", 12)).pack(anchor="w", pady=6)
        row("Username:", user_var)
        row("Password:", pass_var, show="*")
        row("From Email:", from_var)
        row("Default To:", to_var)

        btns = tk.Frame(container); btns.pack(pady=20)

        def on_submit():
            self.email_settings["enabled"] = bool(enabled_var.get())
            self.email_settings["smtp_server"] = server_var.get().strip()
            try:
                self.email_settings["smtp_port"] = int(port_var.get().strip() or "587")
            except Exception:
                self.email_settings["smtp_port"] = 587
            self.email_settings["use_tls"] = bool(tls_var.get())
            self.email_settings["username"] = user_var.get().strip()
            self.email_settings["password"] = pass_var.get()
            self.email_settings["from_email"] = from_var.get().strip()
            self.email_settings["to_email"] = to_var.get().strip()

            # Persist to separate file (email.settings.json)
            self.save_email_settings()
            self.show_info("Saved", "Email settings saved.")
            popup.destroy()

        tk.Button(btns, text="Submit", command=on_submit).pack(side="left", padx=10)
        tk.Button(btns, text="Cancel", command=popup.destroy).pack(side="left", padx=10)

        if self.visual_settings.get("dark_mode"):
            self.apply_theme(popup)

    def open_manage_logs_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("Manage Logs")
        popup.attributes("-fullscreen", True)
        popup.transient(self.root)
        popup.grab_set()
        popup.focus_set()
        popup.lift()
        popup.attributes('-topmost', True)
        popup.bind("<Double-Button-1>", lambda event: popup.attributes("-fullscreen", not popup.attributes("-fullscreen")))

        # Scrollable layout (matches other popups)
        outer_frame = tk.Frame(popup); outer_frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer_frame, highlightthickness=0); canvas.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(outer_frame, orient="vertical", command=canvas.yview); scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollable_frame = tk.Frame(canvas)
        window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(window, width=event.width)

        scrollable_frame.bind("<Configure>", on_frame_configure)
        canvas.bind("<Configure>", on_frame_configure)

        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

        def drag_start(event): canvas.scan_mark(event.x, event.y)
        def drag_motion(event): canvas.scan_dragto(event.x, event.y, gain=1)
        scrollable_frame.bind("<ButtonPress-1>", drag_start)
        scrollable_frame.bind("<B1-Motion>", drag_motion)
        canvas.bind("<ButtonPress-1>", drag_start)
        canvas.bind("<B1-Motion>", drag_motion)

        container = tk.Frame(scrollable_frame)
        container.pack(fill="both", expand=True, padx=20, pady=20)

        tk.Label(container, text="Manage Log Files", font=("Arial", 18, "bold")).pack(pady=10)

        files = self._list_log_files()
        file_var = tk.StringVar(value=files[0] if files else "")
        tk.Label(container, text="Select Log File:", font=("Arial", 12)).pack(pady=(20,5))
        file_box = ttk.Combobox(container, textvariable=file_var, values=files, state="readonly", width=30)
        file_box.pack(pady=5)

        mounts = self._list_mount_points()
        mount_var = tk.StringVar(value=mounts[0] if mounts else "")
        tk.Label(container, text="Download Destination (Mounted Drive):", font=("Arial", 12)).pack(pady=(20,5))
        mount_box = ttk.Combobox(container, textvariable=mount_var, values=mounts, state="readonly", width=45)
        mount_box.pack(pady=5)

        # Email destination
        tk.Label(container, text="Email Recipient:", font=("Arial", 12)).pack(pady=(20,5))
        to_var = tk.StringVar(value=self.email_settings.get("to_email", ""))
        to_entry = tk.Entry(container, textvariable=to_var, font=("Arial", 12), width=40)
        to_entry.pack(pady=5)

        def refresh():
            new = self._list_log_files()
            file_box["values"] = new
            if new:
                file_var.set(new[0])
            else:
                file_var.set("")

        def do_download():
            fname = file_var.get()
            if not fname:
                self.show_error("No file", "No log file selected.")
                return
            dest = mount_var.get()
            if not dest:
                self.show_error("No destination", "No mounted drive selected.")
                return
            try:
                out = self._copy_log_to_mount(fname, dest)
                self.show_info("Downloaded", f"Saved to: {out}")
            except Exception as e:
                self.show_error("Download failed", str(e))

        def do_delete():
            fname = file_var.get()
            if not fname:
                self.show_error("No file", "No log file selected.")
                return
            if self.data_logging_settings.get("enabled", False) and fname == os.path.basename(getattr(self, "_log_current_path", "")):
                self.show_error("In use", "This is the active log file. Disable logging before deleting it.")
                return

            if not self.show_confirm("Confirm Delete", f"Delete {fname}? This cannot be undone."):
                return
            try:
                os.remove(os.path.join(self.logs_dir, fname))
                self.show_info("Deleted", f"Deleted {fname}")
                refresh()
            except Exception as e:
                self.show_error("Delete failed", str(e))

        def do_email():
            fname = file_var.get()
            if not fname:
                self.show_error("No file", "No log file selected.")
                return
            to_addr = to_var.get().strip()
            if not to_addr:
                self.show_error("No recipient", "Please enter an email recipient.")
                return
            try:
                fpath = os.path.join(self.logs_dir, fname)
                self._email_log_file(fpath, to_addr)
                self.show_info("Sent", f"Emailed {fname} to {to_addr}")
            except Exception as e:
                self.show_error("Email failed", str(e))

        tk.Button(container, text="Refresh List", command=refresh).pack(pady=10)
        tk.Button(container, text="Download to Drive", command=do_download).pack(pady=8)
        tk.Button(container, text="Delete File", command=do_delete).pack(pady=8)
        tk.Button(container, text="Email Log File", command=do_email).pack(pady=8)
        tk.Button(container, text="Email Settings", command=self.open_email_settings_popup).pack(pady=8)
        tk.Button(container, text="Cancel", command=popup.destroy).pack(pady=12)

        if self.visual_settings.get("dark_mode"):
            self.apply_theme(popup)

    def open_settings_popup(self, sensor_id):
        popup = tk.Toplevel(self.root)
        popup.title(f"Settings for Sensor {sensor_id}")
        popup.attributes("-fullscreen", True)
        popup.transient(self.root)
        popup.grab_set()
        popup.focus_set()
        popup.lift()
        popup.attributes('-topmost', True)
        popup.bind("<Double-Button-1>", lambda event: popup.attributes("-fullscreen", not popup.attributes("-fullscreen")))

    
        outer_frame = tk.Frame(popup)
        outer_frame.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer_frame, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)

        scrollbar = tk.Scrollbar(outer_frame, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollable_frame = tk.Frame(canvas)
        window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(window, width=event.width)

        scrollable_frame.bind("<Configure>", on_frame_configure)
        canvas.bind("<Configure>", on_frame_configure)

        # Mouse wheel / drag scroll
        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))
        def drag_start(event): canvas.scan_mark(event.x, event.y)
        def drag_motion(event): canvas.scan_dragto(event.x, event.y, gain=1)
        scrollable_frame.bind("<ButtonPress-1>", drag_start)
        scrollable_frame.bind("<B1-Motion>", drag_motion)
        canvas.bind("<ButtonPress-1>", drag_start)
        canvas.bind("<B1-Motion>", drag_motion)

        # Content
        container = tk.Frame(scrollable_frame)
        container.pack(pady=30, padx=40, anchor="center")

        tk.Label(container, text=f"Settings for Sensor {sensor_id}", font=("Arial", 16, "bold")).pack(pady=10)

        fw = getattr(self, "sensor_firmware", {}).get(sensor_id, "UNKNOWN")
        tk.Label(
            container,
            text=f"Sensor Firmware: {fw}",
            font=("Arial", 12, "bold"),
            fg="red"
        ).pack(pady=(0, 12))

        # Sensor Connection (Serial vs Wi-Fi TCP)
        conn_frame = tk.LabelFrame(container, text="Sensor Connection")
        conn_frame.pack(fill="x", pady=(10, 6))

        ep = getattr(self, "endpoints", {}).get(sensor_id, {"type": "serial", "host": "", "port": 8888})
        conn_type_var = tk.StringVar(value=ep.get("type", "serial"))
        ip_var = tk.StringVar(value=ep.get("host", ""))  # Fixed port 8888

        def toggle_ip_state(*_):
            state = tk.NORMAL if conn_type_var.get() == "tcp" else tk.DISABLED
            ip_entry.config(state=state)

        tk.Radiobutton(conn_frame, text="Serial USB", value="serial", variable=conn_type_var,
                       command=toggle_ip_state).grid(row=0, column=0, padx=6, pady=4, sticky="w")
        tk.Radiobutton(conn_frame, text="Wi-Fi TCP", value="tcp", variable=conn_type_var,
                       command=toggle_ip_state).grid(row=0, column=1, padx=6, pady=4, sticky="w")

        tk.Label(conn_frame, text="IP Address:").grid(row=1, column=0, sticky="e", padx=6)
        ip_entry = tk.Entry(conn_frame, textvariable=ip_var, width=18)
        ip_entry.grid(row=1, column=1, sticky="w", padx=6)

        toggle_ip_state()

        # Units / thresholds
        use_liters_var = tk.BooleanVar(value=self.display_units[sensor_id].get("use_liters", False))
        use_gallons_var = tk.BooleanVar(value=self.display_units[sensor_id].get("use_gallons", False))
        use_fahrenheit_var = tk.BooleanVar(value=self.display_units[sensor_id].get("use_fahrenheit", False))

        tk.Checkbutton(container, text="Show Temperature in °F", variable=use_fahrenheit_var).pack(pady=5)

        # Temperature Alarm (mimics pH/TDS alarms)
        temp_alarm_var = tk.BooleanVar(value=self.display_units[sensor_id].get("temp_alarm_enabled", False))
        temp_min_var   = tk.StringVar(value=str(self.display_units[sensor_id].get("temp_min", 0)))
        temp_max_var   = tk.StringVar(value=str(self.display_units[sensor_id].get("temp_max", 0)))

        tk.Label(container, text="Temperature Alarm", font=("Arial", 12, "bold")).pack(pady=(10, 4))
        tk.Checkbutton(container, text="Enable Temperature Alarm", variable=temp_alarm_var).pack(pady=(0, 6))

        temp_row = tk.Frame(container)
        temp_row.pack(pady=(0, 8))
        tk.Label(temp_row, text="Min:").grid(row=0, column=0, padx=(0, 6))
        temp_min_entry = tk.Entry(temp_row, textvariable=temp_min_var, width=8)
        temp_min_entry.grid(row=0, column=1, padx=(0, 14))
        tk.Label(temp_row, text="Max:").grid(row=0, column=2, padx=(0, 6))
        temp_max_entry = tk.Entry(temp_row, textvariable=temp_max_var, width=8)
        temp_max_entry.grid(row=0, column=3)

        def _toggle_temp_fields(*_):
            st = tk.NORMAL if temp_alarm_var.get() else tk.DISABLED
            try:
                temp_min_entry.config(state=st)
                temp_max_entry.config(state=st)
            except Exception:
                pass

        temp_alarm_var.trace_add("write", _toggle_temp_fields)
        _toggle_temp_fields()


        def toggle_dim_fields():
            state = tk.NORMAL if (use_liters_var.get() or use_gallons_var.get()) else tk.DISABLED
            width_entry.config(state=state)
            depth_entry.config(state=state)

        def on_liters_toggle():
            if use_liters_var.get():
                use_gallons_var.set(False)
            toggle_dim_fields()

        def on_gallons_toggle():
            if use_gallons_var.get():
                use_liters_var.set(False)
            toggle_dim_fields()

        tk.Checkbutton(container, text="Display in Liters", variable=use_liters_var,
                       command=on_liters_toggle).pack(pady=2)
        tk.Checkbutton(container, text="Display in Gallons", variable=use_gallons_var,
                       command=on_gallons_toggle).pack(pady=2)

        tk.Label(container, text="(Required for Liter & Gallon Display)", font=("Arial", 10, "italic")).pack(pady=(10, 5))

        dims_row = tk.Frame(container)
        dims_row.pack(pady=(0, 10))

        tk.Label(dims_row, text="Width (cm):").grid(row=0, column=0, padx=(0, 18), sticky="w")
        tk.Label(dims_row, text="Depth (cm):").grid(row=0, column=1, sticky="w")

        width_entry = tk.Entry(dims_row, width=10)
        width_entry.grid(row=1, column=0, padx=(0, 18), sticky="w")

        depth_entry = tk.Entry(dims_row, width=10)
        depth_entry.grid(row=1, column=1, sticky="w")

        # Fill initial values
        width = self.display_units[sensor_id].get("width", 0)
        depth = self.display_units[sensor_id].get("depth", 0)
        width_entry.insert(0, str(width))
        depth_entry.insert(0, str(depth))


        tk.Label(container, text="Set Threshold", font=("Arial", 12, "bold")).pack(pady=(10, 4))

        thresh_row = tk.Frame(container)
        thresh_row.pack(pady=(0, 8))

        tk.Label(thresh_row, text="Min (Pump ON):").grid(row=0, column=0, padx=(0, 18), sticky="w")
        tk.Label(thresh_row, text="Max (Pump OFF):").grid(row=0, column=1, sticky="w")

        on_entry = tk.Entry(thresh_row, width=10)
        on_entry.grid(row=1, column=0, padx=(0, 18), sticky="w")

        off_entry = tk.Entry(thresh_row, width=10)
        off_entry.grid(row=1, column=1, sticky="w")

        on_mmwg = float(self.thresholds[sensor_id].get("on", 315))
        off_mmwg = float(self.thresholds[sensor_id].get("off", 336))

        if use_liters_var.get() and width > 0 and depth > 0:
            height_on_cm = on_mmwg / 10.0
            height_off_cm = off_mmwg / 10.0
            liters_on = height_on_cm * width * depth / 1000.0
            liters_off = height_off_cm * width * depth / 1000.0
            on_entry.insert(0, f"{liters_on:.2f}")
            off_entry.insert(0, f"{liters_off:.2f}")
        elif use_gallons_var.get() and width > 0 and depth > 0:
            height_on_cm = on_mmwg / 10.0
            height_off_cm = off_mmwg / 10.0
            liters_on = height_on_cm * width * depth / 1000.0
            liters_off = height_off_cm * width * depth / 1000.0
            gallons_on = liters_on * 0.264172
            gallons_off = liters_off * 0.264172
            on_entry.insert(0, f"{gallons_on:.2f}")
            off_entry.insert(0, f"{gallons_off:.2f}")
        else:
            on_entry.insert(0, f"{on_mmwg:.1f}")
            off_entry.insert(0, f"{off_mmwg:.1f}")

        toggle_dim_fields()

        # Save handler (writes thresholds, units, and connection)
        def save_thresholds():
            try:
                on_val = float(on_entry.get())
                off_val = float(off_entry.get())
                if on_val >= off_val:
                    raise ValueError("ON threshold must be less than OFF threshold.")

                w = float(width_entry.get() or 0)
                d = float(depth_entry.get() or 0)
                if (use_liters_var.get() or use_gallons_var.get()) and (w <= 0 or d <= 0):
                    raise ValueError("Width and Depth must be positive numbers.")

                # Warn if default thresholds used with volume units
                default_on, default_off = 315, 336
                if (use_liters_var.get() or use_gallons_var.get()) and on_val == default_on and off_val == default_off:
                    if not self.show_confirm(
                        "Default Thresholds Detected",
                        "You selected Liters/Gallons but left default thresholds.\n"
                        "Do you want to continue?"
                    ):
                        return

                # Convert to mmWG if volume selected
                if use_liters_var.get():
                    height_on_cm = (on_val * 1000.0) / (w * d)
                    height_off_cm = (off_val * 1000.0) / (w * d)
                    mmwg_on = height_on_cm * 10.0
                    mmwg_off = height_off_cm * 10.0
                elif use_gallons_var.get():
                    liters_on = on_val / 0.264172
                    liters_off = off_val / 0.264172
                    height_on_cm = (liters_on * 1000.0) / (w * d)
                    height_off_cm = (liters_off * 1000.0) / (w * d)
                    mmwg_on = height_on_cm * 10.0
                    mmwg_off = height_off_cm * 10.0
                else:
                    mmwg_on = on_val
                    mmwg_off = off_val

                # Persist thresholds / units
                self.thresholds[sensor_id]["on"] = mmwg_on
                self.thresholds[sensor_id]["off"] = mmwg_off
                self.display_units[sensor_id]["width"] = w
                self.display_units[sensor_id]["depth"] = d
                self.display_units[sensor_id]["use_liters"] = use_liters_var.get()
                self.display_units[sensor_id]["use_gallons"] = use_gallons_var.get()
                self.display_units[sensor_id]["use_fahrenheit"] = use_fahrenheit_var.get()

                # Temperature alarm settings
                self.display_units[sensor_id]["temp_alarm_enabled"] = temp_alarm_var.get()
                try:
                    self.display_units[sensor_id]["temp_min"] = float(temp_min_var.get())
                    self.display_units[sensor_id]["temp_max"] = float(temp_max_var.get())
                except Exception:
                    # Keep previous values if parse fails
                    pass

                if self.display_units[sensor_id].get("temp_alarm_enabled", False):
                    lo_t = self._num(self.display_units[sensor_id].get("temp_min"))
                    hi_t = self._num(self.display_units[sensor_id].get("temp_max"))
                    if lo_t is None or hi_t is None or lo_t >= hi_t:
                        raise ValueError("Temperature alarm Min must be less than Max.")

                # Persist connection choice (fixed port 8888)
                ct = conn_type_var.get()
                host = ip_var.get().strip()
                if ct == "tcp" and not host:
                     raise ValueError("Please enter an IP address for Wi-Fi TCP.")
                self.endpoints[sensor_id] = {"type": ct, "host": host, "port": 8888}

                self.save_threshold_settings()
                self.show_success_popup(f"Sensor {sensor_id} Updated")
                popup.destroy()

            except Exception as e:
                self.show_error("Invalid Input", str(e))

   
        tk.Button(container, text="Submit", command=save_thresholds).pack(pady=(15, 5))

        tk.Button(container, text="Cancel", command=popup.destroy).pack(pady=(0, 10))

        # Enable reset if the sensor is currently running (works for TCP/Serial)
        tk.Button(
            container,
             text="Reset Sensor",
             state=tk.NORMAL if self.sensors.get(sensor_id, {}).get("is_running") else tk.DISABLED,
             command=lambda: (self.reset_sensor(sensor_id), popup.destroy())
        ).pack(pady=10)
        # Tare Button
        tk.Button(container,
                 text="Tare Level (Zero mmWG)",
                 command=lambda sid=sensor_id, win=popup: self.tare_sensor(sid, win)
        ).pack(pady=8)
        tk.Button(container, text="Data Logging", command=lambda: (popup.destroy(), self.open_data_logging_popup())).pack(pady=10)

        tk.Button(container, text="Graphics", command=lambda: (popup.destroy(), self.open_graphics_popup())).pack(pady=10)

        if self.visual_settings.get("dark_mode"):
            self.apply_theme(popup)
       
    def toggle_dimension_fields():
        state = tk.NORMAL if use_liters_var.get() else tk.DISABLED
        width_entry.config(state=state)
        depth_entry.config(state=state)
 
    # Main settings save
    def save_threshold_settings(self):
        try:
            with open(self._settings_path, "w") as f:
                json.dump({
                    "thresholds": self.thresholds,
                    "screen_profile": getattr(self, "screen_profile", "4.3"),
                    "display_units": self.display_units,
                    #"graphics_settings": getattr(self, "graphics_settings", {})
                    "visual_settings": self.visual_settings,
                    "sensor_firmware": getattr(self, "sensor_firmware", {}),
                    "endpoints": getattr(self, "endpoints", {}),
                    "tare_offsets": getattr(self, "tare_offsets", {"A":0.0,"B":0.0,"C":0.0}),
                    "frame_positions": getattr(self, "frame_positions", {}),
                    "use_frame_positions": getattr(self, "use_frame_positions", True),
                    "frame_visibility": getattr(self, "frame_visibility", {}),
                    "data_logging": getattr(self, "data_logging_settings", {}),

                    "pump_auto_mode": {
                        "A": bool(getattr(getattr(self, "pump_frame_a", {}) or {}, "get", lambda k, d=None: d)("auto_mode_var", tk.BooleanVar(value=False)).get()),
                        "B": bool(getattr(getattr(self, "pump_frame_b", {}) or {}, "get", lambda k, d=None: d)("auto_mode_var", tk.BooleanVar(value=False)).get()),
                    },

                }, f, indent=4)
                print("[SAVE] Threshold and graphics settings saved.")
        except Exception as e:
            print(f"[SAVE ERROR] Failed to save settings: {e}")
   
    # Main settings loading      
    def load_threshold_settings(self):
        try:
            if os.path.exists(self._settings_path):
                with open(self._settings_path, "r") as f:
                    data = json.load(f)
                    self.thresholds.update(data.get("thresholds", {}))
                    self.display_units.update(data.get("display_units", {}))
                    # --- Backward-compatible defaults for new settings keys ---
                    for sid in ("A", "B"):
                        self.display_units.setdefault(sid, {})
                        du = self.display_units[sid]
                        du.setdefault("use_liters", False)
                        du.setdefault("use_gallons", False)
                        du.setdefault("width", 0)
                        du.setdefault("depth", 0)
                        du.setdefault("use_fahrenheit", False)
                        du.setdefault("temp_alarm_enabled", False)
                        du.setdefault("temp_min", 0)
                        du.setdefault("temp_max", 0)

                    # RO Tank
                    self.display_units.setdefault("C", {})
                    duC = self.display_units["C"]
                    duC.setdefault("use_liters", False)
                    duC.setdefault("use_gallons", False)
                    duC.setdefault("width", 0)
                    duC.setdefault("depth", 0)
                    duC.setdefault("capacity", 0)
                    duC.setdefault("level_alarm", False)
                    duC.setdefault("min_alarm", 0)
                    duC.setdefault("max_alarm", 0)
                    duC.setdefault("r2_temp_enabled", False)
                    duC.setdefault("temp_alarm_enabled", False)
                    duC.setdefault("temp_min", 0)
                    duC.setdefault("temp_max", 0)

                    # pH Sensor
                    self.display_units.setdefault("D", {})
                    duD = self.display_units["D"]
                    duD.setdefault("ph_alarm_enabled", False)
                    duD.setdefault("ph_min", 0)
                    duD.setdefault("ph_max", 0)
                    duD.setdefault("use_fahrenheit", False)
                    duD.setdefault("temp_alarm_enabled", False)
                    duD.setdefault("temp_min", 0)
                    duD.setdefault("temp_max", 0)

                    # TDS Sensor (E)
                    self.display_units.setdefault("E", {})
                    duE = self.display_units["E"]
                    duE.setdefault("tds_alarm_enabled", False)
                    duE.setdefault("tds_min", 0)
                    duE.setdefault("tds_max", 0)
                    duE.setdefault("use_fahrenheit", False)
                    duE.setdefault("temp_alarm_enabled", False)
                    duE.setdefault("temp_min", 0)
                    duE.setdefault("temp_max", 0)

                    duE.setdefault("show_fields", {
                        "tds_ppm": True,
                        "cond_uScm": False,
                        "sal_psu": False,
                    })

                    duE.setdefault("cond_alarm_enabled", False)
                    duE.setdefault("cond_min", 0)
                    duE.setdefault("cond_max", 0)
                    duE.setdefault("sal_alarm_enabled", False)
                    duE.setdefault("sal_min", 0)
                    duE.setdefault("sal_max", 0)

                    self.visual_settings.update(data.get("visual_settings", {}))
                    # Ensure image settings keys exist
                    self.visual_settings.setdefault("image_frame_b_file", "image2.png")
                    getattr(self, "sensor_firmware", {}).update(data.get("sensor_firmware", {}))
                    self.endpoints = data.get("endpoints", self.endpoints)
                    self.tare_offsets.update(data.get("tare_offsets", {"A":0.0, "B":0.0, "C":0.0}))
                    self.frame_positions.update(data.get("frame_positions", {}))
                    self.use_frame_positions = data.get("use_frame_positions", True)
                    self.screen_profile = data.get("screen_profile", getattr(self, "screen_profile", "4.3"))
                    if hasattr(self, "SCREEN_PROFILES") and self.screen_profile in self.SCREEN_PROFILES:
                        self.profile = self.SCREEN_PROFILES[self.screen_profile]
                        self.scale = max(0.7, min(1.6, float(self.profile.get("scale", 1.0))))
                    self.frame_visibility.update(data.get("frame_visibility", {}))
                    self.data_logging_settings.update(data.get("data_logging", {}))
                    # Ensure missing keys exist
                    self.data_logging_settings.setdefault("enabled", False)
                    self.data_logging_settings.setdefault("interval", "1Min")
                    self.data_logging_settings.setdefault("sensors", {"A":True,"B":True,"C":True,"D":True,"E":True})
                    self.data_logging_settings.setdefault("retention_days", 30)
                    # If enabled and licensed, start logging after UI init

                    self.graphics_settings = data.get("graphics_settings", {
                        "dark_mode": False,
                        "color_water": "#0000FF",
                        "color_temp": "#FF0000",
                        "color_ph": "#800080"
                    })
                    self.endpoints = data.get("endpoints", {
                        "A": {"type": "serial", "host": "", "port": 8888},
                        "B": {"type": "serial", "host": "", "port": 8888},
                        "C": {"type": "serial", "host": "", "port": 8888},
                        "D": {"type": "serial", "host": "", "port": 8888},
                        "E": {"type": "serial", "host": "", "port": 8888},
                    })

                    # Restore Pump Auto Mode (Enable Auto Mode checkbox)
                    try:
                        pam = data.get("pump_auto_mode", {}) or {}
                        a_val = pam.get("A", pam.get("RO Pump A", False))
                        b_val = pam.get("B", pam.get("RO Pump B", False))
                        try:
                            self.pump_frame_a["auto_mode_var"].set(bool(a_val))
                        except Exception:
                            pass
                        try:
                            self.pump_frame_b["auto_mode_var"].set(bool(b_val))
                        except Exception:
                            pass
                    except Exception:
                        pass

                    print("[LOAD] Threshold and graphics settings loaded.")
            else:
                self.graphics_settings = {
                    "dark_mode": False,
                    "color_water": "#0000FF",
                    "color_temp": "#FF0000",
                    "color_ph": "#800080",
                    "color_tds": "#800080"
                }
                print("[LOAD] No settings file found. Using defaults.")
        except Exception as e:
            print(f"[LOAD ERROR] Failed to load settings: {e}")

    def open_ro_settings_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("RO Tank Settings (Sensor C)")
        popup.attributes("-fullscreen", True)
        popup.transient(self.root)
        popup.grab_set()
        popup.focus_set()
        popup.lift()
        popup.attributes('-topmost', True)
        popup.bind("<Double-Button-1>", lambda event: popup.attributes("-fullscreen", not popup.attributes("-fullscreen")))

        # Scrollable layout
        outer_frame = tk.Frame(popup); outer_frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer_frame, highlightthickness=0); canvas.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(outer_frame, orient="vertical", command=canvas.yview); scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollable_frame = tk.Frame(canvas)
        window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(window, width=event.width)
        scrollable_frame.bind("<Configure>", on_frame_configure)
        canvas.bind("<Configure>", on_frame_configure)

        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

        def drag_start(event): canvas.scan_mark(event.x, event.y)
        def drag_motion(event): canvas.scan_dragto(event.x, event.y, gain=1)
        scrollable_frame.bind("<ButtonPress-1>", drag_start)
        scrollable_frame.bind("<B1-Motion>", drag_motion)
        canvas.bind("<ButtonPress-1>", drag_start)
        canvas.bind("<B1-Motion>", drag_motion)

        # Content
        container = tk.Frame(scrollable_frame)
        container.pack(pady=30, padx=40, anchor="center")

        sensor_id = "C"
        tk.Label(container, text=f"Settings for Sensor {sensor_id}", font=("Arial", 16, "bold")).pack(pady=10)

        fw = getattr(self, "sensor_firmware", {}).get(sensor_id, "UNKNOWN")
        tk.Label(
            container,
            text=f"Sensor Firmware: {fw}",
            font=("Arial", 12, "bold"),
            fg="red"
        ).pack(pady=(0, 12))

        # Sensor Connection (Serial vs Wi-Fi TCP)
        conn_frame = tk.LabelFrame(container, text="Sensor Connection")
        conn_frame.pack(fill="x", pady=(10, 6))

        ep = getattr(self, "endpoints", {}).get(sensor_id, {"type": "serial", "host": "", "port": 8888})
        conn_type_var = tk.StringVar(value=ep.get("type", "serial"))
        ip_var = tk.StringVar(value=ep.get("host", ""))  # Fixed port 8888

        def toggle_ip_state(*_):
            state = tk.NORMAL if conn_type_var.get() == "tcp" else tk.DISABLED
            ip_entry.config(state=state)

        tk.Radiobutton(conn_frame, text="Serial USB", value="serial", variable=conn_type_var,
                       command=toggle_ip_state).grid(row=0, column=0, padx=6, pady=4, sticky="w")
        tk.Radiobutton(conn_frame, text="Wi-Fi TCP", value="tcp", variable=conn_type_var,
                       command=toggle_ip_state).grid(row=0, column=1, padx=6, pady=4, sticky="w")

        tk.Label(conn_frame, text="IP Address:").grid(row=1, column=0, sticky="e", padx=6)
        ip_entry = tk.Entry(conn_frame, textvariable=ip_var, width=18)
        ip_entry.grid(row=1, column=1, sticky="w", padx=6)

        toggle_ip_state()

        # RO settings
        display_unit = self.display_units[sensor_id]
        
        r2_temp_var = tk.BooleanVar(value=display_unit.get("r2_temp_enabled", False))

        # Temperature (R2) enable toggle + dependent Temperature Alarm controls
        def _update_ro_temp_alarm_availability(*_):
            # If R2 temperature is disabled, force-disable temp alarm and grey out fields
            if not r2_temp_var.get():
                temp_alarm_var.set(False)
            _toggle_ro_temp_alarm_fields()

        def _on_enable_ro_temp_alarm():
            # Guard: can't enable temp alarm unless R2 temp is enabled
            if temp_alarm_var.get() and not r2_temp_var.get():
                self.show_info(
                    "Temperature Alarm",
                    "To use Temperature Alarms for the RO Tank, please enable "
                    "'Activate Temperature (R2 Sensors ONLY)' first."
                )
                temp_alarm_var.set(False)
            _toggle_ro_temp_alarm_fields()

        tk.Checkbutton(
            container,
            text="Activate Temperature (R2 Sensors ONLY)",
            variable=r2_temp_var,
            command=_update_ro_temp_alarm_availability
        ).pack(pady=(6, 2))

        # Temperature Alarm (only available when R2 temp is enabled)
        tk.Label(container, text="Temperature Alarm", font=("Arial", 12, "bold")).pack(pady=(14, 4))
        temp_alarm_var = tk.BooleanVar(value=display_unit.get("temp_alarm_enabled", False))
        temp_min_var = tk.StringVar(value=str(display_unit.get("temp_min", 0.0)))
        temp_max_var = tk.StringVar(value=str(display_unit.get("temp_max", 0.0)))

        temp_alarm_check = tk.Checkbutton(
            container,
            text="Enable Temperature Alarm",
            variable=temp_alarm_var,
            command=_on_enable_ro_temp_alarm
        )
        temp_alarm_check.pack(pady=(2, 8))

        temp_row = tk.Frame(container)
        temp_row.pack(pady=(2, 8))

        tmin_label = tk.Label(temp_row, text="Min:")
        tmin_label.grid(row=0, column=0, padx=(0, 6))
        tmin_entry = tk.Entry(temp_row, textvariable=temp_min_var, width=8)
        tmin_entry.grid(row=0, column=1, padx=(0, 14))

        tmax_label = tk.Label(temp_row, text="Max:")
        tmax_label.grid(row=0, column=2, padx=(0, 6))
        tmax_entry = tk.Entry(temp_row, textvariable=temp_max_var, width=8)
        tmax_entry.grid(row=0, column=3)
        def _toggle_ro_temp_alarm_fields():
            # Only usable if R2 temp is enabled AND temp alarm checked
            state = tk.NORMAL if (r2_temp_var.get() and temp_alarm_var.get()) else tk.DISABLED
            for w in (tmin_label, tmin_entry, tmax_label, tmax_entry):
                w.config(state=state)

        _toggle_ro_temp_alarm_fields()

        level_alarm_var = tk.BooleanVar(value=display_unit.get("level_alarm", False))
        use_liters_var = tk.BooleanVar(value=display_unit.get("use_liters", False))
        use_gallons_var = tk.BooleanVar(value=display_unit.get("use_gallons", False))

        width_var = tk.StringVar(value=str(display_unit.get("width", "")))
        depth_var = tk.StringVar(value=str(display_unit.get("depth", "")))
        min_level_var = tk.StringVar(value=str(display_unit.get("min_alarm", "")))
        max_level_var = tk.StringVar(value=str(display_unit.get("max_alarm", "")))

        tk.Checkbutton(container, text="Display in Liters", variable=use_liters_var,
                       command=lambda: (use_gallons_var.set(False), toggle_unit_fields())).pack(pady=2)
        tk.Checkbutton(container, text="Display in Gallons", variable=use_gallons_var,
                       command=lambda: (use_liters_var.set(False), toggle_unit_fields())).pack(pady=2)

        width_row = tk.Frame(container)
        width_row.pack(pady=(6, 8))

        width_label = tk.Label(width_row, text="Width (cm):")
        width_label.grid(row=0, column=0, padx=(0, 18), sticky="w")
        depth_label = tk.Label(width_row, text="Depth (cm):")
        depth_label.grid(row=0, column=1, sticky="w")

        width_entry = tk.Entry(width_row, textvariable=width_var, width=10)
        width_entry.grid(row=1, column=0, padx=(0, 18), sticky="w")
        depth_entry = tk.Entry(width_row, textvariable=depth_var, width=10)
        depth_entry.grid(row=1, column=1, sticky="w")
        alarm_check = tk.Checkbutton(container, text="Enable Level Alarm",
                                     variable=level_alarm_var, command=lambda: toggle_alarm_fields())
        alarm_check.pack(pady=10)

        tk.Label(container, text="Set Threshold", font=("Arial", 12, "bold")).pack(pady=(10, 4))

        thresh_row = tk.Frame(container)
        thresh_row.pack(pady=(0, 8))

        min_label = tk.Label(thresh_row, text="Min:")
        min_label.grid(row=0, column=0, padx=(0, 18), sticky="w")
        max_label = tk.Label(thresh_row, text="Max:")
        max_label.grid(row=0, column=1, sticky="w")

        min_entry = tk.Entry(thresh_row, textvariable=min_level_var, width=10)
        min_entry.grid(row=1, column=0, padx=(0, 18), sticky="w")
        max_entry = tk.Entry(thresh_row, textvariable=max_level_var, width=10)
        max_entry.grid(row=1, column=1, sticky="w")
        def toggle_unit_fields():
            state = tk.NORMAL if use_liters_var.get() or use_gallons_var.get() else tk.DISABLED
            width_entry.config(state=state); depth_entry.config(state=state)
        def toggle_alarm_fields():
            state = tk.NORMAL if level_alarm_var.get() else tk.DISABLED
            for w in (min_label, min_entry, max_label, max_entry): w.config(state=state)
        toggle_unit_fields(); toggle_alarm_fields()

        def save_ro_alarm_settings():
            try:
                # Persist connection choice (fixed 8888)
                ct = conn_type_var.get()
                host = ip_var.get().strip()
                if ct == "tcp" and not host:
                    raise ValueError("Please enter an IP address for Wi-Fi TCP.")
                self.endpoints[sensor_id] = {"type": ct, "host": host, "port": 8888}

                # RO settings save
                display_unit["level_alarm"] = level_alarm_var.get()
                display_unit["use_liters"] = use_liters_var.get()
                display_unit["use_gallons"] = use_gallons_var.get()
                display_unit["r2_temp_enabled"] = r2_temp_var.get()

                # Temperature alarm settings (only valid if R2 Temperature is enabled)
                if r2_temp_var.get():
                    display_unit["temp_alarm_enabled"] = temp_alarm_var.get()
                    # Store thresholds even if alarm disabled, so user doesn't lose inputs
                    try:
                        display_unit["temp_min"] = float(str(temp_min_var.get()).strip() or 0)
                    except Exception:
                        display_unit["temp_min"] = 0.0
                    try:
                        display_unit["temp_max"] = float(str(temp_max_var.get()).strip() or 0)
                    except Exception:
                        display_unit["temp_max"] = 0.0
                    # Basic validation if enabled
                    if display_unit.get("temp_alarm_enabled") and display_unit.get("temp_min", 0) >= display_unit.get("temp_max", 0):
                        raise ValueError("Temperature Alarm: Min Temperature must be less than Max Temperature.")
                else:
                    display_unit["temp_alarm_enabled"] = False
                
                # Show & Hide Label
                temp_lbl = self.ro_tank_frame.get("temperature_label")
                if temp_lbl:
                    if display_unit["r2_temp_enabled"]:
                        if not temp_lbl.winfo_ismapped():
                            temp_lbl.pack(pady=10)
                    else:
                        if temp_lbl.winfo_ismapped():
                            temp_lbl.pack_forget()
                        temp_lbl.config(text="Temperature: --")  # Reset text when hidden

                if use_liters_var.get() or use_gallons_var.get():
                    width = float(width_var.get()); depth = float(depth_var.get())
                    if width <= 0 or depth <= 0:
                        raise ValueError("Width and Depth must be positive numbers.")
                    display_unit["width"] = width; display_unit["depth"] = depth
                else:
                    display_unit["width"] = 0; display_unit["depth"] = 0

                if level_alarm_var.get():
                    min_val = float(min_level_var.get()); max_val = float(max_level_var.get())
                    if min_val >= max_val:
                        raise ValueError("Min level must be less than Max level.")
                    display_unit["min_alarm"] = min_val; display_unit["max_alarm"] = max_val
                else:
                    display_unit["min_alarm"] = 0; display_unit["max_alarm"] = 0
                    
                # If the RO alarm was just turned OFF, stop sound/flash and return to green
                if not level_alarm_var.get():
                    self._set_alarm_state("ro_tank", "normal", self.ro_tank_frame["connection_status"])

                self.save_threshold_settings()
                self.show_success_popup(f"Sensor {sensor_id} Updated")
                popup.destroy()
            except Exception as e:
                self.show_error("Invalid Input", str(e))

        tk.Button(container, text="Submit", command=save_ro_alarm_settings).pack(pady=(15, 5))

        tk.Button(container, text="Cancel", command=popup.destroy).pack(pady=(0, 10))
        tk.Button(
            container,
           text="Reset Sensor",
            state=tk.NORMAL if self.sensors.get(sensor_id, {}).get("is_running") else tk.DISABLED,
            command=lambda: (self.reset_sensor(sensor_id), popup.destroy())
        ).pack(pady=10)
        
        # Tare Button
        tk.Button(container,
                 text="Tare Level (Zero mmWG)",
                 command=lambda win=popup: self.tare_sensor("C", win)
        ).pack(pady=8)
        tk.Button(container, text="Data Logging", command=lambda: (popup.destroy(), self.open_data_logging_popup())).pack(pady=10)

        tk.Button(container, text="Graphics", command=lambda: (popup.destroy(), self.open_graphics_popup())).pack(pady=10)

        if self.visual_settings.get("dark_mode"):
            self.apply_theme(popup)
       
    def open_ph_settings_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("Settings for Sensor D")
        popup.attributes("-fullscreen", True)
        popup.transient(self.root)
        popup.grab_set()
        popup.focus_set()
        popup.lift()
        popup.attributes('-topmost', True)
        popup.bind("<Double-Button-1>", lambda event: popup.attributes("-fullscreen", not popup.attributes("-fullscreen")))

        # Scrollable layout
        outer_frame = tk.Frame(popup); outer_frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer_frame, highlightthickness=0); canvas.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(outer_frame, orient="vertical", command=canvas.yview); scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollable_frame = tk.Frame(canvas)
        window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(window, width=event.width)
        scrollable_frame.bind("<Configure>", on_frame_configure)
        canvas.bind("<Configure>", on_frame_configure)

        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))
        def drag_start(event): canvas.scan_mark(event.x, event.y)
        def drag_motion(event): canvas.scan_dragto(event.x, event.y, gain=1)
        scrollable_frame.bind("<ButtonPress-1>", drag_start)
        scrollable_frame.bind("<B1-Motion>", drag_motion)
        canvas.bind("<ButtonPress-1>", drag_start)
        canvas.bind("<B1-Motion>", drag_motion)

        # Content
        container = tk.Frame(scrollable_frame)
        container.pack(pady=40, padx=40, anchor="center")

        sensor_id = "D"
        tk.Label(container, text="Settings for Sensor D", font=("Arial", 16, "bold")).pack(pady=10)

        fw = getattr(self, "sensor_firmware", {}).get(sensor_id, "UNKNOWN")
        tk.Label(
            container,
            text=f"Sensor Firmware: {fw}",
            font=("Arial", 12, "bold"),
            fg="red"
        ).pack(pady=(0, 12))

        # Sensor Connection (Serial vs Wi-Fi TCP)
        conn_frame = tk.LabelFrame(container, text="Sensor Connection")
        conn_frame.pack(fill="x", pady=(10, 6))

        ep = getattr(self, "endpoints", {}).get(sensor_id, {"type": "serial", "host": "", "port": 8888})
        conn_type_var = tk.StringVar(value=ep.get("type", "serial"))
        ip_var = tk.StringVar(value=ep.get("host", ""))  # fixed 8888

        def toggle_ip_state(*_):
            state = tk.NORMAL if conn_type_var.get() == "tcp" else tk.DISABLED
            ip_entry.config(state=state)

        tk.Radiobutton(conn_frame, text="Serial USB", value="serial", variable=conn_type_var,
                       command=toggle_ip_state).grid(row=0, column=0, padx=6, pady=4, sticky="w")
        tk.Radiobutton(conn_frame, text="Wi-Fi TCP", value="tcp", variable=conn_type_var,
                       command=toggle_ip_state).grid(row=0, column=1, padx=6, pady=4, sticky="w")

        tk.Label(conn_frame, text="IP Address:").grid(row=1, column=0, sticky="e", padx=6)
        ip_entry = tk.Entry(conn_frame, textvariable=ip_var, width=18)
        ip_entry.grid(row=1, column=1, sticky="w", padx=6)

        toggle_ip_state()

        # pH settings
        settings = self.display_units.get(sensor_id, {})
        use_fahrenheit_var = tk.BooleanVar(value=settings.get("use_fahrenheit", False))
        tk.Checkbutton(container, text="Show Temperature in °F", variable=use_fahrenheit_var).pack(pady=5)

        # Temperature Alarm (mimics pH/TDS alarms)
        temp_alarm_var = tk.BooleanVar(value=settings.get("temp_alarm_enabled", False))
        temp_min_var   = tk.StringVar(value=str(settings.get("temp_min", 0)))
        temp_max_var   = tk.StringVar(value=str(settings.get("temp_max", 0)))

        tk.Checkbutton(container, text="Enable Temperature Alarm", variable=temp_alarm_var).pack(pady=(10, 2))
        temp_row = tk.Frame(container)
        temp_row.pack(pady=(2, 8))
        tk.Label(temp_row, text="Min:").grid(row=0, column=0, padx=(0, 6))
        temp_min_entry = tk.Entry(temp_row, textvariable=temp_min_var, width=8)
        temp_min_entry.grid(row=0, column=1, padx=(0, 14))
        tk.Label(temp_row, text="Max:").grid(row=0, column=2, padx=(0, 6))
        temp_max_entry = tk.Entry(temp_row, textvariable=temp_max_var, width=8)
        temp_max_entry.grid(row=0, column=3)

        def _toggle_temp_alarm_fields(*_):
            st = "normal" if temp_alarm_var.get() else "disabled"
            try:
                temp_min_entry.config(state=st)
                temp_max_entry.config(state=st)
            except Exception:
                pass
        temp_alarm_var.trace_add("write", _toggle_temp_alarm_fields)
        _toggle_temp_alarm_fields()

        enable_alarm_var = tk.BooleanVar(value=settings.get("ph_alarm_enabled", False))
        tk.Checkbutton(
            container,
            text="Enable pH Alarm",
            variable=enable_alarm_var,
            font=("Arial", 12)
        ).pack(pady=(10, 2))

        # pH alarm thresholds (match temperature/tds layout)
        min_ph_var = tk.StringVar(value=str(settings.get("ph_min", 0)))
        max_ph_var = tk.StringVar(value=str(settings.get("ph_max", 0)))

        ph_range_frame = tk.Frame(container)
        ph_range_frame.pack(pady=(2, 8))

        tk.Label(ph_range_frame, text="Min:").grid(row=0, column=0, padx=(0, 6), pady=2, sticky="e")
        min_entry = tk.Entry(ph_range_frame, textvariable=min_ph_var, width=10)
        min_entry.grid(row=0, column=1, padx=(0, 14), pady=2)

        tk.Label(ph_range_frame, text="Max:").grid(row=0, column=2, padx=(0, 6), pady=2, sticky="e")
        max_entry = tk.Entry(ph_range_frame, textvariable=max_ph_var, width=10)
        max_entry.grid(row=0, column=3, pady=2)

        def toggle_fields(*_):
            st = tk.NORMAL if enable_alarm_var.get() else tk.DISABLED
            try:
                min_entry.config(state=st)
                max_entry.config(state=st)
            except Exception:
                pass

        enable_alarm_var.trace_add("write", toggle_fields)
        toggle_fields()

        def save_ph_settings():
            try:
                # Persist connection choice (fixed 8888)
                ct = conn_type_var.get()
                host = ip_var.get().strip()
                if ct == "tcp" and not host:
                    raise ValueError("Please enter an IP address for Wi-Fi TCP.")
                self.endpoints[sensor_id] = {"type": ct, "host": host, "port": 8888}

                # Existing pH settings save
                if enable_alarm_var.get():
                    min_val = float(min_ph_var.get()); max_val = float(max_ph_var.get())
                    if min_val >= max_val:
                        raise ValueError("Minimum pH must be less than maximum pH.")
                    settings["ph_min"] = min_val; settings["ph_max"] = max_val
                else:
                    settings["ph_min"] = 0; settings["ph_max"] = 0

                settings["ph_alarm_enabled"] = enable_alarm_var.get()
                settings["use_fahrenheit"] = use_fahrenheit_var.get()

                # Temperature alarm settings
                settings["temp_alarm_enabled"] = temp_alarm_var.get()
                try:
                    settings["temp_min"] = float(temp_min_var.get())
                    settings["temp_max"] = float(temp_max_var.get())
                except Exception:
                    pass

                if settings.get("temp_alarm_enabled", False):
                    lo_t = self._num(settings.get("temp_min"))
                    hi_t = self._num(settings.get("temp_max"))
                    if lo_t is None or hi_t is None or lo_t >= hi_t:
                        raise ValueError("Temperature alarm Min must be less than Max.")
                
                # If the alarm was just turned OFF, immediately normalize UI + sound/flash
                if not settings["ph_alarm_enabled"]:
                    self._set_alarm_state("ph_sensor", "normal", self.ph_level_frame["connection_status"])

                # If alarm disabled, ensure UI not flashing
                if not settings["ph_alarm_enabled"]:
                    self.stop_flashing("pH Sensor")
                    self.safe_gui_update(lambda: (self.ph_level_frame["connection_status"].config(text="Connected", fg="green") if self.alarm_state.get("D_alarm","normal")=="normal" else None))

                self.save_threshold_settings()
                self.show_success_popup(f"Sensor {sensor_id} Updated")
                popup.destroy()
            except Exception as e:
                self.show_error("Invalid Input", str(e))

        tk.Button(container, text="Submit", command=save_ph_settings).pack(pady=(15, 5))

        tk.Button(container, text="Cancel", command=popup.destroy).pack(pady=(0, 10))
        tk.Button(
            container,
            text="Reset Sensor",
            state=tk.NORMAL if self.sensors.get(sensor_id, {}).get("is_running") else tk.DISABLED,
            command=lambda: (self.reset_sensor(sensor_id), popup.destroy())
        ).pack(pady=10)
        tk.Button(container, text="Data Logging", command=lambda: (popup.destroy(), self.open_data_logging_popup())).pack(pady=10)

        tk.Button(container, text="Graphics", command=lambda: (popup.destroy(), self.open_graphics_popup())).pack(pady=10)

        if self.visual_settings.get("dark_mode"):
            self.apply_theme(popup)
                      
    def open_tds_settings_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("Settings for Sensor E")
        popup.attributes("-fullscreen", True)
        popup.transient(self.root)
        popup.grab_set()
        popup.focus_set()
        popup.lift()
        popup.attributes('-topmost', True)
        popup.bind("<Double-Button-1>", lambda event: popup.attributes("-fullscreen", not popup.attributes("-fullscreen")))

        # Scrollable layout
        outer_frame = tk.Frame(popup); outer_frame.pack(fill="both", expand=True)
        canvas = tk.Canvas(outer_frame, highlightthickness=0); canvas.pack(side="left", fill="both", expand=True)
        scrollbar = tk.Scrollbar(outer_frame, orient="vertical", command=canvas.yview); scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollable_frame = tk.Frame(canvas)
        window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(window, width=event.width)
        scrollable_frame.bind("<Configure>", on_frame_configure)
        canvas.bind("<Configure>", on_frame_configure)

        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))
        def drag_start(event): canvas.scan_mark(event.x, event.y)
        def drag_motion(event): canvas.scan_dragto(event.x, event.y, gain=1)
        scrollable_frame.bind("<ButtonPress-1>", drag_start)
        scrollable_frame.bind("<B1-Motion>", drag_motion)
        canvas.bind("<ButtonPress-1>", drag_start)
        canvas.bind("<B1-Motion>", drag_motion)

        # Content
        container = tk.Frame(scrollable_frame)
        container.pack(pady=40, padx=40, anchor="center")

        sensor_id = "E"
        tk.Label(container, text="Settings for Sensor E", font=("Arial", 16, "bold")).pack(pady=10)

        fw = getattr(self, "sensor_firmware", {}).get(sensor_id, "UNKNOWN")
        tk.Label(
            container,
            text=f"Sensor Firmware: {fw}",
            font=("Arial", 12, "bold"),
            fg="red"
        ).pack(pady=(0, 12))

        # Sensor Connection (Serial vs Wi-Fi TCP)
        conn_frame = tk.LabelFrame(container, text="Sensor Connection")
        conn_frame.pack(fill="x", pady=(10, 6))

        ep = getattr(self, "endpoints", {}).get(sensor_id, {"type": "serial", "host": "", "port": 8888})
        conn_type_var = tk.StringVar(value=ep.get("type", "serial"))
        ip_var = tk.StringVar(value=ep.get("host", ""))  # fixed 8888

        def toggle_ip_state(*_):
            state = tk.NORMAL if conn_type_var.get() == "tcp" else tk.DISABLED
            ip_entry.config(state=state)

        tk.Radiobutton(conn_frame, text="Serial USB", value="serial", variable=conn_type_var,
                       command=toggle_ip_state).grid(row=0, column=0, padx=6, pady=4, sticky="w")
        tk.Radiobutton(conn_frame, text="Wi-Fi TCP", value="tcp", variable=conn_type_var,
                       command=toggle_ip_state).grid(row=0, column=1, padx=6, pady=4, sticky="w")

        tk.Label(conn_frame, text="IP Address:").grid(row=1, column=0, sticky="e", padx=6)
        ip_entry = tk.Entry(conn_frame, textvariable=ip_var, width=18)
        ip_entry.grid(row=1, column=1, sticky="w", padx=6)
        toggle_ip_state()

        settings = self.display_units.get(sensor_id, {})
        use_fahrenheit_var = tk.BooleanVar(value=settings.get("use_fahrenheit", False))
        tk.Checkbutton(container, text="Show Temperature in °F", variable=use_fahrenheit_var).pack(pady=5)

        # Temperature Alarm (mimics pH/TDS alarms)
        temp_alarm_var = tk.BooleanVar(value=settings.get("temp_alarm_enabled", False))
        temp_min_var   = tk.StringVar(value=str(settings.get("temp_min", 0)))
        temp_max_var   = tk.StringVar(value=str(settings.get("temp_max", 0)))

        tk.Checkbutton(container, text="Enable Temperature Alarm", variable=temp_alarm_var).pack(pady=(10, 2))
        temp_row = tk.Frame(container)
        temp_row.pack(pady=(2, 8))
        tk.Label(temp_row, text="Min:").grid(row=0, column=0, padx=(0, 6))
        temp_min_entry = tk.Entry(temp_row, textvariable=temp_min_var, width=8)
        temp_min_entry.grid(row=0, column=1, padx=(0, 14))
        tk.Label(temp_row, text="Max:").grid(row=0, column=2, padx=(0, 6))
        temp_max_entry = tk.Entry(temp_row, textvariable=temp_max_var, width=8)
        temp_max_entry.grid(row=0, column=3)

        def _toggle_temp_alarm_fields(*_):
            st = "normal" if temp_alarm_var.get() else "disabled"
            try:
                temp_min_entry.config(state=st)
                temp_max_entry.config(state=st)
            except Exception:
                pass
        temp_alarm_var.trace_add("write", _toggle_temp_alarm_fields)
        _toggle_temp_alarm_fields()

        enable_alarm_var = tk.BooleanVar(value=settings.get("tds_alarm_enabled", False))
        tk.Checkbutton(container, text="Enable TDS Alarm", variable=enable_alarm_var,
                       command=lambda: toggle_fields(), font=("Arial", 12)).pack(pady=10)

        # TDS alarm thresholds (preferred layout)
        min_tds_var = tk.StringVar(value=str(settings.get("tds_min", 0)))
        max_tds_var = tk.StringVar(value=str(settings.get("tds_max", 0)))

        tds_range_frame = tk.Frame(container)
        tds_range_frame.pack(pady=(5, 10))

        min_label = tk.Label(tds_range_frame, text="Min TDS:")
        min_label.grid(row=0, column=0, padx=(0, 6), pady=2, sticky="e")
        min_entry = tk.Entry(tds_range_frame, textvariable=min_tds_var, width=10)
        min_entry.grid(row=0, column=1, padx=(0, 14), pady=2)

        max_label = tk.Label(tds_range_frame, text="Max TDS:")
        max_label.grid(row=0, column=2, padx=(0, 6), pady=2, sticky="e")
        max_entry = tk.Entry(tds_range_frame, textvariable=max_tds_var, width=10)
        max_entry.grid(row=0, column=3, pady=2)

        # Conductivity alarm (requires conductivity reading enabled)
        enable_cond_alarm_var = tk.BooleanVar(value=settings.get("cond_alarm_enabled", False))
        cond_min_var = tk.StringVar(value=str(settings.get("cond_min", 0)))
        cond_max_var = tk.StringVar(value=str(settings.get("cond_max", 0)))

        def _toggle_cond_alarm_fields():
            state = "normal" if enable_cond_alarm_var.get() else "disabled"
            try:
                cond_min_entry.config(state=state)
                cond_max_entry.config(state=state)
            except Exception:
                pass

        def _on_cond_alarm_toggle():
            if enable_cond_alarm_var.get() and not uScm_var.get():
                self.show_info("Enable Conductivity Reading",
                                    "Please enable the Conductivity reading first (Display Mode section) before enabling Conductivity alarms.")
                enable_cond_alarm_var.set(False)
            _toggle_cond_alarm_fields()

        tk.Checkbutton(container, text="Enable Conductivity Alarm", variable=enable_cond_alarm_var,
                       command=_on_cond_alarm_toggle, font=("Arial", 12)).pack(pady=(10, 4))

        cond_range_frame = tk.Frame(container)
        cond_range_frame.pack(pady=(0, 10))

        tk.Label(cond_range_frame, text="Min Cond:").grid(row=0, column=0, padx=(0, 6), pady=2, sticky="e")
        cond_min_entry = tk.Entry(cond_range_frame, textvariable=cond_min_var, width=10)
        cond_min_entry.grid(row=0, column=1, padx=(0, 14), pady=2)

        tk.Label(cond_range_frame, text="Max Cond:").grid(row=0, column=2, padx=(0, 6), pady=2, sticky="e")
        cond_max_entry = tk.Entry(cond_range_frame, textvariable=cond_max_var, width=10)
        cond_max_entry.grid(row=0, column=3, pady=2)

        _toggle_cond_alarm_fields()

        # Salinity alarm (requires salinity reading enabled)
        enable_sal_alarm_var = tk.BooleanVar(value=settings.get("sal_alarm_enabled", False))
        sal_min_var = tk.StringVar(value=str(settings.get("sal_min", 0)))
        sal_max_var = tk.StringVar(value=str(settings.get("sal_max", 0)))

        def _toggle_sal_alarm_fields():
            state = "normal" if enable_sal_alarm_var.get() else "disabled"
            try:
                sal_min_entry.config(state=state)
                sal_max_entry.config(state=state)
            except Exception:
                pass

        def _on_sal_alarm_toggle():
            if enable_sal_alarm_var.get() and not sal_var.get():
                self.show_info("Enable Salinity Reading",
                                    "Please enable the Salinity reading first (Display Mode section) before enabling Salinity alarms.")
                enable_sal_alarm_var.set(False)
            _toggle_sal_alarm_fields()

        tk.Checkbutton(container, text="Enable Salinity Alarm", variable=enable_sal_alarm_var,
                       command=_on_sal_alarm_toggle, font=("Arial", 12)).pack(pady=(10, 4))

        sal_range_frame = tk.Frame(container)
        sal_range_frame.pack(pady=(0, 10))

        tk.Label(sal_range_frame, text="Min Sal:").grid(row=0, column=0, padx=(0, 6), pady=2, sticky="e")
        sal_min_entry = tk.Entry(sal_range_frame, textvariable=sal_min_var, width=10)
        sal_min_entry.grid(row=0, column=1, padx=(0, 14), pady=2)

        tk.Label(sal_range_frame, text="Max Sal:").grid(row=0, column=2, padx=(0, 6), pady=2, sticky="e")
        sal_max_entry = tk.Entry(sal_range_frame, textvariable=sal_max_var, width=10)
        sal_max_entry.grid(row=0, column=3, pady=2)

        _toggle_sal_alarm_fields()


        def toggle_fields():
            st = tk.NORMAL if enable_alarm_var.get() else tk.DISABLED
            for w in (min_label, min_entry, max_label, max_entry): w.config(state=st)
        toggle_fields()

        # Readings to show on the main tile (checkboxes)
        disp_grp = tk.LabelFrame(container, text="Readings to show on the main tile")
        disp_grp.pack(fill="x", padx=12, pady=10)

        show_cfg = self.display_units.get("E", {}).get("show_fields", {})
        tds_var   = tk.BooleanVar(value=show_cfg.get("tds_ppm", True))
        uScm_var  = tk.BooleanVar(value=show_cfg.get("cond_uScm", False))
        sal_var   = tk.BooleanVar(value=show_cfg.get("sal_psu", False))

        tk.Checkbutton(disp_grp, text="TDS (ppm)",            variable=tds_var).pack(anchor="w", padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=3)
        tk.Checkbutton(disp_grp, text="Conductivity (µS/cm)", variable=uScm_var).pack(anchor="w", padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=3)
        tk.Checkbutton(disp_grp, text="Salinity (PSU ≈ ppt)", variable=sal_var).pack(anchor="w", padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=3)

        # Submit / Reset / Graphics
        def save_tds_settings():
            try:
                # Persist connection choice (fixed port 8888)
                ct = conn_type_var.get()
                host = ip_var.get().strip()
                if ct == "tcp" and not host:
                    raise ValueError("Please enter an IP address for Wi-Fi TCP.")
                self.endpoints[sensor_id] = {"type": ct, "host": host, "port": 8888}

                # Alarms & units
                if enable_alarm_var.get():
                    min_val = float(min_tds_var.get())
                    max_val = float(max_tds_var.get())
                    if min_val >= max_val:
                        raise ValueError("Minimum TDS must be less than maximum TDS.")
                    settings["tds_min"] = min_val
                    settings["tds_max"] = max_val
                else:
                    settings["tds_min"] = 0
                    settings["tds_max"] = 0

                settings["tds_alarm_enabled"] = enable_alarm_var.get()

                # Conductivity & Salinity alarm settings (only meaningful if the readings are enabled)
                settings["cond_alarm_enabled"] = enable_cond_alarm_var.get()
                try:
                    settings["cond_min"] = float(cond_min_var.get())
                    settings["cond_max"] = float(cond_max_var.get())
                except Exception:
                    settings["cond_min"] = 0
                    settings["cond_max"] = 0

                settings["sal_alarm_enabled"] = enable_sal_alarm_var.get()
                try:
                    settings["sal_min"] = float(sal_min_var.get())
                    settings["sal_max"] = float(sal_max_var.get())
                except Exception:
                    settings["sal_min"] = 0
                    settings["sal_max"] = 0
                settings["use_fahrenheit"]   = use_fahrenheit_var.get()

                # Mirror °F preference + temp alarm values into display_units for immediate UI effect
                du = self.display_units.setdefault(sensor_id, {})
                du["use_fahrenheit"] = bool(use_fahrenheit_var.get())
                du["temp_alarm_enabled"] = bool(temp_alarm_var.get())
                try:
                    du["temp_min"] = float(temp_min_var.get())
                except Exception:
                    pass
                try:
                    du["temp_max"] = float(temp_max_var.get())
                except Exception:
                    pass


                # Temperature alarm settings
                settings["temp_alarm_enabled"] = temp_alarm_var.get()
                try:
                    settings["temp_min"] = float(temp_min_var.get())
                    settings["temp_max"] = float(temp_max_var.get())
                except Exception:
                    pass

                if settings.get("temp_alarm_enabled", False):
                    lo_t = self._num(settings.get("temp_min"))
                    hi_t = self._num(settings.get("temp_max"))
                    if lo_t is None or hi_t is None or lo_t >= hi_t:
                        raise ValueError("Temperature alarm Min must be less than Max.")

                # Temperature alarm settings
                settings["temp_alarm_enabled"] = temp_alarm_var.get()
                try:
                    settings["temp_min"] = float(temp_min_var.get())
                    settings["temp_max"] = float(temp_max_var.get())
                except Exception:
                    pass

                if settings.get("temp_alarm_enabled", False):
                    lo_t = self._num(settings.get("temp_min"))
                    hi_t = self._num(settings.get("temp_max"))
                    if lo_t is None or hi_t is None or lo_t >= hi_t:
                        raise ValueError("Temperature alarm Min must be less than Max.")

                # If alarm is OFF, normalize UI immediately
                if not settings["tds_alarm_enabled"]:
                    try:
                        self._set_alarm_state("tds_sensor", "normal", self.tds_level_frame["connection_status"])
                        self.stop_flashing("TDS Sensor")
                        self.safe_gui_update(lambda: (self.tds_level_frame["connection_status"].config(text="Connected", fg="green") if self.alarm_state.get("E_alarm","normal")=="normal" else None))
                    except Exception:
                        pass

                # Visibility checkboxes: persist which readings to show on tile
                du = self.display_units.setdefault(sensor_id, {})
                du["show_fields"] = {
                    "tds_ppm":   bool(tds_var.get()),
                    "cond_uScm": bool(uScm_var.get()),
                    "sal_psu":   bool(sal_var.get()),
                }

                # Persist to disk
                self.save_threshold_settings()

                # FORCE a layout pass by resetting the cache, then re-layout
                self.safe_gui_update(self.layout_tds_tile)

                try:
                    self.request_immediate_poll("E")
                except Exception:
                    pass

                self.show_success_popup(f"Sensor {sensor_id} Updated")
                popup.destroy()

            except ValueError as e:
                self.show_error("Invalid Input", str(e))
            except Exception as e:
                self.show_error("Error", f"Failed to save settings: {e}")

        tk.Button(container, text="Submit", command=save_tds_settings).pack(pady=(15, 5))

        tk.Button(container, text="Cancel", command=popup.destroy).pack(pady=(0, 10))
        tk.Button(
            container,
            text="Reset Sensor",
            state=tk.NORMAL if self.sensors.get(sensor_id, {}).get("is_running") else tk.DISABLED,
            command=lambda: (self.reset_sensor(sensor_id), popup.destroy())
        ).pack(pady=10)
        tk.Button(container, text="Data Logging", command=lambda: (popup.destroy(), self.open_data_logging_popup())).pack(pady=10)

        tk.Button(container, text="Graphics", command=lambda: (popup.destroy(), self.open_graphics_popup())).pack(pady=10)

        # Apply dark mode styling after successful build
        if self.visual_settings.get("dark_mode"):
            self.apply_theme(popup)
        
    def open_graphics_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("Graphics Settings")
        popup.attributes("-fullscreen", True)
        popup.transient(self.root)
        popup.grab_set()
        popup.focus_set()
        popup.lift()
        popup.attributes('-topmost', True)
        popup.bind("<Double-Button-1>", lambda event: popup.attributes("-fullscreen", not popup.attributes("-fullscreen")))
        dark_mode = self.visual_settings.get("dark_mode", False)

        outer_frame = tk.Frame(popup)
        outer_frame.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer_frame, highlightthickness=0)
        canvas.pack(side="left", fill="both", expand=True)

        scrollbar = tk.Scrollbar(outer_frame, orient="vertical", command=canvas.yview)
        scrollbar.pack(side="right", fill="y")
        canvas.configure(yscrollcommand=scrollbar.set)

        scrollable_frame = tk.Frame(canvas)
        canvas_window = canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfig(canvas_window, width=event.width)

        scrollable_frame.bind("<Configure>", on_frame_configure)
        canvas.bind("<Configure>", on_frame_configure)

        canvas.bind_all("<MouseWheel>", lambda e: canvas.yview_scroll(int(-1 * (e.delta / 120)), "units"))
        canvas.bind_all("<Button-4>", lambda e: canvas.yview_scroll(-1, "units"))
        canvas.bind_all("<Button-5>", lambda e: canvas.yview_scroll(1, "units"))

        def drag_start(event): canvas.scan_mark(event.x, event.y)
        def drag_motion(event): canvas.scan_dragto(event.x, event.y, gain=1)
        scrollable_frame.bind("<ButtonPress-1>", drag_start)
        scrollable_frame.bind("<B1-Motion>", drag_motion)
        canvas.bind("<ButtonPress-1>", drag_start)
        canvas.bind("<B1-Motion>", drag_motion)

        container = tk.Frame(scrollable_frame)
        container.pack(pady=40, padx=40, anchor="center")

        tk.Label(container, text="Graphics Settings", font=("Arial", 18, "bold")).pack(pady=(10, 20))

        self.dark_mode_var = tk.BooleanVar(value=self.visual_settings.get("dark_mode", False))
        tk.Checkbutton(container, text="Enable Dark Mode", variable=self.dark_mode_var).pack(pady=5)

        self.color_vars = {
            "water": tk.StringVar(value=self.visual_settings.get("colors", {}).get("water", "#0000FF")),
            "temp": tk.StringVar(value=self.visual_settings.get("colors", {}).get("temp", "#FF0000")),
            "ph": tk.StringVar(value=self.visual_settings.get("colors", {}).get("ph", "#00FF00")),
            "tds": tk.StringVar(value=self.visual_settings.get("colors", {}).get("tds", "#A52A2A")),
            "cond": tk.StringVar(value=self.visual_settings.get("colors", {}).get("cond", "#00FF00")),
            "sal": tk.StringVar(value=self.visual_settings.get("colors", {}).get("sal", "#FFA500")),
        }

        palette_colors = [
            "#0000FF", "#00FFFF", "#00FF00", "#FFFF00",
            "#FFA500", "#FF0000", "#800080", "#FFC0CB",
            "#A52A2A", "#808080", "#000000", "#FFFFFF"
        ]

        button_refs = {"water": {}, "temp": {}, "ph": {}, "tds": {}, "cond": {}, "sal": {}}

        def update_highlight(color_key):
            selected = self.color_vars[color_key].get()
            for color, btn in button_refs[color_key].items():
                 btn.config(
                    relief=tk.SUNKEN if color == selected else tk.RAISED,
                    highlightthickness=3 if color == selected else 0
                )

        def create_color_picker_section(label_text, color_key):
            tk.Label(container, text=label_text, font=("Arial", 12, "bold")).pack(pady=(20, 5))
            grid_frame = tk.Frame(container)
            grid_frame.pack(pady=4)

            current_color = self.color_vars[color_key].get()

            def set_color(selected_color):
                self.color_vars[color_key].set(selected_color)
                update_highlight(color_key)

            for i, color in enumerate(palette_colors):
                row = i // 4
                col = i % 4

                square = tk.Canvas(grid_frame, width=40, height=40, highlightthickness=3)
                square.grid(row=row, column=col, padx=4, pady=4)

                # Draw rectangle with color
                square.create_rectangle(2, 2, 38, 38, fill=color, outline=color)

                # Save reference
                button_refs[color_key][color] = square

                def make_click_handler(c=color):
                     return lambda e: set_color(c)

                square.bind("<Button-1>", make_click_handler())

            def update_highlight(c_key):
                selected = self.color_vars[c_key].get()
                for color, square in button_refs[c_key].items():
                    border_color = "red" if color == selected else ("white" if self.visual_settings.get("dark_mode") else "black")
                    square.configure(highlightbackground=border_color)

            update_highlight(color_key)

        create_color_picker_section("Water Reading Color", "water")
        create_color_picker_section("Temperature Reading Color", "temp")
        create_color_picker_section("pH Reading Color", "ph")
        create_color_picker_section("TDS Reading Color", "tds")
        create_color_picker_section("Conductivity Reading Color", "cond")
        create_color_picker_section("Salinity Reading Color", "sal")
        
        # Image selection (Frame B only)
        available_images = self.get_available_images()
        self.image_frame_b_var = tk.StringVar(value=self.visual_settings.get("image_frame_b_file", "image2.png"))
        tk.Label(container, text="Change Image In Frame", font=("Arial", 12, "bold")).pack(pady=(25, 5))
        self.image_frame_b_combo = ttk.Combobox(
            container,
            textvariable=self.image_frame_b_var,
            values=available_images,
            state="readonly"
        )
        self.image_frame_b_combo.pack(pady=5)
        if available_images and self.image_frame_b_var.get() not in available_images:
            self.image_frame_b_var.set(available_images[0])

        # User Adjustable Frames
        layout_box = tk.LabelFrame(container, text="Frame Layout (Positions)")
        layout_box.pack(fill="x", padx=12, pady=(30, 10))

        use_positions_var = tk.BooleanVar(value=getattr(self, "use_frame_positions", True))
        tk.Checkbutton(layout_box, text="Enable Custom Frame Layout", variable=use_positions_var).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=(8, 10)
        )
        tk.Label(layout_box, text="Enabled").grid(row=0, column=5, sticky="w", padx=(0, 10), pady=(8, 10))

        frame_names = [
            "Aquarium A", "RO Pump A", "pH Sensor",
            "Aquarium B", "RO Pump B", "RO Tank",
            "TDS Sensor", "RPi Image", "www.stork.solutions"
        ]

        vis_vars = {}
        pos_vars = {}
        for i, name in enumerate(frame_names, start=1):
            pos = self.frame_positions.get(name, {"row": 0, "col": 0})
            r_var = tk.IntVar(value=int(pos.get("row", 0)))
            c_var = tk.IntVar(value=int(pos.get("col", 0)))
            pos_vars[name] = (r_var, c_var)
            
            v_var = tk.BooleanVar(value=self.frame_visibility.get(name, True))
            vis_vars[name] = v_var

            tk.Label(layout_box, text=name).grid(row=i, column=0, sticky="w", padx=int(getattr(self, "profile", {}).get("outer_pad", 10)), pady=4)
            tk.Label(layout_box, text="Row").grid(row=i, column=1, sticky="e", padx=(10, 2))
            tk.Spinbox(layout_box, from_=0, to=9, width=4, textvariable=r_var).grid(row=i, column=2, sticky="w", padx=(0, 10))
            tk.Label(layout_box, text="Col").grid(row=i, column=3, sticky="e", padx=(10, 2))
            tk.Spinbox(layout_box, from_=0, to=9, width=4, textvariable=c_var).grid(row=i, column=4, sticky="w", padx=(0, 10))
            tk.Checkbutton(layout_box, variable=v_var).grid(row=i, column=5, sticky="w", padx=(0, 10))

        # Button Row
        buttons_frame = tk.Frame(container)
        buttons_frame.pack(pady=(30, 60), anchor="center")

        restore_btn = tk.Button(
            buttons_frame,
            text="Set Default",
            font=("Arial", 12, "bold"),
            width=9,
            height=1,
            command=lambda: (
                self.dark_mode_var.set(False),
                self.color_vars["water"].set("#0000FF"),
                self.color_vars["temp"].set("#FF0000"),
                self.color_vars["ph"].set("#00FF00"),
                self.color_vars["tds"].set("#A52A2A"),
                self.color_vars["cond"].set("#00FF00"),
                self.color_vars["sal"].set("#FFA500"),
                update_highlight("water"),
                update_highlight("temp"),
                update_highlight("ph"),
                update_highlight("tds"),
                update_highlight("cond"),
                update_highlight("sal")
            )
        )
        restore_btn.grid(row=0, column=0, padx=(0, 20))

        def apply_graphics_changes():
            self.visual_settings.update({
                "dark_mode": self.dark_mode_var.get(),
                "colors": {
                    "water": self.color_vars["water"].get(),
                    "temp": self.color_vars["temp"].get(),
                    "ph": self.color_vars["ph"].get(),
                    "tds": self.color_vars["tds"].get(),
                    "cond": self.color_vars["cond"].get(),
                    "sal": self.color_vars["sal"].get(),
                }
            })
            # Save image selection (Frame B) and refresh that frame immediately
            try:
                self.visual_settings["image_frame_b_file"] = self.image_frame_b_var.get()
                self.refresh_image_frame_b()
            except Exception:
                pass
          
            # Apply frame layout choices
            self.use_frame_positions = use_positions_var.get()

            for name, (rv, cv) in pos_vars.items():
                self.frame_positions.setdefault(name, {})
                self.frame_positions[name]["row"] = int(rv.get())
                self.frame_positions[name]["col"] = int(cv.get())
                # Keep existing colspan if present
                self.frame_positions[name].setdefault("colspan", 1)
            for name, vvar in vis_vars.items():
                self.frame_visibility[name] = bool(vvar.get())
                
            self.save_threshold_settings()
            self.apply_theme()
            # Re-apply layout immediately so changes are visible
            try:
                if self.use_frame_positions:
                    self.apply_frame_positions_layout()
                else:
                    self.reflow_grid()
            except Exception:
                pass

            popup.destroy()

        submit_btn = tk.Button(
            buttons_frame,
            text="Submit",
            font=("Arial", 12, "bold"),
            width=9,
            height=1,
            command=apply_graphics_changes
        )

        submit_btn.grid(row=0, column=1)
        
        # Check updates button
        update_btn = tk.Button(
            buttons_frame,
            text="Check for Updates",
            font=("Arial", 12, "bold"),
            width=19,          # Spans roughly same width as 2x width=9 buttons
            height=1,
            command=self.ui_check_gui_update
        )

        update_btn.grid(
            row=1,
            column=0,
            columnspan=2,      
            padx=5,
            pady=(10, 0)
        )


        # Cancel button (below Check for Updates)
        cancel_btn = tk.Button(
            buttons_frame,
            text="Cancel",
            font=("Arial", 12, "bold"),
            width=19,
            height=1,
            command=popup.destroy
        )

        cancel_btn.grid(
            row=2,
            column=0,
            columnspan=2,
            padx=5,
            pady=(10, 0)
        )
        self.apply_theme(popup)
   
    def apply_reading_colors(self):
        try:
            colors = self.visual_settings.get("colors", {})
            water_color = colors.get("water", "#0000FF")
            temp_color = colors.get("temp", "#FF0000")
            ph_color = colors.get("ph", "#800080")
            tds_color = colors.get("tds", "A52A2A")
            cond_color  = colors.get("cond",  "#00FF00")
            sal_color   = colors.get("sal",   "#FFA500")

            # Sensor A & B
            self.aquarium_frame_1["water_gauge_label"].config(fg=water_color)
            self.aquarium_frame_1["temperature_label"].config(fg=temp_color)
            self.aquarium_frame_2["water_gauge_label"].config(fg=water_color)
            self.aquarium_frame_2["temperature_label"].config(fg=temp_color)

            # RO Tank
            self.ro_tank_frame["water_gauge_label"].config(fg=water_color)
            self.ro_tank_frame["temperature_label"].config(fg=temp_color)

            # pH Sensor
            self.ph_level_frame["ph_level_label"].config(fg=ph_color)
            self.ph_level_frame["temperature_label"].config(fg=temp_color)
            
            # TDS Sensor
            self.tds_level_frame["tds_level_label"].config(fg=tds_color)
            self.tds_level_frame["temperature_label"].config(fg=temp_color)
            if "cond_uScm_level_label" in self.tds_level_frame:
                self.tds_level_frame["cond_uScm_level_label"].config(fg=cond_color)
            if "sal_level_label" in self.tds_level_frame:
                self.tds_level_frame["sal_level_label"].config(fg=sal_color)

            for frame in [
                self.aquarium_frame_1,
                self.aquarium_frame_2,
                self.ro_tank_frame,
                self.ph_level_frame,
                self.tds_level_frame
            ]:
                try:
                    lbl = frame.get("connection_status")
                    if not lbl:
                        continue

                    status_text = lbl.cget("text").lower()

                    if "disconnected" in status_text:
                        lbl.config(fg="red")
                    elif "connected" in status_text:
                        lbl.config(fg="green")
                    else:
                        # Fallback to theme text colour
                        lbl.config(fg=self.text_color)

                except Exception:
                    pass

        except Exception as e:
            print(f"[COLOR ERROR] Failed to apply updated colors: {e}")
       
    def apply_theme(self, target=None):
        try:
            dark = self.visual_settings.get("dark_mode", False)

            bg = "#2E2E2E" if dark else "#F0F0F0"
            fg = "#FFFFFF" if dark else "black"
            entry_bg = "#3C3C3C" if dark else "white"
            entry_fg = "#FFFFFF" if dark else "black"

            def style_widget(w):
                try:
                    wtype = w.winfo_class()

                    if wtype in ["Frame", "LabelFrame", "Toplevel"]:
                        w.configure(bg=bg)
                    elif wtype == "Canvas":
                        w.configure(bg=bg)
                    elif wtype == "Label":
                        w.configure(bg=bg, fg=fg)
                    elif wtype == "Button":
                        w.configure(
                            bg=bg,
                            fg=fg,
                            activebackground=bg,
                            activeforeground=fg,
                            relief=tk.RAISED,
                            highlightthickness=0,
                            borderwidth=2
                        )
                    elif wtype == "Checkbutton":
                        w.configure(
                            bg=bg,
                            fg=fg,
                            activebackground=bg,
                            activeforeground=fg,
                            selectcolor=bg
                        )
                    elif wtype == "Radiobutton":
                        w.configure(
                            bg=bg, fg=fg,
                            activebackground=bg, activeforeground=fg,
                            selectcolor=bg,        
                            highlightthickness=0   
                        )
                    elif wtype == "Entry":
                        w.configure(bg=entry_bg, fg=entry_fg, insertbackground=fg)
                    elif wtype == "Scrollbar":
                        w.configure(bg=bg)
                    elif wtype == "Labelframe" or wtype == "LabelFrame":
                        w.configure(bg=bg, fg=fg)
                       
                except Exception as e:
                    print(f"[THEME ERROR] {w.winfo_class()}: {e}")

                for child in w.winfo_children():
                    style_widget(child)

            targets = [target] if target else [self.root] + self.root.winfo_children()
            for win in targets:
                try:
                    win.configure(bg=bg)
                    style_widget(win)
                except Exception as e:
                    print(f"[WINDOW ERROR] {e}")
            self.update_all_pump_status_colors()
            self.apply_reading_colors()
            # Refresh theme-aware icons
            self._refresh_water_change_button_icons()
            print(f"[THEME] {'Dark' if dark else 'Light'} mode applied.")
        except Exception as e:
            print(f"[THEME ERROR] {e}")
    
    def show_success_popup(self, message):
        """Show a themed 'Success' popup depending on dark mode setting."""
        if self.visual_settings.get("dark_mode", False):
            popup = tk.Toplevel(self.root)
            popup.title("Success")
            popup.transient(self.root)
            popup.grab_set()
            popup.attributes("-topmost", True)
 
            body = tk.Frame(popup, bg="#2E2E2E", padx=20, pady=16)
            body.pack(fill="both", expand=True)

            tk.Label(
                body,
                text=message,
                bg="#2E2E2E",
                fg="#FFFFFF",
                font=("Arial", 12)
            ).pack(anchor="w")

            tk.Button(
                body,
                text="OK",
                command=popup.destroy,
                bg="#444444",
                fg="#FFFFFF",
                activebackground="#444444",
                activeforeground="#FFFFFF",
                width=8
            ).pack(anchor="e", pady=(12, 0))

            popup.configure(bg="#2E2E2E")
            popup.update_idletasks()

            try:
                x = self.root.winfo_rootx() + (self.root.winfo_width() // 2) - (popup.winfo_width() // 2)
                y = self.root.winfo_rooty() + (self.root.winfo_height() // 2) - (popup.winfo_height() // 2)
                popup.geometry(f"+{x}+{y}")
            except Exception:
                pass

            popup.bind("<Return>", lambda e: popup.destroy())
            popup.bind("<Escape>", lambda e: popup.destroy())
            popup.wait_window()
        else:
            self.show_info("Success", message)
           
    def update_all_pump_status_colors(self):
        for pump_name, frame in [("RO Pump A", self.pump_frame_a), ("RO Pump B", self.pump_frame_b)]:
            state = self.pump_states.get(pump_name, False)
            label = frame["pump_status"]
            label.config(fg="green" if state else "red")

    def connect_to_sensors(self):
        print("Connecting to sensors (TCP first, then serial fallback)…")
        connected_any = False
        need_serial = set(self.sensors.keys())  # A/B/C/D&E

        # TCP per endpoints
        eps = getattr(self, "endpoints", {})
        for sid in list(need_serial):
            ep = eps.get(sid, {"type":"serial"})
            if ep.get("type") != "tcp": 
                continue
            host = (ep.get("host") or "").strip()
            port = int(ep.get("port", 8888))
            if not host:
                 print(f"[TCP] Sensor {sid}: host not set; skipping.")
                 continue
            try:
                print(f"[TCP] Connecting {sid} at {host}:{port} …")
                t = TransportTCP(host, port, timeout=2.0)
                t.open()
                t.write("RX800\n")
                got = t.readline().strip()
                print(f"[TCP] {sid} ID reply: {got}")
                if got != sid:
                    raise IOError(f"ID mismatch (expected {sid}, got {got!r})")
                
                # Probe sensors (A/B/C: RX203, D: RX205, E: RX207)
                probe_commands = {
                    "A": "RX203\n",
                    "B": "RX203\n",
                    "C": "RX203\n",
                    "D": "RX205\n",
                    "E": "RX207\n",
                }         
                
                probe = probe_commands.get(sid)
                if not probe:
                    raise ValueError(f"Unknown sensor id {sid!r} for probe")
                
                t.write(probe)
                resp = t.readline()
                if not resp:
                    raise IOError("no data on probe")
                self.sensors[sid]["port"] = t
                self.sensors[sid]["is_running"] = True
                self.update_sensor_firmware(sid)
                connected_any = True
                threading.Thread(target=self.read_sensor_data, args=(sid,), daemon=True).start()
                self.setup_sensor_ui(self.get_sensor_frame_by_id(sid), t)
                print(f"[TCP] Sensor {sid} connected.")
                need_serial.discard(sid)
            except Exception as e:
                print(f"[TCP] Sensor {sid} error: {e}")

        # Serial fallback for remaining
        if not need_serial:
            return

        ports = list(serial.tools.list_ports.comports())
           
        print("Scanning COM ports for:", sorted(need_serial))
        for p in ports:
            try:
                ser = serial.Serial(p.device, baudrate=9600, timeout=2)
                ts = TransportSerial(ser)
                ts.open()
                ts.write("RX800\n")
                sid = ts.readline()
                print(f"[SER] {p.device} -> {sid}")
                if sid in need_serial:
                    probe_commands = {
                        "A": "RX203\n",
                        "B": "RX203\n",
                        "C": "RX203\n",
                        "D": "RX205\n",
                        "E": "RX207\n",
                    }
                    
                    probe = probe_commands.get(sid)
                    if not probe:
                        ts.close()
                        continue
                        
                    ts.write(probe)
                    resp = ts.readline()
                    if not resp:
                        ts.close(); continue
                    self.sensors[sid]["port"] = ts
                    self.sensors[sid]["is_running"] = True
                    connected_any = True
                    threading.Thread(target=self.read_sensor_data, args=(sid,), daemon=True).start()
                    self.setup_sensor_ui(self.get_sensor_frame_by_id(sid), ts)
                    print(f"[SER] Sensor {sid} connected on {p.device}")
                    need_serial.discard(sid)
            except Exception as e:
                print(f"[SER] {p.device} - {e}")
               
    def is_valid_response(self, response: str) -> bool:
        if response is None:
            return False
        response = str(response).strip()
        if response == "" or response.lower() == "none":
            return False

        for unit in ["mmWG", "mBar"]:
            if response.endswith(unit):
                response = response.replace(unit, "").strip()

        if response.count(".") > 1:
            return False

        return response.replace(".", "", 1).isdigit()

    def setup_sensor_ui(self, frame, serial_port):
        def update_ui():
            frame["connection_status"].config(text="Connected", fg="green")
        self.root.after(0, update_ui)  
   
    def set_sensor_disconnected(self, frame, sensor_id=None):
        try:
            # Logging cache: force this sensor to disconnected immediately
            if sensor_id:
                try:
                    self._mark_sensor_disconnected_for_logging(sensor_id)
                except Exception:
                    pass

            # Status
            frame["connection_status"].config(text="Disconnected", fg="red")

            # Clear readings safely (only if those labels exist in this frame)
            if "temperature_label" in frame and frame["temperature_label"]:
                frame["temperature_label"].config(text="Temperature: --")
            if "water_gauge_label" in frame and frame["water_gauge_label"]:
                frame["water_gauge_label"].config(text="Level: --")
            if "ph_level_label" in frame and frame["ph_level_label"]:
                frame["ph_level_label"].config(text="pH: --")
            if "tds_level_label" in frame and frame["tds_level_label"]:
                frame["tds_level_label"].config(text="TDS: --")

            # Stop any flashing/sounds tied to this sensor
            try:
                if sensor_id == "C":
                    # RO tank alarm visuals/sound
                    self.stop_alarm_flash("ro_tank")
                elif sensor_id == "D":
                    # pH alarm visuals/sound
                    self.stop_alarm_flash("ph_sensor")
                elif sensor_id == "E":
                    # TDS alarm visuals/sound
                    self.stop_alarm_flash("tds_sensor")
                else:
                    # A/B don't use those keys, but ensure visuals are sane
                    pass

                # Stop any temperature alarm flashing for this sensor
                try:
                    if sensor_id and "temperature_label" in frame and frame["temperature_label"]:
                        self.stop_alarm_flash(f"temp_{sensor_id}", restore=False, label=frame["temperature_label"])
                except Exception:
                    pass
            except Exception as _e:
                print("[ALARM STOP] on disconnect:", _e)

            # Always ensure sound state is reset (no overlapping playback)
            try:
                if hasattr(self, "_reset_alarm_sound_state"):
                    self._reset_alarm_sound_state()
            except Exception as _e:
                print("[SOUND RESET] on disconnect:", _e)

            # Disable reset button if you have it
            if "reset_button" in frame and frame["reset_button"]:
                frame["reset_button"].config(state=tk.DISABLED)

        except Exception as e:
            print(f"[UI ERROR] Failed to update disconnected status: {e}")

    # Sensor Serial & TCP RX & TX Locking 
    def _query_sensor(self, sensor_id: str, cmd: str, timeout: float = 3.0) -> str:
        """
        Atomically send one command to a sensor and read exactly one line back.
        Prevents replies being picked up by the wrong read (the swap bug).
        """
        t = self.sensors.get(sensor_id, {}).get("port")
        if not t:
            return ""

        line = cmd if cmd.endswith("\n") else (cmd + "\n")
        with self.io_locks[sensor_id]:
            try:
                if hasattr(t, "sock"):
                    try:
                        t.sock.settimeout(0.01)
                        try:
                            while t.sock.recv(1024):
                                pass
                        except Exception:
                            pass
                        t.sock.settimeout(timeout)
                    except Exception:
                        pass

                    try: t.write(line)
                    except TypeError: t.write(line.encode())

                    resp = t.readline()
                    return (resp or "").strip()

                else:
                    try: t.write(line)                
                    except TypeError: t.write(line.encode()) 

                    try: t.timeout = timeout
                    except Exception:
                        try: t.ser.timeout = timeout
                        except Exception: pass

                    resp = t.readline()
                    if isinstance(resp, bytes):
                        resp = resp.decode(errors="ignore")
                    return (resp or "").strip()

            except Exception as e:
                print(f"[QUERY ERR] {sensor_id} {cmd}: {e}")
                return ""
            
    # Update Sensor Firmware Settings Menu Display    
    def update_sensor_firmware(self, sensor_id: str):
        """
        Reads RX245 from the sensor and caches the firmware string
        for display in the settings menu.
        """
        try:
            resp = self._query_sensor(sensor_id, "RX245", timeout=2.0)
            if not resp:
                return

            r = str(resp).strip()

            # Pico returns e.g. 'D2.0.0' → strip leading sensor ID
            if r.startswith(sensor_id) and len(r) > 1:
                r = r[1:]

            if r:
                self.sensor_firmware[sensor_id] = r
                print(f"[FW] Sensor {sensor_id}: {r}")

        except Exception as e:
            print(f"[FW] Sensor {sensor_id} read failed: {e}")

    def tare_sensor(self, sensor_id: str, parent_popup=None):
        # Themed confirm
        proceed = self.show_confirm(
            "Tare Level (Zero mmWG)",
            "Make sure the sensor is OUT of water (dry/atmosphere).\n\n"
            "This will set the current reading as 0 mmWG.\n\nProceed?",
            yes_text="Yes, Tare", no_text="Cancel"
        )
        if not proceed:
            return

        # Read current level...
        try:
            resp = self._query_sensor(sensor_id, "RX203", timeout=3.0)
            wl = float(str(resp).replace("mmWG","").replace("mBar","").strip())
        except Exception:
            from tkinter import messagebox
            self.show_error("Tare Failed", f"Could not read a valid level from Sensor {sensor_id}.")
            return

        # Save offset so (raw + offset) == 0
        if not hasattr(self, "tare_offsets"):
            self.tare_offsets = {"A": 0.0, "B": 0.0, "C": 0.0}
        self.tare_offsets[sensor_id] = -wl
        self.save_threshold_settings()

        # Refresh GUI label
        frame = self.get_sensor_frame_by_id(sensor_id)
        try:
            self.update_water_level_label(frame, resp)
        except Exception:
            pass

        # Close the settings popup that launched us
        if parent_popup and parent_popup.winfo_exists():
            parent_popup.destroy()

        # Your existing themed success toast is fine here
        self.show_success_popup(f"Sensor {sensor_id} tared to 0 mmWG.")
  
    def read_sensor_data(self, sensor_id):
        """
        Continuous poll loop with buffer DRains before/after each command to stop
        cross-command mixing on TCP/Serial.
        """

        def _drain(port, max_bytes=4096):
            """Non-blocking drain of any pending bytes on TCP or Serial."""
            try:
                # TCP wrapper with .sock
                if hasattr(port, "sock"):
                    
                    try:
                        port.sock.settimeout(0.0)
                        total = 0
                        while total < max_bytes:
                            try:
                                chunk = port.sock.recv(1024)
                                if not chunk:
                                    break
                                total += len(chunk)
                            except (BlockingIOError, socket.timeout):
                                break
                    finally:
                        port.sock.settimeout(0.5)  # Small per-recv; your readline can set its own
                else:
                    # Serial: use in_waiting if available
                    try:
                        ser = getattr(port, "ser", port)  # Wrapper or raw pyserial
                        n = getattr(ser, "in_waiting", 0)
                        if n:
                            try:
                                ser.read(n)
                            except Exception:
                                pass
                    except Exception:
                        pass
            except Exception:
                pass

        def _send(port, cmd: str):
            line = cmd if cmd.endswith("\n") else (cmd + "\n")
            try:
                port.write(line)          # TCP wrapper often accepts str
            except TypeError:
                port.write(line.encode()) # Raw pyserial expects bytes

        def _read(port, timeout_s=2.5) -> str:
            # If your transports expose a timeout, set it briefly
            try:
                if hasattr(port, "sock"):
                    port.sock.settimeout(timeout_s)
                else:
                    ser = getattr(port, "ser", port)
                    try:
                        ser.timeout = timeout_s
                    except Exception:
                        pass
            except Exception:
                pass

            try:
                resp = port.readline()
                if isinstance(resp, bytes):
                    resp = resp.decode(errors="ignore")
                return (resp or "").strip()
            except Exception:
                return ""

        def _txrx(port, cmd: str, settle: float = 0.0, timeout_s=2.5) -> str:
            # Drain any leftover bytes from previous command(s)
            _drain(port)
            _send(port, cmd)
            if settle > 0:
                time.sleep(settle)
            val = _read(port, timeout_s=timeout_s)
            # Drain anything coalesced after the newline (second line in same packet)
            _drain(port)
            return val

        while self.sensors.get(sensor_id, {}).get("is_running", False):
            try:
                port = self.sensors.get(sensor_id, {}).get("port")
                if not port:
                    break

                if sensor_id == "A":
                    # Temp then Level
                    temperature = _txrx(port, "RX201", settle=0.10, timeout_s=3.0)
                    water_level = _txrx(port, "RX203", settle=0.00, timeout_s=3.0)

                    # Cache latest readings for Data Logging
                    self._update_cached_reading(
                        sensor_id,
                        connected=True,
                        temp_c=safe_parse_float(temperature),
                        water_mmwg=safe_parse_float(water_level),
                    )

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.aquarium_frame_1, temperature, water_level, None, None, None

                    ))
                    # Temperature alarm (mimics pH/TDS alarm behaviour)
                    try:
                        self.check_temp_alarm("A", temperature, self.aquarium_frame_1)
                    except Exception:
                        pass

                    try:
                        wl = float(str(water_level).replace("mmWG","").replace("mBar","").strip())
                        self.water_change_tick("A", self.tared_mmwg("A", wl))
                        self.control_pumps("A", self.tared_mmwg("A", wl))
                    except Exception:
                        pass

                elif sensor_id == "B":
                    temperature = _txrx(port, "RX201", settle=0.10, timeout_s=3.0)
                    water_level = _txrx(port, "RX203", settle=0.00, timeout_s=3.0)

                    # Cache latest readings for Data Logging
                    self._update_cached_reading(
                        sensor_id,
                        connected=True,
                        temp_c=safe_parse_float(temperature),
                        water_mmwg=safe_parse_float(water_level),
                    )

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.aquarium_frame_2, temperature, water_level, None, None, None

                    ))
                    # Temperature alarm (mimics pH/TDS alarm behaviour)
                    try:
                        self.check_temp_alarm("B", temperature, self.aquarium_frame_2)
                    except Exception:
                        pass
 
                    try:
                        wl = float(str(water_level).replace("mmWG","").replace("mBar","").strip())
                        self.water_change_tick("B", self.tared_mmwg("B", wl))
                        self.control_pumps("B", self.tared_mmwg("B", wl))
                    except Exception:
                        pass

                elif sensor_id == "C":
                    # Only read temperature if the R2 toggle is on
                    temperature = None
                    try:
                        if self.display_units.get("C", {}).get("r2_temp_enabled", False):
                            temperature = _txrx(port, "RX201", settle=0.10, timeout_s=3.0)
                    except Exception:
                        temperature = None

                    water_level = _txrx(port, "RX203", settle=0.00, timeout_s=3.0)

                    # Cache latest readings for Data Logging
                    temp_val = safe_parse_float(temperature) if temperature is not None else None

                    self._update_cached_reading(
                        sensor_id,
                        connected=True,
                        temp_c=temp_val,  # will be None if not enabled
                        water_mmwg=safe_parse_float(water_level),
                    )

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.ro_tank_frame, temperature, water_level, None, None, None

                    ))
                    # Temperature alarm for RO Tank (only meaningful if R2 temp enabled + temp alarm enabled)
                    if temperature:
                        try:
                            self.check_temp_alarm("C", temperature, self.ro_tank_frame)
                        except Exception:
                            pass

                    try:
                        wl_mmwg = float(str(water_level).replace("mmWG","").replace("mBar","").strip())
                        self.check_ro_tank_alarm("C", self.tared_mmwg("C", wl_mmwg))
                    except Exception:
                        pass

                elif sensor_id == "D":
                    # pH sensor: temp then pH (give pH a bit more time)
                    temperature = _txrx(port, "RX201", settle=0.20, timeout_s=3.0)
                    ph_level    = _txrx(port, "RX205", settle=0.00, timeout_s=4.0)

                    # Cache latest readings for Data Logging
                    self._update_cached_reading(
                        sensor_id,
                        connected=True,
                        temp_c=safe_parse_float(temperature),
                        ph=safe_parse_float(ph_level),
                    )

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.ph_level_frame, temperature, None, ph_level, None, None, None
                    ))

                    # Combined alarms (pH + temperature) for Sensor D
                    try:
                        self.check_d_alarms("D", ph_level, temperature, self.ph_level_frame)
                    except Exception:
                        pass
                        
                elif sensor_id == "E":
                    # TDS sensor: temp then metrics
                    temperature      = _txrx(port, "RX201", settle=0.20, timeout_s=3.0)   # °C as string
                    cond_uScm_level  = _txrx(port, "RX206", settle=0.00, timeout_s=4.0)   # µS/cm (string)
                    tds_level        = _txrx(port, "RX207", settle=0.00, timeout_s=4.0)   # ppm (string)
                    sal_level        = _txrx(port, "RX208", settle=0.00, timeout_s=4.0)   # PSU ≈ ppt (string)

                    # Cache latest readings for Data Logging
                    self._update_cached_reading(
                        sensor_id,
                        connected=True,
                        temp_c=safe_parse_float(temperature),
                        tds_ppm=safe_parse_float(tds_level),
                        cond_uScm=safe_parse_float(cond_uScm_level),
                        sal_psu=safe_parse_float(sal_level),
                    )

                    # Normalize text
                    def good(x): return bool(x) and x not in ("ERR", "--")
                    use_f = bool(self.display_units.get("E", {}).get("use_fahrenheit", False))
                    if good(temperature):
                        try:
                            temp_val = float(str(temperature).replace("°C", "").replace("°F", "").strip())
                            if use_f:
                                temp_val = temp_val * 9/5 + 32
                                t_text = f"{temp_val:.1f} °F"
                            else:
                                t_text = f"{temp_val:.1f} °C"
                        except Exception:
                            # Fall back to raw string
                            t_text = f"{temperature} {'°F' if use_f else '°C'}"
                    else:
                        t_text = "--"
                    tds_text = f"{tds_level} ppm"  if good(tds_level)    else "--"
                    cu_text  = f"{cond_uScm_level} µS/cm" if good(cond_uScm_level) else "--"
                    s_text   = f"{sal_level} PSU"  if good(sal_level)    else "--"

                    mode = self.display_units.get("E", {}).get("display_mode", "tds_ppm")

                    def _apply():
                        # Connection + temp
                        if self.alarm_state.get("E_alarm","normal")=="normal":
                            self.tds_level_frame["connection_status"].config(text="Connected", fg="green")
                        self.tds_level_frame["temperature_label"].config(text=f"Temperature: {t_text}")

                        # Update all sublabels so user can switch mode and see something
                        self.tds_level_frame["tds_level_label"].config(text=f"TDS: {tds_text}")
                        self.tds_level_frame["cond_uScm_level_label"].config(text=f"Conductivity: {cu_text}")
                        self.tds_level_frame["sal_level_label"].config(text=f"Salinity: {s_text}")

                        # Reset fonts
                        base = ("Arial", 14, "bold")
                        self.tds_level_frame["tds_level_label"].config(font=base)
                        self.tds_level_frame["cond_uScm_level_label"].config(font=base)
                        self.tds_level_frame["sal_level_label"].config(font=base)
                        # Make selected one a touch bigger
                        bigger = ("Arial", 15, "bold")
                        if   mode == "tds_ppm":  self.tds_level_frame["tds_level_label"].config(font=bigger)
                        elif mode == "cond_uScm": self.tds_level_frame["cond_uScm_level_label"].config(font=bigger)
                        elif mode == "sal_psu":   self.tds_level_frame["sal_level_label"].config(font=bigger)

                    self.safe_gui_update(lambda: (
                        # Existing updates you already do…
                       (self.tds_level_frame["connection_status"].config(text="Connected", fg="green") if self.alarm_state.get("E_alarm","normal")=="normal" else None),
                       self.tds_level_frame["temperature_label"].config(text=f"Temperature: {t_text}"),
                       self.tds_level_frame["tds_level_label"].config(text=f"TDS: {tds_text}"),
                       self.tds_level_frame["cond_uScm_level_label"].config(text=f"Conductivity: {cu_text}"),
                       self.tds_level_frame["sal_level_label"].config(text=f"Salinity: {s_text}"),
                       self.safe_gui_update(self.layout_tds_tile)
                    ))

                    # Alarm checks (match existing pH/TDS behaviour)
                    try:
                        self.check_e_alarms("E", tds_level, cond_uScm_level, sal_level, temperature, self.tds_level_frame)
                    except Exception:
                        pass

            except Exception as e:
                print(f"[ERROR] read_sensor_data({sensor_id}): {e}")

                # Logging cache: mark disconnected immediately
                try:
                    self._mark_sensor_disconnected_for_logging(sensor_id)
                except Exception:
                    pass

                try:
                    self.sensors[sensor_id]["is_running"] = False
                except Exception:
                    pass

                break

            time.sleep(0.4)

    def sensor_watchdog(self):
        while True:
            for sensor_id, sensor in self.sensors.items():
                # Skip sensors we have decided to disable after too many failures
                if self.sensor_disabled_flags.get(sensor_id, False):
                    continue

                running = sensor.get("is_running", False)

                if not running:
                    attempt_num = self.sensor_fail_counts.get(sensor_id, 0) + 1
                    print(
                        f"[WATCHDOG] Sensor {sensor_id} not running. "
                        f"Attempting reconnect ({attempt_num}/{self.MAX_SENSOR_RETRIES})."
                    )

                    # Update UI to show disconnected
                    self.safe_gui_update(
                        lambda sid=sensor_id: self.set_sensor_disconnected(
                            self.get_sensor_frame_by_id(sid),
                            sensor_id=sid
                        )
                    )

                    # Try reconnect
                    try:
                        ok = self.reconnect_sensor(sensor_id)
                    except Exception as e:
                        print(f"[WATCHDOG ERROR] Failed to reconnect sensor {sensor_id}: {e}")
                        ok = False

                    if ok:
                        # Reconnect_sensor already reset counts/flags, but be explicit
                        self.sensor_fail_counts[sensor_id] = 0
                        self.sensor_disabled_flags[sensor_id] = False
                    else:
                        # Failed attempt
                        self.sensor_fail_counts[sensor_id] = attempt_num
                        if attempt_num >= self.MAX_SENSOR_RETRIES:
                            self.sensor_disabled_flags[sensor_id] = True
                            print(
                                f"[WATCHDOG] Sensor {sensor_id} disabled after "
                                f"{attempt_num} failed reconnect attempts."
                            )

            time.sleep(5)  # Check every 5 seconds

    def get_sensor_frame_by_id(self, sensor_id):
        mapping = {
            "A": self.aquarium_frame_1,
            "B": self.aquarium_frame_2,
            "C": self.ro_tank_frame,
            "D": self.ph_level_frame,
            "E": self.tds_level_frame,
        }
        return mapping.get(sensor_id, {})
   
    def reconnect_sensor(self, sensor_id):
        ep = getattr(self, "endpoints", {}).get(sensor_id, {"type":"serial"})
        if ep.get("type") == "tcp":
            host = (ep.get("host") or "").strip()
            port = int(ep.get("port", 8888))
            if host:
                try:
                    t = TransportTCP(host, port, timeout=2.0)
                    t.open(); t.write("RX800\n")
                    if t.readline() == sensor_id:
                        probe_commands = {
                            "A": "RX203\n",
                            "B": "RX203\n",
                            "C": "RX203\n",
                            "D": "RX205\n",
                            "E": "RX207\n",
                        }
                        
                        probe = probe_commands.get(sensor_id)
                        if not probe:
                            raise ValueError(f"Unknown sensor id {sid!r} for probe")
                        
                        t.write(probe)
                        if t.readline():
                            self.sensors[sensor_id]["port"] = t
                            self.sensors[sensor_id]["is_running"] = True
                            self.update_sensor_firmware(sensor_id)

                            # Reset failure tracking on success
                            self.sensor_fail_counts[sensor_id] = 0
                            self.sensor_disabled_flags[sensor_id] = False

                            self.safe_gui_update(
                                lambda: self.setup_sensor_ui(self.get_sensor_frame_by_id(sensor_id), t)
                            )
                            threading.Thread(
                                target=self.read_sensor_data,
                                args=(sensor_id,),
                                daemon=True
                            ).start()
                            print(f"[WATCHDOG] Sensor {sensor_id} TCP reconnected {host}:{port}")
                            return True
    
                except Exception as e:
                    print(f"[WATCHDOG TCP] {sensor_id}: {e}")
            # Fall through to serial scan as last resort

        # Serial scan (your existing code)
        ports = list(serial.tools.list_ports.comports())
        for port in ports:
            try:
                ser = serial.Serial(port.device, baudrate=9600, timeout=2)
                ts = TransportSerial(ser)
                ts.write("RX800\n")
                response = ts.readline()
                if response == sensor_id:
                    probe_commands = {
                        "A": "RX203\n",
                        "B": "RX203\n",
                        "C": "RX203\n",
                        "D": "RX205\n",
                        "E": "RX207\n",
                    }
                    
                    probe = probe_commands.get(sensor_id)
                    if not probe:
                        ts.close()
                        continue
                    
                    ts.write(probe)
                    if ts.readline():
                        self.sensors[sensor_id]["port"] = ts
                        self.sensors[sensor_id]["is_running"] = True
                        self.update_sensor_firmware(sensor_id)

                        # Reset failure tracking on success
                        self.sensor_fail_counts[sensor_id] = 0
                        self.sensor_disabled_flags[sensor_id] = False

                        self.safe_gui_update(
                            lambda: self.setup_sensor_ui(self.get_sensor_frame_by_id(sensor_id), ts)
                        )
                        threading.Thread(
                            target=self.read_sensor_data,
                            args=(sensor_id,),
                            daemon=True
                        ).start()
                        print(f"[WATCHDOG] Sensor {sensor_id} reconnected on {port.device}")
                        return True
                    
                ts.close()
            except Exception as e:
                print(f"[RECONNECT ERROR] {port.device}: {e}")
        return False
       
    def safe_gui_update(self, func):
        try:
            if self.root and self.root.winfo_exists():
                self.root.after(0, func)
        except Exception as e:
            print(f"[GUI UPDATED]")

    def update_sensor_ui(self, frame, temperature, water_level, ph_level, tds_level,
                         cond_uScm_level=None, cond_mScm_level=None, sal_level=None):
        self.update_temperature_label(frame, temperature)
        self.update_water_level_label(frame, water_level)
        self.update_ph_label(frame, ph_level)
        self.update_tds_label(frame, tds_level)
        self.update_cond_uScm_label(frame, cond_uScm_level)
        self.update_sal_label(frame, sal_level)

    def update_temperature_label(self, frame, temperature):
        try:
            label = frame.get("temperature_label")
            if not label:
                return

            # Work out which sensor this frame is for
            sensor_id = None
            if frame == self.aquarium_frame_1:
                sensor_id = "A"
            elif frame == self.aquarium_frame_2:
                sensor_id = "B"
            elif frame == self.ph_level_frame:
                sensor_id = "D"
            elif frame == self.ro_tank_frame:
                sensor_id = "C"
            elif frame == self.tds_level_frame:
                sensor_id = "E"

            # RO Tank visibility control
            if frame == self.ro_tank_frame:
                enabled = bool(self.display_units.get("C", {}).get("r2_temp_enabled", False))
                if not enabled:
                    # Hide the label when disabled
                    if label.winfo_ismapped():
                        label.pack_forget()
                    label.config(text="Temperature: --")
                    return
                else:
                    # Ensure it's visible when enabled
                    if not label.winfo_ismapped():
                        label.pack(pady=10)

            # Show/update the text
            if not temperature:
                label.config(text="Temperature: --")
                return

            use_f = bool(self.display_units.get(sensor_id, {}).get("use_fahrenheit", False))
            try:
                temp_val = float(temperature)
                if use_f:
                    temp_val = temp_val * 9/5 + 32
                    label.config(text=f"Temperature: {temp_val:.1f} °F")
                else:
                    label.config(text=f"Temperature: {temp_val:.1f} °C")
            except Exception:
                # Fall back to raw string
                label.config(text=f"Temperature: {temperature}")

        except Exception as e:
            print(f"[ERROR] Updating temperature_label: {e}")

    def update_water_level_label(self, frame, water_level):
        try:
            label = frame.get("water_gauge_label")
            if label and water_level:
                try:
                    wl_mmwg = float(water_level.replace("mmWG", "").replace("mBar", "").strip())
                except ValueError:
                    label.config(text=f"Level: {water_level}")
                    return

                sensor_id = None
                if frame == self.aquarium_frame_1:
                    sensor_id = "A"
                elif frame == self.aquarium_frame_2:
                    sensor_id = "B"
                elif frame == self.ro_tank_frame:
                    sensor_id = "C"

                # Apply tare offset
                if sensor_id:
                    wl_mmwg = wl_mmwg + float(self.tare_offsets.get(sensor_id, 0.0))

                if sensor_id:
                    width = self.display_units[sensor_id].get("width", 0)
                    depth = self.display_units[sensor_id].get("depth", 0)
                    use_liters = self.display_units[sensor_id].get("use_liters", False)
                    use_gallons = self.display_units[sensor_id].get("use_gallons", False)

                    if use_liters and width > 0 and depth > 0:
                        liters = wl_mmwg * width * depth / 10000.0
                        label.config(text=f"Level: {liters:.2f} Liters")
                    elif use_gallons and width > 0 and depth > 0:
                        gallons = (wl_mmwg * width * depth / 10000.0) * 0.264172
                        label.config(text=f"Level: {gallons:.2f} Gallons")
                    else:
                        label.config(text=f"Level: {wl_mmwg:.1f} mmWG")
                else:
                    label.config(text=f"Level: {wl_mmwg:.1f} mmWG")
        except Exception as e:
            print(f"[ERROR] Updating water_gauge_label: {e}")

    def update_ph_label(self, frame, ph_level):
        try:
           label = frame.get("ph_level_label")
           if label and ph_level:
                label.config(text=f"pH Level: {ph_level}")
        except Exception as e:
            print(f"[ERROR] Updating ph_level_label: {e}")
            
    def update_tds_label(self, frame, tds_level):
        try:
           label = frame.get("tds_level_label")
           if label and tds_level:
                label.config(text=f"TDS Level: {tds_level}")
        except Exception as e:
            print(f"[ERROR] Updating tds_level_label: {e}")
            
    def update_cond_uScm_label(self, frame, cond_uScm_level):
        try:
           label = frame.get("cond_uScm_level_label")
           if label and cond_uScm_level:
                label.config(text=f"Conductivity Level: {cond_uScm_level}")
        except Exception as e:
            print(f"[ERROR] Updating cond_uScm_level_label: {e}")
    
    def update_sal_label(self, frame, sal_level):
        try:
           label = frame.get("sal_level_label")
           if label and sal_level:
                label.config(text=f"Salinity Level: {sal_level}")
        except Exception as e:
            print(f"[ERROR] Updating sal_level_label: {e}")    

    def toggle_fullscreen(self, event=None):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    def exit_fullscreen(self, event=None):
        self.fullscreen = False
        self.root.attributes("-fullscreen", False)

    def reset_sensor(self, sensor_id):
        try:
            t = self.sensors.get(sensor_id, {}).get("port")
            if not t:
                raise ValueError("No active connection.")
            print("Sending reset command (r)...")
            t.write("r\n")
        except Exception as e:
            print(f"Exception: {e}")
            self.root.after(0, lambda: self.show_error("Error", f"Failed to reset sensor {sensor_id}: {e}"))

    def check_ro_tank_alarm(self, sensor_id, wl_mmwg):
        """
        sensor_id should be 'C'.
        Uses display_units['C'] keys: level_alarm, use_liters/use_gallons, width, depth, min_alarm, max_alarm.
        Converts wl_mmwg into selected unit and triggers the same alarm states used elsewhere.
        """
        label = self.ro_tank_frame["connection_status"]
        try:
            settings = self.display_units.get(sensor_id, {}) or {}
            if not settings.get("level_alarm", False):
                self._set_alarm_state("ro_tank", "normal", label)
                return

            wl = self._num(wl_mmwg)
            if wl is None:
                self._set_alarm_state("ro_tank", "normal", label)
                return

            use_liters  = bool(settings.get("use_liters", False))
            use_gallons = bool(settings.get("use_gallons", False))
            width = self._num(settings.get("width"))
            depth = self._num(settings.get("depth"))

            # Convert reading into chosen unit
            if use_liters or use_gallons:
                if not width or not depth or width <= 0 or depth <= 0:
                    self._set_alarm_state("ro_tank", "normal", label)
                    return
                height_cm = wl / 10.0
                liters = (height_cm * width * depth) / 1000.0
                value = liters if use_liters else (liters * 0.264172)
            else:
                value = wl

            lo = self._num(settings.get("min_alarm"))
            hi = self._num(settings.get("max_alarm"))
            if lo is None or hi is None or lo >= hi:
                self._set_alarm_state("ro_tank", "normal", label)
                return

            margin = 50.0 if not (use_liters or use_gallons) else (2.0 if use_liters else 0.5)

            if value <= lo or value >= hi:
                self._set_alarm_state("ro_tank", "critical", label)
            elif (lo < value <= lo + margin) or (hi - margin <= value < hi):
                self._set_alarm_state("ro_tank", "approaching", label)
            else:
                self._set_alarm_state("ro_tank", "normal", label)

        except Exception as e:
            print("[RO ALARM] Exception:", e)
            self._set_alarm_state("ro_tank", "normal", label)
    def _num(self, x):
        """best-effort float; returns None on blank, ERR, --, etc."""
        try:
            s = str(x).strip()
            if not s or s.upper() in {"ERR", "NONE", "--"}:
                return None
            return float(s)
        except Exception:
            return None

    def check_ph_alarm(self, sensor_id, ph_reading):
        """
        sensor_id should be 'D'.
        Uses display_units['D'] keys: ph_alarm_enabled, ph_min, ph_max.
        Mimics existing alarm behaviour (APPROACHING LIMIT / LEVEL CRITICAL).
        """
        label = self.ph_level_frame["connection_status"]
        try:
            settings = self.display_units.get(sensor_id, {}) or {}
            if not settings.get("ph_alarm_enabled", False):
                self._set_alarm_state("ph_sensor", "normal", label)
                return

            val = self._num(ph_reading)
            if val is None:
                self._set_alarm_state("ph_sensor", "normal", label)
                return

            lo = self._num(settings.get("ph_min"))
            hi = self._num(settings.get("ph_max"))
            if lo is None or hi is None or lo >= hi:
                self._set_alarm_state("ph_sensor", "normal", label)
                return

            margin = 0.5
            if val <= lo or val >= hi:
                self._set_alarm_state("ph_sensor", "critical", label)
            elif (lo < val <= lo + margin) or (hi - margin <= val < hi):
                self._set_alarm_state("ph_sensor", "approaching", label)
            else:
                self._set_alarm_state("ph_sensor", "normal", label)

        except Exception as e:
            print("[PH ALARM] Exception:", e)
            self._set_alarm_state("ph_sensor", "normal", label)
    

    # ----------------------------
    # Unified alarm evaluation
    # ----------------------------
    def _alarm_severity(self, val, lo, hi, margin):
        """
        Returns: 'critical' | 'approaching' | 'normal'
        """
        try:
            v = self._num(val)
            lo_v = self._num(lo)
            hi_v = self._num(hi)
            if v is None or lo_v is None or hi_v is None or lo_v >= hi_v:
                return "normal"
            m = float(margin)
            if v <= lo_v or v >= hi_v:
                return "critical"
            if (lo_v < v <= lo_v + m) or (hi_v - m <= v < hi_v):
                return "approaching"
            return "normal"
        except Exception:
            return "normal"

    def check_d_alarms(self, sensor_id, ph_reading, temp_reading, frame):
        """
        Combined pH + temperature alarms for Sensor D, mimicking the existing alarm behaviour.
        Drives the *status label* only (APPROACHING LIMIT / LEVEL CRITICAL).
        """
        label = frame["connection_status"]
        try:
            settings = self.display_units.get(sensor_id, {}) or {}

            # pH
            ph_state = "normal"
            if settings.get("ph_alarm_enabled", False):
                ph_state = self._alarm_severity(
                    ph_reading,
                    settings.get("ph_min"),
                    settings.get("ph_max"),
                    margin=0.2
                )

            # Temp
            temp_state = "normal"
            if settings.get("temp_alarm_enabled", False):
                # Use margins that feel similar across units
                is_f = bool(settings.get("use_fahrenheit", False))
                temp_state = self._alarm_severity(
                    temp_reading,
                    settings.get("temp_min"),
                    settings.get("temp_max"),
                    margin=(1.0 if is_f else 0.5)
                )

            # Take worst severity
            worst = "normal"
            if "critical" in (ph_state, temp_state):
                worst = "critical"
            elif "approaching" in (ph_state, temp_state):
                worst = "approaching"

            if worst == "normal":
                self._set_alarm_state("D_alarm", "normal", label)
            else:
                self._set_alarm_state("D_alarm", worst, label)

        except Exception as e:
            print("[D ALARM] Exception:", e)
            self._set_alarm_state("D_alarm", "normal", label)

    def check_e_alarms(self, sensor_id, tds_reading, cond_reading, sal_reading, temp_reading, frame):
        """
        Combined TDS + conductivity + salinity + temperature alarms for Sensor E.
        Mimics pH/TDS behaviour by driving the status label only.
        Conductivity/Salinity alarms are only valid if those readings are enabled (show_fields).
        """
        label = frame["connection_status"]
        try:
            settings = self.display_units.get(sensor_id, {}) or {}
            show_cfg = settings.get("show_fields", {}) or {}

            # TDS
            tds_state = "normal"
            if settings.get("tds_alarm_enabled", False):
                tds_state = self._alarm_severity(
                    tds_reading,
                    settings.get("tds_min"),
                    settings.get("tds_max"),
                    margin=50.0
                )

            # Conductivity
            cond_state = "normal"
            if settings.get("cond_alarm_enabled", False) and bool(show_cfg.get("cond_uScm", False)):
                cond_state = self._alarm_severity(
                    cond_reading,
                    settings.get("cond_min"),
                    settings.get("cond_max"),
                    margin=50.0
                )

            # Salinity
            sal_state = "normal"
            if settings.get("sal_alarm_enabled", False) and bool(show_cfg.get("sal_psu", False)):
                sal_state = self._alarm_severity(
                    sal_reading,
                    settings.get("sal_min"),
                    settings.get("sal_max"),
                    margin=0.5
                )

            # Temperature
            temp_state = "normal"
            if settings.get("temp_alarm_enabled", False):
                is_f = bool(settings.get("use_fahrenheit", False))
                temp_state = self._alarm_severity(
                    temp_reading,
                    settings.get("temp_min"),
                    settings.get("temp_max"),
                    margin=(1.0 if is_f else 0.5)
                )

            worst = "normal"
            if "critical" in (tds_state, cond_state, sal_state, temp_state):
                worst = "critical"
            elif "approaching" in (tds_state, cond_state, sal_state, temp_state):
                worst = "approaching"

            if worst == "normal":
                self._set_alarm_state("E_alarm", "normal", label)
            else:
                self._set_alarm_state("E_alarm", worst, label)

        except Exception as e:
            print("[E ALARM] Exception:", e)
            self._set_alarm_state("E_alarm", "normal", label)

    def check_tds_alarm(self, sensor_id, tds_reading):
        """
        sensor_id should be 'E'.
        Uses display_units['E'] keys: tds_alarm_enabled, tds_min, tds_max.
        Mimics existing alarm behaviour (APPROACHING LIMIT / LEVEL CRITICAL).
        """
        label = self.tds_level_frame["connection_status"]
        try:
            settings = self.display_units.get(sensor_id, {}) or {}
            if not settings.get("tds_alarm_enabled", False):
                self._set_alarm_state("tds_sensor", "normal", label)
                return

            val = self._num(tds_reading)
            if val is None:
                self._set_alarm_state("tds_sensor", "normal", label)
                return

            lo = self._num(settings.get("tds_min"))
            hi = self._num(settings.get("tds_max"))
            if lo is None or hi is None or lo >= hi:
                self._set_alarm_state("tds_sensor", "normal", label)
                return

            margin = 50.0
            if val <= lo or val >= hi:
                self._set_alarm_state("tds_sensor", "critical", label)
            elif (lo < val <= lo + margin) or (hi - margin <= val < hi):
                self._set_alarm_state("tds_sensor", "approaching", label)
            else:
                self._set_alarm_state("tds_sensor", "normal", label)

        except Exception as e:
            print("[TDS ALARM] Exception:", e)
            self._set_alarm_state("tds_sensor", "normal", label)

    def check_temp_alarm(self, sensor_id, temp_reading, frame):
        """
        Temperature alarm that mimics the pH/TDS alarm behaviour.
        Uses display_units[sensor_id] keys: temp_alarm_enabled, temp_min, temp_max, use_fahrenheit.
        Displays APPROACHING LIMIT / LEVEL CRITICAL in the frame's status label and plays WAV (repeating).
        """
        try:
            settings = self.display_units.get(sensor_id, {}) or {}
            status_label = frame.get("connection_status")
            if not status_label:
                return

            alarm_key = f"temp_{sensor_id}"

            # Disabled => stop immediately
            if not settings.get("temp_alarm_enabled", False):
                self._set_alarm_state(alarm_key, "normal", status_label)
                return

            val_c = self._num(temp_reading)
            if val_c is None:
                # No valid reading => don't force "Connected" if device isn't actually connected;
                # just clear this alarm state/flash if it was active.
                self._set_alarm_state(alarm_key, "normal", status_label)
                return

            use_f = bool(settings.get("use_fahrenheit", False))
            val = (val_c * 9.0/5.0 + 32.0) if use_f else val_c

            lo = self._num(settings.get("temp_min"))
            hi = self._num(settings.get("temp_max"))
            if lo is None or hi is None or lo >= hi:
                self._set_alarm_state(alarm_key, "normal", status_label)
                return

            margin = 1.0 if use_f else 0.5
            if val <= lo or val >= hi:
                self._set_alarm_state(alarm_key, "critical", status_label)
            elif (lo < val <= lo + margin) or (hi - margin <= val < hi):
                self._set_alarm_state(alarm_key, "approaching", status_label)
            else:
                self._set_alarm_state(alarm_key, "normal", status_label)

        except Exception as e:
            print(f"[TEMP ALARM] {sensor_id} error: {e}")
            try:
                self._set_alarm_state(f"temp_{sensor_id}", "normal", frame.get("connection_status"))
            except Exception:
                pass

    def start_alarm_flash(self, label, sensor_key, base_color):
        """Flash between base_color and its alt shade. Cancels any existing job for this sensor."""
        alt = "#CC8400" if base_color == "orange" else "#A52A2A"

        # Cancel any stale job first
        self.stop_alarm_flash(sensor_key, restore=False)

        def _tick():
            try:
                cur = label.cget("fg")
                label.config(fg=alt if cur == base_color else base_color)
                self.alarm_flash_jobs[sensor_key] = label.after(500, _tick)
            except Exception as e:
                print(f"[FLASH] {sensor_key} error:", e)

        label.config(fg=base_color)
        self.alarm_flash_jobs[sensor_key] = label.after(500, _tick)

    def stop_alarm_flash(self, sensor_key, restore=True, label=None):
        """Stop flashing job for sensor_key. If restore, set label green."""
        job = self.alarm_flash_jobs.pop(sensor_key, None)
        if job and label:
            try:
                label.after_cancel(job)
            except Exception:
                pass
        elif job:
            pass

        if restore and label:
            try:
                label.config(fg="green")
            except Exception:
                pass

    def _play_wav_async(self, key: str):
        """
        Play a WAV file asynchronously on Raspberry Pi using ALSA (aplay).
        Ensures only one alarm sound plays at a time.
        key: "approaching" | "critical"
        """
        try:
            # Ensure 'aplay' is available
            if shutil.which("aplay") is None:
                print("[ALARM SOUND] 'aplay' not found. Install with: sudo apt-get install alsa-utils")
                return

            path = self.sound_paths.get(key)
            if not path or not os.path.isfile(path):
                print(f"[ALARM SOUND] File missing for key '{key}': {path}")
                return

            # If the same sound is already playing, do nothing
            if self._sound_proc and self._sound_key == key and self._sound_proc.poll() is None:
                return

            self._reset_alarm_sound_state()

            # Launch aplay quietly
            self._sound_key  = key
            self._sound_proc = subprocess.Popen(
                ["aplay", "-q", path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True, 
            )
            print(f"[ALARM SOUND] Playing '{key}' -> {path}")

        except Exception as e:
            print(f"[ALARM SOUND] Failed to play '{key}': {e}")

    def _maybe_play_alarm(self, sensor_id, severity, repeat_interval=120.0):
        """Play the alarm WAV for this sensor/severity, and repeat every repeat_interval while active."""
        # Normalize legacy naming
        sev = str(severity).lower().strip()
        if sev in ("approach", "approaching"):
            sev = "approaching"
        elif sev == "critical":
            sev = "critical"
        else:
            return  # unknown / normal

        now = _time.monotonic()
        k = (sensor_id, sev)
        last = self.alarm_last_play.get(k, 0.0)
        if (now - last) < float(repeat_interval):
            return
        self.alarm_last_play[k] = now

        key = "approaching" if sev == "approaching" else "critical"
        self._play_wav_async(key)


    def _reset_alarm_sound_state(self):
        """
        Stop any alarm WAV currently playing.
        Safe on repeated calls.
        """
        try:
            if self._sound_proc and self._sound_proc.poll() is None:
                # Try graceful terminate
                try:
                    os.killpg(os.getpgid(self._sound_proc.pid), signal.SIGTERM)
                except Exception:
                    # Fallback to terminate/kill if process group isn't available
                    try:
                        self._sound_proc.terminate()
                    except Exception:
                        pass
                # Give it a moment, then hard kill if needed
                try:
                    self._sound_proc.wait(timeout=0.5)
                except Exception:
                    try:
                        os.killpg(os.getpgid(self._sound_proc.pid), signal.SIGKILL)
                    except Exception:
                        try:
                            self._sound_proc.kill()
                        except Exception:
                            pass
            # Clear state
            self._sound_proc = None
            self._sound_key  = None
        except Exception as e:
            print(f"[ALARM SOUND] Reset error: {e}")
            self._sound_proc = None
            self._sound_key  = None

    def _set_alarm_state(self, sensor_key, state, label):
        """
        sensor_key: unique key for this alarm (e.g. 'ph_sensor', 'tds_sensor', 'ro_tank', 'temp_A').
        state: 'normal' | 'approaching'/'approach' | 'critical'
        Behaviour mimics existing pH/TDS alarms:
          - Updates the status label text (Connected / APPROACHING LIMIT / LEVEL CRITICAL)
          - Flashes the status text when approaching/critical
          - Plays the alarm WAV and repeats every 2 minutes while still active
        """
        try:
            st = str(state).lower().strip()
            if st in ("approach", "approaching"):
                st = "approaching"
            elif st == "critical":
                st = "critical"
            else:
                st = "normal"

            prev = self.alarm_state.get(sensor_key, "normal")

            # If unchanged, still allow repeating sound every 2 minutes
            if prev == st:
                if st != "normal":
                    self._maybe_play_alarm(sensor_key, st, repeat_interval=120.0)
                return

            # Stop old flashing for this key
            try:
                self.stop_alarm_flash(sensor_key, restore=False, label=label)
            except Exception:
                pass

            self.alarm_state[sensor_key] = st

            if st == "normal":
                # Clear any repeat timers for this alarm key so re-enabling plays immediately
                try:
                    self.alarm_last_play.pop((sensor_key, "approaching"), None)
                    self.alarm_last_play.pop((sensor_key, "critical"), None)
                except Exception:
                    pass
                try:
                    label.config(text="Connected", fg="green")
                except Exception:
                    pass
                return

            if st == "approaching":
                try:
                    label.config(text="APPROACHING LIMIT")
                except Exception:
                    pass
                self.start_alarm_flash(label, sensor_key, base_color="orange")
                self._maybe_play_alarm(sensor_key, "approaching", repeat_interval=120.0)
                return

            # critical
            try:
                label.config(text="LEVEL CRITICAL")
            except Exception:
                pass
            self.start_alarm_flash(label, sensor_key, base_color="red")
            self._maybe_play_alarm(sensor_key, "critical", repeat_interval=120.0)

        except Exception as e:
            print(f"[ALARM] _set_alarm_state error ({sensor_key}): {e}")

    def layout_tds_tile(self):
        if not hasattr(self, "_tds_last_visibility"):
            self._tds_last_visivility = None
        f = getattr(self, "tds_level_frame", None)
        if not f:
            return

        cfg = (self.display_units.get("E", {}) or {}).get("show_fields", {})
        show_tds  = bool(cfg.get("tds_ppm", True))
        show_u    = bool(cfg.get("cond_uScm", False))
        show_sal  = bool(cfg.get("sal_psu", False))

        vis_state = (show_tds, show_u, show_sal)
        if self._tds_last_visibility == vis_state:
            
            return
        self._tds_last_visibility = vis_state

        # Grab widgets
        status   = f.get("connection_status")
        tds_lbl  = f.get("tds_level_label")
        temp_lbl = f.get("temperature_label")
        u_lbl    = f.get("cond_uScm_level_label")
        s_lbl    = f.get("sal_level_label")
        btn      = f.get("settings_button")

        for w in (status, tds_lbl, temp_lbl, u_lbl, s_lbl, btn):
            try:
                if w and w.winfo_manager():
                    w.pack_forget()
            except Exception:
                pass

        # Fixed order layout
        if status:   status.pack(anchor="n")
        if show_tds and tds_lbl:  tds_lbl.pack(pady=6)
        if temp_lbl: temp_lbl.pack(pady=6)
        if show_u   and u_lbl:    u_lbl.pack(pady=6)
        if show_sal and s_lbl:    s_lbl.pack(pady=6)

        if btn: btn.pack(pady=5)

    def cleanup_on_exit(self):
        print("[CLEANUP] Cleaning up serial ports and GPIO...")
        # Stop all sensor threads
        for sensor_id in self.sensors:
            self.sensors[sensor_id]["is_running"] = False
            port = self.sensors[sensor_id].get("port")
            if port and port.is_open:
                try:
                    port.close()
                    print(f"[CLEANUP] Closed port for Sensor {sensor_id}")
                except Exception as e:
                    print(f"[CLEANUP ERROR] Could not close port for Sensor {sensor_id}: {e}")

        # Turn off pumps safely fail safe
        for pump_name, pin in self.pump_gpio.items():
            try:
                GPIO.output(pin, GPIO.LOW)
            except Exception as e:
               print(f"[CLEANUP ERROR] Could not turn off pump '{pump_name}': {e}")

        # Clean up GPIO
        try:
            GPIO.cleanup()
            print("[CLEANUP] GPIO cleaned up.")
        except Exception as e:
            print(f"[CLEANUP ERROR] GPIO cleanup failed: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    gui = SensorGUI(root)

    def on_closing():
        gui.cleanup_on_exit()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        print("[EXIT] Interrupted by user.")
        on_closing()
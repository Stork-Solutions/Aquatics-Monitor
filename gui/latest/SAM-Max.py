# Wi-Fi TCP - [Enabled]
# 4Ch Relay - [Enabled]
# Auto Updating - [Enabled]
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
import signal
import hashlib
import urllib.request
import urllib.error

__version__ = "1.4.0"
GUI_MANIFEST_URL = "https://raw.githubusercontent.com/Stork-Solutions/Aquatics-Monitor/main/gui/latest/gui_update.json"

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
    def is_open(self):  # for UI checks if you still need them
        try: return self.ser.is_open
        except: return True
    def close(self):
        try: self.ser.close()
        except: pass

class SensorGUI:
    def __init__(self, root):
        self.root = root
        # GUI Setup
        self.root.title("Stork Aquatics Monitor Max V1.4.0 BETA")
        try:
            screen_h = self.root.winfo_screenheight()
        except Exception:
            screen_h = 800

        # Clamp to avoid extremes
        self.scale = max(0.7, min(1.6, screen_h / 800.0))

        def _s(size: int) -> int:
            # helper for scaled font sizes / padding
            return max(8, int(size * self.scale))

        self._s = _s

        self._tds_last_visibility = None   # cache tuple: (show_tds, show_uScm, show_sal)
        # --- Optional per-frame visibility (set to False to hide a frame) ---
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
        # --- Sensor firmware versions (populated later by sensor firmware; default UNKNOWN) ---
        try:
            sensor_ids = list(getattr(self, "sensors", {}).keys())
        except Exception:
            sensor_ids = []

        # If sensors aren't defined yet at this point, seed common IDs used by this build
        if not sensor_ids:
            sensor_ids = ["A", "B", "C", "D", "E"]

        self.sensor_firmware = {sid: "UNKNOWN" for sid in sensor_ids}

        self.fullscreen = True  # Track fullscreen on or off
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
        
        for pin in self.pump_gpio.values():
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)  

        # Per-sensor water level thresholds
        self.thresholds = {
            "A": {"on": 315, "off": 336},
            "B": {"on": 315, "off": 336},
           
        }
       
        self.display_units = {
            "A": {"use_liters": False, "use_gallons": False, "width": 0, "depth": 0, "use_fahrenheit": False},
            "B": {"use_liters": False, "use_gallons": False, "width": 0, "depth": 0, "use_fahrenheit": False},
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
            },
            "D": {
                "ph_alarm_enabled": False,
                "ph_min": 0,
                "ph_max": 0,
                "use_fahrenheit": False,
            },
            "E": {
                "tds_alarm_enabled": False,
                "tds_min": 0,
                "tds_max": 0,
                "use_fahrenheit": False,
            },
        }
        
        self.display_units.setdefault("E", {})
        self.display_units["E"].setdefault("show_fields", {
            "tds_ppm": True,         # TDS on by default
            "cond_uScm": False,      # (Optional) off by default 
            "sal_psu": False,        # (Optional) off bt default 
        })

        self.visual_settings = {
             "dark_mode": False,
             "colors": {
                 "water": "#0000FF",
                 "temp": "#FF0000",
                 "ph": "#800080",
                 "tds": "#A52A2A",
                 "cond": "#00FF00",
                 "sal": "#FFA500"
             }
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
        self.sound_paths = {
            "approaching": os.path.join("MAIN", "approaching_limit.wav"),
            "critical":    os.path.join("MAIN", "level_critical.wav"),
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
        self._wav_paths = {
            "approaching": os.path.join(self._base_dir, "MAIN", "approaching_limit.wav"),
            "critical":   os.path.join(self._base_dir, "MAIN", "level_critical.wav"),
        }
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
        self.use_frame_positions = True
 
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

        # Apply optional visibility toggles
        self.apply_frame_visibility()
        self.load_threshold_settings()
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
        popup.grab_set()

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
            self.root.after(0, lambda: messagebox.showerror("Update", err))
            return

        if not available:
            msg = f"No update available.\nCurrent: {__version__}\nLatest: {latest or __version__}"
            self.root.after(0, lambda: messagebox.showinfo("Update", msg))
            return

        # Confirm with user
        ok = messagebox.askyesno("Update available",
                                f"Update available: {latest}\nCurrent: {__version__}\n\nInstall now?")
        if not ok:
            return

        try:
            data = self._http_get_bytes(url, timeout=20)
            got_sha = self._sha256_hex(data)

            if expect_sha and got_sha != expect_sha.lower():
                self.root.after(0, lambda: messagebox.showerror(
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

            self.root.after(0, lambda: messagebox.showinfo(
                "Update",
                f"Updated to {latest}. Restarting now…"
            ))

            # Restart process
            python = sys.executable or "python3"
            os.execv(python, [python] + sys.argv)

        except Exception as e:
            self.root.after(0, lambda: messagebox.showerror("Update", f"Update failed: {e}"))

    def ui_check_gui_update(self):
        # Run update check/apply in background so Tkinter doesn't freeze
        threading.Thread(target=self.apply_gui_update, daemon=True).start()

    # Start to build frames  
    def create_sensor_frame(self, title, row, column, colspan=1):
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=10, pady=10)
        frame.grid(row=row, column=column, padx=10, pady=10, sticky="nsew", columnspan=colspan)

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
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=10, pady=10)
        frame.grid(row=row, column=column, padx=10, pady=10, sticky="nsew", columnspan=colspan)

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
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=10, pady=10)
        frame.grid(row=row, column=column, padx=10, pady=10, sticky="nsew", columnspan=colspan)

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
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=10, pady=10)
        frame.grid(row=row, column=column, padx=10, pady=10, sticky="nsew")

        # Pump Status
        pump_status = tk.Label(frame, text="OFF", font=("Arial", 14, "bold"), fg="red")
        pump_status.pack(pady=5)

        # Auto Top-Up Label
        auto_top_up_label = tk.Label(frame, text="", font=("Arial", 14, "bold"))
        auto_top_up_label.pack(pady=5)

        # Auto Mode Checkbox
        auto_mode_var = tk.BooleanVar(value=False)
        auto_checkbox = tk.Checkbutton(
            frame,
            text="Enable Auto Mode",
            variable=auto_mode_var,
            font=("Arial", 12),
            anchor="w"
        )
        auto_checkbox.pack(pady=5)

        # Toggle Button
        toggle_button = tk.Button(frame, text="Turn On")
        toggle_button.config(command=lambda: self.toggle_pump(title, pump_status, toggle_button))
        toggle_button.pack(pady=5)

        return {
            "frame": frame,
            "pump_status": pump_status,
            "auto_top_up_label": auto_top_up_label,
            "toggle_button": toggle_button,
            "auto_mode_var": auto_mode_var,
        }
    def apply_frame_visibility(self):
        """Show/hide top-level frames based on self.frame_visibility.
        Uses grid_remove() to keep layout state for quick re-enable."""
        mapping = {}
        try:
            mapping.update({
                "Aquarium A": self.aquarium_frame_1["frame"],
                "Aquarium B": self.aquarium_frame_2["frame"],
                "RO Tank": self.ro_tank_frame["frame"],
                "pH Sensor": self.ph_level_frame["frame"],
                "TDS Sensor": self.tds_level_frame["frame"],
                "RO Pump A": self.pump_frame_a["frame"],
                "RO Pump B": self.pump_frame_b["frame"],
                "RPi Image": self.image_frame_b["frame"],
                "www.stork.solutions": self.image_frame_c["frame"],
            })
        except Exception:
            # If not yet created, skip
            return
        for name, frame in mapping.items():
            show = self.frame_visibility.get(name, True)
            try:
                if show:
                    frame.grid()
                else:
                    frame.grid_remove()
            except Exception:
                pass
            
        if getattr(self, "use_frame_positions", False):
            self.apply_frame_positions_layout()
        else:
            self.reflow_grid()
      
    def reflow_grid(self):
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

        # Lay them out in full-width rows, 1 to 3 per row
        max_cols = 3
        row = 0
        col = 0

        for i, frame in enumerate(visible_frames):

            col = i % max_cols
            row = i // max_cols

            frame.grid(
                row=row,
                column=col,
                padx=10,
                pady=10,
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
                fr.grid(row=r, column=c, columnspan=cs, padx=10, pady=10, sticky="nsew")
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
        def toggle_color():
            current_color = label.cget("fg")
            label.config(fg="red" if current_color == "green" else "green")
            self.flashing_labels[pump_name] = label.after(500, toggle_color)

        # Cancel any existing flash before starting a new one
        self.stop_flashing(pump_name)

        label.config(text="AUTO TOP UP ACTIVE", fg="red")
        toggle_color()
   
    def stop_flashing(self, pump_name):
        if pump_name in self.flashing_labels:
            try:
                self.pump_frame_a["auto_top_up_label"].after_cancel(self.flashing_labels[pump_name])
            except:
                pass
            self.flashing_labels.pop(pump_name, None)

        # Also clear the label visually
        if pump_name == "RO Pump A":
            self.pump_frame_a["auto_top_up_label"].config(text="", fg="black")
        elif pump_name == "RO Pump B":
            self.pump_frame_b["auto_top_up_label"].config(text="", fg="black")
        elif pump_name == "RO Tank":
            self.ph_level_frame["connection_status"].config(text="Connected", fg="green")
        elif pump_name == "pH Sensor":
            self.ph_level_frame["connection_status"].config(text="Connected", fg="green")
        elif pump_name == "TDS Sensor":
            self.tds_level_frame["connection_status"].config(text="Connected", fg="green")

    def control_pumps(self, sensor_id, water_level_mmwg):
        pump_name = "RO Pump A" if sensor_id == "A" else "RO Pump B"
        pump_frame = self.pump_frame_a if sensor_id == "A" else self.pump_frame_b
        pump_status_label = pump_frame["pump_status"]
        auto_top_up_label = pump_frame["auto_top_up_label"]
        auto_mode = pump_frame["auto_mode_var"].get()
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
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=10, pady=10)
        frame.grid(row=row, column=column, padx=10, pady=10, sticky="nsew", columnspan=colspan)

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

    def create_image_frame_b(self, title, row, column, colspan=1):
        frame = tk.LabelFrame(self.root, text=title or "", font=("Arial", 16, "bold"), padx=10, pady=10)
        frame.grid(row=row, column=column, padx=10, pady=10, sticky="nsew", columnspan=colspan)

        # Fixed fit 
        max_w, max_h = 350, 200

        try:
            from pathlib import Path as _P
            base_dir = _P(__file__).resolve().parent
            img_path = base_dir / "MAIN" / "image2.png"
            if not img_path.exists():
                img_path = base_dir / "image2.png"  # fallback if you run inside MAIN

            if img_path.exists():
                original = tk.PhotoImage(file=str(img_path))
                # integer downscale to fit within max_w x max_h while preserving aspect
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
                tk.Label(frame, text="image2.png not found in MAIN/", fg="red").pack()
        except Exception as e:
            tk.Label(frame, text=f"Image error: {e}", fg="red").pack()

        return {"frame": frame}

    def create_image_frame_c(self, title, row, column, colspan=1):
        frame = tk.LabelFrame(self.root, text=title or "", font=("Arial", 16, "bold"), padx=10, pady=10)
        frame.grid(row=row, column=column, padx=10, pady=10, sticky="nsew", columnspan=colspan)

        # Fixed fit 
        max_w, max_h = 350, 200

        try:
            from pathlib import Path as _P
            base_dir = _P(__file__).resolve().parent
            img_path = base_dir / "MAIN" / "image1.png"
            if not img_path.exists():
                img_path = base_dir / "image1.png"  # fallback if you run inside MAIN

            if img_path.exists():
                original = tk.PhotoImage(file=str(img_path))
                # integer downscale to fit within max_w x max_h while preserving aspect
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
                tk.Label(frame, text="image1.png not found in MAIN/", fg="red").pack()
        except Exception as e:
            tk.Label(frame, text=f"Image error: {e}", fg="red").pack()

        return {"frame": frame}
     
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
        ip_var = tk.StringVar(value=ep.get("host", ""))  # fixed port 8888

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

        tk.Label(container, text="Pump ON Threshold:").pack()
        on_entry = tk.Entry(container)
        on_entry.pack(pady=2)

        tk.Label(container, text="Pump OFF Threshold:").pack()
        off_entry = tk.Entry(container)
        off_entry.pack(pady=2)

        tk.Label(container, text="", font=("Arial", 10)).pack(pady=5)
        tk.Label(container, text="(Required for Liter & Gallon Display)", font=("Arial", 10, "italic")).pack(pady=(10, 5))

        tk.Label(container, text="Width (cm):").pack()
        width_entry = tk.Entry(container)
        width_entry.pack(pady=2)

        tk.Label(container, text="Depth (cm):").pack()
        depth_entry = tk.Entry(container)
        depth_entry.pack(pady=2)

        # Fill initial values
        width = self.display_units[sensor_id].get("width", 0)
        depth = self.display_units[sensor_id].get("depth", 0)
        width_entry.insert(0, str(width))
        depth_entry.insert(0, str(depth))

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
                    if not messagebox.askyesno(
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
                messagebox.showerror("Invalid Input", str(e))

   
        tk.Button(container, text="Submit", command=save_thresholds).pack(pady=20)

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
            with open("settings.json", "w") as f:
                json.dump({
                    "thresholds": self.thresholds,
                    "display_units": self.display_units,
                    #"graphics_settings": getattr(self, "graphics_settings", {})
                    "visual_settings": self.visual_settings,
                    "sensor_firmware": getattr(self, "sensor_firmware", {}),
                    "endpoints": getattr(self, "endpoints", {}),
                    "tare_offsets": getattr(self, "tare_offsets", {"A":0.0,"B":0.0,"C":0.0}),
                    "frame_positions": getattr(self, "frame_positions", {}),
                    "use_frame_positions": getattr(self, "use_frame_positions", True),
                    "frame_visibility": getattr(self, "frame_visibility", {}),

                }, f, indent=4)
                print("[SAVE] Threshold and graphics settings saved.")
        except Exception as e:
            print(f"[SAVE ERROR] Failed to save settings: {e}")
   
    # Main settings loading      
    def load_threshold_settings(self):
        try:
            if os.path.exists("settings.json"):
                with open("settings.json", "r") as f:
                    data = json.load(f)
                    self.thresholds.update(data.get("thresholds", {}))
                    self.display_units.update(data.get("display_units", {}))
                    self.visual_settings.update(data.get("visual_settings", {}))
                    getattr(self, "sensor_firmware", {}).update(data.get("sensor_firmware", {}))
                    self.endpoints = data.get("endpoints", self.endpoints)
                    self.tare_offsets.update(data.get("tare_offsets", {"A":0.0, "B":0.0, "C":0.0}))
                    self.frame_positions.update(data.get("frame_positions", {}))
                    self.use_frame_positions = data.get("use_frame_positions", True)
                    self.frame_visibility.update(data.get("frame_visibility", {}))
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
        ip_var = tk.StringVar(value=ep.get("host", ""))  # fixed port 8888

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

        # Existing RO settings
        display_unit = self.display_units[sensor_id]
        
        r2_temp_var = tk.BooleanVar(value=display_unit.get("r2_temp_enabled", False))
        tk.Checkbutton(
            container,
            text="Activate Temperature (R2 Sensors ONLY)",
            variable=r2_temp_var
        ).pack(pady=(6, 2))
        
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

        width_label = tk.Label(container, text="Tank Width (cm):"); width_label.pack()
        width_entry = tk.Entry(container, textvariable=width_var); width_entry.pack()

        depth_label = tk.Label(container, text="Tank Depth (cm):"); depth_label.pack()
        depth_entry = tk.Entry(container, textvariable=depth_var); depth_entry.pack()

        alarm_check = tk.Checkbutton(container, text="Enable Level Alarm",
                                     variable=level_alarm_var, command=lambda: toggle_alarm_fields())
        alarm_check.pack(pady=10)

        min_label = tk.Label(container, text="Min Water Level:"); min_label.pack()
        min_entry = tk.Entry(container, textvariable=min_level_var); min_entry.pack()
        max_label = tk.Label(container, text="Max Water Level:"); max_label.pack()
        max_entry = tk.Entry(container, textvariable=max_level_var); max_entry.pack()

        def toggle_unit_fields():
            state = tk.NORMAL if use_liters_var.get() or use_gallons_var.get() else tk.DISABLED
            width_entry.config(state=state); depth_entry.config(state=state)
        def toggle_alarm_fields():
            state = tk.NORMAL if level_alarm_var.get() else tk.DISABLED
            for w in (min_label, min_entry, max_label, max_entry): w.config(state=state)
        toggle_unit_fields(); toggle_alarm_fields()

        def save_ro_alarm_settings():
            try:
                # persist connection choice (fixed 8888)
                ct = conn_type_var.get()
                host = ip_var.get().strip()
                if ct == "tcp" and not host:
                    raise ValueError("Please enter an IP address for Wi-Fi TCP.")
                self.endpoints[sensor_id] = {"type": ct, "host": host, "port": 8888}

                # existing RO settings save
                display_unit["level_alarm"] = level_alarm_var.get()
                display_unit["use_liters"] = use_liters_var.get()
                display_unit["use_gallons"] = use_gallons_var.get()
                display_unit["r2_temp_enabled"] = r2_temp_var.get()
                
                # Show & Hide Label
                temp_lbl = self.ro_tank_frame.get("temperature_label")
                if temp_lbl:
                    if display_unit["r2_temp_enabled"]:
                        if not temp_lbl.winfo_ismapped():
                            temp_lbl.pack(pady=10)
                    else:
                        if temp_lbl.winfo_ismapped():
                            temp_lbl.pack_forget()
                        temp_lbl.config(text="Temperature: --")  # reset text when hidden

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
                messagebox.showerror("Invalid Input", str(e))

        tk.Button(container, text="Submit", command=save_ro_alarm_settings).pack(pady=20)
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

        # Existing pH settings
        settings = self.display_units.get(sensor_id, {})
        use_fahrenheit_var = tk.BooleanVar(value=settings.get("use_fahrenheit", False))
        tk.Checkbutton(container, text="Show Temperature in °F", variable=use_fahrenheit_var).pack(pady=5)

        enable_alarm_var = tk.BooleanVar(value=settings.get("ph_alarm_enabled", False))
        tk.Checkbutton(container, text="Enable pH Alarm", variable=enable_alarm_var,
                       command=lambda: toggle_fields(), font=("Arial", 12)).pack(pady=10)

        min_label = tk.Label(container, text="Minimum pH Level:"); min_label.pack()
        min_ph_var = tk.StringVar(value=str(settings.get("ph_min", 0)))
        min_entry = tk.Entry(container, textvariable=min_ph_var); min_entry.pack()

        max_label = tk.Label(container, text="Maximum pH Level:"); max_label.pack()
        max_ph_var = tk.StringVar(value=str(settings.get("ph_max", 0)))
        max_entry = tk.Entry(container, textvariable=max_ph_var); max_entry.pack()

        def toggle_fields():
            st = tk.NORMAL if enable_alarm_var.get() else tk.DISABLED
            for w in (min_label, min_entry, max_label, max_entry): w.config(state=st)
        toggle_fields()

        def save_ph_settings():
            try:
                # persist connection choice (fixed 8888)
                ct = conn_type_var.get()
                host = ip_var.get().strip()
                if ct == "tcp" and not host:
                    raise ValueError("Please enter an IP address for Wi-Fi TCP.")
                self.endpoints[sensor_id] = {"type": ct, "host": host, "port": 8888}

                # existing pH settings save
                if enable_alarm_var.get():
                    min_val = float(min_ph_var.get()); max_val = float(max_ph_var.get())
                    if min_val >= max_val:
                        raise ValueError("Minimum pH must be less than maximum pH.")
                    settings["ph_min"] = min_val; settings["ph_max"] = max_val
                else:
                    settings["ph_min"] = 0; settings["ph_max"] = 0

                settings["ph_alarm_enabled"] = enable_alarm_var.get()
                settings["use_fahrenheit"] = use_fahrenheit_var.get()
                
                # If the alarm was just turned OFF, immediately normalize UI + sound/flash
                if not settings["ph_alarm_enabled"]:
                    self._set_alarm_state("ph_sensor", "normal", self.ph_level_frame["connection_status"])

                # if alarm disabled, ensure UI not flashing
                if not settings["ph_alarm_enabled"]:
                    self.stop_flashing("pH Sensor")
                    self.safe_gui_update(lambda: self.ph_level_frame["connection_status"].config(text="Connected", fg="green"))

                self.save_threshold_settings()
                self.show_success_popup(f"Sensor {sensor_id} Updated")
                popup.destroy()
            except Exception as e:
                messagebox.showerror("Invalid Input", str(e))

        tk.Button(container, text="Submit", command=save_ph_settings).pack(pady=20)
        tk.Button(
            container,
            text="Reset Sensor",
            state=tk.NORMAL if self.sensors.get(sensor_id, {}).get("is_running") else tk.DISABLED,
            command=lambda: (self.reset_sensor(sensor_id), popup.destroy())
        ).pack(pady=10)
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

        enable_alarm_var = tk.BooleanVar(value=settings.get("tds_alarm_enabled", False))
        tk.Checkbutton(container, text="Enable TDS Alarm", variable=enable_alarm_var,
                       command=lambda: toggle_fields(), font=("Arial", 12)).pack(pady=10)

        min_label = tk.Label(container, text="Minimum TDS Level:"); min_label.pack()
        min_tds_var = tk.StringVar(value=str(settings.get("tds_min", 0)))
        min_entry = tk.Entry(container, textvariable=min_tds_var); min_entry.pack()

        max_label = tk.Label(container, text="Maximum TDS Level:"); max_label.pack()
        max_tds_var = tk.StringVar(value=str(settings.get("tds_max", 0)))
        max_entry = tk.Entry(container, textvariable=max_tds_var); max_entry.pack()

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

        tk.Checkbutton(disp_grp, text="TDS (ppm)",            variable=tds_var).pack(anchor="w", padx=10, pady=3)
        tk.Checkbutton(disp_grp, text="Conductivity (µS/cm)", variable=uScm_var).pack(anchor="w", padx=10, pady=3)
        tk.Checkbutton(disp_grp, text="Salinity (PSU ≈ ppt)", variable=sal_var).pack(anchor="w", padx=10, pady=3)

        # Submit / Reset / Graphics
        def save_tds_settings():
            try:
                # persist connection choice (fixed port 8888)
                ct = conn_type_var.get()
                host = ip_var.get().strip()
                if ct == "tcp" and not host:
                    raise ValueError("Please enter an IP address for Wi-Fi TCP.")
                self.endpoints[sensor_id] = {"type": ct, "host": host, "port": 8888}

                # alarms & units
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
                settings["use_fahrenheit"]   = use_fahrenheit_var.get()

                # if alarm is OFF, normalize UI immediately
                if not settings["tds_alarm_enabled"]:
                    try:
                        self._set_alarm_state("tds_sensor", "normal", self.tds_level_frame["connection_status"])
                        self.stop_flashing("TDS Sensor")
                        self.safe_gui_update(lambda: self.tds_level_frame["connection_status"].config(text="Connected", fg="green"))
                    except Exception:
                        pass

                # visibility checkboxes: persist which readings to show on tile
                du = self.display_units.setdefault("E", {})
                du["show_fields"] = {
                    "tds_ppm":   bool(tds_var.get()),
                    "cond_uScm": bool(uScm_var.get()),
                    "sal_psu":   bool(sal_var.get()),
                }

                # persist to disk
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
                messagebox.showerror("Invalid Input", str(e))
            except Exception as e:
                messagebox.showerror("Error", f"Failed to save settings: {e}")

        tk.Button(container, text="Submit", command=save_tds_settings).pack(pady=20)
        tk.Button(
            container,
            text="Reset Sensor",
            state=tk.NORMAL if self.sensors.get(sensor_id, {}).get("is_running") else tk.DISABLED,
            command=lambda: (self.reset_sensor(sensor_id), popup.destroy())
        ).pack(pady=10)
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
        
        # User Adjustable Frames
        layout_box = tk.LabelFrame(container, text="Frame Layout (Positions)")
        layout_box.pack(fill="x", padx=12, pady=(30, 10))

        use_positions_var = tk.BooleanVar(value=getattr(self, "use_frame_positions", True))
        tk.Checkbutton(layout_box, text="Enable Custom Frame Layout", variable=use_positions_var).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 10)
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

            tk.Label(layout_box, text=name).grid(row=i, column=0, sticky="w", padx=10, pady=4)
            tk.Label(layout_box, text="Row").grid(row=i, column=1, sticky="e", padx=(10, 2))
            tk.Spinbox(layout_box, from_=0, to=9, width=4, textvariable=r_var).grid(row=i, column=2, sticky="w", padx=(0, 10))
            tk.Label(layout_box, text="Col").grid(row=i, column=3, sticky="e", padx=(10, 2))
            tk.Spinbox(layout_box, from_=0, to=9, width=4, textvariable=c_var).grid(row=i, column=4, sticky="w", padx=(0, 10))
            tk.Checkbutton(layout_box, variable=v_var).grid(row=i, column=5, sticky="w", padx=(0, 10))

        # BUTTON ROW
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
            
            # Apply frame layout choices
            self.use_frame_positions = use_positions_var.get()

            for name, (rv, cv) in pos_vars.items():
                self.frame_positions.setdefault(name, {})
                self.frame_positions[name]["row"] = int(rv.get())
                self.frame_positions[name]["col"] = int(cv.get())
                # keep existing colspan if present
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
            width=19,          # spans roughly same width as 2x width=9 buttons
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
                        # fallback to theme text colour
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

                    if wtype in ["Frame", "LabelFrame"]:
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
            messagebox.showinfo("Success", message)
           
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
                
                # quick probe (A/B/C: RX203, D: RX205, E: RX207)
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
            messagebox.showerror("Tare Failed", f"Could not read a valid level from Sensor {sensor_id}.")
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
                        port.sock.settimeout(0.5)  # small per-recv; your readline can set its own
                else:
                    # Serial: use in_waiting if available
                    try:
                        ser = getattr(port, "ser", port)  # wrapper or raw pyserial
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
                port.write(line.encode()) # raw pyserial expects bytes

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

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.aquarium_frame_1, temperature, water_level, None, None, None
                    ))

                    try:
                        wl = float(str(water_level).replace("mmWG","").replace("mBar","").strip())
                        self.control_pumps("A", self.tared_mmwg("A", wl))
                    except Exception:
                        pass

                elif sensor_id == "B":
                    temperature = _txrx(port, "RX201", settle=0.10, timeout_s=3.0)
                    water_level = _txrx(port, "RX203", settle=0.00, timeout_s=3.0)

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.aquarium_frame_2, temperature, water_level, None, None, None
                    ))
 
                    try:
                        wl = float(str(water_level).replace("mmWG","").replace("mBar","").strip())
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

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.ro_tank_frame, temperature, water_level, None, None, None
                    ))

                    try:
                        wl_mmwg = float(str(water_level).replace("mmWG","").replace("mBar","").strip())
                        self.check_ro_tank_alarm("C", self.tared_mmwg("C", wl_mmwg))
                    except Exception:
                        pass

                elif sensor_id == "D":
                    # pH sensor: temp then pH (give pH a bit more time)
                    temperature = _txrx(port, "RX201", settle=0.20, timeout_s=3.0)
                    ph_level    = _txrx(port, "RX205", settle=0.00, timeout_s=4.0)

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.ph_level_frame, temperature, None, ph_level, None, None, None
                    ))
                    if ph_level:
                        self.check_ph_alarm("D", ph_level)
                        
                elif sensor_id == "E":
                    # TDS sensor: temp then metrics
                    temperature      = _txrx(port, "RX201", settle=0.20, timeout_s=3.0)   # °C as string
                    cond_uScm_level  = _txrx(port, "RX206", settle=0.00, timeout_s=4.0)   # µS/cm (string)
                    tds_level        = _txrx(port, "RX207", settle=0.00, timeout_s=4.0)   # ppm (string)
                    sal_level        = _txrx(port, "RX208", settle=0.00, timeout_s=4.0)   # PSU ≈ ppt (string)

                    # Normalize text
                    def good(x): return bool(x) and x not in ("ERR", "--")
                    t_text   = f"{temperature} °C" if good(temperature) else "--"
                    tds_text = f"{tds_level} ppm"  if good(tds_level)    else "--"
                    cu_text  = f"{cond_uScm_level} µS/cm" if good(cond_uScm_level) else "--"
                    s_text   = f"{sal_level} PSU"  if good(sal_level)    else "--"

                    mode = self.display_units.get("E", {}).get("display_mode", "tds_ppm")

                    def _apply():
                        # connection + temp
                        self.tds_level_frame["connection_status"].config(text="Connected", fg="green")
                        self.tds_level_frame["temperature_label"].config(text=f"Temperature: {t_text}")

                        # update all sublabels so user can switch mode and see something
                        self.tds_level_frame["tds_level_label"].config(text=f"TDS: {tds_text}")
                        self.tds_level_frame["cond_uScm_level_label"].config(text=f"Conductivity: {cu_text}")
                        self.tds_level_frame["sal_level_label"].config(text=f"Salinity: {s_text}")

                        # reset fonts
                        base = ("Arial", 14, "bold")
                        self.tds_level_frame["tds_level_label"].config(font=base)
                        self.tds_level_frame["cond_uScm_level_label"].config(font=base)
                        self.tds_level_frame["sal_level_label"].config(font=base)
                        # make selected one a touch bigger
                        bigger = ("Arial", 15, "bold")
                        if   mode == "tds_ppm":  self.tds_level_frame["tds_level_label"].config(font=bigger)
                        elif mode == "cond_uScm": self.tds_level_frame["cond_uScm_level_label"].config(font=bigger)
                        elif mode == "sal_psu":   self.tds_level_frame["sal_level_label"].config(font=bigger)

                    self.safe_gui_update(lambda: (
                        # existing updates you already do…
                       self.tds_level_frame["connection_status"].config(text="Connected", fg="green"),
                       self.tds_level_frame["temperature_label"].config(text=f"Temperature: {t_text}"),
                       self.tds_level_frame["tds_level_label"].config(text=f"TDS: {tds_text}"),
                       self.tds_level_frame["cond_uScm_level_label"].config(text=f"Conductivity: {cu_text}"),
                       self.tds_level_frame["sal_level_label"].config(text=f"Salinity: {s_text}"),
                       self.safe_gui_update(self.layout_tds_tile)
                    ))

            except Exception as e:
                print(f"[ERROR] read_sensor_data({sensor_id}): {e}")
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
                        # reconnect_sensor already reset counts/flags, but be explicit
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
            # fall through to serial scan as last resort

        # serial scan (your existing code)
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

            # show/update the text
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
                # fall back to raw string
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

                # APPLY TARE OFFSET
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
            self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to reset sensor {sensor_id}: {e}"))

    def check_ro_tank_alarm(self, sensor_id, wl_mmwg):
        """
        sensor_id should be 'C'
        Uses display_units['C'] keys:
          level_alarm (bool), use_liters/use_gallons (bool), width, depth,
          min_alarm, max_alarm
        Converts wl_mmwg to selected unit; if invalid config, falls back to normal.
        """
        label = self.ro_tank_frame["connection_status"]
        try:
            settings = self.display_units.get(sensor_id, {})
            if not settings.get("level_alarm", False):
                self._set_alarm_state("ro_tank", "normal", label)
                return

            # reading -> chosen unit
            wl_val = self._num(wl_mmwg)
            if wl_val is None:
                self._set_alarm_state("ro_tank", "normal", label)
                return

            use_liters  = bool(settings.get("use_liters", False))
            use_gallons = bool(settings.get("use_gallons", False))
            width = self._num(settings.get("width"))
            depth = self._num(settings.get("depth"))

            if use_liters or use_gallons:
                if not width or not depth or width <= 0 or depth <= 0:
                    print("[RO ALARM] Missing/invalid width/depth; treating as normal.")
                    self._set_alarm_state("ro_tank", "normal", label)
                    return
                liters = wl_val * width * depth / 10000.0
                value = liters if use_liters else liters * 0.264172
            else:
                # raw mmWG mode
                value = wl_val

            lo = self._num(settings.get("min_alarm"))
            hi = self._num(settings.get("max_alarm"))
            if lo is None or hi is None or lo >= hi:
                print("[RO ALARM] Invalid thresholds; treating as normal.")
                self._set_alarm_state("ro_tank", "normal", label)
                return

            margin = 50 if not (use_liters or use_gallons) else (2.0 if use_liters else 0.5)
            if value <= lo or value >= hi:
                self._set_alarm_state("ro_tank", "critical", label)
            elif (lo < value <= lo + margin) or (hi - margin <= value < hi):
                self._set_alarm_state("ro_tank", "approach", label)
            else:
                self._set_alarm_state("ro_tank", "normal", label)

        except Exception as e:
            print("[RO ALARM] Exception:", e)
            try:
                import traceback; traceback.print_exc()
            except Exception:
                pass
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

    def _set_alarm_state(self, sensor_key, state, label):
        """sensor_key: 'ro_tank' or 'ph_sensor' or 'tds_sensor'"""
        # Avoid thrash if unchanged
        if self.current_status_text.get(sensor_key, "") == state:
            return
        self.current_status_text[sensor_key] = state

        if state == "normal":
            # stop flashing + set green
            self.stop_alarm_flash(sensor_key)
            try:
                label.config(text="Connected", fg="green")
            except Exception:
                pass
                # also stop any alarm sounds if you’ve added them
                if hasattr(self, "_reset_alarm_sound_state"):
                    self._reset_alarm_sound_state(sensor_key)
                elif state == "approach":
                    label.config(text="APPROACHING LIMIT")
                    self.start_alarm_flash(label, sensor_key, base_color="orange")
                    if hasattr(self, "_maybe_play_alarm"):
                        self._maybe_play_alarm(sensor_key, severity="approach")
                    elif state == "critical":
                        label.config(text="LEVEL CRITICAL")
                        self.start_alarm_flash(label, sensor_key, base_color="red")
                        if hasattr(self, "_maybe_play_alarm"):
                            self._maybe_play_alarm(sensor_key, severity="critical")
 
    def check_ph_alarm(self, sensor_id, ph_reading):
        """
        sensor_id should be 'D'
        Uses display_units['D'] keys:
          ph_alarm_enabled (bool), ph_min (floaty), ph_max (floaty)
        """
        label = self.ph_level_frame["connection_status"]
        try:
            settings = self.display_units.get(sensor_id, {})
            if not settings.get("ph_alarm_enabled", False):
                # alarm disabled => normal
                self._set_alarm_state("ph_sensor", "normal", label)
                return

            val = self._num(ph_reading)
            if val is None:
                # No valid reading => do not alarm; show normal
                self._set_alarm_state("ph_sensor", "normal", label)
                return

            lo = self._num(settings.get("ph_min"))
            hi = self._num(settings.get("ph_max"))
            if lo is None or hi is None or lo >= hi:
                # misconfigured thresholds => treat as disabled
                print("[PH ALARM] Invalid thresholds; treating as normal.")
                self._set_alarm_state("ph_sensor", "normal", label)
                return

            margin = 0.5  # near-threshold buffer
            if val <= lo or val >= hi:
                self._set_alarm_state("ph_sensor", "critical", label)
            elif (lo < val <= lo + margin) or (hi - margin <= val < hi):
                self._set_alarm_state("ph_sensor", "approach", label)
            else:
                self._set_alarm_state("ph_sensor", "normal", label)

        except Exception as e:
            # never surface "Alarm Error" to UI; log and show normal
            print("[PH ALARM] Exception:", e)
            try:
                import traceback; traceback.print_exc()
            except Exception:
                pass
            self._set_alarm_state("ph_sensor", "normal", label) 

    def check_tds_alarm(self, sensor_id, tds_reading):
            """
            sensor_id should be 'E'
            Uses display_units['E'] keys:
              tds_alarm_enabled (bool), tds_min (floaty), tds_max (floaty)
            """
            label = self.tds_level_frame["connection_status"]
            try:
                settings = self.display_units.get(sensor_id, {})
                if not settings.get("tds_alarm_enabled", False):
                    # alarm disabled => normal
                    self._set_alarm_state("tds_sensor", "normal", label)
                    return

                val = self._num(tds_reading)
                if val is None:
                    # No valid reading => do not alarm; show normal
                    self._set_alarm_state("tds_sensor", "normal", label)
                    return

                lo = self._num(settings.get("tds_min"))
                hi = self._num(settings.get("tds_max"))
                if lo is None or hi is None or lo >= hi:
                    # misconfigured thresholds => treat as disabled
                    print("[TDS ALARM] Invalid thresholds; treating as normal.")
                    self._set_alarm_state("tds_sensor", "normal", label)
                    return

                margin = 0.5  # near-threshold buffer
                if val <= lo or val >= hi:
                    self._set_alarm_state("tds_sensor", "critical", label)
                elif (lo < val <= lo + margin) or (hi - margin <= val < hi):
                    self._set_alarm_state("tds_sensor", "approach", label)
                else:
                    self._set_alarm_state("tds_sensor", "normal", label)

            except Exception as e:
                # never surface "Alarm Error" to UI; log and show normal
                print("[TDS ALARM] Exception:", e)
                try:
                    import traceback; traceback.print_exc()
                except Exception:
                    pass
                self._set_alarm_state("tds_sensor", "normal", label)

    def start_alarm_flash(self, label, sensor_key, base_color):
        """Flash between base_color and its alt shade. Cancels any existing job for this sensor."""
        alt = "#CC8400" if base_color == "orange" else "#A52A2A"

        # cancel any stale job first
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

            # If a different sound is playing, stop it first
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

    def _maybe_play_alarm(self, sensor_id, state, min_interval=2.0):
        key = "approaching" if state == "approaching" else "critical"
        now = _time.monotonic()
        k = (sensor_id, state)
        if (now - self.alarm_last_play.get(k, 0.0)) < min_interval:
            return
        self.alarm_last_play[k] = now
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
  
    def _set_alarm_state(self, sensor_id, new_state, label):
        """
        new_state: 'normal' | 'approaching' | 'critical'
        Edge-triggered: only acts when state changes.
        """
        prev = self.alarm_state.get(sensor_id, "normal")
        if prev == new_state:
            return  # no change; prevents sound echo and re-flash

        # Stop old effects first
        self.stop_alarm_flash(sensor_id, restore=False, label=label)
        self._reset_alarm_sound_state()

        # Apply new state
        self.alarm_state[sensor_id] = new_state

        if new_state == "normal":
            label.config(text="Connected", fg="green")
            return

        base = "orange" if new_state == "approaching" else "red"
        self.start_alarm_flash(label, sensor_id, base)
        self._maybe_play_alarm(sensor_id, new_state)
        
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

        # Fixed order:
        if status:   status.pack(anchor="n")
        if show_tds and tds_lbl:  tds_lbl.pack(pady=6)
        if temp_lbl: temp_lbl.pack(pady=6)
        if show_u   and u_lbl:    u_lbl.pack(pady=6)
        if show_sal and s_lbl:    s_lbl.pack(pady=6)

        # Settings button always last
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

        # Turn off pumps safely
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
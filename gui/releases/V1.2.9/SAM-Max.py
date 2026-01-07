import tkinter as tk
from tkinter import messagebox
import serial
import serial.tools.list_ports
import threading
import time
import RPi.GPIO as GPIO
import os
import json

__version__ = "1.2.9"

class SensorGUI:
    def __init__(self, root):
        self.root = root
        # GUI Setup
        self.root.title("Stork Aquatics Monitor Max V1.2.9")
        self.fullscreen = True  # Track fullscreen on or off
        self.fullscreen = False  # Track fullscreen state
        # Bind double-click anywhere on the window to toggle fullscreen
        self.root.bind("<Double-Button-1>", self.toggle_fullscreen)
        self.root.bind("<F11>", self.toggle_fullscreen)
        self.root.bind("<Escape>", self.exit_fullscreen)
        # Configure resizing for various screen types
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)
        self.connect_to_sensors()
        self.root.after(3000, lambda: threading.Thread(target=self.sensor_watchdog, daemon=True).start())

        # GPIO setup for pumps
        GPIO.setmode(GPIO.BCM)  
        self.pump_gpio = {
            "RO Pump A": 4, # GPIO Assignment R1=4,R2=27,R3=22,R4=17
            "RO Pump B": 27,
        }
        for pin in self.pump_gpio.values():
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)  
                 
        self.load_threshold_settings()

        # Per-sensor water level thresholds
        self.thresholds = {
            "A": {"on": 315, "off": 336},
            "B": {"on": 315, "off": 336},
            
        }
       
        self.display_units = {
           "A": {"use_liters": False, "use_gallons": False, "width": 0, "depth": 0},
           "B": {"use_liters": False, "use_gallons": False, "width": 0, "depth": 0},
           "C": {
               "use_liters": False,
               "use_gallons": False,
               "width": 0,
               "depth": 0,
               "capacity": 0,
               "level_alarm": False,
               "min_alarm": 0,
               "max_alarm": 0
        },
        "D": {
            "ph_alarm_enabled": False,
            "ph_min": 0,
            "ph_max": 0
        }
   }
     

        # Serial port connections
        self.sensors = {
            "A": {"port": None, "is_running": False},
            "B": {"port": None, "is_running": False},
            "C": {"port": None, "is_running": False},
            "D": {"port": None, "is_running": False},
        }

        # Pump states
        self.pump_states = {
            "RO Pump A": False,
            "RO Pump B": False,
        }
        self.override_states = {
            "RO Pump A": False,
            "RO Pump B": False,
        }
       
        self.flash_jobs = {
            "RO Pump A": None,
            "RO Pump B": None,
        }
        self.flash_jobs = {}
        self.flashing_labels = {}  # To keep track of flashing loop callbacks

        # Main Grid Layout
        self.aquarium_frame_1 = self.create_sensor_frame("Aquarium A", 0, 0)
        self.aquarium_frame_2 = self.create_sensor_frame("Aquarium B", 0, 1)
        self.ro_tank_frame = self.create_ro_tank_frame("RO Tank", 1, 0)
        self.ph_level_frame = self.create_ph_level_frame("pH Sensor", 1, 1)
        self.pump_frame_a = self.create_pump_frame("RO Pump A", 2, 0)
        self.pump_frame_b = self.create_pump_frame("RO Pump B", 2, 1)
       

        # Automatically connect to sensors and start readings
        self.connect_to_sensors()

    def create_sensor_frame(self, title, row, column, colspan=1):
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=10, pady=10)
        frame.grid(row=row, column=column, padx=10, pady=10, sticky="nsew", columnspan=colspan)

        # Connection Status
        connection_status_label = tk.Label(frame, text="Status:", font=("Arial", 14, "bold"), fg="black")
        connection_status_label.pack(anchor="n", pady=(5, 0))
        connection_status = tk.Label(frame, text="Disonnected!", font=("Arial", 14, "bold"), fg="red")
        connection_status.pack(anchor="n")

        # Readings
        water_gauge_label = tk.Label(frame, text="Water Level: ", font=("Arial", 14, "bold"))
        water_gauge_label.pack(pady=10)
        temperature_label = tk.Label(frame, text="Temperature: --", font=("Arial", 14, "bold"))
        temperature_label.pack(pady=10)
       
        # Settings Button
        settings_button = tk.Button(frame, text="Settings", command=lambda sid=title[-1]: self.open_settings_popup(sid))
        settings_button.pack(pady=5)

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
        water_gauge_label = tk.Label(frame, text="Water Level:--", font=("Arial", 14, "bold"))
        water_gauge_label.pack(pady=10)
        
        settings_button = tk.Button(frame, text="Settings", command=self.open_ro_settings_popup)
        settings_button.pack(pady=5)

        return {
            "frame": frame,
            "connection_status": connection_status,
            "water_gauge_label": water_gauge_label,
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
        ph_level_label = tk.Label(frame, text="pH: --", font=("Arial", 14, "bold"))
        ph_level_label.pack(pady=10)
        temperature_label = tk.Label(frame, text="Temperature: --", font=("Arial", 14, "bold"))
        temperature_label.pack(pady=10)
        
        settings_button = tk.Button(frame, text="Settings", command=self.open_ph_settings_popup)
        settings_button.pack(pady=5)

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
   
        # ✅ If manually turned OFF, disable auto mode checkbox
        if user_override and not self.pump_states[pump_name] and not suppress_auto_disable:
            print(f"[OVERRIDE] User cancelled pump '{pump_name}', disabling auto mode.")
            if pump_name == "RO Pump A":
                self.pump_frame_a["auto_mode_var"].set(False)
            elif pump_name == "RO Pump B":
                self.pump_frame_b["auto_mode_var"].set(False)

        # Stop flashing regardless of pump state
        self.stop_flashing(pump_name)
       
    def open_settings_popup(self, sensor_id):
        popup = tk.Toplevel(self.root)
        popup.title(f"Settings for Sensor {sensor_id}")
        popup.geometry("400x500")
        popup.resizable(False, False)

        tk.Label(popup, text=f"Set Thresholds for Aquarium {sensor_id}", font=("Arial", 12, "bold")).pack(pady=10)

        # Unit selection checkboxes
        use_liters_var = tk.BooleanVar(value=self.display_units[sensor_id].get("use_liters", False))
        use_gallons_var = tk.BooleanVar(value=self.display_units[sensor_id].get("use_gallons", False))

        def on_liters_toggle():
            if use_liters_var.get():
                use_gallons_var.set(False)
            toggle_fields()

        def on_gallons_toggle():
            if use_gallons_var.get():
                use_liters_var.set(False)
            toggle_fields()

        tk.Checkbutton(popup, text="Display in Liters", variable=use_liters_var, command=on_liters_toggle).pack(pady=2)
        tk.Checkbutton(popup, text="Display in Gallons", variable=use_gallons_var, command=on_gallons_toggle).pack(pady=2)

        # Threshold inputs
        tk.Label(popup, text="Pump ON Threshold:").pack()
        on_entry = tk.Entry(popup)
        on_entry.pack(pady=2)

        tk.Label(popup, text="Pump OFF Threshold:").pack()
        off_entry = tk.Entry(popup)
        off_entry.pack(pady=2)

        # Tank dimensions section
        tk.Label(popup, text="").pack(pady=5)
        tk.Label(popup, text="(Required for Liter & Gallon Display)", font=("Arial", 10, "italic")).pack(pady=(10, 5))

        tk.Label(popup, text="Width (cm):").pack()
        width_entry = tk.Entry(popup)
        width_entry.pack(pady=2)

        tk.Label(popup, text="Depth (cm):").pack()
        depth_entry = tk.Entry(popup)
        depth_entry.pack(pady=2)

        # Pre-fill values
        width = self.display_units[sensor_id]["width"]
        depth = self.display_units[sensor_id]["depth"]
        width_entry.insert(0, str(width))
        depth_entry.insert(0, str(depth))
  
        # Pre-fill threshold values in selected unit
        on_val = self.thresholds[sensor_id]["on"]
        off_val = self.thresholds[sensor_id]["off"]
        if use_liters_var.get():
            on_liters = (on_val * width * depth) / 10000
            off_liters = (off_val * width * depth) / 10000
            on_entry.insert(0, f"{on_liters:.2f}")
            off_entry.insert(0, f"{off_liters:.2f}")
        elif use_gallons_var.get():
            on_gallons = ((on_val * width * depth) / 10000) * 0.264172
            off_gallons = ((off_val * width * depth) / 10000) * 0.264172
            on_entry.insert(0, f"{on_gallons:.2f}")
            off_entry.insert(0, f"{off_gallons:.2f}")
        else:
            on_entry.insert(0, str(on_val))
            off_entry.insert(0, str(off_val))

        def toggle_fields():
            state = tk.NORMAL if (use_liters_var.get() or use_gallons_var.get()) else tk.DISABLED
            width_entry.config(state=state)
            depth_entry.config(state=state)

        toggle_fields()

        # ✅ Submit button function
        def save_thresholds():
            try:
                on_val = float(on_entry.get())
                off_val = float(off_entry.get())
                if on_val >= off_val:
                    raise ValueError("ON threshold must be less than OFF threshold.")

                width = float(width_entry.get())
                depth = float(depth_entry.get())
                if (use_liters_var.get() or use_gallons_var.get()) and (width <= 0 or depth <= 0):
                    raise ValueError("Width and Depth must be positive numbers.")

                if use_liters_var.get():
                    mmwg_on = (on_val * 10000) / (width * depth)
                    mmwg_off = (off_val * 10000) / (width * depth)
                elif use_gallons_var.get():
                    liters_on = on_val / 0.264172
                    liters_off = off_val / 0.264172
                    mmwg_on = (liters_on * 10000) / (width * depth)
                    mmwg_off = (liters_off * 10000) / (width * depth)
                else:
                    mmwg_on = on_val
                    mmwg_off = off_val

                self.thresholds[sensor_id]["on"] = mmwg_on
                self.thresholds[sensor_id]["off"] = mmwg_off
                self.display_units[sensor_id]["width"] = width if (use_liters_var.get() or use_gallons_var.get()) else 0
                self.display_units[sensor_id]["depth"] = depth if (use_liters_var.get() or use_gallons_var.get()) else 0
                self.display_units[sensor_id]["use_liters"] = use_liters_var.get()
                self.display_units[sensor_id]["use_gallons"] = use_gallons_var.get()

                self.save_threshold_settings()
                messagebox.showinfo("Success", f"Thresholds updated for Sensor {sensor_id}")
                popup.destroy()
            except Exception as e:
                messagebox.showerror("Invalid Input", str(e))

        tk.Button(popup, text="Submit", command=save_thresholds).pack(pady=20)
        sensor = self.sensors.get(sensor_id, {})
        serial_port = sensor.get("port")

        tk.Button(
            popup,
            text="Reset Sensor",
            state=tk.NORMAL if serial_port and serial_port.is_open else tk.DISABLED,
            command=lambda: (self.reset_sensor(serial_port), popup.destroy())
        ).pack(pady=10)

    def toggle_dimension_fields():
        state = tk.NORMAL if use_liters_var.get() else tk.DISABLED
        width_entry.config(state=state)
        depth_entry.config(state=state)
 
    def save_threshold_settings(self):
        try:
            with open("settings.json", "w") as f:
                json.dump({
                    "thresholds": self.thresholds,
                    "display_units": self.display_units
                }, f, indent=4)
                print("[SAVE] Threshold settings saved.")
        except Exception as e:
            print(f"[SAVE ERROR] Failed to save settings: {e}")
           
    def load_threshold_settings(self):
        try:
            if os.path.exists("settings.json"):
                with open("settings.json", "r") as f:
                    data = json.load(f)
                    self.thresholds.update(data.get("thresholds", {}))
                    self.display_units.update(data.get("display_units", {}))
                    print("[LOAD] Threshold settings loaded.")
            else:
                print("[LOAD] No settings file found. Using defaults.")
        except Exception as e:
            print(f"[LOAD ERROR] Failed to load settings: {e}")

    def open_ro_settings_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("RO Tank Settings")
        popup.geometry("400x500")
        popup.resizable(False, False)

        sensor_id = "C"
        display_unit = self.display_units[sensor_id]

        # Load values
        level_alarm_var = tk.BooleanVar(value=display_unit.get("level_alarm", False))
        use_liters_var = tk.BooleanVar(value=display_unit.get("use_liters", False))
        use_gallons_var = tk.BooleanVar(value=display_unit.get("use_gallons", False))

        width_var = tk.StringVar(value=str(display_unit.get("width", "")))
        depth_var = tk.StringVar(value=str(display_unit.get("depth", "")))
        min_level_var = tk.StringVar(value=str(display_unit.get("min_alarm", "")))
        max_level_var = tk.StringVar(value=str(display_unit.get("max_alarm", "")))

        # Unit checkboxes
        tk.Label(popup, text="Display Units:").pack()
        tk.Checkbutton(popup, text="Liters", variable=use_liters_var,
                       command=lambda: (use_gallons_var.set(False), toggle_unit_fields())).pack()
        tk.Checkbutton(popup, text="Gallons", variable=use_gallons_var,
                      command=lambda: (use_liters_var.set(False), toggle_unit_fields())).pack()

        # Tank dimensions (always required for alarm or unit display)
        width_label = tk.Label(popup, text="Tank Width (cm):")
        width_label.pack()
        width_entry = tk.Entry(popup, textvariable=width_var)
        width_entry.pack()

        depth_label = tk.Label(popup, text="Tank Depth (cm):")
        depth_label.pack()
        depth_entry = tk.Entry(popup, textvariable=depth_var)
        depth_entry.pack()

        # Enable alarm checkbox
        alarm_check = tk.Checkbutton(popup, text="Enable Level Alarm", variable=level_alarm_var, command=lambda: toggle_alarm_fields())
        alarm_check.pack(pady=10)

        # Min/Max level alarms
        min_label = tk.Label(popup, text="Min Water Level:")
        min_label.pack()
        min_entry = tk.Entry(popup, textvariable=min_level_var)
        min_entry.pack()

        max_label = tk.Label(popup, text="Max Water Level:")
        max_label.pack()
        max_entry = tk.Entry(popup, textvariable=max_level_var)
        max_entry.pack()

        def toggle_unit_fields():
            state = tk.NORMAL if use_liters_var.get() or use_gallons_var.get() else tk.DISABLED
            width_entry.config(state=state)
            depth_entry.config(state=state)

        def toggle_alarm_fields():
            state = tk.NORMAL if level_alarm_var.get() else tk.DISABLED
            min_label.config(state=state)
            min_entry.config(state=state)
            max_label.config(state=state)
            max_entry.config(state=state)
            width_label.config(state=state)
            width_entry.config(state=state)
            depth_label.config(state=state)
            depth_entry.config(state=state)

        toggle_alarm_fields()

        # Save button
        def save_ro_alarm_settings():
            try:
                display_unit["level_alarm"] = level_alarm_var.get()
                display_unit["use_liters"] = use_liters_var.get()
                display_unit["use_gallons"] = use_gallons_var.get()

                width = float(width_var.get())
                depth = float(depth_var.get())

                if width <= 0 or depth <= 0:
                    raise ValueError("Width and Depth must be positive numbers.")

                display_unit["width"] = width
                display_unit["depth"] = depth

                if level_alarm_var.get():
                    min_val = float(min_level_var.get())
                    max_val = float(max_level_var.get())

                    if min_val >= max_val:
                        raise ValueError("Min level must be less than Max level.")
                    display_unit["min_alarm"] = min_val
                    display_unit["max_alarm"] = max_val
                else:
                    display_unit["min_alarm"] = 0
                    display_unit["max_alarm"] = 0
  
                self.save_threshold_settings()
                popup.destroy()

            except Exception as e:
                messagebox.showerror("Invalid Input", str(e))

        tk.Button(popup, text="Submit", command=save_ro_alarm_settings).pack(pady=20)
        
        serial_port = self.sensors.get("C", {}).get("port")
        tk.Button(
            popup,
            text="Reset Sensor",
            state=tk.NORMAL if serial_port and serial_port.is_open else tk.DISABLED,
            command=lambda: (self.reset_sensor(serial_port), popup.destroy())
        ).pack(pady=10)
  
    def open_ph_settings_popup(self):
        popup = tk.Toplevel(self.root)
        popup.title("pH Sensor Settings")
        popup.geometry("400x300")
        popup.resizable(False, False)

        sensor_id = "D"
        settings = self.display_units.get(sensor_id, {})

        enable_alarm_var = tk.BooleanVar(value=settings.get("ph_alarm_enabled", False))
        min_ph_var = tk.StringVar(value=str(settings.get("ph_min", 0)))
        max_ph_var = tk.StringVar(value=str(settings.get("ph_max", 0)))

        # Alarm checkbox
        tk.Checkbutton(popup, text="Enable pH Alarm", variable=enable_alarm_var, command=lambda: toggle_fields()).pack(pady=10)

        # Min/Max fields
        min_label = tk.Label(popup, text="Minimum pH Level:")
        min_label.pack()
        min_entry = tk.Entry(popup, textvariable=min_ph_var)
        min_entry.pack()

        max_label = tk.Label(popup, text="Maximum pH Level:")
        max_label.pack()
        max_entry = tk.Entry(popup, textvariable=max_ph_var)
        max_entry.pack()

        def toggle_fields():
            state = tk.NORMAL if enable_alarm_var.get() else tk.DISABLED
            min_label.config(state=state)
            min_entry.config(state=state)
            max_label.config(state=state)
            max_entry.config(state=state)

        toggle_fields()

        def save_ph_settings():
            try:
                if enable_alarm_var.get():
                    min_val = float(min_ph_var.get())
                    max_val = float(max_ph_var.get())
                    if min_val >= max_val:
                        raise ValueError("Minimum pH must be less than maximum pH.")
                    settings["ph_min"] = min_val
                    settings["ph_max"] = max_val
                else:
                    settings["ph_min"] = 0
                    settings["ph_max"] = 0

                settings["ph_alarm_enabled"] = enable_alarm_var.get()

                self.save_threshold_settings()
                popup.destroy()

            except Exception as e:
                messagebox.showerror("Invalid Input", str(e))

        tk.Button(popup, text="Submit", command=save_ph_settings).pack(pady=20)
        
        serial_port = self.sensors.get("D", {}).get("port")
        tk.Button(
            popup,
            text="Reset Sensor",
            state=tk.NORMAL if serial_port and serial_port.is_open else tk.DISABLED,
            command=lambda: (self.reset_sensor(serial_port), popup.destroy())
        ).pack(pady=10)

    def connect_to_sensors(self):
        print("Searching for available COM ports...")
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            messagebox.showerror("Error", "No COM ports available.")
            return

        for port in ports:
            print(f"Checking port: {port.device}")
            try:
                serial_port = serial.Serial(port.device, baudrate=9600, timeout=2)
                serial_port.write("RX800\n".encode())
                sensor_id = serial_port.readline().decode().strip()
                print(f"Sensor Response from {port.device}: {sensor_id}")

                if sensor_id in self.sensors:
                    # Send real sensor read command
                    if sensor_id in ["A", "B", "C"]:
                        serial_port.write("RX203\n".encode())  # Water level
                        response = serial_port.readline().decode().strip()
                    elif sensor_id == "D":
                        serial_port.write("RX205\n".encode())  # pH
                        response = serial_port.readline().decode().strip()
                        if not response:
                            # Wait a moment and try one more time
                            time.sleep(0.5)
                            serial_port.write("RX205\n".encode())
                            response = serial_port.readline().decode().strip()

                    else:
                        response = ""

                    # Validate response
                    if not response or response.lower() == "none":
                        print(f"[SKIP] Sensor {sensor_id} gave no valid data.")
                        serial_port.close()
                        continue

                    # Save sensor connection
                    self.sensors[sensor_id]["port"] = serial_port
                    self.sensors[sensor_id]["is_running"] = True

                    # Setup UI
                    if sensor_id == "A":
                        self.setup_sensor_ui(self.aquarium_frame_1, serial_port)
                    elif sensor_id == "B":
                        self.setup_sensor_ui(self.aquarium_frame_2, serial_port)
                    elif sensor_id == "C":
                        self.setup_sensor_ui(self.ro_tank_frame, serial_port)
                    elif sensor_id == "D":
                        self.setup_sensor_ui(self.ph_level_frame, serial_port)

                    # Start reading thread
                    threading.Thread(target=self.read_sensor_data, args=(sensor_id,), daemon=True).start()

                else:
                    print(f"[SKIP] Unknown sensor ID: {sensor_id}")
                    serial_port.close()

            except Exception as e:
                print(f"Exception: {e}")
                
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
        
    def set_sensor_disconnected(self, frame):
        try:
            frame["connection_status"].config(text="Disconnected", fg="red")
            frame["reset_button"].config(state=tk.DISABLED)
        except Exception as e:
            print(f"[UI ERROR] Failed to update disconnected status: {e}")

    def read_sensor_data(self, sensor_id):
        while self.sensors[sensor_id]["is_running"]:
            try:
                serial_port = self.sensors[sensor_id]["port"]

                temperature = None
                water_level = None
                ph_level = None

                if sensor_id == "A":
                    serial_port.write("RX201\n".encode())
                    temperature = serial_port.readline().decode().strip()
                    serial_port.write("RX203\n".encode())
                    water_level = serial_port.readline().decode().strip()

                    print(f"[READ] Sensor A - Temp: {temperature}, Level: {water_level}, pH: None")

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.aquarium_frame_1, temperature, water_level, None
                    ))
                   
                    try:
                        wl_float = float(water_level.replace("mmWG", "").replace("mBar", "").strip())
                        self.control_pumps(sensor_id, wl_float)
                    except ValueError:
                        print(f"[WARN] Invalid water level from Sensor A: {water_level}")
               
                elif sensor_id == "B":
                    serial_port.write("RX201\n".encode())
                    temperature = serial_port.readline().decode().strip()
                    serial_port.write("RX203\n".encode())
                    water_level = serial_port.readline().decode().strip()

                    print(f"[READ] Sensor B - Temp: {temperature}, Level: {water_level}, pH: None")

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.aquarium_frame_2, temperature, water_level, None
                    ))
                   
                    try:
                        wl_float = float(water_level.replace("mmWG", "").replace("mBar", "").strip())
                        self.control_pumps(sensor_id, wl_float)
                    except ValueError:
                        print(f"[WARN] Invalid water level from Sensor A: {water_level}")

                elif sensor_id == "C":
                    try:
                        serial_port.write("RX203\n".encode())
                        water_level = serial_port.readline().decode().strip()
                        print(f"[READ] Sensor C - Temp: None, Level: {water_level}, pH: None")

                        self.safe_gui_update(lambda: self.update_sensor_ui(
                            self.ro_tank_frame, None, water_level, None
                        ))

                        try:
                            wl_mmwg = float(water_level.replace("mmWG", "").replace("mBar", "").strip())
                            self.check_ro_tank_alarm("C", wl_mmwg)
                        except ValueError:
                            print(f"[WARN] Invalid water level from Sensor C: {water_level}")
                    except Exception as e:
                        print(f"[ERROR] Sensor C read error: {e}")
                        self.sensors[sensor_id]["is_running"] = False
                        break

                elif sensor_id == "D":
                   try:
                       # Read temperature
                       serial_port.write("RX201\n".encode())
                       temperature = serial_port.readline().decode().strip()
                       time.sleep(0.3)

                       # Read pH level with retry
                       serial_port.write("RX205\n".encode())
                       ph_level = serial_port.readline().decode().strip()
                       if not ph_level:
                          time.sleep(0.3)
                          serial_port.write("RX205\n".encode())
                          ph_level = serial_port.readline().decode().strip()

                       # Check if still no data
                       if not ph_level:
                           raise IOError("Sensor D returned no pH data")

                       print(f"[READ] Sensor D - Temp: {temperature}, Level: None, pH: {ph_level}")

                       self.safe_gui_update(lambda: self.update_sensor_ui(
                           self.ph_level_frame, temperature, None, ph_level
                       ))
                       self.check_ph_alarm("D", ph_level)

                   except Exception as e:
                       print(f"[ERROR] Sensor D: {e}")
                       self.sensors[sensor_id]["is_running"] = False

                       print(f"[READ] Sensor D - Temp: {temperature}, Level: None, pH: {ph_level}")

                       self.safe_gui_update(lambda: self.update_sensor_ui(
                           self.ph_level_frame, temperature, None, ph_level
                    ))
                    
                       self.check_ph_alarm("D", ph_level)

            except Exception as e:
                print(f"[ERROR] Sensor {sensor_id}: {e}")
                self.sensors[sensor_id]["is_running"] = False
                break

            time.sleep(0.5)
            
    def sensor_watchdog(self):
        while True:
            for sensor_id, sensor in self.sensors.items():
                port = sensor.get("port")
                running = sensor.get("is_running", False)

                if not running:
                    print(f"[WATCHDOG] Sensor {sensor_id} not running. Attempting reconnect...")
                    self.safe_gui_update(lambda sid=sensor_id: self.set_sensor_disconnected(self.get_sensor_frame_by_id(sid)))
                    try:
                        self.reconnect_sensor(sensor_id)
                    except Exception as e:
                        print(f"[WATCHDOG ERROR] Failed to reconnect sensor {sensor_id}: {e}")
            time.sleep(5)  # Check every 5 seconds

    def get_sensor_frame_by_id(self, sensor_id):
        return {
            "A": self.aquarium_frame_1,
            "B": self.aquarium_frame_2,
            "C": self.ro_tank_frame,
            "D": self.ph_level_frame,
        }.get(sensor_id)
    
    def reconnect_sensor(self, sensor_id):
        ports = list(serial.tools.list_ports.comports())
        for port in ports:
            try:
                serial_port = serial.Serial(port.device, baudrate=9600, timeout=2)
                serial_port.write("RX800\n".encode())
                response = serial_port.readline().decode().strip()

                if response == sensor_id:
                    # Test read to confirm active connection
                    if sensor_id in ["A", "B", "C"]:
                        serial_port.write("RX203\n".encode())
                        test_read = serial_port.readline().decode().strip()
                    elif sensor_id == "D":
                        serial_port.write("RX205\n".encode())
                        test_read = serial_port.readline().decode().strip()
                
                    if not test_read or test_read.lower() == "none":
                        serial_port.close()
                        continue

                    self.sensors[sensor_id]["port"] = serial_port
                    self.sensors[sensor_id]["is_running"] = True
                    self.safe_gui_update(lambda: self.setup_sensor_ui(self.get_sensor_frame_by_id(sensor_id), serial_port))
                    threading.Thread(target=self.read_sensor_data, args=(sensor_id,), daemon=True).start()
                    print(f"[WATCHDOG] Sensor {sensor_id} successfully reconnected on {port.device}")
                    return
                serial_port.close()
            except Exception as e:
                print(f"[RECONNECT ERROR] {port.device}: {e}")
       
    def safe_gui_update(self, func):
        try:
            if self.root and self.root.winfo_exists():
                self.root.after(0, func)
        except Exception as e:
            print(f"[SAFE GUI ERROR] {e}")

    def update_sensor_ui(self, frame, temperature, water_level, ph_level):
        self.update_temperature_label(frame, temperature)
        self.update_water_level_label(frame, water_level)
        self.update_ph_label(frame, ph_level)
    def update_temperature_label(self, frame, temperature):
        try:
            label = frame.get("temperature_label")
            if label and temperature:
                label.config(text=f"Temperature: {temperature} °C")
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

    def toggle_fullscreen(self, event=None):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    def exit_fullscreen(self, event=None):
        self.fullscreen = False
        self.root.attributes("-fullscreen", False)

    def reset_sensor(self, serial_port):
        try:
            if serial_port and serial_port.is_open:
                print("Sending reset command (r)...")
                serial_port.write("r\n".encode())
            else:
                raise ValueError("No active serial connection.")
        except Exception as e:
            print(f"Exception: {e}")
            self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to reset sensor: {e}"))

    def check_ro_tank_alarm(self, sensor_id, wl_mmwg):
        settings = self.display_units[sensor_id]
        label = self.ro_tank_frame["connection_status"]

        # Default display
        status_text = "Connected"
        status_color = "green"

        try:
            if not settings.get("level_alarm"):
                self.safe_gui_update(lambda: label.config(text=status_text, fg=status_color))
                return

            use_liters = settings.get("use_liters", False)
            use_gallons = settings.get("use_gallons", False)
            width = float(settings.get("width", 0))
            depth = float(settings.get("depth", 0))
            min_val = float(settings.get("min_alarm", 0))
            max_val = float(settings.get("max_alarm", 100))

            if min_val >= max_val:
                raise ValueError("Min alarm must be less than max alarm.")

            # Convert water level into appropriate unit
            if use_liters and width > 0 and depth > 0:
                value = wl_mmwg * width * depth / 10000.0
            elif use_gallons and width > 0 and depth > 0:
                value = wl_mmwg * width * depth / 10000.0 * 0.264172
            else:
                value = wl_mmwg  # Use raw mmWG

            margin = 5  # soft warning buffer

            # Determine alarm status
            if value <= min_val or value >= max_val:
                status_text = "LEVEL CRITICAL"
                status_color = "red"
            elif (
                min_val < value <= min_val + margin
                or max_val - margin <= value < max_val
            ):
                status_text = "APPROACHING LIMIT"
                status_color = "orange"
            else:
                status_text = "Connected"
                status_color = "green"

            print(f"[ALARM CHECK] RO Tank level = {value:.2f}, unit = {'L' if use_liters else 'Gal' if use_gallons else 'mmWG'}")

        except Exception as e:
            print(f"[ALARM ERROR] {e}")
            status_text = "Alarm Error"
            status_color = "red"

        self.safe_gui_update(lambda: label.config(text=status_text, fg=status_color))
        
    def check_ph_alarm(self, sensor_id, ph_reading):
        settings = self.display_units[sensor_id]
        label = self.ph_level_frame["connection_status"]

        status_text = "Connected"
        status_color = "green"

        try:
            if not settings.get("ph_alarm_enabled", False):
                self.safe_gui_update(lambda: label.config(text=status_text, fg=status_color))
                return

            ph_value = float(ph_reading)
            min_val = float(settings.get("ph_min", 0))
            max_val = float(settings.get("ph_max", 0))

            if min_val >= max_val:
                raise ValueError("Invalid pH threshold configuration.")

            margin = 0.2  # buffer zone near thresholds

            if ph_value <= min_val or ph_value >= max_val:
                status_text = "LEVEL CRITICAL"
                status_color = "red"
            elif (
                min_val < ph_value <= min_val + margin
                or max_val - margin <= ph_value < max_val
            ):
                status_text = "APPROACHING LIMIT"
                status_color = "orange"

        except Exception as e:
            print(f"[PH ALARM ERROR] {e}")
            status_text = "Alarm Error"
            status_color = "red"

        self.safe_gui_update(lambda: label.config(text=status_text, fg=status_color))
    
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
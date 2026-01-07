import tkinter as tk
from tkinter import messagebox
import serial
import serial.tools.list_ports
import threading
import time
import RPi.GPIO as GPIO

__version__ = "1.2.6"

class SensorGUI:
    def __init__(self, root):
        self.root = root
        # ... setup GUI ...
        self.root.after(100, self.connect_to_sensors)
        self.root.title("Stork Aquatics Monitor Max V1.2.6")

        # Configure resizing for various screen types
        self.root.rowconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.root.rowconfigure(2, weight=1)
        self.root.columnconfigure(0, weight=1)
        self.root.columnconfigure(1, weight=1)

        # GPIO setup for pumps
        GPIO.setmode(GPIO.BCM)  
        self.pump_gpio = {
            "RO Pump A": 4, # GPIO Assignment R1=4,R2=27,R3=22,R4=17
            "RO Pump B": 27,
        }
        for pin in self.pump_gpio.values():
            GPIO.setup(pin, GPIO.OUT)
            GPIO.output(pin, GPIO.LOW)  
            
        # Water level thresholds (in mmWG)
        self.pump_on_threshold = 10
        self.pump_off_threshold = 100

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
        connection_status = tk.Label(frame, text="No Sensor Connected!", font=("Arial", 14, "bold"), fg="red")
        connection_status.pack(anchor="n")

        # Readings
        water_gauge_label = tk.Label(frame, text="Water Level: ", font=("Arial", 14, "bold"))
        water_gauge_label.pack(pady=10)
        temperature_label = tk.Label(frame, text="Temperature: --", font=("Arial", 14, "bold"))
        temperature_label.pack(pady=10)

        # Reset Button
        reset_button = tk.Button(frame, text="Reset Sensor", state=tk.DISABLED)
        reset_button.pack(pady=10)

        return {
            "frame": frame,
            "connection_status": connection_status,
            "temperature_label": temperature_label,
            "water_gauge_label": water_gauge_label,
            "reset_button": reset_button,
        }

    def create_ro_tank_frame(self, title, row, column, colspan=1):
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=10, pady=10)
        frame.grid(row=row, column=column, padx=10, pady=10, sticky="nsew", columnspan=colspan)

        # Connection Status
        connection_status_label = tk.Label(frame, text="Status:", font=("Arial", 14, "bold"), fg="black")
        connection_status_label.pack(anchor="n", pady=(5, 0))
        connection_status = tk.Label(frame, text="No Sensor Found!", font=("Arial", 14, "bold"), fg="red")
        connection_status.pack(anchor="n")

        # Readings (Water Level only for RO Tank)
        water_gauge_label = tk.Label(frame, text="Water Level:--", font=("Arial", 14, "bold"))
        water_gauge_label.pack(pady=10)

        # Reset Button
        reset_button = tk.Button(frame, text="Reset Sensor", state=tk.DISABLED)
        reset_button.pack(pady=10)

        return {
            "frame": frame,
            "connection_status": connection_status,
            "water_gauge_label": water_gauge_label,
            "reset_button": reset_button,
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

        # Reset Button
        reset_button = tk.Button(frame, text="Reset Sensor", state=tk.DISABLED)
        reset_button.pack(pady=10)

        return {
            "frame": frame,
            "connection_status": connection_status,
            "ph_level_label": ph_level_label,
            "temperature_label": temperature_label,
            "reset_button": reset_button,
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

    def control_pumps(self, sensor_id, water_level):
        pump_name = "RO Pump A" if sensor_id == "A" else "RO Pump B"
        pump_frame = self.pump_frame_a if sensor_id == "A" else self.pump_frame_b
        pump_status_label = pump_frame["pump_status"]
        auto_top_up_label = pump_frame["auto_top_up_label"]
        auto_mode = pump_frame["auto_mode_var"].get()
        toggle_button = pump_frame["toggle_button"]

        # 👇 Auto override logic
        if self.override_states[pump_name]:
            # Auto reset override only once water level drops below ON threshold
            if water_level < self.pump_on_threshold:
                print(f"[OVERRIDE RESET] Water level below threshold. Clearing manual override for {pump_name}.")
                self.override_states[pump_name] = False
            else:
                print(f"[OVERRIDE ACTIVE] Manual override blocking auto for {pump_name}.")
                return  # Don't auto-control if override still active

        if auto_mode:
            if water_level <= self.pump_on_threshold and not self.pump_states[pump_name]:
                self.toggle_pump(pump_name, pump_status_label, toggle_button, force_state=True)
                self.flash_auto_top_up(auto_top_up_label, pump_name)

            elif water_level >= self.pump_off_threshold and self.pump_states[pump_name]:
                self.toggle_pump(pump_name, pump_status_label, toggle_button, force_state=False)
                auto_top_up_label.config(text="", fg="black")

        else:
            # Safety mode: force off if manual mode hits max
            if water_level >= self.pump_off_threshold and self.pump_states[pump_name]:
                print(f"[SAFETY] Manual mode overfill shutdown.")
                # Only shut off pump without unchecking auto mode
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
                response = serial_port.readline().decode().strip()
                print(f"Sensor Response from {port.device}: {response}")

                if response in self.sensors:
                    self.sensors[response]["port"] = serial_port
                    self.sensors[response]["is_running"] = True

                    if response == "A":
                        self.setup_sensor_ui(self.aquarium_frame_1, serial_port)
                    elif response == "B":
                        self.setup_sensor_ui(self.aquarium_frame_2, serial_port)
                    elif response == "C":
                        self.setup_sensor_ui(self.ro_tank_frame, serial_port)
                    elif response == "D":
                        self.setup_sensor_ui(self.ph_level_frame, serial_port)

                    threading.Thread(target=self.read_sensor_data, args=(response,), daemon=True).start()
                else:
                    serial_port.close()
                    print(f"No valid sensor found on {port.device}.")

            except Exception as e:
                print(f"Exception: {e}")
                self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to connect to sensors: {e}"))

    def setup_sensor_ui(self, frame, serial_port):
        def update_ui():
            frame["connection_status"].config(text="Connected", fg="green")
            frame["reset_button"].config(state=tk.NORMAL, command=lambda: self.reset_sensor(serial_port))
        self.root.after(0, update_ui)

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
                    serial_port.write("RX203\n".encode())
                    water_level = serial_port.readline().decode().strip()

                    print(f"[READ] Sensor B - Temp: None, Level: {water_level}, pH: None")

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.ro_tank_frame, None, water_level, None
                    ))

                elif sensor_id == "D":
                    serial_port.write("RX201\n".encode())
                    temperature = serial_port.readline().decode().strip()
                    serial_port.write("RX205\n".encode())
                    ph_level = serial_port.readline().decode().strip()

                    print(f"[READ] Sensor D - Temp: {temperature}, Level: None, pH: {ph_level}")

                    self.safe_gui_update(lambda: self.update_sensor_ui(
                        self.ph_level_frame, temperature, None, ph_level
                    ))

            except Exception as e:
                print(f"[ERROR] Sensor {sensor_id}: {e}")
                self.sensors[sensor_id]["is_running"] = False
                break

            time.sleep(1)
            
    def safe_gui_update(self, func):
        try:
            if self.root and self.root.winfo_exists():
                self.root.after(0, func)
        except Exception as e:
            print(f"[SAFE GUI ERROR] {e}")

    def update_sensor_ui(self, frame, temperature, water_level, ph_level):
        try:
            label = frame.get("temperature_label")
            if label and temperature:
                label.config(text=f"Temperature: {temperature} °C")
        except Exception as e:
            print(f"[ERROR] Updating temperature_label: {e}")

        try:
            label = frame.get("water_gauge_label")
            if label and water_level:
                label.config(text=f"Level: {water_level}")
        except Exception as e:
            print(f"[ERROR] Updating water_gauge_label: {e}")

        try:
            label = frame.get("ph_level_label")
            if label and ph_level:
                label.config(text=f"pH Level: {ph_level}")
        except Exception as e:
            print(f"[ERROR] Updating ph_level_label: {e}")

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

if __name__ == "__main__":
    root = tk.Tk()
    gui = SensorGUI(root)
    try:
        root.mainloop()
    finally:
        GPIO.cleanup()
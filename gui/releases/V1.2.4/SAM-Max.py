import tkinter as tk
from tkinter import messagebox
import serial
import serial.tools.list_ports
import threading
import time
import RPi.GPIO as GPIO

__version__ = "1.2.4"

class SensorGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Stork Aquatics Monitor Max V1.2.4")

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
            GPIO.output(pin, GPIO.LOW)  # Default OFF

        # Serial port connections
        self.sensors = {
            "A": {"port": None, "is_running": False},
            "B": {"port": None, "is_running": False},
            "C": {"port": None, "is_running": False},
            "D": {"port": None, "is_rinning": False},
        }

        # Pump states
        self.pump_states = {
            "RO Pump A": False,
            "RO Pump B": False,
        }

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
            "reset_button": reset_button,
        }

    def create_pump_frame(self, title, row, column):
        frame = tk.LabelFrame(self.root, text=title, font=("Arial", 16, "bold"), padx=10, pady=10)
        frame.grid(row=row, column=column, padx=10, pady=10, sticky="nsew")

        # Pump Status
        pump_status = tk.Label(frame, text="OFF", font=("Arial", 14, "bold"), fg="red")
        pump_status.pack(pady=10)

        # Toggle Button
        toggle_button = tk.Button(frame, text="Turn On", command=lambda: self.toggle_pump(title, pump_status, toggle_button))
        toggle_button.pack(pady=10)

        return {
            "frame": frame,
            "pump_status": pump_status,
            "toggle_button": toggle_button,
        }

    def toggle_pump(self, pump_name, status_label, toggle_button):
        self.pump_states[pump_name] = not self.pump_states[pump_name]
        pin = self.pump_gpio[pump_name]
        GPIO.output(pin, GPIO.HIGH if self.pump_states[pump_name] else GPIO.LOW)
       
        if self.pump_states[pump_name]:
            status_label.config(text="ON", fg="green")
            toggle_button.config(text="Turn Off")
        else:
            status_label.config(text="OFF", fg="red")
            toggle_button.config(text="Turn On")

    def connect_to_sensors(self):
        try:
            print("Searching for available COM ports...")  # Debug log
            self.aquarium_frame_1["connection_status"].config(text="Disconnected", fg="red")
            ports = list(serial.tools.list_ports.comports())
            if not ports:
                raise Exception("No COM ports available.")

            for port in ports:
                print(f"Checking port: {port.device}")  # Debug log
                try:
                    # Attempt to open the serial port
                    serial_port = serial.Serial(port.device, baudrate=9600, timeout=2)
                    print(f"Opened port: {port.device}")  # Debug log

                    # Send RX800 to identify the sensor
                    serial_port.write("RX800\n".encode())
                    response = serial_port.readline().decode().strip()
                    print(f"Sensor Response from {port.device}: {response}")  # Debug log

                    # Assign the sensor to its respective frame
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

                        # Start reading data
                        threading.Thread(target=self.read_sensor_data, args=(response,), daemon=True).start()
                    else:
                        serial_port.close()
                        print(f"No valid sensor found on {port.device}.")  # Debug log

                except Exception as e:
                    print(f"Error on port {port.device}: {e}")  # Debug log
                   
        except Exception as e:
            print(f"Exception: {e}")  # Debug log
            messagebox.showerror("Error", f"Failed to connect to sensors: {e}")
            setup_sensor_ui_fail

    def setup_sensor_ui(self, frame, serial_port):
        frame["connection_status"].config(text="Connected", fg="green")
        frame["reset_button"].config(state=tk.NORMAL, command=lambda: self.reset_sensor(serial_port))
       
    def setup_sensor_ui_fail(self, frame, serial_port):
        frame["connection_status"].config(text="Disconnected", fg="red")
        frame["reset_button"].config(state=tk.NORMAL, command=lambda: self.reset_sensor(serial_port))

    def read_sensor_data(self, sensor_id):
        while self.sensors[sensor_id]["is_running"]:
            try:
                serial_port = self.sensors[sensor_id]["port"]

                # Read temperature if applicable
                temperature = None
                if sensor_id != "C":  # Sensor C (RO Tank) does not have temperature or pH
                    serial_port.write("RX201\n".encode())
                    temperature = serial_port.readline().decode().strip()
                   
                # Read ph if applicable
                ph_level = None
                if sensor_id != "A, B, C":  # Sensor A,B&C does not have ph
                    serial_port.write("RX206\n".encode())
                    ph_level = serial_port.readline().decode().strip()    

                # Read water level
                serial_port.write("RX203\n".encode())
                water_level = serial_port.readline().decode().strip()

                print(f"Sensor {sensor_id} - Temperature: {temperature}, Water Level: {water_level}, pH Level: {ph_level}")  # Debug log

                # Update the respective GUI
                if sensor_id == "A":
                    self.update_sensor_ui(self.aquarium_frame_1, temperature, water_level, None)
                elif sensor_id == "B":
                    self.update_sensor_ui(self.aquarium_frame_2, temperature, water_level, None)
                elif sensor_id == "C":
                    self.update_sensor_ui(self.ro_tank_frame, None, water_level, None)
                elif sensor_id == "D":
                    self.update_sensor_ui(self.ph_level_frame, None, None, ph_level)

            except Exception as e:
                print(f"Error reading from Sensor {sensor_id}: {e}")  # Debug log
                self.sensors[sensor_id]["is_running"] = False
                break

            time.sleep(1)  # Delay between readings

    def update_sensor_ui(self, frame, temperature, water_level, ph_level):
        if temperature is not None:
            frame["temperature_label"].config(text=f"Temperature: {temperature} °C")
        if water_level is not None:
            frame["water_gauge_label"].config(text=f"Water Level: {water_level} ")
        if ph_level is not None: 
            frame["ph_level_label"].config(text=f"pH Level: {ph_level} ")           

    def reset_sensor(self, serial_port):
        try:
            if serial_port and serial_port.is_open:
                print("Sending reset command (r)...")  # Debug log
                serial_port.write("r\n".encode())
            else:
                raise ValueError("No active serial connection.")
        except Exception as e:
            print(f"Exception: {e}")  # Debug log
            messagebox.showerror("Error", f"Failed to reset sensor: {e}")

if __name__ == "__main__":
    root = tk.Tk()
    gui = SensorGUI(root)
    try:
        root.mainloop()
    finally:
        GPIO.cleanup()
# Sensor ID "A"
# MPM288DI-0A-G-F-5-0-13 0-350mB Gauge / 0-+70°C 
# Stork Aquatics Level & Temperature Sensor MK2 V1.0.5
import os
import machine
import network
import socket
import time
import ujson
import _thread
from machine import Pin, I2C

# I2C Connection
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=100000)

# GPIO LED Assignment
blue_led = Pin(14, Pin.OUT)  
white_led = Pin(15, Pin.OUT)  
blink_started = False

# LED Handler
def flash_led(led, count=2, duration=0.2):
    for _ in range(count):
        led.on()
        time.sleep(duration)
        led.off()
        time.sleep(duration)

def blink_led(led, interval):
    while True:
        led.toggle()
        time.sleep(interval)       

# Wi-Fi Config file
WIFI_CONFIG_FILE = 'wifi.json'

# Sensor i2c address
MPM288DI_ADDR = 0x6D
#Key Pin Assignment
KEY_PIN = Pin(2, Pin.OUT)
KEY_PIN.value(0)

# Sensor Reads
def read_adc24(register_base):
    try:
        i2c.writeto(MPM288DI_ADDR, bytes([register_base]))
        time.sleep(0.01)
        raw = i2c.readfrom(MPM288DI_ADDR, 3)
        value = int.from_bytes(raw, 'big')
        if value & 0x800000:
            value -= 1 << 24
        return value
    except Exception as e:
        print("I2C error:", e)
        return None

def read_pressure():
    raw = read_adc24(0x06)
    if raw is None:
        return "ERR"
    percent = (raw - (8388608 * 0.1)) / (8388608 * 0.8)
    mbar = percent * 350
    mmwg = mbar * 10.19716
    return str(round(mmwg, 1))

def read_temperature():
    raw = read_adc24(0x09)
    if raw is None:
        return "ERR"
    temp = 25 + (raw / 65536)
    return str(round(temp, 1))

def identify_sensor():
    return "A"

def reset_sensor():
    machine.reset()
# Load Wi-Fi Config
def has_wifi_config():
    try:
        with open("wifi.json", "r") as _:
            return True
    except:
        return False
# Full Reset By Button Press On AP Status Page
def factory_reset():
    try:
        os.remove('wifi.json') 
    except:
        pass
    print("Factory reset: wifi.json removed; rebooting to setup mode…")
    machine.reset()

def ensure_ap_up():
    import network
    ap = network.WLAN(network.AP_IF)
    if not ap.active():
        ap.active(True)
    ap.config(essid='Sensor-A', password='sensor1234')
    print("System Ok")
    print("Looking for config files...")
    print("AP Active?", ap.active())

def save_wifi_config(ssid, password):
    with open(WIFI_CONFIG_FILE, 'w') as f:
        ujson.dump({'ssid': ssid, 'password': password}, f)

def load_wifi_config():
    try:
        with open(WIFI_CONFIG_FILE, 'r') as f:
            return ujson.load(f)
    except:
        return None
    
def update_assigned_ip(ip):
    try:
        cfg = load_wifi_config() or {}
        cfg['assigned_ip'] = ip
        with open(WIFI_CONFIG_FILE, 'w') as f:
            ujson.dump(cfg, f)
        print("Assigned IP saved to wifi.json:", ip)
    except Exception as e:
        print("Failed to update assigned_ip in wifi.json:", e)

def connect_wifi():
    import network
    import time
    import json

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    try:
        with open("wifi.json", "r") as f:
            creds = json.load(f)
            ssid = creds.get("ssid", "")
            password = creds.get("password", "")
            print("Config files found")
            print("Loaded SSID & Password from file for:", ssid)
    except Exception as e:
        print("Error loading wifi config:", e)
        start_captive_portal()
        return

    if not ssid:
        print("No SSID found in config. Starting captive portal...")
        start_captive_portal()
        return

    print("Connecting to:", ssid)
    wlan.connect(ssid, password)

    max_wait = 10
    while max_wait > 0:
        if wlan.isconnected():
            break
        print("Waiting for connection... status =", wlan.status())
        time.sleep(1)
        max_wait -= 2

    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print("Connected to:", ssid)
        print("IP Assigned:", ip)
        update_assigned_ip(ip)
        blue_led.on()
    else:
        print("Failed to connect to Wi-Fi! Starting Captive Portal.")
        start_captive_portal()
        
    if wlan.isconnected():
        return True
    else:
        return False

def start_captive_portal():
    import network, socket, time, sys, _thread

    # Activate AP
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid='Sensor-A', password='sensor1234')
    try:
        ap.ifconfig(('192.168.4.1','255.255.255.0','192.168.4.1','8.8.8.8'))
    except Exception:
        pass
    print("AP Wi-Fi setup page on http://192.168.4.1")

    try:
        _thread.start_new_thread(blink_led, (blue_led, 0.5))
    except Exception:
        pass

    def _urldecode(s):
        try:
            s = s.replace('+', ' ')
            out = b""; i = 0
            bs = s.encode() if isinstance(s, str) else s
            ln = len(bs)
            while i < ln:
                c = bs[i:i+1]
                if c == b'%' and i+2 < ln:
                    try:
                        out += bytes([int(bs[i+1:i+3], 16)])
                        i += 3; continue
                    except Exception:
                        pass
                out += c; i += 1
            return out.decode('utf-8', 'ignore')
        except Exception:
            return s

    def _read_http_request(cl, limit=4096):
        req = b""
        cl.settimeout(5)
        while b"\r\n\r\n" not in req and len(req) < limit:
            chunk = cl.recv(256)
            if not chunk: break
            req += chunk
        headers_part, _, rest = req.partition(b"\r\n\r\n")
        first = headers_part.split(b"\r\n", 1)[0] if headers_part else b""
        # content-length?
        clen = 0
        lower = headers_part.lower()
        idx = lower.find(b"content-length:")
        if idx != -1:
            try:
                line = lower[idx:].split(b"\r\n",1)[0]
                clen = int(line.split(b":",1)[1].strip())
            except Exception:
                clen = 0
        body = rest
        while len(body) < clen and len(body) < limit:
            chunk = cl.recv(256)
            if not chunk: break
            body += chunk
        return first.decode('utf-8','ignore'), body

    # WiFi Setup HTML
    FORM_HTML = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sensor A — Wi-Fi Setup</title>
<style>body{background:#f8f8f8;font-family:sans-serif;margin:0}
.card{max-width:420px;margin:60px auto;padding:24px;background:#fff;border-radius:10px;box-shadow:0 0 10px rgba(0,0,0,.08)}
h2{margin:0 0 12px;color:#2c3e50}input{width:100%;padding:10px;margin:8px 0}
button{padding:10px 16px;background:#3498db;border:0;color:#fff;border-radius:6px;cursor:pointer}</style>
</head><body><div class="card">
<h2>Sensor A — Wi-Fi Setup</h2>
<form method="POST" action="/">
<input name="ssid" placeholder="SSID">
<input name="password" type="password" placeholder="Password">
<button>Connect</button>
</form>
<p style="color:#777;font-size:13px;margin-top:12px">
After connecting, the device will reboot. Reconnect to the sensors Wi-Fi and open <b>http://192.168.4.1</b> to see status & assigned IP.
</p>
</div></body></html>"""

    # HTTP server port :80
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('0.0.0.0', 80))
        s.listen(4)
    except Exception as e:
        print("Captive portal bind FAILED:")
        try: sys.print_exception(e)
        except: print(e)
        return

    while True:
        cl = None
        try:
            cl, raddr = s.accept()
            first, body = _read_http_request(cl)
            parts = first.split()
            method = parts[0] if len(parts) > 0 else ""
            path   = parts[1] if len(parts) > 1 else "/"

            if method == "POST" and path == "/":
                params = {}
                try:
                    data = body.decode('utf-8','ignore')
                except:
                    data = ""
                for pair in data.split("&"):
                    if "=" in pair:
                        k, v = pair.split("=",1)
                        params[k.strip()] = _urldecode(v.strip())
                ssid = params.get("ssid","").strip()
                password = params.get("password","").strip()
                print("Parsed SSID:", ssid)

                if ssid:
                    save_wifi_config(ssid, password)
                    sta = network.WLAN(network.STA_IF)
                    sta.active(True)
                    sta.connect(ssid, password)
                    tmo = 8
                    while tmo > 0 and not sta.isconnected():
                        time.sleep(1); tmo -= 1

                    # Redirect to AP root after reboot is wifi fails to connect.
                    try:
                        cl.send(b"HTTP/1.1 302 Found\r\n"
                                b"Location: http://192.168.4.1/\r\n"
                                b"Content-Length: 0\r\n"
                                b"Connection: close\r\n\r\n")
                    except: pass
                    try: cl.close()
                    except: pass
                    time.sleep(2)
                    machine.reset()
                    return
                else:
                    try:
                        cl.send(b"HTTP/1.1 200 OK\r\n"
                                b"Content-Type: text/html; charset=utf-8\r\n"
                                b"Connection: close\r\n\r\n")
                        cl.send(FORM_HTML)
                    except: pass

            else:
                # Show Wi-Fi Setup HTML
                try:
                    cl.send(b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: text/html; charset=utf-8\r\n"
                            b"Connection: close\r\n\r\n")
                    cl.send(FORM_HTML)
                except: pass

        except Exception as e:
            print("Captive portal error:")
            try: sys.print_exception(e)
            except: print(e)
        finally:
            try:
                if cl: cl.close()
            except: pass
            time.sleep_ms(50)

# AP Status Page            
def run_ap_status_server():
    import socket, network, time, sys

    ap = network.WLAN(network.AP_IF)
    if not ap.active():
        ap.active(True)
    try:
        ap.ifconfig(('192.168.4.1','255.255.255.0','192.168.4.1','8.8.8.8'))
    except Exception:
        pass
    ap_ip = ap.ifconfig()[0]

    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('0.0.0.0', 80)) 
        s.listen(4)
        print("AP status page on http://%s:80 (connect to Sensor-A Wi-Fi)" % ap_ip)
    except Exception as e:
        print("AP status server bind FAILED:")
        try: sys.print_exception(e)
        except: print(e)
        return

    while True:
        cl = None
        try:
            cl, raddr = s.accept()

            # Read request (headers only)
            req = b""
            cl.settimeout(3)
            while b"\r\n\r\n" not in req and len(req) < 4096:
                chunk = cl.recv(512)
                if not chunk: break
                req += chunk

            first = (req.split(b"\r\n", 1)[0] or b"").decode("utf-8","ignore")
            parts = first.split()
            method = parts[0] if len(parts)>0 else ""
            path   = parts[1] if len(parts)>1 else "/"

            # Reset handler
            if method == "POST" and path == "/factory_reset":
                try:
                    cl.send(b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: text/plain; charset=utf-8\r\n"
                            b"Connection: close\r\n\r\n"
                            b"Resetting to setup mode...\n")
                except: pass
                try: cl.close()
                except: pass
                factory_reset()
                return

            # Read current values
            sta = network.WLAN(network.STA_IF)
            sta_ip = sta.ifconfig()[0] if sta.isconnected() else "0.0.0.0"
            try: t = read_temperature()
            except: t = "ERR"
            try: lvl = read_pressure()
            except: lvl = "ERR"

            page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sensor A — Connected</title>
<style>
  body {{ background:#f8f8f8; font-family:sans-serif; margin:0; padding:0; }}
  .container {{ max-width:420px; margin:60px auto; padding:24px; background:#fff;
               border-radius:10px; box-shadow:0 0 10px rgba(0,0,0,.08); }}
  h2 {{ margin-top:0; color:#2c3e50; }}
  p {{ margin:10px 0; }}
  hr {{ border:none; border-top:1px solid #ddd; margin:20px 0; }}
  button {{ padding:10px 16px; background:#e74c3c; border:0; color:#fff;
           border-radius:6px; cursor:pointer; font-size:14px; }}
</style>
</head>
<body>
  <div class="container">
    <h2>Sensor A — Connected</h2>
    <p><b>Assigned IP:</b> {sta_ip}</p>
    <hr>
    <p><b>Temperature (°C):</b> {t}</p>
    <p><b>Level (mmWG):</b> {lvl}</p>
    <form method="POST" action="/factory_reset"
          onsubmit="return confirm('Reset Wi-Fi and reboot into setup mode?');">
      <button>Reset to Captive Mode</button>
    </form>
    <p style="color:#777; font-size:13px; margin-top:14px;">
      This page is always available at <b>http://192.168.4.1</b> when connected to the sensor's Wi-Fi.
    </p>
  </div>
</body>
</html>"""

            body = page.encode()
            hdr = (b"HTTP/1.1 200 OK\r\n"
                   b"Content-Type: text/html; charset=utf-8\r\n"
                   b"Connection: close\r\n"
                   b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n")
            try:
                cl.sendall(hdr); cl.sendall(body)
            except: pass

        except Exception as e:
            print("AP status error:")
            try: sys.print_exception(e)
            except: print(e)
        finally:
            try:
                if cl: cl.close()
            except: pass
            time.sleep_ms(60)

# TCP Server Config
import sys

def tcp_server_thread():
    try:
        tcp_server()
    except Exception as e:
        try:
            import sys
            print("FATAL in tcp_server thread:")
            sys.print_exception(e)
        except:
            print(e)

def tcp_server():
    import sys
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('0.0.0.0', 8888))
    s.listen(1)
    white_led.on()
    print("TCP Server Active")

    while True:
        conn = None
        try:
            conn, addr = s.accept()
            print("Connection from:", addr)
            conn.settimeout(5)
            buf = b""

            while True:
                chunk = conn.recv(256)
                if not chunk:
                    break
                buf += chunk

                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    cmd = line.decode().strip()
                    if not cmd:
                        continue
                    print("RX-CMD:", cmd)

                    try:
                        if cmd == "RX201":
                            val = read_temperature()
                            conn.send((val + "\n").encode())
                            print("TX-Temperature=", val)

                        elif cmd == "RX203":
                            val = read_pressure()
                            conn.send((val + "\n").encode())
                            print("TX-Level=", val)

                        elif cmd == "RX800":
                            val = identify_sensor()
                            conn.send((val + "\n").encode())
                            print("TX-ID=", val)

                        elif cmd.lower() == "r":
                            conn.send(b"Rebooting\n")
                            try: conn.close()
                            except: pass
                            time.sleep(1)
                            reset_sensor()
                            return

                        else:
                            conn.send(b"?\n")

                    except Exception as e:
                        print("Command Error:")
                        try: sys.print_exception(e)
                        except: print(e)

        except Exception as e:
            print("TCP Error:")
            try:
                import sys
                sys.print_exception(e)
            except:
                print(e)
        finally:
            try:
                if conn:
                    conn.close()
            except:
                pass

# BOOT & RUN
import gc
white_led.off()
blue_led.off()
flash_led(white_led)

def has_wifi_config():
    try:
        with open("wifi.json","r") as _:
            return True
    except:
        return False

if not has_wifi_config():
    start_captive_portal()

ensure_ap_up()
time.sleep(0.2)

if connect_wifi():
    try:
        _thread.start_new_thread(tcp_server_thread, ())
    except Exception as e:
        print("Failed to start tcp_server_thread:", e)

try:
    run_ap_status_server()
except Exception as e:
    try:
        import sys
        sys.print_exception(e)
    except:
        print("AP status server crashed:", e)
    time.sleep(1)
    machine.reset()
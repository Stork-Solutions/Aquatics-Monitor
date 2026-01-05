# Sensor ID "A" — UNIVERSAL LEVEL/TEMP (MPM288DI + ME782)
# Stork Aquatics Level & Temperature Sensor MK2 — V2.0.0
# MicroPython (Pico W) — TCP :8888
import os
import machine
import network
import socket
import time
import ujson
import _thread
from machine import Pin, I2C

# -----------------------------
# Identity / Versioning
# -----------------------------
SENSOR_ID = "A"
MODEL = "LEVELTEMP"
FW_VERSION = "2.0.0"

# Commands added (for GUI / support)
# RX245 -> firmware version string (e.g. A2.0.0)
# RX246 -> model string (LEVELTEMP)
# RX247 -> head type (MPM288DI / ME782)
# HEAD? -> returns active head
# HEAD:MPM288DI or HEAD:ME782 -> set head (persist), reboot recommended
# UPDATE? -> NONE or AVAILABLE <ver>
# UPDATE  -> perform OTA update + reboot

# -----------------------------
# GitHub OTA (sensor-managed)
# -----------------------------
OTA_ENABLED = True

# Put your GitHub raw URLs here (leave blank to disable OTA checks safely)
OTA_MANIFEST_URL = ""   # e.g. https://raw.githubusercontent.com/<user>/<repo>/main/sensor_update.json
# Manifest should include entry for "LEVELTEMP-A" (see example in notes)

# -----------------------------
# Files
# -----------------------------
WIFI_CONFIG_FILE = "wifi.json"
SENSOR_CONFIG_FILE = "config.json"   # stores head_type + optional OTA state

# -----------------------------
# Hardware
# -----------------------------
i2c = I2C(0, scl=Pin(1), sda=Pin(0), freq=100000)

blue_led = Pin(14, Pin.OUT)
white_led = Pin(15, Pin.OUT)

def flash_led(led, count=2, duration=0.2):
    for _ in range(count):
        led.on(); time.sleep(duration)
        led.off(); time.sleep(duration)

def blink_led(led, interval):
    while True:
        led.toggle()
        time.sleep(interval)

# -----------------------------
# Config helpers
# -----------------------------
def load_json(path, default=None):
    try:
        with open(path, "r") as f:
            return ujson.load(f)
    except:
        return default

def save_json(path, data):
    try:
        with open(path, "w") as f:
            ujson.dump(data, f)
        return True
    except:
        return False

def load_sensor_config():
    cfg = load_json(SENSOR_CONFIG_FILE, default={}) or {}
    # default head type (freshwater) unless set
    cfg.setdefault("head_type", "MPM288DI")
    cfg.setdefault("ota_last_check", 0)
    return cfg

def save_sensor_config(cfg):
    return save_json(SENSOR_CONFIG_FILE, cfg)

# Active head selection (runtime)
sensor_cfg = load_sensor_config()
HEAD_TYPE = sensor_cfg.get("head_type", "MPM288DI")

# -----------------------------
# Sensor drivers
# -----------------------------

# --- MPM288DI (from your V1.0.5 pack) ---
MPM288DI_ADDR = 0x6D
KEY_PIN = Pin(2, Pin.OUT)
KEY_PIN.value(0)

def _mpm_read_adc24(register_base):
    try:
        i2c.writeto(MPM288DI_ADDR, bytes([register_base]))
        time.sleep(0.01)
        raw = i2c.readfrom(MPM288DI_ADDR, 3)
        value = int.from_bytes(raw, "big")
        if value & 0x800000:
            value -= 1 << 24
        return value
    except Exception as e:
        try: print("MPM I2C error:", e)
        except: pass
        return None

def mpm_read_pressure_mmwg():
    raw = _mpm_read_adc24(0x06)
    if raw is None:
        return "ERR"
    # Your existing transfer mapping (10%..90% of ADC span)
    percent = (raw - (8388608 * 0.1)) / (8388608 * 0.8)
    mbar = percent * 350
    mmwg = mbar * 10.19716
    return str(round(mmwg, 1))

def mpm_read_temp_c():
    raw = _mpm_read_adc24(0x09)
    if raw is None:
        return "ERR"
    temp = 25 + (raw / 65536)
    return str(round(temp, 1))

# --- ME782 (from your V1.0.7 pack) ---
ME782_OUT_REG = 0x78
ME782_ADDR = 0x78  # may be discovered; keeping your pattern

# Head range (your ME782 is 0–0.5 bar gauge, -40..125°C)
P_MIN_BAR = 0.0
P_MAX_BAR = 0.5
T_MIN_C = -40.0
T_MAX_C = 125.0
AP, BP = 0.05, 0.95
AT, BT = 0.10, 0.90

def _me782_discover():
    global ME782_ADDR
    try:
        for addr in i2c.scan():
            try:
                raw = i2c.readfrom(addr, 4)
                if len(raw) == 4:
                    p_raw = ((raw[0] << 8) | raw[1]) & 0x7FFF
                    t_raw = ((raw[2] << 8) | raw[3]) & 0x7FFF
                    if (p_raw | t_raw) != 0 and 0 <= p_raw <= 32767 and 0 <= t_raw <= 32767:
                        ME782_ADDR = addr
                        try: print("ME782 detected at I2C 0x%02X" % addr)
                        except: pass
                        return True
            except:
                pass
    except Exception as e:
        try: print("ME782 scan failed:", e)
        except: pass
    return False

def _me782_read_raw():
    raw = i2c.readfrom(ME782_ADDR, 4)
    p_raw = ((raw[0] << 8) | raw[1]) & 0x7FFF
    t_raw = ((raw[2] << 8) | raw[3]) & 0x7FFF
    return p_raw, t_raw

def _me782_convert_pressure_bar(pdec):
    span = (P_MAX_BAR - P_MIN_BAR)
    scale = span / (BP - AP)
    return (pdec / 32767.0) * scale + (P_MIN_BAR - AP * scale)

def _me782_convert_temp_c(tdec):
    span = (T_MAX_C - T_MIN_C)
    scale = span / (BT - AT)
    return (tdec / 32767.0) * scale + (T_MIN_C - AT * scale)

def me782_read_pressure_mmwg():
    try:
        if not _me782_discover():
            return "ERR"
        pdec, _ = _me782_read_raw()
        p_bar = _me782_convert_pressure_bar(pdec)
        mmwg = p_bar * 10197.16
        return str(round(mmwg, 1))
    except Exception as e:
        try: print("ME782 pressure read error:", e)
        except: pass
        return "ERR"

def me782_read_temp_c():
    try:
        if not _me782_discover():
            return "ERR"
        _, tdec = _me782_read_raw()
        temp_c = _me782_convert_temp_c(tdec)
        return str(round(temp_c, 1))
    except Exception as e:
        try: print("ME782 temp read error:", e)
        except: pass
        return "ERR"

# Unified reads
def read_pressure():
    if HEAD_TYPE == "ME782":
        return me782_read_pressure_mmwg()
    return mpm_read_pressure_mmwg()

def read_temperature():
    if HEAD_TYPE == "ME782":
        return me782_read_temp_c()
    return mpm_read_temp_c()

def identify_sensor():
    return SENSOR_ID

def reset_sensor():
    machine.reset()

# -----------------------------
# Wi-Fi setup (keep your existing behaviour)
# -----------------------------
def has_wifi_config():
    try:
        with open(WIFI_CONFIG_FILE, "r") as _:
            return True
    except:
        return False

def save_wifi_config(ssid, password):
    with open(WIFI_CONFIG_FILE, "w") as f:
        ujson.dump({"ssid": ssid, "password": password}, f)

def load_wifi_config():
    return load_json(WIFI_CONFIG_FILE, default=None)

def factory_reset():
    try:
        os.remove(WIFI_CONFIG_FILE)
    except:
        pass
    print("Factory reset: wifi.json removed; rebooting to setup mode…")
    machine.reset()

def ensure_ap_up():
    ap = network.WLAN(network.AP_IF)
    if not ap.active():
        ap.active(True)
    ap.config(essid="Sensor-A", password="sensor1234")
    print("System Ok")
    print("AP Active?", ap.active())

def start_captive_portal():
    import sys
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid="Sensor-A", password="sensor1234")
    try:
        ap.ifconfig(("192.168.4.1","255.255.255.0","192.168.4.1","8.8.8.8"))
    except:
        pass

    try:
        _thread.start_new_thread(blink_led, (blue_led, 0.5))
    except:
        pass

    def _urldecode(s):
        try:
            s = s.replace("+", " ")
            out = b""; i = 0
            bs = s.encode() if isinstance(s, str) else s
            ln = len(bs)
            while i < ln:
                c = bs[i:i+1]
                if c == b"%" and i+2 < ln:
                    try:
                        out += bytes([int(bs[i+1:i+3], 16)])
                        i += 3; continue
                    except:
                        pass
                out += c; i += 1
            return out.decode("utf-8","ignore")
        except:
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
        clen = 0
        lower = headers_part.lower()
        idx = lower.find(b"content-length:")
        if idx != -1:
            try:
                line = lower[idx:].split(b"\r\n",1)[0]
                clen = int(line.split(b":",1)[1].strip())
            except:
                clen = 0
        body = rest
        while len(body) < clen and len(body) < limit:
            chunk = cl.recv(256)
            if not chunk: break
            body += chunk
        return first.decode("utf-8","ignore"), body

    FORM_HTML = b"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sensor A — Wi-Fi Setup</title></head>
<body style="font-family:sans-serif;background:#f8f8f8;margin:0">
<div style="max-width:420px;margin:60px auto;padding:24px;background:#fff;border-radius:10px">
<h2>Sensor A — Wi-Fi Setup</h2>
<form method="POST" action="/">
<input name="ssid" placeholder="SSID" style="width:100%;padding:10px;margin:8px 0">
<input name="password" type="password" placeholder="Password" style="width:100%;padding:10px;margin:8px 0">
<button style="padding:10px 16px">Connect</button>
</form></div></body></html>"""

    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 80))
    s.listen(4)
    print("AP Wi-Fi setup page on http://192.168.4.1")

    while True:
        cl = None
        try:
            cl, _ = s.accept()
            first, body = _read_http_request(cl)
            parts = first.split()
            method = parts[0] if len(parts)>0 else ""
            path   = parts[1] if len(parts)>1 else "/"

            if method == "POST" and path == "/":
                params = {}
                try:
                    data = body.decode("utf-8","ignore")
                except:
                    data = ""
                for pair in data.split("&"):
                    if "=" in pair:
                        k, v = pair.split("=",1)
                        params[k.strip()] = _urldecode(v.strip())
                ssid = (params.get("ssid","") or "").strip()
                password = (params.get("password","") or "").strip()

                if ssid:
                    save_wifi_config(ssid, password)
                    try:
                        cl.send(b"HTTP/1.1 302 Found\r\nLocation: http://192.168.4.1/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    except:
                        pass
                    try: cl.close()
                    except: pass
                    time.sleep(2)
                    machine.reset()
                    return

            try:
                cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n")
                cl.send(FORM_HTML)
            except:
                pass

        finally:
            try:
                if cl: cl.close()
            except:
                pass

def connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    creds = load_wifi_config()
    if not creds or not creds.get("ssid"):
        start_captive_portal()
        return False

    ssid = creds.get("ssid","")
    password = creds.get("password","")
    print("Connecting to:", ssid)
    wlan.connect(ssid, password)

    max_wait = 10
    while max_wait > 0:
        if wlan.isconnected():
            break
        time.sleep(1)
        max_wait -= 1

    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print("Connected. IP:", ip)
        blue_led.on()
        return True

    print("Failed to connect. Starting captive portal.")
    start_captive_portal()
    return False

def run_ap_status_server():
    import sys
    ap = network.WLAN(network.AP_IF)
    if not ap.active():
        ap.active(True)
    try:
        ap.ifconfig(("192.168.4.1","255.255.255.0","192.168.4.1","8.8.8.8"))
    except:
        pass

    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 80))
    s.listen(4)

    while True:
        cl = None
        try:
            cl, _ = s.accept()
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

            if method == "POST" and path == "/factory_reset":
                try:
                    cl.send(b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nResetting...\n")
                except:
                    pass
                try: cl.close()
                except: pass
                factory_reset()
                return

            sta = network.WLAN(network.STA_IF)
            sta_ip = sta.ifconfig()[0] if sta.isconnected() else "0.0.0.0"
            t = read_temperature()
            lvl = read_pressure()

            page = f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sensor A — Connected</title></head>
<body style="font-family:sans-serif;background:#f8f8f8;margin:0">
<div style="max-width:420px;margin:60px auto;padding:24px;background:#fff;border-radius:10px">
<h2>Sensor A — Connected</h2>
<p><b>Firmware:</b> {SENSOR_ID}{FW_VERSION}</p>
<p><b>Model:</b> {MODEL}</p>
<p><b>Head:</b> {HEAD_TYPE}</p>
<p><b>Assigned IP:</b> {sta_ip}</p><hr>
<p><b>Temperature (°C):</b> {t}</p>
<p><b>Level (mmWG):</b> {lvl}</p>
<form method="POST" action="/factory_reset" onsubmit="return confirm('Reset Wi-Fi and reboot?');">
<button style="padding:10px 16px;background:#e74c3c;color:#fff;border:0;border-radius:6px;cursor:pointer">Reset to Captive Mode</button>
</form></div></body></html>"""

            body = page.encode()
            hdr = (b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\nContent-Length: " +
                   str(len(body)).encode() + b"\r\n\r\n")
            try:
                cl.sendall(hdr); cl.sendall(body)
            except:
                pass

        except Exception as e:
            try: sys.print_exception(e)
            except: pass
        finally:
            try:
                if cl: cl.close()
            except:
                pass

# -----------------------------
# Minimal HTTP GET (for OTA) — no urequests dependency
# -----------------------------
def http_get(url, timeout=8):
    # Supports http:// or https:// (basic); GitHub raw is https.
    # This is intentionally minimal; good enough for small JSON + main.py downloads.
    import ussl
    import ubinascii

    if not url.startswith("http"):
        raise ValueError("URL must start with http/https")

    proto, rest = url.split("://", 1)
    host_path = rest.split("/", 1)
    host = host_path[0]
    path = "/" + (host_path[1] if len(host_path) > 1 else "")

    port = 443 if proto == "https" else 80

    addr = socket.getaddrinfo(host, port)[0][-1]
    s = socket.socket()
    s.settimeout(timeout)
    s.connect(addr)
    if proto == "https":
        s = ussl.wrap_socket(s, server_hostname=host)

    req = "GET {} HTTP/1.1\r\nHost: {}\r\nUser-Agent: SAM-Sensor\r\nConnection: close\r\n\r\n".format(path, host)
    s.write(req.encode())

    # Read all
    data = b""
    while True:
        chunk = s.read(1024)
        if not chunk:
            break
        data += chunk
    try:
        s.close()
    except:
        pass

    # Split headers/body
    header, _, body = data.partition(b"\r\n\r\n")
    # crude status check
    if b" 200 " not in header.split(b"\r\n", 1)[0]:
        raise OSError("HTTP GET failed: " + header.split(b"\r\n", 1)[0].decode("utf-8","ignore"))
    return body

def sha256_bytes(b):
    try:
        import hashlib
        h = hashlib.sha256()
        h.update(b)
        return h.digest()
    except:
        return None

def hexlify(b):
    try:
        import ubinascii
        return ubinascii.hexlify(b).decode()
    except:
        return ""

def ota_check():
    if not OTA_ENABLED or not OTA_MANIFEST_URL:
        return None  # disabled
    try:
        body = http_get(OTA_MANIFEST_URL, timeout=8)
        manifest = ujson.loads(body.decode("utf-8","ignore"))
        key = f"{MODEL}-{SENSOR_ID}"  # e.g. LEVELTEMP-A
        entry = manifest.get(key, None)
        if not entry:
            return None
        latest = str(entry.get("latest_version","")).strip()
        if not latest or latest == FW_VERSION:
            return "NONE"
        return "AVAILABLE " + latest
    except Exception as e:
        try: print("OTA check error:", e)
        except: pass
        return None

def ota_apply():
    if not OTA_ENABLED or not OTA_MANIFEST_URL:
        return False

    try:
        body = http_get(OTA_MANIFEST_URL, timeout=8)
        manifest = ujson.loads(body.decode("utf-8","ignore"))
        key = f"{MODEL}-{SENSOR_ID}"
        entry = manifest.get(key, None)
        if not entry:
            print("No manifest entry for", key)
            return False

        latest = str(entry.get("latest_version","")).strip()
        files = entry.get("files", {})
        if not latest or not files:
            print("Manifest entry missing latest_version/files")
            return False

        # Backup current main.py
        try:
            if "backup" not in os.listdir():
                os.mkdir("backup")
        except:
            pass
        try:
            if "main.py" in os.listdir():
                # keep last-good backup
                try:
                    with open("main.py", "rb") as f:
                        cur = f.read()
                    with open("backup/main.py.bak", "wb") as f:
                        f.write(cur)
                except:
                    pass
        except:
            pass

        # Download each file to temp, verify, then replace
        for relpath, finfo in files.items():
            url = finfo.get("url", "")
            expect = str(finfo.get("sha256","")).lower().strip()

            if not url:
                print("Missing URL for", relpath)
                return False

            print("Downloading", relpath)
            data = http_get(url, timeout=12)

            if expect:
                got = sha256_bytes(data)
                if got is None:
                    print("No hashlib sha256 available; refusing update")
                    return False
                got_hex = hexlify(got)
                if got_hex != expect:
                    print("SHA256 mismatch for", relpath)
                    print(" expected:", expect)
                    print(" got     :", got_hex)
                    return False

            # ensure dirs exist
            parts = relpath.split("/")
            if len(parts) > 1:
                d = ""
                for p in parts[:-1]:
                    d = (d + "/" + p) if d else p
                    try:
                        if d not in os.listdir():
                            os.mkdir(d)
                    except:
                        pass

            tmp_path = relpath + ".new"
            with open(tmp_path, "wb") as f:
                f.write(data)

        # Replace phase (atomic-ish)
        for relpath in files.keys():
            tmp_path = relpath + ".new"
            try:
                # remove old file first if exists
                try:
                    os.remove(relpath)
                except:
                    pass
                os.rename(tmp_path, relpath)
            except Exception as e:
                print("Replace failed for", relpath, e)
                return False

        # Persist last check time
        cfg = load_sensor_config()
        cfg["ota_last_check"] = int(time.time())
        save_sensor_config(cfg)

        print("OTA applied OK -> reboot")
        time.sleep(1)
        machine.reset()
        return True

    except Exception as e:
        try: print("OTA apply error:", e)
        except: pass
        return False

# -----------------------------
# TCP server
# -----------------------------
def tcp_server():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", 8888))
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
                    cmd = line.decode(errors="ignore").strip()
                    if not cmd:
                        continue

                    # --- Standard commands ---
                    if cmd == "RX201":
                        val = read_temperature()
                        conn.send((val + "\n").encode())
                    elif cmd == "RX203":
                        val = read_pressure()
                        conn.send((val + "\n").encode())
                    elif cmd == "RX800":
                        conn.send((identify_sensor() + "\n").encode())

                    # --- New: version/model/head ---
                    elif cmd == "RX245":
                        conn.send((f"{SENSOR_ID}{FW_VERSION}\n").encode())
                    elif cmd == "RX246":
                        conn.send((f"{MODEL}\n").encode())
                    elif cmd == "RX247":
                        conn.send((f"{HEAD_TYPE}\n").encode())

                    # --- New: head selection ---
                    elif cmd == "HEAD?":
                        conn.send((f"{HEAD_TYPE}\n").encode())
                    elif cmd.startswith("HEAD:"):
                        new_head = cmd.split(":", 1)[1].strip().upper()
                        if new_head in ("MPM288DI", "ME782"):
                            # persist and update runtime
                            global HEAD_TYPE
                            HEAD_TYPE = new_head
                            cfg = load_sensor_config()
                            cfg["head_type"] = new_head
                            save_sensor_config(cfg)
                            conn.send(b"OK\n")
                        else:
                            conn.send(b"ERR\n")

                    # --- OTA ---
                    elif cmd == "UPDATE?":
                        st = ota_check()
                        if st is None:
                            conn.send(b"NONE\n")
                        else:
                            conn.send((st + "\n").encode())
                    elif cmd == "UPDATE":
                        conn.send(b"UPDATING\n")
                        try: conn.close()
                        except: pass
                        time.sleep(0.5)
                        ota_apply()
                        return

                    # --- Reboot ---
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
            try: print("TCP Error:", e)
            except: pass
        finally:
            try:
                if conn: conn.close()
            except:
                pass

def tcp_server_thread():
    try:
        tcp_server()
    except Exception as e:
        try: print("FATAL TCP:", e)
        except: pass

# -----------------------------
# Boot
# -----------------------------
white_led.off()
blue_led.off()
flash_led(white_led)

if not has_wifi_config():
    start_captive_portal()

ensure_ap_up()
time.sleep(0.2)

if connect_wifi():
    try:
        _thread.start_new_thread(tcp_server_thread, ())
    except Exception as e:
        try: print("Failed to start tcp server:", e)
        except: pass

try:
    run_ap_status_server()
except Exception as e:
    try: print("AP status server crashed:", e)
    except: pass
    time.sleep(1)
    machine.reset()
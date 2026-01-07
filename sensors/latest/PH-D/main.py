# Stork Aquatics pH Sensor MK2 (Pico W + MAX485)
import os
import machine
import network
import socket
import time
import ujson
import _thread
from machine import Pin, UART

# RS485 / MODBUS Config
UART_ID = 1
PIN_TX  = 4
PIN_RX  = 5
PIN_DE  = 6
BAUD    = 9600
BITS    = 8
PARITY  = None
STOP    = 1
SLAVE_ID = 2

uart = UART(UART_ID, baudrate=BAUD, bits=BITS, parity=PARITY, stop=STOP, tx=Pin(PIN_TX), rx=Pin(PIN_RX))
de_re = Pin(PIN_DE, Pin.OUT)
de_re.value(0)

SENSOR_ID = "D"
MODEL = "PH"
FW_VERSION = "1.1.0"
VARIANT = "RS485-MODBUS"

OTA_ENABLED = True
OTA_MANIFEST_URL = ""   # e.g. https://raw.githubusercontent.com/<user>/<repo>/main/sensor_update.json
SENSOR_CONFIG_FILE = "config.json"

# LEDs
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

# Wi-Fi config
WIFI_CONFIG_FILE = 'wifi.json'

def save_wifi_config(ssid, password):
    with open(WIFI_CONFIG_FILE, 'w') as f:
        ujson.dump({'ssid': ssid, 'password': password}, f)

def load_wifi_config():
    try:
        with open(WIFI_CONFIG_FILE, 'r') as f:
            return ujson.load(f)
    except:
        return None

# Learn-and-lock helper: persist full IP tuple after successful join
def persist_ip_lock_from_wlan(wlan):
    try:
        ip, mask, gw, dns = wlan.ifconfig()
        # Avoid locking AP subnet by mistake
        if isinstance(ip, str) and ip.startswith("192.168.4."):
            print("Skipping AP subnet; not locking assigned_ip")
            return
        cfg = load_wifi_config() or {}
        cfg["assigned_ip"] = ip
        cfg["netmask"] = mask
        cfg["gateway"] = gw
        cfg["dns"] = dns
        cfg["lock_ip"] = True
        with open(WIFI_CONFIG_FILE, 'w') as f:
            ujson.dump(cfg, f)
        print("Locked IP tuple to wifi.json:", ip, mask, gw, dns)
    except Exception as e:
        print("Failed to persist IP lock:", e)

def connect_wifi():
    import network
    import time

    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)

    try:
        with open("wifi.json", "r") as f:
            creds = ujson.load(f)  # <-- use ujson (MicroPython-safe)
            ssid = creds.get("ssid", "")
            password = creds.get("password", "")
            print("Config files found. Loading...")
            print("Loaded SSID & Password from file for:", ssid)
    except Exception as e:
        print("Error loading wifi.json:", e)
        start_captive_portal()
        return

    if not ssid:
        print("No SSID found in config. Starting captive portal.")
        start_captive_portal()
        return

    # If previously locked, use the stored tuple as STATIC now
    try:
        cfg = load_wifi_config() or {}
        if cfg.get("lock_ip") and all(cfg.get(k) for k in ("assigned_ip","netmask","gateway","dns")):
            try:
                wlan.ifconfig((cfg["assigned_ip"], cfg["netmask"], cfg["gateway"], cfg["dns"]))
                print("Using locked STATIC IP tuple:", cfg["assigned_ip"], cfg["netmask"], cfg["gateway"], cfg["dns"])
            except Exception as e:
                print("Static IP set failed; continuing DHCP:", e)
    except Exception as e:
        print("Lock-ip check failed:", e)

    print("Connecting to:", ssid)
    wlan.connect(ssid, password)

    max_wait = 10
    while max_wait > 0:
        if wlan.isconnected():
            break
        print("Waiting for connection... status =", wlan.status())
        time.sleep(1)
        max_wait -= 1

    if wlan.isconnected():
        ip = wlan.ifconfig()[0]
        print("Connected. IP:", ip)

        # learn-and-lock after successful join (your existing helper)
        try:
            persist_ip_lock_from_wlan(wlan)
        except Exception as e:
            print("persist_ip_lock_from_wlan failed:", e)

        blue_led.on()
        return True

    print("Failed to connect. Starting captive portal.")
    start_captive_portal()
    return False

def ensure_ap_up():
    ap = network.WLAN(network.AP_IF)
    if not ap.active():
        ap.active(True)
    ap.config(essid='Sensor-D', password='sensor1234')
    ap.ifconfig(('192.168.4.1','255.255.255.0','192.168.4.1','8.8.8.8'))
    print("System Ok")
    print("Looking for config files...")
    print("AP Active")

def start_captive_portal():
    import network, socket, time, sys

    # Bring AP up
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid='Sensor-D', password='sensor1234')
    try:
        ap.ifconfig(('192.168.4.1','255.255.255.0','192.168.4.1','8.8.8.8'))
    except Exception:
        pass
    print("AP Status page on http://192.168.4.1")

    # Blink LED in a thread (use your wrapper if present)
    try:
        _thread.start_new_thread(blink_led_thread, (blue_led, 0.5))
    except Exception:
        _thread.start_new_thread(blink_led, (blue_led, 0.5))

    # Small helpers
    def _urldecode(s):
        try:
            s = s.replace('+', ' ')
            out = b""
            i = 0
            bs = s.encode() if isinstance(s, str) else s
            ln = len(bs)
            while i < ln:
                c = bs[i:i+1]
                if c == b'%' and i+2 < ln:
                    try:
                        out += bytes([int(bs[i+1:i+3], 16)])
                        i += 3
                        continue
                    except Exception:
                        pass
                out += c
                i += 1
            return out.decode('utf-8', 'ignore')
        except Exception:
            return s

    def _read_http_request(cl, limit=4096):
        req = b""
        cl.settimeout(3)
        while b"\r\n\r\n" not in req and len(req) < limit:
            chunk = cl.recv(256)
            if not chunk:
                break
            req += chunk
        # Headers parsed; if body present, read it too
        headers_part, _, rest = req.partition(b"\r\n\r\n")
        first = headers_part.split(b"\r\n", 1)[0] if headers_part else b""
        # content-length?
        cl_lower = headers_part.lower()
        clen = 0
        idx = cl_lower.find(b"content-length:")
        if idx != -1:
            try:
                line = cl_lower[idx:].split(b"\r\n",1)[0]
                clen = int(line.split(b":",1)[1].strip())
            except Exception:
                clen = 0
        body = rest
        while len(body) < clen and len(body) < limit:
            chunk = cl.recv(256)
            if not chunk:
                break
            body += chunk
        return first.decode('utf-8','ignore'), body

    # Socket server on :80
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('0.0.0.0', 80))
        s.listen(3)
    except Exception as e:
        print("Captive portal bind FAILED:")
        try: sys.print_exception(e)
        except: print(e)
        return

    # Captive Mode - Wi-Fi Setup HTML
    FORM_HTML = b"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sensor D — Wi-Fi Setup</title>
<style>
  body{background:#f8f8f8;font-family:sans-serif;margin:0}
  .card{max-width:420px;margin:60px auto;padding:24px;background:#fff;border-radius:10px;box-shadow:0 0 10px rgba(0,0,0,.08)}
  h2{margin:0 0 12px;color:#2c3e50}
  input{width:100%;padding:10px;margin:8px 0}
  button{padding:10px 16px;background:#3498db;border:0;color:#fff;border-radius:6px;cursor:pointer}
</style>
</head><body>
  <div class="card">
    <h2>Sensor D — Wi-Fi Setup</h2>
    <form method="POST" action="/">
      <input name="ssid" placeholder="SSID" autocomplete="on">
      <input name="password" type="password" placeholder="Password" autocomplete="on">
      <button type="submit">Connect</button>
    </form>
    <p style="color:#777;font-size:13px;margin-top:12px">
      After connecting, the device will reboot. Reconnect to the sensors Wi-Fi
      and load this page again.
    </p>
  </div>
</body></html>"""


    # Main loop
    while True:
        cl = None
        try:
            cl, raddr = s.accept()
            first, body = _read_http_request(cl)

            parts = first.split()
            method = parts[0] if len(parts) > 0 else ""
            path   = parts[1] if len(parts) > 1 else "/"

            # Handle factory reset (works from AP status page too)
            if method == "POST" and path == "/factory_reset":
                try:
                    cl.send(b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: text/plain; charset=utf-8\r\n"
                            b"Connection: close\r\n\r\n"
                            b"Resetting to setup mode...\n")
                except Exception:
                    pass
                try: cl.close()
                except: pass
                factory_reset()
                return

            # Handle Wi-Fi credentials POST
            if method == "POST" and path == "/":
                try:
                    # Parse www-form-urlencoded
                    params = {}
                    try:
                        data = body.decode('utf-8','ignore')
                    except Exception:
                        data = ""
                    for pair in data.split("&"):
                        if "=" in pair:
                            k, v = pair.split("=", 1)
                            params[k.strip()] = _urldecode(v.strip())
                    ssid = params.get("ssid","").strip()
                    password = params.get("password","").strip()
                    print("Parsed SSID:", ssid)

                    if ssid:
                        save_wifi_config(ssid, password)
                        # quick STA connect attempt (non-blocking for long)
                        sta_if = network.WLAN(network.STA_IF)
                        sta_if.active(True)
                        sta_if.connect(ssid, password)
                        tmo = 8
                        while tmo > 0 and (not sta_if.isconnected()):
                            time.sleep(1); tmo -= 1
                        # Redirect to AP status (single page flow)
                        try:
                            cl.send(b"HTTP/1.1 302 Found\r\n"
                                    b"Location: http://192.168.4.1/\r\n"
                                    b"Content-Length: 0\r\n"
                                    b"Connection: close\r\n\r\n")
                        except Exception:
                            pass
                        try: cl.close()
                        except: pass
                        # brief pause so the browser follows redirect, then reboot
                        time.sleep(2)
                        machine.reset()
                        return
                    else:
                        # Missing SSID -> show form again
                        cl.send(b"HTTP/1.1 200 OK\r\n"
                                b"Content-Type: text/html; charset=utf-8\r\n"
                                b"Connection: close\r\n\r\n")
                        cl.send(FORM_HTML)
                except Exception as e:
                    print("POST parse error:")
                    try: sys.print_exception(e)
                    except: print(e)
                    try:
                        cl.send(b"HTTP/1.1 400 Bad Request\r\n"
                                b"Content-Type: text/plain; charset=utf-8\r\n"
                                b"Connection: close\r\n\r\nBad Request")
                    except Exception:
                        pass

            else:
                # GET -> show Wi-Fi setup form (captive stage)
                try:
                    cl.send(b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: text/html; charset=utf-8\r\n"
                            b"Connection: close\r\n\r\n")
                    cl.send(FORM_HTML)
                except Exception:
                    pass

        except Exception as e:
            print("Captive portal error:")
            try: sys.print_exception(e)
            except: print(e)
        finally:
            try:
                if cl:
                    cl.close()
            except:
                pass
            time.sleep_ms(50)

def has_wifi_config():
    try:
        with open("wifi.json","r") as _:
            return True
    except:
        return False

# MODBUS Helpers CRC + Read

def _crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc

def _rs485_tx(buf: bytes):
    time.sleep_ms(4)
    de_re.value(1)
    uart.write(buf)
    try:
        uart.flush()
    except:
        pass
    time.sleep_ms(3)
    de_re.value(0)

def _rs485_read(expected_min=5, timeout_ms=150):
    start = time.ticks_ms()
    rx = b""
    while time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
        chunk = uart.read()
        if chunk:
            rx += chunk
            if len(rx) >= expected_min:
                pass
        else:
            time.sleep_ms(2)
    return rx

def modbus_read_reg16(slave, reg_addr, count=1, timeout_ms=200):
    req = bytes([slave, 0x03, (reg_addr>>8)&0xFF, reg_addr&0xFF, 0x00, count&0xFF])
    crc = _crc16_modbus(req)
    req += bytes([crc & 0xFF, (crc >> 8) & 0xFF])
    uart.read()
    _rs485_tx(req)
    expect = 5 + (count * 2)
    resp = _rs485_read(expected_min=expect, timeout_ms=timeout_ms)
    if not resp or len(resp) < expect:
        raise OSError("timeout")
    if resp[0] != slave or resp[1] != 0x03:
        raise OSError("bad_hdr")
    if len(resp) < 5:
        raise OSError("short")
    data_part = resp[:-2]
    rx_crc = resp[-2] | (resp[-1] << 8)
    calc_crc = _crc16_modbus(data_part)
    if rx_crc != calc_crc:
        raise OSError("crc")
    bc = resp[2]
    if bc != count*2:
        raise OSError("bc")
    vals = []
    p = 3
    for _ in range(count):
        hi = resp[p]; lo = resp[p+1]; p += 2
        vals.append((hi<<8) | lo)
    return vals

def read_ph():
    try:
        v = modbus_read_reg16(SLAVE_ID, 1, count=1, timeout_ms=400)[0]
        ph = v * 0.01
        return str(round(ph, 2))
    except Exception as e:
        print("PH read error:", e)
        return "ERR"

def read_temperature():
    try:
        v = modbus_read_reg16(SLAVE_ID, 0, count=1, timeout_ms=400)[0]
        t = v * 0.1
        return str(round(t, 1))
    except Exception as e:
        print("Temp read error:", e)
        return "ERR"

def identify_sensor():
    return SENSOR_ID

def reset_sensor():
    machine.reset()

import sys

def tcp_server_thread():
    try:
        tcp_server()
    except Exception as e:
        print("FATAL in tcp_server thread:")
        try: sys.print_exception(e)
        except: print(e)

def blink_led_thread(led, interval):
    try:
        blink_led(led, interval)
    except Exception as e:
        print("FATAL in blink_led thread:")
        try: sys.print_exception(e)
        except: print(e)
        
# -----------------------------
# OTA helper functions (GitHub Raw manifest)
# -----------------------------
def _load_json(path, default=None):
    try:
        with open(path, "r") as f:
            return ujson.load(f)
    except:
        return default

def _save_json(path, data):
    try:
        with open(path, "w") as f:
            ujson.dump(data, f)
        return True
    except:
        return False

def _http_get(url, timeout=8):
    import ussl
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

    header, _, body = data.partition(b"\r\n\r\n")
    if b" 200 " not in header.split(b"\r\n", 1)[0]:
        raise OSError("HTTP GET failed: " + header.split(b"\r\n", 1)[0].decode("utf-8", "ignore"))
    return body

def _sha256_bytes(b):
    import hashlib
    h = hashlib.sha256()
    h.update(b)
    return h.digest()

def _hexlify(b):
    import ubinascii
    return ubinascii.hexlify(b).decode()

def ota_check():
    """Return 'NONE' or 'AVAILABLE x.y.z' (or None if disabled/error)."""
    if (not OTA_ENABLED) or (not OTA_MANIFEST_URL):
        return None
    try:
        body = _http_get(OTA_MANIFEST_URL, timeout=8)
        manifest = ujson.loads(body.decode("utf-8", "ignore"))

        key = "{}-{}".format(MODEL, SENSOR_ID)  # e.g. PH-D
        entry = manifest.get(key)
        if not entry:
            return None

        latest = str(entry.get("latest_version", "")).strip()
        if (not latest) or latest == FW_VERSION:
            return "NONE"
        return "AVAILABLE " + latest

    except Exception as e:
        try:
            print("OTA check error:", e)
        except:
            pass
        return None

def ota_apply():
    """Download files listed in manifest entry for this sensor, verify sha256, replace, reboot."""
    if (not OTA_ENABLED) or (not OTA_MANIFEST_URL):
        return False
    try:
        body = _http_get(OTA_MANIFEST_URL, timeout=8)
        manifest = ujson.loads(body.decode("utf-8", "ignore"))

        key = "{}-{}".format(MODEL, SENSOR_ID)  # PH-D
        entry = manifest.get(key)
        if not entry:
            print("No manifest entry for", key)
            return False

        latest = str(entry.get("latest_version", "")).strip()
        files = entry.get("files", {})
        if (not latest) or (not files):
            print("Manifest entry missing latest_version/files")
            return False

        # Backup folder
        try:
            if "backup" not in os.listdir():
                os.mkdir("backup")
        except:
            pass

        # Backup current files
        for relpath in files.keys():
            try:
                # best-effort backup if it exists
                with open(relpath, "rb") as f:
                    cur = f.read()
                with open("backup/{}.bak".format(relpath.replace("/", "_")), "wb") as f:
                    f.write(cur)
            except:
                pass

        # Download & verify to temp files
        for relpath, finfo in files.items():
            url = finfo.get("url", "")
            expect = str(finfo.get("sha256", "")).lower().strip()

            if not url:
                print("Missing URL for", relpath)
                return False

            print("Downloading", relpath)
            data = _http_get(url, timeout=12)

            if expect:
                got_hex = _hexlify(_sha256_bytes(data))
                if got_hex != expect:
                    print("SHA256 mismatch for", relpath)
                    print(" expected:", expect)
                    print(" got     :", got_hex)
                    return False

            # Ensure dirs exist for relpath
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

        # Replace phase
        for relpath in files.keys():
            tmp_path = relpath + ".new"
            try:
                try:
                    os.remove(relpath)
                except:
                    pass
                os.rename(tmp_path, relpath)
            except Exception as e:
                print("Replace failed for", relpath, e)
                return False

        # Bookkeeping
        cfg = _load_json(SENSOR_CONFIG_FILE, default={}) or {}
        cfg["ota_last_check"] = int(time.time())
        _save_json(SENSOR_CONFIG_FILE, cfg)

        print("OTA applied OK -> reboot")
        time.sleep(1)
        machine.reset()
        return True

    except Exception as e:
        try:
            print("OTA apply error:", e)
        except:
            pass
        return False

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

                # Process one line at a time
                while b"\n" in buf:
                    line, _, buf = buf.partition(b"\n")
                    cmd = line.decode().strip()
                    if not cmd:
                        continue
                    print("RX-CMD:", cmd)

                    try:
                        if cmd == "RX201":            # temperature
                            val = read_temperature()
                            conn.send((val + "\n").encode())
                            print("TX-Temperature=", val)

                        elif cmd == "RX205":          # pH
                            val = read_ph()
                            conn.send((val + "\n").encode())
                            print("TX-pH=", val)

                        elif cmd == "RX800":          # identify
                            val = identify_sensor()
                            conn.send((val + "\n").encode())
                            print("TX-ID=", val)

                        elif cmd.lower() == "r":      # reboot
                            conn.send(b"Rebooting\n")
                            try: conn.close()
                            except: pass
                            time.sleep(1)
                            reset_sensor()
                            return
                        
                        elif cmd == "RX245":
                            conn.send((f"{SENSOR_ID}{FW_VERSION}\n").encode())
                        elif cmd == "RX246":
                            conn.send((f"{MODEL}\n").encode())
                        elif cmd == "RX247":
                            conn.send((f"{VARIANT}\n").encode())

                        elif cmd == "UPDATE?":
                            st = ota_check()
                            conn.send(((st or "NONE") + "\n").encode())
                        elif cmd == "UPDATE":
                            conn.send(b"UPDATING\n")
                            try: conn.close()
                            except: pass
                            time.sleep(0.5)
                            ota_apply()
                            return
                        else:
                            conn.send(b"?\n")

                    except Exception as e:
                        print("Command Error:")
                        try: sys.print_exception(e)
                        except: print(e)
                        # don't kill the connection immediately; continue to next line
                        # break  # uncomment if you prefer to drop the client on error

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

def factory_reset():
    try:
        import os
        os.remove("wifi.json")  # Remove stored Wi-Fi credentials
        print("Factory reset: wifi.json removed.")
    except Exception as e:
        print("Factory reset error:", e)
    import machine
    machine.reset()  # Restart Pico

def run_ap_status_server():
    import socket, network, time, sys

    # Make sure AP is up and show its config
    ap = network.WLAN(network.AP_IF)
    if not ap.active():
        ap.active(True)
    try:
        ap.ifconfig(('192.168.4.1','255.255.255.0','192.168.4.1','8.8.8.8'))
    except Exception:
        pass
    ap_ip, ap_mask, ap_gw, ap_dns = ap.ifconfig()
    print("AP ifconfig:", ap_ip, ap_mask, ap_gw, ap_dns)

    # Bind to ALL interfaces (more reliable than binding to ap_ip)
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(('0.0.0.0', 80))
        s.listen(4)
        print("AP status server listening on :80 (AP IP:", ap_ip, ")")
    except Exception as e:
        print("AP status server bind FAILED:")
        try: sys.print_exception(e)
        except: print(e)
        return

    while True:
        # belt & braces: ensure AP stays active even if STA toggles
        try:
            if not ap.active():
                print("AP was down — reactivating…")
                ap.active(True)
                try:
                    ap.ifconfig(('192.168.4.1','255.255.255.0','192.168.4.1','8.8.8.8'))
                except Exception:
                    pass
        except Exception as e:
            print("AP check error:", e)

        cl = None
        try:
            cl, raddr = s.accept()
            print("AP HTTP client:", raddr)

            # read request
            req = b""
            cl.settimeout(3)
            while b"\r\n\r\n" not in req and len(req) < 4096:
                chunk = cl.recv(512)
                if not chunk:
                    break
                req += chunk

            first = (req.split(b"\r\n", 1)[0] or b"").decode("utf-8", "ignore")
            parts = first.split()
            method = parts[0] if len(parts) > 0 else ""
            path   = parts[1] if len(parts) > 1 else "/"
            print("AP HTTP request:", method, path)

            # Reset handler
            if method == "POST" and path == "/factory_reset":
                try:
                    cl.send(b"HTTP/1.1 200 OK\r\n"
                            b"Content-Type: text/plain; charset=utf-8\r\n"
                            b"Connection: close\r\n\r\n"
                            b"Resetting to setup mode...\n")
                except Exception:
                    pass
                try: cl.close()
                except: pass
                factory_reset()
                return

            # Build status
            sta = network.WLAN(network.STA_IF)
            sta_ip = sta.ifconfig()[0] if sta.isconnected() else "0.0.0.0"
            try: temp = read_temperature()
            except: temp = "ERR"
            try: ph = read_ph()
            except: ph = "ERR"

            page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sensor D — Connected</title>
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
    <h2>Sensor D — Connected</h2>
    <p><b>Assigned IP:</b> {sta_ip}</p>
    <hr>
    <p><b>Temperature (°C):</b> {temp}</p>
    <p><b>pH:</b> {ph}</p>
    <form method="POST" action="/factory_reset"
          onsubmit="return confirm('Reset Wi-Fi and reboot into setup mode?');">
      <button>Reset to Captive Mode</button>
    </form>
    <p style="color:#777; font-size:13px; margin-top:14px;">
      This page is always available at <b>http://192.168.4.1</b> When connected to the sensors Wi-Fi.
    </p>
  </div>
</body>
</html>"""

            body = page.encode()
            hdr = (b"HTTP/1.1 200 OK\r\n"
                   b"Content-Type: text/html; charset=utf-8\r\n"
                   b"Connection: close\r\n"
                   b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n")
            cl.sendall(hdr)
            cl.sendall(body)
            print("AP HTTP: served status page to", raddr)

        except Exception as e:
            print("AP HTTP error:")
            try: sys.print_exception(e)
            except: print(e)
        finally:
            try:
                if cl:
                    cl.close()
            except:
                pass
            time.sleep_ms(60)

# BOOT & RUN
import gc
white_led.off()
blue_led.off()
flash_led(white_led)

if not has_wifi_config():
    # No creds yet: captive portal (blocks, reboots after POST)
    start_captive_portal()

# We have creds: keep AP up so 192.168.4.1 is always available
ensure_ap_up()
time.sleep(0.2)

if connect_wifi():
    gc.collect()
    try:
        _thread.start_new_thread(tcp_server_thread, ())
    except Exception as e:
        print("Failed to start tcp_server_thread:", e)

# Run AP status HTTP server in the main thread (blocks forever).
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
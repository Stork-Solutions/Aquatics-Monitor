# Stork Aquatics TDS Sensor MK1 (Pico W + MAX485)
import os
import machine
import network
import socket
import time
import ujson
import _thread
from machine import Pin, UART

SENSOR_ID = "E"
MODEL = "TDS"
FW_VERSION = "1.1.0"
VARIANT = "RS485-MODBUS"

OTA_ENABLED = True

OTA_MANIFEST_URL = ""   # e.g. https://raw.githubusercontent.com/<user>/<repo>/main/sensor_update.json

SENSOR_CONFIG_FILE = "config.json"   # stores ota_last_check timestamp (optional)

# --- TDS conversion config (added) ---
K_DEFAULT = 0.50        # handheld-style k-factor
ALPHA_DEFAULT = 0.020   # per °C temperature coefficient
TC_ON_DEFAULT = True
MODE_DEFAULT = "sensor" # "sensor" -> use sensor TDS (reg 4); "calc" -> compute from EC
CFG_FILE = "tds_cfg.json"
try:
    with open(CFG_FILE, "r") as _f:
        _tds_cfg = ujson.load(_f)
except Exception:
    _tds_cfg = {"k":K_DEFAULT, "alpha":ALPHA_DEFAULT, "tc":TC_ON_DEFAULT, "mode":MODE_DEFAULT}
def _save_tds_cfg():
    try:
        with open(CFG_FILE, "w") as _f:
            ujson.dump(_tds_cfg, _f)
    except Exception as e:
        print("Save tds_cfg failed:", e)
# RS485 / MODBUS Config
UART_ID = 1
PIN_TX  = 4
PIN_RX  = 5
PIN_DE  = 6
BAUD    = 9600
BITS    = 8
PARITY  = None
STOP    = 1
SLAVE_ID = 4

uart = UART(UART_ID, baudrate=BAUD, bits=BITS, parity=PARITY, stop=STOP, tx=Pin(PIN_TX), rx=Pin(PIN_RX))
de_re = Pin(PIN_DE, Pin.OUT)
de_re.value(0)

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
            creds = ujson.load(f)
            ssid = creds.get("ssid", "")
            password = creds.get("password", "")
            print("Config files found. Loading...")
            print("Loaded SSID & Password from file for:", ssid)
    except Exception as e:
        print("Error loading wifi_config.json:", e)
        start_captive_portal()
        return

    if not ssid:
        print("No SSID found in config. Starting captive portal...")
        start_captive_portal()
        return

    # If previously locked, use the stored tuple as STATIC now
    try:
        cfg = load_wifi_config() or {}
        if cfg.get("lock_ip") and all(cfg.get(k) for k in ("assigned_ip","netmask","gateway","dns")):
            try:
                wlan.ifconfig((cfg["assigned_ip"], cfg["netmask"], cfg["gateway"], cfg["dns"]))
                print("Using LOCKED STATIC:", wlan.ifconfig())
            except Exception as e:
                print("Failed to set locked static ifconfig:", e)
    except Exception as e:
        print("Lock-IP pre-check failed:", e)

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
        print("Connected to:", ssid)
        print("IP Assigned:", ip)
        persist_ip_lock_from_wlan(wlan)
        blue_led.on()
        return True
    else:
        print("Failed to connect to Wi-Fi after 10 seconds.")
        start_captive_portal()
        return False

def ensure_ap_up():
    ap = network.WLAN(network.AP_IF)
    if not ap.active():
        ap.active(True)
    ap.config(essid='Sensor-E', password='sensor1234')
    ap.ifconfig(('192.168.4.1','255.255.255.0','192.168.4.1','8.8.8.8'))
    print("System Ok")
    print("Looking for config files...")
    print("AP Active")

def start_captive_portal():
    import network, socket, time, sys

    # Bring AP up
    ap = network.WLAN(network.AP_IF)
    ap.active(True)
    ap.config(essid='Sensor-E', password='sensor1234')
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
<title>Sensor E — Wi-Fi Setup</title>
<style>
  body{background:#f8f8f8;font-family:sans-serif;margin:0}
  .card{max-width:420px;margin:60px auto;padding:24px;background:#fff;border-radius:10px;box-shadow:0 0 10px rgba(0,0,0,.08)}
  h2{margin:0 0 12px;color:#2c3e50}
  input{width:100%;padding:10px;margin:8px 0}
  button{padding:10px 16px;background:#3498db;border:0;color:#fff;border-radius:6px;cursor:pointer}
</style>
</head><body>
  <div class="card">
    <h2>Sensor E — Wi-Fi Setup</h2>
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
    
# Auto Update Via GitHub    
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
        raise OSError("HTTP GET failed: " + header.split(b"\r\n", 1)[0].decode("utf-8","ignore"))
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
    if (not OTA_ENABLED) or (not OTA_MANIFEST_URL):
        return None
    try:
        body = _http_get(OTA_MANIFEST_URL, timeout=8)
        manifest = ujson.loads(body.decode("utf-8","ignore"))
        key = "{}-{}".format(MODEL, SENSOR_ID)  # e.g. TDS-E
        entry = manifest.get(key)
        if not entry:
            return None
        latest = str(entry.get("latest_version","")).strip()
        if (not latest) or latest == FW_VERSION:
            return "NONE"
        return "AVAILABLE " + latest
    except Exception as e:
        try: print("OTA check error:", e)
        except: pass
        return None

def ota_apply():
    if (not OTA_ENABLED) or (not OTA_MANIFEST_URL):
        return False
    try:
        body = _http_get(OTA_MANIFEST_URL, timeout=8)
        manifest = ujson.loads(body.decode("utf-8","ignore"))
        key = "{}-{}".format(MODEL, SENSOR_ID)
        entry = manifest.get(key)
        if not entry:
            print("No manifest entry for", key); return False

        latest = str(entry.get("latest_version","")).strip()
        files = entry.get("files", {})
        if (not latest) or (not files):
            print("Manifest entry missing latest_version/files"); return False

        # Backup folder
        try:
            if "backup" not in os.listdir():
                os.mkdir("backup")
        except:
            pass

        # Backup current main.py (and any files listed)
        for relpath in files.keys():
            try:
                if relpath in os.listdir():
                    with open(relpath, "rb") as f:
                        cur = f.read()
                    with open("backup/{}.bak".format(relpath.replace("/","_")), "wb") as f:
                        f.write(cur)
            except:
                pass

        # Download & verify to temp files
        for relpath, finfo in files.items():
            url = finfo.get("url","")
            expect = str(finfo.get("sha256","")).lower().strip()
            if not url:
                print("Missing URL for", relpath); return False

            print("Downloading", relpath)
            data = _http_get(url, timeout=12)

            if expect:
                got_hex = _hexlify(_sha256_bytes(data))
                if got_hex != expect:
                    print("SHA256 mismatch for", relpath)
                    print(" expected:", expect)
                    print(" got     :", got_hex)
                    return False

            # Ensure dirs exist
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
                try: os.remove(relpath)
                except: pass
                os.rename(tmp_path, relpath)
            except Exception as e:
                print("Replace failed for", relpath, e)
                return False

        # Optional bookkeeping
        cfg = _load_json(SENSOR_CONFIG_FILE, default={}) or {}
        cfg["ota_last_check"] = int(time.time())
        _save_json(SENSOR_CONFIG_FILE, cfg)

        print("OTA applied OK -> reboot")
        time.sleep(1)
        machine.reset()
        return True

    except Exception as e:
        try: print("OTA apply error:", e)
        except: pass
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
    # TX with small guard times for RS-485 turn-around
    time.sleep_ms(3)
    de_re.value(1)          # enable driver (TX)
    uart.write(buf)
    try:
        uart.flush()
    except:
        pass
    time.sleep_ms(4)        # ensure last byte on the wire
    de_re.value(0)          # release to RX
    time.sleep_ms(4)        # give the slave a moment

def _rs485_read_exact(expected_len: int, timeout_ms: int):
    """
    Read up to expected_len bytes or until timeout.
    Uses a fixed-size buffer (no growth), so memory stays bounded.
    Returns bytes() with whatever was received (may be shorter if timeout).
    """
    buf = bytearray(expected_len)
    view = memoryview(buf)
    got = 0
    start = time.ticks_ms()
    while got < expected_len and time.ticks_diff(time.ticks_ms(), start) < timeout_ms:
        n = uart.readinto(view[got:])
        if n and n > 0:
            got += n
        else:
            time.sleep_ms(1)
    return bytes(view[:got])

def modbus_read_reg16(slave, reg_addr, count=1, timeout_ms=600):
    # FC 0x03 (Holding Registers)
    req = bytes([slave, 0x03, (reg_addr>>8)&0xFF, reg_addr&0xFF, 0x00, count & 0xFF])
    crc = _crc16_modbus(req)
    req += bytes([crc & 0xFF, (crc >> 8) & 0xFF])

    uart.read()  # flush any prior noise
    _rs485_tx(req)

    # Response = slave(1) + func(1) + bytecount(1) + data(2*count) + crc(2)
    expect = 1 + 1 + 1 + (2*count) + 2
    resp = _rs485_read_exact(expect, timeout_ms)
    if len(resp) < expect:
        raise OSError("timeout")

    if resp[0] != slave or resp[1] != 0x03:
        raise OSError("bad_hdr")

    data_part = resp[:-2]
    rx_crc = resp[-2] | (resp[-1] << 8)
    if _crc16_modbus(data_part) != rx_crc:
        raise OSError("crc")

    if resp[2] != count*2:
        raise OSError("bc")

    vals = []
    p = 3
    for _ in range(count):
        vals.append((resp[p] << 8) | resp[p+1])
        p += 2
    return vals

def modbus_read_input16(slave, reg_addr, count=1, timeout_ms=700):
    # FC 0x04 (Input Registers)
    req = bytes([slave, 0x04, (reg_addr>>8)&0xFF, reg_addr&0xFF, 0x00, count & 0xFF])
    crc = _crc16_modbus(req)
    req += bytes([crc & 0xFF, (crc >> 8) & 0xFF])

    uart.read()
    _rs485_tx(req)

    expect = 1 + 1 + 1 + (2*count) + 2
    resp = _rs485_read_exact(expect, timeout_ms)
    if len(resp) < expect:
        raise OSError("timeout")

    if resp[0] != slave or resp[1] != 0x04:
        raise OSError("bad_hdr")

    data_part = resp[:-2]
    rx_crc = resp[-2] | (resp[-1] << 8)
    if _crc16_modbus(data_part) != rx_crc:
        raise OSError("crc")

    if resp[2] != count*2:
        raise OSError("bc")

    vals = []
    p = 3
    for _ in range(count):
        vals.append((resp[p] << 8) | resp[p+1])
        p += 2
    return vals

# Pick one FC to start with; per manual 0x03 is standard for these regs.
USE_FC04 = False  # set True only if the 0x03 calls keep timing out

def read_temperature():
    try:
        if USE_FC04:
            v = modbus_read_input16(SLAVE_ID, 0, count=1, timeout_ms=700)[0]
        else:
            v = modbus_read_reg16(SLAVE_ID, 0, count=1, timeout_ms=700)[0]
        return str(round(v * 0.1, 1))
    except Exception as e:
        print("Temp read error:", e)
        return "ERR"

def read_tds():
    try:
        if USE_FC04:
            v = modbus_read_input16(SLAVE_ID, 4, count=1, timeout_ms=700)[0]
        else:
            v = modbus_read_reg16(SLAVE_ID, 4, count=1, timeout_ms=700)[0]
        return str(int(v))
    except Exception as e:
        print("TDS read error:", e)
        return "ERR"


# Helper: Read 2x16-bit registers and decode IEEE754 float (ABCD order) from Holding or Input (added)
def modbus_read_float32_abcd(slave, reg_addr, use_fc04=False, timeout_ms=700):
    vals = modbus_read_input16(slave, reg_addr, count=2, timeout_ms=timeout_ms) if use_fc04 else modbus_read_reg16(slave, reg_addr, count=2, timeout_ms=timeout_ms)
    b0 = (vals[0] >> 8) & 0xFF; b1 = vals[0] & 0xFF; b2 = (vals[1] >> 8) & 0xFF; b3 = vals[1] & 0xFF
    import struct
    try:
        return struct.unpack(">f", bytes([b0,b1,b2,b3]))[0]
    except Exception:
        return None

# New: conductivity in µS/cm (added)
def read_conductivity_uScm():
    """
    Prefer 32-bit float mS/cm at regs 41-42 (0x29) then convert to µS/cm.
    Fallback to 16-bit register 1 if float read fails.
    """
    try:
        v_ms = modbus_read_float32_abcd(SLAVE_ID, 41, use_fc04=USE_FC04, timeout_ms=700)
        if v_ms is not None:
            uS = int(round(v_ms * 1000.0))
            return str(uS)
    except Exception as e:
        print("EC float read error:", e)
    try:
        if USE_FC04:
            v = modbus_read_input16(SLAVE_ID, 1, count=1, timeout_ms=700)[0]
        else:
            v = modbus_read_reg16(SLAVE_ID, 1, count=1, timeout_ms=700)[0]
        return str(int(v))
    except Exception as e:
        print("EC read error:", e)
        return "ERR"

def read_conductivity_uScm_raw():
    """
    Float EC (no temperature compensation) at regs 45-46 (mS/cm) -> µS/cm string.
    """
    try:
        v_ms = modbus_read_float32_abcd(SLAVE_ID, 45, use_fc04=USE_FC04, timeout_ms=700)
        if v_ms is not None:
            return str(int(round(v_ms * 1000.0)))
    except Exception as e:
        print("EC raw float read error:", e)
    return "ERR"

# New: salinity PSU from reg 2 scaled 0.01 (added)
def read_salinity_psu():
    try:
        if USE_FC04:
            v = modbus_read_input16(SLAVE_ID, 2, count=1, timeout_ms=700)[0]
        else:
            v = modbus_read_reg16(SLAVE_ID, 2, count=1, timeout_ms=700)[0]
        return f"{(int(v)/100.0):.2f}"
    except Exception as e:
        print("Salinity read error:", e)
        return "ERR"

# New: temp compensation helper (added)
def _comp_to_25C(ec_uS, temp_C, alpha, enabled):
    if not enabled or ec_uS == "ERR" or temp_C == "ERR":
        return ec_uS
    try:
        ec = float(ec_uS); t = float(temp_C)
        return ec / (1.0 + float(alpha) * (t - 25.0))
    except Exception:
        return ec_uS
    
def read_reg_u16(addr):
    try:
        if USE_FC04:
            v = modbus_read_input16(SLAVE_ID, addr, count=1, timeout_ms=700)[0]
        else:
            v = modbus_read_reg16(SLAVE_ID, addr, count=1, timeout_ms=700)[0]
        return int(v)
    except Exception as e:
        print("Read reg {} err:".format(addr), e)
        return None

def get_probe_cfg_snapshot():
    snap = {}
    # compensated EC (41-42) and raw (45-46) as µS/cm
    try:
        v_ms = modbus_read_float32_abcd(SLAVE_ID, 41, use_fc04=USE_FC04, timeout_ms=700)
        snap["ec_comp_uS"] = None if v_ms is None else int(round(v_ms*1000))
    except Exception as e:
        print("snap ec_comp:", e); snap["ec_comp_uS"] = None
    try:
        v_ms = modbus_read_float32_abcd(SLAVE_ID, 45, use_fc04=USE_FC04, timeout_ms=700)
        snap["ec_raw_uS"] = None if v_ms is None else int(round(v_ms*1000))
    except Exception as e:
        print("snap ec_raw:", e); snap["ec_raw_uS"] = None
    snap["ec_u16_uS"] = read_reg_u16(1)
    snap["tds_reg_ppm"] = read_reg_u16(4)
    snap["temp_tenthsC"] = read_reg_u16(0)
    snap["alpha_x1000"] = read_reg_u16(16)
    snap["tds_k_x1000"] = read_reg_u16(17)
    snap["refT_C"] = read_reg_u16(18)
    snap["meascoef"] = read_reg_u16(20)
    return snap    
    
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
                            
                        elif cmd == "RX207C":         # Calculated TDS with current settings (FOR TESTING ONLY, not used in GUI)
                            ec = read_conductivity_uScm_raw()   # use RAW, not probe-comp
                            temp = read_temperature()
                            # Prefer probe alpha if readable
                            probe_alpha = read_reg_u16(16)
                            alpha = (probe_alpha/1000.0) if isinstance(probe_alpha, int) else _tds_cfg.get("alpha", ALPHA_DEFAULT)
                            ec25 = _comp_to_25C(ec, temp, alpha, _tds_cfg.get("tc", TC_ON_DEFAULT))
                            try:
                                k = float(_tds_cfg.get("k", K_DEFAULT))
                                v = int(round(float(ec25) * k)) if ec25 != "ERR" else None
                                val = str(v) if v is not None else "ERR"
                            except Exception as e:
                                print("calc ppm err:", e); val = "ERR"
                            conn.send((val + "\n").encode())
                            print("TX-TDS(calc)=", val)
                        
                        elif cmd == "RX206":          # Conductivity (µS/cm) (added)
                            val = read_conductivity_uScm()
                            conn.send((val + "\n").encode())
                            print("TX-EC_uS/cm=", val)

                        elif cmd == "RX208":          # Salinity (PSU) (added)
                            val = read_salinity_psu()
                            conn.send((val + "\n").encode())
                            print("TX-PSU=", val)

                        elif cmd == "RX209":          # Conversion settings report (added)
                            srep = "MODE={};K={:.3f};TC={};ALPHA={:.3f};FW={}{}".format(
                                _tds_cfg.get("mode", MODE_DEFAULT),
                                float(_tds_cfg.get("k", K_DEFAULT)),
                                "ON" if _tds_cfg.get("tc", TC_ON_DEFAULT) else "OFF",
                                float(_tds_cfg.get("alpha", ALPHA_DEFAULT)),
                                SENSOR_ID, FW_VERSION
                            )
                            conn.send((srep + "\n").encode())
                            print("TX-CFG=", srep)

                        elif cmd.startswith("RX240"):  # Set k-factor (added)
                            try:
                                _, val = cmd.split(None, 1)
                                _tds_cfg["k"] = max(0.3, min(0.9, float(val)))
                                conn.send(b"OK\n")
                            except Exception as e:
                                print("RX240 err:", e); conn.send(b"ERR\n")

                        elif cmd.startswith("RX241"):  # Set TC on/off (added)
                            try:
                                _, val = cmd.split(None, 1)
                                _tds_cfg["tc"] = (val.strip() in ("1","ON","on","true","True"))
                                conn.send(b"OK\n")
                            except Exception as e:
                                print("RX241 err:", e); conn.send(b"ERR\n")

                        elif cmd.startswith("RX242"):  # Set alpha (added)
                            try:
                                _, val = cmd.split(None, 1)
                                _tds_cfg["alpha"] = max(0.0, min(0.04, float(val)))
                                conn.send(b"OK\n")
                            except Exception as e:
                                print("RX242 err:", e); conn.send(b"ERR\n")

                        elif cmd == "RX243":          # Save cfg (added)
                            _save_tds_cfg(); conn.send(b"OK\n")

                        elif cmd == "RX244":          # Defaults (added)
                            _tds_cfg.update({"k":K_DEFAULT, "alpha":ALPHA_DEFAULT, "tc":TC_ON_DEFAULT, "mode":MODE_DEFAULT})
                            conn.send(b"OK\n")

                        elif cmd == "RX245":          # Firmware version
                            conn.send((f"{SENSOR_ID}{FW_VERSION}\n").encode())

                        elif cmd == "RX246":          # Model
                            conn.send((f"{MODEL}\n").encode())

                        elif cmd == "RX247":          # Variant
                            conn.send((f"{VARIANT}\n").encode())

                        elif cmd == "UPDATE?":        # OTA status
                            st = ota_check()
                            conn.send(((st or "NONE") + "\n").encode())

                        elif cmd == "UPDATE":         # OTA apply
                            conn.send(b"UPDATING\n")
                            try: conn.close()
                            except: pass
                            time.sleep(0.5)
                            ota_apply()
                            return

                        elif cmd == "RX207":          # TDS
                            if str(_tds_cfg.get("mode","sensor")).lower() == "calc":
                                ec = read_conductivity_uScm_raw()
                                temp = read_temperature()
                                probe_alpha = read_reg_u16(16)
                                alpha = (probe_alpha/1000.0) if isinstance(probe_alpha, int) else _tds_cfg.get("alpha", ALPHA_DEFAULT)
                                ec25 = _comp_to_25C(ec, temp, alpha, _tds_cfg.get("tc", TC_ON_DEFAULT))
                                try:
                                    k = float(_tds_cfg.get("k", K_DEFAULT))
                                    v = int(round(float(ec25) * k)) if ec25 != "ERR" else None
                                    val = str(v) if v is not None else "ERR"
                                except Exception as e:
                                    print("calc ppm err:", e); val = "ERR"
                            else:
                                val = read_tds()
                            conn.send((val + "\n").encode())
                            print("TX-TDS=", val)
                        
                        elif cmd == "RX800":          # identify
                            val = identify_sensor()
                            conn.send((val + "\n").encode())
                            print("TX-ID=", val)
                        
                        elif cmd == "RX260":         # DIAG snapshot
                            snap = get_probe_cfg_snapshot()
                            def fmt(x): return "NA" if x is None else str(x)
                            line = ("ECcomp_uS={};ECraw_uS={};ECu16_uS={};TDSreg_ppm={};Temp_tenthsC={};"
                                    "Alpha_x1000={};K_x1000={};RefT_C={};MeasCoef={}").format(
                                fmt(snap.get("ec_comp_uS")), fmt(snap.get("ec_raw_uS")), fmt(snap.get("ec_u16_uS")),
                                fmt(snap.get("tds_reg_ppm")), fmt(snap.get("temp_tenthsC")),
                                fmt(snap.get("alpha_x1000")), fmt(snap.get("tds_k_x1000")),
                                fmt(snap.get("refT_C")), fmt(snap.get("meascoef")))
                            conn.send((line + "\n").encode())
                            print("TX-DIAG=", line)

                        elif cmd.lower() == "r":      # reboot
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
            # Build status
            sta = network.WLAN(network.STA_IF)
            sta_ip = sta.ifconfig()[0] if sta.isconnected() else "0.0.0.0"

            try:
                temp = read_temperature()
            except:
                temp = "ERR"

            try:
                if str(_tds_cfg.get("mode", "sensor")).lower() == "calc":
                    ec = read_conductivity_uScm_raw()
                    t = read_temperature()
                    probe_alpha = read_reg_u16(16)
                    alpha = (probe_alpha/1000.0) if isinstance(probe_alpha, int) else _tds_cfg.get("alpha", ALPHA_DEFAULT)
                    ec25 = _comp_to_25C(ec, t, alpha, _tds_cfg.get("tc", TC_ON_DEFAULT))
                    k = float(_tds_cfg.get("k", K_DEFAULT))
                    tds = str(int(round(float(ec25) * k))) if ec25 != "ERR" else "ERR"
                else:
                    tds = read_tds()
            except:
                tds = "ERR"

            try:
                # Use your current mode-aware behaviour:
                if str(_tds_cfg.get("mode", "sensor")).lower() == "calc":
                    ec = read_conductivity_uScm_raw()
                    t = read_temperature()
                    probe_alpha = read_reg_u16(16)
                    alpha = (probe_alpha/1000.0) if isinstance(probe_alpha, int) else _tds_cfg.get("alpha", ALPHA_DEFAULT)
                    ec25 = _comp_to_25C(ec, t, alpha, _tds_cfg.get("tc", TC_ON_DEFAULT))
                    k = float(_tds_cfg.get("k", K_DEFAULT))
                    tds = str(int(round(float(ec25) * k))) if ec25 != "ERR" else "ERR"
                else:
                    tds = read_tds()
            except:
                tds = "ERR"

            page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sensor E — Connected</title>
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
    <h2>Sensor E — Connected</h2>
    <p><b>Assigned IP:</b> {sta_ip}</p>
    <hr>
    <p><b>Temperature (°C):</b> {temp}</p>
    <p><b>TDS:</b> {tds}</p>
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

def has_wifi_config():
    try:
        with open("wifi.json", "r") as _:
            return True
    except:
        return False

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
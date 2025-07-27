from machine import Pin, I2C
import time
import ds3231
import bluetooth
from ble_simple_peripheral import BLESimplePeripheral
import utime

# --- Configuration ---
BASE_TRIGGER = {
    "year": 2025,
    "month": 7,
    "day": 26,
    "hour": 13,
    "minute": 59
}

SCHEDULED_EVENTS = [
    (2025, 7, 26, 19, 57),
    (2025, 7, 26, 20, 3),
    (2025, 8, 1, 8, 0),
]

INTERVAL_DAYS = 3

RELAY_DURATION_MIN = 1

CHECK_INTERVAL_SEC = 5

# --- Setup RTC and Relay ---
i2c = I2C(0, scl=Pin(5), sda=Pin(4))
rtc = ds3231.DS3231(i2c)
relay = Pin(14, Pin.OUT)

relay_is_on = False
relay_off_time = None

# --- Setup BLE ---
ble = bluetooth.BLE()
ble.active(True)
sp = BLESimplePeripheral(ble)
print("Pico W BLE initialized")


# --- Boot Time Restore ---
current_time = rtc.datetime()
current_unix = utime.mktime((current_time[0], current_time[1], current_time[2],
                             current_time[4], current_time[5], current_time[6], 0, 0))
scheduled_unix = utime.mktime((BASE_TRIGGER["year"], BASE_TRIGGER["month"], BASE_TRIGGER["day"],
                               BASE_TRIGGER["hour"], BASE_TRIGGER["minute"], 0, 0, 0))

if scheduled_unix <= current_unix < scheduled_unix + (RELAY_DURATION_MIN * 60):
    relay.value(0)
    relay_is_on = True
    relay_off_time = scheduled_unix + (RELAY_DURATION_MIN * 60)
    print("Relay restored ON at boot")
    sp.send("Relay ON restored at boot — Time remaining: {} min".format((relay_off_time - current_unix) // 60))

# --- Utility Functions ---
def format_time(dt):
    return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(dt[0], dt[1], dt[2], dt[4], dt[5], dt[6])

def is_scheduled_event(dt):
    for y, m, d, h, minute in SCHEDULED_EVENTS:
        if (dt[0] == y and dt[1] == m and dt[2] == d and dt[4] == h and dt[5] == minute):
            return True
    return False

def is_nth_day_trigger(dt):
    base_unix = utime.mktime((BASE_TRIGGER["year"], BASE_TRIGGER["month"], BASE_TRIGGER["day"],
                              BASE_TRIGGER["hour"], BASE_TRIGGER["minute"], 0, 0, 0))
    now_unix = utime.mktime((dt[0], dt[1], dt[2], dt[4], dt[5], dt[6], 0, 0))
    days_elapsed = (now_unix - base_unix) // (86400)

    return (days_elapsed % INTERVAL_DAYS == 0 and
            dt[4] == BASE_TRIGGER["hour"] and dt[5] == BASE_TRIGGER["minute"])

def next_valid_trigger(now_unix):
    future_triggers = []

    for y, m, d, h, minute in SCHEDULED_EVENTS:
        event_unix = utime.mktime((y, m, d, h, minute, 0, 0, 0))
        if event_unix > now_unix:
            future_triggers.append(event_unix)

    base_unix = utime.mktime((BASE_TRIGGER["year"], BASE_TRIGGER["month"], BASE_TRIGGER["day"],
                              BASE_TRIGGER["hour"], BASE_TRIGGER["minute"], 0, 0, 0))
    days_ahead = 0
    while True:
        nth_unix = base_unix + days_ahead * INTERVAL_DAYS * 86400
        if nth_unix > now_unix:
            future_triggers.append(nth_unix)
            break
        days_ahead += 1

    if future_triggers:
        next_trigger = min(future_triggers)
        next_dt = utime.localtime(next_trigger)
        return "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
            next_dt[0], next_dt[1], next_dt[2], next_dt[3], next_dt[4], next_dt[5])
    else:
        return "No future triggers found"
    
def handle_ble_command():
    rx = sp.read()
    if rx:
        msg = rx.decode().strip()

        if msg.startswith("ADD:"):
            try:
                date_part = msg[4:]
                parts = date_part.split(" ")
                y, m, d = map(int, parts[0].split("-"))
                h, min = map(int, parts[1].split(":"))
                SCHEDULED_EVENTS.append((y, m, d, h, min))
                sp.send("Event added: {} {:02d}:{:02d}".format(parts[0], h, min))
            except:
                sp.send("Invalid ADD format. Use ADD:YYYY-MM-DD HH:MM")

        elif msg.startswith("DURATION:"):
            try:
                new_duration = int(msg.split(":")[1])
                globals()["RELAY_DURATION_MIN"] = new_duration
                sp.send("Duration updated to: {} min".format(new_duration))
            except:
                sp.send("Invalid DURATION format. Use DURATION:X")

MAX_LOG_LINES = 50

def on_rx(msg):
    print("RX received:", msg.decode().strip()) 
    msg = msg.decode().strip()
    print("Received BLE command:", msg)

    if msg.startswith("ADD:"):
        try:
            date_part = msg[4:]
            parts = date_part.split(" ")
            y, m, d = map(int, parts[0].split("-"))
            h, minute = map(int, parts[1].split(":"))
            SCHEDULED_EVENTS.append((y, m, d, h, minute))
            sp.send("Event added: {} {:02d}:{:02d}".format(parts[0], h, minute))
        except:
            sp.send("Invalid ADD format. Use ADD:YYYY-MM-DD HH:MM")

    elif msg.startswith("DURATION:"):
        try:
            new_duration = int(msg.split(":")[1])
            globals()["RELAY_DURATION_MIN"] = new_duration
            sp.send("Duration updated to: {} min".format(new_duration))
            print("⚙️ Duration updated to:", new_duration)
        except Exception as e:
            print("❌ Error parsing DURATION:", e)
            sp.send("Invalid DURATION format. Use DURATION:X")
            
    elif msg == "GETLOG":
        try:
            with open("relay_log.txt", "r") as f:
                lines = f.readlines()[-MAX_LOG_LINES:]
                for line in lines:
                    sp.send("[LOG] " + line.strip())
        except OSError:
            sp.send("[LOG] Failed to read log file")

def log_event(action, timestamp, duration=None):
    log_entry = "{} — {}".format(timestamp, action)
    if duration:
        log_entry += " (Duration: {} min)".format(duration)
    log_entry += "\n"

    try:
        # Read existing lines
        try:
            with open("relay_log.txt", "r") as f:
                lines = f.readlines()
        except OSError:
            lines = []

        # Add new entry
        lines.append(log_entry)

        # Keep only last 50
        if len(lines) > 50:
            lines = lines[-50:]

        # Overwrite file line-by-line
        with open("relay_log.txt", "w") as f:
            for line in lines:
                f.write(line)

    except Exception as e:
        print("⚠️ Log write failed:", e)

# Register the BLE callback:
sp.on_write(on_rx)

relay.value(0) # Turn OFF

# --- Main Loop ---
while True:
    current_time = rtc.datetime()
    timestamp = format_time(current_time)

    if relay_is_on:
        current_unix = utime.time()
        remaining = relay_off_time - current_unix

        if remaining <= 0:
            relay.value(0)  # Turn OFF
            relay_is_on = False
            sp.send("Relay OFF at " + timestamp)
            print("Relay OFF at " + timestamp)
            log_event("Relay OFF", timestamp)

        else:
            elapsed = (RELAY_DURATION_MIN * 60) - remaining
            mins_remain = remaining // 60
            secs_remain = remaining % 60
            mins_elapsed = elapsed // 60
            secs_elapsed = elapsed % 60

            sp.send("Relay ON — Remaining: {:02d}m {:02d}s | Elapsed: {:02d}m {:02d}s".format(
                mins_remain, secs_remain, mins_elapsed, secs_elapsed))
            print("Relay ON at " + timestamp + " | Remaining: {:02d}m {:02d}s | Elapsed: {:02d}m {:02d}s".format(
                mins_remain, secs_remain, mins_elapsed, secs_elapsed))

    else:
        if is_scheduled_event(current_time) or is_nth_day_trigger(current_time):
            relay.value(1)
            
            relay_is_on = True
            relay_off_time = utime.time() + (RELAY_DURATION_MIN * 60)
            sp.send("Current Time at " + timestamp)
            sp.send("Relay ON at " + timestamp + " for {} min".format(RELAY_DURATION_MIN))
            print("Current Time at " + timestamp)
            print("Relay ON at " + timestamp + " for {} min".format(RELAY_DURATION_MIN))
            log_event("Relay ON", timestamp, RELAY_DURATION_MIN)

        else:
            current_unix = utime.mktime((current_time[0], current_time[1], current_time[2],
                                         current_time[4], current_time[5], current_time[6], 0, 0))
            next_time = next_valid_trigger(current_unix)
            sp.send("Current Time at " + timestamp)
            sp.send("Next scheduled change: {} (Relay Duration: {} min)".format(next_time, RELAY_DURATION_MIN))
            print("Current Time at", timestamp)
            print("Next scheduled change:", next_time, "(Relay Duration: {} min)".format(RELAY_DURATION_MIN))

    time.sleep(CHECK_INTERVAL_SEC)
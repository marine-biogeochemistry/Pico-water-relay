from machine import Pin, I2C
import time
import ds3231
import bluetooth
from ble_simple_peripheral import BLESimplePeripheral
import utime
from collections import namedtuple
import os

# --- Configuration ---
BASE_TRIGGER = {
    "year": 2035,
    "month": 8,
    "day": 5,
    "hour": 00,
    "minute": 00
}

SCHEDULED_EVENTS = [
    # (2025, 8, 23, 11, 37),
    # (2025, 7, 26, 20, 3),
    # (2025, 8, 1, 8, 0),
]

INTERVAL_DAYS = 30

RELAY_DURATION_MIN = 2

CHECK_INTERVAL_SEC = 5

MAX_LOG_LINES = 100  # Maximum number of log lines to return

# --- Setup RTC and Relay ---
i2c = I2C(0, scl=Pin(5), sda=Pin(4))
rtc = ds3231.DS3231(i2c)
relay = Pin(14, Pin.OUT)

def relay_on():
    relay.value(0)
    global relay_is_on
    relay_is_on = True

def relay_off():
    relay.value(1)
    global relay_is_on
    relay_is_on = False

relay_is_on = False
relay_off_time = None
active_duration_sec = RELAY_DURATION_MIN * 60  # Tracks the duration of the current ON window
settime_buffer = ""  # Accumulates partial SETTIME command chunks
pending_date = None  # tuple (y,m,d)
pending_time = None  # tuple (h,m,s)

# --- Setup BLE ---
ble = bluetooth.BLE()
ble.active(True)
sp = BLESimplePeripheral(ble)
print(" Pico W BLE initialized and advertising")
print(" Device should be discoverable as 'mpy-uart'")

try:
    timestamp = utime.localtime(os.stat("schedule.txt")[8])
    print(" Schedule last modified:", "{:04d}-{:02d}-{:02d} {:02d}:{:02d}".format(*timestamp[:5]))
    sp.send(" Schedule last updated: {:04d}-{:02d}-{:02d} {:02d}:{:02d}".format(*timestamp[:5]))
except OSError:
    print(" No schedule file found — skipping timestamp feedback")
    sp.send(" No schedule file to restore yet")

try:
    with open("schedule.txt", "r") as f:
        for line in f:
            parts = line.strip().split(" ")
            if len(parts) >= 2:
                y, m, d = map(int, parts[0].split("-"))
                h, minute = map(int, parts[1].split(":"))
                duration = next((int(p) for p in parts[2:] if p.isdigit()), RELAY_DURATION_MIN)
                new_event = (y, m, d, h, minute, duration)
                if new_event not in SCHEDULED_EVENTS:
                    SCHEDULED_EVENTS.append(new_event)

    # Now sort after loading
    SCHEDULED_EVENTS.sort(key=lambda x: utime.mktime((x[0], x[1], x[2], x[3], x[4], 0, 0, 0)))
    print(" Schedule restored from file: {} entries".format(len(SCHEDULED_EVENTS)))
    sp.send(" Schedule file loaded — {} events restored".format(len(SCHEDULED_EVENTS)))

except Exception as e:
    print(" Failed to restore schedule:", e)


# --- Boot Time Restore ---
current_time = rtc.datetime()
current_unix = utime.mktime((current_time[0], current_time[1], current_time[2],
                             current_time[4], current_time[5], current_time[6], 0, 0))
scheduled_unix = utime.mktime((BASE_TRIGGER["year"], BASE_TRIGGER["month"], BASE_TRIGGER["day"],
                               BASE_TRIGGER["hour"], BASE_TRIGGER["minute"], 0, 0, 0))

if scheduled_unix <= current_unix < scheduled_unix + (RELAY_DURATION_MIN * 60):
    relay_on()
    relay_is_on = True
    relay_off_time = scheduled_unix + (RELAY_DURATION_MIN * 60)
    active_duration_sec = RELAY_DURATION_MIN * 60
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
    future_events = []

    # Check regular scheduled events
    for event in SCHEDULED_EVENTS:
        if len(event) == 6:
            y, m, d, h, minute, duration = event
        else:
            y, m, d, h, minute = event
            duration = RELAY_DURATION_MIN
        trigger_unix = utime.mktime((y, m, d, h, minute, 0, 0, 0))
        if trigger_unix > now_unix:
            future_events.append((trigger_unix, duration))

    # Check next Nth day trigger
    base_unix = utime.mktime((BASE_TRIGGER["year"], BASE_TRIGGER["month"], BASE_TRIGGER["day"],
                              BASE_TRIGGER["hour"], BASE_TRIGGER["minute"], 0, 0, 0))
    days_ahead = 0
    while True:
        nth_unix = base_unix + days_ahead * INTERVAL_DAYS * 86400
        if nth_unix > now_unix:
            future_events.append((nth_unix, RELAY_DURATION_MIN))
            break
        days_ahead += 1

    # Return the earliest upcoming event
    if future_events:
        next_trigger, duration = min(future_events, key=lambda x: x[0])
        next_dt = utime.localtime(next_trigger)
        return (
            "{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
                next_dt[0], next_dt[1], next_dt[2], next_dt[3], next_dt[4], next_dt[5]
            ),
            duration
        )
    else:
        return ("No future triggers found", RELAY_DURATION_MIN)
    
def handle_ble_command():
    rx = sp.read()
    if rx:
        msg = rx.decode().strip()

        if msg.startswith("ADD:"):
            try:
                parts = msg[4:].split(" ")
                y, m, d = map(int, parts[0].split("-"))
                h, minute = map(int, parts[1].split(":"))
                duration = int(parts[2]) if len(parts) > 2 else RELAY_DURATION_MIN
                new_event = (y, m, d, h, minute, duration)

                if new_event not in SCHEDULED_EVENTS:
                    SCHEDULED_EVENTS.append(new_event)
                    sp.send("Event added: {} {:02d}:{:02d} Duration: {} min".format(parts[0], h, minute, duration))
                else:
                    sp.send("Duplicate event ignored: {} {:02d}:{:02d}".format(parts[0], h, minute))
            except:
                sp.send("Invalid ADD format. Use ADD:YYYY-MM-DD HH:MM [DURATION]")

        elif msg.startswith("DURATION:"):
            try:
                new_duration = int(msg.split(":")[1])
                globals()["RELAY_DURATION_MIN"] = new_duration
                sp.send("Duration updated to: {} min".format(new_duration))
            except:
                sp.send("Invalid DURATION format. Use DURATION:X")

        elif msg == "NEXTTRIGGER":
            current_time = rtc.datetime()
            current_unix = utime.mktime((current_time[0], current_time[1], current_time[2],
                                         current_time[4], current_time[5], current_time[6], 0, 0))

            future_events = []
            for event in SCHEDULED_EVENTS:
                if len(event) == 6:
                    y, m, d, h, minute, duration = event
                else:
                    y, m, d, h, minute = event
                    duration = RELAY_DURATION_MIN
                trigger_unix = utime.mktime((y, m, d, h, minute, 0, 0, 0))
                if trigger_unix > current_unix:
                    future_events.append((trigger_unix, duration))

            # Check next Nth day trigger
            base_unix = utime.mktime((BASE_TRIGGER["year"], BASE_TRIGGER["month"], BASE_TRIGGER["day"],
                                      BASE_TRIGGER["hour"], BASE_TRIGGER["minute"], 0, 0, 0))
            days_ahead = 0
            while True:
                nth_unix = base_unix + days_ahead * INTERVAL_DAYS * 86400
                if nth_unix > current_unix:
                    future_events.append((nth_unix, RELAY_DURATION_MIN))
                    break
                days_ahead += 1

            if future_events:
                next_event = min(future_events, key=lambda x: x[0])
                dt = utime.localtime(next_event[0])
                details = next_event[1]

                if details[0] == "IntervalTrigger":
                    sp.send(" Next interval: {:04d}-{:02d}-{:02d} {:02d}:{:02d} (Duration: {} min)".format(
                        dt[0], dt[1], dt[2], details[1], details[2], details[3]))
                else:
                    y, m, d, h, minute, duration = details
                    sp.send(" Next scheduled: {:04d}-{:02d}-{:02d} {:02d}:{:02d} (Duration: {} min)".format(
                        y, m, d, h, minute, duration))
            else:
                sp.send(" No upcoming triggers found")
                

# Globals to manage file transfer
receiving_file = False
file_lines = []
MAX_LOG_LINES = 50

# Globals for file upload
uploading_file = False
upload_lines = []
upload_filename = None

manual_override = False

def on_rx(msg):
    global receiving_file, file_lines, uploading_file, upload_lines, upload_filename, manual_override, relay_is_on, relay_off_time, active_duration_sec
    global settime_buffer, pending_date, pending_time
    decoded_msg = msg.decode().strip()
    print("RX received:", decoded_msg)

    # --- main.py Upload Mode ---
    if uploading_file:
        if decoded_msg == "ENDUPLOAD":
            try:
                # Write the file
                with open(upload_filename, "w") as f:
                    for line in upload_lines:
                        if line:  # Skip empty lines
                            f.write(line + "\n")
                
                sp.send(f" File '{upload_filename}' uploaded and saved.")
                
                # If this is main.py, schedule a reset after a short delay
                if upload_filename == "main.py":
                    sp.send(" Restarting in 1 second to load new main.py...")
                    time.sleep(1)  # Give time for the message to be sent
                    import machine
                    machine.reset()
                
            except Exception as e:
                sp.send(f" Failed to write file: {e}")
            finally:
                # Always clean up, even if there was an error
                uploading_file = False
                upload_lines = []
                upload_filename = None
            return
                
        # Handle multi-line chunks - split by newlines and add each line
        chunk_lines = [line for line in decoded_msg.split('\n') if line.strip()]
        if chunk_lines:  # Only process if we have non-empty lines
            upload_lines.extend(chunk_lines)
            # Send progress update every 25 lines to reduce verbosity
            if len(upload_lines) % 25 == 0:
                sp.send(f"Upload progress: {len(upload_lines)} lines received")
        return

    # --- Schedule File Transfer Mode ---
    if receiving_file:
        if decoded_msg == "ENDFILE":
            receiving_file = False
            try:
                with open("schedule.txt", "w") as f:
                    for line in file_lines:
                        f.write(line + "\n")
                sp.send(" Schedule file saved")
                schedule = load_schedule("schedule.txt")
                sp.send(" Schedule reloaded")
                time.sleep(1)  # Give time for file system to sync
                sp.send(" Current Schedule: " + read_schedule())
                import machine # Reset to apply new schedule
                print(" Restarting to apply new schedule...")
                machine.reset()
            except Exception as e:
                sp.send(f" Failed to write schedule file: {e}")
            file_lines = []
        else:
            file_lines.append(decoded_msg)
            sp.send("Line received")
        return

    # --- Command Mode ---
    if decoded_msg.startswith("BEGINUPLOAD:"):
        uploading_file = True
        upload_lines = []
        upload_filename = decoded_msg.split(":", 1)[1].strip() or "main.py"
        sp.send(f" Upload mode started for '{upload_filename}' — send lines then ENDUPLOAD")
        return

    if decoded_msg == "BEGINFILE":
        receiving_file = True
        file_lines = []
        sp.send(" Schedule file mode started — send lines then ENDFILE")
        return
    
    if decoded_msg == "CLOSE_RELAY":
        relay_off()
        relay_is_on = False
        relay_off_time = rtc.datetime()  # Optionally log the time
        sp.send(" Relay closed by user command")
        return

    elif decoded_msg == "READFILE":
        try:
            with open("schedule.txt", "r") as f:
                lines = f.readlines()
                if lines:
                    for line in lines:
                        sp.send("[FILE] " + line.strip())
                else:
                    sp.send(" Schedule file is empty")
        except Exception as e:
            sp.send(" Failed to read schedule file")
        return


    elif decoded_msg.startswith("ADD:"):
        try:
            parts = decoded_msg[4:].split(" ")
            y, m, d = map(int, parts[0].split("-"))
            h, minute = map(int, parts[1].split(":"))
            duration = int(parts[2]) if len(parts) > 2 else RELAY_DURATION_MIN
            new_event = (y, m, d, h, minute, duration)

            if new_event not in SCHEDULED_EVENTS:
                SCHEDULED_EVENTS.append(new_event)
                sp.send("Event added: {} {:02d}:{:02d} Duration: {} min".format(parts[0], h, minute, duration))
            else:
                sp.send("Duplicate event ignored: {} {:02d}:{:02d}".format(parts[0], h, minute))
        except Exception as e:
            sp.send("Invalid ADD format. Use ADD:YYYY-MM-DD HH:MM [DURATION]")

    elif decoded_msg.startswith("DURATION:"):
        try:
            new_duration = int(decoded_msg.split(":")[1])
            globals()["RELAY_DURATION_MIN"] = new_duration
            sp.send("Duration updated to: {} min".format(new_duration))
            print(" Duration updated to:", new_duration)
        except Exception as e:
            print(" Error parsing DURATION:", e)
            sp.send("Invalid DURATION format. Use DURATION:X")

    elif decoded_msg == "GETLOG":
        try:
            with open("relay_log.txt", "r") as f:
                lines = f.readlines()[-MAX_LOG_LINES:]
                if lines:
                    for line in lines:
                        sp.send("[LOG] " + line.strip())
                else:
                    sp.send("[LOG] No log entries found")
        except OSError:
            sp.send("[LOG] Failed to read log file")
            
    elif decoded_msg == "CLEAR_LOG":
        try:
            # Clear the log file by opening it in write mode with empty content
            with open("relay_log.txt", "w") as f:
                f.write("")
            sp.send(" Log file cleared successfully")
            print(" Log file cleared by user command")
        except Exception as e:
            sp.send(" Failed to clear log file: {}".format(str(e)))
            print(" Error clearing log file:", e)
        
    elif decoded_msg == "READ_SCHEDULE":
        try:
            with open("schedule.txt", "r") as f:
                lines = f.readlines()
                if lines:
                    sp.send(" Current Schedule:")
                    for line in lines:
                        sp.send("[SCHEDULE] " + line.strip())
                else:
                    sp.send(" No scheduled events found")
        except Exception as e:
            sp.send(" Failed to read schedule file: {}".format(str(e)))
            
    elif decoded_msg.startswith("SETTIME"):
        try:
            # Accepts: "SETTIME YYYY-MM-DD HH:MM[:SS]" or ISO "SETTIME YYYY-MM-DDTHH:MM[:SS]"
            # Handle fragmented BLE writes: accumulate until we have full date and time
            incoming = decoded_msg.strip()
            global settime_buffer
            if settime_buffer:
                incoming = (settime_buffer + " " + incoming).strip()
                settime_buffer = ""

            parts = incoming.split(None, 1)  # split on any whitespace once
            if len(parts) < 2:
                # Not enough yet; wait for next chunk
                settime_buffer = incoming
                return

            rest = parts[1].strip().replace('T', ' ')
            tokens = [t for t in rest.split() if t]
            # If we don't yet have both tokens or time lacks ':', buffer and wait
            if len(tokens) < 2 or (":" not in tokens[1]):
                settime_buffer = incoming
                sp.send(" Waiting for more time data...")
                return

            date_str = tokens[0]
            # Sanitize time string: keep only digits and ':'; drop trailing 'Z' or other chars
            raw_time = tokens[1].rstrip('Z')
            time_str = ''.join([c for c in raw_time if ('0' <= c <= '9') or c == ':' ])
            y, m, d = map(int, date_str.split('-'))

            tparts = time_str.split(':')
            if len(tparts) < 2:
                raise ValueError("Time must be HH:MM or HH:MM:SS")
            h = int(tparts[0]); minute = int(tparts[1]); sec = int(tparts[2]) if len(tparts) >= 3 else 0

            # Compute weekday Mon=1..Sun=7 using utime
            ts = utime.mktime((y, m, d, h, minute, sec, 0, 0))
            wk_mon0 = utime.localtime(ts)[6]  # 0=Mon..6=Sun in MicroPython
            weekday = (wk_mon0 % 7) + 1       # 1=Mon..7=Sun for DS3231

            rtc.datetime((y, m, d, weekday, h, minute, sec))
            sp.send(" Time updated to {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} (weekday {})".format(
                y, m, d, h, minute, sec, weekday))
            now = rtc.datetime()
            sp.send("Current Time at {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
                now[0], now[1], now[2], now[4], now[5], now[6]))
        except Exception as e:
            # Provide debug context on failure
            try:
                sp.send(" SETTIME parse failed. Received: '" + incoming + "'")
                sp.send(" Parsed rest: '" + rest + "'")
                sp.send(" Tokens: " + str(tokens))
            except Exception:
                pass
            sp.send(" Failed to set time: {}".format(e))
        return

    elif decoded_msg.startswith("SETDATE "):
        try:
            _, date_str = decoded_msg.split(None, 1)
            y, m, d = map(int, date_str.strip().split('-'))
            pending_date = (y, m, d)
            sp.send(" Date received: {:04d}-{:02d}-{:02d}".format(y, m, d))
        except Exception as e:
            sp.send(" Failed to parse SETDATE: {}".format(e))
        return

    elif decoded_msg.startswith("SETCLOCK "):
        try:
            _, time_str = decoded_msg.split(None, 1)
            tparts = time_str.strip().split(':')
            if len(tparts) < 2:
                raise ValueError("Use HH:MM or HH:MM:SS")
            h = int(tparts[0]); minute = int(tparts[1]); sec = int(tparts[2]) if len(tparts) >= 3 else 0
            pending_time = (h, minute, sec)
            sp.send(" Time received: {:02d}:{:02d}:{:02d}".format(h, minute, sec))

            if pending_date is not None:
                y, m, d = pending_date
                # Compute weekday Mon=1..Sun=7 using utime
                ts = utime.mktime((y, m, d, h, minute, sec, 0, 0))
                wk_mon0 = utime.localtime(ts)[6]
                weekday = (wk_mon0 % 7) + 1
                rtc.datetime((y, m, d, weekday, h, minute, sec))
                sp.send(" Time updated to {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d} (weekday {})".format(
                    y, m, d, h, minute, sec, weekday))
                now = rtc.datetime()
                sp.send("Current Time at {:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}".format(
                    now[0], now[1], now[2], now[4], now[5], now[6]))
                pending_date = None
                pending_time = None
            else:
                sp.send(" Waiting for SETDATE...")
        except Exception as e:
            sp.send(" Failed to parse SETCLOCK: {}".format(e))
        return

    elif decoded_msg == "MANUAL_ON":
        manual_override = True
        active_duration_sec = 0
        relay_off_time = None
        relay_on()
        relay_is_on = True
        sp.send(" Relay forced ON (Manual mode). Timers paused.")
        return

    elif decoded_msg == "MANUAL_OFF":
        manual_override = False
        relay_off()
        relay_is_on = False
        relay_off_time = None
        sp.send(" Relay forced OFF (Manual mode disabled). Timers resumed.")
        return

    else:
        sp.send(" Unknown command or unsupported format")

def read_schedule():
    try:
        with open("schedule.txt", "r") as f:
            return f.read()
    except Exception as e:
        return f" Error reading schedule: {e}"

def load_schedule(filename="schedule.txt"):
    schedule = []
    try:
        with open(filename, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                # Example format: "2025-08-03 16:48 60"
                parts = line.split()
                if len(parts) == 3:
                    date_str, time_str, duration_str = parts
                    schedule.append({
                        "date": date_str,
                        "time": time_str,
                        "duration": int(duration_str)
                    })
    except Exception as e:
        print("Failed to load schedule:", e)
    return schedule

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
        print(" Log write failed:", e)

# Register the BLE callback:
sp.on_write(on_rx)

relay_off() # Ensure relay is OFF at startup (active-low relay)
# --- Main Loop ---
loop_counter = 0
ble_status_check_interval = 60  # Check BLE status every 60 loops (60 seconds)
output_interval = 5  # Output status every 5 loops (5 seconds)

while True:
    # Check BLE status periodically
    if loop_counter % ble_status_check_interval == 0:
        if not ble.active():
            print(" BLE inactive, reactivating...")
            ble.active(True)
            time.sleep(0.5)  # Give BLE time to restart
        if not sp.is_connected():
            print(" BLE advertising, waiting for connection...")
    
    current_time = rtc.datetime()
    timestamp = format_time(current_time)
    loop_counter += 1
    
    # Only output status every 5 seconds for readability
    should_output = (loop_counter % output_interval == 0)

    # Manual override mode: keep relay ON indefinitely and skip timers/scheduling
    if manual_override:
        if not relay_is_on:
            relay_on()
            relay_is_on = True
        if should_output:
            sp.send("Relay ON — Manual mode (timers paused)")
            print("Relay ON (Manual) at " + timestamp)
        time.sleep(1)
        continue

    if relay_is_on:
        current_unix = utime.time()
        remaining = relay_off_time - current_unix

        if remaining <= 0:
            relay_off()
            relay_is_on = False
            sp.send("Relay OFF at " + timestamp)
            print("Relay OFF at " + timestamp)
            log_event("Relay OFF", timestamp)
        else:
            elapsed = active_duration_sec - remaining
            mins_remain = remaining // 60
            secs_remain = remaining % 60
            mins_elapsed = elapsed // 60
            secs_elapsed = elapsed % 60

            # Send to BLE and print to console every 5 seconds for readability
            if should_output:
                sp.send("Relay ON — Remaining: {:02d}m {:02d}s | Elapsed: {:02d}m {:02d}s".format(
                    mins_remain, secs_remain, mins_elapsed, secs_elapsed))
                print("Relay ON at " + timestamp + " | Remaining: {:02d}m {:02d}s | Elapsed: {:02d}m {:02d}s".format(
                    mins_remain, secs_remain, mins_elapsed, secs_elapsed))
    else:
        current_triggered = False
        for event in SCHEDULED_EVENTS:
            if len(event) == 6:
                y, m, d, h, minute, duration = event
            else:
                y, m, d, h, minute = event
                duration = RELAY_DURATION_MIN

            if (current_time[0] == y and current_time[1] == m and current_time[2] == d and
                current_time[4] == h and current_time[5] == minute and not current_triggered):
                relay_on()
                relay_is_on = True
                active_duration_sec = duration * 60
                relay_off_time = utime.time() + active_duration_sec
                sp.send("Current Time at " + timestamp)
                sp.send("Relay ON at " + timestamp + " for {} min".format(duration))
                # Print only once when relay actually turns on (not every loop)
                print(" RELAY ACTIVATED: " + timestamp + " for {} min".format(duration))
                log_event("Relay ON", timestamp, duration)
                current_triggered = True

                # Save updated schedule to disk
                try:
                    with open("schedule.txt", "w") as f:
                        for evt in SCHEDULED_EVENTS:
                            if len(evt) == 6:
                                y, m, d, h, minute, dur = evt
                            else:
                                y, m, d, h, minute = evt
                                dur = RELAY_DURATION_MIN
                            f.write(f"{y:04d}-{m:02d}-{d:02d} {h:02d}:{minute:02d} {dur}\n")
                    sp.send(" Schedule saved — {} total events".format(len(SCHEDULED_EVENTS)))
                except Exception as e:
                    sp.send(f" Failed to write schedule file: {e}")

                break

        if not current_triggered and is_nth_day_trigger(current_time):
            duration = RELAY_DURATION_MIN  # or customize it if needed
            relay_on()
            relay_is_on = True
            active_duration_sec = duration * 60
            relay_off_time = utime.time() + active_duration_sec
            sp.send("Current Time at " + timestamp)
            sp.send("Relay ON at " + timestamp + " for {} min".format(duration))
            # Print only once when relay actually turns on (not every loop)
            print(" RELAY ACTIVATED (Nth Day): " + timestamp + " for {} min".format(duration))
            log_event("Relay ON", timestamp, duration)

        if not relay_is_on:
            current_unix = utime.mktime((
                current_time[0], current_time[1], current_time[2],
                current_time[4], current_time[5], current_time[6], 0, 0
            ))
            next_dt, duration = next_valid_trigger(current_unix)
            
            # Send to BLE and print to console every 5 seconds for readability
            if should_output:
                sp.send("Current Time at " + timestamp)
                sp.send("Next scheduled change: {} (Relay Duration: {} min)".format(next_dt, duration))
                print("Current Time at", timestamp)
                print("Next scheduled change:", next_dt, "(Relay Duration: {} min)".format(duration))
        
    # Give BLE time to process connections and advertising
    # Use shorter sleep intervals to ensure BLE responsiveness
    time.sleep(1)  # Sleep 1 second instead of 5
    
    # Additional BLE processing time every few loops
    if loop_counter % 5 == 0:
        time.sleep(0.1)  # Extra 100ms for BLE processing
Raspberry Pi Pico W Water Controller — README (water_main.py)

Overview
- Controls an active-low relay (Pin 14) to switch a pump/valve for watering.
- Keeps time with a DS3231 RTC over I2C (SDA=Pin 4, SCL=Pin 5).
- Exposes a Bluetooth LE UART service (device name: mpy-uart) for status, control, scheduling, and file transfer.
- WiFi Access Point mode for file management via web browser (SSID: WaterPico-AP).
- Built-in LED indicates device is running.
- Supports scheduled events loaded from schedule.txt with per-event durations.
- Logs last 50 relay events to relay_log.txt.

Hardware
- Pico W (built-in LED on at startup)
- DS3231 RTC on I2C0: SDA=GP4, SCL=GP5
- Relay on GP14, active-low: relay ON => Pin=0, OFF => Pin=1

Key Files (on Pico)
- main.py: Entry point, typically imports water_main.py
- water_main.py: Main runtime control loop, BLE interaction, schedule handling, WiFi server integration.
- wifi_toggle.py: WiFi file server module (PicoPiFileServer class).
- ds3231.py: DS3231 RTC driver.
- ble_simple_peripheral.py: BLE UART helper.
- ble_advertising.py: BLE advertising helper.
- schedule.txt: Human-editable schedule lines: "YYYY-MM-DD HH:MM DURATION" (minutes).
- relay_log.txt: Last 50 on/off events, most recent at the end.

Configuration (in water_main.py)
- BASE_TRIGGER: Base date/time for interval trigger (default: 2035-08-05 00:00).
- INTERVAL_DAYS: Days between interval triggers (default 30).
- RELAY_DURATION_MIN: Default minutes relay stays ON (default 2; can be overridden per event or via BLE DURATION:).
- MAX_LOG_LINES: Maximum log entries to display (default 100 for queries, 50 stored).
- Output status printed every 5 loops (5 seconds).

BLE Commands
  File Transfer:
  - BEGINUPLOAD:filename - Start file upload (e.g., BEGINUPLOAD:main.py)
  - ENDUPLOAD - Complete file upload (auto-reboots if main.py)
  - BEGINFILE - Start schedule file upload mode
  - ENDFILE - Complete schedule upload and reboot

  Schedule Management:
  - READFILE - View raw schedule.txt contents
  - READ_SCHEDULE - View parsed schedule entries
  - ADD:YYYY-MM-DD HH:MM [DURATION] - Add scheduled event (duration optional, defaults to RELAY_DURATION_MIN)
  - DURATION:X - Set default duration (minutes)
  - NEXTTRIGGER - Show next scheduled trigger time

  Relay Control:
  - MANUAL_ON - Force relay ON (12-hour max timeout, timers paused)
  - MANUAL_OFF - Force relay OFF and resume timers
  - CLOSE_RELAY - Immediate relay off

  Time Setting:
  - SETTIME YYYY-MM-DD HH:MM[:SS] - Set RTC time (single command)
  - SETDATE YYYY-MM-DD - Stage date for two-step time setting
  - SETCLOCK HH:MM[:SS] - Complete time setting (uses staged SETDATE)

  Logging:
  - GETLOG - View relay log (last 100 entries)
  - CLEAR_LOG - Clear relay log file

  WiFi Control:
  - wifi_on - Start WiFi AP (SSID: WaterPico-AP, Password: XXXXXXXX, Port: 5001)
  - wifi_off - Stop WiFi server
  - wifi_status - Check WiFi server status

  System:
  - RESET - Reboot the device

WiFi File Server
When WiFi is enabled (wifi_on command):
- Connect to SSID: WaterPico-AP (password: XXXXXXXX)
- Open browser to http://192.168.4.1:5001
- Features: Upload files, download files, delete files, list directory
- Max upload size: 300KB
- Useful for transferring schedule.txt or updating code files

Manual Mode Behavior
- MANUAL_ON activates relay with 12-hour maximum timeout
- Status shows remaining time: "Manual mode (Xh Ym remaining)"
- Auto-turns off after 12 hours with message "Manual mode auto-timeout (12 hours)"
- MANUAL_OFF cancels manual mode immediately and resumes normal scheduling

RTC Error Handling
- Automatic I2C reinitialization on read errors
- Falls back to system time if RTC completely fails
- Recovers gracefully from OSError/EIO issues

Typical Workflows
1) Set RTC time:
   - Send: SETTIME 2025-08-23 15:40
   - Or two-step: SETDATE 2025-08-23 then SETCLOCK 15:40

2) Upload schedule via BLE:
   - Send: BEGINFILE
   - Send lines: 2025-08-23 16:30 10
   - Send: ENDFILE

3) Upload schedule via WiFi:
   - Send: wifi_on
   - Connect to WaterPico-AP
   - Browse to 192.168.4.1:5001
   - Upload schedule.txt file
   - Send: wifi_off (optional, to save power)

4) Add single event:
   - Send: ADD:2025-08-23 18:00 5

5) Manual control:
   - Send: MANUAL_ON (runs up to 12 hours max)
   - Send: MANUAL_OFF to resume scheduling

6) Check system status:
   - Send: GETLOG to view recent events
   - Send: READ_SCHEDULE to view upcoming events
   - Send: wifi_status to check WiFi state

Troubleshooting
- No schedule.txt: System continues with no scheduled events
- RTC errors: Auto-recovers, check DS3231 wiring on GP4/GP5
- BLE messages may buffer until complete (fragmented writes supported)
- WiFi/BLE contention: WiFi runs in separate thread to minimize interference
- File upload fails: Ensure file < 300KB, check WiFi connection stability
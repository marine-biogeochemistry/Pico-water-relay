Raspberry Pi Pico W Water Controller — README

Overview
- Controls an active-low relay (Pin 14) to switch a pump/valve for watering.
- Keeps time with a DS3231 RTC over I2C (SDA=Pin 4, SCL=Pin 5).
- Exposes a Bluetooth LE UART service (device name: mpy-uart) for status, control, scheduling, and file transfer.
- Supports two trigger types:
  1) Explicit scheduled events loaded from schedule.txt.
  2) An Nth-day interval trigger based on BASE_TRIGGER + INTERVAL_DAYS.
- Logs last 50 relay events to relay_log.txt.

Hardware
- Pico W
- DS3231 RTC on I2C0: SDA=GP4, SCL=GP5
- Relay on GP14, active-low: relay ON => Pin=0, OFF => Pin=1

Key Files
- main.py: Runtime control loop, BLE interaction, schedule handling.
- ds3231.py: RTC driver.
- ble_simple_peripheral.py: BLE UART helper used by main.py.
- schedule.txt: Optional, human-editable schedule lines: "YYYY-MM-DD HH:MM DURATION" (minutes).
- relay_log.txt: Last 50 on/off events, most recent at the end.

Configuration (in main.py)
- BASE_TRIGGER: Base date/time for interval trigger.
- INTERVAL_DAYS: Days between interval triggers (default 30).
- RELAY_DURATION_MIN: Default minutes relay stays ON (default 2; can be overridden per event or via BLE DURATION:).
- CHECK_INTERVAL_SEC: Loop sleep cadence (effectively 1s; status printed every 5 loops).

BLE Commands
- BEGINUPLOAD:filename - Start file upload
- ENDUPLOAD - Complete file upload
- BEGINFILE/ENDFILE - Manage schedule uploads
- READFILE - View schedule.txt
- ADD:YYYY-MM-DD HH:MM [DURATION] - Add schedule event
- DURATION:X - Set default duration (minutes)
- GETLOG - View relay log
- CLEAR_LOG - Clear log file
- READ_SCHEDULE - View parsed schedule
- SETTIME YYYY-MM-DD HH:MM[:SS] - Set RTC time
- SETDATE YYYY-MM-DD - Stage date for SETCLOCK
- SETCLOCK HH:MM[:SS] - Complete time setting
- MANUAL_ON/MANUAL_OFF - Manual relay control
- CLOSE_RELAY - Immediate relay off

Typical Workflows
1) Set RTC time:
   - Send: SETTIME 2025-08-23 15:40

2) Upload schedule:
   - Send: BEGINFILE
   - Send lines: 2025-08-23 16:30 10
   - Send: ENDFILE

3) Add single event:
   - Send: ADD:2025-08-23 18:00 5

4) Manual control:
   - Send: MANUAL_ON
   - Send: MANUAL_OFF to resume scheduling

Troubleshooting
- No schedule.txt: Runs interval-only logic
- Check DS3231 connection on GP4/GP5
- BLE messages may buffer until complete
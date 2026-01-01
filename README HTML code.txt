# Farm Watering System - Web Interface README

## Overview
Web interface for controlling and monitoring a Raspberry Pi Pico W water controller system via BLE (Bluetooth Low Energy). Manages watering schedules, file transfers, logs, and manual relay control.

## Features

### Main Control Tab
- **Scan & Connect**: Find and connect to Pico W via BLE
- **Connection Status**: Real-time connection state display
- **Log Management**:
  - Clear display
  - Read log file from device
  - Save logs locally to PC
  - Clear log file on device

### WiFi Control Tab
- **WiFi Toggle**:
  - Turn WiFi ON/OFF on device
  - Check WiFi status
- **File Operations** (via HTTP at 192.168.4.1:5001):
  - List files on device
  - Download files from device
  - Upload files (max 300KB, with progress %)
  - Delete files from device

### Schedule Tab
- **Schedule Generator**:
  - Set start date/time
  - Configure duration (minutes)
  - Set number of events
  - Set interval between events (days)
  - Auto-generate batch schedule
- **Schedule Management**:
  - Send schedule file to device
  - Read current schedule from device
  - Clear schedule on device
- **Manual Entry**: Enter schedule lines directly in text area

### Manual Control Tab
- **Device Control**:
  - Reboot device
- **Relay Control**:
  - Manual relay ON
  - Manual relay OFF
- **RTC Time Setting**:
  - Set specific date/time
  - Sync with PC time (one-click)

### Help Tab
- Documentation for all features
- Usage instructions
- Troubleshooting tips

### Status Display
- Live "Current Time" display (updates in place)
- Live "Next Schedule" display (updates in place)
- Last update timestamp
- Scrolling message log area

## Usage
1. Open page in Chrome/Edge browser
2. Click "🔍 Scan & Connect"
3. Select your Pico W device ("mpy-uart")
4. Use tabs to access different features

### WiFi File Transfer
1. Connect via BLE first
2. Click "📶 Turn WiFi ON"
3. Connect your PC to "WaterPico-AP" WiFi network (password: 12345678)
4. Use List/Download/Upload/Delete buttons
5. Click "📶 Turn WiFi OFF" when done

## Requirements
- Modern browser (Chrome or Edge recommended)
- Pico W with water controller firmware
- Bluetooth 4.0+ on PC/phone
- For WiFi transfers: Connect to Pico's WiFi AP

## File Format
Schedule entries use format:
```
YYYY-MM-DD HH:MM DURATION
```
Example:
```
2026-01-15 08:00 60
2026-01-22 08:00 60
```

## Troubleshooting
- **BLE won't connect**: Enable Bluetooth, use Chrome/Edge, ensure HTTPS or localhost
- **WiFi upload fails**: Check connected to Pico WiFi, file under 300KB
- **No response**: Check Pico W is powered and in range
- **Check browser console** (F12) for detailed error messages
- **Refresh page** if interface becomes unresponsive

## File Structure
- `water_index.html` - Main web interface (can be served locally or from Pico)
- `manifest.json` - PWA web app configuration
- `service-worker.js` - Offline support for PWA

## Technical Notes
- Uses Web Bluetooth API (BLE UART service)
- WiFi server runs on 192.168.4.1:5001
- Nordic UART Service UUID: 6e400001-b5a3-f393-e0a9-e50e24dcca9e
- Times displayed in local timezone
- Schedules persist on Pico W flash storage
- Upload limit: 300KB per file
- Status messages update in-place (don't flood the log)
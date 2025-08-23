# Water Controller Web Interface - README

## Overview
Web interface for controlling and monitoring a Raspberry Pi Pico W water controller system via BLE. Manages watering schedules, logs, and manual relay control.

## Features
### Connection
- **Scan & Connect**: Find and connect to Pico W
- **Status**: Real-time connection state
- **Device Info**: Shows connected device

### System
- **Log Management**:
  - View logs
  - Save logs locally
  - Clear display/logs

### Scheduling
- **Generator**:
  - Set start date/time
  - Configure duration
  - Multiple events with intervals
  - Batch generation

### Controls
- Sync device time
- Manual relay ON/OFF

## Usage
1. Click "🔍 Scan & Connect"
2. Select your Pico W
3. Use controls to manage system

## Requirements
- Modern browser (Chrome/Edge recommended)
- Pico W with water controller firmware
- Bluetooth 4.0+

## Troubleshooting
- Enable Bluetooth
- Ensure Pico W is powered/range
- Check browser console (F12)
- Refresh if unresponsive

## File Structure
- [index.html] - Main interface
- [manifest.json] - Web app config
- [service-worker.js] - Offline support

## Notes
- Uses BLE UART service
- Times in local timezone
- Schedules persist on Pico W
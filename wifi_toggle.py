# pico_pi_wifi.py (MicroPython)
import network
import socket
import time
import os
from machine import Pin


class PicoPiFileServer:
    def __init__(self, ssid="PicoPi-AP", password="12345678", port=5001, button_pin=0):
        # Store config so this class can be reused when imported
        self.ssid = ssid
        self.password = password
        self.port = port

        self.ap = network.WLAN(network.AP_IF)
        try:
            self.button = Pin(button_pin, Pin.IN, Pin.PULL_UP)
        except Exception:
            # Button is optional; ignore if pin not available
            self.button = None

        self.file_path = "data_test.txt"
        self.server_socket = None
        self._running = False
        # Cooperative cancel flag (can be set by button or future commands)
        self.cancel_requested = False
        print("File server initialized")

    def _check_cancel(self):
        """Return True if a cancel has been requested (button or flag)."""
        if self.cancel_requested:
            return True
        # If a button was configured, a low value indicates pressed (typical onboard button)
        if self.button is not None:
            try:
                if not self.button.value():
                    print("Cancel button pressed")
                    self.cancel_requested = True
                    return True
            except Exception:
                # Ignore button read errors
                pass
        return False

    def start_ap(self):
        self.ap.active(True)
        # Use configured SSID/password instead of hardcoded values
        try:
            self.ap.config(essid=self.ssid, password=self.password)
        except Exception:
            # Some ports may require authmode or omit password for open AP
            self.ap.config(essid=self.ssid)
        print("Access Point started")
        print("Network config:", self.ap.ifconfig())

    def handle_client(self, conn):
        try:
            # Buffer to hold any bytes read beyond a command line (e.g., size header or initial file data)
            pending = b""
            while True:
                print("Waiting for command from PC...")
                # Read a single line-terminated command (ends with \n). Also detect HTTP early.
                line = None
                while True:
                    # If we already have a full line in pending, split it
                    nl = pending.find(b"\n")
                    if nl != -1:
                        line = pending[:nl]
                        # Trim optional CR
                        if line.endswith(b"\r"):
                            line = line[:-1]
                        pending = pending[nl+1:]
                        break
                    # Need more bytes
                    chunk = conn.recv(512)
                    if not chunk:
                        print("Client disconnected.")
                        return
                    # Early HTTP detection
                    if not pending and (chunk.startswith(b"GET ") or chunk.startswith(b"POST ") or chunk.startswith(b"DELETE ") or chunk.startswith(b"OPTIONS ")):
                        # Hand off to HTTP handler with everything we've seen
                        self.handle_http_session(conn, first_chunk=chunk)
                        return
                    pending += chunk

                # Decode command line preserving case for filename; use lower() only for keyword checks
                # But first, if this line is actually an HTTP request line, hand off to HTTP handler.
                if line.startswith(b"GET ") or line.startswith(b"POST ") or line.startswith(b"DELETE ") or line.startswith(b"OPTIONS "):
                    # Reconstruct the initial bytes we already consumed: the request line + newline + any pending
                    try:
                        first = line + b"\n" + pending
                    except Exception:
                        first = line + b"\n"
                    self.handle_http_session(conn, first_chunk=first)
                    return

                try:
                    cmd_raw = line.decode()
                except Exception:
                    cmd_raw = str(line)
                cmd = cmd_raw.strip()
                cmd_l = cmd.lower()
                print(f"Received command: {cmd}")

                if cmd_l == "send":
                    self.send_file(conn)

                elif cmd_l == "exit":
                    print("Exit command received. Closing connection.")
                    break

                elif cmd_l == "cancel":
                    # Allow client to set a cancel flag (useful when sending a file to client)
                    self.cancel_requested = True
                    try:
                        conn.send(b"Cancel acknowledged")
                    except Exception:
                        pass
                    print("Cancel flag set by client")

                elif cmd_l == "list":
                    try:
                        # Enhanced dual-storage file listing
                        file_list_lines = []
                        
                        # List internal flash files
                        try:
                            internal_files = os.listdir('/')
                            if internal_files:
                                file_list_lines.append("=== Internal Flash ===")
                                for file in sorted(internal_files):
                                    if file != "sd":  # Skip SD mount point
                                        try:
                                            size = os.stat(file)[6]
                                            file_list_lines.append(f"{file} ({size} bytes)")
                                        except:
                                            file_list_lines.append(file)
                        except Exception as e:
                            file_list_lines.append(f"Internal flash error: {e}")
                        
                        # List SD card files if mounted
                        try:
                            sd_files = os.listdir('/sd')
                            if sd_files:
                                file_list_lines.append("=== SD Card ===")
                                for file in sorted(sd_files):
                                    try:
                                        size = os.stat(f'/sd/{file}')[6]
                                        file_list_lines.append(f"sd:{file} ({size} bytes)")
                                    except:
                                        file_list_lines.append(f"sd:{file}")
                        except OSError:
                            file_list_lines.append("=== SD Card: Not mounted ===")
                        except Exception as e:
                            file_list_lines.append(f"SD card error: {e}")
                        
                        file_list = "\n".join(file_list_lines)
                        conn.send(file_list.encode())
                        print("Sent enhanced dual-storage file list")
                    except Exception as e:
                        conn.send(f"Error listing files: {e}".encode())

                elif cmd_l in ("space", "df"):
                    # Report free space for internal flash and SD (if mounted)
                    try:
                        def _free_bytes(path):
                            try:
                                sv = os.statvfs(path)
                                bsize = sv[0]
                                bavail = sv[4] if len(sv) > 4 else sv[3]
                                return bsize * bavail
                            except Exception as e:
                                return None

                        internal_free = _free_bytes('/')
                        sd_free = _free_bytes('/sd')
                        lines = []
                        if internal_free is None:
                            lines.append("Internal: statvfs not available or error")
                        else:
                            lines.append(f"Internal free: {internal_free} bytes ({internal_free//1024} KiB)")
                        if sd_free is None:
                            lines.append("SD: not mounted or statvfs error")
                        else:
                            lines.append(f"SD free: {sd_free} bytes ({sd_free//1024} KiB)")
                        resp = "\n".join(lines)
                        conn.send(resp.encode())
                    except Exception as e:
                        try:
                            conn.send(f"Error reporting space: {e}".encode())
                        except Exception:
                            pass

                elif cmd_l.startswith("get ") or cmd_l.startswith("download "):
                    if cmd_l.startswith("get "):
                        filename = cmd[4:].strip()
                    else:
                        filename = cmd[9:].strip()
                    
                    # Enhanced file location detection - support location prefixes
                    target_location = None
                    actual_filename = filename
                    full_path = None
                    found_location = None
                    
                    # Check for location prefix (e.g., "sd:filename" or "internal:filename")
                    if ':' in filename:
                        parts = filename.split(':', 1)
                        if len(parts) == 2:
                            location_part = parts[0].lower().strip()
                            if location_part in ['sd', 'internal', 'flash']:
                                target_location = 'sd' if location_part == 'sd' else 'internal'
                                actual_filename = parts[1].strip()
                    
                    # Smart file location detection
                    if target_location:
                        # User specified a location - try that location only
                        if target_location == "sd":
                            try:
                                os.stat(f"/sd/{actual_filename}")
                                full_path = f"/sd/{actual_filename}"
                                found_location = "sd"
                            except OSError:
                                conn.send(f"File '{actual_filename}' not found in SD card storage.".encode())
                                continue
                        elif target_location == "internal":
                            try:
                                os.stat(actual_filename)
                                full_path = actual_filename
                                found_location = "internal"
                            except OSError:
                                conn.send(f"File '{actual_filename}' not found in internal flash storage.".encode())
                                continue
                    else:
                        # No location specified - search both locations
                        # Try internal flash first
                        try:
                            os.stat(actual_filename)
                            full_path = actual_filename
                            found_location = "internal"
                        except OSError:
                            # Try SD card
                            try:
                                os.stat(f"/sd/{actual_filename}")
                                full_path = f"/sd/{actual_filename}"
                                found_location = "sd"
                            except OSError:
                                conn.send(f"File '{actual_filename}' not found in any storage location.".encode())
                                continue
                    
                    if full_path:
                        self.file_path = full_path
                        print(f"File found in {found_location} storage: {full_path}")
                        # Clear any previous cancel before starting a new transfer
                        self.cancel_requested = False
                        self.send_file(conn)
                        # After sending the file, try to receive a short ACK from the client
                        # This is more reliable than a blind sleep because it ensures the
                        # client actually received the data before we proceed.
                        try:
                            print("Waiting for client ACK (RECV_OK)...")
                            # Give the client up to 12 seconds to send a short acknowledgement
                            # Try to disable Nagle's algorithm to reduce small-packet delays if supported
                            try:
                                # socket.TCP_NODELAY may not be available on some MicroPython ports
                                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                                print("TCP_NODELAY enabled for connection")
                            except Exception:
                                # Some ports provide no TCP_NODELAY; ignore if not available
                                pass
                            conn.settimeout(12.0)
                            try:
                                ack = conn.recv(64)
                                if ack:
                                    try:
                                        ack_s = ack.decode(errors='ignore')
                                    except Exception:
                                        ack_s = str(ack)
                                    if "RECV_OK" in ack_s:
                                        print("Received client ACK: RECV_OK")
                                    else:
                                        print("Received client data after send (not RECV_OK):", ack_s)
                                else:
                                    print("No ACK data received from client")
                            except Exception as e:
                                # recv can raise timeout or other socket errors
                                print("No ACK from client (recv error/timeout):", e)
                        except Exception:
                            # If anything goes wrong with setting timeout/recv, just continue
                            pass
                        finally:
                            # Restore a short timeout for command reads so the server doesn't block forever
                            try:
                                conn.settimeout(None)
                            except Exception:
                                pass
                        print("TCP transmission/ACK handling complete")
                    else:
                        conn.send(f"File '{filename}' not found.".encode())

                elif cmd_l.startswith("time "):
                    try:
                        from machine import RTC
                        rtc = RTC()
                        parts = cmd[5:].strip().split()
                        date_parts = [int(p) for p in parts[0].split("-")]
                        time_parts = [int(p) for p in parts[1].split(":")]
                        rtc.datetime((date_parts[0], date_parts[1], date_parts[2], 0,
                          time_parts[0], time_parts[1], time_parts[2], 0))
                        conn.send(b"Time set successfully")
                        print("RTC updated")
                    except Exception as e:
                        conn.send(f"Error setting time: {e}".encode())

                elif cmd_l.startswith("upload "):
                    import gc
                    filename = cmd[7:].strip()
                    
                    # Enhanced upload location detection - support location prefixes
                    target_location = None
                    actual_filename = filename
                    full_path = filename  # Default to internal flash
                    
                    # Check for location prefix (e.g., "sd:filename" or "internal:filename")
                    if ':' in filename:
                        parts = filename.split(':', 1)
                        if len(parts) == 2:
                            location_part = parts[0].lower().strip()
                            if location_part in ['sd', 'internal', 'flash']:
                                target_location = 'sd' if location_part == 'sd' else 'internal'
                                actual_filename = parts[1].strip()
                                
                                if target_location == "sd":
                                    # Check if SD card is mounted
                                    try:
                                        os.listdir('/sd')
                                        full_path = f"/sd/{actual_filename}"
                                        print(f"Upload target: SD card ({full_path})")
                                    except OSError:
                                        conn.send(b"Error: SD card not mounted")
                                        continue
                                else:
                                    full_path = actual_filename
                                    print(f"Upload target: Internal flash ({full_path})")
                    
                    print(f"Upload request: {filename} -> {full_path}")
                    filename = full_path  # Update filename to use full path
                    try:
                        conn.settimeout(10)  # Prevent hanging
                        # Read exactly 10-byte size header, using any pending bytes first
                        needed = 10
                        header = b""
                        if len(pending) >= needed:
                            header = pending[:needed]
                            pending = pending[needed:]
                        else:
                            header = pending
                            pending = b""
                            while len(header) < needed:
                                part = conn.recv(needed - len(header))
                                if not part:
                                    raise Exception("connection closed while reading header")
                                header += part
                        try:
                            file_size = int(header.decode())
                        except Exception as e:
                            raise Exception("invalid size header: %r" % header)
                        print(f"Expecting {file_size} bytes for '{filename}'")

                        # Check available disk space for target directory (if possible)
                        try:
                            dirpath = '/' if filename == filename.split('/')[-1] else os.path.dirname(filename) or '/'
                        except Exception:
                            dirpath = '/'
                        free_bytes = None
                        try:
                            sv = os.statvfs(dirpath)
                            # MicroPython statvfs: (bsize, frsize, blocks, bfree, bavail, files, ffree)
                            bsize = sv[0]
                            # bfree: total free blocks, bavail: available to unprivileged users (may differ)
                            bfree = sv[3] if len(sv) > 3 else 0
                            bavail = sv[4] if len(sv) > 4 else bfree
                            # Use the larger of bfree and bavail to avoid platform differences
                            blocks_available = max(bfree, bavail)
                            free_bytes = bsize * blocks_available
                            # Debug output to help troubleshooting SD vs internal free space
                            try:
                                print(f"statvfs({dirpath}) = {sv}, bsize={bsize}, bfree={bfree}, bavail={bavail}, free_bytes={free_bytes}")
                            except Exception:
                                pass
                        except Exception as e:
                            free_bytes = None
                            try:
                                print(f"statvfs error for {dirpath}: {e}")
                            except Exception:
                                pass

                        if free_bytes is not None and free_bytes < file_size:
                            try:
                                msg = f"ERROR No space: need {file_size} bytes, free {free_bytes} bytes"
                                conn.send(msg.encode() + b"\n")
                            except Exception:
                                pass
                            print(msg)
                            # Close connection to stop client sending payload
                            try:
                                conn.close()
                            except Exception:
                                pass
                            continue
                        else:
                            # Inform client we're ready to receive (handshake)
                            try:
                                conn.send(b"READY")
                            except Exception:
                                pass

                        received = 0
                        # Compute CRC32 as we write
                        try:
                            import uhashlib as hashlib
                        except Exception:
                            hashlib = None
                        crc = 0
                        # Clear cancel flag at start of operation
                        self.cancel_requested = False
                        # Set a short recv timeout so we can check for idle timeouts periodically
                        try:
                            conn.settimeout(5)
                        except Exception:
                            pass

                        # Idle timeout: allow longer transfers for big files
                        try:
                            idle_timeout = max(15, file_size // 50000)
                        except Exception:
                            idle_timeout = 30
                        last_activity = time.time()

                        with open(filename, 'wb') as f:
                            # If we already have some file data in pending, write it first
                            if pending:
                                to_write = pending[:file_size]
                                f.write(to_write)
                                received += len(to_write)
                                if hashlib is None:
                                    try:
                                        import binascii
                                        crc = binascii.crc32(to_write, crc)
                                    except Exception:
                                        pass
                                else:
                                    try:
                                        crc = (crc ^ 0xFFFFFFFF)
                                        h = hashlib.crc32()
                                        h.update(to_write)
                                        crc = (crc ^ 0xFFFFFFFF)  # Keep simple if uhashlib lacks crc32
                                    except Exception:
                                        pass
                                pending = pending[len(to_write):]
                            while received < file_size:
                                try:
                                    chunk = conn.recv(min(1024, file_size - received))
                                    if not chunk:
                                        # No data - check idle timeout
                                        if time.time() - last_activity > idle_timeout:
                                            msg = f"ERROR Upload timeout after {idle_timeout}s at {received}/{file_size} bytes"
                                            try:
                                                conn.send(msg.encode() + b"\n")
                                            except Exception:
                                                pass
                                            print(msg)
                                            # Delete partial file to avoid leaving incomplete uploads
                                            try:
                                                f.close()
                                            except Exception:
                                                pass
                                            try:
                                                os.remove(filename)
                                            except Exception:
                                                pass
                                            # Close connection to stop further client traffic
                                            try:
                                                conn.close()
                                            except Exception:
                                                pass
                                            break
                                        # else just continue waiting
                                        continue
                                    # Reset last activity time on receiving data
                                    last_activity = time.time()

                                    # Before writing, optionally re-check free space periodically
                                    if free_bytes is not None and (file_size - received) > free_bytes:
                                        msg = f"ERROR No space during upload: need additional {file_size - received} bytes, free {free_bytes} bytes"
                                        try:
                                            conn.send(msg.encode() + b"\n")
                                        except Exception:
                                            pass
                                        print(msg)
                                        # Delete partial file
                                        try:
                                            f.close()
                                        except Exception:
                                            pass
                                        try:
                                            os.remove(filename)
                                        except Exception:
                                            pass
                                        # Close connection to stop further client traffic
                                        try:
                                            conn.close()
                                        except Exception:
                                            pass
                                        break

                                    f.write(chunk)
                                    received += len(chunk)
                                    # update CRC
                                    try:
                                        import binascii
                                        crc = binascii.crc32(chunk, crc)
                                    except Exception:
                                        pass

                                    if received % 102400 == 0:
                                        f.flush()
                                        gc.collect()
                                        print(f"Received {received}/{file_size} bytes...")

                                    # Cooperative cancel (button or flag)
                                    if self._check_cancel():
                                        print("Upload canceled by user")
                                        break
                                except OSError as e:
                                    print("Socket timeout or error:", e)
                                    break

                        if self.cancel_requested:
                            try:
                                conn.send(b"Upload canceled")
                            except Exception:
                                pass
                            print(f"Upload canceled at {received}/{file_size} bytes")
                        elif received == file_size:
                            # Normalize CRC to unsigned 32-bit and hex
                            try:
                                crc_hex = (crc & 0xFFFFFFFF)
                                crc_hex = "%08X" % crc_hex
                            except Exception:
                                crc_hex = "00000000"
                            conn.send(f"OK {filename} {received} CRC {crc_hex}".encode())
                            print(f"Upload complete: {received} bytes")
                        else:
                            conn.send(f"Upload incomplete: received {received}/{file_size} bytes.".encode())
                            print(f"Upload incomplete: received {received}/{file_size} bytes")

                    except Exception as e:
                        print("Error during upload:", e)
                        try:
                            conn.send(f"Error uploading file: {e}".encode())
                        except Exception:
                            pass

                elif cmd_l.startswith("resume "):
                    import gc
                    filename = cmd[7:].strip()
                    try:
                        # Check how much of the file already exists
                        try:
                            current_size = os.stat(filename)[6]
                        except OSError:
                            current_size = 0

                        conn.send(f"{current_size}".encode())
                        print(f"Resuming upload for '{filename}' from byte {current_size}")

                        # Read 10-byte header for total file size
                        conn.settimeout(10)  # Prevent hanging
                        needed = 10
                        header = b""
                        if len(pending) >= needed:
                            header = pending[:needed]
                            pending = pending[needed:]
                        else:
                            header = pending
                            pending = b""
                            while len(header) < needed:
                                part = conn.recv(needed - len(header))
                                if not part:
                                    raise Exception("connection closed while reading header")
                                header += part
                        try:
                            total_size = int(header.decode())
                        except Exception:
                            raise Exception("invalid size header: %r" % header)
                        print(f"Total file size: {total_size}")

                        received = current_size
                        # Start CRC from existing file content
                        crc = 0
                        try:
                            import binascii
                            # Compute CRC over existing portion in small chunks
                            if current_size > 0:
                                with open(filename, 'rb') as rf:
                                    to_go = current_size
                                    buf = bytearray(1024)
                                    while to_go > 0:
                                        n = rf.readinto(buf)
                                        if not n:
                                            break
                                        crc = binascii.crc32(memoryview(buf)[:n], crc)
                                        to_go -= n
                        except Exception:
                            pass
                        # Clear cancel flag at start of operation
                        self.cancel_requested = False
                        with open(filename, 'ab') as f:
                            # Write any pending payload first
                            if pending and received < total_size:
                                to_write = pending[:(total_size - received)]
                                f.write(to_write)
                                received += len(to_write)
                                try:
                                    import binascii
                                    crc = binascii.crc32(to_write, crc)
                                except Exception:
                                    pass
                                pending = pending[len(to_write):]
                            while received < total_size:
                                try:
                                    chunk = conn.recv(min(1024, total_size - received))
                                    if not chunk:
                                        print("Connection closed or no data received")
                                        break
                                    f.write(chunk)
                                    received += len(chunk)
                                    try:
                                        import binascii
                                        crc = binascii.crc32(chunk, crc)
                                    except Exception:
                                        pass

                                    # Flush and collect garbage periodically
                                    if received % 102400 == 0:
                                        f.flush()
                                        gc.collect()
                                        print(f"Received {received}/{total_size} bytes...")

                                    # Cooperative cancel
                                    if self._check_cancel():
                                        print("Resume canceled by user")
                                        break

                                except OSError as e:
                                    print("Socket timeout or error:", e)
                                    break

                        if self.cancel_requested:
                            try:
                                conn.send(b"Resume canceled")
                            except Exception:
                                pass
                            print(f"Resume canceled at {received}/{total_size} bytes")
                        else:
                            try:
                                crc_hex = (crc & 0xFFFFFFFF)
                                crc_hex = "%08X" % crc_hex
                            except Exception:
                                crc_hex = "00000000"
                            print(f"File '{filename}' resumed and completed ({received} bytes), CRC {crc_hex}")
                            conn.send(f"OK {filename} {received} CRC {crc_hex}".encode())

                    except Exception as e:
                        print("Error during resume:", e)
                        conn.send(f"Error resuming file: {e}".encode())

                else:
                    print("Unknown command.")
                    conn.send(b"Unknown command.")
        except Exception as e:
            print("Error handling client:", e)
        finally:
            conn.close()
            print("Connection closed\n")

    # ---------------- HTTP SERVER (no bridge) -----------------
    def _http_send(self, conn, status_code=200, headers=None, body_bytes=b""):
        reason = {200: "OK", 204: "No Content", 400: "Bad Request", 404: "Not Found", 405: "Method Not Allowed", 500: "Server Error"}.get(status_code, "OK")
        try:
            conn.send(b"HTTP/1.1 %d %s\r\n" % (status_code, reason.encode()))
        except TypeError:
            conn.send("HTTP/1.1 %d %s\r\n" % (status_code, reason))
        # Default headers + CORS
        base = {
            "Connection": "close",
            "Cache-Control": "no-store",
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-Mode, X-Offset",
            # Chrome Private Network Access (PNA) support when page is https and target is private http
            "Access-Control-Allow-Private-Network": "true",
        }
        if headers:
            base.update(headers)
        if body_bytes is not None:
            base.setdefault("Content-Length", str(len(body_bytes)))
        for k, v in base.items():
            try:
                line = b"%s: %s\r\n" % (str(k).encode(), str(v).encode())
            except Exception:
                line = ("%s: %s\r\n" % (k, v))
            conn.send(line)
        conn.send(b"\r\n")
        if body_bytes:
            conn.send(body_bytes)

    def _http_json(self, conn, obj, status_code=200):
        try:
            import ujson as json
        except Exception:
            import json
        body = json.dumps(obj)
        self._http_send(conn, status_code, headers={"Content-Type": "application/json"}, body_bytes=body.encode())

    def _read_until(self, conn, delim=b"\r\n\r\n", max_bytes=4096, first_chunk=None):
        buf = bytearray()
        if first_chunk:
            buf.extend(first_chunk)
            if delim in buf:
                return bytes(buf)
        try:
            conn.settimeout(5)
        except Exception:
            pass
        while len(buf) < max_bytes:
            b = conn.recv(256)
            if not b:
                break
            buf.extend(b)
            if delim in buf:
                break
        return bytes(buf)

    def _parse_request(self, head_bytes):
        try:
            head = head_bytes.decode()
        except Exception:
            head = str(head_bytes)
        parts = head.split("\r\n\r\n", 1)
        header_text = parts[0]
        lines = header_text.split("\r\n")
        if not lines:
            return None
        req_line = lines[0]
        try:
            method, path, _ = req_line.split(" ", 2)
        except ValueError:
            return None
        headers = {}
        for ln in lines[1:]:
            if not ln:
                continue
            kv = ln.split(":", 1)
            if len(kv) == 2:
                headers[kv[0].strip().lower()] = kv[1].strip()
        return method, path, headers

    def _qparam(self, path, key, default=None):
        qpos = path.find("?")
        if qpos == -1:
            return default
        qs = path[qpos+1:]
        for pair in qs.split("&"):
            if not pair:
                continue
            kv = pair.split("=", 1)
            if len(kv) == 2 and kv[0] == key:
                # Basic URL-decoding for spaces and %xx
                val = kv[1].replace("+", " ")
                try:
                    import ubinascii as binascii
                except Exception:
                    import binascii
                i = 0
                out = ""
                while i < len(val):
                    c = val[i]
                    if c == '%' and i+2 < len(val):
                        try:
                            out += chr(int(val[i+1:i+3], 16))
                            i += 3
                            continue
                        except Exception:
                            pass
                    out += c
                    i += 1
                return out
        return default

    def handle_http_session(self, conn, first_chunk=None):
        head = self._read_until(conn, first_chunk=first_chunk)
        req = self._parse_request(head)
        if not req:
            self._http_send(conn, 400)
            return
        method, path, headers = req
        cl = int(headers.get('content-length', '0') or '0')
        # Dispatch
        if method == 'OPTIONS':
            # Preflight OK (no body)
            self._http_send(conn, 204)
            return
        if path.startswith('/api/status'):
            st = self.status()
            d = {"status": "ok"}
            try:
                d.update(st)
            except Exception:
                pass
            self._http_json(conn, d)
            return
        if path.startswith('/api/list') and method == 'GET':
            try:
                # Enhanced dual-storage file listing for HTTP API
                internal_files = []
                sd_files = []
                
                # List internal flash files
                try:
                    if hasattr(os, 'ilistdir'):
                        for ent in os.ilistdir('/'):
                            try:
                                name = ent[0]
                                ftype = ent[1]
                                size = ent[3] if len(ent) > 3 else None
                                if name != "sd" and (ftype == 0x8000 or size is not None):
                                    internal_files.append({
                                        "name": name, 
                                        "size": size if size is not None else 0,
                                        "location": "internal"
                                    })
                            except Exception:
                                pass
                    else:
                        for name in os.listdir('/'):
                            if name != "sd":
                                try:
                                    size = os.stat(name)[6]
                                except Exception:
                                    size = 0
                                internal_files.append({
                                    "name": name, 
                                    "size": size,
                                    "location": "internal"
                                })
                except Exception:
                    pass
                
                # List SD card files if mounted
                try:
                    if hasattr(os, 'ilistdir'):
                        for ent in os.ilistdir('/sd'):
                            try:
                                name = ent[0]
                                ftype = ent[1]
                                size = ent[3] if len(ent) > 3 else None
                                if ftype == 0x8000 or size is not None:
                                    sd_files.append({
                                        "name": f"sd:{name}", 
                                        "size": size if size is not None else 0,
                                        "location": "sd",
                                        "actual_name": name
                                    })
                            except Exception:
                                pass
                    else:
                        for name in os.listdir('/sd'):
                            try:
                                size = os.stat(f'/sd/{name}')[6]
                            except Exception:
                                size = 0
                            sd_files.append({
                                "name": f"sd:{name}", 
                                "size": size,
                                "location": "sd",
                                "actual_name": name
                            })
                except OSError:
                    # SD card not mounted
                    pass
                except Exception:
                    pass
                
                # Combine files from both locations
                all_files = internal_files + sd_files
                
                response = {
                    "status": "ok", 
                    "files": all_files,
                    "internal_count": len(internal_files),
                    "sd_count": len(sd_files),
                    "total_count": len(all_files)
                }
                
                self._http_json(conn, response)
            except Exception as e:
                self._http_json(conn, {"status": "error", "message": str(e)}, status_code=500)
            return
        if path.startswith('/api/get') and method == 'GET':
            name = self._qparam(path, 'name')
            if not name:
                self._http_json(conn, {"status": "error", "message": "missing name"}, status_code=400)
                return
            
            # Enhanced file location detection - support location prefixes
            target_location = None
            actual_filename = name
            full_path = None
            found_location = None
            
            # Check for location prefix (e.g., "sd:filename" or "internal:filename")
            if ':' in name:
                parts = name.split(':', 1)
                if len(parts) == 2:
                    location_part = parts[0].lower().strip()
                    if location_part in ['sd', 'internal', 'flash']:
                        target_location = 'sd' if location_part == 'sd' else 'internal'
                        actual_filename = parts[1].strip()
            
            # Smart file location detection
            if target_location:
                # User specified a location - try that location only
                if target_location == "sd":
                    try:
                        os.stat(f"/sd/{actual_filename}")
                        full_path = f"/sd/{actual_filename}"
                        found_location = "sd"
                    except OSError:
                        self._http_json(conn, {"status": "error", "message": f"File '{actual_filename}' not found in SD card storage"}, status_code=404)
                        return
                elif target_location == "internal":
                    try:
                        os.stat(actual_filename)
                        full_path = actual_filename
                        found_location = "internal"
                    except OSError:
                        self._http_json(conn, {"status": "error", "message": f"File '{actual_filename}' not found in internal flash storage"}, status_code=404)
                        return
            else:
                # No location specified - search both locations
                # Try internal flash first
                try:
                    os.stat(actual_filename)
                    full_path = actual_filename
                    found_location = "internal"
                except OSError:
                    # Try SD card
                    try:
                        os.stat(f"/sd/{actual_filename}")
                        full_path = f"/sd/{actual_filename}"
                        found_location = "sd"
                    except OSError:
                        self._http_json(conn, {"status": "error", "message": f"File '{actual_filename}' not found in any storage location"}, status_code=404)
                        return
            
            try:
                st = os.stat(full_path)
                total = st[6] if isinstance(st, tuple) and len(st) > 6 else st[0]
                hdrs = {
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(total),
                    "Content-Disposition": "attachment; filename=%s" % actual_filename,
                    "X-File-Location": found_location,
                    "X-File-Path": full_path,
                }
                self._http_send(conn, 200, headers=hdrs, body_bytes=b"")
                # Stream file
                self.cancel_requested = False
                buf = bytearray(4096)
                with open(full_path, 'rb') as f:
                    while True:
                        n = f.readinto(buf)
                        if not n:
                            break
                        try:
                            conn.send(buf[:n])
                        except Exception:
                            break
                        if self._check_cancel():
                            break
                print(f"HTTP file download complete: {full_path} from {found_location} storage")
            except Exception as e:
                self._http_json(conn, {"status": "error", "message": str(e)}, status_code=404)
            return
        if path.startswith('/api/upload') and method == 'POST':
            name = self._qparam(path, 'name')
            mode = self._qparam(path, 'mode', 'truncate')
            if not name:
                self._http_json(conn, {"status": "error", "message": "missing name"}, status_code=400)
                return
            
            # Enhanced upload location detection - support location prefixes
            target_location = None
            actual_filename = name
            full_path = name  # Default to internal flash
            
            # Check for location prefix (e.g., "sd:filename" or "internal:filename")
            if ':' in name:
                parts = name.split(':', 1)
                if len(parts) == 2:
                    location_part = parts[0].lower().strip()
                    if location_part in ['sd', 'internal', 'flash']:
                        target_location = 'sd' if location_part == 'sd' else 'internal'
                        actual_filename = parts[1].strip()
                        
                        if target_location == "sd":
                            # Check if SD card is mounted
                            try:
                                os.listdir('/sd')
                                full_path = f"/sd/{actual_filename}"
                                print(f"HTTP upload target: SD card ({full_path})")
                            except OSError:
                                self._http_json(conn, {"status": "error", "message": "SD card not mounted"}, status_code=503)
                                return
                        else:
                            full_path = actual_filename
                            print(f"HTTP upload target: Internal flash ({full_path})")
            
            print(f"HTTP upload request: {name} -> {full_path}")
            name = full_path  # Update name to use full path
            try:
                # Prepare file
                fmode = 'wb' if mode != 'append' else 'ab'
                received = 0
                self.cancel_requested = False
                # Initialize CRC32
                try:
                    import ubinascii as binascii
                except Exception:
                    try:
                        import binascii  # type: ignore
                    except Exception:
                        binascii = None
                import gc
                crc = 0
                with open(name, fmode) as f:
                    remaining = cl
                    while remaining > 0:
                        chunk = conn.recv(min(2048, remaining))
                        if not chunk:
                            break
                        f.write(chunk)
                        received += len(chunk)
                        remaining -= len(chunk)
                        if binascii:
                            try:
                                crc = binascii.crc32(chunk, crc)
                            except Exception:
                                pass
                        if received % 102400 == 0:
                            f.flush()
                            gc.collect()
                            print(f"HTTP upload: Received {received}/{cl} bytes...")
                        if self._check_cancel():
                            break
                if self.cancel_requested:
                    self._http_json(conn, {"status": "error", "message": "upload canceled", "received": received}, status_code=500)
                elif received == cl:
                    crc_hex = "%08X" % ((crc & 0xFFFFFFFF)) if binascii else "00000000"
                    self._http_json(conn, {"status": "ok", "message": "uploaded", "bytes": received, "crc32": crc_hex})
                else:
                    self._http_json(conn, {"status": "error", "message": "upload incomplete", "received": received, "expected": cl}, status_code=500)
            except Exception as e:
                self._http_json(conn, {"status": "error", "message": str(e)}, status_code=500)
            return
        if path.startswith('/api/delete') and method == 'DELETE':
            name = self._qparam(path, 'name')
            if not name:
                self._http_json(conn, {"status": "error", "message": "missing name"}, status_code=400)
                return
            
            # Enhanced delete location detection - support location prefixes
            target_location = None
            actual_filename = name
            full_path = name  # Default to internal flash
            
            # Check for location prefix (e.g., "sd:filename" or "internal:filename")
            if ':' in name:
                parts = name.split(':', 1)
                if len(parts) == 2:
                    location_part = parts[0].lower().strip()
                    if location_part in ['sd', 'internal', 'flash']:
                        target_location = 'sd' if location_part == 'sd' else 'internal'
                        actual_filename = parts[1].strip()
                        
                        if target_location == "sd":
                            # Check if SD card is mounted
                            try:
                                os.listdir('/sd')
                                full_path = f"/sd/{actual_filename}"
                                print(f"HTTP delete target: SD card ({full_path})")
                            except OSError:
                                self._http_json(conn, {"status": "error", "message": "SD card not mounted"}, status_code=503)
                                return
                        else:
                            full_path = actual_filename
                            print(f"HTTP delete target: Internal flash ({full_path})")
            
            print(f"HTTP delete request: {name} -> {full_path}")
            try:
                os.remove(full_path)
                self._http_json(conn, {"status": "ok", "message": "deleted", "file": name})
            except OSError as e:
                self._http_json(conn, {"status": "error", "message": f"delete failed: {e}"}, status_code=404)
            return
        # Fallback
        self._http_send(conn, 404)

    def start_server(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(('', self.port))
        s.listen(1)
        self.server_socket = s
        print(f"TCP Server listening on port {self.port}")
        return s

    def send_file(self, conn):
        try:
            st = os.stat(self.file_path)
            file_size = st[6] if isinstance(st, tuple) and len(st) > 6 else st[0]
            if file_size == 0:
                print("File is empty")
                return False

            # Send 10-byte file size header
            header = ("{:010d}".format(file_size))
            conn.send(header.encode())

            print(f"Sending file ({file_size} bytes)...")
            # Clear cancel flag at start of operation
            self.cancel_requested = False
            
            # Set appropriate timeout based on file size
            try:
                if file_size > 100000:  # Files larger than 100KB
                    send_timeout = max(30, file_size // 25000)  # At least 30s, or ~25KB/s minimum rate
                    print(f"Large file detected, setting send timeout to {send_timeout}s")
                    conn.settimeout(send_timeout)
                else:
                    conn.settimeout(10)  # Standard timeout for smaller files
            except Exception:
                pass
            # Use slightly smaller chunks and sendall to improve delivery reliability
            buf = bytearray(2048)
            sent_bytes = 0
            next_progress = file_size // 10  # Show progress every 10%
            
            with open(self.file_path, "rb") as f:
                while True:
                    n = f.readinto(buf)
                    if not n:
                        break
                    try:
                        # Use sendall so MicroPython will block until the buffer is accepted
                        try:
                            conn.sendall(buf[:n])
                        except Exception:
                            # Fall back to send() if sendall isn't available on this port
                            conn.send(buf[:n])
                        # Small cooperative delay to avoid overwhelming the network stack
                        try:
                            time.sleep(0.002)
                        except Exception:
                            pass
                        sent_bytes += n
                        
                        # Progress reporting for large files
                        if file_size > 100000 and sent_bytes >= next_progress:
                            percent = (sent_bytes / file_size) * 100
                            print(f"Sent {sent_bytes}/{file_size} bytes ({percent:.1f}%)")
                            next_progress += file_size // 10
                            
                    except OSError as e:
                        print(f"Send error after {sent_bytes}/{file_size} bytes:", e)
                        if hasattr(e, 'errno'):
                            print(f"Error code: {e.errno}")
                        return False
                    # Cooperative cancel
                    if self._check_cancel():
                        print("Send canceled by user")
                        return False
            print("File sent successfully")
            return True
        except Exception as e:
            print("Error sending file:", e)
            return False

    def run(self):
        self._running = True
        self.start_ap()

        # Create test file if it doesn't exist
        try:
            os.stat(self.file_path)
        except OSError:
            with open(self.file_path, 'w') as f:
                f.write("Hello, this is a test file from PICO\n")

        # Start and retain server socket
        if not self.server_socket:
            self.start_server()

        while self._running:
            try:
                print("Waiting for PC to connect...")
                conn, addr = self.server_socket.accept()
            except Exception as e:
                # Likely closed during shutdown
                print("Accept failed or server stopped:", e)
                break
            print("Client connected from", addr)
            self.handle_client(conn)

    def status(self):
        try:
            ip = None
            try:
                ip = self.ap.ifconfig()[0]
            except Exception:
                pass
            return {
                "ssid": self.ssid,
                "port": self.port,
                "ap_active": bool(self.ap.active()) if self.ap else False,
                "ip": ip,
                "listening": self.server_socket is not None,
            }
        except Exception as e:
            return {"error": str(e)}


    def shutdown(self):
        import micropython, time
        print("Shutting down network services...")
        try:
            micropython.kbd_intr(-1)  # best-effort ignore Ctrl‑C during cleanup
        except Exception:
            pass
        try:
            self._running = False
            if getattr(self, 'server_socket', None):
                try:
                    self.server_socket.settimeout(0)
                except Exception:
                    pass
                try:
                    self.server_socket.close()
                    print("Server socket closed")
                except Exception as e:
                    print("Error closing socket:", e)
                finally:
                    self.server_socket = None

            if getattr(self, 'ap', None):
                try:
                    self.ap.active(False)
                    print("Wi‑Fi AP disabled")
                except Exception as e:
                    print("Error shutting down Wi‑Fi:", e)

            time.sleep_ms(150)
        finally:
            try:
                micropython.kbd_intr(3)
            except Exception:
                pass



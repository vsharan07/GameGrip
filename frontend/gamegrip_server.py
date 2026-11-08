"""GameGrip bridge: links the website (gamegrip/index.html) to dtw_detector.py.

Run:  python gamegrip_server.py      (opens the site in your browser)

What it does:
  * Serves the gamegrip/ folder (the page, song.mp3) on http://127.0.0.1:8765
  * Serves the sports demo (Demos/) on http://127.0.0.1:8765/demos/ . Once
    calibration is done, the demo picks up every "SWING DETECTED!" from the
    same event stream and uses it as that player's swing.
  * When you pick a game on the page, it opens the ESP32 serial port (the same
    PORT/BAUD as dtw_detector.py) and checks that motion data is coming in.
    If the ESP32 can't be found, the page shows an error.
  * At HOLD STILL, Minecraft records still/right/down and saves the host's
    mouse-look mapping. LAUNCH MINECRAFT enables desktop control after a
    Python-timed countdown, using this same serial connection.
  * DEMOS runs dtw_detector.calibrate()/detect(), unchanged.
  * Everything dtw_detector prints still goes to this terminal. Each line is
    also turned into an event and streamed to the page (Server-Sent Events),
    so the countdowns, recording windows, retries, "CALIBRATED!" and every
    "SWING DETECTED!" show up on the website at the same moment.

dtw_detector.py is imported, not modified.
"""

import argparse
import json
import os
import queue
import re
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import serial

import dtw_detector as dtw
from minecraft_controller import LookCalibrationError, MinecraftController

HOST = "127.0.0.1"
HTTP_PORT = 8765
ROOT = os.path.dirname(os.path.abspath(__file__))
SITE_DIR = os.path.join(ROOT, "gamegrip")
DEMOS_DIR = os.path.join(ROOT, "Demos")
# Which strap this serial port is. The sports demo maps device -> player.
# Two ESP32s (next week) = two Sessions, one per port, each with its own device id.
DEVICE_ID = "esp1"
# How long to wait for the first data line. If the board does reset on connect,
# imuNumbers.ino spends ~5 s trying WiFi before it streams, so leave headroom.
PROBE_SECONDS = 8.0

ANSI = re.compile(r"\x1b\[[0-9;]*m")


# ---------------------------------------------------------------------------
# Event fan-out to every open page (Server-Sent Events)
# ---------------------------------------------------------------------------
_clients = []
_clients_lock = threading.Lock()


def broadcast(event):
  with _clients_lock:
    for q in list(_clients):
      q.put(event)


# ---------------------------------------------------------------------------
# dtw_detector's terminal output -> page events
# ---------------------------------------------------------------------------
def parse_line(line):
  """Maps one line printed by dtw_detector to a page event (or None)."""
  s = ANSI.sub("", line).strip()
  if not s:
    return None
  if s.startswith("--- Step 1:"):
    return {"type": "step", "kind": "still", "step": 0}
  m = re.match(r"--- Step 2: Calibration swing #(\d+) of (\d+)", s)
  if m:
    return {"type": "step", "kind": "swing", "step": int(m.group(1)),
            "total_steps": int(m.group(2))}
  m = re.fullmatch(r"(\d+)\.\.\.", s)
  if m:
    return {"type": "count", "n": int(m.group(1))}
  if s == ">>> Hold still... <<<":
    return {"type": "record-start", "kind": "still", "ms": int(dtw.REST_SECONDS * 1000)}
  m = re.fullmatch(r">>> SWING #(\d+) NOW! <<<", s)
  if m:
    return {"type": "record-start", "kind": "swing", "step": int(m.group(1)),
            "ms": int(dtw.SWING_WINDOW_SECONDS * 1000)}
  if s.startswith("Baseline captured"):
    return {"type": "record-end", "kind": "still"}
  if s == "Recorded.":
    return {"type": "record-end", "kind": "swing"}
  if s == "Still check OK.":
    return {"type": "still-ok"}
  if s.startswith("Sensor moved during the still step"):
    return {"type": "still-moved", "text": s}
  if s.startswith("Let's try that again"):
    return {"type": "still-retry", "text": s}
  if s.startswith("Continuing anyway"):
    return {"type": "still-continue", "text": s}
  if s.startswith("[Warning]"):
    return {"type": "warning", "text": s[len("[Warning]"):].strip()}
  if s == "CALIBRATED!":
    return {"type": "calibrated"}
  if s.startswith("Listening for swings"):
    return {"type": "listening"}
  m = re.search(r"SWING DETECTED!\s+#(\d+)", s)
  if m:
    n = int(m.group(1))
    # Same colour index dtw_detector uses for the terminal banner.
    return {"type": "swing", "n": n, "colour": n % len(dtw.COLOURS)}
  return None


class TeeStdout:
  """Passes everything through to the real terminal, and turns complete lines
  printed by the active session's worker thread into page events."""

  def __init__(self, real):
    self.real = real
    self._buf = {}
    self._lock = threading.Lock()

  def write(self, text):
    self.real.write(text)
    session = getattr(threading.current_thread(), "gg_session", None)
    if session is None:
      return len(text)
    with self._lock:
      buf = self._buf.get(session.id, "") + text
      *lines, rest = buf.split("\n")
      self._buf[session.id] = rest
    for line in lines:
      ev = parse_line(line)
      if ev:
        session.send(ev)
    return len(text)

  def flush(self):
    self.real.flush()

  def __getattr__(self, name):
    return getattr(self.real, name)


# ---------------------------------------------------------------------------
# One calibration session (one game pick on the page)
# ---------------------------------------------------------------------------
_session = None
_session_lock = threading.Lock()


class Session:
  _next_id = 1

  def __init__(self, game, client_id=None):
    self.id = Session._next_id
    Session._next_id += 1
    self.game = game
    self.client_id = client_id
    self.ser = None
    self.ready = threading.Event()
    self.stopped = False
    self.device = DEVICE_ID
    self.listening = False   # True once dtw_detector.detect() is running
    self.controller = None
    self.connected = False
    self.thread = threading.Thread(target=self._run, daemon=True)
    self.thread.gg_session = self

  def send(self, event):
    if self.stopped:
      return
    if event["type"] == "listening":
      self.listening = True
    elif event["type"] == "error":
      self.listening = False
    broadcast({**event, "sid": self.id, "device": self.device})

  def error(self, title, message):
    print(f"{dtw.YELLOW}[Error] {message}{dtw.RESET}", flush=True)
    self.send({"type": "error", "title": title, "message": message})

  def stop(self):
    self.stopped = True
    self.ready.set()
    if self.controller is not None:
      self.controller.stop()
    if self.ser is not None:
      try:
        self.ser.close()  # makes a blocked read in calibrate()/detect() bail out
      except Exception:
        pass
    self.thread.join(timeout=3)

  def _open(self):
    print(f"Connecting to ESP32 on {dtw.PORT}...", flush=True)
    try:
      self.ser = serial.Serial()
      self.ser.port, self.ser.baudrate, self.ser.timeout = dtw.PORT, dtw.BAUD, 0.05
      # Opening a port normally pulses DTR/RTS, which reboots most ESP32 boards
      # (and the sketch then needs ~5 s for its WiFi attempt). Keep both low so
      # the board keeps streaming.
      self.ser.dtr = False
      self.ser.rts = False
      self.ser.open()
      time.sleep(0.3)
    except Exception as e:
      print(f"Error opening serial port: {e}", flush=True)
      self.send({"type": "error", "title": "ESP32 NOT DETECTED",
                 "message": f"Couldn't open {dtw.PORT}: {e}"})
      return False

    # Make sure the ESP32 is actually streaming ax,ay,az,gx,gy,gz lines.
    self.ser.reset_input_buffer()
    last_text = ""
    deadline = time.time() + PROBE_SECONDS
    while time.time() < deadline and not self.stopped:
      raw = self.ser.readline().decode("utf-8", errors="ignore").strip()
      if dtw.parse_sample_line(raw) is not None:
        return True
      if raw:
        last_text = raw
    if self.stopped:
      return False
    msg = (f"{dtw.PORT} opened, but no 'ax,ay,az,gx,gy,gz' lines arrived in "
           f"{PROBE_SECONDS:g}s. Close the Arduino Serial Monitor and check the "
           f"sketch says 'MPU6050 initialized' (it only streams numbers with a real sensor).")
    if last_text:
      msg += f" Last line from the board: \"{last_text}\""
    self.error("NO MOTION DATA", msg)
    return False

  def _run(self):
    try:
      if not self._open():
        return
      self.connected = True
      print("\n=== MPU6050 Swing Calibrator & Detector ===", flush=True)
      print(f"Game: {self.game}. Waiting for the website to start calibration...",
            flush=True)
      self.send({"type": "connected", "port": dtw.PORT})
      self.ready.wait()
      if self.stopped:
        return
      if self.game == "minecraft":
        self._minecraft()
      else:
        tracker = dtw.calibrate(self.ser)
        dtw.detect(self.ser, tracker)
    except (RuntimeError, LookCalibrationError) as e:
      if not self.stopped:
        # dtw_detector's own errors (no samples, sensor too noisy, ...)
        title = "ESP32 NOT DETECTED" if "samples" in str(e) else "CALIBRATION FAILED"
        self.error(title, str(e))
    except Exception as e:
      if not self.stopped:
        self.error("ESP32 DISCONNECTED", f"Lost the ESP32 on {dtw.PORT}: {e}")
    finally:
      self.connected = False
      self.listening = False
      if self.controller is not None:
        self.controller.stop()
      if self.ser is not None:
        try:
          self.ser.close()
        except Exception:
          pass

  def _minecraft(self):
    self.controller = MinecraftController(self.send)
    if self.stopped:
      return
    # Minecraft learns mouse axes and signs; sports keeps its three-swing
    # rotation detector. Both use the existing, Python-timed website events.
    for attempt in range(3):
      print("--- Step 1: Hold still ---", flush=True)
      dtw.countdown()
      if self.stopped:
        return
      print(">>> Hold still... <<<", flush=True)
      rest, _ = dtw.record(self.ser, dtw.REST_SECONDS)
      print("Baseline captured.", flush=True)
      gyro = rest[:, 3:6]
      noise = float(dtw.np.percentile(dtw.np.linalg.norm(gyro - gyro.mean(axis=0), axis=1), 90))
      if noise <= dtw.REST_MAX_NOISE:
        print("Still check OK.", flush=True)
        break
      if attempt == 2:
        raise RuntimeError("The toy kept moving during HOLD STILL. Put it down and use RUN IT BACK.")
      print("Let's try that again. Hold the toy still.", flush=True)
    recordings = []
    for step, direction in enumerate(("right", "down"), 1):
      print(f"--- Step 2: Calibration swing #{step} of 2 ---", flush=True)
      print(f"Turn {direction} once, then hold that position. Return to centre before the next countdown.", flush=True)
      dtw.countdown()
      if self.stopped:
        return
      print(f">>> SWING #{step} NOW! <<<", flush=True)
      samples, _ = dtw.record(self.ser, dtw.SWING_WINDOW_SECONDS)
      recordings.append(samples)
      print("Recorded.", flush=True)
    document = self.controller.calibrate(rest, *recordings)
    if self.stopped or document is None:
      return
    print(f"Mouse look learned: {document['mount']}", flush=True)
    print("CALIBRATED!", flush=True)
    self.controller.last_sample = time.monotonic()
    self.ser.reset_input_buffer()
    while not self.stopped and not self.controller.closed:
      raw = self.ser.readline().decode("utf-8", errors="ignore")
      self.controller.tick(time.monotonic(), raw)


def start_session(game, client_id=None):
  global _session
  if game not in ("minecraft", "demos"):
    raise ValueError("Choose Minecraft or Demos.")
  with _session_lock:
    if _session is not None:
      _session.stop()
    _session = Session(game, client_id=client_id)
    _session.thread.start()
    return _session.id


def status():
  """What the sports demo asks on load: is a calibrated strap streaming swings?"""
  with _session_lock:
    s = _session
    if s is None or s.stopped:
      return {"listening": False, "devices": []}
    return {"listening": s.listening, "sid": s.id, "game": s.game,
            "devices": [s.device] if s.listening else [], "port": dtw.PORT,
            "connected": s.connected,
            "calibrated": bool(s.controller and s.controller.calibrated),
            "controller_live": bool(s.controller and s.controller.live),
            "controller_starting": bool(s.controller and s.controller.launch_at is not None)}


def mark_ready(sid):
  with _session_lock:
    if _session is not None and _session.id == sid:
      _session.ready.set()
      return True
  return False


def launch_controller(sid):
  with _session_lock:
    s = _session
    if s is None or s.id != sid or s.stopped or not s.connected or s.controller is None:
      raise RuntimeError("This calibration session is no longer connected. Use RUN IT BACK.")
    if s.client_id:
      with _clients_lock:
        owner_connected = any(getattr(q, "client_id", None) == s.client_id for q in _clients)
      if not owner_connected:
        raise RuntimeError("The setup page disconnected. Use RUN IT BACK.")
    s.controller.launch(time.monotonic())


def stop_session(sid):
  with _session_lock:
    if _session is not None and _session.id == sid:
      _session.stop()
      return True
  return False


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------
class Handler(SimpleHTTPRequestHandler):
  def __init__(self, *a, **kw):
    super().__init__(*a, directory=SITE_DIR, **kw)

  def log_message(self, *a):
    pass  # keep the terminal identical to dtw_detector's output

  def end_headers(self):
    self.send_header("Cache-Control", "no-store")
    super().end_headers()

  def _json(self, code, obj):
    body = json.dumps(obj).encode()
    self.send_response(code)
    self.send_header("Content-Type", "application/json")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers()
    self.wfile.write(body)

  def _body(self):
    n = int(self.headers.get("Content-Length") or 0)
    try:
      return json.loads(self.rfile.read(n) or b"{}")
    except ValueError:
      return {}

  def translate_path(self, path):
    # /demos/... is served from Demos/, everything else from gamegrip/
    clean = path.split("?", 1)[0].split("#", 1)[0]
    if clean == "/demos" or clean.startswith("/demos/"):
      saved, self.directory = self.directory, DEMOS_DIR
      try:
        return super().translate_path(path[len("/demos"):] or "/")
      finally:
        self.directory = saved
    return super().translate_path(path)

  def do_GET(self):
    path = self.path.split("?")[0]
    if path == "/api/events":
      return self._events()
    if path == "/api/status":
      return self._json(200, status())
    return super().do_GET()

  def do_POST(self):
    origin = self.headers.get("Origin")
    if origin is not None and origin != "http://" + self.headers.get("Host", ""):
      return self._json(403, {"error": "Use the local GameGrip setup page."})
    path = self.path.split("?")[0]
    if path == "/api/start":
      body = self._body()
      game = str(body.get("game", ""))[:32]
      client_id = str(body.get("client", ""))[:64] or None
      try:
        sid = start_session(game, client_id)
      except ValueError as e:
        return self._json(400, {"error": str(e)})
      return self._json(200, {"sid": sid, "port": dtw.PORT})
    if path in ("/api/ready", "/api/launch", "/api/stop"):
      try:
        sid = int(self._body().get("sid", 0))
      except (ValueError, TypeError):
        return self._json(400, {"error": "Invalid session."})
      if path == "/api/ready":
        return self._json(200, {"ok": mark_ready(sid)})
      if path == "/api/stop":
        return self._json(200, {"ok": stop_session(sid)})
      try:
        launch_controller(sid)
      except RuntimeError as e:
        return self._json(409, {"error": str(e)})
      return self._json(200, {"ok": True})
    self._json(404, {"error": "not found"})

  def _events(self):
    self.send_response(200)
    self.send_header("Content-Type", "text/event-stream")
    self.send_header("Connection", "keep-alive")
    self.end_headers()
    q = queue.Queue()
    q.client_id = parse_qs(urlsplit(self.path).query).get("client", [None])[0]
    with _clients_lock:
      _clients.append(q)
    try:
      self.wfile.write(b"retry: 1000\n\n")
      self.wfile.flush()
      while True:
        try:
          ev = q.get(timeout=1)
          self.wfile.write(f"data: {json.dumps(ev)}\n\n".encode())
        except queue.Empty:
          self.wfile.write(b": ping\n\n")
        self.wfile.flush()
    except (ConnectionError, OSError):
      pass
    finally:
      with _clients_lock:
        _clients.remove(q)
      # Browser teardown does not reliably run pagehide/sendBeacon. The owned
      # SSE connection is also a lease for Minecraft desktop input. Server-side
      # pings detect a lost page without background-tab JavaScript timers.
      with _session_lock:
        s = _session
        if s is not None and s.game == "minecraft" and q.client_id and s.client_id == q.client_id:
          s.stop()


class Server(ThreadingHTTPServer):
  def handle_error(self, request, client_address):
    if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
      return  # browser closed a connection (reload / tab closed): not worth a traceback
    super().handle_error(request, client_address)


def main():
  parser = argparse.ArgumentParser(description="Run the GameGrip calibration site and sports demos.")
  parser.add_argument("--port", default=dtw.PORT, help="ESP32 serial port, e.g. COM3.")
  parser.add_argument("--no-browser", action="store_true", help="Do not open a browser automatically.")
  args = parser.parse_args()
  dtw.PORT = args.port
  sys.stdout = TeeStdout(sys.stdout)
  server = Server((HOST, HTTP_PORT), Handler)
  server.daemon_threads = True
  url = f"http://{HOST}:{HTTP_PORT}/"
  print(f"GameGrip running at {url}  (ESP32 port: {dtw.PORT}, Ctrl+C to quit)",
        flush=True)
  print(f"Sports demo: {url}demos/  (uses the strap once calibration is done)",
        flush=True)
  if not args.no_browser:
    threading.Timer(0.5, webbrowser.open, args=(url,)).start()
  try:
    server.serve_forever()
  except KeyboardInterrupt:
    print("\nExiting program.")
  finally:
    if _session is not None:
      _session.stop()
    server.server_close()


if __name__ == "__main__":
  main()

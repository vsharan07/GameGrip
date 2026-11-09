"""Swing, punch, and run events for the sports demos.

The ESP32 sketch is unchanged. It prints ax,ay,az,gx,gy,gz and, on a board
with a button, a 7th column 0 or 1. The button is not used here.

  swing  baseball: dtw_detector's calibrated rotation tracker, per board
  punch  boxing: a light, quick gyro burst above the resting offset
  run    baseball: the left board pumping up and down
"""

import glob
import os
import sys
import threading
import time

FAST_DPS = 36.0          # a small boxing punch, measured from the resting offset
QUIET_DPS = 30.0
SWING_GAP_S = 0.1         # one flick is one punch; the next one can follow right after
PUNCH_DROP = 0.55         # re-arm once speed falls below this share of the punch's peak
PUNCH_REARM_S = 0.3       # or after this long, even if the arm keeps moving
CAL_SWING_DPS = 40.0
DEMO_SWING_SECONDS = 1.0  # three swings, one second each
DEMO_STILL_DPS = 60.0     # wobble allowed while "holding still"

# Baseball uses dtw_detector's rotation tracker, calibrated per board.
# dtw_detector ships at 0.85; higher fires on a smaller share of a swing.
SWING_SENSITIVITY = 1.15
# A swing this fast (share of the calibration peak) counts even if its
# rotation is short, so a quick real swing is never dropped.
PEAK_FRACTION = 0.5
MIN_PEAK_DPS = 90.0
MIN_GATE_DPS = 10.0
CLICK_MAX_S = 0.3
HOLD_AFTER_S = 0.45
HOLD_EVERY_S = 0.12


def parse_motion(line):
  """Return (gyro magnitude deg/s, button or None). None if the line is not a sample."""
  parts = line.strip().split(",")
  button = None
  if len(parts) == 7 and parts[-1].strip() in ("0", "1"):
    button = int(parts[-1].strip())
    parts = parts[:6]
  if len(parts) != 6:
    return None
  try:
    nums = [float(p) for p in parts]
  except ValueError:
    return None
  if any(n != n or n in (float("inf"), float("-inf")) for n in nums):
    return None
  gx, gy, gz = nums[3], nums[4], nums[5]
  return (gx * gx + gy * gy + gz * gz) ** 0.5, button, (gx, gy, gz)


class StrapMotion:
  """One board. push() returns zero or more of 'swing', 'click', 'hold'."""

  def __init__(self):
    self.armed = True
    self.last_swing = -1e9
    self.last_speed = 0.0
    self.peak = 0.0
    self.prev_mag = 0.0
    self.down = False
    self.down_at = None
    self.holding = False
    self.last_hold = -1e9
    self.swings = 0

  def push(self, now, gyro_mag, button, gyro=None):
    events = []
    if button is not None:
      if button and not self.down:
        self.down = True
        self.down_at = now
        self.holding = False
      elif button and self.down and self.down_at is not None:
        if not self.holding and now - self.down_at >= HOLD_AFTER_S:
          self.holding = True
          self.last_hold = now
          events.append("hold")
        elif self.holding and now - self.last_hold >= HOLD_EVERY_S:
          self.last_hold = now
          events.append("hold")
      elif not button and self.down:
        held_for = 0.0 if self.down_at is None else now - self.down_at
        was_holding = self.holding
        self.down = False
        self.holding = False
        self.down_at = None
        if not was_holding and held_for < CLICK_MAX_S:
          events.append("click")
    # A punch fires on the way up. The next one is ready once this one's speed
    # has clearly dropped, or after a short time, so a busy arm still punches.
    if not self.armed:
      self.peak = max(self.peak, gyro_mag)
      if (gyro_mag < max(FAST_DPS, self.peak) * PUNCH_DROP
          or now - self.last_swing >= PUNCH_REARM_S):
        self.armed = True
    rising = gyro_mag >= self.prev_mag
    self.prev_mag = gyro_mag
    if (self.armed and rising and gyro_mag >= FAST_DPS
        and now - self.last_swing >= SWING_GAP_S):
      self.armed = False
      self.peak = gyro_mag
      self.last_swing = now
      self.last_speed = gyro_mag
      self.swings += 1
      events.append("swing")
    return events


RUN_DPS = 75.0
RUN_WINDOW_S = 1.15
RUN_REVERSALS = 3
RUN_EVERY_S = 0.12


class RunCadence:
  """Hand up, hand down, hand up. Three reversals start the runner.

  A reversal is a strong motion pointing against the last strong motion,
  even if the arm slowed to a stop in between.
  """

  def __init__(self):
    self.last_dir = None
    self.reversals = []
    self.active = False
    self.last_emit = -1e9

  def push(self, now, gyro):
    speed = sum(v * v for v in gyro) ** 0.5
    if speed >= RUN_DPS:
      if self.last_dir is not None and sum(a * b for a, b in zip(gyro, self.last_dir)) < 0:
        self.reversals.append(now)
      self.last_dir = gyro
    self.reversals = [t for t in self.reversals if now - t <= RUN_WINDOW_S]
    if len(self.reversals) >= RUN_REVERSALS:
      self.active = True
    elif not any(now - t <= 0.45 for t in self.reversals):
      self.active = False
    if self.active and now - self.last_emit >= RUN_EVERY_S:
      self.last_emit = now
      return ["run"]
    return []


class BoardSwing:
  """Calibrated baseball swing for one board.

  Fires when the rotation tracker sees enough of a swing, or when the arm
  reaches half its calibrated peak speed in the swing direction.
  """

  def __init__(self, tracker, peak):
    self.tracker = tracker
    self.peak_fire = PEAK_FRACTION * max(peak, MIN_PEAK_DPS)
    self.armed = True
    self.last = -1e9
    self.swings = 0

  def push(self, now, gyro):
    import dtw_detector as dtw

    t = self.tracker
    fired = t.update(gyro)
    speed = float(sum(v * v for v in t.smooth) ** 0.5)
    toward = dtw.counted_speed(t.smooth, speed, t.swing_axis)
    if fired:
      self.armed = False
    elif (self.armed and toward >= self.peak_fire
          and now - self.last >= dtw.MIN_GAP_SECONDS):
      fired = True
      self.armed = False
      t.angle = -max(t.full_swing_angle - t.swing_angle, 0.0)
      t.since_fire = 0
    if speed <= t.speed_gate:
      self.armed = True
    if fired:
      self.last = now
      self.swings += 1
    return fired


def build_swing(rest, swings, hz):
  """Same math as dtw_detector.calibrate, without stopping on a weak board."""
  import numpy as np
  import dtw_detector as dtw

  rest = np.asarray(rest, dtype=float).reshape(-1, 3)
  bias = rest.mean(axis=0) if len(rest) else np.zeros(3)
  rest_speed = dtw.smoothed_speed(rest, bias)[5:]
  noise = float(np.percentile(rest_speed, 90)) if len(rest_speed) else 0.0
  swings = [np.asarray(s, dtype=float) for s in swings if len(s) >= 3]
  peaks = [float(dtw.smoothed_speed(g, bias).max()) for g in swings]
  peak = max(float(np.median(peaks)) if peaks else 0.0, MIN_PEAK_DPS)
  gate = max(noise, dtw.SLOW_FRACTION * peak, MIN_GATE_DPS)
  strokes = [dtw.main_stroke(g, bias, gate, hz) for g in swings]
  angles = [rot for rot, _ in strokes if rot > 0]
  full = max(float(np.median(angles)) if angles else 0.0, 20.0)
  axis = None
  dirs = [d for _, d in strokes if d is not None]
  if dirs:
    total = np.sum(dirs, axis=0)
    size = float(np.linalg.norm(total))
    if size / len(dirs) >= 0.5:
      axis = total / size
  tracker = dtw.RotationTracker(bias, gate, full, hz, axis)
  tracker.swing_angle = (0.5 / SWING_SENSITIVITY) * full
  return BoardSwing(tracker, peak), bias


def format_event(device, kind, n=None, speed=None):
  if kind == "swing":
    extra = f" {speed:.0f}" if speed is not None else ""
    return f"[{device}] SWING DETECTED! #{n}{extra}"
  if kind == "punch":
    return f"[{device}] PUNCH"
  if kind == "click":
    return f"[{device}] CLICK"
  if kind == "hold":
    return f"[{device}] HOLD"
  if kind == "run":
    return f"[{device}] RUN"
  raise ValueError(kind)


class BoardRoles:
  """Left USB port is the left player. Right USB port is the right player."""

  def __init__(self):
    self._lock = threading.Lock()
    self.side = {}

  def assign(self, port, device):
    with self._lock:
      self.side[port] = device

  def note_button(self, port, pressed):
    return

  def device_for(self, port):
    with self._lock:
      return self.side.get(port, "esp1")


def usb_location_ids(paths):
  """USB location id for each callout device, from ioreg. Empty if it cannot be read."""
  wanted = set(paths)
  found = {}
  try:
    import subprocess
    text = subprocess.check_output(
        ["ioreg", "-r", "-c", "IOUSBHostDevice", "-l"], text=True, timeout=3)
  except Exception:
    return found
  current = None
  for line in text.splitlines():
    if '"locationID"' in line:
      try:
        current = int(line.split("=", 1)[1].strip())
      except ValueError:
        current = None
    if "IOCalloutDevice" not in line:
      continue
    dev = line.split("=", 1)[-1].strip().strip('"')
    if dev in wanted and current is not None:
      found[dev] = current
  return found


def players_by_side(paths, locations=None):
  """esp1 is the left-hand port, esp2 is the right-hand port.

  On this Mac the left USB-C ports have the smaller location id.
  """
  locs = usb_location_ids(paths) if locations is None else locations
  ordered = sorted(paths, key=lambda path: (locs.get(path, 10 ** 12), path))
  out = {}
  if ordered:
    out[ordered[0]] = "esp1"
  if len(ordered) > 1:
    out[ordered[1]] = "esp2"
  return out


class LineReader:
  """Whole lines only. A half-read line would turn into a fake gyro spike."""

  def __init__(self, ser):
    self.ser = ser
    self.buf = b""
    self.synced = False

  def lines(self):
    try:
      waiting = self.ser.in_waiting
    except Exception:
      waiting = 0
    chunk = self.ser.read(waiting or 1)
    if chunk:
      self.buf += chunk
    *done, self.buf = self.buf.split(b"\n")
    if done and not self.synced:
      done = done[1:]
      self.synced = True
    return [line.decode("utf-8", errors="ignore") for line in done]


def pump(ser, port, roles, stop, swing=None, bias=None, now=None):
  """Read one serial port until stop is set. Prints lines the server already parses.

  swing: BoardSwing for baseball. Punches and runs use the gyro minus bias.
  """
  punches = StrapMotion()
  running = RunCadence()
  base = bias if bias is not None else (0.0, 0.0, 0.0)
  clock = time.monotonic if now is None else now
  reader = LineReader(ser)
  while not stop.is_set():
    for text in reader.lines():
      parsed = parse_motion(text)
      if parsed is None:
        continue
      _mag, _button, gyro = parsed
      rel = (gyro[0] - base[0], gyro[1] - base[1], gyro[2] - base[2])
      mag = (rel[0] ** 2 + rel[1] ** 2 + rel[2] ** 2) ** 0.5
      device = roles.device_for(port)
      moment = clock()
      events = ["punch" for kind in punches.push(moment, mag, None, rel) if kind == "swing"]
      # Already pumping before this sample: the arm is running, not batting.
      pumping = device == "esp1" and running.active
      run_events = running.push(moment, rel) if device == "esp1" else []
      batting = swing is not None and swing.push(moment, gyro)
      if batting and not pumping:
        events.append("swing")
      events.extend(run_events)
      for kind in events:
        n = swing.swings if kind == "swing" else None
        # One write per line: two boards print from two threads.
        sys.stdout.write(format_event(device, kind, n) + "\n")
        sys.stdout.flush()


def wait_for_button(serials, is_stopped, timeout=30):
  """Return the serial port whose button goes down, or None if the wait ends."""
  deadline = time.monotonic() + timeout
  for ser in serials:
    ser.timeout = 0.05
  while time.monotonic() < deadline:
    if is_stopped():
      return None
    for ser in serials:
      raw = ser.readline()
      if not raw:
        continue
      text = raw.decode("utf-8", errors="ignore") if isinstance(raw, (bytes, bytearray)) else raw
      parsed = parse_motion(text)
      if parsed is not None and parsed[1] == 1:
        return ser
  return None


def record_windows(serials, seconds, spare=(), lost=None):
  """Gyro magnitudes and vectors from every board during one shared window.

  spare: ports that may fail without ending the recording. A spare board that
  errors is added to lost (a set) and simply records nothing more.
  """
  mags = {ser.port: [] for ser in serials}
  gyros = {ser.port: [] for ser in serials}
  readers = []
  for ser in serials:
    # Non-blocking so one board cannot make the other wait out a timeout.
    try:
      ser.timeout = 0
      reset = getattr(ser, "reset_input_buffer", None)
      if reset:
        reset()
    except Exception:
      if ser.port not in spare:
        raise
      if lost is not None:
        lost.add(ser.port)
      continue
    readers.append(LineReader(ser))
  end = time.monotonic() + seconds
  while time.monotonic() < end:
    saw = False
    for reader in list(readers):
      try:
        texts = reader.lines()
      except Exception:
        if reader.ser.port not in spare:
          raise
        if lost is not None:
          lost.add(reader.ser.port)
        readers.remove(reader)
        continue
      for text in texts:
        parsed = parse_motion(text)
        if parsed is None:
          continue
        mags[reader.ser.port].append(parsed[0])
        gyros[reader.ser.port].append(parsed[2])
        saw = True
    if not saw:
      time.sleep(0.002)
  return mags, gyros


def record_all(serials, seconds):
  """Gyro magnitudes from every board during one shared window."""
  mags, _gyros = record_windows(serials, seconds)
  return mags


def still_spread(samples):
  """How much the gyro actually wiggles. A constant resting offset is ignored.

  Uses the 75th percentile so one bump or a hand settling does not fail it.
  """
  if len(samples) < 10:
    return None
  n = float(len(samples))
  mean = [sum(sample[axis] for sample in samples) / n for axis in range(3)]
  devs = sorted(
      ((s[0] - mean[0]) ** 2 + (s[1] - mean[1]) ** 2 + (s[2] - mean[2]) ** 2) ** 0.5
      for s in samples)
  return devs[int(0.75 * (len(devs) - 1))]


def mean_gyro(samples):
  """Resting gyro offset. Zero when there is nothing to average."""
  if not samples:
    return (0.0, 0.0, 0.0)
  n = float(len(samples))
  return tuple(sum(s[axis] for s in samples) / n for axis in range(3))


def peak_above(samples, bias=None):
  """Fastest gyro speed in a window, measured from the resting offset."""
  bx, by, bz = bias if bias is not None else (0.0, 0.0, 0.0)
  best = 0.0
  for gx, gy, gz in samples:
    best = max(best, ((gx - bx) ** 2 + (gy - by) ** 2 + (gz - bz) ** 2) ** 0.5)
  return best


def sample_rate(windows, seconds):
  """Samples per second per board, from the still window."""
  counts = [len(v) for v in windows.values() if v]
  if not counts or seconds <= 0:
    return 50.0
  return max(min(counts) / float(seconds), 20.0)


def noise90(values):
  """90th percentile gyro speed. None when the window is too short to trust."""
  if len(values) < 10:
    return None
  ordered = sorted(values)
  return ordered[int(0.9 * (len(ordered) - 1))]


def usb_ports():
  """USB serial ports on this computer, button-board name first when sorted."""
  found = sorted(glob.glob("/dev/cu.usb*"))
  if found:
    return found
  return sorted(extra_usb_ports(""))


def choose_port(configured):
  """Use the configured port. On a Mac, COM10 is a Windows name, so pick a real USB port."""
  if configured and configured.upper().startswith("COM") and os.name != "nt":
    found = usb_ports()
    if found:
      return found[0]
  if configured and configured.startswith("/") and not os.path.exists(configured):
    found = usb_ports()
    if found:
      return found[0]
  return configured


def extra_usb_ports(taken):
  """Other USB serial ports on this computer. Bluetooth ports are skipped."""
  try:
    from serial.tools import list_ports
  except Exception:
    return []
  found = []
  for port in list_ports.comports():
    dev = port.device
    if dev == taken:
      continue
    low = dev.lower()
    # Windows names Bluetooth serial links plain COMn, so also check the
    # description and hardware id (BTHENUM) rather than only the device name.
    about = ("%s %s" % (getattr(port, "description", "") or "",
                        getattr(port, "hwid", "") or "")).lower()
    if ("bluetooth" in low or "debug-console" in low
        or "bluetooth" in about or "bthenum" in about):
      continue
    if (low.startswith("/dev/cu.usb") or low.startswith("/dev/ttyusb")
        or low.startswith("/dev/ttyacm") or "usbserial" in low
        or "usbmodem" in low or "slab" in low or low.startswith("com")):
      found.append(dev)
  return found

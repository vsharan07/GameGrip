"""MPU6050 swing detector (ESP32 over serial).

Flow:
  1. Hold still  -> learns gyro bias and noise.
  2. Swing x3    -> learns how much your arm rotates in one swing.
  3. "CALIBRATED!"
  4. Live mode   -> every time the arm has rotated about half a swing's worth
                    (in ANY direction: swing, curve, shake...), it prints a big
                    coloured banner. Works for slow and fast swings, and for
                    many swings back-to-back with no pause.

How it works: only the gyroscope is used. It measures rotation speed and is
not affected by gravity or which way the arm is pointing, so ending in a
different pose than you started in doesn't confuse it. The script adds up
total rotation (degrees, roughly) while the arm is moving. Total rotation is
the same whether a swing is fast or slow, so there is no speed limit.

Expects the ESP32 to print one line per sample: ax,ay,az,gx,gy,gz
"""

import os
import time

import numpy as np
import serial

# --- Configuration ---
# Button board. On macOS this is usually /dev/cu.usbserial-XXXX (not COM10).
# A second USB serial port is picked up automatically as the other boxer.
PORT = "COM10"
BAUD = 115200
GYRO_COLUMNS = slice(3, 6)   # gx,gy,gz are columns 3,4,5 of each line

REST_SECONDS = 2.0           # How long to hold still for the baseline
REST_MAX_NOISE = 12.0        # deg/s jitter allowed during the still step
REST_ATTEMPTS = 3            # Retries if the sensor moved during the still step
SWING_WINDOW_SECONDS = 3.0   # Recording window for each calibration swing

# ===== MAIN KNOBS TO ADJUST =====
# SENSITIVITY: higher = easier to trigger (smaller swings count).
#   1.0 = fires after ~half of a calibration swing's rotation.
#   Try 0.6-0.8 if backswings / small moves still count, 1.2-1.5 if real
#   swings are missed.
SENSITIVITY = 0.85

# IGNORE_BACKSWING: calibration learns which way your swing rotates. With this
# on, rotation going roughly the OPPOSITE way (a backswing / wind-up / return)
# is ignored. Swings in other directions (curves, side swings) still count.
IGNORE_BACKSWING = True

# BACKSWING_ANGLE: how far (degrees) the motion must point away from your swing
# direction to be treated as a backswing. 180 = exactly reversed.
#   Lower (e.g. 100) = blocks more reverse-ish moves. Higher (e.g. 150) = only
#   blocks near-exact reverses. Must be between 90 and 180.
BACKSWING_ANGLE = 120
# ================================

SWING_FRACTION = 0.5 / SENSITIVITY  # fraction of a calibration swing to fire at

# Rotation slower than this (fraction of your calibration swings' peak speed)
# is ignored as jitter. Lower = slower swings still count.
SLOW_FRACTION = 0.08

# If the arm stays still this long, partly-finished rotation is forgotten,
# so small fidgets don't slowly add up into a fake swing.
FORGET_SECONDS = 0.4

# Minimum time between two detections (stops one swing counting twice).
MIN_GAP_SECONDS = 0.15

ALPHA = 0.5                  # Gyro smoothing (0..1, lower = smoother)

# DEBUG: after each movement, print a grey summary line (rotation, peak speed,
# how close it got, and why it may have been missed). Paste these for missed
# swings. Movements smaller than DEBUG_MIN_FRACTION of the fire point are
# skipped so small fidgets don't spam the screen.
DEBUG = True
DEBUG_MIN_FRACTION = 0.25

if os.name == "nt":
  os.system("")  # Enables ANSI colours in the Windows console

COLOURS = ["\033[42;30;1m", "\033[44;97;1m", "\033[45;97;1m", "\033[43;30;1m"]
GREEN_BG = COLOURS[0]
YELLOW = "\033[93m"
GREY = "\033[90m"
RESET = "\033[0m"


# ---------------------------------------------------------------------------
# Serial helpers
# ---------------------------------------------------------------------------
def parse_sample_line(line):
  """Accept six motion values, optionally followed by the firmware button bit.

  The browser sports detector uses motion only. Keep its six-value sample
  shape while accepting the same button-equipped board as the host app.
  """
  parts = line.strip().split(",")
  if len(parts) == 7:
    if parts[-1].strip() not in ("0", "1"):
      return None
    parts = parts[:6]
  if len(parts) != 6:
    return None
  try:
    sample = [float(p) for p in parts]
  except ValueError:
    return None
  return sample if all(np.isfinite(value) for value in sample) else None


def read_sample(ser):
  """Returns one [ax, ay, az, gx, gy, gz] sample, or None for a bad/empty line."""
  return parse_sample_line(ser.readline().decode("utf-8", errors="ignore"))


def record(ser, seconds):
  """Records for a fixed wall-clock time. Returns (samples array, measured Hz)."""
  ser.reset_input_buffer()
  data = []
  start = time.time()
  while time.time() - start < seconds:
    s = read_sample(ser)
    if s is not None:
      data.append(s)
  elapsed = time.time() - start
  if len(data) < 10:
    raise RuntimeError(
        f"Only got {len(data)} samples in {seconds}s. Is the ESP32 printing "
        "'ax,ay,az,gx,gy,gz' lines at the right baud rate?"
    )
  return np.array(data), len(data) / elapsed


# ---------------------------------------------------------------------------
# Rotation tracking
# ---------------------------------------------------------------------------
BACKSWING_COS = float(np.cos(np.radians(BACKSWING_ANGLE)))


def counted_speed(smooth, speed, swing_axis):
  """Rotation speed that counts toward a swing (0 for backswings)."""
  if swing_axis is not None and speed > 0:
    if np.dot(smooth, swing_axis) / speed < BACKSWING_COS:
      return 0.0
  return speed


class RotationTracker:
  """Turns gyro samples into 'how far has the arm rotated' and fires swings."""

  def __init__(self, bias, speed_gate, full_swing_angle, hz, swing_axis=None):
    self.bias = bias
    self.speed_gate = speed_gate
    self.swing_axis = swing_axis
    self.full_swing_angle = full_swing_angle
    self.swing_angle = SWING_FRACTION * full_swing_angle  # fire point
    self.dt = 1.0 / hz
    self.forget_samples = max(int(FORGET_SECONDS * hz), 1)
    self.gap_samples = max(int(MIN_GAP_SECONDS * hz), 1)
    self.reset()

  def reset(self):
    self.smooth = np.zeros(3)
    self.angle = 0.0
    self.still = 0
    self.since_fire = 10**9
    self.stroke_vec = np.zeros(3)  # direction of the current stroke
    self.move = None        # debug stats for the current movement

  def update(self, gyro):
    """Feed one gyro sample. Returns True when a swing is detected."""
    raw = np.asarray(gyro, dtype=float)
    self.smooth = ALPHA * (raw - self.bias) + (1 - ALPHA) * self.smooth
    speed = float(np.linalg.norm(self.smooth))
    self.since_fire += 1
    fired = False

    if speed > self.speed_gate:
      # Direction reversed -> the previous stroke is over. Drop any leftover
      # "rest of this swing" deduction so it can't build up over rapid
      # back-and-forth swings and cause misses.
      if np.dot(self.smooth, self.stroke_vec) < 0:
        self.angle = max(self.angle, 0.0)
        self.stroke_vec = np.zeros(3)
      self.stroke_vec = self.stroke_vec + self.smooth
      # Backswing samples add nothing, but still count as "moving" so they
      # don't trigger the forget-timer mid swing.
      counted = counted_speed(self.smooth, speed, self.swing_axis)
      self.angle += counted * self.dt
      self.still = 0
      if DEBUG:
        self._track(raw, speed, counted)
    else:
      self.still += 1
      if self.move is not None:
        self.move["samples"] += 1
      if self.still >= self.forget_samples:
        self.angle = 0.0
        self.stroke_vec = np.zeros(3)
        self._finish_movement()

    if self.angle >= self.swing_angle:
      if self.since_fire >= self.gap_samples:
        # We fire part-way through a swing (fast response). Pre-subtract the
        # rest of this swing so its second half isn't counted as another one.
        # Going still resets to 0, so separate quick swings aren't delayed.
        if self.move is not None:
          self.move["fires"] += 1
          self.move["best"] = 0.0
        self.angle = -max(self.full_swing_angle - self.swing_angle, 0.0)
        self.since_fire = 0
        fired = True
      elif self.move is not None:
        self.move["gap_blocked"] += 1

    if self.move is not None:
      self.move["best"] = max(self.move["best"], self.angle)
    return fired

  # --- debug -------------------------------------------------------------
  def _track(self, raw, speed, counted):
    m = self.move
    if m is None:
      m = self.move = dict(samples=0, peak=0.0, axis_peak=0.0, at_peak=0,
                           total=0.0, counted=0.0, fires=0, gap_blocked=0,
                           best=self.angle)
    m["samples"] += 1
    m["peak"] = max(m["peak"], speed)
    m["total"] += speed * self.dt
    m["counted"] += counted * self.dt
    # Saturation check on raw readings: a maxed-out gyro repeats the exact
    # same top value sample after sample.
    a = float(np.max(np.abs(raw)))
    if a > m["axis_peak"] + 0.05:
      m["axis_peak"], m["at_peak"] = a, 1
    elif a >= m["axis_peak"] - 0.05:
      m["at_peak"] += 1

  def _finish_movement(self):
    m, self.move = self.move, None
    if m is None or m["total"] < DEBUG_MIN_FRACTION * self.swing_angle:
      return  # tiny fidget, not worth printing
    dur = (m["samples"] - self.forget_samples) * self.dt  # minus trailing stillness
    ignored = m["total"] - m["counted"]
    line = (f"[move {dur:.2f}s] swings={m['fires']} | peak {m['peak']:.0f} dps"
            f" | rot {m['total']:.0f} = counted {m['counted']:.0f}"
            f" + backswing-ignored {ignored:.0f}"
            + (f" | best {m['best']:.0f} / need {self.swing_angle:.0f}"
               if m["fires"] == 0 else f" | need {self.swing_angle:.0f} per swing"))
    if m["gap_blocked"]:
      line += f" | gap-blocked {m['gap_blocked']}"
    flags = []
    if m["at_peak"] >= 3 and m["axis_peak"] > 100:
      flags.append(f"GYRO MAXED OUT at {m['axis_peak']:.0f} dps")
    if m["fires"] == 0 and m["best"] >= 0.6 * self.swing_angle:
      flags.append("NEAR MISS (raise SENSITIVITY)")
    if m["fires"] == 0 and ignored > m["counted"]:
      flags.append("mostly ignored as backswing (raise BACKSWING_ANGLE)")
    if flags:
      line += "  <<< " + "; ".join(flags)
    print(f"{GREY}{line}{RESET}", flush=True)


def main_stroke(gyro, bias, speed_gate, hz):
  """Finds the single biggest stroke in a calibration recording.

  A recording usually holds wind-up + swing + bringing the arm back. Moving
  samples are split into strokes wherever the rotation direction reverses
  (> 90 degrees from the stroke so far). "The swing" is the EARLIEST stroke
  that is nearly as fast as the fastest one: a slow wind-up is skipped, and
  if the arm comes back just as fast, the return is still second.
  Returns (rotation, unit direction).
  """
  smooth = np.zeros(3)
  strokes = []                 # (peak speed, rotation, summed direction)
  rot, vec, peak = 0.0, np.zeros(3), 0.0
  for g in gyro:
    smooth = ALPHA * (g - bias) + (1 - ALPHA) * smooth
    speed = float(np.linalg.norm(smooth))
    reversed_dir = rot > 0 and np.dot(smooth, vec) < 0
    if speed <= speed_gate or reversed_dir:
      if rot > 0:
        strokes.append((peak, rot, vec))
      rot, vec, peak = 0.0, np.zeros(3), 0.0
    if speed > speed_gate:
      rot += speed / hz
      vec = vec + smooth / hz
      peak = max(peak, speed)
  if rot > 0:
    strokes.append((peak, rot, vec))
  if not strokes:
    return 0.0, None
  fastest = max(p for p, _, _ in strokes)
  _, rot, vec = next(st for st in strokes if st[0] >= 0.75 * fastest)
  n = np.linalg.norm(vec)
  return rot, (vec / n if n > 0 else None)


def flat_top(gyro_raw):
  """If the gyro is maxing out, returns the value it's stuck at, else None.

  A saturated gyro repeats the exact same top reading sample after sample.
  """
  a = np.abs(np.asarray(gyro_raw))
  top = float(a.max())
  if top > 100 and np.count_nonzero(a >= top - 0.05) >= 3:
    return top
  return None


def smoothed_speed(gyro, bias):
  smooth = np.zeros(3)
  out = []
  for g in gyro:
    smooth = ALPHA * (g - bias) + (1 - ALPHA) * smooth
    out.append(np.linalg.norm(smooth))
  return np.array(out)


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------
def countdown(n=3):
  for c in range(n, 0, -1):
    print(f"  {c}...")
    time.sleep(1)


def calibrate(ser):
  # With the imuNumbers sketch the gyro arrives in deg/s: a still sensor is
  # normally under ~3 deg/s. Higher = it moved during the step (or wiring).
  for attempt in range(1, REST_ATTEMPTS + 1):
    print("\n--- Step 1: Put the sensor down and DON'T touch it ---")
    countdown()
    print(">>> Hold still... <<<")
    rest_raw, hz = record(ser, REST_SECONDS)
    rest_gyro = rest_raw[:, GYRO_COLUMNS]
    bias = rest_gyro.mean(axis=0)
    rest_speed = smoothed_speed(rest_gyro, bias)[5:]
    # 90th percentile: a single bump or wire glitch no longer fails the check,
    # but actually moving the sensor still does (real motion is 30+ deg/s).
    noise = float(np.percentile(rest_speed, 90))
    print(f"Baseline captured ({len(rest_raw)} samples, ~{hz:.0f} Hz).")
    print(f"Still check: gyro jitter {noise:.2f} deg/s, "
          f"gyro offset {np.linalg.norm(bias):.1f} deg/s")
    if noise <= REST_MAX_NOISE:
      print("Still check OK.")
      break
    print(f"{YELLOW}Sensor moved during the still step (jitter should be under "
          f"{REST_MAX_NOISE:g}).{RESET}")
    if attempt < REST_ATTEMPTS:
      print("Let's try that again - set it on a table this time.")
    else:
      print(f"{YELLOW}Continuing anyway; detection may be less reliable.{RESET}")

  swing_gyros = []
  for i in range(1, 4):
    print(f"\n--- Step 2: Calibration swing #{i} of 3 ---")
    print("Do ONE normal swing.")
    countdown()
    print(f">>> SWING #{i} NOW! <<<")
    raw, _ = record(ser, SWING_WINDOW_SECONDS)
    swing_gyros.append(raw[:, GYRO_COLUMNS])
    print("Recorded.")
    if i < 3:
      time.sleep(1.0)

  stuck = [v for v in (flat_top(g) for g in swing_gyros) if v is not None]
  if stuck:
    print(f"{YELLOW}[Warning] GYRO MAXED OUT at {max(stuck):.0f} deg/s during "
          f"calibration. Fast swings will be undercounted. If that's ~250, "
          f"the ESP32 is still running the old firmware: reflash "
          f"imuNumbers.ino (it should print 'gyro +-2000 dps' on boot).{RESET}")

  peaks = [smoothed_speed(g, bias).max() for g in swing_gyros]
  speed_gate = max(noise, SLOW_FRACTION * float(np.median(peaks)))

  # Measure only the main swing in each recording (not the wind-up or the
  # arm coming back), so "one swing" really means one swing.
  strokes = [main_stroke(g, bias, speed_gate, hz) for g in swing_gyros]
  angles = [rot for rot, _ in strokes]

  swing_axis = None
  if IGNORE_BACKSWING:
    dirs = [d for _, d in strokes if d is not None]
    consistency = float(np.linalg.norm(np.sum(dirs, axis=0)) / 3) if dirs else 0.0
    if consistency >= 0.5:
      swing_axis = np.sum(dirs, axis=0) / np.linalg.norm(np.sum(dirs, axis=0))
    else:
      print(f"{YELLOW}[Warning] Your 3 calibration swings went in different "
            f"directions (consistency {consistency:.2f}), so backswing "
            f"filtering is OFF. Recalibrate with 3 similar swings to enable "
            f"it.{RESET}")

  if min(peaks) < noise * 3:
    raise RuntimeError(
        "The sensor is almost as 'busy' sitting still as it is swinging "
        f"(still noise {noise:.0f} vs weakest swing {min(peaks):.0f}).\n"
        "  Detection would fire constantly, so stopping here. The IMU readings "
        "are not clean while still: check wiring / ESP32 code, then re-run."
    )

  # Use the median swing so one weird calibration swing doesn't skew it.
  full_swing = float(np.median(angles))
  swing_angle = SWING_FRACTION * full_swing

  print("\n" + "=" * 50)
  print(f"{GREEN_BG}  CALIBRATED!  {RESET}")
  print(f" Sample rate:          ~{hz:.0f} Hz")
  print(f" Rest noise (speed):   {noise:.2f}")
  print(f" Swing peak speeds:    {[f'{p:.1f}' for p in peaks]}")
  print(f" Swing rotations:      {[f'{a:.1f}' for a in angles]}")
  print(f" Ignoring speeds below {speed_gate:.2f}")
  print(f" Swing fires at        {swing_angle:.1f} rotation "
        f"(SENSITIVITY {SENSITIVITY})")
  print(f" Backswing filter:     "
        f"{'ON' if swing_axis is not None else 'OFF'}")
  print("=" * 50)
  return RotationTracker(bias, speed_gate, full_swing, hz, swing_axis)


# ---------------------------------------------------------------------------
# Live detection
# ---------------------------------------------------------------------------
def detect(ser, tracker):
  print("\nListening for swings... (Ctrl+C to exit)\n")
  swings = 0
  ser.reset_input_buffer()
  while True:
    s = read_sample(ser)
    if s is None:
      continue
    if tracker.update(s[GYRO_COLUMNS]):
      swings += 1
      colour = COLOURS[swings % len(COLOURS)]
      print(f"{colour}   SWING DETECTED!  #{swings}   {RESET}", flush=True)


def main():
  print(f"Connecting to ESP32 on {PORT}...")
  try:
    ser = serial.Serial(PORT, BAUD, timeout=1)
    time.sleep(2)
  except Exception as e:
    print(f"Error opening serial port: {e}")
    return

  print("\n=== MPU6050 Swing Calibrator & Detector ===")
  input("Press [Enter] when ready to start calibration...")

  try:
    tracker = calibrate(ser)
    detect(ser, tracker)
  except RuntimeError as e:
    print(f"{YELLOW}[Error] {e}{RESET}")
  except KeyboardInterrupt:
    print("\nExiting program.")
  finally:
    ser.close()


if __name__ == "__main__":
  main()

"""Parse the firmware CSV contract.

A sample line is six numbers: ax, ay, az in g, then gx, gy, gz in deg/s.
Any other line is a diagnostic and is not a sample. A missing chip must
not be turned into zeros that look like a real, still sensor.
"""

CSV_COLUMNS = 6
ACCEL_UNIT = "g"
GYRO_UNIT = "deg/s"


def parse_csv_line(line):
    """Return a 6-tuple, or None when the line is not one sample."""
    parsed = parse_stream_line(line)
    if parsed is None:
        return None
    return parsed[0]


def parse_stream_line(line):
    """Return (sample, button).

    sample is ax, ay, az in g and gx, gy, gz in deg/s.
    button is 0 or 1 when the firmware added a 7th column, else None.
    The 7th column is only accepted as a button state, not as another axis.
    """
    if line is None:
        return None
    text = line.strip()
    if not text:
        return None
    parts = text.split(",")
    button = None
    if len(parts) == CSV_COLUMNS + 1:
        if parts[-1] not in ("0", "1"):
            return None
        button = int(parts[-1])
        parts = parts[:-1]
    if len(parts) != CSV_COLUMNS:
        return None
    values = []
    for part in parts:
        try:
            number = float(part)
        except ValueError:
            return None
        if number != number or number in (float("inf"), float("-inf")):
            return None
        values.append(number)
    return tuple(values), button


class StreamQuality(object):
    """Counts accepted and rejected lines and notices a silent port."""

    def __init__(self, timeout_s):
        self.timeout_s = float(timeout_s)
        self.accepted = 0
        self.rejected = 0
        self.last_accept_s = None

    def observe(self, sample, now_s):
        if sample is None:
            self.rejected += 1
            return
        self.accepted += 1
        self.last_accept_s = now_s

    def is_down(self, now_s):
        if self.last_accept_s is None:
            return True
        return (now_s - self.last_accept_s) >= self.timeout_s


class ArrivalRate(object):
    def __init__(self, window_s=1.0):
        self.window_s = float(window_s)
        self._times = []

    def add(self, now_s):
        self._times.append(now_s)
        cutoff = now_s - self.window_s
        self._times = [stamp for stamp in self._times if stamp > cutoff]

    def hz(self):
        return len(self._times)


class LineSource(object):
    """readline() object that yields text lines. Bytes are decoded."""

    def __init__(self, port):
        self._port = port

    def read_line(self):
        raw = self._port.readline()
        if raw is None or raw == b"" or raw == "":
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="ignore")
        return raw

    def read_frame(self):
        """Return (sample, button) or None. An empty read is None."""
        line = self.read_line()
        if line is None:
            return None
        return parse_stream_line(line)

    def read_sample(self):
        frame = self.read_frame()
        if frame is None:
            return None
        return frame[0]

    def reset_input_buffer(self):
        reset = getattr(self._port, "reset_input_buffer", None)
        if reset is not None:
            reset()

    def close(self):
        close = getattr(self._port, "close", None)
        if close is not None:
            close()


def open_serial(port, baud, timeout_s):
    """Open a real serial port. pyserial is imported only here."""
    import serial

    handle = serial.Serial(port, baud, timeout=timeout_s)
    return LineSource(handle)

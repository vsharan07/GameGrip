"""Commands for calibration, bindings, and play.

The serial loop keeps running between commands. Calibration and a binding
change do not restart the process. Dry-run is the state after every
calibration. Live mode starts only from --live or from the live command
after the user types LIVE.
"""

import sys
import time

from controls import ControlMap
from input.keyboard_control import KeyboardController
from input.mouse_aim import MouseController
from recognition.gestures import (
    GestureLibrary,
    GestureLibraryError,
    RecordingError,
    Recognizer,
    build_gesture,
    load_recognition_settings,
    slug,
    validate_recording,
)
from sensor.csv_stream import ArrivalRate, StreamQuality
from session import PlaySession

HELP = """
Commands, then Enter:
  status
  calibrate <name>     record one gesture three times
  look                 right turn, then up turn
  look show            print the saved mounting
  list
  show <name>
  rename <name> <new name>
  delete <name>
  game <minecraft|roblox>
  bind <gesture> <input>
  unbind <gesture>
  aim on|off
  aim sensitivity <pixels per degree>
  aim axis <horizontal gx|gy|gz> <vertical gx|gy|gz>
  aim invert horizontal|vertical|both|none
  aim deadzone <deg/s>
  aim horizontal <pixels per degree>
  aim vertical <pixels per degree>
  aim accel <1 = linear, higher boosts fast moves>
  aim pause
  aim resume
  live                 ask for confirmation, then send input
  dry                  stop sending input
  stop                 release inputs and latch the emergency stop
  quit
""".strip()


class App(object):
    def __init__(self, session, library, controls, recognition, window_s, hz, paths):
        self.session = session
        self.library = library
        self.controls = controls
        self.recognition = recognition
        self.window_s = window_s
        self.hz = hz
        self.gesture_path, self.controls_path = paths
        self.listening = True

    def save(self):
        self.library.save(self.gesture_path)
        self.controls.save(self.controls_path)


def build_app(safety, gesture_path=None, controls_path=None, window_s=3.0):
    recognition = load_recognition_settings()
    library = GestureLibrary.load(gesture_path)
    controls = ControlMap.load(controls_path)
    session = PlaySession(
        library,
        controls,
        Recognizer(library, recognition),
        recognition.segmenter(recognition.assumed_hz),
        MouseController(live=False),
        KeyboardController(live=False),
        safety,
    )
    app = App(
        session,
        library,
        controls,
        recognition,
        window_s,
        recognition.assumed_hz,
        (gesture_path, controls_path),
    )
    return app


def run_loop(source, app, safety, stale_timeout_s, reopen):
    """Read samples until quit. reopen() returns a new source or raises.

    The source actually in use is returned so the caller can close it.
    """
    quality = StreamQuality(stale_timeout_s)
    rate = ArrivalRate()
    announced_down = False
    announced_stop = False
    last_report = time.time()
    started = last_report
    print(HELP)
    print("DRY-RUN. Gestures are shown and no input is sent." if not app.session.live
          else "LIVE. Mouse and keyboard input will be sent. Type stop, or press Esc three times within one second.")
    source.reset_input_buffer()
    while True:
        if safety.stopped and not announced_stop:
            app.session.release_inputs()
            print("Emergency stop is active. Held keys and mouse buttons are released.")
            announced_stop = True
        command = _poll_command()
        if command is not None:
            if app.command(command, source=source) == "quit":
                app.session.release_inputs()
                return source
        if safety.stopped:
            _drain(source)
            continue
        try:
            frame = source.read_frame()
        except Exception as exc:
            print("Serial read failed (%s). Inputs released. Trying the port again." % exc)
            app.session.release_inputs()
            try:
                source.close()
            except Exception:
                pass
            time.sleep(1.0)
            try:
                source = reopen()
                source.reset_input_buffer()
                announced_down = False
                print("Serial port is open again.")
            except Exception as again:
                print("Could not reconnect: %s" % again)
                return source
            continue
        now_s = time.time()
        sample, button = (None, None) if frame is None else frame
        quality.observe(sample, now_s)
        if sample is not None:
            rate.add(now_s)
            announced_down = False
            for event in app.session.push(sample, now_s, button=button):
                _print_event(event)
        elif quality.is_down(now_s) and not announced_down:
            if quality.last_accept_s is not None or (now_s - started) >= stale_timeout_s:
                print("Sensor data is missing. No input will be injected.")
                app.session.mouse.reset_motion()
                app.session._set_look("neutral", 0.0, 0.0, 0.0, True)
                announced_down = True
        if now_s - last_report >= 1.0:
            mode = "STOPPED" if safety.stopped else ("LIVE" if app.session.live else "DRY-RUN")
            look = app.session.look
            print(
                "stream %.0f Hz gx=%.0f gy=%.0f gz=%.0f mode=%s look=%s rate=%.0f dx=%.1f dy=%.1f%s"
                % (
                    rate.hz(),
                    look.get("gx", 0.0),
                    look.get("gy", 0.0),
                    look.get("gz", 0.0),
                    mode,
                    look["direction"],
                    look["rate_dps"],
                    look["dx"],
                    look["dy"],
                    " paused" if look.get("paused") else (" deadzone" if look.get("deadzone") else ""),
                ),
                flush=True,
            )
            last_report = now_s


def _print_event(event):
    kind = event.get("type")
    if kind == "gesture":
        if event["name"]:
            print(
                "gesture %s  distance %.1f  threshold %.1f  action %s"
                % (event["name"], event["distance"], event["threshold"], event["input"] or "unbound"),
                flush=True,
            )
        else:
            distance = event["distance"]
            shown = "n/a" if distance is None else "%.1f" % distance
            if event["reason"] == "no template":
                return
            print("no match (%s) distance %s" % (event["reason"], shown), flush=True)
    elif kind == "dry-run":
        print("dry-run: would press %s" % event["input"], flush=True)
    elif kind == "injected":
        print("sent %s" % event["input"], flush=True)
    elif kind == "paused":
        print(
            "look paused. A fast swing is a %s click."
            % ("right" if event.get("input") == "right" else "left"),
            flush=True,
        )
    elif kind == "button":
        print("button %s" % (event["input"] or "unbound"), flush=True)


def command(app, line, source=None, confirmed=False):
    """Run one console line. Returns 'quit' when the process should exit."""
    parts = line.strip().split()
    if not parts:
        return None
    name = parts[0].lower()
    if name in ("help", "?"):
        print(HELP)
    elif name == "status":
        _status(app)
    elif name == "quit":
        return "quit"
    elif name == "stop":
        app.session.safety.stopped = True
        app.session.release_inputs()
        print("Emergency stop is active. Held keys and mouse buttons are released.")
    elif name == "dry":
        app.session.set_live(False)
        print("DRY-RUN. Gestures are shown and no input is sent.")
    elif name == "live":
        if not app.listening:
            print("Live mode needs keyboard permission. It stays off.")
            return None
        if not confirmed:
            print("Type LIVE and Enter to send real mouse and keyboard input.")
            typed = sys.stdin.readline().strip()
            if typed != "LIVE":
                print("Staying in dry-run.")
                return None
        app.session.set_live(True)
        print("LIVE. Type stop, or press Esc three times within one second, to release every held input.")
    elif name == "list":
        _list(app)
    elif name == "show" and len(parts) >= 2:
        _show(app, parts[1])
    elif name == "delete" and len(parts) >= 2:
        _delete(app, parts[1])
    elif name == "rename" and len(parts) >= 3:
        _rename(app, parts[1], " ".join(parts[2:]))
    elif name == "bind" and len(parts) >= 3:
        _bind(app, parts[1], parts[2])
    elif name == "unbind" and len(parts) >= 2:
        app.controls.unbind(slug(parts[1]))
        app.save()
        print("Unbound %s." % slug(parts[1]))
    elif name == "game" and len(parts) >= 2:
        _game(app, parts[1], parts[2] if len(parts) > 2 else "combat")
    elif name == "aim":
        _aim(app, parts[1:])
    elif name == "look" and len(parts) >= 2 and parts[1] == "show":
        _look_show(app)
    elif name == "look" and source is not None:
        _run_look(app, source)
    elif name == "look":
        print("Look calibration needs the serial stream. Start main.py with --port.")
    elif name == "calibrate" and len(parts) >= 2 and source is not None:
        _calibrate(app, source, " ".join(parts[1:]))
    elif name == "calibrate":
        print("Use: calibrate <name>")
    else:
        print("Unknown command. Type help.")
    return None


App.command = command


def _status(app):
    mode = "STOPPED" if app.session.safety.stopped else ("LIVE" if app.session.live else "DRY-RUN")
    aim = app.controls.aim
    print(
        "mode=%s game=%s gestures=%d aim=%s axis=%s/%s horizontal=%.3f vertical=%.3f look=%s"
        % (
            mode,
            app.controls.game or "none",
            len(app.library.gestures),
            "paused" if aim.paused else ("on" if aim.enabled else "off"),
            aim.horizontal_axis,
            aim.vertical_axis,
            aim.horizontal_sensitivity,
            aim.vertical_sensitivity,
            app.session.look["direction"],
        )
    )


def _list(app):
    if not app.library.gestures:
        print("No gestures yet. Use: calibrate Swing")
        return
    for gesture_id in app.library.ids():
        gesture = app.library.get(gesture_id)
        print("%s  %s  -> %s" % (gesture_id, gesture["name"], app.controls.binding(gesture_id) or "unbound"))


def _show(app, name):
    gesture = app.library.get(slug(name))
    if gesture is None:
        print("No gesture named %s." % name)
        return
    print(
        "%s (%s) threshold %.1f  rate %.0f Hz  reps %s  saved %s  input %s"
        % (
            gesture["name"],
            gesture["id"],
            gesture["threshold"],
            gesture["sample_rate_hz"],
            gesture["repetition_lengths"],
            gesture["created"],
            app.controls.binding(gesture["id"]) or "unbound",
        )
    )


def _delete(app, name):
    gesture_id = slug(name)
    try:
        app.library.delete(gesture_id)
    except GestureLibraryError as exc:
        print(exc)
        return
    app.controls.unbind(gesture_id)
    app.save()
    print("Deleted %s." % gesture_id)


def _rename(app, name, new_name):
    try:
        app.library.rename(slug(name), new_name)
    except GestureLibraryError as exc:
        print(exc)
        return
    app.save()
    print("Renamed %s to %s." % (slug(name), new_name))


def _bind(app, name, input_name):
    gesture_id = slug(name)
    if app.library.get(gesture_id) is None:
        print("No gesture named %s. Calibrate it first." % name)
        return
    app.controls.bind(gesture_id, input_name)
    app.save()
    print("%s -> %s. Still %s." % (gesture_id, input_name, "LIVE" if app.session.live else "DRY-RUN"))


def _game(app, game, action_set):
    try:
        app.controls.apply_game(game, action_set)
    except Exception as exc:
        print(exc)
        return
    app.save()
    print("Game %s, action set %s." % (game, action_set))
    _list(app)


def _aim(app, parts):
    aim = app.controls.aim
    if not parts:
        print("Use: aim on, aim off, aim pause, aim resume, aim sensitivity, aim horizontal, aim vertical, aim accel, aim deadzone, aim neutral, aim axis, aim invert")
        return
    head = parts[0].lower()
    try:
        if head == "on":
            aim.enabled = True
            aim.paused = False
        elif head == "off":
            aim.enabled = False
        elif head == "pause":
            aim.paused = True
        elif head == "resume":
            aim.paused = False
        elif head == "sensitivity" and len(parts) == 2:
            value = float(parts[1])
            aim.sensitivity = value
            aim.horizontal_sensitivity = value
            aim.vertical_sensitivity = value
        elif head == "horizontal" and len(parts) == 2:
            aim.horizontal_sensitivity = float(parts[1])
        elif head == "vertical" and len(parts) == 2:
            aim.vertical_sensitivity = float(parts[1])
        elif head == "accel" and len(parts) == 2:
            aim.acceleration = float(parts[1])
        elif head == "neutral" and len(parts) == 2:
            aim.neutral_dps = float(parts[1])
        elif head == "deadzone" and len(parts) == 2:
            aim.deadzone_dps = float(parts[1])
        elif head == "axis" and len(parts) == 3:
            if parts[1] not in ("gx", "gy", "gz") or parts[2] not in ("gx", "gy", "gz"):
                print("Axes are gx, gy, or gz.")
                return
            aim.horizontal_axis = parts[1]
            aim.vertical_axis = parts[2]
        elif head == "invert" and len(parts) == 2:
            which = parts[1].lower()
            if which == "horizontal":
                aim.invert_horizontal = not aim.invert_horizontal
            elif which == "vertical":
                aim.invert_vertical = not aim.invert_vertical
            elif which == "both":
                aim.invert_horizontal = True
                aim.invert_vertical = True
            elif which == "none":
                aim.invert_horizontal = False
                aim.invert_vertical = False
            else:
                print("Use horizontal, vertical, both, or none.")
                return
        else:
            print("Aim command not understood.")
            return
    except ValueError:
        print("That value is not a number.")
        return
    app.save()
    print(
        "Aim %s. horizontal %s%s %.3f, vertical %s%s %.3f, accel %.2f, deadzone %.0f deg/s."
        % (
            "paused" if aim.paused else ("on" if aim.enabled else "off"),
            aim.horizontal_axis,
            " inverted" if aim.invert_horizontal else "",
            aim.horizontal_sensitivity,
            aim.vertical_axis,
            " inverted" if aim.invert_vertical else "",
            aim.vertical_sensitivity,
            aim.acceleration,
            aim.deadzone_dps,
        )
    )


def _calibrate(app, source, name):
    print("Recording %s. Do the same movement three times, then hold still." % name)
    recordings = []
    for index in range(1, 4):
        print("Repetition %d of 3. Get ready." % index)
        _countdown()
        print("Move now.")
        source.reset_input_buffer()
        try:
            recording = capture_repetition(source, app.recognition, app.hz, app.window_s, time.time)
        except RecordingError as exc:
            print("Repetition %d rejected: %s" % (index, exc))
            print("Calibration cancelled. Nothing was saved.")
            return
        recordings.append(recording)
        print("Repetition %d accepted (%d samples)." % (index, len(recording)))
    try:
        gesture = build_gesture(name, recordings, app.hz, app.recognition)
    except RecordingError as exc:
        print("Calibration rejected: %s" % exc)
        return
    app.library.add(gesture)
    app.session.recognizer = Recognizer(app.library, app.recognition)
    app.session.segmenter = app.recognition.segmenter(app.hz)
    app.save()
    mode = "LIVE" if app.session.live else "DRY-RUN"
    print(
        "Saved %s. Threshold %.1f. Mode stays %s. Bind it with: bind %s <input>"
        % (gesture["name"], gesture["threshold"], mode, gesture["id"])
    )


def _look_show(app):
    from look_calibration import LookCalibrationError, load_document

    try:
        document = load_document()
    except LookCalibrationError as exc:
        print(exc)
        return
    if document is None:
        print("No look calibration yet. Type: look")
        return
    mount = document["mount"]
    print(
        "Mount %s%s / %s%s. Bias %s deg/s. Noise %.1f. Saved %s."
        % (
            mount["horizontal_axis"],
            " inverted" if mount["invert_horizontal"] else "",
            mount["vertical_axis"],
            " inverted" if mount["invert_vertical"] else "",
            document["bias_dps"],
            document["noise_dps"],
            document["created"],
        )
    )
    for note in document.get("notes") or []:
        print(note)


def _run_look(app, source):
    from look_calibration import LookCalibrationError, apply_to_aim, run_grouped, save_document

    try:
        document = run_grouped(source, print)
    except LookCalibrationError as exc:
        print(exc)
        print("Nothing was saved.")
        return
    path = save_document(document)
    apply_to_aim(document, app.controls.aim)
    app.session.mouse.reset_motion()
    app.save()
    mount = document["mount"]
    print("Saved %s." % path)
    print(
        "Horizontal %s%s, vertical %s%s. Dead zone %.0f deg/s. Sensitivity %.1f / %.1f. Accel %.2f."
        % (
            mount["horizontal_axis"],
            " inverted" if mount["invert_horizontal"] else "",
            mount["vertical_axis"],
            " inverted" if mount["invert_vertical"] else "",
            app.controls.aim.deadzone_dps,
            app.controls.aim.horizontal_sensitivity,
            app.controls.aim.vertical_sensitivity,
            app.controls.aim.acceleration,
        )
    )
    for note in document["notes"]:
        print(note)
    if app.session.live:
        print("Still LIVE. Click the window you want to control.")
    else:
        print("DRY-RUN. Nothing is sent until you type live and then LIVE.")
        print("The status line has to say mode=LIVE before the mouse or W will move.")


def capture_repetition(source, recognition, hz, window_s, clock):
    """Return one movement window, or raise RecordingError with the reason."""
    segmenter = recognition.segmenter(hz)
    start = clock()
    dropped = 0
    seen = 0
    while clock() - start < window_s:
        frame = source.read_frame()
        if frame is None:
            continue
        sample, _button = frame
        if sample is None:
            dropped += 1
            continue
        seen += 1
        window = segmenter.push(sample)
        if window is not None:
            validate_recording(window, recognition, dropped=dropped)
            return window
    window = segmenter.flush()
    if window is not None:
        validate_recording(window, recognition, dropped=dropped)
        return window
    if segmenter.rejection:
        raise RecordingError(segmenter.rejection)
    if seen == 0:
        raise RecordingError("No samples arrived. Check the serial connection.")
    raise RecordingError("No movement was seen. Do one full gesture, then hold still.")


def _countdown(steps=3):
    for value in range(steps, 0, -1):
        print("  %d..." % value)
        time.sleep(1)


def _poll_command():
    import select

    if not sys.stdin.isatty():
        return None
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0)
    except (OSError, ValueError):
        return None
    if not ready:
        return None
    return sys.stdin.readline()


def _drain(source):
    try:
        source.read_frame()
    except Exception:
        pass

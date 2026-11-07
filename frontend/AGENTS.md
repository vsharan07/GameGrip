# GameGrip: guide for maintainers and coding agents

GameGrip is an ESP32 + MPU6050 strap that turns a toy's motion into game input.
This repo has three parts that talk to each other:

| Part | Files | What it is |
|---|---|---|
| Swing detector | `dtw_detector.py`, `imuNumbers/imuNumbers.ino` | Reads the strap over serial, calibrates, then prints `SWING DETECTED! #n` |
| Bridge server | `gamegrip_server.py` | Serves both web pages, runs the detector, and streams what it prints to the pages as events |
| Calibration site | `gamegrip/index.html` (+ `song.mp3`) | The "GAME GRIP!" page: pick a game, calibrate the strap with animated steps |
| Sports demo | `Demos/` | Baseball + boxing party game (45-second judges' demo) that uses the strap once calibration is done |

Run everything with:

```bash
python gamegrip_server.py
```

That serves the site at `http://127.0.0.1:8765/` and the demo at `http://127.0.0.1:8765/demos/`.
Picking **DEMOS** on the site, calibrating and pressing **LAUNCH DEMOS** opens the demo with the strap already working.

---

## 1. How the pieces connect (read this before changing the backend)

```
ESP32 --serial--> dtw_detector.calibrate()/detect() --print()--> gamegrip_server.TeeStdout
      --parse_line()--> event dict --SSE /api/events--> calibration site + sports demo
```

* The server **imports** the detector and never changes its logic; it only watches the lines it prints.
  `parse_line()` in `gamegrip_server.py` turns each printed line into an event.
  **If the detector's print wording changes, `parse_line()` must change with it.**
* The page follows Python's timing exactly. Nothing on the site runs its own countdown: each `3`, `2`, `1`
  appears when Python prints it. Keep it that way.

### Event contract (what the frontend expects)

Every event is JSON with `type`, `sid` (session id) and `device` (strap id, currently always `"esp1"`).

| `type` | Extra fields | Printed line it comes from | Frontend reaction |
|---|---|---|---|
| `connected` | `port` | (server opened the serial port) | starts the HOLD STILL step |
| `step` | `kind` (`still`/`swing`), `step` (0 for still, 1-3 for swings) | `--- Step 1:` / `--- Step 2: Calibration swing #n of 3` | highlights the step chip |
| `count` | `n` (3, 2, 1) | `  3...` | countdown shout, exactly on the beat |
| `record-start` | `kind`, `step`, `ms` | `>>> Hold still... <<<` / `>>> SWING #n NOW! <<<` | REC bar + "HOLD IT..." / "RIGHT!" / "DOWN!" |
| `record-end` | `kind` | `Baseline captured` / `Recorded.` | ding, chip ticks |
| `still-ok`, `still-continue` | | `Still check OK.` / `Continuing anyway` | HOLD STILL chip done |
| `still-retry` | `text` | `Let's try that again` | next "3" says "MOVED! TRY AGAIN IN" |
| `warning` | `text` | `[Warning] ...` | (not shown yet) |
| `calibrated` | | `CALIBRATED!` | finale scene |
| `listening` | | `Listening for swings` | strap is live (the demo starts using it) |
| `swing` | `n`, `colour` | `SWING DETECTED! #n` | swing star burst on the site; a swing/punch in the demo |
| `error` | `title`, `message` | serial or detector failure | "NO GRIP DETECTED!" error card |

HTTP endpoints: `POST /api/start {game}` → `{sid}`, `POST /api/ready {sid}` (= "press Enter" to begin calibration),
`GET /api/status` → `{listening, sid, devices}`, `GET /api/events` (Server-Sent Events).

### Calibration steps: what the player sees and what the detector records

The detector records **hold still + 3 swings**. The player is shown **HOLD STILL → MOVE RIGHT → MOVE DOWN**.
The mapping lives in one table in `gamegrip/index.html`, `STEP_UI`:

| Internal step | Chip | Shouts |
|---|---|---|
| still | HOLD STILL | "HOLD STILL IN 3/2/1", "HOLD IT..." |
| swing 1 | MOVE RIGHT | "MOVE RIGHT!" on 3, "MOVE RIGHT IN" on 2/1, "RIGHT!" while recording |
| swing 2 | MOVE DOWN | "MOVE DOWN!", "MOVE DOWN IN", "DOWN!" |
| swing 3 | MOVE DOWN (shared, silent extra) | same as swing 2; the chip only ticks after this one |

When the new backend lands: if it still prints the same lines, nothing else changes. If it prints new lines,
update `parse_line()` so it still emits the events above (`step`/`count`/`record-start`/`record-end` with the right
`kind` and `step`). If the number of recordings changes, edit `STEP_UI`, `LAST_STEP`, the chips in `#steps`,
and `swingViews` (one camera move per recording).

### Hardware notes

* `PORT = "COM10"`, `BAUD = 115200` are in `dtw_detector.py`. Only one program can hold the port: **close the Arduino Serial Monitor.**
* The server opens the port with DTR/RTS held low so the ESP32 is **not** rebooted. (A reboot costs ~5 s because the sketch tries WiFi first.)
* The sketch only streams `ax,ay,az,gx,gy,gz` lines when the MPU6050 is found.
* Hold-still check: 90th percentile of gyro speed must be under `REST_MAX_NOISE = 12` deg/s (real movement is 30+).

---

## 2. Calibration site style (`gamegrip/index.html`)

One file: three.js + Tailwind (CDN) + GSAP. The look was set deliberately; keep it.

**Default theme: Frutiger-metro green.** Lime `#c8ff3d` / `#9be22c`, deep green `#2f7d00`, black, white.
2000s skate/boombox/silhouette-dancer world, glossy surfaces, halftone + scanline + vignette overlay,
ring/dither corner frame, everything pulses to the beat of `song.mp3` (from 1:34).

**DEMOS theme** (when the DEMOS button is picked): teal `#38b4c8` → cream `#f4efc8` → pink `#f49ac4`, still with black and white,
taken from a floral swirl reference. Two mechanisms:
* `html.theme-demos` CSS rules restyle the DOM (pills, chips, shouts, frame SVG, error card).
* `themePass` (a final screen shader) recolours green/lime pixels of the 3D scene with that left-to-right gradient, keeping brightness.
* **Any new lime/green UI element needs a matching `.theme-demos` rule**, or it stays green in DEMOS mode.

**Reuse these, don't invent new ones:**
* `shout(text, { sub, hold, size, tilt, burst, rays, top })`: the Bangers comic shout with a jagged burst. All calibration text goes through it.
* `cutShouts()`: clears the current shout so the next one lands on Python's beat. Call it before a timed shout instead of delaying.
* `burstSVG()`, `.pill` (glossy buttons: `.mc` green, `.rb` white/grey), `.chip` (`.active`, `.done`), the black tag with the lime offset shadow
  (`bg-black ... shadow-[4px_4px_0_#9be22c]`), `.errbox`.
* Scene building: `pop(tag, obj)` / `reveal(tag)` to pop objects in by group; `moveCam(pos, look, dur)` for camera moves; `RibbonArrow` for the 3D arrows.

**Rules the user asked for:**
* Show only real values from Python. **Don't invent data or buttons.** Reuse existing buttons (e.g. RUN IT BACK) rather than adding new ones.
* Shouts must never wait for or stack on each other. Python often prints a result line and the next step's "3" on the same beat; show the "3"
  (fold the result into its subtitle if needed) rather than a banner that gets cut instantly.
* Keep the centre of the screen clear while the arrow curls and the camera flies to the stage (swing-1 countdown uses `top: true`, smaller).
* The intro arrow must burst the boombox **on contact** with its arrowhead tip, at speed (no ease-out into the target).

---

## 3. Sports demo style (`Demos/`)

### Building

* **Edit `Demos/src/index.src.html`. Never edit `Demos/index.html` by hand.** It is generated.
* `python Demos/build.py` inlines three.js (from `Demos/vendor/`) into `Demos/index.html`, so the demo works offline and when double-clicked.
* `src/index.src.html` loads `../vendor/` through an import map and needs an HTTP server (`/demos/src/index.src.html` on the GameGrip server).

### Look and feel

* Goal: instantly remind judges of playing Wii Sports, **while staying an original design**. Never use Nintendo names, logos, music, Mii assets
  or exact copies of Mii characters. Banners and the stadium are GameGrip-branded ("GAMEGRIP PARK", "SWING IT", "GRIP & RIP").
* **As little HUD as possible.** Instructions are small white text in the top-left corner (`#hud`). Big centred callouts
  (`callout('STRIKE!')`) are the only other overlay. Count and score also show on the in-stadium scoreboard.
* Bright, glossy, toy-like: ACES tone mapping, room environment reflections, soft shadows, sunny sky or lit arena.

### Characters (`makeAvatar` and helpers)

* Proportions: big head sitting right on the shoulders (no neck), A-line shirt over rounded pants, thin straight legs, small rounded feet.
* **Head** (`headGeo`): broad, slightly squared crown tapering to a rounded chin, not a sphere. Hair styles in `hairGeo`: `short`, `spiky`, `bangs`
  (pointed fringe), `flat`, `bob`, `long`, `swoop`.
* **Faces** (`drawFace` / `randomFace`): painted on the head texture: large simple eyes, soft thick brows, a small nose line, a plain mouth.
  Calm and friendly; avoid scowls, tiny eyes and toothy grins (they looked uncanny). Faces are slightly self-lit so the jaw doesn't go dark.
* **Arms** (`armGeo`): one skinned tube per arm (shoulder + elbow bones), **shirt-coloured all the way, hands included**. No visible shoulder
  or elbow joints. Arms are posed with `reach(arm, target, pole)` (two-bone IK) or by rotating `arm.sh` / `arm.el`.
* **Boxers are fixed**, not random (`BOXER_LOOKS`): blue = peach skin, dark-brown pointed bangs, determined brows, flat mouth;
  red = tan skin, short black crop, big open smile. They are modelled on the user's reference characters. Don't randomise them.
* **Crowds** are instanced mini-avatars (`makeCrowd`) placed on evenly spaced seat rings (`seatRows`) so nobody overlaps.

### Gameplay knobs (top of each game in the source)

* `CFG`: `RUNS_TO_WIN`, `OUTS_TO_WIN`, `BOX_HP`, `RESTART_WINDOW` (both players swing within this to restart).
* Baseball hitting: `HIT_WINDOW` (±0.11 s), `SWING_TO_CONTACT`. Early swing pulls left, on time goes up the middle, late goes right (right-handed batter).
* Base running: `MASH_SPEED`, `BASE_SNAP` (runner stops on a bag within 1.5 m), `LEAVE_PRESSES` + `BASE_DWELL` (must mash again to leave a base).

### Input (strap + keyboard)

* Everything ends in `GameGrip.swing(player, intensity, lead)`. Keys: `G`/`F` = player 1, `V`/`J` = player 2, `T` switches games.
* Strap swings arrive as SSE `swing` events → `GameGrip.deviceSwing(device)` → the `GRIP.devices` table picks the player per game
  (`esp1` = batter / red boxer). The other player stays on the keyboard.
* The strap turns on automatically when `/api/status` reports `listening`, or when a `listening` event arrives.
* `GRIP.swingLead` = seconds between detection and bat-on-ball (the detector fires mid-swing). Tune with the real strap.
* **Two straps (planned):** give the server one Session per serial port with device ids `esp1` / `esp2`. `GRIP.devices.esp2` already maps to pitcher / blue boxer.

---

## 4. Testing tips

* The demo exposes `window.__games` (`baseball` / `boxing`, each with `update(dt)`, `press(p)`, `reset()`, and baseball's `dbg`) and
  `window.__makeAvatar` for headless checks: step `update(1/60)` in a loop instead of waiting for frames.
* The site runs on GSAP; if the browser tab is hidden, drive it with `setInterval(() => gsap.ticker.tick(), 16)`.
* To test without an ESP32, run `gamegrip_server`'s `Handler` on a spare port with a fake `Session` subclass whose `_run()` sends the
  detector's lines through `parse_line()` with the real sleeps (`dtw.REST_SECONDS`, `dtw.SWING_WINDOW_SECONDS`, 1 s per count).
* Restart `gamegrip_server.py` after changing it. An old process keeps serving old code (this caused a `/demos/` 404 once).

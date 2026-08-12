#!/usr/bin/env bash
#
# screenshot_wayland_app.sh
#
# Headlessly runs a GTK4/libadwaita app under a real (non-kiosk) Wayland
# compositor and captures a screenshot of it floating, un-maximized, on a
# configurable-color desktop -- entirely offscreen, no GPU, no attached
# display.
#
# ============================================================================
# EXPERIMENT LOG -- what was tried, what failed, what finally worked
# ============================================================================
#
# GOAL: screenshot a GTK app on a virtual Wayland display, with the app's
# window floating (not maximized/fullscreen) against a background you can
# see and color.
#
# ATTEMPT 1: cage (wlroots kiosk compositor) + headless backend + grim
#   - cage is a *kiosk* compositor: it always resizes its single client to
#     fill the entire output. There is no windowed/floating mode -- checked
#     `cage -h`, only flags are -d (no decorations), -m (output selection),
#     -s (VT switching). So "make the screen bigger than the window" is
#     structurally impossible with cage; the app IS the screen.
#   - Getting even this far took two fixes:
#       * default GL/Zink renderer crashed (`VK_ERROR_INCOMPATIBLE_DRIVER`,
#         no /dev/dri in this sandbox) -> fixed with WLR_RENDERER=pixman
#         (wlroots software renderer) + GDK_DEBUG=gl-disable + 
#         GSK_RENDERER=cairo (GTK's own software rasterizer).
#       * grim worked here because cage is wlroots-based and implements the
#         wlr-screencopy-unstable-v1 protocol.
#   - Verdict: worked for a fullscreen capture, but wrong tool once
#     "don't maximize the window" became a requirement.
#
# ATTEMPT 2: weston --backend=headless + grim
#   - Swapped to weston (a real desktop-shell compositor: floating windows,
#     panel, wallpaper) so the app's window would be free to be smaller than
#     the output.
#   - weston's headless backend happily creates a virtual output at any
#     --width/--height.
#   - grim FAILED here: "compositor doesn't support wlr-screencopy-unstable-v1".
#     wlr-screencopy is a wlroots-specific protocol; weston is not built on
#     wlroots and doesn't implement it. grim is a wlroots-ecosystem tool.
#
# ATTEMPT 3: weston --backend=headless + weston-screenshooter
#   - weston ships its own screenshot client/protocol, so tried that instead
#     of grim.
#   - FAILED: "Output capture error: unauthorized". Weston's screenshooter
#     protocol only trusts clients the compositor itself spawns (e.g. via a
#     keybinding inside the session) -- an arbitrary external client
#     connecting over the normal Wayland socket is refused. This is the same
#     underlying Wayland security boundary (a compositor won't let just any
#     process capture surfaces) enforced by a different mechanism than
#     wlroots' permission model.
#   - Verdict: correct architecture (real floating windows) but no working
#     capture path with a pure-Wayland output.
#
# CROSS-CUTTING GOTCHA discovered during attempts 2 & 3: background processes
# started with `&` -- even wrapped in `nohup` -- get reaped as soon as the
# individual shell invocation that started them ends, in this sandboxed
# execution environment. A process that was confirmed alive 5 seconds after
# launch could be completely gone by the very next command. Fix: do
# launch -> wait-for-ready -> action -> cleanup all inside ONE shell
# invocation/script, never relying on anything surviving across separate
# tool calls. This script follows that rule throughout.
#
# ATTEMPT 4 (WORKING): weston --backend=x11 nested inside Xvfb, captured via
# the existing X11 tool (ImageMagick's `import`), not via any Wayland
# screenshot protocol at all.
#   - Idea: weston's `x11` backend renders its entire virtual output as one
#     ordinary top-level X11 window on a real (if virtual, via Xvfb) X
#     display. Once that's true, it's just an X11 window like any other,
#     and X11 has no "clients can't screenshot each other" restriction --
#     any client can grab any window's pixels. So the already-proven
#     Xvfb + ImageMagick `import -window <id>` pipeline (used for the
#     ordinary, non-Wayland version of this workflow) applies unchanged.
#   - This also sidesteps the wlr-screencopy / weston-screenshooter dead
#     ends entirely -- no Wayland screenshot protocol is used or needed.
#   - Hit one more snag getting there: `xdotool search --name "..."` came
#     back completely empty even though the window definitely existed
#     (confirmed with `xwininfo -root -tree`). Root cause: `xdotool search`
#     depends on a window manager publishing EWMH `_NET_CLIENT_LIST`, and
#     plain Xvfb runs no window manager at all. Fix: skip xdotool for window
#     discovery and instead grep the window ID straight out of
#     `xwininfo -root -tree -display :N` (matching weston's fixed window
#     title "Weston Compositor - screen0").
#   - Result: reliable, repeatable capture of a floating, un-maximized GTK
#     window against a visible desktop background.
#
# BACKGROUND COLOR: weston's desktop-shell background color is set via a
# weston.ini config file, [shell] section, `background-color=0xAARRGGBB`
# (hex, alpha first). Passed to weston with `-c <file>` / `--config=<file>`.
# Verified empirically: 0xff2e3440 (a dark slate) rendered correctly on
# screen, so this is exposed here as a configurable script argument.
#
# ============================================================================


set -uo pipefail

# --------------------------------------------------------------------------
# Configuration -- edit these or override via environment/arguments
# --------------------------------------------------------------------------
APP_DIR="${APP_DIR:-$(pwd)}"               # directory containing main.py
APP_CMD="${APP_CMD:-python3 main.py}"      # command to launch the app
OUTPUT_WIDTH="${OUTPUT_WIDTH:-1920}"       # virtual "screen" size -- make
OUTPUT_HEIGHT="${OUTPUT_HEIGHT:-1080}"     # this bigger than the app's window
BG_COLOR="${BG_COLOR:-0xff808080}"         # 0xAARRGGBB, weston background-color
OUTPUT_PNG="${OUTPUT_PNG:-$APP_DIR/weston_output.png}"
APP_STARTUP_WAIT="${APP_STARTUP_WAIT:-5.0}"  # seconds to let the app draw and render
WESTON_SOCKET="${WESTON_SOCKET:-wayland-shot-$$}"

# Find an available X11 display number dynamically if default 93 is taken
XVFB_DISPLAY_NUM="${XVFB_DISPLAY_NUM:-}"
if [ -z "$XVFB_DISPLAY_NUM" ]; then
  for d in $(seq 90 299); do
    if [ ! -f "/tmp/.X${d}-lock" ] && [ ! -S "/tmp/.X11-unix/X${d}" ]; then
      XVFB_DISPLAY_NUM="$d"
      break
    fi
  done
fi
XVFB_DISPLAY_NUM="${XVFB_DISPLAY_NUM:-93}"

# --------------------------------------------------------------------------
# Setup: isolated runtime dirs so repeated runs never collide
# --------------------------------------------------------------------------
echo "[Screenshot Tool] Starting Wayland app screenshot workflow..."
WORKDIR="$(mktemp -d)"
XDG_RUNTIME_DIR="$WORKDIR/xdg-runtime"
mkdir -p "$XDG_RUNTIME_DIR" && chmod 0700 "$XDG_RUNTIME_DIR"
export XDG_RUNTIME_DIR
echo "[Screenshot Tool] Created isolated runtime directory: $XDG_RUNTIME_DIR"

echo "[Screenshot Tool] Generating transparent Xcursor theme..."
python3 -c "
import struct
from pathlib import Path

cursor_dir = Path('$WORKDIR/icons/transparent_theme/cursors')
cursor_dir.mkdir(parents=True, exist_ok=True)

magic, header_size, version, ntoc = b'Xcur', 16, 1, 1
chunk_type, chunk_subtype, chunk_version, chunk_header_size = 0xfffd0002, 18, 1, 36
w, h, xhot, yhot, delay = 18, 18, 0, 0, 0
pixels = b'\x00' * (18 * 18 * 4)

toc_pos = 16 + 12
header = struct.pack('<4sIII', magic, header_size, version, ntoc)
toc = struct.pack('<III', chunk_type, chunk_subtype, toc_pos)
chunk_hdr = struct.pack('<IIIIIIIII', chunk_header_size, chunk_type, chunk_subtype, chunk_version, w, h, xhot, yhot, delay)
xcursor_bytes = header + toc + chunk_hdr + pixels

for cursor_name in ['left_ptr', 'default', 'pointer', 'hand2', 'arrow', 'cross', 'ibeam', 'top_left_arrow']:
    (cursor_dir / cursor_name).write_bytes(xcursor_bytes)
"

CHOSEN_CURSOR_THEME="transparent_theme"
if [ "${PDFATLAS_HIDE_CURSOR:-1}" = "0" ]; then
  CHOSEN_CURSOR_THEME="Adwaita"
fi

export XCURSOR_PATH="$WORKDIR/icons:/usr/share/icons"
export XCURSOR_THEME="$CHOSEN_CURSOR_THEME"
export XCURSOR_SIZE="18"

WESTON_INI="$WORKDIR/weston.ini"
cat > "$WESTON_INI" << EOF
[core]
shell=desktop-shell.so

[shell]
background-color=$BG_COLOR
panel-position=none
cursor-theme=$CHOSEN_CURSOR_THEME
cursor-size=18
EOF

XVFB_LOG="$WORKDIR/xvfb.log"
WESTON_LOG="$WORKDIR/weston.log"
APP_LOG="$WORKDIR/app.log"

# --------------------------------------------------------------------------
# Check for pure wlroots compositor (labwc) + grim
# --------------------------------------------------------------------------
USE_LABWC=0
if command -v labwc >/dev/null 2>&1 && command -v grim >/dev/null 2>&1; then
  USE_LABWC=1
fi

if [ "$USE_LABWC" = "1" ]; then
  echo "[Screenshot Tool] Using pure headless wlroots compositor (labwc + grim)..."
  export WLR_BACKENDS=headless
  export WLR_RENDERER=pixman
  export WLR_HEADLESS_OUTPUTS=1

  labwc -s true > "$WORKDIR/labwc.log" 2>&1 &
  COMPOSITOR_PID=$!
  sleep 1.5

  WAYLAND_SOCK=$(ls -t "$XDG_RUNTIME_DIR"/wayland-* 2>/dev/null | grep -v '\.lock$' | head -1)
  if [ -z "$WAYLAND_SOCK" ]; then
    echo "ERROR: labwc failed to create wayland socket." >&2
    cat "$WORKDIR/labwc.log" >&2
    exit 1
  fi
  WAYLAND_SOCKET_NAME=$(basename "$WAYLAND_SOCK")
  echo "[Screenshot Tool] labwc running on Wayland socket: $WAYLAND_SOCKET_NAME (PID: $COMPOSITOR_PID)"

  if command -v swaybg >/dev/null 2>&1; then
    WAYLAND_DISPLAY="$WAYLAND_SOCKET_NAME" swaybg -c "#808080" > "$WORKDIR/swaybg.log" 2>&1 &
    SWAYBG_PID=$!
    sleep 0.5
  fi

  cleanup() {
    echo "[Screenshot Tool] Beginning teardown..." >&2
    if [ -n "${APP_PID:-}" ]; then
      echo "[Screenshot Tool] Terminating application (PID: $APP_PID)..." >&2
      kill "$APP_PID" 2>/dev/null || true
    fi
    if [ -n "${SWAYBG_PID:-}" ]; then
      echo "[Screenshot Tool] Terminating swaybg (PID: $SWAYBG_PID)..." >&2
      kill "$SWAYBG_PID" 2>/dev/null || true
    fi
    if [ -n "${COMPOSITOR_PID:-}" ]; then
      echo "[Screenshot Tool] Terminating labwc compositor (PID: $COMPOSITOR_PID)..." >&2
      kill "$COMPOSITOR_PID" 2>/dev/null || true
    fi
    if [ -d "${WORKDIR:-}" ]; then
      echo "[Screenshot Tool] Removing temporary work directory ($WORKDIR)..." >&2
      rm -rf "$WORKDIR" 2>/dev/null || true
    fi
    echo "[Screenshot Tool] Teardown complete." >&2
  }
  trap cleanup EXIT

  echo "[Screenshot Tool] Launching application command: '$APP_CMD'..."
  (
    cd "$APP_DIR"
    export WAYLAND_DISPLAY="$WAYLAND_SOCKET_NAME"
    export GDK_BACKEND=wayland
    export GSK_RENDERER=cairo
    export PDFATLAS_HIDE_CURSOR="${PDFATLAS_HIDE_CURSOR:-1}"
    eval exec "$APP_CMD"
  ) > "$APP_LOG" 2>&1 &
  APP_PID=$!

  echo "[Screenshot Tool] Application process spawned (PID: $APP_PID), waiting ${APP_STARTUP_WAIT}s for window render..."
  sleep "$APP_STARTUP_WAIT"
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "ERROR: app failed to start / crashed. Log:" >&2
    cat "$APP_LOG" >&2
    exit 1
  fi
  echo "[Screenshot Tool] Application process alive and running."

  SANDBOX_DIR="$APP_DIR/sandbox.local"
  mkdir -p "$SANDBOX_DIR"

  OUTPUT_BASENAME=$(basename "$OUTPUT_PNG")
  STEM="${OUTPUT_BASENAME%.*}"

  STEP1_RAW="$SANDBOX_DIR/${STEM}_1_raw.png"
  STEP2_TRIMMED="$SANDBOX_DIR/${STEM}_2_trimmed.png"
  STEP3_MASK="$SANDBOX_DIR/${STEM}_3_ui_mask.png"
  STEP4_UNBLENDED="$SANDBOX_DIR/${STEM}_4_unblended.png"

  echo "[Screenshot Tool] Step 1: Capturing pure Wayland frame via grim..."
  WAYLAND_DISPLAY="$WAYLAND_SOCKET_NAME" grim "$STEP1_RAW"

else
  echo "[Screenshot Tool] Falling back to Weston + Xvfb pipeline..."
  # --------------------------------------------------------------------------
  # 1. Xvfb: the underlying (virtual) X11 display that weston's x11 backend
  #    will open a window on.
  # --------------------------------------------------------------------------
  echo "[Screenshot Tool] Launching Xvfb display :$XVFB_DISPLAY_NUM (${OUTPUT_WIDTH}x${OUTPUT_HEIGHT}x24)..."
  Xvfb ":$XVFB_DISPLAY_NUM" -screen 0 "${OUTPUT_WIDTH}x${OUTPUT_HEIGHT}x24" > "$XVFB_LOG" 2>&1 &
  XVFB_PID=$!
  sleep 1
  export DISPLAY=":$XVFB_DISPLAY_NUM"
  if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "ERROR: Xvfb failed to start. Log:" >&2
    cat "$XVFB_LOG" >&2
    exit 1
  fi
  echo "[Screenshot Tool] Xvfb server running (PID: $XVFB_PID, DISPLAY: :$XVFB_DISPLAY_NUM)"

  # --------------------------------------------------------------------------
  # 2. weston, x11 backend
  # --------------------------------------------------------------------------
  echo "[Screenshot Tool] Launching Weston compositor (socket: $WESTON_SOCKET, size: ${OUTPUT_WIDTH}x${OUTPUT_HEIGHT})...."
  weston --backend=x11 --width="$OUTPUT_WIDTH" --height="$OUTPUT_HEIGHT" \
    --socket="$WESTON_SOCKET" -c "$WESTON_INI" > "$WESTON_LOG" 2>&1 &
  WESTON_PID=$!

  for _ in $(seq 1 25); do
    [ -S "$XDG_RUNTIME_DIR/$WESTON_SOCKET" ] && break
    sleep 0.2
  done
  sleep 1
  if ! kill -0 "$WESTON_PID" 2>/dev/null; then
    echo "ERROR: weston failed to start. Log:" >&2
    cat "$WESTON_LOG" >&2
    exit 1
  fi
  echo "[Screenshot Tool] Weston compositor running (PID: $WESTON_PID)"

  cleanup() {
    echo "[Screenshot Tool] Beginning teardown..." >&2
    if [ -n "${APP_PID:-}" ]; then
      echo "[Screenshot Tool] Terminating application (PID: $APP_PID)..." >&2
      pkill -P "$APP_PID" 2>/dev/null || true
      kill "$APP_PID" 2>/dev/null || true
    fi
    if [ -n "${WESTON_PID:-}" ]; then
      echo "[Screenshot Tool] Terminating Weston compositor (PID: $WESTON_PID)..." >&2
      pkill -P "$WESTON_PID" 2>/dev/null || true
      kill "$WESTON_PID" 2>/dev/null || true
    fi
    if [ -n "${XVFB_PID:-}" ]; then
      echo "[Screenshot Tool] Terminating Xvfb server (PID: $XVFB_PID)..." >&2
      pkill -P "$XVFB_PID" 2>/dev/null || true
      kill "$XVFB_PID" 2>/dev/null || true
    fi
    rm -f "/tmp/.X${XVFB_DISPLAY_NUM}-lock" 2>/dev/null || true
    if [ -d "${WORKDIR:-}" ]; then
      echo "[Screenshot Tool] Removing temporary work directory ($WORKDIR)..." >&2
      rm -rf "$WORKDIR" 2>/dev/null || true
    fi
    echo "[Screenshot Tool] Teardown complete." >&2
  }
  trap cleanup EXIT

  echo "[Screenshot Tool] Launching application command: '$APP_CMD'..."
  (
    cd "$APP_DIR"
    export WAYLAND_DISPLAY="$WESTON_SOCKET"
    export GDK_BACKEND=wayland
    export GSK_RENDERER=cairo
    export PDFATLAS_HIDE_CURSOR="${PDFATLAS_HIDE_CURSOR:-1}"
    eval exec "$APP_CMD"
  ) > "$APP_LOG" 2>&1 &
  APP_PID=$!

  echo "[Screenshot Tool] Application process spawned (PID: $APP_PID), waiting ${APP_STARTUP_WAIT}s for window render..."
  sleep "$APP_STARTUP_WAIT"
  if ! kill -0 "$APP_PID" 2>/dev/null; then
    echo "ERROR: app failed to start / crashed. Log:" >&2
    cat "$APP_LOG" >&2
    exit 1
  fi
  echo "[Screenshot Tool] Application process alive and running."

  EXACT_COORDS=$(grep -oE 'NOTE_ICON_EXACT_COORDS: [0-9]+,[0-9]+' "$APP_LOG" | tail -1 | cut -d' ' -f2 || true)
  if [ -n "$EXACT_COORDS" ] && [ "${OVERRIDE_CURSOR_COORDS:-0}" = "0" ]; then
    CURSOR_X=$(echo "$EXACT_COORDS" | cut -d',' -f1)
    CURSOR_Y=$(echo "$EXACT_COORDS" | cut -d',' -f2)
    echo "[Screenshot Tool] Auto-detected note icon exact coordinates: ($CURSOR_X, $CURSOR_Y)"
  else
    CURSOR_X="${CURSOR_X:-960}"
    CURSOR_Y="${CURSOR_Y:-540}"
  fi

  export CURSOR_X="$CURSOR_X"
  export CURSOR_Y="$CURSOR_Y"

  echo "[Screenshot Tool] Moving mouse cursor to ($CURSOR_X, $CURSOR_Y)..."
  DISPLAY=":$XVFB_DISPLAY_NUM" xdotool mousemove "$CURSOR_X" "$CURSOR_Y" 2>/dev/null || true
  sleep 0.2

  echo "[Screenshot Tool] Locating Weston X11 window..."
  WIN_ID=$(xwininfo -root -tree -display ":$XVFB_DISPLAY_NUM" 2>/dev/null \
    | grep "Weston Compositor" | grep -oE '0x[0-9a-f]+' | head -1)

  if [ -z "$WIN_ID" ]; then
    echo "ERROR: could not find weston's X11 window. xwininfo output:" >&2
    xwininfo -root -tree -display ":$XVFB_DISPLAY_NUM" >&2
    exit 1
  fi
  echo "[Screenshot Tool] Found Weston X11 window ID: $WIN_ID"

  SANDBOX_DIR="$APP_DIR/sandbox.local"
  mkdir -p "$SANDBOX_DIR"

  OUTPUT_BASENAME=$(basename "$OUTPUT_PNG")
  STEM="${OUTPUT_BASENAME%.*}"

  STEP1_RAW="$SANDBOX_DIR/${STEM}_1_raw.png"
  STEP2_TRIMMED="$SANDBOX_DIR/${STEM}_2_trimmed.png"
  STEP3_MASK="$SANDBOX_DIR/${STEM}_3_ui_mask.png"
  STEP4_UNBLENDED="$SANDBOX_DIR/${STEM}_4_unblended.png"

  echo "[Screenshot Tool] Step 1: Capturing raw Weston window screenshot..."
  import -window "$WIN_ID" "$STEP1_RAW"
fi

python3 -c "
import os
from PIL import Image, ImageDraw

if os.environ.get('PDFATLAS_HIDE_CURSOR') == '0':
    cx = int(os.environ.get('CURSOR_X', '960'))
    cy = int(os.environ.get('CURSOR_Y', '540'))

    img = Image.open('$STEP1_RAW').convert('RGBA')
    draw = ImageDraw.Draw(img)

    pointer = [
        (cx, cy),
        (cx, cy + 18),
        (cx + 4, cy + 14),
        (cx + 7, cy + 20),
        (cx + 10, cy + 19),
        (cx + 7, cy + 13),
        (cx + 12, cy + 13),
    ]
    draw.polygon(pointer, fill='black')
    inner_pointer = [
        (cx + 1, cy + 2),
        (cx + 1, cy + 15),
        (cx + 4, cy + 12),
        (cx + 7, cy + 18),
        (cx + 8, cy + 17.5),
        (cx + 5.5, cy + 11.5),
        (cx + 10, cy + 11.5),
    ]
    draw.polygon(inner_pointer, fill='white')
    img.save('$STEP1_RAW')
"

MAGICK_CMD="magick"
command -v magick >/dev/null 2>&1 || MAGICK_CMD="convert"
BG_HEX="#${BG_COLOR:4:6}"

echo "[Screenshot Tool] Step 2: Post-processing (trim background + add 32px padding)..."
$MAGICK_CMD "$STEP1_RAW" -bordercolor "$BG_HEX" -fuzz 1% -trim +repage -bordercolor "$BG_HEX" -border 32 "$STEP2_TRIMMED"

echo "[Screenshot Tool] Step 3 & 4: Flood-fill background unblending (fuzz = 35)..."
python3 -c "
from collections import deque
from PIL import Image
import numpy as np

img = Image.open('$STEP2_TRIMMED').convert('RGBA')
arr = np.array(img, dtype=np.int32)
h, w, _ = arr.shape

bg_color = arr[0, 0, :3]
bg_val = 128.0

# 1. Calculate tolerance mask (fuzz = 35)
diff_from_bg = np.max(np.abs(arr[:, :, :3] - bg_color), axis=2)
can_flood = diff_from_bg <= 35

# 2. Pure Python BFS flood-fill from 4 corners
bg_mask = np.zeros((h, w), dtype=bool)
seeds = [(0, 0), (0, w - 1), (h - 1, 0), (h - 1, w - 1)]
queue = deque()

for r, c in seeds:
    if can_flood[r, c] and not bg_mask[r, c]:
        bg_mask[r, c] = True
        queue.append((r, c))

while queue:
    r, c = queue.popleft()
    for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
        nr, nc = r + dr, c + dc
        if 0 <= nr < h and 0 <= nc < w:
            if can_flood[nr, nc] and not bg_mask[nr, nc]:
                bg_mask[nr, nc] = True
                queue.append((nr, nc))

ui_mask = ~bg_mask

# Save Step 3 Mask visualizer
Image.fromarray((bg_mask * 255).astype(np.uint8), 'L').save('$STEP3_MASK')

out = np.zeros((h, w, 4), dtype=np.uint8)

# 3. Window UI region -> 100% opaque original RGB
out[ui_mask, :3] = arr[ui_mask, :3].astype(np.uint8)
out[ui_mask, 3] = 255

# 4. Background & drop-shadow region -> unblend against bg_val = 128
val = np.mean(arr[bg_mask, :3], axis=1)
a_shadow = np.clip(1.0 - (val / bg_val), 0.0, 1.0)
out[bg_mask, 0] = 0
out[bg_mask, 1] = 0
out[bg_mask, 2] = 0
out[bg_mask, 3] = (a_shadow * 255.0).astype(np.uint8)

res_img = Image.fromarray(out, 'RGBA')
res_img.save('$STEP4_UNBLENDED', 'PNG')
res_img.save('$OUTPUT_PNG', 'PNG')
"

echo "[Screenshot Tool] Step-by-step debug screenshots saved to: $SANDBOX_DIR"
echo "[Screenshot Tool] Final screenshot saved to: $OUTPUT_PNG"
echo "--- app log ---"
cat "$APP_LOG"

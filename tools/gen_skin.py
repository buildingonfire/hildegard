#!/usr/bin/env python3
"""Generate Hildegard's hand-written MPC skin: skins/TUI.json, skins/Q-Links.json and PNGs.

    python tools/gen_skin.py   (from the hildegard repo root)

Layout source of truth: docs/UI-LAYOUT.md (locked 2026-11-14). Parameter indices: SPEC.md
Section 5 + src/Hildegard.cpp. Colours: SPEC 7.5 (a final colour pass is still to come; change
the palette block below and rerun). Format reference: the SDK's docs/ui-reference.md.

Pure standard library (zlib PNG writer, 4x supersampled shapes). The output in skins/ is
committed so the SDK build can copy it verbatim; rerun this script after any layout change.

Design notes
- Three tabs: F1 RECORD (landing), F2 LOOP, F3 MIX. The waveform band (one custom component,
  "waveBand") sits at the top of every tab.
- Waveform: 128 bars, each drawn twice (a Meter growing up from the centre line and one
  growing down) bound to wave000-127. Loop spans (Start + Length, wrapping past the take's
  end) come from display params 175-194: piece A coloured 0..endA, masked 0..start in the band
  colour, then piece B 0..endB. Small stream-coloured brackets stand on each span's start
  and end, like a DAW's loop brackets. Head markers are TrackAndThumb sliders bound to
  pos1-4 with no track.
  A transparent "shield" component on top swallows touches so nothing in the band is editable.
- Muted streams: every stream-coloured element exists twice, a lit and a dim copy, and
  IndexedEnabling on Mute N shows exactly one of them. The playhead marker has no dim copy:
  a muted stream shows none.
- Parameter-name captions are fixed Text labels placed over the slots (labels never take
  touches), so each slot reads "SPEED", not "SPEED 1".
"""

import json
import os
import struct
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "skins")

# ---------------------------------------------------------------------------
# Palette (SPEC 7.5). AARRGGBB strings for JSON, RGB tuples for PNGs.
# ---------------------------------------------------------------------------
STREAM_RGB = [(0xEF, 0x9F, 0x27), (0xFA, 0xC7, 0x75), (0x7F, 0xB3, 0x3A), (0x4A, 0x9B, 0xE8)]
BG = (0x16, 0x16, 0x18)
PANEL = (0x22, 0x22, 0x26)
PANEL_FOCUS = (0x2A, 0x2A, 0x30)
LINE = (0x3A, 0x3A, 0x40)
TRACK = (0x2C, 0x2C, 0x31)
BAND = (0x1C, 0x1C, 0x1F)
WAVE = (0x76, 0x76, 0x7E)
MUTED_BTN = (0x55, 0x55, 0x5C)
TEXT = (0xEC, 0xEC, 0xEE)
TEXT_DIM = (0x8A, 0x8A, 0x90)
TEXT_DIMMER = (0x5E, 0x5E, 0x64)
DARK_TEXT = (0x16, 0x16, 0x18)
ERROR = (0xE2, 0x4B, 0x4A)
LIT = (0xEC, 0xE7, 0xDE)          # lit global buttons (Record on, Resample on): colour pass TBD
NEUTRAL_BAR = (0xD6, 0xD3, 0xCC)  # Dry/Wet bar


def mix(a, b, t):
    return tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


STREAM_DIM = [mix(PANEL, c, 0.38) for c in STREAM_RGB]


def argb(rgb, alpha=0xFF):
    return "%02x%02x%02x%02x" % (alpha, rgb[0], rgb[1], rgb[2])


CLEAR = "00000000"

# ---------------------------------------------------------------------------
# Parameter indices (SPEC 5; must match src/Hildegard.cpp)
# ---------------------------------------------------------------------------
P_DRYWET, P_RECORD, P_RESAMPLE, P_SAVE = 0, 1, 2, 4   # Dry/Wet is 0 (host nudges param 0); 3 = retired Fixed Loop Length
O_SPEED, O_TONE, O_PAN, O_VOLUME, O_START, O_LENGTH, O_MODE, O_MUTE = range(8)   # Start/Length were In/Out


def sp(s, off):
    return 5 + s * 8 + off


P_POS0, P_STATUS, P_WAVE0, N_WAVE = 37, 41, 42, 128
P_SCALE = 170                     # Speed Scale, appended after the display params
SCALE_CHOICES = ["Octaves", "Ratios", "Smooth"]
P_SPDBAR0 = 171                   # 171-174: snapped Speed bar positions (display)
SNAPPED = ["RangedEnabling/0/1/3/Parameter 170"]       # Speed Ratios = Octaves or Ratios
SMOOTH = ["IndexedEnabling/2/3/Parameter 170"]
STATUS_CHOICES = ["Press Record", "Ready", "Armed", "Recording", "Saving", "Saved", "Save failed", "Buffer full"]
# Span drawing (display, 5 per stream; src/Hildegard.cpp kSpan0 / SpanOff / kChev*).
P_SPAN0, SPAN_PER = 175, 5
SP_START, SP_ENDA, SP_ENDB, SP_ENDPOS, SP_CHEV = range(5)
CHEV_W = 6          # span-end bracket width in px (SP_CHEV is unused since 2026-11-16)


def span_p(s, off):
    return P_SPAN0 + s * SPAN_PER + off


def P(i):
    return "Parameter %d" % i


# ---------------------------------------------------------------------------
# Geometry (content-area coordinates, 1280 x 628). docs/UI-LAYOUT.md
# ---------------------------------------------------------------------------
BAND_RECT = (20, 8, 1240, 182)
COL_X = [20, 333, 646, 959]
COL_W = 300
NAME_Y, NAME_H = 206, 46
SLOT_Y = [258, 348, 438, 528]
SLOT_H = 84

# Mode slot: three buttons across the 268 px inner width.
MODE_BW = 84
MODE_BX = [16, 108, 200]
# Speed Ratios tile: the whole right column of RECORD (Fixed Loop Length was above it until
# 2026-11-16). Three tall radio buttons, each with its name and a one-line description.
SCALE_TILE_H = 406
SCALE_BW, SCALE_BH = 276, 104
SCALE_BY = [56, 172, 288]
SCALE_DESC = ["1/4, 1/2, 1, 2, 4x", "Adds fifths: 1/3, 2/3, 3/2, 3x", "Any speed, no snapping"]
DRYWET_W = COL_X[3] - 13 - 20    # Dry/Wet fills the rest of the bottom row

# Inside the band
BAR_X0, BAR_X1 = 10.0, 1230.0
BAR_W = 7
BAR_TOP, BAR_MID, BAR_BOT = 12, 70, 128
SPAN_Y = [132, 142, 152, 162]
SPAN_H = 8          # 4 px drew nothing on device (ColourBar outline/inset eats the fill)
SPAN_INSET = 2      # measured on device: a ColourBar fills x+2 .. x+2+v*(w-4), rows 2..5 of 8
CHEV_RISE = 10      # chevrons stand this many px above the span's visible top row
CHEV_H = CHEV_RISE + 1   # image height: the rise plus 1 row overlapping the span (no gap)
MASK_PAD = 1        # wave masks overhang each bar by 1 px a side (covers sub-pixel edges)

# ---------------------------------------------------------------------------
# PNG writing (4x supersampled, premultiplied box filter)
# ---------------------------------------------------------------------------
SS = 4


class Canvas:
    def __init__(self, w, h, fill=None):
        self.w, self.h = w, h
        self.W, self.H = w * SS, h * SS
        px = (fill + (255,)) if fill else (0, 0, 0, 0)
        self.px = [list(px) for _ in range(self.W * self.H)]

    def _set(self, x, y, col):
        if 0 <= x < self.W and 0 <= y < self.H:
            self.px[y * self.W + x] = [col[0], col[1], col[2], 255]

    def shape(self, x0, y0, x1, y1, inside, col):
        """Paint every subpixel in the (output-pixel) bbox whose centre satisfies inside(x, y)."""
        X0, Y0 = max(0, int(x0 * SS)), max(0, int(y0 * SS))
        X1, Y1 = min(self.W, int(x1 * SS + 1)), min(self.H, int(y1 * SS + 1))
        for Y in range(Y0, Y1):
            y = (Y + 0.5) / SS
            for X in range(X0, X1):
                if inside((X + 0.5) / SS, y):
                    self._set(X, Y, col)

    def rect(self, x0, y0, x1, y1, col):
        self.shape(x0, y0, x1, y1, lambda x, y: x0 <= x < x1 and y0 <= y < y1, col)

    def rrect(self, x0, y0, x1, y1, r, col):
        def inside(x, y):
            if not (x0 <= x < x1 and y0 <= y < y1):
                return False
            cx = min(max(x, x0 + r), x1 - r)
            cy = min(max(y, y0 + r), y1 - r)
            return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
        self.shape(x0, y0, x1, y1, inside, col)

    def circle(self, cx, cy, r, col):
        self.shape(cx - r, cy - r, cx + r, cy + r, lambda x, y: (x - cx) ** 2 + (y - cy) ** 2 <= r * r, col)

    def ring(self, cx, cy, r, t, col):
        self.shape(cx - r, cy - r, cx + r, cy + r,
                   lambda x, y: (r - t) ** 2 <= (x - cx) ** 2 + (y - cy) ** 2 <= r * r, col)

    def line(self, ax, ay, bx, by, t, col):
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy or 1e-9
        h = t / 2.0

        def inside(x, y):
            u = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / ll))
            px, py = ax + u * dx, ay + u * dy
            return (x - px) ** 2 + (y - py) ** 2 <= h * h
        self.shape(min(ax, bx) - h, min(ay, by) - h, max(ax, bx) + h, max(ay, by) + h, inside, col)

    def tri(self, p0, p1, p2, col):
        def edge(a, b, x, y):
            return (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0])

        def inside(x, y):
            e0, e1, e2 = edge(p0, p1, x, y), edge(p1, p2, x, y), edge(p2, p0, x, y)
            return (e0 >= 0 and e1 >= 0 and e2 >= 0) or (e0 <= 0 and e1 <= 0 and e2 <= 0)
        xs, ys = [p0[0], p1[0], p2[0]], [p0[1], p1[1], p2[1]]
        self.shape(min(xs), min(ys), max(xs), max(ys), inside, col)

    def save(self, name):
        rows = []
        n = SS * SS
        for y in range(self.h):
            row = bytearray([0])
            for x in range(self.w):
                r = g = b = a = 0
                for sy in range(SS):
                    base = (y * SS + sy) * self.W + x * SS
                    for sx in range(SS):
                        p = self.px[base + sx]
                        pa = p[3]
                        r += p[0] * pa
                        g += p[1] * pa
                        b += p[2] * pa
                        a += pa
                if a:
                    row += bytes((r // a, g // a, b // a, a // n))
                else:
                    row += b"\0\0\0\0"
            rows.append(bytes(row))
        write_png(os.path.join(OUT, name), self.w, self.h, rows)
        return name


def write_png(path, w, h, rows):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(rows)
    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


def solid_png(name, w, h, rgb):
    """Fast path for solid rectangles (bars, markers): no supersampling needed."""
    row = b"\0" + bytes((rgb[0], rgb[1], rgb[2], 255)) * w
    write_png(os.path.join(OUT, name), w, h, [row] * h)
    return name


def bar_x(i):
    """Left edge of waveform bar i in waveBand coordinates (fractional)."""
    pitch = (BAR_X1 - BAR_X0) / N_WAVE
    return BAR_X0 + i * pitch + (pitch - BAR_W) / 2.0


def wave_background_png(name):
    """The waveBand's background: band colour with all bars at full height, horizontally
    anti-aliased at their fractional positions (the same positions as the masks and thumbs)."""
    w, h = BAND_RECT[2], BAND_RECT[3]
    cover = [0.0] * w
    for i in range(N_WAVE):
        a = bar_x(i); b = a + BAR_W
        for x in range(int(a), min(w, int(b) + 1)):
            cover[x] += max(0.0, min(b, x + 1) - max(a, x))
    bar_row = b"\0" + b"".join(bytes(tuple(int(round(BAND[c] + (WAVE[c] - BAND[c]) * min(1.0, k))) for c in range(3)) + (255,))
                               for k in cover)
    band_row = b"\0" + bytes(BAND + (255,)) * w
    rows = [bar_row if BAR_TOP <= y < BAR_BOT else band_row for y in range(h)]
    write_png(os.path.join(OUT, name), w, h, rows)
    return name


def clear_png(name, w, h):
    write_png(os.path.join(OUT, name), w, h, [b"\0" + b"\0\0\0\0" * w] * h)
    return name


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
def make_images():
    imgs = {}
    half = BAR_MID - BAR_TOP
    # Waveform (2026-11-16, lit-bar playheads): the band's background image draws every bar at
    # full height; a playhead's bar is lit by a stream-coloured thumb over it; band-coloured
    # meter masks then cut each bar down to its height (wave params are published inverted).
    imgs["wave_bg"] = wave_background_png("wave_bg.png")
    imgs["mask"] = solid_png("wave_mask.png", BAR_W + 2 * MASK_PAD, half, BAND)
    imgs["mask_off"] = clear_png("wave_mask_off.png", BAR_W + 2 * MASK_PAD, half)
    imgs["hit"] = clear_png("hit_clear.png", COL_W, NAME_H)   # invisible touch target over labels

    for s in range(4):
        imgs["lit%d" % s] = solid_png("litbar_s%d.png" % (s + 1), BAR_W, BAR_BOT - BAR_TOP, STREAM_RGB[s])

        c = Canvas(COL_W, NAME_H)
        c.rrect(0, 0, COL_W, NAME_H, 7, STREAM_RGB[s])
        imgs["name%d" % s] = c.save("name_s%d.png" % (s + 1))
    c = Canvas(COL_W, NAME_H)
    c.rrect(0, 0, COL_W, NAME_H, 7, MUTED_BTN)
    imgs["name_muted"] = c.save("name_muted.png")

    # Mode buttons: forward loop, reverse loop, ping-pong; MODE_BW x 36.
    MW, MH = MODE_BW, 36

    def mode_btn(kind, bg, fg):
        c = Canvas(MW, MH)
        c.rrect(0, 0, MW, MH, 6, bg)
        cx, cy = MW / 2.0, MH / 2.0
        if kind in ("fwd", "rev"):
            # A closed loop (stadium outline) with an arrowhead on the top run: right = forward.
            x0, x1, y0, y1, t = cx - 22, cx + 22, cy - 9, cy + 9, 2.6
            c.rrect(x0, y0, x1, y1, 9, fg)
            c.rrect(x0 + t, y0 + t, x1 - t, y1 - t, 9 - t, bg)
            if kind == "fwd":
                c.tri((cx + 5, y0 - 5), (cx + 5, y0 + 5.6), (cx + 12, y0 + 0.3), fg)
            else:
                c.tri((cx - 5, y0 - 5), (cx - 5, y0 + 5.6), (cx - 12, y0 + 0.3), fg)
        else:
            # |<->| : end stops and a double-headed arrow between them.
            x0, x1 = cx - 24, cx + 24
            c.line(x0, cy - 10, x0, cy + 10, 2.8, fg)
            c.line(x1, cy - 10, x1, cy + 10, 2.8, fg)
            c.line(x0 + 9, cy, x1 - 9, cy, 2.6, fg)
            c.tri((x0 + 4, cy), (x0 + 12, cy - 6), (x0 + 12, cy + 6), fg)
            c.tri((x1 - 4, cy), (x1 - 12, cy - 6), (x1 - 12, cy + 6), fg)
        return c

    for kind in ("fwd", "rev", "pp"):
        imgs[kind + "_off"] = mode_btn(kind, TRACK, TEXT_DIM).save("mode_%s_off.png" % kind)
        imgs[kind + "_offdim"] = mode_btn(kind, TRACK, TEXT_DIMMER).save("mode_%s_off_dim.png" % kind)
        for s in range(4):
            imgs["%s_on%d" % (kind, s)] = mode_btn(kind, STREAM_RGB[s], DARK_TEXT).save("mode_%s_s%d.png" % (kind, s + 1))
            imgs["%s_ondim%d" % (kind, s)] = mode_btn(kind, STREAM_DIM[s], PANEL).save("mode_%s_s%d_dim.png" % (kind, s + 1))

    # Global tiles, 300 x 196: Record, Resample, Save.
    TW, TH = 300, 196
    icx, icy = TW / 2.0, 80

    def tile(lit, icon):
        bg, fg = (LIT, DARK_TEXT) if lit else (PANEL, TEXT_DIM)
        c = Canvas(TW, TH)
        c.rrect(0, 0, TW, TH, 10, bg)
        if not lit:
            c.rrect(1, 1, TW - 1, TH - 1, 9, PANEL)
        if icon == "rec":
            c.circle(icx, icy, 24, fg)
        elif icon == "resample":
            c.ring(icx, icy, 25, 5, fg)
            c.rect(icx + 2, icy - 27, icx + 16, icy - 17, bg)            # gap in the ring
            c.tri((icx + 1, icy - 33), (icx + 1, icy - 11), (icx + 15, icy - 22), fg)
            c.circle(icx, icy, 11, fg)        # Record's dot inside the loop arrow: input + loop
        elif icon == "save":
            c.line(icx, icy - 26, icx, icy + 6, 5, fg)
            c.tri((icx - 14, icy - 2), (icx + 14, icy - 2), (icx, icy + 14), fg)
            c.line(icx - 26, icy + 10, icx - 26, icy + 24, 5, fg)
            c.line(icx + 26, icy + 10, icx + 26, icy + 24, 5, fg)
            c.line(icx - 26, icy + 24, icx + 26, icy + 24, 5, fg)
        return c

    for icon in ("rec", "resample", "save"):
        imgs[icon + "_off"] = tile(False, icon).save("%s_off.png" % icon)
        imgs[icon + "_on"] = tile(True, icon).save("%s_on.png" % icon)

    # Length choice buttons (3 x 2 grid inside the Length tile).
    # Text stays white on both, so "on" = bright 3 px outline around a lighter fill.
    # Span-end brackets: a right triangle standing CHEV_RISE px above the span, tall at the
    # loop's outer edge, sloping down inwards. One per stream, lit and dim.
    w = CHEV_W
    for s in range(4):
        for lit in (True, False):
            col = STREAM_RGB[s] if lit else STREAM_DIM[s]
            for end in (False, True):
                c = Canvas(w, CHEV_H)
                if end:   # tall at x = w (the loop's end), sloping down to the left
                    c.tri((w, 0), (w, CHEV_H), (0, CHEV_H), col)
                else:     # tall at x = 0 (the loop's start), sloping down to the right
                    c.tri((0, 0), (0, CHEV_H), (w, CHEV_H), col)
                name = "chev_s%d_%s_%s.png" % (s + 1, "lit" if lit else "dim", "end" if end else "start")
                imgs["chev%d%s%s" % (s, "L" if lit else "D", "e" if end else "s")] = c.save(name)
    c = Canvas(SCALE_BW, SCALE_BH)
    c.rrect(0, 0, SCALE_BW, SCALE_BH, 6, TRACK)
    imgs["scale_off"] = c.save("scale_off.png")
    c = Canvas(SCALE_BW, SCALE_BH)
    c.rrect(0, 0, SCALE_BW, SCALE_BH, 6, LIT)
    c.rrect(3, 3, SCALE_BW - 3, SCALE_BH - 3, 4, (0x4A, 0x4A, 0x52))
    imgs["scale_on"] = c.save("scale_on.png")
    return imgs


# ---------------------------------------------------------------------------
# JSON building blocks (docs/ui-reference.md). Dicts keep insertion order = documented order.
# DupKeys is for the overlay widgets' documented duplicate "version" keys.
# ---------------------------------------------------------------------------
class DupKeys(list):
    """An object written from (key, value) pairs, allowing a key twice."""


def dumps(v, ind=0):
    pad, pad1 = "  " * ind, "  " * (ind + 1)
    if isinstance(v, DupKeys) or isinstance(v, dict):
        items = list(v) if isinstance(v, DupKeys) else list(v.items())
        if not items:
            return "{}"
        body = ",\n".join("%s%s: %s" % (pad1, json.dumps(k), dumps(x, ind + 1)) for k, x in items)
        return "{\n%s\n%s}" % (body, pad)
    if isinstance(v, list):
        if not v:
            return "[]"
        if all(isinstance(x, (str, int, float)) for x in v):
            return "[" + ", ".join(json.dumps(x) for x in v) + "]"
        return "[\n" + ",\n".join(pad1 + dumps(x, ind + 1) for x in v) + "\n" + pad + "]"
    if isinstance(v, float):
        return repr(float(v))
    return json.dumps(v, ensure_ascii=False)


def fmt(n):
    return ("%.2f" % n).rstrip("0").rstrip(".") if isinstance(n, float) else str(n)


def rect(x, y, w, h):
    return " ".join(fmt(v) for v in (x, y, w, h))


def bounds(r, focus="No", invalid="Show", when="Always", show_if=None):
    b = {"version": 2 if show_if else 1, "acceptsHWFocus": focus, "showWhenDataModelInvalid": invalid,
         "whenVisible": when, "boundsType": "Absolute", "bounds": r}
    if show_if:
        b["additionalInvalidatingHandles"] = list(show_if)
    return b


def entry(name, typ, data, r, remap=None, **kw):
    return {
        "version": 2,
        "componentData": {"version": 1, "name": name, "type": typ, "data": data},
        "handle remapping": {"version": 1, "map": [{"key": k, "value": v} for k, v in (remap or [])]},
        "bounds": bounds(r, **kw),
    }


def custom(name, r, remap=None, **kw):
    return entry(name, name, {"version": 1, "handleName": "Data"}, r, remap, **kw)


def action(on, handler, handle="Data", extra=""):
    return {"version": 1, "onAction": on, "handler": handler, "handleName": handle, "additionalData": extra}


def bg(colour, focus_colour=None):
    return {"version": 1,
            "focussed": {"version": 1, "colour": focus_colour or colour, "image": ""},
            "unfocussed": {"version": 1, "colour": colour, "image": ""}}


def component(children, actions=(), background=CLEAR, focus_bg=None, ignore_mouse=False):
    return {"version": 2, "actions": list(actions), "backgroundData": bg(background, focus_bg),
            "ignoreMousePresses": ignore_mouse, "disableCoarseDataWheel": False,
            "componentsData": list(children)}


def text_style(size, rgb, just="left verticallyCentred", style="Regular", case="Original", alpha=0xFF):
    return {"version": 1, "font": {"version": 1, "name": "Titillium Web", "style": style, "height": float(size)},
            "colour": argb(rgb, alpha), "justification": just, "case": case}


def label(kind, ts, handle="Data", text=None):
    d = {"version": 1, "textStyle": ts, "type": kind, "handleName": handle}
    if kind == "Text":
        d["text"] = text
    return d


def fixed_text(text):
    """Fixed text as a Name label. The MPC (Live III, OS 3.10.1) does NOT draw Label type "Text"
    at all, but Name labels draw, so override a parameter's name with WithName[...].
    Parameter 0 is always valid, so the label never hides. Keep text ASCII and free of ']'."""
    assert "]" not in text and all(ord(c) < 128 for c in text), text
    return "WithName[%s]/%s" % (text, P(0))


def text_entry(name, text, r, ts, show_if=None):
    return entry(name, "Label", label("Name", ts, fixed_text(text)), r, show_if=show_if)


def value_entry(name, handle, r, ts, show_if=None):
    return entry(name, "Label", label("Value", ts, handle), r, show_if=show_if)


def colour_bar(bgc, outline, value, bipolar, handle="Data"):
    return {"version": 2, "sliderType": "ColourBar", "direction": "Horizontal", "backgroundColour": bgc,
            "outlineColour": outline, "valueColour": value, "isBipolar": bipolar, "handleName": handle}


def focus_ring(w, h, rgb):
    return entry("focus", "Focus", {"version": 1, "backgroundColour": "10ffffff", "outlineColour": argb(rgb),
                                    "backgroundInset": 3.0, "outlineThickness": 2.0},
                 rect(0, 0, w, h), when="WhenFocussed")


def overlay_def(widget_type, data_pairs):
    data = DupKeys([("version", data_pairs[0]), ("version", 1),
                    ("actions", [{"version": 1, "onAction": "Focus Lost", "handler": "Focus Lost",
                                  "handleName": "Focus Lost", "additionalData": ""}])] + data_pairs[1:])
    return component([entry("overlay", widget_type, data, "0 0 1280 800", focus="Yes")], ignore_mouse=True)


def knob_overlay(polarity):
    return overlay_def("KnobOverlay", [2, ("overlayType", "Knob With Header"), ("scaleType", "Linear"),
                                       ("polarityType", polarity), ("showMinusSign", True), ("handleName", "Data")])


def muted(s):
    return ["IndexedEnabling/1/2/" + P(sp(s, O_MUTE))]


def active(s):
    return ["IndexedEnabling/0/2/" + P(sp(s, O_MUTE))]


# ---------------------------------------------------------------------------
# Component definitions
# ---------------------------------------------------------------------------
def control_slot(s, bipolar, lit, speed=False):
    """A stream control: value text (right) and a full-width bar. The caption is a tab-level label.
    speed=True: in Octaves/Ratios the visible bar is the snapped position (display param), drawn
    under an invisible copy of the real bar that takes the touches; in Smooth, the plain bar."""
    col = STREAM_RGB[s] if lit else STREAM_DIM[s]
    txt = TEXT_DIM if lit else TEXT_DIMMER
    overlay = "knob overlay bi" if bipolar else "knob overlay"
    br = rect(16, 36, 268, 18)
    real = colour_bar(argb(TRACK), argb(LINE), argb(col), bipolar)
    if speed:
        bars = [entry("bar_snap", "Slider", colour_bar(argb(TRACK), argb(LINE), argb(col), False, P(P_SPDBAR0 + s)),
                      br, show_if=SNAPPED),
                entry("bar", "Slider", real, br, show_if=SMOOTH),
                entry("bar_touch", "Slider", colour_bar(CLEAR, CLEAR, CLEAR, False), br, show_if=SNAPPED)]
    else:
        bars = [entry("bar", "Slider", real, br)]
    # Three centred rows: caption (a tab-level label, y 2-32), bar (36-54), value (56-82).
    return component(
        [focus_ring(COL_W, SLOT_H, STREAM_RGB[s])] + bars +
        [entry("value", "Label", label("Value", text_style(22 if speed else 19, (TEXT if lit else TEXT_DIM) if speed else txt,
                                                           "horizontallyCentred verticallyCentred")),
               rect(8, 55, 284, 28))],
        actions=[action("Mouse Down", "Q-Link"),
                 action("Double Click", "Show Overlay", extra=overlay),
                 action("Enter Pressed", "Show Overlay", extra=overlay)],
        background=argb(PANEL), focus_bg=argb(PANEL_FOCUS))


def mode_slot(s, lit, imgs):
    k = "%d" % s
    kinds = ("fwd", "rev", "pp")                 # buttonId 0/1/2 = Forward / Reverse / Ping-pong

    def btn(kind, bid):
        on = imgs[("%s_on" if lit else "%s_ondim") % kind + k]
        off = imgs[("%s_off" if lit else "%s_offdim") % kind]
        return {"version": 2, "onImage": on, "offImage": off, "buttonId": bid, "numButtonsInGroup": 3,
                "handleName": "Data", "gestureBehaviour": "Instant"}
    # Caption (tab-level, centred) on top; the lit button shows the value, so no value label.
    return component(
        [focus_ring(COL_W, SLOT_H, STREAM_RGB[s])] +
        [entry(kind, "Button", btn(kind, i), rect(MODE_BX[i], 38, MODE_BW, 36)) for i, kind in enumerate(kinds)],
        actions=[action("Mouse Down", "Q-Link")],   # no menu: MenuOverlay draws empty on device
        background=argb(PANEL), focus_bg=argb(PANEL_FOCUS))


def wave_band(imgs):
    """The waveform band, identical on every tab. Uses literal Parameter handles throughout."""
    half = BAR_MID - BAR_TOP
    kids = []
    # Playheads: a stream-coloured full-height bar, stepped onto the bar under the head
    # (pos param = bar / 127). Thumb left = rect x + v * (rect w - BAR_W) = bar_x(k) at v = k/127.
    # Hidden while the stream is muted. Drawn before the masks, which cut it to the bar's height.
    for s in range(4):
        thumb = {"version": 3, "sliderType": "TrackAndThumb", "direction": "Horizontal",
                 "thumbImage": imgs["lit%d" % s], "thumbTrackVisible": False,
                 "revealedImage": "", "handleName": P(P_POS0 + s)}
        x0 = bar_x(0)
        kids.append(entry("litbar%d" % (s + 1), "Slider", thumb,
                          rect(round(x0, 2), BAR_TOP, round(bar_x(N_WAVE - 1) - x0 + BAR_W, 2), BAR_BOT - BAR_TOP),
                          show_if=active(s)))
    # Masks: the top half's grows down from the top, the bottom half's up from the bottom, by
    # (1 - bar height), in the band colour.
    for i in range(N_WAVE):
        x = bar_x(i) - MASK_PAD
        h = P(P_WAVE0 + i)
        for direction, y in (("Down", BAR_TOP), ("Up", BAR_MID)):
            meter = {"version": 3, "direction": direction, "invert": False,
                     "inactiveImage": imgs["mask_off"], "peakImage": imgs["mask"], "rmsImage": imgs["mask"],
                     "peakHandle": h, "rmsHandle": h, "peakHoldHandle": h,
                     "peakHoldSize": 0, "peakHoldColour": CLEAR}
            kids.append(entry("wave%03d%s" % (i, direction[0]), "Meter", meter,
                              rect(round(x, 2), y, BAR_W + 2 * MASK_PAD, half)))

    span_w = BAR_X1 - BAR_X0
    for s in range(4):
        y = SPAN_Y[s]
        # A loop is start..start+length on the take, wrapping past the end. The engine publishes
        # it as display params: piece A = start..endA (coloured 0..endA, then 0..start masked in
        # the band colour), piece B = 0..endB (only when the loop wraps; drawn after the mask).
        for lit in (True, False):
            col = STREAM_RGB[s] if lit else STREAM_DIM[s]
            vis = active(s) if lit else muted(s)
            kids.append(entry("span%d_a" % (s + 1), "Slider",
                              colour_bar(CLEAR, CLEAR, argb(col), False, P(span_p(s, SP_ENDA))),
                              rect(BAR_X0, y, span_w, SPAN_H), show_if=vis))
            kids.append(entry("span%d_mask" % (s + 1), "Slider",
                              colour_bar(CLEAR, CLEAR, argb(BAND), False, P(span_p(s, SP_START))),
                              rect(BAR_X0, y, span_w, SPAN_H), show_if=vis))
            kids.append(entry("span%d_b" % (s + 1), "Slider",
                              colour_bar(CLEAR, CLEAR, argb(col), False, P(span_p(s, SP_ENDB))),
                              rect(BAR_X0, y, span_w, SPAN_H), show_if=vis))
        # Brackets bookend the span: a stream-coloured triangle (a TrackAndThumb thumb) standing
        # on the span's top visible row, tall at the loop's outer edge.
        # Thumb left edge = rect x + value * (rect w - thumb w). The span's fill runs from
        # fx0 = BAR_X0 + SPAN_INSET over fw = span_w - 2 * SPAN_INSET, so the start thumb's rect
        # starts at fx0 (image x = 0 on the start) and the end thumb's at fx0 - w (image x = w
        # on the end); both are fw + w wide.
        fx0, fw, w = BAR_X0 + SPAN_INSET, span_w - 2 * SPAN_INSET, CHEV_W
        cy = y + SPAN_INSET + 1 - CHEV_H     # bottom row of the image = the span's top visible row
        for lit in (True, False):
            for end in (False, True):
                thumb = {"version": 3, "sliderType": "TrackAndThumb", "direction": "Horizontal",
                         "thumbImage": imgs["chev%d%s%s" % (s, "L" if lit else "D", "e" if end else "s")],
                         "thumbTrackVisible": False, "revealedImage": "",
                         "handleName": P(span_p(s, SP_ENDPOS if end else SP_START))}
                kids.append(entry("chev%d%s%s" % (s + 1, "L" if lit else "D", "e" if end else "s"), "Slider", thumb,
                                  rect(fx0 - w if end else fx0, cy, fw + w, CHEV_H),
                                  show_if=active(s) if lit else muted(s)))
    # Faint hint while there is no take yet (status 0 = "Press Record"); gone once a waveform exists.
    kids.append(text_entry("hint", "Ready to record", rect(0, BAR_TOP, BAND_RECT[2], BAR_BOT - BAR_TOP),
                           text_style(30, TEXT_DIMMER, "horizontallyCentred verticallyCentred", "SemiBold"),
                           show_if=["IndexedEnabling/0/%d/%s" % (len(STATUS_CHOICES), P(P_STATUS))]))
    # Shield: last child, so it is on top and takes every touch on the band.
    kids.append(custom("touchShield", rect(0, 0, BAND_RECT[2], BAND_RECT[3])))
    band = component(kids, background=argb(BAND))
    band["backgroundData"]["focussed"]["image"] = band["backgroundData"]["unfocussed"]["image"] = imgs["wave_bg"]
    return band


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------
CAPTION = lambda lit, size=24: text_style(size, TEXT if lit else TEXT_DIMMER, "horizontallyCentred verticallyCentred",
                                          style="SemiBold", case="Upper Case")
CAP_DX = 44         # clears the "Q" badge the MPC draws in the Q-Link focus box's top-left corner


def band_entry():
    return custom("waveBand", rect(*BAND_RECT))


def stream_header(s, imgs):
    x = COL_X[s]
    btn = {"version": 2, "onImage": imgs["name_muted"], "offImage": imgs["name%d" % s], "buttonId": 1,
           "numButtonsInGroup": 1, "handleName": P(sp(s, O_MUTE)), "gestureBehaviour": "Instant"}
    # The label covers the whole button and swallows touches (seen on device), so a
    # transparent button bound to the same parameter goes on top to take the tap.
    hit = dict(btn, onImage=imgs["hit"], offImage=imgs["hit"])
    r = rect(x, NAME_Y, COL_W, NAME_H)
    return [
        entry("stream%d_mute" % (s + 1), "Button", btn, r),
        text_entry("stream%d_active" % (s + 1), "Playhead %d - Active" % (s + 1), r,
                   text_style(22, DARK_TEXT, "horizontallyCentred verticallyCentred", "SemiBold"), show_if=active(s)),
        text_entry("stream%d_muted" % (s + 1), "Playhead %d - Muted" % (s + 1), r,
                   text_style(22, TEXT, "horizontallyCentred verticallyCentred", "SemiBold"), show_if=muted(s)),
        entry("stream%d_mute_hit" % (s + 1), "Button", hit, r, focus="Yes"),
    ]


def stream_slot(s, row, caption, param, kind):
    """kind: 'uni', 'bi', 'mode'. Emits lit + dim copies, each with its caption label."""
    x, y = COL_X[s], SLOT_Y[row]
    out = []
    for lit in (True, False):
        vis = active(s) if lit else muted(s)
        comp = ("mode_s%d" % (s + 1)) if kind == "mode" else ("ctrl_s%d_%s" % (s + 1, kind))
        comp += "" if lit else "_dim"
        out.append(custom(comp, rect(x, y, COL_W, SLOT_H), [("Data", P(param))], focus="Yes", invalid="Hide", show_if=vis))
        out.append(text_entry("cap", caption, rect(x + CAP_DX, y + 2, COL_W - 2 * CAP_DX, 30), CAPTION(lit), show_if=vis))
    return out


def stream_tab(name, fn, rows):
    kids = [band_entry()]
    for s in range(4):
        kids += stream_header(s, IMGS)
        for row, spec in enumerate(rows):
            if spec:
                caption, off, kind = spec
                kids += stream_slot(s, row, caption, sp(s, off), kind)
    qbounds = [rect(x - 4, SLOT_Y[0] - 4, COL_W + 8, SLOT_Y[3] + SLOT_H - SLOT_Y[0] + 8) for x in COL_X]
    return tab(name, fn, kids, qbounds)


def tab(name, fn, kids, qbounds):
    return {"version": 1, "tabName": name, "fnKeyIndex": fn, "fnKeySubIndex": 0,
            "componentDefinition": component(kids, background=argb(BG)),
            "qlinkBoundsData": qbounds}


def record_tab():
    kids = [band_entry()]
    TY, TH = 206, 196
    big = lambda rgb: text_style(28, rgb, "horizontallyCentred verticallyCentred", "SemiBold", "Upper Case")
    small = lambda rgb: text_style(18, rgb, "horizontallyCentred verticallyCentred")

    def toggle_tile(x, param, key, title_off, title_on, sub_off, sub_on, busy=None):
        btn = {"version": 2, "onImage": IMGS[key + "_on"], "offImage": IMGS[key + "_off"], "buttonId": 1,
               "numButtonsInGroup": 1, "handleName": P(param), "gestureBehaviour": "Instant"}
        off, on = ["IndexedEnabling/0/2/" + P(param)], ["IndexedEnabling/1/2/" + P(param)]
        tr, sr = rect(x, TY + 118, 300, 36), rect(x, TY + 150, 300, 28)
        hit = dict(btn, onImage=IMGS["hit"], offImage=IMGS["hit"])   # labels swallow touches
        # busy = (show_if, title, sub): while show_if holds, the lit tile and dark labels are
        # drawn over the normal ones, like Record/Resample when on. For Save, whose parameter
        # springs back at once, the lit state follows Status = Saving instead.
        over = [] if not busy else [
            entry(key + "_busy", "Button", dict(btn, onImage=IMGS[key + "_on"], offImage=IMGS[key + "_on"]),
                  rect(x, TY, 300, TH), show_if=busy[0]),
            text_entry(key + "_tb", busy[1], tr, big(DARK_TEXT), show_if=busy[0]),
            text_entry(key + "_sb", busy[2], sr, small(DARK_TEXT), show_if=busy[0])]
        return [entry(key, "Button", btn, rect(x, TY, 300, TH)),
                text_entry(key + "_t0", title_off, tr, big(TEXT), show_if=off),
                text_entry(key + "_t1", title_on, tr, big(DARK_TEXT), show_if=on),
                text_entry(key + "_s0", sub_off, sr, small(TEXT_DIM), show_if=off),
                text_entry(key + "_s1", sub_on, sr, small(DARK_TEXT), show_if=on)] + over + [
                entry(key + "_hit", "Button", hit, rect(x, TY, 300, TH), focus="Yes")]

    def choice_grid(key, param, choices, cells, img_on, img_off, descs=None):
        """Radio buttons (buttonId = step) with a white label each (and an optional dimmer
        description line under it), then a transparent radio button over the labels, because
        labels swallow touches. cells[i] = (x, y, w, h) on the tab."""
        out = []
        for i, (choice, cell) in enumerate(zip(choices, cells)):
            r = rect(*cell)
            radio = {"version": 2, "onImage": img_on, "offImage": img_off, "buttonId": i,
                     "numButtonsInGroup": len(choices), "handleName": P(param), "gestureBehaviour": "Instant"}
            out.append(entry("%s%d" % (key, i), "Button", radio, r))
            if descs:
                x, y, w, h = cell
                out.append(text_entry("%s%d_txt" % (key, i), choice, rect(x, y + h / 2.0 - 34, w, 36),
                                      text_style(24, TEXT, "horizontallyCentred verticallyCentred", "SemiBold")))
                out.append(text_entry("%s%d_desc" % (key, i), descs[i], rect(x, y + h / 2.0 + 2, w, 28),
                                      text_style(17, TEXT_DIM, "horizontallyCentred verticallyCentred")))
            else:
                out.append(text_entry("%s%d_txt" % (key, i), choice, r,
                                      text_style(20, TEXT, "horizontallyCentred verticallyCentred", "SemiBold")))
            out.append(entry("%s%d_hit" % (key, i), "Button", dict(radio, onImage=IMGS["hit"], offImage=IMGS["hit"]), r))
        return out

    kids += toggle_tile(COL_X[0], P_RECORD, "rec", "Record", "Recording", "Records the input", "Tap to stop")
    kids += toggle_tile(COL_X[1], P_RESAMPLE, "resample", "Resample", "Resampling",
                        "Records input + loop", "Tap to stop")
    saving = ["IndexedEnabling/%d/%d/%s" % (STATUS_CHOICES.index("Saving"), len(STATUS_CHOICES), P(P_STATUS))]
    kids += toggle_tile(COL_X[2], P_SAVE, "save", "Save", "Save", "Bounce to Hildegard Loops", "Bounce to Hildegard Loops",
                        busy=(saving, "Saving", "Bounce to Hildegard Loops"))

    # Bottom row: Dry/Wet. The Speed Ratios tile is the whole right column (top to bottom).
    RY, RH = 414, 198
    kids.append(custom("dryWetTile", rect(20, RY, DRYWET_W, RH), [("Data", P(P_DRYWET))], focus="Yes"))
    kids.append(custom("scaleTile", rect(COL_X[3], TY, 300, SCALE_TILE_H), [("Data", P(P_SCALE))], focus="Yes"))
    kids += choice_grid("scale", P_SCALE, SCALE_CHOICES,
                        [(COL_X[3] + 12, TY + SCALE_BY[i], SCALE_BW, SCALE_BH) for i in range(len(SCALE_CHOICES))],
                        IMGS["scale_on"], IMGS["scale_off"], descs=SCALE_DESC)
    return tab("RECORD", 0, kids, [])


def caption_tile(h, caption=None, size=24):
    """A panel with a focus ring and a centred caption: the parameter's name, or fixed text."""
    handle = fixed_text(caption) if caption else "Data"
    return component(
        [focus_ring(300, h, LIT),
         entry("cap", "Label", label("Name", CAPTION(True, size), handle), rect(8, 14, 284, 30))],
        actions=[action("Mouse Down", "Q-Link")],
        background=argb(PANEL), focus_bg=argb(PANEL_FOCUS))


def dry_wet_tile():
    w = DRYWET_W
    return component(
        [focus_ring(w, 198, LIT),
         entry("cap", "Label", label("Name", CAPTION(True)), rect(24, 14, w - 48, 30)),
         entry("value", "Label", label("Value", text_style(44, TEXT, "horizontallyCentred verticallyCentred", "SemiBold")),
               rect(24, 46, w - 48, 56)),
         entry("bar", "Slider", colour_bar(argb(TRACK), argb(LINE), argb(NEUTRAL_BAR), False), rect(24, 116, w - 48, 44))],
        actions=[action("Mouse Down", "Q-Link"),
                 action("Double Click", "Show Overlay", extra="knob overlay"),
                 action("Enter Pressed", "Show Overlay", extra="knob overlay")],
        background=argb(PANEL), focus_bg=argb(PANEL_FOCUS))


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def tui():
    defs = [("knob overlay", knob_overlay("Unipolar")),
            ("knob overlay bi", knob_overlay("Bipolar")),
            ("touchShield", component([])),
            ("waveBand", wave_band(IMGS)),
            ("scaleTile", caption_tile(SCALE_TILE_H, "Playback Speed Ratios", 22)),
            ("dryWetTile", dry_wet_tile())]
    for s in range(4):
        for lit in (True, False):
            sfx = "" if lit else "_dim"
            defs.append(("ctrl_s%d_uni%s" % (s + 1, sfx), control_slot(s, False, lit)))
            defs.append(("ctrl_s%d_bi%s" % (s + 1, sfx), control_slot(s, True, lit)))
            defs.append(("mode_s%d%s" % (s + 1, sfx), mode_slot(s, lit, IMGS)))
            defs.append(("ctrl_s%d_spd%s" % (s + 1, sfx), control_slot(s, False, lit, speed=True)))

    repl = [(P_RECORD, ["Off", "On"]), (P_RESAMPLE, ["Off", "On"]),
            (P_SAVE, ["Off", "On"])]
    for s in range(4):
        repl += [(sp(s, O_MODE), ["Forward", "Reverse", "Ping-pong"]), (sp(s, O_MUTE), ["Active", "Muted"])]
    repl.append((P_STATUS, STATUS_CHOICES))
    repl.append((P_SCALE, SCALE_CHOICES))

    # Loop: Start, Length, Mode, Speed.  Mix: Tone, Pan, Volume, (empty).  docs/UI-LAYOUT.md
    loop_rows = [("Start", O_START, "uni"), ("Length", O_LENGTH, "uni"), ("Mode", O_MODE, "mode"), ("Speed Ratio", O_SPEED, "spd")]
    mix_rows = [("Tone", O_TONE, "bi"), ("Pan", O_PAN, "bi"), ("Volume", O_VOLUME, "uni"), None]

    return {"pageData": {
        "version": 1,
        "componentDefinitions": {"version": 2, "importFiles": [],
                                 "localComponentDefinitions": [{"key": k, "value": v} for k, v in defs]},
        "replacementStrings": [{"key": P(i), "value": v} for i, v in repl],
        "info": {"version": 1, "type": "CompleteDescription"},
        "tabs": [record_tab(), stream_tab("LOOP", 1, loop_rows), stream_tab("MIX", 2, mix_rows)],
    }}, loop_rows, mix_rows


def qlinks(loop_rows, mix_rows):
    def grid(cells):
        """cells[(row, col)] = param. Q-Link at row r (0 = top), column c is 13 + c - 4r."""
        q = {"Q-Link %d" % n: -1 for n in range(1, 17)}
        for (r, c), p in cells.items():
            q["Q-Link %d" % (13 + c - 4 * r)] = p
        return dict(sorted(q.items(), key=lambda kv: int(kv[0].split()[1])))

    def stream_grid(rows):
        return grid({(r, s): sp(s, spec[1]) for s in range(4) for r, spec in enumerate(rows) if spec})

    mapping = [
        {"Tab": 1, "SubTab": 1, "Bank Direction": "Column",
         "Q-Links": grid({(0, 0): P_DRYWET, (1, 0): P_SCALE})},
        {"Tab": 2, "SubTab": 1, "Bank Direction": "Column", "Q-Links": stream_grid(loop_rows)},
        {"Tab": 3, "SubTab": 1, "Bank Direction": "Column", "Q-Links": stream_grid(mix_rows)},
    ]
    # Program mode: each column = one stream's Speed, Tone, Pan, Volume (top to bottom).
    prog = grid({(r, s): sp(s, off) for s in range(4) for r, off in enumerate((O_SPEED, O_TONE, O_PAN, O_VOLUME))})
    return {"version": 4, "info": {"version": 1, "type": "CompleteDescription"},
            "Screen Mode Q-Links": {"version": 4, "map": mapping},
            "Program Mode Q-Links": prog}


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    for f in os.listdir(OUT):
        if f.endswith(".png") or f.endswith(".json"):
            os.remove(os.path.join(OUT, f))
    IMGS = make_images()
    doc, loop_rows, mix_rows = tui()
    with open(os.path.join(OUT, "TUI.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(doc) + "\n")
    with open(os.path.join(OUT, "Q-Links.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(dumps(qlinks(loop_rows, mix_rows)) + "\n")
    print("wrote %s (%d images)" % (OUT, len([f for f in os.listdir(OUT) if f.endswith('.png')])))

#!/usr/bin/env python3
"""Generate Hildegard's hand-written MPC skin: skins/TUI.json, skins/Q-Links.json and PNGs.

    python plugins/Hildegard/tools/gen_skin.py

Layout source of truth: docs/UI-LAYOUT.md (locked 2026-11-14). Parameter indices: SPEC.md
Section 5 + src/Hildegard.cpp. Colours: SPEC 7.5 (a final colour pass is still to come; change
the palette block below and rerun). Format reference: the SDK's docs/ui-reference.md.

Pure standard library (zlib PNG writer, 4x supersampled shapes). The output in skins/ is
committed so the SDK build can copy it verbatim; rerun this script after any layout change.

Design notes
- Three tabs: F1 RECORD (landing), F2 LOOP, F3 MIX. The waveform band (one custom component,
  "waveBand") sits at the top of every tab.
- Waveform: 128 bars, each drawn twice (a Meter growing up from the centre line and one
  growing down) bound to wave000-127. Loop spans are two stacked ColourBars per stream: one
  filled 0..Out in the stream colour, then one filled 0..In in the band colour, which masks the
  part before In. Head markers are TrackAndThumb sliders bound to pos1-4 with no track.
  A transparent "shield" component on top swallows touches so nothing in the band is editable.
- Muted streams: every stream-coloured element exists twice, a lit and a dim copy, and
  IndexedEnabling on Mute N shows exactly one of them.
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
P_LENGTH, P_RECORD, P_RESAMPLE, P_DRYWET, P_SAVE = 0, 1, 2, 3, 4
O_SPEED, O_TONE, O_PAN, O_VOLUME, O_IN, O_OUT, O_MODE, O_MUTE = range(8)


def sp(s, off):
    return 5 + s * 8 + off


P_POS0, P_STATUS, P_WAVE0, N_WAVE = 37, 41, 42, 128
P_SCALE = 170                     # Speed Scale, appended after the display params
SCALE_CHOICES = ["Octaves", "Ratios", "Smooth"]
STATUS_CHOICES = ["Press Record", "Ready", "Armed", "Recording", "Saving", "Saved", "Save failed", "Buffer full"]
LENGTH_CHOICES = ["Free", "1 bar", "2 bars", "4 bars", "8 bars", "16 bars"]


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

# Length tile: the choices as a 3 x 2 radio grid. The MenuOverlay draws an empty list on device.
LEN_BW, LEN_BH = 88, 58
LEN_BX = [12, 106, 200]      # inside the 300 x 196 tile
LEN_BY = [58, 126]
# Speed Scale tile (bottom right, where Status was): three stacked radio buttons.
SCALE_BW, SCALE_BH = 276, 42
SCALE_BY = [52, 100, 148]
DRYWET_W = COL_X[3] - 13 - 20    # Dry/Wet fills the rest of the bottom row

# Inside the band
BAR_X0, BAR_X1 = 10.0, 1230.0
BAR_W = 7
BAR_TOP, BAR_MID, BAR_BOT = 12, 70, 128
SPAN_Y = [132, 142, 152, 162]
SPAN_H = 8          # 4 px drew nothing on device (ColourBar outline/inset eats the fill)
MARK_TOP = 6
MARK_W = 4

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


def clear_png(name, w, h):
    write_png(os.path.join(OUT, name), w, h, [b"\0" + b"\0\0\0\0" * w] * h)
    return name


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------
def make_images():
    imgs = {}
    half = BAR_MID - BAR_TOP
    imgs["bar"] = solid_png("wave_bar.png", BAR_W, half, WAVE)
    imgs["bar_off"] = clear_png("wave_bar_off.png", BAR_W, half)
    imgs["hit"] = clear_png("hit_clear.png", COL_W, NAME_H)   # invisible touch target over labels

    for s in range(4):
        h = SPAN_Y[s] + SPAN_H - MARK_TOP
        imgs["mark%d" % s] = solid_png("marker_s%d.png" % (s + 1), MARK_W, h, STREAM_RGB[s])
        imgs["markdim%d" % s] = solid_png("marker_s%d_dim.png" % (s + 1), MARK_W, h, STREAM_DIM[s])

        c = Canvas(COL_W, NAME_H)
        c.rrect(0, 0, COL_W, NAME_H, 7, STREAM_RGB[s])
        imgs["name%d" % s] = c.save("name_s%d.png" % (s + 1))
    c = Canvas(COL_W, NAME_H)
    c.rrect(0, 0, COL_W, NAME_H, 7, MUTED_BTN)
    imgs["name_muted"] = c.save("name_muted.png")

    # Mode buttons: loop icon and ping-pong icon, 130 x 36.
    MW, MH = 130, 36

    def mode_btn(kind, bg, fg):
        c = Canvas(MW, MH)
        c.rrect(0, 0, MW, MH, 6, bg)
        cx, cy = MW / 2.0, MH / 2.0
        if kind == "loop":
            # A closed loop (stadium outline) with an arrowhead on the top run, pointing right.
            x0, x1, y0, y1, t = cx - 22, cx + 22, cy - 9, cy + 9, 2.6
            c.rrect(x0, y0, x1, y1, 9, fg)
            c.rrect(x0 + t, y0 + t, x1 - t, y1 - t, 9 - t, bg)
            c.tri((cx + 5, y0 - 5), (cx + 5, y0 + 5.6), (cx + 12, y0 + 0.3), fg)
        else:
            # |<->| : end stops and a double-headed arrow between them.
            x0, x1 = cx - 24, cx + 24
            c.line(x0, cy - 10, x0, cy + 10, 2.8, fg)
            c.line(x1, cy - 10, x1, cy + 10, 2.8, fg)
            c.line(x0 + 9, cy, x1 - 9, cy, 2.6, fg)
            c.tri((x0 + 4, cy), (x0 + 12, cy - 6), (x0 + 12, cy + 6), fg)
            c.tri((x1 - 4, cy), (x1 - 12, cy - 6), (x1 - 12, cy + 6), fg)
        return c

    for kind in ("loop", "pp"):
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
    c = Canvas(LEN_BW, LEN_BH)
    c.rrect(0, 0, LEN_BW, LEN_BH, 6, TRACK)
    imgs["len_off"] = c.save("length_off.png")
    c = Canvas(LEN_BW, LEN_BH)
    c.rrect(0, 0, LEN_BW, LEN_BH, 6, LIT)
    c.rrect(3, 3, LEN_BW - 3, LEN_BH - 3, 4, (0x4A, 0x4A, 0x52))
    imgs["len_on"] = c.save("length_on.png")
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
def control_slot(s, bipolar, lit):
    """A stream control: value text (right) and a full-width bar. The caption is a tab-level label."""
    col = STREAM_RGB[s] if lit else STREAM_DIM[s]
    txt = TEXT_DIM if lit else TEXT_DIMMER
    overlay = "knob overlay bi" if bipolar else "knob overlay"
    # Three centred rows: caption (a tab-level label, y 2-32), bar (36-54), value (56-82).
    return component(
        [focus_ring(COL_W, SLOT_H, STREAM_RGB[s]),
         entry("bar", "Slider", colour_bar(argb(TRACK), argb(LINE), argb(col), bipolar), rect(16, 36, 268, 18)),
         entry("value", "Label", label("Value", text_style(19, txt, "horizontallyCentred verticallyCentred")),
               rect(8, 56, 284, 26))],
        actions=[action("Mouse Down", "Q-Link"),
                 action("Double Click", "Show Overlay", extra=overlay),
                 action("Enter Pressed", "Show Overlay", extra=overlay)],
        background=argb(PANEL), focus_bg=argb(PANEL_FOCUS))


def mode_slot(s, lit, imgs):
    k = "%d" % s
    on_l = imgs[("loop_on" if lit else "loop_ondim") + k]
    on_p = imgs[("pp_on" if lit else "pp_ondim") + k]
    off_l = imgs["loop_off" if lit else "loop_offdim"]
    off_p = imgs["pp_off" if lit else "pp_offdim"]
    txt = TEXT_DIM if lit else TEXT_DIMMER

    def btn(on, off, bid):
        return {"version": 2, "onImage": on, "offImage": off, "buttonId": bid, "numButtonsInGroup": 2,
                "handleName": "Data", "gestureBehaviour": "Instant"}
    # Caption (tab-level, centred) on top; the lit button shows the value, so no value label.
    return component(
        [focus_ring(COL_W, SLOT_H, STREAM_RGB[s]),
         entry("loop", "Button", btn(on_l, off_l, 0), rect(16, 38, 130, 36)),
         entry("pingpong", "Button", btn(on_p, off_p, 1), rect(154, 38, 130, 36))],
        actions=[action("Mouse Down", "Q-Link")],   # no menu: MenuOverlay draws empty on device
        background=argb(PANEL), focus_bg=argb(PANEL_FOCUS))


def wave_band(imgs):
    """The waveform band, identical on every tab. Uses literal Parameter handles throughout."""
    half = BAR_MID - BAR_TOP
    pitch = (BAR_X1 - BAR_X0) / N_WAVE
    kids = []
    for i in range(N_WAVE):
        x = BAR_X0 + i * pitch + (pitch - BAR_W) / 2.0
        h = P(P_WAVE0 + i)
        for direction, y in (("Up", BAR_TOP), ("Down", BAR_MID)):
            meter = {"version": 3, "direction": direction, "invert": False,
                     "inactiveImage": imgs["bar_off"], "peakImage": imgs["bar"], "rmsImage": imgs["bar"],
                     "peakHandle": h, "rmsHandle": h, "peakHoldHandle": h,
                     "peakHoldSize": 0, "peakHoldColour": CLEAR}
            kids.append(entry("wave%03d%s" % (i, direction[0]), "Meter", meter, rect(round(x, 2), y, BAR_W, half)))

    span_w = BAR_X1 - BAR_X0
    for s in range(4):
        y = SPAN_Y[s]
        for lit in (True, False):
            col = STREAM_RGB[s] if lit else STREAM_DIM[s]
            vis = active(s) if lit else muted(s)
            kids.append(entry("span%d_out" % (s + 1), "Slider",
                              colour_bar(CLEAR, CLEAR, argb(col), False, P(sp(s, O_OUT))),
                              rect(BAR_X0, y, span_w, SPAN_H), show_if=vis))
            kids.append(entry("span%d_in" % (s + 1), "Slider",
                              colour_bar(CLEAR, CLEAR, argb(BAND), False, P(sp(s, O_IN))),
                              rect(BAR_X0, y, span_w, SPAN_H), show_if=vis))
    for s in range(4):
        h = SPAN_Y[s] + SPAN_H - MARK_TOP
        for lit in (True, False):
            thumb = {"version": 3, "sliderType": "TrackAndThumb", "direction": "Horizontal",
                     "thumbImage": imgs[("mark%d" if lit else "markdim%d") % s], "thumbTrackVisible": False,
                     "revealedImage": "", "handleName": P(P_POS0 + s)}
            kids.append(entry("marker%d" % (s + 1), "Slider", thumb,
                              rect(BAR_X0 - MARK_W / 2.0, MARK_TOP, span_w + MARK_W, h),
                              show_if=active(s) if lit else muted(s)))
    # Faint hint while there is no take yet (status 0 = "Press Record"); gone once a waveform exists.
    kids.append(text_entry("hint", "Ready to record", rect(0, BAR_TOP, BAND_RECT[2], BAR_BOT - BAR_TOP),
                           text_style(30, TEXT_DIMMER, "horizontallyCentred verticallyCentred", "SemiBold"),
                           show_if=["IndexedEnabling/0/%d/%s" % (len(STATUS_CHOICES), P(P_STATUS))]))
    # Shield: last child, so it is on top and takes every touch on the band.
    kids.append(custom("touchShield", rect(0, 0, BAND_RECT[2], BAND_RECT[3])))
    return component(kids, background=argb(BAND))


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
        text_entry("stream%d_active" % (s + 1), "Stream %d - Active" % (s + 1), r,
                   text_style(22, DARK_TEXT, "horizontallyCentred verticallyCentred", "SemiBold"), show_if=active(s)),
        text_entry("stream%d_muted" % (s + 1), "Stream %d - Muted" % (s + 1), r,
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

    def toggle_tile(x, param, key, title_off, title_on, sub_off, sub_on):
        btn = {"version": 2, "onImage": IMGS[key + "_on"], "offImage": IMGS[key + "_off"], "buttonId": 1,
               "numButtonsInGroup": 1, "handleName": P(param), "gestureBehaviour": "Instant"}
        off, on = ["IndexedEnabling/0/2/" + P(param)], ["IndexedEnabling/1/2/" + P(param)]
        tr, sr = rect(x, TY + 118, 300, 36), rect(x, TY + 150, 300, 28)
        hit = dict(btn, onImage=IMGS["hit"], offImage=IMGS["hit"])   # labels swallow touches
        return [entry(key, "Button", btn, rect(x, TY, 300, TH)),
                text_entry(key + "_t0", title_off, tr, big(TEXT), show_if=off),
                text_entry(key + "_t1", title_on, tr, big(DARK_TEXT), show_if=on),
                text_entry(key + "_s0", sub_off, sr, small(TEXT_DIM), show_if=off),
                text_entry(key + "_s1", sub_on, sr, small(DARK_TEXT), show_if=on),
                entry(key + "_hit", "Button", hit, rect(x, TY, 300, TH), focus="Yes")]

    def choice_grid(key, param, choices, cells, img_on, img_off):
        """Radio buttons (buttonId = step) with a white label each, then a transparent radio button
        over the label, because labels swallow touches. cells[i] = (x, y, w, h) on the tab."""
        out = []
        for i, (choice, cell) in enumerate(zip(choices, cells)):
            r = rect(*cell)
            radio = {"version": 2, "onImage": img_on, "offImage": img_off, "buttonId": i,
                     "numButtonsInGroup": len(choices), "handleName": P(param), "gestureBehaviour": "Instant"}
            out.append(entry("%s%d" % (key, i), "Button", radio, r))
            out.append(text_entry("%s%d_txt" % (key, i), choice, r,
                                  text_style(20, TEXT, "horizontallyCentred verticallyCentred", "SemiBold")))
            out.append(entry("%s%d_hit" % (key, i), "Button", dict(radio, onImage=IMGS["hit"], offImage=IMGS["hit"]), r))
        return out

    kids += toggle_tile(COL_X[0], P_RECORD, "rec", "Record", "Recording", "Records the input", "Tap to stop")
    kids += toggle_tile(COL_X[1], P_RESAMPLE, "resample", "Resample", "Resample",
                        "Records the current loop", "Records the current loop")
    kids += toggle_tile(COL_X[2], P_SAVE, "save", "Save", "Save", "Bounce to Hildegard Loops", "Bounce to Hildegard Loops")
    kids.append(custom("lengthTile", rect(COL_X[3], TY, 300, TH), [("Data", P(P_LENGTH))], focus="Yes"))
    kids += choice_grid("len", P_LENGTH, LENGTH_CHOICES,
                        [(COL_X[3] + LEN_BX[i % 3], TY + LEN_BY[i // 3], LEN_BW, LEN_BH) for i in range(len(LENGTH_CHOICES))],
                        IMGS["len_on"], IMGS["len_off"])

    # Bottom row: Dry/Wet fills the space left of the Speed Scale tile (which replaced Status).
    RY, RH = 414, 198
    kids.append(custom("dryWetTile", rect(20, RY, DRYWET_W, RH), [("Data", P(P_DRYWET))], focus="Yes"))
    kids.append(custom("scaleTile", rect(COL_X[3], RY, 300, RH), [("Data", P(P_SCALE))], focus="Yes"))
    kids += choice_grid("scale", P_SCALE, SCALE_CHOICES,
                        [(COL_X[3] + 12, RY + SCALE_BY[i], SCALE_BW, SCALE_BH) for i in range(len(SCALE_CHOICES))],
                        IMGS["scale_on"], IMGS["scale_off"])
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
            ("lengthTile", caption_tile(196)),
            ("scaleTile", caption_tile(198, "Playback Speed Ratios", 20)),
            ("dryWetTile", dry_wet_tile())]
    for s in range(4):
        for lit in (True, False):
            sfx = "" if lit else "_dim"
            defs.append(("ctrl_s%d_uni%s" % (s + 1, sfx), control_slot(s, False, lit)))
            defs.append(("ctrl_s%d_bi%s" % (s + 1, sfx), control_slot(s, True, lit)))
            defs.append(("mode_s%d%s" % (s + 1, sfx), mode_slot(s, lit, IMGS)))

    repl = [(P_LENGTH, LENGTH_CHOICES), (P_RECORD, ["Off", "On"]), (P_RESAMPLE, ["Off", "On"]),
            (P_SAVE, ["Off", "On"])]
    for s in range(4):
        repl += [(sp(s, O_MODE), ["Loop", "Ping-pong"]), (sp(s, O_MUTE), ["Active", "Muted"])]
    repl.append((P_STATUS, STATUS_CHOICES))
    repl.append((P_SCALE, SCALE_CHOICES))

    # Loop: In, Out, Mode, Speed.  Mix: Tone, Pan, Volume, (empty).  docs/UI-LAYOUT.md
    loop_rows = [("In", O_IN, "uni"), ("Out", O_OUT, "uni"), ("Mode", O_MODE, "mode"), ("Speed Ratio", O_SPEED, "bi")]
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
         "Q-Links": grid({(0, 0): P_DRYWET, (1, 0): P_LENGTH, (2, 0): P_SCALE})},
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

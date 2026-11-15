# Hildegard — Technical Specification (MPC Effect Plugin, v1)

**Building on Fire** · MIT License · Spec version 1.0 · 13 November 2026

Hildegard is an insert effect for standalone Akai MPCs. It records a chunk of the audio passing through its track into a frozen buffer, then plays that buffer back through four independent tape-style playheads ("streams"), each with its own speed, direction, loop region, tone, pan and volume. It works like a controllable multitap delay and a resampler: catch a phrase, bend it four ways, fold it back into the mix.

It is free and open source, built with Akai's official MPC Plugin SDK, and intended for anyone running external plugins on an MPC.

---

## 0. Read This First (for Claude Code)

1. Work inside a clone of Akai's SDK: `https://github.com/inmusic-software/mpc-plugin-builder-sdk`. Read its `AGENTS.md`, `CLAUDE.md`, and the docs in `docs/` before writing code. The SDK's rules override anything in this spec that conflicts with them; flag any conflict you find.
2. Read **Section 13 (Risks to prototype first)** and **Section 14 (Build order)** before scaffolding. Several display and file features are unproven on the hardware; the build order tests them early so the design can adapt.
3. Treat **Section 16 (Defaults to confirm)** as provisional values, not requirements.
4. **Never modify the SDK's own files** (`framework/`, `tools/`, `docs/`, `schemas/`, `ui-kit/`, the skills folders). Keep everything Hildegard needs inside `plugins/Hildegard/`, plus `SPEC.md` at the root. If the SDK seems to need a change, stop and explain why instead. This keeps Akai's future SDK updates mergeable with a plain `git pull`. After any SDK update, rebuild and rerun the host simulator test before installing on a device.
5. The design principle that overrides precision everywhere: **Hildegard sounds good before it is sample-exact.** Every discontinuity (loop wrap, direction change, buffer swap, mute, transport start/stop) is crossfaded.

---

## 1. Platform and Targets

| Item | Value |
|---|---|
| Host | Standalone MPC on **MPC OS 3.10.1 or later**, with Preferences → Enhancements → *Enable Enhancements* and *External Plugin Support* turned on |
| Plugin type | **Effect** (insert). 2 inputs, 2 outputs, processed in place |
| CPU families | One source, two builds: 32-bit ARM (Gen 1: MPC X, X SE, Live, Live II, One, One+, Key 61, Key 37, Force) and 64-bit ARM (Gen 2: Live III, XL and later) |
| Sample rate | 44.1 kHz, fixed by the host |
| Block size | 128 frames by default; anything from 32 to 512 must work |
| Primary test device | MPC Live III (Gen 2). Gen 1 verified through community testers (Section 15) |
| MIDI | None. Effects receive no MIDI on the MPC |
| Screen | 1280 × 800; a tab's content area is 1280 × 628, starting 110 px down |

Not targets: MPC Desktop, MPC Studio controllers, MPC Sample.

---

## 2. Plugin Identity

`plugin.json`:

```json
{
  "name": "Hildegard",
  "manufacturer": "Building on Fire",
  "version": "0.1.0",
  "format": "vst2",
  "kind": "effect",
  "uid": "BfHg",
  "category": "Delay",
  "numInputs": 2,
  "numOutputs": 2,
  "file32": "VST/hildegard-arm32.so",
  "file64": "VST/hildegard-arm64.so",
  "description": "Records a chunk of your track and plays it back through four tape-style playheads with independent speed, direction, loop, tone, pan and volume.",
  "author": "Building on Fire",
  "url": "https://github.com/buildingonfire/hildegard",
  "license": "MIT"
}
```

- Plugin folder on the drive: `VST2 Plugins/Building on Fire - VST - Hildegard/`
- `name`, `manufacturer` and `uid` must match what the binary reports. **Never change `uid` after the first public release**, or saved projects lose the plugin.
- Repository: `github.com/buildingonfire/hildegard`

---

## 3. Signal Flow

```
Track input ──┬─────────────────────────────────────────────┐ dry
              │                                              ▼
              ├─► [Record source] ─► back buffer        [Dry/Wet] ─► soft clip (wet only) ─► track output
              │        ▲  (Resample on: taps the output)     ▲
              │        │                                     │ wet
   front buffer (frozen chunk) ─► Stream 1..4 ─► tone ─► pan ─► volume ─► mute ─► Σ
```

### 3.1 The Buffer

- One shared stereo buffer, **maximum 30 seconds** (1,323,000 frames).
- **Double-buffered:** the *front* buffer is what the streams play; recording writes into the *back* buffer. When recording ends, the buffers swap (Section 3.3), so playback never gaps.
- Allocate both buffers once, in `prepare()` or the constructor (never in `process()`). Float32 stereo: about 10.6 MB each, 21 MB total. If memory proves tight on Gen 1, store as int16 and convert on read.
- Before the first recording the front buffer is empty: streams output silence, the waveform shows nothing, and the status reads "Press Record".

### 3.2 Recording

**Record source (Resample button):**
- **Resample off (default):** records the track's input, before Hildegard.
- **Resample on:** records Hildegard's output exactly as heard (dry/wet mix included, after the soft clip). At 100% wet, only the streams are recorded, giving a pure feedback-loop resample.

**Length setting:** `Free`, `1 bar`, `2 bars`, `4 bars`, `8 bars`, `16 bars`. Default **Free**.

| Length | Press Record | Press Record again | Ends |
|---|---|---|---|
| Free | Recording starts immediately | Stops recording | Also auto-stops at 30 s |
| N bars | **Armed**: waits for the next bar line of the MPC transport | While armed: cancels. While recording: stops early | Auto-stops after N bars at the current tempo |

- Bar timing comes from the transport (`ctx.transport`: `playing`, `bpm`, `ppqPos`, `barStartPpq`). If the transport is stopped when armed, recording begins on the first bar line after the MPC starts.
- If N bars at the current tempo exceeds 30 s, the length's value text reads "16 bars (max 30 s)" and the recording is capped at 30 s. (The MPC cannot grey out individual list items, so this replaces the "greyed out" idea from the design notes.)
- Fade the first and last 5 ms of every recording so the chunk never starts or ends on a click.
- Status text (Section 7.2) shows `Armed`, `Recording`, then returns to `Ready`.

### 3.3 Swap and In/Out Preservation

- When a recording ends, all four streams crossfade from the old chunk to the new one over **~20 ms**.
- Each stream's in and out points are stored as **proportions (0–1) of the chunk**, so a loop over the middle third stays on the middle third of the new take. Each head keeps its relative position within its loop across the swap.
- Recompute the 128 waveform-display values (Section 7.3) after the swap, off the audio thread.

### 3.4 Streams (×4)

Each stream reads the front buffer independently:

- **Read head:** a double-precision position, advanced by `speed × (file rate ÷ 44100)` per frame (file rate is 44100 for recorded audio).
- **Interpolation:** 4-point Hermite (cubic). Cheap and noticeably smoother than linear when pitched down.
- **Speed:** −8× to +8×, tape-style (speed and pitch coupled). Negative plays in reverse; 0 holds the head still and fades the stream to silence (no DC, no clicks).
- **Loop region:** between the in and out points. If in > out, treat the region as running between them (swap internally). Minimum region length 10 ms.
- **Loop mode:**
  - *Loop:* at the region's end (or start, in reverse), wrap to the other end with a **~10 ms equal-power crossfade** that reads slightly past the boundary.
  - *Ping-pong:* at either end, reverse direction with a **~5 ms** declick fade.
- **Region edits:** if moving the in or out point leaves the head outside the region, jump it back inside with a short crossfade.

### 3.5 Per-Stream Processing

In order, after the read head:

1. **Tone (tilt EQ):** one knob, −100 to +100. Split with a one-pole crossover at about 700 Hz; at −100 the lows are +6 dB and highs −6 dB (darker), at +100 the reverse (brighter), 0 is flat. Use the SDK's `dsp/onepole.h`.
2. **Pan:** equal-power, L100 to R100.
3. **Volume:** 0–100%, perceptual (dB-shaped) taper.
4. **Mute:** ~10 ms fade in or out.

Smooth every continuous parameter (SDK `dsp/smoother.h`): about **50 ms** for volume and pan, about **20 ms** for tone and speed.

### 3.6 Output

- Sum the four streams into the wet signal.
- **Soft clip** the wet sum (gentle tanh-style saturation from about −3 dBFS) so stacked streams never produce harsh digital overs. The dry path is untouched.
- **Dry/Wet:** equal-power crossfade, 0–100%, default 50%.
- Remember processing is **in place**: read each frame's input before writing that frame's output.

### 3.7 Transport

- **MPC starts:** every stream jumps to its in point (or its out point if its speed is negative) with a 5 ms fade, and plays.
- **MPC stops:** every stream fades out over ~10 ms and holds. The dry signal keeps passing.
- All heads are always running while the transport plays; muting is the only way to silence a stream.

---

## 4. Speed Knob Snapping

The speed parameter snaps when the knob comes very close to a **landmark: 0, ±0.5×, ±1×, ±2×**. Everywhere else it moves freely.

Implement snapping in the parameter's mapping, not in the UI: the normalized 0–1 value maps to speed through a curve with a small **flat plateau** around each landmark (about 2% of the knob's travel each). A Q-Link turn passes through the plateau, so the value sits exactly on the landmark for a moment, then continues. This works identically for Q-Links, touch, the data wheel and automation.

Value text: two decimals with a multiplication sign, e.g. `1.00×`, `−0.50×`, `0.00×`.

**As built (2026-11-14):**
- The parameter stores a *knob position* from −8 to +8, not the speed. `speedFromKnob()` maps it through knots |x| 0→0, 1→0.5×, 2→1×, 4→2×, 8→8×. A straight −8…+8 line would squeeze 0–0.5× into 3% of the travel, leaving no room for the plateaus.
- Each landmark has a flat plateau of ±0.16 knob units, about 2% of the travel.
- The value text uses a plain `x` (`1.00x`) because the MPC font's support for `×` is unconfirmed.
- The knots are part of saved-project identity: changing them changes how existing projects sound.

---

## 5. Parameters

**Order matters:** indices are what the skin and Q-Links bind to, and projects save by name. Keep this order stable after the first release; add new parameters only at the end.

> **Host quirk:** the MPC nudges **parameter 0** to an extreme and straight back every time it prepares the plugin. Index 0 is therefore `Length`, which is only read when Record is pressed. Never put Record, Save or anything audible at index 0.

### 5.1 Global (0–4)

| Index | ID | Name | Type | Range / choices | Default |
|---|---|---|---|---|---|
| 0 | `length` | Length | choice | Free, 1 bar, 2 bars, 4 bars, 8 bars, 16 bars | Free |
| 1 | `record` | Record | bool (button) | Off / On | Off |
| 2 | `resample` | Resample | bool | Off / On | Off |
| 3 | `drywet` | Dry/Wet | float | 0–100 % | 50 % |
| 4 | `save` | Save | bool (trigger) | Off / On | Off |

`Record` and `Save` act as momentary buttons: the plugin reacts to the press and resets the value itself.

### 5.2 Per Stream (5–36)

For stream *s* (1–4), base index = 5 + (s − 1) × 8.

| Offset | ID | Name | Type | Range / choices | Default (S1 / S2 / S3 / S4) |
|---|---|---|---|---|---|
| +0 | `sN_speed` | Speed N | float, snapping (Section 4) | −8.00× to +8.00× | 1.00× / 0.50× / −1.00× / 2.00× |
| +1 | `sN_tone` | Tone N | float | −100 to +100 (text: darker / flat / brighter with amount) | 0 |
| +2 | `sN_pan` | Pan N | float | L100 to R100 | C / L30 / R30 / C |
| +3 | `sN_volume` | Volume N | float | 0–100 % | 80 / 65 / 60 / 50 % |
| +4 | `sN_in` | In N | float | 0–100 % of chunk | 0 % |
| +5 | `sN_out` | Out N | float | 0–100 % of chunk | 100 % |
| +6 | `sN_mode` | Mode N | choice | Loop, Ping-pong | Loop |
| +7 | `sN_mute` | Mute N | bool | display text "Stream N – Active" / "Stream N – Muted" | S1 Active; S2–S4 Muted |

Indices: Stream 1 = 5–12, Stream 2 = 13–20, Stream 3 = 21–28, Stream 4 = 29–36.

### 5.3 Display-Only (37–169)

Written by the plugin, read by the skin. Mark them non-automatable (`automatable = false`), and name them so they sort to the end of any list (e.g. prefix `~`).

| Index | ID | Purpose | Update rate |
|---|---|---|---|
| 37–40 | `pos1`–`pos4` | Each stream's head position, 0–1 across the whole chunk | ≤ 25 Hz, only while it changes |
| 41 | `status` | Status, as a choice: Press Record, Ready, Armed, Recording, Saving, Saved, Save failed, Buffer full | On change |
| 42–169 | `wave000`–`wave127` | Peak level of each 1/128 slice of the chunk, 0–1 | Once per new recording |

Update display parameters from a non-audio context (a timer or the host's idle call), never by allocating or logging in `process()`. Throttle updates; screen redraws are expensive on the MPC.

---

## 6. Q-Links

- **Bank direction: Column.** Each column of four Q-Links controls one stream, top to bottom.
- On MPCs with four Q-Links (such as the One and Live II), pressing the **Q-Link button** steps the four knobs from stream to stream. On MPCs with a full bank of 16 Q-Links (such as the MPC X and X SE), all four streams are on the knobs at once. Verify the knob count of each model during testing; the mapping works either way.
- Supply `qlinkBoundsData` with one rectangle per column, enclosing that column's four controls (not the stream-name button). **The MPC draws its own focus box there; Hildegard draws no outline of its own.**
- Program Mode default assignment: Stream 1's Mix column.

| Tab | Q-Link row 1 | Row 2 | Row 3 | Row 4 |
|---|---|---|---|---|
| **Mix** (per column) | Speed | Tone | Pan | Volume |
| **Loop** (per column) | In | Out | Mode | *(empty)* |

The Loop tab's fourth slot is deliberately empty: no control, no meter, no label.

Global controls (Record, Resample, Length, Dry/Wet, Save) are on-screen touch controls and are reachable through the data wheel; they are not on the Q-Links.

---

## 7. Screen

The skin is **hand-written** (`plugins/Hildegard/skins/`), because the SDK's `ui.json` generator cannot produce this layout. Use the generator only for the first on-device test (Section 14, step 5). See `docs/ui-reference.md`; the MPC rejects a whole skin file over a single wrong key, version or enum, so validate after every change.

A reference sketch accompanies this spec: `hildegard_mpc_screen_sketch.png`.

### 7.1 Tabs

Two tabs, side by side on the function keys:

- **F1 Mix** — the primary view; Hildegard opens here.
- **F2 Loop**

Both tabs share the same top band and waveform; only the four stream columns differ.

### 7.2 Layout (content area, 1280 × 628; values approximate)

| Region | Bounds (x y w h) | Contents |
|---|---|---|
| Top band | `20 8 1240 46` | Record button, Resample toggle, Length selector, Dry/Wet, status text; Save button at the right |
| Waveform | `20 62 1240 150` | 128 bars, four loop spans, four position markers (7.3) |
| Stream columns | four × `300 × 400`, from `y 222`, x at `20 / 333 / 646 / 959` | Name button, then four controls (7.4) |

Mind the MPC's Q-Link sidebar, which can appear over x ≥ 1025: column 4 sits under it, so `qlinkBoundsData` must be set.

**Status text:** a label bound to `status`'s value. Show "Save failed" and "Buffer full" in red using a second, red label made visible only for those states (`IndexedEnabling`); every other state uses the normal text color.

**Record button:** three looks by state (idle, armed, recording), driven by a film strip following `status` or `record`.

### 7.3 Waveform Display

- **Bars:** 128 thin vertical bars, each a `Meter` (direction `UpDown` if it renders centred bars, otherwise `Up`) bound to one `waveNNN` parameter. Neutral gray. Static between recordings.
- **Loop spans:** one horizontal line per stream, clustered near the bottom of the waveform at slightly different heights, spanning that stream's in to out point, in the stream's color.
- **Position markers:** one vertical marker per stream at its head position, rising from its span line. Implement as a film strip whose frame follows `posN` (e.g. 128 frames, each with the marker one step further right), or another approach that proves cheaper on the device.
- **Marker height = stream volume** was part of the iPad design. Treat it as a stretch goal: if no widget can vary both position and height, use a fixed height.
- Spans may be built from horizontal `HaloDisplay` or `Meter` bars bound to the in/out parameters. If that can't be made to work, fall back to short ticks at the in and out points.

### 7.4 Stream Columns

Each column, top to bottom:

1. **Stream name button** (full column width, ~42 px tall). Tapping it toggles `sN_mute`.
   - Active: filled with the stream's color, dark text "Stream N – Active".
   - Muted: filled gray, light text "Stream N – Muted". The button stays fully readable.
2. **Four controls** (Mix: Speed, Tone, Pan, Volume; Loop: In, Out, Mode, empty). Each shows name, value text, and a horizontal bar filled from the left in the stream's color (bipolar fill from the centre for Tone and Pan).
3. **Mode control (Loop tab):** two image buttons side by side, a loop icon and a ping-pong icon (`|<->|`), as a two-button radio group bound to `sN_mode`. The active one is filled with the stream's color. Turning the Q-Link toggles between them.

**Muted streams** dim their whole column except the name button, and dim their loop span and marker in the waveform (still visible, not prominent). Use `IndexedEnabling` on `sN_mute` to show dimmed variants or a translucent overlay.

### 7.5 Colors

| Use | Color (ARGB) |
|---|---|
| Stream 1 | Orange `ffEF9F27` |
| Stream 2 | Yellow `ffFAC775` (dark text on it) |
| Stream 3 | Green `ff7FB33A` |
| Stream 4 | Blue `ff4A9BE8` |
| Muted button | Gray `ff55555C` |
| Background / panels / lines | `ff161618` / `ff222226` / `ff3A3A40` |
| **Errors only** | Red `ffE24B4A` — never used for anything except error states |

Fonts: use the MPC's built-in families; custom fonts can't be shipped.

---

## 8. Save to Disk

- **Save** writes the current front buffer as a WAV: **44.1 kHz, 24-bit, stereo**, trimmed to the recorded length.
- Folder: **`Hildegard Loops/`** at the root of the drive Hildegard is installed on. Find the drive root from `mpc::pluginFolder()` (the plugin folder is `<drive>/VST2 Plugins/<folder>/`, so the root is two levels up). Create the folder if it doesn't exist.
- Filename: `Hildegard_YYYYMMDD_HHMMSS.wav`. Never overwrite; add a suffix if the name exists.
- Write on a **background thread**. Hold a reference to the buffer being written so a new recording's swap can't free or overwrite it mid-write (copy it, or use a reference-counted chunk).
- Status: `Saving` → `Saved` (for a few seconds) or `Save failed` (red). On failure, log the reason; never crash and never block audio.
- The files are ordinary WAVs, so the MPC's own browser can load them into programs or tracks. In v2, Hildegard the instrument reads this folder.

---

## 9. Project State

- All parameters save with the MPC project automatically (the framework stores them by name).
- **Also save the chunk itself** in the extra state (`saveExtraState` / `loadExtraState`), as int16 stereo (about 5.3 MB at 30 s), so a reopened project still has its loop. Follow the framework's rule: decode into a staging buffer in `loadExtraState()`, swap it in from `reset()`.
- After loading, recompute the waveform display values.

---

## 10. Errors and Edge Cases

| Situation | Behavior |
|---|---|
| No chunk yet | Streams silent; status "Press Record"; waveform empty |
| Recording reaches 30 s | Stops, swaps in, status "Buffer full" briefly (red) then "Ready" |
| Bar-synced record, transport stopped | Stays armed; starts on the first bar line after the MPC starts |
| Tempo unavailable (`tempoValid` false) | Bar lengths behave like Free |
| Save fails (read-only drive, drive removed, disk full) | Status "Save failed" in red; audio unaffected |
| In point equals out point | Enforce the 10 ms minimum region |
| Sample rate other than 44.1 kHz | Not expected on the MPC; if it occurs, scale read increments by the rate ratio |
| Block size changes | Handle any size 32–512 without allocation in `process()` |

---

## 11. Performance Budget

- Target: **well under 15% CPU per instance on a Gen 1 (32-bit) MPC**, per Akai's guidance. Gen 2 has more headroom; design for Gen 1.
- The engine is light: four interpolated read heads, four tilt filters, panning and a soft clip. The main risks are UI-side (display parameter updates and redraws), not DSP.
- No allocation, locking, logging or file access in `process()`.

---

## 12. Out of Scope for v1

Effects inside Hildegard (delay, reverb, inserts, EQ beyond tone), scale or key quantizing, MIDI and pad control, a live rolling buffer, sample file loading, classic overdub, and a position knob.

---

## 13. Risks to Prototype First

In this order, on the Live III, before building the full UI:

1. **Display parameters.** Can plugin-written, non-automatable parameters drive `Meter`, film strip and label widgets, and does the screen update when they change? Does 128 waveform parameters plus 4 positions clutter the MPC's automation or parameter lists, or slow the screen thread? If the position markers stutter, lower their update rate before anything else.
2. **Writing to the drive root.** Akai's docs cover reading files inside the plugin folder, not writing elsewhere. Confirm a WAV can be written to `<drive>/Hildegard Loops/` on a stock Live III. Fallback if not: write inside the plugin folder (`Content/Saved/`) and tell the user where.
3. **Effect I/O and transport.** Confirm in-place processing, transport start/stop detection, and bar-line timing on the device.
4. **Skin strictness.** Build the hand-written skin incrementally; a single invalid key hides every tab.
5. **Gen 1 CPU.** Benchmark through community testers before the first public release.

---

## 14. Build Order

1. Clone the SDK. Read `AGENTS.md`, `CLAUDE.md`, `docs/overview.md`, `docs/host-contract.md`, `docs/plugin-folder-format.md`, `docs/ui-guide.md`. Scaffold an effect named Hildegard with `tools/new_plugin.py`; set `plugin.json` per Section 2.
2. **Engine core, tested in the host simulator:** Free recording into the back buffer, swap, one stream with speed (including reverse), loop mode, and crossfades. Render WAVs and listen.
3. **All four streams:** in/out, ping-pong, tone, pan, volume, mute, dry/wet, soft clip, smoothing, speed snapping.
4. **Transport:** follow start/stop, bar-synced recording, Resample.
5. **First on-device test:** a generated skin from `ui.json` (Mix and Loop pages, four controls per stream column). Install on the Live III and play with it.
6. **Prototype risk 1 (display)** on the device.
7. **Hand-written skin:** full layout per Section 7.
8. **Save to disk** (risk 2), status text and error display.
9. **Project state:** save and restore the chunk.
10. **Polish and release:** CPU benchmarks, Gen 1 testing, user guide (README), packaging, GitHub release, catalog listing (Section 15).

At every step, run the host simulator's `test` command and fix every FAIL before copying anything to the MPC: a plugin crash takes the whole MPC down.

---

## 15. Testing and Release

- **Host simulator:** `.sdk-tools/host_sim build/native/<slug>.<ext> test --seconds 3 --wav build/test.wav` (and `--playing` to simulate a running transport). `dump` prints the parameter list the MPC will see.
- **Validate:** `python tools/validate.py` before every device install.
- **Install:** copy `build/Building on Fire - VST - Hildegard/` into `VST2 Plugins/` on a drive, or use `tools/mpc_remote.py` over the network (it needs the MPC's developer mode, which opens SSH; turn it off on networks you don't control).
- **Package:** `python tools/package.py plugins/Hildegard`.
- **Release:** GitHub release on `buildingonfire/hildegard` with the packaged zip and source.
- **Catalog:** list Hildegard in the community Open MPC catalog (open-source licenses are accepted; releases are marked verified per hardware generation when testers confirm them). Ask testers for Gen 1 results before calling a release stable.
- **User guide (README):** what Hildegard does, install steps (including the two Enhancements switches), the Mix and Loop tabs, Record/Resample/Length/Save, where saved loops go, and the safety note that third-party plugins can crash the MPC.

---

## 16. Defaults to Confirm

Provisional choices made while writing this spec. Each is easy to change:

| Item | Provisional value |
|---|---|
| Stream speeds on load | 1× / 0.5× / −1× / 2× (an octave down, reverse, and an octave up are ready the moment you unmute) |
| Stream pans and volumes on load | C / L30 / R30 / C; 80 / 65 / 60 / 50 % |
| Dry/Wet default | 50 % |
| Tone range | ±6 dB tilt around ~700 Hz |
| Wet soft clip | On, from about −3 dBFS |
| Chunk saved with the project | Yes, int16 (~5.3 MB at 30 s) |
| Saved WAV format | 24-bit, 44.1 kHz, stereo |
| Saved loop folder | `Hildegard Loops/` at the drive root |
| Bar lengths offered | 1, 2, 4, 8, 16 bars |
| Per-stream level meters and CLIP banners | Not in v1 (the MPC's mixer meters the track; the soft clip prevents overs) |

---

## 17. Version 2 Candidates

- **Hildegard the instrument:** the same engine as an instrument plugin that loads loops from `Hildegard Loops/` (chosen by Q-Link through filenames) and receives MIDI, so pads can restart streams or jump heads to positions.
- A Position knob in the Loop tab's empty slot, moving the whole loop window through the chunk.
- Classic overdub: new input layered onto the stored chunk at its existing length.
- Longer maximum recording (60 s or 2 min).
- Tempo detection to suggest a capture length; key detection.

# Hildegard — device test checklist (Live III)

Everything below has been built and tested offline. None of it has run on the MPC yet.
Go in order: the early items decide whether the later ones are reachable at all.

Install: `python tools/build.py plugins/Hildegard`, then the `mpc-plugin-deploy` skill
(`tools/mpc_remote.py`). Restart the MPC after installing, then put Hildegard on an audio
or program track's insert.

## 1. Does the skin load?

- [ ] The plugin shows **three tabs** (RECORD, LOOP, MIX) on F1–F3.
  **No tabs at all** means the MPC rejected TUI.json. Fallback: strip the waveform band
  (`waveBand`: Meters, TrackAndThumb, the ColourBar mask) in `tools/gen_skin.py` first,
  since those are the least-proven widgets. Then reinstall and bisect.
- [ ] RECORD opens first. Tiles: Record, Resample, Save, Length; Dry/Wet bar; Status.
- [ ] LOOP and MIX each have four stream columns, and each column has a name button.

## 2. Waveform band (top of every page)

- [ ] Before any take, the band is empty and the status reads "Press Record".
- [ ] After a take, **128 bars** appear. If they don't, the full-page refresh isn't
      redrawing the Meters; try notifying each bar instead.
- [ ] Bars grow both up and down from the centre line.
- [ ] Each unmuted stream's **loop span** shows in its colour and follows Start/Length.
- [ ] A loop past the end (e.g. Start 80, Length 40) draws as two pieces: 80-100% and 0-20%.
- [ ] Each span's ends have a small bracket rising above the line, tall at the outer edge,
      the same in every loop mode; dim when the stream is muted.
- [ ] Turning Start or Length while a drone plays does not click or flutter; a wrapped loop
      plays smoothly across the end of the take.
- [ ] Each stream's **playhead marker** moves smoothly while playing and holds on Stop.
- [ ] Forward/Reverse loops: the marker wraps to exactly IN (not ~10% in). Inside the loop
      crossfade it moves at double speed and wraps at the fade's midpoint (display only).
      If it stutters, lower the update rate (`kUiPeriodMs`).
- [ ] Muting a stream dims its span, its marker and its column, but not its name button.

## 3. Controls and Q-Links

- [ ] Tap/drag every control; the value text and bar track it.
- [ ] Bipolar bars (Tone, Pan, Speed) fill from the centre; the others fill from the left.
- [ ] Length opens the menu overlay; Dry/Wet double-tap opens the knob overlay.
- [ ] Mode buttons act as a radio pair (loop / ping-pong).
- [ ] Q-Links: on RECORD, Q13 = Dry/Wet and Q9 = Length. On LOOP and MIX, each Q-Link
      column drives its stream's column top to bottom. The MPC's focus box sits on the right column.
- [ ] Program-mode Q-Links: each column is one stream's Speed, Tone, Pan, Volume.

## 4. Speed knob

- [ ] Turning Speed with a Q-Link **pauses briefly on 0, ±0.5×, ±1×, ±2×**. Check that the
      pause is noticeable but not sticky (plateau = `kSnapHalf`, ~2% of travel).
- [ ] The 0–1× range is comfortable to dial; 2–8× is coarser, which is fine.
- [ ] The value text reads like `1.00x`.
- [ ] Projects saved **before** this build will have the wrong speeds (the stored value
      is now a knob position). Expected; re-set them.

## 5. Recording

- [ ] Free: tap Record, play, tap again. The status goes Recording → Ready and the loop plays.
- [ ] **No click or gap when a new take replaces the old one** (20 ms crossfade).
- (Bar-synced recording was retired on 2026-11-16; every take is Free.)
- [ ] **Resample** is its own record button (since 2026-11-15): tap = take of what you hear
      (input + streams at the Dry/Wet mix), tap again = stop.
- [ ] Tapping Resample during a Record take (or vice versa) is ignored; the button springs back.
- [ ] Mode is Forward / Reverse / Ping-pong; Speed is 0-4x and right = faster in every mode.
      Switching Forward <-> Reverse while playing turns the head around without a click.
- [ ] **Stop the transport mid-take.** Expected: the take is kept and Record un-lights. If
      a fresh take starts instead, the MPC does not reset on stop, and something else is going on.
- [ ] Let a Free take run 30 s: the status shows "Buffer full" in red, then "Ready".

## 6. Save

- [ ] Save: the tile's icon turns to three dots (…) while it captures one pass and writes, then
      back to the Save icon ~0.75 s after the file is written. A WAV appears in `Hildegard Loops/`.
- [ ] Save on a read-only or removed drive shows "Save failed" in red.

## 7. Project state

- [ ] Record a take, save the MPC project, reload it. **The loop comes back** and the
      waveform redraws.
- [ ] A 30 s take saves and reloads without a noticeable stall (~5 MB of state). If the
      MPC chokes on that size, fall back to a shorter cap or a sidecar WAV.
- [ ] A project saved while Record was on reopens **not** recording. Record may show lit
      but idle: one tap releases it, the next tap starts a take. (It keeps its saved value
      so the state round-trips exactly; only the "rising edge" is disarmed after a load.)

## 8. Presets

- [ ] The preset list shows Init, Octave Stack, Reverse Swell, Half-Time, Stutter Grid,
      Ping-Pong Tape, Tape Chorus.
- [ ] Choosing one changes the knobs and keeps the recording.
- [ ] Loading a project keeps that project's knob settings. If the MPC re-applies the
      remembered preset over them, programs and projects are fighting.

## 9. CPU

- [ ] Note the MPC's CPU meter with all four streams playing. The target for Gen-1 is under 15%.

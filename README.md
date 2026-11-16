# Hildegard

An insert effect for Akai MPC standalone hardware, by Building on Fire. MIT licensed.

Hildegard records a chunk of the audio passing through its track into a frozen buffer,
then plays that buffer back through four independent tape-style playheads ("streams"),
each with its own speed, direction, loop region, tone, pan and volume. Think of it as a
controllable multitap delay and resampler: catch a phrase, bend it four ways, fold it
back into the mix. See `SPEC.md` for the full design and `docs/UI-LAYOUT.md` for the screen.

## This is a plugin project, not a standalone program

Hildegard is built with Akai's official **MPC Plugin SDK**
(<https://github.com/inmusic-software/mpc-plugin-builder-sdk>). This repo contains only
Hildegard's own source — no SDK code. To build it from source:

1. Clone the SDK: `git clone https://github.com/inmusic-software/mpc-plugin-builder-sdk`
2. Clone this repo into the SDK's `plugins/` folder as `plugins/Hildegard`.
3. From the SDK root: `python tools/build.py plugins/Hildegard`.

**Most people don't need to build it** — grab the packaged `.zip` from this repo's
Releases and copy the folder onto your MPC (see *Install on an MPC* below).

## Using it

Three pages, on the function keys:

- **F1 Record** (opens here): **Record** (tap to start, tap to stop), **Resample** (records
  what you hear: the input plus the loops at the Dry/Wet mix; the new take is normalised so
  its peak plays at -1 dBFS, boosting by at most +12 dB, so repeated resampling doesn't fade
  away), **Save** (bounce
  what you hear to a WAV; the tile lights while it saves), **Dry/Wet**, and **Playback Speed
  Ratios** (Octaves / Ratios / Smooth: how the Speed knobs snap).
- **F2 Loop**: per stream, **Start** and **Length** (the loop, % of the recording; a loop that
  runs past the end of the recording carries on from its beginning), **Mode** (Forward,
  Reverse or Ping-pong) and **Speed**.
- **F3 Mix**: per stream, **Tone** (dark ↔ bright tilt), **Pan** and **Volume**.

Tap a stream's name to mute or unmute it. The waveform across the top of every page shows
the recording, each stream's loop (a line that may wrap from the right edge round to the
left) and where each playhead is. A small bracket rises at each end of a loop line, like a
DAW's loop markers, so you can tell the start from the end when a loop wraps.

- **Record** always records freely: tap to start, tap to stop, up to 30 seconds.
  (Hildegard is built for harmonic material first: drones, layers, resampling. Loops are
  not tempo-locked, and a little drift is part of the sound.)
- **Start / Length while playing**: moving them doesn't disturb what is playing; the loop's
  ends move and you hear the change when the playhead reaches them. If the loop moves away
  from the playhead, it jumps into the loop with a short crossfade.
- **Speed** runs from 0 to 4× (0 = hold and silent); the direction comes from Mode. With
  Playback Speed Ratios on Octaves or Ratios it snaps to musical intervals; Smooth doesn't.
- **Transport**: streams play while the MPC plays, fade out and hold on Stop, and restart
  from their loop starts on Play (Reverse: the loop's end). The dry signal always passes.
- **Save** writes a 24-bit/44.1 kHz stereo WAV of the *effected* sound (one loop length)
  to `Hildegard Loops/` at the root of the drive Hildegard is installed on.
- **Projects** keep the recording: saving an MPC project stores the recorded chunk with
  it (about 5 MB for a full 30 s take), so reopening the project brings the loop back.
- **Presets**: Init, Octave Stack, Reverse Swell, Half-Time, Stutter Grid, Ping-Pong Tape,
  Tape Chorus. Presets change the knobs only; they never touch the recording.

## Status (honest)

Early development (v0.1.0, pre-release).

**Heard and checked on a real MPC Live III:** the engine (recording, four streams, speed
and reverse, loop with its crossfade, ping-pong, tone, pan, volume, mute, dry/wet, soft
clip), transport-follow, Save to `Hildegard Loops/`, and that plugin-written display
values redraw when the plugin notifies the MPC.

**Built and tested offline only, not yet on the device** (host simulator, `tools/validate.py`
and the `_verify/` driver):
- The hand-written skin (`skins/`): the three pages, waveform, loop spans, playhead
  markers, status text, mute dimming, Q-Link maps.
- The sample-accurate take swap with a ~20 ms per-stream crossfade from the old recording
  to the new one (no click).
- The speed-knob curve and snapping.
- Saving the recording with the project, and the built-in presets.
- If the MPC resets the plugin when its transport stops, a take in progress is now kept
  (as if Record had been tapped off) instead of being thrown away.

**Not done yet:** final colour pass.

**Release mode (after v1.0 is locked):** CPU benchmark on Gen-1 (32-bit) MPCs, release
packaging, submission to the plugin collection.

**Future version, if any:**
- A crossfade-length control. (Drawing the loop ends as long as the crossfade was tried on
  2026-11-16 and did not read well; the ends are fixed-size brackets.)

**For a possible rhythm-first sibling plugin (same idea, different plugin):**
- Tempo-exact looping: Fixed Loop Length (bar-synced recording) was removed from Hildegard
  on 2026-11-16. The bar-sync code is still in Hildegard's engine, switched off.
- Start a capture exactly on the downbeat.

**To confirm (SPEC §16):** the square-law volume taper and the tone range.

## Build

```
python tools/build.py plugins/Hildegard
.sdk-tools/host_sim <build/native/hildegard.*> test --wav build/test.wav
```

`tools/gen_skin.py` regenerates `skins/` (TUI.json, Q-Links.json and the PNGs); rerun it
after changing the layout or colours. `_verify/render.cpp` is an offline driver that
scripts Record, transport, state save/load and presets (the host simulator can't), and
prints PASS/FAIL per check; it is not part of the plugin.

## Install on an MPC

Requires MPC OS 3.10.1+, with Preferences → Enhancements (Beta) → Enable Enhancements and
External Plugin Support both on. Copy `build/Building on Fire - VST - Hildegard/` into a
`VST2 Plugins` folder on a drive, or install over the network with `tools/mpc_remote.py`.
Third-party plugins run unsandboxed: a plugin crash takes the whole MPC down.

## Project layout

- `plugin.json` — manifest. `uid` (`BfHg`) must match the C++ `uniqueId`; never change it after release.
- `src/Hildegard.cpp` — parameters and DSP.
- `skins/` — the hand-written touch-screen skin (generated by `tools/gen_skin.py`); it
  replaces anything `ui.json` would generate.
- `docs/UI-LAYOUT.md` — the locked screen layout.
- `build/`, `dist/` — build output and zips (gitignored).

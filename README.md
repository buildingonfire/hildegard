# Hildegard

An insert effect for Akai MPC standalone hardware, by Building on Fire. MIT licensed.

Hildegard records a chunk of the audio passing through its track into a frozen buffer,
then plays that buffer back through four independent tape-style playheads ("streams"),
each with its own speed, direction, loop region, tone, pan and volume. Think of it as a
controllable multitap delay and resampler: catch a phrase, bend it four ways, fold it
back into the mix. See `SPEC.md` for the full design.

## This is a plugin project, not a standalone program

Hildegard is built with Akai's official **MPC Plugin SDK**
(<https://github.com/inmusic-software/mpc-plugin-builder-sdk>). This repo contains only
Hildegard's own source — no SDK code. To build it from source:

1. Clone the SDK: `git clone https://github.com/inmusic-software/mpc-plugin-builder-sdk`
2. Clone this repo into the SDK's `plugins/` folder as `plugins/Hildegard`.
3. From the SDK root: `python tools/build.py plugins/Hildegard`.

**Most people don't need to build it** — grab the packaged `.zip` from this repo's
Releases and copy the folder onto your MPC (see *Install on an MPC* below).

## Status (honest)

Early development (v0.1.0, pre-release). Built against `SPEC.md`, following its build order.

**Working now — the engine core (SPEC build-order steps 2–3), verified in the host
simulator, an offline render, and by ear on a real MPC Live III:**
- 30-second stereo double buffer; Free-length record + buffer swap (currently a short
  wet fade-in across the swap, not yet the per-stream old→new chunk crossfade).
- Four streams: 4-point Hermite interpolation, tape speed −8×…+8× including reverse and
  hold-at-zero (fades to silence), loop (seamless end→start crossfade, default 10% of the
  loop length) and ping-pong.
- Per stream: tilt tone (~700 Hz), equal-power pan, square-law volume, mute; global
  dry/wet; wet soft clip; parameter smoothing.
- **Transport-follow** (SPEC §3.7): streams play when the MPC transport runs; on Stop they
  fade out and hold position (the recorded loop is kept in memory, not lost), and resume
  on Play. Dry always passes.
- **Save to disk**: Save bounces the *effected output* — what you hear, the four streams
  processed and mixed — captured live over one loop length, written as a 24-bit/44.1 kHz
  stereo WAV to `Hildegard Loops/` at the drive root (timestamped, never overwriting) on a
  background thread, with a `Content/Saved/` fallback. Files load straight into MPC kits or
  audio tracks. (A clean/raw-chunk save option could be added as a switch later.)
- Realtime-clean: no allocation/locks/IO in `process()`; buffers allocated once in
  `prepare()`; no per-sample transcendentals in the inner loop. ~0.1% CPU per instance on
  a desktop (Gen-1 ARM not yet benchmarked).

**Not implemented yet (later build-order steps):**
- Display parameters (positions, status, 128-bar waveform) and the on-screen refresh —
  the mechanism is proven (notify the host) but not wired in. The current `ui.json` is a
  placeholder of generic sliders, not the hand-written skin from SPEC §7.
- Bar-synced recording (Length = N bars); Free-length recording works now.
- The speed-knob plateau/snapping curve (speed is a plain linear parameter for now).
- Project-chunk state (saving the chunk with the project), built-in presets.
- On-screen Save feedback ("Saving"/"Saved") arrives with the display-parameter layer;
  for now the saved file simply appears in the browser.

**To confirm (SPEC §16):** the square-law volume taper and the tone range.

## Build

```
python tools/build.py plugins/Hildegard
.sdk-tools/host_sim <build/native/hildegard.*> test --wav build/test.wav
```

`_verify/` holds a throwaway offline driver that records a test tone and renders the
streams playing it back (the host simulator can't script Record); it is not part of the
plugin. Delete it once the engine is trusted.

## Install on an MPC

Requires MPC OS 3.10.1+, with Preferences → Enhancements (Beta) → Enable Enhancements and
External Plugin Support both on. Copy `build/Building on Fire - VST - Hildegard/` into a
`VST2 Plugins` folder on a drive, or install over the network with `tools/mpc_remote.py`.
Third-party plugins run unsandboxed: a plugin crash takes the whole MPC down.

## Project layout

- `plugin.json` — manifest. `uid` (`BfHg`) must match the C++ `uniqueId`; never change it after release.
- `src/Hildegard.cpp` — parameters and DSP.
- `ui.json` — placeholder screen layout (to be replaced by a hand-written skin).
- `build/`, `dist/` — build output and zips (gitignored).

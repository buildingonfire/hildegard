# Hildegard — MPC Feature List

*Running list. Supersedes the iPad/PWA feature list, which is kept for reference. Updated 13 Nov 2026.*

---

## Platform (locked)
- Akai MPC plugin, built with the official MPC Plugin SDK (MPC OS 3.10.1+, External Plugin Support enabled)
- Plugin type: **effect** (insert)
- One source builds both Gen 1 (32-bit ARM) and Gen 2 (64-bit ARM)
- Primary test device: MPC Live III (Gen 2); Gen 1 builds produced but untested on hardware
- **Audience: anyone running external plugins on an MPC** (no longer personal-use only)
  - Raises the bar on: Gen 1 testing, CPU budget, crash safety, graceful failure (e.g. Save can't write), sensible defaults, and a short user guide
- iPad / PWA deprioritized

## Concept (locked)
- A controllable multitap delay / resampler
- Audio passing through the track is captured into one shared buffer
- Four playheads read from that buffer at independent speeds and directions
- Output returns into the same track, for a quick resample-and-return workflow
- **Buffer behavior: frozen.** Recording is manual; the recorded chunk then loops until the next recording
- **Maximum recording length: 30 seconds** (v1). A Free recording stops itself at 30 s; bar lengths that wouldn't fit at the current tempo are greyed out
- **Record length:** a setting of Free or 1, 2, 4, 8… bars; **defaults to Free**
  - Bar-synced: Record arms, starts on the next bar line, stops itself at the set length
  - Free: Record starts immediately; press again to stop
- **Never a gap:** playheads keep looping the old chunk while recording; the new chunk replaces it the moment recording ends
- In/out points keep their relative positions in each new chunk
- **Resample button** (next to Record), off by default:
  - Off: Record grabs the clean input
  - On: Record grabs Hildegard's output as you hear it (streams plus dry input, per dry/wet); the new chunk replaces the old, so each take is a new generation
  - At fully wet, only the streams are recorded: a pure feedback loop
- **Save button:** writes the current chunk as a WAV to a shared folder on the drive (e.g. `Hildegard Loops/`), in the background; files are ordinary WAVs the MPC browser can also load
  - Risk: writing outside the plugin folder isn't documented by Akai; test early on the Live III
- **Sound over sample-accuracy:** short crossfades at loop wraps, ping-pong turns, the chunk swap, mute/unmute, and transport start/stop, so Hildegard always sounds smooth

## Playheads (carried over)
- Four playheads, colors: 1 Orange, 2 Yellow, 3 Green, 4 Blue
- Red reserved exclusively for clip and error states
- Speed: −8× to +8×, tape-style (speed and pitch coupled); negative = reverse; 0 = stopped
- Speed snaps when the knob comes very close to a landmark: **0, ±0.5×, ±1×, ±2×**; free everywhere else
- Loop mode per playhead: loop or ping-pong
- In and out points per playhead
- All heads are always running; **each stream's name is a button at the top of its column; tapping it toggles mute**
  - Active: button filled with the stream's color, reading "Stream 1 – Active"
  - Muted: button filled gray, reading "Stream 2 – Muted"; the button stays fully readable while the rest of the column dims
  - Never red
- On load: stream 1 unmuted, streams 2–4 muted
- **Muted streams are dimmed:** their control column is visibly dimmed, and their loop span and position spike in the waveform stay visible but faint
  - Implementation: show/hide rules switch a dimmed overlay and dimmed markers on when a stream's Mute is on
- **Follows the MPC transport:** when the MPC starts, every stream restarts from its in point; when it stops, they stop

## Layout and Q-Links (locked)
- Each playhead is one audio stream, drawn as a vertical strip; signal flows screen top to bottom
- Strip order, top to bottom: **speed, tone, pan, volume**
- One bank of four Q-Links controls one stream (matches the 4-knob MPC One and Live)
- Pressing the Q-Link button jumps to the next stream; the MPC's own Q-Link focus box marks the active strip, so Hildegard draws no outline of its own
- On MPCs with 16 Q-Links (such as the MPC X and X SE) all four streams are on the knobs at once
- **One shared buffer:** a single on-screen Record button fills it; all four streams read from it
- **Two tabs, side by side on the function keys**, same four-column structure:
  - **F1 Mix (primary view, opens first):** speed, tone, pan, volume; tap the stream name to mute
  - **F2 Loop:** in point, out point, loop mode; the fourth Q-Link slot is left empty (no control, meter or label)
  - Loop mode is two icons, not a slider: a loop icon and a ping-pong icon (|<->|); the active one is lit in the stream's color; turning the Q-Link toggles between them
- Global band across the top of both tabs: Record, length, dry/wet

## Mix
- Per-playhead volume and pan
- Per-playhead tone: a single tilt knob (left darker, right brighter, center flat)
- Level meters, with a latching CLIP indicator that is tapped to reset
- Dry/wet (new; needed for an insert effect)

## Display (proposed, pending confirmation)
- Function-key tabs replace the Zone 3 view switcher
- Coarse static waveform: 128 thin bars, each driven by a read-only parameter, refreshed after each capture
  - Check: 128 display-only parameters must not clutter the MPC's automation list or slow the screen
- Live playhead markers driven by read-only position parameters
- Risk: frequent screen updates are expensive on the MPC; prototype early on the device

## Dropped
- All effects: per-playhead inserts (bit reduction, chorus, filter), delay and reverb sends, tone EQ — the MPC's own effect chain covers these
- Sample file loading — replaced by capture
- 2D touch pads — vol/pan move to Q-Links (details in UI phase)
- Haptic detents — no haptics on the MPC
- Scale selector — dropped for now
- Master start — replaced by the MPC's own transport
- Pad control — effects receive no MIDI; control is touchscreen, Q-Links and sequencer automation

## Distribution (locked)
- **Free and open source**, published on GitHub
- Listed in the community Open MPC plugin catalog, whose testers verify releases on Gen 1 and Gen 2 hardware
- License: **MIT** (matches Akai's SDK)
- **Manufacturer name: Building on Fire.** Plugin folder: `Building on Fire - VST - Hildegard`
- Repository on the GitHub account `buildingonfire` (artist name: Building on Fire)
- Gen 1 testing: community testers; possibly an MPC X SE (Gen 1, 16 Q-Links, so it also tests the all-streams-on-knobs layout)

## Open Questions
- None. Technical spec written: SPEC.md (see its Defaults to Confirm section)

## V2 Candidates
- Position knob in the empty Loop-tab slot: moves the whole loop window through the chunk
- Classic overdub: new input layered onto the stored chunk at its existing length
- **Hildegard the instrument:** same engine as the effect; loads loops from the shared save folder (picked by Q-Link through filenames); receives MIDI, so pads can restart streams or jump heads to positions
- Tempo detection → capture length suggestion
- Key detection → scale suggestion

# Hildegard — locked UI layout (v1)

Decided 2026-11-14 from the interactive mockup. This supersedes the top-band / two-tab
description in SPEC §6 and §7.1–7.2. It does **not** change parameter identity: indices and
ids in SPEC §5 are fixed (0–36). This only defines *which parameter appears on which tab*
and how it's drawn. The hand-written skin (`TUI.json` + `Q-Links.json`) is built from this.

Visual reference (live mockup): https://claude.ai/code/artifact/8ea2f385-20bd-41cb-9fda-b52859fa473b

## Three tabs, left-to-right flow

Global controls live on their **own tab**, not in a top band. Function keys, in order:

1. **F1 — Record / Save** (landing page; shown when the plugin opens)
2. **F2 — Loop**
3. **F3 — Mix**

The flow reads capture → shape → blend: Record/Save, then set the loop, then mix it.

## Shared across every page: the waveform band

At the top of all three pages (content-area coords): **`20 8 1240 182`**.
128 bars + four loop spans (per-stream colour, staggered heights) + four position markers.
It's the one element that never moves between tabs.

## F1 — Record / Save

Below the waveform (`~20 206 1240 ~414`): the global controls, laid out large.
- **Record** (param `record`, idx 1) — toggle; stays lit while recording (status film strip later).
- **Resample** (`resample`, idx 2) — toggle.
- **Save** (`save`, idx 4) — trigger; bounces the effected output.
- **Length** (`length`, idx 0) — selector: Free / 1 / 2 / 4 / 8 / 16 bars.
- **Dry/Wet** (`drywet`, idx 3) — value + unipolar bar.
- **Status** text (display param, later) — big and readable here; red for Save failed / Buffer full.

## F2 — Loop  and  F3 — Mix

Four stream columns, each **300 wide**, at x **20 / 333 / 646 / 959**, from y **206**, height **~414**.
Each column: a full-width **name button** (~42px, taps to toggle `sN_mute`), then four controls.

| Column slot | **F2 Loop** | **F3 Mix** |
|---|---|---|
| 1 | In (`sN_in`) | Tone (`sN_tone`) |
| 2 | Out (`sN_out`) | Pan (`sN_pan`) |
| 3 | Mode (`sN_mode`) | Volume (`sN_volume`) |
| 4 | **Speed** (`sN_speed`) | *(empty)* |

- **Speed moved from Mix to Loop** (slot 4). The empty slot is now on Mix (slot 4) — reserved
  (a per-stream meter is a possible v2 occupant).
- **Mode** is two image buttons (loop / ping-pong icons) as a radio bound to `sN_mode`.

## Control drawing

- Each control: name (left), value text (right), horizontal bar filled in the stream's colour.
- **Bipolar fill** (grows from centre): **Tone, Pan, Speed** (0 / C / 0× is the centre).
- **Unipolar fill** (from the left): **Volume, In, Out, Dry/Wet**.
- **Muted column**: dim everything except the name button; dim that stream's waveform span + marker.

## Q-Links (SPEC §6, updated)

Bank direction = column; each column's four Q-Links map top→bottom to its four controls:
- **Loop tab**: In, Out, Mode, Speed
- **Mix tab**: Tone, Pan, Volume, (empty)

Supply `qlinkBoundsData` per column; the MPC draws its own focus box (Hildegard draws none).

## Open item — colours

Colours currently follow SPEC §7.5 (S1 orange `EF9F27`, S2 yellow `FAC775` dark-text,
S3 green `7FB33A`, S4 blue `4A9BE8`; bg `161618` / panel `222226` / line `3A3A40`; error
`E24B4A`). **A final colour pass is still to come** — build the skin against these, expect to
revisit the palette before release.

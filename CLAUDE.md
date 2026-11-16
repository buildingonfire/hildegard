# CLAUDE.md

Hildegard is an Akai MPC effect plugin built with the MPC Plugin SDK. This repo holds
ONLY Hildegard's own source. The SDK is a separate checkout that sits beside it:

```
repos/mpc-plugin-builder-sdk   <- the SDK (Akai's repo; never edit it, never commit to it from here)
repos/hildegard                <- this repo
```

## Rules

- **Never put Hildegard files inside the SDK, and never copy SDK files into this repo.**
  One copy of each. If the SDK seems to need a change, stop and explain why.
- Read the SDK's instructions before working: `../mpc-plugin-builder-sdk/AGENTS.md`, and the
  skills in `../mpc-plugin-builder-sdk/.claude/skills/` (build, test, ui, dsp, deploy,
  troubleshoot, package, presets). Their paths are relative to the SDK root; this
  project's path from there is `../hildegard`.
- `SPEC.md` is the design spec; `docs/UI-LAYOUT.md` supersedes its UI sections.
  `docs/DEVICE-TEST.md` is the on-device test plan.

## Commands (run from the SDK root, `../mpc-plugin-builder-sdk`)

```
python tools/build.py ../hildegard
.sdk-tools/host_sim ../hildegard/build/native/hildegard.dll test --wav ../hildegard/build/test.wav
python tools/package.py ../hildegard
python tools/mpc_remote.py ...      # deploy to the MPC (mpc-plugin-deploy skill)
python tools/mpc_screen.py ...      # see/tap the MPC screen (on the SDK's mpc-screen-tool branch)
```

Skin: after any layout or colour change, run `python tools/gen_skin.py` from this repo's
root (it regenerates `skins/`, which overrides `ui.json`), then rebuild.

Git: commit and push from this folder (origin = github.com/buildingonfire/hildegard).
`build/` and `dist/` are ignored.

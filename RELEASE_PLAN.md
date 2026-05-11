# Release Plan — Reaction-Diffusion Studio

Goal: turn this working WSL/dev-tree project into something a creative user
(no Python knowledge) can install in one step on Windows, macOS, or Linux,
and optionally try in a browser without installing anything.

The simulation engines (`reaction_diffusion.py`, `reaction_diffusion_leaky.py`)
and the Tkinter GUI (`app.py`) are working — this plan is about packaging,
hygiene, and distribution, not about rewriting the science.

---

## 0. TL;DR on the Mac question

Yes, Mac users can absolutely run this as a double-click `.app`. The cleanest
path is **PyInstaller** producing a `.app` bundle that contains its own Python
interpreter and every dependency. The user downloads a `.dmg`, drags
"Reaction-Diffusion Studio.app" into Applications, and double-clicks. No
Python install, no `pip`, no terminal.

Two friction points to know about up front:

1. **Gatekeeper warning on first launch.** Unsigned apps show "cannot be opened
   because Apple cannot check it for malicious software." The user has to
   right-click → Open the first time, or we pay for an Apple Developer ID
   ($99/year) and notarize. For a free creative tool, document the right-click
   workaround and skip signing for v1.
2. **Apple Silicon vs Intel.** Either ship two binaries (`-arm64.dmg` and
   `-x86_64.dmg`) or build a universal2 binary. Arm64-only is fine if the
   audience is on M1/M2/M3/M4 Macs; ship Intel too if older Macs are likely.

A **web version** is also viable and complementary, not exclusive. Best
low-effort route: wrap the same engine functions in a Gradio app and host on
Hugging Face Spaces (free tier). Pros: zero install, shareable URL, works on
phones/iPads. Cons: long simulations are slow on shared CPU, file uploads have
size limits, concurrent users queue. Recommended as a "try before you install"
landing page, not a replacement for the desktop app.

See §6 and §7 for the concrete build steps for each path.

---

## 1. Repo hygiene — clean up before packaging

The working tree currently has ~60 generated PNG/GIF/MP4 artifacts, scratch
files, and historical prompt docs mixed in with the source. None of this
should ship.

### 1.1 New repo lives in a SIBLING folder — do not touch the current one

The current working tree (`/mnt/c/Users/Raul/Documents/RD_clauded`) stays
exactly as it is. We start a fresh, clean repo next to it:

```
/mnt/c/Users/Raul/Documents/
    RD_clauded/          ← unchanged, full history + scratch + experiments
    rdstudio/            ← NEW clean release tree (proposed name; adjust if you prefer)
```

The new folder is its own git repo (`git init` inside it), with only the
code+assets we actually want to ship. Generated images, prompt-history docs,
and dev-only scripts stay behind in the original folder so they're never lost
but never confuse a fresh contributor either. Once the new repo builds an
installable `.app`/`.exe`, you can push *it* to GitHub and leave `RD_clauded`
as your personal scratchpad.

The first commit in the new repo is the moved + renamed source:

```
rdstudio/                          ← repo root
  pyproject.toml
  README.md
  LICENSE
  .gitignore
  assets/
    icon.icns / icon.ico
    examples/                      ← a few favorite input → output pairs
  rdstudio/                        ← Python package
    __init__.py                    # exposes main()
    __main__.py                    # `python -m rdstudio` → app.main()
    app.py                         # current app.py, simplified UI
    engine_classic.py              # current reaction_diffusion.py (renamed)
    engine_leaky.py                # current reaction_diffusion_leaky.py (renamed)
    presets.py                     # PATTERN_PRESETS + DEFAULT_PARAM_SETS (single source)
    bg.py                          # parse_bg + NAMED_BG_COLORS
    theme.py                       # ttkbootstrap setup, label dictionary
  pattern_thumbnails/              # pre-generated, shipped as data
  tests/
    test_animation_export.py       # moved from current _test_animation_export.py
  scripts/
    probe_trough_values.py         # diagnostic, not user-facing
```

`Reaction-Diffusion.pyw` is replaced by the `rdstudio` console-script entry
point declared in `pyproject.toml`.

### 1.2 Delete or relocate dev-only files

- `_test_animation_export.py` → move to `tests/test_animation_export.py`
- `probe_trough_values.py` → move to `scripts/` (one-off diagnostic, keep for
  reference but mark non-user-facing)
- `PROMPT_LEAKY_TOOL.md`, `NEXT_SESSION.md` → move to `docs/history/` or
  delete (these are prompt logs, not docs)

### 1.3 Purge generated artifacts from the working tree

All of these are sim outputs, not source:
`*_rd*.png`, `*_rd*.gif`, `*_rd*.mp4`, `*_leaky*.png`, `*_edges*.png`,
`*_quantized*.png`, `test*.png`, `test*.mp4`, `RD_test_*.mp4`, etc.

Action:
- Move the few "favorites" the project owner wants to keep into `examples/`
  and commit those.
- Delete the rest.
- Extend `.gitignore` to exclude future generated output by suffix
  (e.g. `*_rd.png`, `*_rd.gif`, `*_rd.mp4`, `*_quantized.png`,
  `*_params.tsv`, `*_leaky_*.png`, `*_edges.*`, `pattern_thumbnails/`).

### 1.4 Standard project files to add

- `README.md` — what it is, screenshots, install instructions per OS, run
  instructions, quick example.
- `LICENSE` — pick one (MIT is friendliest for a creative tool; Apache-2.0
  if patent grant matters).
- `CHANGELOG.md` — start now, even if v0.1.
- `pyproject.toml` — replaces `requirements.txt` (and keep requirements.txt
  as a fallback for pip users).

---

## 2. Code-level cleanup (low risk, before packaging)

These are safe small edits — they don't change behavior, they just make the
codebase easier to maintain and bundle.

1. **Single source of truth for presets.** `PATTERN_PRESETS` lives in `app.py`
   and `DEFAULT_PARAM_SETS` lives in `reaction_diffusion.py`. They duplicate
   each other. Move both into `rdstudio/presets.py` and import from there.
2. **Factor `parse_bg` out** of `reaction_diffusion.py` into `rdstudio/bg.py`
   so the leaky engine doesn't have to `import rd` just to call it.
3. **Reduce noisy stdout.** The engines `print()` progress messages — fine for
   the CLI, but in the GUI they go to a hidden console. Replace with a logger
   the GUI can silence and the CLI can configure.
4. **Make pattern thumbnails ship-able.** Currently generated on first run
   into `pattern_thumbnails/` next to `app.py`. In a PyInstaller bundle
   `__file__` points inside the bundle (read-only). Either:
   - Pre-generate the thumbnails and ship them inside the bundle as data, or
   - Write thumbnails to a user-writable cache dir
     (`~/Library/Caches/RDStudio` on Mac, `%LOCALAPPDATA%\RDStudio` on Windows,
     `~/.cache/rdstudio` on Linux — `platformdirs` handles this).
5. **macOS Tk warning.** Tkinter on Mac with the system Python looks dated.
   PyInstaller-bundled Python 3.11+ ships Tk 8.6, which is fine. Add a one-line
   check at startup that warns if Tk < 8.6.
6. **High-DPI on Windows.** Add `ctypes.windll.shcore.SetProcessDpiAwareness(1)`
   at startup on Windows so the canvas isn't blurry on 4K screens.
7. **Remove dead code.** `_stamp_patch` in `reaction_diffusion.py` is
   "kept for backward compatibility" but is no longer called — delete it.

---

## 3. Distribution targets

Pick how the user gets the software. We can do more than one.

| Target | What user does | Effort | Audience |
|---|---|---|---|
| **macOS `.app` in `.dmg`** | Download, drag to Applications, double-click | Medium | Mac creative users (the main ask) |
| **Windows `.exe` in installer or zip** | Download `.zip`, double-click `.exe` | Medium | Windows users (you yourself) |
| **Linux AppImage / `.tar.gz`** | Download `.AppImage`, mark executable, run | Low (PyInstaller does it) | Linux users |
| **Hugging Face Spaces (Gradio)** | Open a URL | Low | Anyone with a browser; "try before install" |
| **`pip install rdstudio`** | `pip install rdstudio && rdstudio` | Low (after pyproject.toml) | Python users |

Plan for v1: ship **macOS `.app`**, **Windows `.exe`**, **`pip install`**, and
optionally a **Gradio demo URL**. Skip Linux AppImage until someone asks.

---

## 4. Packaging — `pyproject.toml`

Replace `requirements.txt` with a `pyproject.toml` that declares dependencies,
the entry-point, and metadata. After this is in place, anyone with Python can
`pip install .` and get a `rdstudio` command on their PATH.

Sketch:

```toml
[project]
name = "rdstudio"
version = "0.1.0"
description = "Image-based Gray-Scott reaction-diffusion studio"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.10"
dependencies = [
    "numpy>=1.24",
    "Pillow>=9.0",
    "scikit-learn>=1.0",
    "scipy>=1.10",
    "imageio>=2.31",
    "imageio-ffmpeg>=0.4.9",
    "platformdirs>=4.0",
]

[project.optional-dependencies]
build = ["pyinstaller>=6.0"]
web = ["gradio>=4.0"]

[project.scripts]
rdstudio = "rdstudio.app:main"
rdstudio-cli = "rdstudio.engine_classic:main"

[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"
```

Keep `requirements.txt` as a generated copy (or just for pip users who don't
do `pip install .`).

---

## 5. macOS bundle — concrete build steps with PyInstaller

You can't build a Mac app from WSL. You need a Mac to do the actual build —
either your own Mac, a borrowed one, or a GitHub Actions `macos-latest`
runner (recommended, see §8).

On a Mac with the repo cloned:

```bash
# Use a clean venv with python.org's Python (not the system one — its Tk is old)
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .[build]

# Build
pyinstaller \
  --name "Reaction-Diffusion Studio" \
  --windowed \
  --icon assets/icon.icns \
  --add-data "pattern_thumbnails:pattern_thumbnails" \
  --osx-bundle-identifier com.rdstudio.app \
  rdstudio/__main__.py

# Result: dist/Reaction-Diffusion Studio.app
```

Wrap the `.app` in a `.dmg` with `create-dmg` (one Homebrew install):

```bash
brew install create-dmg
create-dmg \
  --volname "Reaction-Diffusion Studio" \
  --window-pos 200 120 --window-size 600 400 \
  --icon-size 100 \
  --icon "Reaction-Diffusion Studio.app" 175 190 \
  --app-drop-link 425 190 \
  "Reaction-Diffusion-Studio-0.1.0-arm64.dmg" \
  "dist/Reaction-Diffusion Studio.app"
```

### Gotchas to handle in code before the first Mac build

- `os.path.dirname(__file__)` does NOT point to a writable dir inside the
  `.app`. The thumbnail cache and any other writes must go to `platformdirs`.
- `imageio-ffmpeg` bundles its own ffmpeg binary on first use — pre-warm it
  during the build, or PyInstaller may miss it. Test MP4 export on the built
  `.app` before shipping.
- File dialogs and color pickers work out of the box on Mac via Tk; nothing to
  do.

### Code signing / notarization (skip for v1)

Without it, the user sees a Gatekeeper warning the first time. Document:
"Right-click the app, choose Open, click Open in the dialog. You only need to
do this once." If we later get an Apple Developer ID, add a notarize step
after `create-dmg`:

```bash
xcrun notarytool submit "Reaction-Diffusion-Studio-0.1.0.dmg" \
  --apple-id "you@example.com" --team-id ABCDE12345 --password "@keychain:notary" \
  --wait
xcrun stapler staple "Reaction-Diffusion-Studio-0.1.0.dmg"
```

---

## 6. Windows bundle — PyInstaller on Windows

On a Windows machine with the repo cloned (you have one):

```cmd
py -3.11 -m venv .venv
.venv\Scripts\activate
pip install -e .[build]

pyinstaller ^
  --name "Reaction-Diffusion Studio" ^
  --windowed ^
  --icon assets\icon.ico ^
  --add-data "pattern_thumbnails;pattern_thumbnails" ^
  rdstudio\__main__.py
```

Result: `dist\Reaction-Diffusion Studio\` (folder mode) or `dist\Reaction-Diffusion Studio.exe`
(single-file mode — slower startup but easier to ship).

Zip the folder for distribution, or build an installer with Inno Setup
(`assets/installer.iss`) if we want a real installer experience.

Windows SmartScreen will warn the first time too. Same right-click → Properties →
Unblock workaround; or buy a code-signing certificate later.

---

## 7. Online option — Gradio on Hugging Face Spaces

Lowest-effort web demo. The engine functions are already pure Python; only the
UI layer needs to change. Rough plan:

1. Add `rdstudio/web.py` that builds a Gradio interface with the same controls
   as the Tk app (image upload, n_colors slider, mode radio, pattern per color,
   max_iter, etc.) and calls the existing `simulate` / `simulate_leaky`
   functions.
2. Add `app.py` at the repo root (Spaces convention) that does
   `from rdstudio.web import demo; demo.launch()`.
3. Push the repo to a new Hugging Face Space with hardware = CPU (free) or
   CPU Upgrade (~$0.05/hr for users that need bigger images).
4. The Space gets a URL like `https://huggingface.co/spaces/<you>/rdstudio`.

What works well:
- Zero install, shareable link, works on phones.
- Same engine code as desktop — no duplication.

What doesn't:
- 10k iterations at 1024px take minutes on a CPU Space; users see a spinner.
  Cap `max_iter` and `working_max` to keep responses under ~60s, or queue jobs.
- File upload size limit (~10MB on free Spaces) — fine for typical inputs.
- Concurrent users queue on the same CPU.

Use this as a "try it in your browser" link from the README, not as the
primary distribution channel.

### Why not Pyodide / PyScript (browser-side Python)?

It works in principle — Pyodide ships numpy and scipy and sklearn — but the
sim loop is heavy and would be much slower than CPython native. The Tk UI
would need a full rewrite to HTML anyway. Not worth it; Gradio gives the same
"open a URL" UX with much less work.

---

## 8. Automate the builds — GitHub Actions

Once `pyproject.toml` and the PyInstaller specs are in, set up a release
workflow so tagging a version automatically produces the Mac + Windows
binaries. This is the real win — no more "I need to find a Mac to build."

Sketch (`.github/workflows/release.yml`):

```yaml
on:
  push:
    tags: ['v*']

jobs:
  build-mac:
    runs-on: macos-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.11' }
      - run: pip install -e .[build]
      - run: pyinstaller rdstudio.spec
      - run: |
          brew install create-dmg
          create-dmg ... "Reaction-Diffusion-Studio-${{ github.ref_name }}-arm64.dmg" \
            "dist/Reaction-Diffusion Studio.app"
      - uses: actions/upload-artifact@v4
        with: { name: mac-dmg, path: '*.dmg' }

  build-windows:
    runs-on: windows-latest
    # ...analogous...

  release:
    needs: [build-mac, build-windows]
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
      - uses: softprops/action-gh-release@v2
        with:
          files: |
            *.dmg
            *.zip
```

Result: pushing `git tag v0.1.0 && git push --tags` builds both binaries and
attaches them to a GitHub Release that users can download.

---

## 9. README — what creative users need to see

The README is the single biggest piece of "this works for non-developers"
real estate. Suggested structure:

1. **Hero image** — one of the existing favorites (e.g. `dandadanwavelet.gif`).
2. **What it does** — one paragraph + 3 example image pairs (input → output).
3. **Download** — three big buttons: macOS `.dmg`, Windows `.zip`, Try Online.
4. **Mac first-launch note** — the right-click → Open workaround, with a
   screenshot of the Gatekeeper dialog.
5. **Quick start** — five sentences: load image, set colors, pick patterns,
   press Run, Save.
6. **Pattern gallery** — thumbnail of each preset with one-line description.
7. **Advanced** — collapsible section for `pip install`, CLI usage,
   per-color density, leaky mode.
8. **Credits / license / link to source paper(s)** — Gray-Scott, Pearson, Munafo.

---

## 10. Sequencing — what to do in what order

If we run this in one or two work sessions, here's the order that gives the
biggest payoff per step and never breaks the working app. The current
`RD_clauded/` folder stays untouched the whole time — all new work happens
in the new sibling repo.

1. ✅ **Create the sibling folder + initial commit** (§1.1) — done.
2. ✅ **Code-level cleanup inside the new repo** (§2.1–2.4) — `bg.py` and
   `presets.py` extracted, writable platformdirs cache, dead code removed,
   tests/scripts relocated.
3. ✅ **`pyproject.toml` + entry point** (§4) — `rdstudio` and
   `rdstudio-cli` console scripts work after `pip install -e .`.
4. ✅ **UI simplification + ttkbootstrap theming** (§11, §12) — labels,
   sliders, View → Dark menu, Animation output promoted to top, single
   Pattern picker + Per-color popup, compact swatch strip with
   theme-aware borders, master pattern thumbnail, Advanced disclosure for
   niche sim controls, Reset to defaults button. ~11 commits.
5. ✅ **PyInstaller spec + Windows build script** (§6) —
   `rdstudio.spec` (cross-platform: same file builds on Win/Mac/Linux)
   and `scripts/build_windows.cmd` (one-step venv → install → build →
   zip) are in. Validated end-to-end with a Linux PyInstaller dry-run:
   bundle launches and enters the Tk mainloop. ⏳ The actual Windows
   build still needs to be run *on Windows* (cannot be done from WSL);
   double-click `scripts\build_windows.cmd` from cmd/PowerShell.
6. ⏳ **GitHub Actions for Mac + Windows** (§8) — `macos-latest` runner
   builds the `.app` without needing a Mac on hand.
7. ⏳ **README + LICENSE + first release** (§9, then `git tag v0.1.0`).
   LICENSE is already MIT; README is minimal — flesh out with
   screenshots, pattern gallery, install instructions per OS.
8. ⏳ **Gradio demo** (§7) — optional follow-up.
9. ⏳ **(Later)** Code signing / notarization if Gatekeeper friction
   starts hurting adoption.

### State at end of step 5

- `rdstudio.spec` (cross-platform) + `scripts/build_windows.cmd` checked in.
- `.gitignore` keeps `*.spec` excluded but **whitelists `rdstudio.spec`**
  so the build config is tracked.
- `rdstudio/__main__.py` uses an absolute import (`from rdstudio.app
  import main`); the relative form crashed the PyInstaller bundle at
  launch and would silently keep happening if reverted.
- Spec dry-run on Linux produced a working bundle (~216 MB) that
  launches into the Tk mainloop. Windows/Mac builds are expected to
  produce similar sizes.
- ⏳ Run `scripts\build_windows.cmd` on the Windows side to produce the
  first real `.exe`; that's the only remaining piece of step 5.

### State at end of step 4

- Working tree clean on `main`.
- `pip install -e .` works on WSL Python 3.12 and Windows Python 3.14.
- `python -m rdstudio` (or the `rdstudio` console script if Scripts is
  on PATH) launches the GUI with the ttkbootstrap `flatly` theme.
- Right-pane order is: Animation output / Mode / Input / Colors /
  Simulation settings, then Reset.
- Single global Pattern picker; `Per color...` opens a modal Toplevel
  for overrides; main combo shows `(mixed)` when per-color patterns
  differ. Selecting a real pattern while in mixed state triggers a
  confirm dialog.
- Master Pattern combo's inline thumbnail is wired through
  `master_pattern_var.trace_add("write", ...)`, not
  `<<ComboboxSelected>>` — the binding early-returned when no image
  was loaded.
- Swatch borders use the current theme's fg color and rebuild on
  theme toggle so white-on-white / black-on-black stays visible.

### Notes for whoever picks this up next (possibly a new session)

- The original `RD_clauded/` repo is **never** touched. All work
  happens in `rdstudio/` (sibling folder under `Documents/`).
- User preference: filled `bootstyle="primary"` (or `success` for Run,
  `danger` for Stop) reads as a "real button"; `outline-*` styles look
  disabled in the flatly theme — avoid them for primary actions.
- Animation output is intentionally the **first** group in the right
  pane (above Mode) — user wanted "do I want a video?" visible
  without scrolling.
- For PyInstaller (step 5): the build is driven by **`rdstudio.spec`**
  in the repo root. Don't pass the long `--windowed --name ...` CLI;
  just run `pyinstaller --noconfirm --clean rdstudio.spec`. On Windows
  the easier path is `scripts\build_windows.cmd`, which creates a venv,
  pip-installs `.[build]`, runs PyInstaller, and zips the result.
  The spec collects `ttkbootstrap` (themes are loaded by name at
  runtime), `imageio_ffmpeg` (bundles the ffmpeg binary so MP4 export
  works without a system install), and `imageio.plugins`. It auto-picks
  `.ico` on Windows, `.icns` on macOS, `.png` on Linux from `assets/`
  — **no icon files exist yet**, and the spec falls back gracefully to
  no icon if missing. Thumbnails already go to
  `platformdirs.user_cache_dir`, so the bundle's read-only `__file__`
  location doesn't bite. Verified caveat fixed in this round:
  `rdstudio/__main__.py` now uses an **absolute** `from rdstudio.app
  import main` because PyInstaller strips package context from the
  entry script — a relative import there crashed the frozen bundle on
  launch.
- The Microsoft Store Python on Windows installs console scripts to a
  Scripts directory that isn't on PATH by default. Document
  `python -m rdstudio` as the primary launch command in the README.

---

## 11. UI simplification — plain-language labels + Advanced disclosure

Chosen direction: **ttkbootstrap** (theme `flatly` for light, `darkly` for
dark — both shipped, user toggles in View menu). Drop-in over the existing
ttk widgets, so the layout work stays small.

### 11.1 What's always visible vs what hides behind "Advanced"

Two-panel structure. The right pane shows only the controls a creative user
actually touches; an "▸ Advanced" disclosure expands the rest.

**Always visible (creative-user level):**

| Current name → | New plain-language label |
|---|---|
| Load image... | **Load image** |
| Number of colors | **Number of colors** *(keep)* |
| Working size + Max side (px) | **Image size** (preset dropdown only — Small / Medium / Large / Huge) |
| Mode: Isolated zones (classic) | **Sharp zones** (radio) |
| Mode: Leaky channels (no walls; colors carried by V) | **Bleeding colors** (radio) |
| (per color) Pattern + per-color rows + Apply to all | **Pattern** (one dropdown applies to ALL colors) + **Per color...** button (see §11.3) |
| Pattern strength | **Pattern boldness** (slider — see §11.2) |
| Smooth sigma | **Edge softness** (slider — see §11.2) |
| Background | **Background** *(keep)* |
| Max iterations | **Simulation length** (Short / Medium / Long / Very long preset, with a Custom option) |
| Record animation + FPS | **Save as video** (checkbox + speed) |
| Run / Stop | **Run** / **Stop** |
| Save output as... | **Save image** |
| Save animation as... | **Save video** |

Plus a compact **color swatch strip** showing the detected palette with a
small checkbox under each swatch to enable/disable that color. The "remove
the white sky" workflow is common enough that disabling a color shouldn't
require opening the per-color panel.

**Hidden inside the Per color... popup (see §11.3):**

- Per-color pattern dropdown (override the main Pattern setting for any
  individual color)
- Per-color enable/disable (redundant with the swatch strip, but the popup
  shows it too for completeness)
- Per-color density picker *(only if "Advanced" toggle inside the popup is on)*
- "Apply to all" master row *(inside the popup — it's the same scope now)*

**Hidden under "▸ Advanced":**

- "Preview every N iterations" *(performance knob, never affects output)*
- Custom "Max side (px)" spinbox *(use Image size preset instead)*
- Seed variety / placement / size range
- Soft zone barrier checkbox *(default-on works for almost every image)*
- Pixel art mode checkbox *(only matters for pixel-art inputs — but **promote** to top-level whenever the loaded image has ≤64 unique colors, since you've already added pixel-art auto-detection in `_update_working_image`)*

Open question for you to confirm: **Pattern strength** and **Smooth sigma** —
based on the explanations above, do you want both visible (current default),
or one/both hidden under Advanced? Mark them in §13.

### 11.2 Sliders + live labels instead of bare spinboxes

Replace the spinbox+number for `pattern_strength`, `smooth_sigma`, and the
"Number of colors" spinbox with **ttkbootstrap sliders** that show their
current value next to the label and a one-line description under the slider.
Sliders communicate "this is a creative knob, drag it" much better than a
numeric spinbox does for a non-technical user.

Example for Pattern strength:

```
Pattern boldness:                 0.70
[==============●===============]
 subtle                         bold
```

### 11.3 Pattern picker — one knob by default, per-color popup on demand

Today: 6 controls per color row (checkbox + swatch + hex + pattern + thumbnail
+ density), times N colors, plus a master "Apply to all" row above. Dense and
form-like; most users don't need any of it.

**New default view — one pattern picker for the whole image:**

```
Pattern:  [ coral             ▾  🖼 ]   [ Per color... ]
```

- The dropdown shows pattern names with their thumbnail inline.
- Changing it sets every enabled color to that pattern instantly.
- The thumbnail to the right of the dropdown shows the current selection at
  larger size (preview of what that pattern looks like).

**Below it, a compact swatch strip:**

```
Colors:  ■ ■ ■ ■ ■
         x x x   x       ← checkbox below each swatch (x = enabled)
```

Click a swatch's checkbox to drop that color from the simulation (its region
becomes background). This is the "remove the white sky" workflow — it stays
one click away in the default view.

**Per color... popup (Toplevel window):**

Clicking the button opens a popup window titled "Per-color patterns" that
shows the full per-color rows, where users can override the global Pattern
setting per color. Layout inside the popup:

```
+-- Per-color patterns ----------------------------------+
|                                                        |
|   Quick set:  [ coral        ▾ ]  [ Apply to all ]     |
|                                                        |
|   [x]  ■■  Color 1     [ coral             ▾ ]  🖼    |
|   [x]  ■■  Color 2     [ labyrinth         ▾ ]  🖼    |
|   [x]  ■■  Color 3     [ maze              ▾ ]  🖼    |
|   [ ]  ■■  Color 4     [ —                 ▾ ]  🖼    |
|   [x]  ■■  Color 5     [ short worms       ▾ ]  🖼    |
|                                                        |
|   [x] Show advanced (density)                          |
|                                                        |
|                                          [ Done ]      |
+--------------------------------------------------------+
```

- The popup's "Quick set" row replaces the current "Apply to all" master row
  — same function, now scoped to the popup where per-color editing happens.
- The popup's "Show advanced" checkbox reveals the per-color density combo
  (currently always visible). Off by default.
- Closing the popup with **Done** commits the per-color overrides. The main
  Pattern dropdown then shows "(mixed)" instead of a single name to indicate
  per-color overrides are active. Reselecting any single pattern in the main
  dropdown wipes all per-color overrides and asks for confirmation.
- The popup is modal (blocks the main window) so there's no "which is the
  source of truth" ambiguity while it's open.

**Naming the trigger button — options to pick from in §13:**

- **Per color...** *(short, my recommendation)*
- **Customize per color...** *(clearer for first-time users)*
- **Customize...** *(generic — leaves room to add more advanced popups later)*
- **Different per color...** *(most explicit; longest)*

**Naming the main label — options:**

- **Pattern:** *(recommended — clean, no extra words needed when there's a
  Per color... button next to it)*
- **Pattern (all colors):**
- **Pattern style:**
- **Look:**

**Why a popup, not an inline expanding section?**

- Inline expansion would push everything below it (Pattern boldness, Edge
  softness, Background, Run button) down by 5–13 rows depending on color
  count. That hurts the layout the user just chose to keep simple.
- A modal popup makes "I'm in advanced mode now" visually explicit, then
  returns the user to the clean default view.
- Modal Toplevel is one of the easiest things in Tk — no scrolling-pane
  acrobatics needed.

### 11.4 Status / progress — friendlier wording

- "Iteration 4500/10000 (5/5 active)" → "Halfway done — 5 of 5 colors still
  growing"
- "Stopped." → "Stopped at your request."
- "Smoothing zone boundaries..." → "Preparing colors..."
- "Preparing fields..." → "Setting up the pattern..."

### 11.5 First-run experience

- Load a bundled example image (`assets/examples/example.png`) automatically
  if no image is loaded — so the user sees a working tool with quantization,
  not an empty canvas with "Load an image to begin."
- "?" help icon next to each control opens a tooltip with the plain-English
  meaning, plus a "low / high" pair of preview images.
- "Surprise me" button that randomizes mode + per-color patterns and runs —
  good for showing the tool's range without making the user learn the controls
  first.

---

## 12. Theme & polish — concrete steps for ttkbootstrap

1. Add `ttkbootstrap>=1.10` to `dependencies` in `pyproject.toml`.
2. Replace `import tkinter as tk` / `from tkinter import ttk` with
   `import ttkbootstrap as ttkb` and `from ttkbootstrap.constants import *`.
3. Swap `class App(tk.Tk)` → `class App(ttkb.Window)`. Constructor takes
   `themename="flatly"` (light) or `"darkly"` (dark).
4. ttkbootstrap's widgets inherit from ttk, so existing `ttk.Frame`,
   `ttk.Label`, `ttk.Button`, etc. keep working — they just look different.
   Only the `tk.Tk()`, `tk.Canvas`, and `tk.BooleanVar` calls need attention
   (Canvas stays, BooleanVar stays — Tk's own classes work fine inside a
   ttkbootstrap Window).
5. Swap colored "primary"/"success"/"danger" styles on key buttons:
   - **Run** → `bootstyle="success"` (green)
   - **Stop** → `bootstyle="danger"` (red)
   - **Load image** / **Save** → `bootstyle="primary"`
   - Subtle/utility buttons → default (gray)
6. Add a View → Light / Dark toggle in a menu bar. ttkbootstrap supports live
   theme switching via `style.theme_use("darkly")`.
7. Window icon: `self.iconphoto(False, ImageTk.PhotoImage(...))` with a 128px
   PNG. Use `.ico` and `.icns` versions for the PyInstaller bundle.
8. Sanity check on macOS: ttkbootstrap themes override native rendering on
   Mac, which is what we want — the macOS default Tk look is dated. Verify
   font sizes are still readable on Retina displays (ttkbootstrap defaults to
   slightly larger fonts than stock Tk, which is good).

Acceptance test for "looks good": screenshot side-by-side with the current
clam-theme look. Ship when the difference is obvious.

---

## 13. Open questions / decisions to make before starting

- **Sibling folder name**: `rdstudio/` next to `RD_clauded/`? Or another name
  (e.g. `RD_Studio/`, `reaction-diffusion-studio/`)?
- **Pattern boldness + Edge softness visibility**: keep both visible? Move
  Edge softness to Advanced (changes shape rather than color, so arguably less
  obviously a "creative knob")? Move both? Per §11.1.
- **Pattern picker labels** (§11.3): main label = "Pattern:" / "Pattern (all
  colors):" / "Pattern style:" / "Look:"? Button = "Per color..." /
  "Customize per color..." / "Customize..." / "Different per color..."?
- **License**: MIT vs Apache-2.0? (recommend MIT for v1.)
- **App name and bundle identifier**: "Reaction-Diffusion Studio" /
  `com.rdstudio.app`?
- **Icon**: do we have artwork, or do we need to commission/make one? Required
  in `.ico` (Windows), `.icns` (Mac), `.png` (Linux).
- **Mac architectures**: arm64 only, or arm64 + x86_64 (universal2)?
- **Theme default**: light (`flatly`) or dark (`darkly`) on first launch?
- **Hosting for the Gradio demo**: HF Spaces free CPU, or pay for upgrade?
- **Bundled example image**: which input image should ship as the first-run
  default?

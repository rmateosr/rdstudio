# Pickups for the next session

Status as of v0.1.0 (committed at tag `v0.1.0`, released
https://github.com/rmateosr/rdstudio/releases/tag/v0.1.0):

- Mac DMG (arm64) and Windows zip download from GitHub Releases.
- `RELEASE_PLAN.md` steps 1–7 done. Steps 8 (Gradio) and 9 (code signing) open.

Three small-to-medium pickups, in rough order of payoff. Each is self-contained;
do them in any order or skip any of them.

---

## 1. App icon

**What.** The app currently uses the default Python feather icon in the
Windows taskbar / macOS dock. Adding a real icon makes it feel less
prototype-y. The PyInstaller spec at `rdstudio.spec` already auto-picks:

- `assets/icon.ico` on Windows
- `assets/icon.icns` on macOS
- `assets/icon.png` on Linux

When any of these exist, the spec embeds them. None exist yet.

**How.** Create or commission a single 1024×1024 PNG, then convert:

```bash
# .icns (macOS) — requires `iconutil`, ships with Xcode CLT
mkdir icon.iconset && sips -z 16 16 source.png -o icon.iconset/icon_16x16.png && ...  # repeat for 32, 64, 128, 256, 512, 1024
iconutil -c icns icon.iconset -o assets/icon.icns

# .ico (Windows) — Pillow can do it
python -c "from PIL import Image; im=Image.open('source.png'); im.save('assets/icon.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
```

Alternatively, an online converter for all three is faster — search
"png to icns ico". Drop the three files into `assets/`, commit, and the next
GitHub Actions build will pick them up automatically.

In the GUI itself, also set the window icon at runtime so non-bundled
launches (`python -m rdstudio`) get it too:

```python
# rdstudio/app.py inside App.__init__, after self.title(...)
from PIL import Image, ImageTk
icon_pil = Image.open(os.path.join(os.path.dirname(__file__), "..", "assets", "icon.png"))
self.iconphoto(False, ImageTk.PhotoImage(icon_pil))
```

The path needs frozen-aware handling — when bundled, `assets/` isn't
sibling to `__file__`. Use `sys._MEIPASS` if frozen, else relative.

---

## 2. Remove the `labyrinth` preset

**What.** Drop `labyrinth` from the engine entirely. It's already gone from
the README gallery (commit `b999676`).

**How.** `rdstudio/presets.py` defines `PATTERN_PRESETS` as a dict. Delete
the `"labyrinth": (...)` entry. The app derives the dropdown list from this
dict, so the UI updates automatically.

Then grep for any per-color defaults or test fixtures referencing the name:

```bash
grep -rn "labyrinth" rdstudio/ tests/ scripts/
```

If `assets/gallery/labyrinth.png` ever comes back, leave it deleted —
already removed in `b999676`. Also remove the cached
`pattern_thumbnails/labyrinth.png` (which is in the user's cache dir, not
the repo — `os.path.join(user_cache_dir("RDStudio", "RNMateos"),
"pattern_thumbnails")`).

Bump version to `0.1.1`, tag, push — release pipeline handles the rest.

---

## 3. Gradio "try in your browser" demo (RELEASE_PLAN §7)

**What.** A Hugging Face Space that runs the engine in the browser. Users
who don't want to download anything can try the tool from a URL. Same
engine code, just a different UI layer.

**How.** Create `rdstudio/web.py` that builds a Gradio Interface around
`engine_classic.simulate` and `engine_leaky.simulate_leaky`. Then a
top-level `app.py` (Spaces convention) does:

```python
from rdstudio.web import demo
demo.launch()
```

Cap `max_iter` and image size aggressively (≤512 px, ≤3000 iter) so
free-tier CPU users don't queue forever. Spaces hardware = CPU Basic
(free) is the right starting point.

`pyproject.toml` already has `[project.optional-dependencies] web =
["gradio>=4.0"]`. Push the repo to a new Space at
`huggingface.co/spaces/rmateosr/rdstudio`; HF auto-detects `app.py` and
runs it. Add a "Try it in your browser" button to the README pointing at
the Space URL.

What won't work as well: long simulations (CPU is slow), large file uploads
(10 MB cap on free Spaces), concurrent users (they queue on one CPU).
That's fine — frame it as a "try before install" preview, not a
replacement.

---

## Suggested prompt for a fresh Claude Code session

> Read `RELEASE_PLAN.md` and `NEXT_SESSION.md` to catch up on context.
> v0.1.0 has shipped. Pick task 1 (icon), task 2 (labyrinth removal), or
> task 3 (Gradio web demo) from `NEXT_SESSION.md` and execute it
> end-to-end, ending with a tagged release if appropriate. Ask which
> task before starting if more than one looks viable.

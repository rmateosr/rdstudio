# Pickups for the next session

Status as of v0.1.1 (released
https://github.com/rmateosr/rdstudio/releases/tag/v0.1.1):

- Mac DMG (arm64) and Windows zip download from GitHub Releases.
- App has a real icon (the cute reaction-diffusion sphere), embedded by
  PyInstaller for the OS-level taskbar/dock and set via Tk `iconphoto`
  for the window chrome.
- `RELEASE_PLAN.md` steps 1–7 done. Steps 8 (Gradio) and 9 (code
  signing) open.

Two remaining pickups, in rough order of payoff. Each is self-contained.

---

## 1. Remove the `labyrinth` preset

**What.** Drop `labyrinth` from the engine entirely. It's already gone
from the README gallery (commit `b999676`).

**How.** `rdstudio/presets.py` defines `PATTERN_PRESETS` as a dict.
Delete the `"labyrinth": (...)` entry. The app derives the dropdown
list from this dict, so the UI updates automatically.

Then grep for any per-color defaults or test fixtures referencing the
name:

```bash
grep -rn "labyrinth" rdstudio/ tests/ scripts/
```

Note: `pattern_thumbnails/labyrinth.png` lives in the user's cache dir
(`platformdirs.user_cache_dir("RDStudio", "RNMateos")/pattern_thumbnails`),
not the repo — leave it; if a user has it cached it's harmless, and
new users won't generate it once the preset is gone.

Bump version to `0.1.2`, tag, push — release pipeline handles the rest.

---

## 2. Gradio "try in your browser" demo (RELEASE_PLAN §7)

**What.** A Hugging Face Space that runs the engine in the browser.
Users who don't want to download anything can try the tool from a URL.
Same engine code, just a different UI layer.

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
runs it. Add a "Try it in your browser" button to the README pointing
at the Space URL.

What won't work as well: long simulations (CPU is slow), large file
uploads (10 MB cap on free Spaces), concurrent users (they queue on
one CPU). That's fine — frame it as a "try before install" preview,
not a replacement.

---

## Suggested prompt for a fresh Claude Code session

> Read `RELEASE_PLAN.md` and `NEXT_SESSION.md` to catch up on context.
> v0.1.1 has shipped. Pick task 1 (labyrinth removal) or task 2
> (Gradio web demo) from `NEXT_SESSION.md` and execute it end-to-end,
> ending with a tagged release if appropriate.

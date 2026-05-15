# Pickups for the next session

## Status

**v0.3.0 — full-feature Pyodide web app shipped.**

`index.html` + `worker.js` at repo root. Enable GitHub Pages (Settings →
Pages → Deploy from branch: main, folder: / (root)) to go live at
`https://rmateosr.github.io/rdstudio/`.

---

## Remaining tasks

- [ ] **Enable GitHub Pages** — repo Settings → Pages → Source: Deploy from branch `main`, folder `/ (root)`. URL will be `https://rmateosr.github.io/rdstudio/`.

- [ ] **Smoke-test in browser** — open via local server (not file://):
  ```bash
  cd /mnt/c/Users/Raul/Documents/rdstudio
  python -m http.server 8080
  # open http://localhost:8080
  ```
  Upload a small image, run Short + Small, verify preview appears and image downloads.

- [ ] **Test video recording** — check "Record timelapse video", run, click "Save video", verify WebM plays.

- [ ] **Test all image sizes** — verify Medium (1024) and Large (1536) complete without error.

- [ ] **Test Bleeding colors mode** — verify it runs and composite looks correct.

- [ ] **Tag v0.3.0** — once smoke tests pass:
  ```bash
  git tag v0.3.0
  git push origin main --tags
  ```

- [ ] **Update HF Spaces** — old Gradio demo can be kept or retired. Low priority.

---

## Architecture notes

- **Hosting:** GitHub Pages (free, static, from repo root on `main` branch)
- **Compute:** Pyodide 0.27.0 — Python runs in the browser (Web Worker)
- **Packages:** numpy, scipy, scikit-learn, pillow — loaded from Pyodide CDN (~50 MB, cached)
- **Engine files:** fetched from same origin at runtime (`rdstudio/*.py`) — no duplication
- **Stop:** `worker.terminate()` + recreate worker; ~3 s reload from browser cache
- **Video:** MediaRecorder captures a hidden `<canvas>` replaying preview frames at 8 fps → WebM
- **Key files:**
  - `index.html` — full UI (HTML/CSS/JS)
  - `worker.js` — Pyodide worker with Python simulation glue
  - `rdstudio/engine_classic.py`, `engine_leaky.py` — unchanged engines, loaded at runtime

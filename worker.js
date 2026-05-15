/* Reaction-Diffusion Studio — Pyodide web worker
 *
 * Runs in a dedicated worker thread. Main thread posts { type: 'run', ... }
 * messages; this worker responds with preview frames, progress updates, and
 * the final composited image.
 *
 * Stop mechanism: call worker.terminate() from the main thread, then create
 * a fresh worker for the next run. Pyodide packages are cached by the browser
 * so re-init is ~3 s on repeat visits.
 */

const PYODIDE_CDN =
  "https://cdn.jsdelivr.net/pyodide/v0.27.0/full/pyodide.js";

importScripts(PYODIDE_CDN);

/* ── Python glue code loaded into Pyodide ─────────────────────────────── */
const PYTHON_SETUP = `
import sys
sys.path.insert(0, '/')

import numpy as np
from rdstudio import engine_classic as rd
from rdstudio import engine_leaky as rdl
from rdstudio.presets import PATTERN_PRESETS, REQUIRES_SCARCE, DU_DEFAULT, DV_DEFAULT
from rdstudio.bg import parse_bg
from js import postMessage, Object
from pyodide.ffi import to_js

DT = 1.0
CONV_TOL = 1e-5

def _post(d):
    # dict_converter=Object.fromEntries produces a plain JS object instead of a Map,
    # so the main thread can access fields with dot notation (data.type, data.w, etc.)
    postMessage(to_js(d, dict_converter=Object.fromEntries))

def run_simulation(config):
    # config arrives as a plain Python dict (JS side used pyodide.toPy)
    import js as _js

    W            = int(config['W'])
    H            = int(config['H'])
    n_colors     = int(config['n_colors'])
    mode         = str(config['mode'])          # 'sharp' | 'bleeding'
    pat_strength = float(config['pattern_strength'])
    sigma        = float(config['smooth_sigma'])
    max_iter     = int(config['max_iter'])
    save_every   = int(config['save_every'])
    bg           = str(config['bg'])
    start_dens   = config['start_density']      # str or list[str]
    enabled      = list(config['enabled'])      # list[bool]
    patterns     = list(config['patterns'])     # list[str]

    # Image arrives as a Uint8Array set on the worker global scope
    img_js = _js.pyodide.globals.get('_img_data')
    arr = np.frombuffer(img_js.to_py(), dtype=np.uint8).reshape(H, W, 4)[:, :, :3].copy()

    rng = np.random.default_rng()

    # ── Quantize ──────────────────────────────────────────────────────────
    _post({'type': 'status', 'msg': 'Quantizing colors...'})
    labels, palette = rd.quantize_colors(arr, n_colors, rng)
    _post({'type': 'palette', 'palette': palette.tolist(), 'n': n_colors})

    # ── Smooth boundaries ─────────────────────────────────────────────────
    _post({'type': 'status', 'msg': 'Smoothing zone boundaries...'})
    labels, soft_masks = rd.smooth_zone_boundaries(labels, n_colors, sigma)

    # ── Resolve "random" pattern choices ──────────────────────────────────
    preset_names = list(PATTERN_PRESETS.keys())
    resolved = [
        preset_names[int(rng.integers(len(preset_names)))] if p == 'random' else p
        for p in patterns
    ]

    # ── Build per-color params ─────────────────────────────────────────────
    params_list = [
        {'f': PATTERN_PRESETS[r][0], 'k': PATTERN_PRESETS[r][1],
         'Du': DU_DEFAULT, 'Dv': DV_DEFAULT}
        for r in resolved
    ]

    # ── Per-color start density (auto-promote for low-feed presets) ────────
    if isinstance(start_dens, list):
        densities = list(start_dens)
    else:
        densities = [str(start_dens)] * n_colors
    for i, r in enumerate(resolved):
        if r in REQUIRES_SCARCE and densities[i] not in ('minimal', 'scarce'):
            densities[i] = 'scarce'

    # ── Callbacks ─────────────────────────────────────────────────────────
    def preview_cb(it, img_arr):
        rgba = np.dstack([img_arr, np.full(img_arr.shape[:2], 255, np.uint8)])
        _post({'type': 'preview', 'iter': it, 'w': W, 'h': H,
               'data': to_js(rgba.ravel())})

    def progress_cb_classic(it, n_active):
        _post({'type': 'progress', 'iter': it, 'maxIter': max_iter,
               'nActive': int(n_active), 'nColors': n_colors})

    def progress_cb_leaky(it, _):
        _post({'type': 'progress', 'iter': it, 'maxIter': max_iter,
               'nActive': -1, 'nColors': n_colors})

    # ── Simulate ──────────────────────────────────────────────────────────
    _post({'type': 'status', 'msg': f'Simulating ({max_iter} iterations)...'})

    if mode == 'bleeding':
        enabled_mask = np.array(enabled, dtype=bool)
        U, V_stack = rdl.initialize_fields_leaky(
            labels, n_colors, rng, enabled_mask=enabled_mask)
        f_arr = np.array([p['f'] for p in params_list])
        k_arr = np.array([p['k'] for p in params_list])
        rdl.simulate_leaky(
            U, V_stack, f_arr, k_arr, DU_DEFAULT, DV_DEFAULT,
            max_iter=max_iter, dt=DT, conv_tol=CONV_TOL,
            check_every=200, save_every=save_every,
            palette=palette, pattern_strength=pat_strength,
            output_base=None, bg=bg,
            preview_callback=preview_cb,
            progress_callback=progress_cb_leaky,
        )
        final = np.array(rdl.composite_image_leaky(
            V_stack, palette, pat_strength, bg=bg))
    else:
        Du_map, Dv_map, f_map, k_map = rd.build_parameter_maps(labels, params_list)
        same_masks, U_pad, V_pad = rd.precompute_neighbor_masks(labels)
        U, V = rd.initialize_fields(labels, n_colors, rng, densities)
        U, V = rd.simulate(
            U, V, Du_map, Dv_map, f_map, k_map,
            same_masks, U_pad, V_pad,
            labels, n_colors, max_iter, DT,
            CONV_TOL, 200, save_every,
            palette, pat_strength, None, bg,
            soft_masks,
            preview_callback=preview_cb,
            progress_callback=progress_cb_classic,
            soft_barrier=True,
        )
        final = rd.composite_image(
            V, labels, n_colors, palette, pat_strength, bg, soft_masks)

    # ── Mask disabled colors ───────────────────────────────────────────────
    bg_rgb = np.array(parse_bg(bg), dtype=np.uint8)
    for i in range(n_colors):
        if not enabled[i]:
            final[labels == i] = bg_rgb

    # ── Send final image ───────────────────────────────────────────────────
    rgba = np.dstack([final, np.full(final.shape[:2], 255, np.uint8)])
    _post({'type': 'done', 'w': W, 'h': H,
           'data': to_js(rgba.ravel()), 'patterns': resolved})
`;

/* ── Worker init ──────────────────────────────────────────────────────── */
async function init() {
  postMessage({ type: "loading", msg: "Loading Python runtime…" });
  const pyodide = await loadPyodide();

  postMessage({ type: "loading", msg: "Loading numpy / scipy…" });
  await pyodide.loadPackage(["numpy", "scipy"]);

  postMessage({ type: "loading", msg: "Loading scikit-learn…" });
  await pyodide.loadPackage(["scikit-learn"]);

  postMessage({ type: "loading", msg: "Loading Pillow…" });
  await pyodide.loadPackage(["Pillow"]);

  postMessage({ type: "loading", msg: "Loading engine modules…" });
  const base = self.location.href.replace(/worker\.js[^/]*$/, "");
  pyodide.FS.mkdir("/rdstudio");
  for (const f of [
    "__init__.py", "presets.py", "bg.py", "engine_classic.py", "engine_leaky.py",
  ]) {
    const resp = await fetch(`${base}rdstudio/${f}`);
    if (!resp.ok) throw new Error(`Cannot load rdstudio/${f}: ${resp.status}`);
    pyodide.FS.writeFile(`/rdstudio/${f}`, await resp.text());
  }

  await pyodide.runPythonAsync(PYTHON_SETUP);
  postMessage({ type: "ready" });
  return pyodide;
}

const pyodidePromise = init().catch((e) =>
  postMessage({ type: "error", msg: String(e) })
);

/* ── Message handler ──────────────────────────────────────────────────── */
self.onmessage = async ({ data }) => {
  if (data.type !== "run") return;

  let pyodide;
  try {
    pyodide = await pyodidePromise;
  } catch (e) {
    postMessage({ type: "error", msg: `Engine not ready: ${e.message}` });
    return;
  }

  try {
    // Transfer image data into Python globals
    pyodide.globals.set("_img_data", new Uint8Array(data.imageBuffer));

    const config = pyodide.toPy({
      W:                data.W,
      H:                data.H,
      n_colors:         data.n_colors,
      mode:             data.mode,
      pattern_strength: data.pattern_strength,
      smooth_sigma:     data.smooth_sigma,
      max_iter:         data.max_iter,
      save_every:       data.save_every,
      bg:               data.bg,
      start_density:    data.start_density,
      enabled:          data.enabled,
      patterns:         data.patterns,
    });

    pyodide.globals.set("_run_config", config);
    await pyodide.runPythonAsync("run_simulation(_run_config)");
  } catch (e) {
    postMessage({ type: "error", msg: e.message || String(e) });
  }
};

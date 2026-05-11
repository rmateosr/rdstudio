#!/usr/bin/env python3
"""Multi-channel Gray-Scott — 'leaky' mode: N V channels, no zone walls.

Each quantized color gets its own V channel V_c with its own (f, k). All
channels share one substrate U pool. Diffusion is unrestricted across the
whole grid (no per-zone masks). Channels are seeded densely in their starting
zone but free to migrate. The shared U makes channels physically compete: a
pixel with high V_a depletes U locally, suppressing other channels there —
emergent separation.

    dU/dt    = Du · Lap(U)  −  U · Σ_c V_c²  +  f̄ · (1 − U)
    dV_c/dt  = Dv · Lap(V_c)  +  U · V_c²    −  (f_c + k_c) · V_c

f̄ tracks the locally-dominant channel:

    f̄ = Σ f_c · V_c² / Σ V_c²

falling back to the mean f where Σ V_c² ≈ 0 (so empty pixels still get
substrate replenished at a sensible rate).

Boundary: Dirichlet=0 padding at the canvas edge — same Lap as the classic
engine outside its zones. Patterns fading at the canvas edge is acceptable.
"""

import os
import time

import numpy as np
from PIL import Image
from scipy.ndimage import label as cc_label

import reaction_diffusion as rd


def _laplacian(field, padded, edge_value=0.0):
    """Standard 4-neighbor Laplacian with Dirichlet BC at canvas edges.

    edge_value sets what the field looks like just outside the canvas:
        0.0 (default): field fades to 0 at the canvas border (used for V —
                       pattern fades at the picture's edge).
        rd.NATURAL_TROUGH_U for U: substrate is held near a natural pattern
                       trough at the canvas border, supplying U inflow.
                       Mimics the classic engine's soft barrier — without
                       it, uniform-high V saturation seeding drains U in
                       the bulk and the reaction collapses.
    """
    padded[:] = edge_value
    padded[1:-1, 1:-1] = field
    return (padded[:-2, 1:-1] + padded[2:, 1:-1]
            + padded[1:-1, :-2] + padded[1:-1, 2:]
            - 4.0 * field)


def initialize_fields_leaky(labels, n_colors, rng, enabled_mask=None,
                            seed_variety="uniform",
                            seed_placement="anywhere",
                            seed_size_range=None):
    """Per-channel seeding: uniform low-V noise across each zone + 3x3 patches.

    For each enabled channel c, V_c is set to:
      - uniform [0, 0.25] across zone c (the same coverage as the classic
        engine's 'medium' density), so the channel's color is faintly
        visible across its zone from frame 1, and
      - one or more 3x3 patches at V in [0.3, 0.6] per connected component
        (one patch per ~2000 px), as established nucleation sites that
        survive even for borderline low-f presets (maze, fingerprints).

    Pure 'saturate' (V=0.4 uniform) was the original spec but isn't viable
    in leaky mode for low-feed presets — the uniform high-V drains shared
    U in the bulk and the reaction collapses inside ~5 iterations. A peaked
    seeding with low-V valleys preserves enough U for patterns to develop.

    U = 1.0 everywhere. Patches deliberately do NOT depress U the way the
    classic engine does: with C channels each placing patches, the total
    U depression compounds and re-creates the same starvation problem the
    saturate seeding caused.

    enabled_mask: optional sequence of length n_colors. Falsy entries leave
    V_c = 0 everywhere — that channel never enters the simulation.
    """
    H, W = labels.shape
    V_stack = np.zeros((n_colors, H, W), dtype=np.float64)

    if enabled_mask is None:
        enabled_mask = [True] * n_colors

    for c in range(n_colors):
        if not enabled_mask[c]:
            continue
        zone = labels == c
        if not zone.any():
            continue

        # The noise floor is suppressed for "edges" placement so the
        # interior really starts empty.
        if seed_placement != "edges":
            V_stack[c] = np.where(zone, rng.uniform(0.0, 0.25, (H, W)), 0.0)

        comp_labels, n_comps = cc_label(zone)
        Vc = V_stack[c]
        for comp_id in range(1, n_comps + 1):
            comp_mask = comp_labels == comp_id
            comp_pixels = int(comp_mask.sum())
            if comp_pixels == 0:
                continue
            if seed_placement == "edges":
                edge_mask = rd._component_boundary(comp_mask)
                coords = np.argwhere(edge_mask)
                if len(coords) == 0:
                    continue
                n_seeds = max(1, len(coords) // 8)
            else:
                coords = np.argwhere(comp_mask)
                n_seeds = min(len(coords), max(1, comp_pixels // 2000))
            pick = rng.choice(len(coords), size=n_seeds, replace=False)
            for idx in pick:
                y, x = coords[idx]
                amp = rd._seed_amplitude(rng, seed_variety)
                rd._stamp_seed(Vc, comp_mask, y, x, amp, H, W,
                               rng, seed_variety, U=None,
                               size_range=(seed_size_range
                                           or rd.DEFAULT_SEED_SIZE_RANGE))

    U = np.ones((H, W), dtype=np.float64)
    return U, V_stack


def simulate_leaky(U, V_stack, f_arr, k_arr, Du, Dv,
                   max_iter, dt, conv_tol, check_every, save_every,
                   palette, pattern_strength, output_base, bg,
                   preview_callback=None, progress_callback=None,
                   stop_flag=None):
    """Multi-channel Gray-Scott loop. See module docstring for the equations.

    V_stack: (C, H, W) float — one V channel per color.
    f_arr, k_arr: (C,) per-channel feed/kill rates (channels carry their preset).
    Du, Dv: scalars shared across all channels and U.

    Convergence: stop when max over all c and pixels of |V_c − V_c_prev|
    falls below conv_tol (checked every check_every steps).
    """
    C, H, W = V_stack.shape
    U_pad = np.zeros((H + 2, W + 2), dtype=np.float64)
    V_pad = np.zeros((H + 2, W + 2), dtype=np.float64)

    # Mean f used as fallback where Σ V_c² ≈ 0 (no V present locally).
    f_arr = np.asarray(f_arr, dtype=np.float64)
    k_arr = np.asarray(k_arr, dtype=np.float64)
    mean_f = float(np.mean(f_arr)) if C > 0 else 0.0
    EPS = 1e-12

    V_prev = V_stack.copy()
    t0 = time.time()
    it = 0
    for it in range(1, max_iter + 1):
        if stop_flag is not None and stop_flag():
            print(f"  Stop requested at iteration {it}.")
            break

        lap_U = _laplacian(U, U_pad, edge_value=rd.NATURAL_TROUGH_U)

        v2 = V_stack * V_stack                                  # (C, H, W)
        sum_v2 = v2.sum(axis=0)                                 # (H, W)
        weighted_f = (f_arr[:, None, None] * v2).sum(axis=0)    # (H, W)
        f_bar = np.where(sum_v2 > EPS,
                         weighted_f / np.maximum(sum_v2, EPS),
                         mean_f)

        uvv_total = U * sum_v2
        dU = Du * lap_U - uvv_total + f_bar * (1.0 - U)
        U = np.clip(U + dt * dU, 0.0, 1.0)

        for c in range(C):
            lap_Vc = _laplacian(V_stack[c], V_pad)
            uvv_c = U * v2[c]
            dVc = Dv * lap_Vc + uvv_c - (f_arr[c] + k_arr[c]) * V_stack[c]
            V_stack[c] = np.clip(V_stack[c] + dt * dVc, 0.0, 1.0)

        if it % check_every == 0:
            elapsed = time.time() - t0
            max_delta = float(np.max(np.abs(V_stack - V_prev))) if C > 0 else 0.0
            print(f"  Iteration {it}/{max_iter} ({elapsed:.1f}s) | "
                  f"max |dV|={max_delta:.2e}")
            V_prev = V_stack.copy()
            if progress_callback is not None:
                progress_callback(it, C)
            if max_delta < conv_tol:
                print(f"  Converged at iteration {it}.")
                break

        if save_every > 0 and it % save_every == 0:
            img = composite_image_leaky(V_stack, palette,
                                        pattern_strength, bg)
            if preview_callback is not None:
                preview_callback(it, img)
            if output_base is not None:
                base, ext = os.path.splitext(output_base)
                Image.fromarray(img).save(f"{base}_iter{it:06d}{ext}")

    elapsed = time.time() - t0
    print(f"  Simulation finished: {it} iterations in {elapsed:.1f}s")
    return U, V_stack


def composite_image_leaky(V_stack, palette, pattern_strength, bg="white"):
    """Weighted-sum composite over background.

    For each pixel: w_c = V_c · strength (after a global rescale so the
    strongest V across all channels reaches `strength`); RGB = Σ w_c·color_c
    + (1 − min(Σ w_c, 1))·bg. Behaves like alpha compositing — a channel
    alone shows its color, two overlapping channels blend additively up to
    opacity 1, and where no channel has any V the background shows through.

    Global (not per-channel) normalization preserves relative brightness:
    a faded or extinct channel renders dim, not artificially boosted.
    """
    C, H, W = V_stack.shape
    bg_rgb = rd.parse_bg(bg)

    global_max = float(V_stack.max()) if C > 0 else 0.0
    if global_max < 1e-10:
        out = np.empty((H, W, 3), dtype=np.uint8)
        out[:] = bg_rgb.astype(np.uint8)
        return out

    scale = pattern_strength / global_max
    weights = V_stack * scale                       # (C, H, W)
    total = weights.sum(axis=0)                     # (H, W)
    bg_alpha = np.clip(1.0 - total, 0.0, 1.0)       # (H, W)

    # Σ w_c · color_c
    rgb = np.tensordot(weights, palette, axes=(0, 0))   # (H, W, 3)
    rgb += bg_alpha[:, :, None] * bg_rgb[None, None, :]

    return np.clip(rgb, 0, 255).astype(np.uint8)


if __name__ == "__main__":
    # Smoke test: 2-color synthetic image, 1000 iterations.
    # Verifies the engine runs end-to-end and produces non-trivial V fields.
    H = W = 128
    labels = np.zeros((H, W), dtype=np.int64)
    labels[:, W // 2:] = 1
    palette = np.array([[200.0,  50.0,  50.0],   # red zone
                        [ 50.0, 100.0, 220.0]],  # blue zone
                       dtype=np.float64)
    f_arr = np.array([0.055, 0.062])              # coral, labyrinth
    k_arr = np.array([0.062, 0.061])
    rng = np.random.default_rng(0)

    U, V_stack = initialize_fields_leaky(labels, 2, rng)
    print(f"Init: U mean={U.mean():.3f}, "
          f"V_0 max={V_stack[0].max():.3f}, V_1 max={V_stack[1].max():.3f}")

    U, V_stack = simulate_leaky(
        U, V_stack, f_arr, k_arr, 0.16, 0.08,
        max_iter=1000, dt=1.0, conv_tol=1e-9, check_every=200,
        save_every=0, palette=palette, pattern_strength=0.7,
        output_base=None, bg="white",
    )

    for c in range(2):
        print(f"Channel {c}: V mean={V_stack[c].mean():.4f}, "
              f"max={V_stack[c].max():.4f}")

    img = composite_image_leaky(V_stack, palette, 0.7, "white")
    out_path = "leaky_smoke_test.png"
    Image.fromarray(img).save(out_path)
    print(f"Saved smoke test: {out_path}")

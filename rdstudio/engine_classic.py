#!/usr/bin/env python3
"""Image-based Gray-Scott reaction-diffusion with per-color regions.

Each pixel uses its zone's (f, k, Du, Dv) parameters; the equations are standard
Gray-Scott:

    dU/dt = Du * Lap(U) - U*V^2 + f*(1 - U)
    dV/dt = Dv * Lap(V) + U*V^2 - (f + k)*V

Zones are kept chemically isolated — neighbors across a color boundary are
substituted with a Dirichlet "outside" value rather than the actual neighbor.
The OUTSIDE values control whether the wall feels like a natural pattern
trough or a depleted dead zone (selected by ``soft_barrier``):

- ``soft_barrier=True`` (default): outside U≈0.85, outside V=0. These are the
  empirical (U, V) values at natural pattern troughs (the gaps between spots
  or stripes inside a single zone), measured by running each preset on a torus
  and reading off the bottom-decile V pixels — see ``probe_trough_values.py``.
  The wall therefore behaves like one more trough between features: V drops to
  0 at the wall but U is held near its trough state, so substrate is not drained.
  Patterns grow up to the wall at the natural Turing spacing — no fade gap.

  (The theoretical asymptote V=0 forces U=1, but real troughs sit at U≈0.83
  because diffusion drags consumed U from neighboring peaks into the trough.
  Using U=1 over-energizes the wall — peaks land at the boundary instead of
  half a wavelength inside.)

- ``soft_barrier=False``: outside U=0, outside V=0. Both substrate and
  activator are drained into the wall (legacy Dirichlet behavior). The
  reaction starves over a half-wavelength fetch on each side, leaving a
  visible no-pattern gap between adjacent colors.

Zone boundaries are smoothed before simulation: each color mask is Gaussian-blurred
and labels are reassigned by dominant vote. This turns pixel-grid staircases into
smooth organic curves. The output is composited using soft mask weights for
anti-aliased zone edges.
"""

import argparse
import os
import time

import numpy as np
from PIL import Image
from scipy.ndimage import gaussian_filter, label as cc_label
from sklearn.cluster import KMeans, MiniBatchKMeans

# 4 cardinal directions for Laplacian: (dy, dx)
SHIFTS = [(-1, 0), (1, 0), (0, -1), (0, 1)]

NAMED_BG_COLORS = {
    "white": (255.0, 255.0, 255.0),
    "gray":  (128.0, 128.0, 128.0),
    "black": (0.0, 0.0, 0.0),
}


def parse_bg(bg):
    """Return an RGB (3,) float array for a background specifier.

    Accepts a named color ("white"/"gray"/"black"), an "#RRGGBB" hex string,
    or an already-resolved (r, g, b) tuple/array. Values are in [0, 255].
    """
    if isinstance(bg, (tuple, list, np.ndarray)):
        arr = np.asarray(bg, dtype=np.float64)
        if arr.shape != (3,):
            raise ValueError(f"bg tuple must have 3 components, got {arr.shape}")
        return arr
    if not isinstance(bg, str):
        raise TypeError(f"bg must be a str or 3-tuple, got {type(bg).__name__}")
    s = bg.strip()
    if s in NAMED_BG_COLORS:
        return np.asarray(NAMED_BG_COLORS[s], dtype=np.float64)
    if s.startswith("#") and len(s) == 7:
        try:
            r = int(s[1:3], 16)
            g = int(s[3:5], 16)
            b = int(s[5:7], 16)
            return np.asarray((r, g, b), dtype=np.float64)
        except ValueError:
            pass
    raise ValueError(f"Invalid bg: {bg!r} (use white/gray/black or #RRGGBB)")

# Curated (f, k) pairs — all with high enough feed rate to sustain patterns
# under Dirichlet BC (where substrate drains at zone boundaries).
DEFAULT_PARAM_SETS = [
    (0.055, 0.062),  # coral                (Karl Sims / Munafo θ-κ border)
    (0.062, 0.061),  # labyrinth            (Munafo π; same regime as fingerprints)
    (0.078, 0.061),  # stripe fragments     (off-catalog high-f; was mislabeled "worms")
    (0.058, 0.065),  # short worms          (Munafo μ; was mislabeled "moving spots")
    (0.054, 0.063),  # maze                 (Munafo κ — dense coral/labyrinth hybrid)
    (0.067, 0.063),  # sparse worms         (informal; no canonical reference)
    (0.064, 0.065),  # sparse stripes       (π/μ border; was mislabeled "spots")
    (0.060, 0.063),  # fingerprints         (Munafo π; same regime as labyrinth)
    (0.055, 0.065),  # flakes               (informal; no canonical reference)
    (0.028, 0.062),  # mitosis              (Pearson λ — needs start_density=scarce)
    (0.035, 0.060),  # zebrafish            (Rougier)
    (0.024, 0.060),  # solitons             (Pearson ζ — needs start_density=scarce)
    (0.018, 0.050),  # wavelets             (Pearson α/β — needs start_density=scarce)
]

MIN_SIM_SIDE = 1024  # minimum dimension for patterns to develop

# Empirical U at pattern troughs, averaged across coral/labyrinth/maze/zebrafish/
# mitosis presets (range 0.78–0.90, mean ≈ 0.83). The theoretical fixed point
# is U=1, V=0, but real troughs sit slightly below 1 because diffusion drags
# consumed U into them. Using U=1 over-energizes the wall (makes it a substrate
# fountain — peaks land AT the wall instead of half-a-wavelength in). 0.85 puts
# the wall in equilibrium with a natural trough: V≈0 at the wall, peaks form
# at the natural spacing inside the zone. See probe_trough_values.py.
NATURAL_TROUGH_U = 0.85
NATURAL_TROUGH_V = 0.0


def parse_args():
    p = argparse.ArgumentParser(
        description="Run Gray-Scott reaction-diffusion on quantized color regions of an image."
    )
    p.add_argument("input", help="Input image path (PNG or JPEG)")
    p.add_argument("-o", "--output", default="output.png", help="Output image path (default: output.png)")
    p.add_argument("-n", "--n-colors", type=int, default=5, help="Number of quantized colors (default: 5)")
    p.add_argument("-p", "--params", default=None, help="TSV file with per-color Gray-Scott parameters")
    p.add_argument("--max-iter", type=int, default=10000, help="Maximum simulation iterations (default: 10000)")
    p.add_argument("--dt", type=float, default=1.0, help="Time step (default: 1.0)")
    p.add_argument("--scale", type=float, default=1.0, help="Downscale factor, e.g. 0.5 for half size (default: 1.0)")
    p.add_argument("--pattern-strength", type=float, default=0.7,
                   help="How strongly the pattern modulates color, 0-1 (default: 0.7)")
    def bg_arg(s):
        try:
            parse_bg(s)
        except (ValueError, TypeError) as e:
            raise argparse.ArgumentTypeError(str(e))
        return s
    p.add_argument("--background", type=bg_arg, default="white",
                   help="Background color: white/gray/black or #RRGGBB (default: white)")
    p.add_argument("--seed", type=int, default=None, help="Random seed for reproducibility")
    p.add_argument("--quantized-only", action="store_true",
                   help="Save quantized image and params template, skip simulation")
    p.add_argument("--save-every", type=int, default=0,
                   help="Save intermediate frames every N iterations (0 = disabled)")
    p.add_argument("--check-every", type=int, default=200,
                   help="Check convergence every N iterations (default: 200)")
    p.add_argument("--conv-tol", type=float, default=1e-5,
                   help="Convergence tolerance for max |delta V| (default: 1e-5)")
    p.add_argument("--smooth-sigma", type=float, default=3.0,
                   help="Gaussian sigma for smoothing zone boundaries. "
                        "Higher = smoother, more organic edges. 0 = no smoothing. (default: 3.0)")
    p.add_argument("--start-density", choices=["scarce", "medium", "high"],
                   default="medium",
                   help="Initial activator (V) density. scarce=sparse seeds "
                        "(classic Gray-Scott, very small zones may fade blank); "
                        "medium=uniform noise [0,0.25] (default); "
                        "high=uniform noise [0,0.5] (dense, converges faster).")
    p.add_argument("--hard-barrier", action="store_true",
                   help="Use hard Dirichlet walls between color zones (chemicals "
                        "drain to 0 at zone edges, leaving a fade gap between "
                        "colors). Default is a soft barrier where U/V diffuse "
                        "freely across zones — only (f,k) changes per zone.")
    return p.parse_args()


def load_image(path, scale):
    """Load image as RGB uint8 array, upscale small images, optionally rescale."""
    img = Image.open(path).convert("RGB")

    min_side = min(img.width, img.height)
    if min_side < MIN_SIM_SIDE:
        upscale = MIN_SIM_SIDE / min_side
        new_w = int(img.width * upscale)
        new_h = int(img.height * upscale)
        print(f"  Upscaling {img.width}x{img.height} -> {new_w}x{new_h} "
              f"(min side {min_side} < {MIN_SIM_SIDE})")
        img = img.resize((new_w, new_h), Image.LANCZOS)

    if scale != 1.0:
        new_w = max(1, int(img.width * scale))
        new_h = max(1, int(img.height * scale))
        img = img.resize((new_w, new_h), Image.LANCZOS)

    return np.array(img)


def quantize_colors(image, n_colors, rng):
    """K-means color quantization. Returns (labels, palette)."""
    H, W, _ = image.shape
    pixels = image.reshape(-1, 3).astype(np.float64)

    n_pixels = H * W
    if n_pixels > 500_000:
        km = MiniBatchKMeans(n_clusters=n_colors, random_state=rng.integers(2**31),
                             batch_size=min(10000, n_pixels), max_iter=100, n_init=3)
    else:
        km = KMeans(n_clusters=n_colors, random_state=rng.integers(2**31),
                    max_iter=50, n_init=10)

    km.fit(pixels)
    labels = km.labels_.reshape(H, W)
    palette = km.cluster_centers_
    return labels, palette


def smooth_zone_boundaries(labels, n_colors, sigma):
    """Smooth zone boundaries to create organic edges instead of pixel staircases.

    For each color, the binary mask is Gaussian-blurred to create a soft membership
    field. Labels are then reassigned by which color has the highest soft weight at
    each pixel. This rounds off jagged corners and stairstep edges into smooth curves.

    The soft masks are also returned for use in compositing (anti-aliased output).

    Returns: (smoothed_labels, soft_masks)
        smoothed_labels: (H, W) int array — hard labels following smooth boundaries
        soft_masks: (n_colors, H, W) float array — blurred membership weights per color
    """
    H, W = labels.shape
    soft_masks = np.zeros((n_colors, H, W), dtype=np.float64)

    for c in range(n_colors):
        mask = (labels == c).astype(np.float64)
        soft_masks[c] = gaussian_filter(mask, sigma=sigma)

    # Reassign labels by dominant soft weight
    smoothed_labels = np.argmax(soft_masks, axis=0).astype(labels.dtype)

    # Re-normalize soft masks so they sum to 1 at each pixel
    mask_sum = soft_masks.sum(axis=0, keepdims=True)
    mask_sum = np.where(mask_sum > 0, mask_sum, 1.0)
    soft_masks /= mask_sum

    return smoothed_labels, soft_masks


def load_params(path, n_colors, palette):
    """Load per-color parameters from TSV, or generate defaults."""
    params = []
    for i in range(n_colors):
        f, k = DEFAULT_PARAM_SETS[i % len(DEFAULT_PARAM_SETS)]
        params.append({"f": f, "k": k, "Du": 0.16, "Dv": 0.08})

    if path is not None:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 6:
                    continue
                idx = int(parts[0])
                if 0 <= idx < n_colors:
                    params[idx] = {
                        "f": float(parts[2]),
                        "k": float(parts[3]),
                        "Du": float(parts[4]),
                        "Dv": float(parts[5]),
                    }
    return params


def export_params_template(palette, params, path):
    """Write a TSV documenting colors found and parameters used."""
    with open(path, "w") as fh:
        fh.write("# Gray-Scott reaction-diffusion parameters per quantized color\n")
        fh.write("# Columns: color_index\thex_color\tf\tk\tDu\tDv\n")
        for i, (color, p) in enumerate(zip(palette, params)):
            r, g, b = int(round(color[0])), int(round(color[1])), int(round(color[2]))
            hex_color = f"#{r:02X}{g:02X}{b:02X}"
            fh.write(f"{i}\t{hex_color}\t{p['f']}\t{p['k']}\t{p['Du']}\t{p['Dv']}\n")
    print(f"  Parameters template saved to: {path}")


def build_parameter_maps(labels, params):
    """Convert per-color params to uniform per-pixel arrays via label indexing."""
    n_colors = len(params)
    Du_arr = np.array([params[i]["Du"] for i in range(n_colors)])
    Dv_arr = np.array([params[i]["Dv"] for i in range(n_colors)])
    f_arr = np.array([params[i]["f"] for i in range(n_colors)])
    k_arr = np.array([params[i]["k"] for i in range(n_colors)])
    return Du_arr[labels], Dv_arr[labels], f_arr[labels], k_arr[labels]


def precompute_neighbor_masks(labels):
    """Precompute same-color masks for the 4 cardinal directions.

    For each direction, mask[i] is True where the neighbor shares the same
    color label as the center. Used by the Laplacian to enforce Dirichlet BC:
    neighbors across a zone boundary are treated as 0 (occupied space).
    """
    H, W = labels.shape
    padded_labels = np.full((H + 2, W + 2), -1, dtype=labels.dtype)
    padded_labels[1:-1, 1:-1] = labels

    center = padded_labels[1:-1, 1:-1]
    same_color_masks = []
    for dy, dx in SHIFTS:
        neighbor = padded_labels[1 + dy: H + 1 + dy, 1 + dx: W + 1 + dx]
        same_color_masks.append(neighbor == center)

    U_pad = np.zeros((H + 2, W + 2), dtype=np.float64)
    V_pad = np.zeros((H + 2, W + 2), dtype=np.float64)
    return same_color_masks, U_pad, V_pad


def fill_padded(padded, field):
    """Fill padded array. Border = 0 (image edge is also occupied/depleted)."""
    padded[:] = 0.0
    padded[1:-1, 1:-1] = field


def masked_laplacian(field, padded, same_color_masks, outside_value=0.0):
    """Discrete Laplacian. Cross-zone neighbors are substituted with ``outside_value``.

    For each of the 4 cardinal neighbors:
        if same zone:  use the actual neighbor value (normal diffusion)
        if diff zone:  use ``outside_value``  (Dirichlet BC at the wall)

    Image-edge neighbors (those in the padding) also use ``outside_value`` —
    same_color_masks is False for them by construction (the padding is labeled
    -1, which never matches any zone).

    Choice of outside_value controls how the wall feels to the reaction:

    - 1.0 for U, 0.0 for V: matches the natural trivial fixed point (V=0, U=1).
      Substrate is replenished at the wall; activator is at its trough value.
      The wall behaves like a permanent pattern trough — no fade gap.

    - 0.0 for both U and V: legacy hard barrier. Substrate is drained at the
      wall, the reaction starves nearby, the pattern fades back over a
      half-wavelength on each side, leaving a visible gap.
    """
    H, W = field.shape
    fill_padded(padded, field)
    lap = np.zeros_like(field)
    for i, (dy, dx) in enumerate(SHIFTS):
        nb = padded[1 + dy: H + 1 + dy, 1 + dx: W + 1 + dx]
        effective_nb = np.where(same_color_masks[i], nb, outside_value)
        lap += effective_nb - field
    return lap


SMALL_COMPONENT_PIXELS = 100  # below this, nucleation is fragile under Dirichlet BC


SEED_VARIETY_NAMES = ("uniform", "mixed")
SEED_PLACEMENT_NAMES = ("anywhere", "edges")


def _component_boundary(comp_mask):
    """Bool mask of pixels in `comp_mask` that touch the component's edge.

    A pixel qualifies if it's in the component and at least one of its
    4-neighbors is outside (including outside the image). Computed via
    binary_erosion with border_value=0, so image-edge pixels of a
    component also count as boundary.
    """
    from scipy.ndimage import binary_erosion
    eroded = binary_erosion(comp_mask, border_value=0)
    return comp_mask & ~eroded


DEFAULT_SEED_SIZE_RANGE = (1, 9)


def _make_seed_mask(rng, variety, size_range=DEFAULT_SEED_SIZE_RANGE):
    """Return (mask, half_y, half_x) describing one seed's shape.

    `mask` is a small 2D bool array; the seed is drawn at all True cells.
    (half_y, half_x) is the offset from the top-left of `mask` to its anchor
    point — the (y, x) passed to _stamp_seed maps onto that anchor so the
    seed stays roughly centered on the chosen pixel.

    "uniform" → always the legacy 3×3 square (size_range ignored).
    "mixed"   → organic-blob sampler. For each seed:
                  • a bounding-box side is drawn uniformly from `size_range`
                    (inclusive on both ends),
                  • the active region is a randomly-squashed ellipse centered
                    in that box (random aspect ratio per seed),
                  • Bernoulli probability inside the ellipse decays smoothly
                    from a peak at the center to zero at the rim, so most
                    interior pixels are lit but some are randomly missing —
                    irregular, non-square outlines.
                Pixels outside the ellipse are always off, so seeds look
                like rough blobs rather than speckled squares.
                For side ≤ 2 we skip the ellipse and just Bernoulli-fill,
                since there's no room for shape.
    """
    if variety == "uniform":
        return np.ones((3, 3), dtype=bool), 1, 1

    lo, hi = size_range
    lo = max(1, int(lo))
    hi = max(lo, int(hi))
    side = int(rng.integers(lo, hi + 1))
    half = side // 2

    if side <= 2:
        p = float(rng.uniform(0.4, 1.0))
        mask = rng.random((side, side)) < p
        if not mask.any():
            mask[half, half] = True
        return mask, half, half

    yy, xx = np.mgrid[:side, :side].astype(np.float64)
    cy = cx = (side - 1) / 2.0
    r_max = side / 2.0

    # Random ellipse aspect — independent x/y semi-axes in [0.55, 1.0]·r_max,
    # so seeds range from near-circular to noticeably elongated.
    ay = float(rng.uniform(0.55, 1.0)) * r_max
    ax = float(rng.uniform(0.55, 1.0)) * r_max
    d_norm = np.sqrt(((yy - cy) / ay) ** 2 + ((xx - cx) / ax) ** 2)

    # Center-weighted probability: p_max at the center, 0 at the rim
    # (d_norm = 1), 0 outside. The (1 - d²) shape gives a soft, organic
    # falloff with naturally rough edges from the Bernoulli draw.
    p_max = float(rng.uniform(0.6, 1.0))
    p_field = np.where(d_norm <= 1.0, p_max * (1.0 - d_norm ** 2), 0.0)
    mask = rng.random((side, side)) < p_field
    if not mask.any():
        mask[half, half] = True
    return mask, half, half


# Amplitude range used when variety="mixed" — wider than the legacy [0.3, 0.6]
# so individual seeds also differ in initial V strength.
MIXED_AMP_RANGE = (0.2, 0.9)
UNIFORM_AMP_RANGE = (0.3, 0.6)


def _seed_amplitude(rng, variety):
    lo, hi = MIXED_AMP_RANGE if variety == "mixed" else UNIFORM_AMP_RANGE
    return float(rng.uniform(lo, hi))


def _stamp_seed(V, comp_mask, y, x, amp, H, W, rng, variety, U=None,
                size_range=DEFAULT_SEED_SIZE_RANGE):
    """Stamp one seed centered on (y, x), clipped to image and component.

    If U is provided (classic engine), U is set to 0.5 at the same cells.
    The leaky engine doesn't depress U — it passes U=None.
    """
    seed_mask, hy, hx = _make_seed_mask(rng, variety, size_range)
    sh, sw = seed_mask.shape
    y_top = y - hy
    x_left = x - hx
    y0 = max(0, y_top)
    x0 = max(0, x_left)
    y1 = min(H, y_top + sh)
    x1 = min(W, x_left + sw)
    if y1 <= y0 or x1 <= x0:
        return
    sy0, sx0 = y0 - y_top, x0 - x_left
    sub = (seed_mask[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
           & comp_mask[y0:y1, x0:x1])
    V[y0:y1, x0:x1][sub] = amp
    if U is not None:
        U[y0:y1, x0:x1][sub] = 0.5


def _stamp_patch(V, U, comp_mask, y, x, amp, H, W):
    """Legacy 3×3 stamp — kept for backward compatibility."""
    _stamp_seed(V, comp_mask, y, x, amp, H, W,
                np.random.default_rng(0), "uniform", U=U)


def initialize_fields(labels, n_colors, rng, start_density="medium",
                      seed_variety="uniform", seed_placement="anywhere",
                      seed_size_range=DEFAULT_SEED_SIZE_RANGE):
    """Initialize U and V fields per connected component.

    Seeding is performed per connected component (4-connected) within each
    color zone, so every disconnected region is guaranteed at least one
    nucleation site. A purely random draw pooled across all pixels of a
    color could leave some components unseeded — in scarce mode those
    components never nucleate and stay blank.

    start_density: a single string applied to all zones, or a list of
    n_colors strings for per-zone control. Each entry must be one of
    "minimal", "scarce", "medium", or "high":

      - "minimal": V=0 except for a single seed per connected component,
                   no matter how large the component is. Sparser than
                   "scarce" — useful for one-source-per-zone studies and
                   for low-feed presets where any extra noise drowns the
                   pattern. Also satisfies the REQUIRES_SCARCE patterns.
      - "scarce":  V=0 except at 3x3 patch seeds (V in [0.3, 0.6], U=0.5
                   on the patch). At least one patch per component, scaling
                   as comp_pixels // 2000. Patches rather than single pixels
                   so stripe-regime presets (zebrafish, etc.) can also
                   nucleate — a single pixel decays via diffusion before a
                   stripe can grow. Required for low-feed presets (mitosis,
                   solitons, wavelets).
      - "medium":  V drawn from uniform [0, 0.25] within the zone, plus
                   at least one strong 3x3 patch per connected component.
      - "high":    V drawn from uniform [0, 0.5] within the zone, plus
                   at least one strong 3x3 patch per connected component.

    Every connected component in every mode receives at least one strong
    patch seed (V in [0.3, 0.6], U=0.5), so no isolated zone is left to
    rely on noise alone — important when components are narrow or the
    regime cannot nucleate from low-amplitude noise.
    """
    H, W = labels.shape
    U = np.ones((H, W), dtype=np.float64)
    V = np.zeros((H, W), dtype=np.float64)

    if isinstance(start_density, str):
        densities = [start_density] * n_colors
    else:
        densities = list(start_density)
    if len(densities) != n_colors:
        raise ValueError(
            f"start_density list length {len(densities)} != n_colors {n_colors}")

    small_components = 0
    total_components = 0

    for c in range(n_colors):
        zone_mask = labels == c
        if not zone_mask.any():
            continue
        d = densities[c]
        comp_labels, n_comps = cc_label(zone_mask)
        total_components += n_comps

        for comp_id in range(1, n_comps + 1):
            comp_mask = comp_labels == comp_id
            comp_pixels = int(comp_mask.sum())
            if comp_pixels == 0:
                continue
            is_small = comp_pixels < SMALL_COMPONENT_PIXELS
            if is_small:
                small_components += 1

            if seed_placement == "edges":
                # Anchor seeds only on the component's outline; leave the
                # interior at V=0 (no noise floor) so patterns must grow
                # inward from the edge. The shape stamped at each anchor
                # may protrude one or two pixels into the interior, which
                # is fine — it's still the "edge layer".
                edge_mask = _component_boundary(comp_mask)
                coords = np.argwhere(edge_mask)
                if len(coords) == 0:
                    continue
                # Dense ring: roughly one anchor every ~8 boundary pixels.
                n_seeds = max(1, len(coords) // 8)
            else:
                coords = np.argwhere(comp_mask)
                if d == "minimal":
                    # Single anchor per component — sparser than scarce.
                    n_seeds = 1
                else:
                    if d == "high":
                        V[comp_mask] = rng.uniform(0.0, 0.5, size=comp_pixels)
                    elif d == "medium":
                        V[comp_mask] = rng.uniform(0.0, 0.25, size=comp_pixels)
                    # "scarce" leaves V=0 in the bulk and only stamps patches.
                    # Every component gets at least one strong patch (more for
                    # larger zones) so noise/scarce both have nucleation sites.
                    n_seeds = min(len(coords), max(1, comp_pixels // 2000))

            pick = rng.choice(len(coords), size=n_seeds, replace=False)
            for idx in pick:
                y, x = coords[idx]
                amp = _seed_amplitude(rng, seed_variety)
                _stamp_seed(V, comp_mask, y, x, amp, H, W,
                            rng, seed_variety, U=U,
                            size_range=seed_size_range)

    if small_components:
        print(f"  Seeded {total_components} components "
              f"({small_components} < {SMALL_COMPONENT_PIXELS} px may "
              f"still fade under Dirichlet BC).")
    else:
        print(f"  Seeded {total_components} components.")

    return U, V


def simulate(U, V, Du_map, Dv_map, f_map, k_map,
             same_color_masks, U_pad, V_pad,
             labels, n_colors, max_iter, dt,
             conv_tol, check_every, save_every,
             palette, pattern_strength, output_base, bg, soft_masks,
             preview_callback=None, progress_callback=None, stop_flag=None,
             soft_barrier=True):
    """Main simulation loop.

    Standard Gray-Scott with occupied space outside each zone (Dirichlet BC):

        lap_U = masked_laplacian(U)     ← outside zone treated as U=0
        lap_V = masked_laplacian(V)     ← outside zone treated as V=0

        U += dt * ( Du * lap_U  -  U*V^2  +  f * (1-U) )
        V += dt * ( Dv * lap_V  +  U*V^2  -  (f+k) * V )

    U drains toward zone edges → reaction starves → pattern fades organically.

    Optional hooks (default None = CLI behavior unchanged):
        preview_callback(iteration, img_uint8_HxWx3): called each `save_every` step
            with the composited preview (in addition to any disk save).
        progress_callback(iteration, n_active_colors): called each `check_every` step.
        stop_flag(): if callable and returns True, simulation breaks early.
        output_base: if None, periodic composites are not written to disk.
    """
    H, W = U.shape
    active_mask = np.ones((H, W), dtype=bool)
    converged = [False] * n_colors
    V_checkpoint = V.copy()
    # soft barrier: wall mimics natural trough (empirically U≈0.85, V≈0).
    # hard barrier: wall is depleted (U=0, V=0) — drains substrate, fade gap.
    U_outside = NATURAL_TROUGH_U if soft_barrier else 0.0
    V_outside = NATURAL_TROUGH_V if soft_barrier else 0.0

    t0 = time.time()
    it = 0
    for it in range(1, max_iter + 1):
        if stop_flag is not None and stop_flag():
            print(f"  Stop requested at iteration {it}.")
            break

        lap_U = masked_laplacian(U, U_pad, same_color_masks, U_outside)
        lap_V = masked_laplacian(V, V_pad, same_color_masks, V_outside)

        uvv = U * V * V
        dU = Du_map * lap_U - uvv + f_map * (1.0 - U)
        dV = Dv_map * lap_V + uvv - (f_map + k_map) * V

        U = np.where(active_mask, np.clip(U + dt * dU, 0.0, 1.0), U)
        V = np.where(active_mask, np.clip(V + dt * dV, 0.0, 1.0), V)

        if it % check_every == 0:
            elapsed = time.time() - t0
            print(f"  Iteration {it}/{max_iter} ({elapsed:.1f}s)", end="")
            for c in range(n_colors):
                if converged[c]:
                    continue
                region = labels == c
                if not region.any():
                    converged[c] = True
                    continue
                max_delta = np.abs(V[region] - V_checkpoint[region]).max()
                if max_delta < conv_tol:
                    converged[c] = True
                    active_mask[region] = False
                    print(f" | color {c} converged", end="")
            n_active = sum(1 for c in converged if not c)
            print(f" | {n_active}/{n_colors} active")
            V_checkpoint = V.copy()

            if progress_callback is not None:
                progress_callback(it, n_active)

            if all(converged):
                print(f"  All colors converged at iteration {it}.")
                break

        if save_every > 0 and it % save_every == 0:
            img = composite_image(V, labels, n_colors, palette,
                                  pattern_strength, bg, soft_masks)
            if preview_callback is not None:
                preview_callback(it, img)
            if output_base is not None:
                base, ext = os.path.splitext(output_base)
                frame_path = f"{base}_iter{it:06d}{ext}"
                Image.fromarray(img).save(frame_path)
                print(f"  Saved intermediate: {frame_path}")

    elapsed = time.time() - t0
    print(f"  Simulation finished: {it} iterations in {elapsed:.1f}s")
    return U, V


def composite_image(V, labels, n_colors, palette, pattern_strength,
                    bg="white", soft_masks=None):
    """Paint reaction-diffusion patterns with their region's color.

    Uses soft masks (from boundary smoothing) for anti-aliased zone edges.
    Each pixel blends contributions from all colors weighted by soft_mask,
    producing smooth organic transitions instead of hard pixel-edge clipping.

    High V = pattern = region's color.  Low V = background.

    `bg` may be a named color ("white"/"gray"/"black"), a hex "#RRGGBB"
    string, or an (r, g, b) tuple in [0, 255].
    """
    H, W = V.shape
    bg_rgb = parse_bg(bg)  # shape (3,)
    output = np.empty((H, W, 3), dtype=np.float64)
    output[:] = bg_rgb

    for c in range(n_colors):
        hard_mask = labels == c
        v_region = V[hard_mask]

        v_min, v_max = v_region.min(), v_region.max()
        if v_max - v_min > 1e-10:
            v_norm = (v_region - v_min) / (v_max - v_min)
        else:
            v_norm = np.ones_like(v_region)

        t = v_norm * pattern_strength

        if soft_masks is not None:
            layer = np.empty((H, W, 3), dtype=np.float64)
            layer[:] = bg_rgb
            layer[hard_mask] = (palette[c] * t[:, np.newaxis]
                                + bg_rgb * (1.0 - t[:, np.newaxis]))
            w = soft_masks[c][:, :, np.newaxis]  # (H, W, 1)
            output += w * (layer - bg_rgb)
        else:
            output[hard_mask] = (palette[c] * t[:, np.newaxis]
                                 + bg_rgb * (1.0 - t[:, np.newaxis]))

    return np.clip(output, 0, 255).astype(np.uint8)


def save_image(image, path, original_size=None):
    """Save image array, optionally upscaling to original size."""
    img = Image.fromarray(image)
    if original_size is not None:
        img = img.resize(original_size, Image.LANCZOS)
    img.save(path)
    print(f"  Output saved to: {path}")


def main():
    args = parse_args()
    rng = np.random.default_rng(args.seed)

    print(f"Loading image: {args.input}")
    original_img = Image.open(args.input).convert("RGB")
    original_size = (original_img.width, original_img.height)
    image = load_image(args.input, args.scale)
    H, W = image.shape[:2]
    print(f"  Working size: {W}x{H} (scale={args.scale})")

    print(f"Quantizing to {args.n_colors} colors...")
    labels, palette = quantize_colors(image, args.n_colors, rng)

    # Smooth zone boundaries
    soft_masks = None
    if args.smooth_sigma > 0:
        print(f"Smoothing zone boundaries (sigma={args.smooth_sigma})...")
        labels, soft_masks = smooth_zone_boundaries(labels, args.n_colors, args.smooth_sigma)
        print("  Boundaries smoothed: pixel staircases -> organic curves")

    for i in range(args.n_colors):
        r, g, b = int(round(palette[i][0])), int(round(palette[i][1])), int(round(palette[i][2]))
        n_pixels = np.sum(labels == i)
        pct = 100.0 * n_pixels / (H * W)
        print(f"  Color {i}: #{r:02X}{g:02X}{b:02X}  ({pct:.1f}% of image)")

    params = load_params(args.params, args.n_colors, palette)
    for i, p in enumerate(params):
        print(f"  Color {i} params: f={p['f']}, k={p['k']}, Du={p['Du']}, Dv={p['Dv']}")

    if args.quantized_only:
        quantized = palette[labels].astype(np.uint8)
        base, ext = os.path.splitext(args.output)
        save_image(quantized, f"{base}_quantized{ext}",
                   original_size if args.scale != 1.0 else None)
        export_params_template(palette, params, f"{base}_params.tsv")
        print("Done (quantized-only mode).")
        return

    print("Building uniform parameter maps per zone...")
    Du_map, Dv_map, f_map, k_map = build_parameter_maps(labels, params)

    print("Precomputing barrier masks (outside zone = occupied, Dirichlet BC)...")
    same_color_masks, U_pad, V_pad = precompute_neighbor_masks(labels)

    print(f"Initializing fields (start density: {args.start_density})...")
    U, V = initialize_fields(labels, args.n_colors, rng, args.start_density)

    soft_barrier = not args.hard_barrier
    print(f"Running simulation (max_iter={args.max_iter}, dt={args.dt})...")
    print(f"  dU/dt = Du*Lap(U) - U*V^2 + f*(1-U)")
    print(f"  dV/dt = Dv*Lap(V) + U*V^2 - (f+k)*V")
    if soft_barrier:
        print(f"  Soft barrier: outside U={NATURAL_TROUGH_U}, V={NATURAL_TROUGH_V} "
              f"(empirical natural trough — no fade gap)")
    else:
        print(f"  Hard barrier: outside U=0, V=0 (drains substrate, fade gap)")
    U, V = simulate(
        U, V, Du_map, Dv_map, f_map, k_map,
        same_color_masks, U_pad, V_pad,
        labels, args.n_colors, args.max_iter, args.dt,
        args.conv_tol, args.check_every, args.save_every,
        palette, args.pattern_strength, args.output, args.background,
        soft_masks, soft_barrier=soft_barrier,
    )

    print("Compositing output image...")
    output = composite_image(V, labels, args.n_colors, palette,
                             args.pattern_strength, args.background, soft_masks)
    save_image(output, args.output,
               original_size if args.scale != 1.0 else None)

    print("Done.")


if __name__ == "__main__":
    main()

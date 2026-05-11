"""Single source of truth for Gray-Scott pattern presets and density names.

Both engine modules and the GUI import from here. Keeps PATTERN_PRESETS
(dict, used by the GUI) and DEFAULT_PARAM_SETS (list, used by the engines)
strictly in sync — the list is derived from the dict.
"""

# (f, k) per named Gray-Scott regime. Keep the insertion order — it doubles
# as the fallback assignment when colors don't have an explicit preset
# (color i gets PATTERN_PRESETS[i % len(...)].
PATTERN_PRESETS = {
    "coral":            (0.055, 0.062),   # Karl Sims / Munafo θ-κ border
    "labyrinth":        (0.062, 0.061),   # Munafo π; same regime as fingerprints
    "stripe fragments": (0.078, 0.061),   # off-catalog high-f
    "short worms":      (0.058, 0.065),   # Munafo μ
    "maze":             (0.054, 0.063),   # Munafo κ — dense coral/labyrinth hybrid
    "sparse worms":     (0.067, 0.063),   # informal, no canonical reference
    "sparse stripes":   (0.064, 0.065),   # π/μ border
    "fingerprints":     (0.060, 0.063),   # Munafo π; same regime as labyrinth
    "flakes":           (0.055, 0.065),   # informal, no canonical reference
    "mitosis":          (0.028, 0.062),   # Pearson λ — needs start_density=scarce
    "zebrafish":        (0.035, 0.060),   # Rougier
    "solitons":         (0.024, 0.060),   # Pearson ζ — needs start_density=scarce
    "wavelets":         (0.018, 0.050),   # Pearson α/β — needs start_density=scarce
}

# Ordered list of (f, k) — used by engines as the fallback parameter cycle.
DEFAULT_PARAM_SETS = list(PATTERN_PRESETS.values())

# Presets that require start_density="scarce" — their reaction cannot
# nucleate from uniform-noise init and will collapse to V=0. Mitosis /
# solitons / wavelets fail because f is too low to sustain nucleation;
# zebrafish fails because single-pixel noise can't seed a stable stripe
# (stripes need a patch-sized seed, which scarce provides).
REQUIRES_SCARCE = ("zebrafish", "mitosis", "solitons", "wavelets")

# Default Gray-Scott diffusion coefficients. Shared by both engines.
DU_DEFAULT = 0.16
DV_DEFAULT = 0.08

# Density names (per-color start density for the classic engine).
DENSITY_NAMES = ["minimal", "scarce", "medium", "high"]
# Densities with no noise floor — both satisfy the REQUIRES_SCARCE patterns.
NO_NOISE_DENSITIES = ("minimal", "scarce")

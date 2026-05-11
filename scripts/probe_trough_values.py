"""Measure (U, V) at pattern troughs/peaks in free-running Gray-Scott.

The hard barrier currently injects U=0, V=0 across zone walls. That's
unnatural: at a real pattern trough, V is near 0 BUT U is near 1
(substrate is replenished where the reaction is dormant). The hard
barrier therefore not only ends V at the wall, it also DRAINS U into
the wall, which starves the reaction near the edge — that's where
the fade gap comes from.

This script runs Gray-Scott on a torus (no walls) for several presets
and reports what U and V actually settle to at troughs vs peaks.
"""
import numpy as np

PRESETS = {
    "coral":     (0.055, 0.062),
    "labyrinth": (0.062, 0.061),
    "maze":      (0.054, 0.063),
    "zebrafish": (0.035, 0.060),
    "mitosis":   (0.028, 0.062),
}

DU, DV = 0.16, 0.08
SIZE = 192
ITERS = 12000


def run(f, k, scarce):
    rng = np.random.default_rng(7)
    U = np.ones((SIZE, SIZE))
    if scarce:
        V = np.zeros((SIZE, SIZE))
    else:
        V = rng.uniform(0.0, 0.25, (SIZE, SIZE))
    cy = cx = SIZE // 2
    V[cy - 4:cy + 4, cx - 4:cx + 4] = 0.5
    U[cy - 4:cy + 4, cx - 4:cx + 4] = 0.5

    for _ in range(ITERS):
        lap_U = (np.roll(U, 1, 0) + np.roll(U, -1, 0)
                 + np.roll(U, 1, 1) + np.roll(U, -1, 1) - 4.0 * U)
        lap_V = (np.roll(V, 1, 0) + np.roll(V, -1, 0)
                 + np.roll(V, 1, 1) + np.roll(V, -1, 1) - 4.0 * V)
        uvv = U * V * V
        U = np.clip(U + DU * lap_U - uvv + f * (1.0 - U), 0.0, 1.0)
        V = np.clip(V + DV * lap_V + uvv - (f + k) * V, 0.0, 1.0)
    return U, V


print(f"{'preset':<10s} | {'V_trough':>9s} {'V_peak':>9s} {'V_mean':>9s} | "
      f"{'U_trough':>9s} {'U_peak':>9s} {'U_mean':>9s}")
print("-" * 78)
for name, (f, k) in PRESETS.items():
    scarce = f < 0.04
    U, V = run(f, k, scarce)
    # Define "trough" as the bottom 10% of V values (within the active region),
    # "peak" as the top 10%. Edge of pattern naturally lives at the trough.
    v_thr_lo = np.quantile(V, 0.10)
    v_thr_hi = np.quantile(V, 0.90)
    trough = V <= v_thr_lo
    peak = V >= v_thr_hi
    print(f"{name:<10s} | {V[trough].mean():9.4f} {V[peak].mean():9.4f} "
          f"{V.mean():9.4f} | {U[trough].mean():9.4f} {U[peak].mean():9.4f} "
          f"{U.mean():9.4f}")

print()
print("Trivial fixed point of dU/dt=0, dV/dt=0 with V=0:  U=1, V=0")
print("That's what a natural pattern trough asymptotes to.")

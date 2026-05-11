#!/usr/bin/env python3
"""Desktop GUI for image-based Gray-Scott reaction-diffusion.

Wraps the engine modules: load an image, quantize its colors, enable or
disable each color, assign a named pattern preset per color, run the simulation
with a live preview, then save the final composited result. All the simulation
physics comes from engine_classic / engine_leaky — this file is the UI layer
and a background thread that drives simulate() with preview callbacks.
"""

import os
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser

import numpy as np
from PIL import Image, ImageTk
from platformdirs import user_cache_dir
import ttkbootstrap as ttkb

# Default theme; user can toggle to "darkly" via View menu (added later).
DEFAULT_THEME = "flatly"

from . import engine_classic as rd
from . import engine_leaky as rdl
from .presets import (
    DENSITY_NAMES,
    DU_DEFAULT,
    DV_DEFAULT,
    NO_NOISE_DENSITIES,
    PATTERN_PRESETS,
    REQUIRES_SCARCE,
)

PATTERN_NAMES = ["random"] + list(PATTERN_PRESETS.keys())

# Thumbnails are cached per user under the platform's standard cache dir
# (~/Library/Caches/RDStudio on macOS, %LOCALAPPDATA%\RDStudio\Cache on
# Windows, ~/.cache/RDStudio on Linux). This survives PyInstaller bundles
# where the package directory is read-only.
THUMB_DIR = os.path.join(user_cache_dir("RDStudio", "RNMateos"), "pattern_thumbnails")
THUMB_DISPLAY = 48
THUMB_SIM_SIZE = 96
THUMB_ITERS = 4500
SWATCH_SIZE = 22

# Working-resolution presets. Values are the longer-side pixel count.
# Simulation cost is roughly quadratic in this number.
SIZE_PRESETS = {
    "Small (512)":   512,
    "Medium (1024)": 1024,
    "Large (1536)":  1536,
    "Giant (2048)":  2048,
}
DEFAULT_SIZE_PRESET = "Medium (1024)"


def _make_thumbnail_pil(f, k, size=THUMB_SIM_SIZE, iters=THUMB_ITERS):
    """Run Gray-Scott with periodic BCs on a small grid to visualize a preset.

    Uses toroidal topology (np.roll) so the pattern fills the frame — this is
    purely illustrative; the actual simulation uses Dirichlet BCs at zone
    boundaries (patterns fade at edges).

    Low-feed regimes (f < 0.04, e.g. canonical mitosis) cannot nucleate from
    uniform noise — they need a pure central seed. High-feed presets keep the
    legacy noise+seed init so their cached thumbnails remain consistent.
    """
    rng = np.random.default_rng(42)
    U = np.ones((size, size), dtype=np.float64)
    if f < 0.04:
        V = np.zeros((size, size), dtype=np.float64)
        iters = max(iters, 6000)
    else:
        V = rng.uniform(0.0, 0.25, (size, size))
    cy = cx = size // 2
    V[cy - 6:cy + 6, cx - 6:cx + 6] = 0.5
    U[cy - 6:cy + 6, cx - 6:cx + 6] = 0.5
    for _ in range(iters):
        lap_U = (np.roll(U, 1, 0) + np.roll(U, -1, 0)
                 + np.roll(U, 1, 1) + np.roll(U, -1, 1) - 4.0 * U)
        lap_V = (np.roll(V, 1, 0) + np.roll(V, -1, 0)
                 + np.roll(V, 1, 1) + np.roll(V, -1, 1) - 4.0 * V)
        uvv = U * V * V
        U = np.clip(U + DU_DEFAULT * lap_U - uvv + f * (1.0 - U), 0.0, 1.0)
        V = np.clip(V + DV_DEFAULT * lap_V + uvv - (f + k) * V, 0.0, 1.0)
    vmin, vmax = float(V.min()), float(V.max())
    if vmax - vmin > 1e-6:
        g = ((V - vmin) / (vmax - vmin) * 255.0).astype(np.uint8)
    else:
        g = (V * 255.0).astype(np.uint8)
    return Image.fromarray(g, mode='L').convert('RGB').resize(
        (THUMB_DISPLAY, THUMB_DISPLAY), Image.LANCZOS)


def _hex_color(c):
    r, g, b = int(round(c[0])), int(round(c[1])), int(round(c[2]))
    return f"#{r:02X}{g:02X}{b:02X}"


class ColorRow:
    """One row of the color list: checkbox, swatch, hex, pattern, thumb, density."""

    def __init__(self, parent, idx, hex_color, pattern_default,
                 density_default, thumbnails):
        self.idx = idx
        self.thumbnails = thumbnails

        self.frame = ttk.Frame(parent)
        self.enabled_var = tk.BooleanVar(value=True)
        self.pattern_var = tk.StringVar(value=pattern_default)
        self.density_var = tk.StringVar(value=density_default)

        ttk.Checkbutton(self.frame, variable=self.enabled_var).grid(
            row=0, column=0, padx=(2, 4))

        swatch = tk.Canvas(self.frame, width=SWATCH_SIZE, height=SWATCH_SIZE,
                           highlightthickness=1, highlightbackground="#777")
        swatch.create_rectangle(0, 0, SWATCH_SIZE, SWATCH_SIZE,
                                fill=hex_color, outline="")
        swatch.grid(row=0, column=1, padx=2)

        ttk.Label(self.frame, text=hex_color, width=9,
                  font=("Courier", 9)).grid(row=0, column=2, padx=2)

        pattern_combo = ttk.Combobox(self.frame, values=PATTERN_NAMES,
                                     textvariable=self.pattern_var,
                                     state="readonly", width=14)
        pattern_combo.grid(row=0, column=3, padx=4)
        pattern_combo.bind("<<ComboboxSelected>>",
                           lambda e: self._on_pattern_change())

        self.thumb_label = ttk.Label(self.frame, width=7, anchor=tk.CENTER)
        self.thumb_label.grid(row=0, column=4, padx=4)

        self.density_combo = ttk.Combobox(self.frame, values=DENSITY_NAMES,
                                          textvariable=self.density_var,
                                          state="readonly", width=7)
        self.density_combo.grid(row=0, column=5, padx=2)

        self._refresh_thumb()

    def set_density_enabled(self, enabled):
        """Density combo is meaningless in leaky mode (saturate is the only seed)."""
        self.density_combo.configure(state="readonly" if enabled else "disabled")

    def _on_pattern_change(self):
        # Low-feed presets cannot nucleate from uniform noise, so auto-promote
        # the density to "scarce" when the user picks one. One-way helper —
        # we don't downgrade back to medium when leaving a low-feed preset.
        if self.pattern_var.get() in REQUIRES_SCARCE:
            if self.density_var.get() not in NO_NOISE_DENSITIES:
                self.density_var.set("scarce")
        self._refresh_thumb()

    def _refresh_thumb(self):
        name = self.pattern_var.get()
        photo = self.thumbnails.get(name)
        if photo is not None:
            self.thumb_label.configure(image=photo, text="")
        else:
            label = "rand" if name == "random" else "..."
            self.thumb_label.configure(image="", text=label)

    def refresh_thumb(self):
        self._refresh_thumb()

    @property
    def enabled(self):
        return bool(self.enabled_var.get())

    @property
    def pattern(self):
        return self.pattern_var.get()

    @property
    def density(self):
        return self.density_var.get()


class App(ttkb.Window):
    def __init__(self):
        super().__init__(themename=DEFAULT_THEME)
        self.title("Reaction-Diffusion Studio")
        self.geometry("1180x840")

        # Image / quantization state
        self.image_path = None
        self.original_pil = None          # PIL image at original resolution
        self.working_image = None         # np.ndarray RGB uint8 used for sim
        self.labels = None                # (H,W) int
        self.palette = None               # (n,3) float
        self.quantized_pil = None
        self.output_pil = None            # final composited result
        self.preview_pil = None           # latest live preview during sim

        # Per-color UI
        self.color_rows = []
        self.thumbnails = {}              # pattern name -> PhotoImage (main thread)
        self._preview_photo = None
        self._quantize_after_id = None
        self._preview_after_id = None
        self._working_after_id = None
        self._syncing_size = False        # re-entrancy guard for preset <-> spinbox sync

        # Simulation thread
        self.sim_thread = None
        self.stop_event = threading.Event()
        self.msg_queue = queue.Queue()
        self.rng = np.random.default_rng()

        # Recorded animation frames (uint8 HxWx3 ndarrays). Populated by the
        # sim worker when recording is enabled, consumed by on_save_animation.
        self.recorded_frames = []

        self._build_ui()

        # "random" placeholder thumb — subtle checker pattern
        self._install_random_thumb()
        # Generate / load presets in a background thread
        threading.Thread(target=self._thumbnail_worker, daemon=True).start()

        self.after(50, self._poll_queue)
        self.after(200, self._refresh_preview)

    # ---------- UI construction ----------

    def _build_ui(self):
        toolbar = ttk.Frame(self, padding=(8, 8, 8, 4))
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="Load image...", bootstyle="primary",
                   command=self.on_load_image).pack(side=tk.LEFT)
        self.path_label = ttk.Label(toolbar, text="(no image loaded)",
                                    foreground="#666")
        self.path_label.pack(side=tk.LEFT, padx=(10, 0))

        self.save_anim_button = ttk.Button(
            toolbar, text="Save animation as...", bootstyle="secondary",
            command=self.on_save_animation, state=tk.DISABLED)
        self.save_anim_button.pack(side=tk.RIGHT, padx=4)
        self.save_button = ttk.Button(toolbar, text="Save output as...",
                                      bootstyle="primary",
                                      command=self.on_save, state=tk.DISABLED)
        self.save_button.pack(side=tk.RIGHT, padx=4)
        self.run_button = ttk.Button(toolbar, text="Run", bootstyle="success",
                                     command=self.on_run_or_stop,
                                     state=tk.DISABLED)
        self.run_button.pack(side=tk.RIGHT, padx=4)

        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # Left: preview area
        left = ttk.Frame(paned)
        paned.add(left, weight=3)

        view_bar = ttk.Frame(left)
        view_bar.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        ttk.Label(view_bar, text="View:").pack(side=tk.LEFT, padx=(0, 6))
        self.view_var = tk.StringVar(value="quantized")
        for label, value in [("Original", "original"),
                             ("Quantized", "quantized"),
                             ("Output", "output")]:
            ttk.Radiobutton(view_bar, text=label, variable=self.view_var,
                            value=value, command=self._refresh_preview
                            ).pack(side=tk.LEFT, padx=4)

        self.canvas = tk.Canvas(left, bg="#1e1e1e", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<Configure>",
                         lambda e: self._schedule_preview_refresh())

        prog_frame = ttk.Frame(left, padding=(0, 6, 0, 0))
        prog_frame.pack(side=tk.BOTTOM, fill=tk.X)
        self.progress = ttk.Progressbar(prog_frame, mode="determinate",
                                        maximum=100)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.status_label = ttk.Label(prog_frame, text="Ready.", width=36,
                                      anchor=tk.W)
        self.status_label.pack(side=tk.LEFT, padx=(8, 0))

        # Right: controls (scrollable so all settings are reachable even when
        # the window is short).
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        self.right_canvas = tk.Canvas(right, highlightthickness=0,
                                      bg=self.cget("bg"))
        right_scrollbar = ttk.Scrollbar(right, orient=tk.VERTICAL,
                                        command=self.right_canvas.yview)
        self.right_canvas.configure(yscrollcommand=right_scrollbar.set)
        self.right_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        right_inner = ttk.Frame(self.right_canvas)
        self._right_window = self.right_canvas.create_window(
            (0, 0), window=right_inner, anchor="nw")
        right_inner.bind(
            "<Configure>",
            lambda e: self.right_canvas.configure(
                scrollregion=self.right_canvas.bbox("all")))
        self.right_canvas.bind(
            "<Configure>",
            lambda e: self.right_canvas.itemconfigure(
                self._right_window, width=e.width))
        self._install_right_pane_wheel_scroll()

        # Mode selector — classic (per-zone walls) vs leaky (multi-channel V).
        mode_group = ttk.LabelFrame(right_inner, text="Mode", padding=6)
        mode_group.pack(fill=tk.X, padx=6, pady=(6, 0))
        self.mode_var = tk.StringVar(value="classic")
        ttk.Radiobutton(
            mode_group, text="Isolated zones (classic)",
            variable=self.mode_var, value="classic",
            command=self._on_mode_changed,
        ).pack(anchor=tk.W)
        ttk.Radiobutton(
            mode_group,
            text="Leaky channels (no walls; colors carried by V)",
            variable=self.mode_var, value="leaky",
            command=self._on_mode_changed,
        ).pack(anchor=tk.W)

        # Input settings
        input_group = ttk.LabelFrame(right_inner, text="Input", padding=6)
        input_group.pack(fill=tk.X, padx=6, pady=(6, 0))

        ttk.Label(input_group, text="Number of colors:").grid(
            row=0, column=0, sticky=tk.W, pady=2)
        self.n_colors_var = tk.IntVar(value=5)
        ttk.Spinbox(input_group, from_=2, to=100, width=6,
                    textvariable=self.n_colors_var,
                    command=self._schedule_quantize).grid(
            row=0, column=1, sticky=tk.W, pady=2)
        self.n_colors_var.trace_add(
            "write", lambda *a: self._schedule_quantize())

        ttk.Label(input_group, text="Working size:").grid(
            row=1, column=0, sticky=tk.W, pady=2)
        self.size_preset_var = tk.StringVar(value=DEFAULT_SIZE_PRESET)
        preset_values = list(SIZE_PRESETS.keys()) + ["Custom"]
        preset_combo = ttk.Combobox(input_group, values=preset_values,
                                    textvariable=self.size_preset_var,
                                    state="readonly", width=14)
        preset_combo.grid(row=1, column=1, sticky=tk.W, pady=2)
        preset_combo.bind("<<ComboboxSelected>>",
                          lambda e: self._on_size_preset_changed())

        ttk.Label(input_group, text="Max side (px):").grid(
            row=2, column=0, sticky=tk.W, pady=2)
        self.working_max_var = tk.IntVar(value=SIZE_PRESETS[DEFAULT_SIZE_PRESET])
        ttk.Spinbox(input_group, from_=128, to=4096, increment=64, width=8,
                    textvariable=self.working_max_var).grid(
            row=2, column=1, sticky=tk.W, pady=2)
        self.working_max_var.trace_add(
            "write", lambda *a: self._on_size_pixels_changed())

        self.resolution_label = ttk.Label(
            input_group, text="(no image loaded)", foreground="#666")
        self.resolution_label.grid(row=3, column=0, columnspan=2,
                                   sticky=tk.W, pady=(4, 0))

        # Color list — grows naturally; the right-pane scrollbar handles
        # overflow when the list is long.
        color_group = ttk.LabelFrame(
            right_inner,
            text="Colors  (uncheck to treat a region as background)",
            padding=6)
        color_group.pack(fill=tk.X, padx=6, pady=(6, 0))

        scarce_list = ", ".join(REQUIRES_SCARCE)
        ttk.Label(color_group,
                  text=f"Per-color: pattern and initial density. "
                       f"{scarce_list} need 'scarce' density — "
                       f"auto-selected when chosen.",
                  foreground="#666", wraplength=420,
                  justify=tk.LEFT).pack(side=tk.BOTTOM, fill=tk.X,
                                        pady=(4, 0))

        # Master "apply to all" row — set every color to the same pattern
        # and density in one click.
        master_frame = ttk.Frame(color_group)
        master_frame.pack(side=tk.TOP, fill=tk.X, pady=(0, 4))
        ttk.Label(master_frame, text="Apply to all:").pack(
            side=tk.LEFT, padx=(2, 4))
        self.master_pattern_var = tk.StringVar(value=PATTERN_NAMES[1])
        ttk.Combobox(master_frame, values=PATTERN_NAMES,
                     textvariable=self.master_pattern_var,
                     state="readonly", width=14).pack(side=tk.LEFT, padx=2)
        self.master_density_var = tk.StringVar(value="medium")
        ttk.Combobox(master_frame, values=DENSITY_NAMES,
                     textvariable=self.master_density_var,
                     state="readonly", width=7).pack(side=tk.LEFT, padx=2)
        ttk.Button(master_frame, text="Apply",
                   command=self._apply_master_to_all).pack(
            side=tk.LEFT, padx=(6, 2))

        self.color_host = ttk.Frame(color_group)
        self.color_host.pack(fill=tk.X, expand=False)

        # Simulation settings
        sim_group = ttk.LabelFrame(right_inner, text="Simulation settings",
                                   padding=6)
        sim_group.pack(fill=tk.X, padx=6, pady=6)

        def add_spin(r, label, var, frm, to, inc, fmt=None):
            ttk.Label(sim_group, text=label).grid(
                row=r, column=0, sticky=tk.W, pady=2)
            kwargs = dict(from_=frm, to=to, increment=inc, width=10,
                          textvariable=var)
            if fmt:
                kwargs["format"] = fmt
            ttk.Spinbox(sim_group, **kwargs).grid(
                row=r, column=1, sticky=tk.W, pady=2)

        self.max_iter_var = tk.IntVar(value=10000)
        add_spin(0, "Max iterations:", self.max_iter_var, 500, 50000, 500)
        self.pattern_strength_var = tk.DoubleVar(value=0.7)
        add_spin(1, "Pattern strength:", self.pattern_strength_var,
                 0.1, 1.0, 0.05, fmt="%.2f")
        self.smooth_sigma_var = tk.DoubleVar(value=3.0)
        add_spin(2, "Smooth sigma:", self.smooth_sigma_var,
                 0.0, 10.0, 0.5, fmt="%.1f")
        self.preview_every_var = tk.IntVar(value=400)
        add_spin(3, "Preview every:", self.preview_every_var, 100, 5000, 100)

        # Pixel-art mode: disables Gaussian boundary smoothing (which is the
        # only step that lets diagonal neighbors influence a pixel's zone).
        # When checked, zone boundaries stay pixel-exact and only V/H
        # neighbors participate anywhere in the pipeline.
        self.pixel_art_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            sim_group,
            text="Pixel art mode (V/H neighbors only, no diagonal smoothing)",
            variable=self.pixel_art_var,
        ).grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

        # Soft barrier: cross-zone neighbor uses U=1, V=0 — the natural pattern
        # trough state. Substrate is replenished at the wall instead of drained,
        # so patterns grow right up to the edge with no fade gap. Unchecked =
        # hard Dirichlet (U=0, V=0 outside): substrate drains, pattern fades
        # back over a half-wavelength, visible gap between colors.
        self.soft_barrier_var = tk.BooleanVar(value=True)
        self.soft_barrier_check = ttk.Checkbutton(
            sim_group,
            text="Soft zone barrier (wall = natural trough, no fade gap)",
            variable=self.soft_barrier_var,
        )
        self.soft_barrier_check.grid(row=6, column=0, columnspan=2,
                                     sticky=tk.W, pady=(2, 0))

        # Seeding controls (global). Variety = shape mix used at each anchor;
        # placement = where anchors are dropped. "edges" suppresses interior
        # seeds and the noise floor so patterns must grow inward from the
        # zone outline.
        ttk.Label(sim_group, text="Seeds:").grid(
            row=7, column=0, sticky=tk.W, pady=(4, 0))
        seed_frame = ttk.Frame(sim_group)
        seed_frame.grid(row=7, column=1, sticky=tk.W, pady=(4, 0))
        self.seed_variety_var = tk.StringVar(value="uniform")
        ttk.Combobox(seed_frame, values=list(rd.SEED_VARIETY_NAMES),
                     textvariable=self.seed_variety_var,
                     state="readonly", width=8).pack(side=tk.LEFT)
        self.seed_placement_var = tk.StringVar(value="anywhere")
        ttk.Combobox(seed_frame, values=list(rd.SEED_PLACEMENT_NAMES),
                     textvariable=self.seed_placement_var,
                     state="readonly", width=9).pack(side=tk.LEFT, padx=(4, 0))

        # Seed size range — only used when variety = "mixed". Each seed
        # picks a random bounding-box side uniformly in [min, max], then
        # fills it with Bernoulli noise. Default 1..9 keeps current
        # behavior; raise the max for chunkier seeds, raise the min to
        # avoid tiny single-pixel dots.
        ttk.Label(sim_group, text="Seed size:").grid(
            row=8, column=0, sticky=tk.W, pady=(2, 0))
        size_frame = ttk.Frame(sim_group)
        size_frame.grid(row=8, column=1, sticky=tk.W, pady=(2, 0))
        self.seed_size_min_var = tk.IntVar(value=rd.DEFAULT_SEED_SIZE_RANGE[0])
        self.seed_size_max_var = tk.IntVar(value=rd.DEFAULT_SEED_SIZE_RANGE[1])
        ttk.Spinbox(size_frame, from_=1, to=50, increment=1, width=4,
                    textvariable=self.seed_size_min_var).pack(side=tk.LEFT)
        ttk.Label(size_frame, text=" to ").pack(side=tk.LEFT)
        ttk.Spinbox(size_frame, from_=1, to=50, increment=1, width=4,
                    textvariable=self.seed_size_max_var).pack(side=tk.LEFT)
        ttk.Label(size_frame, text="  (mixed only)",
                  foreground="#666").pack(side=tk.LEFT, padx=(4, 0))

        # Record animation: capture each preview frame so it can be saved
        # later as a GIF or MP4. Frames are produced at `preview_every`, so
        # total frame count = max_iter / preview_every (memory-bounded by it).
        record_frame = ttk.Frame(sim_group)
        record_frame.grid(row=9, column=0, columnspan=2, sticky=tk.W,
                          pady=(2, 0))
        self.record_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            record_frame,
            text="Record animation (GIF/MP4)",
            variable=self.record_var,
        ).pack(side=tk.LEFT)
        ttk.Label(record_frame, text="  FPS:").pack(side=tk.LEFT)
        self.record_fps_var = tk.IntVar(value=20)
        ttk.Spinbox(record_frame, from_=1, to=60, increment=1, width=5,
                    textvariable=self.record_fps_var).pack(
            side=tk.LEFT, padx=(2, 0))

        ttk.Label(sim_group, text="Background:").grid(
            row=4, column=0, sticky=tk.W, pady=2)
        bg_frame = ttk.Frame(sim_group)
        bg_frame.grid(row=4, column=1, sticky=tk.W, pady=2)
        self.bg_var = tk.StringVar(value="white")
        self.bg_custom_hex = "#808080"  # remembered between "custom..." picks
        bg_combo = ttk.Combobox(
            bg_frame, values=["white", "gray", "black", "custom..."],
            textvariable=self.bg_var, state="readonly", width=10)
        bg_combo.pack(side=tk.LEFT)
        bg_combo.bind("<<ComboboxSelected>>", lambda e: self._on_bg_changed())
        self.bg_swatch = tk.Canvas(
            bg_frame, width=SWATCH_SIZE, height=SWATCH_SIZE,
            highlightthickness=1, highlightbackground="#777")
        self.bg_swatch.pack(side=tk.LEFT, padx=(6, 0))
        self.bg_swatch.bind("<Button-1>", lambda e: self._pick_custom_bg())
        self._refresh_bg_swatch()

    def _install_right_pane_wheel_scroll(self):
        """Route mouse-wheel events to the right pane only when the pointer
        is over it. We bind globally (the canvas's own children otherwise
        swallow the event), then walk up from the widget under the mouse to
        check it's actually inside the right pane before scrolling."""

        def _walks_through_right(widget):
            target = self.right_canvas
            while widget is not None:
                if widget is target:
                    return True
                try:
                    widget = widget.master
                except Exception:
                    return False
            return False

        def _on_wheel(event):
            w = self.winfo_containing(event.x_root, event.y_root)
            if not _walks_through_right(w):
                return
            if event.num == 4:
                step = -1
            elif event.num == 5:
                step = 1
            else:
                # Windows: event.delta is a multiple of 120; macOS uses small
                # ints. Either way, sign tells us direction.
                if not getattr(event, "delta", 0):
                    return
                step = -1 if event.delta > 0 else 1
            self.right_canvas.yview_scroll(step, "units")

        self.bind_all("<MouseWheel>", _on_wheel, add="+")
        self.bind_all("<Button-4>", _on_wheel, add="+")
        self.bind_all("<Button-5>", _on_wheel, add="+")

    # ---------- Thumbnails ----------

    def _install_random_thumb(self):
        arr = np.full((THUMB_DISPLAY, THUMB_DISPLAY, 3), 230, dtype=np.uint8)
        arr[::8, :] = 170
        arr[:, ::8] = 170
        self.thumbnails["random"] = ImageTk.PhotoImage(Image.fromarray(arr))

    def _thumbnail_worker(self):
        """Background: load cached preset thumbnails or generate + cache them."""
        os.makedirs(THUMB_DIR, exist_ok=True)
        for name, (f, k) in PATTERN_PRESETS.items():
            path = os.path.join(THUMB_DIR, f"{name.replace(' ', '_')}.png")
            pil = None
            if os.path.exists(path):
                try:
                    pil = Image.open(path).convert("RGB")
                except Exception:
                    pil = None
            if pil is None:
                pil = _make_thumbnail_pil(f, k)
                try:
                    pil.save(path)
                except Exception:
                    pass
            self.msg_queue.put(("thumbnail", name, pil))

    # ---------- Preview canvas ----------

    def _current_preview_pil(self):
        mode = self.view_var.get()
        if mode == "output":
            return self.preview_pil or self.output_pil or self.quantized_pil
        if mode == "quantized":
            return self.quantized_pil or self.original_pil
        return self.original_pil

    def _schedule_preview_refresh(self):
        if self._preview_after_id is not None:
            try:
                self.after_cancel(self._preview_after_id)
            except Exception:
                pass
        self._preview_after_id = self.after(60, self._refresh_preview)

    def _refresh_preview(self):
        self._preview_after_id = None
        self.canvas.delete("all")
        cw = max(2, self.canvas.winfo_width())
        ch = max(2, self.canvas.winfo_height())
        pil = self._current_preview_pil()
        if pil is None:
            self.canvas.create_text(
                cw // 2, ch // 2, fill="#aaa",
                text="Load an image to begin.", font=("Helvetica", 14))
            return
        w, h = pil.size
        s = min(cw / w, ch / h, 1.0)
        if s < 1.0:
            pil = pil.resize((max(1, int(w * s)), max(1, int(h * s))),
                             Image.LANCZOS)
        self._preview_photo = ImageTk.PhotoImage(pil)
        self.canvas.create_image(cw // 2, ch // 2,
                                 image=self._preview_photo)

    # ---------- Background color ----------

    def _on_bg_changed(self):
        if self.bg_var.get() == "custom...":
            self._pick_custom_bg()
        self._refresh_bg_swatch()

    def _pick_custom_bg(self):
        """Open the Tk color chooser (HSV wheel + RGB sliders + hex input)."""
        result = colorchooser.askcolor(
            initialcolor=self.bg_custom_hex,
            title="Pick background color")
        if result and result[1]:
            self.bg_custom_hex = result[1]
            self.bg_var.set("custom...")
        self._refresh_bg_swatch()

    def _refresh_bg_swatch(self):
        named = {"white": "#FFFFFF", "gray": "#808080", "black": "#000000"}
        name = self.bg_var.get()
        hex_c = self.bg_custom_hex if name == "custom..." else named.get(name, "#FFFFFF")
        self.bg_swatch.delete("all")
        self.bg_swatch.create_rectangle(
            0, 0, SWATCH_SIZE, SWATCH_SIZE, fill=hex_c, outline="")

    def _current_bg_value(self):
        """Background value to hand to the simulation — name or hex string."""
        name = self.bg_var.get()
        return self.bg_custom_hex if name == "custom..." else name

    # ---------- Queue poller (main thread) ----------

    def _poll_queue(self):
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                kind = msg[0]
                if kind == "thumbnail":
                    _, name, pil = msg
                    self.thumbnails[name] = ImageTk.PhotoImage(pil)
                    for row in self.color_rows:
                        row.refresh_thumb()
                elif kind == "progress":
                    _, frac, text = msg
                    self.progress["value"] = max(0.0, min(100.0, frac * 100.0))
                    self.status_label.configure(text=text)
                elif kind == "preview":
                    _, img_arr = msg
                    self.preview_pil = Image.fromarray(img_arr)
                    if self.view_var.get() != "output":
                        self.view_var.set("output")
                    self._refresh_preview()
                elif kind == "done":
                    _, img_arr, frames = msg
                    self.output_pil = Image.fromarray(img_arr)
                    self.preview_pil = None
                    self.recorded_frames = frames or []
                    self.view_var.set("output")
                    self._refresh_preview()
                    self._finish_sim(success=True)
                elif kind == "stopped":
                    _, frames = msg
                    self.recorded_frames = frames or []
                    self._finish_sim(success=False, stopped=True)
                elif kind == "error":
                    _, err_text = msg
                    self._finish_sim(success=False, error=err_text)
        except queue.Empty:
            pass
        self.after(50, self._poll_queue)

    # ---------- Image loading / quantization ----------

    def on_load_image(self):
        path = filedialog.askopenfilename(
            title="Load image",
            filetypes=[("Images", "*.png *.jpg *.jpeg *.bmp *.webp *.gif"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            pil = Image.open(path).convert("RGB")
        except Exception as e:
            messagebox.showerror("Could not load image", str(e))
            return

        self.image_path = path
        self.original_pil = pil
        self._update_working_image()

        self.path_label.configure(text=os.path.basename(path),
                                  foreground="#000")
        self.output_pil = None
        self.preview_pil = None
        self.view_var.set("quantized")
        self._quantize()
        self.run_button.configure(state=tk.NORMAL)

    def _update_working_image(self):
        """Rescale original_pil so its longer side equals `working_max_var`.

        Pixel art (few unique colors) is upscaled with NEAREST so original
        colors and sharp edges are preserved; LANCZOS would interpolate
        single-pixel features into gradients that KMeans then smears across
        the image. Detection: <=64 unique colors in the original.
        """
        if self.original_pil is None:
            return
        try:
            max_side = int(self.working_max_var.get())
        except (tk.TclError, ValueError):
            return
        max_side = max(64, min(8192, max_side))
        pil = self.original_pil
        w, h = pil.size
        s = max_side / max(w, h)
        if abs(s - 1.0) > 1e-6:
            orig_arr = np.asarray(pil).reshape(-1, 3)
            n_unique = len(np.unique(orig_arr, axis=0))
            is_pixel_art = n_unique <= 64 and s > 1.0
            resample = Image.NEAREST if is_pixel_art else Image.LANCZOS
            pil_work = pil.resize(
                (max(1, int(round(w * s))), max(1, int(round(h * s)))),
                resample)
        else:
            n_unique = None
            is_pixel_art = False
            pil_work = pil
        self.working_image = np.array(pil_work)
        suffix = "  [pixel art: NEAREST]" if is_pixel_art else ""
        self.resolution_label.configure(
            text=f"Working: {pil_work.width} × {pil_work.height} px  "
                 f"(original {self.original_pil.width} × "
                 f"{self.original_pil.height}){suffix}")

    def _on_size_preset_changed(self):
        """User picked a preset from the combobox."""
        if self._syncing_size:
            return
        name = self.size_preset_var.get()
        if name == "Custom":
            return                            # leave pixel value as-is
        pixels = SIZE_PRESETS.get(name)
        if pixels is None:
            return
        self._syncing_size = True
        try:
            self.working_max_var.set(pixels)
        finally:
            self._syncing_size = False
        self._schedule_reload_working()

    def _on_size_pixels_changed(self):
        """User typed in the pixel spinbox."""
        if self._syncing_size:
            return
        try:
            pixels = int(self.working_max_var.get())
        except (tk.TclError, ValueError):
            return
        # If the value exactly matches a preset, reflect it; otherwise Custom.
        matched = next((n for n, v in SIZE_PRESETS.items() if v == pixels),
                       "Custom")
        self._syncing_size = True
        try:
            self.size_preset_var.set(matched)
        finally:
            self._syncing_size = False
        self._schedule_reload_working()

    def _schedule_reload_working(self):
        if self.original_pil is None:
            return
        if self._working_after_id is not None:
            try:
                self.after_cancel(self._working_after_id)
            except Exception:
                pass
        self._working_after_id = self.after(400, self._reload_working)

    def _reload_working(self):
        self._working_after_id = None
        self._update_working_image()
        self._quantize()
        if self.view_var.get() == "quantized":
            self._refresh_preview()

    def _schedule_quantize(self):
        if self.working_image is None:
            return
        if self._quantize_after_id is not None:
            try:
                self.after_cancel(self._quantize_after_id)
            except Exception:
                pass
        self._quantize_after_id = self.after(350, self._quantize)

    def _quantize(self):
        self._quantize_after_id = None
        if self.working_image is None:
            return
        try:
            n = int(self.n_colors_var.get())
        except (tk.TclError, ValueError):
            return
        if n < 2 or n > 100:
            return
        self.status_label.configure(text=f"Quantizing ({n} colors)...")
        self.update_idletasks()
        labels, palette = rd.quantize_colors(self.working_image, n, self.rng)
        self.labels = labels
        self.palette = palette
        self.quantized_pil = Image.fromarray(palette[labels].astype(np.uint8))
        self._rebuild_color_rows()
        if self.view_var.get() == "quantized":
            self._refresh_preview()
        self.status_label.configure(text=f"Quantized to {n} colors.")

    def _rebuild_color_rows(self):
        # Preserve (enabled, pattern, density) for indices that still exist.
        prev = {row.idx: (row.enabled, row.pattern, row.density)
                for row in self.color_rows}
        for w in self.color_host.winfo_children():
            w.destroy()
        self.color_rows = []
        if self.palette is None:
            return
        pattern_list = list(PATTERN_PRESETS.keys())
        for i, color in enumerate(self.palette):
            hex_c = _hex_color(color)
            default_pattern = pattern_list[i % len(pattern_list)]
            default_density = "medium"
            if i in prev:
                default_pattern = prev[i][1]
                default_density = prev[i][2]
            if (default_density not in NO_NOISE_DENSITIES
                    and default_pattern in REQUIRES_SCARCE):
                default_density = "scarce"
            row = ColorRow(self.color_host, i, hex_c, default_pattern,
                           default_density, thumbnails=self.thumbnails)
            if i in prev:
                row.enabled_var.set(prev[i][0])
            row.frame.pack(fill=tk.X, pady=2, padx=2)
            self.color_rows.append(row)
        self._apply_mode_to_widgets()

    def _on_mode_changed(self):
        """Mode selector changed — enable/disable controls that don't apply."""
        self._apply_mode_to_widgets()

    def _apply_mode_to_widgets(self):
        """Disable controls irrelevant to the current mode.

        Classic: everything enabled. Leaky: density combos and soft-barrier
        checkbox are disabled (saturate is the only seeding option, and
        there are no zone walls to soften).
        """
        is_leaky = self.mode_var.get() == "leaky"
        # Soft barrier is meaningless in leaky mode — no walls to soften.
        self.soft_barrier_check.configure(
            state="disabled" if is_leaky else "normal")
        for row in self.color_rows:
            row.set_density_enabled(not is_leaky)

    def _apply_master_to_all(self):
        pattern = self.master_pattern_var.get()
        density = self.master_density_var.get()
        if pattern in REQUIRES_SCARCE and density not in NO_NOISE_DENSITIES:
            density = "scarce"
            self.master_density_var.set("scarce")
        for row in self.color_rows:
            row.pattern_var.set(pattern)
            row.density_var.set(density)
            row.refresh_thumb()

    # ---------- Simulation ----------

    def on_run_or_stop(self):
        if self.sim_thread is not None and self.sim_thread.is_alive():
            self.stop_event.set()
            self.status_label.configure(text="Stopping...")
            self.run_button.configure(state=tk.DISABLED)
            return
        self._start_simulation()

    def _start_simulation(self):
        if self.labels is None or self.palette is None:
            messagebox.showwarning("No image", "Load an image first.")
            return
        if not any(r.enabled for r in self.color_rows):
            messagebox.showwarning(
                "No colors enabled",
                "Enable at least one color.")
            return
        try:
            max_iter = int(self.max_iter_var.get())
            pattern_strength = float(self.pattern_strength_var.get())
            smooth_sigma = float(self.smooth_sigma_var.get())
            preview_every = int(self.preview_every_var.get())
        except (tk.TclError, ValueError):
            messagebox.showerror("Invalid settings",
                                 "One of the numeric fields is invalid.")
            return
        if self.pixel_art_var.get():
            smooth_sigma = 0.0
        bg = self._current_bg_value()
        bg_rgb = rd.parse_bg(bg)  # (3,) float array, values in [0, 255]

        self.stop_event.clear()
        self.progress["value"] = 0
        self.status_label.configure(text="Starting...")
        self.run_button.configure(text="Stop", bootstyle="danger")
        self.save_button.configure(state=tk.DISABLED)
        self.save_anim_button.configure(state=tk.DISABLED)
        self.preview_pil = None
        self.output_pil = None
        self.recorded_frames = []
        record = bool(self.record_var.get())
        seed_variety = self.seed_variety_var.get()
        seed_placement = self.seed_placement_var.get()
        try:
            smin = max(1, int(self.seed_size_min_var.get()))
            smax = max(smin, int(self.seed_size_max_var.get()))
        except (tk.TclError, ValueError):
            smin, smax = rd.DEFAULT_SEED_SIZE_RANGE
        seed_size_range = (smin, smax)

        if self.mode_var.get() == "leaky":
            # Leaky: per-channel (f, k); disabled rows mean "don't seed".
            n = len(self.color_rows)
            f_arr = np.zeros(n)
            k_arr = np.zeros(n)
            enabled_mask = []
            for i, row in enumerate(self.color_rows):
                enabled_mask.append(row.enabled)
                if row.pattern == "random":
                    f, k = rd.DEFAULT_PARAM_SETS[i % len(rd.DEFAULT_PARAM_SETS)]
                else:
                    f, k = PATTERN_PRESETS[row.pattern]
                f_arr[i] = f
                k_arr[i] = k
            self.sim_thread = threading.Thread(
                target=self._simulation_worker_leaky,
                kwargs=dict(labels=self.labels.copy(),
                            palette=self.palette.copy(),
                            f_arr=f_arr, k_arr=k_arr,
                            enabled_mask=enabled_mask,
                            max_iter=max_iter,
                            pattern_strength=pattern_strength,
                            smooth_sigma=smooth_sigma,
                            preview_every=preview_every, bg=bg,
                            record=record,
                            seed_variety=seed_variety,
                            seed_placement=seed_placement,
                            seed_size_range=seed_size_range,
                            rng=np.random.default_rng()),
                daemon=True)
            self.sim_thread.start()
            return

        # Classic mode (unchanged): per-zone walls, single V.
        soft_barrier = bool(self.soft_barrier_var.get())
        start_density = [row.density for row in self.color_rows]

        # Disabled colors: paint their palette entry with the bg color (so
        # composite_image renders those zones as background), and zero their
        # reaction params so the simulation does nothing in those regions.
        palette = self.palette.copy()
        params = []
        for i, row in enumerate(self.color_rows):
            if row.enabled:
                if row.pattern == "random":
                    f, k = rd.DEFAULT_PARAM_SETS[i % len(rd.DEFAULT_PARAM_SETS)]
                else:
                    f, k = PATTERN_PRESETS[row.pattern]
                params.append({"f": f, "k": k,
                               "Du": DU_DEFAULT, "Dv": DV_DEFAULT})
            else:
                palette[i] = bg_rgb
                params.append({"f": 0.0, "k": 0.0, "Du": 0.0, "Dv": 0.0})

        self.sim_thread = threading.Thread(
            target=self._simulation_worker,
            kwargs=dict(labels=self.labels.copy(), palette=palette,
                        params=params, max_iter=max_iter,
                        pattern_strength=pattern_strength,
                        smooth_sigma=smooth_sigma,
                        preview_every=preview_every, bg=bg,
                        start_density=start_density,
                        soft_barrier=soft_barrier,
                        record=record,
                        seed_variety=seed_variety,
                        seed_placement=seed_placement,
                        seed_size_range=seed_size_range,
                        rng=np.random.default_rng()),
            daemon=True)
        self.sim_thread.start()

    def _simulation_worker(self, labels, palette, params, max_iter,
                           pattern_strength, smooth_sigma, preview_every,
                           bg, start_density, soft_barrier, record,
                           seed_variety, seed_placement, seed_size_range,
                           rng):
        try:
            n_colors = len(palette)

            if smooth_sigma > 0:
                self.msg_queue.put(
                    ("progress", 0.0, "Smoothing zone boundaries..."))
                labels, soft_masks = rd.smooth_zone_boundaries(
                    labels, n_colors, smooth_sigma)
            else:
                soft_masks = None

            self.msg_queue.put(("progress", 0.0, "Preparing fields..."))
            Du_map, Dv_map, f_map, k_map = rd.build_parameter_maps(
                labels, params)
            same_color_masks, U_pad, V_pad = rd.precompute_neighbor_masks(
                labels)
            U, V = rd.initialize_fields(labels, n_colors, rng, start_density,
                                        seed_variety=seed_variety,
                                        seed_placement=seed_placement,
                                        seed_size_range=seed_size_range)

            local_frames = []

            def preview_cb(it, img):
                if record:
                    local_frames.append(img.copy())
                self.msg_queue.put(("preview", img))
                self.msg_queue.put((
                    "progress", it / max_iter,
                    f"Iteration {it}/{max_iter}"))

            def progress_cb(it, n_active):
                self.msg_queue.put((
                    "progress", it / max_iter,
                    f"Iteration {it}/{max_iter}  "
                    f"({n_active}/{n_colors} active)"))

            def should_stop():
                return self.stop_event.is_set()

            U, V = rd.simulate(
                U, V, Du_map, Dv_map, f_map, k_map,
                same_color_masks, U_pad, V_pad,
                labels, n_colors, max_iter, 1.0,
                1e-5, 200, preview_every,
                palette, pattern_strength, None, bg, soft_masks,
                preview_callback=preview_cb,
                progress_callback=progress_cb,
                stop_flag=should_stop,
                soft_barrier=soft_barrier,
            )

            if self.stop_event.is_set():
                self.msg_queue.put(("stopped", local_frames if record else None))
                return

            output = rd.composite_image(
                V, labels, n_colors, palette,
                pattern_strength, bg, soft_masks)
            if record:
                local_frames.append(output.copy())
            self.msg_queue.put(("done", output, local_frames if record else None))
        except Exception as e:
            import traceback
            self.msg_queue.put(("error",
                                f"{e}\n\n{traceback.format_exc()}"))

    def _simulation_worker_leaky(self, labels, palette, f_arr, k_arr,
                                 enabled_mask, max_iter, pattern_strength,
                                 smooth_sigma, preview_every, bg, record,
                                 seed_variety, seed_placement,
                                 seed_size_range, rng):
        try:
            n_colors = len(palette)

            # Smoothing the label map gives nicer initial channel boundaries
            # (organic curves vs pixel staircases). Channels still flow freely
            # across them — soft_masks aren't used by the leaky compositor.
            if smooth_sigma > 0:
                self.msg_queue.put(
                    ("progress", 0.0, "Smoothing zone boundaries..."))
                labels, _ = rd.smooth_zone_boundaries(
                    labels, n_colors, smooth_sigma)

            self.msg_queue.put(("progress", 0.0, "Preparing fields..."))
            U, V_stack = rdl.initialize_fields_leaky(
                labels, n_colors, rng, enabled_mask,
                seed_variety=seed_variety,
                seed_placement=seed_placement,
                seed_size_range=seed_size_range)

            local_frames = []

            def preview_cb(it, img):
                if record:
                    local_frames.append(img.copy())
                self.msg_queue.put(("preview", img))
                self.msg_queue.put((
                    "progress", it / max_iter,
                    f"Iteration {it}/{max_iter}"))

            def progress_cb(it, n_active):
                self.msg_queue.put((
                    "progress", it / max_iter,
                    f"Iteration {it}/{max_iter}  ({n_active} channels)"))

            def should_stop():
                return self.stop_event.is_set()

            U, V_stack = rdl.simulate_leaky(
                U, V_stack, f_arr, k_arr, DU_DEFAULT, DV_DEFAULT,
                max_iter, 1.0, 1e-5, 200, preview_every,
                palette, pattern_strength, None, bg,
                preview_callback=preview_cb,
                progress_callback=progress_cb,
                stop_flag=should_stop,
            )

            if self.stop_event.is_set():
                self.msg_queue.put(("stopped", local_frames if record else None))
                return

            output = rdl.composite_image_leaky(
                V_stack, palette, pattern_strength, bg)
            if record:
                local_frames.append(output.copy())
            self.msg_queue.put(("done", output, local_frames if record else None))
        except Exception as e:
            import traceback
            self.msg_queue.put(("error",
                                f"{e}\n\n{traceback.format_exc()}"))

    def _finish_sim(self, success, stopped=False, error=None):
        self.run_button.configure(text="Run", bootstyle="success",
                                  state=tk.NORMAL)
        if success:
            self.progress["value"] = 100
            self.status_label.configure(text="Done.")
            self.save_button.configure(state=tk.NORMAL)
        elif stopped:
            self.status_label.configure(text="Stopped.")
        else:
            self.status_label.configure(text="Error.")
            if error:
                messagebox.showerror("Simulation error", error)
        if len(self.recorded_frames) >= 2:
            self.save_anim_button.configure(state=tk.NORMAL)
        self.sim_thread = None

    # ---------- Save ----------

    def on_save(self):
        if self.output_pil is None:
            return
        default = "rd_output.png"
        if self.image_path:
            base = os.path.splitext(os.path.basename(self.image_path))[0]
            default = f"{base}_rd.png"
        path = filedialog.asksaveasfilename(
            defaultextension=".png",
            initialfile=default,
            filetypes=[("PNG", "*.png"),
                       ("JPEG", "*.jpg *.jpeg"),
                       ("All files", "*.*")])
        if not path:
            return
        try:
            self.output_pil.save(path)
            self.status_label.configure(
                text=f"Saved: {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Save failed", str(e))

    def on_save_animation(self):
        """Export the recorded frames as a GIF or MP4.

        GIF uses Pillow's save_all (always available). MP4/WebM go through
        imageio + ffmpeg; if either is missing, we explain how to install it.
        """
        if len(self.recorded_frames) < 2:
            messagebox.showinfo(
                "No animation",
                "No frames were recorded. Enable 'Record animation' in the "
                "simulation settings before running.")
            return

        default = "rd_animation.gif"
        if self.image_path:
            base = os.path.splitext(os.path.basename(self.image_path))[0]
            default = f"{base}_rd.gif"
        path = filedialog.asksaveasfilename(
            defaultextension=".gif",
            initialfile=default,
            filetypes=[("Animated GIF", "*.gif"),
                       ("MP4 video", "*.mp4"),
                       ("WebM video", "*.webm")])
        if not path:
            return

        try:
            fps = max(1, int(self.record_fps_var.get()))
        except (tk.TclError, ValueError):
            fps = 20

        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".gif":
                self._save_gif(path, fps)
            elif ext in (".mp4", ".webm", ".mov", ".mkv"):
                self._save_video(path, fps)
            else:
                messagebox.showerror(
                    "Unsupported format",
                    f"Unknown extension '{ext}'. Use .gif, .mp4, or .webm.")
                return
        except ImportError as e:
            messagebox.showerror(
                "Missing dependency",
                f"{e}\n\nInstall with:\n    pip install imageio imageio-ffmpeg")
            return
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return

        self.status_label.configure(
            text=f"Saved animation: {os.path.basename(path)}")

    def _save_gif(self, path, fps):
        frames = [Image.fromarray(f) for f in self.recorded_frames]
        duration_ms = max(1, int(round(1000.0 / fps)))
        frames[0].save(
            path, save_all=True, append_images=frames[1:],
            duration=duration_ms, loop=0, optimize=False, disposal=2)

    def _save_video(self, path, fps):
        try:
            import imageio.v2 as imageio
        except ImportError:
            try:
                import imageio
            except ImportError as e:
                raise ImportError(
                    "imageio is required to save videos.") from e

        # Most encoders (libx264 in particular) need even dimensions.
        frames = [self._even_pad(f) for f in self.recorded_frames]
        ext = os.path.splitext(path)[1].lower()
        kwargs = {"fps": fps}
        if ext == ".mp4":
            kwargs.update(codec="libx264", quality=8,
                          macro_block_size=1, pixelformat="yuv420p")
        writer = imageio.get_writer(path, **kwargs)
        try:
            for f in frames:
                writer.append_data(f)
        finally:
            writer.close()

    @staticmethod
    def _even_pad(arr):
        h, w = arr.shape[:2]
        if h % 2 == 0 and w % 2 == 0:
            return arr
        new_h = h + (h % 2)
        new_w = w + (w % 2)
        out = np.zeros((new_h, new_w, arr.shape[2]), dtype=arr.dtype)
        out[:h, :w] = arr
        if h % 2:
            out[h:, :w] = arr[-1:, :w]
        if w % 2:
            out[:h, w:] = arr[:h, -1:]
        return out


def _enable_high_dpi():
    """Tell Windows we want crisp scaling on 4K screens."""
    if sys.platform != "win32":
        return
    try:
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def main():
    _enable_high_dpi()
    App().mainloop()


if __name__ == "__main__":
    main()

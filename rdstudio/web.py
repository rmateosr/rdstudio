"""Gradio web UI for Reaction-Diffusion Studio.

Designed to run on Hugging Face Spaces' free CPU tier. Hard caps on image
size and iteration count keep a single run under ~30 s; queueing keeps
concurrent users serialized on the one CPU.

The desktop GUI lives in `app.py`; this is a separate, simpler UI layer
that wraps the same engine functions.
"""

from __future__ import annotations

import numpy as np
import gradio as gr
from PIL import Image

from . import engine_classic as rd
from . import engine_leaky as rdl
from .presets import PATTERN_PRESETS, DU_DEFAULT, DV_DEFAULT, REQUIRES_SCARCE


MAX_DIM_PX = 512          # longest-side cap on the input image
MAX_ITER_MEDIUM = 3000    # "Medium" simulation length cap
MAX_ITER_SHORT = 1500     # "Short" simulation length cap
SMOOTH_SIGMA = 3.0        # boundary smoothing — matches desktop default
DT = 1.0
CONV_TOL = 1e-5
CHECK_EVERY = 200

PATTERN_CHOICES = ["random"] + list(PATTERN_PRESETS.keys())


def _resize_for_web(pil_img: Image.Image) -> Image.Image:
    """Cap longest side at MAX_DIM_PX. Bypasses engine's 1024-px auto-upscale."""
    img = pil_img.convert("RGB")
    long_side = max(img.width, img.height)
    if long_side > MAX_DIM_PX:
        s = MAX_DIM_PX / long_side
        img = img.resize(
            (max(1, int(img.width * s)), max(1, int(img.height * s))),
            Image.LANCZOS,
        )
    return img


def _pick_pattern(pattern_choice: str, rng: np.random.Generator) -> str:
    if pattern_choice == "random":
        names = list(PATTERN_PRESETS.keys())
        return names[int(rng.integers(len(names)))]
    return pattern_choice


def run(
    image: Image.Image,
    n_colors: int,
    pattern: str,
    mode: str,
    pattern_strength: float,
    sim_length: str,
):
    if image is None:
        raise gr.Error("Please upload an image first.")

    rng = np.random.default_rng()
    chosen_pattern = _pick_pattern(pattern, rng)
    f, k = PATTERN_PRESETS[chosen_pattern]
    start_density = "scarce" if chosen_pattern in REQUIRES_SCARCE else "medium"

    max_iter = MAX_ITER_SHORT if sim_length == "Short" else MAX_ITER_MEDIUM

    pil = _resize_for_web(image)
    arr = np.array(pil)
    H, W = arr.shape[:2]

    labels, palette = rd.quantize_colors(arr, int(n_colors), rng)
    labels, soft_masks = rd.smooth_zone_boundaries(labels, int(n_colors), SMOOTH_SIGMA)

    params = [
        {"f": f, "k": k, "Du": DU_DEFAULT, "Dv": DV_DEFAULT}
        for _ in range(int(n_colors))
    ]

    if mode == "Bleeding colors":
        U, V_stack = rdl.initialize_fields_leaky(labels, int(n_colors), rng)
        f_arr = np.array([p["f"] for p in params])
        k_arr = np.array([p["k"] for p in params])
        rdl.simulate_leaky(
            U, V_stack, f_arr, k_arr, DU_DEFAULT, DV_DEFAULT,
            max_iter=max_iter, dt=DT, conv_tol=CONV_TOL,
            check_every=CHECK_EVERY, save_every=0,
            palette=palette, pattern_strength=float(pattern_strength),
            output_base=None, bg="white",
            progress_callback=None,
        )
        out = rdl.composite_image_leaky(
            V_stack, palette, float(pattern_strength), bg="white",
        )
    else:
        Du_map, Dv_map, f_map, k_map = rd.build_parameter_maps(labels, params)
        same_color_masks, U_pad, V_pad = rd.precompute_neighbor_masks(labels)
        U, V = rd.initialize_fields(labels, int(n_colors), rng, start_density)
        rd.simulate(
            U, V, Du_map, Dv_map, f_map, k_map,
            same_color_masks, U_pad, V_pad,
            labels, int(n_colors), max_iter, DT,
            CONV_TOL, CHECK_EVERY, 0,
            palette, float(pattern_strength), None, "white",
            soft_masks, progress_callback=None, soft_barrier=True,
        )
        out = rd.composite_image(
            V, labels, int(n_colors), palette,
            float(pattern_strength), bg="white", soft_masks=soft_masks,
        )

    return out, f"Pattern used: **{chosen_pattern}**  ·  Output: {W}×{H} px"


_DESCRIPTION = """
# Reaction-Diffusion Studio — Web Preview

Turn any image into a Gray-Scott reaction-diffusion painting. Upload a
photo, pick how many colors to keep, choose a pattern, and watch the
simulation grow organic textures inside each color region.

*This is a preview running on a free CPU. Inputs are capped at
**512 px** longest-side and **3000 iterations**. For larger images,
longer runs, and video export, install the desktop app from*
[**GitHub Releases**](https://github.com/rmateosr/rdstudio/releases/latest).
""".strip()


def build_demo() -> gr.Blocks:
    with gr.Blocks(title="Reaction-Diffusion Studio") as demo:
        gr.Markdown(_DESCRIPTION)
        with gr.Row():
            with gr.Column(scale=1):
                inp = gr.Image(type="pil", label="Input image", height=320)
                n_colors = gr.Slider(2, 6, value=4, step=1, label="Number of colors")
                pattern = gr.Dropdown(
                    PATTERN_CHOICES, value="coral", label="Pattern",
                )
                mode = gr.Radio(
                    ["Sharp zones", "Bleeding colors"],
                    value="Sharp zones",
                    label="Mode",
                    info="Sharp zones = each pattern stays inside its color. "
                         "Bleeding colors = patterns leak across boundaries.",
                )
                pattern_strength = gr.Slider(
                    0.2, 1.0, value=0.7, step=0.05,
                    label="Pattern strength",
                )
                sim_length = gr.Radio(
                    ["Short", "Medium"], value="Medium",
                    label="Simulation length",
                    info=f"Short = {MAX_ITER_SHORT} iter, "
                         f"Medium = {MAX_ITER_MEDIUM} iter",
                )
                go = gr.Button("Run", variant="primary")
            with gr.Column(scale=1):
                out_img = gr.Image(label="Output", height=480)
                out_md = gr.Markdown()

        go.click(
            run,
            inputs=[inp, n_colors, pattern, mode, pattern_strength, sim_length],
            outputs=[out_img, out_md],
        )

        gr.Markdown(
            "Source: [github.com/rmateosr/rdstudio]"
            "(https://github.com/rmateosr/rdstudio). MIT licensed."
        )
    return demo


demo = build_demo()

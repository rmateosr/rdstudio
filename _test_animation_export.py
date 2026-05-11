"""Headless test of the animation recording + export paths from app.py.

Builds a tiny RGB test image, runs 100 iterations of the classic simulator
capturing a frame every 10 steps, and saves the result to both .gif and .mp4
using the same _save_gif / _save_video methods the GUI uses.

Run with:  python3 _test_animation_export.py
"""
import os
import sys
import tempfile

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import reaction_diffusion as rd
from app import App, DU_DEFAULT, DV_DEFAULT, PATTERN_PRESETS


def make_test_image(size=64):
    """A 64x64 image with 3 distinct color blocks — easy to quantize cleanly."""
    img = np.zeros((size, size, 3), dtype=np.uint8)
    img[:, : size // 3] = (220, 60, 60)        # red
    img[:, size // 3 : 2 * size // 3] = (60, 200, 60)  # green
    img[:, 2 * size // 3 :] = (60, 80, 220)    # blue
    return img


def run_short_sim(record_every=10, max_iter=100):
    rng = np.random.default_rng(0)
    img = make_test_image()
    labels, palette = rd.quantize_colors(img, 3, rng)
    n_colors = len(palette)

    f_coral, k_coral = PATTERN_PRESETS["coral"]
    params = [{"f": f_coral, "k": k_coral, "Du": DU_DEFAULT, "Dv": DV_DEFAULT}
              for _ in range(n_colors)]

    Du_map, Dv_map, f_map, k_map = rd.build_parameter_maps(labels, params)
    same_color_masks, U_pad, V_pad = rd.precompute_neighbor_masks(labels)
    U, V = rd.initialize_fields(labels, n_colors, rng, ["medium"] * n_colors)

    frames = []

    def preview_cb(it, img_arr):
        frames.append(img_arr.copy())

    rd.simulate(
        U, V, Du_map, Dv_map, f_map, k_map,
        same_color_masks, U_pad, V_pad, labels, n_colors,
        max_iter, 1.0, 1e-5, 200, record_every,
        palette, 0.7, None, "white", None,
        preview_callback=preview_cb,
        soft_barrier=True,
    )
    print(f"Captured {len(frames)} frames during sim.")
    return frames


def main():
    frames = run_short_sim(record_every=10, max_iter=100)
    assert len(frames) >= 5, f"expected several frames, got {len(frames)}"

    # Use the App's save methods without instantiating the Tk window.
    # Both _save_gif and _save_video read self.recorded_frames and
    # self.record_fps_var; we mock those.
    class FakeFpsVar:
        def get(self):
            return 20

    class StubApp:
        recorded_frames = frames
        record_fps_var = FakeFpsVar()
        # Bind the methods from App so they share exact behavior.
        _save_gif = App._save_gif
        _save_video = App._save_video
        # _even_pad is @staticmethod on App; preserve that on the stub.
        _even_pad = staticmethod(App._even_pad)

    stub = StubApp()
    out_dir = tempfile.mkdtemp(prefix="rd_anim_test_")

    gif_path = os.path.join(out_dir, "test.gif")
    stub._save_gif(gif_path, fps=20)
    gif_size = os.path.getsize(gif_path)
    gif_pil = Image.open(gif_path)
    print(f"GIF: {gif_path}")
    print(f"     {gif_size} bytes, {getattr(gif_pil, 'n_frames', 1)} frames, "
          f"{gif_pil.size}, duration={gif_pil.info.get('duration')}ms")

    mp4_path = os.path.join(out_dir, "test.mp4")
    try:
        stub._save_video(mp4_path, fps=20)
        mp4_size = os.path.getsize(mp4_path)
        print(f"MP4: {mp4_path}")
        print(f"     {mp4_size} bytes")
    except ImportError as e:
        print(f"MP4 skipped — imageio missing: {e}")
        return 1
    except Exception as e:
        print(f"MP4 FAILED: {type(e).__name__}: {e}")
        return 2

    print("OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

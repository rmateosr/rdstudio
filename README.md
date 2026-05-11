# Reaction-Diffusion Studio

A desktop tool that turns an image into a reaction-diffusion painting.

Load an image, pick how many colors to quantize it into, choose a Gray-Scott
pattern (coral, labyrinth, maze, fingerprints, mitosis, …) for each color,
and let the simulation grow organic patterns inside each region. Save the
result as a still image or an animation.

> Status: pre-release. See [`RELEASE_PLAN.md`](RELEASE_PLAN.md) for the work
> plan toward a shipped v1.

## Install (developers)

```bash
pip install -e .
```

This installs the `rdstudio` and `rdstudio-cli` commands.

## Run

Launch the GUI:

```bash
rdstudio
# or, equivalently:
python -m rdstudio
```

Run the CLI engine on an image directly:

```bash
rdstudio-cli input.png -o output.png -n 5
```

## License

MIT — see [`LICENSE`](LICENSE).

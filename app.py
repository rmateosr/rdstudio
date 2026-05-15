"""Hugging Face Spaces entrypoint.

Spaces auto-runs the top-level `app.py`. The real UI lives in
`rdstudio/web.py`; this file just wires it up and queues requests so the
single free-tier CPU doesn't get hammered.
"""

from rdstudio.web import demo

if __name__ == "__main__":
    demo.queue(default_concurrency_limit=1, max_size=20).launch()

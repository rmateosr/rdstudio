"""Allow `python -m rdstudio` (and the PyInstaller bundle) to launch the GUI.

Absolute import so the same module works both as a package entry-point
(`python -m rdstudio` → relative-import context) and as a PyInstaller
top-level script (no package context).
"""

from rdstudio.app import main

if __name__ == "__main__":
    main()

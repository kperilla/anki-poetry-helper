#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "genanki>=0.13.1",
# ]
# ///
"""Regenerate the Anki deck defined in config.toml.

Shortcut for:
    uv run python poem_anki_deck.py --config config.toml

Usage:
    uv run run_config.py
"""

from pathlib import Path

import poem_anki_deck

if __name__ == "__main__":
    config_path = Path(__file__).parent / "config.toml"
    poem_anki_deck.main(["--config", str(config_path)])

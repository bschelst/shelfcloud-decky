# Shelfcloud project - https://shelfcloud.cc - bart@schelstraete.org
"""Small helpers used by the plugin backend (main.py)."""

import os
import shlex
from typing import List


def shell_join(parts: List[str]) -> str:
    """Build a Steam shortcut's launch options as one shell-safe string.

    Steam runs a shortcut through `/bin/sh -c "<exe> <options>"`, so every
    argument has to survive a round trip through the shell. Plain `" ".join`
    does not, and the way it fails is genuinely nasty:

    a GeForce NOW URL ends `?game-id=<uuid>&lang=en_US`, and that unquoted `&`
    is a shell operator. `sh` backgrounds everything before it and treats the
    rest as a second command.

    Quoting is therefore not tidiness here. Do not simplify this back.
    """
    return " ".join(shlex.quote(part) for part in parts)


def hardware_label() -> str:
    """What kind of machine this is, for naming the device on the gateway."""
    known = {"Jupiter": "Steam Deck", "Galileo": "Steam Deck OLED"}
    try:
        with open("/sys/class/dmi/id/product_name", encoding="utf-8") as handle:
            product = handle.read().strip()
    except Exception:
        product = ""
    if product in known:
        return known[product]
    if product and product.lower() not in ("to be filled by o.e.m.", "system product name",
                                           "default string", "none"):
        return product.replace(";", " ").replace(")", " ")[:48]
    try:
        return os.uname().nodename[:48] or "Shelfcloud plugin"
    except Exception:
        return "Shelfcloud plugin"

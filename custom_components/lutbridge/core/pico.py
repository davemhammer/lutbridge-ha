# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Dave Hammer
"""Pico identity and buttons. Pure Python, no Home Assistant imports.

Home Assistant names a Lutron device by the decimal form of the same 32-bit number the Pico transmits
on the air, so the on-air serial is just that number in hex: identifier `93694086` is serial `0595a886`.
"""
from __future__ import annotations

# Button masks, identical to PICO_BTN_* in firmware/pico_bridge/main/pico_logic.h.
ON = 0x01
OFF = 0x02
RAISE = 0x04
LOWER = 0x08
FAVORITE = 0x10
ALL = 0x1F

# The lutron_caseta device trigger names the middle button of a 3-button Pico "stop"; on the air it is
# the "favorite" button (body byte 0x03).
SUBTYPE_MASK = {"on": ON, "off": OFF, "raise": RAISE, "lower": LOWER, "stop": FAVORITE, "favorite": FAVORITE}

# Buttons each model has. Matched as a substring of the Home Assistant model string, for example
# "PJ2-3BRL-GXX-X01 (Pico3ButtonRaiseLower)".
MODEL_BUTTONS = (
    ("Pico3ButtonRaiseLower", ON | OFF | FAVORITE | RAISE | LOWER),
    ("Pico2ButtonRaiseLower", ON | OFF | RAISE | LOWER),
    ("Pico2Button", ON | OFF),
    ("Pico4Button", ON | OFF | RAISE | LOWER),
)


def serial_hex(identifier: int | str) -> str:
    """On-air serial (8 lowercase hex digits) from a lutron_caseta device identifier."""
    return f"{int(identifier):08x}"


def serial_int(serial: str) -> int:
    return int(serial, 16)


def buttons_for_model(model: str | None) -> int:
    """Button mask for a Pico model string, or ALL when the model is unknown."""
    for needle, mask in MODEL_BUTTONS:
        if model and needle in model:
            return mask
    return ALL


def is_pico_model(model: str | None) -> bool:
    return bool(model) and "Pico" in model

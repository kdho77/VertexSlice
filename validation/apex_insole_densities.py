#!/usr/bin/env python3
"""Kendon-authored Apex V2 insole rectilinear infill densities (95A baseline)."""
from __future__ import annotations

KENDON_INFILL_DENSITY_PRESETS: dict[str, int] = {
    "Extra Soft": 14,
    "Soft": 20,
    "Medium": 26,
    "Hard": 32,
    "Extra Hard": 38,
}

PROCESS_FILES: dict[str, str] = {
    "Extra Soft": "0.42mm TPU Insole Extra Soft @Vertex Apex V2.json",
    "Soft": "0.42mm TPU Insole @Vertex Apex V2.json",
    "Medium": "0.42mm TPU Insole Medium @Vertex Apex V2.json",
    "Hard": "0.42mm TPU Insole Hard @Vertex Apex V2.json",
    "Extra Hard": "0.42mm TPU Insole Extra Hard @Vertex Apex V2.json",
}

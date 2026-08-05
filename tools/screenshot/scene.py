#!/usr/bin/env python3
"""What the README screenshot shows: a small fleet of game servers with one
already connected and its console populated with real-looking RCON output.

None of this is real data: all hostnames, IPs, and player names are invented.
The game catalog (buttons, protocols, notes) is not duplicated here; the
generator reads it straight from simple_rcon_tool.py so the screenshot can
never show buttons the app doesn't actually have.
"""

SERVERS = [
    {
        "id": "srv-ark",
        "name": "Ragnarok Cluster",
        "host": "10.20.4.11",
        "port": 27020,
        "game": "ark_se",
        "buttons": [],
    },
    {
        "id": "srv-valheim",
        "name": "Iron Vale",
        "host": "10.20.4.24",
        "port": 2458,
        "game": "valheim",
        "buttons": [],
    },
    {
        "id": "srv-rust",
        "name": "Rustbelt PVE",
        "host": "10.20.4.31",
        "port": 28016,
        "game": "rust",
        "buttons": [],
    },
]

# Rendered into the console for the connected server (srv-ark) so the shot
# shows a live session rather than an empty panel.
CONSOLE_LINES = [
    ("Connected to 10.20.4.11:27020.", "ok", "srv-ark"),
    ("$ List Players", "cmd", "srv-ark"),
    ("0. Cerulean (76561198000112233)\n1. Basalt (76561198000556677)",
     "out", "srv-ark"),
    ("$ Save World", "cmd", "srv-ark"),
    ("World Saved", "out", "srv-ark"),
    ("$ Get Chat", "cmd", "srv-ark"),
    ("Cerulean: anyone up for a raid tonight?", "out", "srv-ark"),
]

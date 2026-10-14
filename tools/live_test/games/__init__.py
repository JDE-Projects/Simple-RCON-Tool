"""Definitions for games supported by the live test runner."""

from .minecraft import DEFINITION as MINECRAFT
from .valheim import DEFINITION as VALHEIM

GAMES = {MINECRAFT["key"]: MINECRAFT, VALHEIM["key"]: VALHEIM}

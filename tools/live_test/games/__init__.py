"""Definitions for games supported by the live test runner."""

from .eco import DEFINITION as ECO
from .minecraft import DEFINITION as MINECRAFT
from .valheim import DEFINITION as VALHEIM

GAMES = {ECO["key"]: ECO, MINECRAFT["key"]: MINECRAFT, VALHEIM["key"]: VALHEIM}

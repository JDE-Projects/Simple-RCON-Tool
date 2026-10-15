"""Definitions for games supported by the live test runner."""

from .conan import DEFINITION as CONAN
from .eco import DEFINITION as ECO
from .gmod import DEFINITION as GMOD
from .minecraft import DEFINITION as MINECRAFT
from .pz import DEFINITION as PZ
from .valheim import DEFINITION as VALHEIM

GAMES = {CONAN["key"]: CONAN, ECO["key"]: ECO, GMOD["key"]: GMOD, MINECRAFT["key"]: MINECRAFT, PZ["key"]: PZ, VALHEIM["key"]: VALHEIM}

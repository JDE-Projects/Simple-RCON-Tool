"""Definitions for games supported by the live test runner."""

from .ark_asa import DEFINITION as ARK_ASA
from .conan import DEFINITION as CONAN
from .eco import DEFINITION as ECO
from .gmod import DEFINITION as GMOD
from .minecraft import DEFINITION as MINECRAFT
from .pz import DEFINITION as PZ
from .squad import DEFINITION as SQUAD
from .valheim import DEFINITION as VALHEIM

GAMES = {ARK_ASA["key"]: ARK_ASA, CONAN["key"]: CONAN, ECO["key"]: ECO, GMOD["key"]: GMOD, MINECRAFT["key"]: MINECRAFT, PZ["key"]: PZ, SQUAD["key"]: SQUAD, VALHEIM["key"]: VALHEIM}

"""Guards the splash asset rules in sxt-theme-ui references/icon-splash.md.

PyInstaller's --splash is a Tk layered window that fakes transparency by filling
the background with magenta and asking Windows to key that color out. A soft or
partial-alpha edge blends toward magenta without landing on it exactly, and
whether a near-miss gets keyed is up to DWM and the display driver. That renders
clean on one machine and as a magenta outline on another, so the asset itself has
to be fully opaque and magenta-free.
"""

from pathlib import Path

from PySide6.QtGui import QImage

SPLASH_SIZE = (460, 280)
REPO_ROOT = Path(__file__).resolve().parent.parent


def _splash_pixels():
    """Yield (x, y, r, g, b, a) for every pixel of the app's splash PNG."""
    matches = sorted(REPO_ROOT.glob("*-splash.png"))
    assert len(matches) == 1, f"expected exactly one splash PNG, found {matches}"

    image = QImage(str(matches[0]))
    assert not image.isNull(), f"could not read {matches[0].name}"
    image = image.convertToFormat(QImage.Format_ARGB32)

    raw = memoryview(image.constBits())
    stride = image.bytesPerLine()
    for y in range(image.height()):
        row = y * stride
        for x in range(image.width()):
            b, g, r, a = raw[row + x * 4 : row + x * 4 + 4]
            yield x, y, r, g, b, a


def test_splash_is_the_standard_size():
    matches = sorted(REPO_ROOT.glob("*-splash.png"))
    assert len(matches) == 1, f"expected exactly one splash PNG, found {matches}"

    image = QImage(str(matches[0]))
    assert (image.width(), image.height()) == SPLASH_SIZE


def test_splash_is_fully_opaque():
    transparent = [(x, y) for x, y, _, _, _, a in _splash_pixels() if a != 255]

    assert not transparent, (
        f"{len(transparent)} pixel(s) are not fully opaque, first at "
        f"{transparent[0]}. The splash background is full bleed: every pixel must "
        f"be alpha 255, or PyInstaller's magenta color key can leave an outline."
    )


def test_splash_contains_no_magenta():
    magenta = [
        (x, y)
        for x, y, r, g, b, _ in _splash_pixels()
        if r > 240 and b > 240 and g < 20
    ]

    assert not magenta, (
        f"{len(magenta)} pixel(s) are magenta or near-magenta, first at "
        f"{magenta[0]}. PyInstaller keys magenta out, so those render as holes."
    )

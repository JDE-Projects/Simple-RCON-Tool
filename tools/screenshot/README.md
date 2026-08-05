# README screenshot

Regenerates `screenshots/rcon-light-dark.png` by rendering the real UI, not by
capturing it by hand.

```
python tools/screenshot/make_screenshot.py
```

## What it needs

- **Node 22 or later**, with an installed Edge or Chrome. Drives the browser
  headless over the DevTools protocol (`build-tools/screenshot/capture.mjs`,
  found automatically as a sibling of this repo; override with
  `--build-tools PATH`).
- **Pillow**, for tiling the two theme shots into one image
  (`build-tools/screenshot/compose.py`). Pillow is not one of this app's own
  dependencies and is not installed in this repo's `.venv`, so run this
  script with a Python that already has it (the system Python works) rather
  than adding Pillow to the app's own requirements.

`scene.py` holds the sample fleet of servers and the console lines shown as
already connected. All of it is invented: no real hostnames, IPs, or player
names.

## Options

- `--keep` leaves the temp folder (staged UI copy + capture shots) in place
  for inspection instead of deleting it.
- `--build-tools PATH` points at a build-tools repo checkout somewhere other
  than the default sibling folder.

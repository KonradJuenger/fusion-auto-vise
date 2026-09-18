# Publishing

## Repository contents

- `AutoVise/`: current add-in source, manifest, parallel library, and icons.
- `README.md`, `images/`, `docs/screenshots/`: tutorial and publication images.
- `tests/`: automated regression checks and optional Fusion inspection helper.
- `tools/build_release.py`: builds the installable ZIP from an explicit file list.

Fresh dialog screenshots were captured directly from Fusion on 2026-09-18.
The title image was supplied by the project owner. Development screenshots are
not used in the tutorial.

## Build a release

```sh
python -m unittest discover -s tests
python tools/build_release.py
```

The ZIP in `dist/` contains the `AutoVise` folder needed by Scripts and Add-Ins.
It excludes settings, generated logs, caches, reference CAD models, and local
editor configuration. The manifest supplies the release version.

Before publishing, choose a license and review the pending Git changes. No license
is inferred from a public repository. If a LICENSE file is present, the builder
includes it in the ZIP.

## Local files and history

Obsolete scripts, reference models, an old fit report, editor settings, and the
development screenshots were preserved in ignored `.local-archive/`. Local API
references, runtime settings, logs, and build output are also ignored. Do not upload
the workspace folder wholesale; publish the reviewed Git tree or the release ZIP.

Archiving files in the current tree does not remove them from existing Git history.
The previous commits still contain reference CAD files and historical debug material.
Review that history before making an existing repository public. No history rewrite,
commit, push, or publication is performed by the release builder.

#!/usr/bin/env python3
"""Build the self-contained Enquadra Debian package."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


VERSION = "0.1.0~alpha1"
PACKAGE = "enquadra"
ROOT = Path(__file__).resolve().parents[1]


def _copy_tree(source: Path, destination: Path) -> None:
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(part == "__pycache__" for part in relative.parts) or path.suffix == ".pyc":
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def _write(path: Path, content: str, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def _stage(stage: Path) -> None:
    package_root = stage / "usr/share/enquadra"
    _copy_tree(ROOT / "brio_control", package_root / "brio_control")
    shutil.copy2(ROOT / "scripts/setup-virtual-camera", package_root / "setup-virtual-camera")
    (stage / "usr/share/doc/enquadra").mkdir(parents=True, exist_ok=True)
    for license_name in ("COPYING", "COPYING.LESSER", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / license_name, stage / "usr/share/doc/enquadra" / license_name)

    icon = ROOT / "branding/enquadra.svg"
    (package_root / "branding").mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon, package_root / "branding/enquadra.svg")
    installed_icon = stage / "usr/share/icons/hicolor/scalable/apps/enquadra.svg"
    installed_icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(icon, installed_icon)
    for launcher in ("enquadra", "brio-control"):
        _write(stage / "usr/bin" / launcher, """#!/bin/sh
set -eu
export PYTHONPATH="/usr/share/enquadra${PYTHONPATH:+:$PYTHONPATH}"
exec /usr/bin/python3 -m brio_control "$@"
""", 0o755)
    _write(stage / "usr/share/applications/enquadra.desktop", """[Desktop Entry]
Name=Enquadra
Comment=Enquadramento, fundos e controles locais de câmera no Linux
Exec=enquadra
Icon=enquadra
Terminal=false
Type=Application
Categories=Settings;AudioVideo;
""")

    control = """Package: enquadra
Version: 0.1.0~alpha1
Provides: brio-control
Breaks: brio-control (<< 0.1.0~alpha1.1)
Replaces: brio-control (<< 0.1.0~alpha1.1)
Section: video
Priority: optional
Architecture: all
Maintainer: Caio Lombello <70986039+caiolombello@users.noreply.github.com>
Homepage: https://caiolombello.github.io/enquadra/
Description: Linux controls and local framing for Logitech Brio 500
 Enquadra provides local camera framing, backgrounds and V4L2/UVC controls.
Depends: python3 (>= 3.12), python3-gi, python3-numpy, python3-opencv, gir1.2-gtk-4.0, gir1.2-gstreamer-1.0, gir1.2-gst-plugins-base-1.0, gstreamer1.0-plugins-good, gstreamer1.0-plugins-base
Suggests: v4l2loopback-dkms, ffmpeg, pkexec, mokutil
"""
    _write(stage / "DEBIAN/control", control)
    _write(stage / "usr/share/doc/enquadra/copyright", """Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: Enquadra
Source: https://github.com/caiolombello/enquadra

Files: *
License: LGPL-3.0-or-later
Comment: Project license text is installed in usr/share/doc/enquadra/COPYING and COPYING.LESSER.

Files: usr/share/enquadra/brio_control/models/face_detection_yunet_*.onnx usr/share/enquadra/brio_control/models/LICENSE
License: MIT
Comment: The complete MIT notice for the distributed YuNet models is installed in usr/share/enquadra/brio_control/models/LICENSE.

Files: usr/share/enquadra/brio_control/models/pphumanseg/*
Copyright: 2021 PaddlePaddle Authors
License: Apache-2.0
Comment: The complete Apache 2.0 notice for the distributed PP-HumanSeg model is installed in usr/share/enquadra/brio_control/models/pphumanseg/LICENSE. The OpenCV Zoo source commit is 47534e27c9851bb1128ccc0102f1145e27f23f98.

Files: usr/share/enquadra/brio_control/camera.py
License: LGPL-3.0-or-later
Comment: The V4L2/UVC ABI reference is cameractrls, LGPL-3.0-or-later, commit 6f388257ac21a0e91b143ad11cb2457036fa2c27; the source attribution is retained in camera.py.
""")

    # Workspace modes and umask must not become writable package payloads.
    executable_paths = {
        Path("usr/bin/enquadra"), Path("usr/bin/brio-control"),
        Path("usr/share/enquadra/setup-virtual-camera"),
    }
    stage.chmod(0o755)
    for path in stage.rglob("*"):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o755 if path.relative_to(stage) in executable_paths else 0o644)


def build(output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="enquadra-deb-") as temporary:
        stage = Path(temporary) / "root"
        _stage(stage)
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "--build", str(stage), str(output)],
            check=True,
        )
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "dist" / f"{PACKAGE}_{VERSION}_all.deb")
    args = parser.parse_args()
    result = build(args.output.resolve())
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

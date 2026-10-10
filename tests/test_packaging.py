from __future__ import annotations

import subprocess
import hashlib
import os
import sys
import stat
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackagingTests(unittest.TestCase):
    def test_build_has_expected_artifact_and_no_runtime_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "enquadra.deb"
            subprocess.run([sys.executable, "scripts/build_deb.py", "--output", str(output)], cwd=ROOT, check=True)
            listing = subprocess.check_output(["dpkg-deb", "--contents", str(output)], text=True)
            control = subprocess.check_output(["dpkg-deb", "--field", str(output)], text=True)
            extracted = Path(temporary) / "extracted"
            subprocess.run(["dpkg-deb", "--extract", str(output), str(extracted)], check=True,
                           stdout=subprocess.DEVNULL)
            copyright_text = extracted.joinpath("usr/share/doc/enquadra/copyright").read_text()
            setup = extracted.joinpath("usr/share/enquadra/setup-virtual-camera")
            background_model = extracted.joinpath("usr/share/enquadra/brio_control/models/pphumanseg/human_segmentation_pphumanseg_2023mar.onnx")
            self.assertEqual(background_model.stat().st_size, 6163938)
            self.assertIn("Apache License", background_model.with_name("LICENSE").read_text())
            subprocess.run(["sh", "-n", str(setup)], check=True)
            self.assertEqual(stat.S_IMODE(setup.stat().st_mode), 0o755)
            canonical = ROOT.joinpath("branding/enquadra.svg").read_bytes()
            self.assertEqual(canonical, extracted.joinpath("usr/share/enquadra/branding/enquadra.svg").read_bytes())
            self.assertEqual(canonical, extracted.joinpath("usr/share/icons/hicolor/scalable/apps/enquadra.svg").read_bytes())
            desktop = extracted.joinpath("usr/share/applications/enquadra.desktop").read_text()
            self.assertIn("Name=Enquadra", desktop)
            self.assertIn("Exec=enquadra", desktop)
            self.assertIn("Icon=enquadra", desktop)
            for name in ("enquadra", "brio-control"):
                launcher = extracted.joinpath("usr/bin", name)
                subprocess.run(["sh", "-n", str(launcher)], check=True)
                self.assertEqual(stat.S_IMODE(launcher.stat().st_mode), 0o755)
            executables = {'usr/bin/enquadra', 'usr/bin/brio-control',
                           'usr/share/enquadra/setup-virtual-camera'}
            for path in extracted.rglob('*'):
                with self.subTest(packaged_path=path.relative_to(extracted)):
                    expected_mode = 0o755 if path.is_dir() or str(path.relative_to(extracted)) in executables else 0o644
                    self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected_mode)
            self.assertEqual(hashlib.sha256(background_model.read_bytes()).hexdigest(),
                             "552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24")
        self.assertIn("./usr/bin/enquadra", listing)
        self.assertIn("./usr/bin/brio-control", listing)
        self.assertIn("./usr/share/enquadra/brio_control/camera.py", listing)
        self.assertIn("./usr/share/enquadra/brio_control/models/face_detection_yunet_2022mar.onnx", listing)
        self.assertIn("./usr/share/applications/enquadra.desktop", listing)
        self.assertIn("./usr/share/enquadra/setup-virtual-camera", listing)
        self.assertNotIn("__pycache__", listing)
        self.assertNotIn("./tests/", listing)
        self.assertNotIn(".runtime", listing)
        self.assertIn("Package: enquadra", control)
        self.assertIn("Version: 0.1.0~alpha1", control)
        self.assertIn("Provides: brio-control", control)
        self.assertIn("Maintainer: Caio Lombello <70986039+caiolombello@users.noreply.github.com>", control)
        self.assertIn("Suggests: v4l2loopback-dkms, ffmpeg", control)
        self.assertNotIn("Recommends: v4l2loopback-dkms", control)
        self.assertIn("License: MIT", copyright_text)
        self.assertIn("License: Apache-2.0", copyright_text)
        self.assertIn("License: LGPL-3.0-or-later", copyright_text)


    def _run_setup(self, directory, *, legacy=False, foreign=False, secure_boot=False, load_fails=False):
        """Run the real preflight against isolated files and fake administrator tools."""
        root = Path(directory)
        bin_dir = root / 'bin'
        bin_dir.mkdir()
        for directory in ('etc/modprobe.d', 'etc/modules-load.d', 'lib/modules/test/build',
                          'sys/class/video4linux/video10', 'sys/module/v4l2loopback/parameters',
                          'sys/devices/virtual/video4linux/video10', 'dev'):
            (root / directory).mkdir(parents=True)
        (root / 'etc/os-release').write_text('ID=ubuntu\n')
        if legacy or foreign:
            name = 'Brio Control' if legacy else 'Other Camera'
            (root / 'dev/video10').symlink_to('/dev/null')
            (root / 'sys/class/video4linux/video10/name').write_text(name)
            (root / 'sys/module/v4l2loopback/parameters/video_nr').write_text('10,-1,-1,-1,-1,-1,-1,-1')
            (root / 'sys/module/v4l2loopback/parameters/exclusive_caps').write_text('Y,N,N,N,N,N,N,N')
        else:
            import shutil
            shutil.rmtree(root / 'sys/module/v4l2loopback')
        if legacy:
            (root / 'etc/modprobe.d/brio-control.conf').write_text(
                'options v4l2loopback devices=1 video_nr=10 card_label="Brio Control" exclusive_caps=1\n')
            (root / 'etc/modules-load.d/brio-control.conf').write_text('v4l2loopback\n')
        if foreign:
            (root / 'etc/modprobe.d/other.conf').write_text('options v4l2loopback devices=2\n')
        commands = {
            'id': 'echo 0', 'uname': 'echo test', 'stat': 'echo 0:644',
            'mokutil': 'echo "SecureBoot ' + ('enabled' if secure_boot else 'disabled') + '"',
            'apt-get': 'echo apt >> "$SETUP_LOG"',
            'v4l2-ctl': 'echo "Driver name: v4l2 loopback"', 'udevadm': ':',
            'install': 'for target do :; done; mkdir -p "$target"',
            'modprobe': ('echo modprobe >> "$SETUP_LOG"; exit 1' if load_fails else
                         'echo modprobe >> "$SETUP_LOG"; ln -sf /dev/null "$SETUP_ROOT/dev/video10"; '
                         'mkdir -p "$SETUP_ROOT/sys/class/video4linux/video10"; '
                         'printf "%s" "$SETUP_LABEL" > "$SETUP_ROOT/sys/class/video4linux/video10/name"'),
        }
        for name, command in commands.items():
            executable = bin_dir / name
            executable.write_text('#!/bin/sh\n' + command + '\n')
            executable.chmod(0o755)
        script = ROOT.joinpath('scripts/setup-virtual-camera').read_text()
        script = script.replace('PATH=/usr/sbin:/usr/bin:/sbin:/bin', f'PATH={bin_dir}:/usr/bin:/bin')
        for prefix in ('/etc/', '/lib/modules/', '/sys/', '/dev/video10'):
            script = script.replace(prefix, str(root) + prefix)
        runner = root / 'setup'
        runner.write_text(script)
        result = subprocess.run(['sh', str(runner)], capture_output=True, text=True,
                                env=dict(os.environ, SETUP_LOG=str(root / 'calls'), SETUP_ROOT=str(root),
                                         SETUP_LABEL='Brio Control' if legacy else 'Enquadra'))
        calls = (root / 'calls').read_text() if (root / 'calls').exists() else ''
        return result, calls

    def test_setup_preserves_legacy_configuration_and_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, calls = self._run_setup(temporary, legacy=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            root = Path(temporary)
            self.assertIn('Brio Control', (root / 'etc/modprobe.d/brio-control.conf').read_text())
            self.assertFalse((root / 'etc/modprobe.d/enquadra.conf').exists())
            self.assertIn('Brio Control', result.stdout)
            self.assertIn('modprobe', calls)

    def test_setup_does_not_touch_foreign_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, calls = self._run_setup(temporary, foreign=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('outra configuração', result.stderr)
            self.assertEqual(calls, '')
            self.assertEqual(Path(temporary, 'etc/modprobe.d/other.conf').read_text(),
                             'options v4l2loopback devices=2\n')

    def test_new_setup_uses_enquadra_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, calls = self._run_setup(temporary)
            self.assertEqual(result.returncode, 0, result.stderr)
            config = Path(temporary, 'etc/modprobe.d/enquadra.conf').read_text()
            self.assertIn('card_label="Enquadra"', config)
            self.assertIn('apt', calls)

    def test_secure_boot_failure_has_guidance_and_no_persistent_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            result, calls = self._run_setup(temporary, secure_boot=True, load_fails=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('MOK', result.stderr)
            self.assertIn('Secure Boot foi preservado', result.stderr)
            self.assertFalse(Path(temporary, 'etc/modprobe.d/enquadra.conf').exists())
            self.assertIn('modprobe', calls)

    def test_launcher_and_local_runner_are_executable(self):
        self.assertTrue(ROOT.joinpath("scripts/run-local").stat().st_mode & 0o111)
        self.assertIn("python3 -m brio_control", ROOT.joinpath("scripts/run-local").read_text())


if __name__ == "__main__":
    unittest.main()

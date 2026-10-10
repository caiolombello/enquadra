import contextlib
import io
import json
import signal
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brio_control.camera import Device
from brio_control import probe


DEVICE = Device("/dev/video-test", "Brio", "046d:0943", "private-serial", 10,
                frozenset({5, 13}))


class FakeCamera:
    def __init__(self, directory, original=0):
        self.current = original
        self.original = original
        self.writes = []
        self.fail_write = False
        self.bad_readback = False
        self.restore_fail = False
        self.directory = Path(directory)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def capabilities(self, selector):
        assert selector == 13
        return dict(length=1, info=3, minimum=0, maximum=1, current=self.current, default=0)

    def read(self, selector):
        assert selector == 13
        return bytes([self.current])

    def write(self, selector, value):
        assert selector == 13
        journal = self.directory / "brio-control/show-mode-probe.json"
        assert journal.exists(), "journal must precede even a failing SET"
        assert journal.stat().st_mode & 0o777 == 0o600
        self.writes.append(value)
        if self.restore_fail and value == self.original:
            raise OSError("restore failed")
        self.current = value
        if self.fail_write:
            self.fail_write = False
            raise OSError("SET transport failed after device accepted")
        if self.bad_readback:
            self.bad_readback = False
            raise OSError("readback failed after SET")
        return value


class FakePreview:
    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0

    def wait(self, timeout=None):
        return self.returncode


@contextlib.contextmanager
def setup_probe(directory, camera, answers=None, devices=None):
    output = io.StringIO()
    error = io.StringIO()
    children = []

    def spawn(*args, **kwargs):
        assert "-vf" not in args[0]
        assert "mjpeg" in args[0]
        assert "1920x1080" in args[0]
        assert "30" in args[0]
        child = FakePreview()
        children.append(child)
        return child

    with patch.dict("os.environ", {"XDG_STATE_HOME": directory}), \
            patch.object(probe, "discover_devices", return_value=devices or [DEVICE]), \
            patch.object(probe, "Camera", return_value=camera), \
            patch.object(probe.subprocess, "Popen", side_effect=spawn), \
            patch.object(probe.select, "select", side_effect=lambda *args: ([probe.sys.stdin], [], [])), \
            patch.object(probe.sys, "stdin", io.StringIO(answers if answers is not None else "pass\n" * 15)), \
            contextlib.redirect_stdout(output), contextlib.redirect_stderr(error):
        yield output, error, children


class ProbeTests(unittest.TestCase):
    def test_no_device_never_writes(self):
        from brio_control import probe
        output = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, \
                patch.dict("os.environ", {"XDG_STATE_HOME": directory}), \
                patch.object(probe, "discover_devices", return_value=[]), \
                patch.object(probe, "Camera") as camera, \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(probe.run(), 1)
            camera.assert_not_called()
            self.assertFalse(json.loads(output.getvalue())["validated"])

    def test_complete_user_observed_run_restores_both_original_values(self):
        for original in (0, 1):
            with self.subTest(original=original), tempfile.TemporaryDirectory() as directory:
                camera = FakeCamera(directory, original)
                with setup_probe(directory, camera) as (output, error, children):
                    self.assertEqual(probe.run(), 0)
                result = json.loads(output.getvalue())
                self.assertTrue(result["validated"])
                self.assertEqual(result["observation_source"], "user")
                self.assertEqual(len([o for o in result["observations"] if o["phase"] == 1]), 10)
                self.assertEqual(camera.current, original)
                self.assertTrue(result["restored"])
                self.assertTrue(all(child.poll() is not None for child in children))
                self.assertFalse((Path(directory) / "brio-control/show-mode-probe.json").exists())
                self.assertNotIn("private-serial", output.getvalue())

    def test_failed_set_still_requires_restoration(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory, 1)
            camera.fail_write = True
            with setup_probe(directory, camera) as (output, _, children):
                self.assertEqual(probe.run(), 1)
            self.assertEqual(camera.writes, [0, 1])
            self.assertEqual(camera.current, 1)
            self.assertTrue(json.loads(output.getvalue())["restored"])

    def test_failed_readback_still_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory, 1)
            camera.bad_readback = True
            with setup_probe(directory, camera) as (output, _, children):
                self.assertEqual(probe.run(), 1)
            self.assertEqual(camera.current, 1)
            self.assertTrue(json.loads(output.getvalue())["restored"])

    def test_restore_failure_keeps_journal_and_reports_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory, 1)
            camera.fail_write = camera.restore_fail = True
            with setup_probe(directory, camera) as (output, error, children):
                self.assertEqual(probe.run(), 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "restore_failed")
            self.assertIn("FALHA NA RESTAURAÇÃO", error.getvalue())
            self.assertTrue((Path(directory) / "brio-control/show-mode-probe.json").exists())

    def test_stop_after_write_restores_and_closes_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory, 1)
            with setup_probe(directory, camera, "pass\nstop\n") as (output, _, children):
                self.assertEqual(probe.run(), 1)
            self.assertEqual(camera.current, 1)
            self.assertEqual(json.loads(output.getvalue())["status"], "interrupted")
            self.assertTrue(all(child.poll() is not None for child in children))

    def test_inconclusive_cycle_never_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory)
            with setup_probe(directory, camera, "pass\n" * 6 + "inconclusive\n" + "pass\n" * 8) as (output, _, children):
                self.assertEqual(probe.run(), 1)
            self.assertFalse(json.loads(output.getvalue())["validated"])

    def test_orphan_wrong_identity_never_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory)
            with setup_probe(directory, camera) as (output, _, children):
                path = probe._directory() / "show-mode-probe.json"
                probe._save(path, dict(identityhash="wrong", path=DEVICE.path, original13=1,
                                       lastintended=0))
                self.assertEqual(probe.run(), 1)
            self.assertEqual(camera.writes, [])
            self.assertTrue(path.exists())
            self.assertFalse(children)

    def test_orphan_restores_without_starting_new_experiment(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory, 1)
            camera.current = 0
            with setup_probe(directory, camera) as (output, _, children):
                path = probe._directory() / "show-mode-probe.json"
                probe._save(path, dict(identityhash=probe._identity(DEVICE), path=DEVICE.path,
                                       original13=1, lastintended=0))
                self.assertEqual(probe.run(), 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "recovery")
            self.assertFalse(result["validated"])
            self.assertEqual(camera.current, 1)
            self.assertFalse(path.exists())
            self.assertFalse(children)

    def test_recovery_conflict_never_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory, 1)
            with setup_probe(directory, camera) as (output, _, children):
                path = probe._directory() / "show-mode-probe.json"
                probe._save(path, dict(identityhash=probe._identity(DEVICE), path=DEVICE.path,
                                       original13=0, lastintended=0))
                self.assertEqual(probe.run(), 1)
            self.assertEqual(camera.writes, [])
            self.assertTrue(path.exists())

    def test_sigterm_after_set_restores_and_resets_handler(self):
        previous = signal.getsignal(signal.SIGTERM)
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory, 1)

            def ready(*args):
                if camera.writes:
                    signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)
                return [probe.sys.stdin], [], []

            with setup_probe(directory, camera) as (output, _, children), \
                    patch.object(probe.select, "select", side_effect=ready):
                self.assertEqual(probe.run(), 1)
            self.assertEqual(camera.current, 1)
            self.assertTrue(json.loads(output.getvalue())["restored"])
            self.assertIs(signal.getsignal(signal.SIGTERM), previous)

    def test_closed_preview_aborts_and_restores(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory, 1)
            with setup_probe(directory, camera) as (output, _, children):
                def ready(*args):
                    if camera.writes:
                        children[-1].returncode = 0
                        return [], [], []
                    return [probe.sys.stdin], [], []
                with patch.object(probe.select, "select", side_effect=ready):
                    self.assertEqual(probe.run(), 1)
            self.assertEqual(camera.current, 1)
            self.assertTrue(json.loads(output.getvalue())["restored"])

    def test_bad_capabilities_reject_before_preview_and_write(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory)
            with setup_probe(directory, camera) as (output, _, children), \
                    patch.object(camera, "capabilities", return_value=dict(
                        length=2, info=3, minimum=0, maximum=1, current=0)):
                self.assertEqual(probe.run(), 1)
            self.assertEqual(camera.writes, [])
            self.assertFalse(children)

    def test_disconnection_prevents_restoration_to_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            camera = FakeCamera(directory, 1)
            camera.fail_write = True
            replacement = Device(DEVICE.path, DEVICE.name, DEVICE.usb_id, "other",
                                 DEVICE.xu_unit, DEVICE.xu_selectors)
            with setup_probe(directory, camera) as (output, _, children), \
                    patch.object(probe, "discover_devices", side_effect=[
                        [DEVICE], [DEVICE], [replacement]]):
                self.assertEqual(probe.run(), 1)
            self.assertEqual(camera.writes, [0])
            self.assertEqual(json.loads(output.getvalue())["status"], "restore_failed")

    def test_fov_not_touched_and_negative_or_phase_zero_failure_invalidates(self):
        for failed_answer in (1, 14):
            with self.subTest(failed_answer=failed_answer), tempfile.TemporaryDirectory() as directory:
                camera = FakeCamera(directory)
                answers = ["pass"] * 15
                answers[failed_answer] = "fail"
                with setup_probe(directory, camera, "\n".join(answers) + "\n") as (output, _, children):
                    self.assertEqual(probe.run(), 1)
                self.assertFalse(json.loads(output.getvalue())["validated"])

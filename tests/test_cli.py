import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from brio_control import __main__ as cli
from brio_control.camera import Device
from brio_control.storage import identity_key


DEVICE = Device('/dev/video-test', 'Brio 500', '046d:0943', 'private-serial', 10, frozenset({5, 13}))


class CliTests(unittest.TestCase):
    def test_version_reports_product_without_loading_gui(self):
        output = io.StringIO()
        with patch.object(sys, 'argv', ['enquadra', '--version']), contextlib.redirect_stdout(output), \
                self.assertRaises(SystemExit) as result:
            cli.main()
        self.assertEqual(result.exception.code, 0)
        self.assertEqual(output.getvalue().strip(), 'Enquadra 0.1.0-alpha.1')

    def test_setup_missing_polkit_has_actionable_message_and_no_execution(self):
        with patch.object(cli.os, 'geteuid', return_value=1000), \
                patch.object(cli.shutil, 'which', return_value=None), \
                patch.object(cli.subprocess, 'run') as run, \
                self.assertRaisesRegex(RuntimeError, 'Polkit'):
            cli.setup_virtual_camera()
        run.assert_not_called()

    def test_model_diagnostic_checks_background_without_importing_processing_dependencies(self):
        with patch.dict(sys.modules, {'cv2': None, 'numpy': None}):
            entries = {item['name']: item for item in cli._models_diagnostic()}
        self.assertTrue(entries['pphumanseg/human_segmentation_pphumanseg_2023mar.onnx']['hash_valid'])
        self.assertTrue(entries['face_detection_yunet_2023mar.onnx']['hash_valid'])

    def test_start_without_overrides_preserves_worker_preferences(self):
        client = Mock()
        client.start.return_value = {'running': True}
        module = SimpleNamespace(Client=lambda: client, ensure_worker=Mock())
        with patch.dict(sys.modules, {'brio_control.client': module}), \
                patch.object(sys, 'argv', ['brio-control', 'start']), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(), 0)
        client.start.assert_called_once_with({})

    def test_doctor_reports_observed_rotation_even_when_device_read_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'brio-control'
            directory.mkdir(mode=0o700)
            record = directory / 'hardware-observations.json'
            record.write_text(json.dumps(dict(schema=1, identity=identity_key(DEVICE), show_mode=dict(
                direct_preview_confirmed=True, app_preview_confirmed=True, observation_source='user'))))
            record.chmod(0o600)
            output = io.StringIO()
            with patch.dict('os.environ', {'XDG_STATE_HOME': temporary}), \
                    patch('brio_control.camera.discover_devices', return_value=[DEVICE]), \
                    patch('brio_control.camera.Camera', side_effect=OSError('disconnected private-serial')), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(cli.doctor(), 0)
            report = json.loads(output.getvalue())
            self.assertTrue(report['show_mode']['rotation_observed'])
            self.assertFalse(report['show_mode']['control_available'])
            self.assertTrue(report['devices'][0]['show_mode']['rotation_observed'])
            self.assertIn('error', report['devices'][0])
            self.assertNotIn('private-serial', output.getvalue())

    def test_explicit_start_options_override_saved_preferences(self):
        client = Mock()
        client.start.return_value = {'running': True}
        module = SimpleNamespace(Client=lambda: client, ensure_worker=Mock())
        with patch.dict(sys.modules, {'brio_control.client': module}), \
                patch.object(sys, 'argv', ['brio-control', 'start', '--preview-only', '--no-autoframe', '--size', '1080']), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(), 0)
        client.start.assert_called_once_with(dict(output=None, autoframe=False, width=1920, height=1080))

    def test_preview_and_virtual_output_are_mutually_exclusive(self):
        with patch.object(sys, 'argv', ['brio-control', 'start', '--preview-only', '--output', '/dev/video10']), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            cli.main()
        self.assertEqual(error.exception.code, 2)

    def test_setup_uses_native_polkit_for_users_and_shell_directly_for_root(self):
        for installed in (False, True):
            for uid in (0, 1000):
                with self.subTest(installed=installed, uid=uid), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    module = root / 'brio_control/__main__.py'
                    module.parent.mkdir()
                    module.touch()
                    script = root / ('setup-virtual-camera' if installed else 'scripts/setup-virtual-camera')
                    script.parent.mkdir(exist_ok=True)
                    script.touch()
                    with patch.object(cli.shutil, 'which', return_value='/usr/bin/pkexec'), patch.object(cli, '__file__', str(module)), patch.object(cli.os, 'geteuid', return_value=uid), \
                            patch.object(cli.subprocess, 'run', return_value=SimpleNamespace(returncode=126)) as run:
                        self.assertEqual(cli.setup_virtual_camera(), 126)
                    command = ['/bin/sh', str(script)]
                    if uid: command.insert(0, 'pkexec')
                    run.assert_called_once_with(command, check=False)

    def test_untrusted_or_different_device_observation_never_confirms_rotation(self):
        for wrong_identity, mode in ((True, 0o600), (False, 0o644)):
            with self.subTest(wrong_identity=wrong_identity, mode=mode), tempfile.TemporaryDirectory() as temporary:
                directory = Path(temporary) / 'brio-control'
                directory.mkdir(mode=0o700)
                record = directory / 'hardware-observations.json'
                record.write_text(json.dumps(dict(schema=1, identity='other' if wrong_identity else identity_key(DEVICE),
                    show_mode=dict(direct_preview_confirmed=True, app_preview_confirmed=True, observation_source='user'))))
                record.chmod(mode)
                output = io.StringIO()
                with patch.dict('os.environ', {'XDG_STATE_HOME': temporary}), \
                        patch('brio_control.camera.discover_devices', return_value=[DEVICE]), \
                        patch('brio_control.camera.Camera', side_effect=OSError('disconnected')), \
                        contextlib.redirect_stdout(output):
                    self.assertEqual(cli.doctor(), 0)
                self.assertFalse(json.loads(output.getvalue())['show_mode']['rotation_observed'])

    def test_doctor_keeps_other_reads_when_fov_read_fails_and_never_opens_writable(self):
        camera = Mock()
        camera.__enter__ = Mock(return_value=camera)
        camera.__exit__ = Mock(return_value=False)
        camera.enumerate_controls.return_value = [{'id': 'brightness', 'value': 5}]
        camera.capabilities.side_effect = OSError('unavailable')
        output = io.StringIO()
        with patch('brio_control.camera.discover_devices', return_value=[DEVICE]), \
                patch('brio_control.camera.Camera', return_value=camera) as factory, \
                contextlib.redirect_stdout(output):
            self.assertEqual(cli.doctor(), 0)
        factory.assert_called_once_with(DEVICE)
        entry = json.loads(output.getvalue())['devices'][0]
        self.assertEqual(entry['controls'], [{'id': 'brightness', 'value': 5}])
        self.assertIn('fov_error', entry)
        camera.write.assert_not_called()
        camera.set_control.assert_not_called()

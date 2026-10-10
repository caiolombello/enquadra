import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from brio_control.service import Worker
from brio_control.storage import identity_key


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.discovery = patch('brio_control.service.discover_devices', return_value=[])
        self.discovery.start()
        self.addCleanup(self.discovery.stop)
        root = self.directory.name
        self.worker = Worker(root + '/config', root + '/state', root + '/runtime')
        self.addCleanup(self.worker.close)

    def test_background_defaults_old_profiles_and_failed_updates_are_atomic(self):
        self.worker.processing({'zoom': 2.0, 'mirror': True})
        initial = self.worker.processing_settings.copy()
        for options in ({'background_mode': 'image'}, {'background_blur': True},
                {'background_blur': 10 ** 400}, {'background_mode': 'other'}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.worker.processing(options)
            self.assertEqual(self.worker.processing_settings, initial)
        device = SimpleNamespace(usb_id='046d:0943', identity='test')
        self.worker.camera = MagicMock(device=device)
        self.worker.profiles['Legacy'] = dict(schema=1, identity=identity_key(device),
            controls={}, processing={'zoom': 1.5})
        self.worker.processing_settings.update(background_mode='blur', background_blur=25)
        self.worker.load_profile('Legacy')
        self.assertEqual(self.worker.processing_settings['background_mode'], 'off')
        self.assertEqual(self.worker.processing_settings['background_blur'], 12.0)
        self.assertIsNone(self.worker.processing_settings['background_image'])
        self.assertEqual(self.worker.processing_settings['zoom'], 1.5)

    def test_idle_processing_reuses_validator_and_stream_shares_it(self):
        with patch('brio_control.framing.FrameProcessor') as processor:
            self.worker.processing({'background_mode': 'blur'})
            self.worker.processing({'background_blur': 20})
            processor.assert_called_once()
            with patch('brio_control.stream.VideoEngine') as engine:
                self.worker.start({'synthetic': True})
                self.assertIs(engine.call_args.kwargs['processor'], processor.return_value)
        self.worker.engine = None
        self.worker._processor = None

    def test_matching_user_observation_reports_rotation_without_enabling_control(self):
        device = SimpleNamespace(usb_id='046d:0943', identity='test', path='/dev/video2', name='Brio 500')
        self.worker.camera = MagicMock(device=device)
        self.worker.camera.enumerate_controls.return_value = []
        from brio_control.camera import CameraError
        self.worker.camera.capabilities.side_effect = CameraError('unsupported')
        with patch('brio_control.service.discover_devices', return_value=[device]):
            self.assertFalse(self.worker.state()['show_mode']['rotation_observed'])
            self.worker.state_dir.mkdir(parents=True, exist_ok=True)
            (self.worker.state_dir / 'hardware-observations.json').write_text(json.dumps(dict(
                schema=1, identity=identity_key(device), show_mode=dict(
                    direct_preview_confirmed=True, app_preview_confirmed=True, observation_source='user'))))
            status = self.worker.state()['show_mode']
        self.assertTrue(status['rotation_observed'])
        self.assertFalse(status['validated'])
        self.assertFalse(status['control_available'])

    def test_invalid_or_other_camera_observations_do_not_report_rotation(self):
        device = SimpleNamespace(usb_id='046d:0943', identity='test', path='/dev/video2', name='Brio 500')
        self.worker.camera = MagicMock(device=device)
        self.worker.camera.enumerate_controls.return_value = []
        from brio_control.camera import CameraError
        self.worker.camera.capabilities.side_effect = CameraError('unsupported')
        valid = dict(schema=1, identity=identity_key(device), show_mode=dict(
            direct_preview_confirmed=True, app_preview_confirmed=True, observation_source='user'))
        invalid = [None, [], valid | {'schema': 2}, valid | {'schema': True},
            valid | {'identity': 'another'}, valid | {'show_mode': None}]
        for key, value in [('direct_preview_confirmed', False), ('direct_preview_confirmed', 1),
                ('app_preview_confirmed', False), ('app_preview_confirmed', 1), ('observation_source', 'probe')]:
            invalid.append(valid | {'show_mode': valid['show_mode'] | {key: value}})
        self.worker.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.worker.state_dir / 'hardware-observations.json'
        with patch('brio_control.service.discover_devices', return_value=[device]):
            for value in invalid:
                with self.subTest(value=value):
                    path.write_text(json.dumps(value))
                    status = self.worker.state()['show_mode']
                    self.assertFalse(status['rotation_observed'])
                    self.assertFalse(status['validated'])
                    self.assertFalse(status['control_available'])
            path.write_text('{invalid json')
            self.assertFalse(self.worker.state()['show_mode']['rotation_observed'])
        path.write_text(json.dumps(valid))
        self.worker.camera = None
        self.assertFalse(self.worker.state()['show_mode']['rotation_observed'])

    def test_idle_state_never_captures_or_sets_candidate(self):
        state = self.worker.state()
        self.assertFalse(state['running'])
        self.assertFalse(state['show_mode']['validated'])
        self.assertFalse(state['show_mode']['rotation_observed'])
        self.assertFalse(state['show_mode']['control_available'])
        self.assertEqual(state['controls'], [])
        self.assertIsNone(self.worker.engine)

    def test_state_reports_actual_legacy_virtual_label_without_changing_preferences(self):
        before = self.worker.preference_settings.copy()
        with patch('brio_control.stream.virtual_camera_name', return_value='Brio Control') as label:
            self.assertEqual(self.worker.state()['virtual_camera_name'], 'Brio Control')
            label.assert_called_once_with('/dev/video10')
        self.assertEqual(self.worker.preference_settings, before)
        with patch('brio_control.stream.virtual_camera_name', return_value=None):
            self.assertEqual(self.worker.state()['virtual_camera_name'], 'Enquadra')

    def test_unknown_options_and_resolutions_are_rejected(self):
        for options in ({'width': 65, 'height': 4}, {'unexpected': True}, {'synthetic': 1}):
            with self.assertRaises(ValueError): self.worker.start(options)
        with self.assertRaises(ValueError): self.worker.processing({'unexpected': True})

    def test_dead_engine_is_disposed_before_restarting(self):
        engine = MagicMock(running=False, error='failed')
        self.worker.engine = engine
        with self.assertRaises(ValueError): self.worker.start({'width': 65, 'height': 4})
        engine.stop.assert_called_once()
        self.assertIsNone(self.worker.engine)

    def test_reconnect_same_identity_reopens_control_fd_while_stream_runs(self):
        device = SimpleNamespace(usb_id='046d:0943', identity='test', path='/dev/video2')
        old = MagicMock(device=device)
        from brio_control.camera import CameraError
        old.read_control.side_effect = CameraError('disconnected old fd')
        self.worker.camera = old
        self.worker.engine = MagicMock(running=True)
        self.worker._stream_identity = identity_key(device)
        with patch('brio_control.service.discover_devices', return_value=[device]), patch('brio_control.service.Camera') as cls:
            new = cls.return_value.__enter__.return_value
            self.worker.refresh_devices()
            old.close.assert_called_once()
            self.assertIs(self.worker.camera, new)

    def test_native_framing_controls_locked_during_auto(self):
        device = SimpleNamespace(usb_id='046d:0943', identity='test', xu_selectors=[])
        self.worker.camera = MagicMock(device=device)
        self.worker.camera.enumerate_controls.return_value = [dict(id='0x9a090d', writable=True)]
        self.worker.camera.capabilities.side_effect = ValueError('unsupported')
        from brio_control.camera import CameraError
        self.worker.camera.capabilities.side_effect = CameraError('unsupported')
        self.worker.engine = MagicMock(running=True)
        self.worker.processing_settings['autoframe'] = True
        self.assertFalse(self.worker._controls()[0]['writable'])
        with self.assertRaises(CameraError): self.worker.set_control('0x9a090d', 200)

    def test_stop_restores_temporary_settings_and_preserves_user_change(self):
        device = SimpleNamespace(usb_id='046d:0943', identity='test')
        self.worker.camera = MagicMock(device=device)
        values = {'fov': 65}
        self.worker._read_raw = values.get
        self.worker._write_raw = values.__setitem__
        self.worker.temporary.before_write(device, 'fov', 65, 90)
        values['fov'] = 90
        self.worker.stop()
        self.assertEqual(values['fov'], 65)

    def test_load_profile_auto_controls_precede_manual_and_other_camera_rejected(self):
        device = SimpleNamespace(usb_id='046d:0943', identity='test')
        self.worker.camera = MagicMock(device=device)
        controls = {'0x9a090a': 40, '0x9a090c': 0, 'fov': 90}
        self.worker.profiles['test'] = dict(schema=1, identity=identity_key(device),
            controls=controls, processing={})
        self.worker.set_control = MagicMock()
        self.worker.processing = MagicMock()
        self.worker.load_profile('test')
        self.assertEqual(self.worker.set_control.call_args_list[0].args, ('0x9a090c', 0))
        self.worker.profiles['test']['identity'] = 'another'
        with self.assertRaises(ValueError): self.worker.load_profile('test')

    def test_unavailable_profile_image_preserves_active_session_before_camera_writes(self):
        from pathlib import Path
        from brio_control.framing import FrameProcessor
        device = SimpleNamespace(usb_id='046d:0943', identity='test')
        self.worker.camera = MagicMock(device=device)
        self.worker.engine = MagicMock(running=True)
        self.worker.processing_settings.update(autoframe=True, zoom=2.0, mirror=True)
        self.worker.profile = 'Atual'
        self.worker._persist_camera()
        previous_processing = self.worker.processing_settings.copy()
        previous_preferences = self.worker.preference_settings.copy()
        previous_settings = self.worker.settings_path.read_bytes()
        missing = Path(self.directory.name) / 'deleted.png'
        malformed = Path(self.directory.name) / 'malformed.png'
        malformed.write_bytes(b'not a valid image')
        self.worker.set_control = MagicMock()
        self.worker.processing = MagicMock()
        self.worker._normalize = MagicMock()
        for path in (missing, malformed):
            with self.subTest(path=path):
                self.worker.profiles['Novo'] = dict(schema=1, identity=identity_key(device),
                    controls={'fov': 65}, processing=dict(autoframe=True,
                        background_mode='image', background_image=str(path)))
                # Inject only face detection to isolate image validation from YuNet.
                with patch('brio_control.framing.FrameProcessor',
                        side_effect=lambda: FrameProcessor(detector=lambda frame: [])):
                    with self.assertRaises(ValueError):
                        self.worker.load_profile('Novo')
                self.worker.set_control.assert_not_called()
                self.worker.processing.assert_not_called()
                self.worker._normalize.assert_not_called()
                self.worker.engine.configure.assert_not_called()
                self.worker.camera.set_control.assert_not_called()
                self.assertEqual(self.worker.processing_settings, previous_processing)
                self.assertEqual(self.worker.preference_settings, previous_preferences)
                self.assertEqual(self.worker.settings_path.read_bytes(), previous_settings)
                self.assertEqual(self.worker.profile, 'Atual')
                self.assertFalse(self.worker._restoring)

    def test_loading_manual_profile_disables_tracking_before_native_controls(self):
        device = SimpleNamespace(usb_id='046d:0943', identity='test')
        self.worker.camera = MagicMock(device=device)
        self.worker.processing_settings['autoframe'] = True
        self.worker.profiles['manual'] = dict(schema=1, identity=identity_key(device),
            controls={'fov': 78}, processing={'autoframe': False, 'zoom': 2.0})
        events = []
        def processing(options):
            self.worker.processing_settings.update(options)
            events.append(('processing', options))
        def control(key, value):
            self.assertFalse(self.worker.processing_settings['autoframe'])
            events.append(('control', key, value))
        self.worker.processing = processing
        self.worker.set_control = control
        self.worker.load_profile('manual')
        self.assertEqual(events[0], ('processing', {'autoframe': False}))
        self.assertEqual(events[1], ('control', 'fov', 78))

    def test_preferences_restart_and_invalid_updates_are_atomic(self):
        expected = dict(preview_only=False, output='/dev/video12', size='1080', last_profile='Reuniões')
        self.worker.preferences(expected)
        root = self.directory.name
        restarted = Worker(root + '/config', root + '/state', root + '/runtime')
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.state()['preferences'], expected)
        self.assertIsNone(restarted.engine)
        original = restarted.settings_path.read_text()
        for value in ({'preview_only': 1}, {'output': '/tmp/video'}, {'size': 720},
                {'size': None}, {'last_profile': []}, {'unknown': True}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                restarted.preferences(value)
            self.assertEqual(restarted.settings_path.read_text(), original)
            self.assertEqual(restarted.preference_settings, expected)

    def test_corrupt_configuration_does_not_prevent_worker_start(self):
        for filename in ('profiles.json', 'settings.json'):
            (self.worker.config_dir / filename).write_text('{invalid')
        self.worker.state_dir.mkdir(parents=True, exist_ok=True)
        (self.worker.state_dir / 'temporary-controls.json').write_text('{invalid')
        root = self.directory.name
        restarted = Worker(root + '/config', root + '/state', root + '/runtime')
        self.addCleanup(restarted.close)
        self.assertEqual(restarted.profiles, {})
        self.assertEqual(restarted.state()['preferences']['size'], '720')
        self.assertIsNone(restarted.engine)
        self.assertIn('inválid', restarted.warning)

    def test_invalid_settings_types_and_identity_are_ignored(self):
        root = self.directory.name
        invalid = [[], {'schema': True}, {'schema': 1, 'cameras': []},
            {'schema': 1, 'cameras': {'bad': {'processing': {}}}},
            {'schema': 1, 'cameras': {'a' * 64: {'processing': {'zoom': '2'}}}},
            {'schema': 1, 'cameras': {'a' * 64: {'processing': {'zoom': 10 ** 400}}}},
            {'schema': 1, 'cameras': {'a' * 64: {'processing': {}, 'profile': []}}}]
        for value in invalid:
            with self.subTest(value=value):
                self.worker.settings_path.write_text(json.dumps(value))
                restarted = Worker(root + '/config', root + '/state', root + '/runtime')
                self.assertIsNone(restarted.engine)
                self.assertEqual(restarted.settings['cameras'], {})
                restarted.close()

    def test_processing_rejects_coerced_types_and_nonfinite_values(self):
        for options in ({'zoom': '2'}, {'zoom': True}, {'pan_x': float('nan')},
                {'pan_y': float('inf')}, {'mirror': 1}, {'autoframe': None}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.worker.processing(options)
        self.assertFalse(self.worker.settings_path.exists())

    def test_restart_restores_profile_and_latest_processing_only_for_same_identity(self):
        from brio_control.camera import CameraError
        root = self.directory.name
        device = SimpleNamespace(usb_id='046d:0943', identity='test', path='/dev/video2', name='Brio 500')
        camera = MagicMock(device=device)
        camera.enumerate_controls.return_value = [dict(id='0x980900', value=130, writable=True)]
        camera.capabilities.side_effect = CameraError('unsupported')
        self.worker.camera = camera
        self.worker.processing({'zoom': 1.5})
        self.worker.save_profile('Reuniões')
        self.worker.processing({'zoom': 2.0, 'mirror': True})
        before = self.worker.settings_path.read_text()
        with patch('brio_control.service.discover_devices', return_value=[device]), \
                patch('brio_control.service.Camera') as cls, patch('brio_control.stream.VideoEngine') as engine:
            restored_camera = cls.return_value.__enter__.return_value
            restored_camera.device = device
            restored_camera.enumerate_controls.return_value = camera.enumerate_controls.return_value
            restored_camera.capabilities.side_effect = CameraError('unsupported')
            restarted = Worker(root + '/config', root + '/state', root + '/runtime')
            self.assertEqual(restarted.profile, 'Reuniões')
            self.assertEqual(restarted.processing_settings['zoom'], 2.0)
            self.assertTrue(restarted.processing_settings['mirror'])
            restored_camera.set_control.assert_called_once_with(0x980900, 130)
            engine.assert_not_called()
            restarted.state()
            restored_camera.set_control.assert_called_once()
            self.assertEqual(restarted.settings_path.read_text(), before)
            restarted.close()
        other = SimpleNamespace(**(vars(device) | {'identity': 'other'}))
        with patch('brio_control.service.discover_devices', return_value=[other]), patch('brio_control.service.Camera') as cls:
            cls.return_value.__enter__.return_value.device = other
            restarted = Worker(root + '/config', root + '/state', root + '/runtime')
            self.assertIsNone(restarted.profile)
            self.assertEqual(restarted.processing_settings['zoom'], 1.0)
            cls.return_value.__enter__.return_value.set_control.assert_not_called()
            restarted.close()

    def test_save_profile_uses_same_camera_original_framing_and_omits_adaptive_gain(self):
        from brio_control.camera import CameraError
        device = SimpleNamespace(usb_id='046d:0943', identity='test', xu_selectors=[])
        self.worker.camera = MagicMock(device=device)
        self.worker.camera.capabilities.side_effect = CameraError('unsupported')
        self.worker.camera.enumerate_controls.return_value = [
            dict(id='0x9a090d', value=100, writable=True),
            dict(id='0x9a0901', value=3, writable=True),
            dict(id='0x980913', value=8, writable=True)]
        self.worker.engine = MagicMock(running=True)
        self.worker.processing_settings['autoframe'] = True
        self.worker.temporary.before_write(device, '0x9a090d', 180, 100)
        self.worker.save_profile('Auto')
        self.assertEqual(self.worker.profiles['Auto']['controls'], {'0x9a090d': 180, '0x9a0901': 3})
        self.worker.temporary.data['identity'] = 'b' * 64
        self.worker.save_profile('Other journal')
        self.assertNotIn('0x9a090d', self.worker.profiles['Other journal']['controls'])

    def test_manual_gain_and_legacy_partial_profile_are_supported(self):
        from brio_control.camera import CameraError
        device = SimpleNamespace(usb_id='046d:0943', identity='test', xu_selectors=[])
        self.worker.camera = MagicMock(device=device)
        self.worker.camera.capabilities.side_effect = CameraError('unsupported')
        self.worker.camera.enumerate_controls.return_value = [dict(id='0x9a0901', value=1, writable=True),
            dict(id='0x980913', value=8, writable=True)]
        self.worker.save_profile('Manual')
        self.assertEqual(self.worker.profiles['Manual']['controls']['0x980913'], 8)
        self.worker.profiles['Legacy'] = dict(schema=1, identity=identity_key(device), controls={}, processing={})
        self.worker.load_profile('Legacy')
        self.assertEqual(self.worker.profile, 'Legacy')
        self.assertFalse(self.worker.processing_settings['autoframe'])

    def test_start_uses_persisted_output_and_size_but_does_not_autostart(self):
        self.worker.preferences({'preview_only': False, 'output': '/dev/video12', 'size': '1080'})
        self.assertIsNone(self.worker.engine)
        with patch('brio_control.stream.VideoEngine') as cls:
            self.worker.start({'synthetic': True})
            self.assertEqual(cls.call_args.args[1:3], (1920, 1080))
            self.assertEqual(cls.return_value.start.call_args.args[1], '/dev/video12')
            self.worker.stop()
            self.worker.start({'synthetic': True, 'output': None, 'width': 1280, 'height': 720})
            self.assertEqual(cls.call_args.args[1:3], (1280, 720))
            self.assertIsNone(cls.return_value.start.call_args.args[1])

    def test_restored_tracking_is_validated_and_missing_model_does_not_start_capture(self):
        root = self.directory.name
        device = SimpleNamespace(usb_id='046d:0943', identity='test', path='/dev/video2')
        key = identity_key(device)
        self.worker.settings_path.write_text(json.dumps(dict(schema=1, cameras={key: dict(
            processing={'autoframe': True}, profile=None)})))
        with patch('brio_control.service.discover_devices', return_value=[device]), \
                patch('brio_control.service.Camera') as cls, patch('brio_control.framing.FrameProcessor') as processor:
            cls.return_value.__enter__.return_value.device = device
            processor.return_value.configure.side_effect = ValueError('modelo YuNet ausente')
            restarted = Worker(root + '/config', root + '/state', root + '/runtime')
            processor.return_value.configure.assert_called_once()
            processor.return_value.close.assert_called_once()
            self.assertTrue(restarted.processing_settings['autoframe'])
            self.assertIsNone(restarted.engine)
            self.assertIn('modelo YuNet ausente', restarted.warning)
            restarted.close()

    def test_restart_missing_image_retains_effect_and_blocks_capture_until_resolved(self):
        from pathlib import Path
        device = SimpleNamespace(usb_id='046d:0943', identity='test', path='/dev/video2')
        key = identity_key(device)
        missing = str(Path(self.directory.name) / 'deleted.png')
        root = self.directory.name
        for via_profile in (False, True):
            with self.subTest(via_profile=via_profile):
                effect = dict(background_mode='image', background_image=missing, zoom=1.5)
                if via_profile:
                    self.worker.profiles_path.write_text(json.dumps({'Reunião': dict(
                        schema=1, identity=key, controls={}, processing=effect)}))
                    saved = dict(profile='Reunião', processing={'pan_x': 0.3})
                else:
                    self.worker.profiles_path.write_text('{}')
                    saved = dict(profile=None, processing=effect | {'pan_x': 0.3})
                self.worker.settings_path.write_text(json.dumps(dict(schema=1, cameras={key: saved})))
                previous = self.worker.settings_path.read_bytes()
                with patch('brio_control.service.discover_devices', return_value=[device]), \
                        patch('brio_control.service.Camera') as camera:
                    camera.return_value.__enter__.return_value.device = device
                    restarted = Worker(root + '/config', root + '/state', root + '/runtime')
                self.addCleanup(restarted.close)
                self.assertEqual(restarted.processing_settings['background_mode'], 'image')
                self.assertEqual(restarted.processing_settings['background_image'], missing)
                self.assertEqual(restarted.processing_settings['zoom'], 1.5)
                self.assertEqual(restarted.processing_settings['pan_x'], 0.3)
                self.assertIn('restaurar', restarted.warning)
                self.assertEqual(restarted.settings_path.read_bytes(), previous)
                with patch('brio_control.stream.VideoEngine.start') as capture:
                    with self.assertRaises(ValueError):
                        restarted.start({'synthetic': True})
                    capture.assert_not_called()
                self.assertIsNone(restarted.engine)
                restarted.processing({'background_mode': 'off'})
                self.assertIsNone(restarted.warning)
                with patch('brio_control.stream.VideoEngine') as engine:
                    restarted.start({'synthetic': True})
                    engine.return_value.start.assert_called_once()
                    restarted.stop()
                restarted.close()

    def test_failed_blur_model_restore_retains_blur_intention(self):
        device = SimpleNamespace(usb_id='046d:0943', identity='test')
        self.worker.settings['cameras'][identity_key(device)] = dict(
            profile=None, processing={'background_mode': 'blur', 'background_blur': 20})
        with patch('brio_control.background.BackgroundProcessor._create_segmenter',
                side_effect=ValueError('modelo local ausente')):
            self.worker._restore_settings(device)
        self.assertEqual(self.worker.processing_settings['background_mode'], 'blur')
        self.assertEqual(self.worker.processing_settings['background_blur'], 20)
        self.assertIn('modelo local ausente', self.worker.warning)
        self.assertIsNone(self.worker.engine)
        self.assertFalse(self.worker.settings_path.exists())

    def test_unchanged_preferences_and_processing_do_not_rewrite_settings(self):
        device = SimpleNamespace(usb_id='046d:0943', identity='test')
        self.worker.camera = MagicMock(device=device)
        self.worker.processing({'zoom': 2.0})
        self.worker.preferences({'size': '1080'})
        with patch('brio_control.service.atomic_json') as write:
            self.worker.processing({'zoom': 2.0})
            self.worker.preferences({'size': '1080'})
            write.assert_not_called()

    def test_profile_name_cannot_overwrite_another_camera(self):
        first = SimpleNamespace(usb_id='046d:0943', identity='first')
        second = SimpleNamespace(usb_id='046d:0943', identity='second')
        self.worker.camera = MagicMock(device=first)
        self.worker._controls = MagicMock(return_value=[])
        self.worker.save_profile('Reuniões')
        before = self.worker.profiles_path.read_text()
        settings_before = self.worker.settings_path.read_text()
        self.worker.camera = MagicMock(device=second)
        self.worker._controls.reset_mock()
        with self.assertRaisesRegex(ValueError, 'outra câmera'):
            self.worker.save_profile(' Reuniões ')
        self.worker._controls.assert_not_called()
        self.assertEqual(self.worker.profiles_path.read_text(), before)
        self.assertEqual(self.worker.settings_path.read_text(), settings_before)
        self.assertEqual(self.worker.profiles['Reuniões']['identity'], identity_key(first))
        self.worker.camera = MagicMock(device=first)
        self.worker.save_profile('Reuniões')
        self.assertEqual(self.worker.profiles['Reuniões']['identity'], identity_key(first))

    def test_stop_preserves_warning_produced_during_engine_stop(self):
        for with_camera in (False, True):
            with self.subTest(with_camera=with_camera):
                self.worker.warning = 'aviso anterior'
                engine = SimpleNamespace(warning=None)
                def stop():
                    engine.warning = 'falha ao enviar quadro preto'
                engine.stop = stop
                self.worker.engine = engine
                self.worker.camera = MagicMock() if with_camera else None
                with patch.object(self.worker.temporary, 'restore', return_value=['falha na restauração']) as restore:
                    self.worker.stop()
                    self.assertIsNone(self.worker.engine)
                    self.assertIn('aviso anterior', self.worker.warning)
                    self.assertIn('falha ao enviar quadro preto', self.worker.warning)
                    if with_camera:
                        self.assertIn('falha na restauração', self.worker.warning)
                        restore.assert_called_once()
                    else:
                        restore.assert_not_called()
        self.worker.camera = None

    def test_reaping_failed_engine_restores_journal_without_state_polling(self):
        device = SimpleNamespace(usb_id='046d:0943', identity='test')
        self.worker.camera = MagicMock(device=device)
        values = {'fov': 90}
        self.worker._read_raw = values.get
        self.worker._write_raw = values.__setitem__
        self.worker.temporary.before_write(device, 'fov', 65, 90)
        engine = SimpleNamespace(running=False, error='falha fatal', warning=None, stop=MagicMock())
        self.worker.engine = engine
        with patch.object(self.worker, 'state', side_effect=AssertionError('unexpected polling')), \
                patch('brio_control.service.discover_devices') as discovery:
            self.assertTrue(self.worker.reap_failed_engine())
            self.assertTrue(self.worker.reap_failed_engine())
            discovery.assert_not_called()
        self.assertEqual(values['fov'], 65)
        self.assertFalse(self.worker.temporary.path.exists())
        self.assertIsNone(self.worker.engine)
        self.assertEqual(self.worker.error, 'falha fatal')
        engine.stop.assert_called_once()

    def test_select_device_changes_controls_target_and_filters_profiles_by_identity(self):
        from brio_control.camera import CameraError
        first = SimpleNamespace(usb_id='046d:0943', identity='first', path='/dev/video2', name='First')
        second = SimpleNamespace(usb_id='046d:0943', identity='second', path='/dev/video4', name='Second')
        self.worker.devices = [first, second]
        original_camera = MagicMock(device=first)
        self.worker.camera = original_camera
        for name, device in [('First profile', first), ('Second profile', second)]:
            self.worker.profiles[name] = dict(schema=1, identity=identity_key(device), controls={}, processing={})
        with patch('brio_control.service.discover_devices', return_value=[first, second]), \
                patch('brio_control.service.Camera') as cls:
            new_camera = cls.return_value.__enter__.return_value
            new_camera.device = second
            new_camera.enumerate_controls.return_value = [dict(id='0x980900', value=140, writable=True)]
            new_camera.capabilities.side_effect = CameraError('unsupported')
            self.assertEqual(self.worker.state()['profiles'], ['First profile'])
            self.worker.select_device(second.path)
            self.worker.set_control('0x980900', 150)
            new_camera.set_control.assert_called_once_with(0x980900, 150)
            original_camera.set_control.assert_not_called()
            original_camera.close.assert_called_once()
            state = self.worker.state()
            self.assertEqual(state['selected_device'], second.path)
            self.assertEqual(state['profiles'], ['Second profile'])
        self.worker.camera = None
        with patch('brio_control.service.discover_devices', return_value=[]):
            self.assertEqual(self.worker.state()['profiles'], [])

    def test_select_other_device_is_rejected_while_streaming(self):
        first = SimpleNamespace(usb_id='046d:0943', identity='first', path='/dev/video2')
        second = SimpleNamespace(usb_id='046d:0943', identity='second', path='/dev/video4')
        self.worker.devices = [first, second]
        self.worker.camera = MagicMock(device=first)
        self.worker.engine = MagicMock(running=True)
        self.worker._stream_identity = identity_key(first)
        with self.assertRaisesRegex(ValueError, 'Pare o processamento'):
            self.worker.select_device(second.path)
        self.assertIs(self.worker.camera.device, first)
        self.worker.camera.close.assert_not_called()

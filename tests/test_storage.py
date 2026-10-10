import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from brio_control.storage import TemporaryControls, atomic_json


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'journal.json'
        self.device = SimpleNamespace(usb_id='046d:0943', identity='test-device')

    def test_recover_and_do_not_overwrite_external_change(self):
        journal = TemporaryControls(self.path)
        journal.before_write(self.device, 'zoom', 100, 200)
        values = {'zoom': 200}
        restarted = TemporaryControls(self.path)
        restarted.restore(self.device, values.get, values.__setitem__)
        self.assertEqual(values['zoom'], 100)
        self.assertFalse(self.path.exists())
        journal.before_write(self.device, 'zoom', 100, 200)
        values['zoom'] = 300
        journal.restore(self.device, values.get, values.__setitem__)
        self.assertEqual(values['zoom'], 300)

    def test_unplug_preserves_journal_and_wrong_identity_is_not_written(self):
        journal = TemporaryControls(self.path)
        journal.before_write(self.device, 'zoom', 100, 200)
        calls = []
        other = SimpleNamespace(usb_id='046d:0943', identity='another-device')
        journal.restore(other, lambda _: 200, lambda *args: calls.append(args))
        self.assertEqual(calls, [])
        def disconnected(_): raise OSError('disconnected')
        self.assertTrue(journal.restore(self.device, disconnected, lambda *_: None))
        self.assertTrue(self.path.exists())

    def test_user_setting_replaces_temporary_ownership(self):
        journal = TemporaryControls(self.path)
        journal.before_write(self.device, 'zoom', 100, 200)
        journal.user_changed('zoom')
        self.assertFalse(self.path.exists())

    def test_corrupt_journal_is_ignored_without_writing_camera(self):
        for value in ('{invalid', '[]', '{"schema":true}',
                '{"schema":1,"identity":"wrong","controls":{}}'):
            with self.subTest(value=value):
                self.path.write_text(value)
                journal = TemporaryControls(self.path)
                calls = []
                self.assertEqual(journal.restore(self.device, lambda _: 1, lambda *args: calls.append(args)), [])
                self.assertEqual(calls, [])
                self.assertIsNotNone(journal.warning)

    def test_originals_are_available_only_for_matching_camera(self):
        journal = TemporaryControls(self.path)
        journal.before_write(self.device, 'fov', 65, 90)
        self.assertEqual(journal.originals(self.device), {'fov': 65})
        other = SimpleNamespace(usb_id='046d:0943', identity='other')
        self.assertEqual(journal.originals(other), {})

    def test_failed_atomic_replace_preserves_existing_file_and_cleans_temporary(self):
        atomic_json(self.path, {'size': '720'})
        with patch('brio_control.storage.os.replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                atomic_json(self.path, {'size': '1080'})
        self.assertEqual(self.path.read_text(), '{"size": "720"}')
        self.assertEqual(list(self.path.parent.glob('.write-*')), [])

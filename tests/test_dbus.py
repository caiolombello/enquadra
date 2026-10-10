"""Exercise the real worker on a private bus, using synthetic video only."""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class DBusIntegrationTests(unittest.TestCase):
    def test_worker_video_survives_client_disconnect_and_stops_cleanly(self):
        if not shutil.which('dbus-run-session') or not importlib.util.find_spec('gi'):
            self.skipTest('D-Bus/PyGObject are not available')
        import gi
        try:
            gi.require_version('GstVideo', '1.0')
        except ValueError:
            self.skipTest('GstVideo binding is not installed')
        script = '''
import json, time
from brio_control.client import Client, ClientError, ensure_worker
from brio_control.preview import PreviewReader
ensure_worker()
c = Client()
assert not c.state()['running']
state = c.start({'synthetic': True})
reader = PreviewReader(state['preview_path'])
try:
    end = time.monotonic() + 4
    while reader.read() is None and time.monotonic() < end: time.sleep(.03)
    assert reader.read() is not None
    del c
    time.sleep(.6)
    c = Client()
    state = c.state()
    assert state['running'] and state['fps'] > 20, state
    c.processing({'zoom': 2.0, 'pan_x': -.3, 'mirror': True})
    assert c.state()['processing']['pan_x'] == -.3
    c.stop()
    assert not c.state()['running']
    assert reader.read() is None
    try: c.start({'synthetic': True, 'output': '/dev/video9999'})
    except ClientError: pass
    else: raise AssertionError('missing virtual device was accepted')
    assert not c.state()['running']
    print(json.dumps({'fps': state['fps'], 'client_reconnect': True, 'stop': True}))
finally:
    reader.close()
    c.shutdown()
'''
        with tempfile.TemporaryDirectory() as root:
            env = os.environ.copy()
            for name, suffix in [('XDG_CONFIG_HOME', 'config'), ('XDG_STATE_HOME', 'state'), ('XDG_RUNTIME_DIR', 'runtime')]:
                path = Path(root) / suffix
                path.mkdir(mode=0o700)
                env[name] = str(path)
            result = subprocess.run(['dbus-run-session', sys.executable, '-c', script],
                capture_output=True, text=True, timeout=20, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('"client_reconnect": true', result.stdout)

    def test_preferences_survive_worker_restart_without_starting_capture(self):
        if not shutil.which('dbus-run-session') or not importlib.util.find_spec('gi'):
            self.skipTest('D-Bus/PyGObject are not available')
        script = '''
import time
from brio_control.client import Client, ClientError, ensure_worker
ensure_worker()
c = Client()
expected = dict(preview_only=False, output='/dev/video12', size='1080', last_profile='Reuniões')
assert c.preferences(expected)['preferences'] == expected
try: c.preferences({'preview_only': 1})
except ClientError: pass
else: raise AssertionError('invalid preference was accepted')
assert c.state()['preferences'] == expected
c.shutdown()
time.sleep(.4)
ensure_worker()
c = Client()
state = c.state()
assert state['preferences'] == expected, state
assert not state['running'] and state['preview_path'] is None, state
assert state['virtual_device'] is None, state
c.shutdown()
print('restart_without_capture')
'''
        with tempfile.TemporaryDirectory() as root:
            env = os.environ.copy()
            for name, suffix in [('XDG_CONFIG_HOME', 'config'), ('XDG_STATE_HOME', 'state'), ('XDG_RUNTIME_DIR', 'runtime')]:
                path = Path(root) / suffix
                path.mkdir(mode=0o700)
                env[name] = str(path)
            result = subprocess.run(['dbus-run-session', sys.executable, '-c', script],
                capture_output=True, text=True, timeout=20, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('restart_without_capture', result.stdout)

    def test_cli_status_closes_captured_pipes_while_worker_survives(self):
        if not shutil.which('dbus-run-session') or not importlib.util.find_spec('gi'):
            self.skipTest('D-Bus/PyGObject are not available')
        script = '''
import json, subprocess, sys
from brio_control.client import Client
result = subprocess.run([sys.executable, '-m', 'brio_control', 'status'],
    capture_output=True, text=True, timeout=8)
assert result.returncode == 0, result.stdout + result.stderr
assert json.loads(result.stdout)['running'] is False
c = Client()
try:
    assert c.state()['running'] is False
    c.preferences({'size': '1080'})
    assert c.state()['preferences']['size'] == '1080'
finally:
    c.shutdown()
print('cli_pipes_closed_worker_alive')
'''
        with tempfile.TemporaryDirectory() as root:
            env = os.environ.copy()
            for name, suffix in [('XDG_CONFIG_HOME', 'config'), ('XDG_STATE_HOME', 'state'), ('XDG_RUNTIME_DIR', 'runtime')]:
                path = Path(root) / suffix
                path.mkdir(mode=0o700)
                env[name] = str(path)
            result = subprocess.run(['dbus-run-session', sys.executable, '-c', script],
                capture_output=True, text=True, timeout=20, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('cli_pipes_closed_worker_alive', result.stdout)

    def test_worker_watchdog_restores_failed_engine_without_clients(self):
        if not shutil.which('dbus-run-session') or not importlib.util.find_spec('gi'):
            self.skipTest('D-Bus/PyGObject are not available')
        script = '''
import os, signal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from gi.repository import GLib
from brio_control.service import Worker, run_worker
with patch('brio_control.service.discover_devices', return_value=[]):
    worker = Worker()
device = SimpleNamespace(usb_id='046d:0943', identity='test')
worker.camera = MagicMock(device=device)
values = {'fov': 90}
worker._read_raw = values.get
worker._write_raw = values.__setitem__
worker.temporary.before_write(device, 'fov', 65, 90)
worker.engine = SimpleNamespace(running=False, error='fatal', warning=None, stop=MagicMock())
observed = []
def check():
    observed.append((worker.engine is None, values['fov'], worker.temporary.path.exists(), worker.error))
    os.kill(os.getpid(), signal.SIGTERM)
    return False
GLib.timeout_add(1100, check)
with patch('brio_control.service.Worker', return_value=worker), \\
        patch.object(worker, 'state', side_effect=AssertionError('no clients should poll state')):
    run_worker()
assert observed == [(True, 65, False, 'fatal')], observed
print('watchdog_without_clients')
'''
        with tempfile.TemporaryDirectory() as root:
            env = os.environ.copy()
            for name, suffix in [('XDG_CONFIG_HOME', 'config'), ('XDG_STATE_HOME', 'state'), ('XDG_RUNTIME_DIR', 'runtime')]:
                path = Path(root) / suffix
                path.mkdir(mode=0o700)
                env[name] = str(path)
            result = subprocess.run(['dbus-run-session', sys.executable, '-c', script],
                capture_output=True, text=True, timeout=10, env=env)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('watchdog_without_clients', result.stdout)

    def test_select_device_client_and_dbus_contract(self):
        if not importlib.util.find_spec('gi'):
            self.skipTest('PyGObject is not available')
        from unittest.mock import MagicMock
        from gi.repository import Gio
        from brio_control.client import Client, METHODS, XML
        client = object.__new__(Client)
        client._call = MagicMock(return_value={'selected_device': '/dev/video4'})
        self.assertEqual(client.select_device('/dev/video4'), {'selected_device': '/dev/video4'})
        client._call.assert_called_once_with('SelectDevice', '/dev/video4')
        self.assertEqual(METHODS['SelectDevice'], ['s'])
        method = next(item for item in Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0].methods
                      if item.name == 'SelectDevice')
        self.assertEqual([argument.signature for argument in method.in_args], ['s'])
        self.assertEqual([argument.signature for argument in method.out_args], ['s'])

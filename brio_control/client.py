"""Versioned session D-Bus client for the independently running worker."""

import json
import subprocess
import sys
import time

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

BUS_NAME = "io.github.briocontrol.Worker"
OBJECT_PATH = "/io/github/briocontrol/Worker"
INTERFACE = "io.github.briocontrol.Worker1"
METHODS = {"State": [], "SelectDevice": ["s"], "SetControl": ["s", "i"], "SaveProfile": ["s"],
           "LoadProfile": ["s"], "Start": ["s"], "Stop": [],
           "Processing": ["s"], "Preferences": ["s"], "Shutdown": []}
XML = '<node><interface name="' + INTERFACE + '">' + ''.join(
    '<method name="' + name + '">' + ''.join('<arg type="' + t + '" direction="in"/>' for t in types)
    + '<arg type="s" direction="out"/></method>' for name, types in METHODS.items()
) + '</interface></node>'


class ClientError(Exception):
    pass


class Client:
    def __init__(self):
        self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def _call(self, method, *arguments):
        try:
            signature = '(' + ''.join(METHODS[method]) + ')'
            result = self.connection.call_sync(BUS_NAME, OBJECT_PATH, INTERFACE, method,
                GLib.Variant(signature, arguments), GLib.VariantType.new('(s)'),
                Gio.DBusCallFlags.NONE, 5000, None)
            payload = json.loads(result.unpack()[0])
            if not payload["ok"]:
                raise ClientError(payload["error"])
            return payload["state"]
        except GLib.Error as exc:
            raise ClientError(str(exc)) from exc

    def state(self): return self._call("State")
    def select_device(self, path): return self._call("SelectDevice", path)
    def set_control(self, control_id, value): return self._call("SetControl", control_id, value)
    def save_profile(self, name): return self._call("SaveProfile", name)
    def load_profile(self, name): return self._call("LoadProfile", name)
    def start(self, options): return self._call("Start", json.dumps(options))
    def stop(self): return self._call("Stop")
    def processing(self, options): return self._call("Processing", json.dumps(options))
    def preferences(self, options): return self._call("Preferences", json.dumps(options))
    def shutdown(self): return self._call("Shutdown")


def ensure_worker():
    client = Client()
    try:
        client.state()
        return
    except ClientError:
        pass
    subprocess.Popen([sys.executable, "-m", "brio_control", "worker"],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            client.state()
            return
        except ClientError:
            time.sleep(0.1)
    raise ClientError("O serviço não iniciou. Confira as dependências do sistema.")

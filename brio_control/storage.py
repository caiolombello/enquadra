"""Atomic user profiles and guarded restoration of temporary controls."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def identity_key(device):
    return hashlib.sha256((device.usb_id + ':' + device.identity).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(value, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def valid_identity(value):
    return isinstance(value, str) and re.fullmatch(r'[0-9a-f]{64}', value) is not None


class TemporaryControls:
    def __init__(self, path):
        self.path = Path(path)
        self.data = read_json(self.path, None)
        self.warning = None
        if self.path.exists() and not self._valid(self.data):
            self.data = None
            self.warning = 'Registro temporário inválido; recuperação ignorada.'

    @staticmethod
    def _valid(data):
        return (isinstance(data, dict) and type(data.get('schema')) is int and data['schema'] == 1
            and valid_identity(data.get('identity')) and isinstance(data.get('controls'), dict)
            and all(isinstance(key, str) and isinstance(item, dict)
                and type(item.get('original')) is int and type(item.get('intended')) is int
                and all(-(2 ** 31) <= item[field] < 2 ** 31 for field in ('original', 'intended'))
                for key, item in data['controls'].items()))

    def originals(self, device):
        if not self.data or self.data['identity'] != identity_key(device):
            return {}
        return {key: item['original'] for key, item in self.data['controls'].items()}

    def before_write(self, device, control_id, original, intended):
        key = identity_key(device)
        if self.data and self.data['identity'] != key:
            raise ValueError('Há configurações temporárias de outra câmera aguardando recuperação.')
        if self.data is None:
            self.data = {'schema': 1, 'identity': key, 'controls': {}}
        entry = self.data['controls'].setdefault(control_id, {'original': original})
        entry['intended'] = intended
        atomic_json(self.path, self.data)

    def user_changed(self, control_id):
        if self.data:
            self.data['controls'].pop(control_id, None)
            self._save()

    def restore(self, device, read, write):
        if not self.data or self.data['identity'] != identity_key(device):
            return []
        errors = []
        for control_id, item in list(self.data['controls'].items()):
            try:
                current = read(control_id)
                if current == item['intended'] and current != item['original']:
                    write(control_id, item['original'])
                    if read(control_id) != item['original']:
                        raise ValueError('Leitura divergente na restauração.')
                # A different current value belongs to someone else; leave it intact.
                self.data['controls'].pop(control_id)
            except Exception as exc:
                errors.append(str(exc))
        self._save()
        return errors

    def _save(self):
        if self.data and self.data['controls']:
            atomic_json(self.path, self.data)
        else:
            self.data = None
            self.path.unlink(missing_ok=True)

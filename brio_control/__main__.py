"""Application launcher and local diagnostics."""

import argparse
import ast
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path


def _rotation_observed(device):
    from .storage import identity_key
    directory = Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'brio-control'
    try:
        metadata = directory.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077):
            return False
        fd = os.open(directory / 'hardware-observations.json', os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                    or metadata.st_mode & 0o077 or metadata.st_size > 65536):
                return False
            observation = json.load(stream)
    except (OSError, ValueError, UnicodeError):
        return False
    if (not isinstance(observation, dict) or type(observation.get('schema')) is not int
            or observation['schema'] != 1 or observation.get('identity') != identity_key(device)):
        return False
    show = observation.get('show_mode')
    return (isinstance(show, dict) and show.get('direct_preview_confirmed') is True
            and show.get('app_preview_confirmed') is True and show.get('observation_source') == 'user')


def _models_diagnostic():
    # Read the authoritative pinned metadata without importing NumPy/OpenCV.
    models = []
    for module in ('framing.py', 'background.py'):
        path = Path(__file__).with_name(module)
        try:
            tree = ast.parse(path.read_text())
            info = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign)
                        and any(isinstance(target, ast.Name) and target.id == '_MODEL_INFO' for target in node.targets))
        except (OSError, ValueError, SyntaxError, StopIteration):
            continue
        for name, expected in info.values():
            model = path.with_name('models') / name
            entry = dict(name=name, exists=model.is_file(), expected_sha256=expected, hash_valid=False)
            try:
                entry['hash_valid'] = hashlib.sha256(model.read_bytes()).hexdigest() == expected
            except OSError:
                pass
            models.append(entry)
    return models


def setup_virtual_camera():
    root = Path(__file__).resolve().parent.parent
    script = next((path for path in (root / 'scripts/setup-virtual-camera', root / 'setup-virtual-camera')
                   if path.is_file()), None)
    if script is None:
        raise RuntimeError('Preparação da câmera virtual não encontrada na instalação.')
    command = ['/bin/sh', str(script)]
    if os.geteuid() != 0:
        if shutil.which('pkexec') is None:
            raise RuntimeError('Polkit não está disponível. Instale o pacote pkexec do sistema ou execute esta preparação explicitamente como administrador.')
        command.insert(0, 'pkexec')
    return subprocess.run(command, check=False).returncode


def doctor():
    from .camera import Camera, discover_devices
    from . import __version__
    node = Path('/dev/video10')
    result = dict(version=__version__, python=sys.version.split()[0],
                  opencv=bool(importlib.util.find_spec('cv2')),
                  numpy=bool(importlib.util.find_spec('numpy')),
                  models=_models_diagnostic(),
                  virtual_module_loaded=Path('/sys/module/v4l2loopback').exists(),
                  virtual_device=dict(path=str(node), exists=node.exists(),
                      readable=os.access(node, os.R_OK), writable=os.access(node, os.W_OK)),
                  show_mode=dict(rotation_observed=False, control_available=False, validated=False), devices=[])
    try:
        devices = discover_devices()
    except Exception:
        devices = []
        result['discovery_error'] = 'Falha ao descobrir câmeras.'
    for device in devices:
        observed = _rotation_observed(device)
        entry = dict(path=device.path, name=device.name, usb_id=device.usb_id,
                     show_mode=dict(rotation_observed=observed, control_available=False, validated=False))
        result['show_mode']['rotation_observed'] |= observed
        try:
            with Camera(device) as camera:
                for key, read in (('controls', camera.enumerate_controls), ('fov', lambda: camera.capabilities(5))):
                    try:
                        entry[key] = read()
                    except Exception as exc:
                        entry[key + '_error'] = str(exc).replace(device.identity, '[identidade privada]') if device.identity else str(exc)
        except Exception as exc:
            entry['error'] = str(exc).replace(device.identity, '[identidade privada]') if device.identity else str(exc)
        result['devices'].append(entry)
    try:
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('GstVideo', '1.0')
        result['gtk4_gst_video'] = True
    except (ImportError, ValueError, OSError):
        result['gtk4_gst_video'] = False
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description='Enquadra — câmera e enquadramento no Linux')
    from . import __version__
    parser.add_argument('--version', action='version', version=f'Enquadra {__version__}')
    commands = parser.add_subparsers(dest='command')
    commands.add_parser('gui', help='Abrir interface (captura começa somente pelo botão)')
    commands.add_parser('worker', help='Serviço independente da interface')
    commands.add_parser('doctor', help='Diagnóstico sem captura nem mudanças na câmera')
    for name in ('status', 'stop', 'shutdown'):
        commands.add_parser(name)
    start = commands.add_parser('start')
    start.add_argument('--device')
    output = start.add_mutually_exclusive_group()
    output.add_argument('--output', help='/dev/videoN criado pelo v4l2loopback')
    output.add_argument('--preview-only', action='store_true', help='Usar somente preview local')
    start.add_argument('--size', choices=['720', '1080'])
    start.add_argument('--autoframe', action=argparse.BooleanOptionalAction, default=None)
    commands.add_parser('setup-virtual-camera', help='Preparar câmera virtual com autenticação nativa')
    probe = commands.add_parser('probe-show-mode', help='Ensaio experimental interativo: requer inclinação física')
    probe.add_argument('--device')
    args = parser.parse_args()
    try:
        if args.command in (None, 'gui'):
            from .gui import run
            return run()
        if args.command == 'worker':
            from .service import run_worker
            return run_worker()
        if args.command == 'doctor': return doctor()
        if args.command == 'setup-virtual-camera': return setup_virtual_camera()
        if args.command == 'probe-show-mode':
            from .probe import run
            return run(args.device)
        from .client import Client, ensure_worker
        if args.command not in ('shutdown',): ensure_worker()
        client = Client()
        if args.command == 'start':
            options = {}
            if args.device is not None: options['device'] = args.device
            if args.output is not None or args.preview_only: options['output'] = args.output
            if args.size is not None:
                options['width'], options['height'] = (1920, 1080) if args.size == '1080' else (1280, 720)
            if args.autoframe is not None: options['autoframe'] = args.autoframe
            result = client.start(options)
        elif args.command == 'status': result = client.state()
        elif args.command == 'stop': result = client.stop()
        else: result = client.shutdown()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(f'Enquadra: {exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

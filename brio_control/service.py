"""Session worker: owns camera controls, processing and their lifetime."""

import json
import math
import os
import re
import signal
from pathlib import Path

from .camera import Camera, CameraError, discover_devices
from .storage import TemporaryControls, atomic_json, identity_key, read_json, valid_identity

DEFAULT_PROCESSING = dict(autoframe=False, zoom=1.0, pan_x=0.0, pan_y=0.0, mirror=False,
    background_mode="off", background_image=None, background_blur=12.0)
DEFAULT_PREFERENCES = dict(preview_only=True, output='/dev/video10', size='720', last_profile=None)
FOV_CODES = {90: 0, 78: 1, 65: 2}
FRAMING_CONTROLS = {'fov', '0x9a090d', '0x9a0908', '0x9a0909'}


class Worker:
    def __init__(self, config_dir=None, state_dir=None, runtime_dir=None):
        self.config_dir = Path(config_dir or Path(os.environ.get('XDG_CONFIG_HOME', Path.home() / '.config')) / 'brio-control')
        self.state_dir = Path(state_dir or Path(os.environ.get('XDG_STATE_HOME', Path.home() / '.local/state')) / 'brio-control')
        self.runtime_dir = Path(runtime_dir or Path(os.environ.get('XDG_RUNTIME_DIR', '/tmp')) / f'brio-control-{os.getuid()}')
        self.config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.runtime_dir.stat().st_uid != os.getuid() or self.runtime_dir.stat().st_mode & 0o077:
            raise ValueError('Diretório de runtime não é privado.')
        self.camera = None
        self.devices = []
        self.engine = None
        self._processor = None
        self.virtual_device = None
        self.error = None
        self.warning = None
        self.processing_settings = DEFAULT_PROCESSING.copy()
        self._stream_identity = None
        self.temporary = TemporaryControls(self.state_dir / 'temporary-controls.json')
        self.profiles_path = self.config_dir / 'profiles.json'
        self.profiles = {}
        raw_profiles = read_json(self.profiles_path, None)
        if raw_profiles is None:
            if self.profiles_path.exists(): self.warning = 'Arquivo de perfis inválido; perfis ignorados.'
            raw_profiles = {}
        if isinstance(raw_profiles, dict):
            for name, profile in raw_profiles.items():
                try:
                    self._validate_profile(name, profile)
                    self.profiles[name] = profile
                except ValueError:
                    self.warning = 'Perfis inválidos foram ignorados.'
        else:
            self.warning = 'Arquivo de perfis inválido; perfis ignorados.'
        self.settings_path = self.config_dir / 'settings.json'
        self.settings = dict(schema=1, preferences=DEFAULT_PREFERENCES.copy(), cameras={})
        self.preference_settings = DEFAULT_PREFERENCES.copy()
        self.profile = None
        self._restoring = False
        raw = read_json(self.settings_path, None)
        if raw is not None:
            try:
                if not isinstance(raw, dict) or type(raw.get('schema')) is not int or raw['schema'] != 1:
                    raise ValueError('Configuração inválida.')
                preferences = self._validate_preferences(raw.get('preferences', {}))
                cameras = raw.get('cameras', {})
                if not isinstance(cameras, dict): raise ValueError('Identidades inválidas.')
                for key, value in cameras.items():
                    if not valid_identity(key) or not isinstance(value, dict): raise ValueError('Identidade inválida.')
                    self._validate_processing(value.get('processing', {}))
                    if value.get('profile') is not None and value['profile'] not in self.profiles:
                        raise ValueError('Perfil desconhecido.')
                    if value.get('profile') is not None and self.profiles[value['profile']]['identity'] != key:
                        raise ValueError('Perfil pertence a outra câmera.')
                self.settings = dict(schema=1, preferences=preferences, cameras=cameras)
                self.preference_settings = preferences.copy()
            except (ValueError, TypeError):
                self.warning = 'Preferências inválidas foram ignoradas.'
        elif self.settings_path.exists():
            self.warning = 'Preferências inválidas foram ignoradas.'
        self.warning = '; '.join(filter(None, [self.warning, self.temporary.warning])) or None
        self.refresh_devices()

    def refresh_devices(self):
        self.devices = discover_devices()
        running = bool(self.engine and self.engine.running)
        if self.camera:
            matches = [d for d in self.devices if identity_key(d) == identity_key(self.camera.device)]
            try:
                if not matches or matches[0].path != self.camera.device.path:
                    raise CameraError('Câmera desconectada.')
                # An old FD can survive unplug/replug under the same /dev/videoN.
                self.camera.read_control(0x980900)
            except CameraError:
                self.camera.close()
                self.camera = None
                self.error = 'A câmera foi desconectada. Reconecte a mesma câmera.'
        if self.camera is None:
            candidates = [d for d in self.devices if not running or identity_key(d) == self._stream_identity]
            if candidates:
                self.select_device(candidates[0].path)
                self.error = None
                if running and self.processing_settings['autoframe']:
                    self._normalize()

    def select_device(self, path):
        device = next((d for d in self.devices if d.path == path), None)
        if device is None:
            raise CameraError('Brio 500 não encontrada ou sem permissão de acesso.')
        if self.camera and identity_key(device) == identity_key(self.camera.device):
            return
        running = bool(self.engine and self.engine.running)
        if running and identity_key(device) != self._stream_identity:
            raise ValueError('Pare o processamento antes de trocar a câmera.')
        if self.camera:
            self.camera.close()
        self.camera = Camera(device, writable=True).__enter__()
        errors = [] if running else self.temporary.restore(device, self._read_raw, self._write_raw)
        self.warning = '; '.join(filter(None, [self.warning, *errors])) or None
        if not running:
            self._restore_settings(device)

    def _controls(self):
        if self.camera is None:
            return []
        controls = self.camera.enumerate_controls()
        try:
            caps = self.camera.capabilities(5)
            if caps['length'] == 1 and 5 in self.camera.device.xu_selectors:
                controls.append(dict(id='fov', name='Campo de visão', kind='menu', minimum=65, maximum=90,
                    step=1, default=90, value={code: degrees for degrees, code in FOV_CODES.items()}.get(caps['current']),
                    active=True, writable=(caps['info'] & 3) == 3,
                    options=[dict(value=degree, label=f'{degree}°') for degree in (90, 78, 65)]))
        except CameraError:
            pass
        if self.engine and self.engine.running and self.processing_settings['autoframe']:
            for control in controls:
                if control['id'] in FRAMING_CONTROLS:
                    control['writable'] = False
        return controls

    def reap_failed_engine(self):
        # Run on the worker's GLib/RPC thread, including when no client polls.
        if self.engine and not self.engine.running:
            self.error = self.engine.error or self.error
            self.stop()
        return True

    def state(self):
        from .stream import virtual_camera_name
        self.reap_failed_engine()
        self.refresh_devices()
        try:
            controls = self._controls()
        except CameraError as exc:
            self.error = str(exc)
            controls = []
        engine = self.engine
        return dict(api_version=1, devices=[dict(path=d.path, name=d.name) for d in self.devices],
            selected_device=self.camera.device.path if self.camera else None, controls=controls,
            profiles=sorted(name for name, profile in self.profiles.items()
                if self.camera and profile['identity'] == identity_key(self.camera.device)),
            running=bool(engine and engine.running),
            virtual_device=self.virtual_device,
            virtual_camera_name=virtual_camera_name(self.virtual_device or self.preference_settings['output']) or 'Enquadra',
            error=(engine.error if engine else None) or self.error,
            warning='; '.join(filter(None, [self.warning, engine.warning if engine else None])) or None,
            preview_path=engine.preview_path if engine else None,
            fps=round(engine.fps, 1) if engine else 0,
            processing=self.processing_settings.copy(), preferences=self.preference_settings.copy(),
            profile=self.profile,
            show_mode=dict(validated=False, rotation_observed=self._rotation_observed(),
                control_available=False, message='Controle por software ainda não comprovado.'))

    def _rotation_observed(self):
        if self.camera is None:
            return False
        try:
            observation = json.loads((self.state_dir / 'hardware-observations.json').read_text())
        except (OSError, ValueError):
            return False
        if not isinstance(observation, dict) or type(observation.get('schema')) is not int or observation['schema'] != 1:
            return False
        if observation.get('identity') != identity_key(self.camera.device):
            return False
        show_mode = observation.get('show_mode')
        return (isinstance(show_mode, dict) and show_mode.get('direct_preview_confirmed') is True
            and show_mode.get('app_preview_confirmed') is True and show_mode.get('observation_source') == 'user')

    def _require_camera(self):
        if self.camera is None:
            raise CameraError('Conecte uma Brio 500 para configurar os controles.')

    def _read_raw(self, control_id):
        self._require_camera()
        if control_id == 'fov':
            return {code: degree for degree, code in FOV_CODES.items()}[self.camera.read(5)[0]]
        return self.camera.read_control(int(control_id, 16))

    def _write_raw(self, control_id, value):
        self._require_camera()
        if type(value) is not int:
            raise ValueError('O controle requer um valor inteiro.')
        if control_id == 'fov':
            if value not in FOV_CODES: raise ValueError('Campo de visão inválido.')
            self.camera.write(5, FOV_CODES[value])
        elif re.fullmatch(r'0x[0-9a-f]{1,8}', control_id):
            self.camera.set_control(int(control_id, 16), value)
        else:
            raise ValueError('Controle não suportado.')

    def set_control(self, control_id, value):
        advertised = {c['id'] for c in self._controls() if c['writable']}
        if control_id not in advertised:
            raise CameraError('Controle não disponível para escrita.')
        self._write_raw(control_id, value)
        self.temporary.user_changed(control_id)
        self.error = None

    def _normalize(self):
        self._require_camera()
        warnings = []
        for control_id, intended in [('fov', 90), ('0x9a090d', 100), ('0x9a0908', 0), ('0x9a0909', 0)]:
            try:
                original = self._read_raw(control_id)
                if original == intended:
                    continue
                self.temporary.before_write(self.camera.device, control_id, original, intended)
                self._write_raw(control_id, intended)
            except (CameraError, ValueError, KeyError) as exc:
                warnings.append(f'{control_id}: {exc}')
        self.warning = '; '.join(warnings) or None

    @staticmethod
    def _validate_processing(options):
        if not isinstance(options, dict) or set(options) - set(DEFAULT_PROCESSING):
            raise ValueError('Opção de enquadramento não reconhecida.')
        merged = DEFAULT_PROCESSING | options
        for key in ('autoframe', 'mirror'):
            if type(merged[key]) is not bool: raise ValueError('Opção booleana inválida.')
        for key, minimum, maximum in [('zoom', 1, 4), ('pan_x', -1, 1), ('pan_y', -1, 1)]:
            value = merged[key]
            if type(value) not in (int, float) or not minimum <= value <= maximum or not math.isfinite(value):
                raise ValueError('Zoom ou posição inválida.')
        from .background import validate_settings
        validate_settings(merged["background_mode"], merged["background_image"], merged["background_blur"])
        return merged

    @staticmethod
    def _validate_preferences(options):
        if not isinstance(options, dict) or set(options) - set(DEFAULT_PREFERENCES):
            raise ValueError('Preferência não reconhecida.')
        merged = DEFAULT_PREFERENCES | options
        if type(merged['preview_only']) is not bool: raise ValueError('Saída local inválida.')
        if not isinstance(merged['output'], str) or not re.fullmatch(r'/dev/video[0-9]+', merged['output']):
            raise ValueError('Dispositivo virtual inválido.')
        if merged['size'] not in ('720', '1080'): raise ValueError('Resolução inválida.')
        name = merged['last_profile']
        if name is not None and (not isinstance(name, str) or not 1 <= len(name.strip()) <= 64):
            raise ValueError('Nome de perfil inválido.')
        return merged

    @classmethod
    def _validate_profile(cls, name, profile):
        cls._validate_preferences({'last_profile': name})
        if not isinstance(profile, dict) or type(profile.get('schema')) is not int or profile['schema'] != 1:
            raise ValueError('Perfil inválido.')
        if not valid_identity(profile.get('identity')) or not isinstance(profile.get('controls'), dict):
            raise ValueError('Identidade ou controles inválidos.')
        if any(not isinstance(key, str) or not (key == 'fov' or re.fullmatch(r'0x[0-9a-f]{1,8}', key))
                or type(value) is not int or not -(2 ** 31) <= value < 2 ** 31
                for key, value in profile['controls'].items()):
            raise ValueError('Controles inválidos.')
        cls._validate_processing(profile.get('processing', {}))

    def preferences(self, options):
        merged = self._validate_preferences(self.preference_settings | options) if isinstance(options, dict) else self._validate_preferences(options)
        if merged == self.preference_settings: return
        settings = self.settings | {'preferences': merged}
        atomic_json(self.settings_path, settings)
        self.settings = settings
        self.preference_settings = merged

    def _persist_camera(self):
        if self._restoring or not self.camera: return
        key = identity_key(self.camera.device)
        current = dict(processing=self.processing_settings.copy(), profile=self.profile)
        if self.settings['cameras'].get(key) == current: return
        cameras = self.settings['cameras'] | {key: current}
        settings = self.settings | {'cameras': cameras}
        atomic_json(self.settings_path, settings)
        self.settings = settings

    def _restore_settings(self, device):
        saved = self.settings['cameras'].get(identity_key(device))
        self.profile = None
        self.processing_settings = DEFAULT_PROCESSING.copy()
        if not saved: return
        name = saved.get('profile')
        profile_processing = self.profiles.get(name, {}).get('processing', {})
        intended = self._validate_processing(DEFAULT_PROCESSING | profile_processing | saved.get('processing', {}))
        self._restoring = True
        try:
            if name is not None:
                self.load_profile(name)
            self.processing(saved.get('processing', {}))
        except (CameraError, ValueError, KeyError) as exc:
            # Retain the user's intended effect. Starting capture must validate
            # it again and fail rather than silently exposing the original room.
            self.processing_settings = intended
            self.warning = f'Não foi possível restaurar as preferências desta câmera: {exc}'
        finally:
            self._restoring = False

    def processing(self, options):
        if not isinstance(options, dict) or set(options) - set(DEFAULT_PROCESSING):
            raise ValueError('Opção de enquadramento não reconhecida.')
        merged = self._validate_processing(self.processing_settings | options)
        old = self.processing_settings['autoframe']
        if self.engine:
            self.engine.configure(merged)
        else:
            from .framing import FrameProcessor
            if self._processor is None:
                self._processor = FrameProcessor()
            try:
                self._processor.configure(**merged)
            finally:
                # No tracking thread is needed until capture starts; resources remain cached.
                self._processor.close()
        if self.engine and self.engine.running and not getattr(self.engine, 'synthetic', False):
            if merged['autoframe'] and not old:
                self._normalize()
            elif old and not merged['autoframe'] and self.camera:
                self.warning = '; '.join(self.temporary.restore(self.camera.device, self._read_raw, self._write_raw)) or None
        self.processing_settings = merged
        if not self._restoring and self.warning and self.warning.startswith('Não foi possível restaurar as preferências desta câmera:'):
            self.warning = None
        self._persist_camera()

    def start(self, options):
        if self.engine and self.engine.running:
            raise ValueError('O processamento já está ativo.')
        if self.engine:
            self.stop()
        if set(options) - {'device', 'output', 'width', 'height', 'autoframe', 'synthetic'}:
            raise ValueError('Opção de captura não reconhecida.')
        preferred_size = (1280, 720) if self.preference_settings['size'] == '720' else (1920, 1080)
        width, height = options.get('width', preferred_size[0]), options.get('height', preferred_size[1])
        if (width, height) not in ((1280, 720), (1920, 1080)):
            raise ValueError('Escolha 1280×720 ou 1920×1080.')
        synthetic = options.get('synthetic', False)
        if type(synthetic) is not bool: raise ValueError('Fonte de teste inválida.')
        if not synthetic:
            self.refresh_devices()
            if options.get('device'): self.select_device(options['device'])
            self._require_camera()
        if 'autoframe' in options:
            self.processing({'autoframe': options['autoframe']})
        output = options.get('output', None if self.preference_settings['preview_only'] else self.preference_settings['output'])
        if output is not None and (not isinstance(output, str) or not re.fullmatch(r'/dev/video[0-9]+', output)):
            raise ValueError('Dispositivo virtual inválido.')
        from .stream import VideoEngine
        self.engine = VideoEngine(str(self.runtime_dir), width, height, self.processing_settings,
            processor=self._processor)
        self._processor = self.engine.processor
        self.engine.synthetic = synthetic
        device = self.camera.device if self.camera else None
        expected = identity_key(device) if device else None
        self._stream_identity = expected
        def resolver():
            return next((d.path for d in discover_devices() if identity_key(d) == expected), None)
        try:
            self.engine.start(device.path if device else '/dev/video0', output, resolver, synthetic)
            if self.processing_settings['autoframe'] and not synthetic:
                self._normalize()
            self.virtual_device = output
            self.error = None
        except Exception as exc:
            self.error = str(exc)
            self.stop()
            raise

    def stop(self):
        warnings = [self.warning] if self.warning else []
        if self.engine:
            self.engine.stop()
            if isinstance(self.engine.warning, str) and self.engine.warning:
                warnings.append(self.engine.warning)
            self.engine = None
        if self.camera:
            warnings.extend(self.temporary.restore(self.camera.device, self._read_raw, self._write_raw))
        self.warning = '; '.join(warnings) or None
        self.virtual_device = None
        self._stream_identity = None

    def save_profile(self, name):
        self._require_camera()
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 64:
            raise ValueError('Nome do perfil deve ter entre 1 e 64 caracteres.')
        name = name.strip()
        existing = self.profiles.get(name)
        if existing and existing['identity'] != identity_key(self.camera.device):
            raise ValueError('Este nome de perfil pertence a outra câmera. Escolha outro nome.')
        controls = {c['id']: c['value'] for c in self._controls() if c['writable'] and c['value'] is not None}
        if self.engine and self.engine.running and self.processing_settings['autoframe']:
            originals = self.temporary.originals(self.camera.device)
            controls.update({key: value for key, value in originals.items() if key in FRAMING_CONTROLS})
        # Gain can remain writable while auto exposure or auto gain adjusts it.
        if controls.get('0x9a0901', 1) != 1 or controls.get('0x980912', 0) == 1:
            controls.pop('0x980913', None)
        profiles = self.profiles | {name: dict(schema=1, identity=identity_key(self.camera.device),
            controls=controls, processing=self.processing_settings.copy())}
        atomic_json(self.profiles_path, profiles)
        self.profiles = profiles
        self.profile = name
        if not self._restoring: self.preferences({'last_profile': name})
        self._persist_camera()

    def load_profile(self, name):
        self._require_camera()
        profile = self.profiles.get(name)
        if not isinstance(profile, dict) or profile.get('schema') != 1 or profile.get('identity') != identity_key(self.camera.device):
            raise ValueError('Perfil não corresponde a esta câmera.')
        self._validate_profile(name, profile)
        processing = self._validate_processing(profile.get('processing', {}))
        # Validate local resources before restoring tracking or writing camera
        # controls: a moved image or unavailable model must leave this session intact.
        from .framing import FrameProcessor
        validator = FrameProcessor()
        try:
            validator.configure(**processing)
        finally:
            validator.close()
        controls = profile['controls']
        # Native controls are locked during tracking; restore temporary
        # normalization before applying the saved camera configuration.
        restoring = self._restoring
        self._restoring = True
        try:
            if self.processing_settings['autoframe']:
                self.processing({'autoframe': False})
            automatic = {'0x98090c', '0x980912', '0x9a0901', '0x9a090c'}
            for control_id in sorted(controls, key=lambda key: (key not in automatic, key)):
                if control_id == '0x980913' and (controls.get('0x9a0901', 1) != 1 or controls.get('0x980912', 0) == 1):
                    continue
                self.set_control(control_id, controls[control_id])
            self.processing(processing)
            self.profile = name
        finally:
            self._restoring = restoring
        if not self._restoring: self.preferences({'last_profile': name})
        self._persist_camera()

    def close(self):
        self.stop()
        if self._processor is not None:
            self._processor.close()
        if self.camera:
            self.camera.close()
            self.camera = None


def run_worker():
    import gi
    gi.require_version('Gio', '2.0')
    from gi.repository import Gio, GLib
    from .client import BUS_NAME, INTERFACE, OBJECT_PATH, XML
    connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    response = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'RequestName', GLib.Variant('(su)', (BUS_NAME, 4)),
        GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 5000, None)
    if response.unpack()[0] != 1:
        return 0
    worker = Worker()
    loop = GLib.MainLoop()
    methods = dict(State=worker.state, SelectDevice=worker.select_device,
        SetControl=worker.set_control, SaveProfile=worker.save_profile,
        LoadProfile=worker.load_profile, Start=worker.start, Stop=worker.stop,
        Processing=worker.processing, Preferences=worker.preferences, Shutdown=worker.close)
    def call(_connection, _sender, _path, _interface, method, parameters, invocation):
        try:
            arguments = list(parameters.unpack())
            if method in ('Start', 'Processing', 'Preferences'):
                arguments[0] = json.loads(arguments[0])
                if not isinstance(arguments[0], dict): raise ValueError('Opções devem ser um objeto.')
            methods[method](*arguments)
            payload = dict(ok=True, state=worker.state())
            if method == 'Shutdown': GLib.idle_add(loop.quit)
        except Exception as exc:
            payload = dict(ok=False, error=str(exc), code='camera' if isinstance(exc, CameraError) else 'invalid_request')
        invocation.return_value(GLib.Variant('(s)', (json.dumps(payload, ensure_ascii=False),)))
    info = Gio.DBusNodeInfo.new_for_xml(XML).interfaces[0]
    registration = connection.register_object(OBJECT_PATH, info, call, None, None)
    watchdog = GLib.timeout_add(500, worker.reap_failed_engine)
    def shutdown(*_):
        loop.quit()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        loop.run()
    finally:
        GLib.source_remove(watchdog)
        worker.close()
        connection.unregister_object(registration)
    return 0

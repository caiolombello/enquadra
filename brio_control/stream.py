"""GStreamer capture, frame processing, local preview and virtual output."""

from __future__ import annotations

import ctypes
import fcntl
import os
import re
import threading
import time
from collections import deque

import numpy as np

from .framing import FrameProcessor
from .preview import PreviewWriter


class StreamError(RuntimeError):
    """Capture or output pipeline could not be started or maintained."""


_VIDEO_NODE = re.compile(r"/dev/video[0-9]+\Z")
_OWNED_LOOPBACK_CARDS = (b"Enquadra", b"Brio Control")
_VIDIOC_QUERYCAP = 0x80685600
_VIDIOC_QUERYCTRL = 0xC0445624
_VIDIOC_G_CTRL = 0xC008561B
_VIDIOC_S_CTRL = 0xC008561C
_V4L2_CAP_VIDEO_OUTPUT = 0x00000002
_V4L2_CAP_DEVICE_CAPS = 0x80000000
_V4L2_CTRL_FLAG_NEXT_CTRL = 0x80000000
_V4L2_CTRL_FLAG_DISABLED = 0x00000001
_V4L2_CTRL_FLAG_INACTIVE = 0x00000010
_V4L2_CTRL_FLAG_READ_ONLY = 0x00000004
_V4L2_CTRL_FLAG_WRITE_ONLY = 0x00000040
_V4L2_CTRL_TYPE_INTEGER = 1
_MODEL_FRAME_PERIOD = 1 / 30


class _Capability(ctypes.Structure):
    _fields_ = (
        ("driver", ctypes.c_char * 16), ("card", ctypes.c_char * 32),
        ("bus_info", ctypes.c_char * 32), ("version", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32), ("device_caps", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    )


class _QueryControl(ctypes.Structure):
    _fields_ = (
        ("id", ctypes.c_uint32), ("type", ctypes.c_uint32),
        ("name", ctypes.c_uint8 * 32), ("minimum", ctypes.c_int32),
        ("maximum", ctypes.c_int32), ("step", ctypes.c_int32),
        ("default_value", ctypes.c_int32), ("flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 2),
    )


class _Control(ctypes.Structure):
    _fields_ = (("id", ctypes.c_uint32), ("value", ctypes.c_int32))


def virtual_camera_name(path: str) -> str | None:
    """Read the actual label of our current or legacy loopback without capture."""
    if not isinstance(path, str) or not _VIDEO_NODE.fullmatch(path):
        return None
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        capability = _Capability()
        fcntl.ioctl(fd, _VIDIOC_QUERYCAP, capability, True)
        driver = bytes(capability.driver).split(b"\0", 1)[0]
        card = bytes(capability.card).split(b"\0", 1)[0].strip()
        if driver == b"v4l2 loopback" and card in _OWNED_LOOPBACK_CARDS:
            return card.decode("ascii")
    except OSError:
        pass
    finally:
        if fd is not None:
            os.close(fd)
    return None


class VideoEngine:
    """Owns a single video source and optionally feeds a virtual camera."""

    def __init__(
        self,
        runtime_dir: str,
        width: int = 1280,
        height: int = 720,
        processing: dict | None = None,
        processor: FrameProcessor | None = None,
    ):
        if type(width) is not int or type(height) is not int or not (1 <= width <= 1920 and 1 <= height <= 1080):
            raise StreamError("dimensões de vídeo devem estar entre 1 e 1920×1080")
        self.runtime_dir = runtime_dir
        self.width, self.height = width, height
        self.synthetic = False
        self._processing = {
            "autoframe": False, "zoom": 1.0, "pan_x": 0.0,
            "pan_y": 0.0, "mirror": False,
            "background_mode": "off", "background_image": None, "background_blur": 12.0,
        }
        self.processor = processor if processor is not None else FrameProcessor()
        self._source_pipeline = None
        self._source_bus = None
        self._source_path: str | None = None
        self._output_pipeline = None
        self._output_bus = None
        self._appsrc = None
        self._preview: PreviewWriter | None = None
        self._preview_lock = threading.Lock()
        self._frame_lock = threading.Lock()
        self._output_frame: np.ndarray | None = None
        self._output_at = 0.0
        self._fps_times: deque[float] = deque(maxlen=90)
        self._state_lock = threading.RLock()
        self._lifecycle_lock = threading.Lock()
        self._running = False
        self._error: str | None = None
        self._warning: str | None = None
        self._resolver = None
        self._retry_at = 0.0
        self._stop_event = threading.Event()
        self._ticker_thread: threading.Thread | None = None
        self._monitor_thread: threading.Thread | None = None
        self._Gst = None
        self._GstVideo = None
        self._timeout_restore: tuple[str, int, int, int] | None = None
        self._last_preview_valid = False
        if processing:
            self.configure(processing)
        else:
            self.processor.configure(**self._processing)

    @property
    def processing(self) -> dict:
        with self._state_lock:
            return self._processing.copy()

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._running

    @property
    def preview_path(self) -> str | None:
        with self._preview_lock:
            return self._preview.path if self._preview is not None else None

    @property
    def fps(self) -> float:
        with self._state_lock:
            if len(self._fps_times) < 2 or time.monotonic() - self._fps_times[-1] > 0.5:
                return 0.0
            elapsed = self._fps_times[-1] - self._fps_times[0]
            return (len(self._fps_times) - 1) / elapsed if elapsed > 0 else 0.0

    @property
    def error(self) -> str | None:
        with self._state_lock:
            return self._error or self.processor.last_error

    @property
    def warning(self) -> str | None:
        with self._state_lock:
            return "; ".join(filter(None, [self._warning, getattr(self.processor, "warning", None)])) or None

    def configure(self, settings: dict) -> None:
        if not isinstance(settings, dict):
            raise StreamError("configuração de vídeo deve ser um objeto")
        allowed = set(self._processing)
        if set(settings) - allowed:
            raise StreamError("configuração de enquadramento desconhecida")
        with self._state_lock:
            candidate = self._processing | settings
            self.processor.configure(**candidate)
            if any(candidate[key] != self._processing[key] for key in ('background_mode', 'background_image', 'background_blur')):
                with self._frame_lock:
                    self._output_frame = None
                    self._output_at = 0.0
                self._invalidate_preview()
            self._processing = candidate

    def start(
        self,
        device_path: str,
        output_path: str | None = None,
        resolver=None,
        synthetic: bool = False,
    ) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._running:
                    raise StreamError("processamento de vídeo já está ativo")
            if type(synthetic) is not bool:
                raise StreamError("synthetic deve ser booleano")
            if output_path is not None:
                self._validate_video_node(output_path)
                if output_path == device_path:
                    raise StreamError("a câmera física não pode ser a própria saída virtual")
                if self.width % 2:
                    raise StreamError("a saída YUY2 requer largura par")
                self._validate_owned_loopback(output_path)
            if not synthetic:
                self._validate_video_node(device_path)
            try:
                self._load_gstreamer()
                with self._state_lock:
                    self._error = None
                    self._warning = None
                    self._resolver = resolver
                    self.synthetic = synthetic
                    self._source_path = None if synthetic else device_path
                    self._stop_event.clear()
                self.processor.configure(**self._processing)
                self._preview = PreviewWriter(self.runtime_dir)
                if output_path is not None:
                    self._start_output(output_path)
                    self._prepare_output_timeout(output_path)
                self._start_source(device_path)
                with self._state_lock:
                    self._running = True
                    self._ticker_thread = threading.Thread(target=self._tick_loop, name="video-output", daemon=True)
                    self._monitor_thread = threading.Thread(target=self._monitor_loop, name="video-monitor", daemon=True)
                    self._ticker_thread.start()
                    self._monitor_thread.start()
            except Exception as exc:
                with self._state_lock:
                    self._error = str(exc)
                    self._running = False
                    self._stop_event.set()
                self._cleanup()
                if isinstance(exc, StreamError):
                    raise
                raise StreamError(f"não foi possível iniciar a pipeline: {exc}") from exc

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        """Stop the current session while holding the lifecycle lock."""
        with self._state_lock:
            appsrc = self._appsrc
            running = self._running
            self._running = False
            self._stop_event.set()
            threads = (self._ticker_thread, self._monitor_thread)
        current = threading.current_thread()
        for thread in threads:
            if thread is not None and thread is not current:
                thread.join(timeout=1.5)
        if running and appsrc is not None:
            ticker = threads[0]
            if ticker is not None and ticker.is_alive():
                self._warning = "Não foi possível confirmar o frame preto final: o envio de vídeo não terminou."
            else:
                self._drain_output(appsrc)
        self._cleanup()

    def _drain_output(self, appsrc) -> None:
        """Deliver the final black frame before releasing the output pipeline."""
        Gst = self._Gst
        try:
            if not self._push_output(np.zeros((self.height, self.width, 3), dtype=np.uint8)):
                raise StreamError("a saída rejeitou o frame")
            if appsrc.emit("end-of-stream") != Gst.FlowReturn.OK:
                raise StreamError("a saída rejeitou EOS")
            message = self._output_bus.timed_pop_filtered(
                300 * Gst.MSECOND, Gst.MessageType.EOS | Gst.MessageType.ERROR,
            )
            if message is None:
                raise StreamError("EOS não chegou em até 300 ms")
            if message.type == Gst.MessageType.ERROR:
                raise StreamError(self._message_error(message, "saída virtual"))
        except Exception as exc:
            self._warning = f"Não foi possível confirmar o frame preto final na câmera virtual: {exc}"

    def _load_gstreamer(self) -> None:
        if self._Gst is not None:
            return
        try:
            import gi
            gi.require_version("Gst", "1.0")
            gi.require_version("GstVideo", "1.0")
            from gi.repository import Gst, GstVideo
        except (ImportError, ValueError) as exc:
            raise StreamError("GStreamer, GstVideo e os bindings Python são necessários") from exc
        Gst.init(None)
        self._Gst, self._GstVideo = Gst, GstVideo

    @staticmethod
    def _validate_video_node(path: str) -> None:
        if not isinstance(path, str) or not _VIDEO_NODE.fullmatch(path):
            raise StreamError("o dispositivo deve ter o formato /dev/videoN")

    @staticmethod
    def _validate_owned_loopback(path: str) -> None:
        fd = None
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
            capability = _Capability()
            fcntl.ioctl(fd, _VIDIOC_QUERYCAP, capability, True)
        except OSError as exc:
            raise StreamError(f"não foi possível consultar a saída virtual {path}: {exc}") from exc
        finally:
            if fd is not None:
                os.close(fd)
        driver = bytes(capability.driver).split(b"\0", 1)[0]
        card = bytes(capability.card).split(b"\0", 1)[0].strip()
        caps = capability.device_caps if capability.capabilities & _V4L2_CAP_DEVICE_CAPS else capability.capabilities
        if driver != b"v4l2 loopback" or not caps & _V4L2_CAP_VIDEO_OUTPUT:
            raise StreamError("a saída virtual precisa ser um dispositivo v4l2loopback de saída")
        if card not in _OWNED_LOOPBACK_CARDS:
            raise StreamError("a saída deve pertencer ao Enquadra ou ao Brio Control legado")

    def _start_source(self, device_path: str) -> None:
        Gst = self._Gst
        if self.synthetic:
            description = (
                f"videotestsrc is-live=true pattern=ball ! "
                f"video/x-raw,format=BGR,width={self.width},height={self.height},framerate=30/1 ! "
                "appsink name=video_sink emit-signals=true max-buffers=1 drop=true sync=false"
            )
        else:
            if not _VIDEO_NODE.fullmatch(device_path):
                raise StreamError("caminho de câmera inválido")
            description = (
                f"v4l2src name=physical_camera device={device_path} do-timestamp=true ! "
                "image/jpeg,width=1920,height=1080,framerate=30/1 ! jpegdec ! videoconvert ! "
                "video/x-raw,format=BGR ! "
                "appsink name=video_sink emit-signals=true max-buffers=1 drop=true sync=false"
            )
        try:
            pipeline = Gst.parse_launch(description)
        except Exception as exc:
            raise StreamError(f"pipeline de captura indisponível: {exc}") from exc
        sink = pipeline.get_by_name("video_sink")
        sink.connect("new-sample", self._on_new_sample)
        self._source_pipeline = pipeline
        self._source_bus = pipeline.get_bus()
        state = pipeline.set_state(Gst.State.PLAYING)
        if state == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            self._source_pipeline = self._source_bus = None
            raise StreamError("GStreamer não conseguiu abrir a fonte de vídeo")
        settled, current, _pending = pipeline.get_state(2 * Gst.SECOND)
        if settled == Gst.StateChangeReturn.FAILURE or current != Gst.State.PLAYING:
            message = self._source_bus.timed_pop_filtered(0, Gst.MessageType.ERROR)
            detail = self._message_error(message, "câmera física") if message is not None else "A câmera não iniciou em até 2 segundos. Ela pode estar ocupada."
            pipeline.set_state(Gst.State.NULL)
            self._source_pipeline = self._source_bus = None
            raise StreamError(detail)
        self._source_path = device_path

    def _on_new_sample(self, sink):
        Gst = self._Gst
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        try:
            frame = self._sample_to_bgr(sample)
            # Serialize publication with RPC configuration: an old unprocessed
            # frame must not be published after an effect has been enabled.
            with self._state_lock:
                output = self.processor.process(frame, self.width, self.height)
                timestamp = time.monotonic()
                with self._frame_lock:
                    self._output_frame = output
                    self._output_at = timestamp
                self._fps_times.append(timestamp)
                with self._preview_lock:
                    if self._preview is not None:
                        self._preview.write(self.width, self.height, output.tobytes())
                        self._last_preview_valid = True
            return Gst.FlowReturn.OK
        except Exception as exc:
            with self._state_lock:
                self._error = f"falha no processamento de frame: {exc}"
            return Gst.FlowReturn.ERROR

    def _sample_to_bgr(self, sample) -> np.ndarray:
        Gst, GstVideo = self._Gst, self._GstVideo
        caps = sample.get_caps()
        info = GstVideo.VideoInfo.new_from_caps(caps)
        width, height = int(info.width), int(info.height)
        stride, offset = int(info.stride[0]), int(info.offset[0])
        if width < 1 or height < 1 or stride < width * 3 or offset < 0:
            raise StreamError("GstVideoInfo retornou stride ou dimensões inválidas")
        if caps.get_structure(0).get_string("format") != "BGR":
            raise StreamError("appsink recebeu pixels em formato diferente de BGR")
        buffer = sample.get_buffer()
        mapped, mapping = buffer.map(Gst.MapFlags.READ)
        if not mapped:
            raise StreamError("não foi possível mapear o buffer GStreamer")
        try:
            required = offset + stride * (height - 1) + width * 3
            if required > mapping.size:
                raise StreamError("buffer GStreamer é menor que o stride informado")
            view = np.ndarray(
                (height, width, 3), dtype=np.uint8, buffer=mapping.data,
                offset=offset, strides=(stride, 3, 1),
            )
            return view.copy()
        finally:
            buffer.unmap(mapping)

    def _start_output(self, output_path: str) -> None:
        if self.width % 2:
            raise StreamError("a saída YUY2 requer largura par")
        Gst = self._Gst
        description = (
            f"appsrc name=processed_source is-live=true format=time do-timestamp=true block=false "
            f"max-buffers=2 max-bytes=0 caps=video/x-raw,format=BGR,width={self.width},height={self.height},framerate=30/1 ! "
            "queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream ! "
            f"videoconvert ! video/x-raw,format=YUY2,width={self.width},height={self.height},framerate=30/1 ! "
            f"v4l2sink device={output_path} sync=false"
        )
        try:
            pipeline = Gst.parse_launch(description)
        except Exception as exc:
            raise StreamError(f"pipeline da câmera virtual indisponível: {exc}") from exc
        self._output_pipeline = pipeline
        self._output_bus = pipeline.get_bus()
        self._appsrc = pipeline.get_by_name("processed_source")
        leaky_property = self._appsrc.find_property("leaky-type")
        if leaky_property is not None:
            self._appsrc.set_property("leaky-type", leaky_property.enum_class.DOWNSTREAM)
        state = pipeline.set_state(Gst.State.PLAYING)
        if state == Gst.StateChangeReturn.FAILURE:
            pipeline.set_state(Gst.State.NULL)
            self._output_pipeline = self._output_bus = self._appsrc = None
            raise StreamError("a câmera virtual não pode ser aberta; ela pode estar em uso")
        self._push_output(np.zeros((self.height, self.width, 3), dtype=np.uint8))
        settled, current, _pending = pipeline.get_state(2 * Gst.SECOND)
        if settled == Gst.StateChangeReturn.FAILURE or current != Gst.State.PLAYING:
            message = self._output_bus.pop_filtered(Gst.MessageType.ERROR)
            reason = self._message_error(message, "saída virtual") if message else "produtor não chegou a PLAYING"
            pipeline.set_state(Gst.State.NULL)
            self._output_pipeline = self._output_bus = self._appsrc = None
            raise StreamError(f"a câmera virtual não ficou pronta: {reason}")

    def _prepare_output_timeout(self, path: str) -> None:
        """Set the loopback timeout to 1s and remember a guarded restoration."""
        fd = None
        try:
            fd = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
            capability = _Capability()
            fcntl.ioctl(fd, _VIDIOC_QUERYCAP, capability, True)
            driver = bytes(capability.driver).split(b"\0", 1)[0]
            card = bytes(capability.card).split(b"\0", 1)[0].strip()
            if driver != b"v4l2 loopback" or card not in _OWNED_LOOPBACK_CARDS:
                raise StreamError("a saída virtual não pertence ao Enquadra ativo")
            control = self._find_control(fd, "timeout")
            if control is None:
                self._warning = "O módulo não expõe timeout; após falha abrupta a última imagem pode permanecer."
                return
            query = control
            if query.type != _V4L2_CTRL_TYPE_INTEGER or query.flags & (
                _V4L2_CTRL_FLAG_DISABLED | _V4L2_CTRL_FLAG_INACTIVE |
                _V4L2_CTRL_FLAG_READ_ONLY | _V4L2_CTRL_FLAG_WRITE_ONLY
            ):
                self._warning = "O controle timeout da câmera virtual não permite gravação segura."
                return
            ctrl = _Control(query.id, 0)
            fcntl.ioctl(fd, _VIDIOC_G_CTRL, ctrl, True)
            original = ctrl.value
            ctrl.value = 1000
            fcntl.ioctl(fd, _VIDIOC_S_CTRL, ctrl, True)
            fcntl.ioctl(fd, _VIDIOC_G_CTRL, ctrl, True)
            if ctrl.value != 1000:
                self._warning = "Não foi possível confirmar timeout de 1 segundo na câmera virtual."
                return
            stat = os.fstat(fd)
            self._timeout_restore = (path, stat.st_rdev, query.id, original)
        except StreamError:
            raise
        except OSError as exc:
            self._warning = f"Não foi possível configurar timeout da câmera virtual: {exc}"
        finally:
            if fd is not None:
                os.close(fd)

    @staticmethod
    def _find_control(fd: int, name: str) -> _QueryControl | None:
        query = _QueryControl()
        query.id = _V4L2_CTRL_FLAG_NEXT_CTRL
        while True:
            try:
                fcntl.ioctl(fd, _VIDIOC_QUERYCTRL, query, True)
            except OSError:
                return None
            label = bytes(query.name).split(b"\0", 1)[0].decode(errors="replace").lower()
            if label == name:
                return query
            query.id |= _V4L2_CTRL_FLAG_NEXT_CTRL

    def _restore_output_timeout(self) -> None:
        saved = self._timeout_restore
        self._timeout_restore = None
        if saved is None:
            return
        path, device_number, control_id, original = saved
        fd = None
        try:
            if os.stat(path).st_rdev != device_number:
                return
            fd = os.open(path, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
            current = _Control(control_id, 0)
            fcntl.ioctl(fd, _VIDIOC_G_CTRL, current, True)
            if current.value == 1000:
                current.value = original
                fcntl.ioctl(fd, _VIDIOC_S_CTRL, current, True)
        except OSError as exc:
            self._warning = f"O timeout temporário da câmera virtual não foi restaurado: {exc}"
        finally:
            if fd is not None:
                os.close(fd)

    def _tick_loop(self) -> None:
        period = _MODEL_FRAME_PERIOD
        next_tick = time.monotonic()
        black = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        while self.running and not self._stop_event.is_set():
            with self._state_lock:
                if not self._running or self._stop_event.is_set():
                    break
                now = time.monotonic()
                with self._frame_lock:
                    frame = self._output_frame
                    age = now - self._output_at
                if frame is None or age > 0.5:
                    frame = black
                    self._invalidate_preview()
                if self._appsrc is not None:
                    self._push_output(frame)
            next_tick += period
            self._stop_event.wait(max(0.0, next_tick - time.monotonic()))

    def _push_output(self, frame: np.ndarray) -> bool:
        Gst = self._Gst
        try:
            payload = frame.tobytes()
            buffer = Gst.Buffer.new_allocate(None, len(payload), None)
            buffer.fill(0, payload)
            buffer.pts = Gst.CLOCK_TIME_NONE
            buffer.dts = Gst.CLOCK_TIME_NONE
            buffer.duration = Gst.SECOND // 30
            result = self._appsrc.emit("push-buffer", buffer)
            if result != Gst.FlowReturn.OK and self.running:
                with self._state_lock:
                    self._error = f"câmera virtual rejeitou frame: {result.value_nick}"
            return result == Gst.FlowReturn.OK
        except Exception as exc:
            with self._state_lock:
                self._error = f"falha ao enviar frame à câmera virtual: {exc}"
            return False

    def _invalidate_preview(self) -> None:
        with self._preview_lock:
            if self._preview is not None and self._last_preview_valid:
                self._preview.invalidate()
                self._last_preview_valid = False

    def _monitor_loop(self) -> None:
        Gst = self._Gst
        monitor_thread = threading.current_thread()
        while self._monitor_is_active(monitor_thread):
            if self._source_bus is not None:
                message = self._source_bus.timed_pop_filtered(
                    100 * Gst.MSECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS
                )
                if message is not None:
                    if self.synthetic:
                        self._fatal_stream_error(
                            "a fonte sintética de vídeo terminou",
                            monitor_thread=threading.current_thread(),
                        )
                        return
                    self._source_lost(message, monitor_thread=monitor_thread)
            if not self._monitor_is_active(monitor_thread):
                return
            if self._output_bus is not None:
                message = self._output_bus.timed_pop_filtered(
                    0, Gst.MessageType.ERROR | Gst.MessageType.EOS
                )
                if message is not None:
                    self._fatal_stream_error(
                        self._message_error(message, "saída virtual"),
                        monitor_thread=threading.current_thread(),
                    )
                    return
            if self._source_pipeline is None and self._resolver is not None and time.monotonic() >= self._retry_at:
                self._retry_source(monitor_thread=monitor_thread)
            if self._source_bus is None:
                self._stop_event.wait(0.1)

    def _monitor_is_active(self, monitor_thread: threading.Thread) -> bool:
        with self._state_lock:
            return (
                monitor_thread is self._monitor_thread and self._running
                and not self._stop_event.is_set()
            )

    def _source_lost(self, message, *, monitor_thread: threading.Thread) -> None:
        with self._lifecycle_lock:
            if not self._monitor_is_active(monitor_thread):
                return
            with self._state_lock:
                self._error = self._message_error(message, "câmera física")
                self._retry_at = time.monotonic() + 2.0
                pipeline = self._source_pipeline
            if pipeline is not None:
                pipeline.set_state(self._Gst.State.NULL)
                pipeline.get_state(500 * self._Gst.MSECOND)
            with self._state_lock:
                self._source_pipeline = self._source_bus = None
                self._source_path = None
            with self._frame_lock:
                self._output_frame = None
                self._output_at = 0.0
            self._invalidate_preview()

    def _retry_source(self, *, monitor_thread: threading.Thread) -> None:
        with self._state_lock:
            if not self._monitor_is_active(monitor_thread) or self._resolver is None:
                return
            resolver = self._resolver
            self._retry_at = time.monotonic() + 2.0
        try:
            device_path = resolver()
            with self._lifecycle_lock:
                if not self._monitor_is_active(monitor_thread) or not device_path:
                    return
                self._validate_video_node(device_path)
                self._start_source(device_path)
                with self._state_lock:
                    self._error = None
        except Exception as exc:
            with self._lifecycle_lock:
                if self._monitor_is_active(monitor_thread):
                    with self._state_lock:
                        self._error = f"reconexão da mesma câmera falhou: {exc}"

    def _fatal_stream_error(
        self, message: str, *, monitor_thread: threading.Thread | None = None,
    ) -> None:
        with self._lifecycle_lock:
            if monitor_thread is not None and monitor_thread is not self._monitor_thread:
                return
            self._stop_locked()
            with self._state_lock:
                self._error = message

    @staticmethod
    def _message_error(message, source: str) -> str:
        if message.type == message.type.ERROR:
            error, debug = message.parse_error()
            return f"{source}: {error.message}"
        return f"{source}: fim inesperado da transmissão"

    def _cleanup(self) -> None:
        Gst = self._Gst
        with self._state_lock:
            pipelines = (self._source_pipeline, self._output_pipeline)
        for pipeline in pipelines:
            if pipeline is not None:
                pipeline.set_state(Gst.State.NULL)
                pipeline.get_state(500 * Gst.MSECOND)
        self.processor.close()
        with self._state_lock:
            self._source_pipeline = self._output_pipeline = None
            self._source_bus = self._output_bus = self._appsrc = None
        with self._frame_lock:
            self._output_frame = None
            self._output_at = 0.0
        self._invalidate_preview()
        with self._preview_lock:
            if self._preview is not None:
                self._preview.close()
                self._preview = None
        self._last_preview_valid = False
        self._restore_output_timeout()
        self._ticker_thread = self._monitor_thread = None
        self._resolver = None

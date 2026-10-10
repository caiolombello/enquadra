"""Digital crop and resize for the local Brio Control video pipeline."""

from __future__ import annotations

import hashlib
import math
import threading
import time
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from .background import BackgroundError, BackgroundProcessor


class FramingError(ValueError):
    """Invalid framing configuration or unavailable optional capability."""


Face = tuple[float, float, float, float]
Detector = Callable[[np.ndarray], Iterable[Face]]

# Official OpenCV Zoo LFS artifacts, pinned by immutable source commit and SHA-256.
# 2022mar: 7e062e54cf5410c09b795ff71b4a255e58498c79
# 2023mar: 47534e27c9851bb1128ccc0102f1145e27f23f98
_MODEL_INFO = {
    "2022mar": (
        "face_detection_yunet_2022mar.onnx",
        "50ef07f702a31741ca46a4c0d947773b64143b9362780237bf0d427d6c79bab7",
    ),
    "2023mar": (
        "face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
}


class FrameProcessor:
    """Crop/resize BGR frames, with an optional asynchronous face detector.

    Injected detectors receive an owned, 320x320 BGR copy and return boxes as
    normalized ``(x, y, width, height)`` coordinates. OpenCV is imported only
    when automatic framing is enabled.
    """

    def __init__(self, detector: Detector | None = None, segmenter=None):
        self._processing_lock = threading.RLock()
        self.background = BackgroundProcessor(segmenter)
        self._detector = detector
        self._autoframe = False
        self._zoom = 1.0
        self._pan_x = 0.0
        self._pan_y = 0.0
        self._mirror = False
        self._latest_input: tuple[np.ndarray, int, int, int, int] | None = None
        self._latest_face: Face | None = None
        self._latest_face_at: float | None = None
        self._selected_face: Face | None = None
        self._face_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._last_detection = -math.inf
        self._center = (0.5, 0.5)
        self._effective_zoom = 1.0
        self._last_process_time: float | None = None
        self._no_face_since: float | None = None
        self._opencv = None
        self._last_error: str | None = None

    @property
    def last_error(self) -> str | None:
        """Most recent persistent detector error, suitable for worker status."""
        return self._last_error

    @property
    def warning(self):
        with self._processing_lock:
            return self.background.warning

    def configure(self, **settings) -> None:
        with self._processing_lock:
            try:
                self._configure(**settings)
            except BackgroundError as exc:
                raise FramingError(str(exc)) from exc

    def _configure(
        self,
        *,
        autoframe: bool = False,
        zoom: float = 1.0,
        pan_x: float = 0.0,
        pan_y: float = 0.0,
        mirror: bool = False,
        background_mode: str = "off",
        background_image: str | None = None,
        background_blur: float = 12.0,
    ) -> None:
        try:
            zoom, pan_x, pan_y = float(zoom), float(pan_x), float(pan_y)
        except (TypeError, ValueError) as exc:
            raise FramingError("zoom e pan devem ser números") from exc
        if not math.isfinite(zoom) or not 1.0 <= zoom <= 4.0:
            raise FramingError("zoom deve estar entre 1 e 4")
        if not math.isfinite(pan_x) or not -1.0 <= pan_x <= 1.0:
            raise FramingError("pan_x deve estar entre -1 e 1")
        if not math.isfinite(pan_y) or not -1.0 <= pan_y <= 1.0:
            raise FramingError("pan_y deve estar entre -1 e 1")
        if not isinstance(autoframe, bool) or not isinstance(mirror, bool):
            raise FramingError("autoframe e mirror devem ser booleanos")

        if autoframe and self._detector is None:
            try:
                import cv2
            except ImportError as exc:
                raise FramingError(
                    "enquadramento automático indisponível: instale python3-opencv"
                ) from exc
            self._opencv = cv2
            self._detector = self._create_yunet_detector(cv2)
        self.background.configure(background_mode, background_image, background_blur)
        self._autoframe = autoframe
        self._zoom, self._pan_x, self._pan_y, self._mirror = zoom, pan_x, pan_y, mirror
        if not autoframe:
            self._effective_zoom = zoom
            self._center = (0.5, 0.5)
            self._no_face_since = None
            self._last_process_time = None
        if autoframe:
            self._start_detector()
        else:
            self._latest_input = None
            with self._face_lock:
                self._latest_face = None
                self._latest_face_at = None
                self._selected_face = None

    def process(self, frame, out_width, out_height, now=None) -> np.ndarray:
        with self._processing_lock:
            return self._process(frame, out_width, out_height, now)

    def _process(
        self,
        frame: np.ndarray,
        out_width: int,
        out_height: int,
        now: float | None = None,
    ) -> np.ndarray:
        if not isinstance(frame, np.ndarray) or frame.ndim != 3 or frame.shape[2] != 3:
            raise FramingError("frame deve ser um array BGR com três canais")
        if frame.dtype != np.uint8 or frame.shape[0] < 1 or frame.shape[1] < 1:
            raise FramingError("frame deve conter pixels uint8 válidos")
        if not isinstance(out_width, int) or not isinstance(out_height, int) or out_width < 1 or out_height < 1:
            raise FramingError("dimensões de saída devem ser inteiros positivos")

        timestamp = time.monotonic() if now is None else float(now)
        if not math.isfinite(timestamp):
            raise FramingError("now deve ser finito")
        self._submit_latest(frame)
        center, zoom = self._tracking_state(timestamp, frame.shape[1], frame.shape[0], out_width / out_height)
        crop = self._crop(frame, out_width, out_height, center, zoom)
        resized = self._resize(crop, out_width, out_height)
        resized = self.background.process(resized)
        if self._mirror:
            resized = resized[:, ::-1]
        return np.ascontiguousarray(resized, dtype=np.uint8)

    def close(self) -> None:
        self._stop_detector()
        self._autoframe = False
        self._latest_face = None
        self._latest_face_at = None
        self._latest_input = None

    def _stop_detector(self) -> None:
        self._stop.set()
        worker = self._worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=1.0)
        self._worker = None

    def _submit_latest(self, frame: np.ndarray) -> None:
        if not self._autoframe:
            return
        timestamp = time.monotonic()
        if timestamp - self._last_detection < 0.1:
            return
        self._last_detection = timestamp
        height, width = frame.shape[:2]
        scale = min(320 / width, 320 / height)
        scaled_width, scaled_height = max(1, round(width * scale)), max(1, round(height * scale))
        resized = self._resize(frame, scaled_width, scaled_height)
        pad_x, pad_y = (320 - scaled_width) // 2, (320 - scaled_height) // 2
        canvas = np.zeros((320, 320, 3), dtype=np.uint8)
        canvas[pad_y:pad_y + scaled_height, pad_x:pad_x + scaled_width] = resized
        self._latest_input = (canvas.copy(), scaled_width, scaled_height, pad_x, pad_y)

    def _start_detector(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        self._stop.clear()
        self._worker = threading.Thread(target=self._detector_loop, name="face-detector", daemon=True)
        self._worker.start()

    def _detector_loop(self) -> None:
        while not self._stop.is_set():
            item, self._latest_input = self._latest_input, None
            if item is None:
                self._stop.wait(0.01)
                continue
            frame, scaled_width, scaled_height, pad_x, pad_y = item
            inference_error = None
            try:
                faces = list(self._detector(frame)) if self._detector else []
                valid = []
                for face in faces:
                    if not self._valid_face(face):
                        continue
                    x, y, width, height = face
                    mapped = (
                        (x * 320 - pad_x) / scaled_width,
                        (y * 320 - pad_y) / scaled_height,
                        width * 320 / scaled_width,
                        height * 320 / scaled_height,
                    )
                    clipped = self._clip_face(mapped)
                    if clipped is not None:
                        valid.append(clipped)
                chosen = self._stable_choice(valid)
            except Exception as exc:
                chosen = None
                inference_error = f"YuNet inference failed: {exc}"
            with self._face_lock:
                self._latest_face = chosen
                self._latest_face_at = time.monotonic()
                self._last_error = inference_error

    def _stable_choice(self, faces: list[Face]) -> Face | None:
        largest = max(faces, key=lambda face: face[2] * face[3], default=None)
        previous = self._selected_face
        if largest is None:
            self._selected_face = None
            return None
        # Keep the current target through small detector jitters. Switch only
        # when a candidate overlaps it and remains substantial, or a new face
        # is clearly larger (at least 1.8x its area).
        if previous is not None:
            previous_area = previous[2] * previous[3]
            largest_area = largest[2] * largest[3]
            matching = max(faces, key=lambda face: self._intersection_over_union(previous, face), default=None)
            overlap = self._intersection_over_union(previous, matching) if matching else 0.0
            matching_area = matching[2] * matching[3] if matching else 0.0
            if matching is not None and overlap >= 0.12 and largest_area < matching_area * 1.8:
                largest = tuple(old * 0.65 + new * 0.35 for old, new in zip(previous, matching))
        self._selected_face = largest
        return largest

    @staticmethod
    def _intersection_over_union(first: Face, second: Face) -> float:
        left, top = max(first[0], second[0]), max(first[1], second[1])
        right = min(first[0] + first[2], second[0] + second[2])
        bottom = min(first[1] + first[3], second[1] + second[3])
        intersection = max(0.0, right - left) * max(0.0, bottom - top)
        union = first[2] * first[3] + second[2] * second[3] - intersection
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def _valid_face(face: Face) -> bool:
        return len(face) == 4 and all(math.isfinite(float(value)) for value in face) and face[2] > 0 and face[3] > 0

    @staticmethod
    def _clip_face(face: Face) -> Face | None:
        x, y, width, height = face
        left, top = max(0.0, x), max(0.0, y)
        right, bottom = min(1.0, x + width), min(1.0, y + height)
        if right <= left or bottom <= top:
            return None
        return left, top, right - left, bottom - top

    def _tracking_state(
        self, now: float, frame_width: int, frame_height: int, output_aspect: float
    ) -> tuple[tuple[float, float], float]:
        with self._face_lock:
            face = self._latest_face
            face_at = self._latest_face_at
        if face_at is None or time.monotonic() - face_at > 0.35:
            face = None
        if self._autoframe and face is not None:
            self._no_face_since = None
            target = (face[0] + face[2] / 2, face[1] + face[3] / 2)
            base_crop_height = min(1.0, frame_width / output_aspect / frame_height)
            target_zoom = max(self._zoom, 0.30 * base_crop_height / face[3])
            target_zoom = min(4.0, target_zoom)
        else:
            if self._no_face_since is None:
                self._no_face_since = now
            target = self._center
            target_zoom = self._effective_zoom
            if self._autoframe and now - self._no_face_since >= 2.0:
                target = (0.5, 0.5)
                target_zoom = self._zoom

        if self._last_process_time is not None and now < self._last_process_time:
            self._last_process_time = None
            self._no_face_since = now
        elapsed = 0.0 if self._last_process_time is None else max(0.0, now - self._last_process_time)
        self._last_process_time = now
        alpha = 1.0 - math.exp(-elapsed / 0.22) if elapsed else 1.0
        self._center = tuple(current + (desired - current) * alpha for current, desired in zip(self._center, target))
        self._effective_zoom += (target_zoom - self._effective_zoom) * alpha
        return (
            (self._center[0] + self._pan_x * 0.5, self._center[1] + self._pan_y * 0.5),
            self._effective_zoom,
        )

    def _crop(
        self, frame: np.ndarray, out_width: int, out_height: int,
        center: tuple[float, float], zoom: float,
    ) -> np.ndarray:
        height, width = frame.shape[:2]
        aspect = out_width / out_height
        if width / height > aspect:
            crop_height = height / zoom
            crop_width = crop_height * aspect
        else:
            crop_width = width / zoom
            crop_height = crop_width / aspect
        crop_width, crop_height = max(1.0, crop_width), max(1.0, crop_height)
        max_left, max_top = width - crop_width, height - crop_height
        left = min(max(center[0] * width - crop_width / 2, 0.0), max_left)
        top = min(max(center[1] * height - crop_height / 2, 0.0), max_top)
        x0, y0 = int(round(left)), int(round(top))
        x1, y1 = min(width, max(x0 + 1, int(round(left + crop_width)))), min(height, max(y0 + 1, int(round(top + crop_height))))
        return frame[y0:y1, x0:x1]

    def _resize(self, frame: np.ndarray, width: int, height: int) -> np.ndarray:
        cv2 = self._opencv
        if cv2 is None and self.background.mode != "off":
            cv2 = self.background._opencv
        if cv2 is not None:
            return cv2.resize(frame, (width, height), interpolation=cv2.INTER_LINEAR)
        y = np.minimum((np.arange(height) * frame.shape[0] / height).astype(np.intp), frame.shape[0] - 1)
        x = np.minimum((np.arange(width) * frame.shape[1] / width).astype(np.intp), frame.shape[1] - 1)
        return frame[y[:, None], x[None, :]]

    @staticmethod
    def _create_yunet_detector(cv2):
        version = tuple(int(part) for part in cv2.__version__.split(".")[:2])
        if version < (4, 5):
            raise FramingError("python3-opencv 4.5 ou superior é necessário para YuNet")
        model_key = "2022mar" if version < (4, 8) else "2023mar"
        model_name, expected_hash = _MODEL_INFO[model_key]
        model_path = Path(__file__).with_name("models") / model_name
        try:
            digest = hashlib.sha256(model_path.read_bytes()).hexdigest()
        except OSError as exc:
            raise FramingError(f"modelo YuNet ausente: {model_name}") from exc
        if digest != expected_hash:
            raise FramingError(f"hash inválido para o modelo YuNet {model_name}")
        if not hasattr(cv2, "FaceDetectorYN"):
            raise FramingError("esta compilação do OpenCV não oferece FaceDetectorYN")
        try:
            detector = cv2.FaceDetectorYN.create(
                str(model_path), "", (320, 320), 0.7, 0.3, 5000,
                cv2.dnn.DNN_BACKEND_OPENCV, cv2.dnn.DNN_TARGET_CPU,
            )
        except Exception as exc:
            raise FramingError(f"não foi possível carregar YuNet para OpenCV {cv2.__version__}: {exc}") from exc

        def detect(image: np.ndarray) -> list[Face]:
            detector.setInputSize((320, 320))
            _, rows = detector.detect(image)
            if rows is None:
                return []
            return [
                (float(row[0]) / 320, float(row[1]) / 320,
                 float(row[2]) / 320, float(row[3]) / 320)
                for row in rows
            ]

        return detect

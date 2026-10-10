"""Local CPU person segmentation and background compositing."""

from __future__ import annotations

import hashlib
import math
import os
import stat
from pathlib import Path

import numpy as np

_MODEL_INFO = {'pphumanseg': ('pphumanseg/human_segmentation_pphumanseg_2023mar.onnx', '552d8a984054e59b5d773d24b9b12022b22046ceb2bbc4c9aaeaceb36a9ddf24')}
_MODEL_SIZE = 6163938
_MAX_IMAGE_BYTES = 20 * 1024 * 1024


class BackgroundError(ValueError):
    """Invalid background settings or unavailable local capability."""


def validate_settings(mode, image, blur):
    if mode not in ('off', 'blur', 'image'):
        raise BackgroundError('Modo de fundo inválido.')
    if image is not None and (not isinstance(image, str) or not Path(image).is_absolute()):
        raise BackgroundError('A imagem de fundo deve ter um caminho local absoluto.')
    if type(blur) not in (int, float) or not 1 <= blur <= 30 or not math.isfinite(blur):
        raise BackgroundError('O desfoque deve estar entre 1 e 30.')
    if mode == 'image' and image is None:
        raise BackgroundError('Escolha uma imagem local para o fundo.')


class BackgroundProcessor:
    """Receives the current BGR frame and returns a composited owned frame.

    An injected segmenter returns a 2D float person alpha in [0, 1]. The
    owning FrameProcessor serializes configuration and inference.
    """

    def __init__(self, segmenter=None):
        self.mode = 'off'
        self.image_path = None
        self.blur = 12.0
        self.warning = None
        self._segmenter = segmenter
        self._opencv = None
        self._image = None
        self._covered = None
        self._covered_size = None

    def configure(self, mode='off', image=None, blur=12.0):
        validate_settings(mode, image, blur)
        candidate_image = self._image if image == self.image_path else None
        segmenter = self._segmenter
        if mode != 'off':
            cv2 = self._load_opencv()
            if mode == 'image' and candidate_image is None:
                candidate_image = self._read_image(image, cv2)
            if segmenter is None:
                segmenter = self._create_segmenter(cv2)
        # Commit only after every resource has been validated successfully.
        self._segmenter = segmenter
        self._image = candidate_image
        if image != self.image_path:
            self._covered = self._covered_size = None
        self.mode, self.image_path, self.blur = mode, image, float(blur)
        self.warning = None

    def _load_opencv(self):
        if self._opencv is None:
            try:
                import cv2
            except ImportError as exc:
                raise BackgroundError('Efeitos de fundo indisponíveis: instale python3-opencv.') from exc
            self._opencv = cv2
        return self._opencv

    @staticmethod
    def _read_image(path, cv2):
        try:
            # Nonblocking prevents a replaced path/FIFO from blocking RPC.
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_CLOEXEC', 0))
            with os.fdopen(fd, 'rb') as source:
                info = os.fstat(source.fileno())
                if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= _MAX_IMAGE_BYTES:
                    raise BackgroundError('A imagem deve ser um arquivo regular de até 20 MiB.')
                payload = source.read(_MAX_IMAGE_BYTES + 1)
        except OSError as exc:
            raise BackgroundError(f'Não foi possível ler a imagem de fundo: {exc}') from exc
        if len(payload) > _MAX_IMAGE_BYTES:
            raise BackgroundError('A imagem deve ter até 20 MiB.')
        if not (payload.startswith(b'\x89PNG\r\n\x1a\n') or payload.startswith(b'\xff\xd8\xff')):
            raise BackgroundError('Escolha uma imagem PNG ou JPEG válida.')
        # Check header dimensions before decoding to bound decompression memory.
        width, height = BackgroundProcessor._image_dimensions(payload)
        if not (0 < width <= 4096 and 0 < height <= 4096 and width * height <= 16_000_000):
            raise BackgroundError('A imagem deve ter até 4096 pixels por dimensão e 16 megapixels.')
        image = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None or image.shape[:2] not in ((height, width), (width, height)):
            raise BackgroundError('Não foi possível decodificar a imagem PNG/JPEG.')
        return image

    @staticmethod
    def _image_dimensions(payload):
        if payload.startswith(b'\x89PNG\r\n\x1a\n'):
            if len(payload) < 24 or payload[12:16] != b'IHDR':
                raise BackgroundError('Cabeçalho PNG inválido.')
            return int.from_bytes(payload[16:20], 'big'), int.from_bytes(payload[20:24], 'big')
        offset = 2
        while offset < len(payload):
            if payload[offset] != 255:
                break
            while offset < len(payload) and payload[offset] == 255:
                offset += 1
            if offset >= len(payload):
                break
            marker = payload[offset]
            offset += 1
            if marker in (0x01, *range(0xd0, 0xd9)):
                continue
            if offset + 2 > len(payload):
                break
            length = int.from_bytes(payload[offset:offset + 2], 'big')
            if length < 2 or offset + length > len(payload):
                break
            if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf) and length >= 8:
                return int.from_bytes(payload[offset + 5:offset + 7], 'big'), int.from_bytes(payload[offset + 3:offset + 5], 'big')
            offset += length
        raise BackgroundError('Cabeçalho JPEG inválido.')

    @staticmethod
    def _create_segmenter(cv2):
        name, expected_hash = _MODEL_INFO['pphumanseg']
        path = Path(__file__).with_name('models') / name
        try:
            if path.stat().st_size != _MODEL_SIZE:
                raise BackgroundError('Tamanho inválido do modelo de segmentação.')
            payload = path.read_bytes()
        except OSError as exc:
            raise BackgroundError('Modelo local de segmentação ausente.') from exc
        if hashlib.sha256(payload).hexdigest() != expected_hash:
            raise BackgroundError('Hash inválido do modelo de segmentação.')
        try:
            net = cv2.dnn.readNetFromONNX(np.frombuffer(payload, dtype=np.uint8))
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        except Exception as exc:
            raise BackgroundError(f'Não foi possível carregar o modelo de segmentação: {exc}') from exc

        def segment(frame):
            rgb = cv2.cvtColor(cv2.resize(frame, (192, 192)), cv2.COLOR_BGR2RGB)
            normalized = (rgb.astype(np.float32) / 255.0 - 0.5) / 0.5
            net.setInput(normalized.transpose(2, 0, 1)[None])
            output = np.asarray(net.forward())
            if output.shape != (1, 2, 192, 192) or not np.isfinite(output).all():
                raise BackgroundError('Saída inválida do modelo de segmentação.')
            if output.min() < 0 or output.max() > 1:
                raise BackgroundError('Probabilidades inválidas de segmentação.')
            total = output[0].sum(axis=0)
            if not np.allclose(total, 1, atol=0.02):
                raise BackgroundError('Probabilidades não normalizadas de segmentação.')
            return output[0, 1] / total
        return segment

    def _background(self, frame):
        cv2 = self._opencv
        height, width = frame.shape[:2]
        if self.mode == 'image':
            size = (width, height)
            if self._covered_size != size:
                source = self._image
                ih, iw = source.shape[:2]
                if iw / ih > width / height:
                    crop_width = max(1, min(iw, round(ih * width / height)))
                    left = (iw - crop_width) // 2
                    source = source[:, left:left + crop_width]
                else:
                    crop_height = max(1, min(ih, round(iw * height / width)))
                    top = (ih - crop_height) // 2
                    source = source[top:top + crop_height]
                self._covered = cv2.resize(source, size, interpolation=cv2.INTER_LINEAR)
                self._covered_size = size
            return self._covered
        scale = min(1.0, 320 / width, 180 / height)
        small = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))))
        blurred = cv2.GaussianBlur(small, (0, 0), max(0.5, self.blur * scale))
        return cv2.resize(blurred, (width, height))

    def process(self, frame):
        if self.mode == 'off':
            return frame
        try:
            background = self._background(frame)
        except Exception as exc:
            self.warning = f'Efeito de fundo indisponível; quadro ocultado: {exc}'
            return np.zeros_like(frame)
        try:
            alpha = np.asarray(self._segmenter(frame), dtype=np.float32)
            if alpha.ndim != 2 or not alpha.size or not np.isfinite(alpha).all() or alpha.min() < 0 or alpha.max() > 1:
                raise BackgroundError('Máscara de pessoa inválida.')
            alpha = np.ascontiguousarray(self._opencv.resize(
                alpha, (frame.shape[1], frame.shape[0]), interpolation=self._opencv.INTER_LINEAR,
            ), dtype=np.float32)
            result = self._opencv.blendLinear(frame, background, alpha, 1 - alpha)
            self.warning = None
            return result
        except Exception as exc:
            self.warning = f'Falha na segmentação; fundo aplicado ao quadro inteiro: {exc}'
            return background.copy()

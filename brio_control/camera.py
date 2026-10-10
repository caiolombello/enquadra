"""Controle mínimo V4L2/UVC para a Logitech Brio 500.

A modelagem dos ioctls UVC/V4L2 foi conferida no cameractrls (LGPL-3.0-or-later),
commit 6f388257ac21a0e91b143ad11cb2457036fa2c27; este módulo não importa o
backend nem copia a classe de aplicação do projeto.
"""

from __future__ import annotations

import ctypes
import fcntl
import os
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class CameraError(Exception):
    """Erro de descoberta, acesso ou controle da câmera."""


BRIO_500_USB_ID = "046d:0943"
BRIO_XU_GUID = bytes.fromhex("1502e44934f4fe47b1580e885023e51b")
UVC_GET_CUR = 0x81
UVC_SET_CUR = 0x01
UVC_GET_MIN = 0x82
UVC_GET_MAX = 0x83
UVC_GET_LEN = 0x85
UVC_GET_INFO = 0x86
UVC_GET_DEF = 0x87
# UVC GET_INFO: bit 0 permits GET, bit 1 permits SET.
UVC_CTRL_FLAG_GET_CUR = 1 << 0
UVC_CTRL_FLAG_SET_CUR = 1 << 1
VIDIOC_QUERYCAP = 0x80685600
V4L2_CAP_VIDEO_CAPTURE = 0x00000001
V4L2_CAP_DEVICE_CAPS = 0x80000000
VIDIOC_G_CTRL = 0xC008561B
VIDIOC_S_CTRL = 0xC008561C
VIDIOC_QUERYCTRL = 0xC0445624
VIDIOC_QUERYMENU = 0xC02C5625
V4L2_CTRL_TYPE_INTEGER = 1
V4L2_CTRL_TYPE_BOOLEAN = 2
V4L2_CTRL_TYPE_MENU = 3
V4L2_CTRL_FLAG_DISABLED = 0x0001
V4L2_CTRL_FLAG_GRABBED = 0x0002
V4L2_CTRL_FLAG_READ_ONLY = 0x0004
V4L2_CTRL_FLAG_WRITE_ONLY = 0x0040
V4L2_CTRL_FLAG_INACTIVE = 0x0010
V4L2_CTRL_FLAG_NEXT_CTRL = 0x80000000


class _V4L2Capability(ctypes.Structure):
    _fields_ = (
        ("driver", ctypes.c_char * 16),
        ("card", ctypes.c_char * 32),
        ("bus_info", ctypes.c_char * 32),
        ("version", ctypes.c_uint32),
        ("capabilities", ctypes.c_uint32),
        ("device_caps", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 3),
    )


class _V4L2QueryCtrl(ctypes.Structure):
    _fields_ = (
        ("id", ctypes.c_uint32), ("type", ctypes.c_uint32),
        ("name", ctypes.c_uint8 * 32), ("minimum", ctypes.c_int32),
        ("maximum", ctypes.c_int32), ("step", ctypes.c_int32),
        ("default_value", ctypes.c_int32), ("flags", ctypes.c_uint32),
        ("reserved", ctypes.c_uint32 * 2),
    )


class _V4L2Control(ctypes.Structure):
    _fields_ = (("id", ctypes.c_uint32), ("value", ctypes.c_int32))


class _V4L2QueryMenu(ctypes.Structure):
    _pack_ = 1
    _fields_ = (("id", ctypes.c_uint32), ("index", ctypes.c_uint32),
                ("name", ctypes.c_uint8 * 32), ("reserved", ctypes.c_uint32))


@dataclass(frozen=True)
class Device:
    path: str
    name: str
    usb_id: str
    identity: str
    xu_unit: int
    xu_selectors: frozenset[int] = field(default_factory=frozenset)


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def _usb_ancestors(video_path: Path) -> list[Path]:
    current = video_path.resolve()
    result = [current]
    result.extend(current.parents)
    return result


def _usb_metadata(video_path: Path) -> tuple[str, str, bytes | None, str]:
    vendor = product = serial = ""
    descriptors: bytes | None = None
    ancestry = ""
    for parent in _usb_ancestors(video_path):
        vendor = vendor or _read_text(parent / "idVendor")
        product = product or _read_text(parent / "idProduct")
        serial = serial or _read_text(parent / "serial")
        if descriptors is None:
            try:
                descriptors = (parent / "descriptors").read_bytes()
            except OSError:
                pass
        if vendor and product and not ancestry:
            ancestry = str(parent)
        if vendor and product and descriptors is not None:
            break
    return f"{vendor.lower()}:{product.lower()}", serial, descriptors, ancestry


def _find_xu(descriptors: bytes | None) -> tuple[int, frozenset[int]] | None:
    if not descriptors:
        return None
    pos = 0
    while pos + 3 <= len(descriptors):
        length = descriptors[pos]
        if length < 3 or pos + length > len(descriptors):
            break
        if descriptors[pos + 1] == 0x24 and descriptors[pos + 2] == 0x06:
            if length >= 24 and descriptors[pos + 4 : pos + 20] == BRIO_XU_GUID:
                unit = descriptors[pos + 3]
                pins = descriptors[pos + 21]
                controls_size_offset = 22 + pins
                if controls_size_offset < length:
                    control_size = descriptors[pos + controls_size_offset]
                    bitmap_start = pos + controls_size_offset + 1
                    bitmap_end = min(pos + length, bitmap_start + control_size)
                    selectors = frozenset(
                        byte_index * 8 + bit + 1
                        for byte_index, value in enumerate(descriptors[bitmap_start:bitmap_end])
                        for bit in range(8)
                        if value & (1 << bit)
                    )
                    return unit, selectors
        pos += length
    return None


def _query_capability(fd: int) -> _V4L2Capability:
    cap = _V4L2Capability()
    try:
        ioctl(fd, VIDIOC_QUERYCAP, cap)
    except OSError as exc:
        raise CameraError(f"VIDIOC_QUERYCAP falhou: {exc}") from exc
    effective_caps = (cap.device_caps if cap.capabilities & V4L2_CAP_DEVICE_CAPS
                      else cap.capabilities)
    if not (effective_caps & V4L2_CAP_VIDEO_CAPTURE):
        raise CameraError("nó não anuncia V4L2_CAP_VIDEO_CAPTURE")
    return cap


def discover_devices(sys_root: str = "/sys/class/video4linux") -> list[Device]:
    devices: list[Device] = []
    root = Path(sys_root)
    try:
        entries = sorted(root.glob("video*"))
    except OSError as exc:
        raise CameraError(f"não foi possível listar {root}: {exc}") from exc
    for entry in entries:
        path = Path("/dev") / entry.name
        usb_id, serial, descriptors, ancestry = _usb_metadata(entry / "device")
        if usb_id != BRIO_500_USB_ID:
            continue
        xu = _find_xu(descriptors)
        if xu is None:
            continue
        try:
            fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
            try:
                cap = _query_capability(fd)
            finally:
                os.close(fd)
        except (OSError, CameraError):
            continue
        card = bytes(cap.card).split(b"\0", 1)[0].decode(errors="replace")
        identity = serial or ancestry
        devices.append(Device(str(path), _read_text(entry / "name") or card or entry.name,
                              usb_id, identity, xu[0], xu[1]))
    return devices


def ioctl(fd: int, request: int, arg: Any, mutate: bool = True) -> Any:
    return fcntl.ioctl(fd, request, arg, mutate)


class Camera(AbstractContextManager["Camera"]):
    def __init__(self, device: Device, writable: bool = False):
        self.device = device
        self.writable = writable
        self.fd: int | None = None
        self._lock = threading.RLock()

    def _validate_identity(self) -> None:
        sys_entry = Path("/sys/class/video4linux") / Path(self.device.path).name
        if not sys_entry.exists():
            return
        usb_id, serial, descriptors, ancestry = _usb_metadata(sys_entry / "device")
        xu = _find_xu(descriptors)
        if (usb_id != BRIO_500_USB_ID or xu is None or xu[0] != self.device.xu_unit or
                xu[1] != self.device.xu_selectors or
                (self.device.identity and (serial or ancestry) != self.device.identity)):
            raise CameraError("identidade USB da câmera mudou desde a descoberta")

    def __enter__(self) -> "Camera":
        if self.fd is not None:
            return self
        flags = (os.O_RDWR if self.writable else os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0)
        try:
            self._validate_identity()
            self.fd = os.open(self.device.path, flags)
        except OSError as exc:
            raise CameraError(f"não foi possível abrir {self.device.path}: {exc}") from exc
        try:
            _query_capability(self.fd)
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self.fd is not None:
                fd, self.fd = self.fd, None
                try:
                    os.close(fd)
                except OSError:
                    pass

    def _require_open(self) -> int:
        if self.fd is None:
            raise CameraError("câmera não está aberta")
        return self.fd

    def read(self, selector: int, query: int = UVC_GET_CUR, size: int = 1) -> bytes:
        if not 0 <= selector <= 255 or size <= 0:
            raise CameraError("selector/size inválido")
        with self._lock:
            fd = self._require_open()
            data = (ctypes.c_uint8 * size)()
            request = _UvcQuery(self.device.xu_unit, selector, query, size, data)
            try:
                ioctl(fd, UVCIOC_CTRL_QUERY, request)
            except OSError as exc:
                raise CameraError(f"UVC query selector {selector} falhou: {exc}") from exc
            return bytes(data)

    def capabilities(self, selector: int) -> dict[str, int]:
        info = self.read(selector, UVC_GET_INFO, 1)[0]
        length = int.from_bytes(self.read(selector, UVC_GET_LEN, 2), "little")
        if length <= 0 or length > 4:
            raise CameraError("comprimento XU inválido")
        values = {}
        for name, query in (("minimum", UVC_GET_MIN), ("maximum", UVC_GET_MAX),
                            ("current", UVC_GET_CUR), ("default", UVC_GET_DEF)):
            raw = self.read(selector, query, length)
            values[name] = int.from_bytes(raw, "little", signed=False)
        return {"info": info, "length": length, **values}

    def write(self, selector: int, value: int) -> int:
        if not self.writable:
            raise CameraError("câmera aberta somente para leitura")
        if self.device.usb_id.lower() != BRIO_500_USB_ID or self.device.xu_unit <= 0:
            raise CameraError("dispositivo/XU não corresponde à Brio 500")
        if selector not in self.device.xu_selectors:
            raise CameraError("selector não anunciado pela XU")
        allowed = {5: {0, 1, 2}, 13: {0, 1}}
        if selector not in allowed or value not in allowed[selector]:
            raise CameraError("selector ou valor não permitido")
        with self._lock:
            caps = self.capabilities(selector)
            if caps["length"] != 1 or not (caps["info"] & UVC_CTRL_FLAG_GET_CUR) or not (caps["info"] & UVC_CTRL_FLAG_SET_CUR):
                raise CameraError("XU não anuncia GET/SET de um byte")
            if selector == 13 and (caps["minimum"], caps["maximum"]) != (0, 1):
                raise CameraError("limites XU incompatíveis para selector candidato")
            if selector == 13 and caps["current"] not in {0, 1}:
                raise CameraError("valor corrente inválido para selector candidato")
            if caps["minimum"] > min(allowed[selector]) or caps["maximum"] < max(allowed[selector]):
                raise CameraError("limites XU incompatíveis")
            fd = self._require_open()
            data = (ctypes.c_uint8 * 1)(value)
            request = _UvcQuery(self.device.xu_unit, selector, UVC_SET_CUR, 1, data)
            try:
                ioctl(fd, UVCIOC_CTRL_QUERY, request)
            except OSError as exc:
                raise CameraError(f"UVC write selector {selector} falhou: {exc}") from exc
            readback = self.read(selector, UVC_GET_CUR, 1)[0]
            if readback != value:
                raise CameraError("readback XU divergente")
            return readback

    def read_control(self, control_id: int) -> int:
        """Lê um controle V4L2 padrão sem exigir abertura gravável."""
        if not isinstance(control_id, int) or control_id < 0 or control_id > 0xFFFFFFFF:
            raise CameraError("id de controle inválido")
        with self._lock:
            fd = self._require_open()
            control = _V4L2Control(control_id, 0)
            try:
                ioctl(fd, VIDIOC_G_CTRL, control)
            except OSError as exc:
                raise CameraError(f"VIDIOC_G_CTRL {control_id:#x} falhou: {exc}") from exc
            return int(control.value)

    @staticmethod
    def _control_name(raw: ctypes.Array[ctypes.c_uint8]) -> str:
        return bytes(raw).split(b"\0", 1)[0].decode(errors="replace")

    def _query_control(self, control_id: int) -> _V4L2QueryCtrl:
        query = _V4L2QueryCtrl(id=control_id)
        try:
            ioctl(self._require_open(), VIDIOC_QUERYCTRL, query)
        except OSError as exc:
            raise CameraError(f"VIDIOC_QUERYCTRL {control_id:#x} falhou: {exc}") from exc
        return query

    def _menu_options(self, query: _V4L2QueryCtrl) -> list[dict[str, object]]:
        options: list[dict[str, object]] = []
        for index in range(query.minimum, query.maximum + 1):
            menu = _V4L2QueryMenu(id=query.id, index=index)
            try:
                ioctl(self._require_open(), VIDIOC_QUERYMENU, menu)
            except OSError:
                continue
            options.append({"value": index, "label": self._control_name(menu.name)})
        return options

    def enumerate_controls(self) -> list[dict[str, object]]:
        """Enumera controles integer/boolean/menu expostos pelo driver."""
        with self._lock:
            controls: list[dict[str, object]] = []
            next_id = V4L2_CTRL_FLAG_NEXT_CTRL
            seen: set[int] = set()
            while True:
                try:
                    query = self._query_control(next_id)
                except CameraError:
                    break
                if query.id in seen:
                    break
                seen.add(query.id)
                next_id = query.id | V4L2_CTRL_FLAG_NEXT_CTRL
                if query.type not in (V4L2_CTRL_TYPE_INTEGER, V4L2_CTRL_TYPE_BOOLEAN,
                                      V4L2_CTRL_TYPE_MENU):
                    continue
                active = not bool(query.flags & (V4L2_CTRL_FLAG_DISABLED | V4L2_CTRL_FLAG_INACTIVE))
                writable = active and not bool(query.flags & (V4L2_CTRL_FLAG_GRABBED |
                                                              V4L2_CTRL_FLAG_READ_ONLY |
                                                              V4L2_CTRL_FLAG_WRITE_ONLY))
                kind = {V4L2_CTRL_TYPE_INTEGER: "integer", V4L2_CTRL_TYPE_BOOLEAN: "boolean",
                        V4L2_CTRL_TYPE_MENU: "menu"}[query.type]
                options = self._menu_options(query) if query.type == V4L2_CTRL_TYPE_MENU else []
                value: int | None = None
                if active:
                    try:
                        value = self.read_control(query.id)
                    except CameraError:
                        pass
                controls.append({
                    "id": hex(query.id), "name": self._control_name(query.name),
                    "kind": kind, "minimum": int(query.minimum),
                    "maximum": int(query.maximum), "step": int(query.step),
                    "default": int(query.default_value), "value": value,
                    "active": active, "writable": writable, "options": options,
                })
            return controls

    def set_control(self, control_id: int, value: int) -> int:
        """Define controle V4L2 padrão e retorna o valor lido após a escrita."""
        if not self.writable:
            raise CameraError("câmera aberta somente para leitura")
        if not isinstance(value, int):
            raise CameraError("valor de controle inválido")
        with self._lock:
            query = self._query_control(control_id)
            if query.flags & (V4L2_CTRL_FLAG_DISABLED | V4L2_CTRL_FLAG_INACTIVE |
                              V4L2_CTRL_FLAG_READ_ONLY | V4L2_CTRL_FLAG_WRITE_ONLY):
                raise CameraError("controle inativo ou sem escrita")
            if query.type == V4L2_CTRL_TYPE_MENU:
                if not any(option["value"] == value for option in self._menu_options(query)):
                    raise CameraError("opção de menu inválida")
            elif value < query.minimum or value > query.maximum:
                raise CameraError("valor fora do intervalo")
            elif query.type == V4L2_CTRL_TYPE_BOOLEAN and value not in (0, 1):
                raise CameraError("booleano inválido")
            elif query.step > 0 and (value - query.minimum) % query.step:
                raise CameraError("valor não respeita o passo")
            control = _V4L2Control(control_id, value)
            try:
                ioctl(self._require_open(), VIDIOC_S_CTRL, control)
            except OSError as exc:
                raise CameraError(f"VIDIOC_S_CTRL {control_id:#x} falhou: {exc}") from exc
            readback = self.read_control(control_id)
            if readback != value:
                raise CameraError("readback V4L2 divergente")
            return readback


class _UvcQuery(ctypes.Structure):
    _fields_ = (("unit", ctypes.c_uint8), ("selector", ctypes.c_uint8),
                ("query", ctypes.c_uint8), ("size", ctypes.c_uint16),
                ("data", ctypes.POINTER(ctypes.c_uint8)))


UVCIOC_CTRL_QUERY = 0xC0107521

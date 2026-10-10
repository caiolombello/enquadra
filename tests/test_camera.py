import ctypes
import errno
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from brio_control import camera


def xu_descriptor(unit=10, selectors=(5, 13)):
    bitmap = bytearray(2)
    for selector in selectors:
        bit = selector - 1
        bitmap[bit // 8] |= 1 << (bit % 8)
    payload = bytes([0x06, 0x24, 0x06, unit]) + camera.BRIO_XU_GUID
    payload += bytes([1, 1, 0, 2]) + bytes(bitmap)
    return bytes([len(payload)]) + payload[1:]


class CameraTests(unittest.TestCase):
    def device(self, selectors=frozenset({5, 13})):
        return camera.Device("/dev/video2", "Brio 500", camera.BRIO_500_USB_ID,
                             "serial-local", 10, selectors)

    def test_descriptor_extracts_unit_and_bitmap_without_hardcode(self):
        self.assertEqual(camera._find_xu(xu_descriptor(17, (5,))), (17, frozenset({5})))

    def test_truncated_descriptor_is_rejected(self):
        self.assertIsNone(camera._find_xu(b"\x04\x24\x06"))

    def test_write_requires_writable_and_announced_selector(self):
        with patch.object(camera.os, "open", return_value=42), patch.object(camera.os, "close"), patch.object(camera.Camera, "_validate_identity"), patch.object(camera, "ioctl") as query:
            query.side_effect = lambda fd, req, arg: setattr(arg, "capabilities", camera.V4L2_CAP_VIDEO_CAPTURE)
            with camera.Camera(self.device(), writable=False) as cam:
                with self.assertRaises(camera.CameraError):
                    cam.write(5, 0)
        device = self.device(frozenset())
        cam = camera.Camera(device, writable=True)
        cam.fd = 42
        with self.assertRaises(camera.CameraError):
            cam.write(5, 0)
        cam.close()

    def test_write_gates_info_length_and_range_and_reads_back(self):
        cam = camera.Camera(self.device(), writable=True)
        cam.fd = 42
        responses = {camera.UVC_GET_INFO: b"\x03", camera.UVC_GET_LEN: b"\x01\x00",
                     camera.UVC_GET_MIN: b"\x00", camera.UVC_GET_MAX: b"\x01",
                     camera.UVC_GET_CUR: b"\x01", camera.UVC_GET_DEF: b"\x00"}
        def respond(fd, req, arg):
            if hasattr(arg, "query") and arg.query in responses:
                ctypes.memmove(arg.data, responses[arg.query], len(responses[arg.query]))
            return arg
        with patch.object(camera, "ioctl", side_effect=respond):
            self.assertEqual(cam.write(13, 1), 1)
        cam.close()

    def test_write_rejects_bad_capabilities(self):
        cam = camera.Camera(self.device(), writable=True)
        cam.fd = 42
        responses = {camera.UVC_GET_INFO: b"\x02", camera.UVC_GET_LEN: b"\x01\x00",
                     camera.UVC_GET_MIN: b"\x00", camera.UVC_GET_MAX: b"\x01",
                     camera.UVC_GET_CUR: b"\x00", camera.UVC_GET_DEF: b"\x00"}
        def respond(fd, req, arg):
            ctypes.memmove(arg.data, responses[arg.query], len(responses[arg.query]))
            return arg
        with patch.object(camera, "ioctl", side_effect=respond):
            with self.assertRaises(camera.CameraError):
                cam.write(13, 1)
        cam.close()

    def test_write_rejects_divergent_readback(self):
        cam = camera.Camera(self.device(), writable=True)
        cam.fd = 42
        responses = {camera.UVC_GET_INFO: b"\x03", camera.UVC_GET_LEN: b"\x01\x00",
                     camera.UVC_GET_MIN: b"\x00", camera.UVC_GET_MAX: b"\x01",
                     camera.UVC_GET_CUR: b"\x00", camera.UVC_GET_DEF: b"\x00"}
        def respond(fd, req, arg):
            if hasattr(arg, "query"):
                if arg.query in responses:
                    ctypes.memmove(arg.data, responses[arg.query], len(responses[arg.query]))
            return arg
        with patch.object(camera, "ioctl", side_effect=respond):
            with self.assertRaises(camera.CameraError):
                cam.write(13, 1)
        cam.close()

    def test_discovery_filters_usb_id_and_capture_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            entry = root / "video2"
            entry.mkdir()
            (entry / "name").write_text("Brio 500")
            device = entry / "device"
            device.mkdir()
            (device / "idVendor").write_text("046d")
            (device / "idProduct").write_text("0943")
            (device / "serial").write_text("private-serial")
            (device / "descriptors").write_bytes(xu_descriptor(10, (5, 13)))
            cap = camera._V4L2Capability()
            cap.capabilities = camera.V4L2_CAP_VIDEO_CAPTURE
            cap.card = b"Brio 500"
            def respond(fd, req, arg):
                if req == camera.VIDIOC_QUERYCAP:
                    arg.capabilities = camera.V4L2_CAP_VIDEO_CAPTURE
                    arg.card = b"Brio 500"
                return arg
            with patch.object(camera.os, "open", return_value=42), patch.object(camera.os, "close"), patch.object(camera, "ioctl", side_effect=respond):
                found = camera.discover_devices(str(root))
            self.assertEqual(found[0].xu_unit, 10)

    def test_enumerate_controls_reports_integer_boolean_menu_and_inactive(self):
        cam = camera.Camera(self.device(), writable=False)
        cam.fd = 42
        controls = {
            100: (camera.V4L2_CTRL_TYPE_INTEGER, b"Brightness", -10, 10, 2, 0, 0),
            200: (camera.V4L2_CTRL_TYPE_BOOLEAN, b"Auto", 0, 1, 1, 1, camera.V4L2_CTRL_FLAG_INACTIVE),
            300: (camera.V4L2_CTRL_TYPE_MENU, b"Power Line", 0, 1, 1, 0, 0),
        }
        def respond(fd, req, arg):
            if req == camera.VIDIOC_QUERYCTRL:
                if arg.id == camera.V4L2_CTRL_FLAG_NEXT_CTRL:
                    selected = controls[100]
                else:
                    current = arg.id & ~camera.V4L2_CTRL_FLAG_NEXT_CTRL
                    following = {100: 200, 200: 300}.get(current)
                    if following is None:
                        raise OSError(errno.EINVAL, "fim")
                    selected = controls[following]
                ident = next(k for k, v in controls.items() if v == selected)
                typ, name, minimum, maximum, step, default, flags = selected
                arg.id, arg.type = ident, typ
                arg.minimum, arg.maximum, arg.step = minimum, maximum, step
                arg.default_value, arg.flags = default, flags
                ctypes.memmove(arg.name, name, len(name))
            elif req == camera.VIDIOC_QUERYMENU:
                labels = {0: b"50 Hz", 1: b"60 Hz"}
                ctypes.memmove(arg.name, labels[arg.index], len(labels[arg.index]))
            elif req == camera.VIDIOC_G_CTRL:
                arg.value = {100: 2, 200: 1, 300: 1}[arg.id]
            return arg
        with patch.object(camera, "ioctl", side_effect=respond):
            result = cam.enumerate_controls()
        cam.close()
        self.assertEqual([item["id"] for item in result], ["0x64", "0xc8", "0x12c"])
        self.assertEqual(result[0]["kind"], "integer")
        self.assertEqual(result[0]["step"], 2)
        self.assertEqual(result[2]["options"], [{"value": 0, "label": "50 Hz"}, {"value": 1, "label": "60 Hz"}])
        self.assertFalse(result[1]["active"])
        self.assertIsNone(result[1]["value"])

    def test_set_control_validates_step_menu_and_reads_back(self):
        cam = camera.Camera(self.device(), writable=True)
        cam.fd = 42
        writes = []
        def respond(fd, req, arg):
            if req == camera.VIDIOC_QUERYCTRL:
                arg.id = 100
                arg.type = camera.V4L2_CTRL_TYPE_INTEGER
                arg.minimum, arg.maximum, arg.step = 0, 10, 2
                arg.flags = 0
            elif req == camera.VIDIOC_S_CTRL:
                writes.append(arg.value)
            elif req == camera.VIDIOC_G_CTRL:
                arg.value = writes[-1]
            return arg
        with patch.object(camera, "ioctl", side_effect=respond):
            self.assertEqual(cam.set_control(100, 4), 4)
            with self.assertRaises(camera.CameraError):
                cam.set_control(100, 3)
        cam.close()


if __name__ == "__main__":
    unittest.main()

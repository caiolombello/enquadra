import unittest
import threading
import time
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from brio_control.framing import FrameProcessor, FramingError


class FramingGeometryTests(unittest.TestCase):
    def test_manual_center_is_reset_after_tracking_is_disabled(self):
        processor = FrameProcessor()
        processor._center = (0.8, 0.3)
        processor.configure(autoframe=False, zoom=2.0)
        self.assertEqual(processor._center, (0.5, 0.5))
        self.assertEqual(processor._effective_zoom, 2.0)
        processor.close()

    def test_process_returns_contiguous_bgr_frame_with_requested_size(self):
        source = np.zeros((90, 160, 3), dtype=np.uint8)
        source[20:70, 50:110] = (20, 100, 220)

        result = FrameProcessor().process(source, 80, 45, now=0)

        self.assertEqual(result.shape, (45, 80, 3))
        self.assertEqual(result.dtype, np.uint8)
        self.assertTrue(result.flags.c_contiguous)
        self.assertGreater(int(result[20, 40, 2]), 150)

    def test_zoom_and_pan_crop_inside_source_bounds(self):
        source = np.zeros((100, 200, 3), dtype=np.uint8)
        source[:, :100, 2] = 255
        source[:, 100:, 1] = 255
        processor = FrameProcessor()
        processor.configure(zoom=2, pan_x=1, pan_y=0)

        result = processor.process(source, 100, 50, now=0)

        self.assertGreater(int(result[:, :, 1].mean()), 200)
        self.assertLess(int(result[:, :, 2].mean()), 20)

    def test_mirror_flips_output_horizontally(self):
        source = np.zeros((40, 80, 3), dtype=np.uint8)
        source[:, :20, 2] = 255
        processor = FrameProcessor()
        processor.configure(mirror=True)

        result = processor.process(source, 80, 40, now=0)

        self.assertGreater(int(result[:, -10:, 2].mean()), 200)
        self.assertEqual(int(result[:, :10, 2].mean()), 0)

    def test_configuration_rejects_values_outside_supported_ranges(self):
        processor = FrameProcessor()
        for values in ({"zoom": 0.9}, {"zoom": 4.1}, {"pan_x": -1.1}, {"pan_y": 1.1}):
            with self.subTest(values=values), self.assertRaises(FramingError):
                processor.configure(**values)

    def test_autoframe_reports_missing_optional_opencv(self):
        processor = FrameProcessor()
        with patch.dict(sys.modules, {"cv2": None}):
            with self.assertRaises(FramingError):
                processor.configure(autoframe=True)

    def test_detector_gets_an_owned_frame_and_latest_pending_frame(self):
        started = threading.Event()
        release = threading.Event()
        observed = []

        def detector(frame):
            value = int(frame[160, 160, 0])
            observed.append(value)
            if len(observed) == 1:
                started.set()
                release.wait(1)
            return []

        processor = FrameProcessor(detector=detector)
        processor.configure(autoframe=True)
        original = np.full((90, 160, 3), 10, dtype=np.uint8)
        processor.process(original, 160, 90, now=10.0)
        self.assertTrue(started.wait(1))
        original[:] = 99
        for index in range(1, 8):
            processor.process(np.full((90, 160, 3), 20 + index, dtype=np.uint8), 160, 90, now=10 + index * 0.11)
            time.sleep(0.11)
        release.set()
        deadline = time.monotonic() + 1
        while len(observed) < 2 and time.monotonic() < deadline:
            time.sleep(0.005)
        processor.close()

        self.assertGreaterEqual(len(observed), 2)
        self.assertEqual(observed[0], 10)
        self.assertEqual(observed[1], 27)

    def test_largest_detected_face_is_used_for_automatic_centering(self):
        started = threading.Event()

        def detector(_frame):
            started.set()
            return [(0.1, 0.1, 0.2, 0.2), (0.65, 0.3, 0.25, 0.4)]

        processor = FrameProcessor(detector=detector)
        processor.configure(autoframe=True, zoom=2)
        source = np.zeros((90, 160, 3), dtype=np.uint8)
        source[:, :, 0] = np.arange(160, dtype=np.uint8)[None, :]
        for index in range(5):
            output = processor.process(source, 80, 45, now=20 + index * 0.1)
            if started.wait(0.2):
                time.sleep(0.02)
        processor.close()

        # The larger face is on the right, so the output crop is shifted right.
        self.assertEqual(output.shape, (45, 80, 3))
        self.assertGreater(float(output[:, :, 0].mean()), 100)

    def test_autoframe_zooms_to_face_then_returns_to_manual_view_after_two_seconds(self):
        face_enabled = threading.Event()
        no_face_enabled = threading.Event()
        state = {"face": True}

        def detector(_frame):
            if state["face"]:
                face_enabled.set()
                return [(0.65, 0.3, 0.2, 0.08)]
            no_face_enabled.set()
            return []

        processor = FrameProcessor(detector=detector)
        processor.configure(autoframe=True)
        source = np.zeros((180, 320, 3), dtype=np.uint8)
        source[:, :, 0] = (np.arange(320) * 255 / 319).astype(np.uint8)[None, :]
        output = processor.process(source, 160, 90, now=100.0)
        self.assertTrue(face_enabled.wait(1))
        time.sleep(0.03)
        for index in range(1, 7):
            output = processor.process(source, 160, 90, now=100.0 + index * 0.11)
        tracked_mean = float(output[:, :, 0].mean())
        self.assertGreater(tracked_mean, 180)

        state["face"] = False
        for index in range(7, 18):
            processor.process(source, 160, 90, now=100.0 + index * 0.11)
            time.sleep(0.01)
        self.assertTrue(no_face_enabled.wait(1))
        for index in range(18, 42):
            output = processor.process(source, 160, 90, now=100.0 + index * 0.11)
        processor.close()

        self.assertLess(float(output[:, :, 0].mean()), tracked_mean)

    def test_yunet_model_is_selected_for_the_installed_opencv_generation(self):
        for version, expected_model in (
            ("4.6.0", "face_detection_yunet_2022mar.onnx"),
            ("4.10.0", "face_detection_yunet_2023mar.onnx"),
        ):
            with self.subTest(version=version):
                created = {}
                detector = Mock()
                detector.detect.return_value = (1, np.asarray([[32, 64, 96, 128]], dtype=np.float32))

                class DetectorFactory:
                    @staticmethod
                    def create(*args):
                        created["args"] = args
                        return detector

                cv2 = SimpleNamespace(
                    __version__=version,
                    FaceDetectorYN=DetectorFactory,
                    dnn=SimpleNamespace(DNN_BACKEND_OPENCV=3, DNN_TARGET_CPU=0),
                )
                detect = FrameProcessor._create_yunet_detector(cv2)
                faces = detect(np.zeros((320, 320, 3), dtype=np.uint8))

                self.assertTrue(created["args"][0].endswith(expected_model))
                self.assertEqual(faces, [(0.1, 0.2, 0.3, 0.4)])


if __name__ == "__main__":
    unittest.main()

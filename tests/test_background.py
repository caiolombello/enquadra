import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from brio_control.background import BackgroundError, BackgroundProcessor, _MODEL_SIZE
from brio_control.framing import FrameProcessor, FramingError


class BackgroundTests(unittest.TestCase):
    def setUp(self):
        try:
            import cv2
        except ImportError:
            self.skipTest('OpenCV indisponível')
        self.cv2 = cv2
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'fundo.png'
        self.cv2.imwrite(str(self.path), np.full((20, 40, 3), 100, dtype=np.uint8))

    def processor(self, mask):
        processor = BackgroundProcessor(lambda frame: mask(frame) if callable(mask) else mask)
        processor.configure('image', str(self.path))
        return processor

    def test_off_does_not_import_opencv_or_load_model(self):
        processor = BackgroundProcessor()
        with patch.object(processor, '_load_opencv', side_effect=AssertionError), patch.object(processor, '_create_segmenter', side_effect=AssertionError):
            processor.configure()
            frame = np.ones((2, 3, 3), dtype=np.uint8)
            self.assertIs(processor.process(frame), frame)

    def test_person_alpha_foreground_background_and_soft_edges(self):
        mask = np.asarray([[0, 0.5, 1]], dtype=np.float32)
        frame = np.full((1, 3, 3), 200, dtype=np.uint8)
        result = self.processor(mask).process(frame)
        np.testing.assert_array_equal(result[0, :, 0], [100, 150, 200])

    def test_native_blending_preserves_alpha_extremes_and_soft_edges(self):
        rng = np.random.default_rng(42)
        source = rng.integers(0, 256, (20, 40, 3), dtype=np.uint8)
        for value in (0.0, 0.37, 1.0):
            processor = self.processor(np.full((20, 40), value, dtype=np.float32))
            result = processor.process(source)
            expected = np.rint(source.astype(np.float32) * value + 100 * (1 - value)).astype(np.uint8)
            if value in (0.0, 1.0):
                np.testing.assert_array_equal(result, expected)
            else:
                self.assertLessEqual(int(np.abs(result.astype(int) - expected.astype(int)).max()), 1)

    def test_active_effect_uses_linear_opencv_resize_before_segmentation(self):
        observed = []
        def segment(frame):
            observed.append(frame.copy())
            return np.ones(frame.shape[:2], dtype=np.float32)
        source = np.zeros((12, 20, 3), dtype=np.uint8)
        source[:, :, 0] = np.arange(20, dtype=np.uint8)[None] * 10
        processor = FrameProcessor(segmenter=segment)
        processor.configure(background_mode='image', background_image=str(self.path))
        result = processor.process(source, 10, 6)
        expected = self.cv2.resize(source, (10, 6), interpolation=self.cv2.INTER_LINEAR)
        np.testing.assert_array_equal(observed[0], expected)
        np.testing.assert_array_equal(result, expected)
        processor.close()

    def test_failure_hides_entire_source_and_can_recover(self):
        segment = Mock(side_effect=RuntimeError('teste'))
        processor = self.processor(segment)
        frame = np.full((2, 3, 3), 200, dtype=np.uint8)
        np.testing.assert_array_equal(processor.process(frame), np.full_like(frame, 100))
        self.assertIn('quadro inteiro', processor.warning)
        segment.side_effect = None
        segment.return_value = np.ones((2, 3), dtype=np.float32)
        np.testing.assert_array_equal(processor.process(frame), frame)
        self.assertIsNone(processor.warning)

    def test_bad_mask_fails_closed(self):
        for mask in (np.asarray([[np.nan]]), np.asarray([[2.0]]), np.zeros((1, 1, 3))):
            with self.subTest(mask=mask):
                processor = self.processor(mask)
                result = processor.process(np.full((2, 3, 3), 200, dtype=np.uint8))
                self.assertTrue((result == 100).all())
                self.assertIsNotNone(processor.warning)

    def test_image_cover_uses_center_crop_without_stretching(self):
        image = np.zeros((20, 60, 3), dtype=np.uint8)
        image[:, :20] = 10
        image[:, 20:40] = 100
        image[:, 40:] = 200
        self.cv2.imwrite(str(self.path), image)
        result = self.processor(np.zeros((20, 20), dtype=np.float32)).process(np.zeros((20, 20, 3), dtype=np.uint8))
        self.assertTrue((result == 100).all())

    def test_invalid_changes_preserve_previous_settings_and_image(self):
        processor = self.processor(np.zeros((2, 3), dtype=np.float32))
        for args in [('image', None, 12), ('image', 'relative.png', 12), ('image', str(self.path.parent), 12), ('image', str(self.path), True), ('blur', None, 31)]:
            with self.subTest(args=args), self.assertRaises(BackgroundError):
                processor.configure(*args)
            self.assertEqual(processor.mode, 'image')
            self.assertEqual(processor.image_path, str(self.path))
        self.path.write_bytes(b'not a picture')
        other = self.path.with_name('bad.png')
        other.write_bytes(b'not a picture')
        with self.assertRaises(BackgroundError):
            processor.configure('image', str(other))
        self.assertEqual(processor.image_path, str(self.path))

    def test_image_bounds_before_decode(self):
        payload = b'\x89PNG\r\n\x1a\n' + b'\x00\x00\x00\x0dIHDR' + (5000).to_bytes(4, 'big') + (1).to_bytes(4, 'big')
        self.path.write_bytes(payload)
        with patch.object(self.cv2, 'imdecode', side_effect=AssertionError('must reject before decode')):
            with self.assertRaisesRegex(BackgroundError, '4096'):
                self.processor(np.zeros((1, 1)))

    def test_extreme_image_aspect_never_allocates_an_oversized_resize(self):
        self.cv2.imwrite(str(self.path), np.full((1, 4096, 3), 100, dtype=np.uint8))
        processor = self.processor(np.zeros((90, 160), dtype=np.float32))
        real_resize = self.cv2.resize
        sizes = []
        def resize(source, size, **kwargs):
            sizes.append(size)
            return real_resize(source, size, **kwargs)
        with patch.object(self.cv2, 'resize', side_effect=resize):
            result = processor.process(np.zeros((90, 160, 3), dtype=np.uint8))
        self.assertEqual(result.shape, (90, 160, 3))
        self.assertTrue(all(width <= 160 and height <= 90 for width, height in sizes))

    def test_huge_blur_integer_is_a_validation_error(self):
        with self.assertRaises(BackgroundError):
            BackgroundProcessor().configure('blur', blur=10 ** 400)

    def test_missing_optional_opencv_preserves_off(self):
        import sys
        processor = BackgroundProcessor()
        with patch.dict(sys.modules, {'cv2': None}):
            with self.assertRaisesRegex(BackgroundError, 'python3-opencv'):
                processor.configure('blur')
        self.assertEqual(processor.mode, 'off')

    def test_model_hash_and_size_are_checked_before_loading(self):
        with patch('brio_control.background.Path.stat', return_value=Mock(st_size=1)), patch.object(self.cv2.dnn, 'readNetFromONNX') as load:
            with self.assertRaisesRegex(BackgroundError, 'Tamanho'):
                BackgroundProcessor._create_segmenter(self.cv2)
            load.assert_not_called()
        with patch('brio_control.background.Path.stat', return_value=Mock(st_size=_MODEL_SIZE)), patch('brio_control.background.Path.read_bytes', return_value=b'corrupt'), patch.object(self.cv2.dnn, 'readNetFromONNX') as load:
            with self.assertRaisesRegex(BackgroundError, 'Hash'):
                BackgroundProcessor._create_segmenter(self.cv2)
            load.assert_not_called()

    def test_model_load_is_cached_across_slider_changes(self):
        processor = BackgroundProcessor()
        with patch.object(processor, '_create_segmenter', return_value=lambda frame: np.zeros(frame.shape[:2])) as load:
            processor.configure('blur')
            processor.configure('blur', blur=20)
            processor.configure('off')
            processor.configure('image', str(self.path))
            self.assertEqual(load.call_count, 1)

    def test_blur_failure_never_returns_unprocessed_source(self):
        source = np.zeros((80, 120, 3), dtype=np.uint8)
        source[:, 60:] = 255
        processor = BackgroundProcessor(Mock(side_effect=RuntimeError('teste')))
        processor.configure('blur')
        result = processor.process(source)
        self.assertFalse(np.array_equal(result, source))
        self.assertIsNotNone(processor.warning)

    def test_framing_composites_after_zoom_before_mirror(self):
        observed = []
        def segment(frame):
            observed.append(frame.copy())
            return np.zeros(frame.shape[:2], dtype=np.float32)
        image = np.zeros((20, 40, 3), dtype=np.uint8)
        image[:, :20] = 10
        image[:, 20:] = 200
        self.cv2.imwrite(str(self.path), image)
        processor = FrameProcessor(segmenter=segment)
        processor.configure(background_mode='image', background_image=str(self.path), zoom=2, pan_x=1, mirror=True)
        source = np.full((20, 40, 3), 77, dtype=np.uint8)
        result = processor.process(source, 40, 20)
        self.assertTrue((result[:, :20] == 200).all())
        self.assertTrue((result[:, 20:] == 10).all())
        self.assertEqual(observed[0].shape, (20, 40, 3))
        self.assertTrue((observed[0] == 77).all())
        with self.assertRaises(FramingError):
            processor.configure(background_mode='image')
        self.assertEqual(processor.background.mode, 'image')
        processor.close()

    def test_official_model_produces_finite_soft_person_mask(self):
        segment = BackgroundProcessor._create_segmenter(self.cv2)
        mask = segment(np.full((90, 160, 3), 128, dtype=np.uint8))
        self.assertEqual(mask.shape, (192, 192))
        self.assertTrue(np.isfinite(mask).all())
        self.assertTrue((mask >= 0).all() and (mask <= 1).all())


if __name__ == '__main__':
    unittest.main()

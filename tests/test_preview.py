import tempfile
import unittest
from brio_control.preview import PreviewReader, PreviewWriter


class PreviewTests(unittest.TestCase):
    def test_publish_and_invalidate(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PreviewWriter(directory)
            reader = PreviewReader(writer.path)
            try:
                self.assertIsNone(reader.read())
                writer.write(2, 1, b"123456")
                self.assertEqual(reader.read(), (2, 1, b"123456"))
                writer.invalidate()
                self.assertIsNone(reader.read())
            finally:
                reader.close()
                writer.close()

    def test_reject_bad_frame(self):
        with tempfile.TemporaryDirectory() as directory:
            writer = PreviewWriter(directory)
            try:
                for width, height, pixels in [(0, 1, b""), (1921, 1, b""), (2, 1, b"123")]:
                    with self.assertRaises(ValueError): writer.write(width, height, pixels)
            finally:
                writer.close()

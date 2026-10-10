import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from brio_control.preview import PreviewReader
from brio_control.stream import StreamError, VideoEngine, virtual_camera_name


class VideoEngineTests(unittest.TestCase):
    def test_owned_virtual_labels_support_new_and_legacy_without_accepting_foreign_devices(self):
        def capability(driver, card):
            def query(fd, request, cap, mutable):
                cap.driver = driver
                cap.card = card
                cap.capabilities = 2
            return query
        for driver, card, expected in (
            (b'v4l2 loopback', b'Enquadra', 'Enquadra'),
            (b'v4l2 loopback', b'Brio Control', 'Brio Control'),
            (b'v4l2 loopback', b'OBS', None),
            (b'uvcvideo', b'Enquadra', None),
        ):
            with self.subTest(driver=driver, card=card), patch('brio_control.stream.os.open', return_value=11), \
                    patch('brio_control.stream.os.close') as close, \
                    patch('brio_control.stream.fcntl.ioctl', side_effect=capability(driver, card)):
                self.assertEqual(virtual_camera_name('/dev/video10'), expected)
                if expected:
                    VideoEngine._validate_owned_loopback('/dev/video10')
                else:
                    with self.assertRaises(StreamError):
                        VideoEngine._validate_owned_loopback('/dev/video10')
                close.assert_called_with(11)
        with patch('brio_control.stream.os.open') as opening:
            self.assertIsNone(virtual_camera_name('/tmp/not-video'))
            opening.assert_not_called()
        with patch('brio_control.stream.os.open', side_effect=FileNotFoundError):
            self.assertIsNone(virtual_camera_name('/dev/video10'))

    def test_processing_settings_merge_without_changing_pan_coordinates(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = VideoEngine(directory, width=640, height=360,
                                 processing={"zoom": 1.75, "pan_x": 1.0})
            self.assertEqual(engine.processing, {
                "autoframe": False, "zoom": 1.75, "pan_x": 1.0,
                "pan_y": 0.0, "mirror": False,
                "background_mode": "off", "background_image": None, "background_blur": 12.0,
            })
            engine.configure({"mirror": True})
            self.assertEqual(engine.processing["pan_x"], 1.0)
            self.assertTrue(engine.processing["mirror"])
            engine.stop()

    def test_effect_warning_is_nonfatal_and_activation_clears_old_frame(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            engine = VideoEngine(directory, width=64, height=48)
            processor = Mock(last_error=None, warning='segmentação falhou; quadro protegido')
            engine.processor = processor
            engine._output_frame = np.full((48, 64, 3), 255, dtype=np.uint8)
            engine._output_at = 10.0
            engine.configure({'background_mode': 'blur'})
            self.assertIsNone(engine._output_frame)
            self.assertEqual(engine._output_at, 0.0)
            self.assertIsNone(engine.error)
            self.assertIn('quadro protegido', engine.warning)
            processor.configure.assert_called_once()
            engine.stop()

    def test_synthetic_pipeline_preserves_odd_width_bgr_pixels_and_preview_lifecycle(self):
        try:
            import gi
            gi.require_version("Gst", "1.0")
            gi.require_version("GstVideo", "1.0")
        except (ImportError, ValueError):
            self.skipTest("GStreamer/GstVideo Python bindings are unavailable")

        with tempfile.TemporaryDirectory() as directory:
            engine = VideoEngine(directory, width=65, height=48)
            engine.start("/dev/video-do-not-open", synthetic=True)
            self.assertTrue(engine.running)
            self.assertIsNotNone(engine.preview_path)
            reader = PreviewReader(engine.preview_path)
            try:
                deadline = time.monotonic() + 3
                preview = None
                while time.monotonic() < deadline:
                    preview = reader.read()
                    if preview is not None:
                        break
                    time.sleep(0.02)
                self.assertIsNotNone(preview)
                width, height, pixels = preview
                self.assertEqual((width, height), (65, 48))
                self.assertEqual(len(pixels), 65 * 48 * 3)
                engine._source_pipeline.set_state(engine._Gst.State.NULL)
                stale_deadline = time.monotonic() + 2
                while time.monotonic() < stale_deadline and reader.read() is not None:
                    time.sleep(0.02)
                self.assertIsNone(reader.read())
            finally:
                reader.close()
                engine.stop()
            self.assertFalse(engine.running)
            self.assertIsNone(engine.preview_path)

    def test_stop_does_not_hold_state_lock_while_source_callback_finishes(self):
        try:
            import gi
            gi.require_version("Gst", "1.0")
            gi.require_version("GstVideo", "1.0")
        except (ImportError, ValueError):
            self.skipTest("GStreamer/GstVideo Python bindings are unavailable")

        entered = threading.Event()
        release = threading.Event()

        class BlockingProcessor:
            last_error = None

            def configure(self, **_settings):
                pass

            def process(self, _frame, width, height):
                entered.set()
                release.wait(2)
                import numpy as np
                return np.zeros((height, width, 3), dtype=np.uint8)

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as directory:
            engine = VideoEngine(directory, width=65, height=48)
            engine.processor = BlockingProcessor()
            engine.start("/dev/video-do-not-open", synthetic=True)
            self.assertTrue(entered.wait(1))
            stopped = threading.Event()
            stopper = threading.Thread(target=lambda: (engine.stop(), stopped.set()))
            stopper.start()
            time.sleep(0.05)
            release.set()
            self.assertTrue(stopped.wait(3), "stop deve terminar depois que o callback libera o frame")
            stopper.join()
            self.assertFalse(engine.running)

    def test_fatal_error_cleans_up_and_same_engine_can_restart(self):
        try:
            import gi
            gi.require_version("Gst", "1.0")
            gi.require_version("GstVideo", "1.0")
        except (ImportError, ValueError):
            self.skipTest("GStreamer/GstVideo Python bindings are unavailable")

        with tempfile.TemporaryDirectory() as directory:
            engine = VideoEngine(directory, width=64, height=48)
            try:
                engine.start("/dev/video-do-not-open", synthetic=True)
                self.assertTrue(engine.running)
                engine._fatal_stream_error("erro fatal simulado")
                self.assertFalse(engine.running)
                self.assertEqual(engine.error, "erro fatal simulado")
                self.assertIsNone(engine.preview_path)
                self.assertIsNone(engine._source_pipeline)
                self.assertIsNone(engine._monitor_thread)

                engine.start("/dev/video-do-not-open", synthetic=True)
                self.assertTrue(engine.running)
            finally:
                engine.stop()

    def test_output_path_must_be_a_video_node(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = VideoEngine(directory)
            with self.assertRaises(StreamError):
                engine.start("/dev/video-do-not-open", output_path="/tmp/anything")
            self.assertFalse(engine.running)
            engine.stop()

    def test_old_monitor_fatal_error_cannot_stop_restarted_session(self):
        waiting = threading.Event()

        class ObservedLock:
            def __init__(self):
                self.lock = threading.Lock()

            def __enter__(self):
                if threading.current_thread().name == "old-video-monitor":
                    waiting.set()
                self.lock.acquire()

            def __exit__(self, *_args):
                self.lock.release()

        with tempfile.TemporaryDirectory() as directory:
            engine = VideoEngine(directory)
            engine._lifecycle_lock = ObservedLock()
            engine._cleanup = Mock()
            engine._running = True
            fatal = threading.Thread(
                target=lambda: engine._fatal_stream_error(
                    "erro antigo", monitor_thread=threading.current_thread(),
                ),
                name="old-video-monitor",
            )
            engine._monitor_thread = fatal
            try:
                with engine._lifecycle_lock:
                    fatal.start()
                    self.assertTrue(waiting.wait(1))
                    self.assertTrue(engine.running, "o monitor deve aguardar o lock antes de mudar o estado")
                    # A stop/restart completed while the old monitor awaited the lock.
                    engine._monitor_thread = threading.Thread(name="new-video-monitor")
                fatal.join(1)
                self.assertFalse(fatal.is_alive())
                self.assertTrue(engine.running)
                self.assertIsNone(engine.error)
                engine._cleanup.assert_not_called()
            finally:
                fatal.join(2)

    def test_stop_joins_ticker_before_draining_final_black_frame(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            engine = VideoEngine(directory, width=64, height=48)
            try:
                engine._load_gstreamer()
            except StreamError:
                self.skipTest("GStreamer/GstVideo Python bindings are unavailable")
            Gst = engine._Gst
            pipeline = Gst.parse_launch(
                "appsrc name=processed_source is-live=true format=time do-timestamp=true "
                "caps=video/x-raw,format=BGR,width=64,height=48,framerate=30/1 ! "
                "fakesink name=sink signal-handoffs=true sync=false"
            )
            received = []
            pipeline.get_by_name("sink").connect(
                "handoff", lambda _sink, buffer, _pad: received.append(
                    buffer.extract_dup(0, buffer.get_size())
                ),
            )
            engine._output_pipeline = pipeline
            engine._output_bus = pipeline.get_bus()
            engine._appsrc = pipeline.get_by_name("processed_source")
            pipeline.set_state(Gst.State.PLAYING)
            engine._running = True
            selected = threading.Event()
            pushed = []
            original_push = engine._push_output
            def record_push(frame):
                pushed.append(bool(frame.any()))
                return original_push(frame)
            engine._push_output = record_push
            def pending_tick():
                selected.set()
                engine._stop_event.wait(1)
                engine._push_output(np.ones((48, 64, 3), dtype=np.uint8))
            engine._ticker_thread = threading.Thread(target=pending_tick)
            engine._ticker_thread.start()
            try:
                self.assertTrue(selected.wait(1))
                engine.stop()
                self.assertEqual(pushed, [True, False])
                self.assertTrue(received, "EOS deve drenar frames antes de NULL")
                self.assertEqual(received[-1], bytes(48 * 64 * 3))
                self.assertIsNone(engine.warning)
            finally:
                engine.stop()

    def test_stop_warns_when_final_frame_drain_times_out(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = VideoEngine(directory)
            try:
                engine._load_gstreamer()
            except StreamError:
                self.skipTest("GStreamer/GstVideo Python bindings are unavailable")
            engine._running = True
            engine._appsrc = Mock()
            engine._appsrc.emit.return_value = engine._Gst.FlowReturn.OK
            appsrc = engine._appsrc
            bus = engine._output_bus = Mock()
            bus.timed_pop_filtered.return_value = None
            engine.stop()
            self.assertIn("preto", engine.warning)
            self.assertIn("confirm", engine.warning)
            appsrc.emit.assert_any_call("end-of-stream")
            bus.timed_pop_filtered.assert_called_once_with(
                300 * engine._Gst.MSECOND,
                engine._Gst.MessageType.EOS | engine._Gst.MessageType.ERROR,
            )

    def test_fatal_error_preserves_cause_when_final_frame_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = VideoEngine(directory)
            try:
                engine._load_gstreamer()
            except StreamError:
                self.skipTest("GStreamer/GstVideo Python bindings are unavailable")
            engine._running = True
            appsrc = engine._appsrc = Mock()
            appsrc.emit.return_value = engine._Gst.FlowReturn.ERROR
            engine._fatal_stream_error("causa original")
            self.assertFalse(engine.running)
            self.assertEqual(engine.error, "causa original")
            self.assertIn("preto", engine.warning)
            self.assertIn("rejeitou", engine.warning)
            self.assertEqual(appsrc.emit.call_count, 1)

    def test_blocked_resolver_cannot_reopen_source_after_stop_or_restart(self):
        for restart in (False, True):
            with self.subTest(restart=restart), tempfile.TemporaryDirectory() as directory:
                engine = VideoEngine(directory)
                entered = threading.Event()
                release = threading.Event()
                def resolver():
                    entered.set()
                    release.wait(4)
                    return "/dev/video0"
                engine._resolver = resolver
                engine._running = True
                engine._start_source = Mock()
                monitor = threading.Thread(target=lambda: engine._retry_source(
                    monitor_thread=threading.current_thread(),
                ))
                engine._monitor_thread = monitor
                monitor.start()
                try:
                    self.assertTrue(entered.wait(1))
                    engine.stop()
                    self.assertFalse(engine.running)
                    if restart:
                        engine._monitor_thread = threading.Thread(name="new-video-monitor")
                        engine._running = True
                        engine._stop_event.clear()
                        engine._error = "nova sessão"
                    release.set()
                    monitor.join(1)
                    self.assertFalse(monitor.is_alive())
                    engine._start_source.assert_not_called()
                    if restart:
                        self.assertTrue(engine.running)
                        self.assertEqual(engine.error, "nova sessão")
                finally:
                    release.set()
                    monitor.join(1)

    def test_old_monitor_source_loss_preserves_new_session(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = VideoEngine(directory)
            pipeline = engine._source_pipeline = Mock()
            frame = engine._output_frame = object()
            engine._output_at = 44.0
            engine._running = True
            engine._error = "nova sessão"
            engine._monitor_thread = threading.Thread(name="new-video-monitor")
            engine._source_lost(None, monitor_thread=threading.current_thread())
            self.assertIs(engine._source_pipeline, pipeline)
            self.assertIs(engine._output_frame, frame)
            self.assertEqual(engine._output_at, 44.0)
            self.assertEqual(engine.error, "nova sessão")
            pipeline.set_state.assert_not_called()


if __name__ == "__main__":
    unittest.main()

import importlib.util
import os
import subprocess
import sys
import unittest

from brio_control.gui import _normalise_processing, _quantise_control, _start_options, _text


class GuiWidgetTests(unittest.TestCase):
    """Real GTK widgets and fake worker boundary; no camera or worker is opened."""

    def exercise_window(self, scenario):
        if not importlib.util.find_spec("gi"):
            self.skipTest("GTK/PyGObject are required")
        from brio_control.gui import _load_gtk
        try:
            _load_gtk()
        except (ImportError, ValueError):
            self.skipTest("GTK4 is not installed")
        script = '''
import os
import time
import threading
from unittest.mock import patch
from brio_control import gui
Gtk, Gdk, Gio, GLib = gui._load_gtk()
if not Gtk.init_check():
    raise SystemExit(77)

class FakeClient:
    def __init__(self):
        self.starts = []
        self.running = False
        self.show_mode = {}
        self.processing_state = gui._normalise_processing(None)
        self.processing_calls = []
        self.profiles = []
        self.stops = 0
        self.loaded_profiles = []
        self.controls = []
        self.control_calls = []
        self.error = None
        self.warning = None
        self.preference_state = {"preview_only": True, "output": "/dev/video10", "size": "720", "last_profile": None}
        self.preference_calls = []
        self.devices = [{"path": "/dev/fake-brio", "name": "Brio 500"}]
        self.selected_device = "/dev/fake-brio"
        self.selection_calls = []
    def state(self):
        return {"running": self.running, "devices": self.devices, "selected_device": self.selected_device,
                "show_mode": self.show_mode, "processing": self.processing_state,
                "profiles": self.profiles, "controls": self.controls,
                "error": self.error, "warning": self.warning, "preferences": self.preference_state}
    def select_device(self, path):
        self.selection_calls.append(path)
        self.selected_device = path
        return self.state()
    def preferences(self, options):
        self.preference_calls.append(dict(options))
        self.preference_state.update(options)
    def start(self, options):
        self.starts.append(options)
        self.running = True
    def stop(self):
        self.stops += 1
        self.running = False
    def processing(self, options):
        self.processing_calls.append(dict(options))
        self.processing_state = dict(options)
    def save_profile(self, name):
        self.profiles.append(name)
        self.preference_state["last_profile"] = name
    def load_profile(self, name):
        self.loaded_profiles.append(name)
        self.preference_state["last_profile"] = name
    def set_control(self, control_id, value):
        self.control_calls.append((control_id, value))

def pump_until(predicate):
    deadline = time.monotonic() + 3
    context = GLib.MainContext.default()
    while not predicate():
        while context.pending():
            context.iteration(False)
        assert time.monotonic() < deadline, "GTK action did not complete"
        time.sleep(0.005)

def click_start(window):
    window.start_button.emit("clicked")
    pump_until(lambda: not getattr(window, "_capture_busy", False))

def exercise(app, args):
    app.set_application_id("org.briocontrol.UiRegression.P" + str(os.getpid()))
    app.register(None)
    app.do_activate()
    window = app.window
    try:
        SCENARIO
    finally:
        if not window._closed:
            GLib.source_remove(window._state_source)
            GLib.source_remove(window._preview_source)
        window._closed = True
        app._release_background_hold()
        if window._preferences_timeout:
            GLib.source_remove(window._preferences_timeout)
        if window._processing_timeout:
            GLib.source_remove(window._processing_timeout)
        for source in window._control_timeouts.values():
            GLib.source_remove(source)
        window.destroy()
    return 0

with patch("brio_control.client.ensure_worker"), patch("brio_control.client.Client", FakeClient), \\
     patch.object(Gtk.Application, "run", exercise), patch.object(Gtk.Window, "present"):
    assert gui.run() == 0
'''.replace("        SCENARIO", "\n".join("        " + line for line in scenario.splitlines()))
        result = subprocess.run([sys.executable, "-c", script],
                                capture_output=True, text=True, timeout=15,
                                env={**os.environ, "GTK_A11Y": "none", "GIO_USE_VFS": "local",
                                     "GTK_USE_PORTAL": "0"})
        if result.returncode == 77:
            self.skipTest("GTK display is unavailable")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_identity_and_system_theme_do_not_open_camera(self):
        self.exercise_window('''
assert window.get_title() == "Enquadra"
assert window.get_icon_name() == "enquadra"
assert window.brand_icon.get_paintable() is not None
assert window.preview_icon.get_paintable() is not None
settings = Gtk.Settings.get_default()
settings.set_property("gtk-theme-name", "Adwaita")
for dark in (False, True):
    settings.set_property("gtk-application-prefer-dark-theme", dark)
    assert not window.client.starts
    assert not window.client.processing_calls
window.client.running = True
window.client.preference_state["preview_only"] = False
state = window.client.state()
state.update(virtual_device="/dev/video10", virtual_camera_name="Brio Control")
window._refresh_state(state)
assert "Brio Control" in window.output_hint.get_text()
assert "Brio Control" in window.preview_footer.get_text()
for label in window._virtual_name_labels.values():
    assert "Brio Control" in label.get_text()
''')

    def test_background_defaults_and_programmatic_sync_send_no_writes(self):
        self.exercise_window('''
assert window.background_buttons["off"].get_active()
assert not window.background_blur_box.get_visible()
assert not window.client.processing_calls
assert not window.client.starts
window.client.processing_state.update(background_mode="image", background_image="/tmp/Office.png", background_blur=20)
window._refresh_state()
assert window.background_buttons["image"].get_active()
assert window.background_filename.get_text() == "Office.png"
assert window.background_blur_scale.get_value() == 20
assert not window.client.processing_calls
assert window._pending_processing is None
''')

    def test_background_blur_and_image_preserve_framing_then_original_recovers(self):
        self.exercise_window('''
window.client.processing_state.update(autoframe=True, zoom=1.25, mirror=True)
window._refresh_state()
window.background_buttons["blur"].set_active(True)
window.background_blur_scale.set_value(18)
assert window.background_blur_box.get_visible()
assert window._flush_pending()
assert window.client.processing_state["background_mode"] == "blur"
assert window.client.processing_state["background_blur"] == 18
assert window.client.processing_state["zoom"] == 1.25
assert window.client.processing_state["autoframe"]
assert window.client.processing_state["mirror"]
window._select_background_image("/tmp/Office.png")
assert window._flush_pending()
window.zoom_scale.set_value(1.5)
window.mirror.set_active(False)
assert window._flush_pending()
assert window.client.processing_state["background_mode"] == "image"
assert window.client.processing_state["background_image"] == "/tmp/Office.png"
assert window.background_filename.get_text() == "Office.png"
assert not window.background_blur_box.get_visible()
window.background_buttons["off"].set_active(True)
assert window._flush_pending()
assert window.client.processing_state["background_mode"] == "off"
assert window.client.processing_state["background_image"] == "/tmp/Office.png"
window._refresh_state()
with patch.object(window, "_on_choose_background") as choose:
    window.background_buttons["image"].set_active(True)
    assert window._flush_pending()
    window.background_buttons["blur"].set_active(True)
    assert window._flush_pending()
    window.background_buttons["image"].set_active(True)
    assert window._flush_pending()
assert not choose.called
assert window.client.processing_state["background_mode"] == "image"
assert window.client.processing_state["background_image"] == "/tmp/Office.png"
assert window.client.processing_state["zoom"] == 1.5
assert not window.client.starts
''')

    def test_background_picker_cancel_and_late_response_do_not_apply_effect(self):
        self.exercise_window('''
with patch.object(Gtk.FileChooserNative, "show"):
    window.background_buttons["image"].set_active(True)
    chooser = window._background_chooser
    assert chooser is not None
    assert chooser.get_filters().get_n_items() == 1
    assert window.background_buttons["off"].get_active()
    window._on_background_response(chooser, Gtk.ResponseType.CANCEL)
assert window._background_chooser is None
assert not window.client.processing_calls
assert window._pending_processing is None
with patch.object(Gtk.FileChooserNative, "show"):
    window._on_choose_background()
    chooser = window._background_chooser
    window._on_close_request(window)
    window._on_background_response(chooser, Gtk.ResponseType.ACCEPT)
assert window._background_chooser is None
assert not window.client.processing_calls
assert not window.client.starts
''')

    def test_background_picker_accept_selects_local_path_without_starting(self):
        self.exercise_window('''
from unittest.mock import Mock
chooser = Mock()
chooser.get_file.return_value = Gio.File.new_for_path("/tmp/Office.png")
window._background_chooser = chooser
window._on_background_response(chooser, Gtk.ResponseType.ACCEPT)
assert chooser.destroy.call_count == 1
assert window._flush_pending()
assert window.client.processing_state["background_mode"] == "image"
assert window.client.processing_state["background_image"] == "/tmp/Office.png"
assert not window.client.starts
''')

    def test_background_rejected_image_restores_actual_state_and_original_works(self):
        self.exercise_window('''
from brio_control.client import ClientError
window.client.processing_state.update(background_mode="blur", background_blur=16, zoom=1.25, autoframe=True)
window._refresh_state()
window._select_background_image("/tmp/missing.png")
with patch.object(window.client, "processing", side_effect=ClientError("Invalid background image")):
    assert not window._flush_pending()
assert window.background_buttons["blur"].get_active()
assert window._background_image is None
assert window.zoom_scale.get_value() == 1.25
assert window._pending_processing is None
assert "Invalid background image" in window.status_label.get_text()
assert window.error_revealer.get_reveal_child()
window._refresh_state()
assert window.background_buttons["blur"].get_active()
window.background_buttons["off"].set_active(True)
assert window._flush_pending()
assert not window.error_revealer.get_reveal_child()
assert window.client.processing_state["background_mode"] == "off"
assert not window.client.starts
''')

    def test_background_profile_load_restores_image_and_old_profile_defaults_off(self):
        self.exercise_window('''
window.client.profiles = ["Office", "Old"]
window._refresh_state()
def load(name):
    window.client.loaded_profiles.append(name)
    window.client.processing_state = ({"background_mode": "image", "background_image": "/tmp/Office.png", "zoom": 1.25}
                                      if name == "Office" else {"zoom": 1.5})
with patch.object(window.client, "load_profile", side_effect=load):
    window._on_load_profile(None)
    assert window.background_buttons["image"].get_active()
    assert window.background_filename.get_text() == "Office.png"
    window.profile_dropdown.set_selected(1)
    window._on_load_profile(None)
assert window.background_buttons["off"].get_active()
assert window._background_image is None
assert window.zoom_scale.get_value() == 1.5
assert not window.client.processing_calls
assert not window.client.starts
''')

    def test_restores_output_resolution_and_last_profile_without_capture(self):
        self.exercise_window('''
window.client.preference_state = {"preview_only": False, "output": "/dev/video24", "size": "1080", "last_profile": "Mesa"}
window.client.profiles = ["Reuniões", "Mesa"]
window._preferences_synced = False
window._refresh_state()
assert not window.preview_only.get_active()
assert window.output_entry.get_text() == "/dev/video24"
assert window.resolution_dropdown.get_selected() == 1
assert window.profile_dropdown.get_selected() == 1
assert "Mesa" in window.profile_selection_notice.get_text()
assert not window.client.starts
assert not window.client.loaded_profiles
assert not window.client.preference_calls
with patch("brio_control.gui.os.path.exists", return_value=True):
    click_start(window)
assert window.client.starts[0]["output"] == "/dev/video24"
assert window.client.starts[0]["width"] == 1920
''')

    def test_preferences_survive_polls_and_flush_before_start(self):
        self.exercise_window('''
window.output_entry.set_text("/dev/video22")
window.resolution_dropdown.set_selected(1)
window.preview_only.set_active(False)
window._refresh_state()
assert window.output_entry.get_text() == "/dev/video22"
assert not window.preview_only.get_active()
with patch("brio_control.gui.os.path.exists", return_value=True):
    click_start(window)
assert window.client.preference_state["output"] == "/dev/video22"
assert window.client.preference_state["size"] == "1080"
assert not window.client.preference_state["preview_only"]
assert window.client.starts[0]["output"] == "/dev/video22"
assert len(window.client.preference_calls) == 1
''')

    def test_profile_selection_persists_without_applying_profile(self):
        self.exercise_window('''
window.client.profiles = ["Reuniões", "Mesa"]
window._refresh_state()
window.profile_dropdown.set_selected(1)
assert window._flush_pending()
assert window.client.preference_state["last_profile"] == "Mesa"
assert "Mesa" in window.profile_selection_notice.get_text()
assert not window.client.loaded_profiles
''')

    def test_active_virtual_output_explains_call_selection_without_preference_write(self):
        self.exercise_window('''
state = window.client.state()
state.update(running=True, virtual_device="/dev/video24")
with patch.object(window.client, "state", return_value=state):
    window._refresh_state()
assert "/dev/video24" in window.output_status.get_text()
assert "Enquadra" in window.output_hint.get_text()
assert "Zoom" in window.output_hint.get_text()
assert "Enquadra" in window.preview_footer.get_text()
assert window.output_hint.get_visible()
assert not window.client.preference_calls
assert not window._pending_preferences
state["running"] = False
state["virtual_device"] = None
with patch.object(window.client, "state", return_value=state):
    window._refresh_state()
assert window.preview_only.get_active()
assert window.output_entry.get_text() == "/dev/video10"
assert not window.client.preference_calls
''')

    def test_start_defaults_to_local_preview_without_virtual_device(self):
        self.exercise_window('''
click_start(window)
assert window.client.starts, window.status_label.get_text()
assert window.client.starts[0]["output"] is None, window.client.starts
assert window.preview_only.get_active()
assert not window.output_entry.get_sensitive()
''')

    def test_preview_placeholder_distinguishes_stopped_from_unavailable(self):
        self.exercise_window('''
window._refresh_preview()
assert window.preview_placeholder.get_text() == gui._text("preview_stopped")
click_start(window)
window._refresh_preview()
assert window.preview_placeholder.get_text() == gui._text("no_preview")
''')

    def test_explicit_virtual_output_is_enabled_and_missing_device_is_explained(self):
        self.exercise_window('''
window.preview_only.set_active(False)
assert window.output_entry.get_sensitive()
with patch("brio_control.gui.os.path.exists", return_value=False):
    click_start(window)
assert not window.client.starts
message = window.status_label.get_text()
assert "/dev/video10" in message and "v4l2loopback" in message, message
with patch("brio_control.gui.os.path.exists", return_value=True):
    click_start(window)
assert window.client.starts[0]["output"] == "/dev/video10"
''')

    def test_start_stop_uses_the_worker_and_restores_local_idle_state(self):
        self.exercise_window('''
click_start(window)
assert window.client.running
assert window.start_button.get_label() == gui._text("stop")
click_start(window)
assert window.client.stops == 1
assert not window.client.running
assert window.start_button.get_label() == gui._text("start")
window._refresh_preview()
assert window.preview_placeholder.get_text() == gui._text("preview_stopped")
''')

    def test_processing_restores_worker_state_and_preserves_user_changes(self):
        self.exercise_window('''
window.client.processing_state = {"autoframe": True, "zoom": 2.25, "pan_x": 0.2, "pan_y": 0.8, "mirror": True}
window._refresh_state()
assert window.autoframe.get_active()
assert window.zoom_scale.get_value() == 2.25
assert window.mirror.get_active()
window.zoom_scale.set_value(1.5)
window.pan_x_scale.set_value(0.4)
window.mirror.set_active(False)
pump_until(lambda: window.client.processing_state["zoom"] == 1.5)
assert window.client.processing_state["zoom"] == 1.5
assert window.client.processing_state["pan_x"] == 0.4
assert not window.client.processing_state["mirror"]
''')

    def test_profile_save_and_load_are_routed_to_worker(self):
        self.exercise_window('''
window.profile_entry.set_text("Reuniões")
window._on_save_profile(None)
assert window.client.profiles == ["Reuniões"]
assert window.profile_entry.get_text() == ""
window._on_load_profile(None)
assert window.client.loaded_profiles == ["Reuniões"]
''')

    def test_sidebar_navigates_to_every_settings_page(self):
        self.exercise_window('''
for name in ("image", "framing", "background", "profiles", "output"):
    window.nav_buttons[name].emit("clicked")
    assert window.stack.get_visible_child_name() == name
''')

    def test_virtual_output_error_survives_poll_and_local_fallback_starts_safely(self):
        self.exercise_window('''
window.preview_only.set_active(False)
with patch("brio_control.gui.os.path.exists", return_value=False):
    click_start(window)
assert window.error_revealer.get_reveal_child()
message = window.status_label.get_text()
window._refresh_state()
assert window.error_revealer.get_reveal_child()
assert window.status_label.get_text() == message
window.local_preview_button.emit("clicked")
pump_until(lambda: not window._capture_busy)
assert window.preview_only.get_active()
assert window.client.starts[0]["output"] is None
assert not window.error_revealer.get_reveal_child()
''')

    def test_profile_load_is_disabled_until_a_saved_profile_exists(self):
        self.exercise_window('''
assert not window.profile_load_button.get_sensitive()
window.client.profiles = ["Reuniões"]
window._refresh_state()
assert window.profile_load_button.get_sensitive()
window.profile_load_button.emit("clicked")
assert window.client.loaded_profiles == ["Reuniões"]
''')

    def test_manual_control_unavailable_value_and_activation_follow_worker(self):
        self.exercise_window('''
control_id = "0x98091a"
window.client.controls = [{"id": control_id, "name": "White Balance Temperature",
    "kind": "integer", "minimum": 2000, "maximum": 7500, "step": 10,
    "value": None, "active": False, "writable": False}]
window._refresh_state()
widget = window._control_widgets[control_id]
assert not widget.get_sensitive()
assert window._control_values[control_id].get_text() == "—"
assert not window.client.control_calls
window.client.controls[0].update(value=4500, active=True, writable=True)
window._refresh_state()
assert widget.get_sensitive()
assert widget.get_value() == 4500
assert window._control_values[control_id].get_text() == "4500 K"
assert not window.client.control_calls, "state synchronization must not send writes"
widget.set_value(4523)
pump_until(lambda: bool(window.client.control_calls))
assert window.client.control_calls == [(control_id, 4520)]
''')

    def test_capture_rpc_keeps_ui_responsive_and_prevents_duplicate_start(self):
        self.exercise_window('''
entered, release = threading.Event(), threading.Event()
original_start = window.client.start
def blocked_start(options):
    entered.set()
    assert release.wait(2), "test did not release worker"
    original_start(options)
window.client.start = blocked_start
try:
    window.start_button.emit("clicked")
    assert entered.wait(1), "capture RPC was not dispatched"
    assert window._capture_busy
    assert not window.start_button.get_sensitive()
    assert not window.device_dropdown.get_sensitive()
    window.start_button.emit("clicked")
    # The GTK loop remains free while the worker RPC is pending.
    ticks = []
    GLib.idle_add(lambda: ticks.append(True) and False)
    pump_until(lambda: bool(ticks))
    assert not window.client.starts
finally:
    release.set()
pump_until(lambda: not window._capture_busy)
assert len(window.client.starts) == 1
assert window.start_button.get_sensitive()
''')

    def test_dismissed_worker_error_and_warning_stay_hidden_after_poll(self):
        self.exercise_window('''
for field in ("error", "warning"):
    window.client.error = None
    window.client.warning = None
    setattr(window.client, field, "Worker " + field)
    window._refresh_state()
    assert window.error_revealer.get_reveal_child()
    window._on_dismiss_feedback()
    assert not window.error_revealer.get_reveal_child()
    window._refresh_state()
    assert not window.error_revealer.get_reveal_child()
    setattr(window.client, field, "Different worker " + field)
    window._refresh_state()
    assert window.error_revealer.get_reveal_child(), "a new issue must remain visible"
''')

    def test_failed_capture_rpc_restores_controls_and_retry_starts_camera(self):
        self.exercise_window('''
from brio_control.client import ClientError
original_start = window.client.start
def failing_start(options):
    raise ClientError("Camera temporarily unavailable")
window.client.start = failing_start
click_start(window)
assert not window._capture_busy
assert not window.client.running
assert window.start_button.get_sensitive()
assert window.device_dropdown.get_sensitive()
assert window.inspector.get_sensitive()
assert window.error_revealer.get_reveal_child()
assert "Camera temporarily unavailable" in window.status_label.get_text()
window.client.start = original_start
window.retry_button.emit("clicked")
pump_until(lambda: not window._capture_busy)
assert window.client.running
assert len(window.client.starts) == 1
assert not window.error_revealer.get_reveal_child()
''')

    def test_profile_and_state_errors_never_offer_capture_retry(self):
        self.exercise_window('''
from brio_control.client import ClientError
window.profile_entry.set_text("Reuniões")
with patch.object(window.client, "save_profile", side_effect=ClientError("Save failed")):
    window._on_save_profile(None)
assert window.error_revealer.get_reveal_child()
assert not window.retry_button.get_visible()
window.retry_button.emit("clicked")
assert not window.client.starts
with patch.object(window.client, "state", side_effect=ClientError("Worker unavailable")):
    window._refresh_state()
assert not window.retry_button.get_visible()
window.retry_button.emit("clicked")
assert not window.client.starts
''')

    def test_virtual_start_runtime_error_offers_safe_local_fallback(self):
        self.exercise_window('''
from brio_control.client import ClientError
window.preview_only.set_active(False)
original_start = window.client.start
with patch.object(window.client, "start", side_effect=ClientError("Output device busy")), \
     patch("brio_control.gui.os.path.exists", return_value=True):
    click_start(window)
assert window.retry_button.get_visible()
assert window.local_preview_button.get_visible()
assert "Output device busy" in window.status_label.get_text()
window.local_preview_button.emit("clicked")
pump_until(lambda: not window._capture_busy)
assert window.client.starts[0]["output"] is None
assert window.client.running
''')

    def test_failed_pending_changes_block_start_save_load_and_can_be_retried(self):
        self.exercise_window('''
from brio_control.client import ClientError
window.client.profiles = ["Existing"]
window._refresh_state()
window.profile_entry.set_text("New")
for pending_kind in ("control", "processing"):
    if pending_kind == "control":
        window._schedule_control("0x980900", 140)
        failing_method = "set_control"
    else:
        window.zoom_scale.set_value(1.75)
        failing_method = "processing"
    with patch.object(window.client, failing_method, side_effect=ClientError("Write failed")):
        click_start(window)
        window._on_save_profile(None)
        window._on_load_profile(None)
    assert not window.client.starts
    assert window.client.profiles == ["Existing"]
    assert not window.client.loaded_profiles
    assert window.profile_entry.get_text() == "New"
    assert "Write failed" in window.status_label.get_text()
    window._refresh_state()
    if pending_kind == "control":
        assert window._pending_controls["0x980900"] == 140
    else:
        assert window._pending_processing["zoom"] == 1.75
        assert window.zoom_scale.get_value() == 1.75, "poll must preserve unsent change"
    assert window._flush_pending()
    assert not window._pending_controls
    assert window._pending_processing is None
assert window.client.control_calls == [("0x980900", 140)]
assert window.client.processing_state["zoom"] == 1.75
window._on_save_profile(None)
assert window.client.profiles == ["Existing", "New"]
''')

    def test_device_selection_flushes_old_edits_then_targets_new_camera_without_start(self):
        self.exercise_window('''
worker = window.client
worker.devices.append({"path": "/dev/fake-brio-b", "name": "Brio 500 B"})
worker.profiles = ["Mesa"]
window._refresh_state()
operations = []
original_select = worker.select_device
def record_select(path):
    operations.append(("select", path))
    return original_select(path)
def record_control(control_id, value):
    operations.append(("control", worker.selected_device, value))
def record_load(name):
    operations.append(("profile", worker.selected_device, name))
with patch.object(worker, "select_device", side_effect=record_select), \
     patch.object(worker, "set_control", side_effect=record_control), \
     patch.object(worker, "load_profile", side_effect=record_load):
    window._schedule_control("0x980900", 140)
    window.device_dropdown.set_selected(1)
    assert operations[:2] == [("control", "/dev/fake-brio", 140), ("select", "/dev/fake-brio-b")], operations
    assert window._selected_device == "/dev/fake-brio-b"
    window._schedule_control("0x980900", 160)
    assert window._flush_pending()
    window._on_load_profile(None)
    assert operations[2:] == [("control", "/dev/fake-brio-b", 160), ("profile", "/dev/fake-brio-b", "Mesa")], operations
assert not worker.starts
assert worker.selection_calls == ["/dev/fake-brio-b"]
worker.selected_device = "/dev/fake-brio"
window._refresh_state()
assert window.device_dropdown.get_selected() == 0
assert worker.selection_calls == ["/dev/fake-brio-b"], "programmatic sync must not select again"
''')

    def test_failed_device_selection_reverts_without_recursive_rpc_or_capture(self):
        self.exercise_window('''
from brio_control.client import ClientError
worker = window.client
worker.devices.append({"path": "/dev/fake-brio-b", "name": "Brio 500 B"})
window._refresh_state()
with patch.object(worker, "select_device", side_effect=ClientError("Camera busy")) as select:
    window.device_dropdown.set_selected(1)
    assert select.call_count == 1
assert window.device_dropdown.get_selected() == 0
assert window._selected_device == "/dev/fake-brio"
assert "Camera busy" in window.status_label.get_text()
assert not worker.starts
''')

    def test_background_hold_is_reused_across_reopen_and_released_after_stop(self):
        self.exercise_window('''
click_start(window)
worker = window.client
with patch.object(app, "hold", wraps=app.hold) as hold, \
     patch.object(app, "release", wraps=app.release) as release, \
     patch.object(FakeClient, "__new__", return_value=worker), \
     patch.object(FakeClient, "__init__", return_value=None), \
     patch.object(app, "send_notification"), patch.object(app, "withdraw_notification"):
    assert not window._on_close_request(window)
    assert app._held_for_background
    watch_source = app._watch_source
    assert watch_source
    window.destroy()
    app.do_activate()
    window = app.window
    assert window.client.running
    assert not window._on_close_request(window)
    assert app._watch_source == watch_source, "reopening must reuse the background watcher"
    assert hold.call_count == 1, "one ongoing capture needs only one application hold"
    assert release.call_count == 0
    app._stop_from_notification()
    assert worker.stops == 1
    assert not worker.running
    assert release.call_count == 1
    assert not app._held_for_background
    assert app._watch_source == 0
    app._release_background_hold()
    assert release.call_count == 1
''')

    def test_close_during_capture_rpc_is_completed_after_rpc_finishes(self):
        self.exercise_window('''
entered, release = threading.Event(), threading.Event()
original_start = window.client.start
def blocked_start(options):
    entered.set()
    assert release.wait(2)
    original_start(options)
window.client.start = blocked_start
with patch.object(Gtk.Window, "close") as close:
    try:
        window.start_button.emit("clicked")
        assert entered.wait(1)
        assert window._on_close_request(window)
        assert window._close_pending
        assert not window._closed
        assert not close.called
    finally:
        release.set()
    pump_until(lambda: not window._capture_busy)
    assert close.call_count == 1
    assert not window._close_pending
    assert window.client.running
    assert window.client.stops == 0, "closing must preserve background capture"
''')

    def test_dismissed_connection_rpc_error_stays_hidden_and_recovers(self):
        self.exercise_window('''
from brio_control.client import ClientError
with patch.object(window.client, "state", side_effect=ClientError("Connection lost")):
    window._refresh_state()
    assert window.error_revealer.get_reveal_child()
    window._on_dismiss_feedback()
    for _ in range(3):
        window._refresh_state()
        assert not window.error_revealer.get_reveal_child(), "same RPC failure reopened dismissed feedback"
window._refresh_state()
assert not window.error_revealer.get_reveal_child()
assert not window.status_label.get_text()
assert window._connection_error is None
assert not window.client.starts
''')

    def test_close_after_successful_start_preserves_running_when_poll_fails(self):
        for return_state in (False, True):
            with self.subTest(rpc_returns_state=return_state):
                self.exercise_window('''
from brio_control.client import ClientError
entered, release = threading.Event(), threading.Event()
def blocked_start(options):
    entered.set()
    assert release.wait(2)
    window.client.running = True
    window.client.starts.append(options)
    return {"running": True} if RETURN_STATE else None
window.client.start = blocked_start
with patch.object(window.client, "state", side_effect=ClientError("Follow-up poll failed")), \
     patch.object(Gtk.Application, "send_notification"), \
     patch.object(Gtk.Window, "close", side_effect=lambda: window._on_close_request(window)) as close:
    try:
        window.start_button.emit("clicked")
        assert entered.wait(1)
        assert window._on_close_request(window)
        assert window._close_pending
    finally:
        release.set()
    pump_until(lambda: not window._capture_busy)
    assert close.call_count == 1
    assert window._closed
    assert window._state["running"], "successful Start state must survive failed State poll"
    assert window.client.running
    assert window.client.stops == 0
    assert window.get_application()._held_for_background
'''.replace("RETURN_STATE", repr(return_state)))

    def test_observed_rotation_is_shown_without_claiming_software_validation(self):
        self.exercise_window('''
for portuguese, expected in (
    (True, "Show Mode automático: Rotação ao inclinar observada nesta Brio. Controle por software ainda não comprovado."),
    (False, "Automatic Show Mode: Rotation when tilted was observed on this Brio. Software control is not yet verified."),
):
    window._tr = lambda key, **values: gui._text(key, portuguese=portuguese, **values)
    window.client.show_mode = {"validated": False, "rotation_observed": True, "control_available": False}
    window._refresh_state()
    assert window.show_status.get_text() == expected, window.show_status.get_text()
    assert not window.client.show_mode["validated"]
    window.client.show_mode["rotation_observed"] = False
    window._refresh_state()
    assert window.show_status.get_text().endswith(gui._text("show_pending", portuguese=portuguese))
''')


class GuiContractTests(unittest.TestCase):
    def test_processing_values_have_stable_defaults(self):
        self.assertEqual(
            _normalise_processing(None),
            {"autoframe": False, "zoom": 1.0, "pan_x": 0.0, "pan_y": 0.0, "mirror": False,
             "background_mode": "off", "background_image": None, "background_blur": 12.0},
        )

    def test_processing_values_keep_worker_configuration(self):
        self.assertEqual(
            _normalise_processing({"autoframe": 1, "zoom": 2.25, "pan_x": 0.2,
                                   "pan_y": 0.8, "mirror": True}),
            {"autoframe": True, "zoom": 2.25, "pan_x": 0.2, "pan_y": 0.8, "mirror": True,
             "background_mode": "off", "background_image": None, "background_blur": 12.0},
        )

    def test_preview_only_never_selects_a_virtual_device(self):
        options = _start_options(device="/dev/video2", output="/dev/video10",
                                 preview_only=True, width=1280, height=720, autoframe=False)
        self.assertEqual(options, {"device": "/dev/video2", "output": None,
                                   "width": 1280, "height": 720, "autoframe": False})

    def test_virtual_camera_start_preserves_resolution_and_path(self):
        options = _start_options(device="/dev/video6", output="/dev/video10",
                                 preview_only=False, width=1920, height=1080, autoframe=True)
        self.assertEqual(options["output"], "/dev/video10")
        self.assertEqual((options["width"], options["height"]), (1920, 1080))
        self.assertTrue(options["autoframe"])

    def test_catalog_supports_both_languages_and_format_arguments(self):
        self.assertEqual(_text("start", portuguese=True), "Iniciar câmera")
        self.assertEqual(_text("start", portuguese=False), "Start camera")
        self.assertEqual(_text("fps", portuguese=True, value=27.5), "27.5 quadros/s")
        self.assertEqual(_text("preview_stopped", portuguese=True),
                         "Câmera parada. Selecione Iniciar câmera.")
        self.assertEqual(_text("preview_stopped", portuguese=False),
                         "Camera stopped. Select Start camera.")
        self.assertIn("v4l2loopback", _text("output_missing", portuguese=True, path="/dev/video10"))
        self.assertIn("v4l2loopback", _text("output_missing", portuguese=False, path="/dev/video10"))

    def test_integer_control_values_are_aligned_to_v4l2_step_from_minimum(self):
        self.assertEqual(_quantise_control(10_000, -72_000, 72_000, 3_600), 10_800)
        self.assertEqual(_quantise_control(90_000, -72_000, 72_000, 3_600), 72_000)
        self.assertEqual(_quantise_control(-90_000, -72_000, 72_000, 3_600), -72_000)


if __name__ == "__main__":
    unittest.main()

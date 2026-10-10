"""Interface GTK4 para Enquadra.

A interface conversa exclusivamente com o worker via ``Client``. Ela nunca abre
um nó V4L2 físico; a leitura do preview usa o canal de memória compartilhada do
worker quando este está ativo.
"""

from __future__ import annotations

import locale
import math
import os
import threading
from pathlib import Path

from .ui_controls import control_info, option_label, sort_controls
from .ui_style import stylesheet
from typing import Any


_MESSAGES = {
    "app_title": ("Enquadra", "Enquadra"),
    "camera": ("Câmera", "Camera"),
    "choose_camera": ("Selecione uma Brio 500", "Select a Brio 500"),
    "start": ("Iniciar câmera", "Start camera"),
    "stop": ("Parar câmera", "Stop camera"),
    "preview_only": ("Somente preview local (sem câmera virtual)", "Local preview only (no virtual camera)"),
    "virtual_device": ("Saída da câmera virtual", "Virtual camera output"),
    "resolution": ("Resolução da câmera virtual", "Virtual camera resolution"),
    "image": ("Controles da Brio", "Brio controls"),
    "no_controls": ("Inicie a câmera para carregar os controles disponíveis.", "Start the camera to load available controls."),
    "profiles": ("Perfis", "Profiles"),
    "profile_name": ("Nome do perfil", "Profile name"),
    "save_profile": ("Salvar perfil", "Save profile"),
    "load_profile": ("Carregar", "Load"),
    "framing": ("Enquadramento digital", "Digital framing"),
    "autoframe": ("Enquadramento automático", "Automatic framing"),
    "zoom": ("Zoom digital", "Digital zoom"),
    "pan_x": ("Deslocamento horizontal", "Horizontal position"),
    "pan_y": ("Deslocamento vertical", "Vertical position"),
    "mirror": ("Espelhar imagem", "Mirror image"),
    "show_mode": ("Show Mode automático", "Automatic Show Mode"),
    "show_pending": ("Validação pendente na Brio 500. Incline a câmera para confirmar a inversão automática.", "Brio 500 validation pending. Tilt the camera to confirm automatic image rotation."),
    "show_rotation_observed": ("Rotação ao inclinar observada nesta Brio. Controle por software ainda não comprovado.", "Rotation when tilted was observed on this Brio. Software control is not yet verified."),
    "show_message": ("O comportamento automático ainda não foi comprovado neste modelo.", "Automatic behavior has not yet been verified on this model."),
    "ready": ("Pronto. A câmera só inicia quando você selecionar Iniciar câmera.", "Ready. The camera starts only when you select Start camera."),
    "running": ("Câmera em execução", "Camera is running"),
    "fps": ("{value:.1f} quadros/s", "{value:.1f} frames/s"),
    "no_preview": ("Preview indisponível", "Preview unavailable"),
    "preview_stopped": ("Câmera parada. Selecione Iniciar câmera.", "Camera stopped. Select Start camera."),
    "processing_continues": ("A captura continua em segundo plano. Reabra o Enquadra para pará-la.", "Capture continues in the background. Reopen Enquadra to stop it."),
    "notification_title": ("Enquadra continua ativo", "Enquadra is still running"),
    "notification_stop": ("Parar captura", "Stop capture"),
    "select_device": ("Selecione uma câmera antes de iniciar.", "Select a camera before starting."),
    "profile_saved": ("Perfil salvo.", "Profile saved."),
    "profile_loaded": ("Perfil carregado.", "Profile loaded."),
    "capture_error": ("Não foi possível iniciar a câmera.", "Could not start the camera."),
    "no_profiles": ("Nenhum perfil salvo", "No saved profiles"),
    "control_error": ("Não foi possível aplicar o controle.", "Could not apply the control."),
    "output_required": ("Informe o caminho da câmera virtual.", "Enter the virtual camera path."),
    "output_missing": ("A saída virtual {path} não existe. Configure o v4l2loopback ou marque Somente preview local.", "Virtual output {path} does not exist. Configure v4l2loopback or select Local preview only."),
    "warning": ("Aviso: {message}", "Warning: {message}"),
}



_MESSAGES.update({
    "studio": ("Sua câmera. Seu enquadramento.", "Your camera. Your frame."),
    "preview": ("Pré-visualização", "Preview"),
    "image_tab": ("Imagem", "Image"),
    "framing_tab": ("Quadro", "Framing"),
    "background_tab": ("Fundo", "Background"),
    "background_title": ("Seu fundo", "Your background"),
    "background_intro": ("Veja o resultado no preview. Para usar em Meet ou Zoom, selecione a câmera virtual {name} na chamada.", "Check the result in the preview. For Meet or Zoom, select the {name} virtual camera in your call."),
    "background_off": ("Original", "Original"),
    "background_blur_mode": ("Desfocar", "Blur"),
    "background_image_mode": ("Imagem", "Image"),
    "background_blur": ("Intensidade do desfoque", "Blur strength"),
    "background_choose": ("Escolher imagem…", "Choose image…"),
    "background_empty": ("Nenhuma imagem selecionada", "No image selected"),
    "background_formats": ("PNG ou JPEG · até 20 MiB · até 4096 px por dimensão e 16 MP", "PNG or JPEG · up to 20 MiB · up to 4096 px per dimension and 16 MP"),
    "background_tips": ("O processamento é local. Use boa iluminação. Para mostrar papéis com Show Mode, escolha Original.", "Processing stays on this computer. Use good lighting. To show papers with Show Mode, choose Original."),
    "background_local_file": ("Escolha uma imagem deste computador.", "Choose an image from this computer."),
    "choose": ("Escolher", "Choose"),
    "cancel": ("Cancelar", "Cancel"),
    "output_tab": ("Saída", "Output"),
    "image_title": ("Ajuste sua imagem", "Tune your image"),
    "image_intro": ("Os ajustes da câmera valem também nos aplicativos que usam a Brio diretamente.", "Camera adjustments also apply to apps using the Brio directly."),
    "framing_title": ("Encontre seu enquadramento", "Find your framing"),
    "framing_intro": ("O enquadramento digital aparece neste preview e na câmera virtual do {name}.", "Digital framing appears in this preview and the {name} virtual camera."),
    "output_title": ("Escolha como usar", "Choose how to use it"),
    "profiles_intro": ("Guarde os ajustes da câmera e do enquadramento para usar de novo.", "Keep camera and framing adjustments for next time."),
    "color_group": ("Cor e detalhe", "Color and detail"),
    "exposure_group": ("Luz e exposição", "Light and exposure"),
    "focus_group": ("Foco", "Focus"),
    "native_framing_group": ("Enquadramento da câmera", "Camera framing"),
    "native_advanced": ("Zoom e posição da câmera", "Camera zoom and position"),
    "advanced_group": ("Outros controles", "Other controls"),
    "digital_group": ("Enquadramento digital", "Digital framing"),
    "automatic_hint": ("Acompanha seu rosto com um recorte suave da imagem.", "Follows your face with a smooth image crop."),
    "autoframe_offset": ("Com o automático ativo, a posição desloca o enquadramento em relação ao rosto.", "With automatic framing, position offsets the frame relative to your face."),
    "reset_framing": ("Repor zoom e posição", "Reset zoom and position"),
    "preview_local": ("Preview local", "Local preview"),
    "local_hint": ("Use para testar e ajustar a imagem. Para enviar os efeitos digitais a uma chamada, use a câmera virtual.", "Use it to test and adjust your image. To send digital effects to a call, use the virtual camera."),
    "preview_only": ("Usar apenas o preview local", "Use local preview only"),
    "virtual_hint": ("Para chamadas, o dispositivo virtual precisa estar instalado no Linux. Depois selecione {name} no aplicativo da chamada.", "For calls, a virtual device must be installed on Linux. Then select {name} in your calling app."),
    "resolution": ("Resolução de saída", "Output resolution"),
    "resolution_hint": ("720p prioriza fluidez; 1080p oferece mais detalhe.", "720p favors smoothness; 1080p offers more detail."),
    "output_restart": ("Pare a câmera para alterar a saída ou a resolução.", "Stop the camera to change output or resolution."),
    "virtual_active": ("Câmera virtual ativa · {path}", "Virtual camera active · {path}"),
    "virtual_call_step": ("No Meet, Zoom ou outro aplicativo, selecione {name} como câmera.", "In Meet, Zoom or another app, select {name} as your camera."),
    "selected_profile": ("Último perfil selecionado: {name}", "Last selected profile: {name}"),
    "virtual_available": ("Dispositivo encontrado · verificação ao iniciar", "Device found · checked when starting"),
    "virtual_unavailable": ("Câmera virtual ainda não configurada", "Virtual camera is not set up yet"),
    "virtual_setup": ("O preview local já funciona. A câmera virtual requer o módulo v4l2loopback no sistema.", "Local preview works already. The virtual camera requires the v4l2loopback system module."),
    "ready_short": ("Parada", "Stopped"),
    "live_short": ("Ao vivo", "Live"),
    "connecting": ("Iniciando…", "Starting…"),
    "stopping": ("Parando…", "Stopping…"),
    "empty_title": ("Tudo pronto para começar", "Ready when you are"),
    "empty_no_device": ("Conecte sua Brio 500", "Connect your Brio 500"),
    "empty_no_device_hint": ("Assim que a câmera for reconhecida, ela aparece no seletor acima.", "Once detected, your camera will appear in the selector above."),
    "empty_running": ("Aguardando imagem", "Waiting for image"),
    "empty_error": ("Vamos recuperar o preview", "Let's recover your preview"),
    "privacy_hint": ("O processamento do Enquadra é local.", "Enquadra processing stays on this computer."),
    "close_hint": ("Ao fechar a janela, a captura continua. Use Parar câmera para encerrá-la.", "Closing the window keeps capture running. Use Stop camera to end it."),
    "changes_live": ("Ajustes aplicados na hora", "Adjustments apply immediately"),
    "empty_profiles": ("Seu primeiro perfil", "Your first profile"),
    "empty_profiles_hint": ("Ajuste a imagem e salve uma combinação. Você pode voltar a ela com um clique.", "Tune your image and save a combination. Return to it with one click."),
    "saved_profiles": ("Seus perfis", "Your profiles"),
    "save_current": ("Salvar ajustes atuais", "Save current adjustments"),
    "profile_placeholder": ("Ex.: Reuniões, mesa ou pouca luz", "E.g. Meetings, desk or low light"),
    "profile_missing_name": ("Dê um nome ao perfil para salvar os ajustes.", "Name your profile to save the adjustments."),
    "manual_wb_hint": ("Desative o balanço automático para ajustar a temperatura.", "Turn off auto white balance to adjust temperature."),
    "manual_exposure_hint": ("Escolha exposição Manual para ajustar este controle.", "Choose Manual exposure to adjust this control."),
    "manual_focus_hint": ("Desative o foco automático para ajustar o foco manual.", "Turn off auto focus to adjust manual focus."),
    "tracking_control_hint": ("Gerenciado pelo enquadramento automático durante a captura.", "Managed by automatic framing during capture."),
    "readonly_hint": ("A câmera não permite alterar este controle no estado atual.", "The camera cannot change this control in its current state."),
    "unavailable_value": ("Automático", "Automatic"),
    "feedback_title": ("Não foi possível concluir", "Could not complete the action"),
    "feedback_warning": ("Atenção aos ajustes", "Check your adjustments"),
    "feedback_virtual_title": ("A câmera virtual não está disponível", "Virtual camera is unavailable"),
    "feedback_virtual_hint": ("Continue com o preview local para ajustar sua Brio.", "Continue with local preview to adjust your Brio."),
    "retry": ("Tentar novamente", "Try again"),
    "use_local": ("Usar preview local", "Use local preview"),
    "dismiss": ("Dispensar aviso", "Dismiss message"),
    "details": ("Detalhes", "Details"),
    "show_label": ("Rotação para a mesa", "Desk rotation"),
    "show_observed": ("Observada nesta Brio", "Observed on this Brio"),
    "show_not_observed": ("Ainda não verificada", "Not verified yet"),
})

def _locale_is_pt() -> bool:
    """Use português quando a sessão solicita português; inglês nos demais casos."""
    for name in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(name, "").split(":", 1)[0].lower()
        if value:
            return value.startswith("pt")
    try:
        return (locale.getlocale()[0] or "").lower().startswith("pt")
    except (TypeError, ValueError):
        return True


def _text(key: str, *, portuguese: bool | None = None, **values: Any) -> str:
    """Small local UI catalog; keeps the app usable before gettext catalogs exist."""
    language = _locale_is_pt() if portuguese is None else portuguese
    message = _MESSAGES.get(key, (key, key))[0 if language else 1]
    return message.format(**values) if values else message


def _normalise_processing(value: dict[str, Any] | None) -> dict[str, Any]:
    current = value or {}
    return {
        "autoframe": bool(current.get("autoframe", False)),
        "zoom": float(current.get("zoom", 1.0)),
        "pan_x": float(current.get("pan_x", 0.0)),
        "pan_y": float(current.get("pan_y", 0.0)),
        "mirror": bool(current.get("mirror", False)),
        "background_mode": current.get("background_mode", "off"),
        "background_image": current.get("background_image"),
        "background_blur": float(current.get("background_blur", 12.0)),
    }


def _start_options(
    *, device: str | None, output: str, preview_only: bool,
    width: int, height: int, autoframe: bool,
) -> dict[str, Any]:
    return {
        "device": device,
        "output": None if preview_only else output,
        "width": width,
        "height": height,
        "autoframe": bool(autoframe),
    }


def _quantise_control(value: int, minimum: int, maximum: int, step: int) -> int:
    """Round a V4L2 integer to its advertised grid before sending it to the worker."""
    step = max(1, int(step))
    minimum, maximum, value = int(minimum), int(maximum), int(value)
    value = min(max(value, minimum), maximum)
    offset = value - minimum
    aligned = minimum + math.floor(offset / step + 0.5) * step
    return min(max(aligned, minimum), maximum)


def _load_gtk() -> tuple[Any, Any, Any, Any]:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk

    return Gtk, Gdk, Gio, GLib


def run() -> int:
    """Start GTK4; imports UI/runtime dependencies only when the app is launched."""
    Gtk, Gdk, Gio, GLib = _load_gtk()
    from .client import Client, ClientError, ensure_worker

    try:
        from .preview import PreviewReader
    except ImportError:
        PreviewReader = None

    class MainWindow(Gtk.ApplicationWindow):
        def __init__(self, app: Any):
            super().__init__(application=app, title=_text("app_title"))
            self.set_default_size(1160, 760)
            self.set_size_request(760, 560)
            self.add_css_class("brio-window")
            ensure_worker()
            self.client = Client()
            self._state: dict[str, Any] = {}
            self._controls_signature: tuple[str, ...] = ()
            self._control_widgets: dict[str, Any] = {}
            self._control_hints: dict[str, Any] = {}
            self._control_values: dict[str, Any] = {}
            self._control_options: dict[str, list[dict[str, Any]]] = {}
            self._control_grids: dict[str, tuple[int, int, int]] = {}
            self._control_timeouts: dict[str, int] = {}
            self._pending_controls: dict[str, int] = {}
            self._processing_timeout = 0
            self._pending_processing: dict[str, Any] | None = None
            self._processing_failed = False
            self._background_image: str | None = None
            self._background_chooser: Any = None
            self._preview_reader: Any = None
            self._preview_key: str | None = None
            self._selected_device: str | None = None
            self._syncing_device = False
            self._device_paths: list[str] = []
            self._profile_names: list[str] = []
            self._updating = False
            self._closed = False
            self._capture_busy = False
            self._close_pending = False
            self._local_error: str | None = None
            self._connection_error: str | None = None
            self._retry_start = False
            self._failed_virtual_start = False
            self._feedback_message = ""
            self._dismissed_feedback: str | None = None
            self._output_synced = False
            self._preferences_synced = False
            self._syncing_preferences = False
            self._preferences_timeout = 0
            self._pending_preferences: dict[str, Any] = {}
            self._last_profile: str | None = None
            self._virtual_name_labels: dict[str, Any] = {}
            self._tr = self._translate

            self._build()
            self.connect("close-request", self._on_close_request)
            self._state_source = GLib.timeout_add(1000, self._refresh_state)
            self._preview_source = GLib.timeout_add(33, self._refresh_preview)
            self._refresh_state()

        def _translate(self, key: str, **values: Any) -> str:
            if key in ("background_intro", "framing_intro", "virtual_hint", "virtual_call_step"):
                values.setdefault("name", self._state.get("virtual_camera_name") or "Enquadra")
            return _text(key, **values)

        def _sync_theme(self, *_args: Any) -> None:
            theme = self._settings.get_property("gtk-theme-name") or ""
            dark = bool(self._settings.get_property("gtk-application-prefer-dark-theme")) or "dark" in theme.lower()
            self._css.load_from_data(stylesheet(dark).encode())

        def _label(self, text: str, css: str | None = None) -> Any:
            label = Gtk.Label(label=text, xalign=0, wrap=True)
            label.set_wrap_mode(2)  # Pango.WrapMode.WORD_CHAR for narrow windows.
            label.set_max_width_chars(42)
            if css:
                label.add_css_class(css)
            return label

        def _build(self) -> None:
            self._css = Gtk.CssProvider()
            self._settings = Gtk.Settings.get_default()
            self._sync_theme()
            self._settings.connect("notify::gtk-application-prefer-dark-theme", self._sync_theme)
            self._settings.connect("notify::gtk-theme-name", self._sync_theme)
            self.set_icon_name("enquadra")
            Gtk.StyleContext.add_provider_for_display(
                self.get_display(), self._css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.set_child(root)
            header = Gtk.HeaderBar()
            brand = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
            brand.append(self._label(self._tr("app_title"), "brand-title"))
            brand.append(self._label(self._tr("studio"), "brand-subtitle"))
            brand_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            self.brand_icon = Gtk.Image.new_from_file(str(Path(__file__).resolve().parent.parent / "branding" / "enquadra.svg"))
            self.brand_icon.set_pixel_size(36)
            brand_row.append(self.brand_icon)
            brand_row.append(brand)
            header.set_title_widget(brand_row)
            root.append(header)

            outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
            for edge in ("top", "bottom", "start", "end"):
                getattr(outer, f"set_margin_{edge}")(20)
            outer.set_vexpand(True)
            root.append(outer)
            toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            toolbar.add_css_class("camera-bar")
            outer.append(toolbar)
            camera_icon = Gtk.Image.new_from_icon_name("camera-web-symbolic")
            camera_icon.set_pixel_size(24)
            toolbar.append(camera_icon)
            camera_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            camera_text.append(self._label(self._tr("camera"), "caption"))
            self.device_model = Gtk.StringList.new([self._tr("choose_camera")])
            self.device_dropdown = Gtk.DropDown.new(self.device_model, None)
            self.device_dropdown.connect("notify::selected", self._on_selected_device_changed)
            self.device_dropdown.set_hexpand(True)
            self.device_dropdown.set_tooltip_text(self._tr("choose_camera"))
            camera_text.append(self.device_dropdown)
            camera_text.set_hexpand(True)
            toolbar.append(camera_text)
            self.capture_spinner = Gtk.Spinner()
            toolbar.append(self.capture_spinner)
            self.start_button = Gtk.Button(label=self._tr("start"))
            self.start_button.add_css_class("primary-action")
            self.start_button.connect("clicked", self._on_start_clicked)
            toolbar.append(self.start_button)

            # Errors remain visible until dismissed or a new action succeeds.
            self.error_revealer = Gtk.Revealer()
            outer.append(self.error_revealer)
            self.feedback_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            self.feedback_box.add_css_class("feedback-banner")
            self.error_revealer.set_child(self.feedback_box)
            feedback_copy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            feedback_copy.set_hexpand(True)
            self.feedback_title = self._label("", "feedback-title")
            feedback_copy.append(self.feedback_title)
            self.feedback_hint = self._label("", "helper")
            feedback_copy.append(self.feedback_hint)
            self.status_label = self._label("", "helper")
            self.feedback_details = Gtk.Expander(label=self._tr("details"))
            self.feedback_details.set_child(self.status_label)
            feedback_copy.append(self.feedback_details)
            self.feedback_box.append(feedback_copy)
            feedback_actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            self.retry_button = Gtk.Button(label=self._tr("retry"))
            self.retry_button.connect("clicked", self._on_retry)
            feedback_actions.append(self.retry_button)
            self.local_preview_button = Gtk.Button(label=self._tr("use_local"))
            self.local_preview_button.connect("clicked", self._on_use_local)
            feedback_actions.append(self.local_preview_button)
            dismiss = Gtk.Button.new_from_icon_name("window-close-symbolic")
            dismiss.set_tooltip_text(self._tr("dismiss"))
            dismiss.set_valign(Gtk.Align.START)
            dismiss.connect("clicked", self._on_dismiss_feedback)
            self.feedback_box.append(feedback_actions)
            self.feedback_box.append(dismiss)

            body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
            body.set_vexpand(True)
            outer.append(body)
            preview_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            preview_column.set_hexpand(True)
            preview_column.set_vexpand(True)
            body.append(preview_column)
            preview_heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            preview_title = self._label(self._tr("preview"), "workspace-title")
            preview_title.set_hexpand(True)
            preview_heading.append(preview_title)
            self.live_status = Gtk.Label(label=self._tr("ready_short"))
            self.live_status.add_css_class("status-pill")
            preview_heading.append(self.live_status)
            preview_column.append(preview_heading)
            preview_overlay = Gtk.Overlay()
            preview_overlay.set_hexpand(True)
            preview_overlay.set_vexpand(True)
            preview_overlay.add_css_class("preview-surface")
            preview_overlay.set_overflow(Gtk.Overflow.HIDDEN)
            self.picture = Gtk.Picture()
            self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            self.picture.set_can_shrink(True)
            self.picture.set_hexpand(True)
            self.picture.set_vexpand(True)
            preview_overlay.set_child(self.picture)
            self.preview_empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            self.preview_empty.set_halign(Gtk.Align.CENTER)
            self.preview_empty.set_valign(Gtk.Align.CENTER)
            self.preview_empty.add_css_class("preview-empty")
            self.preview_empty.set_can_target(False)
            self.preview_icon = Gtk.Image.new_from_file(str(Path(__file__).resolve().parent.parent / "branding" / "enquadra.svg"))
            self.preview_icon.set_pixel_size(64)
            self.preview_icon.add_css_class("preview-empty-icon")
            self.preview_empty.append(self.preview_icon)
            self.preview_title = self._label(self._tr("empty_title"), "preview-empty-title")
            self.preview_title.set_justify(Gtk.Justification.CENTER)
            self.preview_title.set_xalign(.5)
            self.preview_empty.append(self.preview_title)
            self.preview_placeholder = self._label(self._tr("preview_stopped"), "preview-empty-text")
            self.preview_placeholder.set_xalign(.5)
            self.preview_placeholder.set_justify(Gtk.Justification.CENTER)
            self.preview_empty.append(self.preview_placeholder)
            preview_overlay.add_overlay(self.preview_empty)
            self.fps_label = Gtk.Label()
            self.fps_label.set_halign(Gtk.Align.END)
            self.fps_label.set_valign(Gtk.Align.END)
            self.fps_label.set_margin_end(12)
            self.fps_label.set_margin_bottom(12)
            self.fps_label.add_css_class("preview-meta")
            self.fps_label.set_visible(False)
            preview_overlay.add_overlay(self.fps_label)
            preview_column.append(preview_overlay)
            self.preview_footer = self._label(self._tr("privacy_hint"), "helper")
            self.preview_footer.set_max_width_chars(70)
            preview_column.append(self.preview_footer)
            show_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            show_box.add_css_class("info-strip")
            show_icon = Gtk.Image.new_from_icon_name("object-rotate-right-symbolic")
            show_icon.set_pixel_size(20)
            show_icon.set_valign(Gtk.Align.START)
            show_box.append(show_icon)
            show_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            show_text.set_hexpand(True)
            show_text.append(self._label(self._tr("show_label"), "section-title"))
            self.show_status = self._label("", "helper")
            self.show_status.set_max_width_chars(80)
            show_text.append(self.show_status)
            show_box.append(show_text)
            preview_column.append(show_box)

            self.inspector = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.inspector.add_css_class("inspector")
            self.inspector.set_size_request(356, -1)
            self.inspector.set_hexpand(False)
            body.append(self.inspector)
            navigation = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
            navigation.add_css_class("inspector-nav")
            self.inspector.append(navigation)
            self.stack = Gtk.Stack()
            self.stack.set_vexpand(True)
            self.stack.set_hhomogeneous(False)
            self.stack.set_vhomogeneous(False)
            self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
            self.inspector.append(self.stack)
            self.nav_buttons = {}
            first = None
            for key, title in (("image", "image_tab"), ("framing", "framing_tab"),
                               ("background", "background_tab"), ("profiles", "profiles"), ("output", "output_tab")):
                button = Gtk.ToggleButton(label=self._tr(title))
                if first is None:
                    first = button
                else:
                    button.set_group(first)
                button.connect("toggled", self._on_page_selected, key)
                navigation.append(button)
                self.nav_buttons[key] = button
            self._group_boxes = {}
            image_page = self._new_page("image", "image_title", "image_intro")
            self.controls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            image_page.append(self.controls_box)
            framing_page = self._new_page("framing", "framing_title", "framing_intro")
            self.native_controls_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            auto_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            auto_row.add_css_class("control-row")
            self.autoframe = Gtk.CheckButton(label=self._tr("autoframe"))
            self.autoframe.connect("toggled", self._on_processing_changed)
            auto_row.append(self.autoframe)
            auto_row.append(self._label(self._tr("automatic_hint"), "helper"))
            framing_page.append(auto_row)
            framing_page.append(self.native_controls_box)
            self._append_section_title(framing_page, self._tr("digital_group"))
            self.zoom_scale = self._new_scale(framing_page, "zoom", 1.0, 4.0, .05)
            self.pan_x_scale = self._new_scale(framing_page, "pan_x", -1.0, 1.0, .01)
            self.pan_y_scale = self._new_scale(framing_page, "pan_y", -1.0, 1.0, .01)
            self.autoframe_hint = self._label(self._tr("autoframe_offset"), "helper")
            framing_page.append(self.autoframe_hint)
            reset = Gtk.Button(label=self._tr("reset_framing"))
            reset.connect("clicked", self._on_reset_framing)
            framing_page.append(reset)
            self.mirror = Gtk.CheckButton(label=self._tr("mirror"))
            self.mirror.set_margin_top(12)
            self.mirror.connect("toggled", self._on_processing_changed)
            framing_page.append(self.mirror)
            self._build_background(self._new_page("background", "background_title", "background_intro"))
            self._build_profiles(self._new_page("profiles", "profiles", "profiles_intro"))
            self._build_output(self._new_page("output", "output_title", None))
            self.nav_buttons["image"].set_active(True)
            self._set_show_mode({})

        def _new_page(self, name: str, title: str, intro: str | None) -> Any:
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_vexpand(True)
            page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            page.add_css_class("settings-page")
            page.append(self._label(self._tr(title), "page-title"))
            if intro:
                intro_label = self._label(self._tr(intro), "helper")
                if intro in ("background_intro", "framing_intro"):
                    self._virtual_name_labels[intro] = intro_label
                page.append(intro_label)
            scroller.set_child(page)
            self.stack.add_named(scroller, name)
            return page

        def _on_page_selected(self, button: Any, name: str) -> None:
            if button.get_active():
                self.stack.set_visible_child_name(name)

        def _build_background(self, page: Any) -> None:
            modes = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            modes.add_css_class("control-row")
            self.background_buttons = {}
            first = None
            for mode, key in (("off", "background_off"), ("blur", "background_blur_mode"),
                              ("image", "background_image_mode")):
                button = Gtk.CheckButton(label=self._tr(key))
                if first is None:
                    first = button
                else:
                    button.set_group(first)
                button.connect("toggled", self._on_background_mode_changed, mode)
                self.background_buttons[mode] = button
                modes.append(button)
            page.append(modes)
            self.background_blur_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self.background_blur_scale = self._new_scale(self.background_blur_box, "background_blur", 1, 30, 1)
            page.append(self.background_blur_box)
            self.background_image_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.background_choose_button = Gtk.Button(label=self._tr("background_choose"))
            self.background_choose_button.connect("clicked", self._on_choose_background)
            self.background_image_box.append(self.background_choose_button)
            self.background_filename = self._label(self._tr("background_empty"), "helper")
            self.background_image_box.append(self.background_filename)
            self.background_image_box.append(self._label(self._tr("background_formats"), "helper"))
            page.append(self.background_image_box)
            tips = self._label(self._tr("background_tips"), "helper")
            tips.add_css_class("info-strip")
            page.append(tips)

        def _update_background_visibility(self) -> None:
            self.background_blur_box.set_visible(self.background_buttons["blur"].get_active())
            # Keep the chooser available in Original, without applying an effect.
            self.background_filename.set_text(os.path.basename(self._background_image)
                                             if self._background_image else self._tr("background_empty"))

        def _on_background_mode_changed(self, button: Any, mode: str) -> None:
            if self._updating or not button.get_active() or self._closed:
                return
            if mode == "image" and not self._background_image:
                # The picker is a proposal: cancellation must not change the mode.
                current = self._pending_processing or _normalise_processing(self._state.get("processing"))
                self._updating = True
                try:
                    self.background_buttons[current["background_mode"]].set_active(True)
                finally:
                    self._updating = False
                self._on_choose_background()
                return
            self._on_processing_changed()

        def _on_choose_background(self, *_args: Any) -> None:
            if self._closed or self._background_chooser is not None:
                return
            chooser = Gtk.FileChooserNative.new(self._tr("background_choose"), self,
                Gtk.FileChooserAction.OPEN, self._tr("choose"), self._tr("cancel"))
            chooser.set_modal(True)
            image_filter = Gtk.FileFilter()
            image_filter.set_name("PNG / JPEG")
            image_filter.add_mime_type("image/png")
            image_filter.add_mime_type("image/jpeg")
            chooser.add_filter(image_filter)
            chooser.connect("response", self._on_background_response)
            self._background_chooser = chooser
            chooser.show()

        def _on_background_response(self, chooser: Any, response: int) -> None:
            if chooser is not self._background_chooser:
                return
            self._background_chooser = None
            path = None
            if not self._closed and response == Gtk.ResponseType.ACCEPT:
                file = chooser.get_file()
                path = file.get_path() if file is not None else None
            chooser.destroy()
            if self._closed or response != Gtk.ResponseType.ACCEPT:
                return
            if not path:
                self._set_error(self._tr("background_local_file"))
                return
            self._select_background_image(path)

        def _select_background_image(self, path: str) -> None:
            if self._closed:
                return
            self._background_image = path
            self._updating = True
            try:
                self.background_buttons["image"].set_active(True)
            finally:
                self._updating = False
            self._on_processing_changed()

        def _build_profiles(self, page: Any) -> None:
            self.profile_empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.profile_empty.set_margin_top(12)
            self.profile_empty.add_css_class("profile-card")
            self.profile_empty.append(self._label(self._tr("empty_profiles"), "section-title"))
            self.profile_empty.append(self._label(self._tr("empty_profiles_hint"), "helper"))
            page.append(self.profile_empty)
            self.profile_saved_group = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            self.profile_saved_group.append(self._label(self._tr("saved_profiles"), "group-title"))
            self.profile_model = Gtk.StringList.new([self._tr("no_profiles")])
            self.profile_dropdown = Gtk.DropDown.new(self.profile_model, None)
            self.profile_dropdown.connect("notify::selected", self._on_profile_selected)
            self.profile_saved_group.append(self.profile_dropdown)
            self.profile_selection_notice = self._label("", "helper")
            self.profile_saved_group.append(self.profile_selection_notice)
            self.profile_load_button = Gtk.Button(label=self._tr("load_profile"))
            self.profile_load_button.connect("clicked", self._on_load_profile)
            self.profile_saved_group.append(self.profile_load_button)
            page.append(self.profile_saved_group)
            page.append(self._label(self._tr("save_current"), "group-title"))
            self.profile_entry = Gtk.Entry(placeholder_text=self._tr("profile_placeholder"))
            self.profile_entry.connect("changed", self._on_profile_name_changed)
            self.profile_entry.connect("activate", self._on_save_profile)
            page.append(self.profile_entry)
            self.profile_save_button = Gtk.Button(label=self._tr("save_profile"))
            self.profile_save_button.set_sensitive(False)
            self.profile_save_button.connect("clicked", self._on_save_profile)
            page.append(self.profile_save_button)
            self.profile_notice = self._label("", "helper")
            page.append(self.profile_notice)

        def _on_profile_name_changed(self, entry: Any) -> None:
            self.profile_save_button.set_sensitive(bool(entry.get_text().strip()))

        def _build_output(self, page: Any) -> None:
            self.preview_only = Gtk.CheckButton(label=self._tr("preview_only"))
            self.preview_only.set_active(True)
            page.append(self.preview_only)
            page.append(self._label(self._tr("local_hint"), "helper"))
            page.append(self._label(self._tr("resolution"), "group-title"))
            self.resolution_model = Gtk.StringList.new(["720p · 1280 × 720", "1080p · 1920 × 1080"])
            self.resolution_dropdown = Gtk.DropDown.new(self.resolution_model, None)
            page.append(self.resolution_dropdown)
            page.append(self._label(self._tr("resolution_hint"), "helper"))
            page.append(self._label(self._tr("virtual_device"), "group-title"))
            self.output_entry = Gtk.Entry(text="/dev/video10")
            self.output_entry.set_sensitive(False)
            self.output_entry.connect("changed", self._on_output_changed)
            page.append(self.output_entry)
            self.output_status = self._label("", "section-title")
            page.append(self.output_status)
            self.output_hint = self._label(self._tr("virtual_setup"), "helper")
            page.append(self.output_hint)
            virtual_hint = self._label(self._tr("virtual_hint"), "helper")
            self._virtual_name_labels["virtual_hint"] = virtual_hint
            page.append(virtual_hint)
            self.output_restart_hint = self._label(self._tr("output_restart"), "helper")
            self.output_restart_hint.set_visible(False)
            page.append(self.output_restart_hint)
            self.preview_only.connect("toggled", self._on_preview_only_toggled)
            self.resolution_dropdown.connect("notify::selected", self._on_output_changed)
            self._refresh_output_status()

        def _refresh_output_status(self) -> None:
            available = os.path.exists(self.output_entry.get_text().strip())
            virtual = self._state.get("virtual_device") if self._state.get("running") else None
            self.output_status.set_text(self._tr("virtual_active", path=virtual) if virtual else
                                        self._tr("virtual_available" if available else "virtual_unavailable"))
            self.output_hint.set_text(self._tr("virtual_call_step" if virtual else "virtual_setup"))
            self.output_hint.set_visible(bool(virtual) or not available)

        def _append_section_title(self, parent: Any, title: str) -> None:
            label = self._label(title, "group-title")
            parent.append(label)

        def _new_scale(self, parent: Any, key: str, minimum: float, maximum: float, step: float) -> Any:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            row.add_css_class("control-row")
            heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            label = self._label(self._tr(key))
            label.set_hexpand(True)
            heading.append(label)
            value_label = Gtk.Label()
            value_label.add_css_class("control-value")
            heading.append(value_label)
            row.append(heading)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, step)
            scale.set_draw_value(False)
            scale.set_value(12.0 if key == "background_blur" else 1.0 if key == "zoom" else 0.0)
            def show_value(widget: Any) -> None:
                value = widget.get_value()
                value_label.set_text(f"{value:.0f}" if key == "background_blur" else f"{value:.2f}×" if key == "zoom" else f"{value:+.0%}")
            show_value(scale)
            scale.connect("value-changed", show_value)
            scale.connect("value-changed", self._on_processing_changed)
            scale.set_tooltip_text(self._tr(key))
            row.append(scale)
            parent.append(row)
            return scale

        def _set_error(self, error: str | None, *, retry_start: bool = False,
                       virtual_start: bool = False, preserve_dismiss: bool = False) -> None:
            self._local_error = str(error) if error else None
            self._retry_start = bool(error and retry_start)
            self._failed_virtual_start = bool(error and virtual_start)
            if not preserve_dismiss:
                self._dismissed_feedback = None
            self._render_feedback()

        def _on_dismiss_feedback(self, *_args: Any) -> None:
            self._dismissed_feedback = str(self._local_error or self._state.get("error") or
                                           self._state.get("warning") or "")
            self._local_error = None
            self.error_revealer.set_reveal_child(False)

        def _render_feedback(self) -> None:
            error = self._local_error or self._state.get("error")
            warning = self._state.get("warning")
            message = str(error or warning or "")
            changed = message != self._feedback_message
            self._feedback_message = message
            if not message:
                self._dismissed_feedback = None
            self.status_label.set_text(message)
            self.error_revealer.set_reveal_child(bool(message) and message != self._dismissed_feedback)
            virtual = bool(error and self._failed_virtual_start)
            self.feedback_title.set_text(self._tr("feedback_virtual_title" if virtual else
                                                 "feedback_title" if error else "feedback_warning"))
            self.feedback_hint.set_text(self._tr("feedback_virtual_hint") if virtual else message)
            self.feedback_details.set_visible(virtual)
            if changed:
                self.feedback_details.set_expanded(False)
            self.local_preview_button.set_visible(virtual and not self._state.get("running"))
            self.retry_button.set_visible(bool(error) and self._retry_start and not self._state.get("running"))
            if error:
                self.feedback_box.add_css_class("error-banner")
            else:
                self.feedback_box.remove_css_class("error-banner")

        def _on_retry(self, *_args: Any) -> None:
            if not self._retry_start:
                return
            self._set_error(None)
            self._on_start_clicked(self.start_button)

        def _on_use_local(self, *_args: Any) -> None:
            self.preview_only.set_active(True)
            self._set_error(None)
            self._on_start_clicked(self.start_button)

        def _refresh_state(self, state: dict[str, Any] | None = None) -> bool:
            if self._closed:
                return False
            if self._capture_busy:
                return True
            try:
                if state is None:
                    state = self.client.state()
            except ClientError as exc:
                self._connection_error = str(exc)
                self._set_error(str(exc), preserve_dismiss=True)
                return True
            if self._connection_error:
                if self._local_error == self._connection_error:
                    self._set_error(None)
                self._connection_error = None
            self._state = state or {}
            for key, label in self._virtual_name_labels.items():
                label.set_text(self._tr(key, name=self._state.get("virtual_camera_name") or "Enquadra"))
            self._refresh_devices(self._state.get("devices", []))
            self._refresh_controls(self._state.get("controls", []))
            if self._output_synced and not self._state.get("running"):
                self._preferences_synced = False
            self._restore_preferences()
            self._refresh_profiles(self._state.get("profiles", []))
            self._set_show_mode(self._state.get("show_mode") or {})
            running = bool(self._state.get("running"))
            self.start_button.set_label(self._tr("stop") if running else self._tr("start"))
            self.start_button.set_sensitive(running or bool(self._device_paths))
            if running:
                self.start_button.add_css_class("stop-action")
                self.live_status.add_css_class("live")
                self.fps_label.set_text(self._tr("fps", value=float(self._state.get("fps") or 0.0)))
                if not self._output_synced:
                    self._syncing_preferences = True
                    self.preview_only.set_active(not bool(self._state.get("virtual_device")))
                    if self._state.get("virtual_device"):
                        self.output_entry.set_text(self._state["virtual_device"])
                    self._syncing_preferences = False
            else:
                self.start_button.remove_css_class("stop-action")
                self.live_status.remove_css_class("live")
            self._output_synced = running
            self.live_status.set_text(self._tr("live_short" if running else "ready_short"))
            self.fps_label.set_visible(running)
            footer = self._tr("close_hint" if running else "privacy_hint")
            if running and self._state.get("virtual_device"):
                footer = self._tr("virtual_call_step") + " " + footer
            self.preview_footer.set_text(footer)
            self.device_dropdown.set_sensitive(not running)
            self.preview_only.set_sensitive(not running)
            self.resolution_dropdown.set_sensitive(not running)
            self.output_entry.set_sensitive(not running and not self.preview_only.get_active())
            self.output_restart_hint.set_visible(running)
            self._refresh_output_status()
            self._render_feedback()
            self._sync_processing(self._state.get("processing"))
            self._set_preview_reader(self._state.get("preview_path"))
            return True

        def _refresh_devices(self, devices: list[dict[str, Any]]) -> None:
            paths = [str(item.get("path", "")) for item in devices if item.get("path")]
            selected = self._state.get("selected_device") or self._selected_device
            self._selected_device = selected if selected in paths else (paths[0] if paths else None)
            self._syncing_device = True
            try:
                if paths != self._device_paths:
                    self._device_paths = paths
                    self.device_model.splice(0, self.device_model.get_n_items(),
                                             [str(item.get("name") or item["path"]) for item in devices if item.get("path")] or [self._tr("choose_camera")])
                self.device_dropdown.set_selected(paths.index(self._selected_device) if self._selected_device in paths else 0)
            finally:
                self._syncing_device = False

        def _on_selected_device_changed(self, *_args: Any) -> None:
            if self._syncing_device or self._capture_busy or self._state.get("running"):
                return
            selected = self._selected_path()
            if not selected or selected == self._selected_device:
                return
            # Pending edits still belong to the previous worker camera.
            if not self._flush_pending():
                self._refresh_devices(self._state.get("devices", []))
                return
            try:
                state = self.client.select_device(selected)
            except ClientError as exc:
                self._set_error(str(exc))
                self._refresh_devices(self._state.get("devices", []))
                return
            self._selected_device = selected
            self._set_error(None)
            self._refresh_state(state if isinstance(state, dict) else None)

        def _refresh_controls(self, controls: list[dict[str, Any]]) -> None:
            signature = tuple(str((c.get("id"), c.get("kind"), c.get("minimum"),
                                   c.get("maximum"), c.get("step"), c.get("options"))) for c in controls)
            if signature != self._controls_signature:
                self._controls_signature = signature
                self._clear_box(self.controls_box)
                self._clear_box(self.native_controls_box)
                self._control_widgets.clear()
                self._control_options.clear()
                self._control_grids.clear()
                self._control_hints.clear()
                self._control_values.clear()
                self._group_boxes.clear()
                if not controls:
                    self.controls_box.append(self._label(self._tr("no_controls"), "helper"))
                else:
                    for group in ("color", "exposure", "focus", "native_framing", "advanced"):
                        members = [c for c in sort_controls(controls)
                                   if control_info(c)["group"] == group]
                        if not members:
                            continue
                        parent = self.native_controls_box if group == "native_framing" else self.controls_box
                        self._append_section_title(parent, self._tr(group + "_group"))
                        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                        parent.append(box)
                        self._group_boxes[group] = box
                        if group == "native_framing":
                            self._native_advanced_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                            advanced = Gtk.Expander(label=self._tr("native_advanced"))
                            advanced.set_child(self._native_advanced_box)
                            parent.append(advanced)
                        for control in members:
                            self._add_control(control)
            else:
                for control in controls:
                    control_id = str(control.get("id"))
                    widget = self._control_widgets.get(control_id)
                    if widget is None or control_id in self._pending_controls or widget.has_focus():
                        continue
                    self._updating = True
                    try:
                        self._update_control_widget(widget, control)
                    finally:
                        self._updating = False

        def _control_hint(self, control: dict[str, Any]) -> str:
            control_id = str(control.get("id"))
            if not control.get("active", True):
                key = {"0x98091a": "manual_wb_hint", "0x9a0902": "manual_exposure_hint",
                       "0x9a090a": "manual_focus_hint"}.get(control_id, "readonly_hint")
                return self._tr(key)
            if not control.get("writable", True):
                tracking = control_info(control)["group"] == "native_framing" and (
                    self._state.get("running") and (self._state.get("processing") or {}).get("autoframe"))
                return self._tr("tracking_control_hint" if tracking else "readonly_hint")
            return control_info(control, _locale_is_pt()).get("hint", "")

        @staticmethod
        def _display_control_value(control: dict[str, Any]) -> str:
            value = control.get("value")
            if value is None:
                return "—"
            if control.get("id") == "0x98091a":
                return f"{value} K"
            if control.get("id") == "0x9a090d":
                return f"{value / 100:.2f}×"
            return str(value)

        def _add_control(self, control: dict[str, Any]) -> None:
            control_id = str(control.get("id"))
            info = control_info(control, _locale_is_pt())
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            row.add_css_class("control-row")
            header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            title = self._label(info["title"])
            title.set_hexpand(True)
            header.append(title)
            row.append(header)
            kind = control.get("kind")
            if kind == "integer":
                self._control_grids[control_id] = (
                    int(control.get("minimum", 0)), int(control.get("maximum", 0)),
                    max(1, int(control.get("step", 1))),
                )
            if kind == "boolean":
                widget = Gtk.Switch()
                widget.set_valign(Gtk.Align.CENTER)
                widget.set_active(bool(control.get("value", False)))
                widget.connect("notify::active", self._on_boolean_control, control_id)
                header.append(widget)
            elif kind == "menu":
                options = control.get("options") or []
                labels = [option_label(control, item, _locale_is_pt()) for item in options]
                model = Gtk.StringList.new(labels or [""])
                widget = Gtk.DropDown.new(model, None)
                self._control_options[control_id] = options
                current = next((idx for idx, item in enumerate(options) if item.get("value") == control.get("value")), 0)
                widget.set_selected(current)
                widget.connect("notify::selected", self._on_menu_control, control_id)
                row.append(widget)
            else:
                minimum = int(control.get("minimum", 0))
                maximum = int(control.get("maximum", minimum))
                step = max(1, int(control.get("step", 1)))
                value_label = Gtk.Label(label=self._display_control_value(control))
                value_label.add_css_class("control-value")
                header.append(value_label)
                self._control_values[control_id] = value_label
                widget = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, minimum, maximum, step)
                widget.set_draw_value(False)
                if control.get("value") is not None:
                    widget.set_value(int(control["value"]))
                widget.connect("value-changed", self._on_integer_control, control_id)
                row.append(widget)
            widget.set_tooltip_text(info["title"])
            widget.set_sensitive(bool(control.get("active", True) and control.get("writable", True)))
            hint = self._label(self._control_hint(control), "helper")
            hint.set_visible(bool(hint.get_text()))
            row.append(hint)
            self._control_hints[control_id] = hint
            if info["group"] == "native_framing" and control_id != "fov":
                self._native_advanced_box.append(row)
            else:
                self._group_boxes[info["group"]].append(row)
            self._control_widgets[control_id] = widget

        def _update_control_widget(self, widget: Any, control: dict[str, Any]) -> None:
            widget.set_sensitive(bool(control.get("active", True) and control.get("writable", True)))
            control_id = str(control.get("id"))
            hint = self._control_hints[control_id]
            hint.set_text(self._control_hint(control))
            hint.set_visible(bool(hint.get_text()))
            if control_id in self._control_values:
                self._control_values[control_id].set_text(self._display_control_value(control))
            value = control.get("value")
            if value is None:
                return
            if isinstance(widget, Gtk.Switch):
                widget.set_active(bool(value))
            elif isinstance(widget, Gtk.DropDown):
                options = self._control_options.get(control_id, [])
                index = next((i for i, option in enumerate(options) if option.get("value") == value), None)
                if index is not None:
                    widget.set_selected(index)
            else:
                widget.set_value(float(value))

        @staticmethod
        def _clear_box(box: Any) -> None:
            child = box.get_first_child()
            while child is not None:
                following = child.get_next_sibling()
                box.remove(child)
                child = following

        def _on_integer_control(self, widget: Any, control_id: str) -> None:
            value = int(widget.get_value())
            label = self._control_values.get(control_id)
            if label is not None and not self._updating:
                label.set_text(self._display_control_value({"id": control_id, "value": value}))
            self._schedule_control(control_id, value)

        def _on_boolean_control(self, widget: Any, _param: Any, control_id: str) -> None:
            self._schedule_control(control_id, int(widget.get_active()))

        def _on_menu_control(self, widget: Any, _param: Any, control_id: str) -> None:
            options = self._control_options.get(control_id, [])
            index = widget.get_selected()
            if index < len(options):
                self._schedule_control(control_id, int(options[index]["value"]))

        def _schedule_control(self, control_id: str, value: int) -> None:
            if self._updating:
                return
            grid = self._control_grids.get(control_id)
            if grid is not None:
                value = _quantise_control(value, *grid)
            self._pending_controls[control_id] = value
            old_timeout = self._control_timeouts.pop(control_id, None)
            if old_timeout:
                GLib.source_remove(old_timeout)
            self._control_timeouts[control_id] = GLib.timeout_add(150, self._apply_control, control_id)

        def _apply_control(self, control_id: str) -> bool:
            self._control_timeouts.pop(control_id, None)
            value = self._pending_controls.get(control_id)
            if value is None:
                return False
            try:
                self.client.set_control(control_id, value)
                self._pending_controls.pop(control_id, None)
                self._set_error(None)
            except ClientError as exc:
                self._set_error(f"{self._tr('control_error')} {exc}")
            return False

        def _sync_processing(self, raw: dict[str, Any] | None) -> None:
            if self._pending_processing is not None:
                return
            values = _normalise_processing(raw)
            self.autoframe_hint.set_visible(values["autoframe"])
            self._updating = True
            try:
                self._background_image = values["background_image"]
                self.background_buttons[values["background_mode"]].set_active(True)
                if not self.background_blur_scale.has_focus():
                    self.background_blur_scale.set_value(values["background_blur"])
                self._update_background_visibility()
                if not self.autoframe.has_focus():
                    self.autoframe.set_active(values["autoframe"])
                if not self.zoom_scale.has_focus():
                    self.zoom_scale.set_value(values["zoom"])
                if not self.pan_x_scale.has_focus():
                    self.pan_x_scale.set_value(values["pan_x"])
                if not self.pan_y_scale.has_focus():
                    self.pan_y_scale.set_value(values["pan_y"])
                if not self.mirror.has_focus():
                    self.mirror.set_active(values["mirror"])
            finally:
                self._updating = False

        def _on_reset_framing(self, *_args: Any) -> None:
            self._updating = True
            try:
                self.zoom_scale.set_value(1.0)
                self.pan_x_scale.set_value(0.0)
                self.pan_y_scale.set_value(0.0)
            finally:
                self._updating = False
            self._on_processing_changed()

        def _on_processing_changed(self, *_args: Any) -> None:
            if self._updating or self._closed:
                return
            self._update_background_visibility()
            self.autoframe_hint.set_visible(self.autoframe.get_active())
            values = {
                "autoframe": self.autoframe.get_active(),
                "zoom": self.zoom_scale.get_value(),
                "pan_x": self.pan_x_scale.get_value(),
                "pan_y": self.pan_y_scale.get_value(),
                "mirror": self.mirror.get_active(),
                "background_mode": next(mode for mode, button in self.background_buttons.items() if button.get_active()),
                "background_image": self._background_image,
                "background_blur": self.background_blur_scale.get_value(),
            }
            self._pending_processing = values
            if self._processing_timeout:
                GLib.source_remove(self._processing_timeout)
            self._processing_timeout = GLib.timeout_add(150, self._apply_processing, values)

        def _apply_processing(self, values: dict[str, Any]) -> bool:
            self._processing_timeout = 0
            self._processing_failed = False
            try:
                result = self.client.processing(values)
                self._pending_processing = None
                self._state["processing"] = (result.get("processing", values)
                                             if isinstance(result, dict) else dict(values))
                self._set_error(None)
            except ClientError as exc:
                self._processing_failed = True
                actual = _normalise_processing(self._state.get("processing"))
                if any(values[key] != actual[key] for key in
                       ("background_mode", "background_image", "background_blur")):
                    # A rejected effect must never look active. Keep the worker's
                    # actual configuration and allow Original to recover.
                    self._pending_processing = None
                    self._sync_processing(actual)
                self._set_error(f"{self._tr('control_error')} {exc}")
            return False

        def _set_show_mode(self, show_mode: dict[str, Any]) -> None:
            if show_mode.get("validated"):
                message = show_mode.get("message") or self._tr("show_message")
            elif show_mode.get("rotation_observed"):
                message = self._tr("show_rotation_observed")
            else:
                message = self._tr("show_pending")
            self.show_status.set_text(f"{self._tr('show_mode')}: {message}")

        def _set_preview_reader(self, path: str | None) -> None:
            if path == self._preview_key:
                return
            if self._preview_reader is not None:
                self._preview_reader.close()
                self._preview_reader = None
            self._preview_key = path
            if path and PreviewReader is not None:
                try:
                    self._preview_reader = PreviewReader(path)
                except (OSError, RuntimeError) as exc:
                    self._set_error(str(exc))

        def _refresh_preview(self) -> bool:
            if self._closed:
                return False
            running = bool(self._state.get("running"))
            no_device = not self._device_paths
            has_error = bool(self._local_error or self._state.get("error"))
            self.preview_title.set_text(self._tr("connecting" if self._capture_busy else
                "empty_no_device" if no_device else "empty_error" if has_error else
                "empty_running" if running else "empty_title"))
            self.preview_placeholder.set_text(self._tr("empty_no_device_hint" if no_device else
                "no_preview" if running else "preview_stopped"))
            if self._preview_reader is None:
                self.picture.set_paintable(None)
                self.preview_empty.set_visible(True)
                return True
            try:
                frame = self._preview_reader.read()
            except (OSError, RuntimeError, ValueError) as exc:
                self._set_error(str(exc))
                frame = None
            if frame is None:
                self.picture.set_paintable(None)
                self.preview_empty.set_visible(True)
                return True
            width, height, bgr_bytes = frame
            try:
                if len(bgr_bytes) != width * height * 3:
                    raise ValueError("preview BGR possui tamanho incompatível com a resolução")
                payload = GLib.Bytes.new(bgr_bytes)
                texture = Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.B8G8R8,
                                                payload, width * 3)
                self.picture.set_paintable(texture)
                self.preview_empty.set_visible(False)
            except (TypeError, ValueError, AttributeError) as exc:
                self._set_error(f"Preview: {exc}")
            return True

        def _refresh_profiles(self, profiles: list[str]) -> None:
            self.profile_empty.set_visible(not bool(profiles))
            self.profile_saved_group.set_visible(bool(profiles))
            self.profile_load_button.set_sensitive(bool(profiles))
            self._update_profile_selection_notice()
            if profiles == self._profile_names:
                return
            selected = self.profile_dropdown.get_selected()
            old_name = self._last_profile or (self._profile_names[selected] if selected < len(self._profile_names) else None)
            self._syncing_preferences = True
            self._profile_names = list(profiles)
            names = self._profile_names or [self._tr("no_profiles")]
            self.profile_model.splice(0, self.profile_model.get_n_items(), names)
            self.profile_dropdown.set_selected(profiles.index(old_name) if old_name in profiles else 0)
            self._syncing_preferences = False
            self._update_profile_selection_notice()

        def _on_save_profile(self, _button: Any) -> None:
            name = self.profile_entry.get_text().strip()
            if not name:
                self._set_error(self._tr("profile_missing_name"))
                return
            try:
                if not self._flush_pending():
                    return
                self.client.save_profile(name)
                self._last_profile = name
                self.profile_entry.set_text("")
                self._set_error(None)
                self.profile_notice.set_text(self._tr("profile_saved"))
                self._refresh_state()
            except ClientError as exc:
                self._set_error(str(exc))

        def _on_load_profile(self, _button: Any) -> None:
            index = self.profile_dropdown.get_selected()
            if index >= len(self._profile_names):
                self._set_error(self._tr("no_profiles"))
                return
            try:
                if not self._flush_pending():
                    return
                self.client.load_profile(self._profile_names[index])
                self._last_profile = self._profile_names[index]
                self._set_error(None)
                self.profile_notice.set_text(self._tr("profile_loaded"))
                self._refresh_state()
            except ClientError as exc:
                self._set_error(str(exc))

        def _selected_path(self) -> str | None:
            index = self.device_dropdown.get_selected()
            return self._device_paths[index] if index < len(self._device_paths) else None

        def _on_preview_only_toggled(self, widget: Any) -> None:
            self.output_entry.set_sensitive(not widget.get_active() and not self._state.get("running"))
            self._on_output_changed()

        def _restore_preferences(self) -> None:
            if self._preferences_synced:
                return
            preferences = self._state.get("preferences") or {}
            self._syncing_preferences = True
            self.preview_only.set_active(bool(preferences.get("preview_only", True)))
            self.output_entry.set_text(preferences.get("output") or "/dev/video10")
            self.resolution_dropdown.set_selected(1 if preferences.get("size") == "1080" else 0)
            self._last_profile = preferences.get("last_profile")
            self._syncing_preferences = False
            self._preferences_synced = True

        def _on_output_changed(self, *_args: Any) -> None:
            self._refresh_output_status()
            if self._syncing_preferences:
                return
            self._queue_preferences({"preview_only": self.preview_only.get_active(),
                                     "output": self.output_entry.get_text().strip(),
                                     "size": "1080" if self.resolution_dropdown.get_selected() == 1 else "720"})

        def _queue_preferences(self, options: dict[str, Any]) -> None:
            self._pending_preferences.update(options)
            if self._preferences_timeout:
                GLib.source_remove(self._preferences_timeout)
            self._preferences_timeout = GLib.timeout_add(250, self._apply_preferences)

        def _apply_preferences(self) -> bool:
            self._preferences_timeout = 0
            try:
                self.client.preferences(dict(self._pending_preferences))
                self._pending_preferences.clear()
            except ClientError as exc:
                self._set_error(str(exc))
            return False

        def _update_profile_selection_notice(self) -> None:
            self.profile_selection_notice.set_text(self._tr("selected_profile", name=self._last_profile)
                                                   if self._last_profile else "")

        def _on_profile_selected(self, *_args: Any) -> None:
            if self._syncing_preferences:
                return
            index = self.profile_dropdown.get_selected()
            if index < len(self._profile_names):
                self._last_profile = self._profile_names[index]
                self._update_profile_selection_notice()
                self._queue_preferences({"last_profile": self._last_profile})

        def _flush_pending(self) -> bool:
            failed_message = None
            if self._pending_preferences:
                if self._preferences_timeout:
                    GLib.source_remove(self._preferences_timeout)
                self._apply_preferences()
                if self._pending_preferences:
                    failed_message = self._local_error
            for control_id in list(self._pending_controls):
                source = self._control_timeouts.pop(control_id, None)
                if source:
                    GLib.source_remove(source)
                self._apply_control(control_id)
                if control_id in self._pending_controls:
                    failed_message = self._local_error
            if self._pending_processing is not None:
                if self._processing_timeout:
                    GLib.source_remove(self._processing_timeout)
                self._processing_timeout = 0
                self._apply_processing(self._pending_processing)
                if self._processing_failed or self._pending_processing is not None:
                    failed_message = self._local_error
            if failed_message:
                self._set_error(failed_message)
                return False
            return True

        def _on_start_clicked(self, _button: Any) -> None:
            if self._capture_busy:
                return
            if self._state.get("running"):
                self._flush_pending()
                self._run_capture_action("stop", None)
                return
            device = self._selected_path()
            if not device:
                self._set_error(self._tr("select_device"))
                return
            output = self.output_entry.get_text().strip()
            if not self.preview_only.get_active() and not output:
                self._set_error(self._tr("output_required"), retry_start=True, virtual_start=True)
                return
            if not self.preview_only.get_active() and not os.path.exists(output):
                self._set_error(self._tr("output_missing", path=output), retry_start=True, virtual_start=True)
                return
            width, height = (1920, 1080) if self.resolution_dropdown.get_selected() == 1 else (1280, 720)
            if not self._flush_pending():
                return
            options = _start_options(device=device, output=output,
                                     preview_only=self.preview_only.get_active(), width=width,
                                     height=height, autoframe=self.autoframe.get_active())
            self._selected_device = device
            self._run_capture_action("start", options)

        def _run_capture_action(self, action: str, options: dict[str, Any] | None) -> None:
            self._capture_busy = True
            self._set_error(None)
            self.start_button.set_sensitive(False)
            self.start_button.set_label(self._tr("connecting" if action == "start" else "stopping"))
            self.capture_spinner.start()
            self.device_dropdown.set_sensitive(False)
            self.inspector.set_sensitive(False)
            self.retry_button.set_sensitive(False)
            self.local_preview_button.set_sensitive(False)
            # Gio.DBusConnection is thread-safe. Keep the GTK main loop available
            # during the blocking Start/Stop call and marshal completion back.
            def invoke() -> None:
                error = None
                result = None
                try:
                    if action == "start":
                        result = self.client.start(options)
                    else:
                        result = self.client.stop()
                except Exception as exc:
                    error = str(exc)
                finally:
                    GLib.idle_add(self._capture_completed, action, error,
                                  bool(options and options.get("output")), result)
            threading.Thread(target=invoke, name="brio-capture-action", daemon=True).start()

        def _capture_completed(self, action: str, error: str | None, virtual_start: bool,
                               result: dict[str, Any] | None = None) -> bool:
            self._capture_busy = False
            if self._closed:
                return False
            self.capture_spinner.stop()
            self.inspector.set_sensitive(True)
            self.retry_button.set_sensitive(True)
            self.local_preview_button.set_sensitive(True)
            if action == "stop" and not error:
                self.get_application()._release_background_hold()
            if not error:
                # The RPC already returns the worker's state. Preserve that
                # evidence even if the follow-up poll fails during a close.
                if isinstance(result, dict) and "running" in result:
                    self._state = result
                else:
                    self._state["running"] = action == "start"
            self._set_error(error, retry_start=action == "start", virtual_start=virtual_start)
            self._refresh_state()
            if self._close_pending:
                self._close_pending = False
                self.close()
            return False

        def _on_close_request(self, _window: Any) -> bool:
            # Finish the requested transition before allowing the window to close.
            if self._capture_busy:
                self._close_pending = True
                return True
            self._flush_pending()
            # O worker é independente da janela. Não enviar shutdown/stop ao fechar.
            self._closed = True
            if self._background_chooser is not None:
                chooser, self._background_chooser = self._background_chooser, None
                chooser.destroy()
            GLib.source_remove(self._state_source)
            GLib.source_remove(self._preview_source)
            if self._preview_reader is not None:
                self._preview_reader.close()
                self._preview_reader = None
            if self._state.get("running"):
                app = self.get_application()
                if not app._held_for_background:
                    app.hold()
                    app._held_for_background = True
                app.client = self.client
                app.window = None
                if not app._watch_source:
                    app._watch_source = GLib.timeout_add_seconds(2, app._watch_background)
                notification = Gio.Notification.new(self._tr("notification_title"))
                notification.set_body(self._tr("processing_continues"))
                notification.add_button(self._tr("notification_stop"), "app.stop")
                self.get_application().send_notification("capture-continues", notification)
            return False

    class BrioApplication(Gtk.Application):
        def __init__(self):
            super().__init__(application_id="org.briocontrol.App",
                             flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
            self.window: MainWindow | None = None
            self.client: Any = None
            self._held_for_background = False
            self._watch_source = 0
            action = Gio.SimpleAction.new("stop", None)
            action.connect("activate", self._stop_from_notification)
            self.add_action(action)

        def do_activate(self) -> None:
            if self.window is None:
                self.window = MainWindow(self)
                self.client = self.window.client
            self.window.present()

        def _stop_from_notification(self, *_args: Any) -> None:
            if self.client is not None:
                try:
                    self.client.stop()
                    self.withdraw_notification("capture-continues")
                    self._release_background_hold()
                    if self.window is not None:
                        self.window._refresh_state()
                except ClientError as exc:
                    if self.window is not None:
                        self.window._set_error(str(exc))

        def _release_background_hold(self) -> None:
            if self._held_for_background:
                self._held_for_background = False
                if self._watch_source:
                    GLib.source_remove(self._watch_source)
                    self._watch_source = 0
                self.release()

        def _watch_background(self) -> bool:
            if not self._held_for_background or self.client is None:
                self._watch_source = 0
                return False
            try:
                running = bool(self.client.state().get("running"))
            except ClientError:
                return True
            if not running:
                self.withdraw_notification("capture-continues")
                self._release_background_hold()
                return False
            return True

    app = BrioApplication()
    return app.run([])


if __name__ == "__main__":
    raise SystemExit(run())

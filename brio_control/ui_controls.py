"""Human-readable names and ordering for controls exposed by the driver.

This module only describes controls returned by the backend. It does not
claim that a particular camera advertises any given V4L2 control.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


# Names use the V4L2 IDs from the local Linux UAPI headers. IDs are canonical
# strings so callers may pass any casing (or an integer) without depending on
# the backend's ``kind`` field.
_CONTROLS: dict[str, tuple[str, str]] = {
    "0x980900": ("Brilho", "color"),
    "0x980901": ("Contraste", "color"),
    "0x980902": ("Saturação", "color"),
    "0x980903": ("Matiz", "color"),
    "0x98090c": ("Balanço de branco automático", "color"),
    "0x980911": ("Exposição", "exposure"),
    "0x980912": ("Ganho automático", "exposure"),
    "0x980913": ("Ganho", "exposure"),
    "0x980918": ("Anticintilação", "exposure"),
    "0x98091a": ("Temperatura do balanço de branco", "color"),
    "0x98091b": ("Nitidez", "color"),
    "0x98091c": ("Compensação de contraluz", "exposure"),
    "0x9a0901": ("Exposição automática", "exposure"),
    "0x9a0902": ("Exposição manual", "exposure"),
    "0x9a0903": ("Taxa de quadros adaptativa", "exposure"),
    "0x9a0908": ("Panorâmica", "native_framing"),
    "0x9a0909": ("Inclinação", "native_framing"),
    "0x9a090a": ("Foco manual", "focus"),
    "0x9a090b": ("Ajuste relativo do foco", "focus"),
    "0x9a090c": ("Foco automático", "focus"),
    "0x9a090d": ("Zoom", "native_framing"),
    "0x9a090e": ("Ajuste relativo do zoom", "native_framing"),
    "0x9a090f": ("Zoom contínuo", "native_framing"),
}
_CONTROLS_EN: dict[str, str] = {
    "0x980900": "Brightness",
    "0x980901": "Contrast",
    "0x980902": "Saturation",
    "0x980903": "Hue",
    "0x98090c": "Auto white balance",
    "0x980911": "Exposure",
    "0x980912": "Auto gain",
    "0x980913": "Gain",
    "0x980918": "Anti-flicker",
    "0x98091a": "White balance temperature",
    "0x98091b": "Sharpness",
    "0x98091c": "Backlight compensation",
    "0x9a0901": "Auto exposure",
    "0x9a0902": "Manual exposure",
    "0x9a0903": "Adaptive frame rate",
    "0x9a0908": "Pan",
    "0x9a0909": "Tilt",
    "0x9a090a": "Manual focus",
    "0x9a090b": "Relative focus adjustment",
    "0x9a090c": "Auto focus",
    "0x9a090d": "Zoom",
    "0x9a090e": "Relative zoom adjustment",
    "0x9a090f": "Continuous zoom",
}

_OPTIONS: dict[str, dict[int, tuple[str, str]]] = {
    # V4L2_CID_POWER_LINE_FREQUENCY values from v4l2-controls.h.
    "0x980918": {
        0: ("Desativada", "Disabled"),
        1: ("50 Hz", "50 Hz"),
        2: ("60 Hz", "60 Hz"),
        3: ("Automática", "Auto"),
    },
    # V4L2_EXPOSURE_* values from v4l2-controls.h.
    "0x9a0901": {
        0: ("Automática", "Auto"),
        1: ("Manual", "Manual"),
        2: ("Prioridade do obturador", "Shutter priority"),
        3: ("Prioridade da abertura", "Aperture priority"),
    },
}

_SORT_BEFORE = (
    ("0x9a0901", "0x9a0902"),  # auto exposure before manual exposure
    ("0x98090c", "0x98091a"),  # auto white balance before temperature
    ("0x9a090c", "0x9a090a"),  # auto focus before manual focus
    ("fov", "0x9a090d"),       # field of view before native zoom/pan/tilt
    ("fov", "0x9a0908"),
    ("fov", "0x9a0909"),
    ("0x9a090d", "0x9a0908"),  # then zoom, pan, and tilt
    ("0x9a0908", "0x9a0909"),
)


def _control_id(control: Mapping[str, Any] | str | int) -> str:
    if isinstance(control, Mapping):
        value = control.get("id", "")
    else:
        value = control
    if isinstance(value, int):
        return f"0x{value:x}"
    if not isinstance(value, str):
        return ""
    value = value.strip().lower()
    if value == "fov":
        return value
    if value.startswith("0x"):
        try:
            return f"0x{int(value, 16):x}"
        except ValueError:
            return value
    return value


def control_info(control: Mapping[str, Any], portuguese: bool = True) -> dict[str, str]:
    """Return a translated title and UI group for a driver control.

    Unknown IDs keep the driver's name and are placed in ``advanced``.
    """
    control_id = _control_id(control)
    if control_id == "fov":
        return {
            "title": "Campo de visão" if portuguese else "Field of view",
            "group": "native_framing",
        }
    known = _CONTROLS.get(control_id)
    if known:
        title, group = known
        if not portuguese:
            title = _CONTROLS_EN[control_id]
    else:
        title = str(control.get("name") or control_id or "Controle")
        group = "advanced"
    result = {"title": title, "group": group}
    if control_id == "0x9a0903":
        result["hint"] = (
            "Em pouca luz, a taxa de quadros pode diminuir."
            if portuguese else "In low light, the frame rate may decrease."
        )
    return result


def option_label(control: Mapping[str, Any] | str | int, option: Any,
                 portuguese: bool = True) -> str:
    """Translate known V4L2 menu values; otherwise retain the driver's label."""
    control_id = _control_id(control)
    value = option.get("value") if isinstance(option, Mapping) else option
    known = _OPTIONS.get(control_id, {}).get(value) if isinstance(value, int) else None
    if known:
        return known[0 if portuguese else 1]
    if isinstance(option, Mapping):
        return str(option.get("label") or value)
    return str(value)


def sort_controls(controls: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Apply only specific UI ordering preferences, preserving other order."""
    ids = [_control_id(control) for control in controls]
    outgoing: list[set[int]] = [set() for _ in controls]
    indegree = [0] * len(controls)
    for before, after in _SORT_BEFORE:
        before_indices = [i for i, value in enumerate(ids) if value == before]
        after_indices = [i for i, value in enumerate(ids) if value == after]
        for first in before_indices:
            for second in after_indices:
                if second not in outgoing[first]:
                    outgoing[first].add(second)
                    indegree[second] += 1

    # Stable topological ordering: unrelated controls retain driver order.
    ready = [i for i, degree in enumerate(indegree) if degree == 0]
    result: list[Mapping[str, Any]] = []
    while ready:
        current = ready.pop(0)
        result.append(controls[current])
        for following in sorted(outgoing[current]):
            indegree[following] -= 1
            if indegree[following] == 0:
                ready.append(following)
                ready.sort()
    if len(result) != len(controls):
        return list(controls)
    return result

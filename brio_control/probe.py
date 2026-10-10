"""Ensaio físico interativo do selector candidato 13; sem gravação de imagens."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .camera import BRIO_500_USB_ID, Camera, CameraError, Device, discover_devices


class ProbeStopped(Exception):
    pass


def _identity(device: Device) -> str:
    return hashlib.sha256((device.usb_id + "\0" + device.identity).encode()).hexdigest()


def _directory() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
    if not base.is_absolute():
        raise CameraError("XDG_STATE_HOME deve ser absoluto")
    directory = base / "brio-control"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    metadata = directory.lstat()
    if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid()
            or metadata.st_mode & 0o077):
        raise CameraError("diretório de estado deve ser privado e pertencer ao usuário")
    return directory


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _save(path: Path, record: dict) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".probe-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _load(path: Path) -> dict | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "r", encoding="utf-8") as stream:
        metadata = os.fstat(stream.fileno())
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid()
                or metadata.st_mode & 0o077 or metadata.st_size > 4096):
            raise CameraError("journal inválido ou não privado; preservado")
        record = json.load(stream)
    if (not isinstance(record, dict) or record.get("original13") not in (0, 1)
            or record.get("lastintended") not in (0, 1)
            or not isinstance(record.get("identityhash"), str)
            or not isinstance(record.get("path"), str)):
        raise CameraError("journal inválido; preservado")
    return record


def _guard(camera: Camera, device: Device) -> dict:
    if (device.usb_id != BRIO_500_USB_ID or not device.identity
            or device.xu_unit <= 0 or 13 not in device.xu_selectors):
        raise CameraError("identidade/XU incompatível com o ensaio")
    caps = camera.capabilities(13)
    if (caps["length"] != 1 or caps["info"] & 3 != 3
            or (caps["minimum"], caps["maximum"]) != (0, 1)
            or caps["current"] not in (0, 1)):
        raise CameraError("selector candidato não passou nas guardas GET/SET")
    return caps


def _check_identity(device: Device) -> None:
    if not any(d.path == device.path and _identity(d) == _identity(device)
               and d.xu_unit == device.xu_unit and d.xu_selectors == device.xu_selectors
               for d in discover_devices()):
        raise CameraError("câmera desconectada ou identidade mudou; escrita cancelada")


def _close_preview(process) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=3)


def _preview(device: Device, phase: str):
    return subprocess.Popen(
        ["ffplay", "-loglevel", "warning", "-f", "v4l2", "-input_format", "mjpeg",
         "-video_size", "1920x1080", "-framerate", "30", "-window_title",
         f"Brio 500 — {phase} — vídeo direto, sem transformação", "-i", device.path],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=sys.stderr,
    )


def _answer(process, prompt: str) -> str:
    print(prompt + " [pass/fail/inconclusive; stop encerra]", file=sys.stderr, flush=True)
    deadline = time.monotonic() + 120
    for _ in range(5):
        while True:
            if process.poll() is not None:
                raise ProbeStopped("preview fechado")
            if time.monotonic() >= deadline:
                raise ProbeStopped("tempo de resposta esgotado")
            if select.select([sys.stdin], [], [], 0.2)[0]:
                line = sys.stdin.readline()
                if not line or line.strip().lower() == "stop":
                    raise ProbeStopped("ensaio interrompido")
                answer = line.strip().lower()
                if answer in {"pass", "fail", "inconclusive"}:
                    return answer
                print("Use pass, fail, inconclusive ou stop.", file=sys.stderr, flush=True)
                break
    raise ProbeStopped("limite de respostas inválidas")


def _restore(camera: Camera, device: Device, path: Path, record: dict) -> None:
    _check_identity(device)
    current = camera.read(13)[0]
    if current not in {record["original13"], record["lastintended"]}:
        raise CameraError("estado divergente; restauração cancelada para evitar conflito")
    if current != record["original13"]:
        # Preserve the prior intended value until readback succeeds: a crash during
        # restoration can leave either that value or the original on the device.
        camera.write(13, record["original13"])
    if camera.read(13)[0] != record["original13"]:
        raise CameraError("readback da restauração divergente")
    path.unlink()
    _sync_directory(path.parent)


def run(device_path: str | None = None) -> int:
    """Executa ensaio somente quando chamado; resultado visual é declarado pelo usuário."""
    report = {"validated": False, "observation_source": "user", "selector": 13,
              "observations": [], "restored": None, "status": "inconclusive"}
    old_handlers = {}
    lock_fd = None
    process = None
    record = None
    restoration_due = False
    exit_code = 1

    def stopped(signum, frame):
        raise ProbeStopped(f"sinal {signum}")

    try:
        directory = _directory()
        lock_fd = os.open(directory / "show-mode-probe.lock",
                          os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path = directory / "show-mode-probe.json"
        orphan = _load(path)
        devices = [d for d in discover_devices() if device_path is None or d.path == device_path]
        if len(devices) != 1:
            raise CameraError("selecione exatamente uma Brio 500 disponível")
        device = devices[0]
        report["device"] = {"path": device.path, "usb_id": device.usb_id,
                            "identityhash": _identity(device), "xu_unit": device.xu_unit}
        if orphan is not None and orphan["identityhash"] != _identity(device):
            raise CameraError("journal pertence a outra câmera; nenhuma escrita realizada")
        for number in (signal.SIGINT, signal.SIGTERM):
            old_handlers[number] = signal.signal(number, stopped)
        with Camera(device, writable=True) as camera:
            try:
                caps = _guard(camera, device)
                report["capabilities"] = caps
                if orphan is not None:
                    if caps["current"] not in {orphan["original13"], orphan["lastintended"]}:
                        raise CameraError("journal conflita com estado atual; nenhuma escrita realizada")
                    record = orphan
                    restoration_due = True
                    report["status"] = "recovery"
                    exit_code = 0
                else:
                    print("Ensaio físico experimental: selector 13 não comprovado na Brio 500. "
                          "Use um marcador assimétrico. Sem gravação de imagens. "
                          "Feche outros controladores. Cada resposta vence em 120 s. "
                          "SIGKILL/desconexão podem impedir restauração; journal permite recuperação.",
                          file=sys.stderr, flush=True)
                    process = _preview(device, "baseline")
                    baseline = _answer(process, "Sem alterar controles, faça frente/mesa/frente. "
                                       "pass = já inverte automaticamente; fail = não inverte")
                    report["observations"].append({"phase": "baseline", "result": baseline,
                                                   "read13": camera.read(13)[0]})
                    if camera.read(13)[0] != caps["current"]:
                        raise CameraError("selector mudou durante baseline; ensaio cancelado")
                    record = {"identityhash": _identity(device), "path": device.path,
                              "original13": caps["current"], "lastintended": caps["current"]}
                    outcomes = []
                    for value, count in ((0, 3), (1, 10)):
                        _close_preview(process)
                        process = None
                        # Start each phase from the original. Thus a crash between
                        # journal fsync and SET always leaves original or intended.
                        if restoration_due:
                            _restore(camera, device, path, record)
                            restoration_due = False
                            report["restored"] = True
                        _check_identity(device)
                        record["lastintended"] = value
                        _save(path, record)
                        restoration_due = True
                        camera.write(13, value)
                        if camera.read(13)[0] != value:
                            raise CameraError("readback do ensaio divergente")
                        process = _preview(device, f"fase {value}")
                        for cycle in range(1, count + 1):
                            expected = ("NÃO deve inverter automaticamente" if value == 0 else
                                        "deve inverter ao descer e retornar ao subir, automaticamente")
                            result = _answer(process, f"Fase {value}, ciclo {cycle}/{count}: "
                                             f"frente/mesa/frente; {expected}. "
                                             "Acomode até 2 s e mantenha 3 s em cada pose")
                            current = camera.read(13)[0]
                            report["observations"].append({"phase": value, "cycle": cycle,
                                                           "result": result, "read13": current})
                            outcomes.append(result == "pass" and current == value)
                    negative = _answer(process, "Câmera imóvel: mova pessoa/objeto e abra/feche "
                                       "obturador sem inclinar. pass = nenhuma inversão espúria")
                    report["observations"].append({"phase": "negative", "result": negative})
                    report["validated"] = all(outcomes) and negative == "pass"
                    report["status"] = "observed_pass" if report["validated"] else "inconclusive"
                    exit_code = 0 if report["validated"] else 1
            finally:
                # A second signal must not interrupt the bounded restoration attempt.
                for number in old_handlers:
                    signal.signal(number, signal.SIG_IGN)
                try:
                    _close_preview(process)
                finally:
                    process = None
                    if restoration_due:
                        try:
                            _restore(camera, device, path, record)
                            report["restored"] = True
                        except Exception as exc:
                            report["restored"] = False
                            report["validated"] = False
                            report["status"] = "restore_failed"
                            exit_code = 1
                            print(f"FALHA NA RESTAURAÇÃO; journal preservado: {exc}", file=sys.stderr)
    except (Exception, KeyboardInterrupt) as exc:
        report["validated"] = False
        if report["status"] != "restore_failed":
            report["status"] = "interrupted" if isinstance(exc, (ProbeStopped, KeyboardInterrupt)) else "error"
        report["error"] = str(exc)
        exit_code = 1
    finally:
        for number, handler in old_handlers.items():
            signal.signal(number, handler)
        if lock_fd is not None:
            os.close(lock_fd)
        print(json.dumps(report, ensure_ascii=False), flush=True)
    return exit_code

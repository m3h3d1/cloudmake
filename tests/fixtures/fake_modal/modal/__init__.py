"""Offline double of the Modal SDK surface used by backend/modal-sandbox.

State lives under FAKE_MODAL_STATE so separate processes share it. Paths under
FAKE_MODAL_ROOT map to the same local path (it doubles as the workspace or
Volume); other remote paths map into a per-sandbox directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid

from . import exception


STATE = Path(os.environ["FAKE_MODAL_STATE"])
ROOT = os.environ["FAKE_MODAL_ROOT"]


def _log(event: str, **details) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE / "calls.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"event": event, **details}) + "\n")


def _sandboxes() -> dict:
    path = STATE / "sandboxes.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save(sandboxes: dict) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    (STATE / "sandboxes.json").write_text(json.dumps(sandboxes), encoding="utf-8")


class App:
    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def lookup(cls, name: str, *, create_if_missing: bool = False, **_) -> "App":
        return cls(name)


class Volume:
    def __init__(self, name: str) -> None:
        self.name = name

    @classmethod
    def from_name(cls, name: str, *, create_if_missing: bool = False, version=None, **_) -> "Volume":
        return cls(name)


class Image:
    def __init__(self, description: str) -> None:
        self.description = description

    @classmethod
    def debian_slim(cls, **_) -> "Image":
        return cls("debian_slim")

    @classmethod
    def from_registry(cls, reference: str, **_) -> "Image":
        return cls(f"registry:{reference}")

    def apt_install(self, *packages: str) -> "Image":
        return Image(f"{self.description}+apt:{','.join(packages)}")


class _Process:
    def __init__(self, command: list[str], disconnect: bool) -> None:
        result = subprocess.run(command, stdout=subprocess.PIPE, text=True)
        if disconnect:
            raise exception.ConnectionError("fake stream disconnected")
        self.stdout = result.stdout.splitlines(keepends=True)
        self.returncode = result.returncode

    def wait(self) -> int:
        return self.returncode


class _Filesystem:
    def __init__(self, sandbox: "Sandbox") -> None:
        self._sandbox = sandbox

    def _path(self, remote: str) -> Path:
        if remote == ROOT or remote.startswith(ROOT + "/"):
            return Path(remote)
        return STATE / "fs" / self._sandbox.object_id / remote.lstrip("/")

    def copy_from_local(self, local, remote: str) -> None:
        target = self._path(remote)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local, target)

    def copy_to_local(self, remote: str, local) -> None:
        marker = os.environ.get("FAKE_MODAL_DOWNLOAD_ERROR")
        if marker and marker in remote:
            raise exception.Error("fake download failure")
        source = self._path(remote)
        if not source.is_file():
            raise exception.SandboxFilesystemNotFoundError(remote)
        shutil.copyfile(source, local)

    def read_text(self, remote: str) -> str:
        source = self._path(remote)
        if not source.is_file():
            raise exception.SandboxFilesystemNotFoundError(remote)
        return source.read_text(encoding="utf-8")

    def write_text(self, data: str, remote: str) -> None:
        target = self._path(remote)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(data, encoding="utf-8")

    def make_directory(self, remote: str, *, create_parents: bool = True) -> None:
        self._path(remote).mkdir(parents=True, exist_ok=True)


class Sandbox:
    def __init__(self, name: str, object_id: str) -> None:
        self.name = name
        self.object_id = object_id
        self.filesystem = _Filesystem(self)

    @classmethod
    def from_name(cls, app_name: str, name: str, **_) -> "Sandbox":
        record = _sandboxes().get(name)
        if record is None or record["state"] != "running":
            raise exception.NotFoundError(name)
        return cls(name, record["id"])

    @classmethod
    def create(cls, *, app, name, image, gpu, cpu, memory, timeout, idle_timeout, volumes, tags, **_) -> "Sandbox":
        if os.environ.get("FAKE_MODAL_CAPACITY"):
            raise exception.ResourceExhaustedError("no capacity")
        sandboxes = _sandboxes()
        if sandboxes.get(name, {}).get("state") == "running":
            raise exception.Error(f"sandbox {name} already running")
        if ROOT not in volumes:
            shutil.rmtree(ROOT, ignore_errors=True)
        Path(ROOT).mkdir(parents=True, exist_ok=True)
        object_id = "sb-" + uuid.uuid4().hex[:12]
        sandboxes[name] = {"id": object_id, "state": "running"}
        _save(sandboxes)
        _log(
            "create", app=app.name, name=name, image=image.description, gpu=gpu,
            cpu=cpu, memory=memory, timeout=timeout, idle_timeout=idle_timeout,
            volumes={path: volume.name for path, volume in volumes.items()}, tags=tags,
        )
        return cls(name, object_id)

    def poll(self):
        return None if _sandboxes().get(self.name, {}).get("id") == self.object_id and _sandboxes()[self.name]["state"] == "running" else 0

    def terminate(self, *, wait: bool = False):
        sandboxes = _sandboxes()
        sandboxes[self.name]["state"] = "terminated"
        _save(sandboxes)
        _log("terminate", name=self.name)
        return 0

    def exec(self, *command: str, **_) -> _Process:
        marker = os.environ.get("FAKE_MODAL_EXEC_DISCONNECT")
        _log("exec", name=self.name, command=list(command))
        return _Process(list(command), bool(marker) and marker in command[-1])

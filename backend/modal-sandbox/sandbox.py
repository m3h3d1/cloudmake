#!/usr/bin/env python3
"""Lifecycle, file, and exec control for one named Modal Sandbox.

Runs under an interpreter that can import the official ``modal`` SDK. Exit
codes: 0 success, 1 provider or transport failure, 2 invalid request,
3 positively absent remote file, 75 capacity or readiness deadline.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, os.fspath(Path(__file__).resolve().parents[2] / "core"))
from run_state import update

import modal
from modal import exception as modal_exception


EX_ABSENT = 3
EX_TEMPFAIL = 75
CONFIG_FILE = "/tmp/.cloudmake-sandbox.json"
DEFAULT_PACKAGES = ("make", "curl", "ca-certificates", "xz-utils")


class ControlError(RuntimeError):
    pass


def lookup(app: str, name: str):
    try:
        return modal.Sandbox.from_name(app, name)
    except modal_exception.NotFoundError:
        return None


def running(app: str, name: str):
    sandbox = lookup(app, name)
    if sandbox is None or sandbox.poll() is not None:
        raise ControlError(f"Modal sandbox {name!r} is not running; start it with: cloudmake --start")
    return sandbox


def requested_config(arguments: argparse.Namespace) -> dict[str, object]:
    return {
        "gpu": arguments.gpu or None,
        "cpu": arguments.cpu,
        "memory": arguments.memory,
        "image": arguments.image or None,
        "persist": arguments.persist == "1",
        "volume": arguments.volume if arguments.persist == "1" else None,
        "remote_root": arguments.remote_root,
    }


def image_for(reference: str):
    if reference:
        return modal.Image.from_registry(reference)
    return modal.Image.debian_slim().apt_install(*DEFAULT_PACKAGES)


def run(sandbox, command: str, *, stream: bool) -> int:
    process = sandbox.exec("sh", "-c", f"{command} 2>&1")
    for chunk in process.stdout:
        if stream:
            sys.stdout.write(chunk)
            sys.stdout.flush()
    process.wait()
    return process.returncode


def wait_ready(sandbox, deadline_seconds: float) -> bool:
    deadline = time.monotonic() + deadline_seconds
    while True:
        try:
            if run(sandbox, "command -v make && command -v tar", stream=False) == 0:
                return True
        except modal_exception.Error:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(3)


def start(arguments: argparse.Namespace) -> int:
    state = Path(arguments.operation_state)
    config = requested_config(arguments)
    update(state, {"phase": "allocation", "provider_state": "unknown"})
    sandbox = lookup(arguments.app, arguments.name)
    if sandbox is not None and sandbox.poll() is None:
        try:
            current = json.loads(sandbox.filesystem.read_text(CONFIG_FILE))
        except modal_exception.Error:
            current = None
        if current != config:
            raise ControlError(
                f"Modal sandbox {arguments.name!r} runs with a different configuration "
                f"({current}); stop it first with: cloudmake --stop"
            )
        created = False
    else:
        app = modal.App.lookup(arguments.app, create_if_missing=True)
        volumes = {}
        if config["persist"]:
            volumes[arguments.remote_root] = modal.Volume.from_name(
                arguments.volume, create_if_missing=True, version=2
            )
        try:
            sandbox = modal.Sandbox.create(
                app=app,
                name=arguments.name,
                image=image_for(arguments.image),
                gpu=arguments.gpu or None,
                cpu=arguments.cpu,
                memory=arguments.memory,
                timeout=arguments.timeout,
                idle_timeout=arguments.idle_timeout,
                volumes=volumes,
                tags={"cloudmake-sandbox": arguments.name},
            )
        except modal_exception.ResourceExhaustedError as error:
            update(state, {"provider_state": "failed", "failure_code": "capacity_unavailable", "retry_safe": True})
            print(f"[modal] Capacity is temporarily unavailable: {error}", file=sys.stderr)
            return EX_TEMPFAIL
        created = True
        update(state, {"session_created": True})
    if not wait_ready(sandbox, arguments.ready_timeout):
        if created:
            sandbox.terminate()
        update(state, {"provider_state": "unknown", "failure_code": "readiness_deadline_exceeded", "retry_safe": True})
        print("[modal] Sandbox did not provide make and tar before the readiness deadline.", file=sys.stderr)
        return EX_TEMPFAIL
    if created:
        sandbox.filesystem.write_text(json.dumps(config), CONFIG_FILE)
    update(state, {"provider_state": "ready", "session_created": created})
    resource_state = Path(arguments.resource_state)
    resource_state.parent.mkdir(parents=True, exist_ok=True)
    resource_state.write_text("started\n" if created else "reused\n", encoding="utf-8")
    return 0


def status(arguments: argparse.Namespace) -> int:
    sandbox = lookup(arguments.app, arguments.name)
    if sandbox is None:
        print(f"sandbox={arguments.name} state=not found")
    elif sandbox.poll() is None:
        print(f"sandbox={arguments.name} id={sandbox.object_id} state=running")
    else:
        print(f"sandbox={arguments.name} id={sandbox.object_id} state=terminated")
    return 0


def stop(arguments: argparse.Namespace) -> int:
    sandbox = lookup(arguments.app, arguments.name)
    if sandbox is None or sandbox.poll() is not None:
        print(f"[modal] Sandbox {arguments.name!r} is not running; nothing to stop.")
        return 0
    sandbox.terminate(wait=True)
    print(f"[modal] Sandbox {arguments.name!r} stopped.")
    return 0


def upload(arguments: argparse.Namespace) -> int:
    sandbox = running(arguments.app, arguments.name)
    parent = os.path.dirname(arguments.remote)
    if parent:
        sandbox.filesystem.make_directory(parent, create_parents=True)
    sandbox.filesystem.copy_from_local(arguments.local, arguments.remote)
    return 0


def download(arguments: argparse.Namespace) -> int:
    sandbox = running(arguments.app, arguments.name)
    try:
        sandbox.filesystem.copy_to_local(arguments.remote, arguments.local)
    except modal_exception.SandboxFilesystemNotFoundError:
        return EX_ABSENT
    return 0


def execute(arguments: argparse.Namespace) -> int:
    sandbox = running(arguments.app, arguments.name)
    returncode = run(sandbox, arguments.command, stream=True)
    if arguments.result is None:
        return returncode
    target = base64.urlsafe_b64decode(arguments.target_b64.encode()).decode()
    result = Path(arguments.result)
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(
        json.dumps({"schema": 1, "target": target, "exit_code": returncode}) + "\n",
        encoding="utf-8",
    )
    return 0


def shell(arguments: argparse.Namespace) -> int:
    sandbox = running(arguments.app, arguments.name)
    print(f"[modal] Project source: {arguments.source}", flush=True)
    os.execvp(arguments.modal_bin, [arguments.modal_bin, "shell", sandbox.object_id])
    return 1


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--app", required=True)
    root.add_argument("--name", required=True)
    commands = root.add_subparsers(dest="command_name", required=True)

    begin = commands.add_parser("start")
    begin.add_argument("--gpu", default="")
    begin.add_argument("--cpu", type=float, required=True)
    begin.add_argument("--memory", type=int, required=True)
    begin.add_argument("--timeout", type=int, required=True)
    begin.add_argument("--idle-timeout", type=int, required=True)
    begin.add_argument("--image", default="")
    begin.add_argument("--persist", choices=("0", "1"), default="0")
    begin.add_argument("--volume", required=True)
    begin.add_argument("--remote-root", required=True)
    begin.add_argument("--ready-timeout", type=float, default=300)
    begin.add_argument("--operation-state", required=True)
    begin.add_argument("--resource-state", required=True)
    begin.set_defaults(handler=start)

    commands.add_parser("status").set_defaults(handler=status)
    commands.add_parser("stop").set_defaults(handler=stop)

    put = commands.add_parser("upload")
    put.add_argument("local")
    put.add_argument("remote")
    put.set_defaults(handler=upload)

    get = commands.add_parser("download")
    get.add_argument("remote")
    get.add_argument("local")
    get.set_defaults(handler=download)

    run_command = commands.add_parser("exec")
    run_command.add_argument("--result")
    run_command.add_argument("--target-b64", default="")
    run_command.add_argument("command")
    run_command.set_defaults(handler=execute)

    interactive = commands.add_parser("shell")
    interactive.add_argument("--modal-bin", default="modal")
    interactive.add_argument("--source", required=True)
    interactive.set_defaults(handler=shell)
    return root


def main() -> int:
    arguments = parser().parse_args()
    try:
        return arguments.handler(arguments)
    except ControlError as error:
        print(f"[modal] {error}", file=sys.stderr)
        return 1
    except modal_exception.AuthError as error:
        print(f"[modal] Authentication failed: {error}. Run: modal token new", file=sys.stderr)
        return 1
    except modal_exception.Error as error:
        print(f"[modal] {arguments.command_name} failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

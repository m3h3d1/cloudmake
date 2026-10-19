#!/usr/bin/env python3
"""Host-side orchestration for encrypted Colab workspace checkpoints."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any


PROJECT_KEY = re.compile(r"^[0-9a-f]{24}$")
REMOTE_CONTROL = "/content/cloud-build-checkpoint-control.json"
REMOTE_RESULT = "/content/.cloud-build/checkpoint-result.json"
REMOTE_TRANSPORT = "/content/cloudmake-checkpoint-transport.py"
REMOTE_PUBLIC_KEY = "/content/.cloud-build/checkpoint-transport/public.pem"
REMOTE_ENVELOPE = "/content/.cloud-build/checkpoint-transport/key.envelope"
REMOTE_TRANSPORT_RESULT = "/content/.cloud-build/checkpoint-transport-result.json"
REMOTE_UNMOUNT_RESULT = "/content/.cloud-build/checkpoint-unmount-result.json"
REMOTE_WORKSPACE_READY = (
    "/content/.cloud-build/workspace/.cloudmake-checkpoint-ready.json"
)
CLIENT_TIMEOUT_SECONDS = 120
CLEANUP_TIMEOUT_SECONDS = 30
DRIVE_MOUNT_RETRY_DELAYS = (5, 15, 30)
CLEANUP_RETRY_DELAYS = (2, 5)


class CheckpointFailure(RuntimeError):
    pass


def run(
    command: list[os.PathLike[str] | str],
    *,
    check: bool = True,
    timeout: float | None = None,
    quiet: bool = False,
) -> int:
    executable = Path(os.fspath(command[0])).name
    try:
        result = subprocess.run(
            [os.fspath(value) for value in command],
            check=False,
            timeout=timeout,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.DEVNULL if quiet else None,
        )
    except subprocess.TimeoutExpired as error:
        if not check:
            return 124
        raise CheckpointFailure(
            f"command {executable!r} timed out after {timeout:g} seconds"
        ) from error
    if check and result.returncode:
        raise CheckpointFailure(
            f"command {executable!r} exited with status "
            f"{result.returncode}"
        )
    return result.returncode


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_receipt(path: Path, operation: str) -> dict[str, Any]:
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:
        raise CheckpointFailure(
            f"Colab did not return a valid {operation} checkpoint receipt"
        ) from error
    if (
        not isinstance(receipt, dict)
        or receipt.get("schema") != 1
        or receipt.get("operation") != operation
    ):
        raise CheckpointFailure(f"Colab returned an invalid {operation} receipt")
    if receipt.get("status") != "succeeded":
        message = receipt.get("error")
        raise CheckpointFailure(
            f"persistent-workspace {operation} failed"
            + (f": {message}" if isinstance(message, str) and message else "")
        )
    return receipt


class ColabCheckpoint:
    def __init__(self, arguments: argparse.Namespace) -> None:
        self.client = arguments.client
        self.session = arguments.session
        self.project_key = arguments.project_key
        self.workspace_id = arguments.workspace_id or arguments.project_key
        self.allow_attach = arguments.allow_attach
        self.adopt_from = arguments.adopt_from
        self.tool_root = arguments.tool_root.resolve()
        self.state_dir = arguments.state_dir.resolve()
        self.receipt_path = arguments.result.resolve()
        self.control = self.state_dir / "control.json"
        self.remote_receipt = self.state_dir / "remote-result.json"
        self.public_key = self.state_dir / "transport-public.pem"
        self.envelope = self.state_dir / "key.envelope"
        self.transport_receipt = self.state_dir / "transport-result.json"
        self.unmount_receipt = self.state_dir / "unmount-result.json"
        self.workspace_ready_receipt = self.state_dir / "workspace-ready.json"
        self.repository = (
            f"/content/drive/MyDrive/.cloudmake/checkpoints/"
            f"{self.workspace_id}/restic"
        )
        self.mounted = False
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def colab(
        self,
        *arguments: str,
        check: bool = True,
        timeout: float = CLIENT_TIMEOUT_SECONDS,
        quiet: bool = False,
    ) -> int:
        return run(
            [self.client, *arguments],
            check=check,
            timeout=timeout,
            quiet=quiet,
        )

    def upload(self, local: Path, remote: str, *, quiet: bool = False) -> None:
        self.colab(
            "upload",
            "-s",
            self.session,
            os.fspath(local),
            remote,
            quiet=quiet,
        )

    def exec_tool(self, local: Path, variable: str, value: str) -> None:
        self.colab(
            "exec",
            "-s",
            self.session,
            "-f",
            os.fspath(local),
            "--env",
            f"{variable}={value}",
            "--timeout",
            "3600",
            timeout=3660,
        )

    def mount(self) -> None:
        attempts = len(DRIVE_MOUNT_RETRY_DELAYS) + 1
        for attempt in range(1, attempts + 1):
            try:
                self.colab("drivemount", "-s", self.session, timeout=900)
                self.mounted = True
                return
            except CheckpointFailure:
                if attempt == attempts:
                    raise
                delay = DRIVE_MOUNT_RETRY_DELAYS[attempt - 1]
                print(
                    "[cloudmake] Drive mount unavailable "
                    f"attempt={attempt}/{attempts}; retrying the same session "
                    f"in {delay}s",
                    file=sys.stderr,
                )
                time.sleep(delay)

    def workspace_ready(self) -> bool:
        self.workspace_ready_receipt.unlink(missing_ok=True)
        result = self.colab(
            "download",
            "-s",
            self.session,
            REMOTE_WORKSPACE_READY,
            os.fspath(self.workspace_ready_receipt),
            check=False,
            timeout=CLEANUP_TIMEOUT_SECONDS,
            quiet=True,
        )
        if result != 0:
            return False
        try:
            marker = json.loads(
                self.workspace_ready_receipt.read_text(encoding="utf-8")
            )
        except Exception:
            return False
        return (
            isinstance(marker, dict)
            and marker.get("schema") == 1
            and marker.get("workspace_id") == self.workspace_id
            and isinstance(marker.get("profile"), str)
            and marker["profile"].startswith("cloudmake-env-")
        )

    def remote_operation(self, operation: str) -> dict[str, Any]:
        atomic_json(
            self.control,
            {
                "schema": 1,
                "project_key": self.project_key,
                "workspace_id": self.workspace_id,
                "allow_attach": self.allow_attach,
                "adopt_from": self.adopt_from,
                "repository": self.repository,
            },
        )
        self.remote_receipt.unlink(missing_ok=True)
        self.colab(
            "rm",
            "-s",
            self.session,
            REMOTE_RESULT,
            check=False,
            timeout=CLEANUP_TIMEOUT_SECONDS,
            quiet=True,
        )
        self.upload(self.control, REMOTE_CONTROL, quiet=True)
        self.upload(
            self.tool_root / "core/checkpoint_transport.py",
            REMOTE_TRANSPORT,
            quiet=True,
        )
        self.exec_tool(
            self.tool_root / "backend/colab-notebook/checkpoint.py",
            "CLOUDMAKE_CHECKPOINT_OPERATION",
            operation,
        )
        self.colab(
            "download",
            "-s",
            self.session,
            REMOTE_RESULT,
            os.fspath(self.remote_receipt),
            quiet=True,
        )
        return load_receipt(self.remote_receipt, operation)

    def ensure_key(self) -> None:
        run(
            [
                sys.executable,
                self.tool_root / "core/checkpoint_keychain.py",
                "ensure",
                "--project-key",
                self.workspace_id,
            ]
        )

    def deliver_key(self) -> None:
        self.public_key.unlink(missing_ok=True)
        self.envelope.unlink(missing_ok=True)
        self.exec_tool(
            self.tool_root / "core/checkpoint_transport.py",
            "CLOUDMAKE_CHECKPOINT_TRANSPORT_OPERATION",
            "generate",
        )
        self.colab(
            "download",
            "-s",
            self.session,
            REMOTE_PUBLIC_KEY,
            os.fspath(self.public_key),
            quiet=True,
        )
        run(
            [
                sys.executable,
                self.tool_root / "core/checkpoint_keychain.py",
                "encrypt",
                "--project-key",
                self.workspace_id,
                "--public-key",
                self.public_key,
                "--output",
                self.envelope,
            ]
        )
        self.upload(self.envelope, REMOTE_ENVELOPE, quiet=True)

    def verified_remote_action(
        self,
        *,
        operation: str,
        tool: Path,
        remote_result: str,
        local_result: Path,
        variable: str | None = None,
    ) -> None:
        local_result.unlink(missing_ok=True)
        self.colab(
            "rm",
            "-s",
            self.session,
            remote_result,
            check=False,
            timeout=CLEANUP_TIMEOUT_SECONDS,
            quiet=True,
        )
        if variable is None:
            self.colab(
                "exec",
                "-s",
                self.session,
                "-f",
                os.fspath(tool),
                "--timeout",
                "180",
                timeout=240,
            )
        else:
            self.exec_tool(tool, variable, operation)
        self.colab(
            "download",
            "-s",
            self.session,
            remote_result,
            os.fspath(local_result),
            quiet=True,
        )
        load_receipt(local_result, operation)
        self.colab(
            "rm",
            "-s",
            self.session,
            remote_result,
            check=False,
            timeout=CLEANUP_TIMEOUT_SECONDS,
            quiet=True,
        )

    def retry_verified_remote_action(self, **arguments: Any) -> None:
        attempts = len(CLEANUP_RETRY_DELAYS) + 1
        operation = str(arguments["operation"])
        for attempt in range(1, attempts + 1):
            try:
                self.verified_remote_action(**arguments)
                return
            except CheckpointFailure:
                if attempt == attempts:
                    raise
                delay = CLEANUP_RETRY_DELAYS[attempt - 1]
                print(
                    f"[cloudmake] {operation} cleanup unavailable "
                    f"attempt={attempt}/{attempts}; retrying the same session "
                    f"in {delay}s",
                    file=sys.stderr,
                )
                time.sleep(delay)

    def cleanup(self) -> list[str]:
        failures: list[str] = []
        try:
            self.retry_verified_remote_action(
                operation="destroy",
                tool=self.tool_root / "core/checkpoint_transport.py",
                remote_result=REMOTE_TRANSPORT_RESULT,
                local_result=self.transport_receipt,
                variable="CLOUDMAKE_CHECKPOINT_TRANSPORT_OPERATION",
            )
        except Exception as error:
            failures.append(f"transient checkpoint key cleanup failed: {error}")
        for remote in (REMOTE_CONTROL, REMOTE_RESULT, REMOTE_TRANSPORT):
            try:
                self.colab(
                    "rm",
                    "-s",
                    self.session,
                    remote,
                    check=False,
                    timeout=CLEANUP_TIMEOUT_SECONDS,
                    quiet=True,
                )
            except Exception:
                pass
        try:
            self.retry_verified_remote_action(
                operation="unmount",
                tool=self.tool_root / "backend/colab-notebook/drive_unmount.py",
                remote_result=REMOTE_UNMOUNT_RESULT,
                local_result=self.unmount_receipt,
            )
        except Exception as error:
            failures.append(f"Drive unmount verification failed: {error}")
        for path in (
            self.control,
            self.remote_receipt,
            self.public_key,
            self.envelope,
            self.transport_receipt,
            self.unmount_receipt,
            self.workspace_ready_receipt,
        ):
            path.unlink(missing_ok=True)
        return failures

    def execute(self, operation: str, resource_state: str) -> dict[str, Any]:
        if operation == "restore" and resource_state != "started":
            if self.workspace_ready():
                print(
                    "[cloudmake] persistent-workspace restore=not-needed "
                    "resource=reused"
                )
                return {"outcome": "skipped", "reason": "resource-reused"}
            print(
                "[cloudmake] persistent-workspace restore=required "
                "reason=workspace-uninitialized"
            )
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.mount()
        if operation == "purge":
            result = self.remote_operation("purge")
            run(
                [
                    sys.executable,
                    self.tool_root / "core/checkpoint_keychain.py",
                    "delete",
                    "--project-key",
                    self.workspace_id,
                ]
            )
            return result
        probe = self.remote_operation("probe")
        exists = probe.get("repository_exists") is True
        if operation == "restore" and not exists:
            self.ensure_key()
            print("[cloudmake] persistent-workspace restore=none repository=new")
            result = {"outcome": "none", "repository": "new"}
            if isinstance(probe.get("runtime"), dict):
                result["runtime"] = probe["runtime"]
            return result
        if not exists:
            self.ensure_key()
        self.deliver_key()
        return self.remote_operation(operation)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    root.add_argument("operation", choices=("restore", "publish", "purge"))
    root.add_argument("--client", required=True)
    root.add_argument("--session", required=True)
    root.add_argument("--project-key", required=True)
    root.add_argument("--workspace-id")
    root.add_argument("--allow-attach", action="store_true")
    root.add_argument("--adopt-from")
    root.add_argument("--resource-state", choices=("started", "reused"), required=True)
    root.add_argument("--tool-root", type=Path, required=True)
    root.add_argument("--state-dir", type=Path, required=True)
    root.add_argument("--result", type=Path, required=True)
    return root


def main() -> int:
    arguments = parser().parse_args()
    if not PROJECT_KEY.fullmatch(arguments.project_key):
        print("[cloudmake] invalid persistent-workspace project identity", file=sys.stderr)
        return 2
    if arguments.workspace_id is not None and not PROJECT_KEY.fullmatch(
        arguments.workspace_id
    ):
        print("[cloudmake] invalid persistent workspace identity", file=sys.stderr)
        return 2
    checkpoint = ColabCheckpoint(arguments)
    receipt: dict[str, Any]
    try:
        details = checkpoint.execute(arguments.operation, arguments.resource_state)
        receipt = {
            "schema": 1,
            "operation": arguments.operation,
            "status": "succeeded",
            **details,
        }
        return_code = 0
    except KeyboardInterrupt:
        receipt = {
            "schema": 1,
            "operation": arguments.operation,
            "status": "interrupted",
        }
        print("[cloudmake] persistent-workspace operation interrupted", file=sys.stderr)
        return_code = 130
    except CheckpointFailure as error:
        receipt = {
            "schema": 1,
            "operation": arguments.operation,
            "status": "failed",
            "error": str(error),
        }
        print(f"[cloudmake] infrastructure failure: {error}", file=sys.stderr)
        return_code = 1
    finally:
        cleanup_failures = checkpoint.cleanup()
    if cleanup_failures:
        message = "; ".join(cleanup_failures)
        print(f"[cloudmake] infrastructure failure: {message}", file=sys.stderr)
        receipt = {
            "schema": 1,
            "operation": arguments.operation,
            "status": "failed",
            "error": message,
        }
        return_code = 1 if return_code == 0 else return_code
    atomic_json(arguments.result, receipt)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())

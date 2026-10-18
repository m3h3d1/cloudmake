from __future__ import annotations

import base64
import json
import os
import re
import tarfile
from pathlib import Path

import pytest

from conftest import PROJECT_ROOT, run_command, write_executable


LAUNCHER = PROJECT_ROOT / "cmd" / "cloudmake"


def engine_dispatch(target: str) -> list[str]:
    encoded = base64.urlsafe_b64encode(target.encode("utf-8")).decode("ascii")
    return [f"REMOTE_TARGET_B64={encoded}", "dispatch"]


def fake_environment(fake_bin: Path, tmp_path: Path) -> dict[str, str]:
    return {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_LOG": str(tmp_path / "provider.jsonl"),
        "FAKE_REMOTE": str(tmp_path / "remote"),
        "CLOUDMAKE_CHECKPOINT_KEYCHAIN": "true",
    }


def launcher_environment(fake_bin: Path, tmp_path: Path) -> dict[str, str]:
    environment = fake_environment(fake_bin, tmp_path)
    environment.update(
        {
            "CLOUDMAKE_CONFIG_HOME": str(tmp_path / "config"),
            "CLOUDMAKE_STATE_HOME": str(tmp_path / "state"),
            "CLOUDMAKE_CACHE_HOME": str(tmp_path / "cache"),
        }
    )
    return environment


def external_project(path: Path) -> Path:
    path.mkdir()
    (path / "Makefile").write_text(
        ".PHONY: build test run package benchmark export-release\n"
        "build test run benchmark:\n\t@true\n"
        "release\\ candidate's\\ [gpu]:\n\t@true\n"
        "package:\n\t@mkdir -p $(OUTPUT_DIR)\n\t@printf artifact > $(OUTPUT_DIR)/result\n"
        "export-release:\n\t@mkdir -p dist\n\t@printf artifact > dist/result\n",
        encoding="utf-8",
    )
    (path / "source.txt").write_text("external project\n", encoding="utf-8")
    return path


def test_codespaces_anchor_provides_common_ssh_transport_prerequisites() -> None:
    configuration = json.loads(
        (PROJECT_ROOT / "backend" / "codespaces-ssh" / "anchor.json").read_text(
            encoding="utf-8"
        )
    )
    dockerfile = (
        PROJECT_ROOT / "backend" / "codespaces-ssh" / "anchor.Dockerfile"
    ).read_text(
        encoding="utf-8"
    )
    assert "ghcr.io/devcontainers/features/sshd:1" in configuration["features"]
    assert not any("docker-in-docker" in name for name in configuration["features"])
    assert configuration["remoteUser"] == "vscode"
    for command in ("make", "python3", "rsync", "tar"):
        assert command in dockerfile


def test_api1_backend_lifecycle_remains_a_compatible_session_reuse_input(
    tmp_path: Path,
) -> None:
    descriptor = tmp_path / "legacy-backend.mk"
    descriptor.write_text(
        "BACKEND := legacy\n"
        "BACKEND_API_VERSION := 1\n"
        "BACKEND_LIFECYCLE := batch\n"
        "BACKEND_CAPABILITIES := sync execute status artifacts\n"
        "BACKEND_OCI_RUNTIMES := none\n"
        "BACKEND_TRANSPORT := legacy\n"
        f"include {PROJECT_ROOT / 'core/resilience.mk'}\n",
        encoding="utf-8",
    )

    result = run_command(["make", "-f", descriptor, "backend-info"], cwd=tmp_path)

    assert "session-reuse=no" in result.stdout
    assert "lifecycle-control=unknown" in result.stdout
    assert "workspace-durability=unknown" in result.stdout
    assert "target_replay=none" in result.stdout
    assert "oci-native=no" in result.stdout
    assert "internet-inbound=unknown" in result.stdout
    assert "internet-outbound=unknown" in result.stdout


def calls(log: Path, program: str | None = None) -> list[list[str]]:
    if not log.exists():
        return []
    records = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    if program is not None:
        records = [record for record in records if record[0] == program]
    return records


def install_fake_colab(fake_bin: Path) -> Path:
    return write_executable(
        fake_bin / "colab",
        r'''#!/usr/bin/env python3
import json
import os
import shutil
import sys
import tarfile
import base64
from pathlib import Path

arguments = sys.argv[1:]
log = Path(os.environ["FAKE_LOG"])
log.parent.mkdir(parents=True, exist_ok=True)
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(["colab", *arguments]) + "\n")
remote = Path(os.environ["FAKE_REMOTE"])
remote.mkdir(parents=True, exist_ok=True)

def option(name, default="cuda-build"):
    return arguments[arguments.index(name) + 1] if name in arguments else default

command = arguments[0]
session = option("-s")
marker = remote / f"session-{session}"
if command == "version":
    print("fake colab 1.0")
elif command == "sessions":
    if os.environ.get("FAKE_COLAB_AUTH_FAIL"):
        print("Colab authentication expired")
        raise SystemExit(1)
    for session_marker in sorted(remote.glob("session-*")):
        print(f"[{session_marker.name.removeprefix('session-')}] running")
elif command == "new":
    attempt_file = remote / "allocation-attempts"
    attempts = int(attempt_file.read_text(encoding="utf-8")) + 1 if attempt_file.exists() else 1
    attempt_file.write_text(str(attempts), encoding="utf-8")
    capacity_failures = int(os.environ.get("FAKE_COLAB_CAPACITY_FAILURES", "0"))
    if os.environ.get("FAKE_COLAB_CAPACITY_UNAVAILABLE") or attempts <= capacity_failures:
        print("HTTP 412: TooManyAssignmentsError: no accelerator capacity available")
        raise SystemExit(1)
    if os.environ.get("FAKE_COLAB_ALLOCATION_ERROR"):
        print(os.environ["FAKE_COLAB_ALLOCATION_ERROR"])
        raise SystemExit(2)
    marker.write_text("running", encoding="utf-8")
elif command == "status":
    print("running" if marker.exists() else "not found")
elif command == "stop":
    marker.unlink(missing_ok=True)
elif command == "upload":
    local, remote_name = arguments[-2:]
    if (
        os.environ.get("FAKE_COLAB_PREP_RECEIPT_UPLOAD_FAIL")
        and remote_name == "/content/cloud-build-prepared"
    ):
        print("Connection was lost while uploading preparation receipt.")
        raise SystemExit(1)
    shutil.copyfile(local, remote / Path(remote_name).name)
elif command == "rm":
    (remote / Path(arguments[-1]).name).unlink(missing_ok=True)
elif command == "download":
    remote_name, local = arguments[-2:]
    source = remote / Path(remote_name).name
    if (
        os.environ.get("FAKE_COLAB_OWNER_DOWNLOAD_CONNECTION_FAIL")
        and remote_name.endswith("/.cloudmake-owner.json")
        and source.exists()
    ):
        print("Connection was lost while downloading owner record.")
        raise SystemExit(1)
    if (
        os.environ.get("FAKE_COLAB_FINGERPRINT_DOWNLOAD_CONNECTION_FAIL")
        and remote_name.endswith("/source.sha256")
        and source.exists()
    ):
        print("Connection was lost while downloading source fingerprint.")
        raise SystemExit(1)
    if not source.exists():
        raise SystemExit(1)
    Path(local).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, local)
elif command == "exec":
    script = Path(option("-f", ""))
    readiness_count = remote / "readiness-count"
    readiness_attempt = int(readiness_count.read_text()) + 1 if readiness_count.exists() else 1
    if script.name == "remote_prerequisites.py":
        readiness_count.write_text(str(readiness_attempt), encoding="utf-8")
    configured_failures = int(os.environ.get("FAKE_COLAB_READINESS_FAILURES", "0"))
    should_fail_readiness = (
        os.environ.get("FAKE_COLAB_READINESS_FAIL_ALWAYS")
        or readiness_attempt <= configured_failures
        or (
            os.environ.get("FAKE_COLAB_READINESS_FAIL_ONCE")
            and not (remote / "readiness-failed-once").exists()
        )
    )
    if script.name == "remote_prerequisites.py" and should_fail_readiness:
        (remote / "readiness-failed-once").write_text("failed", encoding="utf-8")
        print("Connection was lost.")
        raise SystemExit(1)
    if script.name == "control_state.py":
        owner = "present" if (remote / ".cloudmake-owner.json").is_file() else "absent"
        fingerprint = "present" if (remote / "source.sha256").is_file() else "absent"
        print(f"[cloudmake] control-state owner={owner} fingerprint={fingerprint}")
    elif script.name == "sync.py":
        if os.environ.get("FAKE_COLAB_SYNC_INTERNAL_FAIL"):
            print("Traceback (most recent call last):")
            print("ValueError: generated workspace link escapes source")
        else:
            shutil.copyfile(remote / "cloud-build-source.sha256", remote / "source.sha256")
            if os.environ.get("FAKE_COLAB_SYNC_RECEIPT_MISMATCH"):
                (remote / "sync-result.sha256").write_text("wrong-source\n", encoding="utf-8")
            else:
                shutil.copyfile(remote / "cloud-build-source.sha256", remote / "sync-result.sha256")
            shutil.copyfile(remote / "cloud-build-owner.json", remote / ".cloudmake-owner.json")
    elif script.name == "vm_capabilities.py":
        (remote / "vm-capabilities.json").write_text(
            json.dumps(
                {
                    "schema": 1,
                    "kind": "cloudmake-execution-environment",
                    "evidence": "observed",
                    "platform": {"system": "linux", "architecture": "x86_64"},
                    "identity": {"effective_uid": 0},
                    "resources": {"cpu_count": 2},
                    "filesystem": {"executable_files": True, "symbolic_links": True},
                    "isolation": {
                        "user_namespace": {"status": "unusable"},
                        "bind_mount_namespace": {"status": "unusable"},
                    },
                    "devices": {
                        "fuse": {"present": False},
                        "kvm": {"present": False},
                    },
                    "accelerators": {"nvidia": {"status": "unavailable"}},
                }
            )
            + "\n",
            encoding="utf-8",
        )
    elif script.name == "prepare.py":
        if os.environ.get("FAKE_COLAB_PREP_INSTALL_FAIL"):
            print("Connection was lost while installing preparation receipt.")
            raise SystemExit(1)
        shutil.copyfile(remote / "cloud-build-prepared", remote / ".cloudmake-prepared")
    elif script.name == "oci_prepare.py":
        if os.environ.get("FAKE_COLAB_OCI_PREFLIGHT_FAIL"):
            payload = {
                "schema": 1,
                "mode": "preflight",
                "status": "infrastructure-failed",
                "error": "image architecture is incompatible",
            }
        else:
            control = (remote / "cloudmake-oci-control").read_text(encoding="utf-8").splitlines()
            image = base64.urlsafe_b64decode(control[0]).decode()
            devices = json.loads(base64.urlsafe_b64decode(control[1]).decode())
            payload = {
                "schema": 1,
                "mode": "preflight",
                "status": "ready",
                "runner": "oci",
                "runtime": "crun",
                "image": image,
                "digest": image.rsplit("@", 1)[1],
                "devices": devices,
            }
        (remote / "oci-preflight.json").write_text(
            json.dumps(payload) + "\n", encoding="utf-8"
        )
    elif script.suffix == ".ipynb" and (remote / "cloud-build-target").exists():
        control = (remote / "cloud-build-target").read_text(encoding="utf-8").splitlines()
        target_b64 = control[0]
        target = base64.urlsafe_b64decode(target_b64).decode()
        with log.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(["target", target]) + "\n")
        if os.environ.get("FAKE_COLAB_TARGET_CONNECTION_FAIL"):
            print("Connection was lost.")
            raise SystemExit(1)
        (remote / "target-result.json").unlink(missing_ok=True)
        if os.environ.get("FAKE_COLAB_NOTEBOOK_INTERNAL_FAIL"):
            print("Traceback (most recent call last):")
            print("RuntimeError: unexpected notebook infrastructure failure")
            raise SystemExit(1)
        target_exit = int(os.environ.get("FAKE_COLAB_TARGET_EXIT", "0"))
        if os.environ.get("FAKE_COLAB_TARGET_OUTPUT"):
            print(os.environ["FAKE_COLAB_TARGET_OUTPUT"])
        payload = {"schema": 1, "target": target, "exit_code": target_exit}
        if len(control) in (7, 8, 10) and control[5]:
            image = base64.urlsafe_b64decode(control[5]).decode()
            payload.update(
                runner="oci",
                runtime="crun",
                image=image,
                digest=image.rsplit("@", 1)[1],
                status="succeeded" if target_exit == 0 else "target-failed",
            )
        (remote / "target-result.json").write_text(
            json.dumps(payload) + "\n", encoding="utf-8"
        )
        if target_exit == 0 and len(control) in (5, 7, 8, 10) and control[4]:
            payload = remote / "artifact-payload"
            payload.mkdir(exist_ok=True)
            (payload / "hello").write_text("fake artifact\n", encoding="utf-8")
            with tarfile.open(remote / "artifacts.tar.gz", "w:gz") as archive:
                if os.environ.get("FAKE_MALICIOUS_ARTIFACT"):
                    info = tarfile.TarInfo("../escaped")
                    info.size = 3
                    import io
                    archive.addfile(info, io.BytesIO(b"bad"))
                else:
                    archive.add(payload / "hello", arcname="hello")
elif command == "url":
    print("https://colab.example.invalid/session")
elif command == "ssh":
    pass
else:
    raise SystemExit(f"unsupported fake colab command: {command}")
''',
    )


def install_fake_checkpoint_host(fake_bin: Path) -> Path:
    write_executable(
        fake_bin / "secret-tool",
        "#!/bin/sh\nexit 1\n",
    )
    return write_executable(
        fake_bin / "checkpoint-host",
        r'''#!/usr/bin/env python3
import json
import os
import shlex
import sys
from pathlib import Path

arguments = sys.argv[1:]
with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(["checkpoint", arguments[0]]) + "\n")
result = Path(arguments[arguments.index("--result") + 1])
result.parent.mkdir(parents=True, exist_ok=True)
failure = os.environ.get(f"FAKE_CHECKPOINT_{arguments[0].upper()}_FAIL")
result.write_text(
    json.dumps(
        {
            "schema": 1,
            "operation": arguments[0],
            "status": "failed" if failure else "succeeded",
            **(
                {"error": failure}
                if failure
                else {
                    "snapshot": f"{arguments[0]}-snapshot",
                    "files": 42,
                    "bytes": 8192,
                    "runtime": {
                        "cpu_count": 4,
                        "memory_bytes": 17179869184,
                        "gpu": {"name": "Tesla T4", "memory_mib": 15360},
                    },
                }
            ),
        }
    )
    + "\n",
    encoding="utf-8",
)
if failure:
    print(f"[cloudmake] infrastructure failure: {failure}", file=sys.stderr)
    raise SystemExit(1)
''',
    )


def install_fake_kaggle(fake_bin: Path) -> Path:
    return write_executable(
        fake_bin / "kaggle",
        r'''#!/usr/bin/env python3
import json
import os
import sys
import tarfile
from pathlib import Path

arguments = sys.argv[1:]
log = Path(os.environ["FAKE_LOG"])
log.parent.mkdir(parents=True, exist_ok=True)
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(["kaggle", *arguments]) + "\n")

if arguments == ["--version"]:
    print("fake kaggle 1.0")
elif arguments[:2] == ["kernels", "list"]:
    if os.environ.get("FAKE_KAGGLE_AUTH_FAIL"):
        print("Kaggle authentication expired")
        raise SystemExit(1)
    if "--format" in arguments and arguments[arguments.index("--format") + 1] == "json":
        if os.environ.get("FAKE_KAGGLE_LIST_INVALID"):
            print("not-json")
            raise SystemExit(0)
        search = arguments[arguments.index("--search") + 1]
        print(json.dumps([
            {"ref": f"tester/{search}-a"},
            {"ref": f"tester/{search}-b"},
            {"ref": "tester/unrelated"},
        ]))
    else:
        print("ref,title")
elif arguments[:2] == ["kernels", "push"]:
    if os.environ.get("FAKE_KAGGLE_PUSH_FAIL"):
        print("provider submission failed")
        raise SystemExit(1)
    print("Kernel version submitted")
elif arguments[:2] == ["kernels", "status"]:
    print(os.environ.get("FAKE_KAGGLE_STATUS", "complete"))
elif arguments[:2] == ["kernels", "output"]:
    output = Path(arguments[arguments.index("-p") + 1])
    output.mkdir(parents=True, exist_ok=True)
    pattern = arguments[arguments.index("--file-pattern") + 1]
    if "cloud-build" in pattern:
        (output / "cloud-build.log").write_text("fake kaggle log\n", encoding="utf-8")
    elif "cloudmake-target-result" in pattern:
        exit_code = int(os.environ.get("FAKE_KAGGLE_TARGET_EXIT", "0"))
        infrastructure_failure = os.environ.get("FAKE_KAGGLE_INFRA_FAIL")
        (output / "cloudmake-target-result.json").write_text(
            json.dumps(
                {"schema": 1, "status": "infrastructure-failed", "error": infrastructure_failure, "phase": "runner_preflight", "target_submission": "not_submitted", "retry_safe": True}
                if infrastructure_failure
                else {"schema": 1, "status": "succeeded" if exit_code == 0 else "target-failed", "exit_code": exit_code, "target": "fake", "runner": "native", "phase": "target_execution", "target_submission": "submitted", "retry_safe": False}
            ) + "\n",
            encoding="utf-8",
        )
    elif "cloudmake-checkpoint-result" in pattern:
        dispatch_path = output.parent / "dispatch.json"
        dispatch = json.loads(dispatch_path.read_text()) if dispatch_path.is_file() else {}
        snapshot = (
            "tester/wrong-slot"
            if os.environ.get("FAKE_KAGGLE_CHECKPOINT_MISMATCH")
            else dispatch.get("kernel_ref")
        )
        (output / "cloudmake-checkpoint-result.json").write_text(
            json.dumps({"schema": 1, "operation": "publish", "status": "succeeded", "outcome": "published", "workspace_id": dispatch.get("workspace_id"), "snapshot": snapshot, "files": 1, "bytes": 10, "sha256": "0" * 64}) + "\n",
            encoding="utf-8",
        )
    elif "cloudmake-oci-result" in pattern:
        (output / "cloudmake-oci-result.json").write_text(
            json.dumps({"schema": 1, "mode": "run", "status": "succeeded", "runner": "oci", "runtime": "proot", "digest": "sha256:" + "0" * 64, "exit_code": 0}) + "\n",
            encoding="utf-8",
        )
    elif "artifacts" in pattern:
        payload = output / "hello"
        payload.write_text("fake artifact\n", encoding="utf-8")
        with tarfile.open(output / "artifacts.tar.gz", "w:gz") as archive:
            archive.add(payload, arcname="hello")
        payload.unlink()
elif arguments[:2] == ["kernels", "delete"]:
    if os.environ.get("FAKE_KAGGLE_DELETE_FAIL"):
        print("provider delete failed")
        raise SystemExit(1)
    print("Kernel deleted")
else:
    raise SystemExit(f"unsupported fake kaggle command: {arguments}")
''',
    )


def install_fake_lightning(fake_bin: Path) -> Path:
    return write_executable(
        fake_bin / "lightning",
        r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

arguments = sys.argv[1:]
log = Path(os.environ["FAKE_LOG"])
log.parent.mkdir(parents=True, exist_ok=True)
with log.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(["lightning", *arguments]) + "\n")
remote = Path(os.environ["FAKE_REMOTE"])
remote.mkdir(parents=True, exist_ok=True)
marker = remote / "lightning-studio"

def option(name, default=""):
    return arguments[arguments.index(name) + 1] if name in arguments else default

if arguments == ["--version"]:
    print("Lightning CLI version 2026.8.18")
elif arguments[:2] == ["auth", "whoami"]:
    if os.environ.get("FAKE_LIGHTNING_AUTH_FAIL"):
        print("No Lightning credentials are available")
        raise SystemExit(1)
    print(json.dumps({"auth_type": "user", "username": "tester"}))
elif arguments[:2] == ["studio", "list"]:
    if os.environ.get("FAKE_LIGHTNING_ACCESS_FAIL"):
        print("Lightning Studio access denied")
        raise SystemExit(1)
    if marker.exists():
        print(json.dumps([json.loads(marker.read_text(encoding="utf-8"))]))
    else:
        print("[]")
elif arguments[:2] == ["studio", "start"]:
    if os.environ.get("FAKE_LIGHTNING_START_FAIL"):
        print("unauthorized")
        raise SystemExit(1)
    marker.write_text(
        json.dumps(
            {"name": option("--name"), "status": "Running", "machine": option("--machine")}
        ),
        encoding="utf-8",
    )
    print(f"Started {option('--name')}")
elif arguments[:2] == ["studio", "switch"]:
    current = json.loads(marker.read_text(encoding="utf-8"))
    current["machine"] = option("--machine")
    marker.write_text(json.dumps(current), encoding="utf-8")
    print(f"Switched {option('--name')} to {option('--machine')}")
elif arguments[:2] == ["studio", "stop"]:
    current = json.loads(marker.read_text(encoding="utf-8"))
    current["status"] = "Stopped"
    marker.write_text(json.dumps(current), encoding="utf-8")
    print(f"Stopped {option('--name')}")
elif arguments[:2] == ["ssh", "generate"]:
    print(f"# ssh s_fake@ssh.lightning.ai")
    print(f"Host {option('--name')}")
    print("  User s_fake")
    print("  Hostname ssh.lightning.ai")
    print("  IdentityFile ~/.ssh/lightning_rsa")
    print("  IdentitiesOnly yes")
else:
    raise SystemExit(f"unsupported fake lightning command: {arguments}")
''',
    )


def install_fake_ssh_tools(fake_bin: Path) -> None:
    write_executable(
        fake_bin / "gh",
        r'''#!/usr/bin/env python3
import json
import os
import shlex
import sys
from pathlib import Path
arguments = sys.argv[1:]
remote = Path(os.environ["FAKE_REMOTE"])
remote.mkdir(parents=True, exist_ok=True)
with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(["gh", *arguments]) + "\n")
if arguments[:2] == ["auth", "status"]:
    if os.environ.get("FAKE_GH_AUTH_FAIL"):
        print("GitHub authentication expired")
        raise SystemExit(1)
    print("Logged in")
elif arguments == ["--version"]:
    print("gh version 2.55.0")
elif arguments[:3] == ["codespace", "ssh", "--config"]:
    if os.environ.get("FAKE_GH_CONFIG_WAKES"):
        (remote / "codespace-state").write_text("Available\n", encoding="utf-8")
    identity = remote / "codespaces.auto"
    identity.write_text("fake private key", encoding="utf-8")
    Path(f"{identity}.pub").write_text("fake public key", encoding="utf-8")
    print("Host fake-codespace")
    print("    HostName localhost")
    print("    User codespace")
    print(f"    IdentityFile {identity}")
elif arguments[:2] == ["codespace", "ssh"]:
    remote_command = shlex.split(arguments[-1])
    script = (
        remote_command[2]
        if remote_command[:2] == ["sh", "-c"] and len(remote_command) == 3
        else arguments[-1]
    )
    if "/workspaces/*" in script and '"$path/.git"' in script:
        print("/workspaces/cloudmake")
    elif "native-oci-environment.json" in script and "cat >" in script:
        (remote / "native-oci-environment.json").write_text(
            sys.stdin.read(), encoding="utf-8"
        )
    elif "native-oci-environment.json" in script and "cat" in script:
        marker = remote / "native-oci-environment.json"
        if marker.exists():
            print(marker.read_text(encoding="utf-8"), end="")
    elif "native-oci-environment.json" in script and "rm -f" in script:
        (remote / "native-oci-environment.json").unlink(missing_ok=True)
    elif "cloudmake-oci.Dockerfile" in script:
        (remote / "cloudmake-oci.Dockerfile").write_text(
            sys.stdin.read(), encoding="utf-8"
        )
    elif ".devcontainer/devcontainer.json" in script:
        (remote / "devcontainer.json").write_text(sys.stdin.read(), encoding="utf-8")
    elif ".devcontainer/Dockerfile" in script:
        (remote / "Dockerfile").write_text(sys.stdin.read(), encoding="utf-8")
    elif script == "true":
        pass
    else:
        raise SystemExit(f"unsupported fake Codespaces SSH script: {script}")
elif arguments[:2] == ["codespace", "rebuild"]:
    if os.environ.get("FAKE_GH_REBUILD_FAIL"):
        print("rebuild failed", file=sys.stderr)
        raise SystemExit(1)
    with (remote / "rebuild-count").open("a", encoding="utf-8") as stream:
        stream.write("rebuild\n")
    print("Rebuilt")
elif arguments[:2] == ["codespace", "view"]:
    if os.environ.get("FAKE_GH_VIEW_FAIL"):
        print("Codespace state unavailable", file=sys.stderr)
        raise SystemExit(1)
    state_path = remote / "codespace-state"
    state = (
        state_path.read_text(encoding="utf-8").strip()
        if state_path.exists()
        else os.environ.get("FAKE_CODESPACE_STATE", "Available")
    )
    if "--json" in arguments:
        if os.environ.get("FAKE_GH_VIEW_MALFORMED"):
            print("not-json")
        else:
            print(json.dumps({"state": state}))
    else:
        print(state)
elif arguments[:2] == ["codespace", "stop"]:
    (remote / "codespace-state").write_text("Shutdown\n", encoding="utf-8")
    print("Stopped")
else:
    raise SystemExit(f"unsupported fake gh command: {arguments}")
''',
    )
    write_executable(
        fake_bin / "ssh",
        r'''#!/usr/bin/env python3
import io
import json
import os
import sys
import tarfile
from pathlib import Path
with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(["ssh", *sys.argv[1:]]) + "\n")
joined = " ".join(sys.argv[1:])
if "fake-codespace" in sys.argv[1:] and sys.argv[-1:] == ["true"]:
    remote = Path(os.environ["FAKE_REMOTE"])
    remote.mkdir(parents=True, exist_ok=True)
    (remote / "codespace-state").write_text("Available\n", encoding="utf-8")
if sys.argv[1:] == ["-V"]:
    print("OpenSSH_9.9 test")
if os.environ.get("FAKE_SSH_AUTH_FAIL") and "BatchMode=yes" in joined:
    print("Permission denied")
    raise SystemExit(1)
if os.environ.get("FAKE_REMOTE_MISSING"):
    if "command -v" in joined and os.environ["FAKE_REMOTE_MISSING"] in joined:
        raise SystemExit(1)
if "tar -C" in joined and ".cloudmake-artifacts.tar.gz" in joined:
    remote = Path(os.environ["FAKE_REMOTE"])
    remote.mkdir(parents=True, exist_ok=True)
    with tarfile.open(remote / "ssh-artifacts.tar.gz", "w:gz") as archive:
        if os.environ.get("FAKE_MALICIOUS_ARTIFACT"):
            member = tarfile.TarInfo("../escaped")
            member.size = 3
            archive.addfile(member, io.BytesIO(b"bad"))
        else:
            payload = b"fake ssh artifact\n"
            member = tarfile.TarInfo("hello")
            member.size = len(payload)
            archive.addfile(member, io.BytesIO(payload))
if (
    ".cloudmake-oci-runner.py" in joined
    and "--mode run" in joined
    and not os.environ.get("FAKE_SSH_OCI_NO_RECEIPT")
):
    remote = Path(os.environ["FAKE_REMOTE"])
    remote.mkdir(parents=True, exist_ok=True)
    (remote / "ssh-oci-result.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "runner": "oci",
                "runtime": "podman",
                "status": "succeeded",
                "target": "route",
                "exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
if "printf 'running" in joined:
    print("running")
''',
    )
    write_executable(
        fake_bin / "rsync",
        r'''#!/usr/bin/env python3
import json
import os
import shutil
import sys
from pathlib import Path
with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(["rsync", *sys.argv[1:]]) + "\n")
if any(".cloudmake-artifacts.tar.gz" in argument for argument in sys.argv[1:]):
    source = Path(os.environ["FAKE_REMOTE"]) / "ssh-artifacts.tar.gz"
    destination = Path(sys.argv[-1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
elif any(".cloudmake-oci-result.json" in argument for argument in sys.argv[1:]):
    source = Path(os.environ["FAKE_REMOTE"]) / "ssh-oci-result.json"
    destination = Path(sys.argv[-1])
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
''',
    )


@pytest.mark.integration
def test_launcher_runs_external_project_through_colab_native(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "external-colab")
    env = launcher_environment(fake_bin, tmp_path)

    run_command(
        [LAUNCHER, "-b", "colab", "release candidate's [gpu]", "SIZE=large"],
        cwd=project,
        env=env,
    )

    remote = Path(env["FAKE_REMOTE"])
    with tarfile.open(remote / "cloud-build-source.tar.gz", "r:gz") as archive:
        names = {name.removeprefix("./") for name in archive.getnames()}
    assert "Makefile" in names
    assert "source.txt" in names
    assert "Makefile.build" not in names
    target_b64, jobs, makefile, encoded, collect, image, devices, runtimes = (
        remote / "cloud-build-target"
    ).read_text(encoding="utf-8").splitlines()
    assert (base64.urlsafe_b64decode(target_b64).decode(), makefile) == (
        "release candidate's [gpu]",
        "Makefile",
    )
    assert int(jobs) > 0
    assert json.loads(base64.urlsafe_b64decode(encoded)) == ["SIZE=large"]
    assert collect == ""
    assert image == ""
    assert json.loads(base64.urlsafe_b64decode(devices)) == []
    assert json.loads(base64.urlsafe_b64decode(runtimes)) == []
    assert not (project / ".cloud-state").exists()
    assert list((tmp_path / "state").rglob("source.tar.gz"))


@pytest.mark.integration
def test_colab_environment_profile_does_not_sync_or_execute_project(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "external-colab-environment")
    env = launcher_environment(fake_bin, tmp_path)

    result = run_command(
        [LAUNCHER, "-b", "colab", "--environment"],
        cwd=project,
        env=env,
    )

    all_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert "environment os=linux arch=x86_64" in result.stdout
    assert "capabilities filesystem=" in result.stdout
    assert "evidence=observed (not a provider guarantee)" in result.stdout
    assert "oci-docker" not in result.stdout
    profile_path = next((tmp_path / "state").rglob("environment-profile.json"))
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert "runtimes" not in profile
    assert "paths" not in profile
    assert any(call[1] == "exec" and call[-1].endswith("vm_capabilities.py") for call in all_calls)
    assert not any(call[1] == "upload" for call in all_calls)
    assert not (Path(env["FAKE_REMOTE"]) / "cloud-build-target").exists()


@pytest.mark.integration
def test_launcher_prepares_external_project_for_kaggle_batch(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    project = external_project(tmp_path / "external-kaggle")
    env = launcher_environment(fake_bin, tmp_path)
    env["KAGGLE_USERNAME"] = "tester"

    run_command(
        [
            LAUNCHER,
            "-b",
            "kaggle",
            "benchmark",
            "SIZE=large",
        ],
        cwd=project,
        env=env,
    )

    runners = list((tmp_path / "state").rglob("runner.ipynb"))
    assert len(runners) == 1
    serialized = runners[0].read_text(encoding="utf-8")
    match = re.search(r"control[.]write_bytes\(base64[.]b64decode\('([^']+)'\)\)", serialized)
    assert match is not None
    control = json.loads(base64.b64decode(match.group(1)))
    assert control["target"] == "benchmark"
    assert control["makefile"] == "Makefile"
    assert control["project_arguments"] == ["SIZE=large"]
    assert not (project / ".cloud-state").exists()


@pytest.mark.integration
def test_kaggle_failure_still_downloads_and_prints_the_run_log(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    project = external_project(tmp_path / "external-kaggle-failure")
    env = launcher_environment(fake_bin, tmp_path)
    env.update(
        {
            "KAGGLE_USERNAME": "tester",
            "KAGGLE_POLL_SECONDS": "0.01",
            "FAKE_KAGGLE_STATUS": "error: compiler unavailable",
        }
    )

    result = run_command(
        [LAUNCHER, "-b", "kaggle", "benchmark"],
        cwd=project,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "fake kaggle log" in result.stdout
    assert any(
        call[1:3] == ["kernels", "output"]
        for call in calls(Path(env["FAKE_LOG"]), "kaggle")
    )


@pytest.mark.integration
def test_launcher_runs_external_project_through_codespaces_ssh(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    project = external_project(tmp_path / "external-ssh")
    env = launcher_environment(fake_bin, tmp_path)
    env["CODESPACE"] = "test-space"

    result = run_command(
        [
            LAUNCHER,
            "-b",
            "codespaces",
            "benchmark",
            "MESSAGE=hello world",
        ],
        cwd=project,
        env=env,
    )

    all_calls = calls(Path(env["FAKE_LOG"]))
    assert "backend=codespaces-ssh" in result.stdout
    assert "codespace=test-space" in result.stdout
    assert "resource=reused" in result.stdout

    reused = run_command(
        [
            LAUNCHER,
            "-b",
            "codespaces",
            "build",
        ],
        cwd=project,
        env=env,
    )
    assert "resource=reused" in reused.stdout
    assert any(
        call[0] == "ssh" and "fake-codespace" in call and call[-1] == "true"
        for call in all_calls
    )
    assert any(call[0] == "rsync" and f"{project}/" in call for call in all_calls)
    rsync_arguments = {
        argument for call in all_calls if call[0] == "rsync" for argument in call[1:]
    }
    assert {"--exclude=/.git/", "--exclude=/.cloud-state/", "--exclude=/.cloudmake/"} <= rsync_arguments
    assert "--delete" not in rsync_arguments
    assert not any(
        argument in rsync_arguments
        for argument in ("--exclude=/build/", "--exclude=/.venv/", "--exclude=/__pycache__/")
    )
    remote_commands = "\n".join(
        argument
        for call in all_calls
        if call[0] == "ssh"
        for argument in call[1:]
        if "make -C" in argument
    )
    assert "-f Makefile" in remote_commands
    assert "benchmark" in remote_commands
    assert "'MESSAGE=hello world'" in remote_commands
    assert "git clone" not in json.dumps(all_calls)
    assert not (project / ".cloud-state").exists()


@pytest.mark.integration
def test_launcher_runs_external_project_through_user_managed_host_ssh(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    project = external_project(tmp_path / "external-ssh-host")
    env = launcher_environment(fake_bin, tmp_path)

    run_command(
        [
            LAUNCHER,
            "-b",
            "ssh",
            "--host",
            "lab-gpu",
            "benchmark",
            "MESSAGE=hello world",
        ],
        cwd=project,
        env=env,
    )

    all_calls = calls(Path(env["FAKE_LOG"]))
    assert any(
        call[0] == "ssh"
        and "-o" in call
        and "BatchMode=yes" in call
        and "lab-gpu" in call
        and call[-1] == "true"
        for call in all_calls
    )
    assert any(call[0] == "rsync" and f"{project}/" in call for call in all_calls)
    assert any(
        call[0] == "rsync"
        and any(
            argument.startswith("lab-gpu:.cloudmake/")
            and argument.endswith("/src/")
            for argument in call[1:]
        )
        for call in all_calls
    )
    assert not any(call[0] in {"colab", "gh", "kaggle", "lightning"} for call in all_calls)
    assert "git clone" not in json.dumps(all_calls)
    assert not list((tmp_path / "state").rglob("ssh_config"))
    assert not (project / ".cloud-state").exists()


@pytest.mark.integration
def test_host_ssh_status_and_stop_preserve_user_managed_lifecycle(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)

    status = run_command(
        ["make", "BACKEND=host-ssh", "SSH_HOST=lab-gpu", "status"],
        cwd=prototype,
        env=env,
    )
    assert "[cloudmake] status=ready" in status.stdout

    Path(env["FAKE_LOG"]).write_text("", encoding="utf-8")
    stopped = run_command(
        ["make", "BACKEND=host-ssh", "SSH_HOST=lab-gpu", "stop"],
        cwd=prototype,
        env=env,
    )
    assert "user-managed" in stopped.stdout
    assert calls(Path(env["FAKE_LOG"]), "ssh") == []


@pytest.mark.integration
def test_ssh_collect_fetches_artifacts_transactionally(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    project = external_project(tmp_path / "external-package")
    env = launcher_environment(fake_bin, tmp_path)
    env["CODESPACE"] = "test-space"

    run_command(
        [LAUNCHER, "-b", "codespaces", "--collect", "dist", "export-release"],
        cwd=project,
        env=env,
    )

    assert (project / ".cloudmake" / "artifacts" / "hello").read_text(encoding="utf-8") == (
        "fake ssh artifact\n"
    )
    assert any(
        call[0] == "ssh"
        and any("tar -C" in argument and "/src/dist" in argument for argument in call[1:])
        for call in calls(Path(env["FAKE_LOG"]))
    )
    assert any(
        call[0] == "ssh"
        and any("rm -f" in argument and ".cloudmake-artifacts.tar.gz" in argument for argument in call[1:])
        for call in calls(Path(env["FAKE_LOG"]))
    )
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["status"] == "succeeded"
    assert provenance["resource_id"] == "test-space"
    assert len(provenance["source"]["fingerprint"]) == 64
    assert provenance["artifacts"]["files"] == 1
    assert provenance["artifacts"]["total_bytes"] > 0
    assert len(provenance["artifacts"]["fingerprint"]) == 64


@pytest.mark.integration
def test_ssh_oci_mode_requires_runner_plumbing_not_host_make(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    project = external_project(tmp_path / "external-ssh-oci")
    env = launcher_environment(fake_bin, tmp_path)
    image = "registry.example/eda/tools@sha256:" + "b" * 64

    result = run_command(
        [LAUNCHER, "-b", "ssh", "--host", "lab-gpu", "--image", image, "route"],
        cwd=project,
        env=env,
    )

    events = calls(Path(env["FAKE_LOG"]))
    prerequisite_probes = [
        event for event in events if event[0] == "ssh" and "command -v" in " ".join(event)
    ]
    joined = "\n".join(" ".join(event) for event in prerequisite_probes)
    assert "command -v 'python3'" in joined
    assert "command -v 'rsync'" in joined
    assert "command -v 'tar'" in joined
    assert "command -v 'make'" not in joined
    assert any(
        event[0] == "rsync" and any("oci_runner.py" in value for value in event)
        for event in events
    )
    assert "backend=host-ssh runner=oci" in result.stdout


@pytest.mark.integration
def test_host_ssh_devcontainer_uses_least_privilege_runtime_continuum_and_tunnel(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    project = external_project(tmp_path / "external-ssh-devcontainer")
    config = project / ".devcontainer" / "devcontainer.json"
    config.parent.mkdir()
    config.write_text(
        json.dumps(
            {
                "image": "registry.example/eda/tools@sha256:" + "9" * 64,
                "remoteEnv": {"FLOW": "smoke"},
                "hostRequirements": {"cpus": 1},
                "forwardPorts": [8080],
            }
        ),
        encoding="utf-8",
    )
    env = launcher_environment(fake_bin, tmp_path)

    result = run_command(
        [LAUNCHER, "-b", "ssh", "--host", "lab-restricted", "--devcontainer", "route"],
        cwd=project,
        env=env,
    )

    events = calls(Path(env["FAKE_LOG"]))
    assert any(
        event[0] == "rsync" and any("devcontainer_config.py" in value for value in event)
        for event in events
    )
    execution = next(
        event for event in events
        if event[0] == "ssh" and ".cloudmake-oci-runner.py" in " ".join(event)
    )
    serialized = " ".join(execution)
    assert "-o ExitOnForwardFailure=yes" in serialized
    assert "-L 8080:127.0.0.1:8080" in serialized
    assert "--runtime-candidate docker" in serialized
    assert serialized.index("--runtime-candidate docker") < serialized.index(
        "--runtime-candidate podman"
    )
    assert "--runtime-candidate proot" in serialized
    assert "--env FLOW=smoke" in serialized
    assert ".cloudmake-devcontainer.py --check-host" in serialized
    assert "backend=host-ssh runner=oci" in result.stdout
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    contract = provenance["runner"]["devcontainer"]
    assert contract["engine"] == "adapter"
    assert contract["requirement_sources"]["forward-ports"] == ["forwardPorts"]
    assert contract["native_capabilities"] == []
    assert "image-digest" in contract["adapter_capabilities"]


@pytest.mark.integration
def test_ssh_oci_success_continues_to_artifact_collection(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    project = external_project(tmp_path / "external-ssh-oci-collect")
    env = launcher_environment(fake_bin, tmp_path)
    image = "registry.example/eda/tools@sha256:" + "c" * 64

    run_command(
        [
            LAUNCHER,
            "-b",
            "ssh",
            "--host",
            "lab-gpu",
            "--image",
            image,
            "--collect",
            "dist",
            "export-release",
        ],
        cwd=project,
        env=env,
    )

    assert (project / ".cloudmake" / "artifacts" / "hello").read_text(encoding="utf-8") == (
        "fake ssh artifact\n"
    )


@pytest.mark.integration
def test_ssh_oci_missing_terminal_receipt_is_infrastructure_failure(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    project = external_project(tmp_path / "external-ssh-oci-no-receipt")
    env = launcher_environment(fake_bin, tmp_path)
    env["FAKE_SSH_OCI_NO_RECEIPT"] = "1"
    image = "registry.example/eda/tools@sha256:" + "d" * 64

    result = run_command(
        [LAUNCHER, "-b", "ssh", "--host", "lab-gpu", "--image", image, "route"],
        cwd=project,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "OCI infrastructure failure: invalid runner receipt" in result.stdout
    assert "target 'route' failed" not in result.stdout


@pytest.mark.integration
def test_ssh_collect_rejects_unsafe_artifact_and_preserves_previous_output(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    project = external_project(tmp_path / "external-malicious-package")
    artifacts = project / ".cloudmake" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "keep").write_text("keep", encoding="utf-8")
    env = launcher_environment(fake_bin, tmp_path)
    env["CODESPACE"] = "test-space"
    env["FAKE_MALICIOUS_ARTIFACT"] = "1"

    result = run_command(
        [LAUNCHER, "-b", "codespaces", "--collect", "dist", "export-release"],
        cwd=project,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert (artifacts / "keep").read_text(encoding="utf-8") == "keep"
    assert not (project.parent / "escaped").exists()


@pytest.mark.integration
def test_colab_native_uploads_changed_source_and_skips_unchanged_archive(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    log = Path(env["FAKE_LOG"])

    first = run_command(["make", *engine_dispatch("build")], cwd=prototype, env=env)
    assert "Source unchanged" not in first.stdout
    first_calls = calls(log, "colab")
    assert any(call[1] == "new" for call in first_calls)
    assert any(
        call[1:3] == ["exec", "-s"]
        and call[-1].endswith("backend/colab-notebook/sync.py")
        for call in first_calls
    )
    notebook_exec = next(
        call
        for call in first_calls
        if call[1] == "exec" and call[-1].endswith("runner.ipynb")
    )
    assert ".cloud-state/colab-notebook/cloud-build-prototype/runner.ipynb" in notebook_exec[-1]
    assert not (prototype / "backend" / "colab-notebook" / "colab_output.ipynb").exists()
    readiness_calls = [
        call
        for call in first_calls
        if call[1] == "exec" and call[-1].endswith("remote_prerequisites.py")
    ]
    assert all(call[call.index("--timeout") + 1] == "15" for call in readiness_calls)
    assert all(
        call[call.index("--timeout") + 1] == "3600"
        for call in first_calls
        if call[1] == "exec"
        and not call[-1].endswith(("remote_prerequisites.py", "control_state.py"))
    )
    assert any(call[1] == "upload" and call[-1] == "/content/cloud-build-source.tar.gz" for call in first_calls)
    assert not any(call[1] == "ssh" for call in first_calls)

    log.write_text("", encoding="utf-8")
    second = run_command(["make", *engine_dispatch("run")], cwd=prototype, env=env)
    second_calls = calls(log, "colab")
    assert "Source unchanged; skipping archive upload" in second.stdout
    assert not any(call[1] == "new" for call in second_calls)
    assert not any(call[1] == "upload" and call[-1] == "/content/cloud-build-source.tar.gz" for call in second_calls)
    assert any(call[1] == "upload" and call[-1] == "/content/cloud-build-target" for call in second_calls)


@pytest.mark.integration
def test_colab_sync_internal_failure_blocks_project_target(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env["FAKE_COLAB_SYNC_INTERNAL_FAIL"] = "1"

    result = run_command(
        ["make", *engine_dispatch("build")],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "source synchronization did not produce a success receipt" in result.stdout
    events = calls(Path(env["FAKE_LOG"]), "colab")
    assert not any(
        event[1] == "exec"
        and event[event.index("-f") + 1].endswith("runner.ipynb")
        for event in events
    )


@pytest.mark.integration
def test_colab_sync_rejects_receipt_for_different_source(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env["FAKE_COLAB_SYNC_RECEIPT_MISMATCH"] = "1"

    result = run_command(
        ["make", *engine_dispatch("build")],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "source synchronization receipt does not match" in result.stdout
    events = calls(Path(env["FAKE_LOG"]), "colab")
    assert not any(
        event[1] == "exec"
        and event[event.index("-f") + 1].endswith("runner.ipynb")
        for event in events
    )


@pytest.mark.integration
def test_colab_launcher_reports_context_reuse_and_expected_target_failure_cleanly(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "external-context-project")
    env = launcher_environment(fake_bin, tmp_path)
    env["COLAB_SESSION"] = "tilelang-lab"

    selected = run_command([LAUNCHER, "--use", "colab"], cwd=project, env=env)
    first = run_command(
        [LAUNCHER, "--gpu=T4", "build"], cwd=project, env=env
    )
    second = run_command([LAUNCHER, "test"], cwd=project, env=env)

    assert "subsequent targets reuse this selection" in selected.stdout
    assert "selected accelerator=T4" in first.stdout
    assert (
        "[cloudmake] backend=colab-notebook accelerator=T4 "
        "session=tilelang-lab resource=started"
    ) in first.stdout
    assert (
        "[cloudmake] backend=colab-notebook accelerator=T4 "
        "session=tilelang-lab resource=reused"
    ) in second.stdout

    env["FAKE_COLAB_TARGET_EXIT"] = "2"
    env["FAKE_COLAB_TARGET_OUTPUT"] = "make: deliberate project failure"
    failed = run_command(
        [LAUNCHER, "gemm"], cwd=project, env=env, check=False
    )

    assert failed.returncode == 2
    assert "make: deliberate project failure" in failed.stdout
    assert "[cloudmake] target 'gemm' failed with exit status 2" in failed.stdout
    assert "CalledProcessError" not in failed.stdout
    assert "Traceback" not in failed.stdout
    assert "resource=reused" in failed.stdout
    assert "[cloudmake] notebook=" in failed.stdout
    assert "provenance=" in failed.stdout
    runner = next((tmp_path / "state").rglob("runner.ipynb"))
    assert runner.is_file()
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["target"] == "gemm"
    assert provenance["status"] == "failed"
    assert provenance["exit_code"] == 2
    assert provenance["phase"] == "target_execution"
    assert provenance["provider_state"] == "failed"
    assert provenance["target_submission"] == "submitted"
    assert provenance["retry_safe"] is False


@pytest.mark.integration
def test_colab_oci_runner_preflights_before_submitting_target(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "external-oci-project")
    env = launcher_environment(fake_bin, tmp_path)
    env["COLAB_SESSION"] = "oci-project"
    image = "registry.example/orfs@sha256:" + "a" * 64

    result = run_command(
        [LAUNCHER, "-b", "colab", "--image", image, "route"],
        cwd=project,
        env=env,
    )

    events = calls(Path(env["FAKE_LOG"]), "colab")
    preflight = [
        event
        for event in events
        if event[1] == "exec"
        and event[event.index("-f") + 1].endswith("oci_prepare.py")
    ]
    target = [
        event
        for event in events
        if event[1] == "exec"
        and event[event.index("-f") + 1].endswith("runner.ipynb")
    ]
    assert len(preflight) == 1
    assert len(target) == 1
    assert events.index(preflight[0]) < events.index(target[0])
    assert "backend=colab-notebook runner=oci" in result.stdout
    assert "target 'route' failed" not in result.stdout
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["runner"]["kind"] == "oci"
    assert provenance["runner"]["image"] == image
    assert provenance["runner"]["devices"] == []
    assert provenance["runner"]["runtime"] == "crun"
    assert provenance["runner"]["digest"] == "sha256:" + "a" * 64


@pytest.mark.integration
def test_colab_devcontainer_profile_reaches_preflight_and_target_control(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "external-devcontainer-project")
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    image = "registry.example/science/tools@sha256:" + "d" * 64
    configuration.write_text(
        json.dumps(
            {
                "image": image,
                "remoteEnv": {"FLOW": "smoke"},
                "hostRequirements": {"cpus": 1, "gpu": "optional"},
            }
        ),
        encoding="utf-8",
    )
    env = launcher_environment(fake_bin, tmp_path)
    env["COLAB_SESSION"] = "devcontainer-project"

    result = run_command(
        [LAUNCHER, "-b", "colab", "--devcontainer", "route"],
        cwd=project,
        env=env,
    )

    remote = Path(env["FAKE_REMOTE"])
    preflight = (remote / "cloudmake-oci-control").read_text(
        encoding="utf-8"
    ).splitlines()
    target = (remote / "cloud-build-target").read_text(
        encoding="utf-8"
    ).splitlines()
    assert len(preflight) == 8
    assert json.loads(base64.urlsafe_b64decode(preflight[6]).decode()) == [
        "FLOW=smoke"
    ]
    assert json.loads(base64.urlsafe_b64decode(preflight[7]).decode()) == {
        "cpus": 1,
        "gpu": "optional",
    }
    assert len(target) == 10
    assert json.loads(base64.urlsafe_b64decode(target[8]).decode()) == [
        "FLOW=smoke"
    ]
    assert "workstation=devcontainer" in result.stdout
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    contract = provenance["runner"]["devcontainer"]
    assert contract["engine"] == "adapter"
    assert contract["requirement_sources"]["host-requirements"] == [
        "hostRequirements"
    ]
    assert contract["adapter_capabilities"] == [
        "cdi",
        "environment",
        "host-requirements",
        "image-digest",
        "security-policy",
    ]


@pytest.mark.integration
def test_colab_oci_preflight_failure_never_submits_target(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "external-oci-failure")
    env = launcher_environment(fake_bin, tmp_path)
    env["FAKE_COLAB_OCI_PREFLIGHT_FAIL"] = "1"
    image = "registry.example/build@sha256:" + "b" * 64

    result = run_command(
        [LAUNCHER, "-b", "colab", "--image", image, "build"],
        cwd=project,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "OCI preflight failed; the target was not submitted" in result.stdout
    events = calls(Path(env["FAKE_LOG"]), "colab")
    assert not any(
        event[1] == "exec"
        and event[event.index("-f") + 1].endswith("runner.ipynb")
        for event in events
        if "-f" in event
    )
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["phase"] == "runner_preflight"
    assert provenance["target_submission"] == "not_submitted"
    assert provenance["retry_safe"] is True

@pytest.mark.integration
def test_colab_default_path_has_no_checkpoint_or_drive_side_effects(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    checkpoint_host = install_fake_checkpoint_host(fake_bin)
    env = fake_environment(fake_bin, tmp_path)

    run_command(
        [
            "make",
            "BACKEND=colab-notebook",
            f"COLAB_CHECKPOINT_HOST={checkpoint_host}",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
    )

    events = calls(Path(env["FAKE_LOG"]))
    assert not any(event[0] == "checkpoint" for event in events)
    assert not any(
        event[0] == "colab" and event[1] == "drivemount" for event in events
    )
    assert (
        sum(
            event[0] == "colab"
            and event[1] == "exec"
            and event[event.index("-f") + 1].endswith("runner.ipynb")
            for event in events
        )
        == 1
    )


@pytest.mark.integration
def test_colab_checkpoint_wraps_successful_target_in_restore_and_publish(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    checkpoint_host = install_fake_checkpoint_host(fake_bin)
    env = fake_environment(fake_bin, tmp_path)

    run_command(
        [
            "make",
            "BACKEND=colab-notebook",
            "CLOUDMAKE_CHECKPOINT=1",
            f"CLOUDMAKE_PROJECT_KEY={'1' * 24}",
            f"COLAB_CHECKPOINT_HOST={checkpoint_host}",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
    )

    events = calls(Path(env["FAKE_LOG"]))
    restore_index = events.index(["checkpoint", "restore"])
    publish_index = events.index(["checkpoint", "publish"])
    sync_index = next(
        index
        for index, call in enumerate(events)
        if call[0] == "colab" and call[1] == "upload" and "source.tar.gz" in call[-1]
    )
    target_index = next(
        index
        for index, call in enumerate(events)
        if call[0] == "colab" and call[1] == "exec" and call[call.index("-f") + 1].endswith("runner.ipynb")
    )
    assert restore_index < sync_index < target_index < publish_index


@pytest.mark.integration
def test_colab_checkpoint_does_not_publish_after_project_target_failure(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    checkpoint_host = install_fake_checkpoint_host(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env["FAKE_COLAB_TARGET_EXIT"] = "2"

    result = run_command(
        [
            "make",
            "BACKEND=colab-notebook",
            "CLOUDMAKE_CHECKPOINT=1",
            f"CLOUDMAKE_PROJECT_KEY={'2' * 24}",
            f"COLAB_CHECKPOINT_HOST={checkpoint_host}",
            *engine_dispatch("gemm"),
        ],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    checkpoint_calls = calls(Path(env["FAKE_LOG"]), "checkpoint")
    assert checkpoint_calls == [["checkpoint", "restore"]]


@pytest.mark.integration
def test_colab_checkpoint_restore_failure_blocks_sync_and_project_target(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    checkpoint_host = install_fake_checkpoint_host(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env["FAKE_CHECKPOINT_RESTORE_FAIL"] = "restore verification failed"

    result = run_command(
        [
            "make",
            "BACKEND=colab-notebook",
            "CLOUDMAKE_CHECKPOINT=1",
            f"CLOUDMAKE_PROJECT_KEY={'3' * 24}",
            f"COLAB_CHECKPOINT_HOST={checkpoint_host}",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "infrastructure failure: restore verification failed" in result.stdout
    events = calls(Path(env["FAKE_LOG"]))
    assert [event for event in events if event[0] == "checkpoint"] == [
        ["checkpoint", "restore"]
    ]
    assert not any(
        event[0] == "colab"
        and event[1] == "upload"
        and "source.tar.gz" in event[-1]
        for event in events
    )
    assert not any(
        event[0] == "colab"
        and event[1] == "exec"
        and event[event.index("-f") + 1].endswith("runner.ipynb")
        for event in events
    )


@pytest.mark.integration
def test_colab_checkpoint_publish_failure_does_not_repeat_project_target(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    checkpoint_host = install_fake_checkpoint_host(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env["FAKE_CHECKPOINT_PUBLISH_FAIL"] = "publication verification failed"

    result = run_command(
        [
            "make",
            "BACKEND=colab-notebook",
            "CLOUDMAKE_CHECKPOINT=1",
            f"CLOUDMAKE_PROJECT_KEY={'4' * 24}",
            f"COLAB_CHECKPOINT_HOST={checkpoint_host}",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "infrastructure failure: publication verification failed" in result.stdout
    assert "project target completed; the target was not retried" in result.stdout
    events = calls(Path(env["FAKE_LOG"]))
    assert [event for event in events if event[0] == "checkpoint"] == [
        ["checkpoint", "restore"],
        ["checkpoint", "publish"],
    ]
    assert (
        sum(
            event[0] == "colab"
            and event[1] == "exec"
            and event[event.index("-f") + 1].endswith("runner.ipynb")
            for event in events
        )
        == 1
    )


@pytest.mark.integration
def test_colab_checkpoint_uses_one_run_and_records_operation_provenance(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    checkpoint_host = install_fake_checkpoint_host(fake_bin)
    project = external_project(tmp_path / "checkpoint-project")
    env = launcher_environment(fake_bin, tmp_path)
    env["COLAB_CHECKPOINT_HOST"] = str(checkpoint_host)

    result = run_command(
        [LAUNCHER, "--checkpoint", "build"], cwd=project, env=env
    )

    assert "persistent-workspace=enabled" in result.stdout
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["checkpoint"]["enabled"] is True
    assert [
        operation["operation"] for operation in provenance["checkpoint"]["operations"]
    ] == ["restore", "publish"]
    assert all(
        operation["status"] == "succeeded"
        for operation in provenance["checkpoint"]["operations"]
    )
    workspace = next((tmp_path / "state" / "workspaces").glob("*.json"))
    metadata = json.loads(workspace.read_text(encoding="utf-8"))
    assert metadata["session"].startswith("checkpoint-project-")
    assert metadata["session"] != "cuda-build"
    assert metadata["latest_snapshot"] == "publish-snapshot"
    assert metadata["files"] == 42
    assert metadata["logical_bytes"] == 8192
    assert metadata["last_runtime"]["cpu_count"] == 4
    assert len(list(latest.parent.glob("*.json"))) == 2  # run plus latest alias


@pytest.mark.integration
def test_colab_launcher_retains_unexpected_notebook_traceback(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "external-infrastructure-failure")
    env = launcher_environment(fake_bin, tmp_path)
    env["COLAB_SESSION"] = "diagnostic-session"
    env["FAKE_COLAB_NOTEBOOK_INTERNAL_FAIL"] = "1"

    result = run_command(
        [LAUNCHER, "-b", "colab", "build"],
        cwd=project,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "Traceback (most recent call last):" in result.stdout
    assert "unexpected notebook infrastructure failure" in result.stdout
    assert "[cloudmake] infrastructure failure: notebook execution" in result.stdout
    assert "target 'build' failed with exit status" not in result.stdout
    assert "[cloudmake] notebook=" in result.stdout
    assert "provenance=" in result.stdout
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["target_submission"] == "ambiguous"
    assert provenance["retry_safe"] is False
    assert provenance["failure_code"] == "ambiguous_execution"


@pytest.mark.integration
def test_colab_capacity_retry_allocates_after_two_failures_and_runs_target_once(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "capacity-eventual-success")
    env = launcher_environment(fake_bin, tmp_path)
    env.update(
        {
            "FAKE_COLAB_CAPACITY_FAILURES": "2",
            "CLOUDMAKE_RETRY_BASE_SECONDS": "0.01",
            "CLOUDMAKE_RETRY_MAX_SECONDS": "0.01",
            "CLOUDMAKE_RETRY_JITTER": "0",
        }
    )

    result = run_command(
        [LAUNCHER, "-b", "colab", "--gpu=T4", "--retry-for=2s", "build"],
        cwd=project,
        env=env,
    )

    colab_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert sum(call[1] == "new" for call in colab_calls) == 3
    assert sum(
        call[1] == "exec" and call[-1].endswith("runner.ipynb")
        for call in colab_calls
    ) == 1
    assert "capacity unavailable: colab TooManyAssignmentsError" in result.stdout
    assert "attempt=1" in result.stdout
    assert "attempt=2" in result.stdout
    assert "elapsed=" in result.stdout
    assert "remaining=" in result.stdout
    assert "next=" in result.stdout
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["allocation"] == {"attempts": 3, "outcome": "allocated"}
    assert provenance["status"] == "succeeded"
    run_records = [
        path
        for path in latest.parent.glob("*.json")
        if path.name != "latest.json"
    ]
    assert len(run_records) == 1


@pytest.mark.integration
def test_colab_capacity_retry_deadline_returns_tempfail_without_running_target(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "capacity-timeout")
    env = launcher_environment(fake_bin, tmp_path)
    env.update(
        {
            "FAKE_COLAB_CAPACITY_UNAVAILABLE": "1",
            "CLOUDMAKE_RETRY_BASE_SECONDS": "0.2",
            "CLOUDMAKE_RETRY_MAX_SECONDS": "0.2",
            "CLOUDMAKE_RETRY_JITTER": "0",
        }
    )

    result = run_command(
        [LAUNCHER, "-b", "colab", "--retry-for=1s", "build"],
        cwd=project,
        env=env,
        check=False,
        timeout=10,
    )

    colab_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert result.returncode == 75
    assert "retry deadline expired" in result.stdout
    assert "remaining=0s" in result.stdout
    assert not any(
        call[1] == "exec" and call[-1].endswith("runner.ipynb")
        for call in colab_calls
    )
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["exit_code"] == 75
    assert provenance["allocation"]["outcome"] == "capacity-timeout"
    assert provenance["allocation"]["attempts"] >= 1


@pytest.mark.integration
def test_colab_start_capacity_deadline_also_returns_tempfail(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "start-capacity-timeout")
    env = launcher_environment(fake_bin, tmp_path)
    env.update(
        {
            "FAKE_COLAB_CAPACITY_UNAVAILABLE": "1",
            "CLOUDMAKE_RETRY_BASE_SECONDS": "2",
            "CLOUDMAKE_RETRY_MAX_SECONDS": "2",
            "CLOUDMAKE_RETRY_JITTER": "0",
        }
    )

    result = run_command(
        [LAUNCHER, "-b", "colab", "--retry-for=1s", "--start"],
        cwd=project,
        env=env,
        check=False,
        timeout=10,
    )

    assert result.returncode == 75
    assert "retry deadline expired" in result.stdout
    assert not any(
        call[1] == "exec" for call in calls(Path(env["FAKE_LOG"]), "colab")
    )


@pytest.mark.integration
def test_colab_retry_does_not_repeat_non_capacity_allocation_error(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "non-retryable-allocation")
    env = launcher_environment(fake_bin, tmp_path)
    env["FAKE_COLAB_ALLOCATION_ERROR"] = "Invalid accelerator request"

    result = run_command(
        [LAUNCHER, "-b", "colab", "--retry-for=2h", "build"],
        cwd=project,
        env=env,
        check=False,
    )

    colab_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert result.returncode != 0
    assert "Invalid accelerator request" in result.stdout
    assert "capacity unavailable" not in result.stdout
    assert sum(call[1] == "new" for call in colab_calls) == 1
    assert not any(call[1] == "stop" for call in colab_calls)


@pytest.mark.integration
def test_colab_retry_never_recreates_existing_session_after_readiness_ambiguity(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "existing-ambiguous-session")
    env = launcher_environment(fake_bin, tmp_path)
    remote = Path(env["FAKE_REMOTE"])
    remote.mkdir(parents=True)
    (remote / "session-cuda-build").write_text("running", encoding="utf-8")
    env["COLAB_SESSION"] = "cuda-build"
    env["FAKE_COLAB_READINESS_FAIL_ALWAYS"] = "1"
    env["COLAB_READY_TIMEOUT"] = "0.2"
    env["COLAB_READY_POLL_SECONDS"] = "0.02"

    result = run_command(
        [LAUNCHER, "-b", "colab", "--retry-for=2h", "build"],
        cwd=project,
        env=env,
        check=False,
    )

    colab_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert result.returncode != 0
    assert "pre-existing unreachable session was not stopped" in result.stdout
    assert not any(call[1] == "new" for call in colab_calls)
    assert not any(call[1] == "stop" for call in colab_calls)
    assert sum(
        call[1] == "exec" and call[-1].endswith("remote_prerequisites.py")
        for call in colab_calls
    ) >= 2
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["allocation"] == {"attempts": 0, "outcome": "reused"}


@pytest.mark.integration
def test_colab_capacity_without_retry_for_remains_fail_fast(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "capacity-fail-fast")
    env = launcher_environment(fake_bin, tmp_path)
    env["FAKE_COLAB_CAPACITY_UNAVAILABLE"] = "1"

    result = run_command(
        [LAUNCHER, "-b", "colab", "build"],
        cwd=project,
        env=env,
        check=False,
    )

    colab_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert result.returncode != 0
    assert "TooManyAssignmentsError" in result.stdout
    assert "retry disabled" in result.stdout
    assert sum(call[1] == "new" for call in colab_calls) == 1
    assert "next=" not in result.stdout


@pytest.mark.integration
@pytest.mark.parametrize("duration", ["eventually", "0s", "25h"])
def test_invalid_retry_duration_is_rejected_before_contacting_colab(
    fake_bin: Path, tmp_path: Path, duration: str
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "invalid-retry-duration")
    env = launcher_environment(fake_bin, tmp_path)

    result = run_command(
        [LAUNCHER, "-b", "colab", f"--retry-for={duration}", "build"],
        cwd=project,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert "--retry-for" in result.stdout
    assert calls(Path(env["FAKE_LOG"]), "colab") == []


@pytest.mark.integration
def test_colab_native_archive_excludes_only_cloudmake_owned_root_paths(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    for name in (".git", ".cloud-state", ".cloudmake"):
        directory = prototype / name
        directory.mkdir(exist_ok=True)
        (directory / "excluded.txt").write_text("excluded", encoding="utf-8")
    for name in ("artifacts", ".artifacts"):
        directory = prototype / name
        directory.mkdir(exist_ok=True)
        (directory / "included.txt").write_text("included", encoding="utf-8")
    for name in ("build", ".venv", "__pycache__", ".pytest_cache"):
        directory = prototype / "any-layout" / name
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "included.txt").write_text("included", encoding="utf-8")
    (prototype / "kept.txt").write_text("kept", encoding="utf-8")
    (prototype / "generated_output.ipynb").write_text("included", encoding="utf-8")

    run_command(["make", "sync"], cwd=prototype, env=env)

    archive_path = Path(env["FAKE_REMOTE"]) / "cloud-build-source.tar.gz"
    with tarfile.open(archive_path, "r:gz") as archive:
        names = {name.removeprefix("./") for name in archive.getnames()}
    assert "kept.txt" in names
    assert "generated_output.ipynb" in names
    for name in ("artifacts", ".artifacts"):
        assert f"{name}/included.txt" in names
    for name in ("build", ".venv", "__pycache__", ".pytest_cache"):
        assert f"any-layout/{name}/included.txt" in names
    assert not any(
        name.split("/", 1)[0] in {".git", ".cloud-state", ".cloudmake"}
        for name in names
    )


@pytest.mark.integration
def test_colab_native_collect_fetch_open_and_stop(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    env = fake_environment(fake_bin, tmp_path)

    run_command(
        [
            "make",
            "REMOTE_TARGET=package",
            "REMOTE_COLLECT_DIR_B64=b3V0cHV0",
            "collect",
        ],
        cwd=prototype,
        env=env,
    )
    run_command(["make", "fetch"], cwd=prototype, env=env)
    run_command(["make", "open"], cwd=prototype, env=env)
    run_command(["make", "stop"], cwd=prototype, env=env)

    assert (prototype / ".cloudmake" / "artifacts" / "hello").read_text(encoding="utf-8") == "fake artifact\n"
    colab_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert sum(
        call[1] == "exec" and call[-1].endswith("remote_prerequisites.py")
        for call in colab_calls
    ) == 3
    assert any(call[1] == "url" and "--open" in call for call in colab_calls)
    assert any(call[1] == "stop" for call in colab_calls)


@pytest.mark.integration
def test_colab_native_reconciles_a_disappeared_session(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    run_command(["make", *engine_dispatch("build")], cwd=prototype, env=env)
    remote = Path(env["FAKE_REMOTE"])
    (remote / "session-cloud-build-prototype").unlink()
    (remote / "source.sha256").unlink()
    (remote / ".cloudmake-owner.json").unlink()
    Path(env["FAKE_LOG"]).write_text("", encoding="utf-8")

    run_command(["make", *engine_dispatch("test")], cwd=prototype, env=env)

    colab_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert any(call[1] == "new" for call in colab_calls)
    assert any(call[1] == "upload" and call[-1] == "/content/cloud-build-source.tar.gz" for call in colab_calls)


@pytest.mark.integration
def test_colab_native_retries_only_the_non_mutating_readiness_probe(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env["FAKE_COLAB_READINESS_FAIL_ONCE"] = "1"
    env["COLAB_READY_POLL_SECONDS"] = "0.01"

    result = run_command(["make", *engine_dispatch("build")], cwd=prototype, env=env)

    assert "Session is not ready" in result.stdout
    colab_calls = calls(Path(env["FAKE_LOG"]), "colab")
    readiness = [
        call
        for call in colab_calls
        if call[1] == "exec" and call[-1].endswith("remote_prerequisites.py")
    ]
    target_runs = [
        call
        for call in colab_calls
        if call[1] == "exec" and call[-1].endswith("runner.ipynb")
    ]
    assert len(readiness) == 2
    assert len(target_runs) == 1


@pytest.mark.integration
def test_colab_native_waits_through_several_startup_disconnects_and_runs_once(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env.update(
        {
            "FAKE_COLAB_READINESS_FAILURES": "3",
            "COLAB_READY_TIMEOUT": "2",
            "COLAB_READY_POLL_SECONDS": "0.01",
            "COLAB_READY_PROBE_TIMEOUT": "1",
        }
    )

    result = run_command(["make", *engine_dispatch("build")], cwd=prototype, env=env)

    assert "became ready after 4 probes" in result.stdout
    assert len(calls(Path(env["FAKE_LOG"]), "target")) == 1
    readiness = [
        call
        for call in calls(Path(env["FAKE_LOG"]), "colab")
        if call[1] == "exec" and call[-1].endswith("remote_prerequisites.py")
    ]
    assert len(readiness) == 4


@pytest.mark.integration
def test_colab_readiness_deadline_cleans_only_new_session_and_records_safe_retry(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "never-ready")
    env = launcher_environment(fake_bin, tmp_path)
    env.update(
        {
            "FAKE_COLAB_READINESS_FAIL_ALWAYS": "1",
            "COLAB_READY_TIMEOUT": "0.2",
            "COLAB_READY_POLL_SECONDS": "0.02",
            "COLAB_READY_PROBE_TIMEOUT": "0.1",
        }
    )

    result = run_command(
        [LAUNCHER, "-b", "colab", "build"], cwd=project, env=env, check=False
    )

    assert result.returncode == 75
    assert "target was not submitted" in result.stdout
    assert "retrying the command is safe" in result.stdout
    colab_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert sum(call[1] == "new" for call in colab_calls) == 1
    assert sum(call[1] == "stop" for call in colab_calls) == 1
    assert calls(Path(env["FAKE_LOG"]), "target") == []
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["phase"] == "readiness"
    assert provenance["provider_state"] == "stopped"
    assert provenance["session_created"] is True
    assert provenance["target_submission"] == "not_submitted"
    assert provenance["retry_safe"] is True
    assert provenance["failure_code"] == "readiness_deadline_exceeded"


@pytest.mark.integration
def test_colab_native_refuses_to_destroy_an_unreachable_preexisting_session(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "unreachable-existing")
    env = launcher_environment(fake_bin, tmp_path)
    run_command([LAUNCHER, "-b", "colab", "--start"], cwd=project, env=env)
    Path(env["FAKE_LOG"]).write_text("", encoding="utf-8")
    env["FAKE_COLAB_READINESS_FAIL_ALWAYS"] = "1"
    env["COLAB_READY_TIMEOUT"] = "0.2"
    env["COLAB_READY_POLL_SECONDS"] = "0.02"

    result = run_command(
        [LAUNCHER, "-b", "colab", "build"], cwd=project, env=env, check=False
    )

    assert result.returncode != 0
    assert "pre-existing unreachable session was not stopped" in result.stdout
    assert "retry_safe=true" in result.stdout
    colab_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert not any(call[1] == "new" for call in colab_calls)
    assert not any(call[1] == "stop" for call in colab_calls)
    assert not any(
        call[1] == "exec" and call[-1].endswith("runner.ipynb")
        for call in colab_calls
    )
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["runtime_state"] == "unreachable"
    assert provenance["session_created"] is False
    assert provenance["target_submission"] == "not_submitted"
    assert provenance["retry_safe"] is True


@pytest.mark.integration
def test_colab_default_sessions_are_stable_and_distinct_per_project(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    first = external_project(tmp_path / "first-project")
    second = external_project(tmp_path / "second-project")
    shared_log = tmp_path / "provider.jsonl"
    first_env = launcher_environment(fake_bin, tmp_path)
    first_env["FAKE_LOG"] = str(shared_log)
    first_env["FAKE_REMOTE"] = str(tmp_path / "first-remote")
    second_env = dict(first_env)
    second_env["FAKE_REMOTE"] = str(tmp_path / "second-remote")

    run_command([LAUNCHER, "-b", "colab", "build"], cwd=first, env=first_env)
    first_latest = next(
        (tmp_path / "state" / "projects").glob("*/runs/latest.json")
    )
    assert json.loads(first_latest.read_text(encoding="utf-8"))["runtime_state"] == "fresh"
    first_session = next(
        call[call.index("-s") + 1]
        for call in calls(shared_log, "colab")
        if call[1] == "new"
    )
    shared_log.write_text("", encoding="utf-8")
    run_command([LAUNCHER, "-b", "colab", "build"], cwd=second, env=second_env)
    second_session = next(
        call[call.index("-s") + 1]
        for call in calls(shared_log, "colab")
        if call[1] == "new"
    )
    assert first_session != second_session
    assert first_session.startswith("first-project-")
    assert second_session.startswith("second-project-")

    shared_log.write_text("", encoding="utf-8")
    run_command([LAUNCHER, "-b", "colab", "test"], cwd=first, env=first_env)
    assert not any(call[1] == "new" for call in calls(shared_log, "colab"))
    assert all(
        call[call.index("-s") + 1] == first_session
        for call in calls(shared_log, "colab")
        if "-s" in call
    )
    first_provenance = next(
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "state" / "projects").glob("*/runs/latest.json")
        if json.loads(path.read_text(encoding="utf-8"))["project"] == str(first)
    )
    assert first_provenance["runtime_state"] == "same"


@pytest.mark.integration
def test_colab_existing_session_with_lost_control_state_forces_full_sync(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "reset-runtime")
    env = launcher_environment(fake_bin, tmp_path)
    run_command([LAUNCHER, "-b", "colab", "build"], cwd=project, env=env)
    remote = Path(env["FAKE_REMOTE"])
    (remote / ".cloudmake-owner.json").unlink()
    (remote / "source.sha256").unlink()
    Path(env["FAKE_LOG"]).write_text("", encoding="utf-8")

    result = run_command([LAUNCHER, "-b", "colab", "test"], cwd=project, env=env)

    assert "Detected reset runtime" in result.stdout
    assert "runtime-local generated state may be gone" in result.stdout
    colab_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert not any(call[1] == "new" for call in colab_calls)
    assert any(
        call[1] == "upload" and call[-1] == "/content/cloud-build-source.tar.gz"
        for call in colab_calls
    )
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["runtime_state"] == "reset"
    assert provenance["target_submission"] == "submitted"


@pytest.mark.integration
def test_colab_session_preparation_runs_only_for_fresh_runtime(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    command = ["make", "COLAB_SESSION_PREPARE_TARGET=prepare", *engine_dispatch("build")]

    run_command(command, cwd=prototype, env=env)
    run_command(command, cwd=prototype, env=env)
    (prototype / "preparation-input.txt").write_text("changed\n", encoding="utf-8")
    run_command(command, cwd=prototype, env=env)
    remote = Path(env["FAKE_REMOTE"])
    (remote / ".cloudmake-owner.json").unlink()
    (remote / "source.sha256").unlink()
    run_command(command, cwd=prototype, env=env)

    targets = [call[1] for call in calls(Path(env["FAKE_LOG"]), "target")]
    assert targets == [
        "prepare",
        "build",
        "build",
        "prepare",
        "build",
        "prepare",
        "build",
    ]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("failure_variable", "failure_code", "preparation_state"),
    [
        (
            "FAKE_COLAB_PREP_RECEIPT_UPLOAD_FAIL",
            "preparation_receipt_upload_failed",
            "succeeded",
        ),
        (
            "FAKE_COLAB_PREP_INSTALL_FAIL",
            "preparation_receipt_install_ambiguous",
            "ambiguous",
        ),
    ],
)
def test_colab_preparation_receipt_failures_keep_requested_target_unsubmitted(
    fake_bin: Path,
    tmp_path: Path,
    failure_variable: str,
    failure_code: str,
    preparation_state: str,
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / failure_code)
    env = launcher_environment(fake_bin, tmp_path)
    env["COLAB_SESSION_PREPARE_TARGET"] = "prepare"
    env[failure_variable] = "1"

    result = run_command(
        [LAUNCHER, "-b", "colab", "build"], cwd=project, env=env, check=False
    )

    assert result.returncode != 0
    assert "requested target was not submitted" in result.stdout
    assert calls(Path(env["FAKE_LOG"]), "target") == [["target", "prepare"]]
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["phase"] == "preparation"
    assert provenance["provider_state"] == "unknown"
    assert provenance["preparation_state"] == preparation_state
    assert provenance["target_submission"] == "not_submitted"
    assert provenance["retry_safe"] is True
    assert provenance["failure_code"] == failure_code


@pytest.mark.integration
def test_colab_disconnect_after_submission_is_not_retried_and_is_ambiguous(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "ambiguous-target")
    env = launcher_environment(fake_bin, tmp_path)
    env["FAKE_COLAB_TARGET_CONNECTION_FAIL"] = "1"

    result = run_command(
        [LAUNCHER, "-b", "colab", "build"], cwd=project, env=env, check=False
    )

    assert result.returncode != 0
    assert "Automatic replay is unsafe" in result.stdout
    assert [call[1] for call in calls(Path(env["FAKE_LOG"]), "target")] == ["build"]
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["phase"] == "target_execution"
    assert provenance["provider_state"] == "unknown"
    assert provenance["target_submission"] == "ambiguous"
    assert provenance["retry_safe"] is False
    assert provenance["failure_code"] == "ambiguous_execution"


@pytest.mark.integration
def test_colab_native_refuses_foreign_workspace_without_explicit_adoption(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "foreign-workspace")
    env = launcher_environment(fake_bin, tmp_path)
    env["COLAB_SESSION"] = "shared"
    remote = Path(env["FAKE_REMOTE"])
    remote.mkdir(parents=True, exist_ok=True)
    (remote / ".cloudmake-owner.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "project_id": "foreign",
                "project_name": "other-project",
                "source_path": "/other/project",
                "hostname": "other-host",
            }
        ),
        encoding="utf-8",
    )

    refused = run_command(
        [LAUNCHER, "-b", "colab", "build"], cwd=project, env=env, check=False
    )
    assert refused.returncode != 0
    assert "refusing to replace Colab session" in refused.stdout
    assert not any(
        call[1] == "upload" and call[-1] == "/content/cloud-build-source.tar.gz"
        for call in calls(Path(env["FAKE_LOG"]), "colab")
    )
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["runtime_state"] == "foreign"
    assert provenance["target_submission"] == "not_submitted"
    assert provenance["retry_safe"] is True

    env["CLOUDMAKE_ADOPT"] = "1"
    adopted = run_command(
        [LAUNCHER, "-b", "colab", "build"],
        cwd=project,
        env=env,
    )
    assert "Adopting Colab session" in adopted.stdout


@pytest.mark.integration
def test_colab_owner_download_failure_never_overwrites_foreign_workspace(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "owner-download-ambiguity")
    env = launcher_environment(fake_bin, tmp_path)
    env["COLAB_SESSION"] = "shared-foreign"
    env["FAKE_COLAB_OWNER_DOWNLOAD_CONNECTION_FAIL"] = "1"
    remote = Path(env["FAKE_REMOTE"])
    remote.mkdir(parents=True)
    (remote / "session-shared-foreign").write_text("running", encoding="utf-8")
    foreign_owner = {
        "schema": 1,
        "project_id": "foreign",
        "project_name": "other-project",
        "source_path": "/other/project",
        "hostname": "other-host",
    }
    (remote / ".cloudmake-owner.json").write_text(
        json.dumps(foreign_owner), encoding="utf-8"
    )
    (remote / "source.sha256").write_text("f" * 64 + "\n", encoding="utf-8")

    result = run_command(
        [LAUNCHER, "-b", "colab", "build"], cwd=project, env=env, check=False
    )

    assert result.returncode != 0
    assert "owner exists but could not be read" in result.stdout
    assert "synchronization was refused" in result.stdout
    assert json.loads(
        (remote / ".cloudmake-owner.json").read_text(encoding="utf-8")
    ) == foreign_owner
    all_calls = calls(Path(env["FAKE_LOG"]))
    assert not any(
        call[0] == "colab" and call[1] in {"new", "stop"} for call in all_calls
    )
    assert not any(
        call[0] == "colab"
        and call[1] == "upload"
        and call[-1] == "/content/cloud-build-source.tar.gz"
        for call in all_calls
    )
    assert not any(call[0] == "target" for call in all_calls)
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["phase"] == "ownership"
    assert provenance["provider_state"] == "unknown"
    assert provenance["runtime_state"] == "unreachable"
    assert provenance["session_created"] is False
    assert provenance["target_submission"] == "not_submitted"
    assert provenance["retry_safe"] is True
    assert provenance["failure_code"] == "owner_record_unreadable"


@pytest.mark.integration
def test_colab_fingerprint_download_failure_is_not_treated_as_reset(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    project = external_project(tmp_path / "fingerprint-download-ambiguity")
    env = launcher_environment(fake_bin, tmp_path)
    run_command([LAUNCHER, "-b", "colab", "build"], cwd=project, env=env)
    Path(env["FAKE_LOG"]).write_text("", encoding="utf-8")
    env["FAKE_COLAB_FINGERPRINT_DOWNLOAD_CONNECTION_FAIL"] = "1"

    result = run_command(
        [LAUNCHER, "-b", "colab", "test"], cwd=project, env=env, check=False
    )

    assert result.returncode != 0
    assert "fingerprint exists but could not be read" in result.stdout
    all_calls = calls(Path(env["FAKE_LOG"]))
    assert not any(
        call[0] == "colab"
        and call[1] == "upload"
        and call[-1] == "/content/cloud-build-source.tar.gz"
        for call in all_calls
    )
    assert not any(call[0] == "target" for call in all_calls)
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["phase"] == "synchronization"
    assert provenance["provider_state"] == "unknown"
    assert provenance["runtime_state"] == "unreachable"
    assert provenance["target_submission"] == "not_submitted"
    assert provenance["retry_safe"] is True
    assert provenance["failure_code"] == "fingerprint_unreadable"


@pytest.mark.integration
def test_colab_fetch_rejects_malicious_artifact_and_preserves_existing_output(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env["FAKE_MALICIOUS_ARTIFACT"] = "1"
    artifacts = prototype / ".cloudmake" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "keep").write_text("keep", encoding="utf-8")

    result = run_command(
        [
            "make",
            "REMOTE_TARGET=package",
            "REMOTE_COLLECT_DIR_B64=b3V0cHV0",
            "collect",
        ],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert (artifacts / "keep").read_text(encoding="utf-8") == "keep"
    assert not (prototype.parent / "escaped").exists()


@pytest.mark.integration
def test_kaggle_submits_every_target_but_reuses_local_snapshot(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    common = ["make", "BACKEND=kaggle-notebook", "KAGGLE_USERNAME=tester", "KAGGLE_POLL_SECONDS=0.01"]

    first = run_command([*common, *engine_dispatch("build")], cwd=prototype, env=env)
    assert "Refreshed cached source archive" in first.stdout
    second = run_command([*common, *engine_dispatch("test")], cwd=prototype, env=env)
    assert "Source unchanged; reusing cached source archive" in second.stdout

    kaggle_calls = calls(Path(env["FAKE_LOG"]), "kaggle")
    assert sum(call[1:3] == ["kernels", "push"] for call in kaggle_calls) == 2
    metadata = json.loads(
        (prototype / ".cloud-state" / "kaggle-notebook" / "cloud-build-prototype" / "kernel" / "kernel-metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["is_private"] is True
    assert metadata["enable_internet"] is False


@pytest.mark.integration
def test_kaggle_checkpoint_alternates_private_output_slots_without_session_reuse(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env.update({
        "KAGGLE_USERNAME": "tester",
        "KAGGLE_POLL_SECONDS": "0.01",
        "CLOUDMAKE_CONFIG_HOME": str(tmp_path / "config"),
        "CLOUDMAKE_STATE_HOME": str(tmp_path / "state"),
        "CLOUDMAKE_CACHE_HOME": str(tmp_path / "cache"),
    })

    first = run_command(
        [LAUNCHER, "-b", "kaggle", "--persist", "build"], cwd=prototype, env=env
    )
    assert "checkpoint=published" in first.stdout
    head = next((tmp_path / "state").rglob("checkpoint/head.json"))
    first_head = json.loads(head.read_text())
    assert first_head["slot"] == "a"
    assert first_head["kernel_ref"].endswith("-a")

    second = run_command([LAUNCHER, "-b", "kaggle", "test"], cwd=prototype, env=env)
    assert "checkpoint=published" in second.stdout
    second_head = json.loads(head.read_text())
    assert second_head["slot"] == "b"
    assert second_head["kernel_ref"].endswith("-b")
    metadata = json.loads(next((tmp_path / "state").rglob("kernel-metadata.json")).read_text())
    assert metadata["is_private"] is True
    assert metadata["kernel_sources"] == [first_head["kernel_ref"]]
    assert metadata["id"] == second_head["kernel_ref"]
    assert sum(
        call[1:3] == ["kernels", "push"]
        for call in calls(Path(env["FAKE_LOG"]), "kaggle")
    ) == 2
    provenance = json.loads(next((tmp_path / "state").rglob("runs/latest.json")).read_text())
    assert provenance["phase"] == "checkpoint_publication"
    assert provenance["provider_state"] == "succeeded"
    assert provenance["target_submission"] == "submitted"
    assert provenance["retry_safe"] is False


@pytest.mark.integration
def test_kaggle_checkpoint_records_target_failure_once_without_advancing_head(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env.update({
        "KAGGLE_USERNAME": "tester",
        "KAGGLE_POLL_SECONDS": "0.01",
        "CLOUDMAKE_CONFIG_HOME": str(tmp_path / "config"),
        "CLOUDMAKE_STATE_HOME": str(tmp_path / "state"),
        "CLOUDMAKE_CACHE_HOME": str(tmp_path / "cache"),
        "FAKE_KAGGLE_TARGET_EXIT": "2",
    })

    result = run_command(
        [LAUNCHER, "-b", "kaggle", "--persist", "build"],
        cwd=prototype, env=env, check=False,
    )
    assert result.returncode == 2
    assert "checkpoint=unchanged reason=target-failed" in result.stdout
    assert not list((tmp_path / "state").rglob("checkpoint/head.json"))
    assert sum(
        call[1:3] == ["kernels", "push"]
        for call in calls(Path(env["FAKE_LOG"]), "kaggle")
    ) == 1
    provenance = json.loads(next((tmp_path / "state").rglob("runs/latest.json")).read_text())
    assert provenance["phase"] == "target_execution"
    assert provenance["provider_state"] == "failed"
    assert provenance["target_submission"] == "submitted"
    assert provenance["retry_safe"] is False


@pytest.mark.integration
def test_kaggle_infrastructure_failure_never_advances_checkpoint_head(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env.update({
        "KAGGLE_USERNAME": "tester",
        "KAGGLE_POLL_SECONDS": "0.01",
        "CLOUDMAKE_CONFIG_HOME": str(tmp_path / "config"),
        "CLOUDMAKE_STATE_HOME": str(tmp_path / "state"),
        "CLOUDMAKE_CACHE_HOME": str(tmp_path / "cache"),
        "FAKE_KAGGLE_INFRA_FAIL": "checkpoint restore failed",
    })

    result = run_command(
        [LAUNCHER, "-b", "kaggle", "--persist", "build"],
        cwd=prototype, env=env, check=False,
    )

    assert result.returncode != 0
    assert "Kaggle infrastructure failure: checkpoint restore failed" in result.stdout
    assert not list((tmp_path / "state").rglob("checkpoint/head.json"))
    assert sum(
        call[1:3] == ["kernels", "push"]
        for call in calls(Path(env["FAKE_LOG"]), "kaggle")
    ) == 1
    provenance = json.loads(next((tmp_path / "state").rglob("runs/latest.json")).read_text())
    assert provenance["phase"] == "runner_preflight"
    assert provenance["provider_state"] == "failed"
    assert provenance["target_submission"] == "not_submitted"
    assert provenance["retry_safe"] is True


@pytest.mark.integration
def test_kaggle_ambiguous_provider_submission_is_never_marked_retry_safe(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env.update({
        "KAGGLE_USERNAME": "tester",
        "KAGGLE_POLL_SECONDS": "0.01",
        "CLOUDMAKE_CONFIG_HOME": str(tmp_path / "config"),
        "CLOUDMAKE_STATE_HOME": str(tmp_path / "state"),
        "CLOUDMAKE_CACHE_HOME": str(tmp_path / "cache"),
        "FAKE_KAGGLE_PUSH_FAIL": "1",
    })

    result = run_command(
        [LAUNCHER, "-b", "kaggle", "build"], cwd=prototype, env=env, check=False
    )

    assert result.returncode != 0
    provenance = json.loads(next((tmp_path / "state").rglob("runs/latest.json")).read_text())
    assert provenance["phase"] == "provider_submission"
    assert provenance["provider_state"] == "unknown"
    assert provenance["target_submission"] == "ambiguous"
    assert provenance["retry_safe"] is False
    assert provenance["failure_code"] == "provider_submission_failed"
    assert not any(
        call[1:3] == ["kernels", "output"]
        for call in calls(Path(env["FAKE_LOG"]), "kaggle")
    )


@pytest.mark.integration
def test_kaggle_mismatched_checkpoint_receipt_does_not_replace_head(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env.update({
        "KAGGLE_USERNAME": "tester",
        "KAGGLE_POLL_SECONDS": "0.01",
        "CLOUDMAKE_CONFIG_HOME": str(tmp_path / "config"),
        "CLOUDMAKE_STATE_HOME": str(tmp_path / "state"),
        "CLOUDMAKE_CACHE_HOME": str(tmp_path / "cache"),
    })
    run_command(
        [LAUNCHER, "-b", "kaggle", "--persist", "build"], cwd=prototype, env=env
    )
    head = next((tmp_path / "state").rglob("checkpoint/head.json"))
    before = head.read_bytes()
    env["FAKE_KAGGLE_CHECKPOINT_MISMATCH"] = "1"

    result = run_command(
        [LAUNCHER, "-b", "kaggle", "test"], cwd=prototype, env=env, check=False
    )

    assert result.returncode != 0
    assert "checkpoint receipt does not match this dispatch" in result.stdout
    assert head.read_bytes() == before


@pytest.mark.integration
def test_kaggle_checkpoint_requires_private_kernel_before_provider_contact(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = launcher_environment(fake_bin, tmp_path)
    env.update({"KAGGLE_USERNAME": "tester", "KAGGLE_PRIVATE": "false"})

    result = run_command(
        [LAUNCHER, "-b", "kaggle", "--persist", "build"],
        cwd=prototype, env=env, check=False,
    )

    assert result.returncode == 2
    assert "require KAGGLE_PRIVATE=true" in result.stdout
    assert calls(Path(env["FAKE_LOG"]), "kaggle") == []


@pytest.mark.integration
def test_kaggle_generated_notebook_does_not_embed_provider_credentials(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = launcher_environment(fake_bin, tmp_path)
    sentinel = "kaggle-secret-sentinel-8b8e318a"
    env.update({
        "KAGGLE_USERNAME": "tester",
        "KAGGLE_KEY": sentinel,
        "KAGGLE_POLL_SECONDS": "0.01",
    })

    run_command(
        [LAUNCHER, "-b", "kaggle", "--persist", "build"], cwd=prototype, env=env
    )

    notebook = next((tmp_path / "state").rglob("runner.ipynb"))
    assert sentinel not in notebook.read_text(encoding="utf-8")


@pytest.mark.integration
def test_kaggle_workspace_purge_deletes_only_exact_slots(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = launcher_environment(fake_bin, tmp_path)
    env.update({"KAGGLE_USERNAME": "tester", "KAGGLE_POLL_SECONDS": "0.01"})
    run_command(
        [LAUNCHER, "-b", "kaggle", "--persist", "build"], cwd=prototype, env=env
    )
    workspace_record = next((tmp_path / "state/workspaces").glob("*.json"))
    workspace_id = json.loads(workspace_record.read_text())["workspace_id"]

    result = run_command(
        [LAUNCHER, "--force", "--workspace", "purge", workspace_id],
        cwd=prototype, env=env,
    )

    assert "persistent-workspace=" + workspace_id + " purged and detached" in result.stdout
    deletes = [
        call for call in calls(Path(env["FAKE_LOG"]), "kaggle")
        if call[1:3] == ["kernels", "delete"]
    ]
    assert {call[-1] for call in deletes} == {
        f"tester/cloudmake-ws-{workspace_id}-a",
        f"tester/cloudmake-ws-{workspace_id}-b",
    }
    assert not workspace_record.exists()


@pytest.mark.integration
def test_kaggle_workspace_purge_failure_preserves_local_head_and_record(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = launcher_environment(fake_bin, tmp_path)
    env.update({"KAGGLE_USERNAME": "tester", "KAGGLE_POLL_SECONDS": "0.01"})
    run_command(
        [LAUNCHER, "-b", "kaggle", "--persist", "build"], cwd=prototype, env=env
    )
    workspace_record = next((tmp_path / "state/workspaces").glob("*.json"))
    workspace_id = json.loads(workspace_record.read_text())["workspace_id"]
    head = next((tmp_path / "state").rglob("checkpoint/head.json"))
    env["FAKE_KAGGLE_DELETE_FAIL"] = "1"

    result = run_command(
        [LAUNCHER, "--force", "--workspace", "purge", workspace_id],
        cwd=prototype, env=env, check=False,
    )

    assert result.returncode != 0
    assert workspace_record.exists()
    assert head.exists()


@pytest.mark.integration
def test_kaggle_workspace_purge_rejects_invalid_provider_listing(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = launcher_environment(fake_bin, tmp_path)
    env.update({"KAGGLE_USERNAME": "tester", "KAGGLE_POLL_SECONDS": "0.01"})
    run_command(
        [LAUNCHER, "-b", "kaggle", "--persist", "build"], cwd=prototype, env=env
    )
    workspace_record = next((tmp_path / "state/workspaces").glob("*.json"))
    workspace_id = json.loads(workspace_record.read_text())["workspace_id"]
    head = next((tmp_path / "state").rglob("checkpoint/head.json"))
    env["FAKE_KAGGLE_LIST_INVALID"] = "1"

    result = run_command(
        [LAUNCHER, "--force", "--workspace", "purge", workspace_id],
        cwd=prototype, env=env, check=False,
    )

    assert result.returncode != 0
    assert "Kaggle kernel listing is unavailable or invalid" in result.stdout
    assert workspace_record.exists()
    assert head.exists()


@pytest.mark.integration
def test_kaggle_oci_selection_uses_qualified_proot_profile_and_enables_pull_access(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env.update({
        "KAGGLE_USERNAME": "tester",
        "KAGGLE_POLL_SECONDS": "0.01",
        "CLOUDMAKE_CONFIG_HOME": str(tmp_path / "config"),
        "CLOUDMAKE_STATE_HOME": str(tmp_path / "state"),
        "CLOUDMAKE_CACHE_HOME": str(tmp_path / "cache"),
    })
    image = "registry.example/science/tools@sha256:" + "a" * 64

    result = run_command(
        [LAUNCHER, "-b", "kaggle", "--image", image, "build"],
        cwd=prototype, env=env,
    )

    assert result.returncode == 0
    metadata = json.loads(next((tmp_path / "state").rglob("kernel-metadata.json")).read_text())
    assert metadata["enable_internet"] is True
    runner = next((tmp_path / "state").rglob("runner.ipynb")).read_text()
    match = re.search(r"control[.]write_bytes\(base64[.]b64decode\('([^']+)'\)\)", runner)
    assert match is not None
    control = json.loads(base64.b64decode(match.group(1)))
    assert control["runner"] == "oci"
    assert control["image"] == image
    assert control["runtime_candidates"] == ["proot"]
    provenance = json.loads(next((tmp_path / "state").rglob("runs/latest.json")).read_text())
    assert provenance["runner"]["runtime_candidates"] == ["proot"]
    assert provenance["runner"]["runtime"] == "proot"
    assert provenance["runner"]["status"] == "succeeded"


@pytest.mark.integration
def test_kaggle_collect_and_fetch_return_artifact(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_kaggle(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    common = ["make", "BACKEND=kaggle-notebook", "KAGGLE_USERNAME=tester", "KAGGLE_POLL_SECONDS=0.01"]
    run_command(
        [
            *common,
            "REMOTE_TARGET=package",
            "REMOTE_COLLECT_DIR_B64=b3V0cHV0",
            "collect",
        ],
        cwd=prototype,
        env=env,
    )
    run_command([*common, "fetch"], cwd=prototype, env=env)
    assert (prototype / ".cloudmake" / "artifacts" / "hello").read_text(encoding="utf-8") == "fake artifact\n"


@pytest.mark.integration
def test_codespaces_uses_anchor_only_for_ssh_and_never_clones_project(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)

    run_command(
        [
            "make",
            "BACKEND=codespaces-ssh",
            "CODESPACE=test-space",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
    )
    run_command(
        ["make", "BACKEND=codespaces-ssh", "CODESPACE=test-space", "stop"],
        cwd=prototype,
        env=env,
    )

    all_calls = calls(Path(env["FAKE_LOG"]))
    serialized = json.dumps(all_calls)
    assert ["gh", "codespace", "ssh", "--config", "-c", "test-space"] in all_calls
    assert "git clone" not in serialized
    assert "git push" not in serialized
    assert "/workspaces/.cloudmake/cloud-build-prototype/src" in serialized
    assert any(call[0] == "rsync" and f"{prototype}/" in call for call in all_calls)
    assert any("-O" in call and "exit" in call for call in all_calls if call[0] == "ssh")
    assert ["gh", "codespace", "stop", "-c", "test-space"] in all_calls


@pytest.mark.integration
def test_codespaces_stopped_resource_is_woken_transparently(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env["FAKE_CODESPACE_STATE"] = "Shutdown"
    # The real provider may start a stopped Codespace while producing its SSH
    # config. Cloudmake must retain the state observed before that side effect.
    env["FAKE_GH_CONFIG_WAKES"] = "1"

    result = run_command(
        [
            "make",
            "BACKEND=codespaces-ssh",
            "CODESPACE=test-space",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
    )

    assert "backend=codespaces-ssh" in result.stdout
    assert "codespace=test-space" in result.stdout
    assert "resource=started" in result.stdout
    all_calls = calls(Path(env["FAKE_LOG"]))
    assert [
        "gh",
        "codespace",
        "view",
        "-c",
        "test-space",
        "--json",
        "state",
    ] in all_calls
    assert any(call[0] == "ssh" and call[-1] == "true" for call in all_calls)


@pytest.mark.integration
def test_codespaces_state_failure_blocks_before_connection_and_sync(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env["FAKE_GH_VIEW_MALFORMED"] = "1"

    result = run_command(
        [
            "make",
            "BACKEND=codespaces-ssh",
            "CODESPACE=test-space",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "invalid state" in result.stdout
    all_calls = calls(Path(env["FAKE_LOG"]))
    assert not any(call[0] in {"ssh", "rsync"} for call in all_calls)


@pytest.mark.integration
def test_codespaces_stop_preserves_provider_workspace_state(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    sentinel = Path(env["FAKE_REMOTE"]) / "provider-workspace-sentinel"
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("rebuildable cache\n", encoding="utf-8")

    run_command(
        ["make", "BACKEND=codespaces-ssh", "CODESPACE=test-space", "stop"],
        cwd=prototype,
        env=env,
    )
    result = run_command(
        [
            "make",
            "BACKEND=codespaces-ssh",
            "CODESPACE=test-space",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
    )

    assert sentinel.read_text(encoding="utf-8") == "rebuildable cache\n"
    assert "resource=started" in result.stdout

    reused = run_command(
        [
            "make",
            "BACKEND=codespaces-ssh",
            "CODESPACE=test-space",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
    )
    assert "resource=reused" in reused.stdout


@pytest.mark.integration
def test_codespaces_native_oci_rebuilds_once_and_restores_anchor(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    project = external_project(tmp_path / "codespaces-native-oci")
    env = launcher_environment(fake_bin, tmp_path)
    env["CODESPACE"] = "test-space"
    image = "registry.example/science/tools@sha256:" + "a" * 64

    first = run_command(
        [LAUNCHER, "-b", "codespaces", "--image", image, "build"],
        cwd=project,
        env=env,
    )
    second = run_command(
        [LAUNCHER, "-b", "codespaces", "--image", image, "build"],
        cwd=project,
        env=env,
    )

    remote = Path(env["FAKE_REMOTE"])
    assert (remote / "rebuild-count").read_text(encoding="utf-8").splitlines() == [
        "rebuild"
    ]
    assert (remote / "cloudmake-oci.Dockerfile").read_text(
        encoding="utf-8"
    ).startswith(f"FROM {image}\n")
    assert "Codespaces OCI environment changed" in first.stdout
    assert "oci-environment=provider-native" in first.stdout
    assert "state=rebuilt" in first.stdout
    assert "Codespaces OCI environment changed" not in second.stdout
    assert "state=reused" in second.stdout
    latest = next(Path(env["CLOUDMAKE_STATE_HOME"]).rglob("runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["runner"]["runtime"] == "provider-native"
    assert provenance["runner"]["digest"] == "sha256:" + "a" * 64
    serialized = json.dumps(calls(Path(env["FAKE_LOG"])))
    assert ".cloudmake-oci-runner.py" not in serialized

    restored = run_command(
        [LAUNCHER, "-b", "codespaces", "--native", "build"],
        cwd=project,
        env=env,
    )
    assert "Codespaces OCI environment changed" in restored.stdout
    assert "oci-environment=native state=restored" in restored.stdout
    assert len((remote / "rebuild-count").read_text(encoding="utf-8").splitlines()) == 2
    assert not (remote / "native-oci-environment.json").exists()


@pytest.mark.integration
def test_codespaces_native_oci_rebuild_failure_never_submits_target(
    fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    project = external_project(tmp_path / "codespaces-native-oci-rebuild-failure")
    env = launcher_environment(fake_bin, tmp_path)
    env["CODESPACE"] = "test-space"
    env["FAKE_GH_REBUILD_FAIL"] = "1"
    image = "registry.example/science/tools@sha256:" + "a" * 64

    result = run_command(
        [LAUNCHER, "-b", "codespaces", "--image", image, "build"],
        cwd=project,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "environment preparation failed" in result.stdout
    remote = Path(env["FAKE_REMOTE"])
    marker = json.loads(
        (remote / "native-oci-environment.json").read_text(encoding="utf-8")
    )
    assert marker["status"] == "preparing"
    all_calls = calls(Path(env["FAKE_LOG"]))
    assert not any(call[0] in {"ssh", "rsync"} for call in all_calls)


@pytest.mark.integration
def test_colab_ssh_builds_proxy_configuration_and_uses_common_transport(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    identity = tmp_path / "id_ed25519"
    identity.write_text("fake private key used only as a path", encoding="utf-8")

    run_command(
        [
            "make",
            "BACKEND=colab-ssh",
            f"COLAB_IDENTITY={identity}",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
    )
    config = prototype / ".cloud-state" / "colab-ssh" / "cuda-build" / "ssh_config"
    content = config.read_text(encoding="utf-8")
    assert "Host colab.cuda-build" in content
    assert "ssh --proxy-mode" in content
    assert str(identity) in content

    all_calls = calls(Path(env["FAKE_LOG"]))
    serialized = json.dumps(all_calls)
    assert "/content/.cloudmake/cloud-build-prototype/src" in serialized
    assert any(call[0] == "rsync" for call in all_calls)


@pytest.mark.integration
def test_lightning_studio_uses_persistent_ssh_transport_without_git(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_lightning(fake_bin)
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    identity = tmp_path / "lightning_rsa"
    identity.write_text("fake provider-owned private key", encoding="utf-8")
    Path(f"{identity}.pub").write_text("fake public key", encoding="utf-8")
    common = [
        "make",
        "BACKEND=lightning-studio-ssh",
        "LIGHTNING_STUDIO=test-studio",
        "LIGHTNING_TEAMSPACE=tester/general",
        "LIGHTNING_MACHINE=T4",
        f"LIGHTNING_IDENTITY={identity}",
    ]

    run_command([*common, *engine_dispatch("build")], cwd=prototype, env=env)
    switched = [
        value if value != "LIGHTNING_MACHINE=T4" else "LIGHTNING_MACHINE=L4"
        for value in common
    ]
    run_command([*switched, *engine_dispatch("build")], cwd=prototype, env=env)
    run_command([*common, "stop"], cwd=prototype, env=env)

    config = (
        prototype
        / ".cloud-state"
        / "lightning-studio-ssh"
        / "tester--general--test-studio"
        / "ssh_config"
    )
    contents = config.read_text(encoding="utf-8")
    assert "Host test-studio" in contents
    assert "Hostname ssh.lightning.ai" in contents
    assert str(identity) in contents
    assert "~/.ssh/lightning_rsa" not in contents
    generated_state = config.parent
    assert "fake provider-owned private key" not in "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in generated_state.rglob("*")
        if path.is_file()
    )

    all_calls = calls(Path(env["FAKE_LOG"]))
    serialized = json.dumps(all_calls)
    assert [
        "lightning",
        "studio",
        "start",
        "--name",
        "test-studio",
        "--teamspace",
        "tester/general",
        "--machine",
        "T4",
        "--create",
    ] in all_calls
    assert [
        "lightning",
        "studio",
        "switch",
        "--name",
        "test-studio",
        "--teamspace",
        "tester/general",
        "--machine",
        "L4",
    ] in all_calls
    assert [
        "lightning",
        "studio",
        "stop",
        "--name",
        "test-studio",
        "--teamspace",
        "tester/general",
    ] in all_calls
    assert (
        "/teamspace/studios/this_studio/.cloudmake/cloud-build-prototype/src"
        in serialized
    )
    assert "git clone" not in serialized
    assert "git push" not in serialized


def test_unknown_backend_fails_before_provider_execution(prototype: Path) -> None:
    result = run_command(
        ["make", "BACKEND=does-not-exist", *engine_dispatch("build")],
        cwd=prototype,
        check=False,
    )
    assert result.returncode != 0
    assert 'Unknown backend "does-not-exist"' in result.stdout


def test_help_keeps_notebook_and_ssh_names_distinct(prototype: Path) -> None:
    result = run_command(["make", "help"], cwd=prototype)
    assert "local" in result.stdout
    assert "colab-notebook" in result.stdout
    assert "kaggle-notebook" in result.stdout
    assert "colab-ssh" in result.stdout
    assert "codespaces-ssh" in result.stdout
    assert "host-ssh" in result.stdout
    assert "lightning-studio-ssh" in result.stdout


@pytest.mark.parametrize(
    "backend",
    [
        "local",
        "colab-notebook",
        "kaggle-notebook",
        "codespaces-ssh",
        "colab-ssh",
        "host-ssh",
        "lightning-studio-ssh",
    ],
)
@pytest.mark.parametrize("project_target", ["build", "test", "run"])
def test_engine_defines_no_project_target_shortcuts(
    prototype: Path, backend: str, project_target: str
) -> None:
    result = run_command(
        ["make", f"BACKEND={backend}", project_target],
        cwd=prototype,
        check=False,
    )

    assert result.returncode == 2
    assert "No rule to make target" in result.stdout
    assert project_target in result.stdout


@pytest.mark.parametrize(
    (
        "backend",
        "session_reuse",
        "capability",
        "persistence_capability",
        "lifecycle_control",
        "workspace_durability",
        "internet_inbound",
        "internet_outbound",
    ),
    [
        (
            "local",
            "yes",
            "execute",
            "native-persistence",
            "local",
            "host-persistent",
            "inherited",
            "inherited",
        ),
        (
            "colab-notebook",
            "yes",
            "incremental-sync",
            "checkpoint-persistence",
            "provider-managed",
            "ephemeral",
            "no",
            "yes",
        ),
        (
            "kaggle-notebook",
            "no",
            "gpu",
            "checkpoint-persistence",
            "per-target",
            "ephemeral",
            "no",
            "conditional",
        ),
        (
            "codespaces-ssh",
            "yes",
            "shell",
            "native-persistence",
            "provider-managed",
            "stop-persistent",
            "conditional",
            "yes",
        ),
        (
            "gcp-compute-ssh",
            "yes",
            "environment-profile",
            "native-persistence",
            "provider-managed",
            "stop-persistent",
            "conditional",
            "conditional",
        ),
        (
            "colab-ssh",
            "yes",
            "gpu",
            None,
            "provider-managed",
            "ephemeral",
            "no",
            "yes",
        ),
        (
            "host-ssh",
            "yes",
            "incremental-sync",
            "native-persistence",
            "externally-managed",
            "host-persistent",
            "inherited",
            "inherited",
        ),
        (
            "lightning-studio-ssh",
            "yes",
            "persistent-storage",
            "native-persistence",
            "provider-managed",
            "stop-persistent",
            "conditional",
            "conditional",
        ),
        (
            "modal-sandbox",
            "yes",
            "shell",
            "native-persistence",
            "provider-managed",
            "ephemeral",
            "no",
            "yes",
        ),
    ],
)
def test_backend_contract_declares_session_reuse_and_capabilities(
    prototype: Path,
    backend: str,
    session_reuse: str,
    capability: str,
    persistence_capability: str | None,
    lifecycle_control: str,
    workspace_durability: str,
    internet_inbound: str,
    internet_outbound: str,
) -> None:
    result = run_command(["make", f"BACKEND={backend}", "backend-info"], cwd=prototype)
    assert "api=1" in result.stdout
    expected_status = (
        "deprecated"
        if backend in {"kaggle-notebook", "colab-ssh"}
        else "unqualified"
        if backend == "lightning-studio-ssh"
        else "supported"
    )
    assert f"product-status={expected_status}" in result.stdout
    if backend == "kaggle-notebook":
        assert "product-status-reason=fresh VM and full checkpoint materialization" in result.stdout
    elif backend == "colab-ssh":
        assert "product-status-reason=paid Colab SSH duplicates host SSH" in result.stdout
    elif backend == "lightning-studio-ssh":
        assert "product-status-reason=Lightning Studio has no retained" in result.stdout
    assert f"session-reuse={session_reuse}" in result.stdout
    assert f"lifecycle-control={lifecycle_control}" in result.stdout
    assert f"workspace-durability={workspace_durability}" in result.stdout
    assert "target_replay=none" in result.stdout
    assert capability in result.stdout
    if persistence_capability is None:
        assert "native-persistence" not in result.stdout
        assert "checkpoint-persistence" not in result.stdout
    else:
        assert persistence_capability in result.stdout
    expected_oci = (
        "proot"
        if backend == "kaggle-notebook"
        else "crun"
        if backend == "colab-notebook"
        else "none"
        if backend in {"codespaces-ssh", "modal-sandbox"}
        else "docker podman nerdctl proot"
    )
    assert f"oci-runtimes={expected_oci}" in result.stdout
    assert f"oci-native={'yes' if backend == 'codespaces-ssh' else 'no'}" in result.stdout
    assert f"internet-inbound={internet_inbound}" in result.stdout
    assert f"internet-outbound={internet_outbound}" in result.stdout
    if backend not in {"kaggle-notebook", "modal-sandbox"}:
        assert "devcontainer" in result.stdout
        assert "devcontainer-adapter-capabilities=" in result.stdout
        assert "image-digest" in result.stdout
    else:
        assert "devcontainer-adapter-capabilities=none" in result.stdout
    if backend in {"local", "codespaces-ssh"}:
        assert "devcontainer-native-capabilities=" in result.stdout
        if backend == "local":
            assert "image-build" in result.stdout
            assert "lifecycle-create" in result.stdout
            assert "devcontainer-realization-order=adapter native" in result.stdout
        else:
            assert "devcontainer-adapter-capabilities=none" in result.stdout
            assert "devcontainer-realization-order=native" in result.stdout
    else:
        assert "devcontainer-native-capabilities=none" in result.stdout
    if backend.endswith("-ssh"):
        assert "port-forward" in result.stdout


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("BACKEND_INTERNET_INBOUND", "sometimes"),
        ("BACKEND_INTERNET_OUTBOUND", "available"),
    ],
)
def test_backend_contract_rejects_unknown_internet_capability_values(
    tmp_path: Path, field: str, value: str
) -> None:
    descriptor = tmp_path / "invalid-network-backend.mk"
    descriptor.write_text(
        "BACKEND := invalid-network\n"
        "BACKEND_API_VERSION := 1\n"
        "BACKEND_SESSION_REUSE := no\n"
        "BACKEND_CAPABILITIES := sync execute status artifacts\n"
        "BACKEND_OCI_RUNTIMES := none\n"
        f"{field} := {value}\n"
        "BACKEND_TRANSPORT := invalid\n"
        f"include {PROJECT_ROOT / 'core/resilience.mk'}\n",
        encoding="utf-8",
    )

    result = run_command(
        ["make", "-f", descriptor, "backend-info"], cwd=tmp_path, check=False
    )

    assert result.returncode != 0
    assert f"invalid {field}" in result.stdout


def test_backend_contract_rejects_invalid_native_oci_value(tmp_path: Path) -> None:
    descriptor = tmp_path / "invalid-native-oci-backend.mk"
    descriptor.write_text(
        "BACKEND := invalid-native-oci\n"
        "BACKEND_API_VERSION := 1\n"
        "BACKEND_SESSION_REUSE := no\n"
        "BACKEND_CAPABILITIES := sync execute status artifacts\n"
        "BACKEND_OCI_NATIVE := maybe\n"
        "BACKEND_OCI_RUNTIMES := none\n"
        "BACKEND_TRANSPORT := invalid\n"
        f"include {PROJECT_ROOT / 'core/resilience.mk'}\n",
        encoding="utf-8",
    )

    result = run_command(
        ["make", "-f", descriptor, "backend-info"], cwd=tmp_path, check=False
    )

    assert result.returncode != 0
    assert "invalid BACKEND_OCI_NATIVE" in result.stdout


def test_backend_contract_rejects_unknown_target_replay_capability(
    tmp_path: Path,
) -> None:
    descriptor = tmp_path / "invalid-target-replay-backend.mk"
    descriptor.write_text(
        "BACKEND := invalid-target-replay\n"
        "BACKEND_API_VERSION := 1\n"
        "BACKEND_SESSION_REUSE := no\n"
        "BACKEND_CAPABILITIES := sync execute status artifacts\n"
        "BACKEND_TARGET_REPLAY := optimistic\n"
        "BACKEND_OCI_RUNTIMES := none\n"
        "BACKEND_TRANSPORT := invalid\n"
        f"include {PROJECT_ROOT / 'core/resilience.mk'}\n",
        encoding="utf-8",
    )

    result = run_command(
        ["make", "-f", descriptor, "backend-info"], cwd=tmp_path, check=False
    )

    assert result.returncode != 0
    assert "invalid BACKEND_TARGET_REPLAY" in result.stdout


def test_backend_contract_rejects_unknown_devcontainer_capability(tmp_path: Path) -> None:
    descriptor = tmp_path / "invalid-devcontainer-capability.mk"
    descriptor.write_text(
        "BACKEND := invalid-devcontainer-capability\n"
        "BACKEND_API_VERSION := 1\n"
        "BACKEND_SESSION_REUSE := no\n"
        "BACKEND_CAPABILITIES := sync execute status artifacts\n"
        "BACKEND_OCI_RUNTIMES := none\n"
        "BACKEND_DEVCONTAINER_ADAPTER_CAPABILITIES := image-digest magic-root\n"
        "BACKEND_TRANSPORT := invalid\n"
        f"include {PROJECT_ROOT / 'core/resilience.mk'}\n",
        encoding="utf-8",
    )

    result = run_command(
        ["make", "-f", descriptor, "backend-info"], cwd=tmp_path, check=False
    )

    assert result.returncode != 0
    assert "invalid Dev Container adapter capabilities" in result.stdout


@pytest.mark.parametrize(
    ("capabilities", "adapter", "native", "message"),
    [
        (
            "sync execute status artifacts devcontainer oci-runner",
            "none",
            "none",
            "declares devcontainer but no declared realization capabilities",
        ),
        (
            "sync execute status artifacts oci-runner",
            "image-digest",
            "none",
            "engine capabilities without the devcontainer capability",
        ),
        (
            "sync execute status artifacts devcontainer",
            "image-digest",
            "none",
            "without the oci-runner capability",
        ),
        (
            "sync execute status artifacts devcontainer oci-runner",
            "environment",
            "none",
            "must support image-digest",
        ),
        (
            "sync execute status artifacts devcontainer oci-runner",
            "image-digest forward-ports",
            "none",
            "without the port-forward capability",
        ),
    ],
)
def test_backend_contract_rejects_inconsistent_devcontainer_claims(
    tmp_path: Path,
    capabilities: str,
    adapter: str,
    native: str,
    message: str,
) -> None:
    descriptor = tmp_path / "inconsistent-devcontainer.mk"
    descriptor.write_text(
        "BACKEND := inconsistent-devcontainer\n"
        "BACKEND_API_VERSION := 1\n"
        "BACKEND_SESSION_REUSE := no\n"
        f"BACKEND_CAPABILITIES := {capabilities}\n"
        "BACKEND_OCI_RUNTIMES := proot\n"
        f"BACKEND_DEVCONTAINER_ADAPTER_CAPABILITIES := {adapter}\n"
        f"BACKEND_DEVCONTAINER_NATIVE_CAPABILITIES := {native}\n"
        "BACKEND_TRANSPORT := invalid\n"
        f"include {PROJECT_ROOT / 'core/resilience.mk'}\n",
        encoding="utf-8",
    )

    result = run_command(
        ["make", "-f", descriptor, "backend-info"], cwd=tmp_path, check=False
    )

    assert result.returncode != 0
    assert message in result.stdout


@pytest.mark.parametrize(
    ("order", "message"),
    [
        ("native", "omits adapter"),
        ("adapter native", "undeclared native implementation"),
        ("adapter adapter", "contains duplicates"),
        ("magic", "invalid Dev Container realization order"),
    ],
)
def test_backend_contract_rejects_inconsistent_devcontainer_realization_order(
    tmp_path: Path, order: str, message: str
) -> None:
    descriptor = tmp_path / "inconsistent-devcontainer-order.mk"
    descriptor.write_text(
        "BACKEND := inconsistent-devcontainer-order\n"
        "BACKEND_API_VERSION := 1\n"
        "BACKEND_SESSION_REUSE := yes\n"
        "BACKEND_CAPABILITIES := sync execute status artifacts devcontainer oci-runner\n"
        "BACKEND_OCI_RUNTIMES := proot\n"
        "BACKEND_DEVCONTAINER_ADAPTER_CAPABILITIES := image-digest\n"
        "BACKEND_DEVCONTAINER_NATIVE_CAPABILITIES := none\n"
        f"BACKEND_DEVCONTAINER_REALIZATION_ORDER := {order}\n"
        "BACKEND_TRANSPORT := invalid\n"
        f"include {PROJECT_ROOT / 'core/resilience.mk'}\n",
        encoding="utf-8",
    )

    result = run_command(
        ["make", "-f", descriptor, "backend-info"], cwd=tmp_path, check=False
    )

    assert result.returncode != 0
    assert message in result.stdout


def test_legacy_api1_devcontainer_descriptor_without_semantic_sets_remains_valid(
    tmp_path: Path,
) -> None:
    descriptor = tmp_path / "legacy-devcontainer.mk"
    descriptor.write_text(
        "BACKEND := legacy-devcontainer\n"
        "BACKEND_API_VERSION := 1\n"
        "BACKEND_SESSION_REUSE := yes\n"
        "BACKEND_CAPABILITIES := sync execute status artifacts devcontainer oci-runner\n"
        "BACKEND_OCI_RUNTIMES := docker\n"
        "BACKEND_TRANSPORT := legacy\n"
        f"include {PROJECT_ROOT / 'core/resilience.mk'}\n",
        encoding="utf-8",
    )

    result = run_command(
        ["make", "-f", descriptor, "backend-info"], cwd=tmp_path
    )

    assert "devcontainer-adapter-capabilities=none" in result.stdout
    assert "devcontainer-native-capabilities=none" in result.stdout
    assert "devcontainer-realization-order=none" in result.stdout


def test_backend_contract_rejects_nested_runtime_for_native_oci(
    tmp_path: Path,
) -> None:
    descriptor = tmp_path / "invalid-native-oci-runtime.mk"
    descriptor.write_text(
        "BACKEND := invalid-native-oci-runtime\n"
        "BACKEND_API_VERSION := 1\n"
        "BACKEND_SESSION_REUSE := yes\n"
        "BACKEND_CAPABILITIES := sync execute status artifacts\n"
        "BACKEND_OCI_NATIVE := yes\n"
        "BACKEND_OCI_RUNTIMES := docker\n"
        "BACKEND_TRANSPORT := invalid\n"
        f"include {PROJECT_ROOT / 'core/resilience.mk'}\n",
        encoding="utf-8",
    )

    result = run_command(
        ["make", "-f", descriptor, "backend-info"], cwd=tmp_path, check=False
    )

    assert result.returncode != 0
    assert "BACKEND_OCI_NATIVE=yes must declare OCI runtime" in result.stdout


def test_backend_contract_rejects_unknown_backend_status(tmp_path: Path) -> None:
    descriptor = tmp_path / "invalid-status-backend.mk"
    descriptor.write_text(
        "BACKEND := invalid-status\n"
        "BACKEND_API_VERSION := 1\n"
        "BACKEND_PRODUCT_STATUS := fading\n"
        "BACKEND_SESSION_REUSE := no\n"
        "BACKEND_CAPABILITIES := sync execute status artifacts\n"
        "BACKEND_OCI_RUNTIMES := none\n"
        "BACKEND_TRANSPORT := invalid\n"
        f"include {PROJECT_ROOT / 'core/resilience.mk'}\n",
        encoding="utf-8",
    )

    result = run_command(
        ["make", "-f", descriptor, "backend-info"], cwd=tmp_path, check=False
    )

    assert result.returncode != 0
    assert "invalid BACKEND_PRODUCT_STATUS" in result.stdout


@pytest.mark.parametrize("status", ["unqualified", "deprecated"])
def test_backend_contract_requires_reason_for_non_supported_status(
    tmp_path: Path, status: str
) -> None:
    descriptor = tmp_path / "missing-status-reason.mk"
    descriptor.write_text(
        "BACKEND := missing-status-reason\n"
        "BACKEND_API_VERSION := 1\n"
        f"BACKEND_PRODUCT_STATUS := {status}\n"
        "BACKEND_SESSION_REUSE := no\n"
        "BACKEND_CAPABILITIES := sync execute status artifacts\n"
        "BACKEND_OCI_RUNTIMES := none\n"
        "BACKEND_TRANSPORT := invalid\n"
        f"include {PROJECT_ROOT / 'core/resilience.mk'}\n",
        encoding="utf-8",
    )

    result = run_command(
        ["make", "-f", descriptor, "backend-info"], cwd=tmp_path, check=False
    )

    assert result.returncode != 0
    assert "must declare BACKEND_PRODUCT_STATUS_REASON" in result.stdout


@pytest.mark.integration
def test_ssh_remote_prerequisite_failure_blocks_sync(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env["FAKE_REMOTE_MISSING"] = "make"

    result = run_command(
        [
            "make",
            "BACKEND=codespaces-ssh",
            "CODESPACE=test-space",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
        check=False,
    )
    assert result.returncode != 0
    assert "Missing required remote command: make" in result.stdout
    assert calls(Path(env["FAKE_LOG"]), "rsync") == []


@pytest.mark.parametrize(
    ("backend", "override", "missing"),
    [
        ("local", "MAKE_BIN=missing-make-for-test", "missing-make-for-test"),
        ("colab-notebook", "COLAB_BIN=missing-colab-for-test", "missing-colab-for-test"),
        ("kaggle-notebook", "KAGGLE_BIN=missing-kaggle-for-test", "missing-kaggle-for-test"),
        ("codespaces-ssh", "GH_BIN=missing-gh-for-test", "missing-gh-for-test"),
        ("colab-ssh", "COLAB_BIN=missing-colab-for-test", "missing-colab-for-test"),
        ("host-ssh", "SSH_BIN=missing-ssh-for-test", "missing-ssh-for-test"),
        ("lightning-studio-ssh", "LIGHTNING_BIN=missing-lightning-for-test", "missing-lightning-for-test"),
    ],
)
def test_prerequisites_reports_missing_backend_command(
    prototype: Path, backend: str, override: str, missing: str
) -> None:
    result = run_command(
        ["make", f"BACKEND={backend}", override, "prerequisites"],
        cwd=prototype,
        check=False,
    )

    assert result.returncode == 2
    assert f"Missing required command: {missing}" in result.stdout
    assert "README" in result.stdout


@pytest.mark.parametrize(
    ("backend", "arguments", "expected"),
    [
        ("kaggle-notebook", [], "KAGGLE_USERNAME"),
        ("codespaces-ssh", [], "CODESPACE"),
        ("colab-ssh", ["COLAB_IDENTITY=/does/not/exist"], "COLAB_IDENTITY"),
        ("host-ssh", [], "SSH_HOST"),
        (
            "lightning-studio-ssh",
            [
                "LIGHTNING_STUDIO=test-studio",
                "LIGHTNING_TEAMSPACE=tester/general",
                "LIGHTNING_IDENTITY=/does/not/exist",
            ],
            "LIGHTNING_IDENTITY",
        ),
    ],
)
def test_prerequisites_reports_missing_backend_setting(
    prototype: Path,
    fake_bin: Path,
    tmp_path: Path,
    backend: str,
    arguments: list[str],
    expected: str,
) -> None:
    install_fake_colab(fake_bin)
    install_fake_kaggle(fake_bin)
    install_fake_lightning(fake_bin)
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)

    result = run_command(
        ["make", f"BACKEND={backend}", *arguments, "prerequisites"],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert expected in result.stdout


@pytest.mark.parametrize("host", ["researcher@lab.example --bad-option", "-V"])
def test_host_ssh_prerequisites_requires_a_simple_openssh_alias(
    prototype: Path, fake_bin: Path, tmp_path: Path, host: str
) -> None:
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)

    result = run_command(
        [
            "make",
            "BACKEND=host-ssh",
            f"SSH_HOST={host}",
            "prerequisites",
        ],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert "SSH_HOST must be a simple OpenSSH Host alias beginning with" in result.stdout
    assert calls(Path(env["FAKE_LOG"]), "ssh") == []


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        ("KAGGLE_TIMEOUT=0", "KAGGLE_TIMEOUT must be a positive number"),
        ("KAGGLE_POLL_SECONDS=soon", "KAGGLE_POLL_SECONDS must be a positive number"),
    ],
)
def test_kaggle_prerequisites_reject_invalid_timing_settings(
    prototype: Path,
    fake_bin: Path,
    tmp_path: Path,
    setting: str,
    expected: str,
) -> None:
    install_fake_kaggle(fake_bin)
    env = fake_environment(fake_bin, tmp_path)

    result = run_command(
        [
            "make",
            "BACKEND=kaggle-notebook",
            "KAGGLE_USERNAME=tester",
            setting,
            "prerequisites",
        ],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert expected in result.stdout


@pytest.mark.integration
@pytest.mark.parametrize(
    ("backend", "arguments", "expected_program"),
    [
        ("colab-notebook", [], "colab"),
        ("kaggle-notebook", ["KAGGLE_USERNAME=tester"], "kaggle"),
        ("codespaces-ssh", ["CODESPACE=test-space"], "gh"),
        ("host-ssh", ["SSH_HOST=lab-gpu"], "ssh"),
    ],
)
def test_doctor_performs_read_only_provider_probe(
    prototype: Path,
    fake_bin: Path,
    tmp_path: Path,
    backend: str,
    arguments: list[str],
    expected_program: str,
) -> None:
    install_fake_colab(fake_bin)
    install_fake_kaggle(fake_bin)
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)

    result = run_command(
        ["make", f"BACKEND={backend}", *arguments, "doctor"],
        cwd=prototype,
        env=env,
    )

    assert f"Backend {backend} is ready" in result.stdout
    provider_calls = calls(Path(env["FAKE_LOG"]), expected_program)
    assert provider_calls
    serialized = json.dumps(provider_calls)
    assert '"new"' not in serialized
    assert '"push"' not in serialized
    assert '"stop"' not in serialized
    assert '"ssh", "--config"' not in serialized


@pytest.mark.integration
def test_colab_doctor_rejects_incompatible_kernel_client_before_access_probe(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    package_root = tmp_path / "broken-colab-package"
    (package_root / "colab_cli").mkdir(parents=True)
    (package_root / "colab_cli" / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "colab_cli" / "runtime.py").write_text(
        "import jupyter_kernel_client\n"
        "class ColabRuntime:\n"
        "    def __init__(self, *_args):\n"
        "        pass\n"
        "    @property\n"
        "    def kernel_client(self):\n"
        "        return jupyter_kernel_client.KernelClient()\n",
        encoding="utf-8",
    )
    (package_root / "jupyter_kernel_client.py").write_text("", encoding="utf-8")
    env = fake_environment(fake_bin, tmp_path)
    env["PYTHONPATH"] = str(package_root)

    result = run_command(
        ["make", "BACKEND=colab-notebook", "doctor"],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert "Incompatible Colab CLI execution environment" in result.stdout
    assert "does not provide the API required by colab" in result.stdout
    provider_calls = calls(Path(env["FAKE_LOG"]), "colab")
    assert provider_calls == [["colab", "version"]]


@pytest.mark.integration
def test_colab_ssh_doctor_checks_client_and_key_without_opening_ssh(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    identity = tmp_path / "id_ed25519"
    identity.write_text("test-only key path", encoding="utf-8")

    result = run_command(
        [
            "make",
            "BACKEND=colab-ssh",
            f"COLAB_IDENTITY={identity}",
            "doctor",
        ],
        cwd=prototype,
        env=env,
    )

    assert "Backend colab-ssh is ready" in result.stdout
    assert calls(Path(env["FAKE_LOG"]), "colab")
    assert calls(Path(env["FAKE_LOG"]), "ssh") == []
    assert not (prototype / ".cloud-state" / "colab-ssh").exists()


@pytest.mark.integration
def test_lightning_doctor_is_read_only_and_keeps_credentials_provider_owned(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_lightning(fake_bin)
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    identity = tmp_path / "lightning_rsa"
    identity.write_text("test-only provider key path", encoding="utf-8")

    result = run_command(
        [
            "make",
            "BACKEND=lightning-studio-ssh",
            "LIGHTNING_STUDIO=test-studio",
            "LIGHTNING_TEAMSPACE=tester/general",
            f"LIGHTNING_IDENTITY={identity}",
            "doctor",
        ],
        cwd=prototype,
        env=env,
    )

    assert "Backend lightning-studio-ssh is ready" in result.stdout
    provider_calls = calls(Path(env["FAKE_LOG"]), "lightning")
    assert ["lightning", "auth", "whoami", "--json"] in provider_calls
    assert [
        "lightning",
        "studio",
        "list",
        "--teamspace",
        "tester/general",
        "--json",
    ] in provider_calls
    serialized = json.dumps(provider_calls)
    assert '"start"' not in serialized
    assert '"ssh", "generate"' not in serialized
    assert not (prototype / ".cloud-state" / "lightning-studio-ssh").exists()


@pytest.mark.integration
def test_lightning_allocation_failure_stops_before_ssh_or_sync(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_lightning(fake_bin)
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env["FAKE_LIGHTNING_START_FAIL"] = "1"
    identity = tmp_path / "lightning_rsa"
    identity.write_text("test-only provider key path", encoding="utf-8")

    result = run_command(
        [
            "make",
            "BACKEND=lightning-studio-ssh",
            "LIGHTNING_STUDIO=test-studio",
            "LIGHTNING_TEAMSPACE=tester/general",
            f"LIGHTNING_IDENTITY={identity}",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode != 0
    assert "unauthorized" in result.stdout
    all_calls = calls(Path(env["FAKE_LOG"]))
    assert not any(call[:3] == ["lightning", "ssh", "generate"] for call in all_calls)
    assert calls(Path(env["FAKE_LOG"]), "rsync") == []


def test_help_is_available_when_backend_dependency_is_missing(prototype: Path) -> None:
    result = run_command(
        ["make", "BACKEND=colab-notebook", "COLAB_BIN=missing-colab-for-test", "help"],
        cwd=prototype,
    )
    assert "Targets:" in result.stdout
    assert "doctor" in result.stdout


def test_operational_target_is_gated_before_missing_provider_is_executed(
    prototype: Path,
) -> None:
    result = run_command(
        [
            "make",
            "BACKEND=colab-notebook",
            "COLAB_BIN=missing-colab-for-test",
            *engine_dispatch("build"),
        ],
        cwd=prototype,
        check=False,
    )
    assert result.returncode == 2
    assert "Missing required command: missing-colab-for-test" in result.stdout
    assert "missing-colab-for-test: command not found" not in result.stdout


@pytest.mark.integration
@pytest.mark.parametrize(
    ("backend", "arguments", "failure_variable", "forbidden_fragment"),
    [
        ("colab-notebook", [], "FAKE_COLAB_AUTH_FAIL", '"new"'),
        ("kaggle-notebook", ["KAGGLE_USERNAME=tester"], "FAKE_KAGGLE_AUTH_FAIL", '"push"'),
        ("codespaces-ssh", ["CODESPACE=test-space"], "FAKE_GH_AUTH_FAIL", '"ssh", "--config"'),
        ("host-ssh", ["SSH_HOST=lab-gpu"], "FAKE_SSH_AUTH_FAIL", '"rsync"'),
    ],
)
def test_authentication_failure_blocks_provider_operation(
    prototype: Path,
    fake_bin: Path,
    tmp_path: Path,
    backend: str,
    arguments: list[str],
    failure_variable: str,
    forbidden_fragment: str,
) -> None:
    install_fake_colab(fake_bin)
    install_fake_kaggle(fake_bin)
    install_fake_ssh_tools(fake_bin)
    env = fake_environment(fake_bin, tmp_path)
    env[failure_variable] = "1"

    result = run_command(
        ["make", f"BACKEND={backend}", *arguments, *engine_dispatch("build")],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert "authentication or access probe failed" in result.stdout
    assert forbidden_fragment not in json.dumps(calls(Path(env["FAKE_LOG"])))


def test_prerequisites_enforces_minimum_python_version(
    prototype: Path, fake_bin: Path, tmp_path: Path
) -> None:
    install_fake_colab(fake_bin)
    old_python = write_executable(
        fake_bin / "old-python",
        "#!/bin/sh\nexit 1\n",
    )
    env = fake_environment(fake_bin, tmp_path)

    result = run_command(
        [
            "make",
            "BACKEND=colab-notebook",
            f"PYTHON_BIN={old_python.name}",
            "prerequisites",
        ],
        cwd=prototype,
        env=env,
        check=False,
    )

    assert result.returncode == 2
    assert "Python 3.9 or newer is required" in result.stdout

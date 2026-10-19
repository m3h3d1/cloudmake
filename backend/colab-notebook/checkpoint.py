#!/usr/bin/env python3
"""Remote restic checkpoint operations for the native Colab backend."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any


CONTROL = Path("/content/cloud-build-checkpoint-control.json")
RESULT = Path("/content/.cloud-build/checkpoint-result.json")
MOUNT = Path("/content/drive")
WORKSPACE = Path("/content/.cloud-build/workspace")
WORKSPACE_READY = WORKSPACE / ".cloudmake-checkpoint-ready.json"
RESTORE_ROOT = Path("/content/.cloud-build/checkpoint-restore")
OLD_WORKSPACE = Path("/content/.cloud-build/workspace.old")
TRANSPORT_DIR = Path("/content/.cloud-build/checkpoint-transport")
TRANSPORT_HELPER = Path("/content/cloudmake-checkpoint-transport.py")
PROJECT_KEY = re.compile(r"^[0-9a-f]{24}$")
TAG = "cloudmake-v1"
SOURCE_DIRECTORY = "src"
SOURCE_MANIFEST = "source-manifest.json"


class CheckpointError(RuntimeError):
    pass


def profile_tag() -> str:
    system = re.sub(r"[^a-z0-9_-]", "-", platform.system().lower())
    machine = re.sub(r"[^a-z0-9_-]", "-", platform.machine().lower())
    accelerator = (
        "nvidia"
        if Path("/proc/driver/nvidia/version").is_file()
        or shutil.which("nvidia-smi") is not None
        else "cpu"
    )
    if not system or not machine:
        raise CheckpointError("cannot determine checkpoint environment profile")
    return f"cloudmake-env-{system}-{machine}-{accelerator}"


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


def load_control() -> dict[str, Any]:
    try:
        control = json.loads(CONTROL.read_text(encoding="utf-8"))
    except Exception as error:
        raise CheckpointError("checkpoint control is unavailable or corrupt") from error
    if not isinstance(control, dict) or control.get("schema") != 1:
        raise CheckpointError("unsupported checkpoint control schema")
    project_key = control.get("project_key")
    workspace_id = control.get("workspace_id")
    repository = control.get("repository")
    if not isinstance(project_key, str) or not PROJECT_KEY.fullmatch(project_key):
        raise CheckpointError("invalid checkpoint project identity")
    if not isinstance(workspace_id, str) or not PROJECT_KEY.fullmatch(workspace_id):
        raise CheckpointError("invalid persistent workspace identity")
    if not isinstance(control.get("allow_attach"), bool):
        raise CheckpointError("invalid persistent workspace attachment policy")
    adopt_from = control.get("adopt_from")
    if adopt_from is not None and (
        not isinstance(adopt_from, str) or PROJECT_KEY.fullmatch(adopt_from) is None
    ):
        raise CheckpointError("invalid previous workspace owner")
    expected = f"/content/drive/MyDrive/.cloudmake/checkpoints/{workspace_id}/restic"
    if repository != expected:
        raise CheckpointError("checkpoint repository is outside the managed Drive path")
    return control


def require_mount() -> None:
    if not os.path.ismount(MOUNT) or not (MOUNT / "MyDrive").is_dir():
        raise CheckpointError("Google Drive is not mounted")


def repository_exists(repository: Path) -> bool:
    return (repository / "config").is_file()


def install_restic() -> str:
    executable = shutil.which("restic")
    if executable is not None:
        return executable
    subprocess.run(["apt-get", "update", "-qq"], check=True)
    subprocess.run(
        ["apt-get", "install", "-y", "-qq", "restic"],
        check=True,
        stdout=subprocess.DEVNULL,
    )
    executable = shutil.which("restic")
    if executable is None:
        raise CheckpointError("restic installation completed without an executable")
    return executable


def restic_environment(repository: Path) -> dict[str, str]:
    return {
        "HOME": os.environ.get("HOME", "/root"),
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "RESTIC_REPOSITORY": os.fspath(repository),
        "RESTIC_PASSWORD_COMMAND": (
            f"{sys.executable} {TRANSPORT_HELPER} password "
            f"--directory {TRANSPORT_DIR}"
        ),
    }


def restic(
    executable: str, repository: Path, *arguments: str, capture: bool = False
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [executable, *arguments],
        check=True,
        env=restic_environment(repository),
        text=True,
        stdout=subprocess.PIPE if capture else None,
    )


def validate_owner(
    workspace: Path, project_key: str, *, allow_attach: bool = False,
    adopt_from: str | None = None,
) -> None:
    owner_path = workspace / ".cloudmake-owner.json"
    try:
        if not stat.S_ISREG(owner_path.lstat().st_mode):
            raise CheckpointError("restored checkpoint owner record is not a regular file")
        owner = json.loads(owner_path.read_text("utf-8"))
    except CheckpointError:
        raise
    except Exception as error:
        raise CheckpointError("restored checkpoint has no valid owner record") from error
    if owner.get("schema") != 1 or not isinstance(owner.get("project_id"), str):
        raise CheckpointError("restored checkpoint has no valid owner record")
    if owner.get("project_id") != project_key:
        if not allow_attach:
            raise CheckpointError("checkpoint owner does not match the selected project")
        if adopt_from and owner["project_id"] != adopt_from:
            raise CheckpointError("checkpoint owner changed since attachment")


def source_entries(root: Path) -> dict[str, Any]:
    path = root / SOURCE_MANIFEST
    if not path.exists():
        return {}
    try:
        if not stat.S_ISREG(path.lstat().st_mode):
            raise CheckpointError("checkpoint source manifest is not a regular file")
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except CheckpointError:
        raise
    except Exception as error:
        raise CheckpointError("checkpoint contains a corrupt source manifest") from error
    if manifest.get("schema") != 1 or not isinstance(manifest.get("entries"), dict):
        raise CheckpointError("checkpoint contains an unsupported source manifest")
    return manifest["entries"]


def source_owned(relative: PurePosixPath, entries: dict[str, Any]) -> bool:
    return (
        len(relative.parts) > 1
        and relative.parts[0] == SOURCE_DIRECTORY
        and PurePosixPath(*relative.parts[1:]).as_posix() in entries
    )


def validate_strict_link(path: Path, target: PurePosixPath, root: Path) -> None:
    if target.is_absolute():
        raise CheckpointError("checkpoint source contains an absolute symbolic link")
    resolved_root = root.resolve()
    resolved = (path.parent / Path(*target.parts)).resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise CheckpointError("checkpoint source contains an escaping symbolic link")


def validate_tree(root: Path) -> dict[str, int]:
    entries = source_entries(root)
    files = 0
    total_bytes = 0
    for directory, directory_names, file_names in os.walk(
        root, topdown=True, followlinks=False
    ):
        parent = Path(directory)
        for name in [*directory_names, *file_names]:
            path = parent / name
            metadata = path.lstat()
            files += 1
            if stat.S_ISLNK(metadata.st_mode):
                target = PurePosixPath(os.readlink(path))
                relative = PurePosixPath(path.relative_to(root).as_posix())
                # Local source remains subject to the upload boundary.  A link
                # created by the remote project is inert checkpoint data:
                # restic records the link itself and Cloudmake never follows it.
                if source_owned(relative, entries):
                    validate_strict_link(path, target, root / SOURCE_DIRECTORY)
                elif relative.parts[0] != SOURCE_DIRECTORY:
                    validate_strict_link(path, target, root)
                if name in directory_names:
                    directory_names.remove(name)
            elif not (
                stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)
            ):
                raise CheckpointError("checkpoint contains a special filesystem entry")
            elif stat.S_ISREG(metadata.st_mode):
                total_bytes += metadata.st_size
    return {"files": files, "bytes": total_bytes}


def snapshot_statistics(
    executable: str, repository: Path, control: dict[str, Any]
) -> dict[str, int]:
    result = restic(
        executable,
        repository,
        "stats",
        "--json",
        "--host",
        control["workspace_id"],
        "--tag",
        TAG,
        "latest",
        capture=True,
    )
    try:
        statistics = json.loads(result.stdout)
        files = statistics["total_file_count"]
        total_bytes = statistics["total_size"]
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise CheckpointError("restic returned invalid checkpoint statistics") from error
    if not isinstance(files, int) or not isinstance(total_bytes, int):
        raise CheckpointError("restic returned invalid checkpoint statistics")
    return {"files": files, "bytes": total_bytes}


def validate_restore_capacity(statistics: dict[str, int], destination: Path) -> None:
    usage = shutil.disk_usage(destination)
    if statistics["bytes"] > usage.free:
        raise CheckpointError(
            "persistent workspace requires "
            f"{statistics['bytes']} bytes but the Colab VM has only {usage.free} free"
        )
    filesystem = os.statvfs(destination)
    if filesystem.f_favail and statistics["files"] > filesystem.f_favail:
        raise CheckpointError(
            "persistent workspace requires "
            f"{statistics['files']} entries but the Colab VM has only "
            f"{filesystem.f_favail} free inodes"
        )


def runtime_metadata() -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "system": platform.system().lower(),
        "architecture": platform.machine().lower(),
        "cpu_count": os.cpu_count(),
    }
    try:
        release: dict[str, str] = {}
        for line in Path("/etc/os-release").read_text("utf-8").splitlines():
            if "=" in line:
                name, value = line.split("=", 1)
                release[name.lower()] = value.strip().strip('"')
        metadata["os"] = {
            key: release[key] for key in ("id", "version_id") if key in release
        }
    except OSError:
        pass
    try:
        for line in Path("/proc/meminfo").read_text("utf-8").splitlines():
            if line.startswith("MemTotal:"):
                metadata["memory_bytes"] = int(line.split()[1]) * 1024
                break
    except (OSError, ValueError, IndexError):
        pass
    # A newly allocated VM has no managed workspace until restore or source
    # synchronization creates it.  Probe capacity against its parent, but
    # establish that parent first so a valid first restore is not rejected.
    WORKSPACE.parent.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(WORKSPACE.parent)
    metadata["disk"] = {"total_bytes": disk.total, "free_bytes": disk.free}
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            result = subprocess.run(
                [
                    nvidia_smi,
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits",
                ],
                check=False,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                fields = [
                    part.strip()
                    for part in result.stdout.splitlines()[0].split(",", 2)
                ]
                if len(fields) == 3:
                    name, memory, driver = fields
                    metadata["gpu"] = {
                        "name": name,
                        "memory_mib": int(memory) if memory.isdigit() else memory,
                        "driver": driver,
                    }
        except (OSError, subprocess.SubprocessError):
            pass
    return metadata


def probe(control: dict[str, Any]) -> dict[str, Any]:
    require_mount()
    repository = Path(control["repository"])
    return {
        "repository_exists": repository_exists(repository),
        "runtime": runtime_metadata(),
    }


def mark_workspace_ready(control: dict[str, Any]) -> None:
    atomic_json(
        WORKSPACE_READY,
        {
            "schema": 1,
            "workspace_id": control["workspace_id"],
            "profile": profile_tag(),
        },
    )


def restore(control: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    require_mount()
    repository = Path(control["repository"])
    if not repository_exists(repository):
        raise CheckpointError("checkpoint repository disappeared before restore")
    executable = install_restic()
    restic(executable, repository, "check")
    statistics = snapshot_statistics(executable, repository, control)
    validate_restore_capacity(statistics, WORKSPACE.parent)
    shutil.rmtree(RESTORE_ROOT, ignore_errors=True)
    RESTORE_ROOT.mkdir(parents=True)
    restic(
        executable,
        repository,
        "restore",
        "latest",
        "--host",
        control["workspace_id"],
        "--tag",
        TAG,
        "--target",
        os.fspath(RESTORE_ROOT),
    )
    restored = RESTORE_ROOT / WORKSPACE.relative_to("/")
    if not restored.is_dir():
        raise CheckpointError("checkpoint did not contain the managed workspace")
    validate_owner(
        restored,
        control["project_key"],
        allow_attach=control["allow_attach"],
        adopt_from=control.get("adopt_from"),
    )
    validate_tree(restored)
    shutil.rmtree(OLD_WORKSPACE, ignore_errors=True)
    if WORKSPACE.exists():
        WORKSPACE.rename(OLD_WORKSPACE)
    try:
        restored.rename(WORKSPACE)
    except BaseException:
        if OLD_WORKSPACE.exists() and not WORKSPACE.exists():
            OLD_WORKSPACE.rename(WORKSPACE)
        raise
    mark_workspace_ready(control)
    shutil.rmtree(OLD_WORKSPACE, ignore_errors=True)
    shutil.rmtree(RESTORE_ROOT, ignore_errors=True)
    snapshots = restic(
        executable,
        repository,
        "snapshots",
        "--json",
        "--host",
        control["workspace_id"],
        "--tag",
        TAG,
        capture=True,
    )
    try:
        records = json.loads(snapshots.stdout)
    except json.JSONDecodeError as error:
        raise CheckpointError("restic returned invalid snapshot metadata") from error
    if (
        not isinstance(records, list)
        or not records
        or not isinstance(records[-1], dict)
    ):
        raise CheckpointError("restic returned invalid snapshot metadata")
    latest = records[-1]
    snapshot = latest.get("short_id") or latest.get("id")
    tags = latest.get("tags", [])
    if not isinstance(snapshot, str) or not snapshot:
        raise CheckpointError("restic returned snapshot metadata without an identity")
    if not isinstance(tags, list):
        tags = []
    snapshot_profile = next(
        (
            tag
            for tag in tags
            if isinstance(tag, str) and tag.startswith("cloudmake-env-")
        ),
        "unknown",
    )
    current_profile = profile_tag()
    if snapshot_profile != current_profile:
        print(
            "[cloudmake] persistent-workspace environment-changed "
            f"snapshot={snapshot_profile} current={current_profile}; "
            "project Make rules remain responsible for rebuilding incompatible outputs."
        )
    elapsed_seconds = round(time.monotonic() - started, 3)
    print(
        f"[cloudmake] persistent-workspace snapshot-restored={snapshot} "
        f"elapsed={elapsed_seconds:.3f}s"
    )
    return {
        "snapshot": snapshot,
        "elapsed_seconds": elapsed_seconds,
        "profile": snapshot_profile,
        "current_profile": current_profile,
        "runtime": runtime_metadata(),
        **statistics,
    }


def publish(control: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    require_mount()
    validate_owner(WORKSPACE, control["project_key"])
    mark_workspace_ready(control)
    statistics = validate_tree(WORKSPACE)
    executable = install_restic()
    repository = Path(control["repository"])
    if not repository_exists(repository):
        if repository.exists() and any(repository.iterdir()):
            raise CheckpointError("checkpoint repository path is nonempty but uninitialized")
        repository.mkdir(parents=True, exist_ok=True)
        restic(executable, repository, "init")
    result = restic(
        executable,
        repository,
        "backup",
        "--json",
        "--host",
        control["workspace_id"],
        "--tag",
        TAG,
        "--tag",
        profile_tag(),
        os.fspath(WORKSPACE),
        capture=True,
    )
    summaries = [
        json.loads(line) for line in result.stdout.splitlines() if line.strip()
    ]
    summary = next(
        record for record in summaries if record.get("message_type") == "summary"
    )
    restic(executable, repository, "check")
    snapshot = summary.get("snapshot_id")
    elapsed_seconds = round(time.monotonic() - started, 3)
    print(
        f"[cloudmake] persistent-workspace snapshot-published={snapshot} "
        f"data-added={summary.get('data_added', 'unknown')} "
        f"elapsed={elapsed_seconds:.3f}s"
    )
    return {
        "snapshot": snapshot,
        "data_added": summary.get("data_added"),
        "elapsed_seconds": elapsed_seconds,
        "profile": profile_tag(),
        "runtime": runtime_metadata(),
        **statistics,
    }


def purge(control: dict[str, Any]) -> dict[str, Any]:
    require_mount()
    repository = Path(control["repository"])
    workspace_store = repository.parent
    cloudmake_root = MOUNT / "MyDrive" / ".cloudmake"
    checkpoints_root = cloudmake_root / "checkpoints"
    expected = checkpoints_root / control["workspace_id"]
    if workspace_store != expected or any(
        path.is_symlink() for path in (cloudmake_root, checkpoints_root, workspace_store)
    ):
        raise CheckpointError("refusing unsafe persistent workspace purge path")
    if workspace_store.exists():
        shutil.rmtree(workspace_store)
        outcome = "purged"
    else:
        outcome = "absent"
    print(f"[cloudmake] persistent-workspace={control['workspace_id']} {outcome}")
    return {"outcome": outcome, "workspace_id": control["workspace_id"]}


def main() -> None:
    operation = os.environ.get("CLOUDMAKE_CHECKPOINT_OPERATION")
    if operation not in {"probe", "restore", "publish", "purge"}:
        raise CheckpointError("invalid checkpoint operation")
    RESULT.unlink(missing_ok=True)
    try:
        control = load_control()
        details = {
            "probe": probe,
            "restore": restore,
            "publish": publish,
            "purge": purge,
        }[operation](control)
        atomic_json(
            RESULT,
            {"schema": 1, "operation": operation, "status": "succeeded", **details},
        )
    except BaseException as error:
        atomic_json(
            RESULT,
            {
                "schema": 1,
                "operation": operation,
                "status": "failed",
                "error": str(error) or type(error).__name__,
            },
        )
        raise


if __name__ == "__main__":
    main()

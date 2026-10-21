#!/usr/bin/env python3
"""Run one Cloudmake target inside a fresh Kaggle notebook VM."""

from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from typing import Any
import urllib.error
import urllib.request


class InfrastructureError(RuntimeError):
    """A failure before or around project Make execution."""


PROOT_VERSION = "5.4.1"
PROOT_SHA256 = "19f44283f5c0e73091c60195f5fcd4f4c1165505e44410d434e2ab1b677c1a09"
PROOT_URL = f"https://github.com/proot-me/proot/releases/download/v{PROOT_VERSION}/proot"


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def safe_relative(value: str, description: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "\0" in value:
        raise InfrastructureError(f"unsafe {description}: {value!r}")
    return path


def remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def safe_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    names: set[str] = set()
    links: set[PurePosixPath] = set()
    for member in members:
        relative = safe_relative(member.name, "archive member")
        normalized = relative.as_posix()
        if normalized in names:
            raise InfrastructureError(f"duplicate archive member: {normalized}")
        names.add(normalized)
        if member.ischr() or member.isblk() or member.isfifo() or member.isdev():
            raise InfrastructureError(f"unsafe archive member type: {normalized}")
        if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
            raise InfrastructureError(f"unsupported archive member type: {normalized}")
        if any(parent in links for parent in relative.parents):
            raise InfrastructureError(f"archive member traverses a link: {normalized}")
        if member.issym():
            if "\0" in member.linkname:
                raise InfrastructureError(f"unsafe archive link: {normalized}")
            links.add(relative)
        elif member.islnk():
            safe_relative(member.linkname, "archive hard-link target")
    return members


def extract_archive(archive_path: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive_path, "r:gz") as archive:
        members = safe_members(archive)
        directory_modes: list[tuple[Path, int]] = []
        hard_links: list[tuple[Path, PurePosixPath]] = []
        for member in members:
            relative = safe_relative(member.name, "archive member")
            target = destination.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            for parent in [target.parent, *target.parent.parents]:
                if parent == destination.parent:
                    break
                if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                    raise InfrastructureError(
                        f"archive parent is not a directory: {relative.as_posix()}"
                    )
                if parent == destination:
                    break
            if member.isdir():
                if target.is_symlink() or (target.exists() and not target.is_dir()):
                    remove_path(target)
                target.mkdir(parents=True, exist_ok=True)
                # Apply restrictive directory modes after children are restored.
                directory_modes.append((target, stat.S_IMODE(member.mode)))
            elif member.isfile():
                remove_path(target)
                source = archive.extractfile(member)
                if source is None:
                    raise InfrastructureError(f"archive file is unreadable: {member.name}")
                descriptor = os.open(
                    target,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                    stat.S_IMODE(member.mode),
                )
                with source, os.fdopen(descriptor, "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            elif member.issym():
                remove_path(target)
                os.symlink(member.linkname, target)
            else:
                remove_path(target)
                hard_links.append(
                    (target, safe_relative(member.linkname, "archive hard-link target"))
                )
        for target, relative_source in hard_links:
            source = destination.joinpath(*relative_source.parts)
            if not source.is_file() or source.is_symlink():
                raise InfrastructureError(
                    f"archive hard-link target is not a restored regular file: "
                    f"{relative_source.as_posix()}"
                )
            os.link(source, target, follow_symlinks=False)
        for directory, mode in sorted(
            directory_modes, key=lambda item: len(item[0].parts), reverse=True
        ):
            directory.chmod(mode)


def archive_workspace(source: Path, destination: Path) -> tuple[int, int, str]:
    files = 0
    logical_bytes = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        with tarfile.open(temporary, "w:gz", dereference=False) as archive:
            for directory, directory_names, file_names in os.walk(
                source, topdown=True, followlinks=False
            ):
                root = Path(directory)
                directory_names.sort()
                file_names.sort()
                for name in [*directory_names, *file_names]:
                    path = root / name
                    metadata = path.lstat()
                    if not (
                        stat.S_ISDIR(metadata.st_mode)
                        or stat.S_ISREG(metadata.st_mode)
                        or stat.S_ISLNK(metadata.st_mode)
                    ):
                        raise InfrastructureError(
                            f"workspace contains unsupported special file: {path.relative_to(source)}"
                        )
                    if stat.S_ISREG(metadata.st_mode):
                        files += 1
                        logical_bytes += metadata.st_size
                    archive.add(
                        path,
                        arcname=path.relative_to(source).as_posix(),
                        recursive=False,
                    )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return files, logical_bytes, file_sha256(destination)


def prune_oci_materializations(cache: Path) -> int:
    """Discard derived root filesystems while retaining OCI content layers.

    A materialized bundle is VM-local scratch: it is reproducibly reconstructed
    from the digest-pinned OCI layout.  Keeping it in a checkpoint both doubles
    storage and can preserve filesystem details poorly across archive restore.
    """
    images = cache / "images"
    if not images.is_dir():
        return 0
    removed = 0
    for image_cache in sorted(images.iterdir()):
        if (
            not image_cache.is_dir()
            or image_cache.is_symlink()
            or re.fullmatch(r"[0-9a-f]{64}", image_cache.name) is None
        ):
            continue
        bundle = image_cache / "bundle"
        if bundle.is_symlink():
            raise InfrastructureError(
                f"OCI materialization path is an unsafe symlink: {bundle}"
            )
        if bundle.is_file():
            raise InfrastructureError(
                f"OCI materialization path is not a directory: {bundle}"
            )
        if bundle.is_dir():
            shutil.rmtree(bundle)
            removed += 1
    return removed


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("schema") != 1:
        raise InfrastructureError("invalid source manifest")
    entries = value.get("entries")
    if not isinstance(entries, dict):
        raise InfrastructureError("invalid source manifest entries")
    for name, entry in entries.items():
        safe_relative(name, "source manifest path")
        if not isinstance(entry, dict) or entry.get("kind") not in {
            "file", "directory", "symlink"
        }:
            raise InfrastructureError(f"invalid source manifest entry: {name}")
    return value


def reconcile_source(
    source: Path, previous: dict[str, Any], current: dict[str, Any]
) -> None:
    previous_entries = previous.get("entries", {})
    current_entries = current["entries"]
    deleted = set(previous_entries) - set(current_entries)
    for name in sorted(deleted, key=lambda value: (value.count("/"), value), reverse=True):
        path = source.joinpath(*PurePosixPath(name).parts)
        if previous_entries[name].get("kind") == "directory":
            if path.is_dir() and not path.is_symlink():
                try:
                    path.rmdir()
                except OSError:
                    pass
            elif path.exists() or path.is_symlink():
                raise InfrastructureError(f"source-owned path changed type: {name}")
        elif path.exists() or path.is_symlink():
            remove_path(path)
    for name, entry in sorted(current_entries.items()):
        path = source.joinpath(*PurePosixPath(name).parts)
        if entry["kind"] == "directory":
            if path.is_symlink() or (path.exists() and not path.is_dir()):
                remove_path(path)
            path.mkdir(parents=True, exist_ok=True)
        elif path.exists() or path.is_symlink():
            remove_path(path)


def previous_checkpoint(input_root: Path, reference: str, workspace_id: str) -> Path:
    if "/" not in reference:
        raise InfrastructureError("invalid prior Kaggle kernel reference")
    input_resolved = input_root.resolve()
    matches: list[Path] = []
    invalid: list[Path] = []
    for receipt in input_root.rglob("cloudmake-checkpoint-result.json"):
        try:
            receipt.resolve().relative_to(input_resolved)
            metadata = json.loads(receipt.read_text(encoding="utf-8"))
        except Exception:
            invalid.append(receipt)
            continue
        if (
            isinstance(metadata, dict)
            and metadata.get("schema") == 1
            and metadata.get("status") == "succeeded"
            and metadata.get("workspace_id") == workspace_id
            and metadata.get("snapshot") == reference
        ):
            matches.append(receipt.parent)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise InfrastructureError("prior Kaggle checkpoint attachment is ambiguous")
    if invalid:
        raise InfrastructureError("prior Kaggle checkpoint attachment is invalid")
    raise InfrastructureError("prior Kaggle checkpoint attachment is unavailable")


def restore_checkpoint(
    *, control: dict[str, Any], workspace: Path, input_root: Path
) -> dict[str, Any]:
    reference = control.get("previous_kernel_ref")
    if not reference:
        workspace.mkdir(parents=True, exist_ok=True)
        return {"operation": "restore", "status": "succeeded", "outcome": "empty"}
    source = previous_checkpoint(input_root, reference, str(control.get("workspace_id", "")))
    metadata_path = source / "cloudmake-checkpoint-result.json"
    archive_path = source / "cloudmake-checkpoint.tar.gz"
    manifest_path = source / "cloudmake-source-manifest.json"
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    except Exception as error:
        raise InfrastructureError(f"prior Kaggle checkpoint metadata is unavailable: {error}") from error
    if (
        metadata.get("schema") != 1
        or metadata.get("status") != "succeeded"
        or metadata.get("workspace_id") != control.get("workspace_id")
        or metadata.get("snapshot") != reference
    ):
        raise InfrastructureError("prior Kaggle checkpoint identity does not match")
    digest = file_sha256(archive_path)
    if digest != metadata.get("sha256"):
        raise InfrastructureError("prior Kaggle checkpoint digest does not match")
    staging = workspace.with_name(f".{workspace.name}.restore")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        extract_archive(archive_path, staging)
        shutil.rmtree(workspace, ignore_errors=True)
        os.replace(staging, workspace)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "operation": "restore",
        "status": "succeeded",
        "outcome": "restored",
        "snapshot": reference,
        "source_manifest": manifest,
    }


def prepare_current_proot(cache: Path) -> Path:
    destination = cache / "runtime" / "proot" / PROOT_VERSION / "proot"
    if destination.is_file() and file_sha256(destination) == PROOT_SHA256:
        destination.chmod(0o755)
        return destination
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.unlink(missing_ok=True)
    require_network("github.com", "Kaggle PRoot release access")
    temporary = destination.with_name(f".{destination.name}.tmp")
    try:
        with urllib.request.urlopen(PROOT_URL, timeout=60) as response:
            with temporary.open("wb") as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
        observed = file_sha256(temporary)
        if observed != PROOT_SHA256:
            raise InfrastructureError(
                "downloaded PRoot checksum mismatch: "
                f"expected {PROOT_SHA256}, observed {observed}"
            )
        temporary.chmod(0o755)
        os.replace(temporary, destination)
    except (OSError, urllib.error.URLError) as error:
        raise InfrastructureError(
            f"Kaggle PRoot {PROOT_VERSION} download failed: {error}"
        ) from error
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def prepare_oci_runtime(cache: Path) -> Path:
    runtime_packages = {
        "skopeo": "skopeo",
        "umoci": "umoci",
        "setpriv": "util-linux",
    }

    def missing_packages() -> list[str]:
        return sorted({
            package for command, package in runtime_packages.items()
            if shutil.which(command) is None
        })

    packages = missing_packages()
    if not packages:
        return prepare_current_proot(cache)
    print("[cloudmake] preparing Kaggle OCI runtime: " + ", ".join(packages), flush=True)
    package_cache = cache / "apt"
    cached_packages = sorted(package_cache.glob("*.deb"))
    if cached_packages:
        subprocess.run(
            [
                "apt-get", "install", "-y", "-q", "--no-download",
                "--no-install-recommends", *map(os.fspath, cached_packages),
            ],
            check=False,
        )
        packages = missing_packages()
        if not packages:
            return prepare_current_proot(cache)

    require_network("archive.ubuntu.com", "Kaggle OCI package access")
    (package_cache / "partial").mkdir(parents=True, exist_ok=True)
    last_status = 1
    for attempt in range(1, 3):
        try:
            subprocess.run(
                ["apt-get", "update", "-q", "-o", "Acquire::Retries=3"],
                check=True,
            )
            subprocess.run(
                [
                    "apt-get", "install", "-y", "-q", "--no-install-recommends",
                    "-o", "Acquire::Retries=3",
                    "-o", "Binary::apt-get::APT::Keep-Downloaded-Packages=true",
                    "-o", f"Dir::Cache::archives={package_cache}", *packages,
                ],
                check=True,
            )
        except subprocess.CalledProcessError as error:
            last_status = error.returncode
        packages = missing_packages()
        if not packages:
            return prepare_current_proot(cache)
        if attempt == 1:
            print(
                "[cloudmake] Kaggle OCI package preparation incomplete; "
                "refreshing package indexes once",
                flush=True,
            )
    raise InfrastructureError(
        "Kaggle OCI runtime preparation failed with exit status "
        f"{last_status}; missing: {', '.join(packages)}"
    )


def run_target(
    *, control: dict[str, Any], source: Path, cache: Path, home: Path,
    oci_runner: Path, result: Path
) -> int:
    target = control["target"]
    base = [
        "make", "-C", os.fspath(source), "-f", control["makefile"],
        *control["project_arguments"], f"-j{control['jobs']}", "--", target,
    ]
    if control["runner"] == "oci":
        proot = prepare_oci_runtime(cache)
        digest = control["image"].rsplit("@sha256:", 1)[-1]
        image_cache = cache / "images" / digest
        if (
            not (image_cache / "receipt.json").is_file()
            or not (image_cache / "layout/oci-layout").is_file()
            or not (image_cache / "layout/index.json").is_file()
        ):
            registry = control["image"].split("/", 1)[0]
            if "." not in registry and ":" not in registry and registry != "localhost":
                registry = "registry-1.docker.io"
            require_network(registry, "Kaggle OCI registry access")
        cdi_directory = cache / "cdi"
        if control["devices"]:
            generate_nvidia_cdi(cdi_directory, control["devices"])
        command = [
            os.fspath(Path(os.sys.executable)), os.fspath(oci_runner),
            "--mode", "run", "--image", control["image"], "--runtime", "auto",
            "--source", os.fspath(source), "--cache", os.fspath(cache),
            "--makefile", control["makefile"], "--target-b64",
            __import__("base64").urlsafe_b64encode(target.encode()).decode(),
            "--arguments-b64", __import__("base64").urlsafe_b64encode(
                json.dumps(control["project_arguments"], separators=(",", ":")).encode()
            ).decode(),
            "--jobs", str(control["jobs"]), "--rootless-workspace-owner", "65534:65534",
            "--runtime-home", os.fspath(home), "--result", os.fspath(result),
        ]
        if control["devices"]:
            command.extend(["--cdi-spec-dir", os.fspath(cdi_directory)])
        for runtime in control["runtime_candidates"]:
            command.extend(["--runtime-candidate", runtime])
        for device in control["devices"]:
            command.extend(["--device", device])
    else:
        command = base
    print("+", " ".join(command), flush=True)
    environment = os.environ.copy()
    if control["runner"] == "oci":
        environment["PATH"] = os.fspath(proot.parent) + os.pathsep + environment.get(
            "PATH", ""
        )
    return subprocess.run(command, check=False, env=environment).returncode


def require_network(host: str, description: str) -> None:
    import socket
    try:
        socket.getaddrinfo(host, 443)
    except OSError as error:
        raise InfrastructureError(f"{description} is unavailable for {host}: {error}") from error


def generate_nvidia_cdi(directory: Path, requested: list[str]) -> None:
    unsupported = [value for value in requested if value != "nvidia.com/gpu=all"]
    if unsupported:
        raise InfrastructureError(
            "the qualified Kaggle profile currently supports only nvidia.com/gpu=all"
        )
    candidates = set(glob.glob("/dev/nvidia*")) | set(
        glob.glob("/dev/nvidia-caps/*")
    )
    device_paths = sorted(
        value for value in candidates
        if Path(value).is_char_device() or Path(value).is_block_device()
    )
    required = {"/dev/nvidia0", "/dev/nvidiactl", "/dev/nvidia-uvm"}
    if not required.issubset(device_paths):
        raise InfrastructureError(
            "Kaggle did not attach the NVIDIA device nodes requested by CDI"
        )
    libraries: set[str] = set()
    completed = subprocess.run(
        ["ldconfig", "-p"], check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    for line in completed.stdout.splitlines():
        if "=>" not in line:
            continue
        name, path = line.split("=>", 1)
        library = name.strip().split()[0]
        candidate = path.strip()
        if library.startswith(("libcuda.so", "libnvidia-", "libnvoptix.so")) and Path(candidate).is_file():
            libraries.add(candidate)
    if not any(Path(value).name.startswith("libcuda.so") for value in libraries):
        raise InfrastructureError("Kaggle NVIDIA driver libraries are unavailable")
    mounts = [
        {"hostPath": value, "containerPath": value, "options": ["bind", "ro"]}
        for value in sorted(libraries)
    ]
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        mounts.append({
            "hostPath": nvidia_smi,
            "containerPath": "/usr/bin/nvidia-smi",
            "options": ["bind", "ro"],
        })
    payload = {
        "cdiVersion": "0.6.0",
        "kind": "nvidia.com/gpu",
        "devices": [{
            "name": "all",
            "containerEdits": {
                "deviceNodes": [
                    {"path": value, "hostPath": value} for value in device_paths
                ],
                "mounts": mounts,
            },
        }],
    }
    directory.mkdir(parents=True, exist_ok=True)
    atomic_json(directory / "cloudmake-kaggle-nvidia.json", payload)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--control", type=Path, required=True)
    result.add_argument("--source-archive", type=Path, required=True)
    result.add_argument("--oci-runner", type=Path, required=True)
    result.add_argument("--root", type=Path, default=Path("/tmp/cloud-build"))
    result.add_argument("--working", type=Path, default=Path("/kaggle/working"))
    result.add_argument("--input-root", type=Path, default=Path("/kaggle/input"))
    return result


def main() -> int:
    arguments = parser().parse_args()
    control = json.loads(arguments.control.read_text(encoding="utf-8"))
    if control.get("schema") != 1:
        raise InfrastructureError("invalid Kaggle run control")
    current_manifest = validate_manifest(control.get("source_manifest"))
    workspace = arguments.root / "workspace"
    source = workspace / "src"
    home = workspace / "home"
    cache = workspace / "cache" / "oci"
    run_log = arguments.working / "cloud-build.log"
    target_result = arguments.working / "cloudmake-target-result.json"
    checkpoint_result = arguments.working / "cloudmake-checkpoint-result.json"
    oci_result = arguments.working / "cloudmake-oci-result.json"
    phase = "checkpoint_restore"
    target_submission = "not_submitted"
    try:
        shutil.rmtree(arguments.root, ignore_errors=True)
        restore = (
            restore_checkpoint(control=control, workspace=workspace, input_root=arguments.input_root)
            if control["checkpoint"] else {"outcome": "disabled", "source_manifest": {}}
        )
        phase = "source_reconciliation"
        source.mkdir(parents=True, exist_ok=True)
        previous_manifest = restore.pop("source_manifest", {"schema": 1, "entries": {}})
        reconcile_source(source, previous_manifest, current_manifest)
        extract_archive(arguments.source_archive, source)
        home.mkdir(parents=True, exist_ok=True)
        print(f"Workspace: {source}", flush=True)
        print(f"Requested target: {control['target']}", flush=True)
        if control.get("network_required"):
            phase = "network_preflight"
            require_network("pypi.org", "Kaggle requested internet access")
        phase = "runner_preflight" if control["runner"] == "oci" else "target_execution"
        with run_log.open("w", encoding="utf-8") as log:
            remote_environment = os.environ.copy()
            remote_environment.update(
                HOME=os.fspath(home),
                XDG_CACHE_HOME=os.fspath(home / ".cache"),
                XDG_CONFIG_HOME=os.fspath(home / ".config"),
                XDG_STATE_HOME=os.fspath(home / ".local/state"),
            )
            process = subprocess.Popen(
                [
                    os.fspath(Path(os.sys.executable)), os.fspath(Path(__file__).resolve()),
                    "--internal-run", os.fspath(arguments.control), os.fspath(source),
                    os.fspath(cache), os.fspath(home), os.fspath(arguments.oci_runner),
                    os.fspath(oci_result),
                ],
                text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                env=remote_environment,
            )
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
            completed = subprocess.CompletedProcess(process.args, process.wait())
        target_submission = "submitted"
        target_status = "succeeded" if completed.returncode == 0 else "target-failed"
        # An OCI infrastructure receipt overrides the process-code classification.
        if control["runner"] == "oci":
            try:
                oci_receipt = json.loads(oci_result.read_text(encoding="utf-8"))
            except Exception as error:
                raise InfrastructureError(f"OCI execution receipt is unavailable: {error}") from error
            if oci_receipt.get("status") == "infrastructure-failed":
                target_submission = "not_submitted"
                raise InfrastructureError(str(oci_receipt.get("error", "OCI preparation failed")))
        atomic_json(target_result, {
            "schema": 1, "status": target_status, "exit_code": completed.returncode,
            "target": control["target"], "runner": control["runner"],
            "phase": "target_execution", "target_submission": "submitted",
            "retry_safe": False,
        })
        if completed.returncode == 0 and control.get("collect_dir"):
            phase = "artifact_preparation"
            relative = safe_relative(control["collect_dir"], "collection directory")
            collect = source.joinpath(*relative.parts)
            if not collect.is_dir():
                raise InfrastructureError(
                    f"collection directory does not exist: {control['collect_dir']}"
                )
            shutil.make_archive(
                os.fspath(arguments.working / "artifacts"), "gztar", root_dir=collect
            )
        if control["checkpoint"] and completed.returncode == 0:
            phase = "checkpoint_publication"
            if control["runner"] == "oci":
                removed = prune_oci_materializations(cache)
                if removed:
                    print(
                        "[cloudmake] OCI cache=content-addressed "
                        f"materializations-discarded={removed}",
                        flush=True,
                    )
            archive = arguments.working / "cloudmake-checkpoint.tar.gz"
            files, logical_bytes, digest = archive_workspace(workspace, archive)
            snapshot = os.environ.get("KAGGLE_KERNEL_REF", "")
            # The host validates snapshot against its dispatch receipt. Kaggle does
            # not expose the kernel slug inside every image, so control carries it.
            snapshot = control.get("active_kernel_ref", snapshot)
            atomic_json(arguments.working / "cloudmake-source-manifest.json", current_manifest)
            atomic_json(checkpoint_result, {
                "schema": 1, "operation": "publish", "status": "succeeded",
                "outcome": "published", "workspace_id": control["workspace_id"],
                "snapshot": snapshot, "files": files, "bytes": logical_bytes,
                "sha256": digest, "restore": restore,
            })
        elif control["checkpoint"]:
            atomic_json(checkpoint_result, {
                "schema": 1, "operation": "publish", "status": "skipped",
                "outcome": "target-failed", "workspace_id": control["workspace_id"],
                "snapshot": control.get("active_kernel_ref", ""),
            })
        if completed.returncode:
            print(
                f"[cloudmake] target {control['target']!r} failed with exit status "
                f"{completed.returncode}", flush=True,
            )
        return 0
    except Exception as error:
        atomic_json(target_result, {
            "schema": 1, "status": "infrastructure-failed", "error": str(error),
            "phase": phase, "target_submission": target_submission,
            "retry_safe": target_submission == "not_submitted",
        })
        raise


def internal_run(arguments: list[str]) -> int:
    control = json.loads(Path(arguments[0]).read_text(encoding="utf-8"))
    result = Path(arguments[5])
    try:
        return run_target(
            control=control, source=Path(arguments[1]), cache=Path(arguments[2]),
            home=Path(arguments[3]), oci_runner=Path(arguments[4]), result=result
        )
    except InfrastructureError as error:
        atomic_json(
            result,
            {
                "schema": 1,
                "mode": "run",
                "status": "infrastructure-failed",
                "runner": "oci",
                "error": str(error),
            },
        )
        raise


if __name__ == "__main__":
    if os.sys.argv[1:2] == ["--internal-run"]:
        try:
            exit_code = internal_run(os.sys.argv[2:])
        except InfrastructureError as error:
            print(f"[cloudmake] OCI infrastructure failure: {error}", file=os.sys.stderr)
            exit_code = 70
        raise SystemExit(exit_code)
    try:
        exit_code = main()
    except InfrastructureError as error:
        print(f"[cloudmake] Kaggle infrastructure failure: {error}", file=os.sys.stderr)
        exit_code = 70
    raise SystemExit(exit_code)

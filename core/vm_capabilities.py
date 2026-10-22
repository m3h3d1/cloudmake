#!/usr/bin/env python3
"""Observe an execution environment without inferring application compatibility.

The probe is intentionally read-only except for short-lived files beneath the
selected workspace.  It records facts that a future runner may compare with an
explicit execution contract; it does not declare Nix, OCI, SIF, or another
application representation supported merely because a command is installed.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
from typing import Any


DEFAULT_RESULT = Path("/content/.cloud-build/vm-capabilities.json")
DEFAULT_WORKSPACE = Path("/content/.cloud-build/workspace")
CAPABILITY_BITS = {
    "net_admin": 12,
    "sys_chroot": 18,
    "sys_admin": 21,
    "mknod": 27,
}


def run_probe(arguments: list[str], *, timeout: float = 5) -> dict[str, Any]:
    try:
        result = subprocess.run(
            arguments,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except FileNotFoundError:
        return {"status": "dependency-missing"}
    except subprocess.TimeoutExpired:
        return {"status": "unusable", "detail": "probe timed out"}
    detail = result.stdout.strip().splitlines()
    record: dict[str, Any] = {
        "status": "available" if result.returncode == 0 else "unusable",
        "exit_code": result.returncode,
    }
    if detail:
        record["detail"] = detail[0][:240]
    return record


def read_first(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8").strip().splitlines()[0]
    except (OSError, IndexError):
        return None


def os_release() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if "=" in line:
                name, value = line.split("=", 1)
                if name in {"ID", "VERSION_ID", "PRETTY_NAME"}:
                    values[name.lower()] = value.strip().strip('"')
    except OSError:
        pass
    return values


def effective_capabilities() -> dict[str, bool]:
    value = None
    try:
        for line in Path("/proc/self/status").read_text(encoding="utf-8").splitlines():
            if line.startswith("CapEff:"):
                value = int(line.split()[1], 16)
                break
    except (OSError, ValueError, IndexError):
        pass
    if value is None:
        return {}
    return {name: bool(value & (1 << bit)) for name, bit in CAPABILITY_BITS.items()}


def mount_record(path: Path) -> dict[str, Any]:
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    best: tuple[int, dict[str, Any]] | None = None
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {"status": "unknown"}
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
            mount_point = Path(fields[4].replace("\\040", " "))
            resolved.relative_to(mount_point)
        except (ValueError, IndexError):
            continue
        record = {
            "status": "observed",
            "mount_point": os.fspath(mount_point),
            "filesystem": fields[separator + 1],
            "read_only": "ro" in fields[5].split(","),
            "mount_options": fields[5].split(","),
        }
        candidate = (len(mount_point.parts), record)
        if best is None or candidate[0] > best[0]:
            best = candidate
    return best[1] if best else {"status": "unknown"}


def filesystem_features(workspace: Path) -> dict[str, Any]:
    workspace.mkdir(parents=True, exist_ok=True)
    features: dict[str, Any] = {"path": os.fspath(workspace), **mount_record(workspace)}
    with tempfile.TemporaryDirectory(prefix=".cloudmake-capability-", dir=workspace) as name:
        root = Path(name)
        source = root / "source"
        source.write_text("capability\n", encoding="utf-8")
        executable = root / "executable"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o700)
        features["executable_files"] = run_probe([os.fspath(executable)])["status"] == "available"
        try:
            (root / "relative-link").symlink_to("source")
            features["symbolic_links"] = (root / "relative-link").read_text() == "capability\n"
        except OSError:
            features["symbolic_links"] = False
        try:
            os.link(source, root / "hard-link")
            features["hard_links"] = (root / "hard-link").stat().st_ino == source.stat().st_ino
        except OSError:
            features["hard_links"] = False
        try:
            os.setxattr(source, b"user.cloudmake_probe", b"1")
            features["extended_attributes"] = os.getxattr(
                source, b"user.cloudmake_probe"
            ) == b"1"
        except (AttributeError, OSError):
            features["extended_attributes"] = False
    return features


def namespace_features(workspace: Path) -> dict[str, Any]:
    unshare = shutil.which("unshare")
    if unshare is None:
        return {
            "user_namespace": {"status": "dependency-missing"},
            "bind_mount_namespace": {"status": "dependency-missing"},
        }
    user = run_probe([unshare, "--user", "--map-root-user", "true"])
    with tempfile.TemporaryDirectory(prefix=".cloudmake-mount-", dir=workspace) as name:
        root = Path(name)
        source = root / "source"
        target = root / "target"
        source.mkdir()
        target.mkdir()
        if os.geteuid() == 0:
            command = [unshare, "--mount", "mount", "--bind", os.fspath(source), os.fspath(target)]
        else:
            command = [
                unshare,
                "--user",
                "--map-root-user",
                "--mount",
                "mount",
                "--bind",
                os.fspath(source),
                os.fspath(target),
            ]
        bind_mount = run_probe(command)
    return {"user_namespace": user, "bind_mount_namespace": bind_mount}


def cgroup_record() -> dict[str, Any]:
    cgroup = Path("/sys/fs/cgroup")
    if not cgroup.exists():
        return {"version": "none", "writable": False}
    version = "v2" if (cgroup / "cgroup.controllers").exists() else "v1"
    mount = mount_record(cgroup)
    return {
        "version": version,
        "writable": bool(os.access(cgroup, os.W_OK) and not mount.get("read_only", False)),
        "mount": mount,
    }


def device_record(path: str) -> dict[str, Any]:
    device = Path(path)
    return {
        "present": device.exists(),
        "readable": os.access(device, os.R_OK),
        "writable": os.access(device, os.W_OK),
    }


def accelerator_record() -> dict[str, Any]:
    tpu = {"status": "available"} if Path("/dev/accel0").exists() else {"status": "unavailable"}
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return {"nvidia": {"status": "unavailable"}, "tpu": tpu}
    record = run_probe(
        [
            nvidia_smi,
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        timeout=10,
    )
    if record["status"] == "available" and record.get("detail"):
        fields = [part.strip() for part in record["detail"].split(",", 2)]
        if len(fields) == 3:
            record.update(name=fields[0], memory_mib=fields[1], driver=fields[2])
    return {"nvidia": record, "tpu": tpu}


def oci_runtime_record() -> dict[str, Any]:
    commands = {
        "podman": ["podman", "--version"],
        "docker": ["docker", "--version"],
        "nerdctl": ["nerdctl", "--version"],
        "skopeo": ["skopeo", "--version"],
        "umoci": ["umoci", "--version"],
        "proot": ["proot", "--version"],
        "crun": ["crun", "--version"],
    }
    result: dict[str, Any] = {}
    for name, command in commands.items():
        executable = shutil.which(name)
        if executable is None:
            result[name] = {"status": "unavailable"}
            continue
        probe = run_probe(command)
        result[name] = {
            "status": "installed" if probe.get("status") == "available" else "unusable",
            "path": executable,
        }
        detail = probe.get("detail")
        if isinstance(detail, str) and detail:
            result[name]["version"] = detail
    return result


def cdi_record(
    directories: tuple[Path, ...] = (Path("/etc/cdi"), Path("/var/run/cdi"))
) -> dict[str, Any]:
    devices: set[str] = set()
    invalid = 0
    files = 0
    for directory in directories:
        if not directory.is_dir():
            continue
        for path in sorted(
            (
                *directory.glob("*.json"),
                *directory.glob("*.yaml"),
                *directory.glob("*.yml"),
            )
        ):
            files += 1
            if path.suffix != ".json":
                # YAML is valid CDI, but parsing it would add a non-standard
                # dependency to this universal probe. Record its presence
                # without claiming to have validated its devices.
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                kind = payload["kind"]
                entries = payload["devices"]
                if not isinstance(kind, str) or not isinstance(entries, list):
                    raise ValueError
                for entry in entries:
                    name = entry.get("name") if isinstance(entry, dict) else None
                    if isinstance(name, str):
                        devices.add(f"{kind}={name}")
            except Exception:
                invalid += 1
    return {
        "spec_directories": [os.fspath(path) for path in directories],
        "files": files,
        "json_invalid": invalid,
        "devices": sorted(devices),
        "evidence": "observed",
    }


def memory_bytes() -> int | None:
    line = read_first(Path("/proc/meminfo"))
    if line and line.startswith("MemTotal:"):
        try:
            return int(line.split()[1]) * 1024
        except (ValueError, IndexError):
            pass
    return None


def observe(workspace: Path) -> dict[str, Any]:
    filesystem = filesystem_features(workspace)
    namespaces = namespace_features(workspace)
    disk = shutil.disk_usage(workspace)
    return {
        "schema": 1,
        "kind": "cloudmake-execution-environment",
        "evidence": "observed",
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "platform": {
            "system": platform.system().lower(),
            "architecture": platform.machine().lower(),
            "kernel": platform.release(),
            "libc": list(platform.libc_ver()),
            "os": os_release(),
        },
        "identity": {
            "uid": os.getuid(),
            "effective_uid": os.geteuid(),
            "capabilities": effective_capabilities(),
        },
        "resources": {
            "cpu_count": os.cpu_count(),
            "memory_bytes": memory_bytes(),
            "disk_total_bytes": disk.total,
            "disk_free_bytes": disk.free,
        },
        "filesystem": filesystem,
        "isolation": {**namespaces, "cgroup": cgroup_record()},
        "devices": {
            "fuse": device_record("/dev/fuse"),
            "kvm": device_record("/dev/kvm"),
        },
        "accelerators": accelerator_record(),
        "oci": {
            "clients": oci_runtime_record(),
            "cdi": cdi_record(),
        },
    }


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
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


def render(profile: dict[str, Any]) -> None:
    platform_record = profile.get("platform", {})
    resources = profile.get("resources", {})
    identity = profile.get("identity", {})
    accelerator = profile.get("accelerators", {}).get("nvidia", {})
    fields = [
        f"os={platform_record.get('system', 'unknown')}",
        f"arch={platform_record.get('architecture', 'unknown')}",
        f"euid={identity.get('effective_uid', 'unknown')}",
        f"cpus={resources.get('cpu_count', 'unknown')}",
    ]
    if accelerator.get("status") == "available" and accelerator.get("name"):
        fields.append(f"gpu={accelerator['name']}")
    if profile.get("accelerators", {}).get("tpu", {}).get("status") == "available":
        fields.append("tpu=available")
    print("[cloudmake] environment " + " ".join(fields))
    filesystem = profile.get("filesystem", {})
    isolation = profile.get("isolation", {})
    devices = profile.get("devices", {})
    feature_fields = [
        f"filesystem={filesystem.get('filesystem', 'unknown')}",
        f"exec={'yes' if filesystem.get('executable_files') else 'no'}",
        f"symlink={'yes' if filesystem.get('symbolic_links') else 'no'}",
        f"userns={isolation.get('user_namespace', {}).get('status', 'unknown')}",
        f"bindns={isolation.get('bind_mount_namespace', {}).get('status', 'unknown')}",
        f"cgroup={isolation.get('cgroup', {}).get('version', 'unknown')}",
        f"fuse={'yes' if devices.get('fuse', {}).get('present') else 'no'}",
        f"kvm={'yes' if devices.get('kvm', {}).get('present') else 'no'}",
    ]
    print("[cloudmake] capabilities " + " ".join(feature_fields))
    oci = profile.get("oci", {})
    clients = oci.get("clients", {})
    installed = sorted(
        name for name, value in clients.items() if value.get("status") == "installed"
    )
    cdi = oci.get("cdi", {})
    print(
        "[cloudmake] oci-clients="
        + (",".join(installed) if installed else "none")
        + f" cdi-devices={len(cdi.get('devices', []))}"
    )
    print("[cloudmake] environment evidence=observed (not a provider guarantee)")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    result.add_argument("--result", type=Path, default=DEFAULT_RESULT)
    result.add_argument("--render", type=Path)
    result.add_argument("--json", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    arguments_list = sys.argv[1:] if argv is None else argv
    local_mode = any(
        value == option or value.startswith(option + "=")
        for value in arguments_list
        for option in ("--workspace", "--result", "--render", "--json")
    )
    if not local_mode and not any(value in {"-h", "--help"} for value in arguments_list):
        # `colab exec -f` evaluates this file in a Jupyter kernel whose only
        # injected arguments are `-f <kernel-connection.json>`. Accept exactly
        # that shape; arbitrary unknown arguments remain errors.
        kernel_parser = argparse.ArgumentParser(add_help=False)
        kernel_parser.add_argument("-f", dest="kernel_connection_file")
        kernel_parser.parse_args(arguments_list)
        arguments = parser().parse_args([])
    else:
        arguments = parser().parse_args(arguments_list)
    if arguments.render is not None:
        profile = json.loads(arguments.render.read_text(encoding="utf-8"))
    else:
        profile = observe(arguments.workspace)
        atomic_json(arguments.result, profile)
        print(f"[cloudmake] environment-profile={arguments.result}")
    if arguments.json:
        json.dump(profile, sys.stdout, indent=2, sort_keys=True)
        print()
    elif arguments.render is not None:
        render(profile)
    return 0


if __name__ == "__main__":
    # A successful SystemExit is still rendered as an exception by IPython,
    # which is implementation noise in `colab exec -f` output.
    exit_code = main()
    if exit_code:
        raise SystemExit(exit_code)

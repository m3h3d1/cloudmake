from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import tarfile

import pytest

from conftest import PROJECT_ROOT, run_command


REMOTE = PROJECT_ROOT / "backend" / "kaggle-notebook" / "remote.py"
FINGERPRINT = PROJECT_ROOT / "core" / "source_fingerprint.py"


def load_remote():
    specification = importlib.util.spec_from_file_location("kaggle_remote", REMOTE)
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def source_snapshot(project: Path, directory: Path) -> tuple[Path, dict]:
    archive = directory / "source.tar.gz"
    manifest = directory / "manifest.json"
    result = run_command(
        ["python3", FINGERPRINT, "--root", project, "--archive", archive, "--manifest", manifest],
        cwd=directory,
    )
    assert result.returncode == 0
    return archive, json.loads(manifest.read_text())


def control(manifest: dict, *, active: str, previous: str = "", target: str = "build") -> dict:
    return {
        "schema": 1,
        "project_id": "project-id",
        "workspace_id": "0123456789abcdef01234567",
        "checkpoint": True,
        "previous_kernel_ref": previous,
        "active_kernel_ref": active,
        "source_manifest": manifest,
        "target": target,
        "jobs": 1,
        "makefile": "Makefile",
        "project_arguments": [],
        "collect_dir": None,
        "runner": "native",
        "image": "",
        "devices": [],
        "runtime_candidates": [],
    }


def execute(
    directory: Path, archive: Path, value: dict, *, input_root: Path
):
    run_control = directory / "control.json"
    run_control.write_text(json.dumps(value), encoding="utf-8")
    return run_command(
        [
            "python3", REMOTE, "--control", run_control,
            "--source-archive", archive, "--oci-runner", PROJECT_ROOT / "core" / "oci_runner.py",
            "--root", directory / "root", "--working", directory / "working",
            "--input-root", input_root,
        ],
        cwd=directory,
        check=False,
    )


def install_project(project: Path) -> None:
    project.mkdir()
    (project / "Makefile").write_text(
        ".PHONY: build check fail\n"
        "build:\n\t@mkdir -p generated $$HOME/.cache/cloudmake-test\n"
        "\t@printf persisted > generated/tool\n"
        "\t@printf home-persisted > $$HOME/.cache/cloudmake-test/tool\n"
        "check:\n\t@test \"$$(cat generated/tool)\" = persisted\n"
        "\t@test \"$$(cat $$HOME/.cache/cloudmake-test/tool)\" = home-persisted\n"
        "\t@printf checked > generated/result\n"
        "fail:\n\t@mkdir -p generated\n\t@printf partial > generated/partial\n\t@exit 2\n",
        encoding="utf-8",
    )


def attach_output(
    working: Path, input_root: Path, reference: str, *, provider_layout: str = "notebooks"
) -> None:
    owner, slug = reference.split("/", 1)
    destination = (
        input_root / "notebooks" / owner / slug
        if provider_layout == "notebooks"
        else input_root / slug / "versions" / "1"
    )
    destination.mkdir(parents=True)
    for name in (
        "cloudmake-checkpoint.tar.gz",
        "cloudmake-checkpoint-result.json",
        "cloudmake-source-manifest.json",
    ):
        shutil.copy2(working / name, destination / name)


def test_checkpoint_restores_generated_state_across_fresh_kaggle_vms(tmp_path: Path) -> None:
    project = tmp_path / "project"
    install_project(project)
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    (first_dir / "working").mkdir()
    archive, manifest = source_snapshot(project, first_dir)
    first_ref = "tester/cloudmake-ws-0123456789abcdef01234567-a"
    first = execute(first_dir, archive, control(manifest, active=first_ref), input_root=tmp_path / "input")
    assert first.returncode == 0, first.stdout
    assert json.loads((first_dir / "working/cloudmake-target-result.json").read_text())["status"] == "succeeded"

    attach_output(first_dir / "working", tmp_path / "input", first_ref)
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    (second_dir / "working").mkdir()
    archive2, manifest2 = source_snapshot(project, second_dir)
    second_ref = "tester/cloudmake-ws-0123456789abcdef01234567-b"
    second = execute(
        second_dir, archive2,
        control(manifest2, active=second_ref, previous=first_ref, target="check"),
        input_root=tmp_path / "input",
    )
    assert second.returncode == 0, second.stdout
    assert (second_dir / "root/workspace/src/generated/result").read_text() == "checked"
    assert (
        second_dir / "root/workspace/home/.cache/cloudmake-test/tool"
    ).read_text() == "home-persisted"
    receipt = json.loads((second_dir / "working/cloudmake-checkpoint-result.json").read_text())
    assert receipt["restore"]["outcome"] == "restored"
    assert receipt["snapshot"] == second_ref


def test_target_failure_is_receipted_without_checkpoint_or_runner_traceback(tmp_path: Path) -> None:
    project = tmp_path / "project"
    install_project(project)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "working").mkdir()
    archive, manifest = source_snapshot(project, run_dir)
    result = execute(
        run_dir, archive,
        control(manifest, active="tester/cloudmake-ws-0123456789abcdef01234567-a", target="fail"),
        input_root=tmp_path / "input",
    )
    assert result.returncode == 0, result.stdout
    assert "Traceback" not in result.stdout
    target = json.loads((run_dir / "working/cloudmake-target-result.json").read_text())
    assert target["status"] == "target-failed"
    assert target["exit_code"] == 2
    assert not (run_dir / "working/cloudmake-checkpoint.tar.gz").exists()
    checkpoint = json.loads(
        (run_dir / "working/cloudmake-checkpoint-result.json").read_text()
    )
    assert checkpoint["status"] == "skipped"
    assert checkpoint["outcome"] == "target-failed"


def test_target_output_streams_before_the_target_finishes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "Makefile").write_text(
        "stream:\n\t@echo first\n\t@while [ ! -f release ]; do sleep 0.1; done\n\t@echo second\n",
        encoding="utf-8",
    )
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "working").mkdir()
    archive, manifest = source_snapshot(project, run_dir)
    run_control = run_dir / "control.json"
    value = control(manifest, active="tester/cloudmake-ws-0123456789abcdef01234567-a", target="stream")
    run_control.write_text(json.dumps(dict(value, checkpoint=False)), encoding="utf-8")
    process = subprocess.Popen(
        [
            "python3", REMOTE, "--control", run_control, "--source-archive", archive,
            "--oci-runner", PROJECT_ROOT / "core" / "oci_runner.py",
            "--root", run_dir / "root", "--working", run_dir / "working",
            "--input-root", tmp_path / "input",
        ],
        cwd=run_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        seen = ""
        while "first" not in seen:
            assert select.select([process.stdout], [], [], 30)[0], seen
            seen += process.stdout.readline()
        (run_dir / "root/workspace/src/release").touch()
        rest, _ = process.communicate(timeout=30)
    finally:
        process.kill()
    assert process.returncode == 0, seen + rest
    assert "second" in rest
    log = (run_dir / "working/cloud-build.log").read_text()
    assert log.index("first") < log.index("second")


def test_requested_network_failure_stops_before_target(tmp_path: Path, monkeypatch) -> None:
    project = tmp_path / "project"
    install_project(project)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "working").mkdir()
    archive, manifest = source_snapshot(project, run_dir)
    value = control(
        manifest,
        active="tester/cloudmake-ws-0123456789abcdef01234567-a",
    )
    value["network_required"] = True
    module = load_remote()

    def unavailable(_host: str, _description: str) -> None:
        raise module.InfrastructureError("provider internet unavailable")

    monkeypatch.setattr(module, "require_network", unavailable)
    run_control = run_dir / "control.json"
    run_control.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(module.InfrastructureError):
        # Exercise the imported implementation so the network probe can be
        # replaced without contacting the internet.
        old_argv = module.os.sys.argv
        module.os.sys.argv = [
            str(REMOTE), "--control", str(run_control), "--source-archive",
            str(archive), "--oci-runner", str(PROJECT_ROOT / "core/oci_runner.py"),
            "--root", str(run_dir / "root"), "--working", str(run_dir / "working"),
            "--input-root", str(tmp_path / "input"),
        ]
        try:
            module.main()
        finally:
            module.os.sys.argv = old_argv

    receipt = json.loads(
        (run_dir / "working/cloudmake-target-result.json").read_text()
    )
    assert receipt["phase"] == "network_preflight"
    assert receipt["target_submission"] == "not_submitted"
    assert receipt["retry_safe"] is True
    assert not (run_dir / "root/workspace/src/generated").exists()


def test_checkpoint_round_trip_preserves_hard_links_and_read_only_directories(
    tmp_path: Path,
) -> None:
    loaded = load_remote()
    source = tmp_path / "source"
    read_only = source / "store"
    read_only.mkdir(parents=True)
    first = read_only / "first"
    first.write_text("closure", encoding="utf-8")
    os.link(first, read_only / "second")
    read_only.chmod(0o555)
    archive = tmp_path / "checkpoint.tar.gz"

    _, _, digest = loaded.archive_workspace(source, archive)
    restored = tmp_path / "restored"
    loaded.extract_archive(archive, restored)

    assert digest == loaded.file_sha256(archive)
    assert (restored / "store").stat().st_mode & 0o777 == 0o555
    assert (restored / "store/first").stat().st_ino == (
        restored / "store/second"
    ).stat().st_ino


def test_checkpoint_keeps_oci_layers_and_discards_materialized_rootfs(
    tmp_path: Path,
) -> None:
    loaded = load_remote()
    workspace = tmp_path / "workspace"
    digest = "a" * 64
    image_cache = workspace / "cache/oci/images" / digest
    (image_cache / "layout").mkdir(parents=True)
    (image_cache / "layout/oci-layout").write_text("{}", encoding="utf-8")
    (image_cache / "layout/index.json").write_text("{}", encoding="utf-8")
    (image_cache / "receipt.json").write_text("{}", encoding="utf-8")
    (image_cache / "bundle/rootfs").mkdir(parents=True)
    (image_cache / "bundle/rootfs/tool").write_text("derived", encoding="utf-8")
    (workspace / "home/.cache/tool").mkdir(parents=True)
    (workspace / "home/.cache/tool/state").write_text("persistent", encoding="utf-8")

    removed = loaded.prune_oci_materializations(workspace / "cache/oci")

    assert removed == 1
    assert not (image_cache / "bundle").exists()
    assert (image_cache / "layout/oci-layout").is_file()
    assert (image_cache / "receipt.json").is_file()
    assert (workspace / "home/.cache/tool/state").read_text() == "persistent"

    archive = tmp_path / "checkpoint.tar.gz"
    loaded.archive_workspace(workspace, archive)
    restored = tmp_path / "restored"
    loaded.extract_archive(archive, restored)
    restored_cache = restored / "cache/oci/images" / digest
    assert not (restored_cache / "bundle").exists()
    assert (restored_cache / "layout/oci-layout").is_file()
    assert (restored / "home/.cache/tool/state").read_text() == "persistent"


def test_checkpoint_restore_rejects_escaping_hard_link(tmp_path: Path) -> None:
    loaded = load_remote()
    archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        member = tarfile.TarInfo("escape")
        member.type = tarfile.LNKTYPE
        member.linkname = "../outside"
        output.addfile(member)

    try:
        loaded.extract_archive(archive, tmp_path / "restore")
    except loaded.InfrastructureError as error:
        assert "unsafe archive hard-link target" in str(error)
    else:
        raise AssertionError("escaping checkpoint hard link was accepted")


def test_expected_oci_preparation_failure_writes_infrastructure_receipt(
    tmp_path: Path, monkeypatch
) -> None:
    loaded = load_remote()
    control_path = tmp_path / "control.json"
    control_path.write_text("{}", encoding="utf-8")
    result_path = tmp_path / "oci-result.json"

    def fail(**_arguments):
        raise loaded.InfrastructureError("registry unavailable")

    monkeypatch.setattr(loaded, "run_target", fail)
    with pytest.raises(loaded.InfrastructureError):
        loaded.internal_run(
                [
                    str(control_path), str(tmp_path / "source"), str(tmp_path / "cache"),
                    str(tmp_path / "home"), str(tmp_path / "oci.py"), str(result_path),
                ]
        )

    receipt = json.loads(result_path.read_text())
    assert receipt["status"] == "infrastructure-failed"
    assert receipt["error"] == "registry unavailable"


def test_oci_runtime_package_install_retries_once_and_keeps_cache(
    tmp_path: Path, monkeypatch
) -> None:
    loaded = load_remote()
    installed: set[str] = set()
    commands: list[list[str]] = []

    monkeypatch.setattr(
        loaded.shutil, "which", lambda command: f"/usr/bin/{command}" if command in installed else None
    )
    monkeypatch.setattr(loaded, "require_network", lambda *_arguments: None)
    pinned_proot = tmp_path / "cache/runtime/proot/5.4.1/proot"
    monkeypatch.setattr(loaded, "prepare_current_proot", lambda _cache: pinned_proot)

    def run(command, **_arguments):
        commands.append([str(value) for value in command])
        if command[:2] == ["apt-get", "install"]:
            attempts = sum(item[:2] == ["apt-get", "install"] for item in commands)
            if attempts == 1:
                raise subprocess.CalledProcessError(100, command)
            installed.update({"skopeo", "umoci", "setpriv"})
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(loaded.subprocess, "run", run)
    assert loaded.prepare_oci_runtime(tmp_path / "cache") == pinned_proot

    installs = [command for command in commands if command[:2] == ["apt-get", "install"]]
    assert len(installs) == 2
    assert all("Acquire::Retries=3" in command for command in installs)
    assert all("Binary::apt-get::APT::Keep-Downloaded-Packages=true" in command for command in installs)


def test_current_proot_is_checksum_pinned_and_reused_from_checkpoint(
    tmp_path: Path, monkeypatch
) -> None:
    loaded = load_remote()
    payload = b"current proot"
    loaded.PROOT_SHA256 = __import__("hashlib").sha256(payload).hexdigest()
    destination = tmp_path / "cache/runtime/proot/5.4.1/proot"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(payload)
    destination.chmod(0o600)
    monkeypatch.setattr(
        loaded,
        "require_network",
        lambda *_: (_ for _ in ()).throw(AssertionError("must not use network")),
    )

    assert loaded.prepare_current_proot(tmp_path / "cache") == destination
    assert destination.stat().st_mode & 0o111


def test_current_proot_download_rejects_wrong_checksum(
    tmp_path: Path, monkeypatch
) -> None:
    loaded = load_remote()
    monkeypatch.setattr(loaded, "require_network", lambda *_: None)
    monkeypatch.setattr(
        loaded.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: io.BytesIO(b"unexpected binary"),
    )

    with pytest.raises(loaded.InfrastructureError, match="checksum mismatch"):
        loaded.prepare_current_proot(tmp_path / "cache")

    destination = tmp_path / "cache/runtime/proot/5.4.1/proot"
    assert not destination.exists()
    assert not destination.with_name(".proot.tmp").exists()


def test_kaggle_nvidia_cdi_ignores_matching_directories(
    tmp_path: Path, monkeypatch
) -> None:
    loaded = load_remote()
    candidates = {
        "/dev/nvidia0",
        "/dev/nvidiactl",
        "/dev/nvidia-uvm",
        "/dev/nvidia-caps",
        "/dev/nvidia-caps/nvidia-cap1",
    }
    monkeypatch.setattr(loaded.glob, "glob", lambda _pattern: list(candidates))
    monkeypatch.setattr(
        loaded.Path,
        "is_char_device",
        lambda path: str(path) != "/dev/nvidia-caps",
    )
    monkeypatch.setattr(loaded.Path, "is_block_device", lambda _path: False)
    monkeypatch.setattr(
        loaded.Path,
        "is_file",
        lambda path: str(path) == "/usr/lib/x86_64-linux-gnu/libcuda.so.1",
    )
    monkeypatch.setattr(loaded.shutil, "which", lambda _command: None)
    monkeypatch.setattr(
        loaded.subprocess,
        "run",
        lambda *_arguments, **_keywords: subprocess.CompletedProcess(
            [], 0, "libcuda.so.1 (libc6,x86-64) => /usr/lib/x86_64-linux-gnu/libcuda.so.1\n"
        ),
    )

    loaded.generate_nvidia_cdi(tmp_path / "cdi", ["nvidia.com/gpu=all"])

    payload = json.loads(
        (tmp_path / "cdi/cloudmake-kaggle-nvidia.json").read_text()
    )
    device_nodes = payload["devices"][0]["containerEdits"]["deviceNodes"]
    paths = {item["path"] for item in device_nodes}
    assert "/dev/nvidia-caps" not in paths
    assert "/dev/nvidia-caps/nvidia-cap1" in paths
    mounts = payload["devices"][0]["containerEdits"]["mounts"]
    assert mounts == [{
        "hostPath": "/usr/lib/x86_64-linux-gnu/libcuda.so.1",
        "containerPath": "/usr/lib/x86_64-linux-gnu/libcuda.so.1",
        "options": ["bind", "ro"],
    }]


def test_corrupt_prior_checkpoint_fails_before_project_target(tmp_path: Path) -> None:
    project = tmp_path / "project"
    install_project(project)
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    (first_dir / "working").mkdir()
    archive, manifest = source_snapshot(project, first_dir)
    first_ref = "tester/cloudmake-ws-0123456789abcdef01234567-a"
    first = execute(
        first_dir, archive, control(manifest, active=first_ref),
        input_root=tmp_path / "input",
    )
    assert first.returncode == 0
    attach_output(first_dir / "working", tmp_path / "input", first_ref)
    checkpoint = (
        tmp_path
        / "input/notebooks/tester/cloudmake-ws-0123456789abcdef01234567-a"
        / "cloudmake-checkpoint.tar.gz"
    )
    checkpoint.write_bytes(checkpoint.read_bytes() + b"corrupt")

    second_dir = tmp_path / "second"
    second_dir.mkdir()
    (second_dir / "working").mkdir()
    archive2, manifest2 = source_snapshot(project, second_dir)
    second = execute(
        second_dir, archive2,
        control(
            manifest2,
            active="tester/cloudmake-ws-0123456789abcdef01234567-b",
            previous=first_ref,
            target="check",
        ),
        input_root=tmp_path / "input",
    )

    assert second.returncode != 0
    assert "checkpoint digest does not match" in second.stdout
    assert "Traceback" not in second.stdout
    receipt = json.loads(
        (second_dir / "working/cloudmake-target-result.json").read_text()
    )
    assert receipt["status"] == "infrastructure-failed"
    assert not (second_dir / "working/cloudmake-checkpoint-result.json").exists()


def test_checkpoint_discovery_does_not_assume_kaggle_mount_layout(tmp_path: Path) -> None:
    project = tmp_path / "project"
    install_project(project)
    first_dir = tmp_path / "first"
    first_dir.mkdir()
    (first_dir / "working").mkdir()
    archive, manifest = source_snapshot(project, first_dir)
    first_ref = "tester/cloudmake-ws-0123456789abcdef01234567-a"
    first = execute(
        first_dir, archive, control(manifest, active=first_ref),
        input_root=tmp_path / "input",
    )
    assert first.returncode == 0
    attach_output(
        first_dir / "working", tmp_path / "input", first_ref,
        provider_layout="versioned",
    )
    second_dir = tmp_path / "second"
    second_dir.mkdir()
    (second_dir / "working").mkdir()
    archive2, manifest2 = source_snapshot(project, second_dir)

    second = execute(
        second_dir, archive2,
        control(
            manifest2,
            active="tester/cloudmake-ws-0123456789abcdef01234567-b",
            previous=first_ref,
            target="check",
        ),
        input_root=tmp_path / "input",
    )

    assert second.returncode == 0, second.stdout
    receipt = json.loads(
        (second_dir / "working/cloudmake-checkpoint-result.json").read_text()
    )
    assert receipt["restore"]["outcome"] == "restored"

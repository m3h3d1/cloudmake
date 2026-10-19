from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from conftest import PROJECT_ROOT


HOST_PATH = PROJECT_ROOT / "backend" / "colab-notebook" / "checkpoint_host.py"
REMOTE_PATH = PROJECT_ROOT / "backend" / "colab-notebook" / "checkpoint.py"
TRANSPORT_PATH = PROJECT_ROOT / "core" / "checkpoint_transport.py"
KEYCHAIN_PATH = PROJECT_ROOT / "core" / "checkpoint_keychain.py"
PROJECT_KEY = "0123456789abcdef01234567"
WORKSPACE_ID = "fedcba9876543210fedcba98"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_host_command_timeout_is_bounded(monkeypatch) -> None:
    module = load("checkpoint_host_timeout", HOST_PATH)

    def expire(*args, **kwargs):
        del args, kwargs
        raise subprocess.TimeoutExpired(["colab", "rm"], 3)

    monkeypatch.setattr(module.subprocess, "run", expire)

    assert module.run(["colab", "rm"], check=False, timeout=3) == 124
    try:
        module.run(["colab", "download"], timeout=3)
    except module.CheckpointFailure as error:
        assert "timed out after 3 seconds" in str(error)
    else:
        raise AssertionError("provider client timeout was not bounded")


def test_drive_mount_retries_transient_failures_on_same_session(
    monkeypatch, tmp_path: Path
) -> None:
    module = load("checkpoint_host_mount_retry", HOST_PATH)
    arguments = module.parser().parse_args(
        [
            "restore",
            "--client",
            "colab",
            "--session",
            "session",
            "--project-key",
            PROJECT_KEY,
            "--resource-state",
            "started",
            "--tool-root",
            str(PROJECT_ROOT),
            "--state-dir",
            str(tmp_path / "state"),
            "--result",
            str(tmp_path / "result.json"),
        ]
    )
    checkpoint = module.ColabCheckpoint(arguments)
    calls = 0

    def flaky_mount(*args, **kwargs):
        nonlocal calls
        calls += 1
        assert args == ("drivemount", "-s", "session")
        assert kwargs == {"timeout": 900}
        if calls < 3:
            raise module.CheckpointFailure("authorization reply timed out")
        return 0

    delays: list[int] = []
    monkeypatch.setattr(module.time, "sleep", delays.append)
    checkpoint.colab = flaky_mount
    checkpoint.mount()

    assert calls == 3
    assert delays == [5, 15]
    assert checkpoint.mounted is True


def test_drive_mount_stops_after_bounded_attempts(monkeypatch, tmp_path: Path) -> None:
    module = load("checkpoint_host_mount_exhausted", HOST_PATH)
    arguments = module.parser().parse_args(
        [
            "restore",
            "--client",
            "colab",
            "--session",
            "session",
            "--project-key",
            PROJECT_KEY,
            "--resource-state",
            "started",
            "--tool-root",
            str(PROJECT_ROOT),
            "--state-dir",
            str(tmp_path / "state"),
            "--result",
            str(tmp_path / "result.json"),
        ]
    )
    checkpoint = module.ColabCheckpoint(arguments)
    calls = 0

    def unavailable(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise module.CheckpointFailure("DriveFS mount failed")

    monkeypatch.setattr(module.time, "sleep", lambda delay: None)
    checkpoint.colab = unavailable
    try:
        checkpoint.mount()
    except module.CheckpointFailure as error:
        assert "DriveFS mount failed" in str(error)
    else:
        raise AssertionError("bounded Drive mount failure was hidden")

    assert calls == 4
    assert checkpoint.mounted is False


def test_workspace_readiness_marker_is_validated_quietly(tmp_path: Path) -> None:
    module = load("checkpoint_host_ready_probe", HOST_PATH)
    arguments = module.parser().parse_args(
        [
            "restore",
            "--client",
            "colab",
            "--session",
            "session",
            "--project-key",
            PROJECT_KEY,
            "--resource-state",
            "reused",
            "--tool-root",
            str(PROJECT_ROOT),
            "--state-dir",
            str(tmp_path / "state"),
            "--result",
            str(tmp_path / "result.json"),
        ]
    )
    checkpoint = module.ColabCheckpoint(arguments)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def valid_marker(*args, **kwargs):
        calls.append((args, kwargs))
        Path(args[-1]).write_text(
            json.dumps(
                {
                    "schema": 1,
                    "workspace_id": PROJECT_KEY,
                    "profile": "cloudmake-env-linux-x86_64-cpu",
                }
            ),
            encoding="utf-8",
        )
        return 0

    checkpoint.colab = valid_marker

    assert checkpoint.workspace_ready() is True
    assert calls == [
        (
            (
                "download",
                "-s",
                "session",
                module.REMOTE_WORKSPACE_READY,
                str(checkpoint.workspace_ready_receipt),
            ),
            {
                "check": False,
                "timeout": module.CLEANUP_TIMEOUT_SECONDS,
                "quiet": True,
            },
        )
    ]

    def wrong_workspace(*args, **kwargs):
        del kwargs
        Path(args[-1]).write_text(
            json.dumps(
                {
                    "schema": 1,
                    "workspace_id": WORKSPACE_ID,
                    "profile": "cloudmake-env-linux-x86_64-cpu",
                }
            ),
            encoding="utf-8",
        )
        return 0

    checkpoint.colab = wrong_workspace
    assert checkpoint.workspace_ready() is False


class RecordingCheckpoint:
    def __init__(
        self,
        module,
        state_dir: Path,
        *,
        repository_exists: bool,
        workspace_ready: bool = True,
    ) -> None:
        self.module = module
        self.state_dir = state_dir
        self.repository_exists = repository_exists
        self.ready = workspace_ready
        self.calls: list[str] = []

    def workspace_ready(self) -> bool:
        self.calls.append("workspace-ready")
        return self.ready

    def mount(self) -> None:
        self.calls.append("mount")

    def remote_operation(self, operation: str):
        self.calls.append(operation)
        if operation == "probe":
            return {"repository_exists": self.repository_exists}
        return {"status": "succeeded", "snapshot": "snapshot-id"}

    def ensure_key(self) -> None:
        self.calls.append("ensure-key")

    def deliver_key(self) -> None:
        self.calls.append("deliver-key")

    def execute(self, operation: str, resource_state: str):
        return self.module.ColabCheckpoint.execute(self, operation, resource_state)


def test_restore_skips_all_checkpoint_io_for_reused_resource(tmp_path: Path) -> None:
    module = load("checkpoint_host_reused", HOST_PATH)
    checkpoint = RecordingCheckpoint(module, tmp_path, repository_exists=True)

    result = checkpoint.execute("restore", "reused")

    assert result == {"outcome": "skipped", "reason": "resource-reused"}
    assert checkpoint.calls == ["workspace-ready"]


def test_reused_but_uninitialized_resource_restores_checkpoint(tmp_path: Path) -> None:
    module = load("checkpoint_host_reused_uninitialized", HOST_PATH)
    checkpoint = RecordingCheckpoint(
        module,
        tmp_path,
        repository_exists=True,
        workspace_ready=False,
    )

    result = checkpoint.execute("restore", "reused")

    assert result["snapshot"] == "snapshot-id"
    assert checkpoint.calls == [
        "workspace-ready",
        "mount",
        "probe",
        "deliver-key",
        "restore",
    ]


def test_new_repository_generates_key_without_attempting_restore(tmp_path: Path) -> None:
    module = load("checkpoint_host_new", HOST_PATH)
    checkpoint = RecordingCheckpoint(module, tmp_path, repository_exists=False)

    result = checkpoint.execute("restore", "started")

    assert result == {"outcome": "none", "repository": "new"}
    assert checkpoint.calls == ["mount", "probe", "ensure-key"]


def test_existing_repository_delivers_key_and_restores(tmp_path: Path) -> None:
    module = load("checkpoint_host_restore", HOST_PATH)
    checkpoint = RecordingCheckpoint(module, tmp_path, repository_exists=True)

    result = checkpoint.execute("restore", "started")

    assert result["snapshot"] == "snapshot-id"
    assert checkpoint.calls == ["mount", "probe", "deliver-key", "restore"]


def test_first_publication_generates_and_delivers_key(tmp_path: Path) -> None:
    module = load("checkpoint_host_publish", HOST_PATH)
    checkpoint = RecordingCheckpoint(module, tmp_path, repository_exists=False)

    result = checkpoint.execute("publish", "reused")

    assert result["snapshot"] == "snapshot-id"
    assert checkpoint.calls == [
        "mount",
        "probe",
        "ensure-key",
        "deliver-key",
        "publish",
    ]


def test_purge_deletes_remote_workspace_then_its_local_key(
    monkeypatch, tmp_path: Path
) -> None:
    module = load("checkpoint_host_purge", HOST_PATH)
    checkpoint = RecordingCheckpoint(module, tmp_path, repository_exists=True)
    checkpoint.workspace_id = WORKSPACE_ID
    checkpoint.tool_root = PROJECT_ROOT
    commands: list[list[str]] = []

    def record_run(command, *, check=True):
        del check
        commands.append([str(value) for value in command])
        return 0

    monkeypatch.setattr(module, "run", record_run)

    result = checkpoint.execute("purge", "started")

    assert result["status"] == "succeeded"
    assert checkpoint.calls == ["mount", "purge"]
    assert commands[0][-3:] == ["delete", "--project-key", WORKSPACE_ID]


def test_remote_tree_validation_rejects_top_level_escaping_symlink(tmp_path: Path) -> None:
    module = load("checkpoint_remote_tree", REMOTE_PATH)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "escape").symlink_to("../../outside")

    try:
        module.validate_tree(workspace)
    except module.CheckpointError as error:
        assert "escaping symbolic link" in str(error)
    else:
        raise AssertionError("escaping checkpoint symlink was accepted")


def test_remote_tree_validation_preserves_generated_absolute_links(tmp_path: Path) -> None:
    module = load("checkpoint_remote_generated_links", REMOTE_PATH)
    workspace = tmp_path / "workspace"
    generated = workspace / "src" / ".nix-store"
    generated.mkdir(parents=True)
    (generated / "libc.so").symlink_to("/nix/store/example-libc/lib/libc.so")
    (generated / "escape").symlink_to("../../../../ephemeral-activation")

    result = module.validate_tree(workspace)

    assert result == {"files": 4, "bytes": 0}
    assert os.readlink(generated / "libc.so").startswith("/nix/store/")


def test_remote_tree_validation_keeps_source_owned_links_confined(tmp_path: Path) -> None:
    module = load("checkpoint_remote_source_links", REMOTE_PATH)
    workspace = tmp_path / "workspace"
    source = workspace / "src"
    source.mkdir(parents=True)
    (workspace / "source-manifest.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "entries": {"link": {"kind": "symlink", "target": "/etc/passwd"}},
            }
        ),
        encoding="utf-8",
    )
    (source / "link").symlink_to("/etc/passwd")

    try:
        module.validate_tree(workspace)
    except module.CheckpointError as error:
        assert "source contains an absolute symbolic link" in str(error)
    else:
        raise AssertionError("absolute source-owned checkpoint link was accepted")


def test_remote_owner_validation_rejects_another_project(tmp_path: Path) -> None:
    module = load("checkpoint_remote_owner", REMOTE_PATH)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".cloudmake-owner.json").write_text(
        json.dumps({"schema": 1, "project_id": "f" * 24}), encoding="utf-8"
    )

    try:
        module.validate_owner(workspace, PROJECT_KEY)
    except module.CheckpointError as error:
        assert "does not match" in str(error)
    else:
        raise AssertionError("cross-project checkpoint was accepted")

    with pytest.raises(module.CheckpointError, match="changed since attachment"):
        module.validate_owner(workspace, PROJECT_KEY, allow_attach=True, adopt_from="e" * 24)

    module.validate_owner(workspace, PROJECT_KEY, allow_attach=True, adopt_from="f" * 24)


def test_remote_validation_never_follows_symlinked_control_records(tmp_path: Path) -> None:
    module = load("checkpoint_remote_control_links", REMOTE_PATH)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text(
        json.dumps({"schema": 1, "project_id": PROJECT_KEY, "entries": {}}),
        encoding="utf-8",
    )
    (workspace / ".cloudmake-owner.json").symlink_to(outside)

    try:
        module.validate_owner(workspace, PROJECT_KEY)
    except module.CheckpointError as error:
        assert "not a regular file" in str(error)
    else:
        raise AssertionError("symlinked owner record was followed")

    (workspace / ".cloudmake-owner.json").unlink()
    (workspace / "source-manifest.json").symlink_to(outside)
    try:
        module.validate_tree(workspace)
    except module.CheckpointError as error:
        assert "not a regular file" in str(error)
    else:
        raise AssertionError("symlinked source manifest was followed")


def test_control_separates_project_and_persistent_workspace_identity(
    monkeypatch, tmp_path: Path
) -> None:
    module = load("checkpoint_remote_identity", REMOTE_PATH)
    control = tmp_path / "control.json"
    control.write_text(
        json.dumps(
            {
                "schema": 1,
                "project_key": PROJECT_KEY,
                "workspace_id": WORKSPACE_ID,
                "allow_attach": True,
                "adopt_from": "f" * 24,
                "repository": (
                    "/content/drive/MyDrive/.cloudmake/checkpoints/"
                    f"{WORKSPACE_ID}/restic"
                ),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "CONTROL", control)

    loaded = module.load_control()
    assert loaded["workspace_id"] == WORKSPACE_ID
    assert loaded["adopt_from"] == "f" * 24


def test_remote_tree_validation_has_no_cloudmake_size_ceiling(tmp_path: Path) -> None:
    module = load("checkpoint_remote_limits", REMOTE_PATH)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "one").write_bytes(b"1234")
    (workspace / "two").write_bytes(b"5678")

    assert module.validate_tree(workspace) == {"files": 2, "bytes": 8}


def test_runtime_metadata_initializes_fresh_workspace_parent(
    monkeypatch, tmp_path: Path
) -> None:
    module = load("checkpoint_remote_fresh_runtime", REMOTE_PATH)
    workspace = tmp_path / "content" / ".cloud-build" / "workspace"
    monkeypatch.setattr(module, "WORKSPACE", workspace)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)

    metadata = module.runtime_metadata()

    assert workspace.parent.is_dir()
    assert metadata["disk"]["total_bytes"] > 0


def test_restore_capacity_is_checked_against_actual_vm_space(monkeypatch, tmp_path: Path) -> None:
    module = load("checkpoint_remote_snapshot_limits", REMOTE_PATH)
    calls: list[tuple[object, ...]] = []

    def fake_restic(*args, **kwargs):
        calls.append(args)
        return module.subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps({"total_file_count": 12, "total_size": 100}),
        )

    monkeypatch.setattr(module, "restic", fake_restic)
    control = {"workspace_id": PROJECT_KEY}
    assert module.snapshot_statistics("restic", Path("/repo"), control) == {
        "files": 12,
        "bytes": 100,
    }
    assert module.TAG in calls[0]
    assert module.profile_tag() not in calls[0]
    monkeypatch.setattr(
        module.shutil,
        "disk_usage",
        lambda path: module.shutil._ntuple_diskusage(1000, 950, 50),
    )
    try:
        module.validate_restore_capacity({"files": 1, "bytes": 100}, tmp_path)
    except module.CheckpointError as error:
        assert "only 50 free" in str(error)
    else:
        raise AssertionError("snapshot larger than free VM disk was accepted")


def test_restore_reports_profile_change_without_rejecting_workspace(
    monkeypatch, tmp_path: Path, capsys
) -> None:
    module = load("checkpoint_remote_profile_change", REMOTE_PATH)
    remote_root = tmp_path / "content" / ".cloud-build"
    workspace = remote_root / "workspace"
    restore_root = remote_root / "checkpoint-restore"
    old_workspace = remote_root / "workspace.old"
    remote_root.mkdir(parents=True)
    repository = tmp_path / "repository"
    calls: list[tuple[str, ...]] = []

    monkeypatch.setattr(module, "WORKSPACE", workspace)
    monkeypatch.setattr(module, "WORKSPACE_READY", workspace / ".cloudmake-checkpoint-ready.json")
    monkeypatch.setattr(module, "RESTORE_ROOT", restore_root)
    monkeypatch.setattr(module, "OLD_WORKSPACE", old_workspace)
    monkeypatch.setattr(module, "require_mount", lambda: None)
    monkeypatch.setattr(module, "repository_exists", lambda path: True)
    monkeypatch.setattr(module, "install_restic", lambda: "restic")
    monkeypatch.setattr(
        module, "snapshot_statistics", lambda *args: {"files": 2, "bytes": 64}
    )
    monkeypatch.setattr(module, "validate_restore_capacity", lambda *args: None)
    monkeypatch.setattr(module, "runtime_metadata", lambda: {"cpu_count": 2})
    monkeypatch.setattr(
        module, "profile_tag", lambda: "cloudmake-env-linux-x86_64-nvidia"
    )
    clock = iter((10.0, 12.5))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))

    def fake_restic(executable, selected_repository, *arguments, capture=False):
        del executable, selected_repository, capture
        calls.append(arguments)
        if arguments[0] == "restore":
            restored = restore_root / workspace.relative_to("/")
            restored.mkdir(parents=True)
            (restored / ".cloudmake-owner.json").write_text(
                json.dumps({"schema": 1, "project_id": PROJECT_KEY}),
                encoding="utf-8",
            )
            (restored / "output").write_text("portable", encoding="utf-8")
        if arguments[0] == "snapshots":
            return module.subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(
                    [
                        {
                            "short_id": "abc123",
                            "tags": [
                                module.TAG,
                                "cloudmake-env-linux-x86_64-cpu",
                            ],
                        }
                    ]
                ),
            )
        return module.subprocess.CompletedProcess(args=[], returncode=0, stdout="")

    monkeypatch.setattr(module, "restic", fake_restic)
    result = module.restore(
        {
            "project_key": PROJECT_KEY,
            "workspace_id": PROJECT_KEY,
            "allow_attach": False,
            "repository": str(repository),
        }
    )

    restore_call = next(call for call in calls if call[0] == "restore")
    assert module.TAG in restore_call
    assert "cloudmake-env-linux-x86_64-nvidia" not in restore_call
    assert result["profile"] == "cloudmake-env-linux-x86_64-cpu"
    assert result["current_profile"] == "cloudmake-env-linux-x86_64-nvidia"
    assert result["elapsed_seconds"] == 2.5
    output = capsys.readouterr().out
    assert "environment-changed" in output
    assert "snapshot-restored=abc123 elapsed=2.500s" in output
    assert (workspace / "output").read_text(encoding="utf-8") == "portable"


def test_publish_records_elapsed_time(monkeypatch, tmp_path: Path, capsys) -> None:
    module = load("checkpoint_remote_publish_elapsed", REMOTE_PATH)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setattr(module, "WORKSPACE", workspace)
    monkeypatch.setattr(module, "WORKSPACE_READY", workspace / ".ready")
    monkeypatch.setattr(module, "require_mount", lambda: None)
    monkeypatch.setattr(module, "validate_owner", lambda *args, **kwargs: None)
    monkeypatch.setattr(module, "mark_workspace_ready", lambda *args: None)
    monkeypatch.setattr(module, "validate_tree", lambda *args: {"files": 3, "bytes": 64})
    monkeypatch.setattr(module, "install_restic", lambda: "restic")
    monkeypatch.setattr(module, "repository_exists", lambda path: True)
    monkeypatch.setattr(module, "profile_tag", lambda: "cloudmake-env-linux-x86_64-cpu")
    monkeypatch.setattr(module, "runtime_metadata", lambda: {"cpu_count": 2})
    clock = iter((20.0, 23.125))
    monkeypatch.setattr(module.time, "monotonic", lambda: next(clock))

    def fake_restic(executable, selected_repository, *arguments, capture=False):
        del executable, selected_repository
        if arguments[0] == "backup":
            assert capture is True
            return module.subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(
                    {
                        "message_type": "summary",
                        "snapshot_id": "def456",
                        "data_added": 128,
                    }
                ),
            )
        return module.subprocess.CompletedProcess(args=[], returncode=0, stdout="")

    monkeypatch.setattr(module, "restic", fake_restic)
    result = module.publish(
        {
            "project_key": PROJECT_KEY,
            "workspace_id": PROJECT_KEY,
            "repository": str(repository),
        }
    )

    assert result["snapshot"] == "def456"
    assert result["data_added"] == 128
    assert result["elapsed_seconds"] == 3.125
    assert "snapshot-published=def456 data-added=128 elapsed=3.125s" in capsys.readouterr().out


def test_purge_removes_only_the_exact_workspace_directory(
    monkeypatch, tmp_path: Path
) -> None:
    module = load("checkpoint_remote_purge", REMOTE_PATH)
    mount = tmp_path / "drive"
    selected = mount / "MyDrive" / ".cloudmake" / "checkpoints" / PROJECT_KEY
    repository = selected / "restic"
    repository.mkdir(parents=True)
    (repository / "config").write_text("repository", encoding="utf-8")
    sibling = selected.parent / ("f" * 24)
    sibling.mkdir()
    (sibling / "keep").write_text("safe", encoding="utf-8")
    monkeypatch.setattr(module, "MOUNT", mount)
    monkeypatch.setattr(module, "require_mount", lambda: None)

    result = module.purge(
        {"workspace_id": PROJECT_KEY, "repository": str(repository)}
    )

    assert result == {"outcome": "purged", "workspace_id": PROJECT_KEY}
    assert not selected.exists()
    assert (sibling / "keep").read_text(encoding="utf-8") == "safe"


def test_environment_profile_distinguishes_nvidia_from_cpu(monkeypatch) -> None:
    module = load("checkpoint_remote_profile", REMOTE_PATH)
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(module.Path, "is_file", lambda self: False)
    monkeypatch.setattr(module.shutil, "which", lambda name: None)

    assert module.profile_tag() == "cloudmake-env-linux-x86_64-cpu"

    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/nvidia-smi"
    )
    assert module.profile_tag() == "cloudmake-env-linux-x86_64-nvidia"


class Pipe:
    def __init__(self) -> None:
        self.buffer = tempfile.TemporaryFile()

    def isatty(self) -> bool:
        return False


def test_transport_round_trip_and_destroy(tmp_path: Path, monkeypatch) -> None:
    transport = load("checkpoint_transport_roundtrip", TRANSPORT_PATH)
    keychain = load("checkpoint_keychain_roundtrip", KEYCHAIN_PATH)
    secret = b"internal-checkpoint-key-8b4f50a6"
    directory = tmp_path / "transport"
    transport.generate(directory)
    envelope = directory / transport.ENVELOPE_NAME

    class Store:
        def find(self, service: str, account: str):
            del service, account
            return bytearray(secret)

        def add(self, service: str, account: str, value: bytearray) -> None:
            raise AssertionError("existing key must not be replaced")

    keychain.encrypt_key(
        Store(), PROJECT_KEY, directory / transport.PUBLIC_NAME, envelope
    )
    assert secret not in envelope.read_bytes()
    pipe = Pipe()
    monkeypatch.setattr(transport.sys, "stdout", pipe)

    transport.password(directory)

    pipe.buffer.seek(0)
    assert pipe.buffer.read() == secret
    transport.destroy(directory)
    assert not directory.exists()


def test_cleanup_runs_for_reused_session_without_prior_state_directory(
    tmp_path: Path,
) -> None:
    module = load("checkpoint_host_cleanup_reused", HOST_PATH)
    arguments = module.parser().parse_args(
        [
            "restore",
            "--client",
            "colab",
            "--session",
            "session",
            "--project-key",
            PROJECT_KEY,
            "--resource-state",
            "reused",
            "--tool-root",
            str(PROJECT_ROOT),
            "--state-dir",
            str(tmp_path / "new" / "state"),
            "--result",
            str(tmp_path / "result.json"),
        ]
    )
    checkpoint = module.ColabCheckpoint(arguments)
    operations: list[str] = []
    checkpoint.workspace_ready = lambda: True

    def verified_remote_action(**kwargs) -> None:
        operations.append(kwargs["operation"])

    checkpoint.verified_remote_action = verified_remote_action
    module.CLEANUP_RETRY_DELAYS = ()

    assert checkpoint.execute("restore", "reused") == {
        "outcome": "skipped",
        "reason": "resource-reused",
    }
    assert checkpoint.cleanup() == []
    assert operations == ["destroy", "unmount"]


def test_cleanup_failure_is_reported_as_infrastructure_failure(tmp_path: Path) -> None:
    module = load("checkpoint_host_cleanup_failure", HOST_PATH)
    arguments = module.parser().parse_args(
        [
            "restore",
            "--client",
            "colab",
            "--session",
            "session",
            "--project-key",
            PROJECT_KEY,
            "--resource-state",
            "reused",
            "--tool-root",
            str(PROJECT_ROOT),
            "--state-dir",
            str(tmp_path / "state"),
            "--result",
            str(tmp_path / "result.json"),
        ]
    )
    checkpoint = module.ColabCheckpoint(arguments)

    def verified_remote_action(**kwargs) -> None:
        raise module.CheckpointFailure(f"{kwargs['operation']} failed")

    checkpoint.verified_remote_action = verified_remote_action
    module.CLEANUP_RETRY_DELAYS = ()
    checkpoint.colab = lambda *args, **kwargs: 0

    assert checkpoint.cleanup() == [
        "transient checkpoint key cleanup failed: destroy failed",
        "Drive unmount verification failed: unmount failed",
    ]


def test_cleanup_retries_idempotent_actions_on_same_session(
    monkeypatch, tmp_path: Path
) -> None:
    module = load("checkpoint_host_cleanup_retry", HOST_PATH)
    arguments = module.parser().parse_args(
        [
            "restore",
            "--client",
            "colab",
            "--session",
            "session",
            "--project-key",
            PROJECT_KEY,
            "--resource-state",
            "reused",
            "--tool-root",
            str(PROJECT_ROOT),
            "--state-dir",
            str(tmp_path / "state"),
            "--result",
            str(tmp_path / "result.json"),
        ]
    )
    checkpoint = module.ColabCheckpoint(arguments)
    attempts = {"destroy": 0, "unmount": 0}

    def transient(**kwargs) -> None:
        operation = kwargs["operation"]
        attempts[operation] += 1
        if attempts[operation] == 1:
            raise module.CheckpointFailure("Connection was lost")

    delays: list[int] = []
    checkpoint.verified_remote_action = transient
    checkpoint.colab = lambda *args, **kwargs: 0
    monkeypatch.setattr(module.time, "sleep", delays.append)

    assert checkpoint.cleanup() == []
    assert attempts == {"destroy": 2, "unmount": 2}
    assert delays == [2, 2]


def test_cleanup_does_not_swallow_a_second_interrupt(tmp_path: Path) -> None:
    module = load("checkpoint_host_cleanup_interrupt", HOST_PATH)
    arguments = module.parser().parse_args(
        [
            "restore",
            "--client",
            "colab",
            "--session",
            "session",
            "--project-key",
            PROJECT_KEY,
            "--resource-state",
            "reused",
            "--tool-root",
            str(PROJECT_ROOT),
            "--state-dir",
            str(tmp_path / "state"),
            "--result",
            str(tmp_path / "result.json"),
        ]
    )
    checkpoint = module.ColabCheckpoint(arguments)

    def interrupt(**kwargs) -> None:
        del kwargs
        raise KeyboardInterrupt

    checkpoint.verified_remote_action = interrupt

    try:
        checkpoint.cleanup()
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("checkpoint cleanup swallowed Ctrl-C")

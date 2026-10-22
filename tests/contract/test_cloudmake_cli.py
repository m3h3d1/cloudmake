from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from conftest import PROJECT_ROOT, run_command, write_executable


LAUNCHER = PROJECT_ROOT / "cmd" / "cloudmake"

pytestmark = pytest.mark.contract


def make_project(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "Makefile").write_text(
        ".PHONY: build test run package clean fetch status\n"
        "build test run package clean fetch status:\n\t@true\n",
        encoding="utf-8",
    )
    (path / "source.txt").write_text("local source\n", encoding="utf-8")
    return path


def contract_environment(tmp_path: Path, fake_bin: Path) -> tuple[dict[str, str], Path]:
    log = tmp_path / "engine.jsonl"
    write_executable(
        fake_bin / "make",
        r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path
with Path(os.environ["CLOUDMAKE_TEST_ENGINE_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\n")
raise SystemExit(int(os.environ.get("CLOUDMAKE_TEST_ENGINE_EXIT", "0")))
''',
    )
    environment = {
        "PATH": f"{fake_bin}{os.pathsep}{os.environ['PATH']}",
        "CLOUDMAKE_CONFIG_HOME": str(tmp_path / "config"),
        "CLOUDMAKE_STATE_HOME": str(tmp_path / "state"),
        "CLOUDMAKE_CACHE_HOME": str(tmp_path / "cache"),
        "CLOUDMAKE_TEST_ENGINE_LOG": str(log),
    }
    return environment, log


def invoke(project: Path, environment: dict[str, str], *arguments: str, check: bool = True):
    return run_command(
        [LAUNCHER, *arguments], cwd=project, env=environment, check=check
    )


def engine_calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def assert_assignment(call: list[str], name: str, value: str) -> None:
    assert f"{name}={value}" in call


def assert_remote_target(call: list[str], target: str) -> None:
    encoded = next(
        argument.split("=", 1)[1]
        for argument in call
        if argument.startswith("REMOTE_TARGET_B64=")
    )
    assert base64.urlsafe_b64decode(encoded).decode("utf-8") == target


def assert_collect_directory(call: list[str], directory: str) -> None:
    encoded = next(
        argument.split("=", 1)[1]
        for argument in call
        if argument.startswith("REMOTE_COLLECT_DIR_B64=")
    )
    assert base64.urlsafe_b64decode(encoded).decode("utf-8") == directory


def project_arguments(call: list[str]) -> list[str]:
    encoded = next(
        argument.split("=", 1)[1]
        for argument in call
        if argument.startswith("CLOUDMAKE_PROJECT_ARGS_B64=")
    )
    return json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))


def artifact_receipt(directory: Path) -> dict[str, object]:
    digest = hashlib.sha256()
    files = 0
    total_bytes = 0
    for path in sorted(directory.rglob("*"), key=lambda item: item.as_posix()):
        if not path.is_file():
            continue
        relative = path.relative_to(directory).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big") + relative)
        files += 1
        content = path.read_bytes()
        total_bytes += len(content)
        digest.update(content)
    return {
        "path": str(directory),
        "fingerprint": digest.hexdigest(),
        "files": files,
        "total_bytes": total_bytes,
    }


def install_legacy_artifact_receipt(
    project: Path,
    environment: dict[str, str],
    log: Path,
    *,
    fingerprint: str | None = None,
) -> Path:
    invoke(project, environment, "-b", "local", "build")
    latest = next((Path(environment["CLOUDMAKE_STATE_HOME"]) / "projects").glob(
        "*/runs/latest.json"
    ))
    legacy_artifacts = project / "artifacts"
    legacy_artifacts.mkdir()
    (legacy_artifacts / "private-result.txt").write_text(
        "downloaded private output\n", encoding="utf-8"
    )
    receipt = artifact_receipt(legacy_artifacts)
    if fingerprint is not None:
        receipt["fingerprint"] = fingerprint
    (latest.parent / "v1-legacy.json").write_text(
        json.dumps({"schema": 1, "artifacts": receipt}) + "\n",
        encoding="utf-8",
    )
    log.write_text("", encoding="utf-8")
    return latest.parent.parent


def test_no_arguments_is_read_only_and_does_not_invoke_engine(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment)

    assert "project" in result.stdout
    assert "backend" in result.stdout.lower()
    assert engine_calls(log) == []


def test_help_documents_bounded_capacity_retry(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "--help")

    assert "--retry-for=DURATION" in result.stdout
    assert "cloudmake --retry-for=30m --start" in result.stdout
    assert "--idempotent" in result.stdout
    assert "--replay-for=DURATION" in result.stdout
    assert "--accept-legacy-artifacts-as-source" in result.stdout
    assert "at-most-once by default" in result.stdout
    assert engine_calls(log) == []


@pytest.mark.parametrize(
    "arguments",
    [
        ("--replay-for=30s", "build"),
        ("--idempotent", "--start"),
        ("--idempotent",),
        ("--idempotent", "--replay-for=0s", "build"),
        ("--idempotent", "--replay-for=25h", "build"),
        ("--accept-legacy-artifacts-as-source", "--version"),
    ],
)
def test_invalid_target_semantic_options_fail_before_engine_use(
    tmp_path: Path, fake_bin: Path, arguments: tuple[str, ...]
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, *arguments, check=False)

    assert result.returncode == 2
    assert engine_calls(log) == []


@pytest.mark.parametrize(
    ("backend", "extra"),
    [
        ("local", ()),
        ("colab", ()),
        ("kaggle", ()),
        ("codespaces", ()),
        ("colab-ssh", ()),
        ("ssh", ("--host", "lab-gpu")),
        ("lightning", ()),
        ("gcp", ()),
    ],
)
def test_backends_without_fencing_reject_replay_before_provider_contact(
    tmp_path: Path,
    fake_bin: Path,
    backend: str,
    extra: tuple[str, ...],
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    if backend == "gcp":
        environment.update(
            {
                "GCP_PROJECT": "test-project",
                "GCP_ZONE": "us-central1-a",
                "GCP_INSTANCE": "test-instance",
            }
        )

    result = invoke(
        project,
        environment,
        "-b",
        backend,
        *extra,
        "--idempotent",
        "--replay-for=30s",
        "build",
        check=False,
    )

    assert result.returncode == 2
    assert "cannot prove fenced, non-overlapping target attempts" in result.stdout
    assert engine_calls(log) == []


def test_launcher_defaults_legacy_api_1_replay_declaration_to_none(
    tmp_path: Path, fake_bin: Path
) -> None:
    runtime = tmp_path / "legacy-runtime"
    shutil.copytree(PROJECT_ROOT, runtime)
    descriptor = runtime / "backend" / "local" / "backend.mk"
    descriptor.write_text(
        descriptor.read_text(encoding="utf-8").replace(
            "BACKEND_TARGET_REPLAY := none\n", ""
        ),
        encoding="utf-8",
    )
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = run_command(
        [
            runtime / "cmd" / "cloudmake",
            "-b",
            "local",
            "--idempotent",
            "--replay-for=30s",
            "build",
        ],
        cwd=project,
        env=environment,
        check=False,
    )

    assert result.returncode == 2
    assert "declares target_replay=none" in result.stdout
    assert engine_calls(log) == []


def test_launcher_rejects_unknown_api_1_replay_declaration_before_engine_use(
    tmp_path: Path, fake_bin: Path
) -> None:
    runtime = tmp_path / "invalid-runtime"
    shutil.copytree(PROJECT_ROOT, runtime)
    descriptor = runtime / "backend" / "local" / "backend.mk"
    descriptor.write_text(
        descriptor.read_text(encoding="utf-8").replace(
            "BACKEND_TARGET_REPLAY := none",
            "BACKEND_TARGET_REPLAY := overlapping",
        ),
        encoding="utf-8",
    )
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = run_command(
        [runtime / "cmd" / "cloudmake", "-b", "local", "build"],
        cwd=project,
        env=environment,
        check=False,
    )

    assert result.returncode == 2
    assert "invalid target replay declaration" in result.stdout
    assert engine_calls(log) == []


def test_help_documents_opt_in_checkpoint_selection(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "--help")

    assert "--persist" in result.stdout
    assert "--no-persist" in result.stdout
    assert "--checkpoint" in result.stdout
    assert "local and per project" in result.stdout
    assert "not format compatibility" in result.stdout
    assert "local/persistent SSH=native" in result.stdout
    assert "deprecated Kaggle=" in result.stdout
    assert "experimental private-output checkpoint" in result.stdout
    assert "Colab SSH=unsupported" in result.stdout
    assert engine_calls(log) == []


def test_help_documents_digest_pinned_oci_runner_selection(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "--help")

    assert "--image REF@sha256:DIGEST" in result.stdout
    assert "--device CDI_NAME" in result.stdout
    assert "--native" in result.stdout
    assert "OCI/CDI Make" in result.stdout
    assert "--devcontainer[=PATH]" in result.stdout
    assert engine_calls(log) == []


def test_devcontainer_selection_persists_and_drives_the_existing_oci_surface(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    image = "registry.example/workstation@sha256:" + "d" * 64
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps(
            {
                "image": image,
                "containerEnv": {"COURSE": "ece326"},
                "hostRequirements": {"cpus": 2},
                "forwardPorts": [8080],
            }
        ),
        encoding="utf-8",
    )

    selected = invoke(
        project, environment, "--use", "ssh", "--host", "lab-gpu", "--devcontainer"
    )
    invoke(project, environment, "build")

    assert "devcontainer=.devcontainer/devcontainer.json" in selected.stdout
    preference = next((tmp_path / "config" / "projects").glob("*.json"))
    saved = json.loads(preference.read_text(encoding="utf-8"))
    assert saved["devcontainer"] == ".devcontainer/devcontainer.json"
    assert "image" not in saved
    call = engine_calls(log)[0]
    assert_assignment(call, "CLOUDMAKE_RUNNER", "oci")
    assert_assignment(call, "CLOUDMAKE_OCI_IMAGE_B64", base64.urlsafe_b64encode(image.encode()).decode())
    environment_b64 = next(
        value.split("=", 1)[1]
        for value in call
        if value.startswith("CLOUDMAKE_OCI_ENVIRONMENT_B64=")
    )
    assert json.loads(base64.urlsafe_b64decode(environment_b64)) == ["COURSE=ece326"]
    assert_assignment(
        call,
        "CLOUDMAKE_SSH_FORWARD_OPTIONS",
        "-o ExitOnForwardFailure=yes -L 8080:127.0.0.1:8080",
    )


def test_devcontainer_privilege_failure_precedes_provider_contact(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps(
            {
                "image": "registry.example/workstation@sha256:" + "e" * 64,
                "privileged": True,
            }
        ),
        encoding="utf-8",
    )

    result = invoke(project, environment, "--devcontainer", "build", check=False)

    assert result.returncode == 2
    assert "least-privilege profile" in result.stdout
    assert engine_calls(log) == []


def test_richer_devcontainer_selects_qualified_local_native_engine(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps(
            {
                "image": "ubuntu:24.04",
                "features": {"ghcr.io/devcontainers/features/git:1": {}},
                "postCreateCommand": "true",
            }
        ),
        encoding="utf-8",
    )

    result = invoke(project, environment, "--use", "local", "--devcontainer")

    assert "runner=devcontainer-native" in result.stdout
    assert engine_calls(log) == []


def test_richer_devcontainer_rejects_restricted_backend_before_dispatch(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps({"image": "ubuntu:24.04", "postCreateCommand": "true"}),
        encoding="utf-8",
    )

    result = invoke(
        project, environment, "-b", "colab", "--devcontainer", "build", check=False
    )

    assert result.returncode == 2
    assert "cannot honor Dev Container requirement(s)" in result.stdout
    assert "image-tag" in result.stdout
    assert "image-tag (image)" in result.stdout
    assert "lifecycle-create" in result.stdout
    assert "lifecycle-create (postCreateCommand)" in result.stdout
    assert engine_calls(log) == []


DEVCONTAINER_BACKEND_SELECTIONS = [
    ("local", ()),
    ("colab", ()),
    ("codespaces", ()),
    ("gcp", ()),
    ("colab-ssh", ()),
    ("ssh", ("--host", "lab-gpu")),
    ("lightning", ()),
]


def devcontainer_backend_environment(
    environment: dict[str, str], backend: str
) -> dict[str, str]:
    result = dict(environment)
    if backend == "gcp":
        result.update(
            GCP_PROJECT="sample-project",
            GCP_ZONE="us-central1-a",
            GCP_INSTANCE="workstation",
        )
    return result


@pytest.mark.parametrize(("backend", "extra"), DEVCONTAINER_BACKEND_SELECTIONS)
def test_portable_devcontainer_profile_is_selectable_on_every_qualified_backend(
    tmp_path: Path,
    fake_bin: Path,
    backend: str,
    extra: tuple[str, ...],
) -> None:
    project = make_project(tmp_path / f"portable-{backend}")
    environment, log = contract_environment(tmp_path, fake_bin)
    environment = devcontainer_backend_environment(environment, backend)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps(
            {
                "image": "registry.example/workstation@sha256:" + "a" * 64,
                "remoteEnv": {"MODE": "portable"},
                "hostRequirements": {"cpus": 1},
                "securityOpt": ["no-new-privileges"],
            }
        ),
        encoding="utf-8",
    )

    result = invoke(
        project, environment, "--use", backend, *extra, "--devcontainer"
    )

    assert "runner=oci" in result.stdout
    assert "devcontainer=.devcontainer/devcontainer.json" in result.stdout
    assert engine_calls(log) == []


@pytest.mark.parametrize(
    ("backend", "extra"),
    [*DEVCONTAINER_BACKEND_SELECTIONS[1:], ("kaggle", ())],
)
def test_rich_devcontainer_profile_fails_before_every_remote_provider_contact(
    tmp_path: Path,
    fake_bin: Path,
    backend: str,
    extra: tuple[str, ...],
) -> None:
    project = make_project(tmp_path / f"rich-{backend}")
    environment, log = contract_environment(tmp_path, fake_bin)
    environment = devcontainer_backend_environment(environment, backend)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps({"image": "ubuntu:24.04", "postCreateCommand": "true"}),
        encoding="utf-8",
    )

    result = invoke(
        project,
        environment,
        "-b",
        backend,
        *extra,
        "--devcontainer",
        "build",
        check=False,
    )

    assert result.returncode == 2
    assert "Dev Container" in result.stdout
    assert engine_calls(log) == []


def test_incompatible_backend_switch_does_not_mutate_saved_workstation_selection(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "switch-rich-profile")
    environment, log = contract_environment(tmp_path, fake_bin)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps({"image": "ubuntu:24.04", "postCreateCommand": "true"}),
        encoding="utf-8",
    )
    invoke(project, environment, "--use", "local", "--devcontainer")
    preference = next((tmp_path / "config" / "projects").glob("*.json"))
    before = preference.read_text(encoding="utf-8")

    result = invoke(project, environment, "--use", "colab", check=False)

    assert result.returncode == 2
    assert "lifecycle-create (postCreateCommand)" in result.stdout
    assert preference.read_text(encoding="utf-8") == before
    assert engine_calls(log) == []


@pytest.mark.parametrize(
    "arguments",
    [
        ("--start",),
        ("build",),
        ("--collect", "out", "build"),
    ],
)
def test_incompatible_saved_profile_blocks_every_workload_boundary_before_provider_contact(
    tmp_path: Path,
    fake_bin: Path,
    arguments: tuple[str, ...],
) -> None:
    project = make_project(tmp_path / "saved-incompatible-workload")
    environment, log = contract_environment(tmp_path, fake_bin)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps({"image": "ubuntu:24.04", "postCreateCommand": "true"}),
        encoding="utf-8",
    )
    invoke(project, environment, "--use", "local", "--devcontainer")
    preference = next((tmp_path / "config" / "projects").glob("*.json"))
    saved = json.loads(preference.read_text(encoding="utf-8"))
    saved["backend"] = "colab-notebook"
    preference.write_text(json.dumps(saved), encoding="utf-8")

    result = invoke(project, environment, *arguments, check=False)

    assert result.returncode == 2
    assert "lifecycle-create (postCreateCommand)" in result.stdout
    assert engine_calls(log) == []


@pytest.mark.parametrize("operation", ["--status", "--stop", "--sync", "--fetch"])
def test_resource_recovery_operations_ignore_an_incompatible_saved_workstation(
    tmp_path: Path,
    fake_bin: Path,
    operation: str,
) -> None:
    project = make_project(tmp_path / f"recover-{operation[2:]}")
    environment, log = contract_environment(tmp_path, fake_bin)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps({"image": "ubuntu:24.04", "postCreateCommand": "true"}),
        encoding="utf-8",
    )
    invoke(project, environment, "--use", "local", "--devcontainer")
    preference = next((tmp_path / "config" / "projects").glob("*.json"))
    saved = json.loads(preference.read_text(encoding="utf-8"))
    saved["backend"] = "colab-notebook"
    preference.write_text(json.dumps(saved), encoding="utf-8")

    result = invoke(project, environment, operation)

    assert "cannot honor Dev Container" not in result.stdout
    assert len(engine_calls(log)) == 1
    assert operation[2:] in engine_calls(log)[0]


def test_devcontainer_requirements_are_not_satisfied_by_mixing_engines(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps({"image": "ubuntu:24.04", "forwardPorts": [8080]}),
        encoding="utf-8",
    )

    result = invoke(
        project, environment, "-b", "local", "--devcontainer", "build", check=False
    )

    assert result.returncode == 2
    assert "cannot honor the complete Dev Container requirement set" in result.stdout
    assert "adapter missing: image-tag (image)" in result.stdout
    assert "native missing: forward-ports (forwardPorts)" in result.stdout
    assert engine_calls(log) == []


def test_richer_local_devcontainer_executes_through_native_engine_and_records_provenance(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps({"image": "ubuntu:24.04", "postCreateCommand": "true"}),
        encoding="utf-8",
    )
    write_executable(
        fake_bin / "devcontainer",
        """#!/usr/bin/env python3
import json, os, sys
if sys.argv[1] == 'up':
    print(json.dumps({'outcome': 'success', 'containerId': 'native-container'}))
else:
    if 'command -v make >/dev/null' in sys.argv:
        raise SystemExit(0)
    for value in sys.argv:
        if value.startswith('cloudmake-target-submitted-'):
            print(value)
    print('native target output')
    raise SystemExit(int(os.environ.get('FAKE_NATIVE_TARGET_EXIT', '0')))
""",
    )
    write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env python3
import json, sys
if sys.argv[1] == 'inspect':
    print('sha256:native-image')
elif sys.argv[1:3] == ['image', 'inspect']:
    print(json.dumps(['ubuntu@sha256:' + 'a' * 64]))
""",
    )

    result = invoke(project, environment, "-b", "local", "--devcontainer", "build")

    assert "runner=devcontainer-native" in result.stdout
    assert "native target output" in result.stdout
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["runner"]["runtime"] == "devcontainer-cli"
    assert provenance["runner"]["resolved_digest"] == "ubuntu@sha256:" + "a" * 64
    assert provenance["runner"]["target_submission"] == "confirmed"
    assert provenance["runner"]["devcontainer"]["required_capabilities"] == [
        "image-tag", "lifecycle-create"
    ]
    assert provenance["runner"]["devcontainer"]["engine"] == "native"
    assert provenance["runner"]["devcontainer"]["requirement_sources"] == {
        "image-tag": ["image"],
        "lifecycle-create": ["postCreateCommand"],
    }
    assert "image-build" in provenance["runner"]["devcontainer"]["native_capabilities"]
    assert "image-digest" in provenance["runner"]["devcontainer"]["adapter_capabilities"]
    assert engine_calls(log) == []

    environment["FAKE_NATIVE_TARGET_EXIT"] = "7"
    failed = invoke(
        project, environment, "-b", "local", "--devcontainer", "build", check=False
    )
    assert failed.returncode == 7
    assert "native target output" in failed.stdout
    assert "target 'build' failed with exit status 7" in failed.stdout


def test_devcontainer_is_explicit_and_does_not_auto_activate(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps({"image": "registry.example/workstation@sha256:" + "f" * 64}),
        encoding="utf-8",
    )

    invoke(project, environment, "build")

    assert_assignment(engine_calls(log)[0], "CLOUDMAKE_RUNNER", "native")


def test_unqualified_deprecated_backend_rejects_devcontainer_before_dispatch(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps({"image": "registry.example/workstation@sha256:" + "a" * 64}),
        encoding="utf-8",
    )

    result = invoke(
        project, environment, "-b", "kaggle", "--devcontainer", "build", check=False
    )

    assert result.returncode == 2
    assert "does not declare Dev Container support" in result.stdout
    assert engine_calls(log) == []


def test_managed_gpu_devcontainer_requires_explicit_accelerator_selection(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps(
            {
                "image": "registry.example/workstation@sha256:" + "b" * 64,
                "hostRequirements": {"gpu": True},
                "customizations": {
                    "cloudmake": {"devices": ["nvidia.com/gpu=all"]}
                },
            }
        ),
        encoding="utf-8",
    )

    result = invoke(
        project, environment, "-b", "colab", "--devcontainer", "build", check=False
    )

    assert result.returncode == 2
    assert "requires --gpu" in result.stdout
    assert engine_calls(log) == []


def test_oci_selection_persists_and_is_encoded_for_later_targets(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    image = "registry.example/orfs@sha256:" + "a" * 64

    selected = invoke(
        project,
        environment,
        "--use",
        "ssh",
        "--host",
        "lab-gpu",
        "--image",
        image,
        "--device",
        "nvidia.com/gpu=all",
    )
    invoke(project, environment, "route")

    assert "runner=oci" in selected.stdout
    assert "subsequent targets reuse this selection" in selected.stdout
    call = engine_calls(log)[0]
    assert_assignment(call, "CLOUDMAKE_RUNNER", "oci")
    encoded_image = next(
        value.split("=", 1)[1]
        for value in call
        if value.startswith("CLOUDMAKE_OCI_IMAGE_B64=")
    )
    assert base64.urlsafe_b64decode(encoded_image).decode() == image
    encoded_devices = next(
        value.split("=", 1)[1]
        for value in call
        if value.startswith("CLOUDMAKE_OCI_DEVICES_B64=")
    )
    assert json.loads(base64.urlsafe_b64decode(encoded_devices).decode()) == [
        "nvidia.com/gpu=all"
    ]


def test_codespaces_selection_persists_native_image_and_uses_resource_lock(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    environment["CODESPACE"] = "stable-workstation"
    image = "registry.example/science/tools@sha256:" + "c" * 64

    selected = invoke(
        project,
        environment,
        "--use",
        "codespaces",
        "--image",
        image,
    )
    invoke(project, environment, "analyze")

    assert "codespace=stable-workstation" in selected.stdout
    assert "runner=oci" in selected.stdout
    assert "subsequent targets reuse this selection" in selected.stdout
    call = engine_calls(log)[0]
    assert_assignment(call, "BACKEND", "codespaces-ssh")
    lock = next(
        argument.split("=", 1)[1]
        for argument in call
        if argument.startswith("CLOUDMAKE_LOCK_FILE_OVERRIDE=")
    )
    assert lock == str(
        tmp_path
        / "state"
        / "resource-locks"
        / "codespaces-ssh"
        / "stable-workstation.lock"
    )
    encoded_image = next(
        argument.split("=", 1)[1]
        for argument in call
        if argument.startswith("CLOUDMAKE_OCI_IMAGE_B64=")
    )
    assert base64.urlsafe_b64decode(encoded_image).decode() == image
    assert_remote_target(call, "analyze")


def test_codespaces_devcontainer_is_selected_as_provider_native_realization(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    environment["CODESPACE"] = "stable-workstation"
    configuration = project / ".devcontainer" / "devcontainer.json"
    configuration.parent.mkdir()
    configuration.write_text(
        json.dumps(
            {
                "image": "registry.example/science/tools@sha256:" + "c" * 64,
                "remoteEnv": {"MODE": "native"},
            }
        ),
        encoding="utf-8",
    )

    invoke(project, environment, "--use", "codespaces", "--devcontainer")
    invoke(project, environment, "analyze")

    call = engine_calls(log)[0]
    assert_assignment(call, "CLOUDMAKE_RUNNER", "oci")
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["runner"]["devcontainer"]["engine"] == "native"
    assert provenance["runner"]["devcontainer"]["adapter_capabilities"] == []
    assert "image-digest" in provenance["runner"]["devcontainer"]["native_capabilities"]


def test_native_clears_saved_oci_runner_without_changing_make_surface(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    image = "registry.example/build@sha256:" + "b" * 64
    invoke(project, environment, "--use", "colab", "--image", image)

    selected = invoke(project, environment, "--native", "verify")
    again = invoke(project, environment, "verify")

    assert "selected runner=native" in selected.stdout
    for call in engine_calls(log):
        assert_assignment(call, "CLOUDMAKE_RUNNER", "native")
        assert not any(value.startswith("CLOUDMAKE_OCI_IMAGE_B64=") for value in call)
    assert "selected runner" not in again.stdout


@pytest.mark.parametrize(
    "arguments",
    [
        ("--image", "registry.example/build:latest", "build"),
        (
            "--image",
            "registry.example/build@sha256:" + "a" * 64,
            "--device",
            "gpu",
            "build",
        ),
        ("--device", "nvidia.com/gpu=all", "build"),
    ],
)
def test_invalid_oci_selection_is_rejected_before_provider_contact(
    tmp_path: Path, fake_bin: Path, arguments: tuple[str, ...]
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, *arguments, check=False)

    assert result.returncode == 2
    assert engine_calls(log) == []


def test_colab_crun_profile_accepts_cdi_for_remote_preflight(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    image = "registry.example/build@sha256:" + "c" * 64

    result = invoke(
        project,
        environment,
        "-b",
        "colab",
        "--image",
        image,
        "--device",
        "nvidia.com/gpu=all",
        "build",
        check=False,
    )

    assert result.returncode == 0
    calls = engine_calls(log)
    assert len(calls) == 1
    assert "CLOUDMAKE_OCI_RUNTIMES_B64=" in " ".join(calls[0])


def test_colab_crun_profile_accepts_saved_gpu_selection(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    image = "registry.example/build@sha256:" + "d" * 64
    invoke(project, environment, "--use", "colab", "--gpu=T4")

    result = invoke(project, environment, "--image", image, "build", check=False)

    assert result.returncode == 0
    assert len(engine_calls(log)) == 1


def test_backends_reports_persistence_mode_for_every_backend(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "--backends")

    assert "STATUS" in result.stdout
    assert "LIFECYCLE" in result.stdout
    assert "WORKSPACE" in result.stdout
    assert "PERSISTENCE" in result.stdout
    assert "OCI RUNTIMES" in result.stdout
    assert "INTERNET IN" in result.stdout
    assert "INTERNET OUT" in result.stdout
    assert "DEVCONTAINER" in result.stdout
    assert "PORTS" in result.stdout
    rows = {
        line.split()[0]: line.split()
        for line in result.stdout.splitlines()[1:]
        if line.split()
    }
    assert rows["colab-notebook"][1] == "supported"
    assert rows["colab-notebook"][3] == "provider-managed"
    assert rows["colab-notebook"][4] == "ephemeral"
    assert rows["colab-notebook"][5] == "no"
    assert rows["colab-notebook"][6] == "checkpoint"
    assert "crun" in rows["colab-notebook"]
    assert "no" in rows["colab-notebook"]
    assert "yes" in rows["colab-notebook"]
    assert rows["kaggle-notebook"][1] == "deprecated"
    assert rows["kaggle-notebook"][2] == "no"
    assert rows["kaggle-notebook"][3] == "per-target"
    assert rows["kaggle-notebook"][4] == "ephemeral"
    assert rows["kaggle-notebook"][5] == "no"
    assert rows["kaggle-notebook"][6] == "checkpoint"
    assert "proot" in rows["kaggle-notebook"]
    assert "conditional" in rows["kaggle-notebook"]
    assert rows["colab-ssh"][1] == "deprecated"
    assert rows["lightning-studio-ssh"][1] == "unqualified"
    assert rows["gcp-compute-ssh"][1] == "supported"
    assert rows["codespaces-ssh"][5] == "yes"
    assert "provider-native" in rows["codespaces-ssh"]
    assert "docker,podman,nerdctl,proot" in rows["host-ssh"]
    for backend in (
        "local",
        "codespaces-ssh",
        "host-ssh",
        "lightning-studio-ssh",
    ):
        assert rows[backend][6] == "native"
    assert rows["codespaces-ssh"][3:5] == ["provider-managed", "stop-persistent"]
    assert rows["host-ssh"][3:5] == ["externally-managed", "host-persistent"]
    assert rows["colab-ssh"][6] == "unsupported"
    assert rows["modal-sandbox"][1:7] == [
        "supported", "yes", "provider-managed", "ephemeral", "no", "native",
    ]
    for backend in (
        "local",
        "colab-notebook",
        "codespaces-ssh",
        "colab-ssh",
        "host-ssh",
        "lightning-studio-ssh",
    ):
        assert rows[backend][-5] == "yes"
    assert rows["local"][-4] == "adapter+native"
    assert rows["colab-notebook"][-4] == "adapter"
    assert rows["codespaces-ssh"][-4] == "native"
    assert "loopback" in rows["host-ssh"]
    assert "loopback" in rows["codespaces-ssh"]
    assert "loopback" in rows["local"]
    assert "no" in rows["colab-notebook"][-3:-1]
    assert engine_calls(log) == []


def test_deprecated_backend_warns_without_changing_dispatch(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "-b", "kaggle", "build")

    assert "backend=kaggle-notebook status=deprecated" in result.stdout
    assert "fresh VM and full checkpoint materialization" in result.stdout
    assert_assignment(engine_calls(log)[0], "BACKEND", "kaggle-notebook")


@pytest.mark.parametrize(
    ("backend", "canonical", "status", "reason"),
    [
        ("colab-ssh", "colab-ssh", "deprecated", "duplicates host SSH"),
        (
            "lightning",
            "lightning-studio-ssh",
            "unqualified",
            "Lightning Studio has no retained",
        ),
    ],
)
def test_non_supported_backend_warns_without_changing_dispatch(
    tmp_path: Path,
    fake_bin: Path,
    backend: str,
    canonical: str,
    status: str,
    reason: str,
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "-b", backend, "build")

    assert f"backend={canonical} status={status}" in result.stdout
    assert reason in result.stdout
    assert_assignment(engine_calls(log)[0], "BACKEND", canonical)


@pytest.mark.parametrize(
    ("duration", "seconds"),
    [("30s", "30"), ("15m", "900"), ("2h", "7200")],
)
def test_retry_duration_is_passed_to_colab_allocation_only(
    tmp_path: Path,
    fake_bin: Path,
    duration: str,
    seconds: str,
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(
        project,
        environment,
        "-b",
        "colab",
        f"--retry-for={duration}",
        "--start",
    )

    assert_assignment(engine_calls(log)[0], "CLOUDMAKE_RETRY_FOR_SECONDS", seconds)


@pytest.mark.parametrize(
    "arguments",
    [("--version",), ("--backends",), ("--history",), ("--host-templates",)],
)
def test_information_commands_do_not_invoke_build_engine(
    tmp_path: Path, fake_bin: Path, arguments: tuple[str, ...]
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, *arguments)

    assert result.stdout.strip()
    assert engine_calls(log) == []


def test_installed_host_templates_are_listed_and_rendered_without_engine_use(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    listed = invoke(project, environment, "--host-templates")
    rendered = invoke(
        project, environment, "--host-template", "oci-always-free"
    )

    assert "generic" in listed.stdout
    assert "oci-always-free" in listed.stdout
    assert "gcp-e2-micro" in listed.stdout
    assert "Host oci-free" in rendered.stdout
    assert "StrictHostKeyChecking no" not in rendered.stdout
    assert "PRIVATE KEY-----" not in rendered.stdout
    assert engine_calls(log) == []


def test_unknown_host_template_fails_without_engine_use(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(
        project, environment, "--host-template", "unknown", check=False
    )

    assert result.returncode == 2
    assert "unknown SSH host template" in result.stdout
    assert engine_calls(log) == []


def test_doctor_invokes_read_only_backend_readiness_gate(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(project, environment, "-b", "colab", "--doctor")

    call = engine_calls(log)[0]
    assert_assignment(call, "BACKEND", "colab-notebook")
    assert "doctor" in call
    assert "dispatch" not in call
    assert not any(argument.startswith("REMOTE_TARGET_B64=") for argument in call)


def test_colab_default_session_is_stable_and_distinct_per_project(
    tmp_path: Path, fake_bin: Path
) -> None:
    first_project = make_project(tmp_path / "first project")
    second_project = make_project(tmp_path / "second project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(first_project, environment, "-b", "colab", "build")
    invoke(first_project, environment, "-b", "colab", "test")
    invoke(second_project, environment, "-b", "colab", "build")

    sessions = [
        next(argument.split("=", 1)[1] for argument in call if argument.startswith("COLAB_SESSION="))
        for call in engine_calls(log)
    ]
    assert sessions[0] == sessions[1]
    assert sessions[0].startswith("first-project-")
    assert sessions[2].startswith("second-project-")
    assert sessions[0] != sessions[2]


def test_explicit_colab_session_is_preserved_exactly(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    environment["COLAB_SESSION"] = "shared-lab"

    invoke(project, environment, "-b", "colab", "build")

    assert_assignment(engine_calls(log)[0], "COLAB_SESSION", "shared-lab")


def test_existing_v09_cuda_build_state_retains_legacy_default_session(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "-b", "colab", "build")
    first = engine_calls(log)[0]
    state_root = Path(
        next(
            argument.split("=", 1)[1]
            for argument in first
            if argument.startswith("CLOUDMAKE_STATE_ROOT=")
        )
    )
    (state_root / "colab-notebook" / "cuda-build").mkdir(parents=True)
    log.write_text("", encoding="utf-8")

    result = invoke(project, environment, "-b", "colab", "test")

    assert "Continuing with legacy session cuda-build" in result.stdout
    assert "COLAB_SESSION=NAME cloudmake --use colab" in result.stdout
    assert_assignment(engine_calls(log)[0], "COLAB_SESSION", "cuda-build")

    environment["COLAB_SESSION"] = "migrated-project-session"
    selected = invoke(project, environment, "--use", "colab")
    assert "session=migrated-project-session" in selected.stdout
    del environment["COLAB_SESSION"]
    log.write_text("", encoding="utf-8")

    invoke(project, environment, "-b", "colab", "build")

    assert_assignment(
        engine_calls(log)[0], "COLAB_SESSION", "migrated-project-session"
    )


def test_use_persists_canonical_backend_outside_project(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    before = {path.relative_to(project) for path in project.rglob("*")}
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(project, environment, "--use", "colab", "--gpu=T4")
    invoke(project, environment, "build")

    after = {path.relative_to(project) for path in project.rglob("*")}
    assert after == before
    assert len(engine_calls(log)) == 1
    call = engine_calls(log)[0]
    assert_assignment(call, "PROJECT_DIR", str(project.resolve()))
    assert_assignment(call, "BACKEND", "colab-notebook")
    assert_assignment(call, "COLAB_GPU", "T4")
    assert_remote_target(call, "build")
    assert "dispatch" in call


def test_checkpoint_is_disabled_by_default_and_project_arguments_are_unchanged(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(project, environment, "-b", "colab", "build", "MODE=fast")

    call = engine_calls(log)[0]
    assert_assignment(call, "CLOUDMAKE_CHECKPOINT", "0")
    assert_remote_target(call, "build")
    assert project_arguments(call) == ["MODE=fast"]
    assert not (tmp_path / "config" / "projects").exists()


def test_repository_configuration_cannot_enable_persistent_workspace(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    (project / ".cloudmake.json").write_text(
        json.dumps({"backend": "colab", "persistent_workspace": True}),
        encoding="utf-8",
    )
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(project, environment, "build")

    call = engine_calls(log)[0]
    assert_assignment(call, "BACKEND", "colab-notebook")
    assert_assignment(call, "CLOUDMAKE_CHECKPOINT", "0")


def test_global_configuration_cannot_enable_persistent_workspace(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(
        project,
        environment,
        "--global",
        "--use",
        "colab",
        "--persist",
        check=False,
    )

    assert result.returncode == 2
    assert "must be enabled or disabled per project" in result.stdout
    assert engine_calls(log) == []


def test_checkpoint_selection_is_persisted_outside_project_and_passed_to_engine(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    before = {path.relative_to(project) for path in project.rglob("*")}
    environment, log = contract_environment(tmp_path, fake_bin)

    selected = invoke(project, environment, "--use", "colab", "--checkpoint")
    invoke(project, environment, "build")

    assert "persistent-workspace=enabled" in selected.stdout
    call = engine_calls(log)[0]
    assert_assignment(call, "BACKEND", "colab-notebook")
    assert_assignment(call, "CLOUDMAKE_CHECKPOINT", "1")
    project_key_value = next(
        value.split("=", 1)[1]
        for value in call
        if value.startswith("CLOUDMAKE_PROJECT_KEY=")
    )
    assert len(project_key_value) == 24
    preference = next((tmp_path / "config" / "projects").glob("*.json"))
    saved = json.loads(preference.read_text(encoding="utf-8"))
    assert saved["persistent_workspace"] is True
    assert saved["checkpoint"] is True
    assert {path.relative_to(project) for path in project.rglob("*")} == before


def test_no_checkpoint_disables_persisted_selection(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "--use", "colab", "--checkpoint")

    disabled = invoke(project, environment, "--no-checkpoint", "build")
    invoke(project, environment, "test")

    assert "persistent-workspace=disabled" in disabled.stdout
    for call in engine_calls(log):
        assert_assignment(call, "CLOUDMAKE_CHECKPOINT", "0")


def test_persistence_is_remembered_per_backend(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "--use", "colab", "--persist")

    invoke(project, environment, "-b", "kaggle", "--no-persist", "build")
    invoke(project, environment, "test")
    invoke(project, environment, "-b", "kaggle", "test")

    selected = [
        sorted(argument for argument in call if argument.startswith(("BACKEND=", "CLOUDMAKE_CHECKPOINT=")))
        for call in engine_calls(log)
    ]
    assert selected == [
        ["BACKEND=kaggle-notebook", "CLOUDMAKE_CHECKPOINT=0"],
        ["BACKEND=colab-notebook", "CLOUDMAKE_CHECKPOINT=1"],
        ["BACKEND=kaggle-notebook", "CLOUDMAKE_CHECKPOINT=0"],
    ]
    preference = next((tmp_path / "config" / "projects").glob("*.json"))
    saved = json.loads(preference.read_text(encoding="utf-8"))
    assert saved["persistent_workspaces"] == {"colab-notebook": True, "kaggle-notebook": False}


def test_persist_alias_registers_and_lists_a_persistent_workspace(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    selected = invoke(project, environment, "--use", "colab", "--persist")
    listed = invoke(project, environment, "--workspaces")

    assert "persistent-workspace=enabled" in selected.stdout
    record = next((tmp_path / "state" / "workspaces").glob("*.json"))
    metadata = json.loads(record.read_text(encoding="utf-8"))
    assert metadata["workspace_id"] in listed.stdout
    assert metadata["backend"] == "colab-notebook"
    assert metadata["project_name"] == "project"
    assert metadata["session"].startswith("project-")
    assert metadata["session"] != "cuda-build"
    assert engine_calls(log) == []


@pytest.mark.parametrize("legacy_record", [False, True])
def test_workspace_attach_decouples_durable_identity_from_project_path(
    tmp_path: Path, fake_bin: Path, legacy_record: bool
) -> None:
    first = make_project(tmp_path / "first")
    second = make_project(tmp_path / "second")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(first, environment, "--use", "colab", "--gpu=T4", "--persist")
    record = next((tmp_path / "state" / "workspaces").glob("*.json"))
    metadata = json.loads(record.read_text(encoding="utf-8"))
    workspace_id = metadata["workspace_id"]
    previous_project_id = metadata["project_id"]
    if legacy_record:
        metadata.pop("project_id")
        record.write_text(json.dumps(metadata), encoding="utf-8")
    invoke(second, environment, "--use", "local")

    attached = invoke(second, environment, "--workspace", "attach", workspace_id)
    invoke(second, environment, "build")

    assert f"persistent-workspace={workspace_id}" in attached.stdout
    call = engine_calls(log)[0]
    assert_assignment(call, "CLOUDMAKE_WORKSPACE_ID", workspace_id)
    assert_assignment(call, "CLOUDMAKE_ADOPT", "1")
    assert_assignment(call, "CLOUDMAKE_ADOPT_FROM", previous_project_id)
    assert_assignment(call, "COLAB_GPU", "T4")
    configs = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (tmp_path / "config" / "projects").glob("*.json")
    ]
    old = next(config for config in configs if config.get("project") == str(first))
    assert old["persistent_workspace"] is False
    assert "workspace_id" not in old


@pytest.mark.parametrize(
    ("backend", "arguments"),
    [
        ("modal", ()),
        ("kaggle", ()),
        ("ssh", ("--host", "lab-gpu")),
    ],
)
def test_ordinary_run_does_not_adopt_workspace(
    tmp_path: Path, fake_bin: Path, backend: str, arguments: tuple[str, ...]
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(project, environment, "-b", backend, *arguments, "build")

    assert not any(
        argument.startswith("CLOUDMAKE_ADOPT=")
        for argument in engine_calls(log)[0]
    )


def test_workspace_purge_requires_force_and_detaches_local_metadata(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "--use", "colab", "--persist")
    record = next((tmp_path / "state" / "workspaces").glob("*.json"))
    metadata = json.loads(record.read_text(encoding="utf-8"))
    workspace_id = metadata["workspace_id"]

    refused = invoke(
        project, environment, "--workspace", "purge", workspace_id, check=False
    )
    assert refused.returncode == 2
    assert "--force" in refused.stdout
    assert record.exists()
    assert engine_calls(log) == []

    purged = invoke(
        project,
        environment,
        "--force",
        "--workspace",
        "purge",
        workspace_id,
    )
    assert "purged and detached" in purged.stdout
    call = engine_calls(log)[0]
    assert "workspace-purge" in call
    assert_assignment(call, "CLOUDMAKE_WORKSPACE_ID", workspace_id)
    assert_assignment(call, "CLOUDMAKE_CHECKPOINT", "1")
    assert_assignment(call, "COLAB_SESSION", metadata["session"])
    assert not record.exists()
    config = next((tmp_path / "config" / "projects").glob("*.json"))
    saved = json.loads(config.read_text(encoding="utf-8"))
    assert saved["persistent_workspace"] is False
    assert "workspace_id" not in saved


def test_persistence_is_rejected_for_colab_ssh_before_dispatch(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(
        project, environment, "--use", "colab-ssh", "--persist", check=False
    )

    assert result.returncode == 2
    assert "does not support persistent workspaces" in result.stdout
    assert "--no-persist" in result.stdout
    assert engine_calls(log) == []


@pytest.mark.parametrize(
    ("backend", "selection"),
    [
        ("local", ("--use", "local", "--persist")),
        ("codespaces-ssh", ("--use", "codespaces", "--persist")),
        ("host-ssh", ("--use", "ssh", "--host", "lab-gpu", "--persist")),
        ("lightning-studio-ssh", ("--use", "lightning", "--persist")),
    ],
)
def test_native_persistence_preserves_target_dispatch_without_checkpoint_transfer(
    tmp_path: Path,
    fake_bin: Path,
    backend: str,
    selection: tuple[str, ...],
) -> None:
    project = make_project(tmp_path / f"project-{backend}")
    environment, log = contract_environment(tmp_path, fake_bin)

    selected = invoke(project, environment, *selection)
    result = invoke(project, environment, "build", "MODE=fast")

    assert "persistent-workspace=enabled mode=native" in selected.stdout
    assert f"persistence=native backend={backend} checkpoint-transfer=no" in result.stdout
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["persistent_workspace"] == {
        "enabled": True,
        "mode": "native",
        "workspace_id": None,
    }
    assert provenance["checkpoint"] == {"enabled": False}
    call = engine_calls(log)[0]
    if backend != "local":
        assert_assignment(call, "CLOUDMAKE_CHECKPOINT", "0")
        assert project_arguments(call) == ["MODE=fast"]


def test_legacy_checkpoint_alias_selects_native_persistence_without_intrusion(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "local-alias")
    environment, _ = contract_environment(tmp_path, fake_bin)

    selected = invoke(project, environment, "--use", "local", "--checkpoint")
    result = invoke(project, environment, "test")

    assert "persistent-workspace=enabled mode=native" in selected.stdout
    assert "persistence=native backend=local checkpoint-transfer=no" in result.stdout


@pytest.mark.parametrize(
    ("backend", "extra"),
    [
        ("local", ()),
        ("colab", ()),
        ("kaggle", ()),
        ("codespaces", ()),
        ("colab-ssh", ()),
        ("ssh", ("--host", "lab-gpu")),
        ("lightning", ()),
    ],
)
def test_persistence_remains_disabled_by_default_for_every_backend(
    tmp_path: Path,
    fake_bin: Path,
    backend: str,
    extra: tuple[str, ...],
) -> None:
    project = make_project(tmp_path / f"default-{backend}")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "-b", backend, *extra, "build")

    assert "persistence=" not in result.stdout
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["persistent_workspace"] == {
        "enabled": False,
        "mode": "disabled",
        "workspace_id": None,
    }
    assert provenance["checkpoint"] == {"enabled": False}
    call = engine_calls(log)[0]
    if backend != "local":
        assert_assignment(call, "CLOUDMAKE_CHECKPOINT", "0")


def test_switch_between_checkpoint_backends_keeps_distinct_workspace_identities(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "switch-backend")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "--use", "colab", "--persist")
    config = next((tmp_path / "config" / "projects").glob("*.json"))
    colab_workspace = json.loads(config.read_text())["workspace_id"]

    selected = invoke(project, environment, "--use", "kaggle")
    kaggle_config = json.loads(config.read_text())
    kaggle_workspace = kaggle_config["workspace_id"]
    invoke(project, environment, "build")

    assert "persistent-workspace=enabled mode=checkpoint" in selected.stdout
    assert kaggle_workspace != colab_workspace
    assert kaggle_config["workspace_ids"] == {
        "colab-notebook": colab_workspace,
        "kaggle-notebook": kaggle_workspace,
    }
    call = engine_calls(log)[0]
    assert_assignment(call, "BACKEND", "kaggle-notebook")
    assert_assignment(call, "CLOUDMAKE_CHECKPOINT", "1")
    assert_assignment(call, "CLOUDMAKE_WORKSPACE_ID", kaggle_workspace)

    invoke(project, environment, "--use", "colab")
    restored = json.loads(config.read_text())
    assert restored["workspace_id"] == colab_workspace


def test_use_ssh_persists_the_host_alias_outside_the_project(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    before = {path.relative_to(project) for path in project.rglob("*")}
    environment, log = contract_environment(tmp_path, fake_bin)

    saved = invoke(project, environment, "--use", "ssh", "--host", "lab-gpu")
    invoke(project, environment, "benchmark")

    assert "backend=host-ssh, host=lab-gpu" in saved.stdout
    call = engine_calls(log)[0]
    assert_assignment(call, "BACKEND", "host-ssh")
    assert_assignment(call, "SSH_HOST", "lab-gpu")
    assert_remote_target(call, "benchmark")
    assert {path.relative_to(project) for path in project.rglob("*")} == before
    preference = next((tmp_path / "config" / "projects").glob("*.json"))
    assert json.loads(preference.read_text(encoding="utf-8"))["host"] == "lab-gpu"


def test_one_off_ssh_host_does_not_replace_the_saved_host(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "--use", "ssh", "--host", "lab-gpu")

    invoke(project, environment, "--host", "oci-free", "build")
    invoke(project, environment, "test")

    first, second = engine_calls(log)
    assert_assignment(first, "SSH_HOST", "oci-free")
    assert_assignment(second, "SSH_HOST", "lab-gpu")


@pytest.mark.parametrize(
    "arguments",
    [
        ("--use", "ssh"),
        ("-b", "ssh", "build"),
    ],
)
def test_ssh_backend_requires_an_explicit_or_saved_host(
    tmp_path: Path, fake_bin: Path, arguments: tuple[str, ...]
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, *arguments, check=False)

    assert result.returncode == 2
    assert "requires --host HOST" in result.stdout
    assert engine_calls(log) == []


def test_host_option_is_rejected_for_provider_owned_backends(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(
        project, environment, "-b", "codespaces", "--host", "lab-gpu", "build", check=False
    )

    assert result.returncode == 2
    assert "valid only with the ssh backend" in result.stdout
    assert engine_calls(log) == []


@pytest.mark.parametrize(
    "host", ["researcher@lab.example --bad-option", "-V"]
)
def test_ssh_host_alias_must_be_safe_before_engine_dispatch(
    tmp_path: Path, fake_bin: Path, host: str
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(
        project,
        environment,
        "-b",
        "ssh",
        "--host",
        host,
        "build",
        check=False,
    )

    assert result.returncode == 2
    assert "SSH host aliases must begin with" in result.stdout
    assert engine_calls(log) == []


def test_repository_configuration_cannot_choose_a_local_ssh_host(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    (project / ".cloudmake.json").write_text(
        json.dumps({"backend": "ssh", "host": "repo-controlled-host"}),
        encoding="utf-8",
    )
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "build", check=False)

    assert result.returncode == 2
    assert "requires --host HOST" in result.stdout
    assert engine_calls(log) == []


@pytest.mark.parametrize(
    ("alias", "canonical", "extra"),
    [
        ("local", "local", ()),
        ("colab", "colab-notebook", ()),
        ("kaggle", "kaggle-notebook", ()),
        ("codespaces", "codespaces-ssh", ()),
        ("colab-ssh", "colab-ssh", ()),
        ("ssh", "host-ssh", ("--host", "lab-gpu")),
        ("lightning", "lightning-studio-ssh", ()),
        ("modal", "modal-sandbox", ()),
    ],
)
def test_backend_aliases_have_unambiguous_canonical_names(
    tmp_path: Path,
    fake_bin: Path,
    alias: str,
    canonical: str,
    extra: tuple[str, ...],
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(project, environment, "-b", alias, *extra, "test")

    call = engine_calls(log)[0]
    if canonical == "local":
        assert call == ["-f", "Makefile", "test"]
    else:
        assert_assignment(call, "BACKEND", canonical)


def test_local_backend_is_a_direct_make_passthrough_with_provenance(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(
        project,
        environment,
        "-b",
        "local",
        "-j",
        "3",
        "benchmark",
        "SIZE=large",
        "DEBUG=1",
    )

    assert engine_calls(log) == [
        ["-f", "Makefile", "-j", "3", "benchmark", "SIZE=large", "DEBUG=1"]
    ]
    assert "[cloudmake] backend=local resource=local" in result.stdout
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    record = json.loads(latest.read_text(encoding="utf-8"))
    assert record["backend"] == "local"
    assert record["resource_id"] == str(project.resolve())
    assert record["source"] == {"mode": "local-working-tree"}
    assert record["assignments"][0]["name"] == "SIZE"
    assert record["target_submission"] == "submitted"
    assert record["target_semantic"] == {
        "idempotency": "unknown",
        "scope": "invocation",
    }
    assert record["delivery_policy"] == {"mode": "at-most-once", "max_attempts": 1}
    assert record["attempts"][0]["outcome"] == "succeeded"
    assert record["retry_safe"] is False
    assert record["replay_safe"] is False
    assert "large" not in latest.read_text(encoding="utf-8")


def test_idempotency_assertion_is_invocation_only_and_never_replays_failure(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    environment["CLOUDMAKE_TEST_ENGINE_EXIT"] = "7"

    result = invoke(
        project,
        environment,
        "-b",
        "local",
        "--idempotent",
        "release candidate's [gpu]",
        "MESSAGE=hello world",
        "EMPTY=",
        check=False,
    )

    assert result.returncode == 7
    assert engine_calls(log) == [
        [
            "-f",
            "Makefile",
            "release candidate's [gpu]",
            "MESSAGE=hello world",
            "EMPTY=",
        ]
    ]
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    record = json.loads(latest.read_text(encoding="utf-8"))
    assert record["target_semantic"] == {
        "idempotency": "asserted",
        "scope": "invocation",
    }
    assert record["delivery_policy"] == {"mode": "at-most-once", "max_attempts": 1}
    assert len(record["attempts"]) == 1
    assert record["attempts"][0]["target_submission"] == "submitted"
    assert record["attempts"][0]["outcome"] == "target_failed"
    assert record["attempts"][0]["exit_code"] == 7
    assert record["replay_safe"] is False


def test_project_configuration_cannot_self_declare_target_idempotency(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    (project / ".cloudmake.json").write_text(
        json.dumps(
            {
                "backend": "local",
                "idempotent": True,
                "delivery_policy": "bounded-replay",
            }
        ),
        encoding="utf-8",
    )
    environment, _ = contract_environment(tmp_path, fake_bin)

    invoke(project, environment, "build")

    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    record = json.loads(latest.read_text(encoding="utf-8"))
    assert record["target_semantic"]["idempotency"] == "unknown"
    assert record["delivery_policy"] == {"mode": "at-most-once", "max_attempts": 1}


def test_local_oci_runner_preflights_then_submits_target_once(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, engine_log = contract_environment(tmp_path, fake_bin)
    runtime_log = tmp_path / "oci-runtime.jsonl"
    write_executable(
        fake_bin / "docker",
        "#!/bin/sh\nexit 1\n",
    )
    write_executable(
        fake_bin / "podman",
        r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import platform
import sys
with Path(os.environ["OCI_RUNTIME_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1:3] == ["image", "inspect"]:
    architecture = {"x86_64": "amd64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine(), platform.machine())
    print(json.dumps([{"RepoDigests": [sys.argv[-1]], "Os": "linux", "Architecture": architecture}]))
''',
    )
    environment["OCI_RUNTIME_LOG"] = str(runtime_log)
    image = "registry.example/build@sha256:" + "d" * 64

    result = invoke(
        project,
        environment,
        "-b",
        "local",
        "--image",
        image,
        "-j2",
        "benchmark",
        "SIZE=small",
    )

    calls = [
        json.loads(line) for line in runtime_log.read_text(encoding="utf-8").splitlines()
    ]
    assert calls[0] == ["info"]
    assert calls[1] == ["pull", image]
    assert calls[2] == ["image", "inspect", image]
    submissions = [call for call in calls if call and call[0] == "run"]
    assert len(submissions) == 2
    assert submissions[0][-1] == "--version"
    assert submissions[1][-6:] == [
        "-f",
        "Makefile",
        "SIZE=small",
        "-j2",
        "--",
        "benchmark",
    ]
    assert engine_calls(engine_log) == []
    assert "backend=local runner=oci image=" in result.stdout
    assert "runner=oci runtime=podman" in result.stdout
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["runner"]["kind"] == "oci"
    assert provenance["runner"]["runtime"] == "podman"
    assert provenance["runner"]["image"] == image


def test_explicit_gpu_selection_persists_and_is_reused_by_later_targets(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    selected = invoke(project, environment, "--use", "colab")

    first = invoke(project, environment, "--gpu=T4", "bootstrap")
    second = invoke(project, environment, "verify")

    assert "subsequent targets reuse this selection" in selected.stdout
    assert "selected accelerator=T4" in first.stdout
    assert "subsequent targets reuse this selection" in first.stdout
    assert "selected accelerator" not in second.stdout
    first_call, second_call = engine_calls(log)
    for call in (first_call, second_call):
        assert_assignment(call, "BACKEND", "colab-notebook")
        assert_assignment(call, "COLAB_GPU", "T4")
        assert_assignment(call, "CLOUDMAKE_CONTEXT_ACCELERATOR", "T4")
    preference = next((tmp_path / "config" / "projects").glob("*.json"))
    assert json.loads(preference.read_text(encoding="utf-8"))["accelerator"] == "T4"


def test_colab_tpu_selection_persists_and_is_forwarded(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "--use", "colab", "--tpu=v5e1")

    invoke(project, environment, "build")

    call = engine_calls(log)[0]
    assert_assignment(call, "COLAB_TPU", "v5e1")
    assert_assignment(call, "COLAB_GPU", "")
    assert_assignment(call, "CLOUDMAKE_CONTEXT_ACCELERATOR", "tpu-v5e1")


@pytest.mark.parametrize(
    "arguments",
    [
        ("--tpu=v5e1", "--gpu=T4"),
        ("--gpu=T4", "--tpu=v5e1"),
        ("--tpu=v5e1", "--cpu"),
        ("--tpu=invalid",),
    ],
)
def test_invalid_tpu_selection_is_rejected(
    tmp_path: Path, fake_bin: Path, arguments: tuple[str, ...]
) -> None:
    project = make_project(tmp_path / "project")
    environment, _ = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, *arguments, "build", check=False)

    assert result.returncode != 0


def test_tpu_selection_requires_colab(tmp_path: Path, fake_bin: Path) -> None:
    project = make_project(tmp_path / "project")
    environment, _ = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "-b", "modal", "--tpu=v5e1", "build", check=False)

    assert result.returncode != 0
    assert "only by the Colab notebook backend" in result.stdout


def test_saved_cloud_accelerator_does_not_leak_into_local_context(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, _ = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "--use", "colab", "--gpu=T4")
    invoke(project, environment, "--use", "local")

    result = invoke(project, environment, "verify")

    assert "[cloudmake] backend=local resource=local" in result.stdout
    assert "accelerator=" not in result.stdout


def test_local_collect_runs_target_and_transactionally_materializes_artifacts(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "Makefile").write_text(
        ".PHONY: export-release fail\n"
        "export-release:\n\t@mkdir -p dist\n\t@printf fresh > dist/result\n"
        "fail:\n\t@false\n",
        encoding="utf-8",
    )
    artifacts = project / ".cloudmake" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "previous").write_text("keep", encoding="utf-8")
    project_artifacts = project / "artifacts"
    project_artifacts.mkdir()
    (project_artifacts / "owned-by-project").write_bytes(b"project artifact\x00\xff")
    environment = {
        "CLOUDMAKE_CONFIG_HOME": str(tmp_path / "config"),
        "CLOUDMAKE_STATE_HOME": str(tmp_path / "state"),
        "CLOUDMAKE_CACHE_HOME": str(tmp_path / "cache"),
    }

    invoke(
        project,
        environment,
        "-b",
        "local",
        "--collect",
        "dist",
        "export-release",
    )

    assert (artifacts / "result").read_text(encoding="utf-8") == "fresh"
    assert not (artifacts / "previous").exists()
    assert (project_artifacts / "owned-by-project").read_bytes() == b"project artifact\x00\xff"
    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    provenance = json.loads(latest.read_text(encoding="utf-8"))
    assert provenance["artifacts"]["path"] == str(artifacts)

    (artifacts / "keep").write_text("keep", encoding="utf-8")
    failed = invoke(
        project,
        environment,
        "-b",
        "local",
        "--collect",
        "dist",
        "fail",
        check=False,
    )
    assert failed.returncode != 0
    assert (artifacts / "keep").read_text(encoding="utf-8") == "keep"
    assert (project_artifacts / "owned-by-project").read_bytes() == b"project artifact\x00\xff"


def test_known_legacy_collection_is_not_uploaded_without_migration(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "-b", "local", "build")
    log.unlink(missing_ok=True)
    legacy = project / "artifacts"
    legacy.mkdir()
    content = b"private prior output\n"
    (legacy / "report.txt").write_bytes(content)
    digest = hashlib.sha256()
    relative = b"report.txt"
    digest.update(len(relative).to_bytes(8, "big") + relative)
    digest.update(content)
    runs = next((tmp_path / "state" / "projects").glob("*/runs"))
    (runs / "legacy.json").write_text(
        json.dumps(
            {
                "artifacts": {
                    "path": str(legacy),
                    "fingerprint": digest.hexdigest(),
                    "files": 1,
                    "total_bytes": len(content),
                }
            }
        ),
        encoding="utf-8",
    )

    blocked = invoke(project, environment, "-b", "colab", "build", check=False)

    assert blocked.returncode == 2
    assert "exactly matches prior Cloudmake collection provenance" in blocked.stdout
    assert "may contain private downloaded output" in blocked.stdout
    assert "add /artifacts/ to .cloudmakeignore" in blocked.stdout
    assert engine_calls(log) == []

    (project / ".cloudmakeignore").write_text("/artifacts/\n", encoding="utf-8")
    invoke(project, environment, "-b", "colab", "build")
    assert len(engine_calls(log)) == 1


@pytest.mark.parametrize(
    "arguments",
    [("--collect", "dist", "build"), ("--fetch",)],
)
def test_artifact_materialization_rejects_a_symlinked_reserved_namespace(
    tmp_path: Path, fake_bin: Path, arguments: tuple[str, ...]
) -> None:
    project = make_project(tmp_path / "project")
    outside = tmp_path / "outside"
    outside.mkdir()
    (project / ".cloudmake").symlink_to(outside, target_is_directory=True)
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(
        project,
        environment,
        "-b",
        "local",
        *arguments,
        check=False,
    )

    assert result.returncode == 2
    assert "reserved Cloudmake namespace must not be a symlink" in result.stdout
    assert engine_calls(log) == []
    assert list(outside.iterdir()) == []


def test_explicit_legacy_source_acceptance_is_bound_to_exact_fingerprint(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    state_root = install_legacy_artifact_receipt(project, environment, log)

    invoke(
        project,
        environment,
        "-b",
        "colab",
        "--accept-legacy-artifacts-as-source",
        "build",
    )

    acceptance = state_root / "migrations" / "legacy-artifacts-source.json"
    accepted = json.loads(acceptance.read_text(encoding="utf-8"))
    assert accepted["fingerprint"] == artifact_receipt(project / "artifacts")[
        "fingerprint"
    ]
    assert acceptance.stat().st_mode & 0o077 == 0
    log.write_text("", encoding="utf-8")
    invoke(project, environment, "-b", "colab", "build")
    assert len(engine_calls(log)) == 1

    (project / "artifacts" / "private-result.txt").write_text(
        "changed output\n", encoding="utf-8"
    )
    log.write_text("", encoding="utf-8")
    # A changed directory no longer matches the prior collection receipt and is
    # therefore ordinary project source; the old acceptance does not bless it.
    invoke(project, environment, "-b", "colab", "build")
    assert len(engine_calls(log)) == 1


def test_forged_legacy_receipt_cannot_be_explicitly_accepted(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    install_legacy_artifact_receipt(
        project, environment, log, fingerprint="0" * 64
    )

    result = invoke(
        project,
        environment,
        "-b",
        "colab",
        "--accept-legacy-artifacts-as-source",
        "build",
        check=False,
    )

    assert result.returncode == 2
    assert "requires an exact known legacy Cloudmake collection receipt" in result.stdout
    assert engine_calls(log) == []


def test_one_off_backend_does_not_replace_saved_preference(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "--use", "colab")

    invoke(project, environment, "-b", "kaggle", "test")
    invoke(project, environment, "build")

    first, second = engine_calls(log)
    assert_assignment(first, "BACKEND", "kaggle-notebook")
    assert_assignment(second, "BACKEND", "colab-notebook")


def test_global_preference_applies_to_multiple_projects(
    tmp_path: Path, fake_bin: Path
) -> None:
    first_project = make_project(tmp_path / "first")
    second_project = make_project(tmp_path / "second")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(first_project, environment, "--use", "kaggle", "--global")
    invoke(first_project, environment, "build")
    invoke(second_project, environment, "test")

    first, second = engine_calls(log)
    assert_assignment(first, "BACKEND", "kaggle-notebook")
    assert_assignment(second, "BACKEND", "kaggle-notebook")


def test_command_line_backend_overrides_environment_and_saved_preference(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "--use", "colab")
    environment["CLOUDMAKE_BACKEND"] = "kaggle"

    invoke(project, environment, "-b", "codespaces", "build")

    assert_assignment(engine_calls(log)[0], "BACKEND", "codespaces-ssh")


def test_environment_backend_overrides_saved_preference(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "--use", "colab")
    environment["CLOUDMAKE_BACKEND"] = "kaggle"

    invoke(project, environment, "build")

    assert_assignment(engine_calls(log)[0], "BACKEND", "kaggle-notebook")


def test_dash_c_selects_project_without_changing_shell_directory(
    tmp_path: Path, fake_bin: Path
) -> None:
    caller = tmp_path / "caller"
    caller.mkdir()
    project = make_project(tmp_path / "other-project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(caller, environment, "-C", str(project), "-b", "colab", "build")

    assert_assignment(engine_calls(log)[0], "PROJECT_DIR", str(project.resolve()))


def test_unknown_target_and_make_assignments_are_dispatched_losslessly(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(project, environment, "-b", "colab", "benchmark", "SIZE=large", "DEBUG=1")

    call = engine_calls(log)[0]
    assert_remote_target(call, "benchmark")
    assert project_arguments(call) == ["SIZE=large", "DEBUG=1"]
    assert "SIZE=large" not in call
    assert "DEBUG=1" not in call
    assert "dispatch" in call


@pytest.mark.parametrize(
    "target", ["start", "sync", "status", "fetch", "open", "shell", "stop", "doctor", "backends", "collect", "package", "help", "exec", "release candidate's [gpu]"]
)
def test_cloud_operation_words_are_unconditionally_project_targets(
    tmp_path: Path, fake_bin: Path, target: str
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(project, environment, "-b", "colab", target)

    call = engine_calls(log)[0]
    assert_remote_target(call, target)
    assert "dispatch" in call


def test_collect_option_runs_any_target_and_fetches_its_output(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(
        project,
        environment,
        "-b",
        "colab",
        "--collect",
        "dist/release",
        "benchmark",
        "MODE=release",
    )

    call = engine_calls(log)[0]
    assert "collect" in call
    assert "dispatch" not in call
    assert_remote_target(call, "benchmark")
    assert_collect_directory(call, "dist/release")
    assert project_arguments(call) == ["MODE=release"]
    assert "MODE=release" not in call


def test_collect_requires_a_project_target(tmp_path: Path, fake_bin: Path) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "--collect", check=False)

    assert result.returncode == 2
    assert "requires a directory and project target" in result.stdout
    assert engine_calls(log) == []


@pytest.mark.parametrize("directory", ["/absolute", "../escape", "dist/../../escape"])
def test_collect_rejects_unsafe_directories_before_invoking_engine(
    tmp_path: Path, fake_bin: Path, directory: str
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(
        project, environment, "--collect", directory, "benchmark", check=False
    )

    assert result.returncode == 2
    assert "safe project-relative path" in result.stdout
    assert engine_calls(log) == []


def test_package_option_is_not_a_second_target_namespace(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "--package", check=False)

    assert result.returncode == 2
    assert "unknown cloudmake option" in result.stdout
    assert engine_calls(log) == []


@pytest.mark.parametrize("command", ["start", "sync", "sync-dry-run", "status", "environment", "fetch", "open", "shell", "stop"])
def test_lifecycle_options_are_not_remote_make_targets(
    tmp_path: Path, fake_bin: Path, command: str
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(project, environment, "-b", "colab", f"--{command}")

    call = engine_calls(log)[0]
    assert command in call
    assert not any(argument.startswith("REMOTE_TARGET_B64=") for argument in call)


def test_project_variables_cannot_reconfigure_the_host_engine(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(
        project,
        environment,
        "-b",
        "kaggle",
        "-j",
        "8",
        "build",
        "KAGGLE_TIMEOUT=7200",
        "KAGGLE_ACCELERATOR=NvidiaL4",
    )

    call = engine_calls(log)[0]
    assert_assignment(call, "JOBS", "8")
    assert project_arguments(call) == [
        "KAGGLE_TIMEOUT=7200",
        "KAGGLE_ACCELERATOR=NvidiaL4",
    ]
    assert "KAGGLE_TIMEOUT=7200" not in call
    assert "KAGGLE_ACCELERATOR=NvidiaL4" not in call


@pytest.mark.parametrize("option", ["--doctor", "--start", "--sync", "--status", "--environment", "--fetch", "--open", "--shell", "--stop"])
def test_cloud_operations_reject_trailing_project_arguments(
    tmp_path: Path, fake_bin: Path, option: str
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, option, "NAME=value", check=False)

    assert result.returncode == 2
    assert "does not accept positional arguments" in result.stdout
    assert engine_calls(log) == []


@pytest.mark.parametrize(
    "arguments",
    [
        ("-b", "codespaces", "--gpu=T4", "build"),
        ("--use", "codespaces", "--gpu=T4"),
        ("-b", "ssh", "--host", "lab-gpu", "--gpu=T4", "build"),
        ("--use", "ssh", "--host", "lab-gpu", "--gpu=T4"),
    ],
)
def test_accelerator_request_is_rejected_when_backend_cannot_select_hardware(
    tmp_path: Path, fake_bin: Path, arguments: tuple[str, ...]
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, *arguments, check=False)

    assert result.returncode == 2
    assert "does not support accelerator requests" in result.stdout
    assert engine_calls(log) == []


def test_lightning_gpu_request_selects_studio_machine(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    invoke(project, environment, "-b", "lightning", "--gpu=T4", "build")

    call = engine_calls(log)[0]
    assert_assignment(call, "BACKEND", "lightning-studio-ssh")
    assert_assignment(call, "LIGHTNING_MACHINE", "T4")


def test_unknown_backend_fails_without_invoking_engine(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "-b", "unknown", "build", check=False)

    assert result.returncode != 0
    assert "unknown backend" in result.stdout.lower()
    assert engine_calls(log) == []


def test_compute_target_requires_project_makefile(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = tmp_path / "empty-project"
    project.mkdir()
    environment, log = contract_environment(tmp_path, fake_bin)

    result = invoke(project, environment, "-b", "colab", "build", check=False)

    assert result.returncode != 0
    assert "makefile" in result.stdout.lower()
    assert engine_calls(log) == []


def test_credentials_are_not_written_to_project_or_cloudmake_preferences(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, _ = contract_environment(tmp_path, fake_bin)
    sentinel = "must-not-be-persisted-secret"
    environment["GITHUB_TOKEN"] = sentinel
    environment["KAGGLE_API_TOKEN"] = sentinel

    invoke(project, environment, "--use", "codespaces")

    persisted = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for root_name in ("config", "state", "cache")
        for path in (tmp_path / root_name).rglob("*")
        if path.is_file()
    )
    assert sentinel not in persisted
    assert all(
        sentinel not in path.read_text(encoding="utf-8")
        for path in project.rglob("*")
        if path.is_file()
    )


def test_codespace_name_can_be_persisted_as_non_secret_project_selection(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    environment["CODESPACE"] = "steady-cpu-workspace"

    selected = invoke(project, environment, "--use", "codespaces")
    environment.pop("CODESPACE")
    invoke(project, environment, "build")

    assert "codespace=steady-cpu-workspace" in selected.stdout
    assert_assignment(engine_calls(log)[0], "CODESPACE", "steady-cpu-workspace")


def test_execution_provenance_hashes_assignment_values_and_tracks_result(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    secret = "do-not-store-this-value"

    result = invoke(project, environment, "benchmark", f"TOKEN={secret}")

    latest_records = list((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    assert len(latest_records) == 1
    latest = latest_records[0]
    record = json.loads(latest.read_text(encoding="utf-8"))
    assert record["status"] == "succeeded"
    assert record["target"] == "benchmark"
    assert record["exit_code"] == 0
    assert record["assignments"][0]["name"] == "TOKEN"
    assert len(record["assignments"][0]["value_sha256"]) == 64
    assert secret not in latest.read_text(encoding="utf-8")
    assert latest.stat().st_mode & 0o077 == 0
    assert f"run={record['run_id']}" in result.stdout
    assert any(
        argument == f"CLOUDMAKE_RUN_ID={record['run_id']}"
        for argument in engine_calls(log)[0]
    )


def test_colab_default_session_migrates_existing_legacy_project_state(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "legacy-project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "build")
    first = engine_calls(log)[0]
    state_root = Path(
        next(
            argument.split("=", 1)[1]
            for argument in first
            if argument.startswith("CLOUDMAKE_STATE_ROOT=")
        )
    )
    (state_root / "colab-notebook" / "cuda-build").mkdir(parents=True)
    log.write_text("", encoding="utf-8")

    result = invoke(project, environment, "test")

    assert_assignment(engine_calls(log)[0], "COLAB_SESSION", "cuda-build")
    assert "Continuing with legacy session cuda-build" in result.stdout


def test_explicit_colab_session_is_preserved_exactly(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "explicit-project")
    environment, log = contract_environment(tmp_path, fake_bin)
    environment["COLAB_SESSION"] = "teaching-runtime-07"

    invoke(project, environment, "build")

    assert_assignment(engine_calls(log)[0], "COLAB_SESSION", "teaching-runtime-07")


def test_failed_execution_is_retained_in_provenance(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, _ = contract_environment(tmp_path, fake_bin)
    environment["CLOUDMAKE_TEST_ENGINE_EXIT"] = "7"

    result = invoke(project, environment, "build", check=False)

    latest = next((tmp_path / "state" / "projects").glob("*/runs/latest.json"))
    record = json.loads(latest.read_text(encoding="utf-8"))
    assert result.returncode == 7
    assert record["status"] == "failed"
    assert record["exit_code"] == 7


def test_local_target_failure_has_context_and_concise_summary(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, _ = contract_environment(tmp_path, fake_bin)
    environment["CLOUDMAKE_TEST_ENGINE_EXIT"] = "2"

    result = invoke(project, environment, "-b", "local", "build", check=False)

    assert result.returncode == 2
    assert "[cloudmake] backend=local resource=local" in result.stdout
    assert "[cloudmake] target 'build' failed with exit status 2" in result.stdout
    assert "provenance=" in result.stdout


def test_history_lists_recent_execution_without_invoking_engine_again(
    tmp_path: Path, fake_bin: Path
) -> None:
    project = make_project(tmp_path / "project")
    environment, log = contract_environment(tmp_path, fake_bin)
    invoke(project, environment, "benchmark")
    before = engine_calls(log)

    history = invoke(project, environment, "--history")

    assert "succeeded" in history.stdout
    assert "benchmark" in history.stdout
    assert "Provenance directory" in history.stdout
    assert engine_calls(log) == before

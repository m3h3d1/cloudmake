from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import pytest

from conftest import PROJECT_ROOT, run_command, write_executable


FAKE_SDK = PROJECT_ROOT / "tests" / "fixtures" / "fake_modal"


def fake_modal_cli(path: Path) -> Path:
    return write_executable(
        path,
        f"""#!{sys.executable}
import os
import sys

arguments = sys.argv[1:]
if arguments == ["--version"]:
    print("modal client version: 1.6.2")
elif arguments == ["profile", "current"]:
    raise SystemExit(1 if os.environ.get("FAKE_MODAL_AUTH_DENIED") else 0)
elif arguments == ["app", "list"]:
    raise SystemExit(0)
elif arguments[:1] == ["shell"]:
    print("fake-shell " + arguments[1])
else:
    print("unexpected fake modal arguments: " + repr(arguments), file=sys.stderr)
    raise SystemExit(2)
""",
    )


def consumer(tmp_path: Path) -> Path:
    project = tmp_path / "consumer"
    project.mkdir()
    (project / "Makefile").write_text(
        "smoke:\n"
        "\t@mkdir -p build\n"
        "\t@n=$$(cat build/count 2>/dev/null || echo 0); n=$$((n + 1)); "
        "printf '%s\\n' $$n > build/count; printf 'remote-count=%s\\n' $$n\n"
        "fail:\n"
        "\t@exit 7\n"
        "report:\n"
        "\t@mkdir -p out && echo collected > out/report.txt\n",
        encoding="utf-8",
    )
    (project / "notes.txt").write_text("source\n", encoding="utf-8")
    return project


def environment(tmp_path: Path, **extra: str) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    fake_modal_cli(fake_bin / "modal")
    write_executable(fake_bin / "colab", "#!/bin/sh\necho 'fake colab: the real Colab CLI must not be reached' >&2\nexit 99\n")
    return {
        "CLOUDMAKE_CONFIG_HOME": os.fspath(tmp_path / "config"),
        "CLOUDMAKE_STATE_HOME": os.fspath(tmp_path / "state"),
        "CLOUDMAKE_CACHE_HOME": os.fspath(tmp_path / "cache"),
        "PATH": os.pathsep.join((os.fspath(fake_bin), os.environ["PATH"])),
        "PYTHONPATH": os.fspath(FAKE_SDK),
        "FAKE_MODAL_STATE": os.fspath(tmp_path / "modal-state"),
        "FAKE_MODAL_ROOT": os.fspath(tmp_path / "remote"),
        "MODAL_REMOTE_ROOT": os.fspath(tmp_path / "remote"),
        "MODAL_READY_TIMEOUT": "5",
        **extra,
    }


def cloudmake(prototype: Path, project: Path, env: dict[str, str], *arguments: str, check: bool = True):
    return run_command(
        [prototype / "cmd" / "cloudmake", "-C", project, *arguments],
        cwd=prototype,
        env=env,
        check=check,
        timeout=60,
    )


def calls(tmp_path: Path, event: str) -> list[dict]:
    log = tmp_path / "modal-state" / "calls.jsonl"
    if not log.exists():
        return []
    entries = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    return [entry for entry in entries if entry["event"] == event]


def test_start_reuse_sync_failure_and_stop(prototype: Path, tmp_path: Path) -> None:
    project = consumer(tmp_path)
    env = environment(tmp_path)
    run = lambda *arguments, check=True: cloudmake(prototype, project, env, *arguments, check=check)

    run("--use", "modal", "--gpu=T4")
    first = run("smoke")
    second = run("smoke")
    (project / "notes.txt").unlink()
    (project / "added.txt").write_text("new\n", encoding="utf-8")
    third = run("smoke")
    failed = run("fail", check=False)
    status = run("--status")
    stopped = run("--stop")
    absent = run("--status")
    fresh = run("smoke")

    assert "sandbox=" in first.stdout and "resource=started" in first.stdout
    assert "accelerator=T4" in first.stdout
    assert "remote-count=1" in first.stdout
    assert "resource=reused" in second.stdout
    assert "Source unchanged; skipping archive upload" in second.stdout
    assert "remote-count=2" in second.stdout
    assert "remote-count=3" in third.stdout
    source = next((tmp_path / "remote").glob("src"))
    assert not (source / "notes.txt").exists()
    assert (source / "added.txt").read_text(encoding="utf-8") == "new\n"
    assert failed.returncode != 0
    assert "target 'fail' failed with exit status 2" in failed.stdout
    assert "status=ready" in status.stdout
    assert "stopped" in stopped.stdout
    assert "status=absent" in absent.stdout
    assert "resource=started" in fresh.stdout and "remote-count=1" in fresh.stdout
    created = calls(tmp_path, "create")
    assert len(created) == 2
    assert created[0]["gpu"] == "T4"
    assert created[0]["cpu"] == 1.0 and created[0]["memory"] == 8192
    assert created[0]["idle_timeout"] == 1800 and created[0]["timeout"] == 86400
    assert created[0]["volumes"] == {}
    assert created[0]["image"].startswith("debian_slim+apt:make")


def test_collect_downloads_artifacts(prototype: Path, tmp_path: Path) -> None:
    project = consumer(tmp_path)
    env = environment(tmp_path)

    cloudmake(prototype, project, env, "-b", "modal", "--collect", "out", "report")

    artifact = project / ".cloudmake" / "artifacts" / "report.txt"
    assert artifact.read_text(encoding="utf-8") == "collected\n"


def test_persist_mounts_volume_and_survives_replacement(prototype: Path, tmp_path: Path) -> None:
    project = consumer(tmp_path)
    env = environment(tmp_path)
    run = lambda *arguments: cloudmake(prototype, project, env, *arguments)

    run("--use", "modal", "--persist")
    run("smoke")
    run("--stop")
    replaced = run("smoke")

    created = calls(tmp_path, "create")
    assert len(created) == 2
    volume = os.fspath(tmp_path / "remote")
    assert all(entry["volumes"].keys() == {volume} for entry in created)
    assert created[0]["volumes"][volume].startswith("cloudmake-")
    assert "resource=started" in replaced.stdout
    assert "Source unchanged; skipping archive upload" in replaced.stdout
    assert "remote-count=2" in replaced.stdout


def test_reuse_refuses_a_changed_configuration(prototype: Path, tmp_path: Path) -> None:
    project = consumer(tmp_path)
    env = environment(tmp_path)

    cloudmake(prototype, project, env, "--use", "modal", "--cpu")
    cloudmake(prototype, project, env, "smoke")
    changed = cloudmake(prototype, project, env, "--gpu=L4", "smoke", check=False)

    assert changed.returncode != 0
    assert "different configuration" in changed.stdout
    assert len(calls(tmp_path, "create")) == 1


@pytest.mark.xfail(
    strict=True,
    reason="launcher passes CLOUDMAKE_ADOPT=1 whenever workspace_id != project key, "
    "and every non-Colab backend derives a distinct workspace_id",
)
def test_foreign_owner_is_refused(prototype: Path, tmp_path: Path) -> None:
    project = consumer(tmp_path)
    env = environment(tmp_path)
    cloudmake(prototype, project, env, "-b", "modal", "smoke")
    owner = tmp_path / "remote" / ".cloudmake-owner.json"
    record = json.loads(owner.read_text(encoding="utf-8"))
    record["project_id"] = "f" * len(record["project_id"])
    owner.write_text(json.dumps(record), encoding="utf-8")

    refused = cloudmake(prototype, project, env, "-b", "modal", "smoke", check=False)

    assert refused.returncode != 0
    assert "remote-count=2" not in refused.stdout


def test_unreadable_owner_is_ambiguous_not_fresh(prototype: Path, tmp_path: Path) -> None:
    project = consumer(tmp_path)
    env = environment(tmp_path)
    cloudmake(prototype, project, env, "-b", "modal", "smoke")

    result = cloudmake(
        prototype, project, {**env, "FAKE_MODAL_DOWNLOAD_ERROR": ".cloudmake-owner.json"},
        "-b", "modal", "smoke", check=False,
    )

    assert result.returncode != 0
    assert "refusing to treat the workspace as fresh" in result.stdout


def test_lost_exec_stream_is_ambiguous_and_not_replayed(prototype: Path, tmp_path: Path) -> None:
    project = consumer(tmp_path)
    env = environment(tmp_path, FAKE_MODAL_EXEC_DISCONNECT="smoke")

    result = cloudmake(prototype, project, env, "-b", "modal", "smoke", check=False)

    assert result.returncode != 0
    assert "automatic replay is unsafe" in result.stdout
    assert (tmp_path / "remote" / "src" / "build" / "count").read_text(encoding="utf-8") == "1\n"
    targets = [entry for entry in calls(tmp_path, "exec") if "smoke" in entry["command"][-1]]
    assert len(targets) == 1


def test_fetch_never_allocates(prototype: Path, tmp_path: Path) -> None:
    project = consumer(tmp_path)
    env = environment(tmp_path)

    result = cloudmake(prototype, project, env, "-b", "modal", "--fetch", check=False)

    assert result.returncode != 0
    assert "not running" in result.stdout
    assert calls(tmp_path, "create") == []


def test_capacity_shortage_is_temporary(prototype: Path, tmp_path: Path) -> None:
    project = consumer(tmp_path)
    env = environment(tmp_path, FAKE_MODAL_CAPACITY="1")

    result = cloudmake(prototype, project, env, "-b", "modal", "smoke", check=False)

    assert result.returncode == 75
    assert "Capacity is temporarily unavailable" in result.stdout


def test_shell_hands_over_to_modal_shell(prototype: Path, tmp_path: Path) -> None:
    project = consumer(tmp_path)
    env = environment(tmp_path)

    result = cloudmake(prototype, project, env, "-b", "modal", "--shell")

    sandbox_id = calls(tmp_path, "create")[0]
    assert sandbox_id
    assert "fake-shell sb-" in result.stdout
    assert "Project source:" in result.stdout

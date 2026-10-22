from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from conftest import PROJECT_ROOT, write_executable


ALLOCATE = PROJECT_ROOT / "backend" / "colab-notebook" / "allocate.py"


def test_tpu_allocation_and_mismatched_reuse(tmp_path: Path) -> None:
    log = tmp_path / "commands.jsonl"
    client = write_executable(
        tmp_path / "colab",
        r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1] == "sessions":
    print(os.environ.get("FAKE_SESSIONS", "[colab] No active sessions found on server."))
elif sys.argv[1] != "new":
    raise SystemExit(2)
''',
    )
    base = [
        sys.executable,
        str(ALLOCATE),
        "--client", str(client),
        "--session", "test-tpu",
        "--tpu", "v5e1",
        "--retry-seconds", "0",
        "--resource-state", str(tmp_path / "resource-state"),
        "--result", str(tmp_path / "result.json"),
    ]
    environment = {**os.environ, "FAKE_LOG": str(log)}

    allocated = subprocess.run(base, env=environment, capture_output=True, text=True)
    assert allocated.returncode == 0
    commands = [json.loads(line) for line in log.read_text().splitlines()]
    assert commands[-1] == ["new", "-s", "test-tpu", "--tpu", "v5e1"]

    environment["FAKE_SESSIONS"] = (
        "[test-tpu] endpoint | Hardware: T4 | Shape: STANDARD | Variant: GPU"
    )
    reused = subprocess.run(base, env=environment, capture_output=True, text=True)
    assert reused.returncode == 2
    assert "stop it before switching" in reused.stderr
    assert json.loads((tmp_path / "result.json").read_text())["outcome"] == "accelerator-mismatch"

    environment["FAKE_SESSIONS"] = (
        "[test-tpu] endpoint | Hardware: V5E1 | Shape: STANDARD | Variant: TPU"
    )
    matching = subprocess.run(base, env=environment, capture_output=True, text=True)
    assert matching.returncode == 0
    assert (tmp_path / "resource-state").read_text() == "reused\n"


def test_interrupt_stops_capacity_wait_without_provider_cleanup(
    tmp_path: Path,
) -> None:
    log = tmp_path / "colab.jsonl"
    client = write_executable(
        tmp_path / "colab",
        r'''#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

with Path(os.environ["FAKE_LOG"]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\n")
if sys.argv[1] == "sessions":
    raise SystemExit(0)
if sys.argv[1] == "new":
    print("HTTP 412: TooManyAssignmentsError")
    raise SystemExit(1)
raise SystemExit(f"unexpected command: {sys.argv[1]}")
''',
    )
    receipt = tmp_path / "allocation.json"
    resource_state = tmp_path / "resource-state"
    environment = os.environ.copy()
    environment.update(
        {
            "FAKE_LOG": str(log),
            "CLOUDMAKE_RETRY_BASE_SECONDS": "10",
            "CLOUDMAKE_RETRY_MAX_SECONDS": "10",
            "CLOUDMAKE_RETRY_JITTER": "0",
        }
    )
    process = subprocess.Popen(
        [
            sys.executable,
            str(ALLOCATE),
            "--client",
            str(client),
            "--session",
            "interrupt-test",
            "--retry-seconds",
            "3600",
            "--resource-state",
            str(resource_state),
            "--result",
            str(receipt),
        ],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    progress = process.stdout.readline()
    assert "capacity unavailable" in progress

    process.send_signal(signal.SIGINT)
    remainder, _ = process.communicate(timeout=5)

    assert process.returncode == 130
    assert "Traceback" not in progress + remainder
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["attempts"] == 1
    assert payload["outcome"] == "interrupted"
    commands = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [command[0] for command in commands] == ["sessions", "new"]
    assert not resource_state.exists()

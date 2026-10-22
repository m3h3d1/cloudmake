from __future__ import annotations

import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.fspath(Path(__file__).resolve().parents[2] / "core"))
from run_state import update


EX_TEMPFAIL = 75
CAPACITY_MARKER = "TooManyAssignmentsError"


def atomic_json(path: Path, payload: dict[str, object]) -> None:
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
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_receipt(
    path: Path,
    *,
    session: str,
    attempts: int,
    outcome: str,
    classification: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "schema": 1,
        "provider": "colab",
        "session": session,
        "attempts": attempts,
        "outcome": outcome,
    }
    if classification is not None:
        payload["classification"] = classification
    atomic_json(path, payload)


def record_operation_failure(
    state_file: Path, receipt_file: Path, returncode: int
) -> None:
    try:
        receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        receipt = {}
    outcome = receipt.get("outcome")
    provider_state, runtime_state, failure_code = {
        "capacity-timeout": (
            "absent",
            "absent",
            "capacity_retry_deadline_exceeded",
        ),
        "capacity-unavailable": ("absent", "absent", "capacity_unavailable"),
        "session-query-failed": (
            "unknown",
            "unreachable",
            "session_listing_failed",
        ),
        "allocation-failed": ("unknown", "unreachable", "allocation_failed"),
        "retry-configuration-failed": (
            "absent",
            "absent",
            "retry_configuration_failed",
        ),
        "interrupted": ("unknown", "unreachable", "allocation_interrupted"),
    }.get(
        outcome,
        ("unknown", "unreachable", "allocation_receipt_missing"),
    )
    update(
        state_file,
        {
            "phase": "allocation",
            "provider_state": provider_state,
            "runtime_state": runtime_state,
            "session_created": False,
            "target_submission": "not_submitted",
            "retry_safe": returncode != 130,
            "failure_code": failure_code,
        },
    )


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def emit(output: str) -> None:
    if output:
        print(output, end="" if output.endswith("\n") else "\n")


def exit_status(returncode: int) -> int:
    return returncode if 1 <= returncode <= 255 else 1


def session_is_listed(output: str, session: str) -> bool:
    prefix = f"[{session}]"
    return any(
        line == prefix or line.startswith(prefix + " ")
        for line in output.splitlines()
    )


def session_matches_accelerator(
    output: str, session: str, accelerator: str, variant: str
) -> bool:
    prefix = f"[{session}]"
    return any(
        line.startswith(prefix + " ")
        and f"Hardware: {accelerator.upper()}" in line
        and f"Variant: {variant}" in line
        for line in output.splitlines()
    )


def tuning(name: str, default: float, *, minimum: float = 0.0) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as error:
        raise ValueError(f"{name} must be a number") from error
    if not math.isfinite(value) or value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    return value


def duration(value: float, *, round_up: bool = False) -> str:
    if value < 1:
        return f"{value:.2f}s"
    seconds = math.ceil(value) if round_up else math.floor(value)
    if seconds < 60:
        return f"{seconds}s"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}m{remainder}s"


def capacity_timeout(
    arguments: argparse.Namespace, attempts: int, started: float
) -> int:
    elapsed = time.monotonic() - started
    print(
        "[cloudmake] capacity unavailable: colab "
        f"{CAPACITY_MARKER}; attempts={attempts}; "
        f"elapsed={duration(elapsed)} remaining=0s; retry deadline expired",
        file=sys.stderr,
    )
    write_receipt(
        arguments.result,
        session=arguments.session,
        attempts=attempts,
        outcome="capacity-timeout",
        classification=CAPACITY_MARKER,
    )
    return EX_TEMPFAIL


def allocate(arguments: argparse.Namespace) -> int:
    attempts = 0
    arguments.allocation_attempts = attempts
    started = time.monotonic()
    deadline = started + arguments.retry_seconds
    maximum = min(
        60.0, tuning("CLOUDMAKE_RETRY_MAX_SECONDS", 60.0, minimum=0.01)
    )
    base = min(
        maximum, tuning("CLOUDMAKE_RETRY_BASE_SECONDS", 5.0, minimum=0.01)
    )
    jitter = tuning("CLOUDMAKE_RETRY_JITTER", 0.2)
    if jitter > 0.5:
        raise ValueError("CLOUDMAKE_RETRY_JITTER must be between 0 and 0.5")

    sessions = run([arguments.client, "sessions"])
    if sessions.returncode:
        emit(sessions.stdout)
        write_receipt(
            arguments.result,
            session=arguments.session,
            attempts=attempts,
            outcome="session-query-failed",
        )
        return exit_status(sessions.returncode)
    if session_is_listed(sessions.stdout, arguments.session):
        requested = arguments.tpu or arguments.gpu
        variant = "TPU" if arguments.tpu else "GPU"
        if requested and not session_matches_accelerator(
            sessions.stdout, arguments.session, requested, variant
        ):
            print(
                f"[cloudmake] session {arguments.session!r} is not confirmed as "
                f"{variant} {requested}; stop it before switching accelerators",
                file=sys.stderr,
            )
            write_receipt(
                arguments.result,
                session=arguments.session,
                attempts=attempts,
                outcome="accelerator-mismatch",
            )
            return 2
        arguments.resource_state.write_text("reused\n", encoding="utf-8")
        write_receipt(
            arguments.result,
            session=arguments.session,
            attempts=attempts,
            outcome="reused",
        )
        return 0

    command = [arguments.client, "new", "-s", arguments.session]
    if arguments.gpu:
        command.extend(["--gpu", arguments.gpu])
    if arguments.tpu:
        command.extend(["--tpu", arguments.tpu])

    while True:
        if attempts and time.monotonic() >= deadline:
            return capacity_timeout(arguments, attempts, started)
        attempts += 1
        arguments.allocation_attempts = attempts
        result = run(command)
        if result.returncode == 0:
            emit(result.stdout)
            arguments.resource_state.write_text("started\n", encoding="utf-8")
            write_receipt(
                arguments.result,
                session=arguments.session,
                attempts=attempts,
                outcome="allocated",
            )
            return 0

        if CAPACITY_MARKER not in result.stdout:
            emit(result.stdout)
            write_receipt(
                arguments.result,
                session=arguments.session,
                attempts=attempts,
                outcome="allocation-failed",
            )
            return exit_status(result.returncode)

        if arguments.retry_seconds == 0:
            emit(result.stdout)
            print(
                "[cloudmake] capacity unavailable: colab "
                f"{CAPACITY_MARKER}; attempt={attempts}; retry disabled",
                file=sys.stderr,
            )
            write_receipt(
                arguments.result,
                session=arguments.session,
                attempts=attempts,
                outcome="capacity-unavailable",
                classification=CAPACITY_MARKER,
            )
            return exit_status(result.returncode)

        now = time.monotonic()
        elapsed = now - started
        remaining = max(0.0, deadline - now)
        if remaining <= 0:
            return capacity_timeout(arguments, attempts, started)

        exponential = min(maximum, base * (2 ** min(attempts - 1, 30)))
        randomized = min(
            maximum, exponential * random.uniform(1 - jitter, 1 + jitter)
        )
        delay = min(remaining, max(0.01, randomized))
        print(
            "[cloudmake] capacity unavailable: colab "
            f"{CAPACITY_MARKER}; attempt={attempts} "
            f"elapsed={duration(elapsed)} remaining={duration(remaining, round_up=True)} "
            f"next={duration(delay, round_up=True)}",
            file=sys.stderr,
            flush=True,
        )
        time.sleep(delay)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Start or reuse a Colab session with classified capacity retry"
    )
    parser.add_argument("--client", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--gpu", default="")
    parser.add_argument("--tpu", default="")
    parser.add_argument("--retry-seconds", required=True, type=int)
    parser.add_argument("--resource-state", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--state-file", type=Path)
    arguments = parser.parse_args()
    if arguments.gpu and arguments.tpu:
        parser.error("--gpu and --tpu cannot be combined")
    if arguments.retry_seconds < 0:
        parser.error("--retry-seconds must not be negative")
    arguments.resource_state.parent.mkdir(parents=True, exist_ok=True)
    arguments.resource_state.unlink(missing_ok=True)
    arguments.result.unlink(missing_ok=True)
    try:
        returncode = allocate(arguments)
    except KeyboardInterrupt:
        write_receipt(
            arguments.result,
            session=arguments.session,
            attempts=getattr(arguments, "allocation_attempts", 0),
            outcome="interrupted",
        )
        returncode = 130
    except ValueError as error:
        print(f"[cloudmake] allocation retry configuration error: {error}", file=sys.stderr)
        write_receipt(
            arguments.result,
            session=arguments.session,
            attempts=0,
            outcome="retry-configuration-failed",
        )
        returncode = 2
    if returncode != 0 and arguments.state_file is not None:
        record_operation_failure(arguments.state_file, arguments.result, returncode)
    return returncode


if __name__ == "__main__":
    raise SystemExit(main())

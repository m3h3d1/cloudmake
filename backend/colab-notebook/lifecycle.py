from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "core"))
from run_state import update


def run_client(command: list[str], timeout: float) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=max(timeout, 0.01),
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        output = error.stdout or ""
        if isinstance(output, bytes):
            output = output.decode(errors="replace")
        return subprocess.CompletedProcess(command, 124, output)


def record_failure(
    state_file: Path,
    *,
    phase: str,
    provider_state: str,
    runtime_state: str,
    created: bool,
    failure_code: str,
) -> None:
    update(
        state_file,
        {
            "phase": phase,
            "provider_state": provider_state,
            "runtime_state": runtime_state,
            "session_created": created,
            "target_submission": "not_submitted",
            "retry_safe": True,
            "failure_code": failure_code,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Allocate or reuse a Colab session and await readiness"
    )
    parser.add_argument("--client", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--gpu")
    parser.add_argument("--tpu")
    parser.add_argument("--probe-file", type=Path, required=True)
    parser.add_argument("--command-timeout", type=float, required=True)
    parser.add_argument("--ready-timeout", type=float, required=True)
    parser.add_argument("--poll-seconds", type=float, required=True)
    parser.add_argument("--state-file", type=Path, required=True)
    parser.add_argument("--created-marker", type=Path, required=True)
    parser.add_argument("--resource-state", type=Path, required=True)
    parser.add_argument(
        "--preallocated",
        action="store_true",
        help="reuse allocation evidence already written to --resource-state",
    )
    arguments = parser.parse_args()
    if arguments.command_timeout <= 0 or arguments.ready_timeout <= 0:
        parser.error("readiness timeouts must be positive")
    if arguments.poll_seconds < 0:
        parser.error("--poll-seconds must not be negative")

    arguments.created_marker.parent.mkdir(parents=True, exist_ok=True)
    arguments.created_marker.unlink(missing_ok=True)
    if not arguments.preallocated:
        arguments.resource_state.unlink(missing_ok=True)
    base = [arguments.client]
    lifecycle_timeout = max(arguments.command_timeout, 5.0)
    update(
        arguments.state_file,
        {
            "phase": "allocation",
            "provider_state": "unknown",
            "session_created": False,
            "target_submission": "not_submitted",
            "retry_safe": True,
        },
    )

    if arguments.preallocated:
        try:
            resource_state = arguments.resource_state.read_text(
                encoding="utf-8"
            ).strip()
        except OSError as error:
            record_failure(
                arguments.state_file,
                phase="allocation",
                provider_state="unknown",
                runtime_state="unreachable",
                created=False,
                failure_code="allocation_receipt_unreadable",
            )
            print(
                f"[colab] Allocation state could not be read: {error}",
                file=sys.stderr,
            )
            return 75
        if resource_state not in {"started", "reused"}:
            record_failure(
                arguments.state_file,
                phase="allocation",
                provider_state="unknown",
                runtime_state="unreachable",
                created=False,
                failure_code="allocation_receipt_invalid",
            )
            print(
                "[colab] Allocation state was invalid; target was not submitted.",
                file=sys.stderr,
            )
            return 75
        created = resource_state == "started"
        if created:
            arguments.created_marker.touch()
    else:
        listed = run_client([*base, "sessions"], lifecycle_timeout)
        if listed.returncode != 0:
            record_failure(
                arguments.state_file,
                phase="allocation",
                provider_state="unknown",
                runtime_state="unreachable",
                created=False,
                failure_code="session_listing_failed",
            )
            if listed.stdout:
                print(listed.stdout.rstrip(), file=sys.stderr)
            print(
                "[colab] Could not inspect sessions; target was not submitted and retrying is safe.",
                file=sys.stderr,
            )
            return 75

        pattern = re.compile(rf"^\[{re.escape(arguments.session)}\](?:\s|$)")
        exists = any(pattern.match(line) for line in listed.stdout.splitlines())
        created = False
        if not exists:
            command = [*base, "new", "-s", arguments.session]
            if arguments.gpu:
                command.extend(["--gpu", arguments.gpu])
            if arguments.tpu:
                command.extend(["--tpu", arguments.tpu])
            allocated = run_client(command, lifecycle_timeout)
            if allocated.returncode != 0:
                record_failure(
                    arguments.state_file,
                    phase="allocation",
                    provider_state="unknown",
                    runtime_state="unreachable",
                    created=False,
                    failure_code="allocation_failed",
                )
                if allocated.stdout:
                    print(allocated.stdout.rstrip(), file=sys.stderr)
                print(
                    "[colab] Session allocation did not complete; target was not submitted.",
                    file=sys.stderr,
                )
                return 75
            created = True
            arguments.created_marker.touch()

        resource_state = "started" if created else "reused"
        arguments.resource_state.write_text(resource_state + "\n", encoding="utf-8")
    update(
        arguments.state_file,
        {
            "phase": "readiness",
            "provider_state": "starting",
            "runtime_state": "fresh" if created else "unknown",
            "session_created": created,
            "target_submission": "not_submitted",
            "retry_safe": True,
        },
    )

    deadline = time.monotonic() + arguments.ready_timeout
    attempts = 0
    last_output = ""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        attempts += 1
        probe_timeout = min(arguments.command_timeout, remaining)
        probe = run_client(
            [
                *base,
                "exec",
                "-s",
                arguments.session,
                "--timeout",
                str(max(1, int(probe_timeout))),
                "-f",
                str(arguments.probe_file),
            ],
            probe_timeout,
        )
        last_output = probe.stdout
        if probe.returncode == 0:
            if probe.stdout:
                print(probe.stdout.rstrip())
            if attempts > 1:
                elapsed = arguments.ready_timeout - max(
                    0.0, deadline - time.monotonic()
                )
                print(
                    f"[colab] Session became ready after {attempts} probes ({elapsed:.1f}s).",
                    file=sys.stderr,
                )
            update(
                arguments.state_file,
                {
                    "phase": "readiness",
                    "provider_state": "ready",
                    "runtime_state": "fresh" if created else "unknown",
                    "session_created": created,
                    "target_submission": "not_submitted",
                    "retry_safe": True,
                },
            )
            return 0
        if attempts == 1:
            print(
                "[colab] Session is not ready; waiting up to "
                f"{arguments.ready_timeout:g}s before target submission.",
                file=sys.stderr,
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(arguments.poll_seconds, remaining))

    if last_output:
        print(last_output.rstrip(), file=sys.stderr)
    provider_state = "unknown"
    failure_code = "readiness_deadline_exceeded"
    if created:
        stopped = run_client(
            [*base, "stop", "-s", arguments.session], lifecycle_timeout
        )
        if stopped.returncode == 0:
            provider_state = "stopped"
            print(
                "[colab] Newly allocated, never-ready session was released safely.",
                file=sys.stderr,
            )
        else:
            failure_code = "readiness_deadline_cleanup_failed"
            print(
                "[colab] The newly allocated session could not be released; inspect it before stopping.",
                file=sys.stderr,
            )
    record_failure(
        arguments.state_file,
        phase="readiness",
        provider_state=provider_state,
        runtime_state="unreachable",
        created=created,
        failure_code=failure_code,
    )
    print(
        "[colab] Readiness deadline exceeded; target was not submitted and retrying the command is safe.",
        file=sys.stderr,
    )
    print("[colab] Next: cloudmake -b colab --status", file=sys.stderr)
    if not created:
        print(
            "[colab] The pre-existing unreachable session was not stopped, recreated, or adopted.",
            file=sys.stderr,
        )
    print(
        f"[cloudmake] failure phase=readiness provider_state={provider_state} "
        f"session_created={str(created).lower()} target_submission=not_submitted retry_safe=true",
        file=sys.stderr,
    )
    return 75


if __name__ == "__main__":
    raise SystemExit(main())

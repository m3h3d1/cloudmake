from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sys
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


class IdentityError(RuntimeError):
    pass


def project_identity(project_root: Path, project_name: str) -> dict[str, Any]:
    canonical_root = project_root.expanduser().resolve()
    hostname = socket.gethostname()
    material = f"{hostname}\0{canonical_root}".encode("utf-8", errors="surrogateescape")
    return {
        "schema": SCHEMA_VERSION,
        "project_id": hashlib.sha256(material).hexdigest()[:24],
        "project_name": project_name,
        "source_path": os.fspath(canonical_root),
        "hostname": hostname,
    }


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise
    except Exception as error:
        raise IdentityError(
            f"cloudmake owner record is corrupt: {path}; move it aside and retry"
        ) from error
    if not isinstance(value, dict) or value.get("schema") != SCHEMA_VERSION:
        raise IdentityError(
            f"unsupported cloudmake owner record at {path}; move it aside and retry"
        )
    for field in ("project_id", "project_name", "source_path", "hostname"):
        if not isinstance(value.get(field), str) or not value[field]:
            raise IdentityError(f"cloudmake owner record is missing {field}: {path}")
    return value


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def ensure(arguments: argparse.Namespace) -> int:
    state_dir = arguments.state_dir
    expected = project_identity(arguments.project_root, arguments.project_name)
    owner_path = state_dir / "owner.json"
    owner_id_path = state_dir / "owner.id"

    if owner_path.exists():
        current = load(owner_path)
        if current["project_id"] != expected["project_id"]:
            raise IdentityError(
                f"state directory {state_dir} belongs to {current['source_path']} on "
                f"{current['hostname']}, not {expected['source_path']} on {expected['hostname']}"
            )

    atomic_write(owner_path, json.dumps(expected, indent=2, sort_keys=True) + "\n")
    atomic_write(owner_id_path, expected["project_id"] + "\n")
    print(owner_path)
    return 0


def check(arguments: argparse.Namespace) -> int:
    expected = load(arguments.expected)
    if arguments.adopt_from and re.fullmatch(r"[0-9a-f]{24}", arguments.adopt_from) is None:
        raise IdentityError("invalid previous workspace owner")
    try:
        actual = load(arguments.actual)
    except FileNotFoundError:
        return 0

    if actual["project_id"] == expected["project_id"]:
        return 0
    if arguments.adopt_from and actual["project_id"] != arguments.adopt_from:
        raise IdentityError("workspace owner changed since attachment; refusing to adopt")
    if arguments.adopt:
        print(
            f"[cloudmake] Adopting {arguments.resource}; previous owner was "
            f"{actual['source_path']} on {actual['hostname']}.",
            file=sys.stderr,
        )
        return 0

    raise IdentityError(
        f"refusing to replace {arguments.resource}: it belongs to "
        f"{actual['source_path']} on {actual['hostname']} "
        f"(project {actual['project_name']!r}); rerun with CLOUDMAKE_ADOPT=1 "
        "only if replacing that workspace is intentional"
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Manage cloudmake project ownership records")
    subcommands = root.add_subparsers(dest="command", required=True)

    ensure_parser = subcommands.add_parser("ensure")
    ensure_parser.add_argument("--state-dir", type=Path, required=True)
    ensure_parser.add_argument("--project-root", type=Path, required=True)
    ensure_parser.add_argument("--project-name", required=True)
    ensure_parser.set_defaults(run=ensure)

    check_parser = subcommands.add_parser("check")
    check_parser.add_argument("--expected", type=Path, required=True)
    check_parser.add_argument("--actual", type=Path, required=True)
    check_parser.add_argument("--resource", required=True)
    check_parser.add_argument("--adopt", action="store_true")
    check_parser.add_argument("--adopt-from")
    check_parser.set_defaults(run=check)
    return root


def main() -> int:
    arguments = parser().parse_args()
    try:
        return arguments.run(arguments)
    except IdentityError as error:
        print(f"[cloudmake] {error}", file=sys.stderr)
        return 73


if __name__ == "__main__":
    raise SystemExit(main())

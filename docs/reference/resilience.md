# Resilience and recovery

Cloudmake moves source into short-lived or reusable cloud environments, so it
must handle interrupted transfers, expired sessions, concurrent commands, and
unexpectedly reused workspaces without damaging a project. This document
describes those safeguards and the controls available when recovery is needed.

## Project ownership

Before synchronization, cloudmake records a stable project identity derived
from the canonical local path and host. Reusable remote workspaces carry the
corresponding owner record. If another project attempts to use that workspace,
cloudmake stops before replacing source or running `rsync --delete`.

For a locally known managed Colab checkpoint, use
`cloudmake --workspace attach WORKSPACE_ID`, then run the project target normally.
Until a checkpoint publishes successfully, any foreign live or restored owner
must match the recorded previous owner. A failed publication leaves that check
in place for a retry. An unexpected owner is refused.

For remote owner-record adoption outside managed checkpoint attachment, use the
conspicuous invocation-only override:

```sh
# PROJECT_TARGET is supplied by the project's Makefile.
CLOUDMAKE_ADOPT=1 cloudmake PROJECT_TARGET
```

Do not set `CLOUDMAKE_ADOPT=1` after a Colab `--workspace attach`: it bypasses
the previous-owner check. Do not use adoption merely to bypass an unexpected error.
First inspect the old source path and host reported by cloudmake. Owner records
contain provenance, not credentials, but are copied to the selected workspace
or private notebook.

Local owner and state files are written atomically with private file
permissions. Corrupt identity state is refused rather than silently replaced.

## Source selection and preflight

Cloudmake creates a common source manifest for notebook archives and SSH
transfers. Automatic exclusions are intentionally limited to root `.git/`,
`.cloud-state/`, and `.cloudmake/`: repository metadata, legacy in-tree tool
state, and namespaced downloaded output. Other names carry no built-in meaning,
so a project may freely use directories such as `src/`, `build/`, or `.venv/`.

Projects can add exclusions in `.cloudmakeignore`, one glob per line. Blank
lines and `#` comments are allowed:

```text
datasets/
*.trace
local-secrets.json
```

When upgrading from a release that collected into root `artifacts/`, Cloudmake
uses retained provenance to recognize an unchanged legacy collection and fails
before remote transfer. Review and move/delete that directory or exclude
`/artifacts/`. If the exact known contents truly are project source, explicitly
accept that fingerprint with `--accept-legacy-artifacts-as-source`. This guard
preserves project ownership of the name without silently uploading old output.

Negated patterns are deliberately unsupported. This keeps archive and rsync
selection consistent instead of allowing the two transports to interpret the
same file differently.

Preview a source change without authenticating, allocating compute, or
contacting a provider:

```sh
cloudmake --sync-dry-run
```

The report compares the selected tree with the last successful manifest and
lists added, modified, and deleted paths. Selection warns above 25 MiB by
default. `SOURCE_WARN_MB` changes the warning threshold; a nonzero
`SOURCE_MAX_MB` rejects an oversized selection before creating an archive or
starting a provider operation.

Special files are rejected. A symbolic link is accepted only when it resolves
inside the project, preventing an archive from unintentionally capturing data
outside the selected tree.

## Concurrency and locks

Mutating targets are serialized with a per-backend, per-resource local lock. A
process crash releases this operating-system lock automatically.

SSH synchronization and execution also use a token-protected lock on the remote
workspace. Only the holder's token can release it. If a client disappears, the
lock becomes reclaimable after `REMOTE_LOCK_STALE` seconds. This prevents two
local commands from concurrently replacing source or invoking Make in the same
workspace while still permitting recovery after an abandoned connection.

## Transactional updates

State files, manifests, and source snapshots are written to temporary paths and
renamed only after validation succeeds.

On Colab, cloudmake extracts a source archive into a staging directory and swaps
it into place only after every archive member passes path, link, and type checks.
Kaggle notebooks apply the same archive validation before execution.

Downloaded artifact archives are treated as untrusted. Absolute paths,
directory traversal, links, devices, and other special files are rejected. New
artifacts are extracted into a staging directory, so an invalid or interrupted
download leaves the previous `.cloudmake/artifacts/` directory intact.

Extraction is rejected before writing when configured file-count, total-size,
per-file-size, compressed-size, or expansion-ratio budgets are exceeded. A
collection run deletes its prior remote artifact archive before invoking the
project target, so a failed target cannot make an older archive appear to be its
result.

Project-generated files remain wherever the project Makefile places them. A
reusable backend retains them while its synchronized project workspace remains
valid; a batch backend starts from a fresh source snapshot.

Preserving that workspace after an ephemeral VM disappears is the separate
managed-checkpoint capability described in the
[Day 2 tutorial](../tutorials/day2-workspace.md). Its evolving
architecture and qualification history are retained in the maintainer design
workspace rather than forming part of this recovery reference.

## Readiness checks

Every compute operation has two readiness stages:

1. Host-side `prerequisites` and `doctor` validate commands, settings,
   authentication, and read-only provider access before allocation.
2. When a VM is reachable, cloudmake checks required remote commands before
   transferring source. Batch notebooks perform this check at the start of the
   submitted job.

This catches a missing Make, `tar`, `rsync`, compiler, or provider client near
the boundary where the problem can be explained clearly.

## Provider reconciliation

Saved local state is not treated as proof that compute still exists. Colab
sessions are reconciled through `colab sessions`; Codespaces is probed through
`gh`; user-managed hosts are probed through the selected OpenSSH alias;
and Lightning Studios are reconciled through the authenticated Studio list. If
provider-generated SSH configuration has expired, cloudmake refreshes it once
after a failed connection. Host SSH configuration is user-managed, so Cloudmake
never rewrites or regenerates it.

After Colab allocation, Cloudmake polls only its non-mutating remote-readiness
probe until `COLAB_READY_TIMEOUT` (120 seconds by default). Individual provider
calls are bounded by `COLAB_READY_PROBE_TIMEOUT` (15 seconds), and retries wait
`COLAB_READY_POLL_SECONDS` (3 seconds). Progress is reported once when waiting
begins and once on recovery or expiry rather than once per failed probe.

If a session created by the current invocation never becomes ready, Cloudmake
can prove both resource provenance and absence of target submission, so it
releases that session. A pre-existing unreachable session is never stopped,
recreated, or adopted automatically because ownership cannot be checked through
the failed connection. In both cases the requested target is known not submitted
and the diagnostic reports that retrying is safe. Readiness polling is never
applied to a project target or preparation target.

Cloudmake does not blindly retry target execution or a mutating notebook
submission. An ambiguous failure may already have started work, so automatic
repetition could produce duplicate jobs or side effects.

The optional launcher form `cloudmake --retry-for=DURATION ...` is narrower than
a general operation retry. A backend may use it only around a provider allocation
call and only after positively classifying a documented temporary-capacity
response. The Colab notebook backend initially recognizes
`TooManyAssignmentsError`. It does not retry session queries, authentication,
readiness, synchronization, notebook execution, artifact handling, or project
Make. Existing-session readiness retains its separate non-mutating probe policy
and is never converted into allocation or automatic recreation. Deadline expiry
returns `EX_TEMPFAIL` (75); interruption stops the wait without stopping or
recreating provider resources.

A dispatched invocation owns one provenance record across all allocation
attempts. Its `allocation` field records the attempt count, final outcome, and
capacity classification when applicable. Allocation success then enters the
ordinary start/sync/execute path exactly once.

The default native Colab session is the readable project slug plus eight
hexadecimal characters from the stable project identity. Unrelated project
paths therefore do not contend for `cuda-build`. An explicitly supplied
`COLAB_SESSION` is preserved exactly. If the launcher finds this project's
existing local state for the old `cuda-build` default, it continues using that
legacy resource and prints a migration notice rather than silently abandoning
it.

Colab's CLI does not currently expose a stable runtime-instance identifier that
Cloudmake can rely on. Cloudmake therefore treats the remote owner and source
fingerprint control records as the observable runtime identity. A non-mutating
remote probe must positively report a record as absent before its loss is
accepted as a fresh/reset lifecycle event. A record reported as present but
unreadable is an infrastructure ambiguity: Cloudmake refuses synchronization,
leaves the target unsubmitted, and records a safe-retry failure. Confirmed loss
under a still-listed session invalidates cached fingerprint assumptions, forces
a full source upload, and warns that provider-local generated state may be gone.
A matching owner and fingerprint denotes the same reachable workspace; a
different owner is foreign; an unreachable runtime remains ownership-ambiguous.

Projects that need idempotent runtime-local prerequisites may declare
`COLAB_SESSION_PREPARE_TARGET`. It runs only after a fresh/reset runtime or when
its preparation receipt is missing or changed. The receipt is bound to the
preparation target and synchronized source fingerprint and is committed only
after a successful result receipt. Receipt upload and remote-install failures
are recorded separately. A preparation disconnect is not retried; the requested
target remains unsubmitted.

Colab target execution separates an expected project failure from an
infrastructure failure. A completed notebook records the project target and its
Make exit status in an atomic result receipt. A nonzero status preserves all
Make output, returns the same nonzero status locally, and produces a concise
Cloudmake failure summary without turning that ordinary result into a notebook
Python traceback. A missing, malformed, or inconsistent receipt is an
infrastructure failure. Unexpected notebook exceptions continue to fail notebook
execution normally so their traceback and provider diagnostics remain visible.
In both cases, the launcher retains the executed notebook location and writes
the normal provenance record; it never retries the project target automatically.

Provider-specific status is retained and followed by one normalized state:

- `absent`
- `starting`
- `ready`
- `running`
- `succeeded`
- `failed`
- `stopped`
- `unknown`

`unknown` means the provider response was recognized as neither a successful
nor a failed terminal state; it is not silently treated as success.

Launcher executions retain private local JSON provenance under the project's
external Cloudmake state directory. Records include the source and collected
artifact fingerprints, target, backend, resource, timestamps, and result. Colab
records also carry `phase`, `provider_state`, `runtime_state`,
`session_created`, `target_submission`, `retry_safe`, and a stable
`failure_code` when applicable. Make assignment values are hashed rather than
copied. Use `cloudmake --history` to locate and summarize recent records.

## Recovery checklist

When an operation fails:

1. Run `cloudmake -b <name> --status` and read the provider-specific output.
2. Run `cloudmake -b <name> --doctor` to recheck local authentication and access.
3. Run `cloudmake --sync-dry-run` to inspect the selected source and pending
   changes.
4. If ownership differs, verify the reported project path and host. For a known
   Colab checkpoint, use `--workspace attach` and retry without an override;
   stop if its previous-owner check fails. Outside managed attachment, use
   `CLOUDMAKE_ADOPT=1` only for a deliberate reassignment.
5. If a remote lock is abandoned, wait for or deliberately adjust
   `REMOTE_LOCK_STALE`; do not delete an active holder's lock.
6. Re-run a mutating target only after determining whether the provider already
   accepted the earlier request.

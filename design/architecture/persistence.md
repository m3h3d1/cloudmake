# Persistence architecture

For a conceptual introduction to session reuse, native persistence, managed
checkpoints, incremental transfer, and safe publication, read
[Day 2: workspace persistence](../../docs/tutorials/day2-workspace.md).

This document defines the managed checkpoint persistence introduced in
Cloudmake 2.0 and retained by the 2.x line. The high-level `--persist` option
also covers backends whose ordinary storage is already persistent; those native
modes do not invoke this checkpoint lifecycle.

The problem is broader than one application. Accelerator sessions are often
ephemeral, while installing a toolchain and producing intermediate build or
design data can take much longer than uploading source. Cloudmake should make a
later session resume useful work without requiring a project to adopt
provider-specific files, targets, or directory layouts.

## Goals

1. Preserve Cloudmake's existing project contract: an arbitrary, locally
   executable Make target and no prescribed project layout.
2. Keep the local selected tree authoritative for source while preserving
   remote-generated state across target invocations and VM replacement.
3. Back up changed state incrementally, with deletion tracking, integrity
   checks, and an atomic published head.
4. Restore into the VM's fast local filesystem. Durable storage is a backing
   store, not the directory in which Make runs.
5. Make interruption conservative: a partially written snapshot is never the
   latest restorable checkpoint.
6. Keep provider credentials in the provider's client or mounted storage
   surface; never package them with project source or checkpoint metadata.
7. Treat persistent-workspace encryption keys as private Cloudmake implementation state:
   generate and store them automatically, never ask a user to manage a restic
   password, and never expose plaintext during remote delivery.
8. Keep persistence off by default. Without explicit project opt-in, preserve
   the previous execution, dependency, credential, and provider-I/O contract.
9. Treat every persistent workspace and checkpoint as disposable acceleration
   state. A project must remain rebuildable from its selected source and
   Makefile on a fresh compatible backend; persistence may reduce repeated work
   but must not change target meaning or correctness.

## Backend behavior

Cloudmake separates the portable persistence request from its backend-specific
mechanism:

| Mode | Backends | Contract |
| --- | --- | --- |
| Managed checkpoint | `colab-notebook` | Incrementally restore and publish an encrypted workspace through Google Drive. |
| Experimental managed checkpoint | `kaggle-notebook` | Retain the deprecated provider-private alternating-output implementation for compatibility and coarse batch experiments. |
| Native persistence | `local`, `host-ssh`, `codespaces-ssh`, `gcp-compute-ssh`, `lightning-studio-ssh` | Keep using the backend's existing durable project tree; perform no checkpoint transfer. |
| Unsupported | `colab-ssh` | Reject before provider contact rather than imply durability the transport cannot supply. |

Persistence remains an explicit per-project selection in every mode. Native
mode is operationally a no-op because the backend already has the required
storage property, not because Cloudmake ignores the request. Its context and
provenance say `native`, and provider deletion or expiry remains authoritative.
An enabled selection must be explicitly disabled before choosing an unsupported
backend. The legacy `--checkpoint` spelling remains an alias for `--persist`.

For Codespaces, `/workspaces` is `stop-persistent`: Cloudmake may wake or stop
the named provider resource, while the same volume remains attached. No archive
is created and no host round trip occurs. A provider rebuild retains that
directory, but deletion or retention expiry does not; the next resource must be
able to reconstruct it from local source and Make.

For Compute Engine, the attached Persistent Disk is likewise
`stop-persistent`. Cloudmake 2.4 starts or stops compute but neither snapshots
nor transfers that disk. The incremental SSH workspace survives only while the
selected VM and disk survive; deletion, corruption, zone availability, billing,
and retention remain Google Cloud responsibilities. `--persist` acknowledges
this native optimization and adds no GCS or Google Drive dependency.

## Non-goals

- Cloudmake does not extend, evade, or promise a provider's VM lifetime.
- Cloudmake does not provision a general-purpose cloud fleet, schedule jobs, or
  retry an ambiguously completed Make target.
- Cloudmake does not infer application-specific recovery semantics.
- Cloudmake does not capture arbitrary machine state such as `/usr`, kernel
  modules, drivers, running processes, or open network connections.
- Persistent-workspace snapshots are not a replacement for source control or an archival backup of
  the developer's local project.

## Rebuildability principle

The project source and Makefile are the complete specification of the work.
Checkpoint contents may include compiled objects, downloaded dependencies,
tool caches, intermediate results, and materialized application bundles, but
all such state is rebuildable. A missing, expired, corrupt, incompatible, or
explicitly purged workspace must fall back to clean project execution rather
than reveal a hidden project prerequisite.

This principle applies equally to native persistence. A stopped Codespace or
provider volume can make restoration effectively free because no archive moves,
but that filesystem remains a warm build tree rather than a durable source of
truth. Final results that cannot be reconstructed must be explicitly collected
or stored by the project outside Cloudmake's checkpoint contract.

## State model

Cloudmake separates four kinds of state:

| State | Authority | Persistent-workspace treatment |
| --- | --- | --- |
| Selected local source | Local project | Re-applied after restore; local content wins |
| Remote-generated workspace data | Project Make execution | Included when inside the managed workspace |
| Cloudmake control data | Cloudmake | Recorded separately as manifests and provenance |
| VM/tool state outside the workspace | Provider or image | Not captured |

### Durable tool-installation boundary

Only state beneath Cloudmake's managed remote workspace is checkpointable. For
the Colab backend that boundary is `/content/.cloud-build/workspace`. This is a
hard persistence boundary, not a directory-layout convention that Cloudmake can
infer or repair.

A project target named `provision`, `bootstrap`, or anything else receives no
special treatment. If it installs only into `/usr`, `/usr/local`, `/opt`, the
VM user's home directory outside the managed workspace, or a container engine's
system layer store such as `/var/lib/docker`, that installation is lost with the
VM even when the target succeeds and Cloudmake publishes a checkpoint. The same
is true of `apt` packages, drivers, kernel modules, services, and other
machine-level mutations.

Therefore the durable representation of every expensive, reusable tool
installation must live inside the managed workspace. It may be any ordinary
filesystem representation the project controls, including:

- a relocatable installation prefix;
- an opaque compressed root filesystem or tool archive;
- downloaded packages and build caches from which system integration is cheap
  to reconstruct.

The representation must also satisfy the workspace filesystem safety rules.
Uploaded source remains confined to ordinary files, directories, and
non-escaping links. Remote-generated checkpoint state may contain arbitrary
symbolic-link targets because Cloudmake and restic preserve those link objects
without following them. Special files remain rejected. This allows an
already-extracted root filesystem or package store to retain its native link
layout without weakening the host-to-VM source boundary. See
[Execution architecture](execution.md).

The live tool does not have to execute in that directory. On each replacement
VM, the project may extract, load, link, or otherwise materialize the saved
payload into an ephemeral execution location, then recreate lightweight system
integration such as `PATH`, library paths, wrappers, or packages that cannot be
relocated. What must be checkpointed is the costly reusable payload and enough
project-owned metadata to reconstruct it.

Cloudmake cannot generically redirect a conventional system-wide install into
the workspace: doing so would interpret project recipes, change their intended
semantics, and attempt to preserve provider-owned machine state. Projects that
want cross-VM reuse must make this boundary explicit in their own provisioning
recipe. Cloudmake still imposes no target name, project layout, installation
technology, or container choice.

This qualifies the zero-intrusion goal precisely: an existing Make project can
execute unmodified, and project-relative generated state is preserved without a
Cloudmake-specific target. If its existing setup recipe writes the reusable
installation only into machine-owned paths, cross-VM tool persistence requires
a project-side recipe or wrapper that creates a durable payload inside the
project tree. Cloudmake cannot supply that application-specific conversion
without ceasing to be target-agnostic.

## Lifecycle

For a reusable or newly allocated session, a dispatched target follows this
ordering:

```text
allocate or reuse resource
        |
        v
restore latest compatible checkpoint, if needed
        |
        v
reconcile selected local source into the workspace
        |
        v
invoke the exact project-provided Make target once
        |
        +-- failure/unknown --> preserve diagnostics; do not publish a new head
        |
        `-- success --------> incrementally snapshot changed managed state
                                      |
                                      v
                              atomically publish new head
```

The first automatic checkpoint boundary is after a successful Make target. This
is intentionally outside Make: it works with unmodified projects and gives a
clear rule for whether a snapshot may become current. A later, optional
Make-assisted safe-point protocol may permit checkpoints during long targets,
but it cannot become a mandatory target or variable.

## Source reconciliation

The source manifest records every Cloudmake-selected local path and its type,
mode, and content identity. The previous successful source manifest defines
which remote paths Cloudmake owns as synchronized source. On reconciliation:

1. current local source creates or replaces the corresponding remote paths;
2. paths removed from local source are removed remotely only when they were
   previously source-owned;
3. remote-generated paths absent from both source manifests are retained;
4. a source path wins if it conflicts with generated or restored state; and
5. a directory is removed only when empty, so a local directory deletion cannot
   silently erase generated descendants.

This distinction is required for incremental Make behavior. A transport must
not implement local deletion by broadly deleting every remote path absent from
the local tree.

## Persistent-workspace identity and compatibility

A persistent workspace has an opaque stable ID independent of a local checkout's
canonical path. That separation allows an explicit attach operation to reconnect
a moved or newly cloned project. Each snapshot contains at least:

- schema version and checkpoint identifier;
- persistent-workspace ID, attached project identity, and backend family;
- creation time and parent checkpoint, when incremental;
- source-manifest fingerprint used by the run;
- environment compatibility profile;
- content inventory, deletions, sizes, modes, and integrity hashes; and
- provenance identifier and the target outcome that authorized publication.

The environment profile must include the properties needed to reject an unsafe
restore, such as operating-system family, CPU architecture, and accelerator
runtime family where known. Exact accelerator model is recorded for provenance
but should not automatically prevent restoring portable build data. Projects
remain responsible for invalidating outputs that their own Make rules consider
architecture-specific.

Cloudmake refuses an unsupported schema, corrupt inventory, or unexpected
project identity without an explicit attachment before modifying the active
workspace. It records and reports an environment-profile change, then restores
the workspace so the project's own Make rules can invalidate incompatible
outputs.

Cloudmake maintains a local, non-secret registry for workspaces known to this
computer. It records the backend, requested accelerator and session, current
project attachment, recorded durable owner identity, latest snapshot statistics,
and the last observed VM CPU, memory, disk, OS, and accelerator properties.
Metadata informs diagnostics and future launch compatibility; it is not a
promise that an ephemeral provider
will allocate the same machine again.

The management surface remains option-only so it never reserves a project Make
target: list known workspaces, show metadata, explicitly attach one to the
current project, or explicitly purge one. Attaching transfers the local mapping
from any previously attached project on the same computer. A Colab attachment
pins the previous owner until the newly attached project publishes a checkpoint;
failed publication leaves that pin in place for a retry. Purge requires the
exact ID and a force acknowledgement, deletes both the encrypted Drive
repository and its local Cloudmake-owned key, and detaches local project mappings.

## Storage contract

The durable store is an adapter with these logical operations:

1. inspect the current workspace head;
2. read and verify a manifest;
3. fetch referenced immutable content;
4. stage new immutable content and a candidate manifest;
5. atomically compare-and-publish the project head; and
6. list and prune unreferenced history under an explicit retention policy.

Content and manifests are immutable once named. The mutable head is tiny and is
published last. Concurrent writers must use a generation check or equivalent
compare-and-swap; a writer that loses the race leaves the previous head intact.
Garbage collection is never part of the target's success path.

### Initial Colab storage backend

The selected implementation uses restic with an encrypted filesystem
repository whose destination is a user-authorized Google Drive mount. The
project workspace remains under `/content/.cloud-build/workspace`; Drive is not
the working directory. Restic, rather than Cloudmake, owns content-defined
chunking, deduplication, snapshot integrity, and repository locking.

The implementation deliberately excludes rclone and Kopia's direct Drive connector
because either route would require a separate OAuth configuration or service
account inside each ephemeral VM. The official `colab drivemount` flow must own
Drive authentication. Cloudmake generates a separate random encryption key for
each checkpoint repository and stores it in the local operating-system
credential store. It is an internal checkpoint artifact, not a user-managed or
provider credential.

For each restore or publication operation, the VM generates an ephemeral
transport key pair. The local credential-store adapter encrypts the repository
key to that public key without exposing plaintext on stdout. Cloudmake uploads
only the encrypted envelope. A short-lived remote helper decrypts it directly
into restic's password-command pipe, then removes the envelope and transport
key. A new envelope and key pair are used for every operation. No credential or
checkpoint key is present when Make executes.

The operational sequence is stricter than the general lifecycle diagram:

```text
mount Drive -> restore -> unmount Drive -> reconcile source -> Make
    -> on success: mount Drive -> snapshot -> verify -> unmount Drive
```

`colab drivemount -s SESSION` is the required mount surface. On the first mount
in a fresh VM, the official CLI may print a Google authorization URL and wait
for the user to approve it. This happens in the local terminal; the user does
not open or edit a scratch notebook. After authorization has been propagated to
that VM, Drive can be flushed, unmounted, and remounted in the same runtime
without another authorization prompt. A replacement VM requires authorization
again. Cloudmake must present this as one foreground resume step per new VM,
not claim that cross-session recovery is unattended.

DriveFS mounting is also subject to transient provider failures. Cloudmake
retries only a mount-command failure, only against the same already-owned
session, and only before source synchronization or target execution. The
bounded sequence is four total attempts with 5-, 15-, and 30-second delays. It never
recreates an ambiguously owned session, does not retry an unexpected exception,
and lets Ctrl-C stop immediately.

Destroying the transient checkpoint key and unmounting Drive are idempotent
cleanup operations. Cloudmake retries a failed verification twice, after 2 and
5 seconds, against that same session. Exhaustion remains an infrastructure
failure and never triggers session recreation or project-target replay.

The September 2026 storage spike with Colab CLI 0.6.1.dev8 validated:

- CLI-only Drive authorization and mounting on a fresh CPU runtime;
- flush/unmount and immediate remount in the same runtime;
- restic repository initialization, initial and incremental backups, and
  `restic check` on DriveFS;
- byte-for-byte restoration in the producing runtime;
- restoration from the same repository after destroying and replacing the VM;
  and
- forced termination during backup, followed by stale-lock recovery and a
  successful repository check without publication of a partial snapshot.

For an 8 MiB synthetic dataset with a small in-place change, the probe reported
8,390,537 bytes added by the first snapshot and 1,770,684 bytes by the second.
This confirms incremental transfer, though it is not a performance benchmark.

### Format-neutral lifecycle spike

A second live spike used a deliberately small project-local installation rather
than OpenROAD. It built a tiny executable plus a private shared library using a
relative runtime search path. The first encrypted snapshot added 55,397 bytes.
A newly allocated VM restored it, skipped the unchanged source upload, executed
it successfully, and published a second incremental snapshot that added 11,227
bytes. No compiler or payload rebuild ran in the replacement VM.

The experiment validates only the format-neutral checkpoint contract: ordinary
generated files can survive VM replacement and remain executable. Separate
research also exercised Nix and SIF representations, but those results do not
make either mechanism a Cloudmake runner or compatibility promise. Cloudmake
2.0 deliberately does not select a bundle format, runtime, or target named
`provision`. Managed OCI/CDI execution is a separate 2.1 capability described
in [Execution architecture](execution.md).

Checkpoint operations also carry fixed provider overhead: runtime allocation,
Drive authorization and mounting, helper installation, notebook execution, and
repository verification can dominate a small payload. Persistence is therefore
most useful when it avoids substantially more expensive provisioning or build
work. Small workloads should retain the default non-persistent path.

### Colab persistence boundary and the retired GCS 2.4 proposal

A named [Colab CLI session](https://github.com/googlecolab/google-colab-cli/blob/main/docs/01_session_management.md)
can reuse one live VM and kernel across commands, but the name is local control
state for an active provider assignment. It is not a durable VM identity or an
attached persistent disk. `colab stop` releases the assignment, and
[Colab may delete](https://research.google.com/colaboratory/faq.html) an
assigned VM after an idle timeout or its maximum lifetime. The provider exposes
no stopped-but-retained VM state that Cloudmake can later wake.

The official Colab CLI also maintains assignment activity through a daemon on
the client computer. Reusing a live assignment therefore requires that computer
to remain awake and online. If host sleep suspends the daemon and Colab reclaims
the VM, Cloudmake must classify the next runtime as fresh and restore from the
managed checkpoint; it cannot substitute for the provider's keep-alive path.

This differs materially from Codespaces. A Codespace is a provider resource
whose compute may stop while
[its workspace remains retained](https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle)
until deletion or retention expiry. Cloudmake can wake that same resource, so
its persistence adapter is native and requires no checkpoint transfer. Colab's
pooled runtime service deliberately couples the writable VM disk to the
temporary assignment; Cloudmake cannot make it stop-persistent without an
external store.

An initial Cloudmake 2.4 proposal paired native Colab with Google Cloud Storage.
The feasibility review found the mechanism technically sound: GCS Credential
Access Boundaries can restrict a short-lived token to a bucket and object
prefix, and restic can consume that non-refreshable bearer token directly.
However, GCS requires an active Cloud Billing account, its free allowance is
bounded, and a large checkpoint can create storage, operation, or transfer
charges. The IAM and billing setup is disproportionate when compared with a
proper Compute Engine VM whose Persistent Disk already survives stop/start.

The GCS proposal is therefore not the Cloudmake 2.4 objective. Drive remains the
explicitly interactive Colab checkpoint adapter. GCS may be revisited later as
an optional storage adapter, but it must never be described as risk-free free
storage or become the default merely to avoid Drive authorization. No GCP
account, billing relationship, project, bucket, or credential was created by
the feasibility review.

Cloudmake 2.4 instead targets a single-node GCP remote workstation. An already
provisioned Compute Engine VM owns its Persistent Disk, so the backend's
persistence operation is native and checkpoint transfer is a no-op. This keeps
the Make invariant intact: durable state accelerates work, while source and the
project's targets must still be sufficient to rebuild from scratch. See the
[GCP qualification](../qualification.md#gcp-compute-engine).

The feature branch now implements the local credential-store and
encrypted-envelope path as well as the proven storage lifecycle. Focused
automated tests cover ciphertext-only transport, key reuse, missing or locked
credential storage, transient cleanup, Drive unmount verification, actual VM
capacity checks, and failure ordering. The full acceptance gate below has also
passed. Release review must preserve these invariants rather than weakening
credential custody or checkpoint integrity. See the dedicated checkpoint
section in the [security model](../../docs/reference/security.md).

## Failure and cancellation semantics

- Restore and source reconciliation stage changes before replacing the active
  workspace wherever the transport permits it.
- A target failure remains a target failure. Its output and provenance are
  retained, and no new checkpoint head is published automatically.
- An interrupted upload may leave unreferenced immutable objects, but never a
  head that references an incomplete snapshot.
- A lost connection after the target begins is ambiguous. Cloudmake does not
  rerun the target or assert that a checkpoint is safe to publish.
- Cancellation stops host-side waiting promptly and does not destroy a resource
  whose ownership or target state is ambiguous.
- Restore, snapshot, verification, and publication failures are infrastructure
  failures and are reported separately from Make's exit status.

## Security boundary

Selected local source retains Cloudmake's existing secret preflight and explicit
exclusions before transfer. Remote-generated workspace data is arbitrary project
output: Cloudmake cannot prove that it contains no unknown application secret
without interpreting the project. It is encrypted before durable storage, and
provider credentials plus Cloudmake's checkpoint key are absent while Make
runs. Projects remain responsible for keeping their own generated credentials
out of the managed tree. Encryption does not make accidental credential capture
acceptable.

Cloudmake imposes no arbitrary workspace byte or file-count ceiling. Before a
restore, it compares the selected snapshot with the free bytes and inodes of the
allocated VM; publication is bounded by the actual VM filesystem, Drive storage,
and provider quotas. Provider errors remain visible rather than being replaced
with a smaller Cloudmake policy limit.

Restored paths are treated as untrusted input. The restore is staged beneath a
fixed root. Source-owned links remain confined; generated link objects may
retain absolute or escaping targets but are never followed by Cloudmake.
Devices, FIFOs, sockets, symlinked control records, and other special filesystem
entries are rejected before the active workspace is replaced. Cloudmake records
metadata necessary for reproducibility but never restores privileged ownership.

## Initial acceptance gate

The capability is ready to expose only when automated tests demonstrate:

1. generated outputs survive a local source edit in a reusable session;
2. locally deleted source disappears without deleting generated siblings;
3. local source wins a path conflict;
4. a fresh session restores the latest compatible successful checkpoint;
5. failed and ambiguous targets do not advance the checkpoint head;
6. interrupted publication leaves the previous checkpoint restorable;
7. corrupt, cross-project, and incompatible checkpoints are rejected;
8. secret, traversal, and link boundaries are enforced, and restore capacity is
   checked against the actual allocated filesystem;
9. repeated snapshots transfer only new or changed content; and
10. existing commands and projects behave exactly as before when persistence
    is not enabled.

The security gate additionally requires a dummy Cloudmake-generated repository
key to remain absent from notebooks, arguments, environments, files, archives, provenance,
logs, errors, and cancellation output. Drive must be unmounted while Make runs,
and a missing secure secret channel must fail closed.

An opt-in OpenROAD Flow Scripts acceptance project must then exercise a
realistic large tool bootstrap whose durable project state remains inside the
managed workspace, at least two successful stage targets separated by VM
replacement, restoration without rebuilding that state, and collection of a
final result. OpenROAD validates the generic design; it does not define
Cloudmake's project interface, and Cloudmake does not interpret ORFS target
names or design databases.

### Recorded ORFS acceptance result

The gate passed on 2026-09-07 with the pinned ORFS acceptance fixture. Stage 1
completed floorplan, published an encrypted snapshot, and stopped its Colab VM.
A replacement VM restored 3,334,710,226 logical bytes across 3,605 entries,
verified the exact floorplan hash and modification time, completed placement
without rebuilding floorplan, collected the project-owned evidence, published
the successor snapshot, and stopped cleanly.

The first snapshot added 3,275,420,957 repository bytes and took 111.838
seconds. Restore took 121.884 seconds. After placement, the incremental
successor added only 4,444,123 bytes and took 25.193 seconds. These are one
observed Colab/Drive run rather than performance guarantees, but they establish
the intended large-workspace and replacement-VM behavior. Each new VM still
required the user's foreground Drive authorization; Cloudmake neither retained
nor replayed Google credentials.

The following provider-specific state machine records the Colab session boundary used by this architecture.

## Colab session resilience
The stateless Colab resilience foundation was introduced in v1.0.0. Cloudmake
2.0 retains these lifecycle and replay-safety guarantees and adds opt-in durable
workspaces and cross-VM restoration as a separate layer; see
[Persistence architecture](#persistence-architecture).

### Session identity

The launcher derives the default session name from a sanitized project basename
and the first eight characters of a stable project key. The key hashes the
canonical local path together with the host identity. The result is readable,
stable on that host, and unlikely to collide with another project.

An explicit `COLAB_SESSION` always wins. If the project's external Cloudmake
state already contains `colab-notebook/cuda-build`, the launcher keeps the old
v0.9 default and prints the persistent migration command. The operator selects
a durable per-project replacement with
`COLAB_SESSION=NAME cloudmake --use colab`. A normal environment override stays
unpersisted. Before changing the saved selection, the operator inspects
`cuda-build` with `COLAB_SESSION=cuda-build cloudmake -b colab --status` and,
only when it is no longer needed, releases it with the corresponding `--stop`
command. Shared explicit names remain possible, but remote ownership checking
prevents accidental replacement.

### Pre-submission state machine

The Colab lifecycle moves through these states:

```text
session lookup -> create or reuse -> bounded readiness polling
               -> ownership classification -> stateless source sync
               -> optional idempotent preparation -> target submission
```

Only session lookup, creation, and the remote prerequisite probe occur during
readiness. The project target cannot run in that loop. The deadline uses a
monotonic clock, each probe has its own timeout, and polling delay is modest and
configurable.

Positive creation evidence is a successful `colab new` performed by the current
invocation. If that new session never becomes ready, it can be stopped safely.
No other session is automatically destroyed or recreated.

### Runtime classification and stateless recovery

The provider CLI does not expose a stable runtime-instance identifier, so
Cloudmake uses its remote ownership and source-fingerprint files as control
state. It first runs a non-mutating remote existence probe. Only a successful
probe can establish that a file is absent; a failed probe or a failed download
of a file reported present is ambiguous and stops before synchronization:

- `same`: the reachable workspace has the expected owner and control state;
- `fresh`: this invocation created the session and remote ownership is absent;
- `reset`: the named session was reused but ownership or fingerprint state is
  absent;
- `foreign`: ownership belongs to another project;
- `unreachable`: the runtime cannot be inspected, so its identity is ambiguous.

Fresh/reset state invalidates the cached remote fingerprint and forces the v0.9
full-archive synchronization path. That synchronization replaces the remote
source snapshot. It does not preserve generated files across a replacement VM.

`COLAB_SESSION_PREPARE_TARGET` optionally names a project-defined, idempotent
Make target. Cloudmake runs it after fresh/reset synchronization, records a
receipt only after confirmed success, and skips it while the same live session
retains a receipt bound to both the target name and synchronized source
fingerprint. Source changes conservatively rerun the idempotent preparation.
The requested target is uploaded only afterward. Receipt upload and installation
failures retain `not_submitted` for the requested target and have distinct
machine-readable failure codes.

### Failure provenance and replay boundary

Operation state is atomically recorded outside the project tree. Colab run
provenance includes:

- lifecycle phase;
- normalized provider state;
- runtime classification;
- whether the current invocation created the session;
- submission state: `not_submitted`, `submitted`, or `ambiguous`;
- retry safety and a machine-readable failure code.

Before execution submission, failures are retry-safe. Immediately before the
runner notebook crosses the provider execution boundary, submission becomes
`ambiguous` and retry safety becomes false. A connection loss after that point
never triggers an automatic target replay. A confirmed target-result receipt
records success or the project's actual Make exit status.

All regression coverage uses the fake Colab client. Live provider execution is
an explicit acceptance activity and is not required by the offline suite.

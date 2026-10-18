# Test suite

The default suite is deliberately offline. Provider integration tests put fake
`colab`, `kaggle`, `gh`, `gcloud`, `lightning`, `modal`, `ssh`, and `rsync` executables at the front
of `PATH`, and an offline `modal` SDK double from `fixtures/fake_modal/` on
`PYTHONPATH`; they never allocate cloud compute or use account credentials.

The directory layout is part of the test contract:

| Directory | Stable responsibility |
| --- | --- |
| `contract/` | Public CLI, distribution, security, and acceptance-harness invariants. |
| `unit/` | Isolated core, checkpoint, execution, and provider-helper behavior. |
| `integration/` | Offline orchestration across the launcher, engine, and fake providers. |
| `compatibility/` | Opt-in pinned upstream projects and Dev Container configurations. |
| `acceptance/` | Explicit live-provider and real-workload release gates. |
| `fixtures/` | Small repository-owned inputs shared by the layers above. |

Release branches may contain different features, but new tests should retain
these meanings rather than creating version-named directories.

Run everything that is implemented today:

```sh
python3 -m pip install -r requirements-test.txt
python3 -m pytest
```

If pytest is installed by a separate environment manager, invoking its `pytest`
executable directly is equivalent.

Run one stable layer or subsystem:

```sh
python3 -m pytest tests/contract
python3 -m pytest tests/unit/core
python3 -m pytest tests/unit/providers/colab
python3 -m pytest tests/unit/providers/gcp tests/integration/providers/gcp
python3 -m pytest tests/integration
python3 -m pytest -m integration
```

## Real GitHub CUDA projects

Cloudmake carries small Makefile overlays for two public course projects:

- NVIDIA's CUDA C++ Execution Spaces lesson gains `build`, `run`, and `clean`;
- GPU MODE's vector-addition Makefile gains a `run` target while preserving its
  existing default build.

The overlays are covered offline with fake compilers. To also sparse-clone the
pinned upstream revisions, apply the overlays, and exercise their targets:

```sh
CLOUDMAKE_TEST_REAL_GITHUB=1 python3 -m pytest \
  tests/compatibility/test_projects.py -m real_github
```

This invokes `tests/compatibility/projects/prepare.sh`, which prints the two prepared
project directories. The clones are revision-pinned so an upstream change cannot
silently alter a regression run.

## Public Dev Container corpus

The Dev Container compatibility gate clones pinned official samples for C++,
Go, Java, Node.js, Python, and Rust plus the official Ubuntu template. It
resolves tag-based images to recorded digests, checks Cloudmake's exact
compatibility decision for every configuration, and executes the compatible
Rust and Ubuntu workstations through the local Docker adapter:

```sh
CLOUDMAKE_TEST_REAL_DEVCONTAINERS=1 python3 -m pytest \
  tests/compatibility/test_devcontainers.py -m real_github
```

This is an opt-in network and OCI-pull test. Rejection is part of the gate:
Dockerfile builds, Features, lifecycle hooks, and port behavior Cloudmake cannot
preserve must fail before execution instead of being silently ignored. Image
tag resolution in the harness is explicit because Cloudmake's portable profile
currently requires an immutable image digest.

The live Colab gate is intentionally local-only because it allocates GPU compute
through the workstation's existing Colab CLI authentication. It clones both
projects, runs the overlaid `run` targets on separate T4 sessions, and stops each
session in a `finally` block:

```sh
CLOUDMAKE_TEST_LIVE_COLAB=1 python3 -m pytest \
  tests/compatibility/test_projects.py -m live_cloud
```

Do not copy Colab configuration or tokens into GitHub Actions. The repository
also provides a local-only Lightning T4 gate over the same two pinned projects.
It uses the Lightning client's existing login and provider-owned SSH key, then
stops the Studio without deleting its persistent filesystem:

```sh
export LIGHTNING_TEAMSPACE=OWNER/TEAMSPACE
export LIGHTNING_STUDIO=cloudmake-dev
CLOUDMAKE_TEST_LIVE_LIGHTNING=1 python3 -m pytest \
  tests/compatibility/test_projects.py -m live_cloud
```

Do not copy Lightning configuration, login tokens, or SSH keys into GitHub
Actions. Until this gate succeeds and its evidence is retained for the current
service and client, `lightning-studio-ssh` remains `unqualified`.

The supported GCP 2.4 CPU profile has a local-only gate. It uses an
already-provisioned VM, retains its disk, and stops compute without creating or
deleting infrastructure:

```sh
CLOUDMAKE_TEST_LIVE_GCP=1 GCP_GATE=cpu \
  tests/acceptance/gcp-workstation/run.sh /tmp/cloudmake-gcp-cpu-evidence
```

The same harness retains a paid `GCP_GATE=g4` path as a deferred 3.x security
qualification target. CDI remains dynamically checked by the adapter, but the
unexecuted G4 path carries no 2.4 release-quality claim.

Set `GCP_PROJECT`, `GCP_ZONE`, and `GCP_INSTANCE` first. The detailed contract,
billing warning, image requirements, and cleanup checks are in
[`acceptance/gcp-workstation`](acceptance/gcp-workstation/README.md).

The repository provides two credential-free hosted automation layers:

- `CI` runs the offline suite on supported macOS/Linux and Python combinations;
- `Upstream compatibility` runs the pinned public CUDA projects weekly or on
  demand without allocating cloud compute.

## ORFS persistent-workspace acceptance

The final Cloudmake 2.0 persistence gate is opt-in and deliberately separate
from the offline suite. It runs two project-provided ORFS stage targets across a
destroyed and replacement Colab VM, checks Cloudmake restore/publication
evidence, and collects project-owned proof that the second stage consumed the
first stage without rebuilding it:

```sh
export COLAB_SESSION=cloudmake-orfs-acceptance
export CLOUDMAKE_TEST_LIVE_ORFS_CHECKPOINT=1
tests/acceptance/orfs-checkpoint/run.sh \
  /path/to/orfs-project PROJECT_STAGE_1 PROJECT_STAGE_2 evidence
```

The prepared ORFS project owns its tool acquisition, launcher, stage checks,
and target names. See the [gate contract](acceptance/orfs-checkpoint/README.md).
The harness leaves the session intact after any failed or ambiguous operation.

## Historical Kaggle course evaluation

Kaggle is deprecated and no longer gates Cloudmake releases. The retained
opt-in evaluation uses a clean public ECE326 Lab 1 release, the
paired Lab 4 calibration, and the ECE467 accelerator-labs project. It also
forces Kaggle's `session-reuse=no` behavior to prove that checkpointed `$HOME`
state survives every fresh target VM:

```sh
export KAGGLE_USERNAME=your-account-slug
export CLOUDMAKE_TEST_LIVE_KAGGLE_COURSES=1
tests/acceptance/kaggle-courses/run.sh \
  /tmp/ece326-lab1 /path/to/ece326-lab4-pair \
  /path/to/ece467-labs evidence
```

See the [historical evaluation contract](acceptance/kaggle-courses/README.md).
It reproduces the evidence behind deprecation and consumes substantial provider
time; failures are not Cloudmake release blockers.

## ORFS OCI/CDI acceptance

The separate 2.1 gate uses the pinned official ORFS OCI image on a Colab T4.
Its first project target validates OpenROAD, Yosys, and NVIDIA CDI visibility;
later targets compose that immutable tool bundle with the already accepted 2.0
replacement-VM checkpoint flow:

```sh
export COLAB_SESSION=cloudmake-orfs-oci-acceptance
export CLOUDMAKE_TEST_LIVE_ORFS_OCI=1
tests/acceptance/orfs-oci/run.sh \
  tests/acceptance/orfs-oci/project evidence
```

Drive authorization is required because the full gate destroys and replaces
the VM between ORFS stages. The harness stops only after confirmed success and
leaves an ambiguous session intact for inspection.

## Codespaces provider-native workstation acceptance

The opt-in gate in
[`acceptance/codespaces-workstation`](acceptance/codespaces-workstation/README.md)
rebuilds an existing Cloudmake anchor Codespace from a digest-pinned public OCI
image, proves target and stop/start reuse, collects an artifact, restores the
neutral anchor, and stops the resource. It consumes Codespaces quota and is
never part of the offline suite.

`tests/acceptance/devcontainer-ssh` is the v2.3 cross-backend workstation gate.
It can run against any existing Linux OpenSSH alias and has an optional strict
PRoot expectation for a privilege-restricted host. It verifies literal
environment, non-root execution, incremental workspace state, foreground
loopback forwarding, and artifact collection without provisioning or changing
the selected host.

The release-level CPU-consumer gate then uses one digest-pinned Python
workstation and the same Codespace for ECE326 and a bounded ECE467 path:

```sh
export CODESPACE=cloudmake-course-gate-name
export CLOUDMAKE_TEST_LIVE_CODESPACES_COURSES=1
tests/acceptance/codespaces-courses/run.sh \
  /path/to/ece326-lab1 /path/to/ece326-lab4-pair \
  /path/to/ece467-labs /tmp/cloudmake-codespaces-course-evidence
```

It proves separate project identities, provider-native OCI reuse, ECE326
outbound downloads/tests/workload, ECE326 paired Lab 4 calibration, and ECE467
project plus short CPU reference paths. See the
[consumer gate contract](acceptance/codespaces-courses/README.md).

Finally, the Codespaces network gate starts a project-provided HTTP listener,
reaches a unique marker through GitHub's authenticated private port forwarding,
and stops both listener and resource:

```sh
export CODESPACE=cloudmake-network-gate-name
export CLOUDMAKE_TEST_LIVE_CODESPACES_NETWORK=1
tests/acceptance/codespaces-network/run.sh \
  /tmp/cloudmake-codespaces-network-evidence
```

This proves workload ingress without public exposure or credential custody. See
the [network gate contract](acceptance/codespaces-network/README.md).

`tests/contract/` exercises the public `cloudmake` launcher interface, including
configuration precedence, aliases, external project isolation, arbitrary target
dispatch, zero reserved project names, target-agnostic artifact collection,
option-only lifecycle routing, opaque project-assignment transport, and read-only
operations.

Test coverage is organized around observable behavior rather than provider SDK
internals:

- deterministic source fingerprinting and exclusions;
- archive safety and atomic Colab source replacement;
- fingerprint-bound Colab synchronization receipts that prevent a provider CLI
  from masking a remote synchronization exception and dispatching stale source;
- adversarial Colab control-state downloads: a present but unreadable owner or
  fingerprint is never treated as absence, never triggers source replacement,
  and keeps the project target unsubmitted;
- source-bound preparation receipts plus distinct upload/install failure
  provenance;
- Kaggle notebook generation, metadata, bounded status polling, and failure
  handling;
- executable Colab and Kaggle notebooks against temporary projects;
- Colab project-failure receipts, concise diagnostics, retained infrastructure
  tracebacks, and started-versus-reused execution context;
- Colab capacity classification, bounded allocation backoff, temporary-failure
  deadlines, immediate interruption, fail-fast compatibility, and at-most-once
  target dispatch;
- opt-in Colab persistent-workspace selection and provenance, restore/target/publication
  ordering, fail-closed restore and cleanup behavior, no publication after a
  failed target, OS credential-store key reuse, and encrypted-envelope
  non-disclosure;
- persistence-mode behavior across every backend: checkpoint transfer on native
  Colab and private-output checkpoint chaining on Kaggle, no-transfer native
  storage on local and durable SSH workspaces, early rejection on Colab SSH,
  legacy option aliases, and disabled-by-default target compatibility;
- Kaggle alternating-slot publication, fresh-VM restore and source
  reconciliation, failed-head protection, exact-slot purge, credential
  non-disclosure, OCI preflight, cached materialization, strict PRoot CDI
  translation, and safe large/checkpoint filesystem handling;
- stable workspace identity independent of project path, local metadata
  list/show, explicit attachment, force-gated purge, and restore capacity checks
  against the allocated VM rather than a fixed Cloudmake ceiling;
- bounded local and Colab environment characterization with explicit observed
  platform, resource, privilege, filesystem, isolation, device, and accelerator
  evidence, without inferred application-format compatibility;
- ownership-aware checkpoint links: strict uploaded/source-owned links,
  non-followed generated package/rootfs links, rejected special entries and
  symlinked control records, and generated-link preservation across source sync;
- default Colab compatibility with no Drive, credential-store, restore, or
  publication side effects and unchanged project-target argument dispatch;
- portable Make build, test, run, package, clean, and incremental behavior;
- reproducible Makefile overlays for pinned NVIDIA and GPU MODE CUDA lessons;
- backend lifecycle and command construction through fake provider clients;
- Codespaces provider-state classification, transparent wake/reuse banners,
  stop-persistent workspace preservation, and locally persisted non-secret
  resource selection; provider-native OCI rebuild/reuse and neutral-anchor
  restoration without a nested runtime;
- Lightning Studio start/reuse/machine-switch/stop behavior, persistent paths,
  exact key references, and the absence of Git-based project transfer;
- user-managed host SSH synchronization, lifecycle preservation, local alias
  validation, and absence of provider or credential configuration;
- installed generic, OCI, and GCP host-template discovery and safe rendering;
- per-backend commands, settings, Python version, authentication probes, and
  installation guidance;
- project ownership, atomic state, local and remote locks, stale recovery, and
  live-session reconciliation;
- shared source manifests, dry-run plans, ignore rules, source-size gates, and
  backend capability contracts;
- safe transactional artifact replacement and status normalization; and
- artifact resource budgets, secret-transfer refusal, execution provenance, and
  fresh installed-runtime behavior; and
- the launcher against external projects through Colab, Kaggle, and the common
  SSH transport, including lossless Make-variable passthrough without host-engine
  interpretation.

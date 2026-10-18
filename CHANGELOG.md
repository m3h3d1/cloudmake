# Changelog

All notable changes are recorded here. Cloudmake follows semantic versioning
once a version is published as a GitHub release.

## Unreleased

- Add the supported `modal-sandbox` backend (alias `modal`). It starts or
  reuses one named Modal Sandbox per project through the official Python SDK,
  uploads a fingerprinted source archive only when the source changed, runs
  project targets with plain Make, collects artifacts, and supports `--status`,
  `--stop`, and `--shell` (`modal shell`). `--persist` mounts a per-project
  Modal Volume as the workspace. Defaults: 1 CPU core, 8 GiB memory, 30-minute
  idle stop, 24-hour limit. Offline SDK doubles cover the flow; the live CPU
  and T4 GPU gates pass.
- Map a positively classified capacity shortage (`capacity_unavailable`) to
  exit status 75 alongside readiness-deadline failures.

## 2.4.0 - 2026-09-12

Compatibility note: this release deliberately takes one minor-version contract
exception before external adoption. Collection moves from root `artifacts/` to
`.cloudmake/artifacts/`, and `.cloudmake/` becomes a reserved Cloudmake
namespace. The migration is explicit and fail-closed under the
[project contract](docs/reference/project-contract.md#source-selection). No
published tag is rewritten by this decision.

- Move explicit collection output from the generic root `artifacts/` name to
  `.cloudmake/artifacts/`. Root `artifacts/` and `.artifacts/` are ordinary
  project source again; only the Cloudmake-qualified directory is reserved.
  This corrects the last project-namespace exception to intrusion-free adoption
  before the 2.4 contract is released. Existing root `artifacts/` output is not
  deleted or migrated automatically. If it exactly matches a collection receipt
  from an older local Cloudmake run, remote transfer fails closed until the
  operator moves/deletes it, excludes `/artifacts/` in `.cloudmakeignore`, or
  explicitly accepts the exact provenance-bound fingerprint as project source.
- Add an invocation-only `--idempotent` semantic assertion and the separate
  `--replay-for=DURATION` delivery-policy surface. Existing targets remain
  at-most-once. Every current backend rejects replay before provider contact
  because none yet proves non-overlapping attempts across every ambiguous
  boundary; provenance nevertheless records target semantics, delivery policy,
  ordered attempt evidence, and the derived `replay_safe` decision.
- Preserve the v1.1 Colab doctor guard that tests the CLI's actual Python
  execution dependency before its read-only account probe, and document that
  live-session reuse requires the official client host to stay awake and online.
- Replace global Dev Container field rejection with capability negotiation:
  the parser emits a closed required-behavior set and each backend declares
  separate adapter and Dev Container implementation capabilities. One
  execution path must satisfy the complete set; Cloudmake never combines
  partial implementations.
- Qualify the local reference Dev Container CLI over Docker for tagged images,
  image builds, Features, create-phase lifecycle preparation, user/workspace
  selection, literal environment, host requirements, process control, and
  restrictive security policy. Existing digest-only configurations retain the
  portable OCI adapter path.
- Bind Dev Container preparation to the configuration path/content, referenced
  local Dockerfile, implementation contract, and Cloudmake project identity.
  Reuse an unchanged container, reconstruct stale or unreceipted state before
  Make, and preserve the one-target-at-most-once rule.
- Record source image, resolved registry digest or derived image ID, container
  ID, platform, preparation outcome, requirements, and confirmed target
  submission in provenance without taking custody of registry credentials.
- Add a supported, CPU-qualified single-node Google Compute Engine backend for an existing
  VM. It starts or reuses the named instance, adapts `gcloud compute ssh` to the
  common SSH/rsync transport, preserves generated work on attached Persistent
  Disk across stop/start, and never provisions or deletes infrastructure.
- Persist only non-secret project, zone, instance, and optional IAP-routing
  selection. Keep Cloud SDK authentication, OAuth material, service-account
  keys, access tokens, and SSH private keys in their official host clients.
- Classify GCP consistently as paid-tier and report observed machine type,
  accelerator attachment, lifecycle, and conditional `e2-micro` compute
  allowance eligibility before work. Networking, storage, and excess usage
  remain explicitly billable; provider quota/billing errors fail without
  submitting the project target.
- Add fake-provider coverage for lifecycle ambiguity, start/reuse/stop,
  native-disk persistence, the `gcloud` remote-shell adapter, real incremental
  rsync, target-at-most-once provenance, selection persistence, and credential
  exclusion. The live `e2-micro` CPU gate passes. Paid G4 GPU/CDI enablement is
  deferred to the 3.x security model and is not part of the 2.4 contract.
- Wait independently for SSH/IAP readiness after Compute Engine reports a VM
  running, without syncing or submitting the project target during retries.
- Propagate host cancellation through the foreground Make process tree so
  remote locks are released and interrupted provenance remains accurate.
- Harden the Dev Container boundary across every backend: validate descriptor
  consistency, attribute each required behavior to its standard source
  field, prove portable selection and rich-profile rejection across the backend
  matrix, and record the selected execution path plus capability ceilings in
  provenance.
- Keep resource recovery independent from workstation compatibility. A stale
  or newly unsupported saved configuration still cannot start or run work, but
  it can no longer block status, stop, sync, or fetch operations.
- Add a Dev Container execution-landscape tutorial explaining how OCI makes
  application environments portable, how Dev Containers add workstation
  behavior, and how registries, images, engines, low-level runtimes, CDI, host
  capability, and lifecycle divide responsibility.
- Separate persistence into its own tutorial covering session reuse, native
  storage, managed checkpoints, incremental cost, safe publication,
  rebuildability, security, and backend-specific realization.
- Add a four-day remote-workstation learning path and a standalone stateless
  remote-Make tutorial for the 1.x foundation. Keep OCI/CDI and Dev Containers
  together as Day 3; reserve Day 4 for the separate 3.x security model.

## 2.3.1 - 2026-09-10

- Make the local OCI runtime-selection regression hermetic when the test host
  has a working Docker daemon, preserving the intended Docker-to-Podman
  readiness fallback without contacting a registry.

## 2.3.0 - 2026-09-10

- Add explicit `--devcontainer[=PATH]` selection while preserving native Make
  as the default and `--image` as the compatible image-only shorthand. Merely
  committing a Dev Container file does not change execution.
- Consume a fail-closed standard subset: digest-pinned image, literal
  `containerEnv`/`remoteEnv`, CPU/memory/storage/GPU `hostRequirements`,
  loopback-only `forwardPorts`, and CDI names in
  `customizations.cloudmake.devices`.
- Reject unsupported build, Compose, Feature, lifecycle, secret, arbitrary
  mount/run-argument, user, capability, and privilege requirements locally
  before provider contact instead of silently weakening them.
- Align every maintained backend on the profile. Codespaces maps it to the
  provider-native Dev Container; ordinary local and SSH hosts qualify Docker,
  Podman, nerdctl, then the rootless PRoot adapter; Colab uses its qualified
  `crun` profile. The deprecated Kaggle backend remains outside qualification.
- Harden user-managed and provider SSH hosts for privilege-restricted Linux:
  runtime readiness falls through before target submission, PRoot requires no
  daemon/root/user namespace, and Make retains no-new-privileges with current
  UID/GID and explicit CDI translation.
- Validate host requirements on the actual execution machine before Make and
  add bounded local-loopback forwarding for local and SSH foreground targets.
  Colab notebook rejects ports because it has no corresponding tunnel.
- Carry normalized Dev Container metadata through Codespaces rebuild receipts,
  Colab controls, OCI receipts, compact execution banners, and redacted
  provenance without placing environment values in provenance.
- Add focused parser, fail-closed privilege, runtime fallback, SSH tunnel,
  Codespaces translation, Colab control, local execution, and cross-backend
  capability regression coverage.
- Introduce an explicit `unqualified` backend status and apply it to Lightning
  Studio SSH until a successful live release gate is retained. Deprecate paid
  Colab SSH while preserving its compatibility adapter; native Colab and
  conventional host SSH cover the useful execution models without it.

## 2.2.0 - 2026-09-10

- Qualify GitHub Codespaces as the reference CPU remote workstation with
  provider-managed wake/reuse/stop behavior and a stop-persistent `/workspaces`
  tree that avoids checkpoint transfer while remaining disposable and
  rebuildable from source plus Make.
- Add orthogonal backend declarations for lifecycle control, workspace
  durability, and provider-native OCI execution without changing API 1 or the
  target-first CLI surface; existing third-party descriptors receive explicit
  compatibility defaults.
- Make a digest-pinned OCI image the native Codespaces dev-container
  workstation environment. Cloudmake performs one provider rebuild on image
  transition, reuses the same prepared image across targets and stop/start,
  restores the neutral anchor with `--native`, and never launches a nested OCI
  runtime.
- Persist a non-secret Codespace selection locally per project and report
  started versus reused resource state plus rebuilt versus reused native OCI
  environment state in compact execution diagnostics.
- Serialize full operations by Codespace name on the controlling host, record
  pre-rebuild intent for interrupted-transition recovery, fail before source
  synchronization or target submission when preparation is unsuccessful, and
  preserve target exit status through provider-native OCI receipts and normal
  execution provenance.
- Keep Codespaces CPU-only and reject CDI requests before rebuild. The adapter
  adds no privileged mode, capabilities, relaxed security options, host socket,
  or device mount; selected Dev Container image metadata remains trusted input
  under GitHub's provider-native VM boundary.
- Add fake-provider lifecycle, image-transition, recovery, locking, target
  failure, and backward-compatibility coverage plus an opt-in live Codespaces
  workstation gate that restores and stops its resource.
- Harden the live Codespaces transport with current GitHub CLI qualification,
  bounded retries for classified pre-submission control-plane failures, SSH
  connection reuse through a short user-owned control socket, confirmed
  shutdown, and accurate stopped-to-started lifecycle reporting. Project Make
  execution remains once-only and is never covered by these retries.
- Qualify the same provider-native CPU workstation against real ECE326 and
  bounded ECE467 consumers: ECE326 Lab 1 passed 17 tests and a Bottle workload,
  paired Lab 4 passed 36 reference plus 36 candidate tests and all benchmark
  repetitions at Gold, while ECE467 passed its project checks, CPU AXPY/GEMM
  references, and fast llm.c conformance path. The run reused one digest-pinned
  OCI workstation and one stopped/restarted Codespace, and exercised real
  outbound package, Git, and model downloads.
- Qualify inbound workload access through GitHub's authenticated private
  Codespaces port forwarding with a project-provided listener and a unique
  end-to-end response marker. Direct VM ingress remains blocked, public
  visibility remains policy-conditional, and Cloudmake takes no credential
  custody or implicit public-exposure authority.

## 2.1.1 - 2026-09-09

- Inspect digest-pinned images through the selected native runtime after pull,
  so an unrelated host `skopeo` installation cannot add a second registry
  request or change behavior across otherwise equivalent machines.

## 2.1.0 - 2026-09-09

- Add one managed non-native execution surface: digest-pinned OCI images with
  optional standard CDI qualified device requests, while preserving native Make
  as the default and keeping runner, backend, persistence, and source choices
  independent.
- Persist OCI runner selection locally per project with `--image`, `--device`,
  `--no-devices`, and `--native`; project Makefiles remain unchanged and receive
  the same target and user-supplied assignments.
- Validate image digest, Linux platform, runtime, Make availability, and CDI
  device requests before target submission; record architecture comparison and
  selected runtime evidence in the existing single-run provenance while
  allowing the runtime preflight to prove configured architecture emulation.
- Support native Podman, Docker, and nerdctl execution on local and SSH
  backends, with a least-privileged PRoot compatibility fallback and strict CDI
  bind/environment translation.
- Make ordered OCI runtime options an explicit backend property; dynamically
  probe multiple declared host runtimes and fall through when an installed
  client is not operational, without retrying after target submission.
- Add one live-qualified Colab `crun` adapter that materializes OCI rootfs
  layers, adapts the runtime spec to the managed VM's read-only cgroup and
  procfs restrictions, executes Make as UID/GID 65534 with no capabilities and
  `noNewPrivileges`, and supports NVIDIA devices and host drivers through a
  generated CDI specification. Deliberately omit Colab `runc` and bare
  `chroot` alternatives to keep one accelerator-capable maintained profile.
- Retain an experimental Kaggle persistent-workspace adapter that alternates two private
  notebook-output slots, restores the last successful workspace entirely inside
  Kaggle, reconciles current local source, and advances the local head only
  after a successful Make target and validated publication receipt.
- Retain an experimental Kaggle `skopeo` + `umoci` + PRoot OCI profile for trusted computational
  images, including strict `nvidia.com/gpu=all` CDI translation from devices and
  host drivers observed in the actual VM. Cache downloaded runner packages,
  a checksum-pinned current PRoot binary, and the verified OCI layout in the
  private workspace so `session-reuse=no` does not require a cold registry pull
  on every target; discard and reconstruct the derived rootfs to avoid doubling
  checkpoint storage.
- Rehydrate checkpointed virtual-environment links inside the current PRoot
  guest, expose the conventional `/proc` and `/sys` kernel API views required by
  CUDA, and retain UID/GID 65534, empty capabilities, and `noNewPrivileges`.
- Replace backend lifecycle labels with the orthogonal `session-reuse=yes|no`
  property while retaining API-1 lifecycle declarations as compatibility input.
- Add explicit backend status metadata and deprecate Kaggle for remote-
  workstation use after live ECE467 evaluation measured a roughly 14.5 GB
  restore/materialize/publish cycle on every target. Keep the implementation
  available for compatibility and coarse batch experimentation, but remove it
  from release qualification.
- Establish that native workspaces and managed checkpoints are disposable,
  rebuildable acceleration state: source plus Make remain authoritative, and
  persistence must not change target meaning or correctness.
- Distinguish target exit status from OCI infrastructure failure using private
  execution and terminal receipts, including when a project target itself exits
  with `EX_SOFTWARE`/70.
- Extend observed VM characterization with OCI client and CDI evidence while
  retaining active runtime preflight as the compatibility authority.
- Add a digest-pinned ORFS acceptance project whose T4 tool check validates the
  Colab CDI path before real Yosys/OpenROAD stages. The exact `crun` integration
  passed a live non-root T4 smoke test; the full composed replacement-VM
  checkpoint/placement gate remains opt-in because it writes an encrypted
  workspace repository to the user's Google Drive.

## 2.0.0 - 2026-09-07

- Define the storage-neutral Cloudmake 2.0 stateful-workspace and checkpoint
  contract, including source precedence, atomic publication, and failure safety.
- Preserve project-generated files during reusable Colab and SSH source
  reconciliation while removing only paths previously owned by local source.
- Record the Cloudmake 2.0 checkpoint credential-custody contract and gate the
  restic-on-mounted-Drive candidate on non-disclosure and filesystem-reliability
  tests.
- Add opt-in Colab workspace checkpoints backed by an incremental encrypted
  restic repository in the user's Drive. Cloudmake owns the opaque per-project
  repository key in the local OS credential store and sends only per-operation
  encrypted envelopes to the VM; Drive and transient key material are removed
  before project execution.
- Define persistence behavior for every backend: managed checkpoints for native
  Colab, native no-transfer persistence for local and durable SSH workspaces,
  and early rejection for Kaggle batch and Colab SSH. Preserve disabled-by-
  default behavior and the legacy `--checkpoint` option aliases.
- Promote the user-facing abstraction from individual checkpoints to managed
  persistent workspaces, with stable IDs, a local non-secret VM metadata
  registry, explicit list/show/attach/purge operations, and compatibility
  aliases for the earlier checkpoint flags.
- Remove Cloudmake's fixed 50 GiB and one-million-entry workspace ceilings.
  Restore now checks snapshot requirements against the actual free bytes and
  inodes of the allocated VM; Drive and provider capacity remain authoritative.
- Define the durable tool-installation boundary: only the managed project
  workspace is checkpointed, so reusable tool payloads must live there even
  when a project materializes them into an ephemeral execution location.
- Add format-neutral observed VM characterization covering platform, resources,
  privilege, filesystem, namespace, cgroup, device, and accelerator facts.
  Cloudmake 2.0 makes no bundle-compatibility claim; managed OCI/CDI execution
  is explicitly staged as a separate 2.1 capability.
- Freeze the native/OCI runner, persistence, backend, and source axes as
  independent contracts, and add a conservative opt-in ORFS replacement-VM
  acceptance harness for the 2.0 checkpoint gate.
- Pass the live ORFS replacement-VM gate: restore a 3.3 GB workspace in a fresh
  Colab VM, reuse the completed floorplan without rebuilding it, complete
  placement, collect evidence, and publish an incremental successor with about
  4.4 MB of newly added repository data.
- Require a fingerprint-bound success receipt from Colab source reconciliation,
  preventing a provider CLI that masks a remote Python exception from
  dispatching a project target against stale source.

## 1.0.0 - 2026-09-07

- Establish the stable stateless Cloudmake base release. Durable workspaces,
  checkpoints, and cross-VM restoration are outside this release line and begin
  with the stateful 2.0 series.
- Add opt-in, deadline-bounded retry for positively classified Colab allocation
  capacity failures, with exponential backoff, temporary-failure exit status,
  and allocation attempts recorded in the existing run provenance.

## 0.9.1 - 2026-09-07

- Derive the default native Colab session from stable local project identity,
  while preserving explicit `COLAB_SESSION` values and existing v0.9
  `cuda-build` state, with an explicit per-project command to persist migration.
- Replace the two-shot Colab readiness check with configurable deadline-bounded
  polling, cleaning up only a never-ready session created by that invocation
  and never replaying a project target.
- Confirm missing remote Colab control state before treating it as a fresh/reset
  runtime, refuse synchronization when control-state reads are ambiguous, force a
  full stateless source sync, support an optional idempotent session-preparation
  target with source-bound receipts, and record structured lifecycle and
  retry-safety provenance.

## 0.9.0 - 2026-08-28

- Separate expected nonzero Colab Make results from notebook infrastructure
  exceptions, preserving project output and exit status without exposing an
  internal `CalledProcessError` traceback.
- Print compact execution-context banners with the known backend, accelerator,
  resource identity, and started/reused state, and persist explicit accelerator
  selection for subsequent project targets.
- Add the `local` reference backend. Project targets invoke the selected
  project's `Makefile` directly, with no engine dispatch, source transfer,
  session, or injected Make variables, while preserving Cloudmake selection,
  provenance, and target-agnostic artifact collection.
- Define local execution as the semantic baseline that remote backends must
  preserve while adding only transport and provider lifecycle behavior.

## 0.8.0 - 2026-08-25

- Add the `ssh` / `host-ssh` backend and locally persisted `--host` selector for
  incremental execution on any existing user-managed OpenSSH host without
  provisioning, lifecycle control, credential handling, or Git-based transfer.
- Bundle read-only generic, OCI Always Free, and GCP `e2-micro` SSH alias
  templates with installed-runtime listing and rendering commands.
- Document Kaggle's current preinstalled GPU framework/JIT surfaces and clarify
  that its image does not guarantee the standalone `nvcc` compiler.
- Sharpen the project narrative around fragmented accelerator-cloud access and
  distinguish that problem from mature general-purpose CPU cloud tooling.
- State the non-goal principles explicitly, including that Cloudmake preserves
  rather than changes the intended capabilities of backend VMs and runtimes.
- Document transport-aware incremental source synchronization and the separate
  incremental Make behavior available in reusable remote workspaces.

## 0.7.0 - 2026-08-25

- Remove legacy direct-engine `build`, `test`, and `run` shortcuts so every
  project target enters through the single generic dispatch path.
- Make user documentation explicit that example target names are supplied by
  the project's Makefile, and move the internal engine contract to maintainer
  documentation.
- Document Cloudmake's deliberate non-goals and the responsibilities retained
  by projects, provider clients, developers, and cloud services.
- Replace the externally hosted SVG license badge with a portable Apache-2.0
  license link.

## 0.6.0 - 2026-08-25

- Add the `lightning` / `lightning-studio-ssh` backend for persistent CPU and
  GPU Studios through the shared SSH, rsync, remote-Make, and artifact surface.
- Reconcile running Studios onto the requested machine with Lightning's explicit
  switch operation and keep remote state in the documented persistent Studio
  home.
- Keep Lightning login and SSH keys provider-owned, with read-only doctor checks,
  exact SSH-identity references, and offline lifecycle/security regressions.

## 0.5.1 - 2026-08-25

- Remove the hosted live-Colab workflow and its GitHub-secret credential-copy
  path. Live provider gates now run only from a locally authenticated host.
- Add a regression assertion that GitHub workflows never reference repository
  or environment secrets.
- Clarify that Cloudmake delegates authentication to local provider clients and
  never exports, reconstructs, or stores their credentials in hosted CI.

## 0.5.0 - 2026-08-25

- Install the complete Cloudmake runtime instead of only the launcher.
- Add bounded Colab readiness retry without retrying project targets or
  destructively recreating an ambiguously owned session.
- Add artifact file-count, size, archive-size, and expansion-ratio limits.
- Add local execution provenance with hashed Make-assignment values.
- Refuse unmistakable private keys and access tokens before source transfer.
- Prevent failed collection targets from exposing stale artifact archives.
- Add cross-platform CI, scheduled upstream-project checks, a manual live Colab
  gate, and checksummed release automation.
- Add contributor, security, and release policies.

## 0.4.3 - 2026-08-24

- Initial public project with Colab, Kaggle, Codespaces, and paid Colab SSH
  backends; external-project dispatch; artifact collection; resilience controls;
  and real NVIDIA and GPU MODE CUDA regressions.

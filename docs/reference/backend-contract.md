# Backend contract

Cloudmake backends compose up to three independent roles: compute
lifecycle/transport, persistent workspace/checkpoint service, and
application-bundle runtime qualification. Together they expose one execution
model: invoke an ordinary Make target against the selected working tree and
optionally retrieve output. Remote backends synchronize the local tree first.
A backend may explicitly omit any role it cannot provide; one role must not
acquire the semantics or state of another. This document is for Cloudmake
backend authors and maintainers. Project developers should instead read the
[project contract](project-contract.md).

## Project boundary

Every backend must implement the separate [project contract](project-contract.md)
without adding provider-specific project requirements. In particular, a
backend must preserve project-relative paths, dispatch the exact requested Make
target without injecting Make variables, and avoid requiring a provider notebook
or provider-specific Makefile in the project.

Trailing project `NAME=value` arguments must remain opaque to the host engine.
A backend reads its own settings from explicit launcher options or the host
environment, never by interpreting the project's assignment namespace.

Backend lifecycle operations are selected through launcher options such as
`--status` and `--fetch`. A positional name with the same spelling remains a
project target. The target-agnostic `--collect DIR TARGET` operation must validate
`DIR` as project-relative, invoke the exact requested target, archive that
existing directory after success, and transactionally replace the local
`.cloudmake/artifacts/` directory.

## Names and transports

User aliases are short; canonical names identify transport explicitly:

| User alias | Canonical backend | Status | Transport | Persistence mode |
| --- | --- | --- | --- | --- |
| `local` | `local` | supported | Direct project Make invocation | `native` |
| `colab` | `colab-notebook` | supported | Native Colab contents and kernel APIs | `checkpoint` |
| `kaggle` | `kaggle-notebook` | deprecated | Private Kaggle notebook version | experimental `checkpoint` |
| `codespaces` | `codespaces-ssh` | supported | SSH and rsync | `native` |
| `gcp` | `gcp-compute-ssh` | supported (CPU) | `gcloud compute ssh` and rsync | `native` |
| `colab-ssh` | `colab-ssh` | deprecated | SSH and rsync | `unsupported` |
| `ssh` | `host-ssh` | supported | User-managed SSH and rsync | `native` |
| `lightning` | `lightning-studio-ssh` | unqualified | SSH and rsync | `native` |
| `modal` | `modal-sandbox` | unqualified | Modal Sandbox SDK file and exec APIs | `native` (Modal Volume) |

An alias must not silently change transport. In particular, `colab` always
means native notebook access and never falls back to SSH.

## Backend descriptor

Backend source is organized by ownership. The executable launcher lives at
`cmd/cloudmake`; backend-neutral Make, Python, and shell mechanisms live in
`core/`; and each provider implementation lives with its descriptor under
`backend/<backend-id>/`. The descriptor is always
`backend/<backend-id>/backend.mk`. A backend may use a shared core transport,
but provider-specific lifecycle, notebook, checkpoint, and qualification code
must remain in its own directory. This keeps backend boundaries visible without
introducing a registry that maps names to source paths.

Each backend declares:

- the supported backend API version;
- a canonical backend name;
- its `BACKEND_PRODUCT_STATUS`: `supported`, `unqualified`, or `deprecated`,
  plus `BACKEND_PRODUCT_STATUS_REASON` for the latter two;
- whether its execution environment is reusable across target invocations;
- who controls compute lifecycle;
- how long the working filesystem survives;
- whether target replay has proven fencing (`none` by default);
- an ordered set of capabilities, including any persistence and bundle-runtime
  roles;
- an ordered OCI runtime list, or `none`; and
- separate Dev Container capability sets for the Cloudmake adapter and any
  provider/reference-native engine;
- separate public-Internet inbound and outbound declarations; and
- a resource identifier suitable for local serialization.

The shared core validates the descriptor before running an operational target.
Inspect the resolved descriptor with:

```sh
make BACKEND=colab-notebook backend-info
```

`BACKEND_TARGET_REPLAY` is separate from allocation-capacity retry. `none` is
the backward-compatible API-1 default and preserves at-most-once delivery. A
future `fenced` backend must prove that an ambiguous prior attempt cannot
overlap a new attempt; the declaration alone never authorizes replay in a
launcher that lacks the bounded dispatcher. `backend-info` reports the resolved
contract with the v1 vocabulary `target_replay=none`.

Descriptor consistency is also part of the contract. A backend advertising
`devcontainer` must declare at least one nonempty adapter or native semantic
set, while a backend not advertising it must declare neither. A portable
adapter requires `oci-runner` and `image-digest`; declaring adapter
`forward-ports` additionally requires the backend `port-forward` capability.
These invariants are checked both by the shared Make layer and by the launcher,
so an inconsistent descriptor fails before provider contact.
API-1 descriptors that predate both semantic-set declarations remain accepted
with an unqualified empty ceiling; the stricter invariants apply once either
new declaration is present.

Dev Container declarations use
`BACKEND_DEVCONTAINER_ADAPTER_CAPABILITIES` and
`BACKEND_DEVCONTAINER_NATIVE_CAPABILITIES`. Each is either `none` or a closed
list of semantic names such as `image-digest`, `image-tag`, `image-build`,
`features`, `lifecycle-create`, `user-selection`, `forward-ports`, and `cdi`.
They are independent of `BACKEND_OCI_NATIVE`: the former describes standard
behavior Cloudmake can preserve, while the latter identifies who constructs the
OCI workstation. The launcher compares a configuration's complete requirement
set with one engine; it never combines partial engines or silently removes a
requirement.

`BACKEND_DEVCONTAINER_REALIZATION_ORDER` lists `adapter`, `native`, or both in
the order the backend wants them considered. It must name each nonempty
semantic set exactly once. API-1 descriptors that omit it retain the compatible
adapter-then-native order. Codespaces declares only `native`; the local backend
declares `adapter native` so its portable path does not unexpectedly require a
Docker-backed Dev Container CLI.

The normalized requirement set also attributes every semantic requirement to the
standard field or extension path that produced it. Rejection diagnostics and
provenance therefore distinguish, for example, `image-tag (image)` from
`lifecycle-create (postCreateCommand)` instead of reporting an unexplained
backend mismatch.

After a realization passes those requirements, dynamic qualification returns
the selected realization and the evidence proving that it satisfies the
workstation contract. Backend declarations are static capability input;
observed machine properties are live evidence. Neither becomes a second
workload-description language.

`BACKEND_PRODUCT_STATUS=unqualified` identifies an implemented adapter that has
not passed and retained its required live release gate. It is neither a release
claim nor a removal notice. `deprecated` preserves a compatibility
implementation that is no longer recommended. The launcher displays either
status in `cloudmake --backends` and prints the descriptor's reason whenever a
non-supported backend is selected. Neither status silently alters existing
syntax or target dispatch.

Capabilities describe real behavior rather than provider branding. Examples
include synchronization, execution, artifact retrieval, status, opening a web
interface, stopping reusable compute, and an interactive shell. A backend must
not advertise a shell merely because its provider has a browser terminal.

Lifecycle control and workspace durability are separate from session reuse and
checkpoint mode:

| Field | Value | Meaning |
| --- | --- | --- |
| `BACKEND_LIFECYCLE_CONTROL` | `local` | No remote compute lifecycle exists. |
|  | `provider-managed` | Cloudmake may wake, reuse, and stop the named provider resource through its official client. |
|  | `externally-managed` | Cloudmake may connect but must not power or delete the host. |
|  | `per-target` | The provider creates one execution resource for each submitted target. |
|  | `unknown` | API-1 compatibility default for an unqualified third-party backend. |
| `BACKEND_WORKSPACE_DURABILITY` | `ephemeral` | The working filesystem does not survive the relevant VM/session boundary. |
|  | `stop-persistent` | It survives stop/start of the same provider resource, but provider deletion or retention still applies. |
|  | `host-persistent` | Its lifetime belongs to the local or externally managed host. |
|  | `unknown` | API-1 compatibility default for an unqualified third-party backend. |

These fields do not create new lifecycle commands. The common CLI remains
target-first; a provider-managed backend performs any required wake/reuse work
before dispatch. Explicit `--start`, `--status`, and `--stop` remain optional
operations and debugging/cost controls. Native persistence on a
`stop-persistent` workspace performs no transfer.

Public Internet reachability is also independent of the control transport.
Every maintained backend declares `BACKEND_INTERNET_INBOUND` and
`BACKEND_INTERNET_OUTBOUND`; `backend-info` renders them separately as
`internet-inbound` and `internet-outbound`. The vocabulary is:

| Value | Meaning |
| --- | --- |
| `yes` | Part of the qualified backend contract. |
| `no` | Not exposed by the backend. |
| `conditional` | Depends on provider, account, policy, or per-run settings and must be checked when required. |
| `inherited` | Cloudmake uses the already configured local or user-managed host behavior. |
| `unknown` | Not yet qualified; also the API-1 compatibility default for older third-party descriptors. |

“Inbound” means a connection initiated from outside the provider toward a
project-selected workload port. A provider workload-forwarding service counts;
authenticated control channels used only to submit commands, such as
`colab exec`, Kaggle notebook submission, or Cloudmake's SSH transport, do not.
Cloudmake reports these properties; it does not configure firewalls, publish
ports, or promise that every external host is reachable. A conditional outbound
declaration requires a bounded pre-target probe when the selected
invocation explicitly asks Cloudmake for network-dependent preparation. Native
project targets remain opaque; Cloudmake does not infer their network needs
from target names or recipes. A provider metadata request alone is not positive
evidence.

The legacy transport capability named `environment-profile` supplies evidence
for qualification; it is not a workstation contract. It means the backend can
observe the selected execution VM and report the machine,
privilege, filesystem, isolation, device, and accelerator evidence defined by
the backend profile. The broader execution-environment taxonomy and its design
rationale are retained in the maintainer design workspace.
It does not imply support for a particular application format or runner.
Observed properties are not provider guarantees and must not be cached as if
they were promises for a replacement VM.

`oci-runner` is an execution capability, not a persistence mode. A supporting
transport must place the Cloudmake OCI runner beside its own control files,
select an available implementation on the execution VM, perform image/Make/CDI
preflight before target submission, and retrieve the terminal runner receipt.
The requested target may execute at most once. Native execution must remain
byte-for-byte compatible when `CLOUDMAKE_RUNNER=native`; adding OCI support may
not impose an image, runtime dependency, or changed Make command on existing
users. A backend that cannot provide the OCI runner rejects the selection before
provider contact.

All implementations present the project at `/workspace`, use only the OCI
image environment plus explicit Cloudmake command arguments, and distinguish a
runner-infrastructure receipt from the requested Make target's exit status. A
restricted adapter without a native OCI runtime must additionally validate its
mount operations on the actual VM, disable setuid/file-capability elevation,
drop the target to a non-root identity, and document any shared host namespaces.
Observed privilege or an installed command makes such an adapter only a
candidate; it is not a compatibility guarantee.

Every backend must also declare an ordered `BACKEND_OCI_RUNTIMES` list. Use
`none` when OCI is unsupported. The supported runtime vocabulary is `podman`,
`docker`, `nerdctl`, `proot`, and `crun`. For example:

```make
# A host whose installed execution surface must be discovered dynamically.
BACKEND_OCI_RUNTIMES := docker podman nerdctl proot

# A managed notebook with one backend-specific adapter.
BACKEND_OCI_RUNTIMES := crun

# A fresh managed VM with a least-privileged materialization adapter.
BACKEND_OCI_RUNTIMES := proot
```

Every backend also declares `BACKEND_OCI_NATIVE := yes|no`. `yes` means the
provider constructs the execution environment itself from the selected OCI
image; it does not mean that a nested runtime command is present. Codespaces is
the initial native example and therefore declares:

```make
BACKEND_OCI_NATIVE := yes
BACKEND_OCI_RUNTIMES := none
```

With `oci-native=no`, a non-`none` runtime list names Cloudmake adapters inside
the selected environment. With `oci-native=yes`, `none` is not an unsupported
signal: the separate field authorizes provider-native image selection. API-1
descriptors that omit the field retain `no` for compatibility. A native backend
must declare `none` alone; it cannot also advertise a nested runtime path.

The declaration describes mechanisms the backend permits, not commands proven
to work on every instance. Cloudmake rejects selections that no declared option
can satisfy, then probes the actual VM in declaration order. An installed but
unready native runtime is skipped so another declared option can qualify. A
backend-specific entry such as Colab's `crun` means the backend also owns the
exact OCI-spec adaptation, device integration, and security limitations; it is
not a generic endorsement of that command on other managed VMs. The
image-specific preflight remains the final authority and never causes target
replay.

Maintained backends that implement the Cloudmake 2.3 portable subset declare
`devcontainer`. SSH transports that can keep a bounded loopback tunnel for a
foreground target also declare `port-forward`. These capabilities do not add
new transport verbs: the launcher normalizes the selected standard
configuration into the existing image, environment, host-requirement, CDI, and
execution fields. A backend must reject static incompatibilities before
provider contact and live incompatibilities before target submission.

That fail-closed workload boundary must not disable resource recovery. A saved
workstation selection is validated for `--start`, target execution, collection,
and relevant doctor checks. It is not prepared or validated for `--status`, `--stop`,
`--sync`, or `--fetch`; those operations must remain usable when a newer
Cloudmake version no longer accepts an old workstation description.

Cloudmake 2.4 extends this boolean baseline with capability-negotiated standard
behavior. The configuration analyzer produces explicit semantic requirements;
backend descriptors declare the Dev Container behaviors they can implement,
and dynamic qualification verifies the actual instance. These declarations are
orthogonal to transport, persistence, `oci-native`, and the runtime candidate
list. The normative vocabulary, immutable image-resolution rules, credential
boundary, and lifecycle receipt requirements are defined in the
[Dev Container workstation contract](devcontainers.md).

`checkpoint-persistence` means Cloudmake transfers a managed workspace through
an independent durable checkpoint store. `native-persistence` means the
backend's ordinary project workspace already survives its supported stop/start
lifecycle; selecting persistence adds no transfer or checkpoint credential.
A backend advertising neither capability must reject enabled persistence before
provider contact. It must never silently accept the option merely because some
local source archive or provider output happens to be cached.

These modes describe mechanism, not unlimited retention. A native workspace
still disappears if its provider resource or storage is deleted. The launcher
keeps persistence disabled by default for backward compatibility and passes
`CLOUDMAKE_CHECKPOINT=1` to the internal engine only for the managed checkpoint
mode. This variable never enters the project Make assignment namespace.

Kaggle implements checkpoint persistence through two alternating private
kernel-output slots. The next slot attaches only the locally recorded last-good
slot as a provider-side `kernel_source`; a small validated publication receipt
atomically advances the local head. The checkpoint payload is not downloaded to
the host. The backend remains `session-reuse=no`, and provider-private output is
not represented as end-to-end encrypted storage.

Workspace identities are scoped to their persistence adapter. The launcher may
remember several backend-to-workspace mappings for one project, but exactly one
is active with the selected backend. A backend switch must never reinterpret a
Drive repository ID as a Kaggle output slot (or vice versa), and must preserve
released Colab workspace IDs during local configuration migration.

## Session-reuse semantics

Every backend declares exactly one `BACKEND_SESSION_REUSE` value: `yes` or
`no`. It is the only descriptor field for this behavior. “Batch,” “fresh per
target,” and provider-managed release are not additional capabilities. API-1
backends that still declare `BACKEND_LIFECYCLE=local|session|batch` remain
accepted as a compatibility input, but new descriptors must use the direct
boolean property.

A `local` backend declares `session-reuse=yes` because successive targets see
the same machine and working tree, even though it has no remote compute
lifecycle or synchronization boundary. Public project-target execution invokes
the project Makefile directly; lifecycle and synchronization operations report
explicit no-op or readiness semantics. It is the reference behavior that
remote backends must preserve.

A remote backend with `session-reuse=yes` may start or reuse a named VM. It must
reconcile cached identity with live provider state before use and expose honest
`start` and `stop` behavior. For an externally managed host such as `host-ssh`,
`start` means validate the existing execution surface and `stop` must explicitly
preserve the machine rather than claiming lifecycle authority Cloudmake does
not have.

Native Colab derives its default resource identifier from stable local project
identity. An explicit `COLAB_SESSION` remains exact, and existing local state
for the v0.9 `cuda-build` default selects that legacy name. This prevents two
unrelated projects from silently sharing a mutable session while retaining an
intentional shared-session escape hatch guarded by normal ownership checks.
`COLAB_SESSION=NAME cloudmake --use colab` persists a deliberate per-project
replacement; a one-off environment override is not persisted.

A backend with `session-reuse=no` submits a fresh execution environment for
each operational target. `start` may validate readiness, but it must not
pretend to create a reusable VM. `stop` may be a documented no-op when the
provider ends jobs automatically; that is transport behavior and does not need
a separate `release` descriptor.

Such a backend may independently add checkpoint persistence. Restoring a
logical workspace does not change `session-reuse=no`; source staging,
checkpoint restoration, bundle preparation, and checkpoint publication still
have a fixed cost for every project target and must remain visible in context
and provenance. An implementation must not retain a workload credential merely
to imitate reuse.

Capacity retry is an opt-in backend allocation capability, not a wrapper around
an engine operation. A backend supporting `--retry-for` must positively classify
its own provider's temporary-capacity response, bound its wait, and stop retrying
before readiness, transfer, or project execution begins. Backends without such a
classifier reject the option. A transport must never infer retryability from an
arbitrary nonzero command or HTTP status alone.

Readiness polling is a separate pre-execution policy. It may repeat only a
non-mutating remote prerequisite probe and must have a deadline plus a bounded
per-probe timeout. The transport must record whether allocation occurred in the
current invocation and whether target submission is `not_submitted`,
`submitted`, or `ambiguous`. Once submission may have happened, neither
readiness nor capacity policy may replay the project target.

The engine's common lifecycle operations are `start`, `sync`, `status`, `fetch`,
`open`, `shell`, and `stop`; the launcher exposes them as long options. A
positional name with the same spelling remains a project target. Unsupported
operation options fail clearly rather than changing meaning per backend.

## Internal engine dispatch

The `cloudmake` launcher is the only supported project-facing command surface.
The tool's Make engine is an internal backend-maintenance interface. It exposes
provider lifecycle operations and one generic `dispatch` entry point; the
launcher supplies the encoded project target to that entry point.

Transports must not add convenience rules for project-like names such as
`build`, `test`, or `run`. Those names have no Cloudmake meaning and may be used
only when they are supplied by the selected project's Makefile. Maintainer tests
that exercise the engine directly must also enter project execution through
`dispatch`, so the internal test surface cannot accidentally reintroduce a
predefined project-target contract. `tests/fixtures/hello/Makefile` is only
Cloudmake's bundled sample project and does not define the public interface.

## Transport responsibilities

Every remote transport must:

1. gate operations on host prerequisites and a read-only provider probe;
2. use the common source scanner, manifest, exclusions, and size limits;
3. verify project ownership before replacing source;
4. serialize mutations for the selected resource;
5. verify remote execution prerequisites when the environment becomes
   reachable;
6. invoke the selected target through plain Make;
7. retrieve artifacts with safe transactional extraction; and
8. report both provider detail and a normalized cloudmake status.

Reusable transports must also distinguish synchronized source paths from
project-generated paths. Local source additions and modifications win, and
previously synchronized paths deleted locally are removed; a broad mirror
deletion must not erase generated workspace state. Managed checkpoints preserve
the same source-authority rule across replacement sessions.

Before target submission, a session transport may retry only non-mutating
readiness probes and must bound the wait by a deadline. It may automatically
release a never-ready resource only when the current invocation has positive
evidence that it created that resource. A pre-existing unreachable resource
must not be stopped, recreated, or adopted automatically.

Likewise, transport failure is not evidence that a remote control file is
absent. Fresh/reset recovery requires a successful remote probe that positively
reports absence. If the probe says an owner or fingerprint exists but its
contents cannot be downloaded, synchronization must stop as ambiguous.

Once a target request has crossed the execution boundary, a connection failure
is ambiguous. The transport must not replay the target. Failure state records
the phase, normalized provider state, whether the invocation created the
resource, submission certainty (`not_submitted`, `submitted`, or `ambiguous`),
and whether retry is safe.

The local transport is the deliberate exception to remote synchronization,
ownership, locking, and retrieval responsibilities: it operates on the source of
truth itself. It must still preserve exact Make dispatch and apply the same safe,
transactional `.cloudmake/artifacts/` materialization when `--collect` is
requested.

Notebook transports package the selected source and execute provider control
cells. The generated notebook and control code belong to cloudmake rather than
the project. A reusable notebook session can skip upload when the manifest is
unchanged; a batch provider may reuse its local archive but still submits a new
job.

A reusable notebook resource name is not a runtime-instance identity. The
transport must check its remote owner and synchronization control records on
every invocation. Missing control state forces a full source upload and a
reported fresh/reset transition; a foreign owner remains a hard refusal unless
the operator explicitly adopts it.

SSH transports synchronize incrementally with rsync and invoke Make over the
same SSH execution surface. Provider backends supply connection discovery and
lifecycle behavior. The transport must check ownership before applying its
manifest-derived source deletion plan, and synchronize the uploaded project
independently of any anchor repository checked out by the provider.

## State boundary

Generated preferences, fingerprints, notebooks, archives, locks, SSH
configuration, and provider output are tool state. The launcher stores
them in user configuration, state, and cache directories keyed by the local
project identity. They do not belong in either the cloudmake repository or the
actual project repository.

Downloaded project output is different: cloudmake creates and owns the local
project `.cloudmake/artifacts/` directory as the materialized result of
`--collect`.

Configuration precedence is:

```text
command line
  > environment variables
  > per-project local preferences
  > optional shared project configuration
  > global user preferences
  > backend defaults
```

Provider authentication stays with the provider's own client. A backend should
reference credentials or SSH identities when needed, never copy them into
project state or a source archive.

## Adding a remote provider backend

A new remote provider backend should first declare whether its execution
environment can be reused and choose its honest transport. Reuse a shared
transport when its synchronization and execution semantics match; add a new
transport only when the provider surface genuinely differs. The built-in local
backend is the reference adapter described above, not a template for provider
lifecycle code.

The implementation is complete when it has:

1. a canonical, transport-explicit name and optional human-friendly alias;
2. a valid descriptor and capability set;
3. separate, qualified public-Internet inbound and workload-outbound
   declarations;
4. host prerequisite and read-only authentication checks;
5. live status reconciliation;
6. source ownership, locking, transfer, and remote prerequisite handling;
7. target execution and safe artifact retrieval;
8. normalized status mappings; and
9. offline fake-provider tests for successful and failed operations, including
   proof that it does not cross architecture boundaries such as cloning the
   user's project or using SSH from a native-notebook backend.

See [Resilience and recovery](resilience.md) for the required failure behavior
and [Security model](security.md) for trust boundaries.

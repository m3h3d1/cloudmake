# cloudmake

**License:** [Apache License 2.0](LICENSE)

`cloudmake` brings the familiar local Make workflow to the accelerator cloud.
Modern GPUs and other accelerators are expensive and fast-moving, while their
most accessible cloud services are fragmented across notebooks, batch jobs, and
quota-backed development environments. Cloudmake makes a locally executable,
Make-based project work on that hardware—including free-tier services—without
modifying the project. The developer keeps editing and controlling the source
locally while Cloudmake adapts each provider around it.

The intended daily interface is deliberately close to Make:

```sh
cd my-project
cloudmake --use colab --gpu=T4  # once for this project
# These three target names are defined by my-project/Makefile, not cloudmake.
cloudmake compile
cloudmake verify
cloudmake --collect dist export-release
```

The project remains the source of truth. `cloudmake` invokes targets from the
project's normal `Makefile`; remote backends transfer the local working tree and
retrieve selected output around that invocation.
Provider notebooks, transfer logic, session metadata, and generated control files
belong to the cloudmake tool, not to the project.

> [!IMPORTANT]
> The eight documented backends and the external-project `cloudmake` launcher are
> implemented. The regression suite uses offline provider doubles; configure and
> verify your own account with `cloudmake --doctor` before allocating compute.

## Objective

Modern accelerators such as GPUs are expensive, evolve quickly, and are often
outside the practical reach of individual developers. Buying hardware also means
committing to one generation while compilers, drivers, architectures, and
performance characteristics continue to change.

General-purpose CPU cloud has largely converged on familiar Linux VMs,
containers, SSH, and mature infrastructure tooling. The accelerator cloud has
not. GPU type and availability vary; drivers, runtimes, compilers, and hardware
generations are tightly coupled; and the affordable entry points are often
provider-specific notebook sessions, immutable batch jobs, or quota-backed
studios rather than ordinary long-lived machines.

Services such as Colab, Kaggle, and Lightning make accelerator hardware
available free of charge or within limited quotas, but each exposes a different
development lifecycle. One executes notebooks in a reusable session, another
submits fresh batch versions, and another offers a persistent filesystem over
SSH while switching machines. These interfaces break the local
edit-build-test-run loop precisely where developers need to compare and adopt
rapidly changing hardware. CPU services such as Codespaces remain useful as a
portable SSH reference and cross-build surface, but generic CPU cloud is not the
primary problem Cloudmake is designed to solve.

Access to compute should not require moving the source of truth into a provider's
Git service. Cloud development tools often assume that the remote machine will
clone a hosted repository, coupling compute access to repository hosting,
credentials, and provider-specific setup. Notebook templates, special project
files, prescribed directory layouts, and cloud-specific Make targets move even
more provider machinery into the project. The cost is not merely setup time: it
fragments the project's normal local workflow.

Cloudmake is therefore not another general cloud provisioning layer. Its focus
is the awkward boundary between an ordinary local Make project and accessible
accelerator hardware. The desired behavior is simple: make that project work on
the accelerator cloud unmodified. Cloudmake treats the local working tree,
including uncommitted changes and local-only projects, as the source of truth
and adapts each provider around it.

Intrusion-free is a product boundary, not merely a convenience. Cloudmake does
not require provider files, provider-specific Makefiles, fixed target names, a
prescribed source layout, injected Make variables, a Git remote, or committed
source. It adapts itself to the project and keeps provider machinery in the tool.

“Unmodified” describes this integration boundary: cloudmake preserves the
project's Make interface and layout. The selected cloud image must still provide
the toolchain and runtime dependencies that the project itself requires.

```text
local Make project, unmodified
    |
    v
cloudmake: accelerator-cloud adapter for arbitrary project targets
    |
    +-- direct Make ----------> local reference execution
    |
    +-- native notebook API --> Colab or Kaggle
    |
    `-- SSH + rsync ----------> provider sessions or an existing SSH host
                                  |
                                  v
                         evolving GPU/accelerator hardware
```

The design has seven goals:

1. Broaden practical access to modern, rapidly changing accelerators rather
   than duplicate generic cloud infrastructure tooling.
2. Keep adoption intrusion-free: an existing Make project should run unchanged.
3. Preserve the familiar local edit-and-Make workflow; keep the common cloud
   invocation as small as `cloudmake TARGET`.
4. Use plain Make as the common remote execution contract, without reserving
   target names or injecting variables.
5. Keep the local working tree as the source of truth, independent of repository
   hosting and commit state.
6. Preserve backend-specific capabilities through cloudmake options and explicit
   user configuration, never through implicit project requirements.
7. Support reusable sessions, batch notebooks, and SSH VMs without pretending
   that their lifecycles are identical.

Application-bundle execution follows a related portability rule: Cloudmake
reduces an OCI workload to the least-privileged surface needed for automated
computation. The normal contract is an immutable tool image, a writable project
workspace, temporary storage, and only explicitly requested CDI devices. It is
not general-purpose container hosting. Cloudmake does not add privileged mode,
arbitrary host mounts, host credentials, service management, public port
publishing, or nested-container facilities merely to imitate an unrestricted
Docker host. A standard Dev Container profile may explicitly request a bounded
loopback port tunnel; this grants reachability, not additional process authority.

This narrow surface protects both sides of the execution boundary. Users avoid
exposing credentials and unrelated host state to an image; managed-compute
providers are not asked for capabilities, writable kernel control surfaces, or
devices unrelated to the computation. Requiring fewer privileged host features
also lets more managed platforms qualify as Cloudmake backends.

Least privilege describes the authority granted to the workload, not a promise
of strong isolation. A restricted backend may need to share parts of its VM
kernel or namespaces, in which case Cloudmake documents that boundary and may
accept only trusted images. Every additional mount, capability, device,
namespace exception, credential, or host integration must be justified by the
backend, an explicit CDI request, or the core Make execution contract.

## What Cloudmake does not do

Cloudmake is deliberately a narrow host-side dispatcher and transfer layer
around a project's existing Make interface. Three principles govern that
boundary:

1. **Preserve project intent.** Cloudmake adapts itself to the project; it does
   not redefine the project's targets, layout, dependencies, source authority,
   or output semantics.
2. **Preserve backend intent.** Cloudmake does not change the intended
   capability of backend VMs or runtimes. It uses provider-supported execution,
   lifecycle, persistence, and access surfaces as they actually exist. It does
   not turn a notebook into an SSH VM, a batch job into persistent compute, a
   runtime image into a development image, or free-tier access into paid-tier
   entitlement.
3. **Coordinate rather than replace.** Cloudmake synchronizes source, dispatches
   Make, and retrieves selected output. It does not absorb the responsibilities
   of build systems, source control, secrets managers, infrastructure
   provisioners, schedulers, IDEs, or deployment platforms.
4. **Grant only computational authority.** OCI execution supplies the minimum
   mounts, writable paths, process authority, and explicitly requested devices
   needed to run the project target. It does not grow into a general container
   hosting or machine-administration surface.

The detailed exclusions follow from those principles:

| Cloudmake does not | Responsibility remains with |
| --- | --- |
| Provision generic cloud infrastructure, networks, storage systems, or clusters | Cloud providers and established infrastructure tools. |
| Change the intended capability of a backend VM or runtime | The provider's supported image, lifecycle, access method, persistence model, quota, and entitlement remain authoritative. |
| Define `build`, `test`, `run`, or any other project target | The project's Makefile. Every positional target is project-provided. |
| Replace Make or interpret the project's recipes and variables | The project and its chosen build tools. |
| Prescribe source, build, output, or dependency directory layouts | The project. Cloudmake preserves relative paths. |
| Install compilers, CUDA toolchains, libraries, or project dependencies | The selected cloud image or the project's own setup rules. Cloudmake checks only its documented execution prerequisites. |
| Clone, commit, push, or otherwise manage project source control | The developer. The local working tree, including uncommitted files, remains authoritative. |
| Take custody of provider or project credentials, or act as a general secrets manager | Official provider clients and the developer's secret-management mechanism. Cloudmake may manage only its own opaque persistent-workspace encryption key. Selected project files are transferred, so secrets must be excluded from source synchronization. |
| Decide which project output is an artifact | The project chooses and populates a directory; `--collect` only retrieves the explicitly selected directory. |
| Erase differences between reusable sessions, batch jobs, and SSH VMs | Each backend retains its real lifecycle and exposes it consistently through Cloudmake operations. |
| Guarantee free compute, a particular accelerator, runtime duration, persistence, capacity, or price | The cloud provider's current policy, quota, image, and availability. |
| Automatically retry an ambiguously completed project target | The developer must first determine whether the original execution produced side effects. |
| Provide a full remote IDE, job scheduler, or deployment platform | Existing provider interfaces and purpose-built tools. Cloudmake supplies only the documented execution, shell, browser, and artifact surfaces. |

These exclusions are design boundaries rather than missing implicit behavior.
New backends should adapt providers to the same small project contract instead
of changing provider capabilities or expanding Cloudmake into any of these
roles.

## Design

Cloudmake builds a remote-workstation experience around an unmodified local
Make project. Each backend can adapt three independent services:

1. a compute platform, including its allocation, lifecycle, and command
   transport;
2. a persistent workspace or checkpoint service for mutable project state; and
3. an application-bundle runtime for a reproducible professional tool suite.

Persistent state is an acceleration layer, not project authority. A compatible
backend must always be able to rebuild the project from the selected source and
its Makefile when no prior workspace exists. Native persistent filesystems,
managed checkpoints, build caches, downloaded dependencies, and materialized
OCI layers may avoid repeated work, but losing any of them changes cost rather
than target meaning or correctness. Important deliverables must be collected or
stored explicitly; a Cloudmake workspace is not source control or archival
backup.

A backend may implement any subset. Unsupported combinations fail explicitly;
they never acquire accidental meaning. In particular, an OCI registry and its
disposable image cache are not a project checkpoint, durable storage does not
select a runner, and selecting an accelerator does not silently change the
project command.

Session reuse is one backend property rather than a collection of overlapping
lifecycle labels. A backend declaring `session-reuse=yes` can amortize setup
across several targets; `session-reuse=no` means every target receives a new
execution environment. Terms such as “batch” and “fresh per target” describe
that same fact rather than separate capabilities. Adding checkpoint restore may
make the logical workspace durable, but it does not change session reuse or hide
the fixed per-target startup, restore, and publication cost.

Two further properties keep that simple boolean honest without expanding the
daily CLI. Lifecycle control says whether Cloudmake may operate a
provider-managed resource, must leave an externally managed host alone, runs
locally, or submits a per-target job. Workspace durability says whether the
working filesystem is ephemeral, survives stop/start of the same provider
resource, or belongs to an independently managed host. These properties affect
backend behavior and diagnostics, not the project's Make interface.

Network reachability is directional and independent of command transport.
Cloudmake records public Internet inbound access separately from workload
Internet outbound access. An authenticated notebook API or SSH proxy can submit
commands without exposing a public VM endpoint; an environment with no inbound
endpoint may still download dependencies. Conditional provider settings are
treated as requests until the runtime positively demonstrates the needed
outbound path.

Execution composes those services with a fourth independent choice: the source
of the project. Today the local working tree is authoritative; future source
adapters may add other acquisition mechanisms without changing compute,
persistence, or bundle semantics. Native Make remains the default runner.

The capability ladder follows naturally from this model. Cloudmake 2.0 added
opt-in persistent workspaces, preserving project-generated state across
ephemeral accelerator VMs. Cloudmake 2.1 adds one managed non-native runner:
registry-backed OCI images with CDI device requirements, keeping immutable
tools separate from mutable work. Cloudmake 2.2 qualifies provider-managed,
stop-persistent Codespaces as a CPU remote-workstation reference: ordinary
targets wake or reuse the named resource while its `/workspaces` tree avoids
checkpoint transfer, and a selected OCI image becomes the native Codespaces
workstation environment instead of a nested container. Cloudmake 2.3 makes a
fail-closed subset of the standard Dev Container configuration the portable
workstation contract across supported backends, retaining `--image` as its
minimal intrusion-free shorthand. Cloudmake 2.4 adds a single-node Google
Compute Engine CPU remote-workstation backend. It will wake and reuse an already
provisioned VM, preserve its workspace on an attached persistent disk, run the same Dev
Container contract through a qualified standard OCI runtime, and make quota and
billing exposure visible before work begins. The qualified 2.4 profile covers a
conditionally allowance-eligible `e2-micro` CPU host. Paid accelerator machines
such as G4 remain a 3.x target, after the image, driver, CDI, credential, and
device-security boundaries are firm. Google Cloud Storage is no longer a
2.4 objective and may remain a future optional storage adapter rather than a
second Colab execution model. The storage-neutral lifecycle and runtime
contracts are documented in
[Persistence architecture](design/architecture/persistence.md) and
[Execution architecture](design/architecture/execution.md). The
revised release boundary is recorded in the
[GCP qualification](design/qualification.md#gcp-compute-engine).

### Tool repository and project repository are separate

Cloudmake is installed once as a stable tool:

```text
cloudmake/
|-- cmd/cloudmake
|-- Makefile
|-- VERSION
|-- core/
|-- backend/
|-- tests/
|-- docs/
|-- design/
`-- ...
```

An actual project needs only an ordinary Makefile at its root. Everything else
may use any layout:

```text
my-project/
|-- Makefile       # the only required path
`-- ...            # sources, scripts, data, and subdirectories in any layout
```

Cloudmake synchronizes the whole selected project tree while preserving relative
paths. Its remote workspace happens to call the synchronized root `src`, but
that is an internal location and does not require a local `src/` directory.

No provider notebook, Gist ID, Git remote, `Makefile.colab`, or
`Makefile.codespaces` is required in the project. A project may opt into a shared
cloudmake configuration later, but the zero-configuration layout remains the
default.

The cloudmake repository also acts as the neutral GitHub Codespaces anchor. A
Codespace checks out the tool repository only to provision a suitable VM. The
actual project is uploaded from the local computer to a separate workspace and
is never cloned from GitHub by cloudmake.

### Incremental source synchronization

Cloudmake treats transfer time and bandwidth as part of the development loop,
not as an unavoidable full upload before every Make invocation. A common source
manifest gives every remote backend the same selected tree, exclusions,
fingerprint, and change preview. Run `cloudmake --sync-dry-run` on a remote
backend to see added, modified, and deleted paths without authenticating to or
contacting a provider. The local backend reports that no transfer is selected.

The transport then uses the strongest incremental behavior its provider surface
can support:

- The local backend transfers nothing. It invokes the working tree's Makefile
  directly, so the project's existing dependency graph and outputs are already
  available without a synchronization boundary.
- SSH backends use `rsync` to transfer changed source. A manifest-derived
  deletion plan removes only stale synchronized paths, retaining unrelated
  project-generated workspace data.
- A reusable Colab session can skip the source archive upload when both the
  selected tree and owner record are unchanged. When either changes, Colab
  receives a complete validated source snapshot and reconciles it with
  manifest-classified generated workspace data because its native file API does
  not expose an rsync-like delta transport.
- Kaggle starts a fresh batch VM for every submitted target, so remote
  incremental synchronization is impossible. Cloudmake still reuses the local
  compressed snapshot when the source is unchanged, then submits that snapshot
  with the new job.

Source transfer is only the first layer of incrementality. On a reusable
session, Cloudmake invokes successive project-provided targets in the same
remote workspace. Prior outputs that remain in that workspace are therefore
visible to the project's Make dependency graph, which—not Cloudmake—decides
which recipes are already up to date and which must run again. A fresh batch VM
has no previous remote outputs and cannot provide this incremental Make
behavior.

This is deliberately transport-aware: Cloudmake optimizes repeated operations
without pretending that notebook APIs, batch jobs, and SSH offer identical
persistence. Source selection, safety checks, and recovery behavior are
described in [Resilience and recovery](docs/reference/resilience.md).

### Intrusion-free Make contract

Cloudmake invokes an arbitrary target from the project's Makefile. It has no
fixed mandatory target names. The concise rules below are specified fully in
the [project contract](docs/reference/project-contract.md):

| Project contract | Requirement |
| --- | --- |
| Root `Makefile` | Mandatory for every project-target invocation. |
| The target named in `cloudmake TARGET` | Must exist or be resolvable by that Makefile. |
| The target named in `cloudmake --collect DIR TARGET` | Has ordinary Make semantics; after success cloudmake collects project-relative `DIR`. |
| Local `src/`, `build/`, or `output/` directories | Not required. |
| Root `.git/`, `.cloud-state/`, and `.cloudmake/` | Reserved from source synchronization as metadata, legacy tool state, and namespaced downloaded output. |

Cloudmake injects no Make variables. The remote invocation contains the exact
target plus only user-supplied `NAME=value` assignments. For
`--collect DIR TARGET`, the project chooses the project-relative directory and
populates it through its normal rules. After success, cloudmake creates or
transactionally replaces the local `.cloudmake/artifacts/` directory with those
contents.

### Launcher and engine boundary

The `cloudmake` executable is a thin launcher. It discovers the project, resolves
the selected backend, and loads local preferences. Remote operations delegate to
cloudmake's Make-based engine; the local backend deliberately bypasses that
engine and invokes the project Makefile directly. The launcher does not become a
second build system.

Local execution is the reference semantics:

```sh
cloudmake -b local benchmark SIZE=large
# Equivalent project invocation:
make -f Makefile benchmark SIZE=large
```

There is no source scan, archive, synchronization, session, remote workspace, or
backend-variable injection on that path. A remote backend must preserve the same
target and user-supplied assignments while adding only the transfer and lifecycle
machinery its provider requires. Cloudmake still records local provenance, and
`--collect DIR TARGET` still materializes the selected directory safely into the
Cloudmake-owned `.cloudmake/artifacts/` destination.

Every positional name is passed through to the project:

```sh
# clean, benchmark, and test are targets defined by the project's Makefile.
cloudmake clean
cloudmake benchmark SIZE=large
cloudmake -j 8 test
```

Cloudmake reserves no project target names. Even names that resemble its own
operations are unconditionally project targets:

```sh
# These invoke hypothetical project-provided targets named fetch and status.
cloudmake fetch
cloudmake status
```

Cloudmake's own operations use options instead: `cloudmake --fetch` and
`cloudmake --status`. Target names are encoded while crossing the transport, so
spaces, quotes, and other Make-compatible punctuation remain data rather than
shell syntax.

Backend settings remain available through Cloudmake options or the host
environment. They never share the trailing project-assignment namespace:

```sh
COLAB_SESSION=my-project cloudmake -b colab PROJECT_TARGET
SSH_HOST=lab-gpu cloudmake -b ssh --shell
```

Here and throughout this README, `PROJECT_TARGET` is a placeholder for a target
provided by the selected project's Makefile. It is not a target supplied by
Cloudmake.

### State and credentials

Generated state does not belong in either Git repository. The launcher keeps
per-project preferences, fingerprints, generated notebooks, archives, SSH
configuration, and downloaded provider output under the user's configuration,
state, and cache directories. Project identity is derived from the absolute local
path.

Cloudmake owns the project's local `.cloudmake/artifacts/` directory. A successful
`--collect DIR TARGET` creates or transactionally replaces it with the selected
remote directory; the project does not need to create the local destination.
Root `artifacts/` is ordinary project source. During migration, if it still
exactly matches output recorded by an older Cloudmake collection, remote source
transfer fails before provider contact. Review that directory, then move or
delete it, exclude `/artifacts/` in `.cloudmakeignore`, or explicitly accept its
exact known fingerprint as source with
`--accept-legacy-artifacts-as-source`. The complete boundary is specified under
[source selection](docs/reference/project-contract.md#source-selection).

Configuration precedence is:

```text
command line
  > environment variables
  > per-project local preferences
  > optional shared project configuration
  > global user preferences
  > backend defaults
```

The `--host` selection is deliberately local-only: command line and environment
values precede per-project and global user preferences, while repository-shared
configuration cannot select a user's OpenSSH alias.

Cloudmake does not store provider passwords, OAuth tokens, personal access tokens,
or SSH private keys. Authentication remains owned by the `colab`, `kaggle`,
`gh`, `gcloud`, and `lightning` clients or by the user's existing OpenSSH configuration
for a selected host. Source archives and private notebook versions must not be
treated as secret storage. See the
[security model](docs/reference/security.md) for the complete trust boundary.

An opt-in encrypted persistent workspace has one additional piece of Cloudmake-owned
state: an automatically generated per-workspace repository key. It is not a user
credential and is never project configuration. Cloudmake keeps it in macOS
Keychain or Linux Secret Service, transports only a one-use encrypted envelope,
and never prints or asks the user to remember it. Google Drive authorization
continues to belong exclusively to the official Colab CLI.

### Backend naming

Short names are for people; canonical names describe the transport unambiguously:

| User-facing name | Canonical backend | Status | Transport |
| --- | --- | --- | --- |
| `local` | `local` | Supported | Direct project Make invocation |
| `colab` | `colab-notebook` | Supported | Native Colab contents and kernel APIs |
| `kaggle` | `kaggle-notebook` | Deprecated | Private Kaggle notebook version |
| `codespaces` | `codespaces-ssh` | Supported | SSH and rsync |
| `gcp` | `gcp-compute-ssh` | Supported (CPU) | `gcloud compute ssh` and rsync |
| `colab-ssh` | `colab-ssh` | Deprecated | SSH and rsync |
| `ssh` | `host-ssh` | Supported | User-managed SSH and rsync |
| `lightning` | `lightning-studio-ssh` | Unqualified | SSH and rsync |
| `modal` | `modal-sandbox` | Supported | Modal Sandbox SDK file and exec APIs |

`colab` always means native notebook access. It never silently changes to SSH.

## Onboarding

### 1. Install common host tools

Cloudmake targets macOS and Linux. The common host needs:

- POSIX-compatible shell
- GNU Make or a compatible Make implementation
- `tar`
- Python 3 for the current notebook helpers
- One provider CLI where the selected backend requires one
- OpenSSH and `rsync` only for SSH backends

The remote environment needs Make and whatever compiler, runtime, libraries, and
drivers the project itself requires.

### 2. Install cloudmake

Install the launcher from the tool checkout:

```sh
git clone <cloudmake-repository-url>
cd cloudmake
make install
cloudmake --version
```

Installation creates an unprivileged launcher under `~/.local/bin/cloudmake`
and installs its self-contained runtime under `~/.local/libexec/cloudmake/`.
Add `~/.local/bin` to `PATH` if necessary. Before installation, the same
interface is available as `./cmd/cloudmake` from the tool checkout.

### 3. Check provider readiness

The readiness checks are read-only:

```sh
cloudmake --doctor
cloudmake --backends
```

`cloudmake --backends` reports the adapters, directional Internet declarations,
compute-lifecycle control, workspace durability, and whether their main host
clients are installed. `cloudmake --doctor` checks the selected backend's complete local
prerequisites and provider authentication without allocating a VM. It also
prints the installed client version and the client line used for Cloudmake's
latest compatibility validation.

The checker has two levels:

- `prerequisites` validates required local commands, the Python version where
  applicable, backend settings, and readable local files. Every operational
  target is gated by this check.
- `doctor` runs `prerequisites`, then performs a read-only provider probe for
  authentication and access. It never creates, starts, submits, or stops compute.
  Compute and transfer targets pass through this probe before provider work.

Backend maintainers can invoke the same engine gates directly:

```sh
make BACKEND=colab-notebook prerequisites
make BACKEND=colab-notebook doctor
```

Failures identify the missing command or setting and print the backend-specific
installation/authentication hint before any compute operation is attempted.

### 4. Select a backend

Selection is a local preference stored outside the project:

```sh
cloudmake --use colab --gpu=T4
```

Other examples:

```sh
cloudmake --use local
cloudmake --use codespaces
GCP_PROJECT=PROJECT GCP_ZONE=ZONE GCP_INSTANCE=VM cloudmake --use gcp
cloudmake --use ssh --host lab-gpu
cloudmake --use colab --global
```

The retained `kaggle` backend is deprecated and intended only for compatibility
or coarse batch experiments; it is not part of the recommended onboarding path.

Use `-b` for a one-off override without changing the saved preference:

```sh
# verify is a target defined by this project's Makefile.
cloudmake -b kaggle verify
```

An accelerator on the selected backend is also a project preference. For
example, the first command below selects T4 for this project, starts or reuses
the environment, and runs the project-provided `bootstrap` target. The later
targets reuse that selection without repeating `--gpu`:

```sh
export COLAB_SESSION=tilelang-lab
cloudmake --use colab
cloudmake --gpu=T4 bootstrap
cloudmake verify
cloudmake quickstart
```

Without `COLAB_SESSION`, the native Colab backend derives a stable name from
the project directory name and identity, such as `tilelang-lab-a1b2c3d4`.
Different project paths therefore receive different defaults. Existing local
state created with the former `cuda-build` default remains compatible and is
reused with a migration notice.

`--cpu` changes the saved accelerator preference in the same way. An explicit
`-b` remains a one-invocation backend override and does not change saved
selection. Cloudmake reports whenever `--use`, `--gpu`, `--cpu`, or `--start`
changes or establishes reusable project state.

### 5. Run the project

```sh
# compile, verify, and export-release are project-provided Make targets.
cloudmake compile
cloudmake verify
cloudmake --collect dist export-release
cloudmake --history
```

The names in this example belong entirely to the project; any other target works
the same way. Compute commands start or reuse the required environment
automatically. `--collect` changes artifact handling, not the meaning of its
target.
Typing `cloudmake` without a command is read-only: it shows the current project,
selected backend, and concise help. Use `cloudmake --status` when a live provider
status probe is wanted.

At execution time Cloudmake prints one compact context line before project
output. Fields that a backend cannot know reliably are omitted:

```text
[cloudmake] backend=colab-notebook accelerator=T4 session=tilelang-lab resource=started
[cloudmake] backend=colab-notebook accelerator=T4 session=tilelang-lab resource=reused
[cloudmake] backend=local resource=local
[cloudmake] backend=local runner=oci image=registry.example/tools@sha256:... resource=local
[cloudmake] backend=host-ssh runner=oci workstation=devcontainer host=lab-gpu resource=reused
```

`resource=started` means this invocation created or started the resource;
`resource=reused` means it found the existing named resource. This distinction
describes the execution environment, not a requirement of the project target.

## Usage

The intended command form is:

```text
cloudmake [options] TARGET [NAME=value ...]
```

There are no reserved positional commands: `TARGET` always belongs to the
project. Cloudmake operations are option-only. In examples, `PROJECT_TARGET`
means “replace this with a target provided by your project's Makefile”; Cloudmake
does not define a target with that name.

Common options:

| Option | Meaning |
| --- | --- |
| `-b`, `--backend NAME` | Use one backend for this invocation. |
| `-C DIR` | Treat `DIR` as the local project directory. |
| `-j N` | Pass the parallel job count to Make. |
| `--host HOST` | Select a user-managed OpenSSH alias for the `ssh` backend. |
| `--gpu`, `--gpu=TYPE` | Select the default or a named GPU where supported; save it for the selected project unless `-b` is an explicit one-off override. |
| `--cpu` | Select a CPU runtime; save it for the selected project under the same rule. |
| `--persist`, `--no-persist` | Enable or disable the selected backend's persistent-workspace mode and save the choice for later targets. The older `--checkpoint` and `--no-checkpoint` spellings remain compatible aliases. |
| `--devcontainer[=PATH]` | Select a capability-negotiated standard Dev Container workstation; discover the standard project path when `PATH` is omitted. |
| `--image REF@sha256:DIGEST` | Select a digest-pinned OCI image as the project's Make execution environment; this remains the image-only shorthand. |
| `--device CDI_NAME` | Request a CDI qualified device such as `nvidia.com/gpu=all`; repeat for multiple devices. |
| `--no-devices` | Clear saved CDI requests while retaining the selected image. |
| `--native` | Clear the saved OCI image and return the project to native Make execution. |
| `--retry-for DURATION` | Retry only positively classified temporary allocation capacity, for example `30s`, `15m`, or `2h`. |
| `--verbose` | Show provider and transfer commands. |

Cloud operation options:

| Option | Behavior |
| --- | --- |
| `--use BACKEND` | Save the backend and applicable host or accelerator preference. |
| `--workspaces` | List managed checkpoint workspaces known on this computer without contacting a provider. Native backend workspaces remain provider-owned and are not listed. |
| `--workspace show [ID]` | Show locally recorded metadata for the selected or named managed checkpoint workspace. |
| `--workspace attach ID` | Attach the current project to an existing locally known managed checkpoint workspace. |
| `--force --workspace purge ID` | Permanently delete the workspace's Drive repository and Cloudmake-owned local key, then detach it from local projects. |
| `--start` | Allocate or wake a reusable backend when applicable. |
| `--sync` | Synchronize source without running a project target. |
| `--sync-dry-run` | Preview source changes without contacting the provider. |
| `--status` | Show provider and session/job status. |
| `--environment` | Record observed machine, privilege, filesystem, namespace, device, and accelerator facts for the selected local or Colab environment. |
| `--fetch` | Retrieve the latest prepared output. |
| `--collect DIR TARGET` | Run any project target, collect project-relative `DIR`, and fetch it. |
| `--open` | Open the provider's notebook or browser interface. |
| `--shell` | Open an interactive shell on SSH backends. |
| `--stop` | Stop or release reusable compute. |
| `--doctor` | Check local tools and authentication without allocation. |
| `--backends` | List backends and local client availability. |
| `--host-templates` | List bundled OpenSSH alias templates. |
| `--host-template NAME` | Print one bundled alias template without modifying SSH configuration. |

Examples:

```sh
# Work on a project without changing directory; compile is defined by ../solver/Makefile.
cloudmake -C ../solver compile

# Pass settings to the project-provided benchmark target.
cloudmake benchmark DATASET=small DEBUG=1

# Open a real remote terminal where the backend permits SSH.
cloudmake -b codespaces --shell

# Run a project-provided benchmark target on any existing SSH host.
cloudmake -b ssh --host lab-gpu benchmark

# Explicitly release a reusable Colab session.
cloudmake -b colab --stop

# Select a digest-pinned tool image once; verify is project-provided.
cloudmake --use local --image registry.example/tools@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef
cloudmake verify

# Select the standard Dev Container workstation configuration once.
cloudmake --use ssh --host lab-gpu --devcontainer
cloudmake verify
```

## Backend prerequisites

### Local backend

Use `local` as the zero-transfer reference backend or as a persistent local
preference while developing without cloud compute:

```sh
cloudmake --use local
cloudmake --doctor
# benchmark is supplied by the project's Makefile.
cloudmake benchmark SIZE=small
```

It requires only a readable root `Makefile` and GNU Make or a compatible Make
implementation. Project targets run directly in the selected project directory.
`--start`, `--sync`, `--sync-dry-run`, `--status`, and `--stop` report their local
no-op or readiness semantics. `--environment` characterizes the local machine
using the same vocabulary as Colab. There is no provider interface, separate
shell, or fetch operation.
Use `--collect DIR TARGET` when a uniform `.cloudmake/artifacts/`
materialization is useful locally as well as remotely.

The local backend also supports the managed OCI runner when Docker, Podman,
nerdctl, or the `skopeo` + `umoci` + PRoot fallback is installed. Cloudmake
still invokes the project's unchanged Make target, but obtains its tool
environment from the selected image.

A configuration requiring richer 2.4 Dev Container semantics uses the
reference Dev Container CLI and a working local Docker service. Install the CLI
with `npm install -g @devcontainers/cli` (or an equivalent package), ensure
`docker info` succeeds, and run `cloudmake --doctor` after persisting the
Dev Container selection. Digest-only configurations that fit the portable
adapter do not acquire this additional prerequisite.

### Colab native notebook backend

Use this as the normal Colab backend. It uses the official Colab CLI's contents
and kernel APIs rather than SSH, Git, or a Gist.

Prerequisites:

1. A Google account with access to Colab.
2. macOS or Linux. The official CLI currently does not support Windows.
3. The official [`google-colab-cli`](https://github.com/googlecolab/google-colab-cli):

   ```sh
   uv tool install google-colab-cli
   # or
   python3 -m pip install google-colab-cli
   ```

4. Complete the CLI's Google authorization flow on first use, then verify it:

   ```sh
   colab version
   colab sessions
   ```

Persistent workspaces are optional. They additionally require
OpenSSL and a local OS credential store: macOS Keychain on macOS, or
`secret-tool` backed by Secret Service on Linux. Cloudmake checks these local
facilities before contacting Colab when persistence is selected.

Cloudmake uses `colab new`, `sessions`, `upload`, `download`, `exec`, `url`, and
`stop`. The source is fingerprinted before upload. An unchanged tree reuses the
remote source and persistent build directory in the named session.

The official CLI keeps a live runtime assigned through a daemon on the client
computer. That computer must remain awake and online between `--start` and the
last command that expects reuse; host sleep suspends the daemon and Colab may
reclaim the runtime. Cloudmake reports the resulting fresh runtime honestly but
does not replace the provider's keep-alive mechanism. For a bounded unattended
run on macOS, use an explicit `caffeinate` interval and stop the session when the
run completes.

For a normal nonzero project Make result, the notebook preserves the complete
Make stdout/stderr and writes a small result receipt instead of raising a Python
`CalledProcessError`. The local command then prints a concise summary such as
`[cloudmake] target 'gemm' failed with exit status 2` and exits with that status.
The executed notebook and provenance record are still saved and their locations
are printed. Exceptions in notebook setup, synchronization, receipt validation,
or other Cloudmake machinery remain infrastructure failures and retain their
diagnostic exception output.

Colab free-tier accelerator capacity may be temporarily unavailable. Opt into a
bounded allocation wait when that is useful:

```sh
cloudmake --retry-for=30m --start
```

Cloudmake currently retries only a `TooManyAssignmentsError` reported while
creating a new Colab session. It uses bounded exponential backoff with jitter
and exits with temporary-failure status 75 if the deadline expires. Omitting
`--retry-for` preserves immediate failure. Authentication, configuration,
accelerator validation, existing-session readiness, synchronization, artifact,
and project-target failures are never fed into this allocation retry loop. Once
allocation succeeds, the normal workflow continues and the project target is
invoked at most once. The wait remains attached to the foreground command and
does not create a daemon, queue, detached job, or scheduled start.

Colab kernel execution defaults to a 3600-second cloudmake timeout rather than
the CLI's short interactive default. Override it for longer workloads with, for
example, `COLAB_TIMEOUT=7200 cloudmake PROJECT_TARGET`, replacing
`PROJECT_TARGET` with a target from the project's Makefile.

Initial remote readiness has its own bounded policy: a 120-second deadline,
15-second probe timeout, and 3-second polling interval. Tune these with
`COLAB_READY_TIMEOUT`, `COLAB_READY_PROBE_TIMEOUT`, and
`COLAB_READY_POLL_SECONDS`. These retries happen before target submission and
never replay project work.

For idempotent setup that must be restored after Colab replaces a runtime, a
project may opt in with
`COLAB_SESSION_PREPARE_TARGET=prepare-session cloudmake PROJECT_TARGET`.
Cloudmake records successful preparation in the runtime and skips it on reuse.
The receipt is bound to the synchronized source fingerprint, so source changes
rerun the declared idempotent preparation target. A failed receipt transfer
never permits the requested project target to start.
See the [project contract](docs/reference/project-contract.md) before enabling the hook.

Accelerator availability, runtime duration, and usage limits are dynamic and are
not guaranteed. Omitting the GPU requests a CPU runtime. Always stop an unused
session:

```sh
cloudmake -b colab --stop
```

The CLI keeps its own authentication and session information in the user's Colab
CLI configuration; cloudmake does not copy those tokens.

Inspect the selected VM:

```sh
cloudmake --environment
```

This starts or reuses the selected session, but does not synchronize or execute
the project. It reports observed machine, privilege, filesystem, namespace,
device, and accelerator facts and retains a machine-readable profile.
The observation command alone does not infer application-format compatibility
from those facts or from an installed client executable. Observations are not
provider guarantees and may change on a replacement VM. The OCI runner performs
its own execution preflight on every target invocation. The local backend
supports the same observation command for comparison.

`cloudmake --backends` also shows the Dev Container execution-path type,
loopback-port support, ordered OCI runtime options, and separate
public-inbound/workload-outbound Internet declarations for each backend.
Host-oriented backends can declare several choices for dynamic
probing; managed notebook backends can declare one constrained adapter or
explicitly declare OCI unsupported.
The exact per-backend semantic ceiling is documented in the
[Dev Container capability matrix](docs/reference/devcontainers.md#backend-realization).
Cloudmake validates those descriptor claims for internal consistency and names
the originating standard fields in compatibility failures. A rejected or stale
workstation configuration never blocks `--status` or `--stop`.

#### OCI/CDI execution

Cloudmake 2.1 separates immutable tools from mutable project state. Select an
OCI image by exact digest, optionally request devices using standard CDI names,
and continue invoking project-provided targets normally:

```sh
cloudmake --use ssh --host lab-gpu \
  --image registry.example/eda/tools@sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  --device nvidia.com/gpu=all
cloudmake route DESIGN=gcd
```

The runner selection is saved in Cloudmake's local per-project configuration;
it does not modify the repository. `--native` returns to direct Make execution,
and `--no-devices` clears device requests without changing the image. A new
image does not inherit devices from the old image. `--start --image ...` saves
the choice and starts compute, but image preparation waits until the first
project target so it is validated on the actual execution VM.

Before target submission Cloudmake pulls or materializes the exact digest,
checks the image platform, applies CDI requests through a native runtime, and
runs a bounded `make --version` preflight. An incompatible image, missing Make,
or unavailable device is reported as infrastructure failure and the requested
target is not run. After preflight, Make is submitted exactly once and ordinary
target failures retain their exit status and output.

Local and SSH backends prefer Docker, Podman, then nerdctl, with a PRoot
fallback. Codespaces instead declares `oci-native=yes`: the digest-pinned image
is adapted into the provider's dev container and Make runs directly in that
environment. Colab declares exactly one dynamically qualified OCI
runtime: Cloudmake's `crun` adapter. It materializes the digest-pinned rootfs,
uses the namespace and cgroup profile established by live qualification, and
applies NVIDIA devices and host driver libraries from a generated CDI
specification. Make runs as UID/GID 65534 with no capabilities and
`noNewPrivileges`; the image root is read-only while `/workspace` and a fresh
`/tmp` are writable. The deprecated Kaggle backend retains an experimental
PRoot materialization adapter. It
requires no container daemon or privileged namespace operations, maps the
supported CDI subset to explicit PRoot binds and image-environment edits, and
fails before Make if the requested device or registry/network surface is absent.

The registry remains authoritative for immutable layers. Colab and host runtime
caches are disposable. Kaggle retains the verified OCI layout, downloaded
runner packages, and a checksum-pinned current PRoot binary in the private
workspace checkpoint so every fresh VM does not repeat a large registry pull.
It discards the derived root filesystem before publication and rematerializes
it from those layers in each new VM; this reduces checkpoint size but does not
remove per-target unpack time. That cache remains reconstructible and is not an
image authority. The target sees the image's OCI environment, not the host's
environment or credentials. The Colab adapter shares the VM's PID and network
namespaces and uses the host `/proc` because Colab cannot mount the procfs and
cgroup combination expected by stock container profiles. It is not a strong
sandbox for untrusted images. Cloudmake deliberately exposes neither an
alternative Colab `runc` profile—which warned that this no-cgroup mode may stop
working in a future release—nor a bare `chroot` CPU profile that cannot expose
the accelerator motivating the backend. Details, backend coverage, security
boundaries, and the ORFS validation ladder are in the
[OCI/CDI runner guide](docs/reference/oci-runner.md) and
[execution architecture](design/architecture/execution.md). Colab's exact
tested runtime boundary is recorded in
[backend qualification record](design/qualification.md). Kaggle's failed
remote-workstation usability evaluation is preserved as a
[historical backend report](design/history.md#kaggle-notebook), not as a
positive qualification claim.

#### Capability-negotiated Dev Container workstation

Cloudmake 2.4 aligns the maintained backends on a standard project configuration
without replacing the target-first Make interface. Opt in once with
`--devcontainer`; the bare option discovers `.devcontainer/devcontainer.json`
or `.devcontainer.json`, while an explicit project-relative path is accepted.
Configuration presence alone never changes execution, preserving existing
projects and prior CLI behavior.

The portable contract vocabulary includes a digest-pinned `image`, literal
`containerEnv` and `remoteEnv`, basic CPU/memory/storage/GPU
`hostRequirements`, loopback-only `forwardPorts`, and CDI names in
`customizations.cloudmake.devices`; each backend must still qualify every
requested behavior. Richer standard fields become semantic requirements. The
local backend can select the
reference Dev Container CLI over Docker for tagged images, builds, Features,
create-phase lifecycle commands, and user/workspace behavior. Restricted
backends reject static incompatibilities before provider contact and live
machine incompatibilities after allocation but before Make.
Compose, secrets, arbitrary mounts/run arguments, added capabilities, and
privileged mode remain fail-closed. This is capability negotiation, not a
parallel project format.

Codespaces realizes the profile as its provider-native Dev Container. Local and
SSH hosts dynamically qualify Docker first, then Podman, nerdctl, and the
rootless PRoot compatibility path. Colab uses its single declared `crun`
profile. Host requirements are checked on the actual execution machine before
Make. SSH ports exist only as loopback tunnels for the foreground target; they
are not public ingress. The Colab notebook backend rejects `forwardPorts`
because it has no corresponding tunnel. On managed accelerator backends, a
required GPU profile still needs `--gpu` or a saved GPU selection; Cloudmake
does not silently choose a provider product or accelerator model.

See the normative
[Dev Container workstation contract](docs/reference/devcontainers.md) for field
requirements, backend matching, restricted-host behavior, and the security
boundary. Use `--native` to return to direct Make execution.

#### Persistent workspace modes

Persistence is off by default. Unless a project has explicitly selected it,
every backend retains its pre-2.0 execution path and the project receives
exactly the same target and user-supplied Make assignments as before.
Persistence can be enabled only by the user on the command line or through the
resulting local per-project Cloudmake preference. A repository's shared
`.cloudmake.json` and the global preference cannot enable it.

The flag has an explicit backend-dependent implementation:

| Backend | Persistence mode | `--persist` behavior |
| --- | --- | --- |
| `colab-notebook` | `checkpoint` | Restore and publish the encrypted Google Drive workspace described below. |
| `local` | `native` | Use the existing local project tree; no checkpoint transfer occurs. |
| `host-ssh` | `native` | Use the existing remote SSH workspace; Cloudmake does not copy it to another store. |
| `codespaces-ssh` | `native` | Use the Codespace's `/workspaces` storage across stop/start while that Codespace exists. |
| `gcp-compute-ssh` | `native` | Use the attached persistent disk across stop/start; no checkpoint transfer occurs. |
| `lightning-studio-ssh` | `native` | Use the Studio's provider-persistent workspace while that Studio exists. |
| `modal-sandbox` | `native` | Mount a per-project Modal Volume as the workspace so it survives sandbox replacement; no checkpoint transfer occurs. |
| `kaggle-notebook` | `checkpoint` | Alternate two private kernel-output slots; restore the last completed workspace cloud-to-cloud and publish its successor after Make. |
| `colab-ssh` | `unsupported` | Reject because this transport has no cross-VM checkpoint store for the ephemeral Colab VM. |

Native mode is intentionally a Cloudmake data-movement no-op: it records and
reports the selected durability model but adds no Drive mount, key, archive, or
background copy. Provider deletion, expiry, quota, and retention policies still
apply. Switching a project with persistence enabled to an unsupported backend
requires an explicit `--no-persist`, preventing a silent loss of durability.
`cloudmake --backends` reports the mode for every backend.

#### Colab encrypted checkpoints

Enable a persistent workspace once for a project whose generated state should
survive replacement of an ephemeral Colab VM:

```sh
cloudmake --use colab --gpu=T4 --persist
# bootstrap is a target supplied by this project's Makefile.
cloudmake bootstrap
cloudmake verify
```

After every successful project target, Cloudmake incrementally snapshots the
persistent workspace into an encrypted restic repository under the user's own
Google Drive. A newly started VM restores its latest snapshot before
local source is reconciled; a reused VM keeps its existing workspace. Failed or
ambiguous project targets do not publish a new snapshot. `--no-persist` turns
the feature off for subsequent invocations without deleting existing snapshots.

Only `/content/.cloud-build/workspace` is checkpointed. Project-relative build
output survives, but system-wide installs and running processes do not. A
project that needs a toolchain on a replacement VM must save its reusable
payload inside that workspace; Cloudmake does not reinterpret its Make targets
or installation recipes. See [Persistence architecture](design/architecture/persistence.md)
for the full workspace and filesystem rules.

The active workspace stays on the VM's local disk. Drive is mounted only for
restore and publication, and a fresh VM may require browser authorization.
Checkpointing adds fixed provider overhead, so leave it off when rebuilding is
cheaper. The repository key is kept in the local OS credential store;
losing it makes existing snapshots unreadable, though local source is unaffected.
See the [Security model](docs/reference/security.md) for key custody and
failure behavior. Checkpoints are recovery state, not archival backups.

The 2.0 live acceptance gate has restored a 3.3 GB ORFS workspace into a
replacement Colab VM, reused a previously completed floorplan without
rebuilding it, completed placement, collected evidence, and published an
incremental successor that added about 4.4 MB. Exact observations and the gate
contract are recorded in [Persistence architecture](design/architecture/persistence.md).

Cloudmake imposes no fixed byte or file-count ceiling on persistent workspaces.
Before restore it compares the snapshot's logical bytes and entry count with the
actual free disk space and inodes reported by the allocated VM. Publication is
then limited by the VM filesystem, the user's available Google Drive capacity,
and provider quotas; those provider limits remain authoritative.

Each persistent workspace has an opaque 24-character ID independent of the
local project path. Cloudmake keeps a non-secret local registry containing its
backend, requested accelerator, session name, latest known snapshot statistics,
and the CPU, memory, disk, OS, and GPU properties last observed during a
snapshot operation. `--workspace attach` allows a moved or newly cloned project
to resume that durable state explicitly; attachment transfers the local mapping
so one locally known project owns the workspace at a time. `--workspace purge`
is deliberately destructive and therefore requires both the exact ID and
`--force`. Listing, showing, and attaching do not contact a provider. Purging
uses the owning adapter: a Colab runtime for its Drive repository, or the Kaggle
API for the two exact private output slots.

Managed checkpoint stores have backend-distinct workspace identities. Existing
Colab IDs remain unchanged for compatibility; selecting Kaggle derives and
remembers a separate ID for the same project. Switching back restores the prior
backend's mapping rather than treating unrelated Drive and Kaggle state as one
checkpoint.

### Kaggle notebook backend

> **Deprecated and not recommended.** Kaggle remains available for compatibility
> and coarse native batch jobs, but it is not a qualified Cloudmake remote-
> workstation backend. Because `session-reuse=no`, every target repeats VM
> scheduling and—when selected—full checkpoint restore, OCI materialization,
> and checkpoint publication. A live ECE467 workspace moved roughly 14.5 GB per
> target cycle. See the [historical evaluation](design/history.md#kaggle-notebook).

Kaggle is a private batch-notebook backend. Every project-target submission
creates a notebook version and runs in a fresh VM. There is no reusable
interactive session.

Prerequisites:

1. A Kaggle account with notebook access. Complete phone verification for the
   account, and complete Persona identity verification before relying on
   notebook Internet or restricted accelerators. Kaggle may hide the Internet
   control until those account prerequisites are satisfied.
2. Python 3.11 or newer for the current Kaggle CLI.
3. The official [`kaggle` CLI](https://github.com/Kaggle/kaggle-cli):

   ```sh
   uv tool install kaggle
   kaggle auth login
   kaggle --version
   ```

4. Your Kaggle account slug, supplied through the environment or command line:

   ```sh
   export KAGGLE_USERNAME=your-account-slug
   ```

Cloudmake generates a private notebook in its local state, embeds the compressed
source snapshot, submits it with `kaggle kernels push`, waits for completion, and
downloads logs or artifacts through the notebook-output API. The project does not
need a GitHub repository or Gist. `session-reuse=no` remains true even when
persistence is selected: each target still pays Kaggle scheduling and VM startup.

Enable persistence exactly as on another managed backend:

```sh
cloudmake --use kaggle --persist
# provision and verify are project-provided targets.
cloudmake provision
cloudmake verify
```

Cloudmake alternates two private kernel slugs derived from the workspace ID.
Each new version attaches the last completed slot as a `kernel_source`, restores
its checkpoint inside Kaggle infrastructure, reconciles current local source,
runs Make once, and publishes the next slot. The laptop downloads only logs and
small receipts. Ordinary nonzero Make results are receipted, but neither target
failure nor infrastructure failure advances the checkpoint head. This matches
the same successful-target boundary used by Colab. `--workspace show`, `attach`, and destructive
`--force --workspace purge ID` apply to Kaggle workspaces too.

Kaggle target processes receive a workspace-scoped `HOME` plus XDG cache,
configuration, and state directories. A project can therefore keep generated
tool installations outside its source tree in the usual `$HOME/.cache` location
and still recover them in the next fresh batch VM.

Kaggle checkpoints are private provider outputs, not end-to-end encrypted
archives. Kaggle can inspect them under its service boundary. Kaggle credentials
remain only in the host CLI and are never embedded in the notebook or checkpoint.

Although the generated notebook is private, its versions retain uploaded source
in the Kaggle account's version history. Do not include credentials, private keys,
or other secrets in the source tree. Internet is an account- and job-qualified
capability. Cloudmake requests it when configured, probes reachability before
submitting Make, and fails as infrastructure if Kaggle withholds egress. On an
unverified account Kaggle preserved `enable_internet=true` in API metadata but
ran the job without egress; after Persona verification, the same notebook's UI
reported Internet on and the next CLI-submitted job had working DNS and HTTPS.
Metadata alone is therefore still not proof of reachability.

#### Preinstalled Kaggle GPU stack

As of 2026-08-25, Kaggle's official GPU image is built from a pinned Colab GPU
runtime. Its image definition preserves the base image's `torch`, `tensorflow`,
`keras`, and `jax` packages, then installs Kaggle's additional environment and
GPU-specific PyCUDA package. The official image tests exercise the following
GPU surfaces:

| Preinstalled surface | Official GPU-image coverage | Natural batch use |
| --- | --- | --- |
| PyTorch and its Lightning, Ignite, vision, audio, metrics, and tuning ecosystem | CUDA tensors, linear algebra, and recurrent neural-network modules | Training, inference, and framework-managed compilation |
| TensorFlow and Keras | CUDA build, GPU discovery, matrix operations, and model execution | Training and inference |
| JAX and Flax | JAX selects its GPU backend | XLA/JIT workloads |
| CuPy | A custom `ElementwiseKernel` executes on the GPU | NumPy-like GPU code and runtime-compiled kernels |
| Numba | A `numba.cuda.jit` kernel executes on the GPU | Python-authored CUDA JIT kernels |
| PyCUDA | CUDA driver initialization and device discovery | Driver-level Python integration; source compilation is not guaranteed |
| RAPIDS cuDF and cuML | GPU dataframe operations and PCA | Batch dataframe and machine-learning workloads |

Some official tests, including the current cuDF and cuML checks, are exempted on
P100 machines. Package presence therefore does not guarantee that every surface
works on every accelerator Kaggle may offer.

Sources: Kaggle's official
[`Dockerfile.tmpl`](https://github.com/Kaggle/docker-python/blob/main/Dockerfile.tmpl),
[`kaggle_requirements.txt`](https://github.com/Kaggle/docker-python/blob/main/kaggle_requirements.txt),
and [GPU image tests](https://github.com/Kaggle/docker-python/tree/main/tests).

This is an image snapshot, not a permanent Cloudmake guarantee. Kaggle rebuilds
and updates the environment independently. In particular, its image definition
does not install or test `nvcc`, so native CUDA C++ compilation must not assume
that the standalone CUDA compiler is present. Kaggle is therefore best treated
as a framework/JIT or compatible-prebuilt-binary execution backend. The project
should expose its own target for that work; Cloudmake defines no Kaggle-specific
project target.

Kaggle accelerator names are provider-specific. Use the exact identifier exposed
by the installed CLI, for example:

```sh
# PROJECT_TARGET must be provided by the project's Makefile.
cloudmake -b kaggle --gpu=NvidiaTeslaT4 PROJECT_TARGET
```

`start` only verifies authentication, and `stop` is a no-op because Kaggle ends
the batch VM automatically.

The deprecated backend retains an experimental OCI tool-bundle path for
compatibility and investigation; it is not recommended for normal use.
Cloudmake enables
notebook internet for the registry pull, transiently prepares `skopeo`, `umoci`,
a checksum-pinned current PRoot, and `setpriv`, and caches their packages plus
the verified OCI layout inside a selected Kaggle checkpoint. The derived root
filesystem is rematerialized on every fresh VM rather than duplicated in the
checkpoint. The adapter does not claim Docker isolation. With
`--device nvidia.com/gpu=all`, it generates a CDI document from the actually
attached NVIDIA nodes and driver libraries; absent or incomplete attachment is
an infrastructure failure, never a silent CPU fallback. The restricted PRoot
profile also exposes the conventional `/proc` and `/sys` kernel API views
needed by OCI Linux applications while retaining an unprivileged identity,
empty capability sets, and `noNewPrivileges`.

### GitHub Codespaces SSH backend

Codespaces is the standard SSH backend for included-quota or paid CPU VMs. The
cloudmake repository is the neutral anchor repository; the actual project is
uploaded from the local working tree to `/workspaces/.cloudmake/` and is not
cloned from GitHub.

Prerequisites:

1. A GitHub account with Codespaces access and available included quota or a
   configured billing method.
2. A current official [GitHub CLI](https://cli.github.com/). The v2.2 live
   acceptance gate uses the 2.100.x line.
3. GitHub CLI authentication with Codespaces permission:

   ```sh
   gh auth login
   gh auth refresh -h github.com -s codespace
   gh auth status
   ```

4. A Codespace created from the cloudmake anchor repository:

   ```sh
   gh codespace create -r OWNER/cloudmake
   gh codespace list
   ```

5. A working SSH server in the dev container, plus `rsync`, Make, and the desired
   toolchain. The cloudmake anchor will provide these. GitHub's default dev
   container already starts an SSH server; custom images can add the
   `ghcr.io/devcontainers/features/sshd:1` feature. See GitHub's
   [Codespaces CLI guide](https://docs.github.com/en/codespaces/developing-in-a-codespace/using-github-codespaces-with-github-cli).

Verify the connection independently before using cloudmake:

```sh
gh codespace ssh -c CODESPACE-NAME
```

Select an existing Codespace by name and save that non-secret resource choice
for the current project:

```sh
CODESPACE=CODESPACE-NAME cloudmake --use codespaces
# compile is a target supplied by this project's Makefile.
cloudmake compile
```

Source synchronization uses rsync over the SSH configuration produced by
`gh codespace ssh --config`. Cloudmake never commits or pushes the uploaded
project. Every ordinary target first observes the provider state; connecting
wakes a stopped Codespace automatically and the compact context line reports
`resource=started` or `resource=reused`. The project target itself is still
submitted exactly once. Before submission, Cloudmake bounds retries to
positively classified transient GitHub API or SSH-tunnel failures and reuses a
short-lived local SSH control connection. It never applies those retries to the
project target.

Cloudmake keeps its remote tree under `/workspaces`, the
[provider-persistent Codespaces volume](https://docs.github.com/en/codespaces/developing-in-a-codespace/persisting-environment-variables-and-temporary-files).
`cloudmake --stop` stops compute but does not transfer or delete that tree, so
later targets restart the resource and retain incremental Make output and OCI
cache. The provider may still delete the Codespace according to its
[retention policy](https://docs.github.com/en/codespaces/setting-your-user-preferences/configuring-automatic-deletion-of-your-codespaces),
at which point the project must rebuild from local source. This is deliberate:
the remote tree is a disposable acceleration cache, not source control or
archival storage. Stop the Codespace when it is not in use because active and
retained Codespaces consume account quota according to GitHub's current policy.

The included anchor image installs the common SSH tools and an unprivileged
execution identity. With `--image`, Cloudmake derives the Codespaces dev
container from that digest, adds only its SSH/Make transport prerequisites, and
rebuilds once. Later targets reuse the same workstation image. Selecting a new
image rebuilds the tool environment; `--native` restores the neutral anchor.
No Docker-in-Docker, host socket, PRoot, or other nested runtime is used.
Codespaces is qualified as CPU-only, so CDI device requests are rejected before
rebuild. One Codespace has one active workstation image; Cloudmake serializes
its complete operation by resource name on the controlling host. The selected
image is trusted input because GitHub may consume embedded Dev Container
metadata during its provider-native rebuild. See
[Codespaces native OCI qualification](design/qualification.md#codespaces-native-oci-qualification).

Codespaces permits workload connections to the public Internet, and the v2.2
course gate proves that path with ECE326's real package, Git, and model
downloads. Incoming connections are blocked by the VM firewall unless exposed
through GitHub's authenticated or public port-forwarding service; visibility
can also be restricted by organization policy. Cloudmake's authenticated SSH
tunnel is a provider control channel, not public inbound connectivity. A
selected Dev Container's standard `forwardPorts` entries create only bounded
local-loopback tunnels for the foreground target.

A project target may start a listener and the existing GitHub CLI can expose it
through a private authenticated tunnel without changing the Makefile contract:

```sh
# serve is supplied by this project's Makefile and listens on port 8080.
cloudmake serve PORT=8080
gh codespace ports forward 8080:8080 -c "$CODESPACE"
```

The v2.2 live network gate verifies this path end to end without making the port
public. GitHub also supports provider URLs with private, organization, or public
visibility subject to policy. Cloudmake 2.3 consumes standard `forwardPorts` as
private loopback reachability; it does not request public visibility.

### Google Compute Engine backend (2.4 CPU profile)

The `gcp` backend adapts one already-provisioned Compute Engine VM as a
stop-persistent remote workstation. Its offline provider simulation and live
`e2-micro` CPU gate have passed, so the CPU profile is supported. It does not
create a project, network, VM, disk,
IAM binding, billing account, or quota.

Prerequisites:

1. Install the official Google Cloud CLI and authenticate locally with
   `gcloud auth login`.
2. Provision one Linux VM with an attached persistent boot/storage disk, working SSH access,
   and remote Make, `rsync`, and `tar`.
3. For OCI/Dev Container execution, install at least one declared runtime on the
   VM. Docker is preferred; Podman, nerdctl, and the restricted-host PRoot path
   remain ordered fallbacks. A GPU profile additionally needs a working host
   driver and CDI configuration; that path remains dynamically checked but is
   not a qualified 2.4 release profile.
4. Record the non-secret resource coordinates once for the project:

   ```sh
   export GCP_PROJECT=my-project
   export GCP_ZONE=us-central1-a
   export GCP_INSTANCE=cloudmake-workstation
   cloudmake --use gcp
   ```

Later invocations need no repeated resource flags:

```sh
# compile and verify are targets supplied by this project's Makefile.
cloudmake compile
cloudmake verify
cloudmake --stop
```

An ordinary target describes the selected instance, prints its observed machine
type, accelerator attachment, lifecycle state, and billing class, then starts or
reuses it. Cloudmake synchronizes the local source through `gcloud compute ssh`
and rsync, executes the target once, and leaves generated state on the attached
disk. `cloudmake --stop` stops compute and explicitly retains the disk. A later
target starts the same VM and resumes the incremental workspace. `--persist`
therefore records native persistence but transfers no checkpoint.

GCP is always classified as a paid-tier backend because it requires an active
billing account and separately metered resources can apply. Cloudmake reports
when a plain `e2-micro` in an eligible region has a conditionally eligible
compute shape, but that is an allowance property rather than a free-backend
label. External IPv4, disk/network usage outside the allowance, accelerator
quota, and excess usage remain billable. Every GCP start or reuse produces a
compact billing warning, and provider quota errors return without target
submission.

By default, `gcloud compute ssh` uses the VM's normal connection route. For a
private VM reached through Identity-Aware Proxy, save the selection with
`GCP_TUNNEL_THROUGH_IAP=yes`. Cloudmake stores project, zone, instance, and this
boolean locally; it never copies the Cloud SDK configuration, OAuth refresh
tokens, Application Default Credentials, service-account keys, access tokens,
or SSH private keys into the project, VM workspace, provenance, or artifacts.

Inbound and outbound Internet are `conditional`: firewall rules, external IP or
IAP configuration, organization policy, and the selected Dev Container can all
change reachability. Dev Container `forwardPorts` remain local loopback tunnels
for the foreground target and do not create public GCP firewall rules.
The remaining release evidence is tracked in
[GCP qualification](design/qualification.md#gcp-compute-engine).

### Historical Colab SSH backend

The explicit paid-tier `colab-ssh` adapter is deprecated. It is not a fallback
for `colab`, and it does not turn a managed Colab runtime into a full GCP VM.
Native Colab remains the supported restricted-platform path; `ssh` covers an
existing conventional VM, including GCP Compute Engine. The compatibility
adapter and its prerequisites are retained in the
[historical backend report](design/history.md#paid-colab-ssh).

### User-managed SSH host backend

Use `ssh` when the remote machine already exists and you can reach it through
OpenSSH—for example, a university lab server, an organization workstation, an
Oracle or Google free-tier VM, or a rented CPU or GPU machine. Cloudmake treats
that machine as an execution surface only. It never provisions, configures,
starts, stops, or changes the hardware or software capabilities of the host.

Prerequisites:

1. Local OpenSSH, `rsync`, and Python 3.
2. Remote Make, `rsync`, and `tar`, plus the compiler, runtime, libraries, and
   drivers required by the project.
3. A simple OpenSSH `Host` alias in `~/.ssh/config`. Put the username, key,
   jump host, port, and other connection details there rather than in Cloudmake:

   ```sshconfig
   Host lab-gpu
       HostName gpu42.example.edu
       User researcher
       IdentityFile ~/.ssh/id_ed25519
       ProxyJump lab-gateway
   ```

Verify that alias independently, then select it for Cloudmake:

```sh
ssh lab-gpu true
cloudmake -b ssh --host lab-gpu --doctor
cloudmake --use ssh --host lab-gpu
# PROJECT_TARGET must be provided by the project's Makefile.
cloudmake PROJECT_TARGET
```

The saved host is a local per-project preference outside the project tree. A
one-off `--host` switches machines without replacing it:

```sh
cloudmake --host oci-free PROJECT_TARGET
```

`SSH_HOST=lab-gpu` is also accepted as an environment override, but `--host` is
the clearer interactive surface. A repository's `.cloudmake.json` cannot choose
an SSH host because aliases and trust decisions belong to the local user.

Cloudmake installs editable alias templates for a generic Linux host, OCI Always
Free, and a Google Compute Engine free-tier `e2-micro`. List or render them from
either the source checkout or an installed launcher:

```sh
cloudmake --host-templates
cloudmake --host-template generic
cloudmake --host-template oci-always-free
cloudmake --host-template gcp-e2-micro
```

Rendering writes only to standard output. Cloudmake never edits `~/.ssh/config`
or creates a key; the user reviews, edits, and installs a fragment explicitly.
See the bundled [SSH host template guide](backend/host-ssh/README.md) for the safe
copy, `Include`, permission, and first-connection workflow.

Source is synchronized incrementally under `.cloudmake/` in the configured
remote user's login home. `--start` validates the existing connection,
`--status` checks reachability, and `--stop` deliberately reports a no-op because
the machine is user-managed. `--gpu` is not accepted: Cloudmake uses whatever
hardware the selected host already provides and does not alter it.

Cloudmake passes only the selected `SSH_HOST` alias to `ssh` and `rsync`. It
neither reads nor copies SSH keys, agents, certificates, or configuration into
project or tool state. Authentication, host verification, jump-host policy, and
key management remain entirely under OpenSSH and the host administrator or cloud
provider.

The same backend supports the portable Dev Container workstation profile. On a
normal Linux host it tries Docker first, followed by Podman and nerdctl. On a
privilege-restricted host it can fall back to `skopeo`, `umoci`, PRoot, and
`setpriv`: no root login, container daemon, user namespace, or privileged mount
is required. Runtime readiness, host requirements, and CDI edits are validated
before the project target is submitted. A broken installed Docker client may
fall through; a submitted target is never replayed through another runtime.

### Lightning Studio SSH backend

Lightning Studios provide a persistent development filesystem with CPU and GPU
machine switching. This adapter is currently **unqualified**: its offline
provider simulations pass, but no successful live release gate has been
retained. Cloudmake uses the provider's normal SSH surface, so source
updates remain incremental and build outputs survive stop/start cycles. The
actual project still comes from the local working tree; cloudmake does not clone
it from GitHub. Remote project state lives under the provider-documented
persistent Studio home at `/teamspace/studios/this_studio/.cloudmake/`.

Prerequisites:

1. A [Lightning AI account](https://lightning.ai/docs/overview/ai-studio/)
   with Studio access and available credits or quota.
   New accounts may need to finish provider onboarding or verification before
   Studio creation is authorized.
2. The official Lightning SDK/CLI:

   ```sh
   uv tool install lightning-sdk
   lightning login
   lightning auth whoami
   ```

3. A teamspace slug in `OWNER/TEAMSPACE` form and a stable Studio name:

   ```sh
   export LIGHTNING_TEAMSPACE=your-owner/general
   export LIGHTNING_STUDIO=cloudmake-dev
   ```

4. Provider-managed SSH setup. Create or start the Studio once, then let the
   Lightning client establish its own key and SSH entry:

   ```sh
   lightning studio start --name "$LIGHTNING_STUDIO" \
     --teamspace "$LIGHTNING_TEAMSPACE" --machine CPU --create
   lightning ssh configure --name "$LIGHTNING_STUDIO" \
     --teamspace "$LIGHTNING_TEAMSPACE"
   ```

   This creates the provider-owned key at `~/.ssh/lightning_rsa`. Cloudmake
   references that key but never copies it into project, tool, or generated
   source state. Set `LIGHTNING_IDENTITY` only if the provider key is at another
   readable path.

Verify the read-only gate and select the backend:

```sh
cloudmake -b lightning --doctor
cloudmake --use lightning --gpu=T4
# Replace PROJECT_TARGET with a target provided by the project's Makefile.
cloudmake PROJECT_TARGET
```

With no `--gpu`, the backend uses Lightning's 4-CPU `CPU` machine. Accelerator
values are Lightning machine names such as `T4`, `L4`, or `A100`; availability
and cost remain provider-controlled. Each operation starts or reuses the named
Studio on the selected machine. `--stop` releases compute while retaining the
[Studio filesystem and environment](https://lightning.ai/docs/overview/ai-studio/environment-persistence)
and incremental build cache:

```sh
cloudmake -b lightning --stop
```

`--doctor` verifies the installed client, local login, teamspace visibility,
settings, and readable provider SSH key without allocating compute. A provider
eligibility, credit, or capacity failure can only be proven when `--start` or a
project target requests a machine.

### Modal Sandbox backend

[Modal Sandboxes](https://modal.com/docs/guide/sandboxes) provide per-second
CPU and GPU containers (T4 through B200). Cloudmake drives one named sandbox
per project through the official Python SDK's file and exec APIs; there is no
SSH or rsync. The
source is fingerprinted and uploaded as an archive only when it changed, and
project targets run with plain Make under `/cloudmake/src`.

Prerequisites:

1. A [Modal account](https://modal.com/signup). The Starter plan includes a
   monthly credit; GPU, CPU, and memory are billed per second, and Sandbox CPU
   and memory cost more than standard Functions.
2. The official CLI and SDK, then a token:

   ```sh
   uv tool install modal
   modal token new
   modal profile current
   ```

   Cloudmake runs its controller with the interpreter of the `modal` command
   (for `uv tool`, its private environment). Set `MODAL_PYTHON` if the SDK
   lives elsewhere.

Verify the read-only gate and select the backend:

```sh
cloudmake -b modal --doctor
cloudmake --use modal --gpu=T4
# Replace PROJECT_TARGET with a target provided by the project's Makefile.
cloudmake PROJECT_TARGET
cloudmake -b modal --stop
```

Accelerator values are Modal GPU names such as `T4`, `L4`, `A10`, `L40S`,
`A100-40GB`, `A100-80GB`, or `H100`; omit `--gpu` for a CPU sandbox. Defaults
are 1 CPU core and 8 GiB memory (`MODAL_CPU`, `MODAL_MEMORY`). A sandbox stops
itself after `MODAL_IDLE_TIMEOUT` seconds without a running command (default
1800) and after `MODAL_TIMEOUT` seconds in total (default and Modal maximum
86400). A reused sandbox must match the requested accelerator, size, image, and
persistence; otherwise Cloudmake refuses and asks for `--stop`.

The default image is Debian slim with Python, Make, and curl. Set
`MODAL_IMAGE` to a registry reference (for example a CUDA development image)
when the project needs another toolchain; it must provide `sh`, `make`, and
`tar`. This is a backend setting, not the `--image` OCI runner.

`MODAL_APT` and `MODAL_PIP` add space-separated Debian and pip packages to the
image, for example `MODAL_APT=git MODAL_PIP='torch==2.8.0'`. Modal builds the
image once and caches it, so large dependencies such as PyTorch load quickly
instead of being installed into the workspace on each start. Changing either
setting requires `--stop`.

`--persist` mounts the Modal Volume `cloudmake-<sandbox>` at `/cloudmake`, so
project-generated state such as downloaded models or build trees survives
`--stop` and the 24-hour limit. Remove it explicitly with
`modal volume delete cloudmake-<sandbox>`. `--shell` opens `modal shell` in the
running sandbox. `--fetch` never allocates a sandbox. `--image`,
`--devcontainer`, `--retry-for`, and `--environment` are not supported in this
release. Set `MODAL_WEB_PORT=PORT` before starting a sandbox to enable an
authenticated browser route, then run `cloudmake --open` to print a Modal
Connect Token URL. Treat that URL as a credential: it is not recorded by
Cloudmake and must not be shared. Changing the port requires `cloudmake --stop`
before restarting the sandbox.

## Backend behavior summary

The three service-adapter roles are visible in each backend's contract:

| Backend | Status | Lifecycle control | Workspace durability | OCI native | Dev Container | Persistence adapter | Bundle-runtime adapter | Source transfer |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `local` | Supported | Local | Host-persistent | No | Adapter + native CLI | Native tree; no transfer | Docker, Podman, nerdctl, or CPU PRoot; reference Dev Container CLI over Docker | None |
| `colab-notebook` | Supported | Provider-managed | Ephemeral | No | Adapter; no ports | Encrypted Drive checkpoint | Dynamically validated `crun`, including NVIDIA CDI | Fingerprinted archive via Colab API |
| `kaggle-notebook` | Deprecated; not recommended | Per target | Ephemeral | No | No | Experimental alternating private output | Experimental PRoot; NVIDIA CDI subset | Source embedded in private notebook |
| `codespaces-ssh` | Supported | Provider-managed | Stop-persistent | Yes | Provider-native | Native provider workspace | Provider dev container; CPU | Incremental rsync |
| `gcp-compute-ssh` | Supported CPU profile | Provider-managed | Stop-persistent | No | Adapter | Attached persistent disk | Docker, Podman, nerdctl, or PRoot; CDI dynamically checked, G4 unqualified until 3.x | Incremental rsync through `gcloud compute ssh` |
| `colab-ssh` | Deprecated | Provider-managed | Ephemeral | No | Adapter | Unsupported | Docker, Podman, nerdctl, or CPU PRoot | Incremental rsync |
| `host-ssh` | Supported | Externally managed | Host-persistent | No | Adapter | Host filesystem | Docker, Podman, nerdctl, or CPU PRoot | Incremental rsync |
| `lightning-studio-ssh` | Unqualified | Provider-managed | Stop-persistent | No | Adapter | Studio filesystem | Docker, Podman, nerdctl, or CPU PRoot | Incremental rsync |
| `modal-sandbox` | Supported | Provider-managed | Ephemeral; Volume with `--persist` | No | No | Modal Volume | Unsupported | Fingerprinted archive via the Modal SDK |

Network direction is a separate backend property, not an implication of the
transport:

| Backend | Public Internet inbound | Workload Internet outbound |
| --- | --- | --- |
| `local`, `host-ssh` | inherited from the selected host | inherited from the selected host |
| `colab-notebook`, `colab-ssh` | no | yes |
| `kaggle-notebook` | no | conditional; requested per job and probed before a network-dependent target |
| `codespaces-ssh` | conditional on GitHub port-forwarding visibility and policy | yes |
| `gcp-compute-ssh` | conditional on VPC/firewall and forwarding policy | conditional on VPC/firewall and organization policy |
| `lightning-studio-ssh` | conditional on provider configuration | conditional on provider/account policy |
| `modal-sandbox` | no | yes |

Authenticated provider control is not public inbound connectivity. For
example, `colab exec` can submit work over Google's runtime proxy even though
the VM exposes no public listening endpoint. Inspect the declarations with
`cloudmake --backends`, or invoke the maintainer-facing
`make BACKEND=NAME backend-info`; older API-1 third-party descriptors remain
valid and report unqualified lifecycle, workspace, and network properties as
`unknown`.

The roles intentionally converge at the project Makefile, not at their
transport, storage, or bundle layer. Project developers should use the
[project contract](docs/reference/project-contract.md); backend authors and maintainers can
find the adapter interface and extension checklist in the
[backend contract](docs/reference/backend-contract.md).

The local backend is the no-transfer reference path. The Colab backend reuses a
live session and can skip source upload when the fingerprint and owner record
are unchanged. Kaggle reuses its local compressed snapshot when unchanged and
can restore a provider-side logical workspace, but still submits a fresh VM for every target,
represented once as `session-reuse=no`. Its scheduling, restore, and publication
costs therefore remain non-amortized. All SSH backends share the same rsync and
remote-Make transport.

## Reliability and security

Cloudmake protects reusable workspaces with ownership checks, serializes
concurrent operations, validates remote prerequisites, and updates source and
artifacts transactionally. `--workspace attach ID` allows a locally known Colab
checkpoint to be adopted from its recorded previous owner. That permission
ends after successful checkpoint publication. Remote owner-record adoption
outside managed checkpoint attachment uses the conspicuous invocation-only
override:

```sh
# PROJECT_TARGET is a target provided by the project's Makefile.
CLOUDMAKE_ADOPT=1 cloudmake PROJECT_TARGET
```

Do not set this override after a Colab `--workspace attach`: it bypasses the
recorded previous-owner check.

Use `.cloudmakeignore` for source that should never be transferred:

```text
datasets/
*.trace
local-secrets.json
```

Only the root `.git/`, `.cloud-state/`, and `.cloudmake/` paths are excluded
automatically. Cloudmake does not infer that names such as `build/`, `.venv/`,
`src/`, or `*_output.ipynb` are disposable; exclude them explicitly when that
is correct for the project.

Before transfer, Cloudmake refuses unmistakable private-key blocks and GitHub
access-token forms. Credential-like filenames produce a warning. Exclude such
files with `.cloudmakeignore`; for a deliberate test fixture, the conspicuous
one-command override is `CLOUDMAKE_ALLOW_SECRETS=1 cloudmake TARGET`.

`cloudmake --sync-dry-run` reports added, modified, and deleted paths without
authenticating, allocating compute, or contacting the provider. Read
[Resilience and recovery](docs/reference/resilience.md) before adopting a workspace,
adjusting source limits, or recovering an interrupted operation.

The launcher keeps state and cache data in user directories outside both
repositories. Downloaded project output is extracted under the actual project's
`.cloudmake/artifacts/` directory. Artifact archives are bounded by configurable
member-count, total-size, per-file-size, compressed-size, and expansion-ratio
limits before extraction.

Every project execution creates a private local provenance record containing the
backend, remote resource, target, source fingerprint, result, and—when collected—
an artifact fingerprint. Make-assignment names are recorded, but their values are
stored only as hashes. `cloudmake --history` shows the ten latest runs.

Cloudmake verifies workspace ownership before destructive synchronization,
serializes concurrent mutations, reconciles saved sessions with live provider
state, and stages source and artifact replacement so validation failures preserve
the previous valid tree. Operational commands check both host and remote
prerequisites before running project code.

The source tree is still uploaded to and executed by the selected provider.
Cloudmake keeps provider credentials in their official clients, but a private
notebook or ignored filename is not a secrets manager. Released behavior and
operator guidance begin at the [user documentation index](docs/README.md):

- [Day 1: Stateless remote Make](docs/tutorials/day1-stateless.md)
- [Day 2: Workspace persistence](docs/tutorials/day2-workspace.md)
- [Day 3: Dev Container workstations](docs/tutorials/day3-devcon.md)
- [Resilience and recovery](docs/reference/resilience.md)
- [Security model](docs/reference/security.md)
- [Portable Dev Container workstations](docs/reference/devcontainers.md)
- [Project contract](docs/reference/project-contract.md)
- [Backend contract](docs/reference/backend-contract.md)
- [Security reporting](SECURITY.md)

Architecture proposals, decision records, qualification evidence, historical
experiments, and release procedure are indexed separately in the
[maintainer design workspace](design/README.md):

- [Contributing](CONTRIBUTING.md)
- [Release process](design/releasing.md)

## Testing

The default test suite is offline by design. It never connects to a cloud
account or allocates compute. Integration tests place fake `colab`, `kaggle`,
`gh`, `lightning`, `modal`, `ssh`, and `rsync` executables at the front of
`PATH` (plus an offline `modal` SDK double on `PYTHONPATH`), then
exercise the real Makefiles, transports, notebooks, and Python helpers against
temporary projects.

Install the test dependency and run the suite:

```sh
python3 -m pip install -r requirements-test.txt
python3 -m pytest
```

The suite covers portable Make behavior, all eight backends, source selection,
provider lifecycle and failures, prerequisites, ownership, locks, status
normalization, and transactional source and artifact handling. Fake-provider
tests also assert architecture boundaries such as no SSH in the native Colab
flow and no Git clone or push in the Codespaces flow.

An opt-in real-project gate sparse-clones pinned NVIDIA CUDA C++ and GPU MODE
lessons, installs regression-only Makefile overlays, and exercises them without
vendoring either repository. Explicitly enabled live gates run both overlays on
Colab or Lightning T4 sessions. See [`tests/README.md`](tests/README.md) for the
commands and allocation warning.

The separate opt-in Codespaces gate rebuilds an existing anchor from a
digest-pinned public image, proves provider-native image and stop/start reuse,
retrieves an artifact, restores the neutral anchor, and stops the resource. It
consumes Codespaces quota and therefore runs only from an explicitly
authenticated maintainer host. See
[`tests/acceptance/codespaces-workstation`](tests/acceptance/codespaces-workstation/README.md).

Release qualification also runs a bounded CPU-consumer gate against ECE326 and
ECE467. It uses one digest-pinned Python workstation image and one Codespace,
proves image/resource reuse across separate projects, and stops the compute
after ECE326 tests and a real workload plus ECE467 project, CPU AXPY/GEMM, and
fast llm.c checks. See
[`tests/acceptance/codespaces-courses`](tests/acceptance/codespaces-courses/README.md).

The inbound-network gate starts a project-owned listener, reaches its unique
marker through GitHub's authenticated private port forwarding, and stops both
listener and compute. It neither publishes the port nor handles GitHub
credentials. See
[`tests/acceptance/codespaces-network`](tests/acceptance/codespaces-network/README.md).

The v2.3 portable-profile gate runs the same Dev Container configuration through
an existing Linux SSH host. It proves non-root execution, runtime qualification,
incremental state, foreground loopback forwarding, and artifact collection; an
optional strict mode requires the privilege-restricted PRoot path. See
[`tests/acceptance/devcontainer-ssh`](tests/acceptance/devcontainer-ssh/README.md).

GitHub Actions runs only credential-free automation: the offline suite on Linux
and macOS, syntax and notebook checks, and weekly pinned-upstream CUDA project
tests that do not allocate cloud compute. Live provider gates run only from a
locally authenticated host. Stable version tags produce checksummed source
releases only after the offline suite passes again.

`tests/contract/` exercises the public launcher behavior. It specifies backend
aliases, configuration precedence, external project state, `-C`, arbitrary
targets, option-only lifecycle operations, Make-variable passthrough, read-only invocation,
and failure behavior. See [`tests/README.md`](tests/README.md) for focused test
commands.

## Project status

The supported local, Colab notebook, Codespaces SSH, and user-managed host SSH
backends implement the documented launcher, external-project, arbitrary-target,
incremental synchronization, artifact retrieval, prerequisite, ownership,
locking, and recovery contracts. `--collect DIR TARGET` performs target-agnostic
remote export and safe artifact retrieval as one operation; `--fetch` can
retrieve the latest prepared output again.

Lightning Studio SSH remains implemented but unqualified: its offline provider
and shared-transport gates pass, while a successful live release gate has not
been retained. It may be promoted after that gate passes. Paid Colab SSH is
deprecated: native Colab and conventional host SSH cover the useful execution
models without maintaining a second unqualified Colab transport.

Google Compute Engine is supported in v2.4 for the qualified CPU workstation
contract. Its fake-provider
lifecycle, `gcloud` remote-shell adaptation, incremental synchronization,
stop/start disk persistence, and credential-boundary tests pass. The live
`e2-micro` CPU gate also passes. The implementation can dynamically check CDI,
but G4 is not a qualified release profile until the 3.x security model covers
image, driver, device, and credential boundaries. No Google Cloud project or
paid resource is created by the offline suite.

The Kaggle notebook implementation remains available but is deprecated after
live evaluation showed that its fresh-VM-per-target lifecycle makes the
remote-workstation loop impractical. Its retained behavior and evidence are
documented under [historical backends](design/history.md#kaggle-notebook).

Provider quotas, accelerator availability, images, authentication policies, and
billing remain external constraints. Future providers should be added as new
backends against the [backend contract](docs/reference/backend-contract.md), without
changing the project Make surface.

## License

Cloudmake is licensed under the [Apache License 2.0](LICENSE).

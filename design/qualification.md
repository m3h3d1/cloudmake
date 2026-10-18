# Backend qualification records

These records preserve the observed provider boundaries behind Cloudmake's
backend claims. They are maintainer evidence, not a replacement for the stable
user contracts or executable acceptance harnesses.

## Colab OCI/CDI qualification
This section records the live qualification behind Cloudmake 2.1's
`colab-notebook` bundle-runtime property. It is evidence for one maintained
backend profile, not a promise about every future Colab VM image.

### Observed execution surface

The qualification used disposable Colab T4 sessions on 2026-09-08. The observed
VM was Ubuntu 22.04.5 on x86-64 with a 6.6.122 kernel, cgroup v2 mounted
read-only, an NVIDIA T4, driver 580.82.07, and CUDA 12.8 tooling. The VM ran the
notebook process as effective root and permitted bind-mount namespaces, but it
denied a fresh procfs mount and did not provide a writable nested cgroup.

Those restrictions, not the absence of installable packages, determined the
runtime result:

| Candidate | CPU result | NVIDIA/CUDA result | Product decision |
| --- | --- | --- | --- |
| Docker daemon | daemon could start with reduced networking/storage settings; container creation failed on cgroup setup | not reached reliably | unsupported on Colab |
| Podman | CPU execution required disabled cgroups and host namespaces | GPU path conflicted with its PID-namespace requirement | unsupported on Colab |
| containerd/nerdctl | client and daemon started; image unpack/snapshot behavior was not reliable on the VM filesystem | not reached reliably | unsupported on Colab |
| direct `runc` | worked only after the same substantial OCI-spec adaptation | warned that no-cgroup execution may become an error in a future version | unsupported on Colab |
| bare `chroot` | CPU userspace could be entered | no standards-based accelerator injection path | unsupported on Colab |
| direct `crun` | passed with the qualified OCI specification | passed a real CUDA kernel through generated CDI | the sole maintained Colab profile |

Both test sessions were stopped after qualification. Google Drive and
persistent-workspace authentication were not involved.

### Maintained profile

The backend declares:

```make
BACKEND_OCI_RUNTIMES := crun
```

For every preflight and target invocation Cloudmake:

1. installs `skopeo`, `umoci`, and `crun` if the ephemeral VM lacks them;
2. verifies and materializes the digest-pinned Linux image;
3. keeps an OCI mount namespace but removes cgroup, PID, and network namespaces;
4. disables container cgroup creation and binds host `/proc` writable and
   `/sys` read-only;
5. keeps the image root read-only and binds only the project workspace, a fresh
   temporary directory, the result-receipt directory, and selected devices;
6. runs the image process as UID/GID 65534 with empty capabilities and
   `noNewPrivileges`; and
7. for `nvidia.com/gpu=all`, generates a transient CDI JSON specification from
   the allocated VM's NVIDIA device nodes, provider driver libraries, and
   `nvidia-smi` path.

The final replacement-session test compiled and ran a CUDA kernel with the
observable result:

```text
cuda-kernel-value=42 uid=65534 gid=65534
```

The exact Cloudmake integration then ran a project-provided Make target through
the pinned official ORFS image. Preflight selected `crun`; the target verified
UID/GID 65534 and reported `Tesla T4, 580.82.07` through `nvidia-smi`. The
successful invocation reused the qualification session and skipped the
unchanged source upload, confirming that bundle execution does not break
session incrementality. The disposable session was stopped and the provider
reported no active sessions afterward.

The direct CDI adapter supports environment edits, bind mounts, and existing
device nodes. It rejects hooks, node-creation overrides, YAML-only
specifications, unsupported CDI versions or fields, missing sources, and
duplicate destinations. Failure occurs during preflight, before project Make is
submitted.

### Security and compatibility boundary

The outer Colab VM is the security boundary. This profile shares its kernel,
PID namespace, network, and `/proc`; it is not a sandbox for untrusted images.
The image root is immutable for the invocation, the project workspace is the
only general writable host tree exposed, and no host environment or credential
directory is injected.

The runtime cache is ephemeral and may be repopulated from the registry on a
replacement VM. It is not part of a persistent-workspace checkpoint. Mutable
project state and immutable application bundles therefore retain separate
lifecycles and authorities.

Every new VM still receives an active preflight. If Colab changes its VM image,
kernel policy, driver layout, or available privileges, Cloudmake fails closed
instead of falling back to another unqualified Colab profile.

## Codespaces native OCI qualification

GitHub Codespaces is not treated as a generic SSH VM with a nested container
runtime. Its dev container is the provider-managed OCI execution environment.
The backend therefore declares:

```make
BACKEND_OCI_NATIVE := yes
BACKEND_OCI_RUNTIMES := none
```

`oci-native=yes` means the provider constructs the environment from the
selected digest-pinned OCI image. `oci-runtimes=none` means Cloudmake does not
start Docker, Podman, crun, or PRoot inside that environment.

A Codespace has exactly one active workstation image at a time. Cloudmake
therefore serializes image reconciliation, source synchronization, and target
execution by Codespace name, even when multiple local projects select the same
resource from this host. Separate hosts must not concurrently control the same
Codespace.

### Lifecycle

The neutral Cloudmake anchor supplies the initial SSH endpoint. When a project
selects an image, the backend derives a dev-container configuration from that
immutable reference and asks Codespaces to rebuild. The selected image and
adapter revision are recorded in the provider-persistent `/workspaces` tree.
Later targets using the same image skip the rebuild. Selecting another image
rebuilds the environment; selecting `--native` restores the neutral anchor.
The anchor is a dedicated Codespace with one top-level Git checkout under
`/workspaces`; Cloudmake rejects an absent or ambiguous anchor rather than
rebuilding an arbitrary repository.

Cloudmake multiplexes the short-lived SSH operations of one invocation through
a user-owned local control socket. `--stop` closes that socket before requesting
shutdown and returns only after GitHub confirms `Shutdown`; the next target can
therefore report a genuine `resource=started` transition.

Provider state inspection, SSH-configuration discovery, and environment
preparation happen before Make is submitted. Cloudmake retries only positively
classified transient GitHub control-plane or tunnel failures during that safe
pre-submission phase. It does not retry a target after execution begins. A
current GitHub CLI is recommended; the v2.2 live gate was qualified with the
2.100.x line.

The project workspace also remains below `/workspaces`, so an environment
rebuild does not discard source synchronization state or project-generated
files. Those files remain a disposable acceleration cache: source plus Make
must still be sufficient to reconstruct them after provider deletion.

### Network boundary

GitHub documents public-Internet outbound connections as part of the
[Codespaces security model](https://docs.github.com/en/codespaces/reference/security-in-github-codespaces),
so this backend declares `internet-outbound=yes`. The v2.2 consumer gate
exercises that capability through ECE326's actual package, Git, and model
downloads rather than a synthetic connectivity probe.

Codespaces blocks direct incoming Internet connections. Applications can be
exposed through GitHub's
[port-forwarding service](https://docs.github.com/en/codespaces/developing-in-a-codespace/forwarding-ports-in-your-codespace),
whose private, organization, and public visibility options depend on account
and organization policy. The backend therefore declares
`internet-inbound=conditional`. Cloudmake 2.3 maps a selected portable Dev
Container's `forwardPorts` into the provider-native configuration and creates a
local-loopback SSH tunnel while the foreground target runs. It does not request
public visibility. Its authenticated SSH tunnel is a provider control channel
and, by the backend contract, is not public inbound access.

Cloudmake v2.2 qualifies this provider capability through a separate live gate:
a project Make target starts a loopback HTTP listener, `gh codespace ports
forward` creates an authenticated private tunnel, and the host retrieves a
unique marker before both listener and compute are stopped. Public visibility
is deliberately outside the gate. The v2.3 cross-backend gate also verifies the
standard `forwardPorts` mapping; the earlier explicit CLI gate remains useful
provider evidence.

### Adapter boundary

A general OCI image is not automatically a usable Codespaces development
container. The adapter must provide and validate the small Cloudmake transport
surface:

- an SSH server accepted by `gh codespace ssh`;
- a non-root execution identity;
- `make`, `rsync`, and `tar`, or a Debian-compatible `apt-get` from which the
  adapter can install them;
- a Linux image matching the Codespace architecture; and
- no requested CDI device, because the qualified Codespaces backend is CPU.

The adapter may add transport tools while constructing the dev container, but
it does not add application libraries or change the project's Make targets.
Failure to construct or validate this surface is an environment preparation
failure. No project target has been submitted and Cloudmake must not replay it.

### Security boundary

Cloudmake does not request Docker-in-Docker, privileged mode, added Linux
capabilities, host-device mounts, or a host Docker socket. Project Make runs as
the adapter's non-root user inside the Codespaces dev container. GitHub owns
the VM/container isolation and image pull; the GitHub CLI owns authentication.
Cloudmake stores only the non-secret image reference, digest, Codespace name,
and preparation receipt.

The standard OCI image configuration does not by itself declare arbitrary
privilege or hardware requirements. Consequently, `oci-native=yes` is not a
promise that every OCI image is compatible. Cloudmake qualifies the explicit
surface above and rejects unsupported CDI or adapter requirements before Make
execution.

The Dev Container specification also permits an OCI image to carry a
`devcontainer.metadata` label. GitHub may merge that provider-native metadata,
including lifecycle commands or privilege requests, while constructing the
Codespace. Cloudmake adds no privileged mode, capabilities, security-option
relaxation, host socket, or device mount of its own, but it cannot strip or
preflight provider-consumed image metadata before GitHub's build service pulls
the image. A selected workstation image is therefore trusted input and remains
subject to GitHub's policy. This is a documented provider-native boundary, not
the stricter Cloudmake-controlled OCI adapter boundary used on Colab and other
hosts.

### Consumer qualification

The v2.2 release gate uses the same Codespace and digest-pinned Python
workstation image for two independent course consumers. ECE326 runs its clean
Lab 1 setup, 17-test suite, Bottle workload, and paired Lab 4 calibration (36
reference and 36 candidate tests, all repetitions complete, Gold). A bounded
ECE467 CPU path runs all project starter checks, CPU AXPY and GEMM references,
and fast llm.c conformance and checkpoint-reload tests. CUDA, TileLang, model
downloads, and long ECE467 benchmarks are deliberately excluded because this
backend is qualified as CPU-only.

This gate tests application-level use of the provider-native OCI environment;
the separate workstation gate tests image transitions, stop/start reuse,
artifact collection, neutral-anchor restoration, and confirmed shutdown.

## GCP Compute Engine

> **Status:** Qualified for 2.4 as a paid-tier CPU workstation. The offline
> provider simulation and live `e2-micro` CPU gate pass. GPU/CDI is outside the
> 2.4 contract.

The `gcp-compute-ssh` adapter targets one already-provisioned, single-node VM.
It starts or reuses the named instance, reaches it through the official
`gcloud compute ssh` path, synchronizes source with rsync, executes one Make
target, and retains generated work on the attached disk across stop/start. It
does not create or delete projects, networks, VMs, disks, IAM bindings, billing
accounts, or quota.

GCP is classified as paid-tier even when a VM shape may qualify for a
conditional free allowance. An active billing account is required, and IPv4,
storage, transfer, accelerators, and excess compute may be billed. Cloud SDK
authentication, OAuth material, service-account keys, and SSH keys remain with
the official host clients.

The retained 2.4 evidence covers start, incremental source reuse, Dev Container
execution, attached-disk survival across stop/start, outbound access, bounded
inbound forwarding, billing warnings, and clean stop. Release qualification
requires unchanged local, Colab, Codespaces, and host-SSH regression gates plus
a credential scan proving that no Google or SSH credential entered source,
generated control files, logs, provenance, artifacts, or the remote workspace.

The paid G4 harness is retained as a 3.x qualification target. Promoting that
profile must first pass the 3.x security review of trusted images, host drivers,
CDI device mapping, provider credentials, least privilege, billing bounds, and
cleanup. The full VM and adapter retain dynamic CDI qualification; v2.4 simply
makes no release-quality claim for the unexecuted G4 profile.

The executable acceptance procedure lives with the harness under
[`tests/acceptance/gcp-workstation`](../tests/acceptance/gcp-workstation/README.md).

## Modal Sandbox

> **Status:** Qualified as a paid-tier CPU and T4 GPU backend. The offline
> provider simulation and the live gate pass with Modal client 1.6.2.

The `modal-sandbox` adapter drives one named Modal Sandbox per project through
the official Python SDK. It never uses SSH or rsync, and it creates only the
sandbox, the shared `cloudmake` app, and, with `--persist`, the Volume
`cloudmake-<sandbox>`. It never deletes a Volume.

The live gate ran on 2026-10-18:

| Check | Evidence |
|---|---|
| CPU start, reuse, shell, stop | `resource=started`, then `resource=reused` with the source upload skipped; `modal shell` opened; `--stop` terminated the sandbox |
| T4 GPU | `Tesla T4` with a CUDA 12.8 `llama-bench` run (pp512 334 t/s, tg128 27.7 t/s) |
| Volume persistence | after the sandbox ended, a new sandbox reused the Volume: source upload skipped and no model or library download |
| Artifact collection | `--collect` extracted artifacts on both GPU runs |
| Idle stop | a CPU sandbox ended itself 31 minutes after its last command (`MODAL_IDLE_TIMEOUT=1800`) without `--stop` |
| Credentials | no Modal token value in 321 local state, provenance, and artifact files; the Volume held only project files |

Not qualified: multiple GPUs, custom `MODAL_IMAGE` references, `--image`,
`--devcontainer`, port forwarding, `--retry-for`, and `--environment`.

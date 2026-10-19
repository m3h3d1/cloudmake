# Security model

Cloudmake is a source-transfer and remote-execution tool. It reduces accidental
credential coupling, but it does not turn a cloud VM, private notebook, or
unlisted URL into trusted secret storage.

## Trust boundaries

The selected local source is uploaded to a third-party provider or a
user-selected SSH host and executed there. Review the environment's retention,
privacy, access, billing, and acceptable-use policies before using non-public
source or data. Stopping compute does not
necessarily delete notebook history, VM storage, logs, output, or account
metadata.

Provider output is untrusted input when it returns to the host. Cloudmake
validates artifact archive members and replaces the local artifact directory
transactionally, but users must still treat produced executables and data as
code and content generated in a remote environment.

## Credentials

Authentication remains owned by the official provider clients:

- the Colab CLI owns Google authorization and session credentials;
- the Kaggle CLI owns Kaggle authentication;
- the GitHub CLI and OpenSSH own Codespaces authentication and keys;
- OpenSSH owns user-managed host aliases, authentication, keys, agents,
  certificates, host verification, and jump-host configuration; and
- the Lightning CLI and OpenSSH own Studio authentication and keys.

Cloudmake does not store provider passwords, OAuth tokens, personal access
tokens, or SSH private keys. It does not forward GitHub credentials merely to
build a project.

This is a hard product boundary: provider and storage authentication remain in
the official provider client, operating-system credential agent, or an
explicitly configured provider secret facility. Cloudmake must never copy,
serialize, checkpoint, commit, or log the credential material. A backend that
cannot operate without taking custody of such material is not eligible for
integration.

Cloudmake-generated persistent-workspace encryption keys are a separate category. They
are internal product state, not user or provider credentials. Cloudmake may
generate and use them only through the checkpoint key manager defined below; it
must not display them or ask users to create, name, copy, remember, or place them
in project configuration.

This boundary also applies to Cloudmake's own automation. Hosted CI must never
receive a copy of a maintainer's provider configuration or credentials. Live
provider regressions run only from an already authenticated local host; hosted
CI uses credential-free provider doubles and public compatibility checks.

The Codespaces anchor checkout and uploaded project are separate. The anchor
repository provisions a VM; cloudmake does not use its repository token to
clone, commit, or push the actual project.

For provider-native Codespaces OCI selection, Cloudmake requests no privileged
mode, added capability, relaxed security option, host socket, or CDI device.
GitHub's Dev Container implementation may still consume metadata embedded in a
selected image before Cloudmake can inspect the resulting environment. The
digest-pinned workstation image is therefore trusted input governed by the
provider's VM boundary. This is deliberately distinct from a Cloudmake-managed
OCI adapter, whose effective runtime specification Cloudmake can validate
before target submission.

For `host-ssh`, Cloudmake passes the locally selected `SSH_HOST` alias to the
local `ssh` and `rsync` clients. It does not generate SSH configuration, copy
an identity, or administer the selected machine. Access policy and machine
lifecycle remain with the user and host administrator or cloud provider. The
host alias may be saved in local user preferences, but repository-shared
configuration cannot select it.

For `gcp-compute-ssh`, Cloudmake stores only the selected project, zone,
instance name, and IAP-routing boolean in local per-project configuration. The
official `gcloud` client retains authentication and SSH-key custody. Cloudmake
must not read, upload, serialize, or copy Cloud SDK configuration, OAuth refresh
tokens, Application Default Credentials, service-account keys, access tokens,
or SSH private keys. Its generated remote-shell wrapper contains only those
non-secret resource coordinates and invokes `gcloud compute ssh`; it is kept in
the local state directory with user-only permissions.

Bundled host templates are inert examples. Cloudmake can list or print them, but
does not install a fragment into `~/.ssh`, replace existing configuration, set
host-key policy, or generate a key. Users must review and install a fragment
themselves.

## Managed-checkpoint confidentiality and credential custody

Cloudmake's `native` persistence mode uses only the backend's existing project
storage and introduces no checkpoint key, durable-store credential, or transfer.
The encrypted-key custody rules below apply to the `colab-notebook`
managed-checkpoint mode. Its active project workspace remains on the VM's fast
local disk. Google Drive is only the destination of an encrypted backup
repository; Make never runs in the mounted Drive directory.

Persistence is off by default. Only a command-line choice or the resulting
local per-project preference may enable it; checked-in `.cloudmake.json` and
global preferences cannot trigger Drive or credential-store access.

```text
/content/.cloud-build/workspace  -- restic -->  mounted Drive repository
        local Make workspace                     destination only
```

This boundary also limits what is durable. Cloudmake does not snapshot system
directories, the VM user's unrelated home state, or a container engine's
system-owned layer store. A project's expensive reusable tool payload must be
stored beneath the managed workspace, even if the project later materializes it
elsewhere to run. Broadening the snapshot to machine state would mix
provider-owned software, credentials, device configuration, and non-portable VM
state into a project checkpoint, so Cloudmake does not attempt it.

The checkpointed tree remains subject to ownership-aware archive and restore
validation. Local source archives reject traversal, absolute or escaping links,
and special filesystem entries. Remote-generated state may retain arbitrary
symbolic-link targets because restic stores link objects and Cloudmake walks
them with link following disabled. This permits valid package stores and extracted
root filesystems without granting new authority to project code that already
runs as the VM user. Devices, FIFOs, sockets, and other special entries remain
rejected. Ownership and source-manifest records must be regular files, so the
validator never follows a restored control-record link.

Source reconciliation preserves generated link objects without resolving them;
local source still wins every conflict. Artifact collection resolves its
requested directory and refuses it if a generated link escapes the project.
The resulting execution boundary is part of the stable
[OCI/CDI runner contract](oci-runner.md); its architecture rationale is retained
separately in the maintainer design workspace.

## OCI runner credential and isolation boundary

Cloudmake treats least privilege as a two-sided security and portability
contract. It gives an OCI workload only the authority required for automated
computation: its immutable userspace, explicit writable workspace and temporary
paths, and devices requested through CDI. It does not add privileged mode,
arbitrary host mounts, host credential inheritance, service management, public
port publishing, or nested containers merely for compatibility with an unrestricted
container-hosting interface.

The Dev Container profile is parsed locally into explicit semantic
requirements. Privileged mode, added capabilities, relaxed security options,
arbitrary mounts/run arguments, and secret declarations remain fail-closed.
Features and create-phase lifecycle hooks are accepted only by a dynamically
qualified native implementation; restricted adapters reject them before
provider contact.
The portable `forwardPorts` subset creates loopback reachability only for the
foreground target; it does not expose a public listener. Literal environment
entries are project configuration rather than credentials: Cloudmake transports
their values to the target but records only their names in provenance.
Host-variable interpolation is rejected so the adapter never imports a local
secret implicitly.

This reduces exposure of user data and credentials while avoiding requests for
provider capabilities, writable kernel control surfaces, and unrelated devices.
Every exception must be justified by the backend, explicit device request, or
core Make execution contract. Least privilege limits authority but does not
prove strong isolation; the backend's documented kernel and namespace sharing
remains part of the trust decision.

The image-only shorthand and portable adapter select OCI images only by
immutable digest. A dynamically qualified native Dev Container implementation
may accept a standard tag, record the digest actually used by the logical
workstation, and reuse that prepared instance. Registry authentication stays
with the installed official runtime or registry client; Cloudmake does not read,
copy, serialize, or log its credential store. Source archives, runner control
files, notebooks, persistent-workspace snapshots, and provenance contain the
non-secret image reference and selected CDI names, never registry credentials.

Native OCI containers receive no host environment variables from Cloudmake.
The PRoot fallback and Colab `crun` adapter start project Make using only
environment entries from the image's validated OCI runtime configuration plus
safe `PATH` and `HOME` defaults when absent. The Colab adapter keeps the image
root read-only, supplies a fresh writable `/tmp`, mounts the project workspace
writable with `nosuid,nodev`, exposes `/sys` read-only, and runs Make as UID/GID
65534 with empty capability sets and `noNewPrivileges`. NVIDIA device nodes and
host driver files are added only from a generated CDI specification. It does
not expose credential directories or unrelated writable host paths.

That adapter shares the managed VM kernel, PID and network namespaces, and host
`/proc`; it is not a strong sandbox for untrusted images. The official OCI
client itself may use its own
runtime-local registry authentication to pull an image; Cloudmake does not
transport a host credential store to an ephemeral VM. Client custody is
distinct from target-environment injection.

CDI requirements are passed only to a runtime path capable of resolving them.
The Colab adapter accepts only the strict CDI JSON edits it implements; unknown
hooks or fields fail closed. Missing or ambiguous device support fails preflight
and never degrades silently to CPU.

The selected checkpoint engine is restic because content-defined chunking,
snapshot encryption, integrity checks, and interruption handling are backup
responsibilities rather than Cloudmake synchronization features. The initial
Colab design deliberately does not use rclone: copying an rclone configuration,
OAuth refresh token, or service-account key into an ephemeral VM would violate
Cloudmake's credential boundary.

The required authentication flow is:

1. Cloudmake invokes the official `colab drivemount` command in the foreground;
   that CLI performs the user-authorized Drive mount and retains ownership of
   Google authentication;
2. Cloudmake creates one random repository key and stores it under the stable
   persistent-workspace identity in the local operating-system credential store;
3. for each restore or publication, the VM creates an ephemeral transport key
   pair and returns only its public key;
4. the local credential-store helper encrypts the repository key to that public
   key and emits only ciphertext;
5. the VM decrypts the envelope directly into restic's password-command pipe,
   then deletes the envelope and transport private key before Make runs; and
6. missing Drive authorization, missing keychain access, a repository/key
   mismatch, or an unavailable secure transport fails closed. There is no
   empty-password, plaintext argument, environment-variable, notebook, or file
   fallback.

The user-facing abstraction is an encrypted Cloudmake persistent workspace. “Restic
password” is an implementation term confined to architecture and security
documentation. Normal setup generates the key automatically and may report only
its non-secret keychain identity and status. It never prints or prompts for the
key.

The September 2026 live spike confirmed that a fresh VM may require one browser
authorization initiated and completed through the local CLI. No notebook edit
is involved. Once authorized, that VM can be unmounted and remounted without a
second prompt, so Drive can be absent while project Make runs. A replacement VM
requires authorization again; Cloudmake must not describe that recovery path as
unattended. The CLI, rather than Cloudmake, handles the Google credential and
its propagation.

Colab's notebook `google.colab.userdata` secret resolver is not available to
CLI-created sessions. The CLI's `--env KEY=VALUE` substitute is not an acceptable
checkpoint-password channel: it places the value in the local process arguments,
injects the literal into executed Python, retains that code in the CLI JSONL
history, and leaves the value in the kernel environment. Cloudmake must not use
this interface for credentials even if it is convenient for non-secret runtime
configuration.

To avoid unnecessarily expanding the permissions available to project code,
the intended lifecycle mounts Drive for restore, unmounts it before source
reconciliation and Make execution, and mounts it again only after a successful
target to publish the checkpoint. If the provider cannot support that separation
reliably, the backend must remain unavailable rather than leaving the user's
Drive mounted during arbitrary project execution.

Cloudmake may retain non-secret metadata such as a persistent-workspace ID,
snapshot ID, requested accelerator, observed VM specification, Drive mount path,
and symbolic keychain item name. The local workspace registry contains no
provider credential or plaintext repository key.
Provider credentials and plaintext checkpoint keys must not appear in generated
notebooks, process arguments, process environments, control files, source or
artifact archives, checkpoint manifests, provenance, logs, tracebacks, caches,
tests, or hosted CI configuration. The encrypted transport envelope may exist
only for the duration of one checkpoint operation and is never archived.

Purging a persistent workspace is an explicit two-sided deletion: Cloudmake
first removes the exact ID-scoped encrypted repository through the
user-authorized Drive mount, then removes that workspace's key from the local
credential store. It never performs wildcard repository deletion. If either
side cannot be verified, the operation reports an infrastructure failure rather
than claiming that the workspace was fully purged.

Checkpoint contents are always encrypted before being committed to durable
storage. Secret scanning and exclusion rules remain defense in depth; they
cannot prove that arbitrary project output contains no unknown secret. Cloudmake
therefore promises non-disclosure by its own control and storage paths, not that
an untrusted Makefile cannot deliberately reveal data using the VM permissions
the user granted it. Executing project code remains an explicit trust boundary.

The DriveFS/restic storage portion of the September 2026 spike passed initial
and incremental backup, repository check, same-session restoration, restoration
after VM replacement, and forced interruption without publishing a partial
snapshot. The feature branch now implements the keychain/envelope channel, and
focused tests prove its normal plaintext route is limited to local process
memory and pipes while persisted transport material is ciphertext. The
non-disclosure gate verifies a sentinel key is absent from generated files and
captured output across success, failure, cancellation, traceback, and
interrupted publication paths. That gate and the live replacement-VM acceptance
have passed; future backends must satisfy the same contract rather than weaken
it.

### Kaggle provider-private checkpoints

This deprecated backend is retained for compatibility and coarse batch
experimentation, not recommended remote-workstation use. Kaggle uses a distinct
provider-private checkpoint boundary. Cloudmake
alternates two private kernel-output slots and connects the last completed slot
to the next job through Kaggle's `kernel_sources` mechanism. The checkpoint
payload never traverses the laptop, and the Kaggle CLI retains sole custody of
Kaggle credentials; no credential or token is embedded in the notebook,
control record, archive, receipt, or provenance. Unlike the Colab/Drive store,
the payload is not end-to-end encrypted by Cloudmake. Kaggle and anyone granted
access under the account's provider policy can inspect it. Enabling this mode
therefore requires `KAGGLE_PRIVATE=true` and remains an explicit, off-by-default
choice.

Only a successful Make target may advance the locally recorded Kaggle
checkpoint head. Target failure, infrastructure failure, missing output, a
mismatched workspace or slot receipt, and checkpoint digest failure leave the
previous head unchanged. Each restore validates the exact workspace identity,
source slot, and streaming SHA-256 before extraction. Archive traversal,
special files, and unsafe hard links are rejected; symbolic-link objects and
valid hard links are restored without following them outside the staged tree.

Kaggle's PRoot OCI adapter provides compatibility, not a security boundary. It
runs a trusted image userspace as UID/GID 65534 with a clean image-derived
environment, the project workspace, and only CDI-requested host device/library
bindings. It does not add container namespaces, privileged mode, arbitrary
mounts, credential inheritance, or port publishing. The outer Kaggle VM remains
the isolation boundary.

## Source hygiene

Never put credentials, private keys, API tokens, build secrets, or sensitive
datasets in synchronized source. Use `.cloudmakeignore` to exclude local files
that never belong on a provider:

```text
.env
keys/
private-data/
```

This is a convenience boundary, not a secrets manager. Review the effective
selection with `cloudmake --sync-dry-run` before the first upload and after changing
exclusions.

Cloudmake scans selected regular files before packaging. It refuses unmistakable
private-key blocks and GitHub access-token forms and warns about common
credential-like filenames. The scan is intentionally conservative and cannot
identify every secret. An intentional fixture can use `CLOUDMAKE_ALLOW_SECRETS=1`
for one command; this does not make the destination safe or prevent provider
retention.

A private provider notebook, unlisted Gist, archive URL, or hard-to-guess
identifier still grants access through an account or possession of a link. None
should be used as a credential store.

If a remote build legitimately requires a secret, inject it through a
provider-supported secret facility at execution time. Do not place it in the
source tree, Make command line, generated notebook, project configuration, or
downloaded logs.

## Workspace provenance

Reusable workspaces contain an owner record derived from the canonical local
project path and host. This is diagnostic provenance used to prevent accidental
workspace reuse before destructive synchronization. It contains no provider
credential, but it reveals that local path and hostname to the selected remote
workspace or private notebook.

Attaching a locally known managed Colab checkpoint requires any foreign owner
to match its recorded previous owner until successful publication. Remote
owner-record adoption outside that attachment uses `CLOUDMAKE_ADOPT=1`; this
override bypasses the attachment's previous-owner check. Verify the old owner
before using it. These controls protect against mistakes, not against a
malicious user who already controls the same cloud account and workspace.

## Archive and filesystem safety

Source and artifact archives reject absolute paths, traversal outside the
destination, unsafe symbolic or hard links, and special files. Source replacement
and artifact retrieval use staging directories so failed validation preserves
the prior valid tree.

Artifact extraction also enforces member-count, expanded-size, individual-file,
compressed-archive, and expansion-ratio limits. They default respectively to
50,000 files, 2 GiB total, 1 GiB per file, 1 GiB compressed, and 500:1. Advanced
users can set `ARTIFACT_MAX_FILES`, `ARTIFACT_MAX_MB`,
`ARTIFACT_MAX_FILE_MB`, `ARTIFACT_MAX_ARCHIVE_MB`, and `ARTIFACT_MAX_RATIO` in
the host environment.

Source symbolic links may not escape the local project. SSH synchronization
checks remote ownership before applying its manifest-derived source deletion
plan. These controls protect filesystem boundaries during normal operation;
they do not sandbox arbitrary commands in a project's Makefile.

## Execution and supply chain

Cloudmake invokes the project's Makefile and therefore grants its recipes the
permissions of the selected cloud account and remote VM. Review an unfamiliar
project before running it. The same applies to compilers, package managers,
downloaded dependencies, container images, and provider base images used by a
build.

Pin important dependencies where reproducibility matters. Do not assume that a
free notebook runtime, accelerator driver, or preinstalled package set remains
stable between sessions.

## Reporting a security issue

Follow the private reporting process in [`SECURITY.md`](../../SECURITY.md). Do not
include live credentials, private source, or provider tokens in a public issue or
test fixture. A useful report includes the backend, lifecycle step, sanitized
provider response, expected boundary, and a minimal reproduction using dummy
files or fake-provider tests.

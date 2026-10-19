# Cloudmake user documentation

This directory contains the documentation shipped as the stable user contract.
It explains how to use a released Cloudmake version and what behavior projects
and backend integrations may rely on.

## Tutorials

Cloudmake's major capabilities form a three-day learning path. Each tutorial
stands alone, while the sequence adds one concern at a time.

| Day | Release step | Major subject | Central question |
| --- | --- | --- | --- |
| [Day 1](tutorials/day1-stateless.md) | Cloudmake 1.x | Stateless remote Make | How can an unchanged local Make project run on remote hardware? |
| [Day 2](tutorials/day2-workspace.md) | Cloudmake 2.0 | Managed checkpoints | How can useful build artifacts survive disposable compute without becoming project authority? |
| [Day 3](tutorials/day3-devcon.md) | Cloudmake 2.1–2.4 | Dev Container workstations | How can one workstation contract run across unequal backends with the least host privilege? |

The [security reference](reference/security.md) applies across all three days.

```text
remote Make
  -> retain useful build state
  -> supply a portable development workstation
```

## Reference

- [Project contract](reference/project-contract.md)
- [Backend contract](reference/backend-contract.md)
- [Resilience and recovery](reference/resilience.md)
- [Security model](reference/security.md)
- [OCI/CDI runner](reference/oci-runner.md)
- [Dev Container workstation contract](reference/devcontainers.md)

The user documentation describes released behavior. Design proposals,
qualification evidence, historical experiments, and maintainer release notes
live separately under [`design/`](../design/README.md).

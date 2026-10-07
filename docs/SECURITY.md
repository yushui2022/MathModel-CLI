# Security Boundaries

This is a single-user local CLI, not a hostile multi-tenant execution service.

## Trust

Trusted: the user, host CLI, Docker daemon, locally built runtime image, pinned Skill, API provider.
Untrusted: problem attachments, generated code, model text, research/output files and proposed tool calls.

The model cannot edit host control files or trusted validators. Verification runs against a separate
output snapshot with network disabled and no credentials. It recomputes the pinned Skill's automated
gates; it is not an independent proof of mathematical correctness. A malicious payload that exploits
Python/LibreOffice/document parser vulnerabilities remains a risk. Keep Docker and dependencies patched.

## Credentials and Network

The host sends the upstream API credential over stdin to a dedicated relay. It is absent from container
configuration, disk and the Agent environment. Docker administrators and host administrators remain
trusted and can observe process memory. The Agent's temporary relay token allows spending on the locked
model while the task runs; it is not a zero-trust spending boundary. Configure provider-side limits.

The relay accepts bounded POST requests to Responses and compact routes only. It pins the model,
disables hosted tools/background/storage, validates public destination IPs and TLS, and does not follow
redirects or forward upstream error bodies. No other upstream headers are forwarded. Services that
require special headers, nonstandard routes, OAuth, Chat Completions-only APIs or private endpoints are
not supported. Compatibility with individual third-party providers requires real integration testing.

Agent containers have an internal Docker network and no published host ports. **Internal does not mean
complete egress filtering:** containers may reach the network gateway and host services bound there.
Keep sensitive host services inaccessible to Docker networks. Stronger isolation requires a dedicated
VM, host firewall/network policies, or a purpose-built sandbox. Verify Docker Desktop disk location on
Windows yourself before building; the CLI does not move existing Docker/WSL disks.

## Files and Resources

Inputs, Skill and scaffold mounts are read-only; writable mounts are per-task output, research and
Codex home only. No host home or Docker socket is mounted. Agent runtime has no extra capabilities,
no-new-privileges, a nonroot user, read-only root filesystem and CPU/memory/PID limits. Host-side scans
reject symlinks, junctions, hardlinks, device files, traversal and nonportable filenames. Generated
files are never executed directly on the host.

The file scanner limits snapshots to 2 GiB and 20,000 files per directory, and event logs to 64 MiB per
attempt. These are validation limits, **not hard disk quotas**. A running model can still exhaust its
bound volume; use a dedicated volume/quota and monitor free space for untrusted work. Cache and task
history are retained for reproducibility; delete only known completed tasks after archiving what matters.

Accepted output is bound to the immutable inputs/configuration/Skill, runtime image ID, original output
hashes and independently verified snapshot hashes. Export refuses changed or unaccepted artifacts and
never overwrites an existing destination. Original inputs can be confidential and are not exported by
default; preserve the task directory if you need full reproducibility. Logs/sessions can contain problem
data and should be treated as sensitive even though exact configured secrets are redacted from logs.

## Supported Use

Only run problem statements and data you are authorized to use and upload. Respect competition rules
on AI use, authorship, external data and page limits. Review numerical assumptions, sources, references,
language and final layout yourself. A green automated gate is not a competition submission approval.

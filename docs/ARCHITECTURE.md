# Architecture

## Modules

- `config.py` / `job.schema.json`: closed declarative schema; no arbitrary host commands or mounts.
- `files.py` / `skills.py`: portable paths, strict snapshots, pinned ZIP and manifest validation.
- `store.py`: durable atomic controller state and immutable job/inputs/Skill/scaffold hashes.
- `backend.py`: fixed Docker argv, scoped resource labels, limits, streaming events and timeout.
- `runtime/agent.py`: container-only Codex exec / explicit session resume.
- `runtime/proxy.py`: fixed public HTTPS Responses upstream; secrets only in process memory.
- `runtime/verify.py`: separate, offline evidence/authoring/format/workflow validation.
- `workflow.py`: orchestration, repair loop, state transitions, snapshot verification and export.
- `cli.py`: user commands; never executes generated model code itself.

## Lifecycle

`prepare -> READY -> RUNNING -> VERIFYING -> SUCCEEDED | BLOCKED`

API/process failure becomes `FAILED`; timeout or keyboard interruption becomes `INTERRUPTED`.
A failed validation can go back to `RUNNING` with bounded repair feedback. Explicit resume allows a
new invocation with at most `runtime.max_attempts` additional turns. No implicit API-error retry.
An OS crash may leave RUNNING state stale; resume first removes only resources labeled as that task,
revalidates locked inputs and image, then continues the recorded Codex session. It never uses `--last`.

All state writes are atomic and a host file lock excludes concurrent writers. `stop` can remove owned
containers while the running controller holds the lock; that controller records the interrupted process.
Neither shutdown nor failed preparation deletes user input or partial results.

## Storage

```
MATHMODEL_HOME/
  cache/<skill-sha256>.zip, <skill-sha256>/
  runs/<task-id>/
    control/       # job, lock, state, verification proofs; never mounted into Agent
    logs/          # captured outside Agent
    workspace/     # read-only AGENTS.md and child mount points
    inputs/        # immutable original input snapshot
    skill/         # immutable pinned full package
    output/        # Agent-writable paper_output
    research/      # Agent-writable crawled_data (network research disabled)
    codex-home/    # writable isolated Codex sessions; no upstream key
    verification/<id>/paper_output/, crawled_data/, verdict/
```

Both execution and verification see `/workspace`: this preserves the Skill's absolute path bindings.
The verifier recomputes the evidence gate without rewriting its timestamp-bound JSON, then validates
final authoring, requires LibreOffice rendering and checks S8. This avoids accidentally invalidating
the S7 evidence hash merely by rechecking S6.

## Compatibility and Next Work

v0.1 intentionally supports one engine/edition. Adding Pro requires host-owned approvals, not a prompt
that asks the model to approve its own checkpoints. Public research requires a separate policy-aware
fetch service. Other priorities: real model/contest acceptance, strict egress isolation, runtime
dependency lockfiles and signed images, disk quotas, richer live progress, provider usage/cost limits,
and a tested migration contract for completed/paused tasks.

References: [Codex noninteractive mode](https://learn.chatgpt.com/docs/non-interactive-mode),
[Docker run](https://docs.docker.com/engine/containers/run/),
[Docker internal networks](https://docs.docker.com/reference/cli/docker/network/create/).
Provider retry options follow the [pinned Codex provider schema](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/model-provider-info/src/lib.rs).

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

import yaml
from filelock import Timeout

from . import __version__
from .backend import DockerBackend, build_image, docker
from .config import CODEX_VERSION, IMAGE, SKILL_COMMIT, load_job, new_job, validate_job
from .events import redact
from .files import copy_tree, relative_path
from .skills import ensure_skill
from .store import RUN_ID, Store, default_home
from .workflow import Workflow


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="mathmodel", description="Isolated Standard math-modeling jobs with Codex")
    root.add_argument("--version", action="version", version=__version__)
    root.add_argument("--home", type=Path, default=None, help="task/cache storage directory")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a declarative task (does not call a model)")
    init.add_argument("directory", type=Path)
    init.add_argument("--inputs", type=Path)
    init.add_argument("--model", default="YOUR_MODEL_ID")
    validate = commands.add_parser("validate", help="validate YAML without Docker or API access")
    validate.add_argument("job", type=Path)
    fetch = commands.add_parser("fetch-skill", help="cache the pinned Standard package")
    fetch.add_argument("--archive", type=Path)
    for command in ("prepare", "run"):
        item = commands.add_parser(command, help="snapshot a task" if command == "prepare" else "snapshot and run (uses API credits)")
        item.add_argument("job", type=Path)
        item.add_argument("--skill-archive", type=Path)
        item.add_argument("--offline", action="store_true", help="do not download the Skill package")
    resume = commands.add_parser("resume", help="resume a task (uses API credits)")
    resume.add_argument("id")
    resume.add_argument("--fresh-session", action="store_true", help="explicitly start a new Codex conversation")
    status = commands.add_parser("status", help="read controller-owned state")
    status.add_argument("id", nargs="?")
    status.add_argument("--json", action="store_true")
    logs = commands.add_parser("logs", help="show recent task events")
    logs.add_argument("id")
    logs.add_argument("--lines", type=int, default=40)
    commands.add_parser("doctor", help="check Docker and runtime image without calling any API")
    image = commands.add_parser("image", help="manage the runtime image")
    image.add_argument("action", choices=["build"])
    for name in ("verify", "stop"):
        commands.add_parser(name).add_argument("id")
    export = commands.add_parser("export", help="export only a fresh, accepted snapshot")
    export.add_argument("id")
    export.add_argument("--output", required=True, type=Path)
    return root


def display(state: dict, *, full: bool = False):
    if full:
        print(json.dumps(state, ensure_ascii=False, indent=2))
        return
    print(f'{state["id"]}  {state["status"]}  attempts={state["attempts"]}  {state["name"]}')
    if state.get("last_error"):
        print(state["last_error"])


def dispatch(args) -> int:
    store = Store(args.home or default_home())
    workflow = Workflow(store)
    if args.command == "init":
        target = args.directory.absolute()
        if target.exists() and any(target.iterdir()):
            raise ValueError("init requires a new or empty directory; existing files will not be overwritten")
        job = new_job(target.name)
        job["engine"]["model"] = args.model
        validate_job(job)
        if args.inputs:
            copy_tree(args.inputs.absolute(), target / "problem_files")
        else:
            (target / "problem_files").mkdir(parents=True)
        (target / "job.yaml").write_text(yaml.safe_dump(job, allow_unicode=True, sort_keys=False), "utf-8")
        print(f"Created {target / 'job.yaml'}; add the problem and attachments to problem_files/.")
    elif args.command == "validate":
        load_job(args.job)
        print("YAML valid. This does not verify model availability or contest input completeness.")
    elif args.command == "fetch-skill":
        print(ensure_skill(store.home, args.archive))
    elif args.command in ("prepare", "run"):
        if args.command == "run" and args.offline:
            raise ValueError("run calls the model API; use prepare --offline for a fully offline task")
        job = load_job(args.job)
        base = args.job.resolve().parent
        inputs = base / relative_path(job["inputs"])
        # Check the entire input path chain, not just the leaf, for junctions/symlinks.
        from .files import no_link
        for parent in [inputs, *inputs.parents]:
            if parent == base:
                break
            no_link(parent)
        skill = ensure_skill(store.home, args.skill_archive, download=not args.offline)
        run_id = store.create(job, inputs, skill)
        print(f"Task: {run_id}", flush=True)
        state = workflow.run(run_id) if args.command == "run" else store.state(run_id)
        display(state)
        return 0 if state["status"] in ("READY", "SUCCEEDED") else 2
    elif args.command == "resume":
        state = workflow.run(args.id, fresh_session=args.fresh_session)
        display(state)
        return 0 if state["status"] == "SUCCEEDED" else 2
    elif args.command == "verify":
        state = workflow.verify(args.id)
        display(state)
        return 0 if state["status"] == "SUCCEEDED" else 2
    elif args.command == "status":
        if args.id:
            display(store.state(args.id), full=args.json)
        else:
            roots = store.home / "runs"
            states = [store.state(path.name) for path in sorted(roots.iterdir())
                      if RUN_ID.fullmatch(path.name) and (path / "control/state.json").is_file()] if roots.exists() else []
            if args.json:
                print(json.dumps(states, ensure_ascii=False, indent=2))
            else:
                for state in states:
                    display(state)
    elif args.command == "logs":
        from collections import deque
        if not 1 <= args.lines <= 10000:
            raise ValueError("--lines must be between 1 and 10000")
        state = store.state(args.id)
        path = store.path(args.id) / "logs" / f'attempt-{state["attempts"]}.jsonl'
        if not path.exists():
            print("No agent events recorded.")
        else:
            with path.open(encoding="utf-8") as stream:
                print("".join(deque(stream, maxlen=args.lines)), end="")
    elif args.command == "export":
        manifest = workflow.export(args.id, args.output.absolute())
        print(f"Exported {len(manifest['files'])} files to {args.output.absolute()}")
    elif args.command == "stop":
        from .files import utc_now, write_json
        store.state(args.id)
        write_json(store.path(args.id) / "control/stop-request.json", {"requested_at_utc": utc_now()})
        workflow.backend.cleanup(args.id)
        try:
            with store.lock(args.id):
                state = store.state(args.id)
                if state["status"] in ("RUNNING", "VERIFYING"):
                    store.update(args.id, "INTERRUPTED", verification=None, last_error="stopped by user")
        except Timeout:
            pass  # The active controller observes the stopped container and records its result.
        print("Owned containers stopped. Inputs and outputs were preserved.")
    elif args.command == "image":
        build_image()
    elif args.command == "doctor":
        info = {"cli_version": __version__, "home": str(store.home), "docker": shutil.which("docker"),
                "image": IMAGE, "codex_version": CODEX_VERSION, "skill_commit": SKILL_COMMIT,
                "api_checked": False, "status": "BLOCKED"}
        try:
            context = docker("context", "show", timeout=15).stdout.strip()
            info["docker_context"] = context
            info["image_id"] = DockerBackend(store.home).image()
            info["status"] = "READY"
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            info["error"] = str(exc)
        print(json.dumps(info, ensure_ascii=False, indent=2))
        return 0 if info["status"] == "READY" else 2
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return dispatch(parser().parse_args(argv))
    except KeyboardInterrupt:
        print("Interrupted; task files preserved. Use status and resume.", file=sys.stderr)
        return 130
    except Timeout:
        print("Task is locked by another controller; use status or stop.", file=sys.stderr)
        return 2
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Error: {redact(str(exc))}", file=sys.stderr)
        return 2

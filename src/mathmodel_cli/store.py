from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone
from importlib.resources import files
from pathlib import Path

from filelock import FileLock

from .config import CODEX_VERSION, SKILL_COMMIT, SKILL_SHA256, validate_job
from .files import copy_tree, inventory, json_hash, no_link, read_json, utc_now, write_json

RUN_ID = re.compile(r"^\d{8}T\d{6}-[a-f0-9]{12}$")
STATES = {"READY", "RUNNING", "VERIFYING", "SUCCEEDED", "BLOCKED", "FAILED", "INTERRUPTED"}


def default_home() -> Path:
    if os.environ.get("MATHMODEL_HOME"):
        return Path(os.environ["MATHMODEL_HOME"]).expanduser().resolve()
    if os.name == "nt" and Path("G:/").is_dir():
        return Path("G:/Data/MathModel-CLI")
    return Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mathmodel-cli"


class Store:
    def __init__(self, home: Path):
        self.home = home.resolve()

    def path(self, run_id: str) -> Path:
        if not RUN_ID.fullmatch(run_id):
            raise ValueError("invalid task ID")
        root = self.home / "runs" / run_id
        if root.exists():
            no_link(root)
        if root.is_symlink() or not root.resolve().is_relative_to((self.home / "runs").resolve()):
            raise ValueError("unsafe task directory")
        return root

    def lock(self, run_id: str) -> FileLock:
        root = self.path(run_id)
        if not root.is_dir():
            raise ValueError("task not found")
        return FileLock(str(root / "task.lock"), timeout=0)

    def state(self, run_id: str) -> dict:
        state = read_json(self.path(run_id) / "control/state.json")
        if state.get("id") != run_id or state.get("status") not in STATES:
            raise ValueError("invalid task state")
        return state

    def update(self, run_id: str, status: str, **fields) -> dict:
        if status not in STATES:
            raise ValueError("invalid task status")
        state = self.state(run_id)
        state.update(status=status, updated_at_utc=utc_now(), **fields)
        write_json(self.path(run_id) / "control/state.json", state)
        return state

    def create(self, job: dict, inputs: Path, skill: Path) -> str:
        job = validate_job(job)
        input_hashes = inventory(inputs)
        if not input_hashes:
            raise ValueError("input directory is empty")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S-") + uuid.uuid4().hex[:12]
        root = self.path(run_id)
        root.mkdir(parents=True, exist_ok=False)
        for name in ("control", "logs", "workspace", "inputs", "output", "research", "codex-home", "skill"):
            (root / name).mkdir()
        try:
            copy_tree(inputs, root / "inputs")
            skill_hashes = copy_tree(skill, root / "skill")
            workspace = root / "workspace"
            for name in ("problem_files", "paper_output", "crawled_data", ".agents"):
                (workspace / name).mkdir()
            (workspace / "AGENTS.md").write_text(
                files("mathmodel_cli").joinpath("resources/agent-instructions.md").read_text("utf-8"),
                encoding="utf-8", newline="\n")
            write_json(root / "control/job.json", job)
            write_json(root / "control/lock.json", {
                "schema_version": "1", "job_sha256": json_hash(job), "input_hashes": input_hashes,
                "skill_hashes": skill_hashes, "workspace_hashes": inventory(workspace),
                "skill_commit": SKILL_COMMIT, "skill_archive_sha256": SKILL_SHA256,
                "codex_version": CODEX_VERSION, "created_at_utc": utc_now(),
            })
            write_json(root / "control/state.json", {
                "schema_version": "1", "id": run_id, "name": job["name"], "status": "READY",
                "created_at_utc": utc_now(), "updated_at_utc": utc_now(), "attempts": 0,
                "session_id": None, "image_id": None, "last_error": None, "verification": None,
                "usage": [],
            })
        except Exception as exc:
            write_json(root / "control/preparation-error.json", {"error": type(exc).__name__})
            raise
        return run_id

    def verify_locked_inputs(self, run_id: str) -> dict:
        root = self.path(run_id)
        lock = read_json(root / "control/lock.json")
        job = validate_job(read_json(root / "control/job.json"))
        if json_hash(job) != lock["job_sha256"]:
            raise ValueError("task configuration changed; create a new task")
        if lock["skill_commit"] != SKILL_COMMIT or lock["skill_archive_sha256"] != SKILL_SHA256:
            raise ValueError("task uses a different Skill version; use its original CLI version")
        for directory, key in (("inputs", "input_hashes"), ("skill", "skill_hashes"),
                               ("workspace", "workspace_hashes")):
            if inventory(root / directory) != lock[key]:
                raise ValueError(f"{directory} changed after preparation; task rejected")
        return job

from __future__ import annotations

import os
import uuid
from pathlib import Path

from .backend import DockerBackend
from .files import copy_tree, inventory, json_hash, read_json, utc_now, write_json
from .store import Store


class Workflow:
    def __init__(self, store: Store, backend=None):
        self.store = store
        self.backend = backend or DockerBackend(store.home)

    def pinned_image(self, run_id: str) -> str:
        current = self.backend.image()
        state = self.store.state(run_id)
        if state["image_id"] and state["image_id"] != current:
            raise ValueError("runtime image changed; restore the original image before resuming")
        self.store.update(run_id, state["status"], image_id=current)
        return current

    def verify(self, run_id: str) -> dict:
        with self.store.lock(run_id):
            self.backend.cleanup(run_id)
            return self._verify(run_id, self.pinned_image(run_id))

    def _verify(self, run_id: str, image_id: str) -> dict:
        root = self.store.path(run_id)
        job = self.store.verify_locked_inputs(run_id)
        self.store.update(run_id, "VERIFYING", verification=None)
        token = uuid.uuid4().hex
        snapshot = root / "verification" / token
        snapshot.mkdir(parents=True)
        (snapshot / "verdict").mkdir()
        try:
            source = {"output": copy_tree(root / "output", snapshot / "paper_output"),
                      "research": copy_tree(root / "research", snapshot / "crawled_data")}
            code = self.backend.verify(root, snapshot, job, image_id, root / "logs" / f"verify-{token}.jsonl")
            result_path = snapshot / "verdict/result.json"
            result = read_json(result_path) if result_path.is_file() else {"status": "BLOCKED", "error": "no verdict"}
            passed = code == 0 and result.get("status") == "PASS"
            # Required files are checked independently of the verifier's summary.
            output = snapshot / "paper_output"
            if passed:
                from .files import relative_path
                report = read_json(output / "format_check_report.json")
                pdf_path = relative_path(report["render_qa"]["pdf"])
                if pdf_path.parts[0] != "paper_output":
                    raise ValueError("rendered PDF is outside paper_output")
                for path in (output / "final_paper.docx", output / "final_paper_source.md", snapshot / pdf_path):
                    if not path.is_file() or path.stat().st_size == 0:
                        raise ValueError("required delivery artifact is missing or empty")
            self.store.verify_locked_inputs(run_id)
            if any(inventory(root / directory) != hashes for directory, hashes in source.items()):
                raise ValueError("source artifacts changed during independent verification")
            proof = {"id": token, "verified_at_utc": utc_now(), "image_id": image_id,
                     "source_hashes": source, "snapshot_hashes": inventory(snapshot),
                     "lock_sha256": json_hash(read_json(root / "control/lock.json"))}
            write_json(root / "control" / f"verification-{token}.json", proof)
            return self.store.update(run_id, "SUCCEEDED" if passed else "BLOCKED",
                                     verification=token if passed else None,
                                     last_error=None if passed else "Independent verification rejected the output",
                                     feedback=result)
        except Exception as exc:
            self.store.update(run_id, "BLOCKED", verification=None, last_error=str(exc))
            raise

    def run(self, run_id: str, *, fresh_session: bool = False) -> dict:
        with self.store.lock(run_id):
            job = self.store.verify_locked_inputs(run_id)
            state = self.store.state(run_id)
            if state["status"] == "SUCCEEDED":
                self.check_export(run_id)
                return state
            if job["engine"]["model"] == "YOUR_MODEL_ID":
                raise ValueError("set engine.model to a model ID available through your configured API provider")
            key = os.environ.get(job["engine"]["api_key_env"], "")
            if not key:
                raise ValueError(f'Set {job["engine"]["api_key_env"]} locally before running; never put it in YAML')
            if "\r" in key or "\n" in key:
                raise ValueError("invalid credential format")
            image_id = self.pinned_image(run_id)
            self.backend.cleanup(run_id)
            (self.store.path(run_id) / "control/stop-request.json").unlink(missing_ok=True)
            if fresh_session:
                state = self.store.update(run_id, state["status"], session_id=None)
            try:
                for _ in range(job["runtime"]["max_attempts"]):
                    self.store.verify_locked_inputs(run_id)
                    state = self.store.state(run_id)
                    attempt = state["attempts"] + 1
                    self.store.update(run_id, "RUNNING", attempts=attempt, verification=None, last_error=None)

                    remembered_session = state["session_id"]

                    def remember(events):
                        nonlocal remembered_session
                        if events.session_id and events.session_id != remembered_session:
                            self.store.update(run_id, "RUNNING", session_id=events.session_id)
                            remembered_session = events.session_id

                    result = self.backend.execute(
                        self.store.path(run_id), job, image_id, state["session_id"], key,
                        state.get("feedback", {}), self.store.path(run_id) / "logs" / f"attempt-{attempt}.jsonl",
                        remember)
                    current = self.store.state(run_id)
                    self.store.update(run_id, "RUNNING", usage=current["usage"] + result.events.usage)
                    if (self.store.path(run_id) / "control/stop-request.json").exists():
                        return self.store.update(run_id, "INTERRUPTED", last_error="stopped by user")
                    if result.timed_out:
                        return self.store.update(run_id, "INTERRUPTED", last_error="attempt timed out; explicit resume required")
                    if result.code or not result.events.completed or result.events.failed:
                        return self.store.update(run_id, "FAILED", last_error="Codex turn failed or did not complete; no automatic API retry")
                    verified = self._verify(run_id, image_id)
                    if verified["status"] == "SUCCEEDED":
                        return verified
                return self.store.state(run_id)
            except KeyboardInterrupt:
                self.store.update(run_id, "INTERRUPTED", last_error="interrupted by user")
                raise
            except Exception as exc:
                from .events import redact
                self.store.update(run_id, "FAILED", verification=None, last_error=redact(str(exc), (key,)))
                raise

    def check_export(self, run_id: str) -> Path:
        root = self.store.path(run_id)
        self.store.verify_locked_inputs(run_id)
        state = self.store.state(run_id)
        token = state.get("verification")
        if state["status"] != "SUCCEEDED" or not isinstance(token, str) or len(token) != 32:
            raise ValueError("task has no accepted snapshot; run verify first")
        if any(char not in "0123456789abcdef" for char in token):
            raise ValueError("invalid verification ID")
        proof = read_json(root / "control" / f"verification-{token}.json")
        if proof["image_id"] != state["image_id"] or proof["lock_sha256"] != json_hash(read_json(root / "control/lock.json")):
            raise ValueError("acceptance provenance changed")
        snapshot = root / "verification" / token
        if inventory(snapshot) != proof["snapshot_hashes"]:
            raise ValueError("accepted snapshot changed; re-verify before export")
        if any(inventory(root / key) != value for key, value in proof["source_hashes"].items()):
            raise ValueError("working artifacts changed; re-verify before export")
        return snapshot

    def export(self, run_id: str, destination: Path) -> dict:
        with self.store.lock(run_id):
            if destination.resolve().is_relative_to(self.store.home):
                raise ValueError("export outside the internal MathModel data directory")
            if destination.exists():
                raise ValueError("export destination must not exist")
            snapshot = self.check_export(run_id)
            destination.mkdir(parents=True)
            copy_tree(snapshot / "paper_output", destination / "paper_output")
            copy_tree(snapshot / "crawled_data", destination / "crawled_data")
            self.check_export(run_id)
            manifest = {"schema_version": "1", "task": run_id, "exported_at_utc": utc_now(),
                        "scope": "Standard automated validation; not a scientific quality guarantee",
                        "files": inventory(destination)}
            write_json(destination / "SHA256SUMS.json", manifest)
            write_json(destination / "verification.json", read_json(snapshot / "verdict/result.json"))
            return manifest

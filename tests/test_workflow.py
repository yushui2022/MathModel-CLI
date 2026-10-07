import json

import pytest
from filelock import Timeout

from mathmodel_cli.backend import Attempt
from mathmodel_cli.events import Events
from mathmodel_cli.files import inventory, write_json
from mathmodel_cli.workflow import Workflow

SESSION = "12345678-1234-1234-1234-123456789abc"


class FakeBackend:
    """Offline controller fixture. A fake PASS is NEVER evidence of real paper quality."""
    def __init__(self, verdict="PASS", complete=True, code=0, timed_out=False):
        self.verdict, self.complete, self.code, self.timed_out = verdict, complete, code, timed_out
        self.sessions = []
        self.image_id = "sha256:offline-fixture"

    def image(self):
        return self.image_id

    def cleanup(self, run_id):
        pass

    def execute(self, root, job, image_id, session_id, api_key, feedback, log, on_event):
        assert api_key == "offline-dummy-credential"
        self.sessions.append(session_id)
        events = Events()
        events.accept(json.dumps({"type": "thread.started", "thread_id": SESSION}))
        if self.complete:
            events.accept('{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":5}}')
        on_event(events)
        (root / "output/result.txt").write_text("offline output")
        return Attempt(self.code, events, self.timed_out)

    def verify(self, root, snapshot, job, image_id, log):
        output = snapshot / "paper_output"
        (output / "final_paper.docx").write_bytes(b"offline controller fixture, not a DOCX")
        (output / "final_paper_source.md").write_text("fixture")
        (output / "paper.pdf").write_bytes(b"offline controller fixture, not a PDF")
        write_json(output / "format_check_report.json", {"render_qa": {"pdf": "paper_output/paper.pdf"}})
        write_json(snapshot / "verdict/result.json", {"status": self.verdict, "offline_fixture": True})
        return 0 if self.verdict == "PASS" else 1


@pytest.fixture
def credential(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "offline-dummy-credential")


def test_offline_lifecycle_and_export(prepared, tmp_path, credential):
    store, task = prepared
    runner = Workflow(store, FakeBackend())
    assert store.state(task)["status"] == "READY"
    assert runner.run(task)["status"] == "SUCCEEDED"
    assert store.state(task)["usage"] == [{"input_tokens": 12, "output_tokens": 5}]
    destination = tmp_path / "export"
    assert runner.export(task, destination)["files"]
    assert (destination / "verification.json").exists()
    assert not any("credential" in name or "codex-home" in name for name in inventory(destination))
    with pytest.raises(ValueError):
        runner.export(task, destination)


@pytest.mark.parametrize("directory,filename", [("inputs", "problem.txt"), ("skill", "SKILL.md"),
                                               ("workspace", "AGENTS.md")])
def test_immutable_inputs(prepared, directory, filename):
    store, task = prepared
    (store.path(task) / directory / filename).write_text("changed")
    with pytest.raises(ValueError, match="changed"):
        store.verify_locked_inputs(task)


def test_modified_config_rejected(prepared):
    store, task = prepared
    path = store.path(task) / "control/job.json"
    job = json.loads(path.read_text())
    job["engine"]["model"] = "different"
    write_json(path, job)
    with pytest.raises(ValueError, match="configuration changed"):
        store.verify_locked_inputs(task)


def test_failed_gate_never_exports(prepared, tmp_path, credential):
    store, task = prepared
    backend = FakeBackend(verdict="BLOCKED")
    runner = Workflow(store, backend)
    assert runner.run(task)["status"] == "BLOCKED"
    assert len(backend.sessions) == 3
    assert backend.sessions == [None, SESSION, SESSION]
    with pytest.raises(ValueError, match="accepted snapshot"):
        runner.export(task, tmp_path / "not-exported")
    assert not (tmp_path / "not-exported").exists()


@pytest.mark.parametrize("options,status", [({"complete": False}, "FAILED"), ({"code": 1}, "FAILED"),
                                           ({"timed_out": True}, "INTERRUPTED")])
def test_no_api_error_retries(prepared, credential, options, status):
    store, task = prepared
    backend = FakeBackend(**options)
    assert Workflow(store, backend).run(task)["status"] == status
    assert len(backend.sessions) == 1
    assert store.state(task)["session_id"] == SESSION


def test_resume_explicit_session(prepared, credential):
    store, task = prepared
    backend = FakeBackend(timed_out=True)
    runner = Workflow(store, backend)
    runner.run(task)
    backend.timed_out = False
    runner.run(task)
    assert backend.sessions == [None, SESSION]


def test_fresh_session(prepared, credential):
    store, task = prepared
    backend = FakeBackend(timed_out=True)
    runner = Workflow(store, backend)
    runner.run(task)
    runner.run(task, fresh_session=True)
    assert backend.sessions == [None, None]


@pytest.mark.parametrize("where", ["snapshot", "source"])
def test_tampered_artifact_blocks_export(prepared, tmp_path, credential, where):
    store, task = prepared
    runner = Workflow(store, FakeBackend())
    runner.run(task)
    snapshot = runner.check_export(task)
    changed = snapshot / "paper_output/final_paper.docx" if where == "snapshot" else store.path(task) / "output/result.txt"
    changed.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="changed"):
        runner.export(task, tmp_path / "export")


def test_runtime_image_lock(prepared, credential):
    store, task = prepared
    backend = FakeBackend(timed_out=True)
    runner = Workflow(store, backend)
    runner.run(task)
    backend.image_id = "different-image"
    with pytest.raises(ValueError, match="image changed"):
        runner.run(task)


def test_missing_key_does_not_run(prepared, monkeypatch):
    store, task = prepared
    monkeypatch.delenv("TEST_API_KEY", raising=False)
    backend = FakeBackend()
    with pytest.raises(ValueError, match="Set TEST_API_KEY"):
        Workflow(store, backend).run(task)
    assert backend.sessions == []
    assert store.state(task)["status"] == "READY"


def test_lock_and_invalid_id(prepared):
    store, task = prepared
    with store.lock(task):
        with pytest.raises(Timeout):
            with store.lock(task):
                pass
    with pytest.raises(ValueError):
        store.path("../../outside")


def test_verification_mutation_detected(prepared):
    store, task = prepared

    class Mutating(FakeBackend):
        def verify(self, root, *args):
            result = super().verify(root, *args)
            (root / "output/tampered").write_text("changed during validation")
            return result

    with pytest.raises(ValueError, match="changed during"):
        Workflow(store, Mutating()).verify(task)
    assert store.state(task)["status"] == "BLOCKED"

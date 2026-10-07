import csv
import json
import subprocess
import sys

import pytest
from conftest import runtime_module

from mathmodel_cli import backend
from mathmodel_cli.events import Events, redact


def test_events_require_completion_and_valid_session():
    events = Events()
    for line in ("not-json", "[]", '{"type":"thread.started","thread_id":"../../escape"}'):
        events.accept(line)
    assert events.session_id is None and not events.completed
    events.accept('{"type":"turn.completed","usage":{"input_tokens":1,"invalid":-2,"bool":true}}')
    assert events.completed
    assert events.usage == [{"input_tokens": 1}]
    events.accept('{"type":"turn.started"}')
    assert not events.completed
    events.accept('{"type":"turn.failed"}')
    assert events.failed


def test_redaction():
    output = redact('Bearer secret-token and upstream-key', ('upstream-key',))
    assert "secret-token" not in output and "upstream-key" not in output


def test_mount_is_single_csv_argument(tmp_path):
    path = tmp_path / 'space,逗号'
    result = backend.mount(path, "/workspace", readonly=True)
    assert result[0] == "--mount" and len(result) == 2
    values = next(csv.reader([result[1]]))
    assert f"src={path.resolve()}" in values and "readonly" in values


def test_safety_options(job, monkeypatch):
    monkeypatch.setattr(backend, "uid", lambda: "1000:1000")
    args = backend.safety_options(job["runtime"])
    for expected in ("--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true"):
        assert expected in args
    assert "--privileged" not in args
    assert "docker.sock" not in " ".join(args)
    assert "API_KEY" not in " ".join(args)


def test_readonly_inputs_and_writable_outputs(prepared):
    store, task = prepared
    root = store.path(task)
    args = backend.DockerBackend(store.home).workspace_mounts(root, root / "output", root / "research")
    rows = [next(csv.reader([args[i]])) for i in range(1, len(args), 2)]
    for row in rows:
        readonly = any(item in row for item in ("dst=/workspace", "dst=/workspace/problem_files", "dst=/workspace/.agents"))
        assert ("readonly" in row) == readonly
        assert not any("control" in item or "codex-home" in item or "docker.sock" in item for item in row)


def test_refuse_removing_foreign_container(monkeypatch, tmp_path):
    called = []

    def fake(*args, **kwargs):
        called.append(args)
        return subprocess.CompletedProcess(args, 0, json.dumps([{"Config": {"Labels": {"mathmodel.owner": "other"}}}]), "")

    monkeypatch.setattr(backend, "docker", fake)
    with pytest.raises(backend.DockerError, match="foreign"):
        backend.DockerBackend(tmp_path).remove_owned("container", "name", "task")
    assert len(called) == 1


def test_missing_network_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(backend, "docker", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args, 1, "", "Error: network x not found"))
    backend.DockerBackend(tmp_path).cleanup("task")


def test_streaming_events_and_secret_filter(tmp_path):
    script = ('import sys,json; json.load(sys.stdin); '
              'print(json.dumps({"type":"turn.completed","usage":{"input_tokens":5}})); '
              'print("Bearer token secret")')
    log = tmp_path / "log.jsonl"
    result = backend.consume([sys.executable, "-c", script], {}, log, 10, ("secret",))
    assert result.code == 0 and result.events.completed
    assert "secret" not in log.read_text()
    assert "Bearer token" not in log.read_text()


def test_stream_timeout(tmp_path):
    result = backend.consume([sys.executable, "-c", "import time; time.sleep(10)"], {}, tmp_path / "log", 1)
    assert result.timed_out and result.code == 124


def test_codex_command_uses_specific_resume(job):
    agent = runtime_module("agent")
    session = "12345678-1234-1234-1234-123456789abc"
    args = agent.command(job, session)
    assert "resume" in args and session in args
    assert "--last" not in args
    assert args[-1] == "-"
    assert "--json" in args
    assert "--ignore-user-config" in args and "--ignore-rules" in args
    assert "OPENAI_API_KEY" not in " ".join(args)
    with pytest.raises(ValueError):
        agent.command(job, "--last")


def test_remote_docker_rejected(monkeypatch):
    monkeypatch.delenv("DOCKER_CONTEXT", raising=False)
    monkeypatch.setenv("DOCKER_HOST", "tcp://untrusted.example:2375")
    with pytest.raises(backend.DockerError, match="local"):
        backend.require_local_docker()

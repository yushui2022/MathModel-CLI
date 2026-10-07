from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import queue
import secrets
import subprocess
import threading
import time
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from .config import IMAGE
from .events import Events, redact


class DockerError(RuntimeError):
    pass


def docker(*arguments: str, timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["docker", *arguments], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    if check and result.returncode:
        raise DockerError(redact(result.stderr[-3000:] or result.stdout[-3000:]))
    return result


def mount(source: Path, target: str, *, readonly: bool = True) -> list[str]:
    value = str(source.resolve())
    if any(char in value for char in "\r\n\0"):
        raise ValueError("unsafe mount path")
    output = io.StringIO(newline="")
    csv.writer(output, lineterminator="").writerow(
        ["type=bind", f"src={value}", f"dst={target}", *(["readonly"] if readonly else [])])
    return ["--mount", output.getvalue()]


def uid() -> str:
    if os.name == "nt":
        return "1000:1000"
    if os.getuid() == 0:
        raise DockerError("Run MathModel CLI as a regular user, not root")
    return f"{os.getuid()}:{os.getgid()}"


def require_local_docker():
    endpoint = os.environ.get("DOCKER_HOST") if not os.environ.get("DOCKER_CONTEXT") else None
    if not endpoint:
        info = json.loads(docker("context", "inspect").stdout)
        endpoint = info[0]["Endpoints"]["docker"]["Host"]
    if not endpoint.startswith(("unix://", "npipe://")):
        raise DockerError("only local Docker socket/named-pipe contexts are supported")


def safety_options(runtime: dict) -> list[str]:
    return ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--user", uid(), "--pids-limit", str(runtime["pids_limit"]),
            "--cpus", str(runtime["cpus"]), "--memory", f'{runtime["memory_mb"]}m',
            "--memory-swap", f'{runtime["memory_mb"]}m', "--log-driver", "none",
            "--tmpfs", "/tmp:rw,nosuid,nodev,size=536870912,mode=1777",
            "--env", "PYTHONUTF8=1", "--env", "PYTHONDONTWRITEBYTECODE=1"]


@dataclass
class Attempt:
    code: int
    events: Events
    timed_out: bool = False


def consume(argv: list[str], payload: dict, log: Path, timeout: int,
            hidden: tuple[str, ...] = (), on_event=None) -> Attempt:
    """Bound the event buffer and log; preserve events while enforcing a wall-clock timeout."""
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT)
    pending: queue.Queue = queue.Queue(maxsize=128)
    cancelled = threading.Event()

    def reader():
        try:
            while not cancelled.is_set():
                line = process.stdout.readline(1024 * 1024 + 1)
                item = line if line else None
                while not cancelled.is_set():
                    try:
                        pending.put(item, timeout=0.2)
                        break
                    except queue.Full:
                        pass
                if not line:
                    break
        finally:
            process.stdout.close()

    worker = threading.Thread(target=reader, daemon=True)
    worker.start()
    events, size, timed_out = Events(), 0, False
    deadline = time.monotonic() + timeout
    try:
        process.stdin.write(json.dumps(payload, ensure_ascii=False).encode())
        process.stdin.close()
        with log.open("xb") as stream:
            while True:
                if time.monotonic() >= deadline:
                    timed_out = True
                    break
                try:
                    raw = pending.get(timeout=0.2)
                except queue.Empty:
                    continue
                if raw is None:
                    break
                size += len(raw)
                if len(raw) > 1024 * 1024 or size > 64 * 1024**2:
                    raise DockerError("event output exceeded safety limit")
                line = raw.decode("utf-8", errors="replace")
                events.accept(line)
                stream.write(redact(line, hidden).encode())
                stream.flush()
                if on_event:
                    on_event(events)
        code = process.wait(timeout=max(1, deadline - time.monotonic())) if not timed_out else 124
        return Attempt(code, events, timed_out)
    except subprocess.TimeoutExpired:
        return Attempt(124, events, True)
    finally:
        cancelled.set()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        worker.join(timeout=2)


class DockerBackend:
    def __init__(self, home: Path):
        self.owner = hashlib.sha256(str(home.resolve()).encode()).hexdigest()[:12]

    def prefix(self, run_id: str) -> str:
        return f"mm-{self.owner}-{run_id.lower()}"

    def labels(self, run_id: str) -> list[str]:
        return ["--label", f"mathmodel.owner={self.owner}", "--label", f"mathmodel.task={run_id}"]

    def image(self) -> str:
        require_local_docker()
        if docker("info", "--format", "{{.OSType}}", timeout=20).stdout.strip() != "linux":
            raise DockerError("Linux Docker containers are required")
        return docker("image", "inspect", IMAGE, "--format", "{{.Id}}", timeout=20).stdout.strip()

    def remove_owned(self, kind: str, name: str, run_id: str):
        inspected = docker(kind, "inspect", name, check=False)
        if inspected.returncode:
            if any(text in inspected.stderr.lower() for text in ("no such", "not found")):
                return
            raise DockerError("cannot inspect owned Docker resources; cleanup may be required")
        metadata = json.loads(inspected.stdout)[0]
        labels = metadata.get("Config", {}).get("Labels", {}) if kind == "container" else metadata.get("Labels", {})
        if not labels or labels.get("mathmodel.owner") != self.owner or labels.get("mathmodel.task") != run_id:
            raise DockerError(f"refusing to remove foreign {kind}: {name}")
        docker(kind, "rm", *(["--force"] if kind == "container" else []), name)

    def cleanup(self, run_id: str):
        prefix = self.prefix(run_id)
        errors = []
        for kind, suffix in (("container", "agent"), ("container", "verify"),
                             ("container", "gateway"), ("network", "internal")):
            try:
                self.remove_owned(kind, f"{prefix}-{suffix}", run_id)
            except (DockerError, OSError, subprocess.SubprocessError) as exc:
                errors.append(str(exc))
        if errors:
            raise DockerError("cleanup incomplete: " + "; ".join(errors))

    def workspace_mounts(self, root: Path, output: Path, research: Path) -> list[str]:
        return [*mount(root / "workspace", "/workspace"),
                *mount(root / "inputs", "/workspace/problem_files"),
                *mount(root / "skill/.agents", "/workspace/.agents"),
                *mount(output, "/workspace/paper_output", readonly=False),
                *mount(research, "/workspace/crawled_data", readonly=False)]

    def execute(self, root: Path, job: dict, image_id: str, session_id: str | None,
                api_key: str, feedback: dict, log: Path, on_event) -> Attempt:
        run_id = root.name
        prefix = self.prefix(run_id)
        gateway, network, agent = (f"{prefix}-{part}" for part in ("gateway", "internal", "agent"))
        token = secrets.token_urlsafe(32)
        proxy = None
        worker = None
        try:
            docker("network", "create", "--internal", *self.labels(run_id), network)
            docker("create", "-i", "--name", gateway, *self.labels(run_id),
                   *safety_options({"cpus": 1, "memory_mb": 512, "pids_limit": 64}),
                   "--network", "bridge", image_id, "python", "/opt/mathmodel/proxy.py")
            docker("network", "connect", "--alias", "gateway", network, gateway)
            proxy = subprocess.Popen(["docker", "start", "-a", "-i", gateway], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            ready = threading.Event()

            def watch_proxy():
                while line := proxy.stdout.readline(1024):
                    if line.strip() == b"READY":
                        ready.set()
                proxy.stdout.close()

            worker = threading.Thread(target=watch_proxy, daemon=True)
            worker.start()
            proxy.stdin.write(json.dumps({"token": token, "api_key": api_key,
                                          "base_url": job["engine"]["base_url"],
                                          "model": job["engine"]["model"]}).encode())
            proxy.stdin.close()
            if not ready.wait(20) or proxy.poll() is not None:
                raise DockerError("credential gateway did not become ready")
            docker("create", "-i", "--name", agent, *self.labels(run_id),
                   *safety_options(job["runtime"]), "--network", network,
                   "--env", f"MM_PROXY_TOKEN={token}",
                   *self.workspace_mounts(root, root / "output", root / "research"),
                   *mount(root / "codex-home", "/home/agent", readonly=False),
                   image_id, "python", "/opt/mathmodel/agent.py")
            return consume(["docker", "start", "-a", "-i", agent],
                           {"job": job, "session_id": session_id, "feedback": feedback},
                           log, job["runtime"]["attempt_timeout_seconds"], (api_key, token), on_event)
        finally:
            try:
                self.cleanup(run_id)
            finally:
                if proxy:
                    if proxy.poll() is None:
                        proxy.kill()
                    proxy.wait(timeout=10)
                if worker:
                    worker.join(timeout=2)

    def verify(self, root: Path, snapshot: Path, job: dict, image_id: str, log: Path) -> int:
        name = f"{self.prefix(root.name)}-verify"
        try:
            docker("create", "-i", "--name", name, *self.labels(root.name),
                   *safety_options(job["runtime"]), "--network", "none",
                   "--tmpfs", "/home/agent:rw,nosuid,nodev,size=268435456,mode=1777",
                   *self.workspace_mounts(root, snapshot / "paper_output", snapshot / "crawled_data"),
                   *mount(snapshot / "verdict", "/verification", readonly=False),
                   image_id, "python", "-I", "/opt/mathmodel/verify.py")
            return consume(["docker", "start", "-a", "-i", name],
                           {"target_pages": job["delivery"]["target_pages"]}, log, 2100).code
        finally:
            self.remove_owned("container", name, root.name)


def build_image():
    require_local_docker()
    context = files("mathmodel_cli").joinpath("resources/runtime")
    result = subprocess.run(["docker", "build", "--tag", IMAGE, str(context)])
    if result.returncode:
        raise DockerError("image build failed")

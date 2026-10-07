import copy
import importlib.util
from pathlib import Path

import pytest

from mathmodel_cli.config import DEFAULT_JOB
from mathmodel_cli.store import Store


@pytest.fixture
def job():
    result = copy.deepcopy(DEFAULT_JOB)
    result["engine"]["model"] = "offline-test-model"
    result["engine"]["api_key_env"] = "TEST_API_KEY"
    return result


@pytest.fixture
def prepared(tmp_path, job):
    inputs, skill = tmp_path / "inputs", tmp_path / "skill"
    inputs.mkdir()
    (inputs / "problem.txt").write_text("Offline fixture only; not a real contest submission.", "utf-8")
    skill.mkdir()
    (skill / "SKILL.md").write_text("Offline Skill fixture", "utf-8")
    (skill / ".agents").mkdir()
    store = Store(tmp_path / "home")
    run_id = store.create(job, inputs, skill)
    return store, run_id


def runtime_module(name):
    path = Path(__file__).parents[1] / f"src/mathmodel_cli/resources/runtime/{name}.py"
    spec = importlib.util.spec_from_file_location(f"runtime_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

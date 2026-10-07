import pytest

from mathmodel_cli.cli import main


def test_init_validate_status_no_api(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    home, project = tmp_path / "home", tmp_path / "project"
    common = ["--home", str(home)]
    assert main([*common, "init", str(project)]) == 0
    assert main([*common, "validate", str(project / "job.yaml")]) == 0
    assert main([*common, "status", "--json"]) == 0
    assert "[]" in capsys.readouterr().out
    assert main([*common, "init", str(project)]) == 2


def test_offline_run_rejected_before_model(tmp_path):
    assert main(["run", str(tmp_path / "nonexistent.yaml"), "--offline"]) == 2


def test_init_preserves_existing_files(tmp_path):
    (tmp_path / "keep").write_text("mine")
    assert main(["init", str(tmp_path)]) == 2
    assert (tmp_path / "keep").read_text() == "mine"


@pytest.mark.parametrize("command", ["status", "logs", "resume", "verify", "stop"])
def test_bad_id_is_clean_error(tmp_path, command):
    assert main(["--home", str(tmp_path), command, "../../bad"]) == 2

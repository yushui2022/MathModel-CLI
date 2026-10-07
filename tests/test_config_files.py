import os

import pytest
import yaml

from mathmodel_cli.config import load_job, new_job, validate_job
from mathmodel_cli.files import copy_tree, inventory, read_json, relative_path, write_json


@pytest.mark.parametrize("value", ["../x", "x/../y", "/tmp/a", "C:\\data", "a//b", "a/./b",
                                  "\\\\host\\share", "a:ads", "NUL", "a/CON.txt", "a.", "a ", "a\n"])
def test_paths_reject_escape(value):
    with pytest.raises(ValueError):
        relative_path(value)


@pytest.mark.parametrize("value,expected", [("inputs/data.csv", "inputs/data.csv"),
                                           ("附件\\数据.csv", "附件/数据.csv"),
                                           ("inputs\\子目录/file.csv", "inputs/子目录/file.csv")])
def test_portable_paths(value, expected):
    assert relative_path(value).as_posix() == expected


def test_yaml_roundtrip(tmp_path, job):
    path = tmp_path / "job.yaml"
    path.write_text(yaml.safe_dump(job), "utf-8")
    assert load_job(path) == job
    assert new_job("数学建模")["name"] == "数学建模"


@pytest.mark.parametrize("text", ["name: one\nname: two", "x: &x [*x]", "x: !!python/object/apply:os.system ['whoami']"])
def test_unsafe_yaml(tmp_path, text):
    path = tmp_path / "job.yaml"
    path.write_text(text, "utf-8")
    with pytest.raises(ValueError):
        load_job(path)


@pytest.mark.parametrize("change", [
    {"edition": "pro"}, {"inputs": "../secrets"}, {"command": "rm -rf /"}, {"name": "line\nbreak"},
])
def test_closed_schema(job, change):
    job.update(change)
    with pytest.raises(ValueError):
        validate_job(job)


@pytest.mark.parametrize("endpoint", ["http://api.example/v1", "https://user:pass@example.com/v1",
                                     "https://example.com:999/v1", "https://example.com/v1?key=abc",
                                     "https://example.com/v1/../admin", "https://example.com\\@evil.test"])
def test_bad_api_endpoints(job, endpoint):
    job["engine"]["base_url"] = endpoint
    with pytest.raises(ValueError):
        validate_job(job)


def test_invalid_range_and_runtime(job):
    job["delivery"]["target_pages"] = [24, 18]
    with pytest.raises(ValueError):
        validate_job(job)
    job["delivery"]["target_pages"] = [18, 24]
    job["runtime"]["cpus"] = True
    with pytest.raises(ValueError):
        validate_job(job)


def test_json_strict(tmp_path):
    path = tmp_path / "data.json"
    write_json(path, {"hello": "中文"})
    assert read_json(path) == {"hello": "中文"}
    for text in ('{"x":1,"x":2}', '{"x":NaN}', '[]'):
        path.write_text(text, "utf-8")
        with pytest.raises(ValueError):
            read_json(path)


def test_snapshot_and_limits(tmp_path):
    source, target = tmp_path / "in", tmp_path / "out"
    source.mkdir()
    (source / "数据.csv").write_text("x,y\n1,2", "utf-8")
    assert copy_tree(source, target) == inventory(source)
    with pytest.raises(ValueError):
        copy_tree(source, source / "nested")
    with pytest.raises(ValueError):
        copy_tree(source, target)
    with pytest.raises(ValueError):
        inventory(source, max_bytes=1)


def test_hardlink_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "a").write_text("hello")
    os.link(root / "a", root / "b")
    with pytest.raises(ValueError, match="hard links"):
        inventory(root)


def test_symlink_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("symlink privileges unavailable")
    with pytest.raises(ValueError):
        inventory(root)

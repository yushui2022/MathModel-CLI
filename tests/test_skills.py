import hashlib
import json
import stat
import zipfile

import pytest

from mathmodel_cli import skills
from mathmodel_cli.files import inventory, sha256


def archive(tmp_path, monkeypatch, additions=None):
    payload = {"VERSION": b"2.3.0\n",
               ".agents/skills/paper-workflow-orchestrator/MATHMODEL_EDITION.json": b'{"edition":"standard"}',
               ".agents/skills/paper-workflow-orchestrator/SKILL.md": b"test skill"}
    payload.update(additions or {})
    manifest = {"files": {name: hashlib.sha256(data).hexdigest() for name, data in payload.items()}}
    payload["MATHMODEL_BUILD.json"] = json.dumps(manifest).encode()
    path = tmp_path / "skill.zip"
    with zipfile.ZipFile(path, "w") as out:
        for name, data in payload.items():
            out.writestr(name, data)
    monkeypatch.setattr(skills, "SKILL_SHA256", sha256(path))
    return path


def test_verified_cache_no_replace_race(tmp_path, monkeypatch):
    path = archive(tmp_path, monkeypatch)
    home = tmp_path / "home"
    cache = skills.ensure_skill(home, path, download=False)
    before = cache.stat().st_mtime_ns
    assert skills.ensure_skill(home, download=False) == cache
    assert cache.stat().st_mtime_ns == before
    assert inventory(cache)
    (cache / "VERSION").write_text("hacked")
    with pytest.raises(ValueError, match="cache changed"):
        skills.ensure_skill(home, download=False)


@pytest.mark.parametrize("filename", ["../outside.txt", "/etc/passwd", "C:/outside.txt", "a/../../b", "VERSION "])
def test_archive_escape(tmp_path, monkeypatch, filename):
    path = archive(tmp_path, monkeypatch, {filename: b"payload"})
    with pytest.raises(ValueError):
        skills.extract_verified(path, tmp_path / "target")
    assert not (tmp_path / "outside.txt").exists()


def test_wrong_checksum(tmp_path):
    path = tmp_path / "bad.zip"
    path.write_bytes(b"not the pinned archive")
    with pytest.raises(ValueError, match="SHA-256"):
        skills.extract_verified(path, tmp_path / "target")


def test_manifest_and_duplicates(tmp_path, monkeypatch):
    path = archive(tmp_path, monkeypatch)
    with zipfile.ZipFile(path, "a") as out:
        out.writestr("unexpected.txt", b"unlisted")
    monkeypatch.setattr(skills, "SKILL_SHA256", sha256(path))
    with pytest.raises(ValueError, match="manifest"):
        skills.extract_verified(path, tmp_path / "target")


def test_zip_links(tmp_path, monkeypatch):
    path = tmp_path / "link.zip"
    entry = zipfile.ZipInfo("link")
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(path, "w") as out:
        out.writestr(entry, "../../outside")
    monkeypatch.setattr(skills, "SKILL_SHA256", sha256(path))
    with pytest.raises(ValueError, match="links"):
        skills.extract_verified(path, tmp_path / "target")


def test_case_collision(tmp_path, monkeypatch):
    path = archive(tmp_path, monkeypatch, {"version": b"duplicate"})
    with pytest.raises(ValueError, match="duplicate"):
        skills.extract_verified(path, tmp_path / "target")

from __future__ import annotations

import json
import shutil
import stat
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from filelock import FileLock

from .config import SKILL_SHA256, SKILL_URL
from .files import inventory, relative_path, sha256

LIMIT = 64 * 1024**2


def extract_verified(archive: Path, target: Path) -> None:
    if archive.stat().st_size > LIMIT or sha256(archive) != SKILL_SHA256:
        raise ValueError("Skill ZIP does not match the pinned Standard archive SHA-256")
    with zipfile.ZipFile(archive) as bundle:
        names, total = set(), 0
        for entry in bundle.infolist():
            name = relative_path(entry.filename.rstrip("/")).as_posix()
            if name.casefold() in names:
                raise ValueError("duplicate archive path")
            names.add(name.casefold())
            mode = (entry.external_attr >> 16) & 0o170000
            if mode not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise ValueError("archive contains links or special files")
            total += entry.file_size
            if total > LIMIT or len(names) > 5000:
                raise ValueError("archive exceeds extraction limit")
            if entry.is_dir():
                continue
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with bundle.open(entry) as source, destination.open("xb") as sink:
                shutil.copyfileobj(source, sink)
    manifest = json.loads((target / "MATHMODEL_BUILD.json").read_text("utf-8"))
    actual = inventory(target)
    actual.pop("MATHMODEL_BUILD.json", None)
    if actual != manifest["files"]:
        raise ValueError("archive payload manifest mismatch")
    marker = target / ".agents/skills/paper-workflow-orchestrator/MATHMODEL_EDITION.json"
    if json.loads(marker.read_text("utf-8"))["edition"] != "standard":
        raise ValueError("expected Standard edition")


def ensure_skill(home: Path, archive: Path | None = None, *, download: bool = True) -> Path:
    cache = home / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / SKILL_SHA256
    with FileLock(str(cache / "skill.lock"), timeout=60):
        # Re-extract from the pinned archive on each prepare; a mutated cache is never trusted.
        pinned = cache / f"{SKILL_SHA256}.zip"
        if archive is not None:
            if sha256(archive) != SKILL_SHA256:
                raise ValueError("local archive SHA-256 mismatch")
            if archive.resolve() != pinned.resolve():
                shutil.copyfile(archive, pinned)
        if not pinned.exists():
            if not download:
                raise ValueError("Skill not cached; use fetch-skill or prepare --skill-archive")
            request = urllib.request.Request(SKILL_URL, headers={"User-Agent": "MathModel-CLI/0.1"})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(LIMIT + 1)
            if len(data) > LIMIT:
                raise ValueError("Skill download exceeds limit")
            temporary = cache / "skill-download.tmp"
            try:
                temporary.write_bytes(data)
                if sha256(temporary) != SKILL_SHA256:
                    raise ValueError("downloaded Skill SHA-256 mismatch")
                temporary.replace(pinned)
            finally:
                temporary.unlink(missing_ok=True)
        with tempfile.TemporaryDirectory(prefix="skill-", dir=cache) as directory:
            extracted = Path(directory) / "payload"
            extracted.mkdir()
            extract_verified(pinned, extracted)
            if target.exists():
                if target.is_symlink() or target.resolve().parent != cache.resolve():
                    raise ValueError("unsafe cache directory")
                if inventory(target) != inventory(extracted):
                    raise ValueError("Skill cache changed; remove the corrupted cache before retrying")
            else:
                shutil.move(str(extracted), str(target))
    return target

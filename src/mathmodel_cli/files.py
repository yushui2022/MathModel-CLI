from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def json_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".atomic-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f"non-finite JSON value: {value}")

    value = json.loads(path.read_text("utf-8"), object_pairs_hook=unique, parse_constant=invalid)
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path.name}")
    return value


def relative_path(value: str) -> PurePosixPath:
    normalized = value.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (not normalized or path.is_absolute() or re.search(r"[\x00-\x1f:<>|?*]", normalized)
            or any(part in ("..", ".", "") for part in normalized.split("/"))):
        raise ValueError(f"unsafe relative path: {value!r}")
    for part in path.parts:
        if part.endswith((" ", ".")) or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part):
            raise ValueError(f"nonportable path: {value!r}")
    return path


def no_link(path: Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise ValueError(f"symlink/reparse point is not allowed: {path}")
    if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
        raise ValueError(f"special file is not allowed: {path}")
    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        raise ValueError(f"hard links are not allowed: {path}")


def inventory(root: Path, *, max_bytes: int = 2 * 1024**3, max_files: int = 20000) -> dict[str, str]:
    no_link(root)
    if not root.is_dir():
        raise ValueError(f"expected a directory: {root}")
    result, seen = {}, set()
    total = 0
    for folder, directories, names in os.walk(root, followlinks=False):
        if len(seen) + len(directories) + len(names) > max_files:
            raise ValueError("workspace exceeds the entry count safety limit")
        for name in sorted([*directories, *names]):
            path = Path(folder) / name
            no_link(path)
            key = relative_path(path.relative_to(root).as_posix()).as_posix()
            if key.casefold() in seen:
                raise ValueError(f"case-insensitive path collision: {key}")
            seen.add(key.casefold())
            if path.is_file():
                total += path.stat().st_size
                if total > max_bytes or len(result) >= max_files:
                    raise ValueError("workspace exceeds the file count or byte safety limit")
                result[key] = sha256(path)
    return dict(sorted(result.items()))


def copy_tree(source: Path, target: Path) -> dict[str, str]:
    if target.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(target.resolve()):
        raise ValueError("snapshot source and destination must be separate directories")
    before = inventory(source)
    target.mkdir(parents=True, exist_ok=True)
    inventory(target)
    if any(target.iterdir()):
        raise ValueError("snapshot destination must be empty")
    for relative in before:
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, destination, follow_symlinks=False)
    if inventory(target) != before or inventory(source) != before:
        raise ValueError("files changed while copying; snapshot rejected")
    return before

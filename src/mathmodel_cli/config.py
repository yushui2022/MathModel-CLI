from __future__ import annotations

import copy
import json
import re
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from jsonschema import Draft202012Validator

from .files import relative_path

SKILL_COMMIT = "77128764d65e59f6673ea65e5a8dc2ed85eb9885"
SKILL_SHA256 = "5d2ac59be5d9473c82b80cb84e99c929dbe9b7485e34ac05587ddffcececa731"
SKILL_URL = (f"https://raw.githubusercontent.com/yushui2022/MathModel-Skill/{SKILL_COMMIT}"
             "/dist/MathModel-Skill-Codex.zip")
IMAGE = "mathmodel-cli/standard:0.1.0a1"
CODEX_VERSION = "0.160.1"
DEFAULT_JOB = {
    "schema_version": "1",
    "name": "mathmodel-paper",
    "edition": "standard",
    "inputs": "problem_files",
    "engine": {"name": "codex", "model": "YOUR_MODEL_ID", "effort": "high",
               "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    "delivery": {"language": "zh-CN", "target_pages": [18, 24],
                 "instructions": "Follow the current contest rules. Do not pad the paper to reach a page target."},
    "runtime": {"cpus": 4, "memory_mb": 8192, "pids_limit": 256,
                "attempt_timeout_seconds": 7200, "max_attempts": 3},
}


class ConfigError(ValueError):
    pass


class UniqueLoader(yaml.SafeLoader):
    def compose_node(self, parent, index):
        if self.check_event(yaml.AliasEvent):
            raise ConfigError("YAML aliases/anchors are not supported")
        return super().compose_node(parent, index)

    def construct_mapping(self, node, deep=False):
        pairs = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in pairs:
                raise ConfigError("YAML keys must be unique strings")
            pairs[key] = self.construct_object(value_node, deep=deep)
        return pairs


def validate_job(data: object) -> dict:
    schema = json.loads(files("mathmodel_cli").joinpath("resources/job.schema.json").read_text("utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(data), key=lambda e: str(e.path))
    if errors:
        error = errors[0]
        raise ConfigError(f"{'.'.join(map(str, error.path)) or 'job'}: {error.message}")
    data = copy.deepcopy(data)
    relative_path(data["inputs"])
    target = data["delivery"]["target_pages"]
    if target[0] > target[1]:
        raise ConfigError("target_pages minimum must not exceed maximum")
    parsed = urlsplit(data["engine"]["base_url"])
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)
            or re.search(r"[\s\\\x00-\x1f]", data["engine"]["base_url"])):
        raise ConfigError("base_url must be an HTTPS endpoint without credentials, query, or custom port")
    if parsed.path not in ("", "/", "/v1", "/v1/"):
        raise ConfigError("v0.1 supports API endpoints at / or /v1 only")
    data["engine"]["base_url"] = data["engine"]["base_url"].rstrip("/")
    return data


def load_job(path: Path) -> dict:
    if path.stat().st_size > 64 * 1024:
        raise ConfigError("job.yaml exceeds 64 KiB")
    try:
        return validate_job(yaml.load(path.read_text("utf-8-sig"), Loader=UniqueLoader))
    except (yaml.YAMLError, RecursionError, UnicodeError) as exc:
        raise ConfigError("invalid YAML; use a plain declarative job without YAML tags or aliases") from exc


def new_job(name: str) -> dict:
    result = copy.deepcopy(DEFAULT_JOB)
    result["name"] = name
    return validate_job(result)

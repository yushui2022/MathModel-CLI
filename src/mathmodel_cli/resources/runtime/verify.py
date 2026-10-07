"""Trusted, credential-free validation in a separate container and output snapshot."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/workspace")
SKILLS = ROOT / ".agents/skills"


def main() -> int:
    request = json.load(sys.stdin)
    checks = []
    evidence_path = SKILLS / "quality-assurance-auditor/scripts/evidence_gate.py"
    sys.path.insert(0, str(evidence_path.parent))
    spec = importlib.util.spec_from_file_location("trusted_evidence_gate", evidence_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    try:
        # Recompute without rewriting the original evidence report: its hash binds S7.
        evidence = module.evaluate()
        checks.append({"check": "evidence", "passed": evidence.get("status") == "PASS",
                       "failures": evidence.get("failures", [])[:30]})
        for check, relative, arguments in (
            ("authoring", "paper-formal-writer/scripts/validate_authoring.py", ["--final"]),
            ("format", "paper-formal-writer/scripts/check_paper_format.py", ["--render", "required"]),
            ("workflow", "paper-workflow-orchestrator/scripts/workflow_guard.py", ["--step", "S8"]),
        ):
            if not all(item["passed"] for item in checks):
                break
            process = subprocess.run([sys.executable, "-E", "-s", "-B", str(SKILLS / relative), *arguments],
                                     cwd=ROOT, capture_output=True, text=True, timeout=600)
            checks.append({"check": check, "passed": process.returncode == 0,
                           "output": (process.stdout + process.stderr)[-12000:]})
        if all(item["passed"] for item in checks) and len(checks) == 4:
            report = json.loads((ROOT / "paper_output/format_check_report.json").read_text("utf-8"))
            pages = report.get("render_qa", {}).get("page_count", 0)
            low, high = request["target_pages"]
            checks.append({"check": "requested_total_pdf_pages", "passed": low <= pages <= high,
                           "actual": pages, "target": [low, high]})
            checks.append({"check": "competition_scope", "passed": report.get("delivery_mode") == "competition"})
        passed = len(checks) == 6 and all(item["passed"] for item in checks)
        result = {"schema_version": "1", "status": "PASS" if passed else "BLOCKED", "checks": checks,
                  "scope": "STANDARD_AUTOMATED_CHECKS_NOT_SCIENTIFIC_CERTIFICATION"}
    except Exception as exc:
        result = {"schema_version": "1", "status": "BLOCKED", "checks": checks,
                  "error": f"{type(exc).__name__}: {exc}"}
    Path("/verification/result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), "utf-8")
    print(json.dumps({"verification": result["status"]}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

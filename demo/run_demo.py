"""Offline demo. No n8n instance, no network, no keys.

    python demo/run_demo.py

1. Validates the ten workflows.
2. Prints the main path and every failure path for two of them.
3. Breaks a copy of one workflow in twelve realistic ways and shows the validator catching each.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from breakages import BREAKAGES  # noqa: E402
from n8n_validator import validate_workflow  # noqa: E402
from n8n_validator.__main__ import main as cli  # noqa: E402
from n8n_validator.checks import RULES  # noqa: E402


def heading(text: str) -> None:
    print(f"\n{text}\n{'=' * len(text)}")


def main() -> int:
    import os
    os.chdir(ROOT)

    heading("1. Validate the library")
    status = cli(["validate", "workflows"])

    heading("2. Where does a failed item go?")
    cli(["trace", "workflows/01-lead-intake", "workflows/10-error-alerting"])

    heading("3. Break a copy of Lead Intake on purpose")
    good = json.loads((ROOT / "workflows/01-lead-intake/workflow.json").read_text(encoding="utf-8"))
    caught = 0
    for title, expected, damage in BREAKAGES:
        broken = copy.deepcopy(good)
        damage(broken)
        findings = validate_workflow(broken)
        hit = any(f.rule == expected for f in findings)
        caught += hit
        print(f"\n{'CAUGHT' if hit else 'MISSED'}  {title}  (expects {expected}: {RULES[expected]})")
        for finding in findings:
            print(f"        {finding}")
    print(f"\n{caught} of {len(BREAKAGES)} breakages caught")
    return 0 if status == 0 and caught == len(BREAKAGES) else 1


if __name__ == "__main__":
    sys.exit(main())

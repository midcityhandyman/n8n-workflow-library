"""Command line entry point.

    python -m n8n_validator validate workflows
    python -m n8n_validator trace workflows/01-lead-intake/workflow.json
    python -m n8n_validator rules
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import render
from .checks import RULES, validate_file


def find_workflows(targets: list[str]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        path = Path(target)
        if path.is_dir():
            files.extend(sorted(path.rglob("workflow.json")))
        else:
            files.append(path)
    return files


def cmd_validate(args: argparse.Namespace) -> int:
    files = find_workflows(args.paths)
    if not files:
        print("no workflow.json files found", file=sys.stderr)
        return 2
    report = {str(f.as_posix()): [x.to_dict() for x in validate_file(f)] for f in files}
    failed = sum(1 for findings in report.values() if findings)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for name, findings in report.items():
            print(f"{'FAIL' if findings else 'PASS'}  {name}")
            for item in findings:
                where = f" [{item['node']}]" if item["node"] else ""
                print(f"      {item['rule']}{where}: {item['message']}")
        print(f"\n{len(files) - failed} of {len(files)} workflows passed, {len(RULES)} rules each")
    return 1 if failed else 0


def cmd_trace(args: argparse.Namespace) -> int:
    for path in find_workflows(args.paths):
        wf = json.loads(path.read_text(encoding="utf-8"))
        print(f"{wf['name']}  ({path.as_posix()})")
        print("  main path : " + " -> ".join(render.happy_path(wf)))
        for line in render.error_paths(wf):
            print("  on failure: " + line)
        print(f"  last resort: settings.errorWorkflow = {wf.get('settings', {}).get('errorWorkflow', '(none)')}")
        print()
    return 0


def cmd_rules(_: argparse.Namespace) -> int:
    for rule, text in RULES.items():
        print(f"{rule:<10} {text}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="n8n_validator", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="check workflow files, exit 1 on any finding")
    validate.add_argument("paths", nargs="+", help="workflow.json files or folders to search")
    validate.add_argument("--json", action="store_true", help="print findings as JSON")
    validate.set_defaults(func=cmd_validate)

    trace = sub.add_parser("trace", help="print the main path and every error path")
    trace.add_argument("paths", nargs="+")
    trace.set_defaults(func=cmd_trace)

    rules = sub.add_parser("rules", help="list the validation rules")
    rules.set_defaults(func=cmd_rules)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

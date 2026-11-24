"""Generate workflows/<nn-slug>/{workflow.json,README.md,sample.json} and .env.example.

    python tools/build_workflows.py          write the files
    python tools/build_workflows.py --check  exit 1 if the files on disk differ

The JSON files are the product; they import into n8n as they are. This script
exists so the shared reliability pattern and the docs cannot drift apart.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import flowkit  # noqa: E402
import specs  # noqa: E402
from n8n_validator import render  # noqa: E402


def dump(value) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def readme(flow: flowkit.Flow, wf: dict) -> str:
    env = flowkit.env_vars_used(wf)
    creds = flowkit.credentials_used(wf)
    lines = [
        f"# {flow.number:02d} {flow.name}",
        "",
        flow.purpose,
        "",
        "## Trigger",
        "",
        flow.trigger_text,
        "",
        "## Flow",
        "",
        "Dashed lines are error outputs. Shaded nodes are the dead letter path.",
        "",
        "```mermaid",
        render.mermaid(wf),
        "```",
        "",
        "## Nodes",
        "",
        "| Node | Type | Retries | What it does |",
        "| --- | --- | --- | --- |",
    ]
    for node in wf["nodes"]:
        retries = f"{node['maxTries']} tries, {node['waitBetweenTries']} ms apart" if node.get("retryOnFail") else "n/a"
        lines.append(f"| {node['name']} | {flowkit.type_label(node['type'])} | {retries} | {node['notes']} |")
    lines += [
        "",
        "## Error handling",
        "",
        "1. Every node that calls an outside system retries before it gives up.",
        "2. An item that still fails leaves through the node's error output. Other items in the same run carry on.",
        f"3. `{flowkit.DL_BUILD}` records the workflow, the failed node, the error and the item itself.",
        f"4. `{flowkit.DL_STORE}` posts that record to `DEAD_LETTER_URL` so it can be fixed and replayed.",
        "5. The last dead letter node tells a person. If that also fails, the execution fails and "
        + ("there is nothing further to fall back to; n8n keeps the failed execution in its own log."
           if flow.is_error_workflow else
           "the error workflow named in `settings.errorWorkflow` (workflow 10) takes over."),
        "",
        "Error paths in this workflow:",
        "",
        "```text",
        *render.error_paths(wf),
        "```",
        "",
        "## Configuration",
        "",
        "| Environment variable | Purpose |",
        "| --- | --- |",
        *[f"| `{name}` | {flowkit.ENV[name][1]} |" for name in env],
        "",
        "Credentials to create in n8n (type: Header Auth) and select on the nodes after import: "
        + ", ".join(f"`{c}`" for c in creds) + ".",
        "The JSON holds only a placeholder id and the credential name. No secret is stored in the file.",
        "",
        f"## {flow.sample_title}",
        "",
        "Synthetic data, also saved as `sample.json`.",
        "",
        "```json",
        dump(flow.sample).rstrip(),
        "```",
        "",
        "## Import",
        "",
        "1. In the n8n editor open a new workflow, then the three dot menu > Import from File, and pick `workflow.json`. "
        "On a self hosted server `n8n import:workflow --input=workflow.json` does the same.",
        "2. Create the Header Auth credentials named above and select them on the nodes that show a warning.",
        "3. Set the environment variables above on the n8n host. n8n 2.x blocks `$env` in nodes by default, "
        "so also set `N8N_BLOCK_ENV_ACCESS_IN_NODE=false`. On a plan that has n8n Variables you can instead "
        "replace each `$env.NAME` with `$vars.NAME`; the main README explains the trade off.",
    ]
    if flow.is_error_workflow:
        lines.append("4. Save and publish it (activate on n8n 1.x), then pick it under Settings > Error workflow "
                     "in every other workflow of the library.")
    else:
        lines.append("4. Under Settings > Error workflow pick the imported `Error Alerting` workflow "
                     f"(the file ships with the placeholder `{flowkit.ERROR_WORKFLOW_PLACEHOLDER}`).")
        lines.append("5. Run it once with the sample input, then publish it (activate on n8n 1.x).")
    lines += ["", "## Tuning notes", "", *[f"- {note}" for note in flow.tuning], ""]
    return "\n".join(lines)


def env_example() -> str:
    lines = [
        "# Environment variables read by the workflows through $env.",
        "# Set these on the n8n host. Every value below is a placeholder.",
        "# API keys do not belong here. Create them as n8n credentials instead.",
        "",
    ]
    for name, (example, text) in flowkit.ENV.items():
        lines += [f"# {text}", f"{name}={example}", ""]
    return "\n".join(lines)


def outputs() -> dict[Path, str]:
    files: dict[Path, str] = {ROOT / ".env.example": env_example()}
    for make in specs.ALL:
        flow = make()
        wf = flow.build()
        folder = ROOT / "workflows" / flow.slug
        files[folder / "workflow.json"] = dump(wf)
        files[folder / "README.md"] = readme(flow, wf)
        files[folder / "sample.json"] = dump(flow.sample)
    return files


def main() -> int:
    check = "--check" in sys.argv[1:]
    stale = []
    for path, text in outputs().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if check:
            stale.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(ROOT).as_posix()}")
    if stale:
        print("out of date, run python tools/build_workflows.py:\n  " + "\n  ".join(stale))
        return 1
    if check:
        print("generated files are up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())

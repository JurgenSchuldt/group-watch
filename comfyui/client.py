que """Minimal ComfyUI API client for teams and their coding agents. Standard library only.

    python client.py http://10.0.3.17:8188 ad-multishot.json --catalog catalog.json \\
        brief="A trail running shoe for weekend explorers" format=9:16

From outside the VPC use the team's /agent/ address and token:

    python client.py https://d1234.cloudfront.net/agent ad-multishot.json --token <token> ...

Queues the workflow with POST /prompt, polls GET /history/<id> until it finishes,
and downloads every output file with GET /view.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

FORMATS = {"16:9": (1280, 720), "9:16": (720, 1280)}
REQUIRED_TEXT = ("script",)


def apply_overrides(api_graph: dict, inputs: dict[str, dict], overrides: dict[str, object]) -> dict:
    """Return a copy of the graph with named inputs changed. `inputs` is a catalog entry's inputs."""
    graph = copy.deepcopy(api_graph)
    values = dict(overrides)
    if "format" in values:
        fmt = values.pop("format")
        if fmt not in FORMATS:
            raise ValueError(f"format must be one of {', '.join(FORMATS)}, not {fmt!r}")
        if not {"width", "height"} <= inputs.keys():
            raise ValueError("format: this workflow has no width/height inputs")
        values.setdefault("width", FORMATS[fmt][0])
        values.setdefault("height", FORMATS[fmt][1])
    for name, value in values.items():
        if name not in inputs:
            raise ValueError(f"unknown input {name!r}; this workflow takes: {', '.join(sorted(inputs))}")
        voice_on = values.get("use_voice", inputs.get("use_voice", {}).get("default", True))
        if name in REQUIRED_TEXT and not str(value).strip() and voice_on:
            raise ValueError(f"{name} must not be empty; for music only, also set use_voice=false")
        ref = inputs[name]
        graph[ref["node_id"]]["inputs"][ref["input_name"]] = value
    return graph


def parse_overrides(pairs: list[str], inputs: dict[str, dict]) -> dict[str, object]:
    """name=value strings from the command line, typed like each input's catalog default."""
    out: dict[str, object] = {}
    for pair in pairs:
        name, value = pair.split("=", 1)
        default = inputs.get(name, {}).get("default")
        if isinstance(default, bool):
            if value.lower() not in ("true", "false"):
                raise ValueError(f"{name} must be true or false")
            out[name] = value.lower() == "true"
        elif isinstance(default, (int, float)):
            out[name] = type(default)(value)
        else:
            out[name] = value
    return out


_token: str | None = None  # set by run_workflow / upload_image / main for the /agent/ address


def _headers(extra: dict | None = None) -> dict:
    h = dict(extra or {})
    if _token:
        h["Authorization"] = f"Bearer {_token}"
    return h


def _request(url: str, data: dict | None = None, timeout: float = 30) -> bytes:
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=_headers({"Content-Type": "application/json"} if body else None))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def queue(base_url: str, api_graph: dict) -> str:
    try:
        return json.loads(_request(f"{base_url}/prompt", {"prompt": api_graph, "client_id": str(uuid.uuid4())}))["prompt_id"]
    except urllib.error.HTTPError as e:
        detail = json.loads(e.read() or b"{}")
        problems = [detail.get("error", {}).get("message", str(e))]
        for node_id, err in detail.get("node_errors", {}).items():
            problems += [f"node {node_id}: {x.get('message')} {x.get('details', '')}".strip() for x in err.get("errors", [])]
        raise ValueError("; ".join(problems)) from None


def upload_image(base_url: str, path: Path, token: str | None = None) -> str:
    """Upload an image (or audio/video file) to ComfyUI's input folder; returns the name to put in the loader."""
    global _token
    _token = token or _token
    base_url = base_url.rstrip("/")
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(f"{base_url}/upload/image", data=body,
                                 headers=_headers({"Content-Type": f"multipart/form-data; boundary={boundary}"}))
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())["name"]


def run_workflow(base_url: str, api_graph: dict, timeout_s: int = 1800, out_dir: Path = Path("."),
                 poll_s: float = 2.0, token: str | None = None) -> list[Path]:
    """`token`: the team's agent token, needed for the https://<CloudFront>/agent address."""
    global _token
    _token = token
    base_url = base_url.rstrip("/")
    prompt_id = queue(base_url, api_graph)
    deadline = time.monotonic() + timeout_s
    while True:
        entry = json.loads(_request(f"{base_url}/history/{prompt_id}")).get(prompt_id)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                msgs = [m[1].get("exception_message", "") for m in status.get("messages", []) if m[0] == "execution_error"]
                raise RuntimeError(f"workflow failed: {'; '.join(msgs) or 'unknown error'}")
            if status.get("completed"):
                break
        if time.monotonic() > deadline:
            raise TimeoutError(f"workflow {prompt_id} still running after {timeout_s}s")
        time.sleep(poll_s)
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for output in entry.get("outputs", {}).values():
        for items in output.values():
            for item in items if isinstance(items, list) else []:
                if not isinstance(item, dict) or "filename" not in item or item.get("type") == "input":
                    continue  # loaders echo their input file; it is not a result
                q = urllib.parse.urlencode({k: item.get(k, "") for k in ("filename", "subfolder", "type")})
                target = out_dir / Path(item["filename"]).name
                target.write_bytes(_request(f"{base_url}/view?{q}", timeout=600))
                files.append(target)
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run a ComfyUI workflow (API format) and download its outputs.")
    ap.add_argument("url", help="ComfyUI address, e.g. http://10.0.3.17:8188")
    ap.add_argument("workflow", type=Path, help="workflow in API format, e.g. ad-multishot.json")
    ap.add_argument("overrides", nargs="*", help="name=value, e.g. brief=\"...\" format=9:16")
    ap.add_argument("--catalog", type=Path, default=Path("catalog.json"), help="catalog.json from the welcome page")
    ap.add_argument("--out", type=Path, default=Path("outputs"))
    ap.add_argument("--token", default=os.environ.get("COMFY_AGENT_TOKEN"),
                    help="agent token for the /agent/ address (or set COMFY_AGENT_TOKEN)")
    args = ap.parse_args(argv)
    graph = json.loads(args.workflow.read_text())
    if args.overrides:
        slug = args.workflow.stem
        entry = next(w for w in json.loads(args.catalog.read_text())["workflows"] if w["slug"] == slug)
        graph = apply_overrides(graph, entry["inputs"], parse_overrides(args.overrides, entry["inputs"]))
    for f in run_workflow(args.url, graph, out_dir=args.out, token=args.token):
        print(f)
    return 0


if __name__ == "__main__":
    sys.exit(main())

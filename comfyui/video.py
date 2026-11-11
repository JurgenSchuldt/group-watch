"""Connection + video generation helper on top of client.py (standard library only).

    from video import generate_video
    files = generate_video("text-to-video", prompt="Aerial view of a mountain lake at sunrise")

CLI:
    python video.py text-to-video prompt="Aerial view of a mountain lake" --out out/
    python video.py --check            # only test the connection

URL and token are read from COMFY_URL / COMFY_AGENT_TOKEN, or looked up with the AWS CLI
(CloudFormation outputs AgentUrl / AgentTokenSecret in us-east-2). Nothing is written to disk.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

import client

HERE = Path(__file__).parent
IDE_REGION = os.environ.get("IDE_REGION", "us-east-2")  # ComfyUI stack lives here, not in AWS_REGION
VIDEO_WORKFLOWS = ("text-to-video", "image-to-video", "ad-multishot", "ad-spokesperson",
                   "promo-from-video", "vertical-cut")


def _aws(*args: str) -> str:
    return subprocess.run(["aws", *args, "--region", IDE_REGION, "--output", "text"],
                          check=True, capture_output=True, text=True).stdout.strip()


def _stack_output(key: str) -> str:
    return _aws("cloudformation", "describe-stacks", "--stack-name", "remangu-comfyui",
                "--query", f"Stacks[0].Outputs[?OutputKey=='{key}'].OutputValue|[0]")


def get_connection() -> tuple[str, str]:
    """Return (base_url, token). Env vars win; otherwise resolved from AWS."""
    url = os.environ.get("COMFY_URL")
    token = os.environ.get("COMFY_AGENT_TOKEN")
    if not url:
        url = _stack_output("AgentUrl")
    if not token:
        token = _aws("secretsmanager", "get-secret-value", "--secret-id", _stack_output("AgentTokenSecret"),
                     "--query", "SecretString")
    return url.rstrip("/"), token


def check_connection() -> dict:
    """Ping /system_stats with the token; raises on failure."""
    url, token = get_connection()
    client._token = token
    return json.loads(client._request(f"{url}/system_stats"))


def generate_video(workflow: str = "text-to-video", out_dir: Path = Path("out"),
                   timeout_s: int = 1800, **overrides) -> list[Path]:
    """Run a catalog workflow with named overrides (e.g. prompt=..., format="9:16") and download results.

    For image/video inputs, upload first with client.upload_image and pass the returned name.
    """
    catalog = json.loads((HERE / "catalog.json").read_text())["workflows"]
    entry = next((w for w in catalog if w["slug"] == workflow), None)
    if entry is None:
        raise ValueError(f"unknown workflow {workflow!r}; video options: {', '.join(VIDEO_WORKFLOWS)}")
    graph = json.loads((HERE / "workflows" / f"{workflow}.json").read_text())
    if overrides:
        graph = client.apply_overrides(graph, entry["inputs"], overrides)
    url, token = get_connection()
    return client.run_workflow(url, graph, timeout_s=timeout_s, out_dir=Path(out_dir), token=token)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate a video with the team's ComfyUI.")
    ap.add_argument("workflow", nargs="?", default="text-to-video", help=", ".join(VIDEO_WORKFLOWS))
    ap.add_argument("overrides", nargs="*", help="name=value, see catalog.json")
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--check", action="store_true", help="only test the connection")
    args = ap.parse_args(argv)
    try:
        if args.check:
            stats = check_connection()
            print("OK", stats.get("system", {}).get("comfyui_version", ""))
            return 0
        entry = next(w for w in json.loads((HERE / "catalog.json").read_text())["workflows"]
                     if w["slug"] == args.workflow)
        overrides = client.parse_overrides(args.overrides, entry["inputs"])
        for f in generate_video(args.workflow, out_dir=args.out, **overrides):
            print(f)
    except (urllib.error.URLError, subprocess.CalledProcessError, ValueError, StopIteration, RuntimeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

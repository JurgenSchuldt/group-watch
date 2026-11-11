"""Trailer for the chosen movie: Bedrock writes the brief and voice-over, ComfyUI renders a 10 s clip.

Uses the team's ComfyUI `ad-multishot` workflow (see ../comfyui/README.md). The GPU is shared, so
only one trailer renders at a time and it takes minutes. Token and URL come from COMFY_URL /
COMFY_AGENT_TOKEN or are looked up (stack output + Secrets Manager in us-east-2), never stored.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

import boto3

from agent import IDE_REGION, MODEL_ID, _bedrock

COMFY_DIR = Path(__file__).resolve().parent.parent / "comfyui"
sys.path.insert(0, str(COMFY_DIR))
import client as comfy  # noqa: E402  (comfyui/client.py, standard library only)

WORKFLOW = COMFY_DIR / "workflows" / "ad-multishot.json"
CATALOG = COMFY_DIR / "catalog.json"
OUT_DIR = Path(__file__).with_name("out")
GPU_LOCK = threading.Lock()  # one render at a time on the shared GPU

BRIEF_TOOL = {
    "name": "submit_trailer",
    "description": "Submit the trailer brief and voice-over.",
    "inputSchema": {"json": {
        "type": "object", "required": ["brief", "script"],
        "properties": {
            "brief": {"type": "string", "description": "ONE line in English for an image/video model: setting, "
                      "action, mood, lighting. No text on screen, no real people, no brand names."},
            "script": {"type": "string", "description": "Voice-over, about 25 words, ends with a call to watch tonight."},
        }}},
}


def comfy_access() -> tuple[str, str]:
    url, token = os.environ.get("COMFY_URL"), os.environ.get("COMFY_AGENT_TOKEN")
    if url and token:
        return url, token
    cf = boto3.client("cloudformation", region_name=IDE_REGION)
    outs = {}
    for stack in cf.describe_stacks()["Stacks"]:
        for o in stack.get("Outputs", []):
            if o["OutputKey"] in ("AgentUrl", "AgentTokenSecret"):
                outs[o["OutputKey"]] = o["OutputValue"]
    if len(outs) != 2:
        raise RuntimeError("ComfyUI stack outputs not found; run `source comfyui/env.sh` first")
    secret = boto3.client("secretsmanager", region_name=IDE_REGION).get_secret_value(SecretId=outs["AgentTokenSecret"])
    return outs["AgentUrl"], secret["SecretString"]


def write_brief(movie: dict, lang: str, viewers: list[str]) -> dict:
    system = ("You write 10-second trailer briefs inspired by a film's synopsis. Do not depict real actors, "
              "characters' real likenesses, logos or franchise imagery: describe original, generic scenes. "
              f"The voice-over must be in language code '{lang}' and about 25 words.")
    prompt = json.dumps({"title": movie["title"], "synopsis": movie["synopsis"], "genres": movie["genres"],
                         # "tone" only exists in the small fictional catalog, not in catalog_real.json
                         "tone": movie.get("tone"), "watching_tonight": viewers}, ensure_ascii=False)
    resp = _bedrock().converse(
        modelId=MODEL_ID, system=[{"text": system}],
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        toolConfig={"tools": [{"toolSpec": BRIEF_TOOL}], "toolChoice": {"tool": {"name": BRIEF_TOOL["name"]}}},
        inferenceConfig={"maxTokens": 1000, "temperature": 0.7})
    for block in resp["output"]["message"]["content"]:
        if "toolUse" in block:
            return block["toolUse"]["input"]
    raise RuntimeError("model returned no trailer brief")


def render(movie: dict, lang: str, viewers: list[str], out_name: str, step) -> Path:
    """Write the brief, queue ComfyUI, download the mp4 as out/<out_name>.mp4. `step(name, status, detail)`."""
    step("Escribir el guion del tráiler", "running", movie["title"])
    brief = write_brief(movie, lang, viewers)
    step("Escribir el guion del tráiler", "done", brief["script"])

    if not GPU_LOCK.acquire(blocking=False):
        raise RuntimeError("La GPU está ocupada con otro tráiler; inténtalo en unos minutos.")
    try:
        step("Renderizar en ComfyUI (GPU)", "running", "En cola; suele tardar 5-8 minutos…")
        url, token = comfy_access()
        entry = next(w for w in json.loads(CATALOG.read_text())["workflows"] if w["slug"] == "ad-multishot")
        graph = comfy.apply_overrides(json.loads(WORKFLOW.read_text()), entry["inputs"],
                                      {"brief": brief["brief"], "script": brief["script"], "format": "16:9",
                                       "voice_style": "Cinematic movie-trailer narrator, deep, dramatic, medium pace"})
        files = comfy.run_workflow(url, graph, out_dir=OUT_DIR / out_name, token=token)
    finally:
        GPU_LOCK.release()
    videos = [f for f in files if f.suffix == ".mp4"]
    if not videos:
        raise RuntimeError("ComfyUI no devolvió ningún vídeo")
    target = OUT_DIR / f"{out_name}.mp4"
    videos[0].replace(target)
    step("Renderizar en ComfyUI (GPU)", "done", "Vídeo listo")
    return target

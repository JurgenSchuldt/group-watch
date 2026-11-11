# ComfyUI from code

Your team's ComfyUI workstation (by [Remangu](https://remangu.com/)) runs on its own GPU.
People use the canvas (`CanvasUrl` in **Event Outputs**). Agents and scripts use the
**agent API**: the same ComfyUI API, at `AgentUrl`, with a bearer token.

## Set up once per terminal

```bash
export COMFY_URL=$(aws cloudformation describe-stacks --region us-east-2 \
  --query "Stacks[].Outputs[?OutputKey=='AgentUrl'].OutputValue" --output text)
export COMFY_AGENT_TOKEN=$(aws secretsmanager get-secret-value --region us-east-2 \
  --secret-id "$(aws cloudformation describe-stacks --region us-east-2 \
  --query "Stacks[].Outputs[?OutputKey=='AgentTokenSecret'].OutputValue" --output text)" \
  --query SecretString --output text)
```

The token is also on your workstation's welcome page (`CanvasUrl`, then **Agent kit**).
Never commit it.

## Run a workflow

```bash
cd comfyui
python3 client.py "$COMFY_URL" workflows/ad-multishot.json --out out/ \
  brief="A lightweight trail running shoe for weekend explorers, sunrise on mountain trails" \
  script="Meet Ridge. Light enough for every summit, tough enough for every trail." \
  format=9:16
```

`client.py` queues the workflow, waits for it and downloads the results. Standard library
only. Each workflow's settings (`brief`, `script`, `seconds`, `format`, ...) are listed in
`catalog.json`, with a description and a default.

| Workflow | What it makes |
|---|---|
| `ad-multishot` | A 10-second ad with key visuals, motion, voice-over and music |
| `ad-spokesperson` | An ad with a talking presenter |
| `promo-from-video` | A promo cut from your own footage |
| `vertical-cut` | A 9:16 highlight cut from a video |
| `thumbnail-from-video` | A thumbnail from a video frame |
| `dub-voice` / `dub-talking-head` | Dubbing in the original voice, with or without lip sync |
| `product-shot` | A product image |
| `text-to-video` / `image-to-video` | Short clips (Wan2.2) |
| `music` / `sound-effects` / `voice-over` | Audio (Stable Audio 3, Qwen3-TTS) |
| `upscale-4x` | 4x image upscale |

To send your own file (a product photo, a video, a voice sample), upload it first with
`client.upload_image(COMFY_URL, Path("photo.jpg"), token)` and pass the returned name.

Turn `client.run_workflow` into an agent tool (for example a Strands `@tool`), let a
model write the settings, and chain it with Bedrock and S3. The GPU is shared by your
team, so jobs queue: one run at a time.

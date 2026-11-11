---
inclusion: always
---
# Hackathon context

This project is a one-day prototype built at the AWS M&E Agentic AI Partner Hackathon.
Optimise for a working end-to-end demo by the afternoon, not for production hardening.

- **AWS account and regions:** this IDE runs in the team's own AWS account, with
  credentials already configured. Use **`us-east-1`** (N. Virginia) for Bedrock and every
  AWS service you build with (`AWS_REGION` is already set to it). Bedrock is denied in
  `us-east-2`. Only the IDE, the team repo and ComfyUI live in `us-east-2` (`IDE_REGION`),
  so pass `--region us-east-2` when you read this event's CloudFormation stacks.
- **Models:** use Amazon Bedrock for LLMs and agents. Amazon Bedrock AgentCore is
  available for hosting agents.
- **Assets:** sample videos and the Audience 360 dataset are in the team's S3 bucket
  (the bucket name is in the event's outputs, `HackathonAssetsBucketName`). Read them
  from S3; do not copy large files into this repository. Bedrock video models
  (TwelveLabs Pegasus, Marengo) only read from a bucket in the region they are called
  in, so pass them videos from the toolkit bucket `MediaToolkitIngestBucket` (us-east-1),
  which has the same sample videos.
- **ComfyUI:** the team's GPU workstation is driven over HTTP. See `comfyui/README.md`:
  `comfyui/client.py` against `AgentUrl` (an output of this event's ComfyUI stack in
  `us-east-2`) with the bearer token from the `AgentTokenSecret` secret, and the ready
  workflows in `comfyui/workflows/` with their settings in `comfyui/catalog.json`. The GPU
  is shared by the team, so jobs queue: run one at a time and expect minutes per video.
- **Secrets:** never write API keys or credentials into code or into this repository.
  Read them from environment variables or AWS Secrets Manager.
- **Team workflow:** several people share this workspace. Keep changes small and
  scoped to the files you were asked to touch, and work on the current git branch.
- **Judging:** teams are scored on business value as well as the demo, so keep
  `TEAM.md` (problem, user, success metric) in sync with what is being built.

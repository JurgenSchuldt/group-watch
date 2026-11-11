#!/usr/bin/env bash
# Connect Kiro (IDE and kiro-cli) to your team's Unified Media Toolkit MCP gateway.
#
#   bash media-toolkit/connect.sh            connect (and install the toolkit skills)
#   bash media-toolkit/connect.sh --refresh  re-mint the token (it expires after 12 hours)
#
# The toolkit is deployed for you when the event starts and takes about 15 to 30 minutes to finish.
# The script writes the `media-toolkit` server into ~/.kiro/settings/mcp.json (user level,
# outside this repo, because the entry holds a bearer token). Never commit it.
set -euo pipefail

REGION=${MEDIA_TOOLKIT_REGION:-us-east-1}
STACK=${MEDIA_TOOLKIT_STACK:-unified-media-toolkit}
MCP_FILE="$HOME/.kiro/settings/mcp.json"
SKILLS_DIR="$HOME/.kiro/skills"
REFRESH_ONLY=false
[[ "${1:-}" == "--refresh" ]] && REFRESH_ONLY=true

status=$(aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
  --query 'Stacks[0].StackStatus' --output text 2>/dev/null || echo MISSING)
case "$status" in
  CREATE_COMPLETE|UPDATE_COMPLETE) ;;
  CREATE_IN_PROGRESS)
    echo "The media toolkit is still deploying in $REGION (it takes about 15 to 30 minutes). Try again later."; exit 1 ;;
  *)
    echo "Media toolkit stack '$STACK' in $REGION is '$status'. Ask a facilitator."; exit 1 ;;
esac

out() {
  aws cloudformation describe-stacks --region "$REGION" --stack-name "$STACK" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" --output text
}
SECRET_NAME=$(out GatewayCredentialsSecretName)
PROXY_BUCKET=$(out ProxyBucketName)

# Exchange the gateway's client credentials for a 12-hour bearer token. The client
# secret stays in this process and is never written to disk.
CREDS=$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_NAME" \
  --query SecretString --output text)
export CREDS MCP_FILE
python3 - <<'PY'
import base64, json, os, pathlib, urllib.parse, urllib.request

c = json.loads(os.environ["CREDS"])
basic = base64.b64encode(f"{c['clientId']}:{c['clientSecret']}".encode()).decode()
body = urllib.parse.urlencode({"grant_type": "client_credentials", "scope": c["scope"]}).encode()
req = urllib.request.Request(c["tokenEndpoint"], data=body, headers={
    "Authorization": f"Basic {basic}",
    "Content-Type": "application/x-www-form-urlencoded"})
token = json.loads(urllib.request.urlopen(req, timeout=30).read())["access_token"]

path = pathlib.Path(os.environ["MCP_FILE"])
path.parent.mkdir(parents=True, exist_ok=True)
cfg = json.loads(path.read_text()) if path.exists() and path.read_text().strip() else {}
cfg.setdefault("mcpServers", {})["media-toolkit"] = {
    "url": c["endpointUrl"],
    "headers": {"Authorization": f"Bearer {token}"},
    "disabled": False,
    # Read-only tools only (exact names); every submit still asks for confirmation.
    "autoApprove": ["media-ingest___list_files", "media-outputs___get_download_url",
                    "media-outputs___get_download_urls"],
}
path.write_text(json.dumps(cfg, indent=2))
path.chmod(0o600)
print(f"media-toolkit MCP server written to {path} (token valid 12 hours)")
PY

if [[ "$REFRESH_ONLY" == false ]]; then
  # The deployment publishes its skills (analyze-video, select-artworks, ...) in a client kit.
  tmp=$(mktemp -d)
  if aws s3 cp --region "$REGION" "s3://$PROXY_BUCKET/_toolkit/client-kit.zip" "$tmp/kit.zip" --quiet; then
    (cd "$tmp" && python3 -c "import zipfile; zipfile.ZipFile('kit.zip').extractall('kit')")
    if [[ -d "$tmp/kit/skills" ]]; then
      mkdir -p "$SKILLS_DIR"
      cp -R "$tmp/kit/skills/." "$SKILLS_DIR/"
      echo "Toolkit skills installed in $SKILLS_DIR"
    fi
  else
    echo "Could not download the client kit; the MCP tools still work without the skills."
  fi
  rm -rf "$tmp"
fi

echo "Done. Reload MCP servers in Kiro (or restart kiro-cli) and ask, for example:"
echo "  \"Using the media-toolkit tools, list the files in the ingest bucket.\""

#!/usr/bin/env bash
# Carga COMFY_URL y COMFY_AGENT_TOKEN para esta terminal.
# Uso:  source comfyui/env.sh
# No guarda ningún secreto: el token se lee de Secrets Manager en cada ejecución.

_stack_out() {
  aws cloudformation describe-stacks --region us-east-2 \
    --query "Stacks[].Outputs[?OutputKey=='$1'].OutputValue" --output text | tr -d '[:space:]'
}

export COMFY_URL="$(_stack_out AgentUrl)"
export COMFY_AGENT_TOKEN="$(aws secretsmanager get-secret-value --region us-east-2 \
  --secret-id "$(_stack_out AgentTokenSecret)" --query SecretString --output text)"

unset -f _stack_out
echo "ComfyUI listo: $COMFY_URL"

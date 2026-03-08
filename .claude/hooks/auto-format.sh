#!/bin/bash
# PostToolUse hook: auto-format files after Edit or Write
# Auto-detects the project's formatter. Falls back silently if none found.

INPUT=$(cat)
TOOL=$(echo "$INPUT" | jq -r '.tool_name // empty')

if [ "$TOOL" = "Write" ] || [ "$TOOL" = "Edit" ]; then
  FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // empty')
  [ -z "$FILE" ] || [ ! -f "$FILE" ] && exit 0

  # Auto-detect formatter (first match wins)
  if [ -f "biome.json" ] || [ -f "biome.jsonc" ]; then
    npx biome format --write "$FILE" 2>/dev/null
  elif [ -f ".prettierrc" ] || [ -f ".prettierrc.json" ] || [ -f ".prettierrc.yaml" ] || [ -f "prettier.config.js" ] || [ -f "prettier.config.mjs" ]; then
    npx prettier --write "$FILE" 2>/dev/null
  elif [[ "$FILE" == *.py ]] && command -v ruff &>/dev/null; then
    ruff format "$FILE" 2>/dev/null
  elif [[ "$FILE" == *.go ]] && command -v gofmt &>/dev/null; then
    gofmt -w "$FILE" 2>/dev/null
  elif [[ "$FILE" == *.dart ]] && command -v dart &>/dev/null; then
    dart format "$FILE" 2>/dev/null
  elif [[ "$FILE" == *.rs ]] && command -v rustfmt &>/dev/null; then
    rustfmt "$FILE" 2>/dev/null
  fi
fi

exit 0

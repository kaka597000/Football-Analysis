#!/usr/bin/env bash
set -euo pipefail
src="$(cd "$(dirname "$0")" && pwd)"
mkdir -p /workspace/ai-browser/run
cp "$src/package.json" "$src/package-lock.json" /workspace/ai-browser/
npm_config_cache=/workspace/.npm-cache npm ci --prefix /workspace/ai-browser
cat /usr/local/share/ca-certificates/*.crt > /workspace/ai-browser/proxy-ca.pem
cp "$src/browser" /workspace/ai-browser/browser
chmod +x /workspace/ai-browser/browser

#!/usr/bin/env bash
# 预拉镜像（Linux / macOS）
set -euo pipefail
images="${1:-vulhub/fastjson:1.2.24 vulhub/fastjson:1.2.47 vulhub/confluence:7.13.6}"
for img in $images; do
  start=$(date +%s)
  docker pull "$img" >/dev/null
  echo "  $img  $(( $(date +%s) - start ))s"
done

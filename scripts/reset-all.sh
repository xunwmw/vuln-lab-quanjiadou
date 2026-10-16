#!/usr/bin/env bash
# 复位自建靶场
set -euo pipefail
# target-site（8080/8091）没有 HTTP 复位端点：停服务后删 target_site.db 再启动
printf 'reset 8095 -> '
curl -fsS -X POST "http://127.0.0.1:8095/__reset?confirm=RESET" -o /dev/null -w '%{http_code}\n' || echo '失败'

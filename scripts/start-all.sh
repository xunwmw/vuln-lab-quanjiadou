#!/usr/bin/env bash
# 起两个自建靶场（仅本机）
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
( cd "$root/labs/target-site" && python3 app.py 8091 >/tmp/target-site.log 2>&1 & )
( cd "$root/labs/target-site-lab" && python3 app.py 8095 >/tmp/target-site-lab.log 2>&1 & )
sleep 2
for p in 8091 8095; do
  printf '  %s -> ' "$p"
  curl -fsS -o /dev/null -w '%{http_code}\n' "http://127.0.0.1:$p/" || echo '未就绪'
done

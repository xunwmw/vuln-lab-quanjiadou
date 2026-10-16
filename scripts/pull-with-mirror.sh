#!/usr/bin/env bash
# 用国内镜像站拉取 Docker Hub 镜像，并改回原名。
# 背景与用法见 docs/ENVIRONMENT.md 第 4 节；Windows 版：scripts/pull-with-mirror.ps1
#
#   bash scripts/pull-with-mirror.sh                      # 自动读 compose 文件里的 image
#   bash scripts/pull-with-mirror.sh vulnerables/web-dvwa # 指定镜像
#   MIRROR=docker.m.daocloud.io bash scripts/pull-with-mirror.sh
set -u

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mirrors=( "${MIRROR:-docker.1ms.run}" docker.m.daocloud.io docker.1panel.live )

if ! command -v docker >/dev/null 2>&1; then
  echo "ERR: 找不到 docker 命令。第三方靶场需要 Docker Engine 20.10+。" >&2
  exit 2
fi

images=("$@")
if [ ${#images[@]} -eq 0 ]; then
  while IFS= read -r line; do images+=("$line"); done < <(
    grep -rhoE '^[[:space:]]*image:[[:space:]]*[^"'"'"'[:space:]#]+' \
      --include='docker-compose*.yml' --include='docker-compose*.yaml' "$repo_root" 2>/dev/null \
      | sed -E 's/^[[:space:]]*image:[[:space:]]*//' | sort -u
  )
  echo "自动从 compose 文件收集到 ${#images[@]} 个镜像"
fi

if [ ${#images[@]} -eq 0 ]; then
  echo "ERR: 没有镜像可拉。请显式传入镜像名，例如：bash scripts/pull-with-mirror.sh vulnerables/web-dvwa" >&2
  exit 2
fi

fails=0
for img in "${images[@]}"; do
  echo "=== $img ==="
  if docker image inspect "$img" >/dev/null 2>&1; then
    echo "  本地已有该镜像，跳过"
    continue
  fi
  done_ok=0
  for m in "${mirrors[@]}"; do
    src="$m/$img"
    echo "  尝试 $src"
    if docker pull "$src" && docker tag "$src" "$img"; then
      echo "  OK → $img（原名已就绪）"
      done_ok=1
      break
    fi
    echo "  失败：$src"
  done
  if [ "$done_ok" -eq 0 ]; then
    echo "  所有镜像站都失败：$img"
    fails=$((fails + 1))
  fi
done

echo
if [ "$fails" -gt 0 ]; then
  echo "有 $fails 个镜像没拉成功。可换镜像站重试：MIRROR=docker.m.daocloud.io bash scripts/pull-with-mirror.sh"
  exit 1
fi
echo "全部就绪。接下来照常：cd third-party/dvwa && docker compose up -d"

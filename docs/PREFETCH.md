# 预拉镜像：省时间的关键

拉镜像往往是整条流程里最慢的一步（实测个别镜像单条要 20 分钟以上）。
所以规则是：**打当前一条的同时，后台把下一批镜像拉好**。

## 用法

```powershell
# 默认拉"常用 + 下一批"镜像
powershell -File scripts/prefetch-images.ps1

# 指定要预拉的镜像
powershell -File scripts/prefetch-images.ps1 -Images vulhub/fastjson:1.2.24,vulhub/confluence:7.13.6
```

```bash
# Linux / macOS
bash scripts/prefetch-images.sh
```

## 规则（写进任务排期）

1. 每批开打前：先把该批镜像列入预拉清单，后台执行
2. **同族连打**：一次排 3–4 条结构相近的目标（例如 Struts2 系列、Fastjson 1.2.24/1.2.47/1.2.83），减少语境切换
3. 记录每条镜像的实际拉取耗时（写进批次记录），用于后续排期估算
4. 镜像拉取失败时先查加速源，不要反复重试同一地址

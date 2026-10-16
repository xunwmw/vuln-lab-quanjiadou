# Vulhub（单点 CVE 靶场）

[Vulhub](https://github.com/vulhub/vulhub) 提供大量"一条 CVE 一个目录"的 Docker 环境，适合练：
**指纹识别 → 版本判定 → 打点 → 证据留存 → 修复方案**。

## 用法

```bash
git clone --depth 1 https://github.com/vulhub/vulhub.git
cd vulhub/<组件>/<CVE-id>
docker compose up -d          # 只在本机起容器
# 打完清理
docker compose down -v
```

## 注意事项

- 容器端口尽量只映射到 `127.0.0.1`（部分官方 compose 默认绑 `0.0.0.0`，**起之前先改**）
- JNDI / 反序列化类目标需要回连：**只允许回连到本机**（如本地假 LDAP `127.0.0.1:1389`），不要指向公网
- 拉镜像慢是常态：用 `scripts/prefetch-images.ps1` 提前拉（见 [docs/PREFETCH.md](../../docs/PREFETCH.md)）

## 常用镜像（预拉清单示例）

```
vulhub/shiro:1.2.4          vulhub/tomcat:8.5
vulhub/fastjson:1.2.24      vulhub/fastjson:1.2.47
vulhub/solr:7.0.1           vulhub/struts2:2.3.30
vulhub/nacos:1.4.0          vulhub/confluence:7.13.6
```

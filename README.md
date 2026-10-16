# vuln-labs — 本地漏洞靶场合集

**EN** — A curated collection of local-only vulnerability labs: two self-built business-logic labs,
plus ready-to-run setups for DVWA / bWAPP / Juice Shop / Vulhub. Every lab binds `127.0.0.1` only;
the goal is a reproducible practice environment, not a public service.

> 一个**只跑在本机**的漏洞靶场合集：包含 2 个自建业务逻辑靶场，以及 DVWA / bWAPP / Juice Shop / Vulhub 的一键启动方案。
> 所有靶场**只监听 127.0.0.1**，用于本地练习与自动化工具测试；请勿暴露到局域网或公网。

---

## 目录

| 靶场 | 类型 | 作用 | 端口 | 依赖 | 启动 |
| --- | --- | --- | --- | --- | --- |
| [labs/target-site](labs/target-site/README.md) | 自建 | 通用 Web 漏洞（早期版本） | 8080（可传参，如 `8091`） | Python 3 标准库 | `python app.py 8091` |
| [labs/target-site-lab](labs/target-site-lab/README.md) | 自建 | **业务逻辑**：订单/支付/优惠券/会员 | 8095 | Python 3 标准库 | `python app.py` |
| [third-party/dvwa](third-party/dvwa/) | 第三方 | 经典 Web 漏洞广度（LFI/SQLi/XSS/CSRF/上传…） | 8081 | Docker | `docker compose up -d` |
| [third-party/bwapp](third-party/bwapp/) | 第三方 | 100+ Web 漏洞广度 | 8082 | Docker | `docker compose up -d` |
| [third-party/juice-shop](third-party/juice-shop/) | 第三方 | 现代 SPA + 业务逻辑 + JWT + 组件 CVE | 3000 | Docker | `docker compose up -d` |
| [third-party/vulhub](third-party/vulhub/README.md) | 第三方 | 单点 CVE 复现（Shiro/Tomcat/Solr/Struts2…） | 按镜像 | Docker | 见说明 |

> 自建的 `target-site-lab` 同时以 [WebVulnLab](https://github.com/xunwmw/WebVulnLab) 单独立仓库发布；
> 本仓库把它和另一个自建站放在一起，便于"一处拉起全部靶场"。

## 快速开始（Windows）

```powershell
# 起两个自建靶场
powershell -File scripts/start-all.ps1
# → http://127.0.0.1:8091（target-site）  http://127.0.0.1:8095（target-site-lab）

# 起第三方靶场（按需）
cd third-party/dvwa && docker compose up -d
cd third-party/juice-shop && docker compose up -d

# 预拉下一批镜像（省时间，见 docs/PREFETCH.md）
powershell -File scripts/prefetch-images.ps1
```

## 快速开始（Linux / macOS / WSL）

```bash
bash scripts/start-all.sh
bash scripts/reset-all.sh      # 复位自建靶场
```

## 练习建议

1. **自建站练业务逻辑**：越权、支付金额篡改、优惠券并发竞态、密码重置缺陷、会话固定（这些扫描器扫不到）
2. **DVWA / bWAPP 补广度**：LFI、SQLi、XSS、CSRF、文件上传、命令注入…
3. **Juice Shop 练现代应用**：SPA 路由、JWT、组件版本 CVE
4. **Vulhub 练 CVE 复现**：一条 CVE 一个容器，练"指纹 → 版本 → 打点 → 证据"

## 复位

| 靶场 | 复位方式 |
| --- | --- |
| target-site | 停止 → 删除 `target_site.db` → 重启（该靶场无 HTTP 复位端点） |
| target-site-lab | `python app.py --reset` 或 `curl -X POST "http://127.0.0.1:8095/__reset?confirm=RESET"` |
| DVWA | `docker compose down -v && docker compose up -d` |
| bWAPP / Juice Shop | 同上（Juice Shop 另有内置"重置进度"） |
| Vulhub | `docker compose down -v`（每个 CVE 目录内） |

## 安全边界（务必阅读）

- 所有靶场**只监听 `127.0.0.1`**：连同一个局域网里的其他设备都访问不到，这是刻意设计
- **不要**把监听地址改成 `0.0.0.0`，也不要为了让别人访问而做端口转发/内网穿透
- 靶场里的种子口令**只用于本机练习**，任何其他系统都不得复用
- 这些靶场**故意包含漏洞**，请勿部署到任何可被外部访问的环境

详见 [docs/SAFETY.md](docs/SAFETY.md)。

## 添加新靶场

见 [docs/ADD-A-LAB.md](docs/ADD-A-LAB.md)：每个新靶场一个目录 + 一份 README（作用/依赖/启动/端口/账号/漏洞清单/复位/演练建议）+ CI 要求。

## 许可

自建靶场代码：MIT。第三方靶场（DVWA / bWAPP / Juice Shop / Vulhub）遵循各自原项目许可，本仓库只提供启动配置。

# 运行环境与网络要求（Environment & Network）

> **一句话结论**：自建的两个靶场只用 Python 标准库，**拿到就能跑、断网也能跑**；
> 第三方靶场（DVWA / bWAPP / Juice Shop / Vulhub）需要 Docker，而**中国大陆直连 Docker Hub 是不通的**，
> 必须先配镜像加速 —— 本文第 3、4 节给了两种做法，照抄即可。

实测时间：2026-10-16 15:02（UTC+8）
测试机：Windows 11 x64 · Python 3.12 · Node 20 · Docker 27（Compose v2）
网络：上海 · 中国移动 AS9808 家庭宽带 · **未开任何代理**
同一台机器开代理后复测：25/25 个端点全部可达（对比见下表）

---

## 1. 最低环境

| 组件 | 最低要求 | 我实测过的版本 | 说明 |
| --- | --- | --- | --- |
| 操作系统 | Windows 10 1909+ / Ubuntu 20.04+ / Debian 11+ / macOS 12+ | Windows 11 x64 | Win10 家庭版装 Docker 需要 WSL2 后端 |
| Python | 3.8+ | 3.12.7 | **只用于自建靶场**，标准库，无需 pip 安装任何东西 |
| Docker | Engine 20.10+ / Desktop 4.x（Compose **v2**：`docker compose`） | Docker 27 + Compose v2 | 只跑第三方靶场时需要 |
| 磁盘 | 约 6 GB 空闲 | — | Juice Shop ≈ 1 GB，DVWA/bWAPP ≈ 500 MB，Vulhub 镜像另算 |
| 内存 | 4 GB+ | 8 GB | Juice Shop 是 Node 应用，吃内存比另两个多 |
| 端口 | 8080 / 8081 / 8082 / 8091 / 8095 / 3000 空闲 | — | 冲突就换端口，见第 5 节 |

> 自建靶场在 Linux / macOS 上同样只用标准库，`start-all.sh` 已验证语法与启动逻辑；CI 里跑的是 Ubuntu + Python 3.12。

---

## 2. 装依赖

```bash
# 自建靶场：不需要装任何依赖，有 Python 就行
python --version

# 第三方靶场：只需要 Docker + Compose v2
docker --version
docker compose version
```

---

## 3. 中国大陆网络实测（**未开代理**）

| 域名 | 国内直连结果 | 延迟 | 谁需要它 |
| --- | --- | --- | --- |
| `registry-1.docker.io` / `auth.docker.io` / `hub.docker.com` / `production.cloudflare.docker.com` | ❌ 连接超时 | — | **Docker Hub 官方**（`docker compose up` 默认就走这里） |
| `docker.m.daocloud.io` | ✅ 可用 | 217 ms | Docker Hub 镜像加速（替代上一行） |
| `docker.1ms.run` | ✅ 可用 | 248 ms | 同上 |
| `docker.1panel.live` | ✅ 可用 | 1127 ms | 同上 |
| `registry.cn-hangzhou.aliyuncs.com` | ✅ 可用 | 352 ms | 同上（阿里云，需先在控制台拿加速器地址） |
| `pypi.org` / `files.pythonhosted.org` | ✅ 可用 | 345 / 628 ms | pip |
| `mirrors.aliyun.com` / `pypi.tuna.tsinghua.edu.cn` | ✅ 可用 | 476 / 670 ms | pip 国内镜像 |
| `registry.npmjs.org` | ✅ 可用 | 858 ms | npm |
| `registry.npmmirror.com` | ✅ 可用 | 132 ms | npm 国内镜像 |
| `github.com`（网页 / HTTPS 克隆） | ⚠️ TLS 握手超时 | — | `git clone` 本仓库 |
| `codeload.github.com`（下载 zip） | ❌ 超时 | — | 「Download ZIP」按钮 |
| `raw.githubusercontent.com` | ❌ 连接被重置 | — | raw 文件直链 |
| `api.github.com` | ✅ 可用 | 331 ms | `gh` CLI / API |
| `objects.githubusercontent.com` | ✅ 可用 | 389 ms | Release 附件直链 |
| `ghcr.io` | ✅ 可用 | 548 ms | GitHub 容器镜像 |

> 结论：**本仓库 100% 能在国内直连环境跑起来，只要给 Docker 配一个镜像加速**。
> 唯一要注意的是：如果 `git clone` 卡住不动，那是 `github.com` 握手被干扰，不是本项目的问题（见第 5 节）。

---

## 4. Docker 镜像加速（二选一）

### 做法 A：改 Docker 配置（一次配置，compose 文件不用动）

Docker Desktop → Settings → Docker Engine，把下面这段合进去（Linux 改 `/etc/docker/daemon.json`），然后重启 Docker：

```json
{
  "registry-mirrors": ["https://docker.1ms.run", "https://docker.m.daocloud.io"]
}
```

之后照常 `docker compose up -d` 即可。

### 做法 B：不改配置，用脚本把镜像拉下来并改回原名

```powershell
# Windows：自动读 third-party/*/docker-compose.yml 里的 image
powershell -File scripts/pull-with-mirror.ps1

# 也可以指定镜像、指定镜像站
powershell -File scripts/pull-with-mirror.ps1 vulnerables/web-dvwa raesene/bwapp -Mirror docker.m.daocloud.io
```

```bash
# Linux / macOS
bash scripts/pull-with-mirror.sh
```

脚本会依次尝试 `docker.1ms.run` → `docker.m.daocloud.io` → `docker.1panel.live`，
拉下来后 `docker tag` 成原名（`vulnerables/web-dvwa` 等），这样 compose 文件保持原样就能用。
**做法 B 不需要管理员权限，推荐给不想动 Docker 配置的人。**

---

## 5. 官方安装链接 + 直连不通时用官方 WARP

### 官方安装链接（国内可达性实测）

| 工具 | 官方链接 | 国内直连（实测） |
| --- | --- | --- |
| Python 3.8+（自建靶场用） | <https://www.python.org/downloads/> | ✅ 669 ms |
| Docker Desktop | <https://www.docker.com/products/docker-desktop/> | 未实测（安装包托管在 `objects.githubusercontent.com`，✅ 425 ms） |
| Docker Engine（Linux 发行版） | <https://docs.docker.com/engine/install/> | 未实测 |
| Docker 官方文档（换源/镜像说明） | <https://docs.docker.com/docker-hub/mirror/> | 未单独实测（`docs.docker.com` 与 `www.docker.com` 同属 Cloudflare，通常可达） |
| Vulhub | <https://github.com/vulhub/vulhub> | ⚠️ `github.com` 克隆可能握手超时，见下 |

### 直连不通时：用**官方** WARP（别装第三方"加速器"）

| 官方入口 | 国内直连（实测） | 说明 |
| --- | --- | --- |
| `1.1.1.1`（WARP 官网） | ❌ **超时** | 官网在国内打不开，**这不是你的网络坏了** |
| `www.cloudflare.com` | ✅ 848 ms | 官网主页可开 |
| `developers.cloudflare.com` | ✅ 785 ms | 官方文档站，含各平台安装步骤 |
| `install.appcenter.ms` | ✅ 1583 ms | 官方安装包托管（Microsoft App Center） |

Windows 一条命令装（不需要翻墙）：

```powershell
winget install --id Cloudflare.Warp -e
```

> ⚠️ 别敲 `scoop install warp`：scoop 里那个 `warp` 是另一个不相干的工具（0.3.0）。

装完确认生效：

```powershell
& "C:\Program Files\Cloudflare\Cloudflare WARP\warp-cli.exe" status   # 期望 Connected
```

或打开 <https://www.cloudflare.com/cdn-cgi/trace> 看到 `warp=on`。

**注意三点**：① 本地靶场不受影响（WARP 默认排除内网段，`127.0.0.1` 照常能打）；② 卡在 `Connecting` 就 `warp-cli disconnect` 再 `connect`，约半分钟能上去（我实测遇到过一次）；③ 免费档出口可能绕远（我这边是洛杉矶），只在下载卡住时开。

---

## 6. 常见报错 → 处理

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `docker compose up` 卡住，报 `dial tcp ... i/o timeout` / `TLS handshake timeout` | Docker Hub 直连不通 | 见第 4 节，用镜像加速 |
| `git clone` 卡住或 `TLS handshake timeout` | `github.com` 握手被干扰 | 开代理（WARP 等）、或改用 SSH、或用 Release 附件（`objects.githubusercontent.com` 直连可用） |
| 「Download ZIP」失败 | `codeload.github.com` 不通 | 改用 `git clone`（配合代理），或从代理环境下载后拷过来 |
| `Address already in use` | 端口被占 | 自建靶场：`python app.py 8091`；第三方：改 `docker-compose.yml` 的 `ports` 左侧 |
| 浏览器打不开 `localhost:8095` | `localhost` 被解析成 IPv6 `::1` | 用 `http://127.0.0.1:8095/` |
| Juice Shop 起来后很卡 | 内存不足 | 给它 2 GB 以上，或先只跑 DVWA/bWAPP |

---

## 7. 安全边界（和网络无关，但必须说）

所有靶场**只监听 `127.0.0.1`**，连同一个局域网里的其他设备都访问不到 —— 这是刻意设计，不是配置漏了。
所以：**别人没法访问你机器上的这些靶场**；想让别人用，把仓库发给他、让他在自己机器上跑。
不要为了"让别人能访问"改成 `0.0.0.0`，也不要做端口转发/内网穿透。

---

## English summary

- Self-built labs are pure Python stdlib: **no pip install, work offline**.
- Third-party labs need Docker + Compose v2. **Docker Hub official endpoints are unreachable from mainland China without a proxy** (`registry-1.docker.io`, `auth.docker.io`, `hub.docker.com` all time out). Use a registry mirror — either `"registry-mirrors"` in `daemon.json` (option A) or `scripts/pull-with-mirror.*` (option B, no admin rights needed).
- Also unreachable without a proxy: `codeload.github.com` (Download ZIP) and `raw.githubusercontent.com`. `github.com` HTTPS clone is unstable.
- Reachable without a proxy: PyPI, npm, Docker mirror endpoints, `api.github.com`, `ghcr.io`, `objects.githubusercontent.com`.
- With a proxy enabled, all 25 probed endpoints succeeded.
- All labs bind `127.0.0.1` on purpose: other machines on your LAN cannot reach them.

# DVWA（Damn Vulnerable Web Application）

| 项 | 说明 |
| --- | --- |
| 作用 | 经典 Web 漏洞广度练习（LFI / SQLi / XSS / CSRF / 文件上传 / 命令注入…） |
| 依赖 | Docker |
| 启动 | `docker compose up -d` → http://127.0.0.1:8081 |
| 默认账号 | `admin` / `password`（首次进入点 "Create / Reset Database"） |
| 复位 | `docker compose down -v && docker compose up -d` |

难度可在页面上调（Low / Medium / High / Impossible），建议从 High 开始练"不带提示的挖"。

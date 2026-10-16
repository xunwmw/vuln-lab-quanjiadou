# bWAPP（buggy Web Application）

| 项 | 说明 |
| --- | --- |
| 作用 | 100+ 类 Web 漏洞的广度练习，覆盖 DVWA 没有的类别（XML/XXE、SSRF、LDAP、SSI 等） |
| 依赖 | Docker |
| 启动 | `docker compose up -d` → http://127.0.0.1:8082/install.php 初始化 |
| 默认账号 | `bee` / `bug` |
| 复位 | `docker compose down -v && docker compose up -d` |

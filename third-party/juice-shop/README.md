# OWASP Juice Shop

| 项 | 说明 |
| --- | --- |
| 作用 | 现代 SPA 靶场：业务逻辑、越权、JWT、支付/优惠券、组件版本 CVE |
| 依赖 | Docker |
| 启动 | `docker compose up -d` → http://127.0.0.1:3000 |
| 复位 | `docker compose down -v && docker compose up -d`（或界面里重置进度） |
| 提示 | 自带 Score Board（含提示），**练盲挖时别看它** |

配合"组件 CVE 排查"很好用：它有大量依赖旧版本的组件，可用来练版本识别 → CVE 匹配 → 非破坏性验证。

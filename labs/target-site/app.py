#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
target-site —— 仅使用 Python 3 标准库实现的最小业务站点（安全加固示例 v2）

启动:   python app.py [端口]            # 默认端口 8080
监听:   仅 127.0.0.1（硬编码，拒绝绑定其它地址）
依赖:   仅标准库（http.server / sqlite3 / hashlib / hmac / secrets ...）

功能:
  GET  /                       首页（带导航）
  GET  /register  POST /register   注册（限速 + 密码复杂度）
  GET  /login     POST /login      登录（账号/IP/全局三重防爆破）
  POST /logout                     退出（CSRF 保护）
  POST /change-password            修改密码（成功后吊销该用户全部会话）
  GET  /profile                个人资料（手机号/余额仅本人可见）
  GET  /api/profile?id=<uid>   用户查询（仅可查本人，其余统一 404）
  GET  /api/orders?id=<oid>    订单查询（仅属主，非属主与不存在统一 404）

安全设计均为真实实现，详见 README.md。
"""

import collections
import hashlib
import hmac
import html
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import traceback
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 重定向日志时也用 UTF-8，避免中文启动信息乱码（交互式控制台不受影响）
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
HOST = "127.0.0.1"          # 硬编码回环地址，绝不允许 0.0.0.0
DEFAULT_PORT = 8080

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "target_site.db")
AUDIT_PATH = os.path.join(BASE_DIR, "audit.log")
AUDIT_MAX_BYTES = 1 * 1024 * 1024   # 审计日志超过 1MB 轮转一次

# 反代开关：
# False（默认，本机直连场景）= 完全忽略 X-Forwarded-For，客户端 IP 只认 TCP 对端；
# True（仅当前面是会清洗/重写 XFF 的可信反代时才可开）= 取 XFF 最左地址。
TRUST_PROXY = False

# 密码哈希：PBKDF2-HMAC-SHA256，每用户独立盐
PBKDF2_ALGO = "sha256"
PBKDF2_ITERATIONS = 200_000
PBKDF2_SALT_BYTES = 16

# 会话：服务端存储；2 小时空闲过期 + 12 小时绝对过期；每用户最多 5 个并发会话
SESSION_IDLE_TTL = 2 * 60 * 60
SESSION_ABSOLUTE_TTL = 12 * 60 * 60
MAX_CONCURRENT_SESSIONS = 5
COOKIE_NAME = "session"

# 请求体大小上限（防巨量 body 打爆内存）
MAX_BODY_BYTES = 64 * 1024

# 输入校验
USERNAME_RE = re.compile(r"^[a-z0-9_]{3,20}$")
PHONE_RE = re.compile(r"^[0-9]{6,15}$")
ID_RE = re.compile(r"^[0-9]{1,10}$")
MIN_PASSWORD_LEN = 10
MAX_PASSWORD_LEN = 128

# 登录防爆破：账号维度 / IP 维度 5 次失败锁 15 分钟；全局 30 次失败锁 15 分钟
MAX_LOGIN_FAILURES = 5
GLOBAL_LOGIN_FAILURES = 30
LOCK_SECONDS = 15 * 60

# 滑动窗口限速（内存，进程重启清零；登录锁定另在 DB 持久化）
API_LIMIT, API_WINDOW = 120, 60          # 每 IP / 每会话 每分钟查询接口上限
LOGIN_POST_LIMIT, LOGIN_POST_WINDOW = 20, 300
REGISTER_POST_LIMIT, REGISTER_POST_WINDOW = 10, 300
REGISTER_HOURLY_IP = 5                   # 每 IP 每小时成功/尝试注册上限
REGISTER_HOURLY_GLOBAL = 20
REGISTER_HOURLY_WINDOW = 3600

# 全局锁：sqlite 本地文件写入串行化，简单可靠
DB_LOCK = threading.Lock()
AUDIT_LOCK = threading.Lock()

# 预计算一个“假哈希”，用户名不存在时也跑一次 PBKDF2，抹平计时差异
_DUMMY_SALT = secrets.token_bytes(PBKDF2_SALT_BYTES)
_DUMMY_HASH = hashlib.pbkdf2_hmac(
    PBKDF2_ALGO, b"dummy-password", _DUMMY_SALT, PBKDF2_ITERATIONS
).hex()

# 弱口令黑名单（小写匹配，仅内置常见弱口令，无外部依赖）
COMMON_PASSWORDS = {
    "password", "password1", "password123", "12345678", "123456789",
    "1234567890", "qwerty123", "qwertyuiop", "11111111", "00000000",
    "abc12345", "iloveyou", "admin1234", "welcome1", "passw0rd",
    "qwer1234", "zaq12wsx", "1qaz2wsx", "q1w2e3r4", "abcd1234",
}


# --------------------------------------------------------------------------- #
# 滑动窗口限速器（内存）
# --------------------------------------------------------------------------- #
class SlidingWindowLimiter:
    def __init__(self):
        self._hits = {}
        self._lock = threading.Lock()

    def check(self, key, limit, window):
        """返回 (是否放行, 建议 Retry-After 秒)。"""
        now = time.time()
        with self._lock:
            bucket = self._hits.setdefault(key, collections.deque())
            while bucket and bucket[0] <= now - window:
                bucket.popleft()
            if len(bucket) >= limit:
                return False, max(1, int(window - (now - bucket[0])) + 1)
            bucket.append(now)
            if len(self._hits) > 10_000:
                self._hits = {k: v for k, v in self._hits.items() if v}
            return True, 0


RATE_LIMITER = SlidingWindowLimiter()


# --------------------------------------------------------------------------- #
# 审计日志（JSON Lines，落盘，带大小轮转；任何异常都不得影响请求）
# --------------------------------------------------------------------------- #
def audit(event, **fields):
    try:
        with AUDIT_LOCK:
            if (os.path.exists(AUDIT_PATH)
                    and os.path.getsize(AUDIT_PATH) > AUDIT_MAX_BYTES):
                try:
                    os.replace(AUDIT_PATH, AUDIT_PATH + ".1")
                except OSError:
                    pass
            record = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "epoch": round(time.time(), 3),
                "event": event,
            }
            # 绝不记录密码、token、CSRF
            for banned in ("password", "password2", "old_password",
                           "new_password", "token", "csrf_token"):
                fields.pop(banned, None)
            record.update(fields)
            with open(AUDIT_PATH, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 数据库
# --------------------------------------------------------------------------- #
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    username           TEXT    NOT NULL UNIQUE,
    password_salt      TEXT    NOT NULL,
    password_hash      TEXT    NOT NULL,
    pbkdf2_iterations INTEGER NOT NULL,
    phone              TEXT,
    balance_cents      INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS orders (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    item         TEXT    NOT NULL,
    amount_cents INTEGER NOT NULL,
    created_at   TEXT    NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS sessions (
    token            TEXT PRIMARY KEY,
    user_id          INTEGER,          -- NULL = 仅用于 CSRF 的匿名会话
    csrf_token       TEXT    NOT NULL,
    created_at       REAL    NOT NULL,
    last_seen        REAL    NOT NULL,
    expires_at       REAL    NOT NULL,  -- 空闲过期
    absolute_expires REAL    NOT NULL DEFAULT 0  -- 绝对过期（0=旧数据不限制）
);
CREATE TABLE IF NOT EXISTS login_attempts (
    key          TEXT PRIMARY KEY,     -- 'user:<name>' / 'ip:<addr>' / 'global'
    failures     INTEGER NOT NULL,
    locked_until REAL    NOT NULL
);
"""


def now_str():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _migrate(conn):
    """对旧库做幂等加列迁移。"""
    need = [("sessions", "absolute_expires", "REAL NOT NULL DEFAULT 0")]
    for table, column, decl in need:
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
        if column not in cols:
            # 表名/列名均为代码内常量，不存在注入
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS):
    """返回 (salt_hex, hash_hex)；盐缺省时随机生成。"""
    if salt is None:
        salt = secrets.token_bytes(PBKDF2_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        PBKDF2_ALGO, password.encode("utf-8"), salt, iterations
    )
    return salt.hex(), digest.hex()


def verify_password(password, salt_hex, expected_hash_hex, iterations):
    """常量时间比较，防时序侧信道。"""
    salt = bytes.fromhex(salt_hex)
    _, candidate = hash_password(password, salt, iterations)
    return hmac.compare_digest(candidate, expected_hash_hex)


def dummy_verify(password):
    """用户不存在时消耗与真实验证接近的时间，防用户枚举计时差。"""
    _, candidate = hash_password(password, _DUMMY_SALT)
    hmac.compare_digest(candidate, _DUMMY_HASH)


def password_policy_errors(username, password):
    """返回违规原因列表；空列表表示通过。"""
    errors = []
    if not (MIN_PASSWORD_LEN <= len(password) <= MAX_PASSWORD_LEN):
        errors.append("length")
    classes = sum((
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"[0-9]", password)),
        bool(re.search(r"[^A-Za-z0-9]", password)),
    ))
    if classes < 3:
        errors.append("classes")
    if username and username.lower() in password.lower():
        errors.append("contains_username")
    if password.lower() in COMMON_PASSWORDS:
        errors.append("common")
    return errors


def create_user(conn, username, password, phone=None, balance_cents=0):
    salt_hex, hash_hex = hash_password(password)
    cur = conn.execute(
        "INSERT INTO users(username, password_salt, password_hash,"
        " pbkdf2_iterations, phone, balance_cents, created_at)"
        " VALUES (?,?,?,?,?,?,?)",
        (username, salt_hex, hash_hex, PBKDF2_ITERATIONS,
         phone, balance_cents, now_str()),
    )
    return cur.lastrowid


def init_db():
    """首次启动自动建表并写入两条测试数据。"""
    conn = get_db()
    try:
        with conn:
            conn.executescript(SCHEMA)
            _migrate(conn)
            count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            if count == 0:
                # 测试账号（本地靶场，口令直接写在首页方便测试；种子数据豁免复杂度）
                uid1 = create_user(conn, "demo", "demo1234",
                                   phone="13800000001", balance_cents=10000)
                uid2 = create_user(conn, "alice", "alice1234",
                                   phone="13800000002", balance_cents=250000)
                conn.execute(
                    "INSERT INTO orders(user_id,item,amount_cents,created_at)"
                    " VALUES (?,?,?,?)",
                    (uid1, "机械键盘", 29900, now_str()))
                conn.execute(
                    "INSERT INTO orders(user_id,item,amount_cents,created_at)"
                    " VALUES (?,?,?,?)",
                    (uid1, "USB-C 数据线", 2900, now_str()))
                conn.execute(
                    "INSERT INTO orders(user_id,item,amount_cents,created_at)"
                    " VALUES (?,?,?,?)",
                    (uid2, "无线鼠标", 9900, now_str()))
                print("[init] 已写入测试账号: demo/demo1234, alice/alice1234")
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 会话（服务端存储，数据库为唯一权威，cookie 只持随机 token）
# --------------------------------------------------------------------------- #
def session_create(conn, user_id):
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO sessions(token,user_id,csrf_token,created_at,"
        " last_seen,expires_at,absolute_expires) VALUES (?,?,?,?,?,?,?)",
        (token, user_id, csrf, now, now,
         now + SESSION_IDLE_TTL, now + SESSION_ABSOLUTE_TTL),
    )
    conn.commit()
    _enforce_concurrency(conn, user_id)
    return token, csrf


def _enforce_concurrency(conn, user_id):
    """同一用户保留最新的 MAX_CONCURRENT_SESSIONS 个会话，其余踢下线。"""
    if user_id is None:
        return
    rows = conn.execute(
        "SELECT token FROM sessions WHERE user_id=? ORDER BY created_at ASC",
        (user_id,),
    ).fetchall()
    extra = len(rows) - MAX_CONCURRENT_SESSIONS
    for row in rows[:max(0, extra)]:
        conn.execute("DELETE FROM sessions WHERE token=?", (row["token"],))
    conn.commit()


def session_get(conn, token):
    if not token:
        return None
    row = conn.execute(
        "SELECT * FROM sessions WHERE token=?", (token,)
    ).fetchone()
    if row is None:
        return None
    now = time.time()
    expired = row["expires_at"] < now
    if not expired and row["absolute_expires"] and \
            row["absolute_expires"] < now:
        expired = True
    if expired:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()
        return None
    conn.execute(
        "UPDATE sessions SET last_seen=?, expires_at=? WHERE token=?",
        (now, now + SESSION_IDLE_TTL, token),
    )
    conn.commit()
    return row


def session_destroy(conn, token):
    if token:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))
        conn.commit()


def session_revoke_user(conn, user_id, keep_token=None):
    """吊销某用户全部会话（改密后调用），可保留一个当前 token。"""
    if keep_token:
        conn.execute(
            "DELETE FROM sessions WHERE user_id=? AND token!=?",
            (user_id, keep_token),
        )
    else:
        conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
    conn.commit()


# --------------------------------------------------------------------------- #
# 登录防爆破（账号 / IP / 全局 三重，持久化在 DB）
# --------------------------------------------------------------------------- #
def _bf_row(conn, key):
    return conn.execute(
        "SELECT * FROM login_attempts WHERE key=?", (key,)
    ).fetchone()


def brute_force_is_locked(conn, key, now):
    row = _bf_row(conn, key)
    if row is None:
        return False
    if row["locked_until"] > now:
        return True
    if row["locked_until"] != 0:
        # 之前锁过、且锁定期刚结束：清零，重新计数
        conn.execute(
            "UPDATE login_attempts SET failures=0, locked_until=0 WHERE key=?",
            (key,),
        )
        conn.commit()
    return False


def brute_force_record_fail(conn, key, now, threshold=MAX_LOGIN_FAILURES):
    row = _bf_row(conn, key)
    failures = (row["failures"] if row else 0) + 1
    locked_until = now + LOCK_SECONDS if failures >= threshold else 0
    if row is None:
        conn.execute(
            "INSERT INTO login_attempts(key,failures,locked_until)"
            " VALUES (?,?,?)",
            (key, failures, locked_until),
        )
    else:
        conn.execute(
            "UPDATE login_attempts SET failures=?, locked_until=? WHERE key=?",
            (failures, locked_until, key),
        )
    conn.commit()


def brute_force_reset(conn, key):
    conn.execute("DELETE FROM login_attempts WHERE key=?", (key,))
    conn.commit()


# --------------------------------------------------------------------------- #
# HTML 渲染（所有动态值一律 html.escape，从根上切断 XSS）
# --------------------------------------------------------------------------- #
def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def yuan(cents):
    return f"{cents / 100:.2f}"


CSS = """
*{box-sizing:border-box}
body{margin:0;font-family:"Segoe UI","Microsoft YaHei",sans-serif;
 background:#f5f6f8;color:#222;line-height:1.6}
nav{background:#1f2937;color:#fff;padding:0 24px;display:flex;
 align-items:center;justify-content:space-between;height:56px}
nav .brand{font-weight:700;font-size:18px}
nav a{color:#cbd5e1;text-decoration:none;margin-left:18px}
nav a:hover{color:#fff}
nav form{display:inline;margin-left:18px}
nav button{background:#374151;color:#fff;border:0;border-radius:6px;
 padding:6px 14px;cursor:pointer}
.container{max-width:860px;margin:32px auto;background:#fff;border-radius:10px;
 padding:28px 34px;box-shadow:0 1px 3px rgba(0,0,0,.08)}
h1{margin-top:0}
h2{border-bottom:1px solid #e5e7eb;padding-bottom:6px}
.flash{background:#fef3c7;border:1px solid #f59e0b;color:#92400e;
 padding:10px 14px;border-radius:8px;margin-bottom:18px}
label{display:block;margin:14px 0;font-weight:600}
input{display:block;width:100%;padding:9px 11px;margin-top:6px;
 border:1px solid #cbd5e1;border-radius:6px;font-size:14px}
button.primary{margin-top:18px;background:#2563eb;color:#fff;border:0;
 border-radius:8px;padding:10px 22px;font-size:15px;cursor:pointer}
button.primary:hover{background:#1d4ed8}
table{border-collapse:collapse;width:100%;margin:14px 0}
th,td{border:1px solid #e5e7eb;padding:8px 12px;text-align:left}
th{background:#f9fafb}
code{background:#f1f5f9;padding:2px 6px;border-radius:4px}
.private{color:#b91c1c;font-weight:700}
.muted{color:#6b7280;font-size:13px}
"""

FLASH_MESSAGES = {
    "registered": "注册成功，请登录。",
    "bad": "用户名或密码错误。",
    "locked": "登录失败次数过多，该账号/IP 已被临时锁定 15 分钟。",
    "exists": "用户名已被注册，请更换。",
    "invalid": "输入不合法：密码至少 10 位，且须包含大写、小写、数字、"
               "特殊符号中的至少 3 类，不得包含用户名。",
    "ratelimited": "操作过于频繁，请稍后再试。",
    "logout": "你已退出登录。",
    "needlogin": "请先登录。",
    "pwchanged": "密码已修改，所有其它登录设备已被强制下线。",
    "badoldpw": "原密码错误，修改未生效。",
}


def page_layout(title, body, session=None, username=None, flash=None):
    """拼页面。session 用于渲染导航里的退出表单 CSRF。"""
    if session and session["user_id"] is not None:
        nav_links = (
            '<a href="/">首页</a>'
            f'<a href="/profile">个人中心（{esc(username)}）</a>'
            f'<form method="post" action="/logout" class="inline">'
            f'<input type="hidden" name="csrf_token" '
            f'value="{esc(session["csrf_token"])}">'
            f'<button type="submit">退出登录</button></form>'
        )
    else:
        nav_links = ('<a href="/">首页</a>'
                     '<a href="/login">登录</a>'
                     '<a href="/register">注册</a>')
    flash_html = ""
    if flash and flash in FLASH_MESSAGES:
        flash_html = f'<div class="flash">{esc(FLASH_MESSAGES[flash])}</div>'
    return (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc(title)}</title>\n"
        f"<style>{CSS}</style>\n"
        "</head>\n<body>\n"
        f'<nav><div class="brand">靶场商城 · 安全演示站</div>'
        f'<div class="nav-links">{nav_links}</div></nav>\n'
        f'<div class="container">{flash_html}{body}</div>\n'
        "</body>\n</html>\n"
    )


def csrf_field(session):
    return (
        '<input type="hidden" name="csrf_token" '
        f'value="{esc(session["csrf_token"])}">'
    )


def home_page(session=None, username=None, flash=None):
    body = """
<h1>最小业务站点（安全加固版 v2）</h1>
<p>本站仅用 Python 3 标准库实现，所有安全防护均为真实代码，可直接测试。</p>
<h2>功能与测试入口</h2>
<table>
<tr><th>功能</th><th>入口</th></tr>
<tr><td>注册（限速 + 密码复杂度）</td><td><code>GET/POST /register</code></td></tr>
<tr><td>登录（账号/IP/全局三重锁定）</td><td><code>GET/POST /login</code></td></tr>
<tr><td>修改密码（成功后踢掉全部会话）</td><td><code>POST /change-password</code>（个人中心页）</td></tr>
<tr><td>个人资料（手机号/余额仅本人可见）</td><td><code>GET /profile</code></td></tr>
<tr><td>用户查询 API（只能查自己）</td><td><code>GET /api/profile?id=1</code></td></tr>
<tr><td>订单查询 API（仅属主）</td><td><code>GET /api/orders?id=1</code></td></tr>
</table>
<h2>预置测试账号</h2>
<table>
<tr><th>用户名</th><th>密码</th><th>说明</th></tr>
<tr><td>demo</td><td>demo1234</td><td>有 2 个订单（id=1,2）</td></tr>
<tr><td>alice</td><td>alice1234</td><td>有 1 个订单（id=3）</td></tr>
</table>
<p class="muted">枚举测试：未登录访问任意 <code>/api/profile?id=X</code> 一律 401；
登录 demo 后访问他人资料/订单（无论是否存在）一律 404，响应体完全相同。</p>
"""
    return page_layout("首页", body, session, username, flash)


def login_page(session, flash=None):
    body = f"""
<h1>登录</h1>
<form method="post" action="/login" autocomplete="off">
{csrf_field(session)}
<label>用户名
<input name="username" maxlength="20" required autofocus></label>
<label>密码
<input type="password" name="password" maxlength="{MAX_PASSWORD_LEN}" required></label>
<button class="primary" type="submit">登录</button>
</form>
<p class="muted">还没有账号？<a href="/register">立即注册</a></p>
"""
    return page_layout("登录", body, session, flash=flash)


def register_page(session, flash=None):
    body = f"""
<h1>注册</h1>
<form method="post" action="/register" autocomplete="off">
{csrf_field(session)}
<label>用户名（3-20 位小写字母、数字或下划线）
<input name="username" maxlength="20" pattern="[a-z0-9_]{{3,20}}" required></label>
<label>密码（{MIN_PASSWORD_LEN}-{MAX_PASSWORD_LEN} 位；大写字母、小写字母、数字、
特殊符号至少包含 3 类；不得包含用户名；不得为常见弱口令）
<input type="password" name="password" maxlength="{MAX_PASSWORD_LEN}" required></label>
<label>确认密码
<input type="password" name="password2" maxlength="{MAX_PASSWORD_LEN}" required></label>
<label>手机号（可选，仅本人可见）
<input name="phone" maxlength="15" inputmode="numeric"></label>
<button class="primary" type="submit">注册</button>
</form>
<p class="muted">注册接口限速：同一 IP 每小时最多 {REGISTER_HOURLY_IP} 次。</p>
"""
    return page_layout("注册", body, session, flash=flash)


def profile_page(user, orders, session, flash=None):
    rows = "".join(
        f"<tr><td>{o['id']}</td><td>{esc(o['item'])}</td>"
        f"<td>¥{yuan(o['amount_cents'])}</td>"
        f"<td>{esc(o['created_at'])}</td></tr>"
        for o in orders
    )
    if not rows:
        rows = '<tr><td colspan="4" class="muted">暂无订单</td></tr>'
    body = f"""
<h1>个人中心</h1>
<table>
<tr><th>用户 ID</th><td>{user['id']}</td></tr>
<tr><th>用户名</th><td>{esc(user['username'])}</td></tr>
<tr><th>注册时间</th><td>{esc(user['created_at'])}</td></tr>
<tr><th>手机号 <span class="private">[仅本人可见]</span></th>
    <td class="private">{esc(user['phone']) or '未填写'}</td></tr>
<tr><th>账户余额 <span class="private">[仅本人可见]</span></th>
    <td class="private">¥{yuan(user['balance_cents'])}</td></tr>
</table>
<h2>我的订单</h2>
<table>
<tr><th>订单 ID</th><th>商品</th><th>金额</th><th>下单时间</th></tr>
{rows}
</table>
<h2>修改密码</h2>
<p class="muted">修改成功后，该账号在所有设备上的登录会话都会被立即吊销。</p>
<form method="post" action="/change-password" autocomplete="off">
{csrf_field(session)}
<label>原密码
<input type="password" name="old_password" maxlength="{MAX_PASSWORD_LEN}" required></label>
<label>新密码（{MIN_PASSWORD_LEN} 位以上，大写/小写/数字/特殊符号至少 3 类，
不得包含用户名）
<input type="password" name="new_password" maxlength="{MAX_PASSWORD_LEN}" required></label>
<label>确认新密码
<input type="password" name="new_password2" maxlength="{MAX_PASSWORD_LEN}" required></label>
<button class="primary" type="submit">修改密码</button>
</form>
"""
    return page_layout("个人中心", body, session,
                       username=user["username"], flash=flash)


def error_page(code, message, session=None, username=None):
    body = f"<h1>{code} 错误</h1><p>{esc(message)}</p>"
    return page_layout(f"{code}", body, session, username)


# --------------------------------------------------------------------------- #
# HTTP 处理器
# --------------------------------------------------------------------------- #
SECURITY_HEADERS = [
    ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Referrer-Policy", "no-referrer"),
    # 无任何 JavaScript / 外部资源；仅允许内联样式
    ("Content-Security-Policy",
     "default-src 'none'; style-src 'unsafe-inline'; "
     "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"),
    ("Cache-Control", "no-store"),
    ("Permissions-Policy", "geolocation=(), camera=(), microphone=()"),
]


class Handler(BaseHTTPRequestHandler):
    server_version = "TargetSite/1.0"

    def version_string(self):
        return "TargetSite/1.0"  # 不暴露 Python 版本

    # -- 审计 / 客户端 IP -------------------------------------------------- #
    def audit(self, event, **fields):
        fields.setdefault("ip", self.client_ip())
        fields.setdefault("method", self.command)
        fields.setdefault("path", urllib.parse.urlparse(self.path).path)
        audit(event, **fields)

    def client_ip(self):
        """TCP 对端为权威来源；默认忽略可伪造的 X-Forwarded-For。"""
        peer = self.client_address[0]
        if not TRUST_PROXY:
            return peer
        xff = self.headers.get("X-Forwarded-For", "")
        if xff:
            candidate = xff.split(",")[0].strip()
            try:
                ipaddress.ip_address(candidate)
                return candidate
            except ValueError:
                pass
        return peer

    # -- 响应工具 ---------------------------------------------------------- #
    def _cookie_headers(self):
        if getattr(self, "_set_cookie", None):
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}={self._set_cookie}; HttpOnly; Path=/; "
                f"SameSite=Lax; Max-Age={SESSION_IDLE_TTL}",
            )
        if getattr(self, "_clear_cookie", False):
            self.send_header(
                "Set-Cookie",
                f"{COOKIE_NAME}=; HttpOnly; Path=/; SameSite=Lax; Max-Age=0",
            )

    def _write(self, code, content_type, body, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in SECURITY_HEADERS:
            self.send_header(k, v)
        self._cookie_headers()
        for k, v in (extra_headers or []):
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def html(self, code, html_str):
        self._write(code, "text/html; charset=utf-8",
                    html_str.encode("utf-8"))

    def json(self, code, obj, extra_headers=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._write(code, "application/json; charset=utf-8", body,
                    extra_headers=extra_headers)

    def redirect(self, location):
        self._write(303, "text/html; charset=utf-8", b"",
                    extra_headers=[("Location", location)])

    # -- 会话/请求工具 ----------------------------------------------------- #
    def load_session(self, conn, create_if_missing=False):
        """从 cookie 解析会话；create_if_missing 时为表单页签发匿名会话。"""
        token = None
        try:
            cookie = SimpleCookie(self.headers.get("Cookie", ""))
            if COOKIE_NAME in cookie:
                token = cookie[COOKIE_NAME].value
        except Exception:
            token = None
        row = session_get(conn, token) if token else None
        if row is None and create_if_missing:
            token, _ = session_create(conn, None)
            row = session_get(conn, token)
            self._set_cookie = token
        return row

    def read_form(self):
        """读取并解析表单，返回 (dict, error_code)。"""
        ctype = self.headers.get("Content-Type", "").split(";")[0].strip()
        if ctype != "application/x-www-form-urlencoded":
            return None, 415
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return None, 400
        if length < 0 or length > MAX_BODY_BYTES:
            return None, 413
        raw = self.rfile.read(length)
        try:
            parsed = urllib.parse.parse_qs(
                raw.decode("utf-8"), keep_blank_values=True,
                strict_parsing=False,
            )
        except UnicodeDecodeError:
            return None, 400
        return {k: v[0] for k, v in parsed.items()}, None

    def check_csrf(self, session, form):
        if session is None:
            return False
        return hmac.compare_digest(
            str(session["csrf_token"]), str(form.get("csrf_token", ""))
        )

    def check_origin(self):
        """同源校验（CSRF 纵深防御；Origin/Referer 都缺失时交给 CSRF token）。"""
        target = self.headers.get("Origin") or self.headers.get("Referer")
        if not target:
            return True
        parsed = urllib.parse.urlparse(target)
        if parsed.scheme not in ("http", "https"):
            return False
        if parsed.hostname not in ("127.0.0.1", "localhost"):
            return False
        try:
            server_port = self.server.server_address[1]
            if parsed.port and parsed.port != server_port:
                return False
        except Exception:
            return False
        return True

    def query_params(self):
        parsed = urllib.parse.urlparse(self.path)
        return parsed.path, urllib.parse.parse_qs(parsed.query)

    @staticmethod
    def parse_id(params, key):
        values = params.get(key, [])
        if len(values) != 1 or not ID_RE.fullmatch(values[0]):
            return None
        value = int(values[0])
        return value if 1 <= value <= 2_147_483_647 else None

    def rate_gate(self, key, limit, window, event="rate_limited",
                  json_mode=False):
        """返回 True 表示被限流（调用方应直接回 429）。"""
        ok, retry_after = RATE_LIMITER.check(key, limit, window)
        if not ok:
            self.audit(event, limit=limit, window=window,
                       bucket=key.split(":", 1)[0])
            if json_mode:
                self.json(429, {"error": "请求过于频繁，请稍后再试"},
                          extra_headers=[("Retry-After", str(retry_after))])
            else:
                self.redirect("/?m=ratelimited")
            return True
        return False

    # -- 路由入口 ---------------------------------------------------------- #
    def do_GET(self):
        try:
            self.route_get()
        except Exception:
            traceback.print_exc()
            self._safe_500()

    def do_HEAD(self):
        try:
            self.route_get()
        except Exception:
            self._safe_500()

    def do_POST(self):
        try:
            self.route_post()
        except Exception:
            traceback.print_exc()
            self._safe_500()

    def _method_not_allowed(self):
        try:
            self.html(405, error_page(405, "不支持的请求方法。"))
        except Exception:
            pass

    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_OPTIONS = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_TRACE = _method_not_allowed

    def _safe_500(self):
        try:
            self.html(500, error_page(500, "服务器内部错误。"))
        except Exception:
            pass

    def route_get(self):
        path, params = self.query_params()
        flash = params.get("m", [None])[0]

        if path == "/favicon.ico":
            self._write(204, "image/x-icon", b"")
            return

        if path == "/":
            with DB_LOCK:
                conn = get_db()
                try:
                    sess = self.load_session(conn)
                    name = self._username_of(conn, sess)
                    self.html(200, home_page(sess, name, flash))
                finally:
                    conn.close()
            return

        if path in ("/login", "/register"):
            with DB_LOCK:
                conn = get_db()
                try:
                    sess = self.load_session(conn, create_if_missing=True)
                    if sess and sess["user_id"] is not None:
                        self.redirect("/profile")
                        return
                    if path == "/login":
                        self.html(200, login_page(sess, flash))
                    else:
                        self.html(200, register_page(sess, flash))
                finally:
                    conn.close()
            return

        if path == "/profile":
            with DB_LOCK:
                conn = get_db()
                try:
                    sess = self.load_session(conn)
                    if sess is None or sess["user_id"] is None:
                        self.redirect("/login?m=needlogin")
                        return
                    user = conn.execute(
                        "SELECT * FROM users WHERE id=?",
                        (sess["user_id"],)).fetchone()
                    if user is None:  # 会话指向已删除用户
                        self._clear_cookie = True
                        self.redirect("/login?m=needlogin")
                        return
                    orders = conn.execute(
                        "SELECT * FROM orders WHERE user_id=? ORDER BY id",
                        (user["id"],)).fetchall()
                    self.html(200, profile_page(user, orders, sess, flash))
                finally:
                    conn.close()
            return

        if path == "/api/profile":
            self.api_profile(params)
            return

        if path == "/api/orders":
            self.api_orders(params)
            return

        with DB_LOCK:
            conn = get_db()
            try:
                sess = self.load_session(conn)
                name = self._username_of(conn, sess)
                self.html(404, error_page(404, "页面不存在。", sess, name))
            finally:
                conn.close()

    @staticmethod
    def _username_of(conn, sess):
        if sess and sess["user_id"] is not None:
            row = conn.execute(
                "SELECT username FROM users WHERE id=?",
                (sess["user_id"],)).fetchone()
            return row["username"] if row else None
        return None

    def route_post(self):
        path, _ = self.query_params()

        if path == "/register":
            self.handle_register()
            return
        if path == "/login":
            self.handle_login()
            return
        if path == "/logout":
            self.handle_logout()
            return
        if path == "/change-password":
            self.handle_change_password()
            return

        self.html(404, error_page(404, "接口不存在。"))

    # -- 注册 -------------------------------------------------------------- #
    def handle_register(self):
        form, err = self.read_form()
        if err is not None:
            self.html(err, error_page(err, "请求格式不被接受。"))
            return
        if not self.check_origin():
            self.audit("origin_rejected")
            self.html(403, error_page(403, "跨站请求被拒绝。"))
            return

        ip = self.client_ip()
        # 内存滑动窗口：突发限速 + 每小时配额（IP 维度 + 全局维度）
        if self.rate_gate(f"reg:post:ip:{ip}",
                          REGISTER_POST_LIMIT, REGISTER_POST_WINDOW):
            return
        if self.rate_gate(f"reg:hour:ip:{ip}",
                          REGISTER_HOURLY_IP, REGISTER_HOURLY_WINDOW,
                          event="register_rate_limited"):
            return
        if self.rate_gate("reg:hour:global",
                          REGISTER_HOURLY_GLOBAL, REGISTER_HOURLY_WINDOW,
                          event="register_rate_limited"):
            return

        with DB_LOCK:
            conn = get_db()
            try:
                sess = self.load_session(conn, create_if_missing=True)
                if not self.check_csrf(sess, form):
                    self.audit("csrf_failure", form_action="register")
                    self.html(403, error_page(
                        403, "CSRF 校验失败，请回到表单页重新提交。"))
                    return

                username = form.get("username", "").strip().lower()
                password = form.get("password", "")
                password2 = form.get("password2", "")
                phone = form.get("phone", "").strip()

                if (not USERNAME_RE.fullmatch(username)
                        or password != password2
                        or password_policy_errors(username, password)
                        or (phone and not PHONE_RE.fullmatch(phone))):
                    self.audit("register_invalid", username=username)
                    self.redirect("/register?m=invalid")
                    return

                exists = conn.execute(
                    "SELECT 1 FROM users WHERE username=?",
                    (username,)).fetchone()
                if exists:
                    self.audit("register_duplicate", username=username)
                    self.redirect("/register?m=exists")
                    return
                try:
                    uid = create_user(conn, username, password,
                                      phone=phone or None)
                    conn.commit()
                except sqlite3.IntegrityError:
                    self.audit("register_duplicate", username=username)
                    self.redirect("/register?m=exists")
                    return
                self.audit("register_success", username=username, user_id=uid)
                self.redirect("/login?m=registered")
            finally:
                conn.close()

    # -- 登录 -------------------------------------------------------------- #
    def handle_login(self):
        form, err = self.read_form()
        if err is not None:
            self.html(err, error_page(err, "请求格式不被接受。"))
            return
        if not self.check_origin():
            self.audit("origin_rejected")
            self.html(403, error_page(403, "跨站请求被拒绝。"))
            return

        ip = self.client_ip()
        if self.rate_gate(f"login:post:ip:{ip}",
                          LOGIN_POST_LIMIT, LOGIN_POST_WINDOW):
            return

        with DB_LOCK:
            conn = get_db()
            try:
                sess = self.load_session(conn, create_if_missing=True)
                if not self.check_csrf(sess, form):
                    self.audit("csrf_failure", form_action="login")
                    self.html(403, error_page(
                        403, "CSRF 校验失败，请回到登录页重新提交。"))
                    return

                username = form.get("username", "").strip().lower()[:20]
                password = form.get("password", "")[:MAX_PASSWORD_LEN]
                now = time.time()
                ukey, ikey, gkey = f"user:{username}", f"ip:{ip}", "global"

                # 三重锁定：账号 / IP / 全局，任一命中即拒绝
                if (brute_force_is_locked(conn, ukey, now)
                        or brute_force_is_locked(conn, ikey, now)
                        or brute_force_is_locked(conn, gkey, now)):
                    self.audit("login_locked", username=username)
                    self.redirect("/login?m=locked")
                    return

                user = conn.execute(
                    "SELECT * FROM users WHERE username=?",
                    (username,)).fetchone()

                if user is not None:
                    ok = verify_password(
                        password, user["password_salt"],
                        user["password_hash"], user["pbkdf2_iterations"])
                else:
                    dummy_verify(password)  # 抹平“用户是否存在”的计时差
                    ok = False

                if not ok:
                    brute_force_record_fail(conn, ukey, now)
                    brute_force_record_fail(conn, ikey, now)
                    brute_force_record_fail(
                        conn, gkey, now, threshold=GLOBAL_LOGIN_FAILURES)
                    self.audit("login_failure", username=username)
                    self.redirect("/login?m=bad")
                    return

                # 成功：清失败计数；销毁旧会话（含匿名会话），签发新会话
                # —— 防会话固定（session fixation）
                brute_force_reset(conn, ukey)
                brute_force_reset(conn, ikey)
                brute_force_reset(conn, gkey)
                session_destroy(conn, sess["token"])
                new_token, _ = session_create(conn, user["id"])
                self._set_cookie = new_token
                self.audit("login_success", username=user["username"],
                           user_id=user["id"])
                self.redirect("/profile")
            finally:
                conn.close()

    def handle_logout(self):
        form, err = self.read_form()
        if err is not None:
            self.html(err, error_page(err, "请求格式不被接受。"))
            return
        with DB_LOCK:
            conn = get_db()
            try:
                sess = self.load_session(conn)
                # 退出同样要求 CSRF，防止第三方页面伪造退出
                if sess is not None and self.check_csrf(sess, form):
                    uid = sess["user_id"]
                    session_destroy(conn, sess["token"])
                    self.audit("logout", user_id=uid)
                self._clear_cookie = True
                self.redirect("/?m=logout")
            finally:
                conn.close()

    # -- 修改密码 ---------------------------------------------------------- #
    def handle_change_password(self):
        form, err = self.read_form()
        if err is not None:
            self.html(err, error_page(err, "请求格式不被接受。"))
            return
        if not self.check_origin():
            self.audit("origin_rejected")
            self.html(403, error_page(403, "跨站请求被拒绝。"))
            return
        with DB_LOCK:
            conn = get_db()
            try:
                sess = self.load_session(conn)
                if sess is None or sess["user_id"] is None:
                    self.redirect("/login?m=needlogin")
                    return
                if not self.check_csrf(sess, form):
                    self.audit("csrf_failure", form_action="change_password")
                    self.html(403, error_page(
                        403, "CSRF 校验失败，请回到页面重新提交。"))
                    return

                user = conn.execute(
                    "SELECT * FROM users WHERE id=?",
                    (sess["user_id"],)).fetchone()
                old_pw = form.get("old_password", "")[:MAX_PASSWORD_LEN]
                new_pw = form.get("new_password", "")[:MAX_PASSWORD_LEN]
                new_pw2 = form.get("new_password2", "")[:MAX_PASSWORD_LEN]

                if user is None or not verify_password(
                        old_pw, user["password_salt"], user["password_hash"],
                        user["pbkdf2_iterations"]):
                    self.audit("password_change_failed",
                               user_id=sess["user_id"], reason="bad_old")
                    self.redirect("/profile?m=badoldpw")
                    return

                if (new_pw != new_pw2
                        or new_pw == old_pw
                        or password_policy_errors(user["username"], new_pw)):
                    self.audit("password_change_failed",
                               user_id=user["id"], reason="weak_or_mismatch")
                    self.redirect("/profile?m=invalid")
                    return

                salt_hex, hash_hex = hash_password(new_pw)
                conn.execute(
                    "UPDATE users SET password_salt=?, password_hash=?,"
                    " pbkdf2_iterations=? WHERE id=?",
                    (salt_hex, hash_hex, PBKDF2_ITERATIONS, user["id"]))
                conn.commit()

                # 改密成功：吊销该用户全部会话（含当前会话），再签发全新会话
                session_revoke_user(conn, user["id"])
                new_token, _ = session_create(conn, user["id"])
                self._set_cookie = new_token
                self.audit("password_changed", user_id=user["id"],
                           username=user["username"])
                self.redirect("/profile?m=pwchanged")
            finally:
                conn.close()

    # -- JSON API（统一响应，杜绝枚举） ------------------------------------ #
    def _api_rate_gate(self, sess):
        ip = self.client_ip()
        if self.rate_gate(f"api:ip:{ip}", API_LIMIT, API_WINDOW,
                          json_mode=True):
            return True
        if sess is not None and sess["user_id"] is not None:
            if self.rate_gate(f"api:user:{sess['user_id']}",
                              API_LIMIT, API_WINDOW, json_mode=True):
                return True
        return False

    def api_profile(self, params):
        target_id = self.parse_id(params, "id")
        if target_id is None:
            self.json(400, {"error": "参数 id 必须是正整数"})
            return
        with DB_LOCK:
            conn = get_db()
            try:
                sess = self.load_session(conn)
                if self._api_rate_gate(sess):
                    return
                # 未认证：对任何 id（存在与否）统一 401，杜绝枚举
                if sess is None or sess["user_id"] is None:
                    self.json(401, {"error": "未认证，请先登录"})
                    return
                user = conn.execute(
                    "SELECT id,username,phone,balance_cents,created_at"
                    " FROM users WHERE id=?", (target_id,)).fetchone()
                # 非本人（无论目标是否存在）统一 404、统一响应体
                if user is None or user["id"] != sess["user_id"]:
                    if user is not None:
                        self.audit("api_forbidden", resource="profile",
                                   target_id=target_id)
                    self.json(404, {"error": "资源不存在"})
                    return
                self.json(200, {
                    "id": user["id"],
                    "username": user["username"],
                    "created_at": user["created_at"],
                    "phone": user["phone"],
                    "balance_cents": user["balance_cents"],
                    "balance_yuan": yuan(user["balance_cents"]),
                })
            finally:
                conn.close()

    def api_orders(self, params):
        target_id = self.parse_id(params, "id")
        if target_id is None:
            self.json(400, {"error": "参数 id 必须是正整数"})
            return
        with DB_LOCK:
            conn = get_db()
            try:
                sess = self.load_session(conn)
                if self._api_rate_gate(sess):
                    return
                # 未认证：对任何 id 统一 401
                if sess is None or sess["user_id"] is None:
                    self.json(401, {"error": "未认证，请先登录"})
                    return
                order = conn.execute(
                    "SELECT * FROM orders WHERE id=?",
                    (target_id,)).fetchone()
                # 非属主与不存在：统一 404、统一响应体，不泄露订单是否存在
                if order is None or order["user_id"] != sess["user_id"]:
                    if order is not None:
                        self.audit("api_forbidden", resource="order",
                                   target_id=target_id)
                    self.json(404, {"error": "资源不存在"})
                    return
                self.json(200, {
                    "id": order["id"],
                    "user_id": order["user_id"],
                    "item": order["item"],
                    "amount_cents": order["amount_cents"],
                    "amount_yuan": yuan(order["amount_cents"]),
                    "created_at": order["created_at"],
                })
            finally:
                conn.close()


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main():
    port = DEFAULT_PORT
    if len(sys.argv) > 1:
        try:
            port = int(sys.argv[1])
            if not (1 <= port <= 65535):
                raise ValueError
        except ValueError:
            print(f"用法: python {os.path.basename(__file__)} [端口 1-65535]")
            sys.exit(2)

    # 最后一道保险：只允许回环
    if HOST != "127.0.0.1":
        raise SystemExit("安全约束：仅允许监听 127.0.0.1")

    init_db()
    try:
        server = ThreadingHTTPServer((HOST, port), Handler)
    except OSError as exc:
        print(f"[!] 无法绑定 {HOST}:{port}（端口可能被占用或无权限）: {exc}")
        print(f"[!] 可换端口启动: python {os.path.basename(__file__)} 8092")
        sys.exit(1)
    server.daemon_threads = True
    print(f"[+] 服务已启动: http://{HOST}:{port}")
    print(f"[+] 数据库: {DB_PATH}")
    print(f"[+] 审计日志: {AUDIT_PATH}")
    print(f"[+] X-Forwarded-For 信任: {TRUST_PROXY}（False=只认 TCP 对端 IP）")
    print("[+] 按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[-] 正在停止...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

"""Приём заявок с сайта design-processov.ru и доставка в Telegram.

Запуск: python3 /opt/leads-api/leads_api.py
Слушает 127.0.0.1:8099, наружу — через Caddy (api.design-processov.ru).
Данные никуда не уходят, кроме Telegram: заявка сразу пишется в тему «Задачи».
"""
import json
import os
import re
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ENV_FILE = Path(os.getenv("ENV_FILE", "/etc/leads-api.env"))
LOG_DIR = Path(os.getenv("LOG_DIR", "/home/agent/leads-api/logs"))
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG = LOG_DIR / "leads.log"

ALLOWED_ORIGINS = {
    "https://design-processov.ru",
    "https://www.design-processov.ru",
}
MAX_BYTES = 64 * 1024
MAX_REQUESTS_PER_HOUR = 20
_hits = defaultdict(list)


def cfg():
    """Читает токен и адрес доставки из защищённого файла."""
    data = {}
    try:
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                data[k.strip()] = v.strip()
    except OSError:
        pass
    return data


def log(text: str):
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")


def esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def rate_ok(ip: str) -> bool:
    now = time.time()
    window = [t for t in _hits[ip] if now - t < 3600]
    _hits[ip] = window
    if len(window) >= MAX_REQUESTS_PER_HOUR:
        return False
    window.append(now)
    return True


def send_to_telegram(payload: dict) -> tuple[bool, str]:
    c = cfg()
    token = c.get("BOT_TOKEN", "")
    chat = c.get("CHAT_ID", "")
    thread = c.get("THREAD_ID", "")
    if not token or not chat:
        return False, "не настроены BOT_TOKEN/CHAT_ID"

    name = str(payload.get("name", ""))[:120]
    email = str(payload.get("email", ""))[:160]
    phone = str(payload.get("phone", ""))[:60]
    consent = "да" if payload.get("consent") else "нет"
    answers = payload.get("answers") or {}
    recs = payload.get("recs") or []
    when = time.strftime("%d.%m.%Y %H:%M")

    lines = [
        "<b>Заявка с сайта</b>",
        f"<b>Имя:</b> {esc(name)}",
        f"<b>Почта:</b> {esc(email)}",
    ]
    if phone:
        lines.append(f"<b>Телефон:</b> {esc(phone)}")
    lines.append(f"<b>Согласие:</b> {consent} · {when}")

    if isinstance(answers, dict) and answers:
        pairs = [f"{esc(str(k))}: {esc(str(v))}" for k, v in list(answers.items())[:12]]
        lines.append("\n<b>Ответы диагностики</b>\n" + "\n".join("• " + p for p in pairs))

    if isinstance(recs, list) and recs:
        top = [esc(str(r)) for r in recs[:5]]
        lines.append("\n<b>Рекомендации</b>\n" + "\n".join("• " + t for t in top))

    text = "\n".join(lines)[:3800]
    data = {
        "chat_id": chat,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }
    if thread.isdigit():
        data["message_thread_id"] = thread

    root = c.get("TG_API_ROOT", "https://api.telegram.org").rstrip("/")

    def deliver(params: dict) -> tuple[bool, str]:
        body = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(f"{root}/bot{token}/sendMessage", data=body)
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                res = json.loads(r.read())
            if res.get("ok"):
                mid = (res.get("result") or {}).get("message_id")
                return True, f"отправлено, сообщение {mid}"
            return False, "telegram ответил не ok"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {str(e)[:120]}"

    ok, info = deliver(data)
    if ok:
        return True, f"в тему «Задачи» ({info})"

    # резерв: бот может быть не добавлен в группу — тогда шлём владельцу в личку
    owner = c.get("OWNER_ID", "").strip()
    if owner:
        fallback = {"chat_id": owner, "text": "⚠️ Заявка не ушла в тему — бот не в группе.\n\n" + text[:3500],
                    "parse_mode": "HTML", "disable_web_page_preview": "true"}
        ok2, info2 = deliver(fallback)
        log(f"резервная доставка владельцу: {info2}")
        if ok2:
            return True, "в личку (бот не в группе)"
    return False, info


class Handler(BaseHTTPRequestHandler):
    server_version = "leads/1.0"

    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.rstrip("/") == "/health":
            self._json(200, {"ok": True})
        else:
            self._json(404, {"ok": False})

    def do_POST(self):
        if self.path.rstrip("/") != "/lead":
            self._json(404, {"ok": False, "error": "not found"})
            return

        ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        if not rate_ok(ip):
            self._json(429, {"ok": False, "error": "слишком много заявок, попробуйте позже"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_BYTES:
            self._json(413, {"ok": False, "error": "некорректный размер"})
            return

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8", "ignore"))
        except Exception:  # noqa: BLE001
            self._json(400, {"ok": False, "error": "не JSON"})
            return

        email = str(payload.get("email", "")).strip()
        name = str(payload.get("name", "")).strip()
        if len(name) < 2 or not re.match(r"^[^\s@]+@[^\s@]+\.[a-zA-Zа-яА-Я]{2,}$", email):
            self._json(400, {"ok": False, "error": "проверьте имя и почту"})
            return
        if not payload.get("consent"):
            self._json(400, {"ok": False, "error": "нужно согласие на обработку данных"})
            return

        # в журнал — только факт, без персональных данных
        log(f"заявка с {ip}: имя {len(name)} симв., почта домен {email.split('@')[-1]}")
        ok, info = send_to_telegram(payload)
        log(f"доставка: {'ок' if ok else 'ошибка'} — {info}")
        self._json(200 if ok else 502, {"ok": ok, "info": info})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    port = int(cfg().get("PORT", "8099"))
    # адрес прослушивания: на сервере (systemd) — только localhost, в контейнере — все интерфейсы,
    # иначе docker не сможет достучаться до приложения через проброс порта
    host = os.getenv("HOST", "127.0.0.1")
    log(f"старт на {host}:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()

import os
import time
import logging
import threading

import requests
import telebot
from telebot import types
from flask import Flask, request

try:
    import psycopg2
except ImportError:
    psycopg2 = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("citabot")

# ---------------- CONFIG ----------------

TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
BOT_URL = (os.environ.get("BOT_URL") or "").strip().rstrip("/")
ADMIN_ID = (os.environ.get("ADMIN_ID") or "").strip()
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

SERVICES = ["NIE", "TIE", "Huellas", "Padrón"]

CITIES = [
    "Barcelona",
    "Madrid",
    "Badalona",
    "L'Hospitalet",
    "Terrassa",
    "Sabadell",
    "Mataró",
    "Santa Coloma",
    "Valencia",
    "Sevilla",
]

STATE = {}
USE_DB = False
MEMORY = []
MEMORY_NEXT_ID = 1


# ---------------- ACCESS ----------------

def allowed_chat(chat_id):
    return (not ADMIN_ID) or str(chat_id) == ADMIN_ID


def allowed(message):
    return allowed_chat(message.chat.id)


def admin_chat_id(message):
    if ADMIN_ID:
        return int(ADMIN_ID)
    return message.chat.id


# ---------------- DATABASE ----------------

def db_conn():
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


def init_storage():
    global USE_DB

    if not DATABASE_URL or psycopg2 is None:
        log.warning("Database not configured -> MEMORY mode")
        return

    try:
        conn = db_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS clients (
                        id SERIAL PRIMARY KEY,
                        service TEXT NOT NULL,
                        city TEXT NOT NULL,
                        details TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT now()
                    )
                """)
        finally:
            conn.close()

        USE_DB = True
        log.info("Database connected -> DATABASE mode")

    except Exception:
        log.exception("Database connection failed")
        USE_DB = False


def save_record(service, city, details):
    global MEMORY_NEXT_ID

    if USE_DB:
        conn = db_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clients (service, city, details) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (service, city, details),
                )
                return cur.fetchone()[0]
        finally:
            conn.close()

    rec = {
        "id": MEMORY_NEXT_ID,
        "service": service,
        "city": city,
        "details": details,
    }
    MEMORY_NEXT_ID += 1
    MEMORY.append(rec)
    return rec["id"]


def list_records():
    if USE_DB:
        conn = db_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT id, service, city, details "
                    "FROM clients ORDER BY id"
                )
                return [
                    {
                        "id": r[0],
                        "service": r[1],
                        "city": r[2],
                        "details": r[3],
                    }
                    for r in cur.fetchall()
                ]
        finally:
            conn.close()

    return list(MEMORY)


def delete_record(rec_id):
    if USE_DB:
        conn = db_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM clients WHERE id = %s",
                    (rec_id,),
                )
                return cur.rowcount > 0
        finally:
            conn.close()

    for i, rec in enumerate(MEMORY):
        if rec["id"] == rec_id:
            del MEMORY[i]
            return True

    return False


# ---------------- HELPERS ----------------

def send_long(chat_id, text):
    while text:
        chunk, text = text[:3800], text[3800:]
        bot.send_message(chat_id, chunk)


def service_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(*[
        types.InlineKeyboardButton(
            s, callback_data=f"svc:{i}"
        )
        for i, s in enumerate(SERVICES)
    ])
    return kb


def city_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(*[
        types.InlineKeyboardButton(
            c, callback_data=f"city:{i}"
        )
        for i, c in enumerate(CITIES)
    ])
    kb.add(
        types.InlineKeyboardButton(
            "✏️ Dusra shehar (likhein)",
            callback_data="city:other",
        )
    )
    return kb


# ---------------- WEBHOOK ----------------

def configure_webhook():
    if not BOT_URL:
        log.warning("BOT_URL missing")
        return False

    try:
        bot.remove_webhook()
        ok = bot.set_webhook(url=f"{BOT_URL}/{TOKEN}")
        log.info("Webhook configured: %s", ok)
        return ok
    except Exception:
        log.exception("Webhook setup failed")
        return False


@app.route("/" + TOKEN, methods=["POST"])
def receive_update():
    raw = request.get_data(as_text=True)

    try:
        update = telebot.types.Update.de_json(raw)
        bot.process_new_updates([update])
    except Exception:
        log.exception("Webhook update error")

    return "OK", 200


@app.route("/")
def index():
    return "System Active Aur Ready Hai!", 200


@app.route("/setwebhook")
def set_webhook_route():
    return f"Webhook set: {configure_webhook()}", 200


@app.route("/webhookinfo")
def webhook_info_route():
    try:
        info = bot.get_webhook_info()
        url = (info.url or "").replace(TOKEN, "<TOKEN>")

        return (
            f"url: {url}\n"
            f"pending_update_count: {info.pending_update_count}\n"
            f"last_error_date: {info.last_error_date}\n"
            f"last_error_message: {info.last_error_message}\n"
            f"storage: {'DATABASE' if USE_DB else 'MEMORY'}\n"
        ), 200, {
            "Content-Type": "text/plain; charset=utf-8"
        }

    except Exception as e:
        log.exception("Webhook info error")
        return f"Error: {e}", 500


# ---------------- COMMANDS ----------------

@bot.message_handler(commands=["myid"])
def my_id(message):
    bot.reply_to(
        message,
        f"Aapki chat ID: {message.chat.id}"
    )


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")

    bot.reply_to(
        message,
        "🇪🇸 Cita Fácil España Bot Active Hai!\n\n"
        "/addclient - Naya client\n"
        "/viewclients - Saved clients\n"
        "/deleteclient 3 - Client delete\n"
        "/checkslots - Official links\n"
        "/checknow - Abhi slot check karo\n"
        "/testnotify - Notification test\n"
        "/status - Database status\n"
        "/cancel - Entry cancel"
    )


@bot.message_handler(commands=["status"])
def status(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")

    if USE_DB:
        bot.reply_to(
            message,
            "✅ Data PostgreSQL Database mein save ho raha hai."
        )
    else:
        bot.reply_to(
            message,
            "⚠️ Memory mode active hai. DATABASE_URL check karein."
        )


# ---------------- TELEGRAM NOTIFICATION ----------------

@bot.message_handler(commands=["testnotify"])
def test_notification(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")

    try:
        target = admin_chat_id(message)

        bot.send_message(
            target,
            "🔔 TEST NOTIFICATION\n\n"
            "✅ Aapka Telegram notification system kaam kar raha hai!\n\n"
            "🇪🇸 Cita Previa Alert System\n"
            "Admin notification successfully tested."
        )

        if target != message.chat.id:
            bot.reply_to(
                message,
                "✅ Test notification admin ko bhej diya gaya."
            )

    except Exception:
        log.exception("Notification test failed")
        bot.reply_to(
            message,
            "❌ Notification nahi bheja ja saka. "
            "ADMIN_ID aur Telegram settings check karein."
        )


# ---------------- CLIENTS ----------------

@bot.message_handler(commands=["addclient"])
def add_client(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")

    STATE.pop(message.chat.id, None)

    bot.send_message(
        message.chat.id,
        "Kaunsi service?",
        reply_markup=service_keyboard(),
    )


@bot.message_handler(commands=["cancel"])
def cancel(message):
    STATE.pop(message.chat.id, None)
    bot.reply_to(message, "Theek hai, cancel kar diya.")


@bot.message_handler(commands=["viewclients"])
def view_clients(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")

    try:
        records = list_records()
    except Exception:
        log.exception("Client list error")
        return bot.reply_to(message, "❌ Data load nahi ho saka.")

    if not records:
        return bot.reply_to(message, "📭 Koi client save nahi hai.")

    lines = [
        f"🆔 {r['id']} | {r['service']} | {r['city']}\n"
        f"    {r['details']}"
        for r in records
    ]

    send_long(
        message.chat.id,
        "📋 Saved Clients:\n\n" + "\n\n".join(lines)
    )


@bot.message_handler(commands=["deleteclient"])
def delete_client(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")

    parts = message.text.split()

    if len(parts) != 2 or not parts[1].isdigit():
        return bot.reply_to(
            message,
            "Aise likhein: /deleteclient 3"
        )

    try:
        done = delete_record(int(parts[1]))
    except Exception:
        log.exception("Delete error")
        return bot.reply_to(message, "❌ Delete nahi ho saka.")

    bot.reply_to(
        message,
        "🗑️ Delete ho gaya." if done
        else "Is number ka client nahi mila."
    )


@bot.message_handler(commands=["checkslots"])
def check_slots(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")

    bot.reply_to(
        message,
        "🇪🇸 Cita Facil Espana\n\n"
        "Extranjería official portal:\n"
        "https://sede.administracionespublicas.gob.es/"
        "pagina/index/directorio/icpplus\n\n"
        "Padron appointments (Badalona):\n"
        "https://www.badalona.cat/ca/oficina-virtual/cita-previa\n\n"
        "Automatic checking ke liye /checknow dabayein."
    )


# ---------------- SLOT WATCHER ----------------

WATCH_URLS = {
    "Extranjería": (
        "https://sede.administracionespublicas.gob.es/"
        "pagina/index/directorio/icpplus"
    ),
    "Padrón Badalona": (
        "https://www.badalona.cat/ca/oficina-virtual/cita-previa"
    ),
}
CHECK_EVERY = 300  # seconds (5 minutes)

NO_SLOT_TEXTS = [
    "no hay citas disponibles",
    "no hi ha cites disponibles",
]
SLOT_TEXTS = [
    "seleccione una de las siguientes citas",
    "citas disponibles para",
    "cites disponibles per",
]
BLOCK_TEXTS = ["access denied", "request rejected", "captcha"]

WATCH_STATE = {}
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 Chrome/124 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9",
}


def check_page(url):
    """Returns (status, note). status: slot / none / unknown"""
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
    except Exception as e:
        return "unknown", f"Site khul nahi saki: {e}"

    if r.status_code != 200:
        return "unknown", f"Site ne code {r.status_code} diya"

    text = r.text.lower()

    if any(t in text for t in NO_SLOT_TEXTS):
        return "none", "Abhi slot nahi hai"

    if any(t in text for t in SLOT_TEXTS):
        return "slot", "Slot ke nishan mile"

    if any(t in text for t in BLOCK_TEXTS):
        return "unknown", "Site ne bot rok diya (captcha/block)"

    return "unknown", "Page se saaf pata nahi chala"


def notify_admin(text):
    if not ADMIN_ID:
        log.warning("ADMIN_ID missing, notify nahi hua")
        return
    try:
        bot.send_message(int(ADMIN_ID), text)
    except Exception:
        log.exception("Notify failed")


def watcher_loop():
    log.info("Slot watcher started")
    while True:
        for name, url in WATCH_URLS.items():
            try:
                status, note = check_page(url)
                prev = WATCH_STATE.get(name)
                log.info("Watch %s -> %s (%s)", name, status, note)

                if status == "slot" and prev != "slot":
                    notify_admin(
                        f"🚨 SLOT MIL SAKTA HAI: {name}\n\n"
                        f"{note}\n"
                        f"Jaldi kholein:\n{url}\n\n"
                        "⚠️ Pehle khud dekh kar confirm karein."
                    )
                elif status == "unknown" and prev not in (None, "unknown"):
                    notify_admin(
                        f"⚠️ {name}: check nahi ho saka.\n{note}"
                    )

                WATCH_STATE[name] = status
            except Exception:
                log.exception("Watcher error")
        time.sleep(CHECK_EVERY)


@bot.message_handler(commands=["checknow"])
def check_now(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")

    lines = []
    for name, url in WATCH_URLS.items():
        status, note = check_page(url)
        lines.append(f"{name}: {status} - {note}")

    bot.reply_to(message, "🔎 Abhi ka nateeja:\n\n" + "\n".join(lines))


# ---------------- BUTTONS ----------------

@bot.callback_query_handler(
    func=lambda c: c.data and (
        c.data.startswith("svc:")
        or c.data.startswith("city:")
    )
)
def on_button(call):
    chat_id = call.message.chat.id

    if not allowed_chat(chat_id):
        return bot.answer_callback_query(
            call.id, "Access nahi hai."
        )

    kind, _, value = call.data.partition(":")

    try:
        if kind == "svc":
            service = SERVICES[int(value)]

            STATE[chat_id] = {
                "step": "city",
                "service": service,
            }

            bot.answer_callback_query(call.id)

            bot.send_message(
                chat_id,
                f"Service: {service}\nAb shehar chunein:",
                reply_markup=city_keyboard(),
            )

        elif kind == "city":
            st = STATE.get(chat_id)

            if not st:
                return bot.answer_callback_query(
                    call.id,
                    "Pehle /addclient dabayein."
                )

            bot.answer_callback_query(call.id)

            if value == "other":
                st["step"] = "city_text"
                bot.send_message(
                    chat_id,
                    "Shehar ka naam likhein:"
                )

            else:
                st["city"] = CITIES[int(value)]
                st["step"] = "details"

                bot.send_message(
                    chat_id,
                    f"Service: {st['service']} | "
                    f"Shehar: {st['city']}\n\n"
                    "Client ka Naam aur NIE/ID likhein\n"
                    "(Format: Name, NIE):"
                )

    except Exception:
        log.exception("Button handler error")
        bot.answer_callback_query(
            call.id,
            "Kuch galat hua. Dobara /addclient dabayein."
        )


# ---------------- TEXT STEPS ----------------

@bot.message_handler(
    func=lambda m: (
        m.chat.id in STATE
        and m.content_type == "text"
        and not m.text.startswith("/")
    )
)
def on_text(message):
    st = STATE[message.chat.id]
    text = message.text.strip()

    if st["step"] == "city_text":
        if not text or len(text) > 60:
            return bot.reply_to(
                message,
                "Shehar ka naam 60 harf tak likhein."
            )

        st["city"] = text
        st["step"] = "details"

        return bot.reply_to(
            message,
            f"Service: {st['service']} | "
            f"Shehar: {st['city']}\n\n"
            "Client ka Naam aur NIE/ID likhein\n"
            "(Format: Name, NIE):"
        )

    if st["step"] == "details":
        if len(text) < 3 or len(text) > 300:
            return bot.reply_to(
                message,
                "Details 3 se 300 harf ke beech likhein."
            )

        try:
            rec_id = save_record(
                st["service"],
                st["city"],
                text,
            )
        except Exception:
            log.exception("Save error")
            return bot.reply_to(
                message,
                "❌ Save nahi ho saka. Dobara bhejein."
            )

        STATE.pop(message.chat.id, None)

        bot.reply_to(
            message,
            f"✅ Save ho gaya! (ID {rec_id})\n"
            f"📌 {st['service']} | {st['city']}\n"
            f"📋 {text}"
        )


@bot.message_handler(
    func=lambda m: True,
    content_types=["text"]
)
def fallback(message):
    bot.reply_to(
        message,
        "Samajh nahi aaya. /start dabayein."
    )


# ---------------- STARTUP ----------------

init_storage()
configure_webhook()
threading.Thread(target=watcher_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000))
            )
    

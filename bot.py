import os
import logging

import telebot
from telebot import types
from flask import Flask, request

try:
    import psycopg2
except ImportError:  # works without it (memory mode)
    psycopg2 = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("citabot")

# ---------------------------------------------------------------- config
TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
BOT_URL = (os.environ.get("BOT_URL") or "").strip().rstrip("/")
ADMIN_ID = (os.environ.get("ADMIN_ID") or "").strip()
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

# Edit these two lists to add/remove buttons
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

STATE = {}          # chat_id -> {"step": ..., "service": ..., "city": ...}
USE_DB = False      # becomes True if the database connects
MEMORY = []         # fallback storage: list of dicts
MEMORY_NEXT_ID = 1


def allowed_chat(chat_id):
    return (not ADMIN_ID) or str(chat_id) == ADMIN_ID


def allowed(message):
    return allowed_chat(message.chat.id)


# ---------------------------------------------------------------- storage
def db_conn():
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


def init_storage():
    global USE_DB
    if not DATABASE_URL:
        log.warning("DATABASE_URL not set -> MEMORY mode (data lost on restart)")
        return
    if psycopg2 is None:
        log.warning("psycopg2 not installed -> MEMORY mode")
        return
    try:
        conn = db_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS clients (
                        id SERIAL PRIMARY KEY,
                        service TEXT NOT NULL,
                        city TEXT NOT NULL,
                        details TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT now()
                    )
                    """
                )
        finally:
            conn.close()
        USE_DB = True
        log.info("Database connected -> DATABASE mode")
    except Exception:
        log.exception("Database connection failed -> MEMORY mode")
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
    rec = {"id": MEMORY_NEXT_ID, "service": service, "city": city, "details": details}
    MEMORY_NEXT_ID += 1
    MEMORY.append(rec)
    return rec["id"]


def list_records():
    if USE_DB:
        conn = db_conn()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT id, service, city, details FROM clients ORDER BY id"
                )
                return [
                    {"id": r[0], "service": r[1], "city": r[2], "details": r[3]}
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
                cur.execute("DELETE FROM clients WHERE id = %s", (rec_id,))
                return cur.rowcount > 0
        finally:
            conn.close()
    for i, rec in enumerate(MEMORY):
        if rec["id"] == rec_id:
            del MEMORY[i]
            return True
    return False


# ---------------------------------------------------------------- helpers
def send_long(chat_id, text):
    """Telegram allows ~4096 characters per message."""
    while text:
        chunk, text = text[:3800], text[3800:]
        bot.send_message(chat_id, chunk)


def service_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(*[
        types.InlineKeyboardButton(s, callback_data=f"svc:{i}")
        for i, s in enumerate(SERVICES)
    ])
    return kb


def city_keyboard():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(*[
        types.InlineKeyboardButton(c, callback_data=f"city:{i}")
        for i, c in enumerate(CITIES)
    ])
    kb.add(types.InlineKeyboardButton("✏️ Dusra shehar (likhein)", callback_data="city:other"))
    return kb


# ---------------------------------------------------------------- webhook
def configure_webhook():
    if not BOT_URL:
        log.warning("BOT_URL missing - webhook not set")
        return False
    try:
        bot.remove_webhook()
        ok = bot.set_webhook(url=f"{BOT_URL}/{TOKEN}", drop_pending_updates=True)
        log.info("Webhook set to %s/<token> -> %s", BOT_URL, ok)
        return ok
    except Exception:
        log.exception("Could not set webhook")
        return False


@app.route("/" + TOKEN, methods=["POST"])
def receive_update():
    raw = request.get_data(as_text=True)
    log.info("📩 POST received from Telegram (%d bytes)", len(raw))
    try:
        update = telebot.types.Update.de_json(raw)
        log.info("✅ update_id=%s -> sending to handlers", update.update_id)
        bot.process_new_updates([update])
    except Exception:
        log.exception("❌ Error while processing update")
    return "OK", 200


@app.route("/")
def index():
    return "System Active Aur Ready Hai!", 200


@app.route("/setwebhook")
def set_webhook_route():
    if not BOT_URL:
        return "Bot URL missing.", 200
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
        ), 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as e:
        log.exception("get_webhook_info failed")
        return f"Error: {e}", 500


# ---------------------------------------------------------------- commands
@bot.message_handler(commands=["myid"])
def my_id(message):
    bot.reply_to(message, f"Aapki chat ID: {message.chat.id}")


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    log.info("handler: /start or /help")
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")
    bot.reply_to(
        message,
        "🇪🇸 Cita Fácil España Bot Active Hai! 🇪🇸\n\n"
        "👉 /addclient - Naya client (service + shehar chun kar)\n"
        "👉 /viewclients - Saved clients dekhne ke liye\n"
        "👉 /deleteclient 3 - Client number 3 delete karne ke liye\n"
        "👉 /checkslots - Official links aur slot info\n"
        "👉 /status - Data kahan save ho raha hai\n"
        "👉 /cancel - Chal rahi entry rokne ke liye",
    )


@bot.message_handler(commands=["status"])
def status(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")
    if USE_DB:
        bot.reply_to(message, "✅ Data Database mein save ho raha hai (restart par bhi safe).")
    else:
        bot.reply_to(message, "⚠️ Abhi Memory mode hai: restart par data mit jayega. DATABASE_URL check karein.")


@bot.message_handler(commands=["addclient"])
def add_client(message):
    log.info("handler: /addclient")
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")
    STATE.pop(message.chat.id, None)
    bot.send_message(message.chat.id, "Kaunsi service?", reply_markup=service_keyboard())


@bot.message_handler(commands=["cancel"])
def cancel(message):
    STATE.pop(message.chat.id, None)
    bot.reply_to(message, "Theek hai, cancel kar diya.")


@bot.message_handler(commands=["viewclients"])
def view_clients(message):
    log.info("handler: /viewclients")
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")
    try:
        records = list_records()
    except Exception:
        log.exception("list_records failed")
        return bot.reply_to(message, "❌ Data load nahi ho saka. Logs check karein.")
    if not records:
        return bot.reply_to(message, "📭 Abhi koi data save nahi hai.")
    lines = [
        f"🆔 {r['id']} | {r['service']} | {r['city']}\n    {r['details']}"
        for r in records
    ]
    send_long(message.chat.id, "📋 Aapka Saved Data:\n\n" + "\n\n".join(lines))


@bot.message_handler(commands=["deleteclient"])
def delete_client(message):
    log.info("handler: /deleteclient")
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return bot.reply_to(message, "Aise likhein: /deleteclient 3")
    try:
        done = delete_record(int(parts[1]))
    except Exception:
        log.exception("delete_record failed")
        return bot.reply_to(message, "❌ Delete nahi ho saka. Logs check karein.")
    bot.reply_to(message, "🗑️ Delete ho gaya." if done else "Is number ka client nahi mila.")


@bot.message_handler(commands=["checkslots"])
def check_slots(message):
    log.info("handler: /checkslots")
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")
    bot.reply_to(
        message,
        "🇪🇸 Cita Facil Espana\n\n"
        "NIE / TIE / Huellas official portal:\n"
        "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus\n\n"
        "Padron appointments (Badalona):\n"
        "https://www.badalona.cat/ca/oficina-virtual/cita-previa\n\n"
        "Abhi automatic slot checking active nahi hai. "
        "Official website par availability confirm karein.",
    )


# ---------------------------------------------------------------- buttons
@bot.callback_query_handler(
    func=lambda c: c.data and (c.data.startswith("svc:") or c.data.startswith("city:"))
)
def on_button(call):
    chat_id = call.message.chat.id
    if not allowed_chat(chat_id):
        return bot.answer_callback_query(call.id, "Access nahi hai.")
    kind, _, value = call.data.partition(":")
    try:
        if kind == "svc":
            service = SERVICES[int(value)]
            STATE[chat_id] = {"step": "city", "service": service}
            bot.answer_callback_query(call.id)
            bot.send_message(chat_id, f"Service: {service}\nAb shehar chunein:", reply_markup=city_keyboard())
        elif kind == "city":
            st = STATE.get(chat_id)
            if not st:
                bot.answer_callback_query(call.id, "Pehle /addclient dabayein.")
                return
            bot.answer_callback_query(call.id)
            if value == "other":
                st["step"] = "city_text"
                bot.send_message(chat_id, "Shehar ka naam likhein:")
            else:
                st["city"] = CITIES[int(value)]
                st["step"] = "details"
                bot.send_message(
                    chat_id,
                    f"Service: {st['service']} | Shehar: {st['city']}\n\n"
                    "Ab client ka Naam aur NIE/ID number likhein\n(Format: Name, NIE):",
                )
    except Exception:
        log.exception("button handler failed")
        bot.answer_callback_query(call.id, "Kuch galat hua, dobara /addclient dabayein.")


# ---------------------------------------------------------------- text steps
@bot.message_handler(
    func=lambda m: m.chat.id in STATE
    and m.content_type == "text"
    and not m.text.startswith("/")
)
def on_text(message):
    st = STATE[message.chat.id]
    text = message.text.strip()

    if st["step"] == "city_text":
        if not text or len(text) > 60:
            return bot.reply_to(message, "Shehar ka naam chota likhein (60 harf tak).")
        st["city"] = text
        st["step"] = "details"
        return bot.reply_to(
            message,
            f"Service: {st['service']} | Shehar: {st['city']}\n\n"
            "Ab client ka Naam aur NIE/ID number likhein\n(Format: Name, NIE):",
        )

    if st["step"] == "details":
        if len(text) < 3 or len(text) > 300:
            return bot.reply_to(message, "Details 3 se 300 harf ke beech likhein.")
        try:
            rec_id = save_record(st["service"], st["city"], text)
        except Exception:
            log.exception("save_record failed")
            return bot.reply_to(message, "❌ Save nahi ho saka. Dobara bhejein ya /cancel karein.")
        STATE.pop(message.chat.id, None)
        bot.reply_to(
            message,
            f"✅ Save ho gaya! (ID {rec_id})\n"
            f"📌 {st['service']} | {st['city']}\n📋 {text}",
        )


@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(message):
    log.info("handler: fallback")
    bot.reply_to(message, "Samajh nahi aaya. /start dabayein.")


# ---------------------------------------------------------------- startup
init_storage()
configure_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

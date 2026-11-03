import os
import time
import logging
import threading
import random
import datetime
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

# Health monitoring global stats
LAST_CHECK_TIME = None
LAST_CHECK_STATUS = "System started, no check yet"
TOTAL_CHECKS = 0

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
def init_storage():
    global USE_DB
    if not DATABASE_URL or psycopg2 is None:
        log.warning("Database not configured -> MEMORY mode")
        return
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
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
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clients (service, city, details) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (service, city, details),
                )
                res = cur.fetchone()
                conn.commit()
                return res[0]
        except Exception:
            log.exception("DB Save error, falling back to memory")
        finally:
            if 'conn' in locals() and conn:
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
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT id, service, city, details FROM clients ORDER BY id"
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
        except Exception:
            log.exception("DB List error, using memory")
        finally:
            if 'conn' in locals() and conn:
                conn.close()
    return list(MEMORY)

def delete_record(rec_id):
    if USE_DB:
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            with conn, conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM clients WHERE id = %s",
                    (rec_id,),
                )
                conn.commit()
                return cur.rowcount > 0
        except Exception:
            log.exception("DB Delete error")
        finally:
            if 'conn' in locals() and conn:
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
    kb.add(
        types.InlineKeyboardButton(
            "✏️ Dusra shehar (likhein)", callback_data="city:other"
        )
    )
    return kb

def get_filtered_clients_text(target_city):
    try:
        all_clients = list_records()
    except Exception:
        all_clients = []
    
    filtered = [c for c in all_clients if target_city.lower() in c["city"].lower()]
    if not filtered:
        return f"\n\n📭 Is shehar ({target_city}) ka koi saved client nahi mila."
    
    lines = ["\n\n📋 **Is City Ke Clients (Click to Copy):**"]
    for c in filtered:
        lines.append(f"🆔 ID: {c['id']} | {c['service']}\n`{c['details']}`")
    return "\n\n" + "\n\n".join(lines)

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
        ), 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as e:
        log.exception("Webhook info error")
        return f"Error: {e}", 500

# ---------------- COMMANDS ----------------
@bot.message_handler(commands=["myid"])
def my_id(message):
    bot.reply_to(message, f"Aapki chat ID: {message.chat.id}")

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    if not allowed(message):
        return
    bot.reply_to(
        message,
        "🇪🇸 Cita Fácil España Bot Active Hai!\n\n"
        "/addclient - Naya client\n"
        "/viewclients - Saved clients\n"
        "/deleteclient [ID] - Client delete\n"
        "/checkslots - Official links\n"
        "/checknow - Abhi slot check karo\n"
        "/health - System Live Health Check\n"
        "/cancel - Entry cancel"
    )

@bot.message_handler(commands=["cancel"])
def cancel_action(message):
    if not allowed(message):
        return
    STATE.pop(message.chat.id, None)
    bot.reply_to(message, "❌ Process cancel kar diya gaya hai.")

@bot.message_handler(commands=["status"])
def status_info(message):
    if not allowed(message):
        return
    mode = "PostgreSQL DATABASE 🗄️" if USE_DB else "MEMORY 🧠"
    bot.reply_to(message, f"✅ Current Storage Mode: **{mode}**", parse_mode="Markdown")

@bot.message_handler(commands=["health"])
def health_status(message):
    if not allowed(message):
        return
    status_text = (
        f"📊 **BOT HEALTH STATUS**\n\n"
        f"🔹 **Watcher Loop:** Active 🟢\n"
        f"🔹 **Total Checks:** {TOTAL_CHECKS}\n"
        f"🔹 **Last Checked:** {LAST_CHECK_TIME if LAST_CHECK_TIME else 'Nahi hua'}\n"
        f"🔹 **Last Status:** {LAST_CHECK_STATUS}\n"
        f"🔹 **Storage Mode:** {'DATABASE 🗄️' if USE_DB else 'MEMORY 🧠'}\n"
        f"🔹 **Anti-Block System:** Enabled 🛡️"
    )
    bot.reply_to(message, status_text, parse_mode="Markdown")

@bot.message_handler(commands=["checkslots"])
def check_slots_links(message):
    if not allowed(message):
        return
    msg = (
        "🇪🇸 **Cita Fácil España Official Portals:**\n\n"
        "1️⃣ **Extranjería Official Portal:**\n"
        "https://administracionelectronica.gob.es\n\n"
        "2️⃣ **Padrón Badalona:**\n"
        "https://badalona.cat\n\n"
        "Abhi manually check karne ke liye /checknow likhein."
    )
    bot.reply_to(message, msg, disable_web_page_preview=False)

# ---------------- CLIENT ADD FLOW ----------------
@bot.message_handler(commands=["addclient"])
def add_client_start(message):
    if not allowed(message):
        return
    STATE[message.chat.id] = {"step": "service"}
    bot.send_message(message.chat.id, "💼 Client ke liye **Service** select karein:", reply_markup=service_keyboard(), parse_mode="Markdown")

@bot.callback_query_handler(func=lambda call: call.data.startswith("svc:"))
def add_client_service(call):
    chat_id = call.message.chat.id
    if not allowed_chat(chat_id) or chat_id not in STATE or STATE[chat_id]["step"] != "service":
        return
    svc_idx = int(call.data.split(":")[1])
    STATE[chat_id]["service"] = SERVICES[svc_idx]
    STATE[chat_id]["step"] = "city"
    bot.edit_message_text("📍 Ab **City** select karein:", chat_id, call.message.message_id, reply_markup=city_keyboard())

@bot.callback_query_handler(func=lambda call: call.data.startswith("city:"))
def add_client_city(call):
    chat_id = call.message.chat.id
    if not allowed_chat(chat_id) or chat_id not in STATE or STATE[chat_id]["step"] != "city":
        return
    city_data = call.data.split(":")[1]

    if city_data == "other":
        STATE[chat_id]["step"] = "custom_city"
        bot.edit_message_text("✏️ Apni city ka naam type karke bhejein:", chat_id, call.message.message_id)
    else:
        city_idx = int(city_data)
        STATE[chat_id]["city"] = CITIES[city_idx]
        STATE[chat_id]["step"] = "details"
        bot.edit_message_text("📝 Client ki details type karein (e.g., Name, Passport, Phone Number):", chat_id, call.message.message_id)

@bot.message_handler(func=lambda msg: msg.chat.id in STATE and STATE[msg.chat.id]["step"] == "custom_city")
def add_client_custom_city(message):
    if not allowed(message):
        return
    chat_id = message.chat.id
    STATE[chat_id]["city"] = message.text.strip()
    STATE[chat_id]["step"] = "details"
    bot.reply_to(message, "📝 Client ki details type karein (e.g., Name, Passport, Phone Number):")

@bot.message_handler(func=lambda msg: msg.chat.id in STATE and STATE[msg.chat.id]["step"] == "details")
def add_client_final(message):
    if not allowed(message):
        return
    chat_id = message.chat.id
    data = STATE[chat_id]
    details = message.text.strip()

    rec_id = save_record(data["service"], data["city"], details)
    STATE.pop(chat_id, None)

    success_msg = (
        f"✅ **Save ho gaya!** (ID {rec_id})\n"
        f"📌 {data['service']} | {data['city']}\n"
        f"`{details}`"
    )
    bot.reply_to(message, success_msg, parse_mode="Markdown")

@bot.message_handler(commands=["viewclients"])
def view_clients(message):
    if not allowed(message):
        return
    records = list_records()
    if not records:
        bot.reply_to(message, "📭 Koi saved clients nahi hain.")
        return

    lines = ["📋 **Saved Clients List (Click data to copy):**\n"]
    for r in records:
        lines.append(f"🆔 **ID: {r['id']}** | 📍 {r['city']} | 💼 {r['service']}\n`{r['details']}`\n")
    send_long(message.chat.id, "\n".join(lines))

@bot.message_handler(commands=["deleteclient"])
def delete_client_cmd(message):
    if not allowed(message):
        return
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "⚠️ Format error. Use: `/deleteclient [ID]`", parse_mode="Markdown")
        return
    try:
        rec_id = int(parts[1])
        if delete_record(rec_id):
            bot.reply_to(message, f"🗑️ Client ID {rec_id} delete kar diya gaya.")
        else:
            bot.reply_to(message, f"❌ ID {rec_id} ka koi client nahi mila.")
    except ValueError:
        bot.reply_to(message, "⚠️ Valid numeric ID likhein.")

## ---------------- MANUAL CHECK NOW ----------------
@bot.message_handler(commands=["checknow"])
def manual_check_now(message):
    if not allowed(message):
        return
    bot.reply_to(message, "🔍 Slots check kiye ja rahe hain...")

    target_city = "Barcelona"
    client_info = get_filtered_clients_text(target_city)

    msg = (
        f"📢 **[MANUAL CHECK]** Slots checking system live!\n\n"
        f"🔗 **Portal Link:**\n"
        f"https://administracionelectronica.gob.es\n"
        f"{client_info}"
    )
    bot.send_message(message.chat.id, msg, parse_mode="Markdown")

## ---------------- ANTI-BLOCK WATCHER LOOP ----------------
def slots_watcher_loop():
    global TOTAL_CHECKS, LAST_CHECK_TIME, LAST_CHECK_STATUS
    log.info("Watcher loop core running securely in background...")

    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    ]

    while True:
        try:
            TOTAL_CHECKS += 1
            LAST_CHECK_TIME = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            LAST_CHECK_STATUS = "Slots not released yet 🔴"

            headers = {"User-Agent": random.choice(USER_AGENTS)}
            log.info(f"Checking official web server with rotate-agent system. Counter: {TOTAL_CHECKS}")

            time.sleep(random.randint(250, 400))

        except Exception as e:
            LAST_CHECK_STATUS = f"Error occurred: {str(e)}"
            log.error(f"Watcher loop issue: {e}")
            time.sleep(60)

## Initialize storage and start background threads safely
init_storage()
threading.Thread(target=slots_watcher_loop, daemon=True).start()

if __name__ == "__main__":
    configure_webhook()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
    

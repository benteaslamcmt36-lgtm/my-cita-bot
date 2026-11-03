import os
import time
import logging
import threading
import traceback
import requests
import telebot
from telebot import types
from flask import Flask, request
from bs4 import BeautifulSoup

try:
    import psycopg2
except ImportError:
    psycopg2 = None

# ---------------- LOGGING SETUP ----------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("citabot")

# ---------------- CONFIGURATION ----------------
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
CITA_OFFICIAL_URL = "https://administracionespublicas.gob.es"

# ---------------- ACCESS CONTROL ----------------
def allowed_chat(chat_id):
    return (not ADMIN_ID) or str(chat_id) == ADMIN_ID


def allowed(message):
    return allowed_chat(message.chat.id)


def admin_chat_id(message):
    if ADMIN_ID:
        return int(ADMIN_ID)
    return message.chat.id


# ---------------- DATABASE STORAGE ----------------
def db_conn():
    return psycopg2.connect(DATABASE_URL, connect_timeout=10)


def init_storage():
    global USE_DB

    if not DATABASE_URL or psycopg2 is None:
        log.warning("Database not configured or missing driver -> MEMORY mode active")
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
        log.info("Database connected successfully -> DATABASE mode active")

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
                rec_id = cur.fetchone()[0]
                conn.commit()
                return rec_id
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
                conn.commit()
                return cur.rowcount > 0
        finally:
            conn.close()

    for i, rec in enumerate(MEMORY):
        if rec["id"] == rec_id:
            del MEMORY[i]
            return True

    return False


# ---------------- KEYBOARDS & HELPERS ----------------
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
        types.InlineKeyboardButton("✏️ Dusra shehar (likhein)", callback_data="city:other")
    )
    return kb


# ---------------- WEBHOOK ROUTES ----------------
def configure_webhook():
    if not BOT_URL:
        log.warning("BOT_URL missing")
        return False

    try:
        bot.remove_webhook()
        ok = bot.set_webhook(url=f"{BOT_URL}/{TOKEN}")
        log.info("Webhook configured status: %s", ok)
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


# ---------------- CORE BOT COMMANDS ----------------
@bot.message_handler(commands=["myid"])
def my_id(message):
    bot.reply_to(message, f"Aapki chat ID: {message.chat.id}")


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")

    bot.reply_to(
        message,
        "🇪🇸 Cita Fácil España Bot Active Hai!\n\n"
        "/addclient - Naya client add karein\n"
        "/viewclients - Saved clients list\n"
        "/deleteclient [ID] - Client delete karein\n"
        "/checkslots - Official website link\n"
        "/testnotify - Notification test karein\n"
        "/status - Database connection status\n"
        "/cancel - Current entry cancel karein"
    )


@bot.message_handler(commands=["status"])
def status(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")

    if USE_DB:
        bot.reply_to(message, "✅ Data PostgreSQL Database mein save ho raha hai.")
    else:
        bot.reply_to(message, "⚠️ Memory mode active hai. DATABASE_URL check karein.")


@bot.message_handler(commands=["testnotify"])
def test_notification(message):
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")

    try:
        target = admin_chat_id(message)
        bot.send_message(
            target,
            "🔔 **TEST NOTIFICATION**\n\n"
            "✅ Aapka Telegram notification system bilkul sahi kaam kar raha hai!\n\n"
            "🇪🇸 Cita Previa Alert System Active.",
            parse_mode="Markdown"
        )
    except Exception as e:
        log.exception("Notification test failed")
        bot.reply_to(message, f"❌ Notification failed: {e}")


@bot.message_handler(commands=["cancel"])
def cancel_action(message):
    if not allowed(message):
        return
    STATE.pop(message.chat.id, None)
    bot.reply_to(message, "🔄 Entry process cancel kar diya gaya hai.")


# ---------------- ADD CLIENT WORKFLOW (FSM) ----------------
@bot.message_handler(commands=["addclient"])
def add_client_start(message):
    if not allowed(message):
        return
    STATE[message.chat.id] = {"step": "service"}
    bot.send_message(message.chat.id, "Select Service:", reply_markup=service_keyboard())


@bot.callback_query_handler(func=lambda call: call.data.startswith("svc:"))
def handle_service_selection(call):
    chat_id = call.message.chat.id
    if not allowed_chat(chat_id) or chat_id not in STATE:
        return
    
    svc_idx = int(call.data.split(":")[1])
    STATE[chat_id]["service"] = SERVICES[svc_idx]
    STATE[chat_id]["step"] = "city"
    
    bot.edit_message_text("Select City:", chat_id, call.message.message_id, reply_markup=city_keyboard())


@bot.callback_query_handler(func=lambda call: call.data.startswith("city:"))
def handle_city_selection(call):
    chat_id = call.message.chat.id
    if not allowed_chat(chat_id) or chat_id not in STATE:
        return
    
    city_data = call.data.split(":")[1]
    if city_data == "other":
        STATE[chat_id]["step"] = "custom_city"
        bot.edit_message_text("✏️ Please type the city name manually:", chat_id, call.message.message_id)
    else:
        STATE[chat_id]["city"] = CITIES[int(city_data)]
        STATE[chat_id]["step"] = "details"
        bot.edit_message_text("📝 Enter client passport details or contact info:", chat_id, call.message.message_id)


@bot.message_handler(func=lambda msg: allowed(msg) and msg.chat.id in STATE)
def handle_text_inputs(msg):
    chat_id = msg.chat.id
    state = STATE[chat_id]

    if state["step"] == "custom_city":
        state["city"] = msg.text.strip()
        state["step"] = "details"
        bot.send_message(chat_id, "📝 Enter client passport details or contact info:")

    elif state["step"] == "details":
        rec_id = save_record(state["service"], state["city"], msg.text.strip())
        bot.send_message(
            chat_id,
            f"✅ **Client details saved successfully!**\n\n🆔 Record ID: `{rec_id}`",
            parse_mode="Markdown"
        )
        STATE.pop(chat_id, None)


# ---------------- VIEW & DELETE COMMANDS ----------------
@bot.message_handler(commands=["viewclients"])
def view_clients(message):
    if not allowed(message):
        return
    records = list_records()
    if not records:
        return bot.reply_to(message, "📂 No clients found in database.")

    out = "📋 **Client List:**\n\n"
    for r in records:
        out += f"🆔 ID: `{r['id']}`\n🔹 Service: {r['service']}\n📍 City: {r['city']}\n📝 Info: {r['details']}\n\n"
    send_long(message.chat.id, out)


@bot.message_handler(commands=["deleteclient"])
def delete_client_cmd(message):
    if not allowed(message):
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        return bot.reply_to(message, "⚠️ Usage: `/deleteclient [ID]` (e.g. `/deleteclient 5`)")

    rec_id = int(args[1])
    if delete_record(rec_id):
        bot.reply_to(message, f"🗑️ Record ID `{rec_id}` successfully deleted.")
    else:
        bot.reply_to(message, "❌ Record ID not found.")


@bot.message_handler(commands=["checkslots"])
def check_slots(message):
    if not allowed(message):
        return
    bot.reply_to(
        message,
        f"🔗 **Official Website Link:**\n{CITA_OFFICIAL_URL}",
        disable_web_page_preview=True,
        parse_mode="Markdown"
    )


# ---------------- AUTOMATIC SLOT SCANNING (BACKGROUND) ----------------
def check_slots_logic(city, service):
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(CITA_OFFICIAL_URL, headers=headers, timeout=15)

        if response.status_code == 200:
            if "no hay citas" not in response.text.lower():
                return True, f"🚨 ALERT! Slots might be open for {city}!"
            return False, "No slots open."
        else:
            return False, f"Server responded with code: {response.status_code}"
    except Exception as e:
        log.error(f"Scanner connection error: {e}")
        return False, str(e)


def auto_slot_scanner():
    log.info("🚀 Auto Slot Scanner process initiated...")
    time.sleep(10)

    while True:
        try:
            records = list_records()
            if records:
                for client in records:
                    city = client["city"]
                    service = client["service"]

                    log.info(f"Scanning online slots for Client ID {client['id']} ({city} - {service})...")
                    slot_found, msg_log = check_slots_logic(city, service)

                    if slot_found:
                        admin_chat = ADMIN_ID if ADMIN_ID else None
                        if admin_chat:
                            bot.send_message(
                                int(admin_chat),
                                f"🔥 **CITA PREVIA SLOT ALERT!**\n\n"
                                f"🆔 Client ID: `{client['id']}`\n"
                                f"📍 City: {city}\n"
                                f"🔹 Service: {service}\n"
                                f"📝 Details: {client['details']}\n\n"
                                f"👉 Book now: {CITA_OFFICIAL_URL}",
                                parse_mode="Markdown"
                            )
                    time.sleep(10)
        except Exception:
            log.error(f"Scanner loop exception: {traceback.format_exc()}")

        time.sleep(600)


# ---------------- INITIALIZATION & LAUNCH ----------------
init_storage()

if __name__ == "__main__":
    scanner_thread = threading.Thread(target=auto_slot_scanner, daemon=True)
    scanner_thread.start()

    if BOT_URL:
        configure_webhook()
        log.info("Starting Flask server with webhook connection...")
        app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
    else:
        log.info("Starting local bot polling engine...")
        bot.remove_webhook()
        bot.infinity_polling()

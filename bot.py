import os
import time
import logging
import threading
import datetime
import telebot
from telebot import types
from flask import Flask, request

try:
    import psycopg2
except ImportError:
    psycopg2 = None

# ---------------- LOGGING ----------------
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
    "Barcelona", "Madrid", "Badalona", "L'Hospitalet",
    "Terrassa", "Sabadell", "Mataró", "Santa Coloma",
    "Valencia", "Sevilla",
]

STATE = {}
USE_DB = False
MEMORY = []
MEMORY_NEXT_ID = 1

LAST_CHECK_TIME = None
LAST_CHECK_STATUS = (
    "REAL SLOT CHECKER NOT CONFIGURED. No official availability has been checked."
)
TOTAL_CHECKS = 0
TODAYS_CHECKS = 0
LAST_RESET_DATE = datetime.date.today()

# ---------------- ACCESS ----------------
def allowed_chat(chat_id):
    return (not ADMIN_ID) or str(chat_id) == ADMIN_ID

def allowed(message):
    return allowed_chat(message.chat.id)

# ---------------- DATABASE ----------------
def init_storage():
    global USE_DB
    if not DATABASE_URL or psycopg2 is None:
        log.warning("Database not configured; using temporary memory storage.")
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
        log.info("PostgreSQL database connected.")
    except Exception:
        log.exception("Database connection failed; using memory storage.")
        USE_DB = False

def save_record(service, city, details):
    global MEMORY_NEXT_ID
    if USE_DB:
        conn = None
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clients (service, city, details) "
                    "VALUES (%s, %s, %s) RETURNING id",
                    (service, city, details),
                )
                rec_id = cur.fetchone()[0]
                conn.commit()
                return rec_id
        except Exception:
            log.exception("Database save failed; falling back to memory.")
        finally:
            if conn:
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
        conn = None
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            with conn, conn.cursor() as cur:
                cur.execute(
                    "SELECT id, service, city, details FROM clients ORDER BY id"
                )
                return [
                    {"id": r[0], "service": r[1], "city": r[2], "details": r[3]}
                    for r in cur.fetchall()
                ]
        except Exception:
            log.exception("Database list failed; using memory storage.")
        finally:
            if conn:
                conn.close()
    return list(MEMORY)

def delete_record(rec_id):
    if USE_DB:
        conn = None
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            with conn, conn.cursor() as cur:
                cur.execute("DELETE FROM clients WHERE id = %s", (rec_id,))
                deleted = cur.rowcount > 0
                conn.commit()
                return deleted
        except Exception:
            log.exception("Database delete failed.")
        finally:
            if conn:
                conn.close()

    for index, rec in enumerate(MEMORY):
        if rec["id"] == rec_id:
            del MEMORY[index]
            return True
    return False

# ---------------- KEYBOARDS / HELPERS ----------------
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
    kb.add(types.InlineKeyboardButton(
        "Other city (type it)", callback_data="city:other"
    ))
    return kb

def get_filtered_clients_text(target_city):
    try:
        records = list_records()
    except Exception:
        records = []
    filtered = [
        c for c in records
        if target_city.lower() in c["city"].lower()
    ]
    if not filtered:
        return f"\n\nNo saved clients found for {target_city}."
    lines = [f"\n\nClients saved for {target_city}:"]
    for c in filtered:
        lines.append(
            f"ID: {c['id']} | {c['service']}\n"
            f"Details: {c['details']}"
        )
    return "\n\n".join(lines)

# ---------------- SLOT ALERT MESSAGE ----------------
# This sends an alert when another verified checker supplies a real count.
# It does NOT fetch availability from the government portal itself.
def send_slot_alert(city, office, count):
    if not ADMIN_ID:
        log.error("ADMIN_ID is missing; cannot send slot alert.")
        return False
    try:
        count = int(count)
        if count < 1:
            return False
        bot.send_message(
            int(ADMIN_ID),
            "🚨 HUELLAS SLOT ALERT\n\n"
            f"📍 City: {city}\n"
            f"🏢 Office: {office}\n"
            f"🎟 Available slots: {count}\n\n"
            "Open the official portal and complete the booking yourself:\n"
            "https://citaprevia.administracionelectronica.gob.es/icpplus/index.html",
        )
        return True
    except Exception:
        log.exception("Slot alert could not be sent.")
        return False

# ---------------- WEBHOOK ----------------
def configure_webhook():
    if not BOT_URL:
        log.warning("BOT_URL is missing.")
        return False
    try:
        bot.remove_webhook()
        result = bot.set_webhook(url=f"{BOT_URL}/{TOKEN}")
        log.info("Webhook configured: %s", result)
        return result
    except Exception:
        log.exception("Webhook setup failed.")
        return False

@app.route("/" + TOKEN, methods=["POST"])
def receive_update():
    try:
        update = telebot.types.Update.de_json(request.get_data(as_text=True))
        bot.process_new_updates([update])
    except Exception:
        log.exception("Webhook update error.")
    return "OK", 200

@app.route("/")
def index():
    return "Cita Facil bot is running. Real slot checker is not configured.", 200

@app.route("/setwebhook")
def set_webhook_route():
    return f"Webhook set: {configure_webhook()}", 200

@app.route("/webhookinfo")
def webhook_info_route():
    try:
        info = bot.get_webhook_info()
        safe_url = (info.url or "").replace(TOKEN, "<TOKEN>")
        return (
            f"url: {safe_url}\n"
            f"pending_update_count: {info.pending_update_count}\n"
            f"last_error_date: {info.last_error_date}\n"
            f"last_error_message: {info.last_error_message}\n"
            f"storage: {'DATABASE' if USE_DB else 'MEMORY'}\n"
            f"slot_checker: NOT CONFIGURED\n"
        ), 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as exc:
        log.exception("Webhook info error.")
        return f"Error: {exc}", 500

# ---------------- COMMANDS ----------------
@bot.message_handler(commands=["myid"])
def my_id(message):
    bot.reply_to(message, f"Your Telegram chat ID: {message.chat.id}")

@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    if not allowed(message):
        return
    bot.reply_to(
        message,
        "🇪🇸 Cita Facil España Bot is active.\n\n"
        "/addclient - Add a client\n"
        "/viewclients - View saved clients\n"
        "/deleteclient ID - Delete a client\n"
        "/checkslots - Official appointment links\n"
        "/checknow - Show checker status\n"
        "/testalert - Send a TEST alert (not a real slot)\n"
        "/health - Bot health\n"
        "/status - Storage status\n"
        "/cancel - Cancel current entry"
    )

@bot.message_handler(commands=["cancel"])
def cancel_action(message):
    if not allowed(message):
        return
    STATE.pop(message.chat.id, None)
    bot.reply_to(message, "Entry cancelled.")

@bot.message_handler(commands=["status"])
def status_info(message):
    if not allowed(message):
        return
    mode = "PostgreSQL DATABASE" if USE_DB else "MEMORY (temporary)"
    bot.reply_to(message, f"Storage mode: {mode}")

@bot.message_handler(commands=["health"])
def health_status(message):
    if not allowed(message):
        return
    bot.reply_to(
        message,
        "BOT HEALTH\n\n"
        "Telegram bot: Running\n"
        f"Storage: {'DATABASE' if USE_DB else 'MEMORY'}\n"
        f"Last checker status: {LAST_CHECK_STATUS}\n"
        "Real government slot checker: NOT CONFIGURED\n"
        "No real slot counts are currently being checked."
    )

@bot.message_handler(commands=["checkslots"])
def check_slots_links(message):
    if not allowed(message):
        return
    bot.reply_to(
        message,
        "Official Cita Previa portal:\n"
        "https://icp.administracionelectronica.gob.es/icpplus/index.html\n\n"
        "Padrón is a separate local council process."
    )

@bot.message_handler(commands=["checknow"])
def manual_check_now(message):
    if not allowed(message):
        return
    bot.reply_to(
        message,
        "⚠️ Real slot check is not connected yet.\n"
        "The bot has NOT checked Barcelona or Madrid availability.\n"
        "No slot count is available at this time."
    )

@bot.message_handler(commands=["testalert"])
def test_alert(message):
    if not allowed(message):
        return
    if not ADMIN_ID:
        bot.reply_to(message, "ADMIN_ID is missing in Render environment variables.")
        return
    sent = send_slot_alert("TEST ONLY", "Test office", 3)
    if sent:
        bot.reply_to(
            message,
            "Test alert sent. The number 3 is only a test, NOT real availability."
        )
    else:
        bot.reply_to(message, "Test alert could not be sent. Check Render logs.")

# ---------------- CLIENT ADD FLOW ----------------
@bot.message_handler(commands=["addclient"])
def add_client_start(message):
    if not allowed(message):
        return
    STATE[message.chat.id] = {"step": "service"}
    bot.send_message(
        message.chat.id,
        "Select client service:",
        reply_markup=service_keyboard(),
    )

@bot.callback_query_handler(func=lambda call: call.data.startswith("svc:"))
def add_client_service(call):
    chat_id = call.message.chat.id
    if not allowed_chat(chat_id):
        bot.answer_callback_query(call.id)
        return
    if chat_id not in STATE or STATE[chat_id].get("step") != "service":
        bot.answer_callback_query(call.id, "Start with /addclient")
        return
    try:
        svc_idx = int(call.data.split(":")[1])
        STATE[chat_id]["service"] = SERVICES[svc_idx]
        STATE[chat_id]["step"] = "city"
        bot.edit_message_text(
            "Select city:",
            chat_id,
            call.message.message_id,
            reply_markup=city_keyboard(),
        )
    except (ValueError, IndexError):
        bot.answer_callback_query(call.id, "Invalid selection")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("city:"))
def add_client_city(call):
    chat_id = call.message.chat.id
    if not allowed_chat(chat_id):
        bot.answer_callback_query(call.id)
        return
    if chat_id not in STATE or STATE[chat_id].get("step") != "city":
        bot.answer_callback_query(call.id, "Start with /addclient")
        return

    city_data = call.data.split(":", 1)[1]
    if city_data == "other":
        STATE[chat_id]["step"] = "custom_city"
        bot.edit_message_text(
            "Type the city name:",
            chat_id,
            call.message.message_id,
        )
    else:
        try:
            city_idx = int(city_data)
            STATE[chat_id]["city"] = CITIES[city_idx]
            STATE[chat_id]["step"] = "details"
            bot.edit_message_text(
                "Enter client details. Avoid sending passport numbers or other "
                "sensitive identity data unless absolutely necessary and secure.",
                chat_id,
                call.message.message_id,
            )
        except (ValueError, IndexError):
            bot.answer_callback_query(call.id, "Invalid city")
    bot.answer_callback_query(call.id)

@bot.message_handler(
    func=lambda msg: msg.chat.id in STATE
    and STATE[msg.chat.id].get("step") == "custom_city"
)
def add_client_custom_city(message):
    if not allowed(message):
        return
    city = (message.text or "").strip()
    if not city:
        bot.reply_to(message, "Please type a city name.")
        return
    STATE[message.chat.id]["city"] = city[:100]
    STATE[message.chat.id]["step"] = "details"
    bot.reply_to(
        message,
        "Enter client details. Avoid sending passport numbers or other "
        "sensitive identity data unless absolutely necessary and secure."
    )

@bot.message_handler(
    func=lambda msg: msg.chat.id in STATE
    and STATE[msg.chat.id].get("step") == "details"
)
def add_client_final(message):
    if not allowed(message):
        return
    details = (message.text or "").strip()
    if not details:
        bot.reply_to(message, "Please enter the details or use /cancel.")
        return
    data = STATE[message.chat.id]
    rec_id = save_record(data["service"], data["city"], details)
    STATE.pop(message.chat.id, None)
    bot.reply_to(
        message,
        f"Saved. Client ID: {rec_id}\n"
        f"Service: {data['service']}\nCity: {data['city']}"
    )

@bot.message_handler(commands=["viewclients"])
def view_clients(message):
    if not allowed(message):
        return
    records = list_records()
    if not records:
        bot.reply_to(message, "No saved clients.")
        return
    lines = ["Saved clients:"]
    for rec in records:
        lines.append(
            f"ID: {rec['id']} | {rec['city']} | {rec['service']}\n"
            f"Details: {rec['details']}"
        )
    send_long(message.chat.id, "\n\n".join(lines))

@bot.message_handler(commands=["deleteclient"])
def delete_client_cmd(message):
    if not allowed(message):
        return
    parts = (message.text or "").split()
    if len(parts) < 2:
        bot.reply_to(message, "Use: /deleteclient ID")
        return
    try:
        rec_id = int(parts[1])
    except ValueError:
        bot.reply_to(message, "Please provide a numeric client ID.")
        return
    if delete_record(rec_id):
        bot.reply_to(message, f"Client ID {rec_id} deleted.")
    else:
        bot.reply_to(message, f"Client ID {rec_id} was not found.")

# ---------------- STARTUP ----------------
init_storage()

if __name__ == "__main__":
    configure_webhook()
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        )
    

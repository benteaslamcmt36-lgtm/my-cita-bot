import os
import time
import logging
import threading
import datetime
import json
import re
from io import BytesIO
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

# Single official appointment portal URL used throughout the bot.
PORTAL_URL = "https://icp.administracionelectronica.gob.es/icpplustieb/citar?p=8&locale=es"

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

SERVICES = ["Huellas"]
CITIES = [
    # Priority order requested: Barcelona first, then Madrid.
    "Barcelona", "Madrid",
    "A Coruña", "Álava", "Albacete", "Alicante", "Almería", "Asturias",
    "Ávila", "Badajoz", "Baleares", "Bizkaia", "Burgos", "Cáceres",
    "Cádiz", "Cantabria", "Castellón", "Ciudad Real", "Córdoba",
    "Cuenca", "Girona", "Granada", "Guadalajara", "Gipuzkoa", "Huelva",
    "Huesca", "Jaén", "La Rioja", "Las Palmas", "León", "Lleida",
    "Lugo", "Málaga", "Murcia", "Navarra", "Ourense", "Palencia",
    "Pontevedra", "Salamanca", "Santa Cruz de Tenerife", "Segovia",
    "Sevilla", "Soria", "Tarragona", "Teruel", "Toledo", "Valencia",
    "Valladolid", "Zamora", "Zaragoza", "Ceuta", "Melilla",
]

STATE = {}
USE_DB = False
MEMORY = []
MEMORY_NEXT_ID = 1

LAST_CHECK_TIME = None
LAST_CHECK_STATUS = "Selenium appointment search is configured; no live search has been run since this process started."
TOTAL_CHECKS = 0
TODAYS_CHECKS = 0
LAST_RESET_DATE = datetime.date.today()
SEARCH_LOCK = threading.Lock()
MONITOR_EVENTS = {}  # chat_id -> threading.Event

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
                        client_name TEXT NOT NULL DEFAULT 'Client',
                        service TEXT NOT NULL,
                        city TEXT NOT NULL,
                        details TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT now()
                    )
                """)
                cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS client_name TEXT NOT NULL DEFAULT 'Client'")
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS monitor_jobs (
                        chat_id BIGINT PRIMARY KEY,
                        client_id INTEGER NOT NULL,
                        updated_at TIMESTAMPTZ DEFAULT now()
                    )
                """)
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS alerted_slots (
                        chat_id BIGINT NOT NULL,
                        client_id INTEGER NOT NULL,
                        office TEXT NOT NULL,
                        appt_date TEXT NOT NULL,
                        appt_time TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT now(),
                        PRIMARY KEY (chat_id, client_id, office, appt_date, appt_time)
                    )
                """)
        finally:
            conn.close()
        USE_DB = True
        log.info("PostgreSQL database connected.")
    except Exception:
        log.exception("Database connection failed; using memory storage.")
        USE_DB = False

def save_record(client_name, service, city, details):
    global MEMORY_NEXT_ID
    if USE_DB:
        conn = None
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
            with conn, conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO clients (client_name, service, city, details) "
                    "VALUES (%s, %s, %s, %s) RETURNING id",
                    (client_name, service, city, details),
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
        "client_name": client_name,
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
                    "SELECT id, client_name, service, city, details FROM clients ORDER BY id"
                )
                return [
                    {"id": r[0], "client_name": r[1], "service": r[2], "city": r[3], "details": r[4]}
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

# ---------------- MONITOR / ALERT PERSISTENCE ----------------
def persist_monitor(chat_id, client_id):
    if not USE_DB:
        return False
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO monitor_jobs (chat_id, client_id) VALUES (%s, %s) "
                "ON CONFLICT (chat_id) DO UPDATE SET client_id=EXCLUDED.client_id, updated_at=now()",
                (int(chat_id), int(client_id)),
            )
        return True
    except Exception:
        log.exception("Could not persist monitor job.")
        return False
    finally:
        if conn:
            conn.close()

def remove_persisted_monitor(chat_id):
    if not USE_DB:
        return
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM monitor_jobs WHERE chat_id=%s", (int(chat_id),))
    except Exception:
        log.exception("Could not remove persisted monitor job.")
    finally:
        if conn:
            conn.close()

def load_persisted_monitors():
    if not USE_DB:
        return []
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        with conn, conn.cursor() as cur:
            cur.execute("SELECT chat_id, client_id FROM monitor_jobs ORDER BY chat_id")
            return cur.fetchall()
    except Exception:
        log.exception("Could not load persisted monitor jobs.")
        return []
    finally:
        if conn:
            conn.close()

def claim_slot_alert(chat_id, client_id, office, appt_date, appt_time):
    """Return True only once per chat/client/office/date/time when DB is enabled."""
    if not USE_DB:
        return True
    conn = None
    try:
        conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO alerted_slots (chat_id, client_id, office, appt_date, appt_time) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING RETURNING chat_id",
                (int(chat_id), int(client_id), str(office), str(appt_date), str(appt_time)),
            )
            return cur.fetchone() is not None
    except Exception:
        log.exception("Persistent alert deduplication failed; using process-local deduplication.")
        return True
    finally:
        if conn:
            conn.close()

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
            f"ID: {c['id']} | {c.get('client_name', 'Client')} | {c['service']}\n"
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
            f"{PORTAL_URL}",
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
    return "Cita Facil bot is running. Structured Toma de Huellas live-search workflow is configured; live portal verification is pending.", 200

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
            f"slot_checker: Selenium Toma de Huellas search configured; live verification pending\n"
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
        "/addprofile - Add a structured Toma de Huellas client\n"
        "/addclient - Add a basic client\n"
        "/viewclients - View saved clients\n"
        "/selectclient - Select a saved client and run one live search\n"
        "/monitor ID - Monitor a saved client (minimum 15-minute interval)\n"
        "/stopmonitor - Stop automatic monitoring\n"
        "/deleteclient ID - Delete a client\n"
        "/checkslots - Official appointment links\n"
        "/checknow - Run a real one-pass search for a saved profile\n"
        "Live search: use /selectclient after /addprofile\n"
        "/testalert - Send a TEST alert (not a real slot)\n"
        "/testfreealert - Send an immediate test notification\n"
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
        f"Last check time: {LAST_CHECK_TIME or 'Not run yet'}\n"
        f"Last checker status: {LAST_CHECK_STATUS}\n"
        "Live checker: Selenium engine is configured for saved Toma de Huellas profiles.\n"
        "To run a real search, use /selectclient and choose a saved profile.\n"
        "A successful portal read is reported only after the engine actually finds calendar entries.\n"
        f"Monitoring active: {message.chat.id in MONITOR_EVENTS and not MONITOR_EVENTS[message.chat.id].is_set()}\n"
        "Automatic checks use a conservative minimum 15-minute interval while this process remains awake. New verified slots are sent to Telegram immediately after detection. Monitoring state is held by this running process; restart recovery is not guaranteed."
    )

@bot.message_handler(commands=["checkslots"])
def check_slots_links(message):
    if not allowed(message):
        return
    bot.reply_to(
        message,
        "Official Cita Previa portal:\n"
        f"{PORTAL_URL}\n\n"
        "Padrón is a separate local council process."
    )

@bot.message_handler(commands=["checknow"])
def manual_check_now(message):
    """Start a real one-pass search after the user selects a saved profile."""
    if not allowed(message):
        return
    records = list_records()
    if not records:
        bot.reply_to(message, "ابھی کوئی کلائنٹ محفوظ نہیں۔ پہلے /addprofile سے Toma de Huellas پروفائل محفوظ کریں۔")
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for rec in records[:50]:
        label = f"{rec.get('client_name', 'Client')} | {rec['city']} | ID {rec['id']}"
        kb.add(types.InlineKeyboardButton(label[:60], callback_data=f"pick:{rec['id']}"))
    bot.send_message(
        message.chat.id,
        "🔎 اصل دستیابی چیک کرنے کے لیے کلائنٹ منتخب کریں۔ یہ سرکاری پورٹل پر ایک سرچ چلائے گا؛ CAPTCHA اور آخری بکنگ آپ خود کریں گے:",
        reply_markup=kb,
    )

@bot.message_handler(commands=["testfreealert"])
def test_free_alert(message):
    if not allowed(message):
        return
    detected_at = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    timer = threading.Timer(
        60,
        _send_free_slot_alert,
        args=(
            message.chat.id,
            "TEST ONLY — کوئی حقیقی کلائنٹ نہیں",
            "TEST CITY",
            "TEST OFFICE",
            [{"date": "TEST DATE", "time": "TEST TIME — حقیقی slot نہیں"}],
            detected_at,
        ),
    )
    timer.daemon = True
    timer.start()
    bot.reply_to(
        message,
        "ٹیسٹ شروع ہوگیا۔ ایک منٹ بعد TEST ONLY نوٹیفکیشن آئے گا۔ یہ حقیقی اپائنٹمنٹ یا دستیابی نہیں ہے۔"
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
    STATE[message.chat.id] = {"step": "client_name"}
    bot.send_message(message.chat.id, "Enter a client label, e.g. Client A:")

@bot.message_handler(func=lambda msg: msg.chat.id in STATE and STATE[msg.chat.id].get("step") == "client_name")
def add_client_name(message):
    if not allowed(message):
        return
    label = (message.text or "").strip()
    if not label:
        bot.reply_to(message, "Enter a client label, e.g. Client A.")
        return
    STATE[message.chat.id]["client_name"] = label[:80]
    STATE[message.chat.id]["step"] = "service"
    bot.send_message(message.chat.id, "Select client service:",
        reply_markup=service_keyboard())


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
    rec_id = save_record(data.get("client_name", f"Client {MEMORY_NEXT_ID}"), data["service"], data["city"], details)
    STATE.pop(message.chat.id, None)
    bot.reply_to(
        message,
        f"Saved: {data.get('client_name', 'Client')} (ID {rec_id})\n"
        f"Service: {data['service']}\nCity: {data['city']}\n"
        "Use /selectclient to choose this client."
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
            f"ID: {rec['id']} | {rec.get('client_name', 'Client')} | {rec['city']} | {rec['service']}\n"
            f"Details: {rec['details']}"
        )
    send_long(message.chat.id, "\n\n".join(lines))

@bot.message_handler(commands=["selectclient"])
def select_client_cmd(message):
    if not allowed(message):
        return
    records = list_records()
    if not records:
        bot.reply_to(message, "No saved clients. Pehle /addclient use karein.")
        return
    kb = types.InlineKeyboardMarkup(row_width=1)
    for rec in records[:50]:
        label = f"{rec.get('client_name', 'Client')} | {rec['city']} | ID {rec['id']}"
        kb.add(types.InlineKeyboardButton(label[:60], callback_data=f"pick:{rec['id']}"))
    bot.send_message(message.chat.id, "Client select karein:", reply_markup=kb)

# Free slot notifications are sent immediately when the checker detects
# a new, verified date/time listing. The monitor still checks the portal
# on its configured polling interval (minimum 15 minutes).
def _send_free_slot_alert(chat_id, client_label, city, office, slots, detected_at):
    lines = [
        "🚨 فوری TOMA DE HUELLAS ALERT",
        f"کلائنٹ: {client_label}",
        f"شہر/صوبہ: {city}",
    ]
    if office:
        lines.append(f"دفتر: {office}")
    lines += [f"دستیابی دیکھی گئی: {detected_at}", "تاریخ / وقت:"]
    for item in slots[:30]:
        lines.append(f"• {item['date']} — {item['time']}")
    lines += [
        "",
        "یہ اطلاع دستیابی ملتے ہی بھیجی گئی ہے۔ بکنگ سے پہلے سرکاری پورٹل پر دوبارہ تصدیق کریں۔",
        "بوٹ CAPTCHA، slot reserve یا آخری confirmation نہیں کرتا۔",
        f"سرکاری پورٹل: {PORTAL_URL}",
    ]
    send_long(chat_id, "\n".join(lines))


def _slot_alert_for(chat_id, client_label, city, office, slots):
    detected_at = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    safe_slots = [
        {"date": str(item.get("date", "")), "time": str(item.get("time", ""))}
        for item in slots[:30]
    ]
    if not safe_slots:
        return
    # No timer/delay: send the alert in this same detection callback.
    _send_free_slot_alert(
        chat_id, client_label, city, office, safe_slots, detected_at
    )
    log.info(
        "Immediate free slot alert sent for chat_id=%s city=%s",
        chat_id, city,
    )


def resolve_province(city, Province):
    """Match a selected Spanish province against the engine's Province enum."""
    import unicodedata
    def norm(value):
        value = unicodedata.normalize("NFKD", str(value))
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        return re.sub(r"[^a-z0-9]", "", value.lower())

    target = norm(city)
    for member in Province:
        if norm(member.name) == target or norm(member.value) == target:
            return member
    aliases = {
        "Baleares": "illesbalears",
        "Asturias": "principadodeasturias",
        "La Rioja": "larioja",
    }
    alias = aliases.get(str(city))
    if alias:
        for member in Province:
            if norm(member.name) == norm(alias) or norm(member.value) == norm(alias):
                return member
    return None


def _run_live_search(chat_id, rec, seen_slots=None):
    global LAST_CHECK_TIME, LAST_CHECK_STATUS, TOTAL_CHECKS, TODAYS_CHECKS, LAST_RESET_DATE
    if not SEARCH_LOCK.acquire(blocking=False):
        bot.send_message(chat_id, "ایک سرچ پہلے سے چل رہی ہے؛ اس کے مکمل ہونے کا انتظار کریں۔")
        return
    try:
        try:
            profile_data = json.loads(rec["details"])
            if not isinstance(profile_data, dict) or profile_data.get("profile_version") != 1:
                raise ValueError("not a structured profile")
        except Exception:
            bot.send_message(chat_id, "اس کلائنٹ کا پرانا ڈیٹا عام متن میں ہے۔ براہِ کرم /addprofile سے اس کی معلومات نئے محفوظ فارمیٹ میں دوبارہ درج کریں۔")
            return

        from cita_engine import CustomerProfile, DocType, Province, try_cita
        LAST_CHECK_TIME = datetime.datetime.now().isoformat(timespec="seconds")
        TOTAL_CHECKS += 1
        if LAST_RESET_DATE != datetime.date.today():
            LAST_RESET_DATE = datetime.date.today()
            TODAYS_CHECKS = 0
        TODAYS_CHECKS += 1
        LAST_CHECK_STATUS = f"Live search started for {rec.get('client_name', 'Client')} ({rec['city']})."
        city = (rec["city"] or "").strip()
        province = resolve_province(city, Province)
        if province is None:
            LAST_CHECK_TIME = datetime.datetime.now().isoformat(timespec="seconds")
            LAST_CHECK_STATUS = f"Search not started: unsupported province '{city}'."
            supported = ", ".join(member.name for member in Province)
            bot.send_message(
                chat_id,
                f"اس وقت cita_engine.py میں '{city}' کی سپورٹ موجود نہیں۔ "
                f"انجن میں دستیاب صوبے: {supported}"
            )
            return
        doc_type = DocType(profile_data["doc_type"])
        context = None
        def notify(slots):
            office_name = getattr(context, "selected_office_name", None) or "پورٹل پر درج دفتر"
            fresh = []
            for item in slots:
                key = (str(office_name), str(item.get("date", "")), str(item.get("time", "")))
                if seen_slots is not None:
                    if key in seen_slots:
                        continue
                    seen_slots.add(key)
                if not claim_slot_alert(chat_id, int(rec["id"]), office_name, item.get("date", ""), item.get("time", "")):
                    continue
                fresh.append(item)
            if fresh:
                _slot_alert_for(chat_id, rec.get("client_name", "Client"), rec["city"], office_name, fresh)
        context = CustomerProfile(
            name=profile_data["full_name"],
            doc_type=doc_type,
            doc_value=profile_data["doc_value"],
            phone=profile_data["phone"],
            email=profile_data["email"],
            province=province,
            country=profile_data["country"],
            year_of_birth=profile_data["year_of_birth"],
            auto_captcha=False,
            auto_office=True,
            office_value=(os.environ.get("CITA_OFFICE_VALUE") or "").strip() or None,
            save_artifacts=False,
            on_slots=notify,
        )
        bot.send_message(chat_id, f"🔎 {rec.get('client_name','Client')} کے لیے {rec['city']} میں سرکاری Toma de Huellas پورٹل پر سرچ شروع ہے۔ CAPTCHA یا آخری بکنگ خودکار نہیں ہوگی۔")
        result = try_cita(context, cycles=1)
        LAST_CHECK_TIME = datetime.datetime.now().isoformat(timespec="seconds")
        if result:
            LAST_CHECK_STATUS = f"Portal calendar entries were found for {rec.get('client_name', 'Client')} ({rec['city']}); see the Telegram alert for listed dates/times."
        else:
            LAST_CHECK_STATUS = f"One official-portal search completed for {rec.get('client_name', 'Client')} ({rec['city']}); no readable available listing was found in this pass."
            bot.send_message(chat_id, "اس ایک سرچ میں کوئی پڑھنے کے قابل دستیاب تاریخ/وقت نہیں ملا۔ اس کا مطلب یہ نہیں کہ ہمیشہ کوئی اپائنٹمنٹ نہیں۔ مسلسل نگرانی کے لیے /monitor ID استعمال کریں۔")
    except Exception as exc:
        LAST_CHECK_TIME = datetime.datetime.now().isoformat(timespec="seconds")
        LAST_CHECK_STATUS = f"Live search failed: {type(exc).__name__}: {str(exc)[:180]}"
        log.exception("Live appointment search failed")
        bot.send_message(chat_id, f"❌ سرچ مکمل نہ ہو سکی: {type(exc).__name__}: {str(exc)[:300]}")
    finally:
        SEARCH_LOCK.release()


@bot.message_handler(commands=["monitor"])
def start_monitor_cmd(message):
    if not allowed(message):
        return
    parts = (message.text or "").split()
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "استعمال: /monitor ID\nپہلے /selectclient سے کلائنٹ کی ID دیکھیں۔")
        return
    chat_id = message.chat.id
    if chat_id in MONITOR_EVENTS and not MONITOR_EVENTS[chat_id].is_set():
        bot.reply_to(message, "اس چیٹ میں نگرانی پہلے سے چل رہی ہے۔ روکنے کے لیے /stopmonitor دبائیں۔")
        return
    rec_id = int(parts[1])
    rec = next((r for r in list_records() if int(r["id"]) == rec_id), None)
    if not rec:
        bot.reply_to(message, "یہ کلائنٹ ID نہیں ملی۔ /selectclient سے فہرست دیکھیں۔")
        return
    stop_event = threading.Event()
    MONITOR_EVENTS[chat_id] = stop_event
    if USE_DB:
        persist_monitor(chat_id, rec_id)
    threading.Thread(target=_monitor_worker, args=(chat_id, rec, stop_event), daemon=True).start()
    bot.reply_to(message, f"🔔 نگرانی شروع ہوگئی: {rec.get('client_name','Client')} — {rec['city']}۔ کم از کم وقفہ 15 منٹ ہے؛ دستیابی ملتے ہی Telegram اطلاع بھیجے گا۔ روکنے کے لیے /stopmonitor۔")


def _monitor_worker(chat_id, rec, stop_event):
    # Conservative minimum interval to avoid excessive requests to the official portal.
    # Operators may increase the interval with MONITOR_INTERVAL_SECONDS, never reduce it below 15 min.
    try:
        configured = int(os.environ.get("MONITOR_INTERVAL_SECONDS", "900"))
    except (TypeError, ValueError):
        configured = 900
    interval = max(900, configured)
    seen_slots = set()
    try:
        while not stop_event.is_set():
            if stop_event.is_set():
                break
            _run_live_search(chat_id, rec, seen_slots=seen_slots)
            if stop_event.wait(interval):
                break
        bot.send_message(chat_id, "⏹ نگرانی بند ہوگئی۔ دوبارہ شروع کرنے کے لیے /monitor ID استعمال کریں۔")
    except Exception:
        log.exception("Monitor worker failed")
        try:
            bot.send_message(chat_id, "نگرانی میں خرابی آگئی؛ /health سے اسٹیٹس دیکھیں۔")
        except Exception:
            pass
    finally:
        if MONITOR_EVENTS.get(chat_id) is stop_event:
            MONITOR_EVENTS.pop(chat_id, None)


@bot.message_handler(commands=["stopmonitor"])
def stop_monitor_cmd(message):
    if not allowed(message):
        return
    event = MONITOR_EVENTS.get(message.chat.id)
    if event and not event.is_set():
        event.set()
        remove_persisted_monitor(message.chat.id)
        bot.reply_to(message, "نگرانی روکنے کی درخواست دے دی گئی ہے۔ موجودہ سرچ ختم ہوتے ہی بند ہو جائے گی۔")
    else:
        bot.reply_to(message, "اس وقت اس چیٹ میں نگرانی نہیں چل رہی۔")


@bot.callback_query_handler(func=lambda call: call.data.startswith("pick:"))
def select_client_callback(call):
    if not allowed_chat(call.message.chat.id):
        bot.answer_callback_query(call.id)
        return
    try:
        rec_id = int(call.data.split(":", 1)[1])
        rec = next((r for r in list_records() if int(r["id"]) == rec_id), None)
        if not rec:
            bot.answer_callback_query(call.id, "Client not found")
            return
        bot.answer_callback_query(call.id, "Live search started")
        threading.Thread(target=_run_live_search, args=(call.message.chat.id, rec), daemon=True).start()
    except Exception:
        log.exception("Client selection failed")
        bot.answer_callback_query(call.id, "Selection error")


@bot.message_handler(commands=["addprofile"])
def add_profile_start(message):
    if not allowed(message):
        return
    STATE[message.chat.id] = {"step": "profile_label"}
    bot.send_message(message.chat.id, "کلائنٹ کا لیبل لکھیں، مثلاً Client A:")


PROFILE_STEPS = [
    ("profile_full_name", "کلائنٹ کا پورا نام (پاسپورٹ/NIE کے مطابق) لکھیں:"),
    ("profile_doc_type", "دستاویز کی قسم لکھیں: NIE، DNI یا PASSPORT:"),
    ("profile_doc_value", "NIE/DNI/Passport نمبر لکھیں:"),
    ("profile_country", "پیدائش کا ملک (سرکاری پورٹل کے ہسپانوی نام کے مطابق، مثلاً PAKISTAN):"),
    ("profile_birth", "پیدائش کا سال لکھیں، مثلاً 1990:"),
    ("profile_phone", "اسپین کا فون نمبر لکھیں:"),
    ("profile_email", "ای میل ایڈریس لکھیں:"),
]


@bot.message_handler(func=lambda msg: msg.chat.id in STATE and str(STATE[msg.chat.id].get("step", "")).startswith("profile_"))
def add_profile_step(message):
    if not allowed(message):
        return
    chat_id = message.chat.id
    value = (message.text or "").strip()
    if not value:
        bot.reply_to(message, "یہ خانہ خالی نہ چھوڑیں۔")
        return
    data = STATE[chat_id]
    step = data["step"]
    if step == "profile_label":
        data["client_name"] = value[:80]
        data["step"] = "profile_city"
        kb = types.InlineKeyboardMarkup(row_width=2)
        for city in CITIES:
            kb.add(types.InlineKeyboardButton(city, callback_data=f"profilecity:{city}"))
        bot.send_message(chat_id, "صوبہ/شہر منتخب کریں (پہلے Barcelona، پھر Madrid):", reply_markup=kb)
        return
    if step == "profile_city":
        return
    if step == "profile_full_name":
        data["full_name"] = value
    elif step == "profile_doc_type":
        v = value.upper()
        if v not in ("NIE", "DNI", "PASSPORT"):
            bot.reply_to(message, "صرف NIE، DNI یا PASSPORT لکھیں۔")
            return
        data["doc_type"] = v.lower()
    elif step == "profile_doc_value": data["doc_value"] = value
    elif step == "profile_country": data["country"] = value.upper()
    elif step == "profile_birth":
        if not re.fullmatch(r"\d{4}", value):
            bot.reply_to(message, "صرف چار ہندسوں والا سال لکھیں، مثلاً 1990۔")
            return
        data["year_of_birth"] = value
    elif step == "profile_phone": data["phone"] = value
    elif step == "profile_email":
        if "@" not in value:
            bot.reply_to(message, "درست ای میل لکھیں۔")
            return
        data["email"] = value
        payload = {k: data[k] for k in ("full_name", "doc_type", "doc_value", "country", "year_of_birth", "phone", "email")}
        payload["profile_version"] = 1
        rec_id = save_record(data["client_name"], "Huellas", data["city"], json.dumps(payload, ensure_ascii=False))
        label, city = data["client_name"], data["city"]
        STATE.pop(chat_id, None)
        bot.send_message(chat_id, f"✅ {label} محفوظ ہوگیا (ID {rec_id})۔ اب /selectclient دبائیں۔\nشہر: {city}\nنوٹ: شناختی معلومات حساس ہیں؛ صرف اپنی اجازت والے کلائنٹ کا ڈیٹا درج کریں۔")
        return
    # Advance to next profile question
    current = [x[0] for x in PROFILE_STEPS]
    try:
        idx = current.index(step)
        next_step, prompt = PROFILE_STEPS[idx + 1]
        data["step"] = next_step
        bot.send_message(chat_id, prompt)
    except (ValueError, IndexError):
        bot.send_message(chat_id, "مرحلہ مکمل نہیں ہوا۔ /cancel کرکے /addprofile دوبارہ شروع کریں۔")


@bot.callback_query_handler(func=lambda call: call.data.startswith("profilecity:"))
def profile_city_callback(call):
    chat_id = call.message.chat.id
    if not allowed_chat(chat_id) or chat_id not in STATE or STATE[chat_id].get("step") != "profile_city":
        bot.answer_callback_query(call.id)
        return
    city = call.data.split(":", 1)[1]
    STATE[chat_id]["city"] = city
    STATE[chat_id]["step"] = "profile_full_name"
    bot.answer_callback_query(call.id, city)
    bot.send_message(chat_id, PROFILE_STEPS[0][1])


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

@app.route("/browser-test")
def browser_test():
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto("https://example.com")
            title = page.title()
            browser.close()
            return f"Chromium OK: {title}", 200
    except Exception as e:
        return f"Chromium ERROR: {str(e)}", 500

def restore_persisted_monitors():
    """Resume saved monitor jobs after a Render restart when PostgreSQL is configured."""
    if not USE_DB:
        log.warning("Monitor restart recovery unavailable: DATABASE_URL is not configured.")
        return
    records = {int(r["id"]): r for r in list_records()}
    for chat_id, client_id in load_persisted_monitors():
        rec = records.get(int(client_id))
        if not rec:
            remove_persisted_monitor(chat_id)
            continue
        if int(chat_id) in MONITOR_EVENTS:
            continue
        stop_event = threading.Event()
        MONITOR_EVENTS[int(chat_id)] = stop_event
        threading.Thread(target=_monitor_worker, args=(int(chat_id), rec, stop_event), daemon=True).start()
        try:
            bot.send_message(int(chat_id), f"♻️ Render دوبارہ شروع ہونے کے بعد نگرانی بحال ہوگئی: {rec.get('client_name','Client')} — {rec['city']}۔")
        except Exception:
            log.exception("Could not send monitor-resumed message.")

# ---------------- STARTUP ----------------
init_storage()

if __name__ == "__main__":
    restore_persisted_monitors()
    configure_webhook()
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
            )

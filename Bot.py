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

# Global counters for health stats
LAST_CHECK_TIME = "Nahi hua"
LAST_CHECK_STATUS = "Nahi chala"
SUCCESSFUL_CHECKS = 0

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
                res = cur.fetchone()[0]
                conn.commit()
                return res
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
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
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
        try:
            conn = psycopg2.connect(DATABASE_URL, connect_timeout=10)
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
            f"url: {url}
"
            f"pending_update_count: {info.pending_update_count}
"
            f"last_error_date: {info.last_error_date}
"
            f"last_error_message: {info.last_error_message}
"
            f"storage: {'DATABASE' if USE_DB else 'MEMORY'}
"
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
        "/testnotify - Notification test\n"
        "/status - Database status\n"
        "/health - Bot health checker\n"
        "/cancel - Entry cancel"
    )

@bot.message_handler(commands=["status"])
def status(message):
    if not allowed(message):
        return
    if USE_DB:
        bot.reply_to(message, "✅ Data PostgreSQL Database mein save ho raha hai.")
    else:
        bot.reply_to(message, "⚠️ Memory mode active hai. DATABASE_URL check karein.")

@bot.message_handler(commands=["health"])
def health_status(message):
    if not allowed(message):
        return
    status_text = (
        f"📊 **BOT HEALTH STATUS**\n\n"
        f"🔹 **Watcher Loop:** Active 🟢\n"
        f"🔹 **Total Checks:** {SUCCESSFUL_CHECKS}\n"
        f"🔹 **Last Checked:** {LAST_CHECK_TIME}\n"
        f"🔹 **Last Status:** {LAST_CHECK_STATUS}\n"
        f"🔹 **Storage Mode:** {'DATABASE 🗄️' if USE_DB else 'MEMORY 🧠'}\n"
        f"🔹 **Anti-Block System:** Enabled ✅ (Random delays active)"
    )
    bot.reply_to(message, status_text, parse_mode="Markdown")

# ---------------- TELEGRAM NOTIFICATION ----------------
@bot.message_handler(commands=["testnotify"])
def test_notification(message):
    if not allowed(message):
        return
    try:
        target = admin_chat_id(message)
        bot.send_message(
            target,
            "🔔 LOUD TEST NOTIFICATION\n\n"
            "✅ Aapka High-Priority Sound alert system bilkul theek kaam kar raha hai!",
            disable_notification=False
        )
        if target != message.chat.id:
            bot.reply_to(message, "✅ Test notification admin ko bhej diya gaya.")
    except Exception:
        log.exception("Notification test failed")
        bot.reply_to(message, "❌ Notification nahi bheja ja saka.")

# ---------------- CLIENTS ----------------
@bot.message_handler(commands=["addclient"])
def add_client(message):
    if not allowed(message):
        return
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
        return
    try:
        records = list_records()
    except Exception:
        log.exception("Client list error")
        return bot.reply_to(message, "❌ Data load nahi ho saka.")

    if not records:
        return bot.reply_to(message, "📭 Koi client save nahi hai.")

    lines = [
        f"🆔 {r['id']} | {r['service']} | {r['city']}\nDetails: `{r['details']}`"
        for r in records
    ]
    send_long(message.chat.id, "📋 Saved Clients:\n\n" + "\n\n".join(lines))

@bot.message_handler(commands=["deleteclient"])
def delete_client(message):
    if not allowed(message):
        return
    parts = message.text.split()
    if len(parts) != 2 or not parts[1].isdigit():
        return bot.reply_to(message, "Aise likhein: /deleteclient 3")
    try:
        done = delete_record(int(parts[1]))
    except Exception:
        log.exception("Delete error")
        return bot.reply_to(message, "❌ Delete nahi ho saka.")

    bot.reply_to(
        message,
        "🗑️ Delete ho gaya." if done else "Is number ka client nahi mila."
    )

@bot.message_handler(commands=["checkslots"])
def check_slots(message):
    if not allowed(message):
        return
    bot.reply_to(
        message,
        "🇪🇸 Cita Fácil España\n\n"
        "1️⃣ Extranjería Official Portal:\n"
        "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus\n\n"
        "2️⃣ Padrón Badalona:\n"
        "https://www.badalona.cat/ca/oficina-virtual/cita-previa\n\n"
        "Abhi manually check karne ke liye /checknow likhein."
    )

# ---------------- SLOT WATCHER (BARCELONA & MADRID) ----------------
WATCH_URLS = {
    "Barcelona": "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus",
    "Madrid": "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus",
    "Badalona": "https://www.badalona.cat/ca/oficina-virtual/cita-previa"
}

NO_SLOT_TEXTS = ["no hay citas disponibles", "no hi ha cites disponibles"]
SLOT_TEXTS = ["seleccione una de las siguientes citas", "citas disponibles para", "cites disponibles per"]
BLOCK_TEXTS = ["access denied", "request rejected", "captcha"]
WATCH_STATE = {}

def check_page(url):
    global LAST_CHECK_TIME, LAST_CHECK_STATUS, SUCCESSFUL_CHECKS
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LAST_CHECK_TIME = now_str
    
    # Dynamic headers to minimize block risk
    user_agents = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ]
    
    headers = {
        "User-Agent": random.choice(user_agents),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }
    
    try:
        r = requests.get(url, headers=headers, timeout=20)
    except Exception as e:
        LAST_CHECK_STATUS = f"Error: {e}"
        return "unknown", f"Site khul nahi saki: {e}"

    if r.status_code != 200:
        LAST_CHECK_STATUS = f"Bad Code ({r.status_code})"
        return "unknown", f"Site ne code {r.status_code} diya"

    text = r.text.lower()
    SUCCESSFUL_CHECKS += 1
    
    if any(t in text for t in NO_SLOT_TEXTS):
        LAST_CHECK_STATUS = "No slots available"
        return "none", "Abhi slot nahi hai"
    if any(t in text for t in SLOT_TEXTS):
        LAST_CHECK_STATUS = "🔥 SLOT FOUND 🔥"
        return "slot", "Slot ke nishan mile"
    if any(t in text for t in BLOCK_TEXTS):
        LAST_CHECK_STATUS = "Blocked ⛔"
        return "unknown", "Site ne bot rok diya (captcha/block)"
        
    LAST_CHECK_STATUS = "Unclear Page content"
    return "unknown", "Page se saaf pata nahi chala"

def notify_admin_with_filter(city_name, url, note):
    if not ADMIN_ID:
        return
        
    # Get only clients matching this specific city for Auto-Fill / Quick Copy
    try:
        all_records = list_records()
        matching_clients = [r for r in all_records if city_name.lower() in r["city"].lower()]
    except Exception:
        matching_clients = []

    client_data_text = ""
    if matching_clients:
        client_data_text = f"\n\n📋 **{city_name} Ke Saved Clients (Click to Copy):**\n"
        for c in matching_clients:
            client_data_text += f"\n👤 `{c['details']}` ({c['service']})"
    else:
        client_data_text = f"\n\n📭 {city_name} ka koi client saved nahi hai."

    alert_message = (
        f"🚨🚨 **URGENT: SLOT MIL SAKTA HAI ({city_name})** 🚨🚨\n\n"
        f"📌 **Status:** {note}\n"
        f"🔗 **Link:** {url}"
        f"{client_data_text}\n\n"
        f"⚠️ Jaldi check karein, slots khatam ho jate hain!"
    )
    
    try:
        msg = bot.send_message(int(ADMIN_ID), alert_message, parse_mode="Markdown", disable_notification=False)
        bot.pin_chat_message(msg.chat.id, msg.message_id)
    except Exception:
        log.exception("Notify failed")

def watcher_loop():
    log.info("Slot watcher started with Anti-Block system")
    while True:
        for name, url in WATCH_URLS.items():
            try:
                status, note = check_page(url)
                prev = WATCH_STATE.get(name)
                log.info("Watch %s -> %s (%s)", name, status, note)

                if status == "slot" and prev != "slot":
                    notify_admin_with_filter(name, url, note)
                elif status == "unknown" and prev not in (None, "unknown"):
                    if ADMIN_ID:
                        bot.send_message(int(ADMIN_ID), f"⚠️ {name}: Check nahi ho saka.\n{note}")

                WATCH_STATE[name] = status
            except Exception:
                log.exception("Watcher error")
                
        # Anti-Block: Random delay between 4 to 7 minutes (240 to 420 seconds)
        delay = random.randint(240, 420)
        log.info(f"Anti-Block sleep active. Next check in {delay} seconds...")
        time.sleep(delay)

@bot.message_handler(commands=["checknow"])
def check_now(message):
    if not allowed(message):
        return
    bot.reply_to(message, "🔎 Live checking shuru... Ek minute rukiye...")
    lines = []
    for name, url in WATCH_URLS.items():
        status, note = check_page(url)
        lines.append(f"🔹 {name}: **{status}** - {note}")
        if status == "slot":
            notify_admin_with_filter(name, url, note)
    bot.reply_to(message, "🔎 **Abhi Ka Live Nateeja:**\n\n" + "\n".join(lines), parse_mode="Markdown")

# ---------------- BUTTONS ----------------
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
                return bot.answer_callback_query(call.id, "Pehle /addclient dabayein.")

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
                    "Client ka Naam aur NIE/ID likhein\n(Format: Name, NIE):"
                )
    except Exception:
        log.exception("Button handler error")
        bot.answer_callback_query(call.id, "Kuch galat hua. Dobara /addclient dabayein.")

# ---------------- TEXT STEPS ----------------
@bot.message_handler(
    func=lambda m: (m.chat.id in STATE and m.content_type == "text" and not m.text.startswith("/"))
)
def on_text(message):
    st = STATE[message.chat.id]
    text = message.text.strip()

    if st["step"] == "city_text":
        if not text or len(text) > 60:
            return bot.reply_to(message, "Shehar ka naam 60 harf tak likhein.")
        st["city"] = text
        st["step"] = "details"
        return bot.reply_to(
            message,
            f"Service: {st['service']} | Shehar: {st['city']}\n\n"
            "Client ka Naam aur NIE/ID likhein\n(Format: Name, NIE):"
        )

    if st["step"] == "details":
        if len(text) < 3 or len(text) > 300:
            return bot.reply_to(message, "Details 3 se 300 harf ke beech likhein.")
        try:
            rec_id = save_record(st["service"], st["city"], text)
        except Exception:
            log.exception("Save error")
            return bot.reply_to(message, "❌ Save nahi ho saka. Dobara bhejein.")

        STATE.pop(message.chat.id, None)
        bot.reply_to(
            message,
            f"✅ Save ho gaya! (ID {rec_id})\n📌 {st['service']} | {st['city']}\n📋 `{text}`",
            parse_mode="Markdown"
        )

@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(message):
    bot.reply_to(message, "Samajh nahi aaya. /start dabayein.")

# ---------------- STARTUP ----------------
init_storage()
configure_webhook()
threading.Thread(target=watcher_loop, daemon=True).start()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

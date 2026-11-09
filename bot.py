import os
import logging
import threading
from datetime import datetime, timezone

import telebot
from flask import Flask, request, abort

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except ImportError:
    psycopg2 = None
    RealDictCursor = None

# Render Environment Variables:
# BOT_TOKEN, BOT_URL, ADMIN_ID, DATABASE_URL
TOKEN = os.getenv("BOT_TOKEN", "").strip()
BOT_URL = os.getenv("BOT_URL", "").strip().rstrip("/")
ADMIN_ID_RAW = os.getenv("ADMIN_ID", "").strip()
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Add it in Render > Environment.")

ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("cita-bot")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

# Temporary conversation state. Client records themselves are stored in PostgreSQL
# when DATABASE_URL is configured.
waiting_for_client = set()
memory_clients = []
memory_lock = threading.Lock()

SERVICE_LINKS = {
    "NIE": "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus",
    "TIE": "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus",
    "Huellas": "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus",
    "Padrón": "https://www.badalona.cat/"
}

def is_admin(message):
    return ADMIN_ID is not None and message.chat.id == ADMIN_ID

def ensure_database():
    if not DATABASE_URL:
        log.warning("DATABASE_URL is not set; client records will be temporary.")
        return
    if psycopg2 is None:
        raise RuntimeError("psycopg2 is missing. Install psycopg2-binary.")
    with psycopg2.connect(DATABASE_URL, sslmode="require") as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cita_clients (
                    id SERIAL PRIMARY KEY,
                    telegram_user_id BIGINT NOT NULL,
                    client_name TEXT NOT NULL,
                    service TEXT NOT NULL,
                    city TEXT NOT NULL,
                    phone TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)
        conn.commit()

def db_add_client(user_id, name, service, city, phone):
    if DATABASE_URL:
        with psycopg2.connect(DATABASE_URL, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO cita_clients
                    (telegram_user_id, client_name, service, city, phone)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id
                """, (user_id, name, service, city, phone))
                new_id = cur.fetchone()[0]
            conn.commit()
        return new_id
    with memory_lock:
        new_id = len(memory_clients) + 1
        memory_clients.append({
            "id": new_id, "telegram_user_id": user_id,
            "client_name": name, "service": service,
            "city": city, "phone": phone
        })
        return new_id

def db_list_clients():
    if DATABASE_URL:
        with psycopg2.connect(DATABASE_URL, sslmode="require") as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("""
                    SELECT id, telegram_user_id, client_name, service, city, phone,
                           created_at
                    FROM cita_clients ORDER BY id DESC LIMIT 100
                """)
                return cur.fetchall()
    with memory_lock:
        return list(reversed(memory_clients[-100:]))

def db_delete_client(client_id):
    if DATABASE_URL:
        with psycopg2.connect(DATABASE_URL, sslmode="require") as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM cita_clients WHERE id = %s", (client_id,))
                deleted = cur.rowcount
            conn.commit()
        return deleted > 0
    with memory_lock:
        for item in memory_clients:
            if item["id"] == client_id:
                memory_clients.remove(item)
                return True
    return False

def clients_count():
    return len(db_list_clients())

@bot.message_handler(commands=["start"])
def start(message):
    bot.reply_to(
        message,
        "السلام علیکم! 🇪🇸\n"
        "Cita Previa مددگار بوٹ تیار ہے۔\n\n"
        "کمانڈز:\n"
        "/help - تمام کمانڈز\n"
        "/myid - اپنی Telegram ID\n"
        "/addclient - کلائنٹ شامل کریں\n"
        "/viewclients - محفوظ کلائنٹس (صرف ایڈمن)\n"
        "/deleteclient ID - کلائنٹ حذف کریں (صرف ایڈمن)\n"
        "/checkslots - سرکاری اپائنٹمنٹ لنکس\n"
        "/status - بوٹ کی حالت"
    )

@bot.message_handler(commands=["myid"])
def myid(message):
    bot.reply_to(message, f"آپ کی Telegram ID: {message.chat.id}")

@bot.message_handler(commands=["help"])
def help_command(message):
    bot.reply_to(
        message,
        "استعمال کا طریقہ:\n"
        "1) /addclient لکھیں۔\n"
        "2) اگلے پیغام میں یہ فارمیٹ بھیجیں:\n"
        "نام | سروس | شہر | فون (اختیاری)\n\n"
        "مثال:\n"
        "Ali | Huellas | Barcelona | 600123456\n\n"
        "سروس: NIE، TIE، Huellas یا Padrón\n"
        "کلائنٹس دیکھنے/حذف کرنے کے لیے ایڈمن اجازت ضروری ہے۔\n\n"
        "نوٹ: یہ ورژن ابھی سرکاری پورٹل سے خودکار slot availability چیک نہیں کرتا۔"
    )

@bot.message_handler(commands=["addclient"])
def addclient(message):
    waiting_for_client.add(message.chat.id)
    bot.reply_to(
        message,
        "کلائنٹ کی تفصیل ایک ہی پیغام میں بھیجیں:\n"
        "نام | سروس | شہر | فون (اختیاری)\n\n"
        "مثال: Ali | Huellas | Barcelona | 600123456\n"
        "منسوخ کرنے کے لیے /cancel لکھیں۔"
    )

@bot.message_handler(commands=["cancel"])
def cancel(message):
    waiting_for_client.discard(message.chat.id)
    bot.reply_to(message, "عمل منسوخ کر دیا گیا۔")

@bot.message_handler(commands=["viewclients"])
def viewclients(message):
    if not is_admin(message):
        bot.reply_to(message, "یہ کمانڈ صرف ایڈمن کے لیے ہے۔")
        return
    rows = db_list_clients()
    if not rows:
        bot.reply_to(message, "ابھی کوئی کلائنٹ محفوظ نہیں۔")
        return
    lines = ["محفوظ کلائنٹس (زیادہ سے زیادہ آخری 100):"]
    for row in rows:
        phone = row.get("phone") or "-"
        lines.append(
            f"ID {row['id']}: {row['client_name']} | "
            f"{row['service']} | {row['city']} | {phone}"
        )
    # Telegram message limit safety
    text = "\n".join(lines)
    for start_index in range(0, len(text), 3500):
        bot.send_message(message.chat.id, text[start_index:start_index + 3500])

@bot.message_handler(commands=["deleteclient"])
def deleteclient(message):
    if not is_admin(message):
        bot.reply_to(message, "یہ کمانڈ صرف ایڈمن کے لیے ہے۔")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].isdigit():
        bot.reply_to(message, "استعمال: /deleteclient 12")
        return
    if db_delete_client(int(parts[1])):
        bot.reply_to(message, "کلائنٹ حذف کر دیا گیا۔")
    else:
        bot.reply_to(message, "اس ID کا کلائنٹ نہیں ملا۔")

@bot.message_handler(commands=["checkslots"])
def checkslots(message):
    bot.reply_to(
        message,
        "سرکاری اپائنٹمنٹ پورٹل:\n"
        f"NIE / TIE / Huellas: {SERVICE_LINKS['NIE']}\n"
        f"Padrón (Badalona): {SERVICE_LINKS['Padrón']}\n\n"
        "ہر شہر کا Padrón الگ ہوتا ہے۔ یہ لنکس صرف سرکاری صفحات کھولتے ہیں؛ "
        "اس ورژن میں خودکار slot detection فعال نہیں ہے۔"
    )

@bot.message_handler(commands=["checknow"])
def checknow(message):
    bot.reply_to(
        message,
        "ابھی بوٹ نے کوئی حقیقی slot چیک نہیں کیا۔\n"
        "خودکار چیکنگ شامل کرنے کے لیے متعلقہ سرکاری پورٹل کا جائز، "
        "دستیاب طریقہ/API پہلے verify کرنا ہوگا۔ CAPTCHA یا حفاظتی رکاوٹ "
        "کو bypass نہیں کیا جائے گا۔\n\n"
        "فی الحال /checkslots سے سرکاری لنک کھولیں۔"
    )

@bot.message_handler(commands=["status", "health"])
def status(message):
    db_status = "PostgreSQL connected/configured" if DATABASE_URL else "Temporary memory only (DATABASE_URL missing)"
    bot.reply_to(
        message,
        "بوٹ چل رہا ہے۔\n"
        f"Database: {db_status}\n"
        f"Saved clients: {clients_count()}\n"
        "Automatic slot checker: ابھی فعال نہیں"
    )

@bot.message_handler(commands=["notify"])
def notify(message):
    if not is_admin(message):
        bot.reply_to(message, "یہ کمانڈ صرف ایڈمن کے لیے ہے۔")
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "استعمال: /notify آپ کا پیغام")
        return
    bot.reply_to(message, "پیغام صرف آپ کے ایڈمن چیٹ میں موصول ہوا؛ براڈکاسٹ ابھی شامل نہیں۔")

@bot.message_handler(func=lambda m: m.chat.id in waiting_for_client, content_types=["text"])
def receive_client(message):
    raw = message.text.strip()
    if raw.startswith("/"):
        return
    parts = [p.strip() for p in raw.split("|")]
    if len(parts) < 3 or not all(parts[:3]):
        bot.reply_to(message, "فارمیٹ درست نہیں۔ مثال:\nAli | Huellas | Barcelona | 600123456")
        return
    name, service, city = parts[:3]
    phone = parts[3] if len(parts) > 3 else ""
    allowed_services = {"nie", "tie", "huellas", "padrón", "padron"}
    if service.casefold() not in allowed_services:
        bot.reply_to(message, "سروس NIE، TIE، Huellas یا Padrón میں سے لکھیں۔")
        return
    if service.casefold() == "padron":
        service = "Padrón"
    elif service.casefold() == "nie":
        service = "NIE"
    elif service.casefold() == "tie":
        service = "TIE"
    elif service.casefold() == "huellas":
        service = "Huellas"
    try:
        new_id = db_add_client(message.chat.id, name, service, city, phone)
        waiting_for_client.discard(message.chat.id)
        storage = "PostgreSQL" if DATABASE_URL else "عارضی میموری"
        bot.reply_to(
            message,
            f"کلائنٹ محفوظ ہو گیا۔ ID: {new_id}\n"
            f"نام: {name}\nسروس: {service}\nشہر: {city}\n"
            f"محفوظ کرنے کی جگہ: {storage}"
        )
    except Exception:
        log.exception("Could not save client")
        bot.reply_to(message, "ڈیٹا محفوظ نہیں ہو سکا۔ Render logs اور DATABASE_URL چیک کریں۔")

@app.get("/")
def home():
    return "Cita Bot is running. Automatic appointment slot checking is not enabled.", 200

@app.get("/health")
def web_health():
    return {"status": "ok", "time_utc": datetime.now(timezone.utc).isoformat()}, 200

@app.post("/" + TOKEN)
def telegram_webhook():
    if request.headers.get("content-type") != "application/json":
        abort(403)
    update = telebot.types.Update.de_json(request.get_data().decode("utf-8"))
    bot.process_new_updates([update])
    return "OK", 200

def configure_webhook():
    if not BOT_URL:
        log.warning("BOT_URL missing; webhook was not configured.")
        return
    try:
        result = bot.set_webhook(url=f"{BOT_URL}/{TOKEN}")
        log.info("Webhook configured: %s", result)
    except Exception:
        log.exception("Webhook configuration failed")

try:
    ensure_database()
except Exception:
    log.exception("Database initialization failed. Bot will still start, but database features may fail.")

configure_webhook()

if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
    

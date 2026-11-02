import os
import logging

import telebot
from flask import Flask, request

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("citabot")

# ---------------------------------------------------------------- config
TOKEN = (os.environ.get("BOT_TOKEN") or "").strip()
BOT_URL = (os.environ.get("BOT_URL") or "").strip().rstrip("/")
ADMIN_ID = (os.environ.get("ADMIN_ID") or "").strip()  # optional

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

CLIENTS_DATA = {}       # client_id -> text (memory only: lost on restart)
WAITING_CLIENT = set()  # chat ids that are in the middle of /addclient


def allowed(message):
    """If ADMIN_ID is set, only that chat may use the bot."""
    return (not ADMIN_ID) or str(message.chat.id) == ADMIN_ID


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
    # Always answer 200 so Telegram does not keep retrying the same update
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
    """Shows what Telegram sees, with the token hidden."""
    try:
        info = bot.get_webhook_info()
        url = (info.url or "").replace(TOKEN, "<TOKEN>")
        return (
            f"url: {url}\n"
            f"pending_update_count: {info.pending_update_count}\n"
            f"last_error_date: {info.last_error_date}\n"
            f"last_error_message: {info.last_error_message}\n"
        ), 200, {"Content-Type": "text/plain; charset=utf-8"}
    except Exception as e:
        log.exception("get_webhook_info failed")
        return f"Error: {e}", 500


# ---------------------------------------------------------------- handlers
@bot.message_handler(commands=["myid"])
def my_id(message):
    log.info("handler: /myid")
    bot.reply_to(message, f"Aapki chat ID: {message.chat.id}")


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    log.info("handler: /start or /help")
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")
    bot.reply_to(
        message,
        "🇪🇸 Cita Fácil España Bot Active Hai! 🇪🇸\n\n"
        "👉 /addclient - Client ka data save karne ke liye\n"
        "👉 /viewclients - Saved clients dekhne ke liye\n"
        "👉 /checkslots - Slots alert check karne ke liye\n"
        "👉 /cancel - Chal rahi entry rokne ke liye",
    )


@bot.message_handler(commands=["addclient"])
def add_client(message):
    log.info("handler: /addclient")
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")
    WAITING_CLIENT.add(message.chat.id)
    bot.reply_to(message, "Client ka data likhein (Format: Name, NIE, City):")


@bot.message_handler(commands=["cancel"])
def cancel(message):
    WAITING_CLIENT.discard(message.chat.id)
    bot.reply_to(message, "Theek hai, cancel kar diya.")


@bot.message_handler(commands=["viewclients"])
def view_clients(message):
    log.info("handler: /viewclients")
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")
    if CLIENTS_DATA:
        lines = "\n".join(f"👤 {cid}: {d}" for cid, d in CLIENTS_DATA.items())
        bot.reply_to(message, f"📋 Aapka Saved Data:\n\n{lines}")
    else:
        bot.reply_to(message, "📭 Abhi koi data save nahi hai.")
@bot.message_handler(commands=["checkslots"])
deqf check_slots(message):
    if not allowed(message):
        return bot.reply_to(message, "Access nahi hai.")

bot.reply_to(
    message,
    "🇪🇸 Cita Facil Espana\n\n"
    "NIE / TIE / Huellas official portal:\n"
    "https://sede.administracionespublicas.gob.es/pagina/index/directorio/icpplus\n\n"
    "Padron appointments (Badalona):\n"
    "https://www.badalona.cat/ca/oficina-virtual/cita-previa\n\n"
    "Abhi automatic slot checking active nahi hai. "
    "Official website par availability confirm karein."
)

    



    
    


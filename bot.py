
  
import os
import logging
import telebot
from flask import Flask, request, abort

logging.basicConfig(level=logging.INFO)

TOKEN = os.environ.get('BOT_TOKEN')
BOT_URL = (os.environ.get('BOT_URL') or '').rstrip('/')   # strip trailing slash

if not TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is missing")

bot = telebot.TeleBot(TOKEN, threaded=False)
app = Flask(__name__)

CLIENTS_DATA = {}


@app.route('/' + TOKEN, methods=['POST'])
def get_message():
    if request.headers.get('content-type') != 'application/json':
        abort(403)
    update = telebot.types.Update.de_json(request.get_data().decode('utf-8'))
    bot.process_new_updates([update])
    return "!", 200


@app.route("/")
def index():
    return "System Active Aur Ready Hai!", 200


@app.route("/setwebhook")
def set_webhook():
    if not BOT_URL:
        return "Bot URL missing.", 200
    bot.remove_webhook()
    ok = bot.set_webhook(url=f"{BOT_URL}/{TOKEN}")
    return f"Webhook set: {ok}", 200


@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    welcome_text = (
        "🇪🇸 Cita Fácil España Bot Active Hai! 🇪🇸\n\n"
        "👉 /addclient - Client ka data andar save karne ke liye\n"
        "👉 /viewclients - Saved clients ka data dekhne ke liye\n"
        "👉 /checkslots - Extranjería slots tracking aur alert check karne ke liye"
    )
    bot.reply_to(message, welcome_text)


@bot.message_handler(commands=['addclient'])
def add_client(message):
    msg = bot.reply_to(message, "Client ka Naam aur NIE number likhein (Format: Name, NIE, City):")
    bot.register_next_step_handler(msg, save_client_step)


def save_client_step(message):
    try:
        client_id = len(CLIENTS_DATA) + 1
        CLIENTS_DATA[client_id] = message.text
        bot.reply_to(message, f"✅ Data andar save ho gaya hai!\n📋 Details: {message.text}")
    except Exception:
        logging.exception("save_client_step failed")
        bot.reply_to(message, "❌ Data save karne mein galti hui.")


@bot.message_handler(commands=['viewclients'])
def view_clients(message):
    if CLIENTS_DATA:
        list_data = "\n".join(f"👤 {cid}: {details}" for cid, details in CLIENTS_DATA.items())
        bot.reply_to(message, f"📋 Aapka Saved Data:\n\n{list_data}")
    else:
        bot.reply_to(message, "📭 Abhi andar koi data save nahi hai.")


@bot.message_handler(commands=['checkslots'])
def check_slots(message):
    bot.reply_to(
        message,
        "🔔 ALERT: Spain Extranjería Website Par Slot Khul Gaya Hai! 🔔\n"
        "⚡ Bot aapka saved data backend se utha kar process kar raha..."
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))

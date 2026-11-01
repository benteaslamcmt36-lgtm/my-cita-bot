import os
import telebot
from flask import Flask, request

# Telegram Bot Token config
TOKEN = os.environ.get('BOT_TOKEN')
bot = telebot.TeleBot(TOKEN)

# Flask Server (Render par 24 ghante chalane ke liye zaroori hai)
app = Flask(__name__)

@app.route('/' + TOKEN, methods=['POST'])
def getMessage():
    json_string = request.get_data().decode('utf-8')
    update = telebot.types.Update.de_json(json_string)
    bot.process_new_updates([update])
    return "!", 200

@app.route("/")
def webhook():
    bot.remove_webhook()
    # Note: URL baad mein Render dashboard se milne par environment variable mein set karenge
    BOT_URL = os.environ.get('BOT_URL')
    if BOT_URL:
        bot.set_webhook(url=BOT_URL + '/' + TOKEN)
        return "Webhook set kamyabi se!", 200
    return "Bot running, but URL not set yet.", 200

# Bot ke Messages ka Reply
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "As-salamu alaykum! Aapka naya Cita Bot Render par 100% free aur 24 ghante active hai. Main aapki kya madad kar sakta hoon?")

@bot.message_handler(func=lambda message: True)
def echo_all(message):
    bot.reply_to(message, f"Aapne kaha: {message.text}. Main jald hi aapko Cita ki details check karke batata hoon!")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)))
  

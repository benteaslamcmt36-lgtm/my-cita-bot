        
def send_welcome(message):
    log.info("handler: /start or /help")
    if not allowed(message):
        return bot.reply_to(message, "⛔ Access nahi hai.")
    bot.reply_to(
        message,
        "🇪🇸 Cita Fácil España Bot Active Hai! 🇪🇸\n\n"
        "👉 /addclient - Client ka data save karne ke liye\n"
        "👉 /viewclients - Saved clients dekhne ke liye\n"
        "👉 /checkslots - Official links aur slot info\n"
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


@bot.message_handler(
    func=lambda m: m.chat.id in WAITING_CLIENT
    and m.content_type == "text"
    and not m.text.startswith("/")
)
def save_client(message):
    log.info("handler: save client entry")
    parts = [p.strip() for p in message.text.split(",")]
    if len(parts) != 3 or not all(parts):
        return bot.reply_to(message, "Format galat hai. Dobara likhein: Name, NIE, City")
    client_id = len(CLIENTS_DATA) + 1
    CLIENTS_DATA[client_id] = message.text
    WAITING_CLIENT.discard(message.chat.id)
    bot.reply_to(message, f"✅ Save ho gaya!\n📋 {message.text}")


@bot.message_handler(func=lambda m: True, content_types=["text"])
def fallback(message):
    log.info("handler: fallback")
    bot.reply_to(message, "Samajh nahi aaya. /start dabayein.")


# Set the webhook once when the app starts (works with gunicorn and python)
configure_webhook()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))



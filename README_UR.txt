CITA FACIL — اپڈیٹ شدہ پروجیکٹ

فائلیں:
1) bot.py — Telegram bot اور webhook
2) cita_engine.py — سرکاری پورٹل پر دستیابی پڑھنے کا انجن
3) requirements.txt — Python dependencies

اہم:
- دونوں .py فائلیں ایک ہی فولڈر میں اپلوڈ کریں۔ صرف bot.py کافی نہیں، کیونکہ یہ cita_engine.py کو import کرتی ہے۔
- requirements.txt بھی اسی فولڈر میں رکھیں۔
- موجودہ Render Environment Variables (BOT_TOKEN, BOT_URL, ADMIN_ID, DATABASE_URL وغیرہ) کو نہ مٹائیں۔
- MONITOR_INTERVAL_SECONDS اختیاری ہے؛ کم از کم 900 سیکنڈ (15 منٹ) لاگو ہوں گے۔
- CITA_OFFICE_VALUE اختیاری ہے۔ اگر خالی ہو تو انجن فہرست میں پہلا دستیاب دفتر منتخب کرے گا؛ اب random دفتر منتخب نہیں ہوگا۔
- /monitor ID مسلسل نگرانی شروع کرتا ہے؛ /stopmonitor اسے روکتا ہے۔ /selectclient صرف ایک سرچ کرتا ہے۔
- نگرانی صرف اسی وقت جاری رہ سکتی ہے جب Render کا service process چلتا رہے۔ Free/sleeping service، restart یا deploy کے بعد thread بند ہو سکتا ہے؛ ایسی صورت میں /monitor ID دوبارہ چلائیں۔ مستقل 24/7 کے لیے Render پر always-on paid service یا الگ background worker درکار ہو سکتا ہے۔
- سرکاری ویب سائٹ کی رفتار/حدود کے احترام کے لیے خودکار سرچ ہر 15 منٹ سے زیادہ تیز نہیں ہوگی۔
- CAPTCHA، SMS verification، slot reservation اور آخری confirmation خودکار نہیں ہیں۔ صارف خود سرکاری پورٹل پر مکمل کرے گا۔
- پرانے کیلنڈر میں اگر صرف تاریخ نظر آئے تو بوٹ تاریخ کی اطلاع دے گا اور واضح کرے گا کہ وقت ابھی ظاہر نہیں ہوا۔
- دستیابی لمحہ بہ لمحہ بدل سکتی ہے؛ Telegram alert بکنگ کی ضمانت نہیں ہے۔

Render start command (اگر آپ کا موجودہ setup یہی استعمال کرتا ہے):
python bot.py

اس اپڈیٹ میں اصل uploaded source فائلیں تبدیل نہیں کی گئیں؛ یہ نئی فائلیں ہیں۔

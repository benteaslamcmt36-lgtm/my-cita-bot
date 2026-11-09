# Cita Previa Telegram Bot

Telegram client manager with a Selenium-based Toma de Huellas search workflow for Barcelona and Madrid.

- Structured client intake through `/addprofile`.
- Select a saved client through `/selectclient`.
- Selenium opens the official Cita Previa flow, fills identity/contact fields, selects an office and reads date/time options when exposed by the portal.
- Results are sent to Telegram.

## Safety and verification
CAPTCHA is not solved or bypassed. The bot stops before choosing/reserving a slot, SMS verification, or final booking confirmation. A security challenge, portal change, network issue or rate limit can stop a run. No results in one run do not prove that no appointments exist. Python syntax is checked, but live government-portal end-to-end behavior has not been verified.

Only enter client identity data with authorization. Protect Telegram and database access.

See `SETUP_URDU.txt` for deployment instructions. Dockerfile installs Chromium and ChromeDriver.

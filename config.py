import os
from zoneinfo import ZoneInfo

USERNAME = os.getenv("LEM_USERNAME")
PASSWORD = os.getenv("LEM_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHATS = [c.strip() for c in os.getenv("CHAT_IDS", "").split(",") if c.strip()]
ADMIN_ID = os.getenv("ADMIN_ID")

LOGIN_URL = "http://login.lemsystem.cl/"
PANEL_URL = "http://optimus.lemsystem.cl/LemSense.php"
AGROCLIMA_URL = "https://www.agroclima.cl/InfoInforme/Evapotranspiracion?codigo=270026"

CHECK_INTERVAL = 120
REPORTE_INTERVAL = 3600
RESTART_BROWSER_INTERVAL = 86400

FACTOR_LAVADO = 0.10
LLUVIA_EFECTIVA_MM = 0.0

CHILE_TZ = ZoneInfo("America/Santiago")
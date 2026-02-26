import os
import re
import asyncio
import time
from datetime import datetime
from flask import Flask
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException
from telegram.ext import Application, CommandHandler
from telegram import Bot

# =============================
# CONFIG
# =============================
USERNAME = os.getenv("LEM_USERNAME", "8.496.887-0")
PASSWORD = os.getenv("LEM_PASSWORD", "8496887")
TOKEN = os.getenv("TELEGRAM_TOKEN", "TU_TOKEN")
CHATS = [c.strip() for c in os.getenv("CHAT_IDS", "123456").split(",") if c.strip()]

LOGIN_URL = "http://login.lemsystem.cl/"
PANEL_URL = "http://optimus.lemsystem.cl/LemSense.php"

# =============================
# UTILIDADES
# =============================
def ahora():
    return datetime.now().strftime("%d/%m/%Y %H:%M:%S")

def estado_caudal(c):
    if c == 0:
        return "DETENIDO", "🔴"
    if c < 10:
        return "CRÍTICO", "🔴"
    if c < 30:
        return "BAJO", "🟠"
    return "NORMAL", "🟢"

# =============================
# MONITOR
# =============================
class Monitor:

    def __init__(self):
        self.driver = None
        self.ultimos = {}
        self.alertas = {}
        self.ultimo_reporte = 0
        self.bot = Bot(TOKEN)

    # -------------------------
    # DRIVER
    # -------------------------
    def _crear_driver(self):
        options = Options()
        for arg in [
            "--headless=new",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1920,1080",
            "--page-load-strategy=eager"
        ]:
            options.add_argument(arg)

        options.binary_location = "/usr/bin/google-chrome"
        service = Service("/usr/local/bin/chromedriver")

        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(60)
        return driver

    async def crear_driver(self):
        self.driver = await asyncio.to_thread(self._crear_driver)

    # -------------------------
    # LOGIN
    # -------------------------
    def _login(self):
        self.driver.get(LOGIN_URL)
        time.sleep(3)
        self.driver.find_element(By.XPATH, "//input[@placeholder='Usuario']").send_keys(USERNAME)
        self.driver.find_element(By.XPATH, "//input[@placeholder='Contraseña']").send_keys(PASSWORD)
        self.driver.find_element(By.ID, "loading").click()
        time.sleep(5)

    async def login(self):
        await asyncio.to_thread(self._login)
        await self.enviar(f"🤖 Bot iniciado\n📅 {ahora()}")

    # -------------------------
    # TELEGRAM
    # -------------------------
    async def enviar(self, texto):
        for chat in CHATS:
            try:
                await self.bot.send_message(chat_id=chat, text=texto, parse_mode="HTML")
            except Exception as e:
                print(f"Error Telegram {chat}: {e}")

    # -------------------------
    # PROCESAR ALERTAS
    # -------------------------
    async def procesar_alerta(self, nombre, caudal, anterior):
        estado, emoji = estado_caudal(caudal)
        ahora_ts = time.time()

        # alertas repetidas
        if estado in ["DETENIDO", "CRÍTICO"]:
            ultima = self.alertas.get(nombre, 0)
            if ahora_ts - ultima >= 120:
                await self.enviar(
                    f"<b>{emoji} {estado}</b>\n\n"
                    f"<b>Pozo:</b> {nombre}\n"
                    f"<b>Caudal:</b> {caudal} L/s\n"
                    f"<b>📅 {ahora()}</b>"
                )
                self.alertas[nombre] = ahora_ts

        # cambio de estado
        elif anterior is not None:
            estado_ant, _ = estado_caudal(anterior)
            if estado != estado_ant:
                await self.enviar(
                    f"<b>{emoji} CAMBIO DE ESTADO</b>\n\n"
                    f"<b>Pozo:</b> {nombre}\n"
                    f"<b>Nuevo estado:</b> {estado}\n"
                    f"<b>Caudal:</b> {caudal} L/s\n"
                    f"<b>📅 {ahora()}</b>"
                )

    # -------------------------
    # SCRAPING
    # -------------------------
    def _obtener_datos(self):
        self.driver.get(PANEL_URL)
        time.sleep(5)

        contenedor = self.driver.find_element(By.ID, "insidethepopup_alerta")
        pozos = contenedor.find_elements(By.CLASS_NAME, "col-lg-2")

        resultados = {}

        for pozo in pozos:
            texto = pozo.text
            nombre = texto.split("\n")[0]
            match = re.search(r"Caudal:\s*([0-9\.]+)", texto)
            if match:
                resultados[nombre] = float(match.group(1))

        return resultados

    async def verificar(self):
        try:
            datos = await asyncio.to_thread(self._obtener_datos)

            for nombre, caudal in datos.items():
                anterior = self.ultimos.get(nombre)
                self.ultimos[nombre] = caudal
                await self.procesar_alerta(nombre, caudal, anterior)

            await self.reporte_horario()

        except TimeoutException:
            print("Timeout, reiniciando driver...")
            await self.crear_driver()
            await self.login()

    # -------------------------
    # REPORTE
    # -------------------------
    async def reporte_horario(self):
        if time.time() - self.ultimo_reporte < 3600:
            return

        det = sum(1 for c in self.ultimos.values() if c == 0)
        cri = sum(1 for c in self.ultimos.values() if 0 < c < 10)
        baj = sum(1 for c in self.ultimos.values() if 10 <= c < 30)
        nor = sum(1 for c in self.ultimos.values() if c >= 30)

        msg = (
            f"<b>📊 REPORTE HORARIO</b>\n\n"
            f"🔴 Detenidos: {det}\n"
            f"🔴 Críticos: {cri}\n"
            f"🟠 Bajos: {baj}\n"
            f"🟢 Normales: {nor}\n\n"
            f"<b>📅 {ahora()}</b>"
        )

        await self.enviar(msg)
        self.ultimo_reporte = time.time()

    # -------------------------
    # LOOP PRINCIPAL
    # -------------------------
    async def loop(self):
        await self.crear_driver()
        await self.login()

        while True:
            await self.verificar()
            await asyncio.sleep(120)


# =============================
# TELEGRAM COMMANDS
# =============================
async def cmd_caudales(update, context):
    monitor: Monitor = context.bot_data["monitor"]

    if not monitor.ultimos:
        await update.message.reply_text("🔄 Cargando datos...")
        return

    msg = "<b>📊 ESTADO ACTUAL</b>\n\n"
    for n, c in monitor.ultimos.items():
        estado, emoji = estado_caudal(c)
        msg += f"<b>{n}:</b> {c} L/s - {emoji} {estado}\n"

    msg += f"\n🕐 {ahora()}"
    await update.message.reply_text(msg, parse_mode="HTML")


# =============================
# FLASK HEALTH
# =============================
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot activo"

@app.route("/health")
def health():
    return "OK", 200


# =============================
# MAIN ASYNC
# =============================
async def main():
    monitor = Monitor()

    telegram_app = Application.builder().token(TOKEN).build()
    telegram_app.bot_data["monitor"] = monitor

    telegram_app.add_handler(CommandHandler("caudales", cmd_caudales))

    # iniciar monitor en background
    asyncio.create_task(monitor.loop())

    # iniciar telegram
    await telegram_app.initialize()
    await telegram_app.start()
    await telegram_app.updater.start_polling()

    # iniciar flask en thread
    loop = asyncio.get_running_loop()
    loop.run_in_executor(None, lambda: app.run(host="0.0.0.0", port=5000, use_reloader=False))

    await telegram_app.updater.idle()


if __name__ == "__main__":
    asyncio.run(main())
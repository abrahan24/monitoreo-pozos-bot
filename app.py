import os
import re
import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from flask import Flask
from playwright.async_api import async_playwright
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ==========================
# CONFIGURACIÓN
# ==========================

USERNAME = os.getenv("LEM_USERNAME")
PASSWORD = os.getenv("LEM_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHATS = [c.strip() for c in os.getenv("CHAT_IDS", "").split(",") if c.strip()]

LOGIN_URL = "http://login.lemsystem.cl/"
PANEL_URL = "http://optimus.lemsystem.cl/LemSense.php"

CHECK_INTERVAL = 120
REPORTE_INTERVAL = 3600
RESTART_BROWSER_INTERVAL = 21600  # 6 horas


# ==========================
# UTILIDADES
# ==========================

def ahora():
    return datetime.now(ZoneInfo("America/Santiago")).strftime("%d/%m/%Y %H:%M:%S")


def estado_caudal(valor):
    if valor == 0:
        return "DETENIDO", "🔴"
    if valor < 10:
        return "CRÍTICO", "🔴"
    if valor < 30:
        return "BAJO", "🟠"
    return "NORMAL", "🟢"


# ==========================
# MONITOR
# ==========================

class Monitor:

    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        self.ultimos = {}
        self.alertas = {}
        self.ultimo_reporte = 0
        self.inicio_browser = 0

    async def iniciar(self):
        if self.browser:
            await self.browser.close()

        self.playwright = await async_playwright().start()

        self.browser = await self.playwright.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu"
            ]
        )

        self.context = await self.browser.new_context()
        self.page = await self.context.new_page()

        await self.login()
        self.inicio_browser = time.time()
        print("🟢 Navegador iniciado correctamente")

    async def login(self):
        print("🔐 Iniciando sesión...")
        await self.page.goto(LOGIN_URL, timeout=60000)

        await self.page.fill("input[placeholder='Usuario']", USERNAME)
        await self.page.fill("input[placeholder='Contraseña']", PASSWORD)
        await self.page.click("#loading")

        await self.page.wait_for_timeout(5000)

    async def obtener_datos(self):
        await self.page.goto(PANEL_URL, timeout=60000)
        await self.page.wait_for_timeout(5000)

        # Si volvió al login, reloguear
        if "login" in self.page.url.lower():
            print("⚠ Sesión expirada. Reintentando login...")
            await self.login()
            await self.page.goto(PANEL_URL, timeout=60000)
            await self.page.wait_for_timeout(5000)

        elementos = await self.page.query_selector_all("#insidethepopup_alerta .col-lg-2")

        datos = {}

        for el in elementos:
            texto = await el.inner_text()
            nombre = texto.split("\n")[0]
            match = re.search(r"Caudal:\s*([0-9\.]+)", texto)

            if match:
                datos[nombre] = float(match.group(1))

        return datos

    async def enviar(self, app: Application, mensaje: str):
        for chat in CHATS:
            try:
                await app.bot.send_message(
                    chat_id=chat,
                    text=mensaje,
                    parse_mode="HTML"
                )
            except Exception as e:
                print("Error Telegram:", e)

    async def procesar_alertas(self, app, nombre, caudal, anterior):
        estado, emoji = estado_caudal(caudal)
        ahora_ts = time.time()

        if estado in ["DETENIDO", "CRÍTICO"]:
            ultima_alerta = self.alertas.get(nombre, 0)

            if ahora_ts - ultima_alerta >= 120:
                mensaje = (
                    f"<b>{emoji} {estado}</b>\n\n"
                    f"<b>Pozo:</b> {nombre}\n"
                    f"<b>Caudal:</b> {caudal} L/s\n"
                    f"<b>📅 {ahora()}</b>"
                )
                await self.enviar(app, mensaje)
                self.alertas[nombre] = ahora_ts

        elif anterior is not None:
            estado_ant, _ = estado_caudal(anterior)
            if estado != estado_ant:
                mensaje = (
                    f"<b>{emoji} CAMBIO DE ESTADO</b>\n\n"
                    f"<b>Pozo:</b> {nombre}\n"
                    f"<b>Nuevo estado:</b> {estado}\n"
                    f"<b>Caudal:</b> {caudal} L/s\n"
                    f"<b>📅 {ahora()}</b>"
                )
                await self.enviar(app, mensaje)

    async def reporte_horario(self, app):
        if time.time() - self.ultimo_reporte < REPORTE_INTERVAL:
            return

        if not self.ultimos:
            return

        detenidos = sum(1 for v in self.ultimos.values() if v == 0)
        criticos = sum(1 for v in self.ultimos.values() if 0 < v < 10)
        bajos = sum(1 for v in self.ultimos.values() if 10 <= v < 30)
        normales = sum(1 for v in self.ultimos.values() if v >= 30)

        # 🔹 Ordenar pozos alfabéticamente
        pozos_ordenados = sorted(self.ultimos.items())

        detalle_pozos = ""
        for nombre, caudal in pozos_ordenados:

            if caudal == 0:
                estado = "🔴"
            elif 0 < caudal < 10:
                estado = "🔴"
            elif 10 <= caudal < 30:
                estado = "🟠"
            else:
                estado = "🟢"

            detalle_pozos += f"{estado} <b>{nombre}</b>: {caudal} L/s\n"

        mensaje = (
            f"<b>📊 REPORTE HORARIO</b>\n\n"
            f"🔴 Detenidos: {detenidos}\n"
            f"🔴 Críticos: {criticos}\n"
            f"🟠 Bajos: {bajos}\n"
            f"🟢 Normales: {normales}\n\n"
            f"<b>📍 DETALLE POR POZO</b>\n"
            f"{detalle_pozos}\n"
            f"<b>📅 {ahora()}</b>"
        )

        await self.enviar(app, mensaje)
        self.ultimo_reporte = time.time()

        async def loop(self, app: Application):
            while True:
                try:
                    # Reinicio preventivo cada 6 horas
                    if time.time() - self.inicio_browser > RESTART_BROWSER_INTERVAL:
                        print("♻ Reinicio preventivo del navegador")
                        await self.iniciar()

                    datos = await self.obtener_datos()

                    for nombre, caudal in datos.items():
                        anterior = self.ultimos.get(nombre)
                        self.ultimos[nombre] = caudal
                        await self.procesar_alertas(app, nombre, caudal, anterior)

                    await self.reporte_horario(app)

                except Exception as e:
                    print("❌ Error en scraping:", e)
                    await self.iniciar()

                await asyncio.sleep(CHECK_INTERVAL)

# ==========================
# FLASK
# ==========================

flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot activo"

@flask_app.route("/health")
def health():
    return "OK", 200


# ==========================
# TELEGRAM COMMAND
# ==========================

async def cmd_caudales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    monitor: Monitor = context.application.bot_data["monitor"]

    if not monitor.ultimos:
        await update.message.reply_text("🔄 Aún no hay datos disponibles...")
        return

    mensaje = "<b>📊 ESTADO ACTUAL</b>\n\n"

    for nombre, valor in monitor.ultimos.items():
        estado, emoji = estado_caudal(valor)
        mensaje += f"<b>{nombre}:</b> {valor} L/s - {emoji} {estado}\n"

    mensaje += f"\n🕐 {ahora()}"

    await update.message.reply_text(mensaje, parse_mode="HTML")


# ==========================
# MAIN
# ==========================

async def main():

    monitor = Monitor()
    await monitor.iniciar()

    app = Application.builder().token(TOKEN).build()
    app.bot_data["monitor"] = monitor

    app.add_handler(CommandHandler("caudales", cmd_caudales))

    # Iniciar monitor en background
    asyncio.create_task(monitor.loop(app))

    print("🚀 Bot iniciado correctamente en Render Starter")

    # Inicialización manual correcta
    await app.initialize()
    await app.start()
    await app.updater.start_polling()

    # Mantener vivo el proceso
    await asyncio.Event().wait()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
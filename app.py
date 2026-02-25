import time
import re
import asyncio
import threading
import os
import traceback
from datetime import datetime
from flask import Flask
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes

# ==============================
# CONFIGURACIÓN BÁSICA
# ==============================
USERNAME = os.environ.get("LEM_USERNAME", "8.496.887-0")
PASSWORD = os.environ.get("LEM_PASSWORD", "8496887")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8556172137:AAFZVZ5d5Xj5J4ergSIvkcV9EjARFdXFYrw")
CHAT_IDS = [chat_id.strip() for chat_id in os.environ.get("CHAT_IDS", "5921135865").split(",") if chat_id.strip()]

LOGIN_URL = "http://login.lemsystem.cl/"
PANEL_URL = "http://optimus.lemsystem.cl/LemSense.php"

bot = Bot(token=TELEGRAM_TOKEN)

# ==============================
# ESTADO
# ==============================
estado_pozos = {}
ultimos_caudales = {}
ultimo_reporte_5min = 0
ultima_alerta = {}

# ==============================
# HORA CHILE
# ==============================
try:
    import pytz
    chile_tz = pytz.timezone('America/Santiago')
    def get_hora():
        return datetime.now(pytz.UTC).astimezone(chile_tz).strftime('%d/%m/%Y %H:%M:%S')
except:
    def get_hora():
        return datetime.now().strftime('%d/%m/%Y %H:%M:%S')

# ==============================
# CHROME DRIVER
# ==============================
def create_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.binary_location = "/usr/bin/google-chrome"
    
    try:
        return webdriver.Chrome(service=Service('/usr/local/bin/chromedriver'), options=options)
    except:
        return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)

# ==============================
# TELEGRAM - COMANDOS
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) in CHAT_IDS:
        await update.message.reply_text("🤖 Bot de Monitoreo de Pozos\n/caudales - Ver estado")

async def caudales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) not in CHAT_IDS:
        return
    
    if not ultimos_caudales:
        await update.message.reply_text("🔄 No hay datos aún")
        return
    
    msg = "<b>📊 ESTADO ACTUAL</b>\n\n"
    for n, c in ultimos_caudales.items():
        if c == 0: e = "🔴 DETENIDO"
        elif c < 10: e = "🔴 CRÍTICO"
        elif c < 30: e = "🟠 BAJO"
        else: e = "🟢 NORMAL"
        msg += f"<b>{n}:</b> {c} L/s - {e}\n"
    msg += f"\n🕐 {get_hora()}"
    await update.message.reply_text(msg, parse_mode='HTML')

# ==============================
# ENVIAR MENSAJE (SIMPLIFICADO)
# ==============================
def send(msg):
    for chat_id in CHAT_IDS:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot.send_message(chat_id=chat_id, text=msg, parse_mode='HTML'))
            loop.close()
            print(f"✅ Mensaje a {chat_id}")
        except Exception as e:
            print(f"❌ Error a {chat_id}: {e}")
        time.sleep(1)

# ==============================
# LOGIN
# ==============================
def login(driver):
    print("🔑 Iniciando sesión...")
    driver.get(LOGIN_URL)
    time.sleep(3)
    driver.find_element(By.XPATH, "//input[@placeholder='Usuario']").send_keys(USERNAME)
    driver.find_element(By.XPATH, "//input[@placeholder='Contraseña']").send_keys(PASSWORD)
    driver.find_element(By.ID, "loading").click()
    time.sleep(5)
    print("✅ Sesión iniciada")
    send(f"🤖 Bot iniciado\n📅 {get_hora()}")

# ==============================
# REPORTE CADA 5 MIN
# ==============================
def reporte_5min():
    global ultimo_reporte_5min
    ahora = time.time()
    if ahora - ultimo_reporte_5min >= 300 and ultimos_caudales:
        detenidos = sum(1 for c in ultimos_caudales.values() if c == 0)
        criticos = sum(1 for c in ultimos_caudales.values() if 0 < c < 10)
        bajos = sum(1 for c in ultimos_caudales.values() if 10 <= c < 30)
        normales = sum(1 for c in ultimos_caudales.values() if c >= 30)
        
        msg = f"<b>📊 REPORTE CADA 5 MIN</b>\n\n"
        msg += f"<b>📅 {get_hora()}</b>\n\n"
        msg += f"🔴 Detenidos: {detenidos}\n"
        msg += f"🔴 Críticos: {criticos}\n"
        msg += f"🟠 Bajos: {bajos}\n"
        msg += f"🟢 Normales: {normales}\n\n"
        msg += f"<b>Detalle:</b>\n"
        
        for n, c in ultimos_caudales.items():
            if c == 0: e = "🔴 DETENIDO"
            elif c < 10: e = "🔴 CRÍTICO"
            elif c < 30: e = "🟠 BAJO"
            else: e = "🟢 NORMAL"
            msg += f"• {n}: {c} L/s - {e}\n"
        
        send(msg)
        ultimo_reporte_5min = ahora
        print("✅ Reporte 5min enviado")

# ==============================
# VERIFICAR POZOS
# ==============================
def verificar(driver):
    global ultimos_caudales
    
    print(f"\n🔍 Verificando - {get_hora()}")
    
    # Reporte cada 5 min
    reporte_5min()
    
    driver.get(PANEL_URL)
    time.sleep(5)
    
    try:
        contenedor = driver.find_element(By.ID, "insidethepopup_alerta")
        pozos = contenedor.find_elements(By.CLASS_NAME, "col-lg-2")
        
        for pozo in pozos:
            texto = pozo.text
            nombre = texto.split("\n")[0]
            match = re.search(r"Caudal:\s*([0-9\.]+)", texto)
            if not match: continue
            
            caudal = float(match.group(1))
            print(f"📊 {nombre} → {caudal} L/s")
            ultimos_caudales[nombre] = caudal
            
            # Alertas
            estado_anterior = estado_pozos.get(nombre, "normal")
            ahora = time.time()
            
            if caudal == 0:
                if estado_anterior != "detenido":
                    send(f"<b>🚨 POZO DETENIDO</b>\n\n<b>{nombre}</b>\nCaudal: 0 L/s\n📅 {get_hora()}")
                estado_pozos[nombre] = "detenido"
                
            elif caudal < 10:
                if estado_anterior != "critico":
                    send(f"<b>🔴 CAUDAL CRÍTICO</b>\n\n<b>{nombre}</b>\nCaudal: {caudal} L/s\n📅 {get_hora()}")
                estado_pozos[nombre] = "critico"
                
            elif caudal < 30:
                if estado_anterior not in ["bajo", "critico", "detenido"]:
                    send(f"<b>⚠️ CAUDAL BAJO</b>\n\n<b>{nombre}</b>\nCaudal: {caudal} L/s\n📅 {get_hora()}")
                estado_pozos[nombre] = "bajo"
                
            else:
                if estado_anterior in ["bajo", "critico", "detenido"]:
                    send(f"<b>✅ POZO NORMALIZADO</b>\n\n<b>{nombre}</b>\nCaudal: {caudal} L/s\n📅 {get_hora()}")
                estado_pozos[nombre] = "normal"
                
    except Exception as e:
        print(f"❌ Error: {e}")

# ==============================
# BOT PRINCIPAL
# ==============================
def run():
    driver = None
    try:
        driver = create_driver()
        login(driver)
        
        # Iniciar bot de Telegram en otro hilo
        app = Application.builder().token(TELEGRAM_TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.add_handler(CommandHandler("caudales", caudales))
        
        from threading import Thread
        Thread(target=app.run_polling, daemon=True).start()
        
        # Loop principal
        while True:
            verificar(driver)
            print("⏱️ Esperando 2 minutos...")
            time.sleep(120)
            
    except Exception as e:
        print(f"❌ Error fatal: {e}")
    finally:
        if driver: driver.quit()

# ==============================
# FLASK
# ==============================
app_flask = Flask(__name__)

@app_flask.route('/')
def home():
    return f"Bot activo - {get_hora()}"

@app_flask.route('/health')
def health():
    return "OK", 200

# ==============================
# INICIO
# ==============================
if __name__ == "__main__":
    # Forzar un solo worker
    os.environ['WEB_CONCURRENCY'] = '1'
    
    print("🚀 Iniciando bot...")
    Thread(target=run, daemon=True).start()
    app_flask.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
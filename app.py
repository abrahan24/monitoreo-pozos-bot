import time
import re
import threading
import os
import traceback
import sys
import asyncio
from datetime import datetime
from flask import Flask
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from telegram import Bot
from telegram.ext import Application, CommandHandler

# Forzar flush de prints
sys.stdout.reconfigure(line_buffering=True)

# ==============================
# CONFIGURACIÓN
# ==============================
USERNAME = os.environ.get("LEM_USERNAME", "8.496.887-0")
PASSWORD = os.environ.get("LEM_PASSWORD", "8496887")
TOKEN = os.environ.get("TELEGRAM_TOKEN", "8556172137:AAFZVZ5d5Xj5J4ergSIvkcV9EjARFdXFYrw")
CHATS = [c.strip() for c in os.environ.get("CHAT_IDS", "5921135865").split(",") if c.strip()]

LOGIN_URL = "http://login.lemsystem.cl/"
PANEL_URL = "http://optimus.lemsystem.cl/LemSense.php"

bot = Bot(token=TOKEN)

# ==============================
# ESTADO
# ==============================
ultimos_caudales = {}
ultimo_reporte = 0
driver = None
driver_lock = threading.Lock()

# ==============================
# HORA CHILE
# ==============================
try:
    import pytz
    chile = pytz.timezone('America/Santiago')
    def ahora():
        return datetime.now(pytz.UTC).astimezone(chile).strftime('%d/%m/%Y %H:%M:%S')
except:
    def ahora():
        return datetime.now().strftime('%d/%m/%Y %H:%M:%S')

# ==============================
# CHROME
# ==============================
def crear_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.binary_location = "/usr/bin/google-chrome"
    
    try:
        service = Service('/usr/local/bin/chromedriver')
        driver = webdriver.Chrome(service=service, options=options)
        print("✅ Chrome driver creado exitosamente")
        return driver
    except Exception as e:
        print(f"❌ Error con ChromeDriver: {e}")
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        return driver

# ==============================
# TELEGRAM (simplificado)
# ==============================
def enviar(texto):
    for chat in CHATS:
        try:
            # Usar el bot directamente sin asyncio complicado
            import requests
            url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
            data = {
                "chat_id": chat,
                "text": texto,
                "parse_mode": "HTML"
            }
            response = requests.post(url, data=data, timeout=10)
            if response.status_code == 200:
                print(f"✅ Enviado a {chat}")
            else:
                print(f"❌ Error {response.status_code} con {chat}")
        except Exception as e:
            print(f"❌ Error con {chat}: {e}")
        time.sleep(1)

# ==============================
# COMANDOS
# ==============================
async def cmd_start(update, context):
    if str(update.effective_chat.id) in CHATS:
        await update.message.reply_text("🤖 Bot de pozos activo\n/caudales - Ver estado")

async def cmd_caudales(update, context):
    if str(update.effective_chat.id) not in CHATS:
        return
    if not ultimos_caudales:
        await update.message.reply_text("🔄 Cargando datos...")
        return
    
    msg = f"<b>📊 ESTADO ACTUAL</b>\n\n"
    for n, c in ultimos_caudales.items():
        if c == 0: e = "🔴 DETENIDO"
        elif c < 10: e = "🔴 CRÍTICO"
        elif c < 30: e = "🟠 BAJO"
        else: e = "🟢 NORMAL"
        msg += f"<b>{n}:</b> {c} L/s - {e}\n"
    msg += f"\n🕐 {ahora()}"
    await update.message.reply_text(msg, parse_mode='HTML')

# ==============================
# FUNCIONES LEM
# ==============================
def login():
    global driver
    print("🔑 Iniciando sesión...")
    sys.stdout.flush()
    driver.get(LOGIN_URL)
    time.sleep(3)
    driver.find_element(By.XPATH, "//input[@placeholder='Usuario']").send_keys(USERNAME)
    driver.find_element(By.XPATH, "//input[@placeholder='Contraseña']").send_keys(PASSWORD)
    driver.find_element(By.ID, "loading").click()
    time.sleep(5)
    print("✅ Sesión iniciada")
    sys.stdout.flush()
    enviar(f"🤖 Bot iniciado\n📅 {ahora()}")

def verificar():
    global ultimos_caudales, ultimo_reporte, driver
    hora = ahora()
    print(f"\n🔍 Verificando - {hora}")
    sys.stdout.flush()
    
    # Reporte cada 5 minutos
    if time.time() - ultimo_reporte >= 300:
        if ultimos_caudales:
            det = sum(1 for c in ultimos_caudales.values() if c == 0)
            cri = sum(1 for c in ultimos_caudales.values() if 0 < c < 10)
            baj = sum(1 for c in ultimos_caudales.values() if 10 <= c < 30)
            nor = sum(1 for c in ultimos_caudales.values() if c >= 30)
            
            msg = f"<b>📊 REPORTE CADA 5 MIN</b>\n\n"
            msg += f"<b>📅 {hora}</b>\n\n"
            msg += f"🔴 Detenidos: {det}\n🔴 Críticos: {cri}\n🟠 Bajos: {baj}\n🟢 Normales: {nor}\n\n"
            msg += f"<b>Detalle:</b>\n"
            for n, c in ultimos_caudales.items():
                if c == 0: e = "🔴 DETENIDO"
                elif c < 10: e = "🔴 CRÍTICO"
                elif c < 30: e = "🟠 BAJO"
                else: e = "🟢 NORMAL"
                msg += f"• {n}: {c} L/s - {e}\n"
            
            enviar(msg)
            ultimo_reporte = time.time()
    
    # Obtener datos
    with driver_lock:
        driver.get(PANEL_URL)
        time.sleep(5)
        
        try:
            contenedor = driver.find_element(By.ID, "insidethepopup_alerta")
            pozos = contenedor.find_elements(By.CLASS_NAME, "col-lg-2")
            
            for pozo in pozos:
                texto = pozo.text
                nombre = texto.split("\n")[0]
                match = re.search(r"Caudal:\s*([0-9\.]+)", texto)
                if match:
                    caudal = float(match.group(1))
                    print(f"📊 {nombre} → {caudal} L/s")
                    sys.stdout.flush()
                    ultimos_caudales[nombre] = caudal
                    
        except Exception as e:
            print(f"❌ Error: {e}")
            sys.stdout.flush()

# ==============================
# HILO DE MONITOREO (SECUNDARIO)
# ==============================
def hilo_monitoreo():
    global driver
    try:
        driver = crear_driver()
        login()
        
        print("🔄 Monitoreando cada 2 minutos...")
        sys.stdout.flush()
        while True:
            verificar()
            print("⏱️ Esperando 2 minutos...")
            sys.stdout.flush()
            time.sleep(120)
            
    except Exception as e:
        print(f"❌ Error fatal en monitoreo: {e}")
        sys.stdout.flush()
        traceback.print_exc()
    finally:
        if driver:
            driver.quit()

# ==============================
# FLASK
# ==============================
app = Flask(__name__)

@app.route('/')
def home():
    return f"Bot activo - {ahora()} - Pozos: {len(ultimos_caudales)}"

@app.route('/health')
def health():
    return "OK", 200

# ==============================
# INICIO (AHORA EL BOT ESTÁ EN EL HILO PRINCIPAL)
# ==============================
if __name__ == "__main__":
    print("🚀 Iniciando bot...")
    sys.stdout.flush()
    os.environ['WEB_CONCURRENCY'] = '1'
    
    # Iniciar monitoreo en un hilo secundario
    threading.Thread(target=hilo_monitoreo, daemon=True).start()
    
    # Iniciar bot de Telegram en el hilo principal
    print("🤖 Iniciando bot de Telegram en hilo principal...")
    sys.stdout.flush()
    
    telegram_app = Application.builder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CommandHandler("caudales", cmd_caudales))
    
    # Ejecutar Flask en un hilo separado
    port = int(os.environ.get('PORT', 5000))
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=port), daemon=True).start()
    
    # Ejecutar el bot (esto bloquea el hilo principal)
    telegram_app.run_polling(drop_pending_updates=True)
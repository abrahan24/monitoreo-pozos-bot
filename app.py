import asyncio
import time
import re
import threading
import os
import traceback
import sys  # <-- AGREGAR ESTA LÍNEA
from datetime import datetime
from flask import Flask
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from telegram import Bot
from telegram.ext import Application, CommandHandler

# Forzar flush de prints para que se vean en Render
sys.stdout.reconfigure(line_buffering=True)  # <-- AGREGAR ESTA LÍNEA

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
    
    # Ruta del binario de Chrome en el contenedor
    options.binary_location = "/usr/bin/google-chrome"
    
    try:
        # Usar ChromeDriver instalado por Docker
        service = Service('/usr/local/bin/chromedriver')
        driver = webdriver.Chrome(service=service, options=options)
        print("✅ Chrome driver creado exitosamente")
        return driver
    except Exception as e:
        print(f"❌ Error con ChromeDriver: {e}")
        # Fallback: webdriver-manager
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        return driver

# ==============================
# TELEGRAM
# ==============================
def enviar(texto):
    for chat in CHATS:
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot.send_message(chat_id=chat, text=texto, parse_mode='HTML'))
            loop.close()
            print(f"✅ Enviado a {chat}")
            sys.stdout.flush()  # <-- FORZAR PRINT
        except Exception as e:
            print(f"❌ Error con {chat}: {e}")
            sys.stdout.flush()  # <-- FORZAR PRINT
        time.sleep(2)

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
def login(driver):
    print("🔑 Iniciando sesión...")
    sys.stdout.flush()  # <-- FORZAR PRINT
    driver.get(LOGIN_URL)
    time.sleep(3)
    driver.find_element(By.XPATH, "//input[@placeholder='Usuario']").send_keys(USERNAME)
    driver.find_element(By.XPATH, "//input[@placeholder='Contraseña']").send_keys(PASSWORD)
    driver.find_element(By.ID, "loading").click()
    time.sleep(5)
    print("✅ Sesión iniciada")
    sys.stdout.flush()  # <-- FORZAR PRINT
    enviar(f"🤖 Bot iniciado\n📅 {ahora()}")

def verificar(driver):
    global ultimos_caudales, ultimo_reporte
    hora = ahora()
    print(f"\n🔍 Verificando - {hora}")
    sys.stdout.flush()  # <-- FORZAR PRINT
    
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
                sys.stdout.flush()  # <-- FORZAR PRINT
                ultimos_caudales[nombre] = caudal
                
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.stdout.flush()  # <-- FORZAR PRINT

# ==============================
# HILO DEL BOT
# ==============================
# ==============================
# HILO DEL BOT (CORREGIDO)
# ==============================
def ejecutar_bot():
    """Ejecuta el bot de Telegram"""
    # Crear un nuevo event loop para este hilo
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("caudales", cmd_caudales))
    print("🤖 Bot de Telegram iniciado")
    sys.stdout.flush()
    
    # Ejecutar el bot en este loop
    app.run_polling(drop_pending_updates=True)

# ==============================
# HILO PRINCIPAL
# ==============================
def main():
    driver = None
    try:
        driver = crear_driver()
        login(driver)
        
        # Iniciar bot en otro hilo
        threading.Thread(target=ejecutar_bot, daemon=True).start()
        
        print("🔄 Monitoreando cada 2 minutos...")
        sys.stdout.flush()  # <-- FORZAR PRINT
        while True:
            verificar(driver)
            print("⏱️ Esperando 2 minutos...")
            sys.stdout.flush()  # <-- FORZAR PRINT
            time.sleep(120)
            
    except Exception as e:
        print(f"❌ Error fatal: {e}")
        sys.stdout.flush()  # <-- FORZAR PRINT
        traceback.print_exc()
    finally:
        if driver:
            driver.quit()

# ==============================
# FLASK (OBLIGATORIO PARA RENDER)
# ==============================
app = Flask(__name__)

@app.route('/')
def home():
    return f"Bot activo - {ahora()}"

@app.route('/health')
def health():
    return "OK", 200

# ==============================
# INICIO
# ==============================
if __name__ == "__main__":
    print("🚀 Iniciando bot...")
    sys.stdout.flush()  # <-- FORZAR PRINT
    os.environ['WEB_CONCURRENCY'] = '1'
    
    hilo_principal = threading.Thread(target=main, daemon=True)
    hilo_principal.start()
    
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
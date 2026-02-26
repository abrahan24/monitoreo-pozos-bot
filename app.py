import time
import re
import threading
import os
import traceback
import sys
import asyncio
from datetime import datetime
from flask import Flask, request
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler
import requests

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

# ==============================
# ESTADO
# ==============================
ultimos_caudales = {}
ultimo_reporte = 0
driver = None
driver_lock = threading.Lock()
bot_iniciado = False
bot_lock = threading.Lock()

# ==============================
# CONTROL DE ALERTAS
# ==============================
ultima_alerta_detenido = {}
ultima_alerta_critico = {}

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
# CHROME (MEJORADO)
# ==============================
def crear_driver():
    """Crea driver con mejor configuración de timeouts"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--page-load-strategy=eager")
    options.add_argument("--disable-web-security")
    options.add_argument("--disable-features=VizDisplayCompositor")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.binary_location = "/usr/bin/google-chrome"
    
    try:
        service = Service('/usr/local/bin/chromedriver')
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(60)
        print("✅ Chrome driver creado exitosamente")
        return driver
    except Exception as e:
        print(f"❌ Error con ChromeDriver: {e}")
        from webdriver_manager.chrome import ChromeDriverManager
        service = Service(ChromeDriverManager().install())
        driver = webdriver.Chrome(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(60)
        return driver

def verificar_driver():
    """Verifica si el driver sigue funcionando"""
    global driver
    try:
        driver.current_url
        return True
    except:
        return False

# ==============================
# TELEGRAM (MEJORADO)
# ==============================
def enviar(texto, max_intentos=3):
    """Envía mensaje con reintentos"""
    for chat in CHATS:
        for intento in range(max_intentos):
            try:
                url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
                data = {
                    "chat_id": chat,
                    "text": texto,
                    "parse_mode": "HTML"
                }
                response = requests.post(url, data=data, timeout=10)
                if response.status_code == 200:
                    print(f"✅ Enviado a {chat}")
                    break
                else:
                    print(f"❌ Error {response.status_code} con {chat}, intento {intento + 1}")
            except Exception as e:
                print(f"❌ Error con {chat}, intento {intento + 1}: {e}")
                if intento < max_intentos - 1:
                    time.sleep(2)
                else:
                    print(f"❌ Falló envío a {chat} después de {max_intentos} intentos")
        time.sleep(1)

# ==============================
# COMANDOS
# ==============================
async def cmd_start(update, context):
    if str(update.effective_chat.id) in CHATS:
        await update.message.reply_text(
            "🤖 *Bot de Monitoreo de Pozos*\n\n"
            "Comandos disponibles:\n"
            "/caudales - Ver estado actual de todos los pozos\n"
            "/ayuda - Mostrar esta ayuda",
            parse_mode='Markdown'
        )

async def cmd_ayuda(update, context):
    await cmd_start(update, context)

async def cmd_caudales(update, context):
    if str(update.effective_chat.id) not in CHATS:
        return
    
    if not ultimos_caudales:
        await update.message.reply_text("🔄 Cargando datos, espera un momento...")
        return
    
    msg = f"<b>📊 ESTADO ACTUAL DE POZOS</b>\n\n"
    for n, c in ultimos_caudales.items():
        if c == 0:
            e = "🔴 DETENIDO"
        elif c < 10:
            e = "🔴 CRÍTICO"
        elif c < 30:
            e = "🟠 BAJO"
        else:
            e = "🟢 NORMAL"
        msg += f"<b>{n}:</b> {c} L/s - {e}\n"
    msg += f"\n🕐 {ahora()}"
    
    try:
        await update.message.reply_text(msg, parse_mode='HTML')
    except Exception as e:
        print(f"❌ Error enviando caudales: {e}")

# ==============================
# FUNCIONES LEM
# ==============================
def login():
    """Inicia sesión con reintentos"""
    global driver
    max_intentos = 3
    
    for intento in range(max_intentos):
        try:
            print(f"🔑 Iniciando sesión (intento {intento + 1})...")
            sys.stdout.flush()
            
            driver.set_page_load_timeout(60)
            driver.get(LOGIN_URL)
            time.sleep(3)
            
            driver.find_element(By.XPATH, "//input[@placeholder='Usuario']").send_keys(USERNAME)
            driver.find_element(By.XPATH, "//input[@placeholder='Contraseña']").send_keys(PASSWORD)
            driver.find_element(By.ID, "loading").click()
            time.sleep(5)
            
            print("✅ Sesión iniciada")
            sys.stdout.flush()
            enviar(f"🤖 Bot iniciado correctamente\n📅 {ahora()}")
            return True
            
        except Exception as e:
            print(f"❌ Error en login (intento {intento + 1}): {e}")
            sys.stdout.flush()
            if intento < max_intentos - 1:
                time.sleep(10)
    
    return False

def verificar():
    """Verifica pozos con mejor manejo de errores"""
    global ultimos_caudales, ultimo_reporte, driver
    global ultima_alerta_detenido, ultima_alerta_critico
    
    hora = ahora()
    print(f"\n🔍 Verificando pozos - {hora}")
    sys.stdout.flush()
    
    # Verificar si el driver está vivo
    if not verificar_driver():
        print("⚠️ Driver no responde, recreando...")
        try:
            driver.quit()
        except:
            pass
        driver = crear_driver()
        if not login():
            print("❌ No se pudo reiniciar sesión")
            return
    
    # Reporte cada 60 minutos
    if time.time() - ultimo_reporte >= 3600 and ultimos_caudales:
        det = sum(1 for c in ultimos_caudales.values() if c == 0)
        cri = sum(1 for c in ultimos_caudales.values() if 0 < c < 10)
        baj = sum(1 for c in ultimos_caudales.values() if 10 <= c < 30)
        nor = sum(1 for c in ultimos_caudales.values() if c >= 30)
        
        msg = f"<b>📊 REPORTE HORARIO</b>\n\n"
        msg += f"<b>📅 {hora}</b>\n\n"
        msg += f"🔴 Detenidos: {det}\n"
        msg += f"🔴 Críticos (<10): {cri}\n"
        msg += f"🟠 Bajos (10-29): {baj}\n"
        msg += f"🟢 Normales (≥30): {nor}\n\n"
        msg += f"<b>Detalle por pozo:</b>\n"
        
        for n, c in ultimos_caudales.items():
            if c == 0:
                e = "🔴 DETENIDO"
            elif c < 10:
                e = "🔴 CRÍTICO"
            elif c < 30:
                e = "🟠 BAJO"
            else:
                e = "🟢 NORMAL"
            msg += f"• {n}: {c} L/s - {e}\n"
        
        enviar(msg)
        ultimo_reporte = time.time()
    
    # Obtener datos
    try:
        with driver_lock:
            driver.set_page_load_timeout(90)
            driver.get(PANEL_URL)
            time.sleep(8)
            
            contenedor = driver.find_element(By.ID, "insidethepopup_alerta")
            pozos = contenedor.find_elements(By.CLASS_NAME, "col-lg-2")
            
            pozos_procesados = 0
            for pozo in pozos:
                try:
                    texto = pozo.text
                    nombre = texto.split("\n")[0]
                    match = re.search(r"Caudal:\s*([0-9\.]+)", texto)
                    
                    if match:
                        caudal = float(match.group(1))
                        print(f"📊 {nombre} → {caudal} L/s")
                        sys.stdout.flush()
                        
                        caudal_anterior = ultimos_caudales.get(nombre)
                        ultimos_caudales[nombre] = caudal
                        pozos_procesados += 1
                        
                        tiempo_actual = time.time()
                        
                        # DETENIDO (0 L/s) - Alerta cada 2 minutos
                        if caudal == 0:
                            ultima_alerta = ultima_alerta_detenido.get(nombre, 0)
                            if tiempo_actual - ultima_alerta >= 120:
                                mensaje = f"""<b>🚨 POZO DETENIDO</b>

<b>Pozo:</b> {nombre}
<b>Caudal:</b> 0 L/s
<b>📅 {hora}</b>"""
                                
                                enviar(mensaje)
                                ultima_alerta_detenido[nombre] = tiempo_actual
                                print(f"⏰ Alerta DETENIDO para {nombre}")
                        
                        # CRÍTICO (<10) - Alerta cada 2 minutos
                        elif 0 < caudal < 10:
                            ultima_alerta = ultima_alerta_critico.get(nombre, 0)
                            if tiempo_actual - ultima_alerta >= 120:
                                mensaje = f"""<b>🔴 CAUDAL CRÍTICO</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>📅 {hora}</b>"""
                                
                                enviar(mensaje)
                                ultima_alerta_critico[nombre] = tiempo_actual
                                print(f"⏰ Alerta CRÍTICO para {nombre}")
                        
                        # BAJO (10-29) - Solo cuando cambia
                        elif 10 <= caudal < 30:
                            if caudal_anterior is None or caudal_anterior >= 30 or caudal_anterior < 10:
                                mensaje = f"""<b>⚠️ CAUDAL BAJO</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>📅 {hora}</b>"""
                                
                                enviar(mensaje)
                                print(f"📩 Alerta BAJO para {nombre}")
                        
                        # NORMAL (>=30) - Solo cuando se recupera
                        elif caudal >= 30:
                            if caudal_anterior is not None and (caudal_anterior < 30 or caudal_anterior == 0):
                                mensaje = f"""<b>✅ POZO NORMALIZADO</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>📅 {hora}</b>"""
                                
                                enviar(mensaje)
                                print(f"📩 Alerta NORMALIZADO para {nombre}")
                                
                                if nombre in ultima_alerta_detenido:
                                    del ultima_alerta_detenido[nombre]
                                if nombre in ultima_alerta_critico:
                                    del ultima_alerta_critico[nombre]
                    
                except Exception as e:
                    print(f"⚠️ Error procesando pozo: {e}")
                    continue
            
            print(f"✅ Procesados {pozos_procesados} pozos")
            sys.stdout.flush()
            
    except TimeoutException:
        print("❌ Timeout al cargar la página")
        sys.stdout.flush()
        # Recrear driver
        try:
            driver.quit()
        except:
            pass
        driver = crear_driver()
        login()
        
    except Exception as e:
        print(f"❌ Error en verificación: {e}")
        sys.stdout.flush()
        traceback.print_exc()

# ==============================
# HILO DE MONITOREO
# ==============================
def hilo_monitoreo():
    """Hilo que ejecuta el monitoreo de pozos"""
    global driver
    reintentos = 0
    
    while True:
        try:
            if driver is None or not verificar_driver():
                driver = crear_driver()
                if not login():
                    print("❌ No se pudo iniciar sesión, reintentando en 60s...")
                    time.sleep(60)
                    continue
            
            print("🔄 Monitoreando cada 2 minutos...")
            sys.stdout.flush()
            reintentos = 0
            
            while True:
                try:
                    verificar()
                    print("⏱️ Esperando 2 minutos...")
                    sys.stdout.flush()
                    time.sleep(120)
                except Exception as e:
                    print(f"❌ Error en ciclo de verificación: {e}")
                    sys.stdout.flush()
                    time.sleep(30)
                    break
                    
        except Exception as e:
            reintentos += 1
            print(f"❌ Error fatal en monitoreo (reintento {reintentos}): {e}")
            sys.stdout.flush()
            traceback.print_exc()
            
            tiempo_espera = min(300, 60 * reintentos)
            print(f"⏱️ Reintentando en {tiempo_espera} segundos...")
            time.sleep(tiempo_espera)
            
            if driver:
                try:
                    driver.quit()
                except:
                    pass
                driver = None

# ==============================
# APLICACIÓN TELEGRAM CON WEBHOOKS
# ==============================
telegram_app = None

async def setup_webhook():
    """Configura el webhook para el bot de Telegram"""
    global telegram_app
    
    telegram_app = Application.builder().token(TOKEN).build()
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CommandHandler("ayuda", cmd_ayuda))
    telegram_app.add_handler(CommandHandler("caudales", cmd_caudales))
    
    await telegram_app.initialize()
    
    # Obtener URL pública de Render
    public_url = os.environ.get('RENDER_EXTERNAL_URL', None)
    if not public_url:
        # En desarrollo local, usar ngrok o similar
        public_url = f"https://{os.environ.get('RENDER_EXTERNAL_HOSTNAME', 'localhost')}"
    
    webhook_url = f"{public_url}/webhook"
    
    # Configurar webhook
    await telegram_app.bot.set_webhook(url=webhook_url, allowed_updates=['message'])
    print(f"✅ Webhook configurado en {webhook_url}")
    sys.stdout.flush()
    
    return telegram_app

# ==============================
# FLASK (MANEJA WEBHOOK Y HEALTH CHECK)
# ==============================
app = Flask(__name__)

@app.route('/')
def home():
    estado_driver = "✅ Activo" if driver and verificar_driver() else "❌ Inactivo"
    return f"""
    <html>
        <head><title>Bot de Pozos</title></head>
        <body style="font-family: Arial; padding: 20px;">
            <h1>🤖 Bot de Monitoreo de Pozos</h1>
            <p><b>Estado:</b> Activo</p>
            <p><b>Hora Chile:</b> {ahora()}</p>
            <p><b>Driver Chrome:</b> {estado_driver}</p>
            <p><b>Pozos monitoreados:</b> {len(ultimos_caudales)}</p>
            <p><b>Chats autorizados:</b> {len(CHATS)}</p>
            <p><b>Modo:</b> Webhook</p>
            <p><a href="/health">Health Check</a></p>
        </body>
    </html>
    """

@app.route('/health')
def health():
    if driver and verificar_driver():
        return "OK", 200
    else:
        return "Driver no disponible", 503

@app.route('/webhook', methods=['POST'])
def webhook():
    """Endpoint para recibir actualizaciones de Telegram"""
    if telegram_app is None:
        return "Bot no inicializado", 503
    
    # Procesar la actualización
    update_data = request.get_json()
    update = Update.de_json(update_data, telegram_app.bot)
    
    # Procesar en un hilo para no bloquear
    threading.Thread(target=lambda: asyncio.run(telegram_app.process_update(update)), daemon=True).start()
    
    return "OK", 200

# ==============================
# INICIO
# ==============================
if __name__ == "__main__":
    print("🚀 Iniciando bot de monitoreo de pozos...")
    print(f"📅 Hora Chile: {ahora()}")
    print(f"📱 Chats autorizados: {CHATS}")
    sys.stdout.flush()
    
    # Configurar entorno
    os.environ['WEB_CONCURRENCY'] = '1'
    
    # Iniciar monitoreo en un hilo secundario
    monitor_thread = threading.Thread(target=hilo_monitoreo, daemon=True)
    monitor_thread.start()
    print("✅ Hilo de monitoreo iniciado")
    sys.stdout.flush()
    
    # Configurar webhook de Telegram
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(setup_webhook())
    
    # Iniciar Flask (esto bloquea el hilo principal)
    port = int(os.environ.get('PORT', 5000))
    print(f"✅ Flask iniciado en puerto {port}")
    sys.stdout.flush()
    
    app.run(host='0.0.0.0', port=port, debug=False, use_reloader=False)
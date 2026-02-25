import time
import re
import asyncio
import threading
import os
import traceback
import signal
import sys
import fcntl
import atexit
from datetime import datetime
from flask import Flask
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

# ==============================
# CONFIGURACIÓN (USAR VARIABLES DE ENTORNO)
# ==============================
USERNAME = os.environ.get("LEM_USERNAME", "8.496.887-0")
PASSWORD = os.environ.get("LEM_PASSWORD", "8496887")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8556172137:AAFZVZ5d5Xj5J4ergSIvkcV9EjARFdXFYrw")
CHAT_IDS_AUTORIZADOS = os.environ.get("CHAT_IDS", "5921135865").split(",")
RENDER_INSTANCE_ID = os.environ.get("RENDER_INSTANCE_ID", "local")

LOGIN_URL = "http://login.lemsystem.cl/"
PANEL_URL = "http://optimus.lemsystem.cl/LemSense.php"

bot = Bot(token=TELEGRAM_TOKEN)

# ==============================
# ARCHIVO DE LOCK PARA EVITAR MÚLTIPLES INSTANCIAS
# ==============================
LOCK_FILE = "/tmp/bot_monitoreo.lock"
lock_file_handle = None

def adquirir_lock():
    """Adquiere un lock de archivo para asegurar que solo una instancia corre"""
    global lock_file_handle
    try:
        lock_file_handle = open(LOCK_FILE, 'w')
        fcntl.flock(lock_file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_file_handle.write(str(os.getpid()))
        lock_file_handle.flush()
        print(f"✅ Lock adquirido por instancia {RENDER_INSTANCE_ID} (PID: {os.getpid()})")
        return True
    except (IOError, OSError):
        print(f"❌ No se pudo adquirir lock - otra instancia ya está corriendo")
        return False

def liberar_lock():
    """Libera el lock de archivo"""
    global lock_file_handle
    if lock_file_handle:
        try:
            fcntl.flock(lock_file_handle, fcntl.LOCK_UN)
            lock_file_handle.close()
            os.unlink(LOCK_FILE)
            print("🔓 Lock liberado")
        except:
            pass

# ==============================
# ESTADO POZOS
# ==============================
estado_pozos = {}
ultimos_caudales = {}
ultimo_reporte_horario = 0
ultima_alerta_detenido = {}
ultima_alerta_critico = {}
TIEMPO_ENTRE_ALERTAS = 300

# ==============================
# ZONA HORARIA CHILE (OBLIGATORIA)
# ==============================
try:
    import pytz
    CHILE_TZ = pytz.timezone('America/Santiago')
    PYTZ_AVAILABLE = True
    print("✅ pytz instalado correctamente, usando hora Chile")
except ImportError:
    PYTZ_AVAILABLE = False
    print("❌ ERROR CRÍTICO: pytz no está instalado")
    print("⚠️ El bot necesita pytz para funcionar correctamente")
    print("📦 Ejecuta: pip install pytz")

def obtener_hora_chilena():
    """Retorna la hora actual en formato HH:MM:SS con zona horaria de Chile"""
    if PYTZ_AVAILABLE:
        try:
            ahora_utc = datetime.now(pytz.UTC)
            ahora_chile = ahora_utc.astimezone(CHILE_TZ)
            return ahora_chile.strftime('%H:%M:%S')
        except Exception as e:
            return time.strftime('%H:%M:%S')
    else:
        return time.strftime('%H:%M:%S')

def obtener_fecha_hora_chilena_completa():
    """Retorna la fecha y hora completa en formato DD/MM/YYYY HH:MM:SS con zona horaria de Chile"""
    if PYTZ_AVAILABLE:
        try:
            ahora_utc = datetime.now(pytz.UTC)
            ahora_chile = ahora_utc.astimezone(CHILE_TZ)
            return ahora_chile.strftime('%d/%m/%Y %H:%M:%S')
        except:
            return time.strftime('%d/%m/%Y %H:%M:%S')
    else:
        return time.strftime('%d/%m/%Y %H:%M:%S')

# ==============================
# CONTROL DE INSTANCIA DEL BOT
# ==============================
bot_instance_running = False
bot_instance_lock = threading.RLock()  # Usar RLock en lugar de Lock
application_instance = None

# ==============================
# CONFIGURACIÓN DE SELENIUM PARA RENDER
# ==============================
def create_driver():
    """Crea y configura el driver de Chrome para Render"""
    hora_chile = obtener_hora_chilena()
    print(f"🔧 [{hora_chile}] Configurando Chrome driver...")
    
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument("--remote-debugging-port=9222")
    chrome_options.add_argument("--disable-setuid-sandbox")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--disable-web-security")
    chrome_options.add_argument("--disable-features=VizDisplayCompositor")
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)
    
    chrome_options.binary_location = "/usr/bin/google-chrome"
    
    try:
        service = Service('/usr/local/bin/chromedriver')
        driver = webdriver.Chrome(service=service, options=chrome_options)
        print(f"✅ [{hora_chile}] Chrome driver creado exitosamente")
        return driver
    except Exception as e:
        print(f"❌ [{hora_chile}] Error creando driver: {e}")
        try:
            print(f"🔄 [{hora_chile}] Intentando con webdriver-manager...")
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            return driver
        except Exception as e2:
            print(f"❌ [{hora_chile}] Error también con webdriver-manager: {e2}")
            raise e2

# ==============================
# FUNCIONES DE TELEGRAM
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    hora_chile = obtener_hora_chilena()
    
    if chat_id in CHAT_IDS_AUTORIZADOS:
        await update.message.reply_text(
            "🤖 *Sistema de Monitoreo de Pozos*\n\n"
            "Comandos disponibles:\n"
            "/caudales - Ver estado actual de todos los pozos\n"
            "/ayuda - Mostrar esta ayuda",
            parse_mode='Markdown'
        )
        print(f"✅ [{hora_chile}] Comando /start ejecutado por chat {chat_id}")
    else:
        await update.message.reply_text("⛔ No autorizado")
        print(f"⚠️ [{hora_chile}] Intento no autorizado de /start desde chat {chat_id}")

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def caudales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    hora_chile = obtener_hora_chilena()
    
    if chat_id not in CHAT_IDS_AUTORIZADOS:
        await update.message.reply_text("⛔ No autorizado")
        print(f"⚠️ [{hora_chile}] Intento no autorizado de /caudales desde chat {chat_id}")
        return
    
    if not ultimos_caudales:
        await update.message.reply_text("🔄 Aún no hay datos disponibles. Espera la próxima actualización...")
        return
    
    mensaje = "<b>📊 ESTADO ACTUAL DE POZOS</b>\n\n"
    
    for nombre, caudal in ultimos_caudales.items():
        if caudal == 0:
            emoji = "🔴 DETENIDO"
        elif caudal < 10:
            emoji = "🔴 CRÍTICO"
        elif caudal < 30:
            emoji = "🟠 BAJO"
        else:
            emoji = "🟢 NORMAL"
        
        mensaje += f"<b>{nombre}:</b> {caudal} L/s - {emoji}\n"
    
    fecha_hora_completa = obtener_fecha_hora_chilena_completa()
    mensaje += f"\n🕐 Actualizado: {fecha_hora_completa} (hora Chile)"
    
    await update.message.reply_text(mensaje, parse_mode='HTML')
    print(f"✅ [{hora_chile}] Comando /caudales ejecutado para chat {chat_id}")

# ==============================
# FUNCIÓN PARA ENVIAR MENSAJES
# ==============================
def enviar_telegram(mensaje, chat_ids=None):
    """Envía mensaje a chats específicos o a todos los autorizados"""
    hora_chile = obtener_hora_chilena()
    
    if chat_ids is None:
        chat_ids = CHAT_IDS_AUTORIZADOS
    
    chat_ids = [chat_id for chat_id in chat_ids if chat_id.strip()]
    
    if not chat_ids:
        print(f"⚠️ [{hora_chile}] No hay chat IDs configurados para enviar mensajes")
        return
    
    print(f"📤 [{hora_chile}] Enviando mensaje a {len(chat_ids)} chat(s)")
    
    for chat_id in chat_ids:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(bot.send_message(
                chat_id=chat_id.strip(), 
                text=mensaje,
                parse_mode='HTML'
            ))
            loop.close()
            print(f"✅ [{hora_chile}] Mensaje enviado a chat {chat_id}")
        except TelegramError as e:
            print(f"❌ [{hora_chile}] Error de Telegram al enviar a {chat_id}: {e}")
        except Exception as e:
            print(f"❌ [{hora_chile}] Error inesperado al enviar a {chat_id}: {e}")
        
        time.sleep(1)

# ==============================
# FUNCIONES DE LEM
# ==============================
def login():
    """Inicia sesión en el sistema LEM"""
    hora_chile = obtener_hora_chilena()
    print(f"🔑 [{hora_chile}] Iniciando sesión...")
    
    driver.get(LOGIN_URL)
    time.sleep(3)

    driver.find_element(By.XPATH, "//input[@placeholder='Usuario']").send_keys(USERNAME)
    driver.find_element(By.XPATH, "//input[@placeholder='Contraseña']").send_keys(PASSWORD)
    driver.find_element(By.ID, "loading").click()

    time.sleep(5)
    print(f"✅ [{hora_chile}] Sesión iniciada correctamente")
    
    fecha_hora_completa = obtener_fecha_hora_chilena_completa()
    enviar_telegram(f"🤖 *Sistema de monitoreo de pozos iniciado en Render*\n\n📅 {fecha_hora_completa} (hora Chile)\n\nUsa /caudales para ver el estado actual")

def enviar_reporte_horario():
    """Envía un reporte cada hora con el estado de todos los pozos"""
    global ultimo_reporte_horario
    hora_chile = obtener_hora_chilena()
    fecha_hora_completa = obtener_fecha_hora_chilena_completa()
    
    hora_actual = time.time()
    
    if hora_actual - ultimo_reporte_horario >= 3600:
        print(f"⏰ [{hora_chile}] ¡Es hora del reporte horario!")
        
        if ultimos_caudales:
            mensaje = f"<b>📊 REPORTE HORARIO</b>\n\n"
            mensaje += f"<b>📅 {fecha_hora_completa} (hora Chile)</b>\n\n"
            
            detenidos = sum(1 for c in ultimos_caudales.values() if c == 0)
            criticos = sum(1 for c in ultimos_caudales.values() if 0 < c < 10)
            bajos = sum(1 for c in ultimos_caudales.values() if 10 <= c < 30)
            normales = sum(1 for c in ultimos_caudales.values() if c >= 30)
            
            mensaje += f"🔴 Detenidos: {detenidos}\n"
            mensaje += f"🔴 Críticos (<10): {criticos}\n"
            mensaje += f"🟠 Bajos (10-29): {bajos}\n"
            mensaje += f"🟢 Normales (≥30): {normales}\n\n"
            mensaje += f"<b>Detalle por pozo:</b>\n"
            
            for nombre, caudal in ultimos_caudales.items():
                if caudal == 0:
                    estado = "🔴 DETENIDO"
                elif caudal < 10:
                    estado = "🔴 CRÍTICO"
                elif caudal < 30:
                    estado = "🟠 BAJO"
                else:
                    estado = "🟢 NORMAL"
                
                mensaje += f"• {nombre}: {caudal} L/s - {estado}\n"
            
            enviar_telegram(mensaje)
            ultimo_reporte_horario = hora_actual
            print(f"✅ [{hora_chile}] Reporte horario enviado")
        else:
            print(f"⚠️ [{hora_chile}] No hay datos de caudales")

def verificar_pozos():
    """Verifica el estado de todos los pozos"""
    global ultimos_caudales
    
    hora_chile = obtener_hora_chilena()
    fecha_hora_completa = obtener_fecha_hora_chilena_completa()
    
    print(f"\n🔍 [{hora_chile}] Verificando pozos")
    
    enviar_reporte_horario()
    
    driver.get(PANEL_URL)
    time.sleep(5)

    try:
        contenedor = driver.find_element(By.ID, "insidethepopup_alerta")
        pozos = contenedor.find_elements(By.CLASS_NAME, "col-lg-2")
        
        if not pozos:
            print(f"⚠️ [{hora_chile}] No se encontraron pozos")
            return

        for pozo in pozos:
            texto = pozo.text
            nombre = texto.split("\n")[0]

            match = re.search(r"Caudal:\s*([0-9\.]+)", texto)
            if not match:
                continue

            caudal = float(match.group(1))
            print(f"📊 [{hora_chile}] {nombre} → {caudal} L/s")

            ultimos_caudales[nombre] = caudal
            estado_anterior = estado_pozos.get(nombre, "normal")
            tiempo_actual = time.time()

            if caudal == 0:
                ultima_alerta = ultima_alerta_detenido.get(nombre, 0)
                if tiempo_actual - ultima_alerta >= TIEMPO_ENTRE_ALERTAS:
                    mensaje = f"""<b>🚨 POZO DETENIDO (alerta cada 5 min)</b>

<b>Pozo:</b> {nombre}
<b>Caudal:</b> 0 L/s
<b>Estado:</b> DETENIDO
<b>📅 {fecha_hora_completa} (hora Chile)</b>
<b>⏱️ El pozo continúa detenido</b>"""
                    
                    enviar_telegram(mensaje)
                    ultima_alerta_detenido[nombre] = tiempo_actual
                
                estado_pozos[nombre] = "detenido"
                continue

            if 0 < caudal < 10:
                ultima_alerta = ultima_alerta_critico.get(nombre, 0)
                if tiempo_actual - ultima_alerta >= TIEMPO_ENTRE_ALERTAS:
                    mensaje = f"""<b>🔴 CAUDAL CRÍTICO (alerta cada 5 min)</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>Umbral:</b> Menor a 10 L/s
<b>📅 {fecha_hora_completa} (hora Chile)</b>
<b>⏱️ El caudal sigue crítico</b>"""
                    
                    enviar_telegram(mensaje)
                    ultima_alerta_critico[nombre] = tiempo_actual
                
                estado_pozos[nombre] = "critico"
                continue

            if 10 <= caudal < 30:
                if estado_anterior != "bajo":
                    mensaje = f"""<b>⚠️ CAUDAL BAJO</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>Umbral:</b> Menor a 30 L/s
<b>📅 {fecha_hora_completa} (hora Chile)</b>"""
                    
                    enviar_telegram(mensaje)
                
                estado_pozos[nombre] = "bajo"
                continue

            if caudal >= 30:
                if estado_anterior in ["bajo", "critico", "detenido"]:
                    mensaje = f"""<b>✅ POZO NORMALIZADO</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>Estado:</b> Operación normal
<b>📅 {fecha_hora_completa} (hora Chile)</b>"""
                    
                    enviar_telegram(mensaje)
                    
                    if nombre in ultima_alerta_detenido:
                        del ultima_alerta_detenido[nombre]
                    if nombre in ultima_alerta_critico:
                        del ultima_alerta_critico[nombre]
                
                estado_pozos[nombre] = "normal"
        
        print(f"✅ [{hora_chile}] Verificación completada")
                
    except Exception as e:
        print(f"❌ [{hora_chile}] Error en verificación: {e}")
        traceback.print_exc()

# ==============================
# CONFIGURAR BOT DE TELEGRAM (VERSIÓN FINAL)
# ==============================
async def run_bot_polling():
    """Ejecuta el bot de Telegram de forma asíncrona"""
    global bot_instance_running, application_instance
    hora_chile = obtener_hora_chilena()
    
    with bot_instance_lock:
        if bot_instance_running:
            print(f"⚠️ [{hora_chile}] El bot ya está corriendo, ignorando nueva instancia")
            return
        bot_instance_running = True
    
    try:
        application_instance = Application.builder().token(TELEGRAM_TOKEN).build()
        
        application_instance.add_handler(CommandHandler("start", start))
        application_instance.add_handler(CommandHandler("ayuda", ayuda))
        application_instance.add_handler(CommandHandler("caudales", caudales))
        
        print(f"🤖 [{hora_chile}] Bot de Telegram iniciado")
        
        await application_instance.initialize()
        await application_instance.start()
        await application_instance.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=['message'],
            poll_interval=1.0,
            timeout=30
        )
        
        print(f"✅ [{hora_chile}] Bot de Telegram está escuchando comandos")
        
        # Mantener el bot corriendo
        while bot_instance_running:
            await asyncio.sleep(1)
            
    except Exception as e:
        print(f"❌ [{hora_chile}] Error en el bot de Telegram: {e}")
        traceback.print_exc()
    finally:
        with bot_instance_lock:
            bot_instance_running = False
        if application_instance:
            try:
                await application_instance.stop()
            except:
                pass

def iniciar_bot_telegram():
    """Inicia el bot de Telegram en un hilo separado"""
    hora_chile = obtener_hora_chilena()
    print(f"🔄 [{hora_chile}] Iniciando bot de Telegram...")
    
    with bot_instance_lock:
        if bot_instance_running:
            print(f"⚠️ [{hora_chile}] Bot ya estaba corriendo")
            return
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(run_bot_polling())
    except Exception as e:
        print(f"❌ [{hora_chile}] Error en el bot: {e}")
    finally:
        loop.close()

# ==============================
# FUNCIÓN PRINCIPAL PARA RENDER
# ==============================
driver = None

def signal_handler(signum, frame):
    """Manejador de señales para cerrar gracefulmente"""
    hora_chile = obtener_hora_chilena()
    print(f"\n🛑 [{hora_chile}] Señal {signum} recibida, cerrando gracefulmente...")
    if driver:
        try:
            driver.quit()
        except:
            pass
    liberar_lock()
    sys.exit(0)

def run_bot():
    """Función que ejecuta el bot principal"""
    global driver, bot_instance_running
    
    # Registrar manejadores de señales
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Registrar liberación de lock al salir
    atexit.register(liberar_lock)
    
    # Adquirir lock de archivo
    if not adquirir_lock():
        print("❌ Otra instancia ya está corriendo, saliendo...")
        return
    
    intentos = 0
    max_intentos = 3
    
    while intentos < max_intentos:
        hora_chile = obtener_hora_chilena()
        
        try:
            driver = create_driver()
            print(f"✅ [{hora_chile}] Driver creado")
            
            login()
            print(f"✅ [{hora_chile}] Login exitoso")
            
            time.sleep(2)
            
            # Iniciar bot solo si no está corriendo
            with bot_instance_lock:
                if not bot_instance_running:
                    bot_thread = threading.Thread(target=iniciar_bot_telegram, daemon=True)
                    bot_thread.start()
                    print(f"✅ [{hora_chile}] Bot de Telegram iniciado")
                else:
                    print(f"✅ [{hora_chile}] Bot ya estaba corriendo")
            
            time.sleep(5)
            intentos = 0
            
            print(f"🔄 [{hora_chile}] Iniciando monitoreo...")
            while True:
                try:
                    verificar_pozos()
                    print(f"⏱️ [{obtener_hora_chilena()}] Esperando 2 minutos...")
                    time.sleep(120)
                except Exception as e:
                    print(f"❌ [{obtener_hora_chilena()}] Error: {e}")
                    time.sleep(30)
                    
        except Exception as e:
            intentos += 1
            print(f"❌ [{hora_chile}] Error fatal (intento {intentos}/{max_intentos}): {e}")
            
            if intentos < max_intentos:
                time.sleep(60 * intentos)
            else:
                time.sleep(300)
                intentos = 0
        finally:
            if driver:
                try:
                    driver.quit()
                except:
                    pass

# ==============================
# SERVIDOR FLASK
# ==============================
app = Flask(__name__)

@app.route('/')
def home():
    hora_chile = obtener_hora_chilena()
    return f"Bot de monitoreo de pozos - Hora Chile: {hora_chile} - Instancia: {RENDER_INSTANCE_ID}"

@app.route('/health')
def health():
    return "OK", 200

# Punto de entrada
if __name__ != '__main__':
    hora_chile = obtener_hora_chilena()
    print(f"🚀 [{hora_chile}] Iniciando en producción (Render) - Instancia: {RENDER_INSTANCE_ID}")
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

if __name__ == "__main__":
    hora_chile = obtener_hora_chilena()
    print(f"🚀 [{hora_chile}] Modo desarrollo local")
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    app.run(host='0.0.0.0', port=5000, debug=False)
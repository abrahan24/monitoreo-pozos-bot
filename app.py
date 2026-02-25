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
from telegram.error import TelegramError, TimedOut, NetworkError

# ==============================
# CONFIGURACIÓN (USAR VARIABLES DE ENTORNO)
# ==============================
USERNAME = os.environ.get("LEM_USERNAME", "8.496.887-0")
PASSWORD = os.environ.get("LEM_PASSWORD", "8496887")
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8556172137:AAFZVZ5d5Xj5J4ergSIvkcV9EjARFdXFYrw")
CHAT_IDS_AUTORIZADOS = os.environ.get("CHAT_IDS", "5921135865").split(",")

LOGIN_URL = "http://login.lemsystem.cl/"
PANEL_URL = "http://optimus.lemsystem.cl/LemSense.php"

# Crear una sola instancia de Bot para reutilizar
bot = Bot(token=TELEGRAM_TOKEN)

# ==============================
# ESTADO POZOS
# ==============================
estado_pozos = {}
ultimos_caudales = {}
ultimo_reporte_automatico = 0
ultima_alerta_detenido = {}
ultima_alerta_critico = {}
TIEMPO_ENTRE_ALERTAS = 300

# ==============================
# ZONA HORARIA CHILE
# ==============================
try:
    import pytz
    CHILE_TZ = pytz.timezone('America/Santiago')
    PYTZ_AVAILABLE = True
    print("✅ pytz instalado correctamente, usando hora Chile")
except ImportError:
    PYTZ_AVAILABLE = False
    print("⚠️ pytz no instalado, instalando...")
    os.system("pip install pytz")
    time.sleep(2)
    try:
        import pytz
        CHILE_TZ = pytz.timezone('America/Santiago')
        PYTZ_AVAILABLE = True
        print("✅ pytz instalado ahora")
    except:
        PYTZ_AVAILABLE = False
        print("⚠️ No se pudo instalar pytz, usando hora UTC")

def obtener_hora_chilena():
    """Retorna la hora actual en formato HH:MM:SS con zona horaria de Chile"""
    if PYTZ_AVAILABLE:
        try:
            ahora_utc = datetime.now(pytz.UTC)
            ahora_chile = ahora_utc.astimezone(CHILE_TZ)
            return ahora_chile.strftime('%H:%M:%S')
        except:
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
    chrome_options.add_argument("--disable-setuid-sandbox")
    chrome_options.add_argument("--disable-extensions")
    
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
# FUNCIÓN SIMPLIFICADA PARA ENVIAR MENSAJES
# ==============================
def enviar_telegram(mensaje, chat_ids=None):
    """Envía mensaje a chats específicos de forma simplificada"""
    hora_chile = obtener_hora_chilena()
    
    if chat_ids is None:
        chat_ids = CHAT_IDS_AUTORIZADOS
    
    chat_ids = [chat_id.strip() for chat_id in chat_ids if chat_id.strip()]
    
    if not chat_ids:
        print(f"⚠️ [{hora_chile}] No hay chat IDs configurados")
        return
    
    for chat_id in chat_ids:
        try:
            # Crear un nuevo loop para cada mensaje
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Enviar mensaje
            loop.run_until_complete(
                bot.send_message(
                    chat_id=chat_id,
                    text=mensaje,
                    parse_mode='HTML'
                )
            )
            loop.close()
            print(f"✅ [{hora_chile}] Mensaje enviado a {chat_id}")
            
        except Exception as e:
            print(f"❌ [{hora_chile}] Error al enviar a {chat_id}: {e}")
        
        time.sleep(2)  # Pausa entre mensajes

# ==============================
# FUNCIONES DE LEM
# ==============================
def login():
    """Inicia sesión en el sistema LEM"""
    global driver
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

def enviar_reporte_automatico():
    """Envía un reporte automático cada 5 minutos con el estado de todos los pozos"""
    global ultimo_reporte_automatico
    hora_chile = obtener_hora_chilena()
    fecha_hora_completa = obtener_fecha_hora_chilena_completa()
    
    hora_actual = time.time()
    
    # INTERVALO DE 5 MINUTOS (300 segundos)
    INTERVALO_REPORTE = 300
    
    if hora_actual - ultimo_reporte_automatico >= INTERVALO_REPORTE:
        print(f"⏰ [{hora_chile}] Enviando reporte automático (cada 5 minutos)")
        
        if ultimos_caudales:
            # Construir mensaje del reporte
            mensaje = f"<b>📊 REPORTE CADA 5 MINUTOS</b>\n\n"
            mensaje += f"<b>📅 {fecha_hora_completa} (hora Chile)</b>\n\n"
            
            # Resumen
            detenidos = sum(1 for c in ultimos_caudales.values() if c == 0)
            criticos = sum(1 for c in ultimos_caudales.values() if 0 < c < 10)
            bajos = sum(1 for c in ultimos_caudales.values() if 10 <= c < 30)
            normales = sum(1 for c in ultimos_caudales.values() if c >= 30)
            
            mensaje += f"🔴 DETENIDOS: {detenidos}\n"
            mensaje += f"🔴 CRÍTICOS: {criticos}\n"
            mensaje += f"🟠 BAJOS: {bajos}\n"
            mensaje += f"🟢 NORMALES: {normales}\n\n"
            
            mensaje += f"<b>📋 DETALLE POR POZO:</b>\n"
            
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
            
            # Enviar como mensaje normal del bot
            enviar_telegram(mensaje)
            
            # Actualizar timestamp
            ultimo_reporte_automatico = hora_actual
            print(f"✅ [{hora_chile}] Reporte automático enviado")
        else:
            print(f"⚠️ [{hora_chile}] No hay datos de caudales para enviar reporte")

def verificar_pozos():
    """Verifica el estado de todos los pozos"""
    global ultimos_caudales, driver
    
    hora_chile = obtener_hora_chilena()
    fecha_hora_completa = obtener_fecha_hora_chilena_completa()
    
    print(f"\n🔍 [{hora_chile}] Verificando pozos")
    
    # ENVIAR REPORTE AUTOMÁTICO CADA 5 MINUTOS
    enviar_reporte_automatico()
    
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

            # DETENIDO (0 L/s)
            if caudal == 0:
                ultima_alerta = ultima_alerta_detenido.get(nombre, 0)
                if tiempo_actual - ultima_alerta >= TIEMPO_ENTRE_ALERTAS:
                    mensaje = f"""<b>🚨 POZO DETENIDO</b>

<b>Pozo:</b> {nombre}
<b>Caudal:</b> 0 L/s
<b>📅 {fecha_hora_completa} (hora Chile)</b>"""
                    
                    enviar_telegram(mensaje)
                    ultima_alerta_detenido[nombre] = tiempo_actual
                
                estado_pozos[nombre] = "detenido"
                continue

            # CRÍTICO (<10)
            if 0 < caudal < 10:
                ultima_alerta = ultima_alerta_critico.get(nombre, 0)
                if tiempo_actual - ultima_alerta >= TIEMPO_ENTRE_ALERTAS:
                    mensaje = f"""<b>🔴 CAUDAL CRÍTICO</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>📅 {fecha_hora_completa} (hora Chile)</b>"""
                    
                    enviar_telegram(mensaje)
                    ultima_alerta_critico[nombre] = tiempo_actual
                
                estado_pozos[nombre] = "critico"
                continue

            # BAJO (10–29)
            if 10 <= caudal < 30:
                if estado_anterior != "bajo":
                    mensaje = f"""<b>⚠️ CAUDAL BAJO</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>📅 {fecha_hora_completa} (hora Chile)</b>"""
                    
                    enviar_telegram(mensaje)
                
                estado_pozos[nombre] = "bajo"
                continue

            # NORMAL (>=30)
            if caudal >= 30:
                if estado_anterior in ["bajo", "critico", "detenido"]:
                    mensaje = f"""<b>✅ POZO NORMALIZADO</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
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
# CONFIGURAR BOT DE TELEGRAM
# ==============================
async def run_bot_polling():
    """Ejecuta el bot de Telegram de forma asíncrona"""
    try:
        # Configurar Application
        application = Application.builder().token(TELEGRAM_TOKEN).build()
        
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("ayuda", ayuda))
        application.add_handler(CommandHandler("caudales", caudales))
        
        print(f"🤖 Bot de Telegram iniciado")
        
        # Inicializar y empezar polling
        await application.initialize()
        await application.start()
        await application.updater.start_polling(
            drop_pending_updates=True,
            allowed_updates=['message'],
            poll_interval=1.0,
            timeout=10
        )
        
        print(f"✅ Bot de Telegram está escuchando comandos")
        
        # Mantener el bot corriendo
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        print(f"❌ Error en el bot de Telegram: {e}")
        traceback.print_exc()

def iniciar_bot_telegram():
    """Inicia el bot de Telegram en un hilo separado"""
    print(f"🔄 Iniciando bot de Telegram...")
    
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(run_bot_polling())
    except Exception as e:
        print(f"❌ Error en el bot: {e}")
    finally:
        loop.close()

# ==============================
# VARIABLES GLOBALES
# ==============================
driver = None
bot_thread_started = False

# ==============================
# FUNCIÓN PRINCIPAL
# ==============================
def run_bot():
    """Función que ejecuta el bot principal"""
    global driver, bot_thread_started
    
    print("🚀 Iniciando bot principal...")
    
    intentos = 0
    max_intentos = 5
    
    while intentos < max_intentos:
        hora_chile = obtener_hora_chilena()
        
        try:
            # Crear driver
            driver = create_driver()
            print(f"✅ [{hora_chile}] Driver creado")
            
            # Iniciar sesión
            login()
            print(f"✅ [{hora_chile}] Login exitoso")
            
            # Iniciar bot de Telegram solo una vez
            if not bot_thread_started:
                bot_thread = threading.Thread(target=iniciar_bot_telegram, daemon=True)
                bot_thread.start()
                bot_thread_started = True
                print(f"✅ [{hora_chile}] Bot de Telegram iniciado")
                time.sleep(5)  # Esperar a que el bot se inicie
            
            # Resetear contador de intentos
            intentos = 0
            
            print(f"🔄 [{hora_chile}] Iniciando monitoreo...")
            while True:
                try:
                    verificar_pozos()
                    print(f"⏱️ [{obtener_hora_chilena()}] Esperando 2 minutos...")
                    time.sleep(120)  # 2 minutos
                    
                except Exception as e:
                    print(f"❌ [{obtener_hora_chilena()}] Error en monitoreo: {e}")
                    time.sleep(30)  # Esperar 30 segundos antes de reintentar
                    
        except Exception as e:
            intentos += 1
            print(f"❌ [{hora_chile}] Error fatal (intento {intentos}/{max_intentos}): {e}")
            traceback.print_exc()
            
            # Cerrar driver si existe
            if driver:
                try:
                    driver.quit()
                except:
                    pass
                driver = None
            
            if intentos < max_intentos:
                tiempo_espera = 60 * intentos
                print(f"⏳ Esperando {tiempo_espera} segundos antes de reintentar...")
                time.sleep(tiempo_espera)
            else:
                print("❌ Demasiados intentos fallidos. Esperando 10 minutos...")
                time.sleep(600)  # 10 minutos
                intentos = 0  # Reiniciar contador

# ==============================
# SERVIDOR FLASK
# ==============================
app = Flask(__name__)

@app.route('/')
def home():
    hora_chile = obtener_hora_chilena()
    num_pozos = len(ultimos_caudales) if ultimos_caudales else 0
    return f"Bot de monitoreo de pozos activo<br>Hora Chile: {hora_chile}<br>Pozos monitoreados: {num_pozos}"

@app.route('/health')
def health():
    return "OK", 200

# ==============================
# PUNTO DE ENTRADA
# ==============================
if __name__ != '__main__':
    # Modo producción (Gunicorn)
    print(f"🚀 Iniciando en modo producción (Render)")
    # Forzar un solo worker
    os.environ['WEB_CONCURRENCY'] = '1'
    # Iniciar bot en un hilo
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()

if __name__ == "__main__":
    # Modo desarrollo local
    print(f"🚀 Modo desarrollo local")
    os.environ['WEB_CONCURRENCY'] = '1'
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    app.run(host='0.0.0.0', port=5000, debug=False)
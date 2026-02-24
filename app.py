import time
import re
import asyncio
import threading
import os
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

LOGIN_URL = "http://login.lemsystem.cl/"
PANEL_URL = "http://optimus.lemsystem.cl/LemSense.php"

bot = Bot(token=TELEGRAM_TOKEN)

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
# CONFIGURACIÓN DE SELENIUM PARA RENDER
# ==============================
def create_driver():
    """Crea y configura el driver de Chrome para Render"""
    print("🔧 Configurando Chrome driver...")
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
    
    # Ruta del binario de Chrome en el contenedor
    chrome_options.binary_location = "/usr/bin/google-chrome"
    
    try:
        # Usar ChromeDriver instalado por Docker
        service = Service('/usr/local/bin/chromedriver')
        driver = webdriver.Chrome(service=service, options=chrome_options)
        print("✅ Chrome driver creado exitosamente")
        return driver
    except Exception as e:
        print(f"❌ Error creando driver: {e}")
        # Fallback: intentar con webdriver-manager
        try:
            print("🔄 Intentando con webdriver-manager...")
            service = Service(ChromeDriverManager().install())
            driver = webdriver.Chrome(service=service, options=chrome_options)
            return driver
        except Exception as e2:
            print(f"❌ Error también con webdriver-manager: {e2}")
            raise e2

# ==============================
# FUNCIONES DE TELEGRAM (sin cambios)
# ==============================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id in CHAT_IDS_AUTORIZADOS:
        await update.message.reply_text(
            "🤖 *Sistema de Monitoreo de Pozos*\n\n"
            "Comandos disponibles:\n"
            "/caudales - Ver estado actual de todos los pozos\n"
            "/ayuda - Mostrar esta ayuda",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("⛔ No autorizado")

async def ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)

async def caudales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    
    if chat_id not in CHAT_IDS_AUTORIZADOS:
        await update.message.reply_text("⛔ No autorizado")
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
    
    mensaje += f"\n🕐 Actualizado: {time.strftime('%H:%M:%S')}"
    
    await update.message.reply_text(mensaje, parse_mode='HTML')

# ==============================
# FUNCIÓN PARA ENVIAR MENSAJES
# ==============================
def enviar_telegram(mensaje, chat_ids=None):
    if chat_ids is None:
        chat_ids = CHAT_IDS_AUTORIZADOS
    
    for chat_id in chat_ids:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot.send_message(
                chat_id=chat_id, 
                text=mensaje,
                parse_mode='HTML'
            ))
            loop.close()
            print(f"✅ Mensaje enviado a chat {chat_id}")
        except Exception as e:
            print(f"❌ Error al enviar a {chat_id}: {e}")
        
        time.sleep(1)

# ==============================
# FUNCIONES DE LEM
# ==============================
def login():
    """Inicia sesión en el sistema LEM"""
    print("🔑 Iniciando sesión...")
    driver.get(LOGIN_URL)
    time.sleep(3)

    driver.find_element(By.XPATH, "//input[@placeholder='Usuario']").send_keys(USERNAME)
    driver.find_element(By.XPATH, "//input[@placeholder='Contraseña']").send_keys(PASSWORD)
    driver.find_element(By.ID, "loading").click()

    time.sleep(5)
    print("✅ Sesión iniciada correctamente")
    
    enviar_telegram("🤖 *Sistema de monitoreo de pozos iniciado en Render*\n\nUsa /caudales para ver el estado actual")

def enviar_reporte_horario():
    """Envía un reporte cada hora con el estado de todos los pozos"""
    global ultimo_reporte_horario
    
    hora_actual = time.time()
    if hora_actual - ultimo_reporte_horario >= 3600:
        if ultimos_caudales:
            mensaje = f"<b>📊 REPORTE HORARIO - {time.strftime('%H:%M')}</b>\n\n"
            
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
            print(f"📊 Reporte horario enviado - {time.strftime('%H:%M')}")

def verificar_pozos():
    """Verifica el estado de todos los pozos"""
    global ultimos_caudales
    
    print(f"\n🔍 Verificando pozos - {time.strftime('%H:%M:%S')}")
    
    driver.get(PANEL_URL)
    time.sleep(5)

    try:
        contenedor = driver.find_element(By.ID, "insidethepopup_alerta")
        pozos = contenedor.find_elements(By.CLASS_NAME, "col-lg-2")
        
        if not pozos:
            print("⚠️ No se encontraron pozos")
            return

        for pozo in pozos:
            texto = pozo.text
            nombre = texto.split("\n")[0]

            match = re.search(r"Caudal:\s*([0-9\.]+)", texto)
            if not match:
                continue

            caudal = float(match.group(1))
            print(f"📊 {nombre} → {caudal} L/s")

            ultimos_caudales[nombre] = caudal
            estado_anterior = estado_pozos.get(nombre, "normal")
            tiempo_actual = time.time()

            # DETENIDO (0 L/s)
            if caudal == 0:
                ultima_alerta = ultima_alerta_detenido.get(nombre, 0)
                if tiempo_actual - ultima_alerta >= TIEMPO_ENTRE_ALERTAS:
                    mensaje = f"""<b>🚨 POZO DETENIDO (alerta cada 5 min)</b>

<b>Pozo:</b> {nombre}
<b>Caudal:</b> 0 L/s
<b>Estado:</b> DETENIDO
<b>Hora:</b> {time.strftime('%H:%M:%S')}
<b>⏱️ El pozo continúa detenido</b>"""
                    
                    enviar_telegram(mensaje)
                    ultima_alerta_detenido[nombre] = tiempo_actual
                    print(f"⏰ Alerta cada 5 min para {nombre} (DETENIDO)")
                
                estado_pozos[nombre] = "detenido"
                continue

            # CRÍTICO (<10)
            if 0 < caudal < 10:
                ultima_alerta = ultima_alerta_critico.get(nombre, 0)
                if tiempo_actual - ultima_alerta >= TIEMPO_ENTRE_ALERTAS:
                    mensaje = f"""<b>🔴 CAUDAL CRÍTICO (alerta cada 5 min)</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>Umbral:</b> Menor a 10 L/s
<b>Hora:</b> {time.strftime('%H:%M:%S')}
<b>⏱️ El caudal sigue crítico</b>"""
                    
                    enviar_telegram(mensaje)
                    ultima_alerta_critico[nombre] = tiempo_actual
                    print(f"⏰ Alerta cada 5 min para {nombre} (CRÍTICO: {caudal} L/s)")
                
                estado_pozos[nombre] = "critico"
                continue

            # BAJO (10–29)
            if 10 <= caudal < 30:
                if estado_anterior != "bajo":
                    mensaje = f"""<b>⚠️ CAUDAL BAJO</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>Umbral:</b> Menor a 30 L/s
<b>Hora:</b> {time.strftime('%H:%M:%S')}"""
                    
                    enviar_telegram(mensaje)
                    print(f"📩 Alerta única para {nombre} (BAJO: {caudal} L/s)")
                
                estado_pozos[nombre] = "bajo"
                continue

            # NORMAL (>=30)
            if caudal >= 30:
                if estado_anterior in ["bajo", "critico", "detenido"]:
                    mensaje = f"""<b>✅ POZO NORMALIZADO</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>Estado:</b> Operación normal
<b>Hora:</b> {time.strftime('%H:%M:%S')}"""
                    
                    enviar_telegram(mensaje)
                    print(f"📩 Alerta única para {nombre} (NORMALIZADO)")
                    
                    if nombre in ultima_alerta_detenido:
                        del ultima_alerta_detenido[nombre]
                    if nombre in ultima_alerta_critico:
                        del ultima_alerta_critico[nombre]
                
                estado_pozos[nombre] = "normal"
        
        enviar_reporte_horario()
                
    except Exception as e:
        print(f"❌ Error en verificación: {e}")
        raise e

# ==============================
# CONFIGURAR BOT DE TELEGRAM
# ==============================
def iniciar_bot_telegram():
    """Inicia el bot de Telegram en un hilo separado"""
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", ayuda))
    application.add_handler(CommandHandler("caudales", caudales))
    
    print("🤖 Bot de Telegram iniciado")
    application.run_polling()

# ==============================
# FUNCIÓN PRINCIPAL PARA RENDER
# ==============================
driver = None

def run_bot():
    """Función que ejecuta el bot principal"""
    global driver
    try:
        # Crear driver
        driver = create_driver()
        
        # Iniciar sesión
        login()
        
        # Iniciar bot de Telegram en un hilo
        bot_thread = threading.Thread(target=iniciar_bot_telegram, daemon=True)
        bot_thread.start()
        
        # Bucle principal de monitoreo
        print("🔄 Iniciando monitoreo continuo (cada 2 minutos)...")
        while True:
            try:
                verificar_pozos()
                print("⏱️ Esperando 2 minutos para la próxima verificación...")
                time.sleep(120)
            except Exception as e:
                print(f"❌ Error en el bucle principal: {e}")
                print("🔄 Reintentando en 30 segundos...")
                time.sleep(30)
    except Exception as e:
        print(f"❌ Error fatal: {e}")
    finally:
        if driver:
            driver.quit()

# ==============================
# SERVIDOR FLASK PARA HEALTH CHECK
# ==============================
app = Flask(__name__)

@app.route('/')
def home():
    return "Bot de monitoreo de pozos está funcionando."

@app.route('/health')
def health():
    return "OK", 200

# Punto de entrada para Gunicorn
if __name__ != '__main__':
    # En producción (Render), iniciar el bot en un hilo
    print("🚀 Iniciando en modo producción (Render)...")
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    print("✅ Bot iniciado en segundo plano")

# Para ejecución local
if __name__ == "__main__":
    print("🚀 Modo desarrollo local...")
    # Iniciar el bot en un hilo
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    # Ejecutar Flask para pruebas locales
    app.run(host='0.0.0.0', port=5000, debug=False)
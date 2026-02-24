import time
import re
import asyncio
import threading
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from telegram.error import TelegramError

# ==============================
# CONFIGURACIÓN LEM
# ==============================

USERNAME = "8.496.887-0"
PASSWORD = "8496887"

LOGIN_URL = "http://login.lemsystem.cl/"
PANEL_URL = "http://optimus.lemsystem.cl/LemSense.php"

# ==============================
# CONFIGURACIÓN TELEGRAM
# ==============================

TELEGRAM_TOKEN = "8556172137:AAFZVZ5d5Xj5J4ergSIvkcV9EjARFdXFYrw"
CHAT_IDS_AUTORIZADOS = [
    "5921135865",  # Tu ID de Telegram
]

bot = Bot(token=TELEGRAM_TOKEN)

# ==============================
# ESTADO POZOS
# ==============================

estado_pozos = {}  # Estado actual de los pozos
ultimos_caudales = {}  # Para guardar los últimos caudales leídos
ultimo_reporte_horario = 0  # Timestamp del último reporte

# ==============================
# CONTROL DE ALERTAS CADA 5 MIN
# ==============================

ultima_alerta_detenido = {}  # Control para pozos DETENIDOS (0 L/s)
ultima_alerta_critico = {}   # Control para pozos CRÍTICOS (<10 L/s)
TIEMPO_ENTRE_ALERTAS = 300   # 300 segundos = 5 minutos

# ==============================
# CHROME
# ==============================

options = Options()
options.add_argument("--headless=new")
options.add_argument("--no-sandbox")
options.add_argument("--disable-dev-shm-usage")

driver = webdriver.Chrome(
    service=Service(ChromeDriverManager().install()),
    options=options
)

# ==============================
# FUNCIONES DE TELEGRAM (COMANDOS)
# ==============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /start"""
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
    """Comando /ayuda"""
    await start(update, context)

async def caudales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /caudales - Muestra el estado actual de todos los pozos"""
    chat_id = str(update.effective_chat.id)
    
    if chat_id not in CHAT_IDS_AUTORIZADOS:
        await update.message.reply_text("⛔ No autorizado")
        return
    
    if not ultimos_caudales:
        await update.message.reply_text("🔄 Aún no hay datos disponibles. Espera la próxima actualización...")
        return
    
    # Construir mensaje con todos los caudales
    mensaje = "<b>📊 ESTADO ACTUAL DE POZOS</b>\n\n"
    
    for nombre, caudal in ultimos_caudales.items():
        # Determinar emoji según el caudal
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
    """Envía mensaje a chats específicos o a todos los autorizados"""
    if chat_ids is None:
        chat_ids = CHAT_IDS_AUTORIZADOS
    
    for chat_id in chat_ids:
        try:
            # CORREGIDO: Crear nuevo event loop correctamente
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(bot.send_message(
                chat_id=chat_id, 
                text=mensaje,
                parse_mode='HTML'
            ))
            loop.close()
            print(f"✅ Mensaje enviado a chat {chat_id}")
        except TelegramError as e:
            print(f"❌ Error de Telegram con chat {chat_id}: {e}")
        except Exception as e:
            print(f"❌ Error inesperado con chat {chat_id}: {e}")
        
        time.sleep(1)

# ==============================
# LOGIN
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
    
    enviar_telegram("🤖 *Sistema de monitoreo de pozos iniciado*\n\nUsa /caudales para ver el estado actual")

# ==============================
# REPORTE HORARIO
# ==============================

def enviar_reporte_horario():
    """Envía un reporte cada hora con el estado de todos los pozos"""
    global ultimo_reporte_horario
    
    hora_actual = time.time()
    # Enviar solo si pasó al menos 1 hora (3600 segundos)
    if hora_actual - ultimo_reporte_horario >= 3600:
        if ultimos_caudales:
            # Construir mensaje del reporte horario
            mensaje = f"<b>📊 REPORTE HORARIO - {time.strftime('%H:%M')}</b>\n\n"
            
            # Contar estados
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

# ==============================
# VERIFICAR POZOS
# ==============================

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

            # Guardar en ultimos_caudales para el comando /caudales
            ultimos_caudales[nombre] = caudal

            estado_anterior = estado_pozos.get(nombre, "normal")
            tiempo_actual = time.time()

            # 🔴 DETENIDO (0 L/s) - Alerta CADA 5 MINUTOS
            if caudal == 0:
                # CORREGIDO: Completar la línea que estaba truncada
                ultima_alerta = ultima_alerta_detenido.get(nombre, 0)
                
                # Enviar alerta si pasaron 5 minutos desde la última
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

            # 🔴 CRÍTICO (<10) - Alerta CADA 5 MINUTOS
            if 0 < caudal < 10:
                # Verificar última alerta para este pozo
                ultima_alerta = ultima_alerta_critico.get(nombre, 0)
                
                # Enviar alerta si pasaron 5 minutos desde la última
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

            # 🟠 BAJO (10–29) - Solo cuando cambia
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

            # 🟢 NORMAL (>=30) - Solo cuando cambia
            if caudal >= 30:
                if estado_anterior in ["bajo", "critico", "detenido"]:
                    mensaje = f"""<b>✅ POZO NORMALIZADO</b>

<b>Pozo:</b> {nombre}
<b>Caudal actual:</b> {caudal} L/s
<b>Estado:</b> Operación normal
<b>Hora:</b> {time.strftime('%H:%M:%S')}"""
                    
                    enviar_telegram(mensaje)
                    print(f"📩 Alerta única para {nombre} (NORMALIZADO)")
                    
                    # Limpiar alertas periódicas cuando se normaliza
                    if nombre in ultima_alerta_detenido:
                        del ultima_alerta_detenido[nombre]
                    if nombre in ultima_alerta_critico:
                        del ultima_alerta_critico[nombre]
                
                estado_pozos[nombre] = "normal"
        
        # Verificar si es hora de enviar reporte horario
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
    
    # Agregar comandos
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("ayuda", ayuda))
    application.add_handler(CommandHandler("caudales", caudales))
    
    # Iniciar bot
    print("🤖 Bot de Telegram iniciado")
    application.run_polling()

# ==============================
# FUNCIÓN PRINCIPAL
# ==============================

def main():
    """Función principal del programa"""
    try:
        # Iniciar sesión en LEM
        login()
        
        # Iniciar bot de Telegram en un hilo separado
        bot_thread = threading.Thread(target=iniciar_bot_telegram, daemon=True)
        bot_thread.start()
        
        # Bucle principal de monitoreo
        print("🔄 Iniciando monitoreo continuo (cada 2 minutos)...")
        while True:
            try:
                verificar_pozos()
                print("⏱️ Esperando 2 minutos para la próxima verificación...")
                time.sleep(120)  # 2 minutos
            except Exception as e:
                print(f"❌ Error en el bucle principal: {e}")
                print("🔄 Reintentando en 30 segundos...")
                time.sleep(30)
                
    except KeyboardInterrupt:
        print("\n👋 Programa detenido por el usuario")
    except Exception as e:
        print(f"❌ Error fatal: {e}")
    finally:
        driver.quit()
        print("🛑 Navegador cerrado")

if __name__ == "__main__":
    main()
# ==========================
# COMANDOS TELEGRAM
# ==========================

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from config import ADMIN_ID
from core.monitor import Monitor
from core.riego import formatear_lista_kc_uva
from core.utils import ahora, ahora_dt, estado_caudal
from data.sectores import SECTORES_RIEGO


async def cmd_caudales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    monitor: Monitor = context.application.bot_data["monitor"]

    if not monitor.ultimos:
        await update.message.reply_text("🔄 Aún no hay datos disponibles...")
        return

    mensaje = "<b>📊 ESTADO ACTUAL</b>\n\n"

    for nombre, valor in sorted(monitor.ultimos.items()):
        estado, emoji = estado_caudal(valor)
        mensaje += f"<b>{nombre}:</b> {valor} L/s - {emoji} {estado}\n"

    mensaje += f"\n🕐 {ahora()}"

    await update.message.reply_text(mensaje, parse_mode="HTML")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    monitor: Monitor = context.application.bot_data["monitor"]

    uptime = "N/A"
    if monitor.inicio_browser:
        segundos = int((ahora_dt() - monitor.inicio_browser).total_seconds())
        uptime = f"{segundos // 60} min"

    mensaje = (
        "<b>🤖 STATUS DEL BOT</b>\n\n"
        f"🟢 Navegador activo: {'Sí' if monitor.browser else 'No'}\n"
        f"⏱ Uptime navegador: {uptime}\n"
        f"📊 Últimos pozos cargados: {len(monitor.ultimos)}\n"
        f"⚠ Fallos consecutivos: {monitor.fallos_consecutivos}\n"
        f"🕐 {ahora()}"
    )

    await update.message.reply_text(mensaje, parse_mode="HTML")


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if str(update.effective_chat.id) != str(ADMIN_ID):
        await update.message.reply_text("⛔ No autorizado")
        return

    monitor: Monitor = context.application.bot_data["monitor"]
    usuario = update.effective_user.full_name if update.effective_user else "Desconocido"
    ahora_txt = ahora()

    await update.message.reply_text("♻ Reiniciando navegador...")

    try:
        await monitor.iniciar()
        monitor.fallos_consecutivos = 0

        mensaje_ok = (
            f"✅ <b>Navegador reiniciado correctamente</b>\n\n"
            f"👤 Solicitado por: {usuario}\n"
            f"🕐 {ahora_txt}"
        )

        await context.application.bot.send_message(
            chat_id=ADMIN_ID,
            text=mensaje_ok,
            parse_mode="HTML"
        )

    except Exception as e:
        mensaje_error = (
            f"❌ <b>Error al reiniciar</b>\n\n"
            f"{str(e)}\n"
            f"🕐 {ahora_txt}"
        )

        await context.application.bot.send_message(
            chat_id=ADMIN_ID,
            text=mensaje_error,
            parse_mode="HTML"
        )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(f"Tu chat ID es: {update.effective_chat.id}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("⚠ Error global de Telegram:", context.error)

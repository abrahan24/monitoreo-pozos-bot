# ==========================
# COMANDOS TELEGRAM
# ==========================

from telegram import Update
from telegram.ext import ContextTypes

from config import ADMIN_ID
from core.monitor import Monitor
from core.utils import ahora, ahora_dt, estado_caudal

DIVISOR = "━━━━━━━━━━━━━━━━━━━━"


async def cmd_caudales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    monitor: Monitor = context.application.bot_data["monitor"]

    if not monitor.ultimos:
        await update.message.reply_text("Aún no hay datos cargados.")
        return

    lineas = ["<b>📊 Estado actual</b>", "", DIVISOR, ""]

    for nombre, valor in sorted(monitor.ultimos.items()):
        estado, emoji = estado_caudal(valor)
        lineas.extend([
            f"<b>{nombre}</b>",
            f"• Caudal: <b>{valor} L/s</b>",
            f"• Estado: {emoji} <b>{estado}</b>",
            DIVISOR,
            "",
        ])

    lineas.extend([f"🕐 {ahora()}"])
    await update.message.reply_text("\n".join(lineas), parse_mode="HTML")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    monitor: Monitor = context.application.bot_data["monitor"]

    uptime = "N/A"
    if monitor.inicio_browser:
        segundos = int((ahora_dt() - monitor.inicio_browser).total_seconds())
        uptime = f"{segundos // 60} min"

    mensaje = (
        "<b>🤖 Estado del bot</b>\n\n"
        f"{DIVISOR}\n"
        "<b>Navegador</b>\n"
        f"• Activo: {'Sí' if monitor.browser else 'No'}\n"
        f"• Uptime: {uptime}\n\n"
        "<b>Monitoreo</b>\n"
        f"• Pozos cargados: {len(monitor.ultimos)}\n"
        f"• Fallos consecutivos: {monitor.fallos_consecutivos}\n"
        f"{DIVISOR}\n\n"
        f"🕐 {ahora()}"
    )

    await update.message.reply_text(mensaje, parse_mode="HTML")


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return

    if str(update.effective_chat.id) != str(ADMIN_ID):
        await update.message.reply_text("No autorizado. Este comando es solo para el administrador.")
        return

    monitor: Monitor = context.application.bot_data["monitor"]
    usuario = update.effective_user.full_name if update.effective_user else "Desconocido"
    ahora_txt = ahora()

    await update.message.reply_text("Reiniciando navegador...")

    try:
        await monitor.iniciar()
        monitor.fallos_consecutivos = 0

        mensaje_ok = (
            f"✅ <b>Navegador reiniciado correctamente</b>\n\n"
            f"• Solicitado por: {usuario}\n"
            f"• Fecha: {ahora_txt}\n\n"
            f"{DIVISOR}\n"
            "El monitoreo continuará automáticamente."
        )

        await context.application.bot.send_message(
            chat_id=ADMIN_ID,
            text=mensaje_ok,
            parse_mode="HTML",
        )

    except Exception as e:
        mensaje_error = (
            f"❌ <b>Error al reiniciar</b>\n\n"
            f"{str(e)}\n"
            f"\n{DIVISOR}\n"
            f"🕐 {ahora_txt}"
        )

        await context.application.bot.send_message(
            chat_id=ADMIN_ID,
            text=mensaje_error,
            parse_mode="HTML",
        )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return
    await update.message.reply_text(f"Tu chat ID es: {update.effective_chat.id}")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("Error global de Telegram:", context.error)

import asyncio
import logging

from telegram.ext import Application
from telegram.request import HTTPXRequest

from config import TOKEN, TELEGRAM_ALERT_CHAT_ID
from core.monitor import Monitor
from core.agroclima import ejecutar_cierre_con_mensaje
from bot.commands import error_handler
from bot.handlers import registrar_handlers


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)

logger = logging.getLogger(__name__)


async def tarea_revision_cierre(context):
    """
    Revisa si toca ejecutar el cierre mensual.
    Si corresponde, guarda y envía mensaje por Telegram.
    """
    try:
        mensaje = ejecutar_cierre_con_mensaje()

        if mensaje:
            logger.info("Se generó mensaje de cierre mensual: %s", mensaje)

            if TELEGRAM_ALERT_CHAT_ID:
                await context.bot.send_message(
                    chat_id=TELEGRAM_ALERT_CHAT_ID,
                    text=mensaje,
                    parse_mode="HTML",
                )
                logger.info("Mensaje de cierre mensual enviado a Telegram.")
            else:
                logger.warning("No existe TELEGRAM_ALERT_CHAT_ID; solo se registró en log.")

    except Exception:
        logger.exception("Error en tarea_revision_cierre")


def main():
    if not TOKEN:
        raise ValueError("Falta la variable de entorno TELEGRAM_TOKEN.")

    monitor = Monitor()

    request = HTTPXRequest(
        connect_timeout=30.0,
        read_timeout=60.0,
        write_timeout=30.0,
        pool_timeout=30.0,
    )

    app = (
        Application.builder()
        .token(TOKEN)
        .request(request)
        .get_updates_request(request)
        .build()
    )

    app.bot_data["monitor"] = monitor
    registrar_handlers(app)
    app.add_error_handler(error_handler)

    async def post_init(application: Application):
        await monitor.iniciar()
        asyncio.create_task(monitor.loop(application))

        # Revisión al iniciar el bot
        await tarea_revision_cierre(application)

        # Revisión automática cada hora
        if application.job_queue:
            application.job_queue.run_repeating(
                tarea_revision_cierre,
                interval=3600,   # cada 1 hora
                first=30,        # primera revisión 30 segundos después de iniciar
                name="revision_cierre_mensual",
            )
            logger.info("Job de revisión de cierre mensual programado cada 1 hora.")

        logger.info("🚀 Bot iniciado correctamente en Railway")

    app.post_init = post_init
    app.run_polling(drop_pending_updates=True, timeout=30)


if __name__ == "__main__":
    main()
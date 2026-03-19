import asyncio
from telegram.ext import Application
from telegram.request import HTTPXRequest

from config import TOKEN
from core.monitor import Monitor
from bot.commands import error_handler
from bot.handlers import registrar_handlers

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
        print("🚀 Bot iniciado correctamente en Railway")

    app.post_init = post_init
    app.run_polling(drop_pending_updates=True, timeout=30)

if __name__ == "__main__":
    main()
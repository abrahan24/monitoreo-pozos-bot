from telegram.ext import CommandHandler, ConversationHandler, MessageHandler, filters

from bot.commands import cmd_caudales, cmd_status, cmd_restart, cmd_id
from bot.conversations import (
    cmd_riego_inicio, cmd_riego_sector, cmd_riego_kc,
    cmd_riego_general_inicio, cmd_riego_general_kc,
    cmd_cancelar, RIEGO_SECTOR, RIEGO_KC, RIEGO_GENERAL_KC
)

def registrar_handlers(app):
    app.add_handler(CommandHandler("caudales", cmd_caudales))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("id", cmd_id))

    riego_handler = ConversationHandler(
        entry_points=[CommandHandler("riego", cmd_riego_inicio)],
        states={
            RIEGO_SECTOR: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_riego_sector)],
            RIEGO_KC: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_riego_kc)],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar)],
    )

    riego_general_handler = ConversationHandler(
        entry_points=[CommandHandler("riego_general", cmd_riego_general_inicio)],
        states={
            RIEGO_GENERAL_KC: [MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_riego_general_kc)],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar)],
    )

    app.add_handler(riego_handler)
    app.add_handler(riego_general_handler)
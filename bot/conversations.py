import re

from telegram import Update
from telegram.ext import ContextTypes, ConversationHandler

from config import FACTOR_LAVADO, LLUVIA_EFECTIVA_MM
from core.agroclima import obtener_eto_agroclima
from core.riego import (
    calcular_riego_sector,
    formatear_lista_kc_uva,
    formatear_resultado_riego,
    formatear_resultado_riego_general,
)
from data.sectores import SECTORES_RIEGO

RIEGO_SECTOR, RIEGO_KC, RIEGO_GENERAL_KC = range(3)


async def cmd_riego_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    sectores_txt = "\n".join(f"• {s}" for s in SECTORES_RIEGO.keys())

    await update.message.reply_text(
        "💧 Cálculo de riego semanal\n\n"
        "Selecciona el sector que deseas calcular:\n\n"
        f"{sectores_txt}\n\n"
        "Luego te mostraré una lista referencial de Kc para uva.\n\n"
        "Para salir usa /cancelar."
    )
    return RIEGO_SECTOR


async def cmd_riego_sector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    sector = re.sub(r"\s+", "", update.message.text.strip())

    if sector not in SECTORES_RIEGO:
        await update.message.reply_text(
            "⚠ Sector no válido.\n\n"
            "Escribe uno de estos sectores:\n\n"
            + "\n".join(f"• {s}" for s in SECTORES_RIEGO.keys())
        )
        return RIEGO_SECTOR

    context.user_data["sector_riego"] = sector

    mensaje_kc = (
        f"✅ Sector seleccionado: {sector}\n\n"
        f"{formatear_lista_kc_uva()}\n\n"
        "Ahora ingresa el valor de Kc.\n"
        "Ejemplo: 0.90"
    )

    await update.message.reply_text(mensaje_kc, parse_mode="HTML")
    return RIEGO_KC


async def cmd_riego_kc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    texto_kc = update.message.text.strip().replace(",", ".")

    try:
        kc = float(texto_kc)
    except ValueError:
        await update.message.reply_text(
            "⚠ Kc no válido. Ingresa un número.\nEjemplo: 0.9"
        )
        return RIEGO_KC

    if kc <= 0 or kc > 2:
        await update.message.reply_text(
            "⚠ El Kc parece fuera de rango.\nIngresa un valor razonable, por ejemplo 0.9"
        )
        return RIEGO_KC

    sector = context.user_data.get("sector_riego")

    try:
        etos = obtener_eto_agroclima()
        resultado = calcular_riego_sector(
            etos=etos,
            sector=sector,
            kc=kc,
            lluvia_efectiva_mm=LLUVIA_EFECTIVA_MM,
            factor_lavado=FACTOR_LAVADO,
        )

        mensaje = formatear_resultado_riego(resultado)
        await update.message.reply_text(mensaje, parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(f"❌ Error al calcular riego:\n{str(e)}")

    context.user_data.pop("sector_riego", None)
    return ConversationHandler.END


async def cmd_cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    context.user_data.pop("sector_riego", None)
    await update.message.reply_text("Operación cancelada.")
    return ConversationHandler.END


async def cmd_riego_general_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    mensaje = (
        "💧 Cálculo de riego general semanal\n\n"
        f"{formatear_lista_kc_uva()}\n\n"
        "Ingresa el valor de Kc para calcular todos los sectores.\n"
        "Ejemplo: 0.90\n\n"
        "Para salir usa /cancelar."
    )

    await update.message.reply_text(mensaje, parse_mode="HTML")
    return RIEGO_GENERAL_KC


async def cmd_riego_general_kc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    texto_kc = update.message.text.strip().replace(",", ".")

    try:
        kc = float(texto_kc)
    except ValueError:
        await update.message.reply_text(
            "⚠ Kc no válido. Ingresa un número.\nEjemplo: 0.9"
        )
        return RIEGO_GENERAL_KC

    if kc <= 0 or kc > 2:
        await update.message.reply_text(
            "⚠ El Kc parece fuera de rango.\nIngresa un valor razonable, por ejemplo 0.9"
        )
        return RIEGO_GENERAL_KC

    try:
        etos = obtener_eto_agroclima()
        resultados = []

        for sector in SECTORES_RIEGO.keys():
            resultado = calcular_riego_sector(
                etos=etos,
                sector=sector,
                kc=kc,
                lluvia_efectiva_mm=LLUVIA_EFECTIVA_MM,
                factor_lavado=FACTOR_LAVADO,
            )
            resultados.append(resultado)

        mensaje = formatear_resultado_riego_general(resultados)
        await update.message.reply_text(mensaje, parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(f"❌ Error al calcular riego general:\n{str(e)}")

    return ConversationHandler.END

import os
import re
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

# ==========================
# CONFIGURACIÓN
# ==========================

USERNAME = os.getenv("LEM_USERNAME")
PASSWORD = os.getenv("LEM_PASSWORD")
TOKEN = os.getenv("TELEGRAM_TOKEN")
CHATS = [c.strip() for c in os.getenv("CHAT_IDS", "").split(",") if c.strip()]
ADMIN_ID = os.getenv("ADMIN_ID")

LOGIN_URL = "http://login.lemsystem.cl/"
PANEL_URL = "http://optimus.lemsystem.cl/LemSense.php"

CHECK_INTERVAL = 120
REPORTE_INTERVAL = 3600
RESTART_BROWSER_INTERVAL = 86400  # 24 horas

CHILE_TZ = ZoneInfo("America/Santiago")
AGROCLIMA_URL = "https://www.agroclima.cl/InfoInforme/Evapotranspiracion?codigo=270026"

SECTORES_RIEGO = {
    "1-5-10": {"mm_h": 1.50},
    "6-10-11": {"mm_h": 1.50},
    "8": {"mm_h": 1.50},
    "9": {"mm_h": 1.50},
    "13": {"mm_h": 1.50},
    "2-3": {"mm_h": 1.33},
    "4": {"mm_h": 1.33},
    "7": {"mm_h": 1.33},
    "12": {"mm_h": 1.33},
}

ETAPAS_UVA_KC = [
    ("Brotación", 0.30),
    ("Desarrollo inicial de brotes", 0.40),
    ("Prefloración", 0.50),
    ("Floración", 0.60),
    ("Cuaja", 0.70),
    ("Crecimiento de bayas", 0.80),
    ("Cierre de racimo", 0.90),
    ("Envero", 0.85),
    ("Maduración", 0.75),
    ("Postcosecha", 0.55),
]

FACTOR_LAVADO = 0.10
LLUVIA_EFECTIVA_MM = 0.0

RIEGO_SECTOR, RIEGO_KC, RIEGO_GENERAL_KC = range(3)

# ==========================
# UTILIDADES
# ==========================

def ahora() -> str:
    return datetime.now(CHILE_TZ).strftime("%d/%m/%Y %H:%M:%S")


def ahora_dt() -> datetime:
    return datetime.now(CHILE_TZ)


def estado_caudal(valor: float):
    if valor == 0:
        return "DETENIDO", "🔴"
    if valor < 10:
        return "CRÍTICO", "🔴"
    if valor < 30:
        return "BAJO", "🟠"
    return "NORMAL", "🟢"

def extraer_ult_dato_min(texto: str):
    """
    Extrae los minutos desde líneas tipo:
    'Ult. dato hace: 2 min'
    """
    match = re.search(r"Ult\.\s*dato\s*hace:\s*(\d+)\s*min", texto, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def extraer_numeros_eto(texto: str) -> list[float]:
    encontrados = re.findall(r"\d+[.,]\d+", texto)
    return [float(x.replace(",", ".")) for x in encontrados]


def obtener_eto_agroclima() -> list[float]:
    """
    Extrae valores de ETo desde Agroclima.
    Devuelve idealmente los últimos 7 valores válidos.
    """
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(AGROCLIMA_URL, headers=headers, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        raise ValueError(f"No se pudo conectar a Agroclima: {e}")

    soup = BeautifulSoup(r.text, "html.parser")
    texto = soup.get_text("\n", strip=True)

    patrones = [
        r"Evapotranspiración\s*\(mm/día\)\s*([0-9,\.\s]+)",
        r"Evapotranspiracion\s*\(mm/día\)\s*([0-9,\.\s]+)",
        r"Evapotranspiración Potencial\s*([0-9,\.\s]+)",
        r"Evapotranspiracion Potencial\s*([0-9,\.\s]+)",
    ]

    for patron in patrones:
        m = re.search(patron, texto, re.IGNORECASE)
        if m:
            valores = extraer_numeros_eto(m.group(1))
            valores = [v for v in valores if 0 <= v <= 15]
            if len(valores) >= 7:
                return valores[-7:]

    tablas = soup.find_all("table")
    for tabla in tablas:
        txt = tabla.get_text(" ", strip=True)
        if "Evapotranspir" in txt or "mm/día" in txt or "mm/dia" in txt:
            nums = extraer_numeros_eto(txt)
            nums = [v for v in nums if 0 <= v <= 15]
            if len(nums) >= 7:
                return nums[-7:]

    raise ValueError("No se pudo extraer la ETo desde Agroclima.")


def horas_a_hm(horas_float: float):
    horas = int(horas_float)
    minutos = int(round((horas_float - horas) * 60))

    if minutos == 60:
        horas += 1
        minutos = 0

    return horas, minutos


def calcular_riego_sector(
    etos: list[float],
    sector: str,
    kc: float,
    lluvia_efectiva_mm: float = 0.0,
    factor_lavado: float = 0.0
) -> dict:
    if len(etos) < 7:
        raise ValueError("Se requieren al menos 7 valores de ETo.")

    if sector not in SECTORES_RIEGO:
        raise ValueError("Sector no válido.")

    eto_semana = sum(etos[-7:])
    mm_h = SECTORES_RIEGO[sector]["mm_h"]

    etc_semana = eto_semana * kc
    etc_ajustada = max(0.0, etc_semana - lluvia_efectiva_mm)
    etc_ajustada *= (1 + factor_lavado)

    horas_semana = etc_ajustada / mm_h if mm_h > 0 else 0.0
    horas_dia = horas_semana / 7

    return {
        "sector": sector,
        "kc": round(kc, 2),
        "eto_semana": round(eto_semana, 2),
        "etc_semana": round(etc_semana, 2),
        "etc_ajustada": round(etc_ajustada, 2),
        "mm_h": mm_h,
        "horas_semana": round(horas_semana, 2),
        "horas_dia": round(horas_dia, 2),
    }


def formatear_resultado_riego(data: dict) -> str:
    hs, ms = horas_a_hm(data["horas_semana"])
    hd, md = horas_a_hm(data["horas_dia"])

    return (
        f"<b>💧 RIEGO SEMANAL</b>\n\n"
        f"<b>Sector:</b> {data['sector']}\n"
        f"<b>Kc:</b> {data['kc']}\n"
        f"<b>ETo acumulada 7 días:</b> {data['eto_semana']} mm\n"
        f"<b>ETc semanal:</b> {data['etc_semana']} mm\n"
        f"<b>Precipitación del sector:</b> {data['mm_h']} mm/h\n"
        f"<b>Riego semanal:</b> {hs} h {ms:02d} min\n"
        f"<b>Promedio diario:</b> {hd} h {md:02d} min\n\n"
        f"🕐 {ahora()}"
    )

def formatear_resultado_riego_general(resultados: list[dict]) -> str:
    lineas = ["<b>💧 RIEGO GENERAL SEMANAL</b>", ""]

    if resultados:
        lineas.append(f"<b>ETo acumulada 7 días:</b> {resultados[0]['eto_semana']} mm")
        lineas.append(f"<b>Kc aplicado:</b> {resultados[0]['kc']}")
        lineas.append("")

    for data in resultados:
        hs, ms = horas_a_hm(data["horas_semana"])
        hd, md = horas_a_hm(data["horas_dia"])

        lineas.append(
            f"<b>Sector {data['sector']}</b>\n"
            f"• Precipitación: {data['mm_h']} mm/h\n"
            f"• ETc semanal: {data['etc_semana']} mm\n"
            f"• Riego semanal: <b>{hs} h {ms:02d} min</b>\n"
            f"• Promedio diario: {hd} h {md:02d} min\n"
        )

    lineas.append(f"🕐 {ahora()}")
    return "\n".join(lineas)

def formatear_lista_kc_uva() -> str:
    lineas = ["<b>🍇 Kc referencial para uva</b>", ""]
    for etapa, kc in ETAPAS_UVA_KC:
        lineas.append(f"• {etapa}: <b>{kc:.2f}</b>")
    return "\n".join(lineas)

# ==========================
# MONITOR
# ==========================

class Monitor:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        self.ultimos = {}
        self.alertas = {}
        self.alertas_telemetria = {}
        self.telemetria_estado = {}
        self.intervalo_alerta_telemetria = 600  # 10 min entre alertas repetidas
        self.ultimo_reporte = None
        self.inicio_browser = None

        self.fallos_consecutivos = 0
        self.max_fallos = 3
        self.lock_reinicio = asyncio.Lock()

    async def cerrar_recursos(self):
        if self.context:
            try:
                await self.context.close()
            except Exception:
                pass
            self.context = None

        if self.browser:
            try:
                await self.browser.close()
            except Exception:
                pass
            self.browser = None

        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception:
                pass
            self.playwright = None

        self.page = None

    async def iniciar(self):
        async with self.lock_reinicio:
            await self.cerrar_recursos()

            self.playwright = await async_playwright().start()

            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )

            self.context = await self.browser.new_context()
            self.page = await self.context.new_page()

            await self.login()
            self.inicio_browser = ahora_dt()

            print("🟢 Navegador iniciado correctamente")

    async def login(self):
        if not USERNAME or not PASSWORD:
            raise ValueError("Faltan variables de entorno LEM_USERNAME o LEM_PASSWORD.")

        print("🔐 Iniciando sesión...")
        await self.page.goto(LOGIN_URL, timeout=60000)

        await self.page.fill("input[placeholder='Usuario']", USERNAME)
        await self.page.fill("input[placeholder='Contraseña']", PASSWORD)
        await self.page.click("#loading")
        await self.page.wait_for_timeout(5000)

    async def obtener_datos(self) -> dict:
        await self.page.goto(PANEL_URL, timeout=60000)
        await self.page.wait_for_timeout(5000)

        if "login" in self.page.url.lower():
            print("⚠ Sesión expirada. Reintentando login...")
            await self.login()
            await self.page.goto(PANEL_URL, timeout=60000)
            await self.page.wait_for_timeout(5000)

        elementos = await self.page.query_selector_all("#insidethepopup_alerta .col-lg-2")
        datos = {}

        for el in elementos:
            texto = await el.inner_text()
            nombre = texto.split("\n")[0].strip()

            match_caudal = re.search(r"Caudal:\s*([0-9\.]+)", texto)
            ult_dato_min = extraer_ult_dato_min(texto)

            datos[nombre] = {
                "caudal": float(match_caudal.group(1)) if match_caudal else 0.0,
                "ult_dato_min": ult_dato_min,
                "texto_crudo": texto,
            }

        return datos

    async def enviar(self, app: Application, mensaje: str):
        for chat in CHATS:
            try:
                await app.bot.send_message(
                    chat_id=chat,
                    text=mensaje,
                    parse_mode="HTML"
                )
            except Exception as e:
                print("Error Telegram:", e)

    async def procesar_alertas(self, app: Application, nombre: str, caudal: float, anterior):
        estado, emoji = estado_caudal(caudal)
        ahora_ts = ahora_dt()

        if estado in ["DETENIDO", "CRÍTICO"]:
            ultima_alerta = self.alertas.get(nombre)

            if not ultima_alerta or (ahora_ts - ultima_alerta).total_seconds() >= 120:
                mensaje = (
                    f"<b>{emoji} {estado}</b>\n\n"
                    f"<b>Pozo:</b> {nombre}\n"
                    f"<b>Caudal:</b> {caudal} L/s\n"
                    f"<b>📅 {ahora()}</b>"
                )
                await self.enviar(app, mensaje)
                self.alertas[nombre] = ahora_ts

        elif anterior is not None:
            estado_ant, _ = estado_caudal(anterior)
            if estado != estado_ant:
                mensaje = (
                    f"<b>{emoji} CAMBIO DE ESTADO</b>\n\n"
                    f"<b>Pozo:</b> {nombre}\n"
                    f"<b>Nuevo estado:</b> {estado}\n"
                    f"<b>Caudal:</b> {caudal} L/s\n"
                    f"<b>📅 {ahora()}</b>"
                )
                await self.enviar(app, mensaje)

    async def procesar_alerta_telemetria(self, app: Application, nombre: str, ult_dato_min):
        """
        Alerta cuando el último dato recibido supera 10 minutos.
        Envía recuperación cuando vuelve a <= 10 min.
        """
        if ult_dato_min is None:
            return

        ahora_ts = ahora_dt()
        caida = ult_dato_min > 10
        estado_anterior = self.telemetria_estado.get(nombre, False)

        # Si está caída la telemetría
        if caida:
            ultima_alerta = self.alertas_telemetria.get(nombre)

            if (not estado_anterior) or (
                not ultima_alerta or
                (ahora_ts - ultima_alerta).total_seconds() >= self.intervalo_alerta_telemetria
            ):
                mensaje = (
                    f"<b>📡 FALLA DE TELEMETRÍA</b>\n\n"
                    f"<b>Pozo:</b> {nombre}\n"
                    f"<b>Último dato hace:</b> {ult_dato_min} min\n"
                    f"<b>Estado:</b> Sin actualización reciente\n"
                    f"<b>📅 {ahora()}</b>"
                )
                await self.enviar(app, mensaje)
                self.alertas_telemetria[nombre] = ahora_ts

            self.telemetria_estado[nombre] = True

        # Si se recuperó
        else:
            if estado_anterior:
                mensaje = (
                    f"<b>✅ TELEMETRÍA RECUPERADA</b>\n\n"
                    f"<b>Pozo:</b> {nombre}\n"
                    f"<b>Último dato hace:</b> {ult_dato_min} min\n"
                    f"<b>📅 {ahora()}</b>"
                )
                await self.enviar(app, mensaje)

            self.telemetria_estado[nombre] = False

    async def reporte_horario(self, app: Application):
        ahora_actual = ahora_dt()

        if self.ultimo_reporte and \
           (ahora_actual - self.ultimo_reporte).total_seconds() < REPORTE_INTERVAL:
            return

        if not self.ultimos:
            return

        detenidos = sum(1 for v in self.ultimos.values() if v == 0)
        criticos = sum(1 for v in self.ultimos.values() if 0 < v < 10)
        bajos = sum(1 for v in self.ultimos.values() if 10 <= v < 30)
        normales = sum(1 for v in self.ultimos.values() if v >= 30)

        detalle = ""
        for nombre, caudal in sorted(self.ultimos.items()):
            _, emoji = estado_caudal(caudal)
            detalle += f"{emoji} <b>{nombre}</b>: {caudal} L/s\n"

        mensaje = (
            f"<b>📊 REPORTE HORARIO</b>\n\n"
            f"🔴 Detenidos: {detenidos}\n"
            f"🔴 Críticos: {criticos}\n"
            f"🟠 Bajos: {bajos}\n"
            f"🟢 Normales: {normales}\n\n"
            f"<b>📍 DETALLE POR POZO</b>\n"
            f"{detalle}\n"
            f"<b>📅 {ahora()}</b>"
        )

        await self.enviar(app, mensaje)
        self.ultimo_reporte = ahora_actual

    async def loop(self, app: Application):
        while True:
            try:
                if self.inicio_browser and \
                   (ahora_dt() - self.inicio_browser).total_seconds() > RESTART_BROWSER_INTERVAL:
                    print("♻ Reinicio preventivo programado")
                    await asyncio.sleep(2)
                    await self.iniciar()
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                datos = await self.obtener_datos()
                self.fallos_consecutivos = 0

                for nombre, info in datos.items():
                    caudal = info.get("caudal", 0.0)
                    ult_dato_min = info.get("ult_dato_min")

                    anterior = self.ultimos.get(nombre)
                    self.ultimos[nombre] = caudal

                    await self.procesar_alertas(app, nombre, caudal, anterior)
                    await self.procesar_alerta_telemetria(app, nombre, ult_dato_min)

                await self.reporte_horario(app)

            except Exception as e:
                print("❌ Error en scraping:", e)
                self.fallos_consecutivos += 1

                if self.fallos_consecutivos >= self.max_fallos:
                    print("⚠ Reiniciando navegador por fallos consecutivos...")
                    await asyncio.sleep(10)
                    try:
                        await self.iniciar()
                    except Exception as reinicio_error:
                        print("❌ Error reiniciando navegador:", reinicio_error)
                    self.fallos_consecutivos = 0
                else:
                    await asyncio.sleep(30)

            await asyncio.sleep(CHECK_INTERVAL)

# ==========================
# COMANDOS TELEGRAM
# ==========================

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


async def cmd_riego_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    sectores_txt = "\n".join(f"• {s}" for s in SECTORES_RIEGO.keys())

    await update.message.reply_text(
        "💧 Cálculo de riego semanal\n\n"
        "Ingresa el sector que deseas calcular:\n\n"
        f"{sectores_txt}\n\n"
        "Luego el bot te mostrará una lista referencial de Kc para uva.\n"
        "Para salir usa /cancelar"
    )
    return RIEGO_SECTOR


async def cmd_riego_sector(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    sector = re.sub(r"\s+", "", update.message.text.strip())

    if sector not in SECTORES_RIEGO:
        await update.message.reply_text(
            "⚠ Sector no válido.\n"
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
            factor_lavado=FACTOR_LAVADO
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
    await update.message.reply_text("❌ Operación cancelada.")
    return ConversationHandler.END

async def cmd_riego_general_inicio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message:
        return ConversationHandler.END

    mensaje = (
        "💧 Cálculo de riego general semanal\n\n"
        f"{formatear_lista_kc_uva()}\n\n"
        "Ingresa el valor de Kc para calcular todos los sectores.\n"
        "Ejemplo: 0.90\n\n"
        "Para salir usa /cancelar"
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
                factor_lavado=FACTOR_LAVADO
            )
            resultados.append(resultado)

        mensaje = formatear_resultado_riego_general(resultados)
        await update.message.reply_text(mensaje, parse_mode="HTML")

    except Exception as e:
        await update.message.reply_text(
            f"❌ Error al calcular riego general:\n{str(e)}"
        )

    return ConversationHandler.END

# ==========================
# MAIN
# ==========================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print("⚠ Error global de Telegram:", context.error)

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

    app.add_handler(CommandHandler("caudales", cmd_caudales))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("restart", cmd_restart))
    app.add_handler(CommandHandler("id", cmd_id))

    riego_handler = ConversationHandler(
        entry_points=[CommandHandler("riego", cmd_riego_inicio)],
        states={
            RIEGO_SECTOR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_riego_sector)
            ],
            RIEGO_KC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_riego_kc)
            ],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar)],
    )

    riego_general_handler = ConversationHandler(
        entry_points=[CommandHandler("riego_general", cmd_riego_general_inicio)],
        states={
            RIEGO_GENERAL_KC: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cmd_riego_general_kc)
            ],
        },
        fallbacks=[CommandHandler("cancelar", cmd_cancelar)],
    )

    app.add_handler(riego_handler)
    app.add_handler(riego_general_handler)
    app.add_error_handler(error_handler)

    async def post_init(application: Application):
        await monitor.iniciar()
        asyncio.create_task(monitor.loop(application))
        print("🚀 Bot iniciado correctamente en Railway")

    app.post_init = post_init
    
    app.run_polling(
    drop_pending_updates=True,
    timeout=30,
    )

if __name__ == "__main__":
    main()
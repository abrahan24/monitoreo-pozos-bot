import re
import asyncio
from playwright.async_api import async_playwright
from telegram.ext import Application

from config import (
    CHATS, USERNAME, PASSWORD, LOGIN_URL, PANEL_URL,
    CHECK_INTERVAL, REPORTE_INTERVAL, RESTART_BROWSER_INTERVAL
)
from core.utils import ahora, ahora_dt, estado_caudal, extraer_ult_dato_min

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
        caida = ult_dato_min > 15
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

        pozos_ordenados = sorted(self.ultimos.items(), key=lambda x: x[1])

        detalle = "\n\n".join(
            f"{estado_caudal(caudal)[1]} <b>{nombre}</b>\n"
            f"   ↳ Estado: <b>{estado_caudal(caudal)[0].capitalize()}</b>\n"
            f"   ↳ Caudal: <code>{caudal:.1f} L/s</code>"
            for nombre, caudal in pozos_ordenados
        )

        mensaje = (
            f"<b>📡 ESTADO OPERATIVO DE POZOS BUITRON (COPAYAPU)</b>\n"
            f"<i>Sistema de monitoreo automático</i>\n\n"
            f"<b>📍 Estado por pozo</b>\n"
            f"{detalle}\n\n"
            f"🕒 <b>Hora de monitoreo:</b> {ahora()}"
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
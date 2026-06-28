import re
import asyncio

from playwright.async_api import async_playwright
from telegram.ext import Application

from config import (
    CHATS,
    USERNAME,
    PASSWORD,
    LOGIN_URL,
    PANEL_URL,
    CHECK_INTERVAL,
    REPORTE_INTERVAL,
    RESTART_BROWSER_INTERVAL,
)
from core.utils import ahora, ahora_dt, estado_caudal, extraer_ult_dato_min


class Monitor:
    def __init__(self):
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None

        self.ultimos = {}
        self.detalles = {}
        self.alertas = {}
        self.alertas_telemetria = {}
        self.telemetria_estado = {}
        self.intervalo_alerta_telemetria = 600
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

            print("Navegador iniciado correctamente")

    async def login(self):
        if not USERNAME or not PASSWORD:
            raise ValueError("Faltan variables de entorno LEM_USERNAME o LEM_PASSWORD.")

        print("Iniciando sesión...")
        await self.page.goto(LOGIN_URL, timeout=60000)

        await self.page.fill("input[placeholder='Usuario']", USERNAME)
        await self.page.fill("input[placeholder='Contraseña']", PASSWORD)
        await self.page.click("#loading")
        await self.page.wait_for_timeout(5000)

    async def obtener_datos(self) -> dict:
        await self.page.goto(PANEL_URL, timeout=60000)
        await self.page.wait_for_timeout(5000)

        if "login" in self.page.url.lower():
            print("Sesión expirada. Reintentando login...")
            await self.login()
            await self.page.goto(PANEL_URL, timeout=60000)
            await self.page.wait_for_timeout(5000)

        elementos = await self.page.query_selector_all("#insidethepopup_alerta .col-lg-2")
        datos = {}

        for el in elementos:
            texto = await el.inner_text()
            info = self._parsear_bloque_pozo(texto)
            datos[info["nombre"]] = info

        return datos

    @staticmethod
    def _to_float(valor: str | None) -> float | None:
        if valor is None:
            return None
        try:
            return float(valor.replace(",", "."))
        except ValueError:
            return None

    def _parsear_bloque_pozo(self, texto: str) -> dict:
        texto = re.sub(r"\s+", " ", texto).strip()

        fin_nombre = re.search(
            r"(Nodo:|Caudal:|Altura Agua:|Vol total:|Ult\.\s*dato hace:|Ver aplicación)",
            texto,
            re.IGNORECASE,
        )
        if fin_nombre:
            nombre = texto[:fin_nombre.start()].strip()
        else:
            nombre = texto.strip()

        nombre = re.sub(r"^Pozo:\s*", "", nombre, flags=re.IGNORECASE)

        nodo_match = re.search(r"Nodo:\s*(\d+)", texto, re.IGNORECASE)
        caudal_match = re.search(r"Caudal:\s*([-0-9.,]+)\s*L/s", texto, re.IGNORECASE)
        altura_match = re.search(r"Altura Agua:\s*([-0-9.,]+)\s*m", texto, re.IGNORECASE)
        vol_match = re.search(r"Vol total:\s*([-0-9.,]+)\s*m3", texto, re.IGNORECASE)
        ult_dato_min = extraer_ult_dato_min(texto)

        return {
            "nombre": nombre,
            "nodo": nodo_match.group(1) if nodo_match else None,
            "caudal": self._to_float(caudal_match.group(1)) if caudal_match else 0.0,
            "altura_agua": self._to_float(altura_match.group(1)) if altura_match else None,
            "vol_total": self._to_float(vol_match.group(1)) if vol_match else None,
            "ult_dato_min": ult_dato_min,
            "texto_crudo": texto,
        }

    async def enviar(self, app: Application, mensaje: str):
        for chat in CHATS:
            try:
                await app.bot.send_message(
                    chat_id=chat,
                    text=mensaje,
                    parse_mode="HTML",
                )
            except Exception as e:
                print("Error Telegram:", e)

    async def procesar_alertas(self, app: Application, info: dict, anterior):
        nombre = info["nombre"]
        caudal = info.get("caudal", 0.0)
        estado, emoji = estado_caudal(caudal)
        ahora_ts = ahora_dt()
        nodo = info.get("nodo") or "-"
        altura_agua = info.get("altura_agua")
        vol_total = info.get("vol_total")
        ult_dato_min = info.get("ult_dato_min")
        altura_txt = f"{altura_agua} m" if altura_agua is not None else "-"
        vol_txt = f"{vol_total} m3" if vol_total is not None else "-"
        ult_dato_txt = f"{ult_dato_min} min" if ult_dato_min is not None else "-"

        if estado in ["DETENIDO", "CRÍTICO"]:
            ultima_alerta = self.alertas.get(nombre)

            if not ultima_alerta or (ahora_ts - ultima_alerta).total_seconds() >= 120:
                mensaje = (
                    f"<b>{emoji} {estado}</b>\n\n"
                    f"Pozo: <b>{nombre}</b>\n"
                    f"Nodo: <b>{nodo}</b>\n"
                    f"Caudal: <b>{caudal} L/s</b>\n"
                    f"Altura agua: <b>{altura_txt}</b>\n"
                    f"Vol total: <b>{vol_txt}</b>\n"
                    f"Último dato hace: <b>{ult_dato_txt}</b>\n"
                    f"Fecha: {ahora()}"
                )
                await self.enviar(app, mensaje)
                self.alertas[nombre] = ahora_ts

        elif anterior is not None:
            estado_ant, _ = estado_caudal(anterior)
            if estado != estado_ant:
                mensaje = (
                    f"<b>{emoji} Cambio de estado</b>\n\n"
                    f"Pozo: <b>{nombre}</b>\n"
                    f"Nodo: <b>{nodo}</b>\n"
                    f"Nuevo estado: <b>{estado}</b>\n"
                    f"Caudal: <b>{caudal} L/s</b>\n"
                    f"Altura agua: <b>{altura_txt}</b>\n"
                    f"Vol total: <b>{vol_txt}</b>\n"
                    f"Último dato hace: <b>{ult_dato_txt}</b>\n"
                    f"Fecha: {ahora()}"
                )
                await self.enviar(app, mensaje)

    async def procesar_alerta_telemetria(self, app: Application, info: dict):
        """
        Alerta cuando el último dato recibido supera 15 minutos.
        Envía recuperación cuando vuelve a <= 15 min.
        """
        nombre = info["nombre"]
        ult_dato_min = info.get("ult_dato_min")
        if ult_dato_min is None:
            return

        ahora_ts = ahora_dt()
        caida = ult_dato_min > 15
        estado_anterior = self.telemetria_estado.get(nombre, False)

        if caida:
            ultima_alerta = self.alertas_telemetria.get(nombre)

            if (not estado_anterior) or (
                not ultima_alerta
                or (ahora_ts - ultima_alerta).total_seconds() >= self.intervalo_alerta_telemetria
            ):
                altura_agua = info.get("altura_agua")
                vol_total = info.get("vol_total")
                mensaje = (
                    f"<b>📡 Falla de telemetría</b>\n\n"
                    f"Pozo: <b>{nombre}</b>\n"
                    f"Nodo: <b>{info.get('nodo') or '-'}</b>\n"
                    f"Último dato hace: <b>{ult_dato_min} min</b>\n"
                    f"Caudal: <b>{info.get('caudal', 0.0)} L/s</b>\n"
                    f"Altura agua: <b>{altura_agua if altura_agua is not None else '-'} m</b>\n"
                    f"Vol total: <b>{vol_total if vol_total is not None else '-'} m3</b>\n"
                    f"Estado: Sin actualización reciente\n"
                    f"Fecha: {ahora()}"
                )
                await self.enviar(app, mensaje)
                self.alertas_telemetria[nombre] = ahora_ts

            self.telemetria_estado[nombre] = True

        else:
            if estado_anterior:
                mensaje = (
                    f"<b>✅ Telemetría recuperada</b>\n\n"
                    f"Pozo: <b>{nombre}</b>\n"
                    f"Nodo: <b>{info.get('nodo') or '-'}</b>\n"
                    f"Último dato hace: <b>{ult_dato_min} min</b>\n"
                    f"Fecha: {ahora()}"
                )
                await self.enviar(app, mensaje)

            self.telemetria_estado[nombre] = False

    async def reporte_horario(self, app: Application):
        ahora_actual = ahora_dt()

        if self.ultimo_reporte and (ahora_actual - self.ultimo_reporte).total_seconds() < REPORTE_INTERVAL:
            return

        if not self.ultimos:
            return

        pozos_ordenados = sorted(self.ultimos.items(), key=lambda x: x[1])

        detalle = "\n\n".join(
            self._formatear_resumen_pozo(self.detalles.get(nombre, {}), caudal)
            for nombre, caudal in pozos_ordenados
        )

        mensaje = (
            f"<b>📡 Estado operativo de pozos Buitrón (Copayapu)</b>\n"
            f"<i>Resumen automático</i>\n\n"
            f"<b>Estado por pozo</b>\n\n"
            f"{detalle}\n\n"
            f"🕒 <b>Hora de monitoreo:</b> {ahora()}"
        )

        await self.enviar(app, mensaje)
        self.ultimo_reporte = ahora_actual

    def _formatear_resumen_pozo(self, info: dict, caudal: float) -> str:
        nombre = info.get("nombre") or "Sin nombre"
        nodo = info.get("nodo") or "-"
        altura_agua = info.get("altura_agua")
        vol_total = info.get("vol_total")
        ult_dato_min = info.get("ult_dato_min")
        estado, emoji = estado_caudal(caudal)

        return (
            f"{emoji} <b>{nombre}</b>\n"
            f"Nodo: {nodo}\n"
            f"Estado: {estado}\n"
            f"Caudal: <b>{caudal:.1f} L/s</b>\n"
            f"Altura agua: {altura_agua if altura_agua is not None else '-'} m\n"
            f"Vol total: {vol_total if vol_total is not None else '-'} m3\n"
            f"Último dato hace: {ult_dato_min if ult_dato_min is not None else '-'} min"
        )

    async def loop(self, app: Application):
        while True:
            try:
                if self.inicio_browser and (ahora_dt() - self.inicio_browser).total_seconds() > RESTART_BROWSER_INTERVAL:
                    print("Reinicio preventivo programado")
                    await asyncio.sleep(2)
                    await self.iniciar()
                    await asyncio.sleep(CHECK_INTERVAL)
                    continue

                datos = await self.obtener_datos()
                self.fallos_consecutivos = 0

                for nombre, info in datos.items():
                    caudal = info.get("caudal", 0.0)

                    anterior = self.ultimos.get(nombre)
                    self.ultimos[nombre] = caudal
                    self.detalles[nombre] = info

                    await self.procesar_alertas(app, info, anterior)
                    await self.procesar_alerta_telemetria(app, info)

                await self.reporte_horario(app)

            except Exception as e:
                print("Error en scraping:", e)
                self.fallos_consecutivos += 1

                if self.fallos_consecutivos >= self.max_fallos:
                    print("Reiniciando navegador por fallos consecutivos...")
                    await asyncio.sleep(10)
                    try:
                        await self.iniciar()
                    except Exception as reinicio_error:
                        print("Error reiniciando navegador:", reinicio_error)
                    self.fallos_consecutivos = 0
                else:
                    await asyncio.sleep(30)

            await asyncio.sleep(CHECK_INTERVAL)

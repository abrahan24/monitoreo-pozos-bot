import re
import json
import logging
import requests
from pathlib import Path
from datetime import date, timedelta
from bs4 import BeautifulSoup

from config import AGROCLIMA_URL
from core.utils import extraer_numeros_eto

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
ARCHIVO_HISTORIAL = BASE_DIR / "eto_historial.json"
ARCHIVO_CIERRE_MENSUAL = BASE_DIR / "eto_cierre_mensual.json"


def cargar_json(path: Path) -> dict:
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def guardar_json(path: Path, data: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def cargar_historial() -> dict:
    return cargar_json(ARCHIVO_HISTORIAL)


def guardar_historial(historial: dict) -> None:
    guardar_json(ARCHIVO_HISTORIAL, historial)


def cargar_cierre_mensual() -> dict:
    return cargar_json(ARCHIVO_CIERRE_MENSUAL)


def guardar_cierre_mensual(data: dict) -> None:
    guardar_json(ARCHIVO_CIERRE_MENSUAL, data)


def limpiar_historial(historial: dict, max_dias: int = 60) -> dict:
    hoy = date.today()
    fecha_minima = hoy - timedelta(days=max_dias)

    historial_filtrado = {}
    for fecha_str, valor in historial.items():
        try:
            fecha_obj = date.fromisoformat(fecha_str)
            if fecha_obj >= fecha_minima:
                historial_filtrado[fecha_str] = valor
        except ValueError:
            pass

    return historial_filtrado


def guardar_valores_en_historial(valores: list[float]) -> None:
    if not valores:
        return

    historial = cargar_historial()
    hoy = date.today()

    # Se asume que vienen del más antiguo al más reciente
    for i, valor in enumerate(valores):
        fecha = hoy - timedelta(days=(len(valores) - 1 - i))
        historial[str(fecha)] = valor

    historial = limpiar_historial(historial, max_dias=60)
    guardar_historial(historial)


def obtener_ultimos_etos_historial(dias: int = 7) -> list[float]:
    historial = cargar_historial()

    if not historial:
        return []

    registros = []
    for fecha_str, valor in historial.items():
        try:
            fecha_obj = date.fromisoformat(fecha_str)
            registros.append((fecha_obj, valor))
        except ValueError:
            continue

    registros.sort(key=lambda x: x[0])
    return [valor for _, valor in registros[-dias:]]


def es_ultimo_dia_del_mes(fecha: date | None = None) -> bool:
    if fecha is None:
        fecha = date.today()
    return (fecha + timedelta(days=1)).month != fecha.month


def ya_se_guardo_cierre_de_este_mes() -> bool:
    cierre = cargar_cierre_mensual()
    mes_guardado = cierre.get("mes_respaldo")
    mes_actual = date.today().strftime("%Y-%m")
    return mes_guardado == mes_actual


def guardar_respaldo_fin_de_mes(etos: list[float]) -> dict:
    ultimos_7 = etos[-7:]

    respaldo = {
        "mes_respaldo": date.today().strftime("%Y-%m"),
        "fecha_guardado": str(date.today()),
        "etos": ultimos_7,
    }

    guardar_cierre_mensual(respaldo)
    logger.info("Cierre mensual guardado: %s", ultimos_7)
    return respaldo


def obtener_etos_para_calculo(dias: int = 7) -> list[float]:
    historial = obtener_ultimos_etos_historial(dias)

    if len(historial) >= dias:
        return historial[-dias:]

    cierre = cargar_cierre_mensual()
    etos_cierre = cierre.get("etos", [])

    combinados = etos_cierre + historial
    return combinados[-dias:]


def obtener_eto_agroclima() -> list[float]:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(AGROCLIMA_URL, headers=headers, timeout=30)
        r.raise_for_status()
    except requests.RequestException as e:
        historial = obtener_etos_para_calculo(7)
        if historial:
            logger.warning("Fallo Agroclima; usando historial local: %s", historial)
            return historial
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

            if valores:
                guardar_valores_en_historial(valores)
                historial = obtener_etos_para_calculo(7)
                if historial:
                    logger.info("ETo obtenida desde patrón. Últimos valores: %s", historial)
                    return historial

    tablas = soup.find_all("table")
    for tabla in tablas:
        txt = tabla.get_text(" ", strip=True)
        if "Evapotranspir" in txt or "mm/día" in txt or "mm/dia" in txt:
            nums = extraer_numeros_eto(txt)
            nums = [v for v in nums if 0 <= v <= 15]

            if nums:
                guardar_valores_en_historial(nums)
                historial = obtener_etos_para_calculo(7)
                if historial:
                    logger.info("ETo obtenida desde tabla. Últimos valores: %s", historial)
                    return historial

    historial = obtener_etos_para_calculo(7)
    if historial:
        logger.warning("No se pudo extraer desde HTML; usando historial local: %s", historial)
        return historial

    raise ValueError("No se pudo extraer la ETo desde Agroclima.")


def ejecutar_cierre_con_mensaje() -> str | None:
    """
    Ejecuta el cierre mensual SOLO si:
    - hoy es el último día del mes
    - aún no se guardó el cierre de este mes

    Devuelve:
    - str con mensaje si se guardó o hubo error
    - None si no corresponde ejecutar
    """
    if not es_ultimo_dia_del_mes():
        return None

    if ya_se_guardo_cierre_de_este_mes():
        return None

    try:
        etos = obtener_eto_agroclima()

        if not etos:
            mensaje = "⚠️ No se pudo obtener ETo para el cierre mensual."
            logger.warning(mensaje)
            return mensaje

        respaldo = guardar_respaldo_fin_de_mes(etos)

        mensaje = (
            "✅ <b>Cierre mensual de ETo guardado</b>\n\n"
            f"• Fecha: {respaldo['fecha_guardado']}\n"
            f"• Últimos ETo guardados: {respaldo['etos']}"
        )

        logger.info("Mensaje de cierre mensual listo para enviar.")
        return mensaje

    except Exception as e:
        logger.exception("Error al ejecutar el cierre mensual")
        return f"❌ Error al guardar cierre mensual: {e}"

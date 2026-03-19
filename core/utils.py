import re
from datetime import datetime
from config import CHILE_TZ

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
    match = re.search(r"Ult\.\s*dato\s*hace:\s*(\d+)\s*min", texto, re.IGNORECASE)
    if match:
        return int(match.group(1))
    return None

def extraer_numeros_eto(texto: str) -> list[float]:
    encontrados = re.findall(r"\d+[.,]\d+", texto)
    return [float(x.replace(",", ".")) for x in encontrados]

def horas_a_hm(horas_float: float):
    horas = int(horas_float)
    minutos = int(round((horas_float - horas) * 60))
    if minutos == 60:
        horas += 1
        minutos = 0
    return horas, minutos
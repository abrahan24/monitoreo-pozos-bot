import re
import requests
from bs4 import BeautifulSoup
from config import AGROCLIMA_URL
from core.utils import extraer_numeros_eto

def obtener_eto_agroclima() -> list[float]:
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

            if valores:
                if len(valores) < 7:
                    print(f"Advertencia: solo se encontraron {len(valores)} valores de ETo.")
                return valores[-7:]

    tablas = soup.find_all("table")
    for tabla in tablas:
        txt = tabla.get_text(" ", strip=True)
        if "Evapotranspir" in txt or "mm/día" in txt or "mm/dia" in txt:
            nums = extraer_numeros_eto(txt)
            nums = [v for v in nums if 0 <= v <= 15]

            if nums:
                if len(nums) < 7:
                    print(f"Advertencia: solo se encontraron {len(nums)} valores de ETo.")
                return nums[-7:]

    raise ValueError("No se pudo extraer la ETo desde Agroclima.")
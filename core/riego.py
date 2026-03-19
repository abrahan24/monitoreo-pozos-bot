from data.sectores import SECTORES_RIEGO, ETAPAS_UVA_KC
from core.utils import horas_a_hm, ahora

def calcular_riego_sector(etos, sector, kc, lluvia_efectiva_mm=0.0, factor_lavado=0.0):
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

def formatear_resultado_riego(data):
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

def formatear_resultado_riego_general(resultados):
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

def formatear_lista_kc_uva():
    lineas = ["<b>🍇 Kc referencial para uva</b>", ""]
    for etapa, kc in ETAPAS_UVA_KC:
        lineas.append(f"• {etapa}: <b>{kc:.2f}</b>")
    return "\n".join(lineas)
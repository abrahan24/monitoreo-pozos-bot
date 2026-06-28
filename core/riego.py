from data.sectores import SECTORES_RIEGO, ETAPAS_UVA_KC
from core.utils import horas_a_hm, ahora


def calcular_riego_sector(etos, sector, kc, lluvia_efectiva_mm=0.0, factor_lavado=0.0):
    if not etos:
        raise ValueError("No se recibieron valores de ETo.")
    if sector not in SECTORES_RIEGO:
        raise ValueError("Sector no válido.")

    # Tomar hasta los últimos 7 valores disponibles
    etos_usados = etos[-7:]
    dias_calculados = len(etos_usados)

    eto_periodo = sum(etos_usados)
    mm_h = SECTORES_RIEGO[sector]["mm_h"]

    etc_periodo = eto_periodo * kc
    etc_ajustada = max(0.0, etc_periodo - lluvia_efectiva_mm)
    etc_ajustada *= (1 + factor_lavado)

    horas_periodo = etc_ajustada / mm_h if mm_h > 0 else 0.0
    horas_dia = horas_periodo / dias_calculados if dias_calculados > 0 else 0.0

    return {
        "sector": sector,
        "kc": round(kc, 2),
        "dias_calculados": dias_calculados,
        "eto_periodo": round(eto_periodo, 2),
        "etc_periodo": round(etc_periodo, 2),
        "etc_ajustada": round(etc_ajustada, 2),
        "mm_h": mm_h,
        "horas_periodo": round(horas_periodo, 2),
        "horas_dia": round(horas_dia, 2),
    }


def formatear_resultado_riego(data):
    hp, mp = horas_a_hm(data["horas_periodo"])
    hd, md = horas_a_hm(data["horas_dia"])

    lineas = [
        "<b>💧 Riego del período</b>",
        "",
        f"• <b>Sector:</b> {data['sector']}",
        f"• <b>Kc:</b> {data['kc']}",
        f"• <b>Días calculados:</b> {data['dias_calculados']}",
        f"• <b>ETo acumulada:</b> {data['eto_periodo']} mm",
        f"• <b>ETc del período:</b> {data['etc_periodo']} mm",
        f"• <b>Precipitación del sector:</b> {data['mm_h']} mm/h",
        f"• <b>Riego del período:</b> {hp} h {mp:02d} min",
        f"• <b>Promedio diario:</b> {hd} h {md:02d} min",
        "",
        f"🕐 {ahora()}",
    ]

    return "\n".join(lineas)


def formatear_resultado_riego_general(resultados):
    lineas = ["<b>💧 Riego general</b>", ""]

    if resultados:
        lineas.append(f"• <b>Días calculados:</b> {resultados[0]['dias_calculados']}")
        lineas.append(f"• <b>ETo acumulada:</b> {resultados[0]['eto_periodo']} mm")
        lineas.append(f"• <b>Kc aplicado:</b> {resultados[0]['kc']}")
        lineas.append("")

    for data in resultados:
        hp, mp = horas_a_hm(data["horas_periodo"])
        hd, md = horas_a_hm(data["horas_dia"])

        lineas.append(
            f"<b>Sector {data['sector']}</b>\n"
            f"• Precipitación: {data['mm_h']} mm/h\n"
            f"• ETc del período: {data['etc_periodo']} mm\n"
            f"• Riego del período: <b>{hp} h {mp:02d} min</b>\n"
            f"• Promedio diario: {hd} h {md:02d} min\n"
        )

    lineas.append(f"🕐 {ahora()}")
    return "\n".join(lineas)


def formatear_lista_kc_uva():
    lineas = ["<b>🍇 Kc referencial para uva</b>", ""]
    for etapa, kc in ETAPAS_UVA_KC:
        lineas.append(f"• {etapa}: <b>{kc:.2f}</b>")
    return "\n".join(lineas)

"""Qualificação: pontuação de aderência ao ICP, explicável e auditável.

Regras:
- Cada critério tem peso (segmento 3,0 · localização 2,0 · palavra-chave 1,5 · porte 1,0).
- Status por critério:
    · `atendido`  — há evidência coletada de fonte real (FATO);
    · `inferido`  — sinal indireto sem evidência direta (ex.: domínio .br ⇒ Brasil) (INFERÊNCIA);
    · `ausente`   — nada encontrado (INFORMAÇÃO AUSENTE — nunca tratada como negativa).
- Pontuação = Σ(peso atendido + 0,5·peso inferido) / Σpesos × 100.
- A explicação lista, para cada critério, as evidências usadas (trecho, URL, data).
"""
from __future__ import annotations


def pontuar(
    criterios: list[dict],
    evidencias: list[dict],
    sinais_inferencia: list[dict] | None = None,
    url_fonte_inicial: str | None = None,
) -> dict:
    """Calcula a pontuação de aderência e a explicação completa.

    `sinais_inferencia`: [{criterio_id, detalhe, fonte}] — inferências etiquetadas.
    `url_fonte_inicial`: URL do registro descoberto (ex.: OSM). Critério comprovado
    APENAS por essa fonte única fica com status `parcial` (0,7 × peso), pois falta
    corroboração independente; com qualquer evidência adicional vira `atendido`.
    """
    sinais_inferencia = sinais_inferencia or []
    por_criterio: list[dict] = []
    soma_pesos = 0.0
    soma_ganha = 0.0

    for criterio in criterios:
        cid = criterio["id"]
        peso = float(criterio.get("peso", 1.0))
        soma_pesos += peso
        minhas = [e for e in evidencias if e.get("criterio_id") == cid]
        inferencias = [s for s in sinais_inferencia if s.get("criterio_id") == cid]

        corroboração = [
            e for e in minhas
            if url_fonte_inicial is None or e.get("url") != url_fonte_inicial
        ]
        if corroboração:
            status = "atendido"
            ganho = peso
        elif minhas:  # só a fonte inicial (descoberta) — parcial
            status = "parcial"
            ganho = 0.7 * peso
        elif inferencias:
            status = "inferido"
            ganho = 0.5 * peso
        else:
            status = "ausente"
            ganho = 0.0
        soma_ganha += ganho

        por_criterio.append(
            {
                "criterio_id": cid,
                "tipo": criterio["tipo"],
                "termo": criterio["termo"],
                "peso": peso,
                "status": status,
                "evidencias": [
                    {
                        "trecho": e["trecho"],
                        "url": e["url"],
                        "data_coleta": e["data_coleta"],
                        "campo": e.get("campo", "texto"),
                        "natureza": "fato",
                    }
                    for e in minhas
                ],
                "inferencias": [
                    {"detalhe": s["detalhe"], "fonte": s.get("fonte", ""), "natureza": "inferencia"}
                    for s in inferencias
                ],
            }
        )

    pontuacao = round(100.0 * soma_ganha / soma_pesos, 1) if soma_pesos else 0.0
    return {"pontuacao": pontuacao, "por_criterio": por_criterio, "soma_pesos": soma_pesos}


def rotulo_status(status: str) -> str:
    return {
        "atendido": "Atendido (fato corroborado)",
        "parcial": "Parcial (só fonte de descoberta)",
        "inferido": "Inferido",
        "ausente": "Não encontrado (ausente)",
    }[status]

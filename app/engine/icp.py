"""Interpretação do ICP: texto livre em pt-BR → critérios de pesquisa revisáveis.

Modo determinístico (padrão, sempre disponível):
- dicionário de segmentos de negócio com termos de busca equivalentes;
- lista de estados e grandes cidades do Brasil;
- padrões linguísticos ("empresas de X", "que tenham/usem Y", "mais de N funcionários").

Cliente LLM opcional: se `LLM_*` estiver configurado, o modelo pode adicionar
palavras-chave extras — mas nunca substitui o critério determinístico nem cria
empresas/evidências (a coleta continua vindo apenas de fontes reais).
"""
from __future__ import annotations

import re
import unicodedata

# --- dicionário de segmentos: termos do usuário → termo de busca (OSM/Nominatim) ---
SEGMENTOS: dict[str, str] = {
    "fisioterapia": "fisioterapia",
    "fisioterapeuta": "fisioterapia",
    "odontologia": "dentista",
    "dentista": "dentista",
    "odontologico": "dentista",
    "clínica odontológica": "dentista",
    "advocacia": "advogado",
    "advogado": "advogado",
    "jurídico": "advogado",
    "academia": "academia",
    "fitness": "academia",
    "pilates": "pilates",
    "escola": "escola",
    "ensino": "escola",
    "curso": "escola",
    "restaurante": "restaurante",
    "bar": "bar",
    "café": "café",
    "clinica": "clínica",
    "clínica": "clínica",
    "médico": "clínica",
    "saúde": "clínica",
    "veterinario": "veterinário",
    "veterinário": "veterinário",
    "pet": "veterinário",
    "contabilidade": "contador",
    "contador": "contador",
    "contábil": "contador",
    "marketing": "agência",
    "agência": "agência",
    "publicidade": "agência",
    "software": "tecnologia",
    "tecnologia": "tecnologia",
    "ti": "tecnologia",
    "startup": "tecnologia",
    "desenvolvimento de sistemas": "tecnologia",
    "varejo": "loja",
    "loja": "loja",
    "comércio": "loja",
    "indústria": "indústria",
    "fábrica": "indústria",
    "construção": "construtora",
    "imobiliária": "imobiliária",
    "imóveis": "imobiliária",
    "engenharia": "engenharia",
    "arquitetura": "arquiteto",
    "nutrição": "nutricionista",
    "psicologia": "psicólogo",
    "psicólogo": "psicólogo",
    "farmácia": "farmácia",
    "ótica": "ótica",
    "transportadora": "transporte",
    "logística": "transporte",
    "eventos": "evento",
}

# --- localidades: cidades (normalizadas sem acento) → exibição + UF ---
_CIDADES = {
    "sao paulo": ("São Paulo", "SP"), "campinas": ("Campinas", "SP"), "guarulhos": ("Guarulhos", "SP"),
    "santos": ("Santos", "SP"), "sorocaba": ("Sorocaba", "SP"), "ribeirao preto": ("Ribeirão Preto", "SP"),
    "sao jose dos campos": ("São José dos Campos", "SP"), "sao bernardo do campo": ("São Bernardo do Campo", "SP"),
    "osasco": ("Osasco", "SP"), "santo andre": ("Santo André", "SP"), "jundiai": ("Jundiaí", "SP"),
    "rio de janeiro": ("Rio de Janeiro", "RJ"), "niteroi": ("Niterói", "RJ"), "petropolis": ("Petrópolis", "RJ"),
    "belo horizonte": ("Belo Horizonte", "MG"), "uberlandia": ("Uberlândia", "MG"), "contagem": ("Contagem", "MG"),
    "vitoria": ("Vitória", "ES"), "curitiba": ("Curitiba", "PR"), "londrina": ("Londrina", "PR"),
    "maringa": ("Maringá", "PR"), "porto alegre": ("Porto Alegre", "RS"), "caxias do sul": ("Caxias do Sul", "RS"),
    "florianopolis": ("Florianópolis", "SC"), "joinville": ("Joinville", "SC"), "blumenau": ("Blumenau", "SC"),
    "salvador": ("Salvador", "BA"), "recife": ("Recife", "PE"), "olinda": ("Olinda", "PE"),
    "fortaleza": ("Fortaleza", "CE"), "natal": ("Natal", "RN"), "joao pessoa": ("João Pessoa", "PB"),
    "maceio": ("Maceió", "AL"), "aracaju": ("Aracaju", "SE"), "teresina": ("Teresina", "PI"),
    "manaus": ("Manaus", "AM"), "belem": ("Belém", "PA"), "goiania": ("Goiânia", "GO"),
    "brasilia": ("Brasília", "DF"), "cuiaba": ("Cuiabá", "MT"), "campo grande": ("Campo Grande", "MS"),
    "brasília": ("Brasília", "DF"),
}

_ESTADOS = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM", "bahia": "BA", "ceara": "CE",
    "distrito federal": "DF", "espirito santo": "ES", "goias": "GO", "maranhao": "MA", "mato grosso": "MT",
    "mato grosso do sul": "MS", "minas gerais": "MG", "para": "PA", "paraiba": "PB", "parana": "PR",
    "pernambuco": "PE", "piaui": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN",
    "rio grande do sul": "RS", "rondonia": "RO", "roraima": "RR", "santa catarina": "SC", "sao paulo": "SP",
    "sergipe": "SE", "tocantins": "TO",
}

_SIGLAS = {"SP", "RJ", "MG", "ES", "PR", "RS", "SC", "BA", "PE", "CE", "RN", "PB", "AL", "SE", "PI",
           "AM", "PA", "GO", "DF", "MT", "MS", "AC", "AP", "RO", "RR", "MA", "TO"}


def _sem_acento(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")


def _normalizar(texto: str) -> str:
    return _sem_acento(texto.lower())


def extrair_criterios(texto_icp: str) -> dict:
    """Extrai critérios do texto do ICP. Retorna dict com `criterios`, `pais`, `alertas`."""
    criterios: list[dict] = []
    alertas: list[str] = []
    contador = 0

    def adicionar(tipo: str, termo: str, peso: float, consulta: str | None = None) -> None:
        nonlocal contador
        contador += 1
        criterio = {
            "id": f"c{contador}",
            "tipo": tipo,
            "termo": termo.strip(),
            "peso": peso,
        }
        if consulta:
            criterio["consulta"] = consulta
        criterios.append(criterio)

    texto = " ".join(texto_icp.split())
    normalizado = _normalizar(texto)

    # --- segmento(s): palavras do dicionário, com correspondência por palavra/frase (aceita plural) ---
    termos_encontrados: list[str] = []
    for chave in sorted(SEGMENTOS, key=len, reverse=True):
        padrao = r"(?<![a-z0-9])" + re.escape(_normalizar(chave)) + r"s?(?![a-z0-9])"
        if re.search(padrao, normalizado):
            consulta = SEGMENTOS[chave]
            if consulta not in termos_encontrados:
                termos_encontrados.append(consulta)
                adicionar("segmento", chave, 3.0, consulta=consulta)
    if not termos_encontrados:
        # fallback: "empresas de X" / "empresas que atuam em X"
        m = re.search(r"empresas\s+(?:de|do|que\s+atuam\s+em|que\s+fa[çc]am)\s+([\wà-úÀ-Ú\s]+?)(?:,|\.|;|$)", texto, re.IGNORECASE)
        if m:
            termo = m.group(1).strip()
            adicionar("segmento", termo, 3.0, consulta=termo)
        else:
            # fallback 2: substantivo antes de "em" ("floriculturas em Porto Alegre")
            m2 = re.search(r"([\wà-úÀ-Ú]{3,31})\s+em\s+", texto, re.IGNORECASE)
            if m2:
                termo = m2.group(1)
                consulta = termo[:-1] if termo.endswith("s") and len(termo) > 4 else termo
                adicionar("segmento", termo, 3.0, consulta=consulta)
            else:
                alertas.append(
                    "Não identifiquei um segmento de negócio no texto. "
                    "Revise os critérios abaixo — sem um segmento, a busca fica muito ampla."
                )

    # --- segmento genérico redundante: "clínicas de fisioterapia" não são dois segmentos ---
    segmentos_atual = [c for c in criterios if c["tipo"] == "segmento"]
    if len(segmentos_atual) > 1 and any(c["consulta"] == "clínica" for c in segmentos_atual) \
            and any(c["consulta"] != "clínica" for c in segmentos_atual):
        criterios = [c for c in criterios
                     if not (c["tipo"] == "segmento" and c["consulta"] == "clínica")]
        for i, c in enumerate(criterios, 1):
            c["id"] = f"c{i}"

    # --- localização ---
    localizacoes: list[tuple[str, str]] = []  # (exibição, uf)
    for chave, (nome, uf) in _CIDADES.items():
        padrao = r"(?<![a-z0-9])" + re.escape(chave) + r"(?![a-z0-9])"
        if re.search(padrao, normalizado) and (nome, uf) not in localizacoes:
            localizacoes.append((nome, uf))
    if not localizacoes:
        for nome_estado, sigla in _ESTADOS.items():
            padrao = r"(?<![a-z0-9])" + re.escape(nome_estado) + r"(?![a-z0-9])"
            if re.search(padrao, normalizado):
                localizacoes.append((nome_estado.title(), sigla))
                break
    if not localizacoes:
        for sigla in _SIGLAS:
            padrao = r"\(\s*" + sigla + r"\s*\)"
            if re.search(padrao, texto, re.IGNORECASE):
                localizacoes.append((sigla, sigla))
                break
    for nome, uf in localizacoes:
        adicionar("localizacao", f"{nome} ({uf})", 2.0, consulta=nome)

    # --- porte ---
    m = re.search(r"(?:mais de|acima de|no m[íi]nimo)\s+(\d+)\s*(?:funcion[áa]rios|colaboradores|empregados)", texto, re.IGNORECASE)
    if m:
        adicionar("porte", f"mais de {m.group(1)} funcionários", 1.0)
    else:
        m = re.search(r"\b(pequenas|m[ée]dias|grandes)\s+empresas\b", texto, re.IGNORECASE)
        if m:
            adicionar("porte", f"{m.group(1).lower()} empresas", 1.0)

    # --- palavras-chave: "que tenham/usem/ofereçam X", "precisem de X" ---
    for m in re.finditer(
        r"que\s+(?:tenham?|tenha|usem?|utilize[ms]?|ofere[çc]am?|precise[ms]?|queiram?|busquem?|"
        r"atendam?|trabalhem?|aceitem?|possuam?|disponham?|queira)\s+"
        r"([\wà-úÀ-Ú][\wà-úÀ-Ú\s,/-]{2,80}?)(?=[\.;!?]|$|\be\b\s+que)",
        texto,
        re.IGNORECASE,
    ):
        bruto = m.group(1)
        for termo in re.split(r"\s*(?:,|e|/|-)\s*", bruto):
            termo = termo.strip(" .")
            if len(termo) >= 3 and _normalizar(termo) not in _normalizar(" ".join(c["termo"] for c in criterios)):
                adicionar("palavra_chave", termo, 1.5)

    # --- termos entre aspas → palavras-chave explícitas ---
    for termo in re.findall(r"\"([^\"]{3,40})\"", texto):
        if not any(termo.lower() == c["termo"].lower() for c in criterios):
            adicionar("palavra_chave", termo, 1.5)

    pais = "BR" if localizacoes or "brasil" in normalizado else None
    if pais is None:
        alertas.append("Nenhuma localização identificada — a busca não será restrita geograficamente.")

    return {"criterios": criterios, "pais": pais, "alertas": alertas}


def enriquecer_criterios(criterios: list[dict]) -> list[dict]:
    """Garante a chave `consulta` (termo de busca) em critérios de segmento/localização.

    Critérios vindos da revisão pela interface chegam só com {id, tipo, termo, peso};
    esta função recalcula o termo de busca a partir do `termo` — mapeando pelo
    dicionário de segmentos quando possível.
    """
    enriquecidos: list[dict] = []
    for criterio in criterios:
        novo = dict(criterio)
        tipo = novo.get("tipo")
        termo = (novo.get("termo") or "").strip()
        if tipo == "segmento":
            chave = _normalizar(termo)
            consulta = SEGMENTOS.get(termo) or SEGMENTOS.get(chave)
            if not consulta:
                for chave_dic, busca in sorted(SEGMENTOS.items(), key=lambda par: len(par[0]), reverse=True):
                    if chave_dic in chave or _normalizar(chave_dic) in chave:
                        consulta = busca
                        break
            novo["consulta"] = consulta or termo or ""
        elif tipo == "localizacao":
            # "Campinas (SP)" → consulta "Campinas"; a sigla polui a busca geográfica
            novo["consulta"] = re.sub(r"\s*\([A-Z]{2}\)\s*$", "", termo).strip() or termo
        enriquecidos.append(novo)
    return enriquecidos


def consultas_pesquisa(criterios: list[dict]) -> list[str]:
    """Gera consultas de descoberta a partir dos critérios (segmento × localização)."""
    segmentos = [c for c in criterios if c["tipo"] == "segmento"]
    locais = [c for c in criterios if c["tipo"] == "localizacao"]

    def termo_busca(c: dict) -> str:
        # critérios revisados na interface podem não ter `consulta` — usa o termo
        return (c.get("consulta") or c["termo"]).strip()

    consultas: list[str] = []
    if segmentos and locais:
        for s in segmentos:
            for l in locais:
                consultas.append(f"{termo_busca(s)} {termo_busca(l)}")
    elif segmentos:
        consultas.extend(termo_busca(s) for s in segmentos)
    elif locais:
        consultas.extend(termo_busca(l) for l in locais)
    return [c for c in consultas if c]

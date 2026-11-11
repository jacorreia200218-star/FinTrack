"""Categorização automática de movimentos por palavras-chave.

As regras por omissão estão abaixo; as regras que crias na app (quando mudas a categoria
de um movimento) ficam guardadas na base de dados e têm prioridade.
"""

from __future__ import annotations

import re

from .importers.common import normalize

# Categorias que não contam como "gasto" (dinheiro que só muda de sítio)
NON_SPENDING = {"Transferências internas", "Investimento", "Rendimento", "Câmbio"}

CATEGORIES = [
    "Supermercado",
    "Restauração",
    "Takeaway",
    "Transportes",
    "Combustível",
    "Subscrições",
    "Telecomunicações",
    "Casa e Utilidades",
    "Saúde",
    "Ginásio e Desporto",
    "Compras",
    "Lazer",
    "Viagens",
    "Educação",
    "Seguros",
    "Impostos e Taxas",
    "Levantamentos",
    "Transferências",
    "Transferências internas",
    "Investimento",
    "Câmbio",
    "Rendimento",
    "Outros",
]

DEFAULT_RULES: dict[str, list[str]] = {
    "Transferências internas": [
        "p/ revolut", "para revolut", "revolut bank", "revolut ltd", "top-up", "topup",
        "trf p/ activobank", "trf de activobank",
    ],
    "Supermercado": [
        "pingo doce", "continente", "modelo continente", "lidl", "aldi", "mercadona",
        "minipreco", "auchan", "intermarche", "el corte ingles super", "spar", "meu super",
        "froiz", "amanhecer", "go natural", "celeiro",
    ],
    "Takeaway": ["uber eats", "glovo", "bolt food", "too good to go", "just eat"],
    "Restauração": [
        "restaurante", "mcdonald", "burger king", "kfc", "telepizza", "pizza hut",
        "domino", "starbucks", "pastelaria", "padaria", "cafe ", "cafetaria", "taberna",
        "tasca", "churrasqueira", "sushi", "h3", "vitaminas", "padaria portuguesa",
        "cervejaria", "snack bar",
    ],
    "Transportes": [
        "uber", "bolt", "cabify", "free now", "comboios de portugal", "cp ", "metro",
        "carris", "navegante", "fertagus", "flixbus", "rede expressos", "via verde",
        "emel", "parque", "parking", "portagem", "brisa", "telpark", "saba",
    ],
    "Subscrições": [
        "netflix", "spotify", "disney", "hbo", "max.com", "prime video", "amazon prime",
        "apple.com", "icloud", "youtube", "google one", "google storage", "chatgpt",
        "openai", "claude.ai", "anthropic", "playstation", "xbox", "nintendo", "steam",
        "patreon", "dazn", "sport tv", "skyshowtime", "audible", "kindle",
    ],
    "Telecomunicações": ["vodafone", "meo ", "meo,", "nos comunicacoes", "digi ", "nowo", "uzo "],
    "Casa e Utilidades": [
        "edp", "endesa", "iberdrola", "goldenergy", "galp energia", "epal", "aguas",
        "smas", "renda", "condominio", "ikea", "leroy merlin", "aki", "bricomarche",
    ],
    "Combustível": ["galp", "repsol", "bp ", "prio", "cepsa", "shell", "moeve", "intermarche gas"],
    "Saúde": [
        "farmacia", "wells", "cuf", "luz saude", "hospital", "clinica", "dentista",
        "medicare", "multicare", "opticas", "multiopticas",
    ],
    "Ginásio e Desporto": [
        "fitness hut", "solinca", "holmes place", "basic-fit", "basic fit", "go fit",
        "ginasio", "decathlon", "padel", "myfit", "vivafit",
    ],
    "Compras": [
        "amazon", "worten", "fnac", "zara", "primark", "h&m", "pull&bear", "bershka",
        "shein", "aliexpress", "temu", "mango", "sport zone", "radio popular", "pcdiga",
        "globaldata", "el corte ingles", "uniqlo", "nike", "adidas",
    ],
    "Lazer": ["cinema", "nos cinemas", "uci", "ticketline", "blueticket", "bilheteira", "museu", "bar "],
    "Viagens": [
        "booking", "airbnb", "ryanair", "easyjet", "tap air", "flytap", "vueling",
        "hotel", "hostel", "expedia", "wizz",
    ],
    "Seguros": ["seguro", "fidelidade", "tranquilidade", "ageas", "allianz", "generali", "zurich", "ok! seguros"],
    "Impostos e Taxas": ["at - ", "autoridade tributaria", "financas", "iuc", "imi", "seg social", "seguranca social", "comissao", "imposto do selo"],
    "Educação": ["universidade", "faculdade", "propina", "udemy", "coursera", "livraria", "bertrand"],
    "Levantamentos": ["levantamento", "lev. ", "atm", "multibanco lev", "cash withdrawal"],
    "Investimento": ["degiro", "flatex", "trading 212", "xtb", "interactive brokers", "ppr", "certificados de aforro"],
    "Rendimento": ["vencimento", "salario", "ordenado", "payroll", "remuneracao", "reembolso irs"],
}

# Tipos da Revolut com categoria óbvia
REVOLUT_KIND_RULES = {
    "TOPUP": "Transferências internas",
    "EXCHANGE": "Câmbio",
    "ATM": "Levantamentos",
    "INTEREST": "Rendimento",
    "CASHBACK": "Rendimento",
}


# Cada palavra-chave tem de começar no início de uma palavra ("aki" não apanha "takis")
_COMPILED_RULES = [
    (category, re.compile("|".join(r"(?<![a-z0-9])" + re.escape(kw) for kw in keywords)))
    for category, keywords in DEFAULT_RULES.items()
]


def merchant_key(description: str) -> str:
    """Normaliza a descrição para agrupar movimentos do mesmo comerciante.

    'COMPRA 1234 PINGO DOCE LISBOA 12/03' e 'PINGO DOCE LISBOA' ficam iguais.
    """
    s = normalize(description)
    s = re.sub(r"\b(compra|pagamento|pag\.?|trf\.?|transf\.?|mb way|mbway|dd|debito direto|cartao|card)\b", " ", s)
    s = re.sub(r"\d+[/\-.]\d+([/\-.]\d+)?", " ", s)  # datas
    s = re.sub(r"\b\d+\b", " ", s)  # números soltos
    s = re.sub(r"[^a-z0-9&.+ ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:60]


def categorize(
    description: str,
    amount: float,
    kind: str = "",
    user_rules: list[tuple[str, str]] | None = None,
) -> str:
    """Devolve a categoria de um movimento.

    user_rules: lista de (padrão, categoria) criadas pelo utilizador — têm prioridade.
    """
    text = " " + normalize(description) + " "
    key = merchant_key(description)

    for pattern, category in user_rules or []:
        p = normalize(pattern)
        if p and (p == key or p in text):
            return category

    if kind in REVOLUT_KIND_RULES:
        return REVOLUT_KIND_RULES[kind]

    for category, pattern in _COMPILED_RULES:
        if pattern.search(text):
            if category == "Rendimento" and amount < 0:
                continue
            return category

    if kind == "TRANSFER" or "transf" in text or "trf" in text or "mb way" in text:
        return "Transferências"
    if amount > 0:
        return "Rendimento" if amount >= 300 else "Outros"
    return "Outros"

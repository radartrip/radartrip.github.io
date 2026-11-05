# Radar Trip - Vigia de promocoes (v3.6 - cacador de resgates em texto + LATAM Pass)
# Novidade v3.6: o leitor de milhas tambem entende promocoes escritas em TEXTO
# ("Uberlandia (UDI) x Brasilia (BSB) - a partir de 6.073 milhas + taxas por trecho",
# "entre Rio (GIG) e Milao (MXP) ... a partir de 63.544 milhas"), e nao so tabelas.
# Resgate da LATAM Pass saindo/chegando em GYN/BSB ganha prioridade maxima. A vitrine sai no
# formato "Oportunidade de resgate" (rota, programa, pontos o trecho + taxas, datas por mes,
# milheiro, link para emitir e aviso de simulacao). Com GECKO_API_KEY no cofre, o resgate
# LATAM com data e sem preco do mesmo dia busca o preco em dinheiro AO VIVO na LATAM
# (GeckoAPI, 5 creditos), com teto mensal de creditos (GECKO_MAX_CREDITOS_MES).
#
# v3.5 - vitrine sem citar o blog: link oficial
# Novidade v3.5: a VITRINE nao cita mais a fonte (blog). O Radar Trip e a fonte: o assinante
# recebe o link de quem vende (companhia, programa de pontos, banco) achado dentro do post,
# limpo de rastreio (utm, afiliado). Sem link oficial, vai o site do programa; sem programa,
# o post sai sem link. O titulo perde marcas de blog ("Alerta PPV"...). Bastidores seguem
# mostrando a fonte e o link do blog (so o dono ve).
# Le os blogs de promocao, filtra o que interessa e avisa no Telegram.
#
# Novidade v2: nos posts de passagens com milhas, abre o post completo pelo canal
# publico do WordPress (wp-json), le a tabela de resgates, separa os trechos que
# saem ou chegam em GYN/BSB, busca o preco em dinheiro do mesmo trecho e calcula
# o milheiro: "vale usar milhas" ou "melhor pagar em dinheiro".
#
# Novidade v3: alertas fortes (milhas GYN/BSB, promoções GYN/BSB, transferência >= 80%,
# relâmpago) vão para a VITRINE (canal do Telegram) com código RT-xxxx; o resto vai
# para os BASTIDORES (chat do dono). O canal é descoberto sozinho quando o robô vira
# administrador dele. Sem canal, a vitrine chega ao dono como "prévia".
#
# Novidade v3.1: entende a cadeia de pontos (banco/cartão -> Livelo/Esfera -> companhia),
# com corte de bônus POR PROGRAMA (CORTE_BONUS), compra de pontos com desconto, clubes
# pelo milheiro, prazo da promoção, antirrepetição de "prorrogou/últimas horas" e o
# COMBO (compra Livelo/Esfera + transferência com bônus = milheiro final).
# Acúmulo ("X pontos por real") fica só nos bastidores. Cada alerta sai etiquetado
# (categoria, programas, trechos) para a futura personalização por assinante.
#
# Novidade v3.3.1: TESTE FORÇADO. Na próxima rodada, manda UMA vez ao canal o alerta de
# demonstração "Praia no fim de semana" (marcado como TESTE, sem gastar código RT-xxxx).
# Depois grava dados/teste_praia.json e nunca mais repete.
#
# Novidade v3.4 (vitrine mais cheia, sem perder a curadoria):
#   - OFERTA DE PASSAGEM vem primeiro: post cujo título traz passagem/voo/trecho com preço
#     ("a partir de R$ 47", "a partir de 3.520 pontos") ou "Alerta PPV" vira passagem, mesmo
#     que o texto cite bônus de transferência. Vai para a vitrine como nacional, internacional
#     ou GYN/BSB. Notícias (reajuste de tabela, dicas, seguro, apps) ficam nos bastidores
#     com a etiqueta "Notícia / outros".
#   - Acúmulo "X pontos por real" não é mais confundido com clube; acúmulo de 10+ pontos por
#     real vai à vitrine (campanha forte), além da regra de 2x o normal.
#   - Reavaliação única: na 1ª rodada da v3.4, as promoções das últimas 24h que ficaram de
#     fora são reavaliadas com as regras novas.

import html
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser

FONTES = {
    "Melhores Destinos": "https://www.melhoresdestinos.com.br/feed",
    "Passageiro de Primeira": "https://passageirodeprimeira.com/feed/",
    "Pontos pra Voar": "https://pontospravoar.com/feed/",
}

PASTA = "dados"
ARQ_VISTOS = os.path.join(PASTA, "vistos.json")
ARQ_ALERTAS = os.path.join(PASTA, "alertas.json")
ARQ_MILHAS = os.path.join(PASTA, "milhas.json")
MAX_VISTOS = 3000
MAX_ALERTAS = 400
MAX_RESGATES = 3000

TERMOS_PROMO = [
    "passage", "milha", "pontos", "bônus", "bonus", "transferên", "transferen",
    "milheiro", "cpm", "smiles", "latam pass", "azul fidelidade", "tudoazul",
    "livelo", "esfera", "tarifa", "voos a partir", "ida e volta",
    "a partir de r$", "por r$", "voos saindo de", "voos de goiânia", "voos de brasília",
]
TERMOS_IGNORAR = [
    "visto", "amenity", "wi-fi", "internet", "como ir", "onde ficar",
    "como funciona", "como acumular", "sorteia", "ceo", "youtube", "vídeo novo",
    "nomad", "sam's", "sam’s", "estapar", "ibest", "hotéis", "hoteis", "ibis",
    "declaração de saúde", "pijama", "bate-papo", "grupo grátis", "alertas grátis",
    "black november dos alertas", "cashback",
]
TERMOS_GYN = ["goiânia", "goiania", "gyn", "goiás", "goias", "brasília", "brasilia", "bsb"]
TERMOS_URGENTE = ["erro de tarifa", "relâmpago", "relampago", "tarifa erro", "bug"]

# ---- Leitor de milhas ----
AEROPORTOS_CASA = {"GYN", "BSB"}
MAX_TRECHOS_POR_POST = 8

# Quanto custa, em média, COMPRAR 1.000 milhas em promoção (R$). Referência para o
# veredito: se o resgate "rende" mais que isso por milheiro, vale usar milhas.
# Ajuste quando o mercado mudar.
CUSTO_MILHEIRO = {
    "Smiles": 17,
    "LATAM Pass": 25,
    "Azul Fidelidade": 16,
}

# Códigos de aeroporto -> código de cidade usado no medidor de preços (precos.json)
CIDADE_DO_AEROPORTO = {"GRU": "SAO", "CGH": "SAO", "VCP": "SAO", "GIG": "RIO", "SDU": "RIO",
                       "AEP": "BUE", "EZE": "BUE"}

MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4, "maio": 5, "junho": 6,
         "julho": 7, "agosto": 8, "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}


def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 RadarTrip/2.0"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read()


def limpar(texto):
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = html.unescape(texto)
    texto = re.sub(r"O post .* apareceu primeiro em .*", "", texto)
    return re.sub(r"\s+", " ", texto).strip()


def ler_feed(nome, url):
    itens = []
    raiz = ET.fromstring(baixar(url))
    for item in raiz.iter("item"):
        titulo = limpar(item.findtext("title"))
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        desc = limpar(item.findtext("description"))[:400]
        cats = [limpar(c.text) for c in item.findall("category")]
        data = item.findtext("pubDate")
        try:
            data_iso = parsedate_to_datetime(data).astimezone(timezone.utc).isoformat()
        except Exception:
            data_iso = datetime.now(timezone.utc).isoformat()
        itens.append({
            "id": guid, "titulo": titulo, "link": link, "fonte": nome,
            "resumo": desc, "categorias": cats, "data": data_iso,
        })
    return itens


def maior_bonus(texto):
    nums = re.findall(r"(\d{2,3})\s*%\s*(?:de\s*)?b[ôo]nus", texto)
    nums += re.findall(r"b[ôo]nus\s*(?:de\s*)?(?:até\s*)?(\d{2,3})\s*%", texto)
    return max([int(n) for n in nums], default=0)


# ================= Analisador de promoções de pontos =================
# Programas das companhias (destino final das milhas)
PROGRAMAS_AEREOS = {
    "Smiles": ["smiles"],
    "LATAM Pass": ["latam pass", "latam"],
    "Azul Fidelidade": ["azul fidelidade", "tudoazul", "azul"],
    "TAP Miles&Go": ["tap miles", "miles&go", "miles & go"],
    "AAdvantage": ["aadvantage"],
}
# Programas de pontos e bancos (origem dos pontos)
PROGRAMAS_PONTOS = {
    "Livelo": ["livelo"],
    "Esfera": ["esfera"],
    "C6 Átomos": ["átomos", "atomos", "c6"],
    "Itaú": ["itaú", "itau", "iupp"],
    "Inter Loop": ["inter loop", "loop"],
    "BTG": ["btg"],
    "XP": ["xp investimentos", "cartão xp", "xp "],
    "Bradesco": ["bradesco"],
    "Santander": ["santander"],
    "Banco do Brasil": ["banco do brasil"],
    "Caixa": ["caixa"],
    "Nubank": ["nubank", "ultravioleta"],
    "Sicoob": ["sicoob"],
    "Sicredi": ["sicredi"],
}
CENTRAIS_DE_PONTOS = {"Livelo", "Esfera"}

# Bônus mínimo de transferência para ir à VITRINE, por programa de DESTINO.
# Base: faixas reais de 2025-2026 (LATAM 20-40%, Smiles 70-100%, Azul até 115%).
CORTE_BONUS = {
    "LATAM Pass": 25,
    "Smiles": 70,
    "Azul Fidelidade": 80,
    "TAP Miles&Go": 50,
    "AAdvantage": 40,
    "Livelo": 20,      # banco/cartão -> Livelo
    "Esfera": 20,      # banco/cartão -> Esfera
}
COMPRA_DESCONTO_MIN = 50     # compra de pontos Livelo/Esfera: desconto mínimo para a vitrine
COMPRA_MILHEIRO_MAX = 32     # ...ou milheiro informado de até R$ 32
CLUBE_TOLERANCIA = 1.20      # clube entra se o milheiro for até 20% acima da referência

# Acúmulo (pontos por real em parceiros): só vai à vitrine quando compensa MUITO
ACUMULO_MULTIPLO = 2.0       # pontuação >= 2x o "normal" já visto daquele parceiro
ACUMULO_MIN_HISTORICO = 2    # observações anteriores necessárias para comparar
TERMOS_ACUMULO_FORTE = [r"em dobro", r"\bdobr", r"\bdupl", r"\btripl", r"\b[23]x\b", r"\b[23] vezes mais"]
PARCEIROS = {
    "Mercado Livre": ["mercado livre", "meli"], "Amazon": ["amazon"], "Magalu": ["magalu", "magazine luiza"],
    "Casas Bahia": ["casas bahia"], "Americanas": ["americanas"], "Shopee": ["shopee"], "AliExpress": ["aliexpress"],
    "Netshoes": ["netshoes"], "Centauro": ["centauro"], "Nike": ["nike"], "Adidas": ["adidas"],
    "Booking": ["booking"], "Hoteis.com": ["hoteis.com", "hotéis.com"], "Decolar": ["decolar"],
    "iFood": ["ifood"], "Uber": ["uber"], "Shell": ["shell"], "Ipiranga": ["ipiranga"], "Petrobras": ["petrobras", "br mania"],
    "Drogasil/Raia": ["drogasil", "droga raia", "raia"], "Natura": ["natura"], "O Boticário": ["boticário", "boticario"],
    "Apple": ["apple"], "Samsung": ["samsung"], "Carrefour": ["carrefour"], "Pão de Açúcar": ["pão de açúcar", "pao de acucar"],
    "Fast Shop": ["fast shop", "fastshop"], "Dell": ["dell"], "Renner": ["renner"], "C&A": ["c&a"], "Riachuelo": ["riachuelo"],
    "Dafiti": ["dafiti"], "Sephora": ["sephora"], "Polishop": ["polishop"], "Leroy Merlin": ["leroy merlin"],
    "Zattini": ["zattini"], "Kabum": ["kabum"], "Ponto": ["pontofrio", "ponto frio"], "Vivara": ["vivara"],
}
TERMOS_NACIONAL = [r"passagens nacionais", r"voos nacionais", r"todo o brasil", r"pelo brasil",
                   r"azul,? gol e latam", r"gol,? azul e latam", r"latam,? gol e azul", r"mais de \d+ mil voos"]
TERMOS_NACIONAL += [r"\bnacionais\b", r"\bnordeste\b", r"destinos? nacion", r"pelo pa[ií]s", r"\bsul do brasil",
                    r"\bpraias? do", r"capitais"]
TERMOS_INTERNACIONAL = [r"internacion", r"exterior", r"europa", r"lisboa", r"portugal", r"orlando", r"miami",
                        r"nova york", r"estados unidos", r"\beua\b", r"buenos aires", r"argentina", r"santiago",
                        r"\bchile\b", r"paris", r"madri", r"roma", r"londres", r"canc[uú]n", r"punta cana",
                        r"montevid", r"lima\b", r"bogot", r"panam", r"caribe", r"jap[aã]o", r"dubai", r"[aá]frica",
                        r"n[óo]rdic", r"copenhag", r"estocolm", r"\boslo\b", r"berlim", r"frankfurt", r"amsterd",
                        r"zurique", r"mil[aã]o", r"istambul", r"t[óo]quio", r"sydney", r"austr[aá]lia", r"m[eé]xico",
                        r"nova zel", r"\b(?:lis|mad|cdg|fco|lhr|jfk|mia|mco|eze|aep|scl|cun)\b"]
# Título que anuncia passagem com preço = oferta de passagem
RE_PALAVRA_PASSAGEM = r"passage|\bvoos?\b|\bvoe\b|\bvoar\b|\btrechos?\b|bilhetes?|\bemiss|\bida e volta"
RE_PRECO_TITULO = (r"(?:a partir de|por apenas|por|desde|de)\s*(?:r\$\s*\d|\d[\d.,]*\s*(?:mil\s*)?"
                   r"(?:pontos|milhas|pts))")
RE_ALERTA_TITULO = r"alerta ppv|alerta de disponibilidade|alerta de passage|muito barato|baixou|imperd[ií]vel"
# Notícias que citam passagem/milhas mas não são oferta
TERMOS_NOTICIA = [r"reajust", r"encarec", r"\baumento", r"\baumenta", r"tabela fixa", r"novas? regras?",
                  r"\bmudan", r"\bmuda\b", r"deixa de", r"\bdicas?\b", r"seguro viagem", r"awardwallet",
                  r"revolut", r"entrevista", r"balan[cç]o", r"lucro", r"\bsugest[õo]es de leitura"]
ACUMULO_FORTE_MIN = 10       # 10+ pontos por real em parceiro = campanha forte (vitrine)
JANELA_REPETICAO_DIAS = 10   # mesma promoção não volta à vitrine dentro desse prazo
VALIDADE_SEM_PRAZO_DIAS = 3  # promoção sem prazo informado conta como ativa por 3 dias (para combos)

TERMOS_REPOSTAGEM = ["prorrog", "últimas horas", "ultimas horas", "acaba hoje", "último dia", "ultimo dia",
                     "ainda dá tempo", "ainda da tempo", "segue valendo", "voltou", "só hoje", "so hoje",
                     "apenas hoje", "corra", "termina hoje", "reta final"]
TERMOS_ACUMULO = [r"(?:pontos?|milhas?|pts)(?:\s+[a-zà-ú]+){0,2}?\s*(?:por|a cada)\s*(?:real|r\$|d[óo]lar)",
                  r"(?:pontos|milhas|pontua\w*)\s*(?:em\s*)?dobr", r"(?:pontos|milhas)\s*(?:em\s*)?tripl",
                  r"acumul", r"cashback", r"pontos em compras", r"lojas parceiras", r"shopping"]


def tem_termo(texto, termos):
    return any(re.search(r"(?<![\w])" + re.escape(t.strip()) + r"(?![\w])", texto) for t in termos)


def programas_citados(texto, tabela):
    achados = []
    for nome, termos in tabela.items():
        if tem_termo(texto, termos):
            achados.append(nome)
    return achados


def maior_desconto(texto):
    nums = re.findall(r"(\d{2})\s*%\s*(?:de\s*)?(?:desconto|off)", texto)
    nums += re.findall(r"desconto\s*(?:de\s*)?(?:até\s*)?(\d{2})\s*%", texto)
    return max([int(n) for n in nums], default=0)


def milheiro_citado(texto):
    m = re.search(r"milheiro.{0,30}?r\$\s*(\d{1,3}(?:,\d{1,2})?)", texto)
    if not m:
        return None
    return float(m.group(1).replace(",", "."))


def prazo_da_promo(texto, hoje=None):
    hoje = hoje or date.today()
    if re.search(r"acaba hoje|último dia|ultimo dia|últimas horas|ultimas horas|só hoje|so hoje|apenas hoje|termina hoje", texto):
        return hoje
    if re.search(r"até amanhã|ate amanha", texto):
        return date.fromordinal(hoje.toordinal() + 1)
    m = re.search(r"até\s*(?:o\s*dia\s*)?(\d{1,2})/(\d{1,2})", texto)
    if m:
        return proxima_data(int(m.group(1)), int(m.group(2)), hoje)
    m = re.search(r"até\s*(?:o\s*dia\s*)?(\d{1,2})\s*de\s*(" + "|".join(MESES) + r")", texto)
    if m:
        return proxima_data(int(m.group(1)), MESES[m.group(2)], hoje)
    return None


def analisar_pontos(item, texto):
    """Etiqueta o post: categoria, programas de origem e destino, bônus, desconto, milheiro e prazo."""
    aereos = programas_citados(texto, PROGRAMAS_AEREOS)
    pontos = programas_citados(texto, PROGRAMAS_PONTOS)
    bonus = maior_bonus(texto)
    desconto = maior_desconto(texto)
    transf = bool(re.search(r"transf|convers|converta|envie pontos|envie seus pontos", texto))
    # "Esfera oferece 30% de bônus para o LATAM Pass": bônus + central/banco + companhia também é transferência
    transf = transf or bool(bonus and pontos and (aereos or any(p in CENTRAIS_DE_PONTOS for p in pontos))
                            and len(set(pontos + aereos)) >= 2)
    compra = bool(re.search(r"compra de (?:pontos|milhas)|compre (?:pontos|milhas)|comprar (?:pontos|milhas)", texto))
    por_real = bool(re.search(TERMOS_ACUMULO[0], texto))
    clube = "clube" in texto and not por_real
    acumulo = any(re.search(t, texto) for t in TERMOS_ACUMULO) and not transf and not compra

    if transf and bonus:
        categoria = "transferencia"
    elif compra:
        categoria = "compra"
    elif por_real and not transf:
        categoria = "acumulo"
    elif clube:
        categoria = "clube"
    elif acumulo:
        categoria = "acumulo"
    else:
        categoria = ""

    # Na transferência, destino é a companhia; sem companhia citada, é a central (banco -> Livelo/Esfera)
    if categoria == "transferencia":
        destinos = aereos or [p for p in pontos if p in CENTRAIS_DE_PONTOS]
        origens = [p for p in pontos if p not in destinos]
    else:
        destinos, origens = aereos, pontos

    if categoria == "acumulo":
        nums = re.findall(r"(\d{1,2}(?:,\d)?)\s*(?:pontos?|milhas?|pts)(?:\s+[a-zà-ú]+){0,2}?\s*(?:por|a cada)\s*(?:real|r\$)", texto)
        item["pontos_por_real"] = max([float(n.replace(",", ".")) for n in nums], default=0)
        item["parceiro"] = next((nome for nome, termos in PARCEIROS.items() if tem_termo(texto, termos)), "")
        item["acumulo_forte_texto"] = any(re.search(t, texto) for t in TERMOS_ACUMULO_FORTE)
    item["nacional"] = any(re.search(t, texto) for t in TERMOS_NACIONAL)
    item["categoria"] = categoria
    item["programas_origem"] = origens
    item["programas_destino"] = destinos
    item["bonus"] = bonus
    item["desconto"] = desconto
    item["milheiro_citado"] = milheiro_citado(texto)
    prazo = prazo_da_promo(texto)
    item["prazo"] = prazo.isoformat() if prazo else ""
    item["repostagem"] = tem_termo(texto, TERMOS_REPOSTAGEM)
    item["assinatura"] = "|".join([categoria, ",".join(sorted(origens)), ",".join(sorted(destinos)),
                                   str(bonus or desconto or item.get("pontos_por_real", "")),
                                   item.get("parceiro", "")]) if categoria else ""


def classificar(item):
    texto = (item["titulo"] + " " + item["resumo"]).lower()
    titulo = item["titulo"].lower()

    if any(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", titulo) for t in TERMOS_IGNORAR):
        return None
    if not any(t in texto for t in TERMOS_PROMO):
        return None

    analisar_pontos(item, texto)
    oferta = eh_oferta_de_passagem(titulo, texto)
    if oferta:
        # Oferta de passagem manda: mesmo que o post cite bônus de transferência, ele é passagem
        item["categoria"] = "passagem"
        numeros = sorted(set(re.findall(r"\d[\d.,]*", titulo)))
        item["assinatura"] = "passagem|" + ",".join(sorted(item.get("programas_destino") or [])) + "|" + ",".join(numeros)
        item["escopo"] = ("gyn_bsb" if tem_termo(texto, TERMOS_GYN) else
                          "internacional" if any(re.search(t, texto) for t in TERMOS_INTERNACIONAL) else
                          "nacional")
        item["nacional"] = item["nacional"] or item["escopo"] == "nacional"
    categoria = item["categoria"]
    if categoria == "passagem":
        tipo = "Passagem com milhas" if re.search(r"pontos|milhas|\bpts\b", titulo) else "Passagem em dinheiro"
    elif categoria == "transferencia":
        tipo = "Transferência bonificada"
    elif categoria in ("compra", "clube"):
        tipo = "Clube / compra de pontos"
    elif categoria == "acumulo":
        tipo = "Acúmulo de pontos"
    elif re.search(RE_PALAVRA_PASSAGEM, titulo) and not eh_noticia(titulo):
        # Fala de passagem mas sem preço no título: o leitor de milhas ainda abre o post
        tipo = "Passagem com milhas" if ("milhas" in texto or "pontos" in texto) else "Passagem em dinheiro"
    else:
        tipo = "Notícia / outros"

    motivos = []
    if any(t in texto for t in TERMOS_GYN) and tipo in ("Passagem em dinheiro", "Passagem com milhas"):
        motivos.append("sai de GYN/BSB")
    if categoria == "transferencia" and bonus_forte(item):
        motivos.append(f"bônus de {item['bonus']}%")
    if any(t in texto for t in TERMOS_URGENTE):
        motivos.append("promoção relâmpago")

    item["tipo"] = tipo
    item["prioridade"] = "alta" if motivos else "normal"
    item["motivos"] = motivos
    return item


def eh_noticia(titulo):
    return any(re.search(t, titulo) for t in TERMOS_NOTICIA)


def eh_oferta_de_passagem(titulo, texto):
    """Título anuncia passagem com preço (dinheiro ou pontos) ou é um alerta de disponibilidade."""
    if eh_noticia(titulo):
        return False
    if re.search(RE_ALERTA_TITULO, titulo) and re.search(r"pontos|milhas|r\$|passage|voo|trecho", titulo):
        return True
    return bool(re.search(RE_PALAVRA_PASSAGEM, titulo) and re.search(RE_PRECO_TITULO, titulo))


def bonus_forte(item):
    """Bônus atinge o corte de pelo menos um programa de destino citado."""
    destinos = item.get("programas_destino") or []
    cortes = [CORTE_BONUS[d] for d in destinos if d in CORTE_BONUS]
    corte = min(cortes) if cortes else 80
    return item.get("bonus", 0) >= corte


# ================= Leitor de milhas =================

class LeitorTabelas(HTMLParser):
    """Junta todas as tabelas do post: cada linha vira lista de células {texto, link, cabecalho}."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tabelas, self.linha, self.celula = [], None, None
        self.nivel = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.nivel += 1
            if self.nivel == 1:
                self.tabelas.append([])
        elif self.nivel and tag == "tr":
            self.linha = []
        elif self.nivel and tag in ("td", "th") and self.linha is not None:
            self.celula = {"texto": "", "link": "", "cabecalho": tag == "th"}
        elif self.nivel and tag == "a" and self.celula is not None and not self.celula["link"]:
            self.celula["link"] = dict(attrs).get("href") or ""
        elif self.nivel and tag == "br" and self.celula is not None:
            self.celula["texto"] += " "

    def handle_endtag(self, tag):
        if tag == "table" and self.nivel:
            self.nivel -= 1
        elif self.nivel and tag in ("td", "th") and self.celula is not None and self.linha is not None:
            self.celula["texto"] = re.sub(r"\s+", " ", self.celula["texto"]).strip()
            self.linha.append(self.celula)
            self.celula = None
        elif self.nivel and tag == "tr" and self.linha is not None:
            if self.linha and self.tabelas:
                self.tabelas[-1].append(self.linha)
            self.linha = None

    def handle_data(self, data):
        if self.celula is not None:
            self.celula["texto"] += data


def numero_milhas(texto):
    t = (texto or "").lower().replace(" ", "")
    m = re.search(r"(\d+(?:[.,]\d+)?)mil(?!h)", t)
    if m:
        return int(round(float(m.group(1).replace(",", ".")) * 1000))
    m = re.search(r"\d{1,3}(?:\.\d{3})+|\d{3,6}", t)
    return int(m.group(0).replace(".", "")) if m else 0


def codigo_aeroporto(texto):
    m = re.search(r"\(([A-Z]{3})\)", texto or "")
    return m.group(1) if m else ""


def proxima_data(dia, mes, hoje=None):
    hoje = hoje or date.today()
    for ano in (hoje.year, hoje.year + 1):
        try:
            d = date(ano, mes, dia)
        except ValueError:
            continue
        if d >= hoje:
            return d
    return None


def data_do_trecho(texto, link, hoje=None):
    # 1º: data dentro do link de reserva (Smiles usa departureDate em milissegundos)
    if link:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(html.unescape(link)).query)
        valor = (q.get("departureDate") or [""])[0]
        if valor.isdigit() and len(valor) >= 12:
            return datetime.fromtimestamp(int(valor) / 1000, tz=timezone.utc).date()
    # 2º: texto da coluna Datas, ex.: "8 de janeiro" ou "3, 5 e 10 de março" (pega a primeira)
    t = (texto or "").lower()
    m = re.search(r"\b(" + "|".join(MESES) + r")\b", t)
    if m:
        dia = re.search(r"\d{1,2}", t[:m.start()])
        if dia:
            return proxima_data(int(dia.group(0)), MESES[m.group(1)], hoje)
    m = re.search(r"(\d{1,2})/(\d{1,2})", t)
    if m:
        return proxima_data(int(m.group(1)), int(m.group(2)), hoje)
    return None


def programa_do_post(titulo):
    t = titulo.lower()
    if "smiles" in t:
        return "Smiles"
    if "latam" in t:
        return "LATAM Pass"
    if "azul" in t:
        return "Azul Fidelidade"
    return ""


def indice(cabecalhos, *palavras, evitar=()):
    for i, c in enumerate(cabecalhos):
        if any(p in c for p in palavras) and not any(e in c for e in evitar):
            return i
    return None


def extrair_trechos(conteudo_html, hoje=None):
    """Devolve todos os trechos das tabelas: origem, destino, milhas (padrão e clube), data, link."""
    leitor = LeitorTabelas()
    leitor.feed(conteudo_html or "")
    trechos = []
    for tabela in leitor.tabelas:
        if len(tabela) < 2:
            continue
        cab = [c["texto"].lower() for c in tabela[0]]
        i_ori = indice(cab, "origem")
        i_des = indice(cab, "destino")
        if i_ori is None or i_des is None:
            continue
        i_dat = indice(cab, "data")
        i_cia = indice(cab, "companhia")
        i_res = indice(cab, "reserva")
        cols_milhas = [i for i, c in enumerate(cab)
                       if i not in (i_ori, i_des, i_dat, i_cia, i_res)
                       and any(p in c for p in ("trecho", "cliente", "diamante", "clube", "milhas", "pontos"))]
        if not cols_milhas:
            continue
        i_padrao = indice(cab, "demais", "cliente", "padrão", "padrao")
        if i_padrao not in cols_milhas:
            i_padrao = None
        for linha in tabela[1:]:
            if len(linha) < len(cab):
                continue
            ori = codigo_aeroporto(linha[i_ori]["texto"])
            des = codigo_aeroporto(linha[i_des]["texto"])
            valores = {i: numero_milhas(linha[i]["texto"]) for i in cols_milhas}
            valores = {i: v for i, v in valores.items() if v >= 1000}
            if not ori or not des or not valores:
                continue
            padrao = valores.get(i_padrao) or max(valores.values())
            clube = min(valores.values())
            link = linha[i_res]["link"] if i_res is not None else ""
            if not link:
                link = next((c["link"] for c in linha if c["link"]), "")
            texto_data = linha[i_dat]["texto"] if i_dat is not None else ""
            quando = data_do_trecho(texto_data, link, hoje)
            trechos.append({
                "origem": ori, "destino": des,
                "origem_nome": re.sub(r"\s*\([A-Z]{3}\)", "", linha[i_ori]["texto"]).strip(),
                "destino_nome": re.sub(r"\s*\([A-Z]{3}\)", "", linha[i_des]["texto"]).strip(),
                "milhas": padrao, "milhas_clube": clube if clube < padrao else None,
                "data": quando.isoformat() if quando else "", "datas_texto": texto_data,
                "cia": linha[i_cia]["texto"] if i_cia is not None else "",
                "reserva": html.unescape(link),
            })
    return trechos


# ---- v3.6: trechos escritos em texto corrido ----
RE_SEPARADOR_TRECHO = re.compile(r"^\s*(?:x|×|>|→|⇄|–|—|-|/|e|para|até|ate|com destino a)\s+", re.I)
RE_MILHAS_TEXTO = re.compile(r"(\d{1,3}(?:\.\d{3})+|\d+(?:[.,]\d+)?\s*mil(?!h)|\d{3,6})\s*(?:milhas|pontos|pts)\b", re.I)
RE_BLOCO_FIM = re.compile(r"</(?:p|li|h[1-6]|tr|div|blockquote)>|<br\s*/?>", re.I)

def blocos_do_post(conteudo_html):
    """HTML do post -> [(texto, [links])], um parágrafo / item de lista por bloco."""
    blocos = []
    for pedaco in RE_BLOCO_FIM.split(conteudo_html or ""):
        links = [html.unescape(h) for h in re.findall(r"href=[\"']([^\"']+)[\"']", pedaco)]
        texto = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", pedaco))).strip()
        if texto:
            blocos.append((texto, links))
    return blocos


def linhas_do_post(conteudo_html):
    """HTML do post -> linhas de texto (um parágrafo / item de lista por linha)."""
    return [texto for texto, _ in blocos_do_post(conteudo_html)]


CONECTORES_NOME = {"de", "do", "da", "dos", "das", "del"}


def nome_antes(texto):
    """Nome da cidade logo antes do '(AAA)': palavras com inicial maiúscula (e 'de/do/da' no meio)."""
    ultimo = re.split(r"[:;,.!?>–—→⇄(\[\]]", texto or "")[-1].split()
    nome = []
    for palavra in reversed(ultimo):
        if palavra[:1].isupper() or (palavra.lower() in CONECTORES_NOME and nome):
            nome.insert(0, palavra)
        else:
            break
    while nome and nome[0].lower() in CONECTORES_NOME:
        nome.pop(0)
    return " ".join(nome)


def extrair_trechos_texto(conteudo_html):
    """Trechos citados em texto: 'Cidade (AAA) x Cidade (BBB) – a partir de N milhas'."""
    achados = []
    for linha, links in blocos_do_post(conteudo_html):
        codigos = list(re.finditer(r"\(([A-Z]{3})\)", linha))
        if len(codigos) < 2:
            continue
        for a, b in zip(codigos, codigos[1:]):
            entre = linha[a.end():b.start()]
            sep = RE_SEPARADOR_TRECHO.match(entre)
            if not sep or len(entre) > 50:
                continue
            resto = linha[b.end():]
            corte = re.search(r"\([A-Z]{3}\)", resto)           # milhas antes do próximo trecho
            m = RE_MILHAS_TEXTO.search(resto[:corte.start()] if corte else resto)
            if not m:
                continue
            milhas = numero_milhas(m.group(0))
            if milhas < 1000:
                continue
            ambos = sep.group(0).strip().lower() in ("x", "×", "⇄", "e", "/")
            achados.append({
                "origem": a.group(1), "destino": b.group(1),
                "origem_nome": nome_antes(linha[:a.start()]),
                "destino_nome": nome_antes(entre[sep.end():] if entre[sep.end():].strip() else ""),
                "milhas": milhas, "milhas_clube": None, "data": "", "datas_texto": "",
                "cia": "", "reserva": next((l for l in links if link_oficial_ok(l)), ""),
                "ambos_sentidos": ambos, "formato": "texto",
            })
    # mesmo trecho repetido no post: fica o menor preço
    unicos = {}
    for t in achados:
        chave = tuple(sorted((t["origem"], t["destino"]))) if t["ambos_sentidos"] else (t["origem"], t["destino"])
        if chave not in unicos or t["milhas"] < unicos[chave]["milhas"]:
            unicos[chave] = t
    return list(unicos.values())


RE_DIAS = r"(\d{1,2}(?:\s*(?:,|e)\s*\d{1,2})*)"
RE_MES = r"(" + "|".join(sorted(MESES, key=len, reverse=True)) + r")"


def datas_por_mes(texto, hoje=None):
    """'3, 5 e 10 de março' / 'Dezembro/2026: 3, 5 e 8' -> ['Março/2027: 3, 5 e 10', ...]."""
    hoje = hoje or date.today()
    t = (texto or "").lower()
    grupos = {}
    padroes = [
        (re.compile(RE_DIAS + r"\s+de\s+" + RE_MES + r"(?:\s+(?:de\s+)?(\d{4}))?"), (1, 2, 3)),
        (re.compile(RE_MES + r"(?:\s*(?:/|de)\s*(\d{4}))?\s*:\s*" + RE_DIAS), (3, 1, 2)),
    ]
    for regex, (g_dias, g_mes, g_ano) in padroes:
        for m in regex.finditer(t):
            mes = MESES[m.group(g_mes)]
            dias = sorted({int(d) for d in re.findall(r"\d{1,2}", m.group(g_dias)) if 1 <= int(d) <= 31})
            if not dias:
                continue
            if m.group(g_ano):
                ano = int(m.group(g_ano))
            else:
                primeiro = proxima_data(dias[-1], mes, hoje)
                ano = primeiro.year if primeiro else hoje.year
            if date(ano, mes, 1) < date(hoje.year, hoje.month, 1):
                continue
            grupos.setdefault((ano, mes), set()).update(dias)
    nomes = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro",
             "Outubro", "Novembro", "Dezembro"]
    saida = []
    for (ano, mes) in sorted(grupos):
        dias = [str(d) for d in sorted(grupos[(ano, mes)])]
        lista = dias[0] if len(dias) == 1 else ", ".join(dias[:-1]) + " e " + dias[-1]
        saida.append(f"{nomes[mes - 1]}/{ano}: {lista}")
    return saida


def conteudo_do_post(link):
    """Pega o post completo pelo canal público do WordPress (wp-json)."""
    partes = urllib.parse.urlparse(link)
    slug = [p for p in partes.path.split("/") if p]
    if not slug:
        return ""
    api = (f"{partes.scheme}://{partes.netloc}/wp-json/wp/v2/posts?"
           + urllib.parse.urlencode({"slug": slug[-1], "_fields": "content"}))
    dados = json.loads(baixar(api).decode("utf-8"))
    if isinstance(dados, list) and dados:
        return (dados[0].get("content") or {}).get("rendered") or ""
    return ""


# ================= Link oficial (v3.5): a vitrine nao cita o blog =================

DOMINIOS_OFICIAIS = (
    "smiles.com.br", "shoppingsmiles.com.br", "voegol.com.br", "latamairlines.com", "latam.com",
    "latampass.com", "voeazul.com.br", "tudoazul.com", "azulfidelidade.com.br", "livelo.com.br",
    "esfera.com.vc", "flytap.com", "aa.com", "united.com", "delta.com", "copaair.com", "aerolineas.com.ar",
    "airfrance.com.br", "klm.com.br", "iberia.com", "aireuropa.com", "jetsmart.com", "flysky.com",
    "c6bank.com.br", "itau.com.br", "iupp.com.br", "bradesco.com.br", "santander.com.br", "bb.com.br",
    "caixa.gov.br", "bancointer.com.br", "inter.co", "nubank.com.br", "btgpactual.com", "xpi.com.br",
    "sicoob.com.br", "sicredi.com.br",
)
SITE_DO_PROGRAMA = {
    "Smiles": "https://www.smiles.com.br", "LATAM Pass": "https://latampass.latam.com",
    "Azul Fidelidade": "https://www.voeazul.com.br", "TAP Miles&Go": "https://www.flytap.com",
    "AAdvantage": "https://www.aa.com", "Livelo": "https://www.livelo.com.br", "Esfera": "https://www.esfera.com.vc",
}
LINK_VOOS_GYN = ("https://www.google.com/travel/flights?hl=pt-BR&gl=BR&curr=BRL&q="
                 + urllib.parse.quote("Voos saindo de Goiânia"))
RE_RASTREIO = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|refer|aff|afil|parceiro|partner|source$|origem$|campaign)", re.I)
RE_MARCA_BLOG = re.compile(r"\b(alerta(?:s)? (?:de passagens? )?ppv|ppv|pontos pra voar|passageiro de primeira|"
                           r"melhores destinos)\b\s*[:\-–|!]*\s*", re.I)


class LeitorLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            href = dict(attrs).get("href") or ""
            if href.startswith("http"):
                self.links.append(href)


def link_oficial_ok(url):
    try:
        partes = urllib.parse.urlparse(url)
    except ValueError:
        return False
    host = (partes.hostname or "").lower()
    if host.endswith("google.com") and partes.path.startswith("/travel"):
        return True
    return any(host == d or host.endswith("." + d) for d in DOMINIOS_OFICIAIS)


def limpar_link(url):
    """Tira do link os parametros de rastreio do blog (utm, afiliado...)."""
    try:
        partes = urllib.parse.urlparse(url)
        query = [(k, v) for k, v in urllib.parse.parse_qsl(partes.query, keep_blank_values=True)
                 if not RE_RASTREIO.match(k)]
        return urllib.parse.urlunparse(partes._replace(query=urllib.parse.urlencode(query), fragment=""))
    except ValueError:
        return url


def achar_link_oficial(item):
    """Link de quem vende: 1o link oficial do post; senao reserva do resgate; senao site do programa."""
    if "link_oficial" in item:
        return item["link_oficial"]
    link = ""
    try:
        leitor = LeitorLinks()
        leitor.feed(conteudo_do_post(item["link"]))
        link = next((limpar_link(u) for u in leitor.links if link_oficial_ok(u)), "")
    except Exception as erro:
        print(f"Link oficial: não consegui ler {item.get('link')}: {erro}")
    if not link:
        link = next((limpar_link(t["reserva"]) for t in item.get("resgates") or []
                     if t.get("reserva") and link_oficial_ok(t["reserva"])), "")
    if not link:
        destino, origem = item.get("programas_destino") or [], item.get("programas_origem") or []
        # Na transferencia, quem faz a acao e a origem (Livelo/Esfera/banco); no resto, o programa da companhia
        programas = origem + destino if item.get("categoria") == "transferencia" else destino + origem
        link = next((SITE_DO_PROGRAMA[p] for p in programas if p in SITE_DO_PROGRAMA), "")
    item["link_oficial"] = link
    return link


def titulo_limpo(titulo):
    """Titulo para a vitrine, sem marca de blog ('Alerta PPV: ...')."""
    limpo = RE_MARCA_BLOG.sub("", titulo or "").strip(" -–|:!")
    return (limpo[:1].upper() + limpo[1:]) if limpo else (titulo or "")


def preco_dinheiro(origem, destino, quando, token, precos_medidor):
    """Menor preço SÓ IDA em dinheiro do mesmo trecho. Devolve (preço, de onde veio)."""
    if token and quando:
        for data_busca, rotulo in ((quando, "mesmo dia"), (quando[:7], "mesmo mês")):
            try:
                params = {"origin": origem, "destination": destino, "currency": "brl",
                          "departure_at": data_busca, "one_way": "true", "sorting": "price",
                          "limit": 10, "market": "br"}
                url = ("https://api.travelpayouts.com/aviasales/v3/prices_for_dates?"
                       + urllib.parse.urlencode(params))
                req = urllib.request.Request(url, headers={
                    "X-Access-Token": token, "Accept-Encoding": "identity", "User-Agent": "RadarTrip/2.0"})
                with urllib.request.urlopen(req, timeout=25) as r:
                    resp = json.loads(r.read().decode("utf-8"))
                dados = resp.get("data") or []
                dados = list(dados.values()) if isinstance(dados, dict) else dados
                valores = [o["price"] for o in dados if o.get("price")]
                if valores:
                    return int(min(valores)), rotulo
            except Exception as erro:
                print(f"Milhas: preço em dinheiro {origem}-{destino} falhou: {erro}")
                break
    # Último recurso: metade do ida e volta que o medidor de preços já guardou
    if origem in AEROPORTOS_CASA:
        cidade = CIDADE_DO_AEROPORTO.get(destino, destino)
        ultimo = (precos_medidor.get(f"{origem}-{cidade}") or [{}])[-1]
        if ultimo.get("preco"):
            return int(ultimo["preco"] / 2), "estimativa: metade do ida e volta"
    return None, ""


# ---- v3.6: preço em dinheiro AO VIVO na LATAM pela GeckoAPI (pago: 5 créditos por busca) ----
ARQ_GECKO = os.path.join(PASTA, "gecko.json")
GECKO_API = "https://api.geckoapi.com.br/v1/extract"
GECKO_CUSTO = 5                  # créditos por busca na LATAM
GECKO_MAX_CREDITOS_MES = 50      # teto mensal só para o vigia (10 buscas). Suba para 150+ quando assinar um plano.


def preco_latam_gecko(origem, destino, quando):
    """Menor preço só ida na LATAM, ao vivo. Só roda com chave no cofre e dentro do teto do mês."""
    chave = os.environ.get("GECKO_API_KEY")
    if not chave or not quando:
        return None
    estado = carregar(ARQ_GECKO, {"mes": "", "creditos": 0, "cache": {}})
    mes = date.today().strftime("%Y-%m")
    if estado.get("mes") != mes:
        estado = {"mes": mes, "creditos": 0, "cache": estado.get("cache", {})}
    id_busca = f"{origem}-{destino}-{quando}"
    if id_busca in estado["cache"]:
        return estado["cache"][id_busca]
    if estado["creditos"] + GECKO_CUSTO > GECKO_MAX_CREDITOS_MES:
        print(f"Gecko: teto do mês atingido ({estado['creditos']} créditos); {id_busca} sem busca ao vivo.")
        return None
    corpo = {"target": "latamairlines.com", "type": "plp", "from": origem, "to": destino,
             "departureDate": quando, "numAdults": 1, "numChildren": 0, "numInfants": 0}
    req = urllib.request.Request(GECKO_API, data=json.dumps(corpo).encode("utf-8"), headers={
        "Authorization": f"Bearer {chave}", "Content-Type": "application/json", "User-Agent": "RadarTrip/3.6"})
    preco = None
    try:
        with urllib.request.urlopen(req, timeout=100) as r:
            resp = json.loads(r.read().decode("utf-8"))
        estado["creditos"] += GECKO_CUSTO
        itens = ((resp.get("data") or {}).get("items")) or []
        valores = [i["price"]["amount"] for i in itens if (i.get("price") or {}).get("amount")]
        preco = int(min(valores)) if valores else None
    except urllib.error.HTTPError as erro:
        if erro.code not in (402, 429) and erro.code < 500:
            estado["creditos"] += GECKO_CUSTO
        print(f"Gecko: LATAM {id_busca} falhou: HTTP {erro.code}")
    except Exception as erro:
        print(f"Gecko: LATAM {id_busca} falhou: {erro}")
    estado["cache"][id_busca] = preco
    estado["cache"] = dict(list(estado["cache"].items())[-300:])
    salvar(ARQ_GECKO, estado)
    return preco


def avaliar_trecho(trecho, programa, token, precos_medidor):
    preco, origem_preco = preco_dinheiro(trecho["origem"], trecho["destino"], trecho["data"],
                                         token, precos_medidor)
    if programa == "LATAM Pass" and trecho.get("data") and origem_preco != "mesmo dia":
        ao_vivo = preco_latam_gecko(trecho["origem"], trecho["destino"], trecho["data"])
        if ao_vivo:
            preco, origem_preco = ao_vivo, "LATAM ao vivo"
    trecho["programa"] = programa
    trecho["preco_dinheiro"] = preco
    trecho["preco_origem"] = origem_preco
    trecho["milheiro"] = round(preco / (trecho["milhas"] / 1000), 2) if preco else None
    custo = CUSTO_MILHEIRO.get(programa)
    if trecho["milheiro"] and custo:
        trecho["veredito"] = "milhas" if trecho["milheiro"] >= custo else "dinheiro"
    else:
        trecho["veredito"] = ""
    return trecho


def ler_milhas(item, token, precos_medidor):
    """Abre o post, separa os trechos de/para GYN/BSB e calcula o milheiro de cada um."""
    conteudo = conteudo_do_post(item["link"])
    trechos = extrair_trechos(conteudo)
    vistos = {(t["origem"], t["destino"]) for t in trechos}
    em_texto = [t for t in extrair_trechos_texto(conteudo)
                if (t["origem"], t["destino"]) not in vistos and (t["destino"], t["origem"]) not in vistos]
    if len(em_texto) == 1:
        # Uma rota só no post: as datas citadas no texto são dela
        linhas = linhas_do_post(conteudo)
        datas = datas_por_mes(" ".join(l for l in linhas if "volta" not in l.lower()))
        volta = datas_por_mes(" ".join(l for l in linhas if "volta" in l.lower()))
        if volta:
            em_texto[0]["datas_volta"] = volta
        if datas:
            em_texto[0]["datas_mes"] = datas
            primeira = re.match(r"(\w+)/(\d{4}): (\d{1,2})", datas[0])
            if primeira:
                mes = MESES.get(primeira.group(1).lower())
                try:
                    em_texto[0]["data"] = date(int(primeira.group(2)), mes, int(primeira.group(3))).isoformat()
                except (TypeError, ValueError):
                    pass
    trechos += em_texto
    for t in trechos:
        # Rota de ida e volta (A x B): mostra saindo de casa
        if t.get("ambos_sentidos") and t["destino"] in AEROPORTOS_CASA and t["origem"] not in AEROPORTOS_CASA:
            t["origem"], t["destino"] = t["destino"], t["origem"]
            t["origem_nome"], t["destino_nome"] = t["destino_nome"], t["origem_nome"]
        if not t.get("datas_mes") and t.get("datas_texto"):
            t["datas_mes"] = datas_por_mes(t["datas_texto"])
    casa = [t for t in trechos if t["origem"] in AEROPORTOS_CASA or t["destino"] in AEROPORTOS_CASA]
    casa.sort(key=lambda t: (t["origem"] != "GYN", t["origem"] not in AEROPORTOS_CASA, t["milhas"]))
    programa = programa_do_post(item["titulo"]) or programa_do_post(" ".join(linhas_do_post(conteudo))[:600])
    return len(trechos), [avaliar_trecho(t, programa, token, precos_medidor)
                          for t in casa[:MAX_TRECHOS_POR_POST]]


def processar_milhas(itens, token, precos_medidor, registro):
    """Para cada post de milhas ainda não lido, guarda os trechos GYN/BSB no item e no registro."""
    for item in itens:
        if item.get("tipo") != "Passagem com milhas" or item["id"] in registro["posts"]:
            continue
        try:
            total, trechos = ler_milhas(item, token, precos_medidor)
        except Exception as erro:
            print(f"Milhas: não consegui ler {item['link']}: {erro}")
            continue
        registro["posts"].append(item["id"])
        item["trechos_total"] = total
        item["resgates"] = trechos
        if trechos:
            if "resgate de GYN/BSB" not in item["motivos"]:
                item["motivos"].append("resgate de GYN/BSB")
            if trechos[0].get("programa") == "LATAM Pass" and "LATAM Pass de GYN/BSB" not in item["motivos"]:
                item["motivos"].insert(0, "LATAM Pass de GYN/BSB")
            item["prioridade"] = "alta"
            for t in trechos:
                registro["resgates"].append({**t, "post": item["link"], "titulo": item["titulo"],
                                             "visto_em": datetime.now(timezone.utc).isoformat()})
        print(f"Milhas: {item['titulo'][:60]}... -> {total} trechos, {len(trechos)} de GYN/BSB")
    registro["posts"] = registro["posts"][-MAX_VISTOS:]
    registro["resgates"] = registro["resgates"][-MAX_RESGATES:]


# ================= Saída =================

def carregar(caminho, padrao):
    try:
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return padrao


def salvar(caminho, dados):
    os.makedirs(PASTA, exist_ok=True)
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)


def telegram(texto, chat=None):
    """Envia para o chat de BASTIDORES (padrão) ou para o chat informado (ex.: canal vitrine)."""
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = chat or os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Sem TELEGRAM_TOKEN/TELEGRAM_CHAT_ID; mensagem não enviada.")
        print(texto)
        return
    corpo = json.dumps({
        "chat_id": chat, "text": texto, "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=corpo, headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(req, timeout=25).read()


# ================= Vitrine (canal) x Bastidores (chat do dono) =================

ARQ_CANAL = os.path.join(PASTA, "canal.json")
ARQ_CODIGOS = os.path.join(PASTA, "codigos.json")
MAX_CODIGOS = 5000
RODAPE_VITRINE = ("<i>Preços e disponibilidade mudam a todo momento. "
                  "Confira no site da companhia antes de comprar.</i>")


def descobrir_canal():
    """Procura o canal em que o robô foi colocado como administrador (sem ninguém copiar código)."""
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        return None
    filtro = urllib.parse.quote(json.dumps(["my_chat_member", "channel_post"]))
    url = f"https://api.telegram.org/bot{token}/getUpdates?allowed_updates={filtro}"
    try:
        with urllib.request.urlopen(url, timeout=25) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except Exception as erro:
        print(f"Vitrine: não consegui procurar o canal: {erro}")
        return None
    achado = None
    for upd in resp.get("result") or []:
        membro = upd.get("my_chat_member")
        if membro and (membro.get("chat") or {}).get("type") == "channel":
            status = (membro.get("new_chat_member") or {}).get("status")
            achado = membro["chat"] if status == "administrator" else None
        post = upd.get("channel_post")
        if post and (post.get("chat") or {}).get("type") == "channel":
            achado = post["chat"]
    if not achado:
        return None
    canal = {"id": achado["id"], "titulo": achado.get("title", "")}
    salvar(ARQ_CANAL, canal)
    telegram(f"📣 <b>Canal vitrine conectado:</b> {html.escape(canal['titulo'])}\n"
             "A partir de agora, os alertas fortes vão para lá. Aqui ficam só os bastidores.")
    return canal


def canal_vitrine():
    canal = carregar(ARQ_CANAL, None)
    if not canal:
        canal = descobrir_canal()
    return (canal or {}).get("id")


def enviar_vitrine(texto, registro):
    """Manda um alerta forte para a VITRINE com código de rastreio RT-xxxx.
    Sem canal criado ainda, manda ao chat do dono como prévia."""
    codigos = carregar(ARQ_CODIGOS, {"ultimo": 0, "alertas": []})
    codigos["ultimo"] += 1
    codigo = f"RT-{codigos['ultimo']:04d}"
    final = f"{texto}\n\n🏷️ <b>{codigo}</b> · Radar Trip\n{RODAPE_VITRINE}"
    canal = canal_vitrine()
    if canal:
        telegram(final, canal)
    else:
        telegram("👁️ <i>Prévia da vitrine (canal ainda não criado)</i>\n\n" + final)
    codigos["alertas"].append({"codigo": codigo, "enviado_em": datetime.now(timezone.utc).isoformat(),
                               "canal": bool(canal), **registro})
    codigos["alertas"] = codigos["alertas"][-MAX_CODIGOS:]
    salvar(ARQ_CODIGOS, codigos)
    return codigo


# ================= Teste forçado (v3.3.1): alerta de praia, uma única vez =================

ARQ_TESTE = os.path.join(PASTA, "teste_praia.json")
# Pesquisa no Google Flights de 03/11/2026: sexta de manhã -> domingo à noite, saindo de GYN
TESTE_PRAIA = [
    {"medalha": "🥇", "cidade": "Aracaju", "cod": "AJU", "ida": "2027-03-05", "volta": "2027-03-07",
     "preco": "R$ 1.076", "cia": "Azul", "voo_ida": "04:55 → 09:40, 1 parada",
     "voo_volta": "17:45 → 23:55, 1 parada"},
    {"medalha": "🥈", "cidade": "Rio de Janeiro", "cod": "RIO", "ida": "2027-02-19", "volta": "2027-02-21",
     "preco": "R$ 1.137", "cia": "LATAM", "voo_ida": "06:10 → 09:50, 1 parada",
     "voo_volta": "17:00 → 23:55, 2 paradas"},
    {"medalha": "🥉", "cidade": "Florianópolis", "cod": "FLN", "ida": "2027-02-26", "volta": "2027-02-28",
     "preco": "R$ 1.154", "cia": "LATAM", "voo_ida": "04:55 → 10:30, 1 parada",
     "voo_volta": "19:20 → 23:55, 1 parada"},
]


def montar_teste_praia():
    e = html.escape
    partes = [
        "🧪 <b>TESTE</b> · mensagem de demonstração do Radar Trip",
        "",
        "🏖️ <b>PRAIA NO FIM DE SEMANA · SAINDO DE GOIÂNIA</b>",
        "<i>Sexta de manhã → domingo à noite. Sem faltar ao trabalho.</i>",
    ]
    for o in TESTE_PRAIA:
        consulta = f"Voos de GYN para {o['cod']} em {o['ida']} volta {o['volta']}"
        link = "https://www.google.com/travel/flights?hl=pt-BR&curr=BRL&q=" + consulta.replace(" ", "%20")
        partes += [
            "",
            f"{o['medalha']} <b>{e(o['cidade'])}</b> · {data_curta(o['ida'])} a {data_curta(o['volta'])}",
            f"💰 <b>{o['preco']}</b> ida e volta · {e(o['cia'])}",
            f"🛫 Sex {e(o['voo_ida'])}",
            f"🛬 Dom {e(o['voo_volta'])}",
            f"🔗 <a href=\"{e(link)}\">Ver no Google Flights</a>",
        ]
    partes += [
        "",
        "💡 Quer voltar sem escala no domingo? Maceió e Recife têm voo direto à noite.",
        "📅 Melhor época: do fim de fevereiro a junho e de agosto a setembro.",
        "<i>Preços por pessoa, com taxas, pesquisados em 03/11/2026.</i>",
        "",
        "🏷️ <b>RT-TESTE</b> · Radar Trip",
        RODAPE_VITRINE,
    ]
    return "\n".join(partes)


def teste_forcado():
    """Manda o alerta de demonstração UMA vez (canal ou, sem canal, chat do dono). Nunca derruba o robô."""
    if os.path.exists(ARQ_TESTE):
        return
    try:
        texto = montar_teste_praia()
        canal = canal_vitrine()
        if canal:
            telegram(texto, canal)
        else:
            telegram("👁️ <i>Prévia da vitrine (canal ainda não criado)</i>\n\n" + texto)
        salvar(ARQ_TESTE, {"enviado_em": datetime.now(timezone.utc).isoformat(), "canal": bool(canal)})
        print("Teste da praia enviado " + ("ao canal vitrine." if canal else "ao chat do dono (prévia)."))
    except Exception as erro:
        print(f"Teste da praia falhou: {erro}")


EMOJI_TIPO = {
    "Notícia / outros": "📰",
    "Acúmulo de pontos": "🛍️",
    "Transferência bonificada": "🔄",
    "Clube / compra de pontos": "🛒",
    "Passagem com milhas": "🎯",
    "Passagem em dinheiro": "💵",
}


def reais(valor):
    texto = f"{valor:,.2f}" if valor < 100 else f"{valor:,.0f}"
    return "R$ " + texto.replace(",", "X").replace(".", ",").replace("X", ".")


def milhar(n):
    return f"{n:,}".replace(",", ".")


def data_curta(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m")
    except ValueError:
        return ""


def linha_resgate(t):
    e = html.escape
    quando = data_curta(t["data"]) or e(t["datas_texto"][:20])
    clube = f" ({milhar(t['milhas_clube'])} no clube)" if t.get("milhas_clube") else ""
    ori = f"{t['origem_nome']} ({t['origem']})" if t.get("origem_nome") else t["origem"]
    des = f"{t['destino_nome']} ({t['destino']})" if t.get("destino_nome") else t["destino"]
    seta = "⇄" if t.get("ambos_sentidos") else "→"
    quando = f" · {quando}" if quando else ""
    linha = (f"• <b>{e(ori)} {seta} {e(des)}</b>{quando} · "
             f"<b>{milhar(t['milhas'])}</b> milhas{clube}")
    if t.get("milheiro"):
        estimativa = (" (estimado)" if t["preco_origem"].startswith("estimativa") else
                      " (LATAM ao vivo)" if t["preco_origem"] == "LATAM ao vivo" else "")
        linha += (f"\n   💵 Em dinheiro: {reais(t['preco_dinheiro'])} só ida{estimativa} → "
                  f"milheiro <b>{reais(t['milheiro'])}</b>")
        custo = CUSTO_MILHEIRO.get(t.get("programa"))
        if t["veredito"] == "milhas":
            linha += f"\n   ✅ <b>Vale usar milhas</b> (comprar 1.000 milhas sai ~{reais(custo)})"
        elif t["veredito"] == "dinheiro":
            linha += f"\n   💸 <b>Melhor pagar em dinheiro</b> (comprar 1.000 milhas sai ~{reais(custo)})"
    else:
        linha += "\n   ❔ Sem preço em dinheiro recente para comparar"
    if t.get("reserva"):
        linha += f"\n   <a href=\"{e(limpar_link(t['reserva']))}\">Reservar</a>"
    return linha


AVISO_RESGATE = ("<i>ℹ️ Simulação para 1 adulto, tarifa mais barata encontrada. Pontos e datas mudam a "
                 "todo momento e podem variar conforme a categoria no programa. Confira antes de emitir.</i>")


def bloco_resgate(t):
    """v3.6: um trecho no formato 'Oportunidade de resgate' (vitrine)."""
    e = html.escape
    ori = f"{t['origem_nome']} ({t['origem']})" if t.get("origem_nome") else t["origem"]
    des = f"{t['destino_nome']} ({t['destino']})" if t.get("destino_nome") else t["destino"]
    seta = "⇄" if t.get("ambos_sentidos") else "→"
    linhas = [f"🛫 <b>{e(ori)} {seta} {e(des)}</b>"]
    if t.get("cia"):
        linhas.append(f"✈️ Voando {e(t['cia'])}")
    clube = f" ({milhar(t['milhas_clube'])} no clube)" if t.get("milhas_clube") else ""
    linhas.append(f"💰 A partir de <b>{milhar(t['milhas'])} pontos</b> o trecho + taxas{clube}")
    datas, volta = t.get("datas_mes") or [], t.get("datas_volta") or []
    if datas:
        linhas.append("📅 <b>Datas de ida:</b>" if volta else "📅 <b>Datas:</b>")
        linhas += [f"   {e(d)}" for d in datas[:6]]
    if volta:
        linhas.append("📅 <b>Datas de volta:</b>")
        linhas += [f"   {e(d)}" for d in volta[:6]]
    if not datas and not volta and data_curta(t.get("data") or ""):
        linhas.append(f"📅 Data: {data_curta(t['data'])}")
    if t.get("milheiro"):
        origem = (" (estimado)" if t["preco_origem"].startswith("estimativa") else
                  " · conferido ao vivo na LATAM" if t["preco_origem"] == "LATAM ao vivo" else "")
        linhas.append(f"💵 Pagando em dinheiro: {reais(t['preco_dinheiro'])} só ida{origem}")
        custo = CUSTO_MILHEIRO.get(t.get("programa"))
        texto_milheiro = f"📊 Cada 1.000 pontos rendem <b>{reais(t['milheiro'])}</b> aqui"
        if t["veredito"] == "milhas":
            texto_milheiro += f" ✅ <b>vale usar pontos</b> (comprar 1.000 sai ~{reais(custo)})"
        elif t["veredito"] == "dinheiro":
            texto_milheiro += f" 💸 <b>melhor pagar em dinheiro</b> (comprar 1.000 sai ~{reais(custo)})"
        linhas.append(texto_milheiro)
    link = limpar_link(t["reserva"]) if t.get("reserva") and link_oficial_ok(t["reserva"]) else \
        SITE_DO_PROGRAMA.get(t.get("programa"), "")
    if link:
        linhas.append(f"👉 <a href=\"{e(link)}\">Emitir no site {'da ' + e(t['programa']) if t.get('programa') else ''}</a>")
    return "\n".join(linhas)


def formatar(item):
    e = html.escape
    topo = "🔥 <b>PRIORIDADE</b> · " + e(", ".join(item["motivos"])) + "\n" if item["prioridade"] == "alta" else ""
    texto = (
        f"{topo}{EMOJI_TIPO.get(item['tipo'], '✈️')} <b>{e(item['titulo'])}</b>\n"
        f"<i>{e(item['tipo'])} · {e(item['fonte'])}</i>\n"
    )
    if item.get("resgates"):
        texto += ("\n✈️ <b>Trechos de/para GYN/BSB:</b>\n"
                  + "\n".join(linha_resgate(t) for t in item["resgates"])
                  + "\n<i>Milheiro sem contar taxas. Confira no site antes de emitir.</i>\n\n")
    if item.get("fora_vitrine"):
        texto += f"<i>Fora da vitrine: {e(item['fora_vitrine'])}</i>\n"
    return texto + f"<a href=\"{e(item['link'])}\">Abrir promoção</a>"


ARQ_PROMOS = os.path.join(PASTA, "promos.json")
ARQ_ESTREIA = os.path.join(PASTA, "reavaliacao_v34.json")   # v3.4: reavalia as últimas 24h uma vez
HORAS_ESTREIA = 24


def prazo_texto(iso):
    if not iso:
        return ""
    d = date.fromisoformat(iso)
    if d == date.today():
        return "⏰ <b>Termina hoje</b>"
    return f"⏰ Até {d.strftime('%d/%m')}"


def custo_ref(programas):
    valores = [CUSTO_MILHEIRO[p] for p in programas if p in CUSTO_MILHEIRO]
    return max(valores) if valores else None


def decidir_vitrine(item, promos, hoje=None):
    """Devolve (vai_para_vitrine, motivo). Só o que é forte e relevante vai para a vitrine."""
    hoje = hoje or date.today()
    categoria = item.get("categoria", "")
    vai, motivo = False, ""
    if categoria == "acumulo":
        vai, motivo = acumulo_compensa(item, promos)
    elif item.get("resgates"):
        vai = True
    elif "promoção relâmpago" in item.get("motivos", []):
        vai = True
    elif "sai de GYN/BSB" in item.get("motivos", []):
        vai = True
    elif categoria == "passagem":
        vai = True
    elif item.get("nacional") and item["tipo"] in ("Passagem em dinheiro", "Passagem com milhas"):
        vai = True
    elif categoria == "transferencia":
        vai = bonus_forte(item)
        motivo = "" if vai else "bônus abaixo do corte do programa"
    elif categoria == "compra":
        centrais = [p for p in item.get("programas_origem", []) if p in CENTRAIS_DE_PONTOS]
        milheiro = item.get("milheiro_citado")
        if centrais:
            vai = item.get("desconto", 0) >= COMPRA_DESCONTO_MIN or (milheiro is not None and milheiro <= COMPRA_MILHEIRO_MAX)
        else:
            ref = custo_ref(item.get("programas_destino", []))
            vai = milheiro is not None and ref is not None and milheiro <= ref
        motivo = "" if vai else "compra sem desconto/milheiro forte"
    elif categoria == "clube":
        milheiro = item.get("milheiro_citado")
        if any(p in CENTRAIS_DE_PONTOS for p in item.get("programas_origem", [])):
            ref = COMPRA_MILHEIRO_MAX          # Clube Livelo/Esfera: mesma régua da compra de pontos
        else:
            ref = custo_ref(item.get("programas_destino", []))
        ref = ref * CLUBE_TOLERANCIA if ref is not None else None
        vai = milheiro is not None and ref is not None and milheiro <= ref
        if vai:
            motivo = ""
        elif milheiro is None:
            motivo = "clube sem milheiro informado"
        else:
            motivo = f"clube com milheiro de {reais(milheiro)}, acima da referência"
    else:
        motivo = "sem critério de vitrine"

    assinatura = item.get("assinatura")
    if vai and assinatura:
        ultimo = promos["vitrine"].get(assinatura)
        if ultimo and (hoje - date.fromisoformat(ultimo[:10])).days < JANELA_REPETICAO_DIAS:
            return False, "repetição de promoção já enviada"
    return vai, motivo


def chave_acumulo(item):
    programa = ",".join(sorted((item.get("programas_origem") or []) + (item.get("programas_destino") or [])))
    return f"{item.get('parceiro', '')}>{programa}"


def acumulo_compensa(item, promos):
    """Acúmulo só vai à vitrine se o post falar em dobro/triplo ou se a pontuação for >= 2x o normal do parceiro."""
    if item.get("acumulo_forte_texto"):
        return True, ""
    valor = item.get("pontos_por_real") or 0
    if item.get("parceiro") and valor >= ACUMULO_FORTE_MIN:
        return True, ""
    if not item.get("parceiro") or not valor:
        return False, "acúmulo genérico (fica nos bastidores)"
    hist = promos.setdefault("acumulo", {}).get(chave_acumulo(item), [])
    if len(hist) < ACUMULO_MIN_HISTORICO:
        return False, f"acúmulo {item['parceiro']}: ainda aprendendo o normal ({len(hist)} registro(s))"
    normal = sorted(hist)[len(hist) // 2]
    item["pontos_normal"] = normal
    if valor >= normal * ACUMULO_MULTIPLO:
        return True, ""
    return False, f"acúmulo {item['parceiro']}: {valor:g} pts/R$ (normal {normal:g})"


def registrar_acumulo(item, promos):
    if item.get("categoria") != "acumulo" or not item.get("parceiro") or not item.get("pontos_por_real"):
        return
    hist = promos.setdefault("acumulo", {}).setdefault(chave_acumulo(item), [])
    hist.append(item["pontos_por_real"])
    del hist[:-30]


def registrar_promo(item, promos, hoje=None):
    """Guarda transferências e compras ativas (com prazo) para montar combos."""
    hoje = hoje or date.today()
    if item.get("categoria") not in ("transferencia", "compra", "clube") or not item.get("assinatura"):
        return
    if item["categoria"] == "clube" and not item.get("milheiro_citado"):
        return
    promos["ativas"] = [p for p in promos["ativas"] if p["assinatura"] != item["assinatura"]]
    promos["ativas"].append({
        "assinatura": item["assinatura"], "categoria": item["categoria"],
        "origens": item.get("programas_origem", []), "destinos": item.get("programas_destino", []),
        "bonus": item.get("bonus", 0), "desconto": item.get("desconto", 0),
        "milheiro": item.get("milheiro_citado"), "prazo": item.get("prazo", ""),
        "visto_em": hoje.isoformat(), "titulo": item["titulo"], "link": achar_link_oficial(item),
    })


def limpar_promos(promos, hoje=None):
    hoje = hoje or date.today()
    vivas = []
    for p in promos["ativas"]:
        if p["prazo"]:
            if date.fromisoformat(p["prazo"]) >= hoje:
                vivas.append(p)
        elif (hoje - date.fromisoformat(p["visto_em"])).days <= VALIDADE_SEM_PRAZO_DIAS:
            vivas.append(p)
    promos["ativas"] = vivas
    limite = hoje.toordinal() - 60
    promos["vitrine"] = {k: v for k, v in promos["vitrine"].items() if date.fromisoformat(v[:10]).toordinal() >= limite}
    promos["combos"] = {k: v for k, v in promos["combos"].items() if date.fromisoformat(v[:10]).toordinal() >= limite}


def achar_combos(promos):
    """Compra de pontos Livelo/Esfera + transferência dessa central para uma companhia = milheiro final."""
    combos = []
    compras = [p for p in promos["ativas"] if p["categoria"] in ("compra", "clube") and p["milheiro"]
               and any(o in CENTRAIS_DE_PONTOS for o in p["origens"])]
    transfs = [p for p in promos["ativas"] if p["categoria"] == "transferencia" and p["bonus"]]
    for c in compras:
        central = next(o for o in c["origens"] if o in CENTRAIS_DE_PONTOS)
        for t in transfs:
            if central not in t["origens"]:
                continue
            for destino in t["destinos"]:
                ref = CUSTO_MILHEIRO.get(destino)
                if not ref:
                    continue
                final = round(c["milheiro"] / (1 + t["bonus"] / 100), 2)
                chave = f"{c['assinatura']}>>{t['assinatura']}>>{destino}"
                if final <= ref and chave not in promos["combos"]:
                    prazos = [p for p in (c["prazo"], t["prazo"]) if p]
                    combos.append({"chave": chave, "central": central, "destino": destino, "compra": c,
                                   "transf": t, "final": final, "ref": ref,
                                   "prazo": min(prazos) if prazos else ""})
    return combos


def formatar_combo(combo):
    e = html.escape
    c, t = combo["compra"], combo["transf"]
    linhas = [
        "💎 <b>COMBO DO MOMENTO</b>",
        "",
        (f"🎟️ Assine o <b>Clube {e(combo['central'])}</b>" if c["categoria"] == "clube" else f"🛒 Compre <b>{e(combo['central'])}</b>")
        + (f" com {c['desconto']}% OFF" if c["desconto"] else "")
        + f" → milheiro de {reais(c['milheiro'])}" + (f" (até {date.fromisoformat(c['prazo']).strftime('%d/%m')})" if c["prazo"] else ""),
        f"🔄 Transfira para a <b>{e(combo['destino'])}</b> com {t['bonus']}% de bônus"
        + (f" (até {date.fromisoformat(t['prazo']).strftime('%d/%m')})" if t["prazo"] else ""),
        f"✈️ <b>Milheiro final: {reais(combo['final'])}</b> — abaixo da referência de {reais(combo['ref'])} ✅",
    ]
    if combo["prazo"]:
        linhas.append(prazo_texto(combo["prazo"]).replace("Até", "Faça até"))
    linhas.append(f"<i>Conta: {reais(c['milheiro'])} ÷ (1 + {t['bonus']}%) = {reais(combo['final'])}</i>")
    botoes = []
    if c.get("link") and link_oficial_ok(c["link"]):
        botoes.append(f"<a href=\"{e(c['link'])}\">{'Assinar o clube' if c['categoria'] == 'clube' else 'Comprar os pontos'}</a>")
    if t.get("link") and link_oficial_ok(t["link"]):
        botoes.append(f"<a href=\"{e(t['link'])}\">Fazer a transferência</a>")
    if botoes:
        linhas.append("")
        linhas.append("🔗 " + " · ".join(botoes))
    return "\n".join(linhas)


def formatar_vitrine(item):
    e = html.escape
    categoria = item.get("categoria", "")
    origens = " + ".join(item.get("programas_origem") or [])
    destinos = " / ".join(item.get("programas_destino") or [])
    detalhe = []
    if item.get("resgates"):
        programa = item["resgates"][0].get("programa") or ""
        cab = ("🔥 " if programa == "LATAM Pass" else "") + "🎫 <b>OPORTUNIDADE DE RESGATE" + \
              (f" · {e(programa.upper())}" if programa else "") + "</b>"
    elif "promoção relâmpago" in item.get("motivos", []):
        cab = "⚡ <b>PROMOÇÃO RELÂMPAGO</b>"
    elif categoria == "transferencia":
        cab = "🔄 <b>TRANSFERÊNCIA BONIFICADA</b>"
        detalhe.append(f"<b>{e(origens or 'Pontos')} → {e(destinos or 'programa')} · {item['bonus']}% de bônus</b>")
        if item.get("milheiro_citado"):
            detalhe.append(f"💡 Milheiro gerado: a partir de {reais(item['milheiro_citado'])}")
    elif categoria == "compra":
        cab = "🛒 <b>COMPRA DE PONTOS COM DESCONTO</b>"
        nome = origens or destinos or "Pontos"
        detalhe.append(f"<b>{e(nome)}</b>" + (f" · <b>{item['desconto']}% OFF</b>" if item.get("desconto") else ""))
        if item.get("milheiro_citado"):
            detalhe.append(f"💡 Milheiro a partir de {reais(item['milheiro_citado'])}")
    elif categoria == "acumulo":
        cab = "🛍️ <b>COMPRAS QUE RENDEM MUITOS PONTOS</b>"
        prog = " / ".join((item.get("programas_origem") or []) + (item.get("programas_destino") or [])) or "pontos"
        linha = f"<b>{e(item.get('parceiro') or 'Parceiros')} → {e(prog)}</b>"
        if item.get("pontos_por_real"):
            linha += f" · {item['pontos_por_real']:g} pontos por real"
        detalhe.append(linha)
        if item.get("pontos_normal"):
            detalhe.append(f"📈 {item['pontos_por_real'] / item['pontos_normal']:.1f}x o normal (costuma ser {item['pontos_normal']:g} por real)")
    elif categoria == "clube":
        cab = "🎟️ <b>CLUBE DE MILHAS EM CONTA</b>"
        detalhe.append(f"<b>{e(origens or destinos or 'Clube')}</b> · milheiro a partir de {reais(item['milheiro_citado'])}")
    elif categoria == "passagem" and item.get("escopo") == "internacional" and "sai de GYN/BSB" not in item.get("motivos", []):
        cab = "🌎 <b>INTERNACIONAL EM PROMOÇÃO · SAINDO DO BRASIL</b>"
        detalhe.append("🧭 Sai de outro aeroporto? Vale somar o trecho de Goiânia/Brasília até lá "
                       "ou procurar a mesma data saindo de GYN/BSB.")
    elif item.get("nacional") and "sai de GYN/BSB" not in item.get("motivos", []):
        cab = "🇧🇷 <b>PASSAGENS NACIONAIS EM PROMOÇÃO</b> · confira as saídas de Goiânia e Brasília"
    elif item["tipo"] == "Passagem em dinheiro":
        cab = "💵 <b>PASSAGEM EM PROMOÇÃO · GOIÂNIA / BRASÍLIA</b>"
    else:
        cab = "🎯 <b>PASSAGEM COM MILHAS · GOIÂNIA / BRASÍLIA</b>"
    if item.get("prazo"):
        detalhe.append(prazo_texto(item["prazo"]))
    texto = cab + "\n"
    if detalhe:
        texto += "\n" + "\n".join(detalhe) + "\n"
    texto += f"\n{e(titulo_limpo(item['titulo']))}\n"
    if item.get("resgates"):
        texto += ("\n" + "\n\n".join(bloco_resgate(t) for t in item["resgates"][:4]) + "\n\n" + AVISO_RESGATE)
        return texto          # cada trecho ja tem o seu "Emitir"
    link = achar_link_oficial(item)
    if link:
        texto += f"\n👉 <a href=\"{e(link)}\">Acessar a promoção</a>"
    elif item.get("tipo") in ("Passagem em dinheiro", "Passagem com milhas"):
        texto += f"\n👉 <a href=\"{e(LINK_VOOS_GYN)}\">Ver voos saindo de Goiânia</a>"
    return texto


def etiquetas(item):
    """Etiquetas para a futura personalização por assinante."""
    return {
        "categoria": item.get("categoria") or item["tipo"],
        "programas": sorted(set((item.get("programas_origem") or []) + (item.get("programas_destino") or []))),
        "trechos": [f"{t['origem']}-{t['destino']}" for t in item.get("resgates", [])],
    }


def itens_para_estreia(alertas, agora_ts=None):
    """Promoções das últimas 48h que ainda não foram à vitrine, reanalisadas com as regras atuais."""
    agora_ts = agora_ts or datetime.now(timezone.utc).timestamp()
    escolhidos = []
    for a in alertas:
        if a.get("codigo"):
            continue
        try:
            recente = datetime.fromisoformat(a["data"]).timestamp() >= agora_ts - HORAS_ESTREIA * 3600
        except Exception:
            recente = False
        if not recente:
            continue
        base = {k: a.get(k) for k in ("id", "titulo", "link", "fonte", "resumo", "categorias", "data")}
        base["resumo"] = base.get("resumo") or ""
        novo = classificar(base)
        if not novo:
            continue
        if a.get("resgates"):
            novo["resgates"] = a["resgates"]
            if "resgate de GYN/BSB" not in novo["motivos"]:
                novo["motivos"].append("resgate de GYN/BSB")
        novo["_original"] = a
        escolhidos.append(novo)
    escolhidos.sort(key=lambda i: i["data"])
    return escolhidos


def enviar_em_blocos(partes, cabecalho):
    bloco = cabecalho
    for p in partes:
        if len(bloco) + len(p) + 2 > 3800:
            telegram(bloco)
            bloco = ""
        bloco += ("\n\n" if bloco else "") + p
    if bloco:
        telegram(bloco)


def precos_do_medidor():
    dados = carregar(os.path.join(PASTA, "precos.json"), {})
    return dados.get("historico", {}) if isinstance(dados, dict) else {}


def main():
    primeira_vez = not os.path.exists(ARQ_VISTOS)
    leitor_novo = not os.path.exists(ARQ_MILHAS)
    vistos = carregar(ARQ_VISTOS, [])
    alertas = carregar(ARQ_ALERTAS, [])
    registro = carregar(ARQ_MILHAS, {"posts": [], "resgates": []})
    promos = carregar(ARQ_PROMOS, {"ativas": [], "vitrine": {}, "combos": {}, "acumulo": {}})
    conjunto = set(vistos)
    token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    precos_medidor = precos_do_medidor()

    novos, falhas, total_lidos = [], [], 0
    for nome, url in FONTES.items():
        try:
            itens = ler_feed(nome, url)
            total_lidos += len(itens)
        except Exception as erro:
            falhas.append(f"{nome}: {erro}")
            continue
        for item in itens:
            if item["id"] in conjunto:
                continue
            conjunto.add(item["id"])
            vistos.append(item["id"])
            classificado = classificar(item)
            if classificado:
                classificado["visto_em"] = datetime.now(timezone.utc).isoformat()
                novos.append(classificado)

    # Na estreia do leitor de milhas, também relê os posts de milhas dos últimos 2 dias
    reprocessados = []
    if leitor_novo and not primeira_vez:
        limite = datetime.now(timezone.utc).timestamp() - 48 * 3600
        for a in alertas:
            try:
                recente = datetime.fromisoformat(a["data"]).timestamp() >= limite
            except Exception:
                recente = False
            if recente and a.get("tipo") == "Passagem com milhas":
                a.setdefault("motivos", [])
                reprocessados.append(a)

    processar_milhas(novos + reprocessados, token, precos_medidor, registro)

    novos.sort(key=lambda i: (i["prioridade"] != "alta", i["data"]), reverse=False)
    salvar(ARQ_VISTOS, vistos[-MAX_VISTOS:])
    salvar(ARQ_MILHAS, registro)

    print(f"Lidos: {total_lidos} | Novos relevantes: {len(novos)} | Falhas: {falhas}")

    canal_vitrine()  # conecta o canal assim que o robô virar administrador dele
    teste_forcado()  # v3.3.1: alerta de demonstração da praia, uma única vez

    if primeira_vez:
        salvar(ARQ_ALERTAS, (novos + alertas)[:MAX_ALERTAS])
        telegram(
            "✅ <b>Radar Trip ligado!</b>\n\n"
            f"👀 Vigiando {len(FONTES)} fontes a cada 15 minutos.\n"
            f"🗂️ {len(novos)} promoções atuais já registradas.\n\n"
            "A partir de agora, você só recebe o que for <b>novo</b>. ✈️"
        )
        return

    if leitor_novo:
        com_casa = [a for a in reprocessados if a.get("resgates")]
        lidos = [a for a in reprocessados if "trechos_total" in a]
        cab = ("🎯 <b>Leitor de milhas ligado!</b>\n"
               f"Li {len(lidos)} post(s) de milhas dos últimos 2 dias: "
               f"{len(com_casa)} com trechos de/para GYN/BSB.\n"
               "A partir de agora, todo post novo de milhas vem com o milheiro calculado.")
        enviar_em_blocos([formatar(a) for a in com_casa], cab)

    hoje = date.today()
    limpar_promos(promos, hoje)
    vitrine, bastidores = [], []

    # Estreia do canal: na 1ª rodada com o canal conectado, reavalia as últimas 48h com as regras atuais
    estreia = []
    if canal_vitrine() and not os.path.exists(ARQ_ESTREIA):
        estreia = itens_para_estreia(alertas)
        salvar(ARQ_ESTREIA, {"feita_em": datetime.now(timezone.utc).isoformat(), "reavaliados": len(estreia)})
    estreia_enviados = []
    for item in estreia:
        if item.get("prazo") and date.fromisoformat(item["prazo"]) < hoje:
            continue
        vai, _ = decidir_vitrine(item, promos, hoje)
        registrar_promo(item, promos, hoje)
        registrar_acumulo(item, promos)
        if vai:
            codigo = enviar_vitrine(formatar_vitrine(item), {
                "tipo": item["tipo"], "titulo": item["titulo"], "link": item["link"], "fonte": item["fonte"],
                "etiquetas": etiquetas(item), "estreia": True,
            })
            item["_original"]["codigo"] = codigo
            if item.get("assinatura"):
                promos["vitrine"][item["assinatura"]] = datetime.now(timezone.utc).isoformat()
            estreia_enviados.append(codigo)
    if estreia:
        print(f"Estreia da vitrine: {len(estreia)} reavaliados, {len(estreia_enviados)} publicados")

    for item in novos:
        vai, motivo = decidir_vitrine(item, promos, hoje)
        registrar_promo(item, promos, hoje)
        registrar_acumulo(item, promos)
        if vai:
            item["codigo"] = enviar_vitrine(formatar_vitrine(item), {
                "tipo": item["tipo"], "titulo": item["titulo"], "link": item["link"], "fonte": item["fonte"],
                "etiquetas": etiquetas(item),
            })
            if item.get("assinatura"):
                promos["vitrine"][item["assinatura"]] = datetime.now(timezone.utc).isoformat()
            vitrine.append(item)
        else:
            item["fora_vitrine"] = motivo
            bastidores.append(item)
    for combo in achar_combos(promos):
        codigo = enviar_vitrine(formatar_combo(combo), {
            "tipo": "Combo", "titulo": f"{combo['central']} → {combo['destino']}", "milheiro_final": combo["final"],
            "etiquetas": {"categoria": "combo", "programas": [combo["central"], combo["destino"]], "trechos": []},
        })
        promos["combos"][combo["chave"]] = datetime.now(timezone.utc).isoformat()
        vitrine.append({"codigo": codigo})
    salvar(ARQ_PROMOS, promos)
    salvar(ARQ_ALERTAS, (novos + alertas)[:MAX_ALERTAS])

    if estreia:
        telegram("🛠️ <b>Bastidores</b> · 🔁 <b>Vigia v3.4 no ar</b>\n"
                 f"Reavaliei {len(estreia)} promoção(ões) das últimas {HORAS_ESTREIA}h com as regras novas e publiquei "
                 f"{len(estreia_enviados)} na vitrine" + (": " + ", ".join(estreia_enviados) if estreia_enviados else "."))

    if bastidores or vitrine:
        cab = f"🛠️ <b>Bastidores</b> · {len(bastidores)} novidade(s) fora da vitrine"
        if vitrine:
            cab += f"\n📣 {len(vitrine)} alerta(s) enviados à vitrine: " + ", ".join(i["codigo"] for i in vitrine)
        enviar_em_blocos([formatar(i) for i in bastidores], cab)

    if falhas and len(falhas) == len(FONTES):
        telegram("⚠️ Radar Trip: não consegui ler nenhuma fonte nesta rodada.\n" + html.escape("\n".join(falhas)))


if __name__ == "__main__":
    main()

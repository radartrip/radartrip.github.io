# Radar Trip - Vigia de promocoes (v2 - leitor de milhas)
# Le os blogs de promocao, filtra o que interessa e avisa no Telegram.
#
# Novidade v2: nos posts de passagens com milhas, abre o post completo pelo canal
# publico do WordPress (wp-json), le a tabela de resgates, separa os trechos que
# saem ou chegam em GYN/BSB, busca o preco em dinheiro do mesmo trecho e calcula
# o milheiro: "vale usar milhas" ou "melhor pagar em dinheiro".

import html
import json
import os
import re
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


def classificar(item):
    texto = (item["titulo"] + " " + item["resumo"]).lower()
    titulo = item["titulo"].lower()

    if any(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", titulo) for t in TERMOS_IGNORAR):
        return None
    if not any(t in texto for t in TERMOS_PROMO):
        return None

    bonus = maior_bonus(texto)
    if "transfer" in texto and bonus:
        tipo = "Transferência bonificada"
    elif "clube" in texto or "compra de pontos" in texto or "compre pontos" in texto or "compra de milhas" in texto:
        tipo = "Clube / compra de pontos"
    elif "milhas" in texto or "pontos" in texto:
        tipo = "Passagem com milhas"
    else:
        tipo = "Passagem em dinheiro"

    motivos = []
    if any(t in texto for t in TERMOS_GYN):
        motivos.append("sai de GYN/BSB")
    if bonus >= 80:
        motivos.append(f"bônus de {bonus}%")
    if any(t in texto for t in TERMOS_URGENTE):
        motivos.append("promoção relâmpago")

    item["tipo"] = tipo
    item["bonus"] = bonus
    item["prioridade"] = "alta" if motivos else "normal"
    item["motivos"] = motivos
    return item


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
        ultimo = (precos_medidor.get(cidade) or [{}])[-1]
        if ultimo.get("preco") and ultimo.get("origem", "GYN") == origem:
            return int(ultimo["preco"] / 2), "estimativa: metade do ida e volta"
    return None, ""


def avaliar_trecho(trecho, programa, token, precos_medidor):
    preco, origem_preco = preco_dinheiro(trecho["origem"], trecho["destino"], trecho["data"],
                                         token, precos_medidor)
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
    trechos = extrair_trechos(conteudo_do_post(item["link"]))
    casa = [t for t in trechos if t["origem"] in AEROPORTOS_CASA or t["destino"] in AEROPORTOS_CASA]
    casa.sort(key=lambda t: (t["origem"] != "GYN", t["origem"] not in AEROPORTOS_CASA, t["milhas"]))
    programa = programa_do_post(item["titulo"])
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


def telegram(texto):
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
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


EMOJI_TIPO = {
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
    linha = (f"• <b>{e(t['origem'])} → {e(t['destino'])}</b> · {quando} · "
             f"<b>{milhar(t['milhas'])}</b> milhas{clube}")
    if t.get("milheiro"):
        estimativa = " (estimado)" if t["preco_origem"].startswith("estimativa") else ""
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
        linha += f"\n   <a href=\"{e(t['reserva'])}\">Reservar</a>"
    return linha


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
    return texto + f"<a href=\"{e(item['link'])}\">Abrir promoção</a>"


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
    alertas = (novos + alertas)[:MAX_ALERTAS]
    salvar(ARQ_VISTOS, vistos[-MAX_VISTOS:])
    salvar(ARQ_ALERTAS, alertas)
    salvar(ARQ_MILHAS, registro)

    print(f"Lidos: {total_lidos} | Novos relevantes: {len(novos)} | Falhas: {falhas}")

    if primeira_vez:
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

    if novos:
        cab = f"✈️ <b>Radar Trip</b> · {len(novos)} novidade(s)"
        enviar_em_blocos([formatar(i) for i in novos], cab)

    if falhas and len(falhas) == len(FONTES):
        telegram("⚠️ Radar Trip: não consegui ler nenhuma fonte nesta rodada.\n" + html.escape("\n".join(falhas)))


if __name__ == "__main__":
    main()

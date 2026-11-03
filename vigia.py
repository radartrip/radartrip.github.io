# Radar Trip - Vigia de promocoes
# Le os blogs de promocao, filtra o que interessa e avisa no Telegram.

import html
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

FONTES = {
    "Melhores Destinos": "https://www.melhoresdestinos.com.br/feed",
    "Passageiro de Primeira": "https://passageirodeprimeira.com/feed/",
    "Pontos pra Voar": "https://pontospravoar.com/feed/",
}

PASTA = "dados"
ARQ_VISTOS = os.path.join(PASTA, "vistos.json")
ARQ_ALERTAS = os.path.join(PASTA, "alertas.json")
MAX_VISTOS = 3000
MAX_ALERTAS = 400

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


def baixar(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 RadarTrip/1.0"})
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


def formatar(item):
    e = html.escape
    topo = "🔥 <b>PRIORIDADE</b> · " + e(", ".join(item["motivos"])) + "\n" if item["prioridade"] == "alta" else ""
    return (
        f"{topo}{EMOJI_TIPO.get(item['tipo'], '✈️')} <b>{e(item['titulo'])}</b>\n"
        f"<i>{e(item['tipo'])} · {e(item['fonte'])}</i>\n"
        f"<a href=\"{e(item['link'])}\">Abrir promoção</a>"
    )


def enviar_em_blocos(partes, cabecalho):
    bloco = cabecalho
    for p in partes:
        if len(bloco) + len(p) + 2 > 3800:
            telegram(bloco)
            bloco = ""
        bloco += ("\n\n" if bloco else "") + p
    if bloco:
        telegram(bloco)


def main():
    primeira_vez = not os.path.exists(ARQ_VISTOS)
    vistos = carregar(ARQ_VISTOS, [])
    alertas = carregar(ARQ_ALERTAS, [])
    conjunto = set(vistos)

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

    novos.sort(key=lambda i: (i["prioridade"] != "alta", i["data"]), reverse=False)
    alertas = (novos + alertas)[:MAX_ALERTAS]
    salvar(ARQ_VISTOS, vistos[-MAX_VISTOS:])
    salvar(ARQ_ALERTAS, alertas)

    print(f"Lidos: {total_lidos} | Novos relevantes: {len(novos)} | Falhas: {falhas}")

    if primeira_vez:
        telegram(
            "✅ <b>Radar Trip ligado!</b>\n\n"
            f"👀 Vigiando {len(FONTES)} fontes a cada 15 minutos.\n"
            f"🗂️ {len(novos)} promoções atuais já registradas.\n\n"
            "A partir de agora, você só recebe o que for <b>novo</b>. ✈️"
        )
        return

    if novos:
        cab = f"✈️ <b>Radar Trip</b> · {len(novos)} novidade(s)"
        enviar_em_blocos([formatar(i) for i in novos], cab)

    if falhas and len(falhas) == len(FONTES):
        telegram("⚠️ Radar Trip: não consegui ler nenhuma fonte nesta rodada.\n" + html.escape("\n".join(falhas)))


if __name__ == "__main__":
    main()

# Radar Trip - Medidor de precos (Vigia 2.0)
# Consulta os menores precos saindo de Goiania, guarda o historico
# e avisa no Telegram quando um preco cai bem abaixo da media.

import html
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from vigia import telegram

ORIGEM = "GYN"
ORIGEM_NOME = "Goiânia"

# Para incluir ou tirar um destino, edite esta lista: "CÓDIGO": "Nome"
DESTINOS = {
    "REC": "Recife",
    "SSA": "Salvador",
    "FOR": "Fortaleza",
    "MCZ": "Maceió",
    "NAT": "Natal",
    "BPS": "Porto Seguro",
    "SAO": "São Paulo",
    "RIO": "Rio de Janeiro",
    "FLN": "Florianópolis",
    "POA": "Porto Alegre",
    "BUE": "Buenos Aires",
    "SCL": "Santiago",
    "LIS": "Lisboa",
    "MCO": "Orlando",
}

QUEDA_MINIMA = 0.20          # avisa se o preço ficar 20% ou mais abaixo da média
DIAS_MEDIA = 7               # média dos últimos 7 dias
MIN_LEITURAS = 12            # só compara depois de ter histórico suficiente
INTERVALO_MIN = 55           # consulta no máximo 1 vez por hora
MAX_LEITURAS = 1500          # cerca de 2 meses de histórico por destino
MARKER = "800145"            # seu ID de afiliado Travelpayouts

CIAS = {"G3": "Gol", "LA": "LATAM", "JJ": "LATAM", "AD": "Azul", "AR": "Aerolíneas Argentinas",
        "TP": "TAP", "AA": "American", "UA": "United", "DL": "Delta", "CM": "Copa", "AV": "Avianca",
        "H2": "Sky", "JA": "JetSMART", "IB": "Iberia", "UX": "Air Europa", "AF": "Air France", "KL": "KLM"}

PASTA = "dados"
ARQ = os.path.join(PASTA, "precos.json")
API = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"


def agora():
    return datetime.now(timezone.utc)


def consultar(destino, token):
    params = {
        "origin": ORIGEM, "destination": destino, "currency": "brl",
        "sorting": "price", "one_way": "false", "limit": 30, "market": "br",
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "X-Access-Token": token, "Accept-Encoding": "identity",
        "User-Agent": "RadarTrip/2.0",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        resposta = json.loads(r.read().decode("utf-8"))
    if not resposta.get("success", True):
        raise RuntimeError(resposta.get("error") or "resposta sem sucesso")
    opcoes = [o for o in resposta.get("data") or [] if o.get("price")]
    if not opcoes:
        return None
    melhor = min(opcoes, key=lambda o: o["price"])
    return {
        "preco": int(melhor["price"]),
        "ida": (melhor.get("departure_at") or "")[:10],
        "volta": (melhor.get("return_at") or "")[:10],
        "cia": CIAS.get(melhor.get("airline") or "", melhor.get("airline") or ""),
        "escalas": melhor.get("transfers"),
        "link": montar_link(melhor.get("link") or ""),
    }


def montar_link(caminho):
    if not caminho:
        return ""
    base = "https://www.aviasales.com" + caminho
    junta = "&" if "?" in base else "?"
    return f"{base}{junta}marker={MARKER}"


def data_br(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d/%m")
    except ValueError:
        return "?"


def reais(valor):
    return "R$ " + f"{valor:,.0f}".replace(",", ".")


def carregar():
    try:
        with open(ARQ, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def salvar(dados):
    os.makedirs(PASTA, exist_ok=True)
    with open(ARQ, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=1)


def media_recente(leituras, limite):
    valores = [l["preco"] for l in leituras if l["t"] >= limite]
    if len(valores) < MIN_LEITURAS:
        return None
    return sum(valores) / len(valores)


def avaliar(codigo, atual, historico, alertas, momento):
    leituras = historico.get(codigo, [])
    limite = (momento - timedelta(days=DIAS_MEDIA)).isoformat()
    media = media_recente(leituras, limite)
    if not media:
        return None
    queda = 1 - atual["preco"] / media
    if queda < QUEDA_MINIMA:
        return None

    anterior = alertas.get(codigo)
    if anterior:
        recente = anterior["t"] >= (momento - timedelta(hours=48)).isoformat()
        if recente and atual["preco"] >= anterior["preco"] * 0.95:
            return None

    alertas[codigo] = {"preco": atual["preco"], "t": momento.isoformat()}
    return media, queda


def linha_destino(nome, atual):
    datas = f"{data_br(atual['ida'])} a {data_br(atual['volta'])}" if atual["volta"] else data_br(atual["ida"])
    return f"• <b>{html.escape(nome)}</b>: {reais(atual['preco'])} ({datas})"


def main():
    token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    if not token:
        print("Sem TRAVELPAYOUTS_TOKEN; medidor de preços não rodou.")
        return

    dados = carregar()
    primeira_vez = dados is None
    dados = dados or {"ultima_consulta": "", "historico": {}, "alertas": {}}
    momento = agora()

    if dados["ultima_consulta"]:
        ultima = datetime.fromisoformat(dados["ultima_consulta"])
        if momento - ultima < timedelta(minutes=INTERVALO_MIN):
            print("Medidor: consultado há menos de 1 hora, aguardando.")
            return

    historico, alertas = dados["historico"], dados["alertas"]
    resultados, falhas, avisos = {}, [], []

    for codigo, nome in DESTINOS.items():
        try:
            atual = consultar(codigo, token)
        except Exception as erro:
            falhas.append(f"{codigo}: {erro}")
            continue
        if not atual:
            continue
        resultados[codigo] = atual
        queda = avaliar(codigo, atual, historico, alertas, momento)
        if queda:
            avisos.append((codigo, nome, atual, *queda))
        leitura = {"t": momento.isoformat(), **atual}
        historico.setdefault(codigo, []).append(leitura)
        historico[codigo] = historico[codigo][-MAX_LEITURAS:]

    dados["ultima_consulta"] = momento.isoformat()
    salvar(dados)
    print(f"Medidor: {len(resultados)} destinos com preço | {len(avisos)} alertas | falhas: {falhas}")

    if primeira_vez:
        if resultados:
            linhas = [linha_destino(DESTINOS[c], r) for c, r in sorted(resultados.items(), key=lambda x: x[1]["preco"])]
            telegram(
                "📉 <b>Medidor de preços ligado!</b>\n"
                f"Menores preços de ida e volta saindo de {ORIGEM_NOME} agora:\n\n"
                + "\n".join(linhas)
                + "\n\n🧠 Vou checar de hora em hora e avisar quando algum cair "
                f"<b>{int(QUEDA_MINIMA * 100)}% ou mais</b> abaixo da média. "
                "Os alertas começam depois de ~12 horas de histórico."
            )
        else:
            telegram(
                "⚠️ <b>Medidor de preços:</b> não consegui ler nenhum preço na primeira tentativa.\n"
                + html.escape("\n".join(falhas) or "A fonte não trouxe resultados para os destinos.")
            )
        return

    for codigo, nome, atual, media, queda in avisos:
        datas = f"{data_br(atual['ida'])} a {data_br(atual['volta'])}" if atual["volta"] else data_br(atual["ida"])
        escalas = "direto" if atual["escalas"] == 0 else f"{atual['escalas']} escala(s)" if atual["escalas"] else ""
        detalhes = " · ".join(x for x in [atual["cia"], escalas] if x)
        link = f"\n<a href=\"{html.escape(atual['link'])}\">Ver voo</a>" if atual["link"] else ""
        telegram(
            f"📉 <b>PREÇO EM QUEDA</b> · {ORIGEM} → {html.escape(nome)}\n\n"
            f"💰 <b>{reais(atual['preco'])}</b> ida e volta\n"
            f"📊 {int(queda * 100)}% abaixo da média de {DIAS_MEDIA} dias ({reais(media)})\n"
            f"📅 {datas}" + (f" · {html.escape(detalhes)}" if detalhes else "")
            + link
            + "\n\n⚠️ Preço de busca recente: confirme no site antes de comprar."
        )

    if falhas and not resultados:
        telegram("⚠️ Medidor de preços: nenhuma consulta funcionou nesta rodada.\n" + html.escape("\n".join(falhas[:5])))


if __name__ == "__main__":
    main()

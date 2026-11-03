# Radar Trip - Medidor de precos (Vigia 2.3)
# Consulta os menores precos de ida e volta saindo de Goiania, guarda o historico
# e avisa no Telegram quando um preco cai bem abaixo da media.
#
# O robo tenta "gavetas" em ordem e usa a primeira que tiver ida e volta:
#   Saindo de GYN:
#     1. datas  -> prices_for_dates sem datas (buscas das ultimas 48h)
#     2. mes    -> prices_for_dates mes a mes, ida e volta explicita, proximos 6 meses
#     3. semana -> get_latest_prices (buscas guardadas por ate 7 dias)
#   Plano D, so se GYN vier vazio (saindo de Brasilia, ~2h30 de carro):
#     4. bsb     -> prices_for_dates sem datas saindo de BSB
#     5. bsb_mes -> mes a mes saindo de BSB
# O registro (log) do GitHub mostra quantas opcoes cada gaveta trouxe (diagnostico).

import html
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from vigia import telegram

ORIGEM = "GYN"
ORIGEM_NOME = "Goiânia"
ORIGEM_ALT = "BSB"           # plano D: usada só quando GYN não tem preço
ORIGEM_ALT_NOME = "Brasília"

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
VERSAO = 4                   # versão do formato dos dados (4 = plano D, Brasília)
MESES_PLANO_C = 6            # quantos meses à frente a busca mês a mês procura

CIAS = {"G3": "Gol", "LA": "LATAM", "JJ": "LATAM", "AD": "Azul", "AR": "Aerolíneas Argentinas",
        "TP": "TAP", "AA": "American", "UA": "United", "DL": "Delta", "CM": "Copa", "AV": "Avianca",
        "H2": "Sky", "JA": "JetSMART", "IB": "Iberia", "UX": "Air Europa", "AF": "Air France", "KL": "KLM"}

# De onde veio o preço: rótulo curto para o Telegram
FONTES = {
    "datas": "",
    "mes": "busca mês a mês (plano C)",
    "semana": "busca de até 7 dias atrás (plano C)",
    "bsb": f"saindo de {ORIGEM_ALT_NOME} (plano D)",
    "bsb_mes": f"saindo de {ORIGEM_ALT_NOME}, busca mês a mês (plano D)",
}
ICONES = {"mes": " 🗓️", "semana": " 🕘", "bsb": " 🚗", "bsb_mes": " 🚗"}

PASTA = "dados"
ARQ = os.path.join(PASTA, "precos.json")
BASE = "https://api.travelpayouts.com"


def agora():
    return datetime.now(timezone.utc)


def chamar(caminho, params, token):
    url = BASE + caminho + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "X-Access-Token": token, "Accept-Encoding": "identity",
        "User-Agent": "RadarTrip/2.3",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        resposta = json.loads(r.read().decode("utf-8"))
    if resposta.get("success") is False:
        raise RuntimeError(resposta.get("error") or "resposta sem sucesso")
    return resposta.get("data") or []


def so_ida_e_volta(opcoes):
    # Fica só com o que tem preço e data de volta (o robô não usa "só ida")
    return [o for o in opcoes if o.get("price") and (o.get("return_at") or "")]


def lista(dados):
    if isinstance(dados, dict):
        return list(dados.values())
    return list(dados or [])


def busca_datas(origem, destino, token):
    return lista(chamar("/aviasales/v3/prices_for_dates", {
        "origin": origem, "destination": destino, "currency": "brl",
        "sorting": "price", "one_way": "false", "limit": 30, "market": "br",
    }, token))


def meses_a_frente(qtd, base=None):
    base = base or agora()
    ano, mes = base.year, base.month
    meses = []
    for _ in range(qtd + 1):
        meses.append(f"{ano:04d}-{mes:02d}")
        mes += 1
        if mes > 12:
            ano, mes = ano + 1, 1
    return meses


def busca_mes(origem, destino, token):
    # Ida e volta EXPLÍCITA, mês a mês: volta no mesmo mês ou no mês seguinte
    meses = meses_a_frente(MESES_PLANO_C)
    opcoes, erros = [], []
    for i in range(MESES_PLANO_C):
        for volta in (meses[i], meses[i + 1]):
            try:
                opcoes += lista(chamar("/aviasales/v3/prices_for_dates", {
                    "origin": origem, "destination": destino, "currency": "brl",
                    "departure_at": meses[i], "return_at": volta,
                    "sorting": "price", "one_way": "false", "limit": 30, "market": "br",
                }, token))
            except Exception as erro:
                erros.append(str(erro))
    if erros and not opcoes and len(erros) == MESES_PLANO_C * 2:
        raise RuntimeError(erros[0])
    return opcoes


def busca_semana(origem, destino, token):
    dados = chamar("/aviasales/v3/get_latest_prices", {
        "origin": origem, "destination": destino, "currency": "brl",
        "period_type": "year", "one_way": "false", "sorting": "price",
        "market": "br", "limit": 30, "page": 1,
    }, token)
    return [{
        "price": o.get("value"),
        "departure_at": o.get("depart_date") or "",
        "return_at": o.get("return_date") or "",
        "airline": "",
        "transfers": o.get("number_of_changes"),
        "link": "",
    } for o in lista(dados)]


# (nome da gaveta, origem, função de busca) — tentadas nesta ordem
BUSCAS = [
    ("datas", ORIGEM, busca_datas),
    ("mes", ORIGEM, busca_mes),
    ("semana", ORIGEM, busca_semana),
    ("bsb", ORIGEM_ALT, busca_datas),
    ("bsb_mes", ORIGEM_ALT, busca_mes),
]


def consultar(destino, token, diag=None):
    """Tenta as gavetas em ordem e devolve o menor preço da primeira que tiver ida e volta.
    Em diag anota o que cada gaveta trouxe: total bruto / quantos de ida e volta (ou erro)."""
    diag = diag if diag is not None else {}
    erros = []
    for fonte, origem, busca in BUSCAS:
        try:
            brutas = busca(origem, destino, token)
        except Exception as erro:
            erros.append(f"{fonte}: {erro}")
            diag[fonte] = "erro"
            continue
        opcoes = so_ida_e_volta(brutas)
        diag[fonte] = f"{len(brutas)}/{len(opcoes)}"
        if not opcoes:
            continue
        melhor = min(opcoes, key=lambda o: o["price"])
        ida = (melhor.get("departure_at") or "")[:10]
        volta = (melhor.get("return_at") or "")[:10]
        return {
            "preco": int(melhor["price"]),
            "ida": ida,
            "volta": volta,
            "cia": CIAS.get(melhor.get("airline") or "", melhor.get("airline") or ""),
            "escalas": melhor.get("transfers"),
            "link": montar_link(melhor.get("link") or "", origem, destino, ida, volta),
            "fonte": fonte,
            "origem": origem,
        }
    if erros and len(erros) == len(BUSCAS):
        raise RuntimeError("; ".join(erros))
    return None


def ddmm(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d%m")
    except ValueError:
        return ""


def montar_link(caminho, origem, destino, ida, volta):
    if caminho:
        if not caminho.startswith("/"):
            caminho = "/" + caminho
        if not caminho.startswith("/search"):
            caminho = "/search" + caminho
        base = "https://www.aviasales.com" + caminho
    elif ddmm(ida) and ddmm(volta):
        # Sem link pronto: monta a busca do Aviasales com as datas (1 adulto)
        base = f"https://www.aviasales.com/search/{origem}{ddmm(ida)}{destino}{ddmm(volta)}1"
    else:
        return ""
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


def media_recente(leituras, limite, fonte):
    # Só compara preço com preço da mesma gaveta (laranja com laranja)
    valores = [l["preco"] for l in leituras
               if l["t"] >= limite and l.get("fonte", "datas") == fonte]
    if len(valores) < MIN_LEITURAS:
        return None
    return sum(valores) / len(valores)


def avaliar(codigo, atual, historico, alertas, momento):
    leituras = historico.get(codigo, [])
    limite = (momento - timedelta(days=DIAS_MEDIA)).isoformat()
    media = media_recente(leituras, limite, atual["fonte"])
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


def texto_datas(atual):
    if atual["volta"]:
        return f"{data_br(atual['ida'])} a {data_br(atual['volta'])}"
    return data_br(atual["ida"])


def linha_destino(nome, atual):
    extra = ICONES.get(atual.get("fonte"), "")
    return f"• <b>{html.escape(nome)}</b>: {reais(atual['preco'])} ({texto_datas(atual)}){extra}"


def legenda(resultados):
    usadas = {r["fonte"] for r in resultados.values()}
    linhas = []
    if "mes" in usadas:
        linhas.append("🗓️ = busca mês a mês (plano C)")
    if "semana" in usadas:
        linhas.append("🕘 = busca de até 7 dias atrás (plano C)")
    if usadas & {"bsb", "bsb_mes"}:
        linhas.append(f"🚗 = sem dados saindo de GYN; preço saindo de {ORIGEM_ALT_NOME} (~2h30 de carro)")
    return "\n".join(linhas)


def resumo(resultados, sem_preco):
    linhas = [linha_destino(DESTINOS[c], r)
              for c, r in sorted(resultados.items(), key=lambda x: x[1]["preco"])]
    texto = "\n".join(linhas)
    leg = legenda(resultados)
    if leg:
        texto += "\n\n" + leg
    if sem_preco:
        texto += "\n\n😶 Ainda sem preço: " + html.escape(", ".join(sem_preco))
    return texto


def main():
    token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    if not token:
        print("Sem TRAVELPAYOUTS_TOKEN; medidor de preços não rodou.")
        return

    dados = carregar()
    primeira_vez = dados is None
    dados = dados or {"ultima_consulta": "", "historico": {}, "alertas": {}}
    atualizacao = not primeira_vez and dados.get("versao", 1) < VERSAO
    momento = agora()

    if dados["ultima_consulta"] and not atualizacao:
        ultima = datetime.fromisoformat(dados["ultima_consulta"])
        if momento - ultima < timedelta(minutes=INTERVALO_MIN):
            print("Medidor: consultado há menos de 1 hora, aguardando.")
            return

    historico, alertas = dados["historico"], dados["alertas"]
    resultados, falhas, avisos, sem_preco, diagnostico = {}, [], [], [], {}

    for codigo, nome in DESTINOS.items():
        diag = {}
        try:
            atual = consultar(codigo, token, diag)
        except Exception as erro:
            falhas.append(f"{codigo}: {erro}")
            sem_preco.append(nome)
            continue
        finally:
            if "mes" in diag:
                diagnostico[codigo] = diag
        if not atual:
            sem_preco.append(nome)
            continue
        resultados[codigo] = atual
        queda = avaliar(codigo, atual, historico, alertas, momento)
        if queda:
            avisos.append((codigo, nome, atual, *queda))
        leitura = {"t": momento.isoformat(), **atual}
        historico.setdefault(codigo, []).append(leitura)
        historico[codigo] = historico[codigo][-MAX_LEITURAS:]

    dados["ultima_consulta"] = momento.isoformat()
    dados["versao"] = VERSAO
    salvar(dados)
    fontes_usadas = {f: sum(1 for r in resultados.values() if r["fonte"] == f) for f, _, _ in BUSCAS}
    print(f"Medidor: {len(resultados)} destinos com preço {fontes_usadas} | "
          f"{len(avisos)} alertas | sem preço: {sem_preco} | falhas: {falhas}")
    for codigo, diag in diagnostico.items():
        # Diagnóstico: "bruto/ida-e-volta" que cada gaveta trouxe para destinos que precisaram de plano C/D
        print(f"Diagnóstico {codigo}: " + " | ".join(f"{f} {v}" for f, v in diag.items()))

    if primeira_vez or atualizacao:
        if resultados:
            titulo = ("🔧 <b>Medidor atualizado: plano D (Brasília) ligado!</b>" if atualizacao
                      else "📉 <b>Medidor de preços ligado!</b>")
            telegram(
                f"{titulo}\n"
                f"Menores preços de ida e volta saindo de {ORIGEM_NOME} agora "
                f"({len(resultados)} de {len(DESTINOS)} destinos):\n\n"
                + resumo(resultados, sem_preco)
                + "\n\n🧠 Vou checar de hora em hora e avisar quando algum cair "
                f"<b>{int(QUEDA_MINIMA * 100)}% ou mais</b> abaixo da média."
                + ("" if atualizacao else " Os alertas começam depois de ~12 horas de histórico.")
            )
        else:
            telegram(
                "⚠️ <b>Medidor de preços:</b> não consegui ler nenhum preço nesta tentativa.\n"
                + html.escape("\n".join(falhas[:5]) or "A fonte não trouxe resultados para os destinos.")
            )
        return

    for codigo, nome, atual, media, queda in avisos:
        origem = atual.get("origem", ORIGEM)
        escalas = "direto" if atual["escalas"] == 0 else f"{atual['escalas']} escala(s)" if atual["escalas"] else ""
        detalhes = " · ".join(x for x in [atual["cia"], escalas] if x)
        link = f"\n<a href=\"{html.escape(atual['link'])}\">Ver voo</a>" if atual["link"] else ""
        origem_preco = FONTES.get(atual["fonte"], "")
        nota_fonte = f"\n🔎 Origem do preço: {origem_preco}" if origem_preco and origem == ORIGEM else ""
        nota_carro = (f"\n🚗 Sem dados saindo de GYN: preço saindo de {ORIGEM_ALT_NOME} (~2h30 de carro)"
                      if origem != ORIGEM else "")
        telegram(
            f"📉 <b>PREÇO EM QUEDA</b> · {origem} → {html.escape(nome)}\n\n"
            f"💰 <b>{reais(atual['preco'])}</b> ida e volta\n"
            f"📊 {int(queda * 100)}% abaixo da média de {DIAS_MEDIA} dias ({reais(media)})\n"
            f"📅 {texto_datas(atual)}" + (f" · {html.escape(detalhes)}" if detalhes else "")
            + nota_fonte
            + nota_carro
            + link
            + "\n\n⚠️ Preço de busca recente: confirme no site antes de comprar."
        )

    if falhas and not resultados:
        telegram("⚠️ Medidor de preços: nenhuma consulta funcionou nesta rodada.\n" + html.escape("\n".join(falhas[:5])))


if __name__ == "__main__":
    main()

# Radar Trip - Medidor de precos (Vigia 2.2)
# Consulta os menores precos de ida e volta saindo de Goiania, guarda o historico
# e avisa no Telegram quando um preco cai bem abaixo da media.
#
# Plano C (v2.2): se a busca principal vier vazia, o robo tenta outras duas
# "gavetas" da mesma API, sempre ida e volta:
#   1. prices_for_dates sem datas      -> buscas das ultimas 48h (principal)
#   2. prices_for_dates mes a mes      -> ida e volta explicita nos proximos 6 meses
#   3. get_latest_prices               -> buscas guardadas por ate 7 dias (rede de seguranca)
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
VERSAO = 3                   # versão do formato dos dados (3 = plano C mês a mês)
MESES_PLANO_C = 6            # quantos meses à frente o plano C procura

CIAS = {"G3": "Gol", "LA": "LATAM", "JJ": "LATAM", "AD": "Azul", "AR": "Aerolíneas Argentinas",
        "TP": "TAP", "AA": "American", "UA": "United", "DL": "Delta", "CM": "Copa", "AV": "Avianca",
        "H2": "Sky", "JA": "JetSMART", "IB": "Iberia", "UX": "Air Europa", "AF": "Air France", "KL": "KLM"}

# De onde veio o preço: rótulo curto para o Telegram
FONTES = {
    "datas": "",
    "mes": "busca mês a mês (plano C)",
    "semana": "busca de até 7 dias atrás",
}

PASTA = "dados"
ARQ = os.path.join(PASTA, "precos.json")
BASE = "https://api.travelpayouts.com"


def agora():
    return datetime.now(timezone.utc)


def chamar(caminho, params, token):
    url = BASE + caminho + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "X-Access-Token": token, "Accept-Encoding": "identity",
        "User-Agent": "RadarTrip/2.2",
    })
    with urllib.request.urlopen(req, timeout=25) as r:
        resposta = json.loads(r.read().decode("utf-8"))
    if resposta.get("success") is False:
        raise RuntimeError(resposta.get("error") or "resposta sem sucesso")
    return resposta.get("data") or []


def so_ida_e_volta(opcoes):
    # Fica só com o que tem preço e data de volta (o plano C não usa "só ida")
    return [o for o in opcoes if o.get("price") and (o.get("return_at") or "")]


def lista(dados):
    if isinstance(dados, dict):
        return list(dados.values())
    return list(dados or [])


def busca_datas(destino, token):
    return lista(chamar("/aviasales/v3/prices_for_dates", {
        "origin": ORIGEM, "destination": destino, "currency": "brl",
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


def busca_mes(destino, token):
    # Ida e volta EXPLÍCITA, mês a mês: volta no mesmo mês ou no mês seguinte
    meses = meses_a_frente(MESES_PLANO_C)
    opcoes, erros = [], []
    for i in range(MESES_PLANO_C):
        for volta in (meses[i], meses[i + 1]):
            try:
                opcoes += lista(chamar("/aviasales/v3/prices_for_dates", {
                    "origin": ORIGEM, "destination": destino, "currency": "brl",
                    "departure_at": meses[i], "return_at": volta,
                    "sorting": "price", "one_way": "false", "limit": 30, "market": "br",
                }, token))
            except Exception as erro:
                erros.append(str(erro))
    if erros and not opcoes and len(erros) == MESES_PLANO_C * 2:
        raise RuntimeError(erros[0])
    return opcoes


def busca_semana(destino, token):
    dados = chamar("/aviasales/v3/get_latest_prices", {
        "origin": ORIGEM, "destination": destino, "currency": "brl",
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


BUSCAS = [("datas", busca_datas), ("mes", busca_mes), ("semana", busca_semana)]


def consultar(destino, token, diag=None):
    """Tenta as 3 gavetas em ordem e devolve o menor preço da primeira que tiver ida e volta.
    Em diag anota o que cada gaveta trouxe: total bruto / quantos de ida e volta (ou erro)."""
    diag = diag if diag is not None else {}
    erros = []
    for fonte, busca in BUSCAS:
        try:
            brutas = busca(destino, token)
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
            "link": montar_link(melhor.get("link") or "", destino, ida, volta),
            "fonte": fonte,
        }
    if erros and len(erros) == len(BUSCAS):
        raise RuntimeError("; ".join(erros))
    return None


def ddmm(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d%m")
    except ValueError:
        return ""


def montar_link(caminho, destino, ida, volta):
    if caminho:
        if not caminho.startswith("/"):
            caminho = "/" + caminho
        if not caminho.startswith("/search"):
            caminho = "/search" + caminho
        base = "https://www.aviasales.com" + caminho
    elif ddmm(ida) and ddmm(volta):
        # Sem link pronto: monta a busca do Aviasales com as datas (1 adulto)
        base = f"https://www.aviasales.com/search/{ORIGEM}{ddmm(ida)}{destino}{ddmm(volta)}1"
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
    extra = " 🗓️" if atual.get("fonte") == "mes" else " 🕘" if atual.get("fonte") == "semana" else ""
    return f"• <b>{html.escape(nome)}</b>: {reais(atual['preco'])} ({texto_datas(atual)}){extra}"


LEGENDA = ("🗓️ = busca mês a mês (plano C)\n"
           "🕘 = busca de até 7 dias atrás (plano C)")


def resumo(resultados, sem_preco):
    linhas = [linha_destino(DESTINOS[c], r)
              for c, r in sorted(resultados.items(), key=lambda x: x[1]["preco"])]
    texto = "\n".join(linhas)
    if any(r["fonte"] != "datas" for r in resultados.values()):
        texto += "\n\n" + LEGENDA
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
    fontes_usadas = {f: sum(1 for r in resultados.values() if r["fonte"] == f) for f, _ in BUSCAS}
    print(f"Medidor: {len(resultados)} destinos com preço {fontes_usadas} | "
          f"{len(avisos)} alertas | sem preço: {sem_preco} | falhas: {falhas}")
    for codigo, diag in diagnostico.items():
        # Diagnóstico: "bruto/ida-e-volta" que cada gaveta trouxe para destinos que precisaram do plano C
        print(f"Diagnóstico {codigo}: " + " | ".join(f"{f} {v}" for f, v in diag.items()))

    if primeira_vez or atualizacao:
        if resultados:
            titulo = ("🔧 <b>Medidor atualizado: plano C ligado!</b>" if atualizacao
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
        escalas = "direto" if atual["escalas"] == 0 else f"{atual['escalas']} escala(s)" if atual["escalas"] else ""
        detalhes = " · ".join(x for x in [atual["cia"], escalas] if x)
        link = f"\n<a href=\"{html.escape(atual['link'])}\">Ver voo</a>" if atual["link"] else ""
        origem_preco = FONTES.get(atual["fonte"], "")
        nota_fonte = f"\n🔎 Origem do preço: {origem_preco}" if origem_preco else ""
        telegram(
            f"📉 <b>PREÇO EM QUEDA</b> · {ORIGEM} → {html.escape(nome)}\n\n"
            f"💰 <b>{reais(atual['preco'])}</b> ida e volta\n"
            f"📊 {int(queda * 100)}% abaixo da média de {DIAS_MEDIA} dias ({reais(media)})\n"
            f"📅 {texto_datas(atual)}" + (f" · {html.escape(detalhes)}" if detalhes else "")
            + nota_fonte
            + link
            + "\n\n⚠️ Preço de busca recente: confirme no site antes de comprar."
        )

    if falhas and not resultados:
        telegram("⚠️ Medidor de preços: nenhuma consulta funcionou nesta rodada.\n" + html.escape("\n".join(falhas[:5])))


if __name__ == "__main__":
    main()

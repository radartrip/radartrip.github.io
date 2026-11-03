# Radar Trip - Medidor de precos (Vigia 3.0)
# Mede os menores precos de ida e volta saindo de GOIANIA e de BRASILIA para os
# mesmos destinos, guarda o historico de cada rota e avisa quando um preco cai bem
# abaixo da media.
#
# Para cada rota (origem -> destino) o robo tenta "gavetas" em ordem e usa a
# primeira que tiver ida e volta:
#   1. datas  -> prices_for_dates sem datas (buscas das ultimas 48h)
#   2. mes    -> prices_for_dates mes a mes, ida e volta explicita, proximos 6 meses
#   3. semana -> get_latest_prices (buscas guardadas por ate 7 dias)
#
# Alertas de queda vao para a VITRINE (canal) com codigo RT-xxxx e comparacao com a
# outra origem. Avisos tecnicos (atualizacao, falhas, diagnostico) vao para os
# BASTIDORES (chat do dono).

import html
import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from vigia import enviar_vitrine, telegram

# Origens medidas, na ordem de prioridade (GYN primeiro)
ORIGENS = {
    "GYN": "Goiânia",
    "BSB": "Brasília",
}
NOTA_ORIGEM = {"BSB": "🚗 a ~2h30 de carro de Goiânia"}

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

QUEDA_MINIMA = 0.15          # avisa se o preço ficar 15% ou mais abaixo da média
DIAS_MEDIA = 7               # média dos últimos 7 dias
MIN_LEITURAS = 12            # só compara depois de ter histórico suficiente
INTERVALO_MIN = 55           # consulta no máximo 1 vez por hora
MAX_LEITURAS = 1500          # cerca de 2 meses de histórico por rota
MARKER = "800145"            # seu ID de afiliado Travelpayouts
VERSAO = 5                   # versão do formato dos dados (5 = GYN e BSB como origens completas)
MESES_PLANO_C = 6            # quantos meses à frente a busca mês a mês procura

CIAS = {"G3": "Gol", "LA": "LATAM", "JJ": "LATAM", "AD": "Azul", "AR": "Aerolíneas Argentinas",
        "TP": "TAP", "AA": "American", "UA": "United", "DL": "Delta", "CM": "Copa", "AV": "Avianca",
        "H2": "Sky", "JA": "JetSMART", "IB": "Iberia", "UX": "Air Europa", "AF": "Air France", "KL": "KLM"}

PASTA = "dados"
ARQ = os.path.join(PASTA, "precos.json")
BASE = "https://api.travelpayouts.com"


def agora():
    return datetime.now(timezone.utc)


def rota(origem, destino):
    return f"{origem}-{destino}"


def chamar(caminho, params, token):
    url = BASE + caminho + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "X-Access-Token": token, "Accept-Encoding": "identity",
        "User-Agent": "RadarTrip/3.0",
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


# (nome da gaveta, função de busca) — tentadas nesta ordem para cada rota
BUSCAS = [("datas", busca_datas), ("mes", busca_mes), ("semana", busca_semana)]


def consultar(origem, destino, token, diag=None):
    """Tenta as gavetas em ordem e devolve o menor preço da primeira que tiver ida e volta.
    Em diag anota o que cada gaveta trouxe: total bruto / quantos de ida e volta (ou erro)."""
    diag = diag if diag is not None else {}
    erros = []
    for fonte, busca in BUSCAS:
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


def migrar(dados):
    """Versões antigas guardavam o histórico por destino ("REC"); agora é por rota ("GYN-REC").
    Leituras que vieram de Brasília (plano D) vão para a rota BSB, com a gaveta equivalente."""
    gaveta = {"bsb": "datas", "bsb_mes": "mes"}
    novo_hist = {}
    for chave, leituras in dados.get("historico", {}).items():
        if "-" in chave:
            novo_hist.setdefault(chave, []).extend(leituras)
            continue
        for l in leituras:
            origem = l.get("origem", "GYN")
            l = {**l, "origem": origem, "fonte": gaveta.get(l.get("fonte"), l.get("fonte", "datas"))}
            novo_hist.setdefault(rota(origem, chave), []).append(l)
    novos_alertas = {}
    for chave, alerta in dados.get("alertas", {}).items():
        novos_alertas[chave if "-" in chave else rota("GYN", chave)] = alerta
    dados["historico"], dados["alertas"] = novo_hist, novos_alertas
    return dados


def media_recente(leituras, limite, fonte):
    # Só compara preço com preço da mesma gaveta (laranja com laranja)
    valores = [l["preco"] for l in leituras
               if l["t"] >= limite and l.get("fonte", "datas") == fonte]
    if len(valores) < MIN_LEITURAS:
        return None
    return sum(valores) / len(valores)


def avaliar(chave, atual, historico, alertas, momento):
    leituras = historico.get(chave, [])
    limite = (momento - timedelta(days=DIAS_MEDIA)).isoformat()
    media = media_recente(leituras, limite, atual["fonte"])
    if not media:
        return None
    queda = 1 - atual["preco"] / media
    if queda < QUEDA_MINIMA:
        return None

    anterior = alertas.get(chave)
    if anterior:
        recente = anterior["t"] >= (momento - timedelta(hours=48)).isoformat()
        if recente and atual["preco"] >= anterior["preco"] * 0.95:
            return None

    alertas[chave] = {"preco": atual["preco"], "t": momento.isoformat()}
    return media, queda


def texto_datas(atual):
    if atual["volta"]:
        return f"{data_br(atual['ida'])} a {data_br(atual['volta'])}"
    return data_br(atual["ida"])


def quadro(resultados, sem_preco):
    """Resumo para os BASTIDORES: cada destino com o preço de cada origem."""
    linhas = []
    melhores = []
    for destino, nome in DESTINOS.items():
        precos = {o: resultados.get(rota(o, destino)) for o in ORIGENS}
        validos = [p["preco"] for p in precos.values() if p]
        melhores.append((min(validos) if validos else 10 ** 9, destino, nome, precos))
    for _, destino, nome, precos in sorted(melhores):
        partes = [f"{o} {reais(p['preco'])}" if p else f"{o} —" for o, p in precos.items()]
        linhas.append(f"• <b>{html.escape(nome)}</b>: " + " · ".join(partes))
    texto = "\n".join(linhas)
    if sem_preco:
        texto += "\n\n😶 Sem preço nesta rodada: " + html.escape(", ".join(sem_preco))
    return texto


def alerta_vitrine(origem, destino, atual, media, queda, resultados):
    nome = DESTINOS[destino]
    escalas = "direto" if atual["escalas"] == 0 else f"{atual['escalas']} escala(s)" if atual["escalas"] else ""
    detalhes = " · ".join(x for x in [atual["cia"], escalas] if x)
    texto = (
        "📉 <b>PREÇO EM QUEDA</b>\n\n"
        f"✈️ <b>{ORIGENS[origem]} → {html.escape(nome)}</b>"
        + (f"\n{NOTA_ORIGEM[origem]}" if origem in NOTA_ORIGEM else "")
        + f"\n💰 <b>{reais(atual['preco'])}</b> ida e volta"
        f"\n📊 {int(queda * 100)}% abaixo da média dos últimos {DIAS_MEDIA} dias ({reais(media)})"
        f"\n📅 {texto_datas(atual)}" + (f" · {html.escape(detalhes)}" if detalhes else "")
    )
    # Compara com a outra origem: ajuda o goiano a decidir se vale ir até Brasília (ou não)
    for outra in ORIGENS:
        if outra == origem:
            continue
        outro = resultados.get(rota(outra, destino))
        if outro:
            diferenca = outro["preco"] - atual["preco"]
            comparacao = (f"R$ {abs(diferenca):,.0f} mais caro".replace(",", ".") if diferenca > 0
                          else f"R$ {abs(diferenca):,.0f} mais barato".replace(",", ".") if diferenca < 0
                          else "mesmo preço")
            texto += f"\n🔁 Saindo de {ORIGENS[outra]} hoje: {reais(outro['preco'])} ({comparacao})"
    if atual["link"]:
        texto += f"\n\n🔗 <a href=\"{html.escape(atual['link'])}\">Ver voo</a>"
    return texto


def main():
    token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    if not token:
        print("Sem TRAVELPAYOUTS_TOKEN; medidor de preços não rodou.")
        return

    dados = carregar()
    primeira_vez = dados is None
    dados = dados or {"ultima_consulta": "", "historico": {}, "alertas": {}, "versao": VERSAO}
    atualizacao = not primeira_vez and dados.get("versao", 1) < VERSAO
    if atualizacao:
        dados = migrar(dados)
    momento = agora()

    if dados["ultima_consulta"] and not atualizacao:
        ultima = datetime.fromisoformat(dados["ultima_consulta"])
        if momento - ultima < timedelta(minutes=INTERVALO_MIN):
            print("Medidor: consultado há menos de 1 hora, aguardando.")
            return

    historico, alertas = dados["historico"], dados["alertas"]
    resultados, falhas, avisos, sem_preco, diagnostico = {}, [], [], [], {}

    for origem in ORIGENS:
        for destino, nome in DESTINOS.items():
            chave = rota(origem, destino)
            diag = {}
            try:
                atual = consultar(origem, destino, token, diag)
            except Exception as erro:
                falhas.append(f"{chave}: {erro}")
                sem_preco.append(f"{origem}→{nome}")
                continue
            finally:
                if "mes" in diag:
                    diagnostico[chave] = diag
            if not atual:
                sem_preco.append(f"{origem}→{nome}")
                continue
            resultados[chave] = atual
            queda = avaliar(chave, atual, historico, alertas, momento)
            if queda:
                avisos.append((origem, destino, atual, *queda))
            leitura = {"t": momento.isoformat(), **atual}
            historico.setdefault(chave, []).append(leitura)
            historico[chave] = historico[chave][-MAX_LEITURAS:]

    dados["ultima_consulta"] = momento.isoformat()
    dados["versao"] = VERSAO
    salvar(dados)
    por_origem = {o: sum(1 for c in resultados if c.startswith(o + "-")) for o in ORIGENS}
    print(f"Medidor: {len(resultados)} rotas com preço {por_origem} | {len(avisos)} alertas | "
          f"sem preço: {sem_preco} | falhas: {falhas}")
    for chave, diag in diagnostico.items():
        # Diagnóstico: "bruto/ida-e-volta" que cada gaveta trouxe para rotas que precisaram das gavetas extras
        print(f"Diagnóstico {chave}: " + " | ".join(f"{f} {v}" for f, v in diag.items()))

    if primeira_vez or atualizacao:
        if resultados:
            titulo = ("🔧 <b>Medidor atualizado: Goiânia e Brasília medidas lado a lado</b>" if atualizacao
                      else "📉 <b>Medidor de preços ligado!</b>")
            telegram(
                f"🛠️ <b>Bastidores</b>\n{titulo}\n"
                f"Menores preços de ida e volta agora ({len(resultados)} de "
                f"{len(ORIGENS) * len(DESTINOS)} rotas):\n\n"
                + quadro(resultados, sem_preco)
                + "\n\n🧠 Confiro de hora em hora. Quedas de "
                f"<b>{int(QUEDA_MINIMA * 100)}% ou mais</b> abaixo da média vão para a vitrine."
                + ("" if atualizacao else " Os alertas começam depois de ~12 horas de histórico.")
            )
        else:
            telegram(
                "🛠️ <b>Bastidores</b>\n⚠️ <b>Medidor de preços:</b> não consegui ler nenhum preço nesta tentativa.\n"
                + html.escape("\n".join(falhas[:5]) or "A fonte não trouxe resultados.")
            )
        return

    for origem, destino, atual, media, queda in avisos:
        enviar_vitrine(alerta_vitrine(origem, destino, atual, media, queda, resultados), {
            "tipo": "Preço em queda", "rota": rota(origem, destino), "preco": atual["preco"],
            "media": round(media), "ida": atual["ida"], "volta": atual["volta"], "link": atual["link"],
        })

    if falhas and not resultados:
        telegram("🛠️ <b>Bastidores</b>\n⚠️ Medidor de preços: nenhuma consulta funcionou nesta rodada.\n"
                 + html.escape("\n".join(falhas[:5])))


if __name__ == "__main__":
    main()

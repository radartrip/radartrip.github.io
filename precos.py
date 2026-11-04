# Radar Trip - Medidor de precos (Vigia 3.2 - sem afiliado + resumo das 7h)
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
#
# Novidade v3.1 (plano C):
#   - 4 praias novas no radar: Aracaju, Joao Pessoa, Navegantes (Balneario Camboriu) e Ilheus.
#   - REGUA DO FIM DE SEMANA: "preco normal" de uma viagem sexta -> domingo saindo de GYN,
#     tirado da pesquisa no Google Flights de 03/11/2026 (REFERENCIA_FDS). Fica salva
#     tambem em dados/referencia_praias.json (base do dashboard).
#   - Para cada praia saindo de GYN, procura um fim de semana sexta -> domingo: nas buscas
#     que o medidor ja faz e no fim de semana campeao da pesquisa (1 consulta extra).
#     Se sair 15% ou mais abaixo do normal, manda "PRAIA NO FIM DE SEMANA" para a vitrine.
#   - O alerta de queda das praias passa a mostrar a regua do fim de semana.
#
# Novidade v3.2:
#   - SEM AFILIADO: o botao de cada alerta abre a busca do Google Flights (rota e datas),
#     sem marker/comissao. A Travelpayouts segue so como termometro interno de preco.
#   - RESUMO DAS 7H na vitrine: uma vez por dia, a partir das 7h de Brasilia, manda
#     "Radar do dia" com os destinos mais baratos saindo de Goiania e de Brasilia.

import html
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

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
    "AJU": "Aracaju",
    "JPA": "João Pessoa",
    "IOS": "Ilhéus",
    "SAO": "São Paulo",
    "RIO": "Rio de Janeiro",
    "FLN": "Florianópolis",
    "NVT": "Navegantes (Balneário Camboriú)",
    "POA": "Porto Alegre",
    "BUE": "Buenos Aires",
    "SCL": "Santiago",
    "LIS": "Lisboa",
    "MCO": "Orlando",
}

# ---- Régua do fim de semana de praia (saindo de GYN, sexta -> domingo, ida e volta, 1 adulto) ----
# Fonte: pesquisa no Google Flights de 03/11/2026 (preço com taxas).
#   normal      = menor preço de um fim de semana nessa rota, sem escolher horário
#   manha_noite = ida sexta de manhã + volta domingo à noite (o "sem faltar ao trabalho")
#   ida/volta   = fim de semana campeão da pesquisa (o robô consulta essas datas toda hora)
# Atualize uma vez por mês, junto com a troca dos fins de semana no Google Flights.
REFERENCIA_FDS_DATA = "2026-11-03"
REFERENCIA_FDS = {
    "AJU": {"normal": 1076, "manha_noite": 1076, "ida": "2027-03-05", "volta": "2027-03-07"},
    "RIO": {"normal": 1109, "manha_noite": 1137, "ida": "2027-02-19", "volta": "2027-02-21"},
    "FLN": {"normal": 853, "manha_noite": 1154, "ida": "2027-02-26", "volta": "2027-02-28"},
    "FOR": {"normal": 949, "manha_noite": 1455, "ida": "2027-04-23", "volta": "2027-04-25"},
    "MCZ": {"normal": 1318, "manha_noite": 1519, "ida": "2027-01-29", "volta": "2027-01-31"},
    "REC": {"normal": 1453, "manha_noite": 1587, "ida": "2027-01-15", "volta": "2027-01-17"},
    "NVT": {"normal": 1081, "manha_noite": 1598, "ida": "2027-02-19", "volta": "2027-02-21"},
    "BPS": {"normal": 1443, "manha_noite": 1672, "ida": "2027-02-19", "volta": "2027-02-21"},
    "JPA": {"normal": 1331, "manha_noite": 1903, "ida": "2027-03-12", "volta": "2027-03-14"},
    "NAT": {"normal": 1496, "manha_noite": 2382, "ida": "2027-02-26", "volta": "2027-02-28"},
    "SSA": {"normal": 1950, "manha_noite": None, "ida": "2027-02-19", "volta": "2027-02-21"},
    "IOS": {"normal": 799, "manha_noite": None, "ida": "2027-09-10", "volta": "2027-09-12"},
}
ORIGEM_FDS = "GYN"           # a régua foi medida saindo de Goiânia
QUEDA_FDS = 0.15             # fim de semana 15% ou mais abaixo do "normal" vai para a vitrine
REPETE_FDS_DIAS = 7          # o mesmo fim de semana não volta em 7 dias, salvo nova queda de 5%

QUEDA_MINIMA = 0.15          # avisa se o preço ficar 15% ou mais abaixo da média
DIAS_MEDIA = 7               # média dos últimos 7 dias
MIN_LEITURAS = 12            # só compara depois de ter histórico suficiente
INTERVALO_MIN = 55           # consulta no máximo 1 vez por hora
MAX_LEITURAS = 1500          # cerca de 2 meses de histórico por rota
VERSAO = 6                   # versão do formato dos dados (6 = praias novas + régua do fim de semana)
MESES_PLANO_C = 6            # quantos meses à frente a busca mês a mês procura

CIAS = {"G3": "Gol", "LA": "LATAM", "JJ": "LATAM", "AD": "Azul", "AR": "Aerolíneas Argentinas",
        "TP": "TAP", "AA": "American", "UA": "United", "DL": "Delta", "CM": "Copa", "AV": "Avianca",
        "H2": "Sky", "JA": "JetSMART", "IB": "Iberia", "UX": "Air Europa", "AF": "Air France", "KL": "KLM"}

PASTA = "dados"
ARQ = os.path.join(PASTA, "precos.json")
ARQ_REFERENCIA = os.path.join(PASTA, "referencia_praias.json")
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


def resultado(opcao, origem, destino, fonte):
    """Transforma uma opção da API no formato que o robô guarda e manda."""
    ida = (opcao.get("departure_at") or "")[:10]
    volta = (opcao.get("return_at") or "")[:10]
    return {
        "preco": int(opcao["price"]),
        "ida": ida,
        "volta": volta,
        "cia": CIAS.get(opcao.get("airline") or "", opcao.get("airline") or ""),
        "escalas": opcao.get("transfers"),
        "link": montar_link(opcao.get("link") or "", origem, destino, ida, volta),
        "fonte": fonte,
        "origem": origem,
    }


def eh_fim_de_semana(opcao):
    """Ida numa sexta e volta no domingo seguinte."""
    try:
        ida = date.fromisoformat((opcao.get("departure_at") or "")[:10])
        volta = date.fromisoformat((opcao.get("return_at") or "")[:10])
    except ValueError:
        return False
    return ida.weekday() == 4 and (volta - ida).days == 2


def melhor_fim_de_semana(opcoes, origem, destino, fonte):
    fds = [o for o in so_ida_e_volta(opcoes) if eh_fim_de_semana(o)]
    if not fds:
        return None
    return resultado(min(fds, key=lambda o: o["price"]), origem, destino, fonte)


def consultar(origem, destino, token, diag=None):
    """Tenta as gavetas em ordem e devolve o menor preço da primeira que tiver ida e volta.
    Em diag anota o que cada gaveta trouxe: total bruto / quantos de ida e volta (ou erro).
    Também devolve, em "fds", o fim de semana sexta -> domingo mais barato dessa gaveta."""
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
        melhor = resultado(min(opcoes, key=lambda o: o["price"]), origem, destino, fonte)
        melhor["fds"] = melhor_fim_de_semana(opcoes, origem, destino, fonte)
        return melhor
    if erros and len(erros) == len(BUSCAS):
        raise RuntimeError("; ".join(erros))
    return None


def busca_fds_fixo(origem, destino, ref, token, hoje=None):
    """Preço do fim de semana campeão da pesquisa (datas fixas). 1 consulta por praia."""
    hoje = hoje or date.today()
    if not ref.get("ida") or date.fromisoformat(ref["ida"]) < hoje:
        return None
    opcoes = lista(chamar("/aviasales/v3/prices_for_dates", {
        "origin": origem, "destination": destino, "currency": "brl",
        "departure_at": ref["ida"], "return_at": ref["volta"],
        "sorting": "price", "one_way": "false", "limit": 10, "market": "br",
    }, token))
    return melhor_fim_de_semana(opcoes, origem, destino, "fds_fixo")


def procurar_fds(origem, destino, atual, token, falhas):
    """Fim de semana mais barato achado para a praia: nas buscas normais + no fim de semana campeão."""
    if origem != ORIGEM_FDS or destino not in REFERENCIA_FDS:
        return None
    candidatos = [atual["fds"]] if atual and atual.get("fds") else []
    try:
        fixo = busca_fds_fixo(origem, destino, REFERENCIA_FDS[destino], token)
        if fixo:
            candidatos.append(fixo)
    except Exception as erro:
        falhas.append(f"{rota(origem, destino)} fim de semana: {erro}")
    return min(candidatos, key=lambda c: c["preco"]) if candidatos else None


def avaliar_fds(chave, fds, alertas, momento):
    """Devolve a queda (0.15 = 15%) se o fim de semana estiver bem abaixo do normal e não for repetição."""
    ref = REFERENCIA_FDS[chave.split("-")[1]]
    queda = 1 - fds["preco"] / ref["normal"]
    if queda < QUEDA_FDS:
        return None
    marca = f"FDS-{chave}-{fds['ida']}"
    anterior = alertas.get(marca)
    if anterior:
        recente = anterior["t"] >= (momento - timedelta(days=REPETE_FDS_DIAS)).isoformat()
        if recente and fds["preco"] >= anterior["preco"] * 0.95:
            return None
    alertas[marca] = {"preco": fds["preco"], "t": momento.isoformat()}
    return queda


def limpar_alertas_fds(alertas, momento):
    """Esquece marcas de fim de semana com mais de 30 dias (não deixa o arquivo crescer)."""
    limite = (momento - timedelta(days=30)).isoformat()
    for marca in [m for m, a in alertas.items() if m.startswith("FDS-") and a["t"] < limite]:
        del alertas[marca]


def salvar_referencia():
    """Guarda a régua em dados/referencia_praias.json (só reescreve quando muda)."""
    conteudo = {"origem": ORIGEM_FDS, "pesquisa": REFERENCIA_FDS_DATA,
                "fonte": "Google Flights, sexta -> domingo, ida e volta, 1 adulto, com taxas",
                "destinos": {d: {"nome": DESTINOS.get(d, d), **r} for d, r in REFERENCIA_FDS.items()}}
    try:
        with open(ARQ_REFERENCIA, encoding="utf-8") as f:
            if json.load(f) == conteudo:
                return
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    os.makedirs(PASTA, exist_ok=True)
    with open(ARQ_REFERENCIA, "w", encoding="utf-8") as f:
        json.dump(conteudo, f, ensure_ascii=False, indent=1)


def ddmm(iso):
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d%m")
    except ValueError:
        return ""


def montar_link(caminho, origem, destino, ida, volta):
    """Link do alerta: busca pública do Google Flights (sem afiliado, sem comissão).
    O 'caminho' do Aviasales é ignorado de propósito desde a v3.2."""
    if ida and volta:
        consulta = f"Flights from {origem} to {destino} on {ida} through {volta}"
    elif ida:
        consulta = f"Flights from {origem} to {destino} on {ida}"
    else:
        consulta = f"Flights from {origem} to {destino}"
    return ("https://www.google.com/travel/flights?hl=pt-BR&gl=BR&curr=BRL&q="
            + urllib.parse.quote(consulta))


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
    Leituras que vieram de Brasília (plano D) vão para a rota BSB, com a gaveta equivalente.
    Dados já no formato por rota passam sem mudança (v5 -> v6 só acrescenta destinos)."""
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


def detalhes_voo(atual):
    escalas = "direto" if atual["escalas"] == 0 else f"{atual['escalas']} escala(s)" if atual["escalas"] else ""
    return " · ".join(x for x in [atual["cia"], escalas] if x)


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


def quadro_fds(achados):
    """Resumo para os BASTIDORES: fim de semana sexta -> domingo de cada praia x régua."""
    linhas = []
    for destino, ref in sorted(REFERENCIA_FDS.items(), key=lambda x: x[1]["normal"]):
        nome = html.escape(DESTINOS.get(destino, destino))
        fds = achados.get(rota(ORIGEM_FDS, destino))
        if fds:
            diferenca = 1 - fds["preco"] / ref["normal"]
            sinal = f"{abs(diferenca) * 100:.0f}% {'abaixo' if diferenca >= 0 else 'acima'}"
            linhas.append(f"• <b>{nome}</b> {texto_datas(fds)}: {reais(fds['preco'])} "
                          f"(normal {reais(ref['normal'])}, {sinal})")
        else:
            linhas.append(f"• <b>{nome}</b>: sem fim de semana no cache (normal {reais(ref['normal'])})")
    return "\n".join(linhas)


def linha_regua(destino):
    ref = REFERENCIA_FDS.get(destino)
    if not ref:
        return ""
    return f"\n🏖️ Fim de semana sex→dom nessa rota costuma sair a partir de {reais(ref['normal'])}"


def alerta_vitrine(origem, destino, atual, media, queda, resultados):
    nome = DESTINOS[destino]
    detalhes = detalhes_voo(atual)
    texto = (
        "📉 <b>PREÇO EM QUEDA</b>\n\n"
        f"✈️ <b>{ORIGENS[origem]} → {html.escape(nome)}</b>"
        + (f"\n{NOTA_ORIGEM[origem]}" if origem in NOTA_ORIGEM else "")
        + f"\n💰 <b>{reais(atual['preco'])}</b> ida e volta"
        f"\n📊 {int(queda * 100)}% abaixo da média dos últimos {DIAS_MEDIA} dias ({reais(media)})"
        f"\n📅 {texto_datas(atual)}" + (f" · {html.escape(detalhes)}" if detalhes else "")
    )
    if origem == ORIGEM_FDS:
        texto += linha_regua(destino)
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


def alerta_fds(destino, fds, queda):
    """Alerta da vitrine: fim de semana de praia (sexta -> domingo) bem abaixo do normal."""
    ref = REFERENCIA_FDS[destino]
    detalhes = detalhes_voo(fds)
    texto = (
        "🏖️ <b>PRAIA NO FIM DE SEMANA · ABAIXO DO NORMAL</b>\n\n"
        f"✈️ <b>{ORIGENS[ORIGEM_FDS]} → {html.escape(DESTINOS[destino])}</b>\n"
        f"📅 Sexta {data_br(fds['ida'])} a domingo {data_br(fds['volta'])}\n"
        f"💰 <b>{reais(fds['preco'])}</b> ida e volta" + (f" · {html.escape(detalhes)}" if detalhes else "") + "\n"
        f"📏 Um fim de semana nessa rota costuma sair a partir de {reais(ref['normal'])} "
        f"→ <b>{round(queda * 100)}% abaixo</b>"
    )
    if ref.get("manha_noite"):
        texto += (f"\n🕐 Com ida sexta de manhã e volta domingo à noite costuma sair ~{reais(ref['manha_noite'])}. "
                  "Confira os horários no link.")
    else:
        texto += "\n🕐 Confira os horários no link."
    if fds["link"]:
        texto += f"\n\n🔗 <a href=\"{html.escape(fds['link'])}\">Ver voo</a>"
    return texto


# ================= Resumo diário (vitrine, 7h de Brasília) =================
ARQ_RESUMO = os.path.join(PASTA, "resumo_diario.json")
HORA_RESUMO = 7              # hora de Brasília a partir da qual o resumo sai (1x por dia)
FUSO_BR = timezone(timedelta(hours=-3))
NACIONAIS = {"REC", "SSA", "FOR", "MCZ", "NAT", "BPS", "SAO", "RIO", "FLN", "POA", "AJU", "JPA", "NVT", "IOS"}
TOP_RESUMO = 8


def hora_do_resumo(momento):
    """True se já passou das 7h em Brasília e o resumo de hoje ainda não saiu."""
    local = momento.astimezone(FUSO_BR)
    if local.hour < HORA_RESUMO:
        return False
    try:
        with open(ARQ_RESUMO, encoding="utf-8") as f:
            ultimo = json.load(f).get("data", "")
    except (FileNotFoundError, json.JSONDecodeError):
        ultimo = ""
    return ultimo != local.date().isoformat()


def montar_resumo(resultados, momento):
    """Melhor preço de cada destino (GYN x BSB lado a lado), do mais barato ao mais caro."""
    linhas_nac, linhas_int = [], []
    for destino, nome in DESTINOS.items():
        precos = {o: resultados.get(rota(o, destino)) for o in ORIGENS}
        validos = {o: p for o, p in precos.items() if p}
        if not validos:
            continue
        melhor_origem = min(validos, key=lambda o: validos[o]["preco"])
        melhor = validos[melhor_origem]
        partes = []
        for o in ORIGENS:
            p = precos.get(o)
            valor = reais(p["preco"]) if p else "—"
            partes.append(f"<b>{o} {valor}</b>" if o == melhor_origem and len(validos) > 1 else f"{o} {valor}")
        nome_html = html.escape(nome)
        if melhor.get("link"):
            nome_html = f"<a href=\"{html.escape(melhor['link'])}\">{nome_html}</a>"
        linha = (melhor["preco"], f"• {nome_html}: " + " · ".join(partes))
        (linhas_nac if destino in NACIONAIS else linhas_int).append(linha)
    if not linhas_nac and not linhas_int:
        return ""
    local = momento.astimezone(FUSO_BR)
    texto = (f"☀️ <b>RADAR DO DIA · {local.strftime('%d/%m')}</b>\n"
             "Menores preços de ida e volta encontrados agora saindo de Goiânia (GYN) e Brasília (BSB) 🚗 ~2h30\n")
    if linhas_nac:
        texto += "\n🇧🇷 <b>Brasil</b>\n" + "\n".join(l for _, l in sorted(linhas_nac)[:TOP_RESUMO]) + "\n"
    if linhas_int:
        texto += "\n🌎 <b>Exterior</b>\n" + "\n".join(l for _, l in sorted(linhas_int)) + "\n"
    texto += (f"\n<i>Em negrito, a origem mais barata. Toque no destino para ver as datas no Google Flights. "
              f"Preços de buscas recentes, consultados às {local.strftime('%Hh%M')}: podem mudar.</i>")
    return texto


def enviar_resumo(resultados, momento):
    texto = montar_resumo(resultados, momento)
    if not texto:
        return None
    codigo = enviar_vitrine(texto, {"tipo": "Resumo diário", "titulo": "Radar do dia",
                                    "etiquetas": {"categoria": "resumo_diario", "programas": [], "trechos": []}})
    os.makedirs(PASTA, exist_ok=True)
    with open(ARQ_RESUMO, "w", encoding="utf-8") as f:
        json.dump({"data": momento.astimezone(FUSO_BR).date().isoformat(), "codigo": codigo,
                   "enviado_em": momento.isoformat()}, f, ensure_ascii=False, indent=1)
    return codigo


def main():
    token = os.environ.get("TRAVELPAYOUTS_TOKEN")
    if not token:
        print("Sem TRAVELPAYOUTS_TOKEN; medidor de preços não rodou.")
        return

    dados = carregar()
    primeira_vez = dados is None
    dados = dados or {"ultima_consulta": "", "historico": {}, "alertas": {}, "versao": VERSAO}
    atualizacao = not primeira_vez and dados.get("versao", 1) < VERSAO
    versao_antiga = dados.get("versao", 1)
    if atualizacao:
        dados = migrar(dados)
    momento = agora()

    if dados["ultima_consulta"] and not atualizacao:
        ultima = datetime.fromisoformat(dados["ultima_consulta"])
        if momento - ultima < timedelta(minutes=INTERVALO_MIN):
            print("Medidor: consultado há menos de 1 hora, aguardando.")
            return

    salvar_referencia()
    historico, alertas = dados["historico"], dados["alertas"]
    limpar_alertas_fds(alertas, momento)
    resultados, falhas, avisos, sem_preco, diagnostico = {}, [], [], [], {}
    fds_achados, avisos_fds = {}, []

    for origem in ORIGENS:
        for destino, nome in DESTINOS.items():
            chave = rota(origem, destino)
            diag = {}
            atual = None
            try:
                atual = consultar(origem, destino, token, diag)
            except Exception as erro:
                falhas.append(f"{chave}: {erro}")
            finally:
                if "mes" in diag:
                    diagnostico[chave] = diag

            # Régua do fim de semana de praia (só GYN)
            fds = procurar_fds(origem, destino, atual, token, falhas)
            if fds:
                fds_achados[chave] = fds
                # Na estreia/atualização só mostra o quadro nos bastidores (não marca nem envia)
                queda_fds = None if (primeira_vez or atualizacao) else avaliar_fds(chave, fds, alertas, momento)
                if queda_fds:
                    avisos_fds.append((destino, fds, queda_fds))

            if not atual:
                sem_preco.append(f"{origem}→{nome}")
                continue
            resultados[chave] = atual
            queda = avaliar(chave, atual, historico, alertas, momento)
            if queda:
                avisos.append((origem, destino, atual, *queda))
            leitura = {"t": momento.isoformat(), **{k: v for k, v in atual.items() if k != "fds"}}
            historico.setdefault(chave, []).append(leitura)
            historico[chave] = historico[chave][-MAX_LEITURAS:]

    dados["ultima_consulta"] = momento.isoformat()
    dados["versao"] = VERSAO
    dados["fins_de_semana"] = {c: {**f, "t": momento.isoformat()} for c, f in fds_achados.items()}
    salvar(dados)
    por_origem = {o: sum(1 for c in resultados if c.startswith(o + "-")) for o in ORIGENS}
    print(f"Medidor: {len(resultados)} rotas com preço {por_origem} | {len(avisos)} alertas | "
          f"{len(fds_achados)} fins de semana achados, {len(avisos_fds)} abaixo do normal | "
          f"sem preço: {sem_preco} | falhas: {falhas}")
    for chave, diag in diagnostico.items():
        # Diagnóstico: "bruto/ida-e-volta" que cada gaveta trouxe para rotas que precisaram das gavetas extras
        print(f"Diagnóstico {chave}: " + " | ".join(f"{f} {v}" for f, v in diag.items()))

    if primeira_vez or atualizacao:
        if resultados:
            if atualizacao and versao_antiga >= 5:
                titulo = ("🔧 <b>Medidor atualizado: 4 praias novas + régua do fim de semana</b>\n"
                          "Aracaju, João Pessoa, Navegantes e Ilhéus entraram no radar.")
            elif atualizacao:
                titulo = "🔧 <b>Medidor atualizado: Goiânia e Brasília medidas lado a lado</b>"
            else:
                titulo = "📉 <b>Medidor de preços ligado!</b>"
            telegram(
                f"🛠️ <b>Bastidores</b>\n{titulo}\n"
                f"Menores preços de ida e volta agora ({len(resultados)} de "
                f"{len(ORIGENS) * len(DESTINOS)} rotas):\n\n"
                + quadro(resultados, sem_preco)
                + "\n\n🏖️ <b>Fim de semana sex→dom saindo de Goiânia × preço normal:</b>\n"
                + quadro_fds(fds_achados)
                + "\n\n🧠 Confiro de hora em hora. Quedas de "
                f"<b>{int(QUEDA_MINIMA * 100)}% ou mais</b> abaixo da média e fins de semana de praia "
                f"<b>{int(QUEDA_FDS * 100)}% ou mais</b> abaixo do normal vão para a vitrine."
                + ("" if atualizacao else " Os alertas de queda começam depois de ~12 horas de histórico.")
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

    for destino, fds, queda in avisos_fds:
        enviar_vitrine(alerta_fds(destino, fds, queda), {
            "tipo": "Praia no fim de semana", "rota": rota(ORIGEM_FDS, destino), "preco": fds["preco"],
            "normal": REFERENCIA_FDS[destino]["normal"], "ida": fds["ida"], "volta": fds["volta"],
            "link": fds["link"],
            "etiquetas": {"categoria": "praia_fim_de_semana", "programas": [],
                          "trechos": [rota(ORIGEM_FDS, destino)]},
        })

    if resultados and hora_do_resumo(momento):
        codigo = enviar_resumo(resultados, momento)
        print(f"Resumo do dia enviado: {codigo}")

    if falhas and not resultados:
        telegram("🛠️ <b>Bastidores</b>\n⚠️ Medidor de preços: nenhuma consulta funcionou nesta rodada.\n"
                 + html.escape("\n".join(falhas[:5])))


if __name__ == "__main__":
    main()

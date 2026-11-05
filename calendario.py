# Radar Trip - Calendario de milhas (Smiles) - v1.0
#
# Todo dia, de manha, o robo abre o site da Smiles num navegador de verdade (sem login),
# clica em "Ver mais datas e tarifas" e le o calendario que a propria pagina recebe:
# o menor valor em milhas de cada dia, na ida e na volta, para 2 meses.
# Publica 1 destino por dia na vitrine (rodizio), no estilo "calendario do mes".
#
# Regras de ouro:
#   - Nada de login e nada de disfarce: o robo usa o navegador automatico como ele e.
#     Se a Smiles barrar, ele NAO insiste nem tenta contornar: so avisa nos bastidores.
#   - Ritmo de gente: 1 busca por dia (no maximo 2, se o 1o destino vier sem calendario).
#   - Teste manual (Actions > Run workflow > "teste"): a previa vai so para os bastidores.

import html
import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from vigia import CUSTO_MILHEIRO, enviar_vitrine, telegram

ORIGEM = "GYN"
ORIGEM_NOME = "Goiânia"

# Rodizio: um destino por dia, nesta ordem. Para incluir ou tirar, edite a lista.
ROTAS = ["REC", "SSA", "FOR", "MCZ", "NAT", "FLN", "GIG"]
NOMES = {
    "REC": "Recife", "SSA": "Salvador", "FOR": "Fortaleza", "MCZ": "Maceió", "NAT": "Natal",
    "FLN": "Florianópolis", "GIG": "Rio de Janeiro", "SDU": "Rio de Janeiro (Santos Dumont)",
    "GRU": "São Paulo (Guarulhos)", "CGH": "São Paulo (Congonhas)", "AJU": "Aracaju",
    "JPA": "João Pessoa", "BPS": "Porto Seguro", "NVT": "Navegantes", "IOS": "Ilhéus",
    "POA": "Porto Alegre", "CWB": "Curitiba", "BEL": "Belém", "MAO": "Manaus",
}

MESES_A_FRENTE = 1          # 1 = o calendario comeca no mes que vem (ex.: em novembro, dezembro + janeiro)
NOITES_MIN, NOITES_MAX = 3, 10
FAIXA_VERDE = 0.10          # verde = ate 10% acima do menor valor do mes
MAX_ROTAS_POR_DIA = 2       # se o destino do dia vier sem calendario, tenta o proximo (no maximo 2)
PAUSA_APOS = 3              # barrado 3 dias seguidos -> o robo pausa sozinho (volta com um teste manual que de certo)
ESPERA_MS = int(os.environ.get("ESPERA_MS", "90000"))   # quanto o robo espera a pagina responder

FUSO_BR = timezone(timedelta(hours=-3))
BASE = os.environ.get("SMILES_BASE", "https://www.smiles.com.br")
PASTA = "dados"
ARQ = os.path.join(PASTA, "calendario.json")
PASTA_PRINTS = "prints"     # prints de falha: vao como anexo do Actions, nao para o repositorio
MAX_LEITURAS_ROTA = 8
MESES_PT = ["", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
            "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]


class Barrado(Exception):
    """A Smiles nao deixou o robo ler (bloqueio, erro ou pagina diferente do esperado)."""


class SemCalendario(Exception):
    """A rota nao tem calendario com precos (acontece em rotas de pouca procura)."""


# ================= Datas e links =================

def primeiro_dia(base, meses):
    ano, mes = base.year, base.month + meses
    while mes > 12:
        ano, mes = ano + 1, mes - 12
    return date(ano, mes, 1)


def ms(dia):
    """Data no formato do site da Smiles: milissegundos, meio-dia de Brasilia."""
    return int(datetime(dia.year, dia.month, dia.day, 12, tzinfo=FUSO_BR).timestamp() * 1000)


def url_busca(origem, destino, ida, volta):
    q = {
        "adults": 1, "cabin": "ALL", "children": 0, "departureDate": ms(ida), "infants": 0,
        "isElegible": "false", "isFlexibleDateChecked": "false", "returnDate": ms(volta),
        "searchType": "g3", "segments": 1, "tripType": 1,
        "originAirport": origem, "originCity": "", "originCountry": "", "originAirportIsAny": "false",
        "destinationAirport": destino, "destinCity": "", "destinCountry": "", "destinAirportIsAny": "false",
        "novo-resultado-voos": "true",
    }
    return BASE + "/mfe/emissao-passagem/?" + urllib.parse.urlencode(q)


def ddmm(iso):
    return f"{iso[8:10]}/{iso[5:7]}"


def milhar(n):
    return f"{n:,}".replace(",", ".")


def mil(n):
    """43500 -> '43,5 mil'; 50000 -> '50 mil'."""
    if n % 1000 == 0:
        return f"{n // 1000} mil"
    return f"{n / 1000:.1f}".replace(".", ",") + " mil"


def reais(valor):
    return "R$ " + f"{valor:,.0f}".replace(",", ".")


# ================= Leitura no site (navegador de verdade) =================

def abrir_navegador(p):
    """Usa o Google Chrome que ja vem no servidor do GitHub; sem ele, o Chromium do Playwright."""
    caminho = os.environ.get("CHROME_PATH")
    if caminho:
        return p.chromium.launch(executable_path=caminho, headless=True)
    try:
        return p.chromium.launch(channel="chrome", headless=True)
    except Exception:
        return p.chromium.launch(headless=True)


def recusar_cookies(page):
    """Recusa cookies nao essenciais quando o aviso aparece (opcao mais privada)."""
    try:
        botao = page.get_by_role("button", name="Rejeitar todos")
        if botao.count() and botao.first.is_visible():
            botao.first.click(timeout=3000)
    except Exception:
        pass


def resumo_pagina(page):
    try:
        titulo = page.title()
        texto = " ".join(page.inner_text("body").split())[:120]
        return f"título \"{titulo}\"; texto \"{texto}\""
    except Exception:
        return "página sem texto"


def ler_calendario(page, destino, ida, volta, erro_navegador):
    """Abre a busca, clica em 'Ver mais datas e tarifas' e devolve o calendario que a pagina recebeu."""
    try:
        page.goto(url_busca(ORIGEM, destino, ida, volta), wait_until="domcontentloaded", timeout=ESPERA_MS)
    except erro_navegador as erro:
        raise Barrado("o site da Smiles não abriu (" + str(erro).splitlines()[0][:120] + ")")
    recusar_cookies(page)
    botao = page.get_by_text("Ver mais datas e tarifas").filter(visible=True).first
    try:
        botao.wait_for(state="visible", timeout=ESPERA_MS)
    except erro_navegador:
        raise Barrado("a página de resultados não apareceu (" + resumo_pagina(page) + ")")
    recusar_cookies(page)
    try:
        with page.expect_response(lambda r: "/airlines/calendar/month" in r.url, timeout=60000) as info:
            botao.click()
    except erro_navegador:
        raise Barrado("o calendário não respondeu")
    resposta = info.value
    if resposta.status != 200:
        raise Barrado(f"o calendário respondeu com erro {resposta.status}")
    try:
        dados = resposta.json()
    except Exception:
        raise Barrado("o calendário veio num formato inesperado")
    if not isinstance(dados, dict):
        raise Barrado("o calendário veio num formato inesperado")
    return dados


def interpretar(dados):
    """Separa o calendario em ida e volta: {'2027-03-01': 50000, ...}. Dias sem preco ficam de fora."""
    ida, volta, tarifas = {}, {}, set()
    for seg in dados.get("calendarSegmentList") or []:
        alvo = {"SEGMENT_1": ida, "SEGMENT_2": volta}.get(seg.get("type"))
        if alvo is None:
            continue
        for dia in seg.get("calendarDayList") or []:
            milhas = dia.get("miles")
            if isinstance(milhas, (int, float)) and milhas > 0 and dia.get("date"):
                alvo[dia["date"][:10]] = int(milhas)
                tarifas.add(((dia.get("fare") or {}).get("type") or "").strip())
    return ida, volta, tarifas


# ================= Analise =================

def resumo_mes(precos):
    """Menor valor, dias do menor, dias 'verdes' (ate +10%) e dias mais caros do mes."""
    valores = sorted(precos.values())
    menor, maior = valores[0], valores[-1]
    melhores = sorted(d for d, v in precos.items() if v == menor)
    teto = menor * (1 + FAIXA_VERDE)
    verdes = sorted(d for d, v in precos.items() if menor < v <= teto)
    caros = []
    if maior > menor * 1.2:
        corte = menor + 0.75 * (maior - menor)
        caros = sorted(d for d, v in precos.items() if v >= corte)
    return {
        "menor": menor, "maior": maior, "melhores": melhores, "verdes": verdes,
        "teto_verde": max((precos[d] for d in verdes), default=None),
        "caros": caros, "piso_caro": min((precos[d] for d in caros), default=None),
    }


def dias(lista):
    return " ".join(d[8:10] for d in lista)


def linhas_mes(rotulo, emoji, precos):
    r = resumo_mes(precos)
    quando = "dia" if len(r["melhores"]) == 1 else "dias"
    linhas = [f"{emoji} <b>{rotulo}</b>: 🏆 <b>{mil(r['menor'])}</b> · {quando} {dias(r['melhores'])}"]
    if r["verdes"]:
        linhas.append(f"   🟢 até {mil(r['teto_verde'])}: {dias(r['verdes'])}")
    if r["caros"]:
        faixa = mil(r["maior"]) if r["piso_caro"] == r["maior"] else f"{mil(r['piso_caro'])} a {mil(r['maior'])}"
        linhas.append(f"   🔴 mais caros ({faixa}): {dias(r['caros'])}")
    return linhas


def melhores_combos(ida, volta, limite=2):
    """Ida e volta mais baratas com 3 a 10 noites; a 2a opcao fica longe da 1a (outra semana)."""
    combos = []
    for d1, v1 in ida.items():
        inicio = date.fromisoformat(d1)
        for noites in range(NOITES_MIN, NOITES_MAX + 1):
            d2 = (inicio + timedelta(days=noites)).isoformat()
            if d2 in volta:
                combos.append((v1 + volta[d2], d1, d2, noites))
    combos.sort()
    escolhidos = []
    for c in combos:
        if all(abs((date.fromisoformat(c[1]) - date.fromisoformat(e[1])).days) >= 5 for e in escolhidos):
            escolhidos.append(c)
        if len(escolhidos) == limite:
            break
    return escolhidos


def melhor_fim_de_semana(ida, volta):
    """Sexta -> domingo mais barato (o 'praia sem faltar ao trabalho')."""
    opcoes = []
    for d1, v1 in ida.items():
        inicio = date.fromisoformat(d1)
        if inicio.weekday() == 4:
            d2 = (inicio + timedelta(days=2)).isoformat()
            if d2 in volta:
                opcoes.append((v1 + volta[d2], d1, d2))
    return min(opcoes) if opcoes else None


def montar_post(destino, ida, volta, tarifas, momento):
    nome = NOMES.get(destino, destino)
    so_clube = tarifas == {"SMILES_CLUB"}
    linhas = [
        f"📅 <b>CALENDÁRIO DE MILHAS · {ORIGEM_NOME.upper()} ⇄ {html.escape(nome.upper())}</b>",
        "Smiles · menor valor de cada dia, por trecho"
        + (" · preço para assinantes do Clube Smiles" if so_clube else ""),
        "",
    ]
    for mes in sorted({d[:7] for d in list(ida) + list(volta)}):
        ano, m = mes.split("-")
        linhas.append(f"🗓️ <b>{MESES_PT[int(m)]}/{ano}</b>")
        for rotulo, emoji, precos in ((f"Ida ({ORIGEM} → {destino})", "🛫", ida),
                                      (f"Volta ({destino} → {ORIGEM})", "🛬", volta)):
            do_mes = {d: v for d, v in precos.items() if d.startswith(mes)}
            if do_mes:
                linhas += linhas_mes(rotulo, emoji, do_mes)
            else:
                linhas.append(f"{emoji} <b>{rotulo}</b>: sem preço no calendário")
        linhas.append("")
    custo = CUSTO_MILHEIRO.get("Smiles")
    combos = melhores_combos(ida, volta)
    if combos:
        total, d1, d2, noites = combos[0]
        linhas.append(f"💡 <b>Melhor ida e volta: {milhar(total)} milhas</b> · {ddmm(d1)} → {ddmm(d2)} ({noites} noites)")
        if len(combos) > 1:
            t2, a1, a2, n2 = combos[1]
            linhas.append(f"   Outra opção: {milhar(t2)} milhas · {ddmm(a1)} → {ddmm(a2)} ({n2} noites)")
        if custo:
            linhas.append(f"💰 ≈ {reais(total * custo / 1000)} comprando milhas a {reais(custo)} o milheiro (+ taxas)")
        link = url_busca(ORIGEM, destino, date.fromisoformat(d1), date.fromisoformat(d2))
        linhas.append(f"🔗 <a href=\"{html.escape(link)}\">Ver essas datas na Smiles</a>")
    fds = melhor_fim_de_semana(ida, volta)
    if fds:
        total, d1, d2 = fds
        linhas.append(f"🏖️ Fim de semana sex→dom mais barato: {ddmm(d1)} → {ddmm(d2)} · {milhar(total)} milhas")
    local = momento.astimezone(FUSO_BR)
    linhas += ["", f"<i>Valores \"a partir de\" do calendário da Smiles, consultados às {local.strftime('%Hh%M')} "
                   f"de {local.strftime('%d/%m')}. Mudam a todo momento; taxas de embarque à parte.</i>"]
    return "\n".join(linhas)


# ================= Estado =================

def carregar_estado():
    try:
        with open(ARQ, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"versao": 1, "proxima": 0, "ultimo_post": "", "ultimo_aviso": "",
                "falhas_seguidas": 0, "historico": {}}


def salvar_estado(estado):
    os.makedirs(PASTA, exist_ok=True)
    with open(ARQ, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=1)


def guardar_leitura(estado, destino, ida, volta, momento, codigo):
    chave = f"{ORIGEM}-{destino}"
    hist = estado["historico"].setdefault(chave, [])
    hist.append({"t": momento.isoformat(), "ida": ida, "volta": volta, "codigo": codigo})
    estado["historico"][chave] = hist[-MAX_LEITURAS_ROTA:]


def salvar_print(page, destino):
    caminho = os.path.join(PASTA_PRINTS, f"falha-{destino}.png")
    try:
        os.makedirs(PASTA_PRINTS, exist_ok=True)
        page.screenshot(path=caminho, full_page=False)
        return caminho
    except Exception:
        return None


def enviar_print(caminho, legenda):
    """Manda o print da tela para os bastidores (Telegram sendPhoto). Nunca derruba o robo."""
    token = os.environ.get("TELEGRAM_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat or not caminho or not os.path.exists(caminho):
        return
    try:
        limite = "RadarTrip" + str(int(datetime.now().timestamp() * 1000))
        with open(caminho, "rb") as f:
            foto = f.read()
        partes = [
            f"--{limite}\r\nContent-Disposition: form-data; name=\"{nome}\"\r\n\r\n{valor}\r\n".encode("utf-8")
            for nome, valor in (("chat_id", chat), ("caption", legenda[:1000]))
        ]
        partes.append(f"--{limite}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"print.png\"\r\n"
                      "Content-Type: image/png\r\n\r\n".encode("utf-8") + foto + b"\r\n")
        partes.append(f"--{limite}--\r\n".encode("utf-8"))
        api = os.environ.get("TELEGRAM_API", "https://api.telegram.org")
        req = urllib.request.Request(f"{api}/bot{token}/sendPhoto", data=b"".join(partes),
                                     headers={"Content-Type": f"multipart/form-data; boundary={limite}"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as erro:
        print(f"Calendário: não consegui mandar o print: {erro}")


def avisar(estado, hoje, texto, teste):
    """Avisos tecnicos vao para os bastidores, no maximo 1 por dia (no teste, sempre)."""
    if teste or estado.get("ultimo_aviso") != hoje.isoformat():
        telegram("🛠️ <b>Bastidores</b> · " + texto)
        if not teste:
            estado["ultimo_aviso"] = hoje.isoformat()


# ================= Principal =================

def main():
    teste = os.environ.get("TESTE") == "1"
    estado = carregar_estado()
    momento = datetime.now(timezone.utc)
    hoje = momento.astimezone(FUSO_BR).date()

    if not teste and estado.get("ultimo_post") == hoje.isoformat():
        print("Calendário: já publicado hoje.")
        return
    if not teste and estado.get("ultima_falha") == hoje.isoformat():
        # A 2a chamada do dia (rede de seguranca do agendador) nao insiste se a Smiles ja barrou hoje
        print("Calendário: a Smiles já barrou hoje; tento de novo amanhã.")
        return
    if not teste and estado.get("falhas_seguidas", 0) >= PAUSA_APOS:
        # Respeito ao site: depois de 3 dias barrado, o robo para de tentar ate alguem rodar o teste manual
        print(f"Calendário: pausado depois de {PAUSA_APOS} dias barrado. Rode o teste manual para reativar.")
        return

    if teste:
        fila = [(os.environ.get("ROTA_TESTE") or ROTAS[0]).strip().upper()]
    else:
        inicio = estado.get("proxima", 0) % len(ROTAS)
        fila = [ROTAS[(inicio + k) % len(ROTAS)] for k in range(MAX_ROTAS_POR_DIA)]

    ida_busca = primeiro_dia(hoje, MESES_A_FRENTE) + timedelta(days=9)   # dia 10 do mês
    volta_busca = ida_busca + timedelta(days=7)

    try:
        from playwright.sync_api import Error as ErroNavegador
        from playwright.sync_api import sync_playwright
    except ImportError:
        avisar(estado, hoje, "⚠️ Calendário Smiles: o navegador do robô (Playwright) não está instalado.", teste)
        salvar_estado(estado)
        return

    sem_calendario, publicado = [], None
    with sync_playwright() as p:
        try:
            navegador = abrir_navegador(p)
        except Exception as erro:
            avisar(estado, hoje, "⚠️ Calendário Smiles: não consegui abrir o navegador do robô ("
                   + html.escape(str(erro).splitlines()[0][:150]) + ").", teste)
            salvar_estado(estado)
            return
        contexto = navegador.new_context(viewport={"width": 1280, "height": 900},
                                         locale="pt-BR", timezone_id="America/Sao_Paulo")
        page = contexto.new_page()
        for destino in fila:
            try:
                dados = ler_calendario(page, destino, ida_busca, volta_busca, ErroNavegador)
                ida, volta, tarifas = interpretar(dados)
                if not ida and not volta:
                    raise SemCalendario(f"calendário sem preços ({dados.get('calendarStatus', '?')})")
            except SemCalendario as erro:
                sem_calendario.append(f"{NOMES.get(destino, destino)}: {erro}")
                continue
            except Barrado as erro:
                foto = salvar_print(page, destino)
                if not teste and estado.get("ultima_falha") != hoje.isoformat():
                    estado["falhas_seguidas"] = estado.get("falhas_seguidas", 0) + 1
                    estado["ultima_falha"] = hoje.isoformat()
                pausa = (not teste and estado.get("falhas_seguidas", 0) >= PAUSA_APOS)
                ja_avisou = not teste and estado.get("ultimo_aviso") == hoje.isoformat()
                avisar(estado, hoje,
                       f"⛔ <b>Calendário Smiles</b>: a Smiles não deixou o robô ler o calendário de "
                       f"{ORIGEM} → {html.escape(destino)} ({html.escape(str(erro))}). Nada foi publicado. "
                       "Regra do projeto: o robô não se disfarça para passar."
                       + (f" Dias seguidos barrado: {estado['falhas_seguidas']}." if not teste else "")
                       + (f"\n⏸️ <b>Robô do calendário pausado</b> ({PAUSA_APOS} dias seguidos). "
                          "Para reativar, rode o teste manual; se der certo, ele volta sozinho." if pausa else "")
                       + ("\n🖼️ Print da tela logo abaixo." if foto else ""), teste)
                if not ja_avisou:
                    enviar_print(foto, f"Tela que o robô viu na Smiles ({ORIGEM} → {destino})")
                break
            except Exception as erro:
                # Falha do proprio robo (nao da Smiles): avisa, nao conta como "barrado" e tenta amanha
                avisar(estado, hoje, "⚠️ Calendário Smiles: falha técnica do robô ("
                       + html.escape(str(erro).splitlines()[0][:150]) + "). Amanhã ele tenta de novo.", teste)
                break

            texto = montar_post(destino, ida, volta, tarifas, momento)
            if teste:
                reativado = estado.get("falhas_seguidas", 0) > 0
                estado["falhas_seguidas"] = 0          # teste que deu certo tira o robo da pausa
                estado["ultima_falha"] = ""            # ...e libera a rodada do dia
                telegram("🧪 <b>TESTE do calendário</b> (não foi para o canal)\n"
                         f"Li {len(ida)} dias de ida e {len(volta)} de volta."
                         + (" ▶️ Robô do calendário reativado." if reativado else "")
                         + " Prévia:\n\n" + texto)
                publicado = "TESTE"
            else:
                publicado = enviar_vitrine(texto, {
                    "tipo": "Calendário de milhas", "titulo": f"Smiles {ORIGEM} ⇄ {destino}",
                    "rota": f"{ORIGEM}-{destino}",
                    "etiquetas": {"categoria": "calendario_milhas", "programas": ["Smiles"],
                                  "trechos": [f"{ORIGEM}-{destino}", f"{destino}-{ORIGEM}"]},
                })
                estado["ultimo_post"] = hoje.isoformat()
                estado["falhas_seguidas"] = 0
                estado["proxima"] = (ROTAS.index(destino) + 1) % len(ROTAS) if destino in ROTAS else 0
            guardar_leitura(estado, destino, ida, volta, momento, publicado)
            break
        navegador.close()

    if sem_calendario and not publicado:
        if not teste:
            estado["proxima"] = (estado.get("proxima", 0) + len(fila)) % len(ROTAS)
        avisar(estado, hoje, "ℹ️ Calendário Smiles sem preços hoje: " + html.escape("; ".join(sem_calendario))
               + ". Amanhã o rodízio segue.", teste)
    salvar_estado(estado)
    print(f"Calendário: publicado={publicado} | sem calendário={sem_calendario} | "
          f"falhas seguidas={estado.get('falhas_seguidas', 0)}")


if __name__ == "__main__":
    main()

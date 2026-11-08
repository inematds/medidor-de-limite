#!/usr/bin/env python3
"""medidor-de-limite — para onde vai o limite da sua assinatura (Claude Code + Codex).

Lê os registros locais das sessões (JSONL) e mostra quem gasta e quem repete.
Só biblioteca padrão. Só leitura: nunca pausa, apaga ou envia nada.

  python3 medir.py relatorio [--dias 7]       relatório no terminal
  python3 medir.py html [--saida medidor.html] página com ponteiro e barras
  python3 medir.py calibrar                    acha o teto semanal nos seus registros
"""

import argparse
import difflib
import glob
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9
    ZoneInfo = None

VERSAO = "1.0.0"
AQUI = os.path.dirname(os.path.abspath(__file__))
CONFIG_PADRAO = os.path.expanduser("~/.config/medidor-de-limite/config.json")

# Pesos por tipo de token, na proporção do preço público de cada fornecedor.
# Servem para comparar sessões entre si; o teto é calibrado na MESMA unidade.
PESOS_PADRAO = {
    "claude": {"novos": 1.0, "cache_esc": 1.25, "cache_le": 0.1, "saida": 5.0},
    "codex": {"novos": 1.0, "cache_esc": 1.0, "cache_le": 0.1, "saida": 8.0},
}
RAIZES_PADRAO = ["~/projetos", "~/projects", "~/code", "~/src", "~/dev", "~/repos"]
CAMPOS = ("novos", "cache_le", "cache_esc", "saida")


# --------------------------------------------------------------------------- config

def carregar_config(caminho=None):
    caminho = caminho or CONFIG_PADRAO
    cfg = {}
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg.setdefault("tetos", {})
    cfg.setdefault("pesos", {})
    for ferr, pesos in PESOS_PADRAO.items():
        cfg["pesos"].setdefault(ferr, {})
        for k, v in pesos.items():
            cfg["pesos"][ferr].setdefault(k, v)
    cfg.setdefault("raizes", RAIZES_PADRAO)
    cfg.setdefault("publicos", [])
    cfg["_caminho"] = caminho
    return cfg


def salvar_config(cfg):
    caminho = cfg.get("_caminho") or CONFIG_PADRAO
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    limpo = {k: v for k, v in cfg.items() if not k.startswith("_")}
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(limpo, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return caminho


# --------------------------------------------------------------------------- utilidades

def quando(ts):
    """ISO 8601 (com Z) -> datetime com fuso. None se inválido."""
    if not isinstance(ts, str):
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def dia_local(dt):
    return dt.astimezone().strftime("%Y-%m-%d")


RE_LEMBRETE = re.compile(r"<(system-reminder|command-[a-z-]+|local-command-[a-z-]+)>.*?</\1>", re.S)
RE_TAG = re.compile(r"<[^>]{1,80}>")
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
RE_URL = re.compile(r"https?://\S+")
RE_CAMINHO = re.compile(r"(~|/home/[^/\s]+|/Users/[^/\s]+|[A-Z]:\\Users\\[^\\\s]+)[^\s'\"`)]*")


def limpar_pedido(texto):
    """Tira lembretes de sistema e tags; devolve o texto que a pessoa (ou o timer) pediu."""
    if not isinstance(texto, str):
        return ""
    texto = RE_LEMBRETE.sub(" ", texto)
    if texto.lstrip().startswith(("Caveat:", "[Request interrupted")):
        return ""
    texto = RE_TAG.sub(" ", texto)
    return " ".join(texto.split())


def mascarar(texto, limite=80):
    """Início do pedido para mostrar: sem e-mail, URL nem caminho pessoal, até `limite` caracteres."""
    texto = RE_EMAIL.sub("<email>", texto)
    texto = RE_URL.sub("<url>", texto)
    texto = RE_CAMINHO.sub("<pasta>", texto)
    return texto[:limite]


def normalizar(texto):
    """Chave de comparação: minúsculas, números e datas viram #, sem caminhos/URLs."""
    t = texto.lower()
    t = RE_URL.sub(" ", t)
    t = RE_CAMINHO.sub(" ", t)
    t = re.sub(r"\d+", "#", t)
    t = re.sub(r"[^\w#]+", " ", t)
    return " ".join(t.split())


def chave_rotina(texto, tamanho=80):
    return normalizar(texto)[:tamanho]


def projeto_de(cwd, raizes):
    if not cwd:
        return "(sem pasta)"
    cwd = os.path.normpath(cwd)
    for raiz in raizes:
        r = os.path.normpath(os.path.expanduser(raiz))
        if cwd == r:
            return os.path.basename(r)
        if cwd.startswith(r + os.sep):
            return cwd[len(r) + 1:].split(os.sep)[0]
    if cwd.startswith(("/tmp", "/var/tmp")):
        return "(temporária)"
    if cwd == os.path.expanduser("~"):
        return "(pasta pessoal)"
    return os.path.basename(cwd) or cwd


def zero():
    return {k: 0 for k in CAMPOS}


def peso(tokens, pesos):
    return sum(tokens[k] * pesos[k] for k in CAMPOS)


def total(tokens):
    return sum(tokens[k] for k in CAMPOS)


# --------------------------------------------------------------------------- leitura

class Coletor:
    """Guarda só contadores por sessão e dia — nunca o conteúdo das conversas."""

    def __init__(self, corte, raizes, pesos):
        self.corte = corte
        self.raizes = raizes
        self.pesos = pesos
        self.sessoes = {}
        self.ids = set()
        self.limites = []
        self.arquivos = 0
        self.bytes = 0

    def sessao(self, ferramenta, sid):
        chave = (ferramenta, sid)
        s = self.sessoes.get(chave)
        if s is None:
            s = self.sessoes[chave] = {
                "ferramenta": ferramenta, "id": sid, "cwd": None, "headless": None,
                "inicio": None, "pedido": "", "por_dia": {}, "eventos": [],
            }
        return s

    def somar(self, s, dt, tokens):
        if dt is None or dt < self.corte:
            return
        dia = dia_local(dt)
        d = s["por_dia"].setdefault(dia, zero())
        for k in CAMPOS:
            d[k] += tokens[k]
        # pontos (horário, peso) para medir a janela semanal desde o reset
        s["eventos"].append((dt.timestamp(), peso(tokens, self.pesos[s["ferramenta"]])))
        if s["inicio"] is None or dt < s["inicio"]:
            s["inicio"] = dt

    def limite(self, ferramenta, dt, texto):
        if dt is None:
            return
        m = re.search(r"hit your (weekly |session |usage )?limit", texto, re.I)
        tipo = (m.group(1) or "geral").strip().lower() if m else "geral"
        if tipo == "usage":
            tipo = "geral"
        reset = re.search(r"resets ([^\n\"]{1,60})", texto)
        self.limites.append({"ferramenta": ferramenta, "tipo": tipo, "quando": dt,
                             "reset": reset.group(1).strip() if reset else None})


def texto_de(conteudo):
    if isinstance(conteudo, str):
        return conteudo
    if isinstance(conteudo, list):
        partes = []
        for b in conteudo:
            if isinstance(b, dict) and b.get("type") in ("text", "input_text"):
                partes.append(b.get("text") or "")
        return "\n".join(partes)
    return ""


def ler_claude(arquivo, col):
    eh_sub = os.sep + "subagents" + os.sep in arquivo
    achou = eh_sub  # subagente: o pedido que vale é o da sessão principal
    with open(arquivo, "rb") as f:
        for linha in f:
            col.bytes += len(linha)
            tem_uso = b'"usage"' in linha and b'"assistant"' in linha
            tem_lim = b'rate_limit' in linha
            tem_user = (not achou) and b'"user"' in linha and b'"tool_result"' not in linha[:4000]
            if not (tem_uso or tem_lim or tem_user):
                continue
            try:
                r = json.loads(linha)
            except ValueError:
                continue
            if not isinstance(r, dict):
                continue
            sid = r.get("sessionId")
            if not sid:
                continue
            tipo = r.get("type")
            s = col.sessao("claude", sid)
            if not r.get("isSidechain"):
                if r.get("cwd") and not s["cwd"]:
                    s["cwd"] = r["cwd"]
                if r.get("entrypoint") and s["headless"] is None:
                    s["headless"] = r["entrypoint"] == "sdk-cli"
            msg = r.get("message") if isinstance(r.get("message"), dict) else {}
            dt = quando(r.get("timestamp"))
            if tipo == "assistant":
                if r.get("error") == "rate_limit" or (r.get("isApiErrorMessage") and tem_lim):
                    col.limite("claude", dt, texto_de(msg.get("content")))
                    continue
                uso = msg.get("usage")
                if not isinstance(uso, dict) or msg.get("model") == "<synthetic>":
                    continue
                mid = msg.get("id") or r.get("requestId") or r.get("uuid")
                if mid:
                    if mid in col.ids:  # a mesma resposta aparece em várias linhas
                        continue
                    col.ids.add(mid)
                col.somar(s, dt, {
                    "novos": uso.get("input_tokens") or 0,
                    "cache_le": uso.get("cache_read_input_tokens") or 0,
                    "cache_esc": uso.get("cache_creation_input_tokens") or 0,
                    "saida": uso.get("output_tokens") or 0,
                })
            elif tipo == "user" and not eh_sub and not r.get("isSidechain") and not s["pedido"]:
                pedido = limpar_pedido(texto_de(msg.get("content")))
                if pedido:
                    s["pedido"] = pedido[:600]
                    achou = True


def ler_codex(arquivo, col):
    sid = None
    s = None
    anterior = None
    with open(arquivo, "rb") as f:
        for linha in f:
            col.bytes += len(linha)
            if not (b'"session_meta"' in linha or b'"token_count"' in linha
                    or b'"user_message"' in linha or b'usage limit' in linha):
                continue
            try:
                r = json.loads(linha)
            except ValueError:
                continue
            p = r.get("payload") if isinstance(r, dict) else None
            if not isinstance(p, dict):
                continue
            dt = quando(r.get("timestamp"))
            tipo = r.get("type")
            if tipo == "session_meta":
                sid = p.get("id") or p.get("session_id") or os.path.basename(arquivo)
                s = col.sessao("codex", sid)
                s["cwd"] = s["cwd"] or p.get("cwd")
                s["headless"] = p.get("originator") == "codex_exec" or p.get("source") == "exec"
                continue
            if s is None:
                sid = os.path.basename(arquivo)
                s = col.sessao("codex", sid)
            ptipo = p.get("type")
            if tipo == "event_msg" and ptipo == "user_message" and not s["pedido"]:
                s["pedido"] = limpar_pedido(p.get("message") or "")[:600]
            elif tipo == "event_msg" and ptipo == "token_count":
                info = p.get("info")
                if not isinstance(info, dict):
                    continue
                t = info.get("total_token_usage") or {}
                atual = (t.get("input_tokens") or 0, t.get("cached_input_tokens") or 0,
                         t.get("cache_write_input_tokens") or 0, t.get("output_tokens") or 0)
                base = anterior or (0, 0, 0, 0)
                delta = [max(0, a - b) for a, b in zip(atual, base)]
                anterior = atual
                if any(delta):
                    # No Codex, input_tokens JÁ inclui o cache lido; separamos aqui.
                    col.somar(s, dt, {"novos": max(0, delta[0] - delta[1] - delta[2]),
                                      "cache_le": delta[1], "cache_esc": delta[2],
                                      "saida": delta[3]})
            elif b'usage limit' in linha and ptipo in ("error", "stream_error"):
                col.limite("codex", dt, p.get("message") or "")


def pastas_codex(extra=None):
    pastas = []
    for c in (extra or []):
        pastas.append(os.path.expanduser(c))
    if os.environ.get("CODEX_HOME"):
        pastas.append(os.environ["CODEX_HOME"])
    pastas += glob.glob(os.path.expanduser("~/.codex*"))
    vistos, saida = set(), []
    for p in pastas:
        s = os.path.join(os.path.realpath(p), "sessions")
        if os.path.isdir(s) and s not in vistos:
            vistos.add(s)
            saida.append(s)
    return saida


def coletar(dias, cfg, agora=None, pasta_claude=None, pastas_cdx=None, progresso=True):
    agora = agora or datetime.now(timezone.utc)
    corte = agora - timedelta(days=dias)
    col = Coletor(corte, cfg["raizes"], cfg["pesos"])
    corte_mtime = corte.timestamp() - 3600
    pasta_claude = pasta_claude or os.path.expanduser("~/.claude/projects")
    lista = [("claude", f) for f in glob.glob(os.path.join(pasta_claude, "**", "*.jsonl"), recursive=True)]
    if pastas_cdx is None:
        pastas_cdx = pastas_codex(cfg.get("codex_homes"))
    for p in pastas_cdx:
        lista += [("codex", f) for f in glob.glob(os.path.join(p, "**", "*.jsonl"), recursive=True)]
    lista = [(t, f) for t, f in lista if os.path.getmtime(f) >= corte_mtime]
    for i, (t, f) in enumerate(lista, 1):
        try:
            (ler_claude if t == "claude" else ler_codex)(f, col)
        except OSError:
            continue
        col.arquivos += 1
        if progresso and sys.stderr.isatty() and i % 50 == 0:
            print(f"\r  lendo {i}/{len(lista)} arquivos, {col.bytes / 1e9:.1f} GB", end="", file=sys.stderr)
    if progresso and sys.stderr.isatty():
        print("\r" + " " * 60 + "\r", end="", file=sys.stderr)
    col.agora = agora
    return col


# --------------------------------------------------------------------------- análise

def agrupar_repetidos(sessoes, janela_min=60, similar=0.85):
    """Rotinas headless com pedido parecido que começaram no mesmo dia, a menos de
    `janela_min` minutos uma da outra. Timer diário (um por dia) NÃO entra."""
    cands = sorted((s for s in sessoes if s["headless"] and s["inicio"] and s["chave"]),
                   key=lambda s: s["inicio"])
    pai = list(range(len(cands)))

    def raiz(i):
        while pai[i] != i:
            pai[i] = pai[pai[i]]
            i = pai[i]
        return i

    norm = [normalizar(s["pedido"])[:300] for s in cands]
    for i, a in enumerate(cands):
        for j in range(i + 1, len(cands)):
            b = cands[j]
            if (b["inicio"] - a["inicio"]).total_seconds() > janela_min * 60:
                break
            if dia_local(a["inicio"]) != dia_local(b["inicio"]):
                continue
            if norm[i] == norm[j]:
                pai[raiz(j)] = raiz(i)
                continue
            m = difflib.SequenceMatcher(None, norm[i], norm[j])
            if m.real_quick_ratio() >= similar and m.quick_ratio() >= similar and m.ratio() >= similar:
                pai[raiz(j)] = raiz(i)
    grupos = {}
    for i, s in enumerate(cands):
        grupos.setdefault(raiz(i), []).append(s)
    saida = []
    for g in grupos.values():
        if len(g) < 2:
            continue
        pesos = [s["peso"] for s in g]
        saida.append({
            "n": len(g),
            "pedido": g[0]["pedido_curto"],
            "projetos": sorted({s["projeto"] for s in g}),
            "ferramenta": g[0]["ferramenta"],
            "dia": dia_local(g[0]["inicio"]),
            "de": g[0]["inicio"].astimezone().strftime("%H:%M"),
            "ate": g[-1]["inicio"].astimezone().strftime("%H:%M"),
            "identicos": len({normalizar(s["pedido"])[:300] for s in g}) == 1,
            "peso": sum(pesos),
            # piso: cada execução extra paga pelo menos o custo da menor (abrir sessão, instruções, ferramentas)
            "economia": (len(g) - 1) * min(pesos),
            # teto: se fossem o mesmo trabalho, bastaria a maior
            "economia_teto": sum(pesos) - max(pesos),
            "tokens": sum(s["tokens_total"] for s in g),
            "economia_tokens": (len(g) - 1) * min(s["tokens_total"] for s in g),
        })
    saida.sort(key=lambda r: -r["economia"])
    return saida


def consumo_desde(sessoes, ferramenta, desde, ate):
    a, b = desde.timestamp(), ate.timestamp()
    return sum(p for s in sessoes if s["ferramenta"] == ferramenta
               for ts, p in s["eventos"] if a <= ts <= b)


def cor(pct):
    if pct is None:
        return "cinza"
    return "verde" if pct < 70 else ("amarelo" if pct < 90 else "vermelho")


def analisar(col, cfg, dias, anonimo=False, janela_min=60, similar=0.85):
    agora = col.agora
    sessoes = []
    for (ferr, sid), s in col.sessoes.items():
        tokens = zero()
        for d in s["por_dia"].values():
            for k in CAMPOS:
                tokens[k] += d[k]
        if not total(tokens):
            continue
        s = dict(s)
        s["tokens"] = tokens
        s["tokens_total"] = total(tokens)
        s["peso"] = peso(tokens, cfg["pesos"][ferr])
        s["projeto"] = projeto_de(s["cwd"], cfg["raizes"])
        s["headless"] = bool(s["headless"])
        s["chave"] = chave_rotina(s["pedido"]) if s["pedido"] else ""
        s["pedido_curto"] = mascarar(s["pedido"]) if s["pedido"] else "(sem pedido de texto)"
        sessoes.append(s)

    # nomes privados -> privado-N (só no modo anônimo)
    publicos = {p.lower() for p in cfg.get("publicos", [])}
    apelidos = {}

    def nome(p):
        if not anonimo or p.lower() in publicos:
            return p
        if p.startswith("("):
            return p + " "  # pasta genérica: nome aparece, pedido não
        if p not in apelidos:
            apelidos[p] = f"privado-{len(apelidos) + 1}"
        return apelidos[p]

    soma_peso = sum(s["peso"] for s in sessoes) or 1
    por_ferr = {}
    for s in sessoes:
        f = por_ferr.setdefault(s["ferramenta"], {"tokens": zero(), "peso": 0, "sessoes": 0, "headless": 0,
                                                  "peso_headless": 0})
        for k in CAMPOS:
            f["tokens"][k] += s["tokens"][k]
        f["peso"] += s["peso"]
        f["sessoes"] += 1
        if s["headless"]:
            f["headless"] += 1
            f["peso_headless"] += s["peso"]
    for f in por_ferr.values():
        f["tokens_total"] = total(f["tokens"])

    dias_lista = sorted({d for s in sessoes for d in s["por_dia"]}
                        | {dia_local(agora - timedelta(days=i)) for i in range(dias)})
    por_dia = []
    for d in dias_lista:
        linha = {"dia": d}
        for ferr in ("claude", "codex"):
            tk = zero()
            for s in sessoes:
                if s["ferramenta"] == ferr and d in s["por_dia"]:
                    for k in CAMPOS:
                        tk[k] += s["por_dia"][d][k]
            linha[ferr] = {"tokens_total": total(tk), "peso": peso(tk, cfg["pesos"][ferr])}
        por_dia.append(linha)

    def top(chave_fn, rotulo_fn, filtro=lambda s: True, n=10):
        grupos = {}
        for s in sessoes:
            if not filtro(s):
                continue
            k = chave_fn(s)
            g = grupos.setdefault(k, {"rotulo": rotulo_fn(s), "peso": 0, "tokens": 0, "sessoes": 0,
                                      "ferramentas": set(), "projetos": set()})
            g["peso"] += s["peso"]
            g["tokens"] += s["tokens_total"]
            g["sessoes"] += 1
            g["ferramentas"].add(s["ferramenta"])
            g["projetos"].add(nome(s["projeto"]))
        lista = sorted(grupos.values(), key=lambda g: -g["peso"])[:n]
        for g in lista:
            g["pct"] = round(100 * g["peso"] / soma_peso, 1)
            g["ferramentas"] = sorted(g["ferramentas"])
            g["projetos"] = sorted(g["projetos"])
        return lista

    projetos = top(lambda s: s["projeto"], lambda s: nome(s["projeto"]))
    rotinas = top(lambda s: (s["ferramenta"], s["projeto"], s["chave"]),
                  lambda s: rotulo_pedido(s, nome, anonimo),
                  filtro=lambda s: s["headless"] and s["chave"], n=10_000)
    rotinas = [r for r in rotinas if r["sessoes"] >= 2][:10]  # rotina = repetiu na janela
    for r in rotinas:
        r["por_dia"] = round(r["sessoes"] / max(1, dias), 1)
    top_sessoes = top(lambda s: (s["ferramenta"], s["id"]),
                      lambda s: rotulo_pedido(s, nome, anonimo))

    repetidos = agrupar_repetidos(sessoes, janela_min, similar)
    if anonimo:
        for r in repetidos:
            r["projetos"] = [nome(p) for p in r["projetos"]]
            if all(p.startswith("privado-") or p.endswith(" ") for p in r["projetos"]):
                r["pedido"] = "(pedido de projeto privado)"

    # projeção e teto por ferramenta
    medidores = {}
    for ferr, f in por_ferr.items():
        media = f["peso"] / max(1, dias)
        proj = media * 7
        teto = (cfg["tetos"].get(ferr) or {}).get("peso")
        ativos = len({d for s in sessoes if s["ferramenta"] == ferr for d in s["por_dia"]}) or 1
        ritmo = f["peso"] / ativos * 7
        m = {"media_dia": media, "projecao_semana": proj, "teto": teto,
             "pct_projecao": round(100 * proj / teto, 1) if teto else None,
             "dias_com_uso": ativos, "projecao_ritmo": ritmo,
             "pct_ritmo": round(100 * ritmo / teto, 1) if teto else None}
        reset = (cfg["tetos"].get(ferr) or {}).get("reset")
        if teto and reset:
            ini = ultimo_reset(reset, agora)
            if ini and ini < col.corte:
                m["aviso"] = (f"a semana começou em {ini.astimezone():%Y-%m-%d %H:%M}, antes da janela lida;"
                              f" use --dias {max(7, (agora - ini).days + 1)} para o ponteiro da semana")
            if ini and ini >= col.corte:
                usado = consumo_desde(sessoes, ferr, ini, agora)
                m["semana_desde"] = ini.astimezone().strftime("%Y-%m-%d %H:%M")
                m["usado_semana"] = usado
                m["pct_semana"] = round(100 * usado / teto, 1)
        m["cor"] = "cinza" if m.get("aviso") else cor(m.get("pct_semana", m["pct_projecao"]))
        medidores[ferr] = m

    limites = {}
    for ev in col.limites:
        if ev["quando"] < col.corte:
            continue
        chave = (ev["ferramenta"], ev["tipo"], ev["quando"].strftime("%Y-%m-%dT%H:%M"))
        limites[chave] = ev
    resumo_lim = {}
    for (ferr, tipo, _), ev in sorted(limites.items()):
        r = resumo_lim.setdefault(f"{ferr}:{tipo}", {"ferramenta": ferr, "tipo": tipo, "vezes": 0,
                                                       "horas": set(), "ultimo": None})
        r["vezes"] += 1
        r["horas"].add(ev["quando"].astimezone().strftime("%Y-%m-%d %H"))
        r["ultimo"] = ev["quando"].astimezone().strftime("%Y-%m-%d %H:%M")
    for r in resumo_lim.values():
        r["horas_distintas"] = len(r.pop("horas"))

    return {
        "versao": VERSAO,
        "medido_em": agora.astimezone().strftime("%Y-%m-%d %H:%M %z"),
        "dias": dias,
        "arquivos": col.arquivos,
        "gb_lidos": round(col.bytes / 1e9, 2),
        "anonimo": anonimo,
        "pesos": cfg["pesos"],
        "ferramentas": por_ferr,
        "por_dia": por_dia,
        "projetos": projetos,
        "rotinas": rotinas,
        "sessoes": top_sessoes,
        "repetidos": repetidos[:30],
        "repetidos_total": {
            "grupos": len(repetidos),
            "execucoes": sum(r["n"] for r in repetidos),
            "economia_peso": sum(r["economia"] for r in repetidos),
            "economia_tokens": sum(r["economia_tokens"] for r in repetidos),
            "pct": round(100 * sum(r["economia"] for r in repetidos) / soma_peso, 1),
            "economia_teto": sum(r["economia_teto"] for r in repetidos),
            "pct_teto": round(100 * sum(r["economia_teto"] for r in repetidos) / soma_peso, 1),
            "peso": sum(r["peso"] for r in repetidos),
            "pct_peso": round(100 * sum(r["peso"] for r in repetidos) / soma_peso, 1),
            "janela_min": janela_min, "similaridade": similar,
        },
        "medidores": medidores,
        "limites": list(resumo_lim.values()),
        "peso_total": soma_peso,
    }


def rotulo_pedido(s, nome, anonimo):
    p = nome(s["projeto"])
    if anonimo and (p.startswith("privado-") or p.endswith(" ")):
        h = hashlib.sha1(s["chave"].encode()).hexdigest()[:4]
        return f"{p} · rotina {h}" if s["headless"] else f"{p} · sessão"
    return f"{p} · {s['pedido_curto']}"


# --------------------------------------------------------------------------- calibrar

DIAS_SEMANA = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6,
               "seg": 0, "ter": 1, "qua": 2, "qui": 3, "sex": 4, "sab": 5, "dom": 6}


def interpretar_reset(texto, hit):
    """'9pm (America/Sao_Paulo)' ou 'Mon 9:10pm (...)' -> datetime do próximo reset após `hit`."""
    if not texto:
        return None
    m = re.search(r"(?:(\w{3})\w*\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)", texto, re.I)
    if not m:
        return None
    hora = int(m.group(2)) % 12 + (12 if m.group(4).lower() == "pm" else 0)
    minuto = int(m.group(3) or 0)
    fuso = re.search(r"\(([\w/+-]+)\)", texto)
    tz = None
    if fuso and ZoneInfo:
        try:
            tz = ZoneInfo(fuso.group(1))
        except Exception:
            tz = None
    local = hit.astimezone(tz) if tz else hit.astimezone()
    alvo = local.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    dia = DIAS_SEMANA.get((m.group(1) or "").lower()[:3])
    if dia is not None:
        alvo += timedelta(days=(dia - alvo.weekday()) % 7)
    while alvo <= local:
        alvo += timedelta(days=7 if dia is not None else 1)
    return alvo


def ultimo_reset(reset, agora):
    """reset = {'dia_semana': 0-6, 'hora': 'HH:MM', 'fuso': 'America/Sao_Paulo'}."""
    try:
        tz = ZoneInfo(reset.get("fuso")) if (ZoneInfo and reset.get("fuso")) else None
    except Exception:
        tz = None
    local = agora.astimezone(tz) if tz else agora.astimezone()
    h, m = (int(x) for x in reset["hora"].split(":"))
    alvo = local.replace(hour=h, minute=m, second=0, microsecond=0)
    alvo -= timedelta(days=(alvo.weekday() - reset["dia_semana"]) % 7)
    if alvo > local:
        alvo -= timedelta(days=7)
    return alvo


def calibrar_por_limites(col, cfg):
    """Para cada 'weekly limit' nos registros: consumo desde o início daquela semana até o corte."""
    sessoes = [dict(s, ferramenta=s["ferramenta"]) for s in col.sessoes.values()]
    vistos, achados = set(), []
    for ev in sorted(col.limites, key=lambda e: e["quando"]):
        if ev["tipo"] != "weekly":
            continue
        reset = interpretar_reset(ev["reset"], ev["quando"])
        inicio = reset - timedelta(days=7) if reset else ev["quando"] - timedelta(days=7)
        chave = (ev["ferramenta"], inicio.strftime("%Y-%m-%d %H"))
        if chave in vistos:
            continue
        vistos.add(chave)
        usado = consumo_desde(sessoes, ev["ferramenta"], inicio, ev["quando"])
        achados.append({"ferramenta": ev["ferramenta"], "cortou_em": ev["quando"], "reset": reset,
                        "inicio": inicio, "peso": usado,
                        "janela_completa": inicio >= col.corte})
    return achados


# --------------------------------------------------------------------------- saída

def fmt(n):
    n = float(n or 0)
    for lim, suf in ((1e9, " bi"), (1e6, " mi"), (1e3, " mil")):
        if abs(n) >= lim:
            return f"{n / lim:.1f}{suf}".replace(".", ",")
    return f"{n:.0f}"


def relatorio_texto(r):
    L = []
    L.append(f"medidor-de-limite {r['versao']} · últimos {r['dias']} dia(s) · medido em {r['medido_em']}")
    L.append(f"{r['arquivos']} arquivos de sessão lidos ({str(r['gb_lidos']).replace('.', ',')} GB)")
    L.append("")
    for ferr, f in sorted(r["ferramentas"].items()):
        t = f["tokens"]
        L.append(f"{ferr.upper():7} {fmt(f['tokens_total'])} tokens processados · peso {fmt(f['peso'])}"
                 f" · {f['sessoes']} sessões ({f['headless']} robôs/timers)")
        L.append(f"        novos {fmt(t['novos'])} · cache lido {fmt(t['cache_le'])}"
                 f" · cache escrito {fmt(t['cache_esc'])} · saída {fmt(t['saida'])}")
    L.append("")
    for ferr, m in sorted(r["medidores"].items()):
        if m["teto"]:
            base = (f"semana desde {m['semana_desde']}: {m['pct_semana']}% do teto"
                    if m.get("pct_semana") is not None else f"projeção da semana: {m['pct_projecao']}% do teto")
            L.append(f"PONTEIRO {ferr}: [{m['cor'].upper()}] {base} (teto {fmt(m['teto'])})")
            L.append(f"        últimos {r['dias']} dia(s) em ritmo semanal (média × 7): {m['pct_projecao']}% · no ritmo dos"
                     f" {m['dias_com_uso']} dia(s) de calendário com uso: {m['pct_ritmo']}%")
            if m.get("pct_ritmo") and m["pct_ritmo"] >= 100:
                L.append("        ! no ritmo atual, a semana passa do teto")
            if m.get("aviso"):
                L.append(f"        ! {m['aviso']}")
        else:
            L.append(f"PONTEIRO {ferr}: sem teto definido — rode `python3 medir.py calibrar`"
                     f" (projeção da semana: peso {fmt(m['projecao_semana'])})")
    for lim in r["limites"]:
        L.append(f"  ! {lim['ferramenta']} bateu o limite {lim['tipo']} em {lim['horas_distintas']} horas"
                 f" diferentes (último: {lim['ultimo']})")
    L.append("")
    L.append("TOP 10 PROJETOS (pasta)                       % do peso   sessões")
    for g in r["projetos"]:
        L.append(f"  {g['rotulo'][:44]:44} {g['pct']:6.1f}%   {g['sessoes']:5}")
    L.append("")
    L.append("TOP 10 ROTINAS (claude -p / codex exec, agrupadas pelo 1º pedido)")
    for g in r["rotinas"]:
        L.append(f"  {g['pct']:5.1f}%  {g['sessoes']:4}× ({g['por_dia']}/dia)  {'/'.join(g['ferramentas'])}  {g['rotulo'][:90]}")
    L.append("")
    rt = r["repetidos_total"]
    L.append(f"REPETIDOS: {rt['grupos']} grupos, {rt['execucoes']} execuções com pedido parecido"
             f" no mesmo dia a menos de {rt['janela_min']} min.")
    L.append(f"  Esses grupos somam {rt['pct_peso']}% do peso. Juntando cada grupo numa execução só:"
             f" economia de pelo menos {rt['pct']}% ({fmt(rt['economia_tokens'])} tokens);"
             f" até {rt['pct_teto']}% se eram o mesmo trabalho.")
    for g in r["repetidos"][:10]:
        L.append(f"  {g['n']}× {'(igual) ' if g['identicos'] else ''}{g['dia']} {g['de']}–{g['ate']} {g['ferramenta']} [{', '.join(g['projetos'])[:40]}]"
                 f" {g['pedido'][:60]}")
    if r["repetidos"]:
        L.append("  Sugestão: ache o timer que dispara isto (`systemctl --user list-timers`, `crontab -l`)"
                 " e junte ou pause você mesmo. O medidor não mexe em nada.")
    return "\n".join(L)


def para_json(obj):
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(type(obj))


def gerar_html(r, saida):
    with open(os.path.join(AQUI, "medidor.template.html"), encoding="utf-8") as f:
        modelo = f.read()
    dados = json.dumps(r, ensure_ascii=False, default=para_json).replace("</", "<\\/")
    with open(saida, "w", encoding="utf-8") as f:
        f.write(modelo.replace("/*__DADOS__*/null", dados))
    return saida


# --------------------------------------------------------------------------- CLI

def main(argv=None):
    ap = argparse.ArgumentParser(description="Para onde vai o limite da sua assinatura (Claude Code + Codex).")
    ap.add_argument("--versao", action="version", version=VERSAO)
    sub = ap.add_subparsers(dest="cmd")

    def comuns(p, dias=7):
        p.add_argument("--dias", type=int, default=dias, help=f"janela em dias (padrão {dias})")
        p.add_argument("--config", help="arquivo de config (padrão ~/.config/medidor-de-limite/config.json)")
        p.add_argument("--claude-dir", help="pasta dos registros do Claude Code (padrão ~/.claude/projects)")
        p.add_argument("--codex-home", action="append", help="pasta do Codex (repita para várias contas)")
        p.add_argument("--anonimo", action="store_true", help="troca nomes de pastas fora de `publicos` por privado-N")
        p.add_argument("--publicos", help="arquivo com nomes de pasta que podem aparecer no modo anônimo (1 por linha)")
        p.add_argument("--janela", type=int, default=60, help="minutos entre execuções para contar repetido")
        p.add_argument("--similar", type=float, default=0.85, help="similaridade mínima do pedido (0–1)")

    p = sub.add_parser("relatorio", help="relatório no terminal")
    comuns(p)
    p.add_argument("--json", help="grava o resumo também em JSON")
    p = sub.add_parser("html", help="gera medidor.html")
    comuns(p)
    p.add_argument("--saida", default="medidor.html")
    p = sub.add_parser("calibrar", help="define o teto semanal (automático pelos registros ou à mão)")
    comuns(p, dias=35)
    p.add_argument("--ferramenta", choices=["claude", "codex"], default=None)
    p.add_argument("--teto", type=float, help="teto semanal à mão (na unidade 'peso')")
    p.add_argument("--estourou-em", help="AAAA-MM-DD HH:MM em que o limite semanal cortou (se o log não tiver)")
    p.add_argument("--salvar", action="store_true", help="grava o teto no config")
    a = ap.parse_args(argv)
    if not a.cmd:
        ap.print_help()
        return 0

    cfg = carregar_config(a.config)
    if a.publicos:
        with open(os.path.expanduser(a.publicos), encoding="utf-8") as f:
            cfg["publicos"] = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    homes = None
    if a.codex_home:
        homes = pastas_codex(a.codex_home)
    col = coletar(a.dias, cfg, pasta_claude=a.claude_dir and os.path.expanduser(a.claude_dir),
                  pastas_cdx=homes)

    if a.cmd == "calibrar":
        return calibrar(a, cfg, col)
    r = analisar(col, cfg, a.dias, anonimo=a.anonimo, janela_min=a.janela, similar=a.similar)
    if a.cmd == "relatorio":
        print(relatorio_texto(r))
        if a.json:
            with open(a.json, "w", encoding="utf-8") as f:
                json.dump(r, f, ensure_ascii=False, indent=1, default=para_json)
    else:
        print(f"gerado: {gerar_html(r, a.saida)}")
    return 0


def calibrar(a, cfg, col):
    escolha = None
    if a.teto:
        escolha = {"ferramenta": a.ferramenta or "claude", "peso": a.teto, "origem": "à mão"}
    elif a.estourou_em:
        ferr = a.ferramenta or "claude"
        hit = datetime.strptime(a.estourou_em, "%Y-%m-%d %H:%M").astimezone()
        sess = list(col.sessoes.values())
        escolha = {"ferramenta": ferr, "peso": consumo_desde(sess, ferr, hit - timedelta(days=7), hit),
                   "origem": f"7 dias antes de {a.estourou_em} (estimativa por cima)"}
    else:
        achados = calibrar_por_limites(col, cfg)
        if a.ferramenta:
            achados = [x for x in achados if x["ferramenta"] == a.ferramenta]
        if not achados:
            print(f"Nenhum 'weekly limit' nos registros dos últimos {a.dias} dias.\n"
                  "Use --estourou-em 'AAAA-MM-DD HH:MM' (quando o limite cortou) ou --teto N.")
            return 1
        print("Semanas em que o limite semanal cortou (achadas nos seus registros):")
        for x in achados:
            aviso = "" if x["janela_completa"] else "  (semana começa antes da janela lida: aumente --dias)"
            print(f"  {x['ferramenta']}: cortou em {x['cortou_em'].astimezone():%Y-%m-%d %H:%M};"
                  f" semana desde {x['inicio'].astimezone():%Y-%m-%d %H:%M} → peso {fmt(x['peso'])}{aviso}")
        validos = [x for x in achados if x["janela_completa"]] or achados
        x = validos[-1]
        escolha = {"ferramenta": x["ferramenta"], "peso": round(x["peso"]),
                   "origem": f"limite semanal cortou em {x['cortou_em'].astimezone():%Y-%m-%d %H:%M}"}
        if x["reset"]:
            rl = x["reset"]
            escolha["reset"] = {"dia_semana": rl.weekday(), "hora": rl.strftime("%H:%M"),
                                "fuso": getattr(rl.tzinfo, "key", None)}
    print(f"\nTeto sugerido para {escolha['ferramenta']}: peso {fmt(escolha['peso'])} ({escolha['origem']}).")
    if a.salvar:
        cfg["tetos"][escolha["ferramenta"]] = {k: v for k, v in escolha.items() if k != "ferramenta"}
        print(f"Gravado em {salvar_config(cfg)}")
    else:
        print("Para gravar, rode de novo com --salvar.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

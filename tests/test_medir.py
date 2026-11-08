"""Testes sem rede, com registros JSONL sintéticos. Rode: python3 -m pytest -q  (ou python3 -m unittest)"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import medir  # noqa: E402

AGORA = datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc)


def ts(horas_atras):
    return (AGORA - timedelta(hours=horas_atras)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def claude_sessao(pasta, sid, cwd, pedido, horas_atras, respostas, headless=True, nome=None):
    """respostas = lista de (id, input, cache_le, cache_esc, saida). Repete cada linha 2x (como o Claude Code faz)."""
    os.makedirs(pasta, exist_ok=True)
    ep = "sdk-cli" if headless else "cli"
    linhas = [{"type": "user", "sessionId": sid, "cwd": cwd, "entrypoint": ep, "isSidechain": False,
               "timestamp": ts(horas_atras),
               "message": {"role": "user", "content": "<system-reminder>contexto</system-reminder>" + pedido}}]
    for i, (mid, inp, le, esc, out) in enumerate(respostas):
        reg = {"type": "assistant", "sessionId": sid, "cwd": cwd, "entrypoint": ep, "isSidechain": False,
               "timestamp": ts(horas_atras - 0.01 * (i + 1)),
               "message": {"id": mid, "model": "claude-x", "role": "assistant",
                           "content": [{"type": "text", "text": "segredo da conversa"}],
                           "usage": {"input_tokens": inp, "cache_read_input_tokens": le,
                                     "cache_creation_input_tokens": esc, "output_tokens": out}}}
        linhas += [reg, reg]
    with open(os.path.join(pasta, (nome or sid) + ".jsonl"), "w") as f:
        for l in linhas:
            f.write(json.dumps(l) + "\n")


def cfg_teste(tmp):
    cfg = medir.carregar_config(os.path.join(tmp, "nao-existe.json"))
    cfg["raizes"] = ["/p"]
    return cfg


def coletar(tmp, cfg, codex=None, dias=7):
    return medir.coletar(dias, cfg, agora=AGORA, pasta_claude=os.path.join(tmp, "claude"),
                         pastas_cdx=codex or [], progresso=False)


class TestSoma(unittest.TestCase):
    def test_soma_usage_sem_duplicar_e_sem_sintetico(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = os.path.join(tmp, "claude", "-p-proj")
            claude_sessao(pasta, "s1", "/p/proj", "faça X", 5,
                          [("m1", 10, 1000, 200, 50), ("m2", 5, 2000, 0, 30)])
            # mensagem sintética de limite: não soma, mas vira evento de limite
            with open(os.path.join(pasta, "s1.jsonl"), "a") as f:
                f.write(json.dumps({"type": "assistant", "sessionId": "s1", "timestamp": ts(4),
                                    "error": "rate_limit", "isApiErrorMessage": True,
                                    "message": {"id": "x", "model": "<synthetic>", "usage": {"input_tokens": 0},
                                                "content": [{"type": "text", "text":
                                                             "You've hit your weekly limit · resets 9pm (America/Sao_Paulo)"}]}}) + "\n")
            # linha fora da janela (10 dias atrás): não entra
            claude_sessao(pasta, "velha", "/p/proj", "faça X", 240, [("m9", 999, 0, 0, 0)], nome="velha")
            cfg = cfg_teste(tmp)
            col = coletar(tmp, cfg)
            r = medir.analisar(col, cfg, 7)
            t = r["ferramentas"]["claude"]["tokens"]
            self.assertEqual(t, {"novos": 15, "cache_le": 3000, "cache_esc": 200, "saida": 80})
            self.assertEqual(r["ferramentas"]["claude"]["tokens_total"], 3295)
            pesos = medir.PESOS_PADRAO["claude"]
            self.assertAlmostEqual(r["ferramentas"]["claude"]["peso"],
                                   15 * pesos["novos"] + 3000 * pesos["cache_le"] + 200 * pesos["cache_esc"] + 80 * pesos["saida"])
            self.assertEqual(len(col.limites), 1)
            self.assertEqual(col.limites[0]["tipo"], "weekly")
            # nada da conversa vaza para o resumo
            self.assertNotIn("segredo", json.dumps(r, default=medir.para_json))

    def test_codex_input_inclui_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = os.path.join(tmp, "codex", "sessions", "2026", "11", "08")
            os.makedirs(pasta)
            linhas = [
                {"type": "session_meta", "timestamp": ts(3), "payload": {"id": "c1", "cwd": "/p/img", "originator": "codex_exec"}},
                {"type": "event_msg", "timestamp": ts(3), "payload": {"type": "user_message", "message": "Gere 1 imagem"}},
                {"type": "event_msg", "timestamp": ts(2.9), "payload": {"type": "token_count", "info": {
                    "total_token_usage": {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 10}}}},
                {"type": "event_msg", "timestamp": ts(2.8), "payload": {"type": "token_count", "info": {
                    "total_token_usage": {"input_tokens": 3000, "cached_input_tokens": 2500, "output_tokens": 30}}}},
            ]
            with open(os.path.join(pasta, "rollout-c1.jsonl"), "w") as f:
                f.write("\n".join(json.dumps(l) for l in linhas) + "\n")
            os.makedirs(os.path.join(tmp, "claude"))
            cfg = cfg_teste(tmp)
            r = medir.analisar(coletar(tmp, cfg, codex=[os.path.join(tmp, "codex", "sessions")]), cfg, 7)
            t = r["ferramentas"]["codex"]["tokens"]
            self.assertEqual(t, {"novos": 500, "cache_le": 2500, "cache_esc": 0, "saida": 30})
            self.assertEqual(r["ferramentas"]["codex"]["tokens_total"], 3030)  # = input + output do Codex
            self.assertEqual(r["projetos"][0]["rotulo"], "img")


class TestRotinas(unittest.TestCase):
    def test_agrupa_rotina_pela_primeira_mensagem(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = os.path.join(tmp, "claude", "-p-blog")
            # mesmo molde, datas e números diferentes, dias diferentes -> uma rotina com 3 execuções
            for i, d in enumerate((70, 46, 22)):
                claude_sessao(pasta, f"r{i}", "/p/blog", f"Resuma as notícias de 0{i + 5}/11/2026 em 10 itens", d,
                              [(f"r{i}m", 1, 100, 10, 5)])
            claude_sessao(pasta, "outra", "/p/blog", "Escreva um poema", 30, [("om", 1, 100, 10, 5)])
            claude_sessao(pasta, "humano", "/p/blog", "Resuma as notícias de 08/11/2026 em 10 itens", 1,
                          [("hm", 1, 100, 10, 5)], headless=False)
            cfg = cfg_teste(tmp)
            r = medir.analisar(coletar(tmp, cfg), cfg, 7)
            self.assertEqual(len(r["rotinas"]), 1)  # avulsas (1 execução) e sessão interativa não são rotina
            self.assertEqual(r["rotinas"][0]["sessoes"], 3)
            self.assertIn("Resuma as notícias", r["rotinas"][0]["rotulo"])
            self.assertNotIn("system-reminder", r["rotinas"][0]["rotulo"])

    def test_normalizar_e_mascarar(self):
        self.assertEqual(medir.chave_rotina("Edição de 07/11/2026"), medir.chave_rotina("Edição de 08/11/2026"))
        m = medir.mascarar("mande para fulano@x.com o arquivo /home/ana/segredo.txt " + "a" * 200)
        self.assertNotIn("fulano", m)
        self.assertNotIn("/home/ana", m)
        self.assertLessEqual(len(m), 80)


class TestRepetidos(unittest.TestCase):
    def test_repetido_no_mesmo_horario_e_timer_diario_nao(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = os.path.join(tmp, "claude", "-p-x")
            # 3 execuções do mesmo pedido em 20 min no mesmo dia -> 1 grupo de 3
            for i, h in enumerate((5.0, 4.9, 4.7)):
                claude_sessao(pasta, f"d{i}", "/p/x", "Gere o relatório diário de vendas", h,
                              [(f"d{i}m", 0, 1000 * (i + 1), 0, 0)])
            # timer diário: mesmo pedido, 1 por dia -> NÃO é repetido
            for i, h in enumerate((30, 54, 78)):
                claude_sessao(pasta, f"t{i}", "/p/x", "Faça o backup das fotos", h, [(f"t{i}m", 0, 500, 0, 0)])
            cfg = cfg_teste(tmp)
            r = medir.analisar(coletar(tmp, cfg), cfg, 7)
            self.assertEqual(r["repetidos_total"]["grupos"], 1)
            g = r["repetidos"][0]
            self.assertEqual(g["n"], 3)
            self.assertTrue(g["identicos"])
            # piso = (n-1) x menor execução; teto = total - maior
            self.assertEqual(g["economia_tokens"], 2 * 1000)
            self.assertAlmostEqual(g["economia_teto"], (1000 + 2000 + 3000 - 3000) * 0.1)


class TestTeto(unittest.TestCase):
    def test_projecao_semanal_e_cor_do_ponteiro(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = os.path.join(tmp, "claude", "-p-a")
            # 7 dias com 1000 tokens de saída por dia -> peso 5000/dia -> projeção 35000
            for d in range(7):
                claude_sessao(pasta, f"s{d}", "/p/a", f"tarefa {d}", 24 * d + 1, [(f"m{d}", 0, 0, 0, 1000)])
            cfg = cfg_teste(tmp)
            cfg["tetos"]["claude"] = {"peso": 50000}
            r = medir.analisar(coletar(tmp, cfg), cfg, 7)
            m = r["medidores"]["claude"]
            self.assertAlmostEqual(m["projecao_semana"], 35000)
            self.assertEqual(m["pct_projecao"], 70.0)
            self.assertEqual(m["cor"], "amarelo")
            self.assertEqual(medir.cor(50), "verde")
            self.assertEqual(medir.cor(95), "vermelho")
            self.assertEqual(medir.cor(None), "cinza")

    def test_calibrar_pela_semana_que_estourou(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = os.path.join(tmp, "claude", "-p-a")
            hit = AGORA - timedelta(hours=2)
            # reset às 21h de São Paulo (00:00 UTC do dia seguinte): semana começa 7 dias antes desse reset
            reset = medir.interpretar_reset("9pm (America/Sao_Paulo)", hit)
            self.assertEqual(reset.astimezone(timezone.utc), datetime(2026, 11, 9, 0, 0, tzinfo=timezone.utc))
            dentro = (hit - (reset - timedelta(days=7))).total_seconds() / 3600 + 2 - 1   # 1 h depois do início
            fora = dentro + 5                                                        # antes do início: não conta
            claude_sessao(pasta, "a", "/p/a", "x", dentro, [("ma", 0, 0, 0, 100)])
            claude_sessao(pasta, "b", "/p/a", "y", fora, [("mb", 0, 0, 0, 999)])
            with open(os.path.join(pasta, "a.jsonl"), "a") as f:
                f.write(json.dumps({"type": "assistant", "sessionId": "a", "timestamp": hit.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                                    "error": "rate_limit", "isApiErrorMessage": True,
                                    "message": {"model": "<synthetic>", "content": [{"type": "text", "text":
                                                "You've hit your weekly limit · resets 9pm (America/Sao_Paulo)"}]}}) + "\n")
            cfg = cfg_teste(tmp)
            achados = medir.calibrar_por_limites(coletar(tmp, cfg, dias=10), cfg)
            self.assertEqual(len(achados), 1)
            self.assertAlmostEqual(achados[0]["peso"], 100 * 5.0)


if __name__ == "__main__":
    unittest.main()

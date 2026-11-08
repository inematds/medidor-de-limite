# medidor-de-limite

[![Quem comeu seu limite](guia/assets/banner.jpg)](https://inematds.github.io/medidor-de-limite/guia/)

**🇧🇷 [Português](README.md) · 🇺🇸 [English](README.en.md) · 🇪🇸 [Español](README.es.md)**

> Seu limite acabou na quarta? Veja quem comeu.

## O que é

Um medidor que lê os registros que o Claude Code e o Codex já gravam no seu computador e mostra para onde foi o limite da sua assinatura. Serve para quem roda muitas sessões, robôs e timers (`claude -p`, `codex exec`) e bate o limite da semana sem saber quem gastou. Ele mostra um ponteiro verde, amarelo ou vermelho, os projetos e rotinas que mais gastam e as execuções repetidas que dava para juntar. Precisa só de Python 3; não usa internet, chave nem API, e não apaga nem pausa nada.

## 📖 Guia de uso

Guia completo (landing + passo a passo): **https://inematds.github.io/medidor-de-limite/guia/**

## Em 3 comandos

```bash
git clone https://github.com/inematds/medidor-de-limite.git
cd medidor-de-limite
python3 medir.py relatorio
```

```bash
python3 medir.py calibrar --salvar
python3 medir.py html --saida medidor.html
```

## O que ele lê

| Ferramenta | Onde | O que soma |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` (inclui subagentes) | `usage` de cada resposta: `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`. A mesma resposta aparece em várias linhas: conta uma vez só (pelo `message.id`). |
| Codex | `$CODEX_HOME/sessions` e `~/.codex*/sessions` (uma pasta por conta) | `total_token_usage` dos eventos `token_count`, em diferença. No Codex o `input_tokens` já inclui o cache lido; o medidor separa. |

Agrupa por **dia**, **projeto** (a pasta onde a sessão rodou; a primeira pasta dentro de `~/projetos`, `~/projects`, `~/code`…), **sessão** e **rotina**: sessões sem pessoa no teclado (Claude `entrypoint=sdk-cli`, ou seja `claude -p` e bots pelo SDK; Codex `codex exec`) agrupadas pela primeira mensagem, com números e datas trocados por `#`. Rotina é o que rodou 2 vezes ou mais na janela.

## Duas medidas, sem esconder nada

- **Tokens processados**: tudo o que passou pelo modelo (novos + cache lido + cache escrito + saída). É o número grande, e quase todo ele é cache lido.
- **Peso**: os mesmos tokens com o peso que o preço público dá a cada tipo (Claude: novos 1, cache escrito 1,25, cache lido 0,1, saída 5; Codex: novos 1, cache lido 0,1, saída 8). O ponteiro usa o peso, porque ler cache custa bem menos que escrever resposta. Os pesos estão no config e você pode trocar.

## O teto é seu, não do plano

O plano **não publica o limite em tokens**. Por isso o teto é calibrado com os seus próprios registros:

- `python3 medir.py calibrar` procura nos registros a mensagem "You've hit your weekly limit · resets …". Achou: soma o peso desde o início daquela semana (7 dias antes do reset) até o corte, e esse é o teto. Também guarda o dia e a hora do reset, para o ponteiro medir a semana corrente.
- Não achou: `python3 medir.py calibrar --estourou-em "2026-10-26 18:00" --salvar` (estimativa por cima: 7 dias antes do corte) ou `--teto N` à mão.
- Uso no site, no app ou em outra máquina conta no limite, mas não está nos registros daqui. O teto calibrado já embute isso, desde que o seu jeito de usar não mude muito.
- O Codex quase nunca grava o corte nos registros; o teto dele costuma ficar à mão.

O ponteiro fica **verde** abaixo de 70% do teto, **amarelo** de 70% a 90% e **vermelho** acima, sempre com o rótulo escrito.

## Repetidos

Duas ou mais sessões de robô com o pedido inicial parecido (similaridade ≥ 0,85, ajustável) que começaram **no mesmo dia, a menos de 60 minutos uma da outra**. Timer diário que roda uma vez por dia não entra. Para cada grupo o medidor mostra duas economias:

- **pelo menos** (n − 1) × a menor execução: o custo fixo de abrir cada sessão (instruções, ferramentas, contexto);
- **até** o total menos a maior execução, se as execuções faziam o mesmo trabalho.

O medidor só sugere. Quem decide juntar ou pausar o timer é você (`systemctl --user list-timers`, `crontab -l`).

## Exemplo real (máquina do INEMA, 08/11/2026)

Em `exemplos/`: `relatorio.txt`, `relatorio.json`, `medidor.html` e prints em 1440 px e 390 px. Gerado com `--anonimo`: pastas de projetos privados viram `privado-N` e os pedidos delas não aparecem.

- Claude Code: 4,2 bilhões de tokens processados em 7 dias (96% cache lido), 1.055 sessões, 996 de robôs e timers.
- Codex: 186 milhões de tokens, 1.027 sessões, todas `codex exec`.
- Ponteiro do Claude: **amarelo, 80,6% do teto** na semana que começou segunda 21h; no ritmo dos dias com uso, a semana fecha em 102%.
- Uma só pasta respondeu por 59% do peso da semana.
- 91 grupos de repetidos (701 execuções); juntar cada grupo economiza pelo menos 8,6% do peso.

## Opções

```bash
python3 medir.py relatorio --dias 14
python3 medir.py relatorio --json resumo.json
python3 medir.py html --anonimo --publicos publicos.txt --saida medidor.html
python3 medir.py relatorio --codex-home ~/.codex-2
python3 medir.py relatorio --janela 30 --similar 0.9
```

Config em `~/.config/medidor-de-limite/config.json` (tetos, pesos, `raizes` das pastas de projeto, `publicos`, `codex_homes`).

## O processo, do início ao fim

| # | Passo | Comando | Quem faz |
|---|---|---|---|
| 1 | Baixar | `git clone https://github.com/inematds/medidor-de-limite.git` | 🧠 você |
| 2 | Primeira leitura (7 dias) | `python3 medir.py relatorio` | ⚙️ script |
| 3 | Achar o teto na semana que estourou | `python3 medir.py calibrar --salvar` | ⚙️ script (você confere) |
| 4 | Ver o ponteiro e as barras | `python3 medir.py html` e abrir `medidor.html` | ⚙️ script |
| 5 | Ler o top 10 e os repetidos | — | 🧠 você |
| 6 | Juntar ou pausar timers repetidos | `systemctl --user list-timers` | 🧠 você (o medidor nunca mexe) |
| 7 | Repetir toda semana (opcional) | timer seu que roda o passo 4 | ⚙️ timer |

## Testes

```bash
python3 -m pytest -q
```

Sem rede, com registros JSONL sintéticos: soma de usage (sem duplicar e sem as mensagens de erro), Codex com cache dentro do input, rotina pela primeira mensagem, repetidos × timer diário, projeção e cor do ponteiro, calibragem pela semana do corte.

## Privacidade

Só leitura. Não imprime o conteúdo das conversas: só números e o início do pedido (até 80 caracteres, sem e-mail, URL nem caminho pessoal). Nada sai do seu computador.

---

Feito pelo [INEMA.CLUB](https://inema.club) · conteúdo aberto e gratuito · licença MIT

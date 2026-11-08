# medidor-de-limite

[![Who used up your limit?](guia/assets/banner-en.jpg)](https://inematds.github.io/medidor-de-limite/guia/en/)

**🇧🇷 [Português](README.md) · 🇺🇸 [English](README.en.md) · 🇪🇸 [Español](README.es.md)**

> Hit your limit by Wednesday? See who used it up.

## What it is

A usage meter that reads the logs Claude Code and Codex already save on your computer and shows where your subscription limit went. It’s for people who run lots of sessions, bots, and timers (`claude -p`, `codex exec`) and hit their weekly limit without knowing what used it up. It shows a green, yellow, or red indicator, the projects and routines that use the most, and repeated runs that could be combined. It only needs Python 3; it doesn’t use the internet, keys, or APIs, and it doesn’t delete or pause anything.

## 📖 User guide

Full guide (landing page + walkthrough): **https://inematds.github.io/medidor-de-limite/guia/en/**

## Quick start

```bash
git clone https://github.com/inematds/medidor-de-limite.git
cd medidor-de-limite
python3 medir.py relatorio
```

```bash
python3 medir.py calibrar --salvar
python3 medir.py html --saida medidor.html
```

## What it reads

| Tool | Where | What it counts |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` (includes subagents) | The `usage` from each response: `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`. The same response can appear on multiple lines; it’s counted only once (by `message.id`). |
| Codex | `$CODEX_HOME/sessions` and `~/.codex*/sessions` (one folder per account) | `total_token_usage` from `token_count` events, as a difference. In Codex, `input_tokens` already includes the cache read; the meter separates it. |

Groups usage by **day**, **project** (the folder where the session ran; the first folder inside `~/projetos`, `~/projects`, `~/code`…), **session**, and **routine**: sessions without a person at the keyboard (Claude `entrypoint=sdk-cli`, meaning `claude -p` and SDK bots; Codex `codex exec`) grouped by their first message, with numbers and dates replaced by `#`. A routine is something that ran 2 or more times in the time window.

## Two measures, nothing hidden

- **Processed tokens**: everything that went through the model (new tokens + cache read + cache write + output). This is the larger number, and almost all of it is cache read.
- **Weight**: the same tokens weighted according to the public price for each type (Claude: new 1, cache write 1.25, cache read 0.1, output 5; Codex: new 1, cache read 0.1, output 8). The indicator uses weight because reading cache costs much less than generating a response. The weights are in the config and you can change them.

## The ceiling is yours, not the plan’s

The plan **doesn’t publish its limit in tokens**. So the ceiling is calibrated from your own logs:

- `python3 medir.py calibrar` searches the logs for the message "You've hit your weekly limit · resets …". If it finds one, it adds up the weight from the start of that week (7 days before the reset) up to the cutoff, and uses that as the ceiling. It also saves the reset date and time so the indicator can measure the current week.
- If it doesn’t find one: run `python3 medir.py calibrar --estourou-em "2026-10-26 18:00" --salvar` (a rough estimate: 7 days before the cutoff), or set `--teto N` manually.
- Usage on the website, in the app, or on another machine counts toward the limit but isn’t in these logs. The calibrated ceiling already accounts for it, as long as your usage habits don’t change much.
- The weekly gauge needs the window to cover the time since the last reset: with a smaller `--dias`, it warns you and falls back to the projection.
- Codex almost never saves the cutoff in its logs; its ceiling usually has to be set manually.

The indicator is **green** below 70% of the ceiling, **yellow** from 70% to 90%, and **red** above 90%, always with a written label.

## Repeated runs

Two or more bot sessions with similar initial requests (similarity ≥ 0.85, adjustable) that started **on the same day, less than 60 minutes apart**. A daily timer that runs once a day isn’t included. For each group, the meter shows two potential savings:

- **at least** (n − 1) × the smallest run: the fixed cost of opening each session (instructions, tools, context);
- **up to** the total minus the largest run, if the runs did the same work.

The meter only makes suggestions. You decide whether to combine runs or pause the timer (`systemctl --user list-timers`, `crontab -l`).

## Real-world example (INEMA machine, 08/11/2026)

In `exemplos/`: `relatorio.txt`, `relatorio.json`, `medidor.html`, and screenshots at 1440 px and 390 px. Live: https://inematds.github.io/medidor-de-limite/exemplos/medidor.html. Generated with `--anonimo`: private project folders become `privado-N`, and their requests aren’t shown.

- Claude Code: 4.3 billion processed tokens in 7 days (96% cache read), 1,059 sessions, 999 from bots and timers.
- Codex: 189 million tokens, 1,039 sessions, all `codex exec`.
- Claude indicator: **yellow, 81.9% of the ceiling** in the week that started Monday at 9 p.m.; at the pace of days with usage, the week will end at 103%.
- One folder accounted for 60% of the weight over the 7 days.
- 91 groups of repeated runs (699 runs); combining each group would save at least 8.6% of the weight.

## Options

```bash
python3 medir.py relatorio --dias 14
python3 medir.py relatorio --json resumo.json
python3 medir.py html --anonimo --publicos publicos.txt --saida medidor.html
python3 medir.py relatorio --codex-home ~/.codex-2
python3 medir.py relatorio --janela 30 --similar 0.9
```

Config at `~/.config/medidor-de-limite/config.json` (ceilings, weights, project folder `raizes`, `publicos`, `codex_homes`).

## The process, from start to finish

| # | Step | Command | Who does it |
|---|---|---|---|
| 1 | Download | `git clone https://github.com/inematds/medidor-de-limite.git` | 🧠 you |
| 2 | First read (7 days) | `python3 medir.py relatorio` | ⚙️ script |
| 3 | Find the ceiling in the week you hit it | `python3 medir.py calibrar --salvar` | ⚙️ script (you check) |
| 4 | View the indicator and bars | `python3 medir.py html` and open `medidor.html` | ⚙️ script |
| 5 | Review the top 10 and repeated runs | — | 🧠 you |
| 6 | Combine or pause repeated timers | `systemctl --user list-timers` | 🧠 you (the meter never changes anything) |
| 7 | Repeat weekly (optional) | your own timer that runs step 4 | ⚙️ timer |

## Tests

```bash
python3 -m unittest discover tests
```

With pytest installed (`pip install pytest`), `python3 -m pytest -q` also works.

No network access; uses synthetic JSONL logs: usage totals (without duplicates or error messages), Codex cache included in input, routines grouped by first message, repeated runs vs. daily timers, projections and indicator color, calibration by the week of the cutoff.

## Privacy

Read only. It doesn’t print conversation contents: only numbers and the start of the request (up to 80 characters, without email, URL, or personal path). Nothing leaves your computer.

---

Made by [INEMA.CLUB](https://inema.club) · open and free content · MIT License
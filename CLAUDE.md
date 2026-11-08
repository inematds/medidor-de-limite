# CLAUDE.md — medidor-de-limite

Medidor de para onde vai o limite da assinatura (Claude Code + Codex). Um arquivo só (`medir.py`, stdlib),
página em `medidor.template.html`, testes em `tests/`, guia em `guia/` (PT) + `guia/en/` + `guia/es/`.

## Regras do projeto
- Só leitura: o medidor nunca pausa, apaga nem envia nada. Sugestões são texto.
- Nunca imprimir conteúdo de conversa: só números e o início do pedido (≤ 80 caracteres, mascarado).
- Exemplo publicado (`exemplos/`) sempre com `--anonimo --publicos <lista>`; conferir com grep por nomes de pasta privados antes do commit.
- O plano não publica o limite em tokens: o teto vem de `calibrar` (registros do usuário) ou é à mão. Não escrever "limite oficial".
- Projeto sem fonte: não citar material de referência, comunidade ou autor que inspirou o kit.
- Autor dos commits: `inematds <inematds@gmail.com>`.
- Testes sem rede: `python3 -m unittest discover tests` (ou `python3 -m pytest -q`).

## Self-learning

When I correct you, or you catch yourself making a mistake: before continuing, add the lesson as a one-line rule at the TOP of the Lessons list below, so it never happens again.

## Lessons
- Busca por "hit your weekly limit" com grep acha também a saída gravada da própria sessão que fez o grep; contar só mensagem `assistant` com `error=rate_limit` (08/11/2026).
- Máquina desligada vira dia sem uso: a projeção "média × 7" cai; mostrar também "no ritmo dos dias com uso" (08/11/2026).
- Lote do mesmo molde (46 resumos em 12 min) não é trabalho duplicado: economia principal = (n−1) × menor execução, não total − maior (08/11/2026).

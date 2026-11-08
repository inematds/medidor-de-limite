# medidor-de-limite

[![Quién consumió tu límite](guia/assets/banner-es.jpg)](https://inematds.github.io/medidor-de-limite/guia/es/)

**🇧🇷 [Português](README.md) · 🇺🇸 [English](README.en.md) · 🇪🇸 [Español](README.es.md)**

> ¿Se te acabó el límite el miércoles? Mira quién lo consumió.

## Qué es

Un medidor que lee los registros que Claude Code y Codex ya guardan en tu computadora y muestra en qué se usó el límite de tu suscripción. Sirve para quienes ejecutan muchas sesiones, robots y temporizadores (`claude -p`, `codex exec`) y llegan al límite semanal sin saber quién lo consumió. Muestra un indicador verde, amarillo o rojo, los proyectos y rutinas que más consumen, y las ejecuciones repetidas que se podrían agrupar. Solo necesita Python 3; no usa internet, claves ni API, y no borra ni pausa nada.

## 📖 Guía de uso

Guía completa (página de inicio + paso a paso): **https://inematds.github.io/medidor-de-limite/guia/es/**

## En 3 comandos

```bash
git clone https://github.com/inematds/medidor-de-limite.git
cd medidor-de-limite
python3 medir.py relatorio
```

```bash
python3 medir.py calibrar --salvar
python3 medir.py html --saida medidor.html
```

## Qué lee

| Herramienta | Dónde | Qué suma |
|---|---|---|
| Claude Code | `~/.claude/projects/**/*.jsonl` (incluye subagentes) | `usage` de cada respuesta: `input_tokens`, `cache_read_input_tokens`, `cache_creation_input_tokens`, `output_tokens`. La misma respuesta aparece en varias líneas: se cuenta una sola vez (por `message.id`). |
| Codex | `$CODEX_HOME/sessions` y `~/.codex*/sessions` (una carpeta por cuenta) | `total_token_usage` de los eventos `token_count`, por diferencia. En Codex, `input_tokens` ya incluye la caché leída; el medidor la separa. |

Agrupa por **día**, **proyecto** (la carpeta donde se ejecutó la sesión; la primera carpeta dentro de `~/projetos`, `~/projects`, `~/code`…), **sesión** y **rutina**: las sesiones sin una persona frente al teclado (Claude `entrypoint=sdk-cli`, es decir, `claude -p` y bots por el SDK; Codex `codex exec`) se agrupan por el primer mensaje, reemplazando números y fechas por `#`. Una rutina es algo que se ejecutó 2 veces o más durante el período.

## Dos medidas, sin ocultar nada

- **Tokens procesados**: todo lo que pasó por el modelo (nuevos + caché leída + caché escrita + salida). Es el número grande, y casi todo es caché leída.
- **Peso**: los mismos tokens con el peso que el precio público asigna a cada tipo (Claude: nuevos 1, caché escrita 1,25, caché leída 0,1, salida 5; Codex: nuevos 1, caché leída 0,1, salida 8). El indicador usa el peso, porque leer la caché cuesta mucho menos que generar una respuesta. Los pesos están en la configuración y puedes cambiarlos.

## El límite es tuyo, no del plan

El plan **no publica el límite en tokens**. Por eso, el límite se calibra con tus propios registros:

- `python3 medir.py calibrar` busca en los registros el mensaje "You've hit your weekly limit · resets …". Si lo encuentra: suma el peso desde el inicio de esa semana (7 días antes del reinicio) hasta el momento del corte, y ese es el límite. También guarda el día y la hora del reinicio para que el indicador mida la semana actual.
- Si no lo encuentra: `python3 medir.py calibrar --estourou-em "2026-10-26 18:00" --salvar` (estimación conservadora: 7 días antes del corte) o `--teto N` manualmente.
- El uso en el sitio, en la app o en otra máquina cuenta para el límite, pero no aparece en estos registros. El límite calibrado ya lo contempla, siempre que tu forma de usarlo no cambie mucho.
- Codex casi nunca guarda el corte en los registros; su límite suele establecerse manualmente.

El indicador queda **verde** por debajo del 70% del límite, **amarillo** del 70% al 90% y **rojo** por encima, siempre con una etiqueta escrita.

## Repetidos

Dos o más sesiones de robots con solicitudes iniciales parecidas (similitud ≥ 0,85, ajustable) que comenzaron **el mismo día y con menos de 60 minutos de diferencia**. Un temporizador diario que se ejecuta una vez al día no cuenta. Para cada grupo, el medidor muestra dos posibles ahorros:

- **como mínimo** (n − 1) × la ejecución más pequeña: el costo fijo de abrir cada sesión (instrucciones, herramientas, contexto);
- **hasta** el total menos la ejecución más grande, si las ejecuciones hacían el mismo trabajo.

El medidor solo sugiere. Tú decides si agrupas las ejecuciones o pausas el temporizador (`systemctl --user list-timers`, `crontab -l`).

## Ejemplo real (computadora del INEMA, 08/11/2026)

En `exemplos/`: `relatorio.txt`, `relatorio.json`, `medidor.html` e imágenes en 1440 px y 390 px. Generado con `--anonimo`: las carpetas de proyectos privados se reemplazan por `privado-N` y sus solicitudes no se muestran.

- Claude Code: 4,2 mil millones de tokens procesados en 7 días (96% de caché leída), 1.055 sesiones, 996 de robots y temporizadores.
- Codex: 186 millones de tokens, 1.027 sesiones, todas `codex exec`.
- Indicador de Claude: **amarillo, 80,6% del límite** en la semana que comenzó el lunes a las 21 h; al ritmo de los días con uso, la semana termina en 102%.
- Una sola carpeta representó el 59% del peso de la semana.
- 91 grupos de ejecuciones repetidas (701 ejecuciones); agrupar cada grupo ahorra al menos el 8,6% del peso.

## Opciones

```bash
python3 medir.py relatorio --dias 14
python3 medir.py relatorio --json resumo.json
python3 medir.py html --anonimo --publicos publicos.txt --saida medidor.html
python3 medir.py relatorio --codex-home ~/.codex-2
python3 medir.py relatorio --janela 30 --similar 0.9
```

Configuración en `~/.config/medidor-de-limite/config.json` (límites, pesos, `raizes` de las carpetas de proyectos, `publicos`, `codex_homes`).

## El proceso, de principio a fin

| # | Paso | Comando | Quién lo hace |
|---|---|---|---|
| 1 | Descargar | `git clone https://github.com/inematds/medidor-de-limite.git` | 🧠 tú |
| 2 | Primera lectura (7 días) | `python3 medir.py relatorio` | ⚙️ script |
| 3 | Encontrar el límite en la semana en que se alcanzó | `python3 medir.py calibrar --salvar` | ⚙️ script (tú lo verificas) |
| 4 | Ver el indicador y las barras | `python3 medir.py html` y abrir `medidor.html` | ⚙️ script |
| 5 | Leer el top 10 y las ejecuciones repetidas | — | 🧠 tú |
| 6 | Agrupar o pausar temporizadores repetidos | `systemctl --user list-timers` | 🧠 tú (el medidor nunca los modifica) |
| 7 | Repetir cada semana (opcional) | un temporizador tuyo que ejecuta el paso 4 | ⚙️ temporizador |

## Pruebas

```bash
python3 -m pytest -q
```

Sin red, con registros JSONL sintéticos: suma de `usage` (sin duplicados ni mensajes de error), Codex con caché incluida en `input`, rutina según el primer mensaje, ejecuciones repetidas frente a temporizador diario, proyección y color del indicador, calibración según la semana del corte.

## Privacidad

Solo lectura. No imprime el contenido de las conversaciones: solo números y el inicio de la solicitud (hasta 80 caracteres, sin correo electrónico, URL ni ruta personal). Nada sale de tu computadora.

---

Hecho por [INEMA.CLUB](https://inema.club) · contenido abierto y gratuito · licencia MIT
# Агент-стажёр

AI-агент на базе opencode: наблюдает за действиями пользователя, запоминает процедуры в
долговременную память, обобщает на аналогичные задачи (сервер1 → сервер2), пишет
человекочитаемые инструкции, переиспользует решения и обновляет память при неудачах.

Полный план и требования: [PLAN.md](PLAN.md) (результат обсуждений комитета: аналитик,
инженер, системный архитектор, AI-специалист).

## Установка у нового пользователя

Это репозиторий-шаблон: клонируйте его и работайте в своей копии (память процедур —
ваши данные, версионируются git в вашем репозитории).

1. Требования: opencode (Desktop или CLI), Python 3.11+ с `pyyaml` и `jsonschema`,
   git. Для проверки типов плагина — node/npx (опционально).
2. `git clone https://github.com/PlanbAI/AI_intern.git <папка проекта>`
3. `cd <папка проекта>` и запустите opencode **именно в этой папке** — иначе
   проектный конфиг, плагин guard.ts, агент и скилл не загрузятся
   (opencode читает `opencode.json` и `.opencode/` из рабочей директории).
4. Выберите агента **intern** (Tab или в списке агентов).
5. Проверка установки: `python scripts/index.py` → «индекс пересобран: процедур 2»,
   и `python scripts/stats.py` (сводка + метрики процедур).
6. Обновление шаблона: `git pull` (конфликты возможны, если меняли файлы шаблона;
   свои процедуры в `agent-memory/procedures/` конфликтов не создают).

Структура памяти per-проект: у каждого проекта своя `agent-memory/` — процедуры
одного проекта не смешиваются с процедурами другого. Для переноса процедур между
проектами скопируйте файлы `agent-memory/procedures/*.yaml` (+ `state/*.yaml`) и
пересоберите индекс.

Краткая справка по работе с агентом: [HELP.md](HELP.md) — в чате открывается
командой `/intern` (`.opencode/commands/intern.md`); можно указать раздел:
`/intern запоминание`.

## Структура

```
agent-intern/
├─ agent-memory/               # долговременная память (git)
│  ├─ procedures/<id>.yaml     #   определение процедуры (схема: schema/procedure.schema.json)
│  ├─ state/<id>.yaml          #   состояние: прогоны runs/ok, биндинги per-host
│  ├─ instructions/<id>.md     #   человекочитаемые инструкции (версии в git-истории)
│  ├─ telemetry/*.jsonl        #   телеметрия событий
│  ├─ golden_set.json          #   размеченные запросы для метрик ретривала
│  └─ index.json               #   ПРОИЗВОДНЫЙ артефакт (пересобирается scripts/index.py)
├─ .opencode/skills/intern-agent/SKILL.md   # операционный мануал агента
├─ schema/                     # JSON Schema для процедур и состояния
└─ scripts/                    # Python-скрипты (pyyaml доступен; PS 5.1 не парсит YAML)
```

## Скрипты

| Скрипт | Назначение |
|---|---|
| `python scripts/index.py` | пересборка index.json из procedures/ (единственный писатель индекса) |
| `python scripts/log.py --session <id> --event run --status ok ...` | телеметрия (атомарная запись, lock) |
| `python scripts/stats.py` | сводка: success rate, latency p95, стоимость |
| `python scripts/capture.py --input sessions/<s>.json --out agent-memory/drafts/<id>_draft.yaml` | черновик процедуры из наблюдаемых команд |
| `python scripts/param_detect.py --file sessions/<s>.json` | детерминированные кандидаты переменных (правила, не LLM) |
| `python scripts/save-procedure.py --input <final.yaml> --host <хост> --bindings "HOST=x;PORT=y"` | валидация по JSON Schema + атомарное сохранение процедуры, state, инструкции |
| `python scripts/retrieve.py --query "задача" --host <хост>` | ретривал: TF-IDF + keywords + теги, top-k, уверенность (≥0.70 high / 0.40–0.70 medium / <0.40 новая задача) |
| `python scripts/eval-retrieval.py` | метрики ретривала по golden set: precision@k, MRR |
| `python scripts/classify.py --command "..." | --procedure P002` | уровень P0/P1/P2 по правилам (P2-сигналы: scp, systemctl, службы, права, диски; P0 — только allowlist) |
| `python scripts/run_procedure.py --procedure P002 --host <хост> --bindings "HOST=x" --approve-all` | исполнение процедуры: биндинги, лимиты шагов/таймаута, step.check, criteria, обновление state, телеметрия |
| `python scripts/verify-procedure.py --procedure P002 --host <хост> [--check-types ...] [--file <лог>]` | независимая сверка criteria после процедуры (state не трогается) |
| `python scripts/verify.py --type exit_code|http_status|regex --value <v> --command/--url/--text` | машинная проверка критерия (не LLM), exit 0/1/2 |

Блокировка `agent-memory/.lock` + атомарная запись (temp+rename) — в `scripts/lib/common.py`.

## Безопасность (фаза 2)

Три слоя защиты (defense-in-depth, каждый слой самостоятелен):

1. **permission-правила** (`opencode.json`, порядок важен — побеждает последнее совпадение):
   - allow: read-only операции (git status/log/diff, tail/grep/ps/df, Get-Content,
     curl -sf к health, Test-NetConnection), скрипты проекта `python scripts/*`;
   - deny: `rm`, `Remove-Item`, `del`, `rd`, `Invoke-Expression`, `iex`,
     `base64 -d`, конвейеры `curl|*`, `format`, `diskpart`, деструктивный git
     (`git rm`, `git reset --hard`, `git clean`, `git push --force`);
   - `external_directory` + `read`: deny `~/.ssh`, `.env`, `secrets/`, `*.pem`, `*.key`.
2. **Плагин `.opencode/plugins/guard.ts`** (загружается автоматически, проверен tsc strict):
   - `tool.execute.before`: регэксп-блокировка опасных команд (в т.ч. в обфусцированном
     виде: `powershell -Command rm -r`), запрет чтения чувствительных путей — `throw Error`;
   - `tool.execute.after`: маскирование секретов (api_key/password/token, Bearer,
     AWS-ключи, приватные ключи, строки подключения user:pass@host) → `REDACTED`,
     усечение вывода (300 строк / 15 КБ), маркер `<!-- untrusted -->` — вывод считается
     данными, а не инструкциями.
3. **Поведенческие правила скилла** intern-agent: P0/P1/P2, стоп-вопросы, сомнение → вверх.

ВАЖНО: guard — не граница безопасности (PS-скрипты могут выполняться напрямую),
а защита в глубину. Новые процедуры исполняются только на P1.

## Фазы (статус)

- [x] Фаза 1. Каркас памяти (репо, схемы, lock/атомарность, index, телеметрия, golden set, скилл)
- [x] Фаза 2. Безопасность: permission-правила, плагин guard.ts (проверен tsc), deny-списки, агент intern
- [x] Фаза 3. Захват: capture.py/param_detect.py/save-procedure.py, LLM-разметка с батч-подтверждением, кэш биндингов, инструкции-рендер
- [x] Фаза 4. Ретривал: TF-IDF (index v2) + keywords + теги, top-k, уверенность, host-статистика, eval по golden set
- [x] Фаза 5. Исполнение: classify.py (P0/P1/P2), verify.py (машинные проверки), run_procedure.py (лимиты, state, телеметрия)
- [x] Фаза 6. Верификация: verify-procedure.py (независимая сверка, state не трогает), штрафы уверенности (non-working ×0.5, деградация хоста ×0.75), метрики процедур в stats.py
- [ ] Пост-MVP: эмбеддинги, wiki, многопользовательность, офлайн-чат

## Начало работы

1. `git init` в корне проекта (память версионируется через git).
2. Создай первую процедуру: скопируй `agent-memory/procedures/_template.yaml` → `P001.yaml`.
3. `python scripts/index.py` — пересобери индекс.
4. Запиши первое событие: `python scripts/log.py --session test --event learn --status ok`.
5. Проверь сводку: `python scripts/stats.py`.
6. Загружай скилл intern-agent при работе с памятью.

## Решения, принятые комитетом

- Скрипты данных — Python (pyyaml), не PowerShell: PS 5.1 не парсит YAML нативно.
- index.json не коммитится в git (производный артефакт, .gitignore).
- Версии инструкций — git-история; «нерабочие» решения помечаются `status: non-working`,
  не удаляются.

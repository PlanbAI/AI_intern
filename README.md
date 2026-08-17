# AI Intern

An AI agent built on opencode that watches what you do, stores reusable
procedures in long-term memory, generalizes them to similar tasks
(server1 → server2), writes human-readable instructions, reuses known
solutions, and updates its memory when a solution fails.

Full plan and requirements: [PLAN.md](PLAN.md) (the result of committee
discussions: analyst, engineer, system architect, AI specialist).

> English README · [Русская версия](README.ru.md)

## Setup for a new user

This is a template repository: clone it and work in your own copy
(procedural memory is your data, versioned with git in your repository).

1. Requirements: **Windows 10/11 with winget** (built-in) and internet.
   Everything else — git, Python 3.11+, Node.js, Google Chrome and
   OpenCode Desktop — the setup script installs automatically
   (UAC prompts may appear). On Linux/macOS install them manually
   (setup.ps1 is Windows-only).
2. `git clone https://github.com/PlanbAI/AI_intern.git <project-folder>`
3. `cd <project-folder>` and run the one-shot setup script:
   ```powershell
   powershell -ExecutionPolicy Bypass -File setup.ps1
   ```
   It installs missing dependencies via winget (with fallback to
   official installers), installs Python deps (pyyaml, jsonschema),
   installs `@playwright/mcp` (or falls back to npx), registers the MCP
   server in the **global** opencode config (`~/.config/opencode/
   opencode.jsonc`) with machine-specific paths and a persistent browser
   profile `.opencode/browser-profile`, copies the agent to the legacy
   `.opencode/agent/` path (Desktop 1.18.x reads it, not `.opencode/agents/`),
   creates a desktop shortcut "OpenCode - <project>", and rebuilds the
   index. Idempotent — safe to re-run. Use `-SkipInstall` to only check
   and configure without installing anything.
4. Launch opencode **from this exact folder** (use the desktop shortcut,
   or pass the folder path to OpenCode.exe) — otherwise the project
   config, the guard.ts plugin, the agent and the skill will not load.
5. Select the **intern** agent (Tab, or in the agent list).
6. Verify the install: `python scripts/index.py` → «index rebuilt:
   3 procedures», and `python scripts/stats.py` (summary + procedure
   metrics).
7. Updating the template: `git pull` (conflicts are possible if you
   changed template files; your own procedures in
   `agent-memory/procedures/` never conflict).
8. First run of a browser procedure (steps with `type: agent`, e.g.
   P003): the MCP server uses a **separate** Chrome profile
   (`.opencode/browser-profile/`, gitignored — cookies!). The first
   Google login is manual, sessions persist afterwards. The permission
   rule `"playwright_*": "ask"` is set; the guard.ts plugin denies
   `playwright_browser_evaluate`/`run_code` (arbitrary JS).

Memory is per-project: every project has its own `agent-memory/` —
procedures of one project never mix with another. To move procedures
between projects, copy `agent-memory/procedures/*.yaml`
(+ `state/*.yaml`) and rebuild the index.

Quick user guide: [HELP.md](HELP.md) — in chat run `/intern`
(`.opencode/commands/intern.md`), optionally with a section name:
`/intern capture`.

## Structure

```
agent-intern/
├─ agent-memory/               # long-term memory (git)
│  ├─ procedures/<id>.yaml     #   procedure definition (schema: schema/procedure.schema.json)
│  ├─ state/<id>.yaml          #   state: runs/ok, per-host bindings
│  ├─ instructions/<id>.md     #   human-readable instructions (versions in git history)
│  ├─ telemetry/*.jsonl        #   event telemetry
│  ├─ golden_set.json          #   labeled queries for retrieval metrics
│  └─ index.json               #   DERIVED artifact (rebuilt by scripts/index.py)
├─ .opencode/skills/intern-agent/SKILL.md   # the agent's operating manual
├─ .opencode/agents/intern.md               # agent (documented path)
├─ .opencode/agent/intern.md                # agent (legacy path, Desktop 1.18.x)
├─ schema/                     # JSON Schemas for procedures and state
├─ scripts/                    # Python scripts (pyyaml available; PS 5.1 can't parse YAML)
├─ setup.ps1                   # one-shot environment setup for a new machine
└─ requirements.txt            # Python deps (pyyaml, jsonschema)
```

## Scripts

| Script | Purpose |
|---|---|
| `python scripts/index.py` | rebuild index.json from procedures/ (the only index writer) |
| `python scripts/log.py --session <id> --event run --status ok ...` | telemetry (atomic write, lock) |
| `python scripts/stats.py` | summary: success rate, latency p95, cost |
| `python scripts/capture.py --input sessions/<s>.json --out agent-memory/drafts/<id>_draft.yaml` | procedure draft from observed commands |
| `python scripts/param_detect.py --file sessions/<s>.json` | deterministic variable candidates (rules, not LLM) |
| `python scripts/save-procedure.py --input <final.yaml> --host <host> --bindings "HOST=x;PORT=y"` | JSON Schema validation + atomic save of procedure, state, instructions |
| `python scripts/retrieve.py --query "task" --host <host>` | retrieval: TF-IDF + keywords + tags, top-k, confidence (≥0.70 high / 0.40–0.70 medium / <0.40 new task) |
| `python scripts/eval-retrieval.py` | retrieval metrics on the golden set: precision@k, MRR |
| `python scripts/classify.py --command "..." --procedure P002` | P0/P1/P2 level by rules (P2 signals: scp, systemctl, services, permissions, disks; P0 — allowlist only) |
| `python scripts/run_procedure.py --procedure P002 --host <host> --bindings "HOST=x" --approve-all` | procedure execution: bindings, step/time limits, step.check, criteria, state update, telemetry |
| `python scripts/mark_result.py --procedure P003 --host local --ok\|--fail [--error "..."]` | record the outcome of a procedure executed by the agent itself (`type: agent` steps via MCP; run_procedure refuses such procedures, exit 3) |
| `python scripts/verify-procedure.py --procedure P002 --host <host> [--check-types ...] [--file <log>]` | independent criteria verification after a procedure (state untouched) |
| `python scripts/verify.py --type exit_code\|http_status\|regex --value <v> --command/--url/--text` | machine check of a criterion (not LLM), exit 0/1/2 |

`agent-memory/.lock` + atomic write (temp+rename) — in
`scripts/lib/common.py`.

## Security (phase 2)

Three layers of defense (defense-in-depth, each layer independent):

1. **Permission rules** (`opencode.json`, order matters — the last
   match wins):
   - allow: read-only operations (git status/log/diff, tail/grep/ps/df,
     Get-Content, curl -sf to health, Test-NetConnection), project
     scripts `python scripts/*`;
   - deny: `rm`, `Remove-Item`, `del`, `rd`, `Invoke-Expression`, `iex`,
     `base64 -d`, pipelines `curl|*`, `format`, `diskpart`,
     destructive git (`git rm`, `git reset --hard`, `git clean`,
     `git push --force`);
   - `external_directory` + `read`: deny `~/.ssh`, `.env`, `secrets/`,
     `*.pem`, `*.key`.
2. **Plugin `.opencode/plugins/guard.ts`** (loaded automatically,
   type-checked with tsc strict):
   - `tool.execute.before`: regex blocking of dangerous commands
     (including obfuscated ones: `powershell -Command rm -r`), denial
     of sensitive paths — `throw Error`;
   - `tool.execute.after`: secret masking (api_key/password/token,
     Bearer, AWS keys, private keys, connection strings
     user:pass@host) → `REDACTED`, output truncation (300 lines /
     15 KB), `<!-- untrusted -->` marker — output is data, not
     instructions.
3. **Behavioral rules of the intern-agent skill**: P0/P1/P2, stop
   questions, uncertainty → escalate.

IMPORTANT: guard is not a security boundary (PS scripts can be executed
directly), it is defense in depth. New procedures are executed only at
P1.

## Phases (status)

- [x] Phase 1. Memory framework (repo, schemas, lock/atomicity, index, telemetry, golden set, skill)
- [x] Phase 2. Security: permission rules, guard.ts plugin (tsc-checked), deny lists, intern agent
- [x] Phase 3. Capture: capture.py/param_detect.py/save-procedure.py, LLM labeling with batch confirmation, binding cache, instruction render
- [x] Phase 4. Retrieval: TF-IDF (index v2) + keywords + tags, top-k, confidence, host stats, golden-set eval
- [x] Phase 5. Execution: classify.py (P0/P1/P2), verify.py (machine checks), run_procedure.py (limits, state, telemetry)
- [x] Phase 6. Verification: verify-procedure.py (independent check, state untouched), confidence penalties (non-working ×0.5, host degradation ×0.75), procedure metrics in stats.py
- [ ] Post-MVP: embeddings, wiki, multi-user, offline chat

## Getting started

1. `git init` in the project root (memory is versioned via git).
2. Create your first procedure: copy
   `agent-memory/procedures/_template.yaml` → `P001.yaml`.
3. `python scripts/index.py` — rebuild the index.
4. Log the first event: `python scripts/log.py --session test --event learn --status ok`.
5. Check the summary: `python scripts/stats.py`.
6. Procedures run **by meaning, not by id**: ask the intern «are there any
   meetings soon?» and it will find the calendar procedure via
   `retrieve.py --query "<your phrase>"` (or run the retrieval yourself
   to preview candidates). If several procedures fit, the intern shows a
   numbered list sorted by confidence and waits for your choice —
   the default is option 1 (most likely); empty answer means «1».
7. Load the intern-agent skill when working with memory.

## Decisions made by the committee

- Data scripts are Python (pyyaml), not PowerShell: PS 5.1 cannot parse
  YAML natively.
- index.json is not committed to git (derived artifact, .gitignore).
- Instruction versions live in git history; «non-working» solutions are
  marked `status: non-working`, never deleted.
<#
    setup.ps1 — автонастройка проекта «Агент-стажёр» (AI_intern) на новом компьютере.

    Что делает (идемпотентно, повторный запуск безопасен):
      1. Проверяет окружение: git, Python 3.11+, Node.js.
      2. Ставит Python-зависимости (pyyaml, jsonschema) из requirements.txt.
      3. Устанавливает @playwright/mcp глобально (npm); если не выйдет — использует npx.
      4. Находит Google Chrome (для --browser chrome).
      5. Прописывает MCP-сервер playwright в ГЛОБАЛЬНЫЙ конфиг opencode
         (~/.config/opencode/opencode.jsonc) с определёнными путями и постоянным
         профилем .opencode/browser-profile (Desktop не читает проектные MCP).
      6. Дублирует агента intern в легаси-путь .opencode/agent/
         (Desktop 1.18.x не читает документированный .opencode/agents/).
      7. Создаёт ярлык на рабочем столе «OpenCode - <папка проекта>».
      8. Пересобирает индекс процедур (python scripts/index.py).

    Запуск (в корне склонированного репозитория):
      powershell -ExecutionPolicy Bypass -File setup.ps1

    Флаги:
      -SkipNpmInstall   не устанавливать @playwright/mcp (уже стоит)
      -SkipShortcut     не создавать ярлык на рабочем столе
      -SkipGlobalMCP    не трогать глобальный конфиг opencode (для CLI-пользователей)
#>
param(
    [switch]$SkipNpmInstall,
    [switch]$SkipShortcut,
    [switch]$SkipGlobalMCP
)
$ErrorActionPreference = "Stop"
$ProjectDir = (Resolve-Path (Split-Path -Parent $MyInvocation.MyCommand.Path)).Path
$ProjectName = Split-Path $ProjectDir -Leaf

function Write-Step($m)  { Write-Host "`n==> $m" -ForegroundColor Cyan }
function Write-Ok($m)    { Write-Host "    OK: $m" -ForegroundColor Green }
function Write-Warn($m)  { Write-Host "    ВНИМАНИЕ: $m" -ForegroundColor Yellow }
function Write-Fail($m)  { Write-Host "    ОШИБКА: $m" -ForegroundColor Red }

Write-Host "Агент-стажёр: настройка проекта '$ProjectName' ($ProjectDir)" -ForegroundColor Magenta

# ---------------------------------------------------------------- 1. окружение
Write-Step "1. Проверка окружения"
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) { Write-Fail "git не найден. Установите git (https://git-scm.com)."; exit 1 }
Write-Ok "git: $($git.Source)"

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Fail "python не найден. Установите Python 3.11+ (галочка 'Add to PATH')."; exit 1 }
$pyVer = (& python --version 2>&1)
Write-Ok "python: $pyVer"
if ($pyVer -notmatch "3\.(1[1-9]|[2-9][0-9])") {
    Write-Warn "Рекомендуется Python 3.11+, продолжаю с тем, что есть."
}

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) { Write-Warn "node не найден — MCP-браузер будет недоступен (ставьте node: https://nodejs.org)." }
else { Write-Ok "node: $($node.Source)" }

# ---------------------------------------------------------------- 2. python-зависимости
Write-Step "2. Python-зависимости (pyyaml, jsonschema)"
try {
    & python -m pip install -r (Join-Path $ProjectDir "requirements.txt") --quiet
    if ($LASTEXITCODE -ne 0) { throw "pip install вернул код $LASTEXITCODE" }
    Write-Ok "зависимости установлены"
} catch { Write-Warn "pip install не удался: $($_.Exception.Message). Ставьте вручную: pip install -r requirements.txt" }

# ---------------------------------------------------------------- 3. @playwright/mcp
Write-Step "3. Playwright MCP"
$cliPath = $null
$npmBin = Get-Command npm -ErrorAction SilentlyContinue
if ($node -and $npmBin) {
    $npmRoot = (& npm root -g 2>$null | Select-Object -First 1)
    if ($npmRoot) {
        # npm root -g уже оканчивается на \node_modules — НЕ добавляем его повторно
        $candidate = Join-Path $npmRoot "@playwright\mcp\cli.js"
        if (Test-Path $candidate) { $cliPath = $candidate; Write-Ok "уже установлен: $candidate" }
        elseif (-not $SkipNpmInstall) {
            Write-Step "3.1 Установка @playwright/mcp (npm i -g)"
            try {
                & npm install -g "@playwright/mcp" --no-fund --no-audit
                if ($LASTEXITCODE -ne 0) { throw "npm install вернул код $LASTEXITCODE" }
                if (Test-Path $candidate) { $cliPath = $candidate; Write-Ok "установлен: $candidate" }
            } catch { Write-Warn "npm i -g не удался: $($_.Exception.Message)" }
        }
    }
}
if (-not $cliPath -and $node -and $npmBin) {
    Write-Warn "Использую npx-вариант (требует сеть при первом запуске MCP)."
    $cliPath = "npx"   # фолбэк: opencode сам выполнит npx -y playwright-mcp@latest
}
if (-not $cliPath) { Write-Warn "MCP-браузер не настроен (нет node/npm)." }

# ---------------------------------------------------------------- 4. Chrome
Write-Step "4. Поиск Google Chrome"
$chrome = $null
foreach ($p in @(
        "C:\Program Files\Google\Chrome\Application\chrome.exe",
        "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe"))) {
    if (Test-Path $p) { $chrome = $p; break }
}
if ($chrome) { Write-Ok "Chrome: $chrome" }
else { Write-Warn "Chrome не найден — MCP-сервер запустится без --browser chrome (скачает свой Chromium или упадёт; установите Chrome)." }

# ---------------------------------------------------------------- 5. глобальный MCP-конфиг
if (-not $SkipGlobalMCP -and $cliPath) {
    Write-Step "5. Глобальный конфиг opencode: MCP playwright"
    $ocDir = Join-Path $env:USERPROFILE ".config\opencode"
    $ocFile = Join-Path $ocDir "opencode.jsonc"
    New-Item -ItemType Directory -Path $ocDir -Force | Out-Null

    $nodePath = $node.Source
    if ($cliPath -eq "npx") {
        # фолбэк без глобальной установки: npx -y playwright-mcp@latest
        $cmd = @("npx", "-y", "playwright-mcp@latest")
    } else {
        $cmd = @($nodePath, $cliPath)
    }
    if ($chrome) { $cmd += @("--browser", "chrome") }
    $cmd += @("--user-data-dir", (Join-Path $ProjectDir ".opencode\browser-profile"))

    # читаем существующий конфиг (JSON/JSONC без комментариев-строк)
    $config = @{}
    if (Test-Path $ocFile) {
        $raw = Get-Content $ocFile -Raw -Encoding UTF8
        $clean = ($raw -split "`n" | Where-Object { $_ -notmatch '^\s*//' }) -join "`n"
        try { $config = $clean | ConvertFrom-Json -ErrorAction Stop } catch {
            Write-Warn "Не смог распарсить $ocFile — перезаписываю только ключ mcp поверх пустого конфига."
            $config = @{}
        }
    }
    if (-not $config.mcp) { $config | Add-Member -NotePropertyName mcp -NotePropertyValue @{} }
    $config.mcp | Add-Member -NotePropertyName playwright -NotePropertyValue @{
        type = "local"
        command = $cmd
        enabled = $true
    } -Force

    $json = $config | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($ocFile, $json, (New-Object System.Text.UTF8Encoding $true))
    Write-Ok "записан $ocFile с mcp.playwright: $($cmd -join ' ')"
} else {
    Write-Step "5. Пропущено (флаг -SkipGlobalMCP или MCP недоступен)"
}

# ---------------------------------------------------------------- 6. легаси-агент
Write-Step "6. Агент intern в легаси-путь .opencode\agent"
$srcAgents = Join-Path $ProjectDir ".opencode\agents"
$legacyDir = Join-Path $ProjectDir ".opencode\agent"
if (Test-Path $srcAgents) {
    New-Item -ItemType Directory -Path $legacyDir -Force | Out-Null
    Get-ChildItem $srcAgents -Filter *.md | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $legacyDir $_.Name) -Force
        Write-Ok "скопирован $($_.Name) -> .opencode\agent\$($_.Name)"
    }
} else { Write-Warn ".opencode\agents не найден — агент не продублирован." }

# ---------------------------------------------------------------- 7. ярлык
if (-not $SkipShortcut) {
    Write-Step "7. Ярлык на рабочем столе"
    $desktop = [Environment]::GetFolderPath('Desktop')
    $openCodeExe = $null
    foreach ($p in @(
            (Join-Path $env:LOCALAPPDATA "Programs\@opencode-aidesktop\OpenCode.exe"),
            "C:\Program Files\@opencode-aidesktop\OpenCode.exe")) {
        if (Test-Path $p) { $openCodeExe = $p; break }
    }
    if ($openCodeExe) {
        $lnkPath = Join-Path $desktop "OpenCode - $ProjectName.lnk"
        $ws = New-Object -ComObject WScript.Shell
        $sc = $ws.CreateShortcut($lnkPath)
        $sc.TargetPath = $openCodeExe
        $sc.Arguments = '"' + $ProjectDir + '"'
        $sc.WorkingDirectory = $ProjectDir
        $sc.IconLocation = "$openCodeExe,0"
        $sc.Description = "OpenCode Desktop с проектом $ProjectName"
        $sc.Save()
        Write-Ok "ярлык: $lnkPath"
    } else {
        Write-Warn "OpenCode Desktop не найден — ярлык не создан (запускайте opencode из папки $ProjectDir)."
    }
}

# ---------------------------------------------------------------- 8. индекс
Write-Step "8. Пересборка индекса процедур"
try {
    Push-Location $ProjectDir
    & python scripts\index.py
    if ($LASTEXITCODE -eq 0) { Write-Ok "index.json пересобран" } else { Write-Warn "index.py вернул код $LASTEXITCODE" }
    Pop-Location
} catch { Pop-Location; Write-Warn "index.py: $($_.Exception.Message)" }

# ---------------------------------------------------------------- итог
Write-Host "`n======================================================================" -ForegroundColor Magenta
Write-Host "Готово! Осталось вручную:" -ForegroundColor Magenta
Write-Host "  1. Закройте все окна OpenCode и запустите его через ярлык" -ForegroundColor White
Write-Host "     'OpenCode - $ProjectName' (или: opencode из папки $ProjectDir)."
Write-Host "  2. Выберите агента intern (Tab)."
Write-Host "  3. При первом использовании браузера (P003 и др.) войдите в Google" -ForegroundColor White
Write-Host "     вручную — сессия сохранится в .opencode\browser-profile."
Write-Host "  4. Для пуша в GitHub настройте креды: git remote add origin <url>"
Write-Host "======================================================================" -ForegroundColor Magenta

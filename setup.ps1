<#
    setup.ps1 — автонастройка проекта «Агент-стажёр» (AI_intern) на новом компьютере.

    Скрипт САМ устанавливает всё недостающее (Windows 10/11, winget):
      0. Автоустановка: git, Python 3.11+, Node.js, Google Chrome, OpenCode Desktop
         (winget; фолбэки: GitHub API / python.org / nodejs.org / официальные
         установщики). При установке могут появиться окна UAC — подтвердите.
      1. Проверяет окружение.
      2. Ставит Python-зависимости (pyyaml, jsonschema) из requirements.txt.
      3. Устанавливает @playwright/mcp глобально (npm); если не выйдет — npx.
      4. Находит Google Chrome (для --browser chrome).
      5. Прописывает MCP-сервер playwright в ГЛОБАЛЬНЫЙ конфиг opencode
         (~/.config/opencode/opencode.jsonc) с определёнными путями и постоянным
         профилем .opencode/browser-profile (Desktop не читает проектные MCP).
      6. Дублирует агента intern в легаси-путь .opencode/agent/
         (Desktop 1.18.x не читает документированный .opencode/agents/).
      7. Создаёт ярлык на рабочем столе «OpenCode - <папка проекта>».
      8. Пересобирает индекс процедур (python scripts/index.py).

    Идемпотентен: повторный запуск безопасен, ничего не переустанавливает.

    Запуск (в корне склонированного репозитория):
      powershell -ExecutionPolicy Bypass -File setup.ps1

    Флаги:
      -SkipInstall      не устанавливать зависимости (только проверить/настроить)
      -SkipNpmInstall   не устанавливать @playwright/mcp (уже стоит)
      -SkipShortcut     не создавать ярлык на рабочем столе
      -SkipGlobalMCP    не трогать глобальный конфиг opencode (для CLI-пользователей)
#>
param(
    [switch]$SkipInstall,
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

function Refresh-Path {
    # Перечитываем PATH из реестра: свежеустановленные winget-пакеты иначе не видны.
    $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("Path", "User")
}
function Test-Cmd([string]$Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }
function Install-Winget([string]$Id, [string]$Label) {
    Write-Host "    Установка: $Label ($Id)… (подтвердите UAC, если появится окно)"
    winget install --id $Id -e --silent --accept-package-agreements `
        --accept-source-agreements --disable-interactivity 2>&1 | Out-Null
    return ($LASTEXITCODE -eq 0)
}

Write-Host "Агент-стажёр: настройка проекта '$ProjectName' ($ProjectDir)" -ForegroundColor Magenta

# ---------------------------------------------------------------- 0. автоустановка
Write-Step "0. Автоустановка недостающих зависимостей"
$installedNow = @()
if (-not $SkipInstall) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $winget) {
        Write-Warn "winget не найден — пропускаю автоустановку; недостающее ставьте вручную."
    }

    # --- 0.1 git
    if (Test-Cmd git) { Write-Ok "git уже установлен" }
    elseif ($winget -and (Install-Winget "Git.Git" "Git")) { $installedNow += "git" }
    else {
        try { # фолбэк: GitHub API, свежий Git for Windows
            $rel = Invoke-RestMethod -Uri "https://api.github.com/repos/git-for-windows/git/releases/latest" -Headers @{ "User-Agent" = "setup" }
            $asset = $rel.assets | Where-Object { $_.name -match "64-bit\.exe$" -and $_.name -notmatch "portable" } | Select-Object -First 1
            $tmp = Join-Path $env:TEMP "git-install.exe"
            Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $tmp -UseBasicParsing
            Start-Process -FilePath $tmp -ArgumentList "/VERYSILENT", "/NORESTART" -Wait
            $installedNow += "git (фолбэк)"
        } catch { Write-Warn "git не установлен: $($_.Exception.Message)" }
    }
    Refresh-Path

    # --- 0.2 python 3.11+ (перебор актуальных веток; python добавляется в PATH сам)
    if (Test-Cmd python) { Write-Ok "python уже установлен" }
    else {
        $pyOk = $false
        foreach ($id in @("Python.Python.3.13", "Python.Python.3.12", "Python.Python.3.11")) {
            if ($winget -and (Install-Winget $id "Python ($id)")) { $pyOk = $true; $installedNow += "python"; break }
        }
        if (-not $pyOk) {
            try { # фолбэк: официальный установщик python.org
                $tmp = Join-Path $env:TEMP "python-setup.exe"
                Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe" -OutFile $tmp -UseBasicParsing
                Start-Process -FilePath $tmp -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_pip=1" -Wait
                $installedNow += "python (фолбэк)"
            } catch { Write-Warn "python не установлен: $($_.Exception.Message)" }
        }
    }
    Refresh-Path

    # --- 0.3 node.js
    if (Test-Cmd node) { Write-Ok "node уже установлен" }
    elseif ($winget -and (Install-Winget "OpenJS.NodeJS" "Node.js")) { $installedNow += "node" }
    else {
        try { # фолбэк: официальный MSI последней LTS-ветки 22.x
            $idx = (Invoke-WebRequest -Uri "https://nodejs.org/dist/latest-v22.x/" -UseBasicParsing).Content
            $m = [regex]::Match($idx, 'node-v[\d.]+-x64\.msi')
            if ($m.Success) {
                $tmp = Join-Path $env:TEMP "node-setup.msi"
                Invoke-WebRequest -Uri "https://nodejs.org/dist/latest-v22.x/$($m.Value)" -OutFile $tmp -UseBasicParsing
                Start-Process msiexec -ArgumentList "/i", ("`"" + $tmp + "`""), "/qn", "/norestart" -Wait
                $installedNow += "node (фолбэк)"
            } else { throw "не найден msi в каталоге nodejs.org/dist/latest-v22.x" }
        } catch { Write-Warn "node не установлен: $($_.Exception.Message)" }
    }
    Refresh-Path

    # --- 0.4 google chrome
    $chromeFound = $false
    foreach ($p in @("C:\Program Files\Google\Chrome\Application\chrome.exe",
                     "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                     (Join-Path $env:LOCALAPPDATA "Google\Chrome\Application\chrome.exe"))) {
        if (Test-Path $p) { $chromeFound = $true; break }
    }
    if ($chromeFound) { Write-Ok "Chrome уже установлен" }
    elseif ($winget -and (Install-Winget "Google.Chrome" "Google Chrome")) { $installedNow += "chrome" }
    else {
        try { # фолбэк: официальный standalone-установщик Google
            $tmp = Join-Path $env:TEMP "chrome-setup.exe"
            Invoke-WebRequest -Uri "https://dl.google.com/chrome/install/standalonesetup64.exe" -OutFile $tmp -UseBasicParsing
            Start-Process -FilePath $tmp -ArgumentList "/silent", "/install" -Wait
            $installedNow += "chrome (фолбэк)"
        } catch { Write-Warn "Chrome не установлен: $($_.Exception.Message)" }
    }

    # --- 0.5 opencode (Desktop предпочтительно; CLI — запасной вариант)
    if (Test-Cmd opencode) { Write-Ok "opencode (CLI) уже установлен" }
    elseif (Test-Path (Join-Path $env:LOCALAPPDATA "Programs\@opencode-aidesktop\OpenCode.exe")) {
        Write-Ok "OpenCode Desktop уже установлен"
    }
    elseif ($winget -and (Install-Winget "SST.OpenCodeDesktop" "OpenCode Desktop")) {
        $installedNow += "opencode desktop"
    }
    elseif ($winget -and (Install-Winget "SST.opencode" "opencode CLI")) {
        $installedNow += "opencode CLI"
    }
    else {
        Write-Warn "opencode не установлен: скачайте Desktop с opencode.ai/download"
        Write-Host "        или установите CLI: irm https://opencode.ai/install | iex"
    }
    Refresh-Path
} else {
    Write-Warn "Флаг -SkipInstall: зависимости не устанавливаю (только проверка и настройка)."
}

# ---------------------------------------------------------------- 1. окружение
Write-Step "1. Проверка окружения"
$git = Get-Command git -ErrorAction SilentlyContinue
if (-not $git) { Write-Warn "git не найден. Установите git (https://git-scm.com) — пуш/пул памяти не заработает." }
else { Write-Ok "git: $($git.Source)" }

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { Write-Fail "python не найден. Установите Python 3.11+ (галочка 'Add to PATH') или перезапустите терминал после установки." }
else {
    $pyVer = (& python --version 2>&1)
    Write-Ok "python: $pyVer"
    if ($pyVer -notmatch "3\.(1[1-9]|[2-9][0-9])") { Write-Warn "Рекомендуется Python 3.11+, продолжаю с тем, что есть." }
}

$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) { Write-Warn "node не найден — MCP-браузер будет недоступен (ставьте node: https://nodejs.org)." }
else { Write-Ok "node: $($node.Source)" }

# ---------------------------------------------------------------- 2. python-зависимости
Write-Step "2. Python-зависимости (pyyaml, jsonschema)"
if (-not $py) { Write-Warn "python недоступен — пропускаю." }
else {
    try {
        & python -m pip install -r (Join-Path $ProjectDir "requirements.txt") --quiet
        if ($LASTEXITCODE -ne 0) { throw "pip install вернул код $LASTEXITCODE" }
        Write-Ok "зависимости установлены"
    } catch { Write-Warn "pip install не удался: $($_.Exception.Message). Ставьте вручную: pip install -r requirements.txt" }
}

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
if (-not $py) { Write-Warn "python недоступен — индекс не пересобран." }
else {
    try {
        Push-Location $ProjectDir
        & python scripts\index.py
        if ($LASTEXITCODE -eq 0) { Write-Ok "index.json пересобран" } else { Write-Warn "index.py вернул код $LASTEXITCODE" }
        Pop-Location
    } catch { Pop-Location; Write-Warn "index.py: $($_.Exception.Message)" }
}

# ---------------------------------------------------------------- итог
Write-Host "`n======================================================================" -ForegroundColor Magenta
if ($installedNow.Count -gt 0) {
    Write-Host "Установлено скриптом: $($installedNow -join ', ')" -ForegroundColor Green
    Write-Host "(если после установки python/git не видны — закройте и откройте терминал)" -ForegroundColor Yellow
}
Write-Host "Готово! Осталось вручную:" -ForegroundColor Magenta
Write-Host "  1. Закройте все окна OpenCode и запустите его через ярлык" -ForegroundColor White
Write-Host "     'OpenCode - $ProjectName' (или: opencode из папки $ProjectDir)."
Write-Host "  2. Выберите агента intern (Tab)."
Write-Host "  3. При первом использовании браузера (P003 и др.) войдите в Google" -ForegroundColor White
Write-Host "     вручную — сессия сохранится в .opencode\browser-profile."
Write-Host "  4. Для пуша в GitHub настройте креды: git remote add origin <url>"
Write-Host "======================================================================" -ForegroundColor Magenta
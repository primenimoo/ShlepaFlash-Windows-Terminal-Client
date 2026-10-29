# ShlepaFlash Windows installer. Run from a public GitHub repository:
# Resolve the repository default branch first, then execute the bootstrap installer from that branch.
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

function Write-Step([string]$Message) { Write-Host "`n[SHLEPAFLASH] $Message" -ForegroundColor Cyan }
function Write-Fail([string]$Message) { Write-Host "`n[SHLEPAFLASH] ОШИБКА: $Message" -ForegroundColor Red; throw $Message }

$Repository = $env:SHLEPAFLASH_REPO
if ([string]::IsNullOrWhiteSpace($Repository) -or $Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    Write-Fail 'Не задано имя публичного GitHub-репозитория. См. README_RU.md: команда установки из GitHub.'
}

$AsciiLogo = @'
  ____  _   _ _     _____ ____   _____ _      ____  _____
 / ___|| | | | |   | ____|  _ \ |  ___| |    / ___|| ____|
 \___ \| |_| | |   |  _| | |_) || |_  | |    \___ \|  _|
  ___) |  _  | |___| |___|  __/ |  _| | |___  ___) | |___
 |____/|_| |_|_____|_____|_|    |_|   |_____| |____/|_____|
'@
Write-Host $AsciiLogo -ForegroundColor White
Write-Host 'SHLEPA' -NoNewline -ForegroundColor White
Write-Host 'FLASH' -ForegroundColor DarkYellow
Write-Host 'Установка интерактивного терминального клиента Windows' -ForegroundColor Gray

$InstallRoot = Join-Path $env:LOCALAPPDATA 'ShlepaFlash'
$BinDir = Join-Path $InstallRoot 'bin'
$AppDir = Join-Path $InstallRoot 'app'
$StageDir = Join-Path $InstallRoot ('stage-' + [guid]::NewGuid().ToString('N'))
$BackupDir = Join-Path $InstallRoot ('app-backup-' + [guid]::NewGuid().ToString('N'))
$ZipPath = Join-Path $env:TEMP ('shlepaflash-' + [guid]::NewGuid().ToString('N') + '.zip')
$VenvPython = Join-Path $InstallRoot '.venv\Scripts\python.exe'
$VenvPip = Join-Path $InstallRoot '.venv\Scripts\pip.exe'

New-Item -ItemType Directory -Force -Path $InstallRoot, $BinDir | Out-Null

# Prefer the Python launcher; install Python 3.12 only if no usable Python 3.11+ exists.
$PythonCommand = $null
$PyLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
if ($PyLauncher) {
    try {
        & $PyLauncher.Source -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { $PythonCommand = @($PyLauncher.Source, '-3') }
    } catch { }
}
if (-not $PythonCommand) {
    $PythonCmd = Get-Command 'python.exe' -ErrorAction SilentlyContinue
    if ($PythonCmd) {
        try {
            & $PythonCmd.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" 2>$null
            if ($LASTEXITCODE -eq 0) { $PythonCommand = @($PythonCmd.Source) }
        } catch { }
    }
}
if (-not $PythonCommand) {
    $Winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
    if (-not $Winget) { Write-Fail 'Не найдены Python 3.11+ и winget. Установите Python 3.12+ с https://www.python.org/downloads/windows/, затем повторите команду.' }
    Write-Step 'Python 3.12+ не найден. Устанавливаю Python 3.12 в профиль пользователя…'
    & $Winget.Source install --exact --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    if ($LASTEXITCODE -ne 0) { Write-Fail 'Установка Python через winget завершилась ошибкой. Установите Python вручную и повторите команду.' }
    $candidate = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
    if (Test-Path $candidate) { $PythonCommand = @($candidate) }
    else {
        $PyLauncher = Get-Command 'py.exe' -ErrorAction SilentlyContinue
        if ($PyLauncher) { $PythonCommand = @($PyLauncher.Source, '-3.12') }
    }
}
if (-not $PythonCommand) { Write-Fail 'Python установлен, но запускаемый интерпретатор не найден. Откройте новое окно PowerShell и повторите команду.' }

Write-Step 'Определяю стандартную ветку публичного GitHub-репозитория…'
try {
    $Headers = @{ 'User-Agent' = 'ShlepaFlash-Installer/1.0'; 'Accept' = 'application/vnd.github+json' }
    $Meta = Invoke-RestMethod -Uri "https://api.github.com/repos/$Repository" -Headers $Headers -TimeoutSec 15
    $Branch = [string]$Meta.default_branch
    if ([string]::IsNullOrWhiteSpace($Branch)) { $Branch = 'main' }
} catch {
    Write-Fail "GitHub-репозиторий недоступен. Проверьте, что $Repository существует и открыт для чтения без авторизации. Подробность: $($_.Exception.Message)"
}

Write-Step "Скачиваю исходники из $Repository (ветка $Branch)…"
try {
    Invoke-WebRequest -Uri "https://github.com/$Repository/archive/refs/heads/$Branch.zip" -OutFile $ZipPath -TimeoutSec 60
    if (-not (Test-Path $ZipPath) -or (Get-Item $ZipPath).Length -lt 5000) { Write-Fail 'GitHub вернул пустой или слишком маленький архив.' }
    if (Test-Path $StageDir) { Remove-Item $StageDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $StageDir -Force
    $ProjectFile = Get-ChildItem -LiteralPath $StageDir -Filter 'pyproject.toml' -Recurse -File | Select-Object -First 1
    if (-not $ProjectFile) { Write-Fail 'В GitHub-архиве не найден pyproject.toml. Загружайте содержимое ZIP в корень репозитория, а не сам ZIP-файл.' }
    $StagedApp = $ProjectFile.Directory.FullName
} catch {
    Write-Fail "Не удалось скачать или распаковать репозиторий: $($_.Exception.Message)"
}

# Ensure a venv is available before switching source. On an update keep a source backup for recovery.
if (-not (Test-Path $VenvPython)) {
    Write-Step 'Создаю изолированное Python-окружение…'
    if ($PythonCommand.Count -eq 2) { & $PythonCommand[0] $PythonCommand[1] -m venv (Join-Path $InstallRoot '.venv') }
    else { & $PythonCommand[0] -m venv (Join-Path $InstallRoot '.venv') }
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VenvPython)) { Write-Fail 'Не удалось создать виртуальное окружение Python.' }
}

$HadOldApp = Test-Path $AppDir
try {
    if ($HadOldApp) { Move-Item -LiteralPath $AppDir -Destination $BackupDir }
    New-Item -ItemType Directory -Force -Path $AppDir | Out-Null
    Get-ChildItem -LiteralPath $StagedApp -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $AppDir -Recurse -Force
    }
    if (-not (Test-Path (Join-Path $AppDir 'pyproject.toml'))) { throw 'В целевой папке установки не найден pyproject.toml.' }
    Write-Step 'Устанавливаю зависимости и команду shlepaflash…'
    & $VenvPython -m pip install --disable-pip-version-check --upgrade pip
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось обновить pip.' }
    & $VenvPython -m pip install --disable-pip-version-check -e $AppDir
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить пакет и его зависимости из PyPI.' }
    & $VenvPython -c "import httpx, serial, PIL, esptool, textual; print('Зависимости: OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Проверка установленных зависимостей завершилась ошибкой.' }
} catch {
    if (Test-Path $AppDir) { Remove-Item -LiteralPath $AppDir -Recurse -Force -ErrorAction SilentlyContinue }
    if ($HadOldApp -and (Test-Path $BackupDir)) { Move-Item -LiteralPath $BackupDir -Destination $AppDir }
    Write-Fail "Установка не завершилась. Прежние исходники восстановлены, если они были. Детали: $($_.Exception.Message)"
}

# qFlipper is an official separate desktop app for Flipper Zero. Install best-effort;
# if winget/network/package availability blocks it, ShlepaFlash itself remains installed.
$QFlipperCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\qFlipper\qFlipper.exe'),
    (Join-Path $env:ProgramFiles 'qFlipper\qFlipper.exe'),
    (Join-Path $env:ProgramFiles 'Flipper Devices\qFlipper\qFlipper.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'qFlipper\qFlipper.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Flipper Devices\qFlipper\qFlipper.exe')
)
$QFlipperFound = $false
foreach ($Candidate in $QFlipperCandidates) { if (Test-Path $Candidate) { $QFlipperFound = $true; break } }
if (-not $QFlipperFound) {
    $Winget = Get-Command 'winget.exe' -ErrorAction SilentlyContinue
    if ($Winget) {
        Write-Step 'Пробую установить официальный qFlipper для Flipper Zero (необязательно)…'
        try {
            & $Winget.Source install --exact --id FlipperDevicesInc.qFlipper --silent --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -eq 0) { Write-Host 'qFlipper установлен/уже доступен.' -ForegroundColor Green }
            else { Write-Host 'qFlipper не удалось установить автоматически. Можно установить его позже: https://flipperzero.one/update' -ForegroundColor Yellow }
        } catch {
            Write-Host 'qFlipper не удалось установить автоматически. Клиент ESP32 работает независимо; установите qFlipper позже: https://flipperzero.one/update' -ForegroundColor Yellow
        }
    } else {
        Write-Host 'winget не найден, qFlipper не устанавливался. Для Flipper Zero установите его отдельно: https://flipperzero.one/update' -ForegroundColor Yellow
    }
}

$Launcher = Join-Path $BinDir 'shlepaflash.cmd'
$LauncherContent = @'
@echo off
setlocal
set "SF_ROOT=%LOCALAPPDATA%\ShlepaFlash"
"%SF_ROOT%\.venv\Scripts\python.exe" -m shlepaflash %*
endlocal
'@
Set-Content -LiteralPath $Launcher -Value $LauncherContent -Encoding ASCII

# Add only our small launcher directory to the user's PATH; do not use setx (it can truncate long PATH values).
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$PathEntries = @($UserPath -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
if (-not ($PathEntries | Where-Object { $_.TrimEnd('\') -ieq $BinDir.TrimEnd('\') })) {
    $NewUserPath = (@($PathEntries) + $BinDir) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $NewUserPath, 'User')
}
if ($env:Path -notlike "*$BinDir*") { $env:Path = "$BinDir;$env:Path" }

Remove-Item -LiteralPath $ZipPath -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $StageDir -Recurse -Force -ErrorAction SilentlyContinue
if (Test-Path $BackupDir) { Remove-Item -LiteralPath $BackupDir -Recurse -Force -ErrorAction SilentlyContinue }

Write-Host "`n╭────────────────────────────────────────────────╮" -ForegroundColor DarkYellow
Write-Host '│  SHLEPAFLASH УСПЕШНО УСТАНОВЛЕН                 │' -ForegroundColor White
Write-Host '╰────────────────────────────────────────────────╯' -ForegroundColor DarkYellow
Write-Host "Команда запуска: shlepaflash" -ForegroundColor Green
Write-Host "Каталог загрузок: $(Join-Path $InstallRoot 'downloads')" -ForegroundColor Gray
Write-Host "Журнал: $(Join-Path $InstallRoot 'logs\client.log')" -ForegroundColor Gray
Write-Host 'Если команда не распознаётся в другом окне, закройте терминал и откройте его заново.' -ForegroundColor Yellow
Write-Host 'Flipper Zero обновляется отдельным пакетом через qFlipper; ESP32 BIN проходит preflight перед записью.' -ForegroundColor Gray

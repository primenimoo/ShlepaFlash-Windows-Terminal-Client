$ErrorActionPreference = 'Stop'
$InstallRoot = Join-Path $env:LOCALAPPDATA 'ShlepaFlash'
$BinDir = Join-Path $InstallRoot 'bin'
$UserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$PathEntries = @($UserPath -split ';' | Where-Object { -not [string]::IsNullOrWhiteSpace($_) -and $_.TrimEnd('\') -ine $BinDir.TrimEnd('\') })
[Environment]::SetEnvironmentVariable('Path', ($PathEntries -join ';'), 'User')
if (Test-Path $BinDir) { Remove-Item -LiteralPath $BinDir -Recurse -Force }
Write-Host 'Команда shlepaflash удалена из PATH.' -ForegroundColor Green
Write-Host "Папка $InstallRoot и скачанные прошивки оставлены для возможности восстановления. Удалите её вручную, только если больше не нужны журналы и загрузки." -ForegroundColor Yellow

#requires -Version 5.1
<#
.SYNOPSIS
Подготовка сборщика НПА на Windows x64 с Python 3.9+.
.DESCRIPTION
Зависимости распаковываются в out/deps без установки в систему.
7-Zip загружается как локальный инструмент распаковки.
.PARAMETER WithTests
Установить также pytest для разработки и проверки проекта.
#>
param([switch]$WithTests)

$ErrorActionPreference = 'Stop'

function Get-PravoDownload {
    param([string]$Url, [string]$Target, [string]$Sha256)
    if (([uri]$Url).Scheme -ne 'https') { throw 'Для загрузки разрешён только HTTPS.' }
    if ((Test-Path -LiteralPath $Target) -and
        (Get-FileHash -LiteralPath $Target -Algorithm SHA256).Hash -eq $Sha256) { return }
    $taskPart = "$Target.$([guid]::NewGuid().ToString('N')).part"
    try {
        Write-Host "Загрузка: $([IO.Path]::GetFileName($Target))"
        Invoke-WebRequest -Uri $Url -OutFile $taskPart -UseBasicParsing -TimeoutSec 180
        if ((Get-FileHash -LiteralPath $taskPart -Algorithm SHA256).Hash -ne $Sha256) {
            throw "Не совпала SHA-256: $([IO.Path]::GetFileName($Target))"
        }
        Move-Item -LiteralPath $taskPart -Destination $Target -Force
    }
    finally {
        if (Test-Path -LiteralPath $taskPart) { Remove-Item -LiteralPath $taskPart }
    }
}

function Get-PravoPython {
    param([string]$Root)
    # py -3 и обычный python поддерживаются без привязки к компьютеру автора.
    if ($Root) {
        $taskExistingPython = Join-Path $Root '.venv\Scripts\python.exe'
        if (Test-Path -LiteralPath $taskExistingPython) {
            & $taskExistingPython -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return @{ Executable = $taskExistingPython; Prefix = @() }
            }
        }
    }
    foreach ($taskName in @('py', 'python', 'python3')) {
        $taskCommand = Get-Command $taskName -CommandType Application -ErrorAction SilentlyContinue
        if (-not $taskCommand) { continue }
        $taskPrefix = @()
        if ($taskName -eq 'py') { $taskPrefix = @('-3') }
        try {
            & $taskCommand.Source @taskPrefix -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                return @{ Executable = $taskCommand.Source; Prefix = $taskPrefix }
            }
        }
        catch { continue }
    }
    throw 'Установите Python 3.9+ с python.org (с модулем venv и pip), затем повторите настройку.'
}

function Install-Pravo {
    param([switch]$InstallTests)
    if ($env:OS -ne 'Windows_NT') { throw 'Этот установщик предназначен для Windows x64.' }
    $taskArch = $env:PROCESSOR_ARCHITEW6432
    if (-not $taskArch) { $taskArch = $env:PROCESSOR_ARCHITECTURE }
    if ($taskArch -ne 'AMD64') { throw 'Готовые OCR-программы рассчитаны на Windows x64.' }
    $taskRoot = Split-Path -Parent $PSScriptRoot
    $taskPython = Join-Path $taskRoot '.venv-pravo\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $taskPython)) {
        $taskSystemPython = Get-PravoPython -Root $taskRoot
        $taskPrefix = $taskSystemPython.Prefix
        & $taskSystemPython.Executable @taskPrefix -m venv (Join-Path $taskRoot '.venv-pravo')
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось создать .venv-pravo. Проверьте установку Python.' }
    }
    & $taskPython -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Для существующего .venv-pravo требуется Python 3.9+.' }
    $taskDeps = Join-Path $taskRoot 'out\deps'
    $taskDownloads = Join-Path $taskDeps 'downloads'
    New-Item -ItemType Directory -Force -Path $taskDownloads | Out-Null

    # Версии и SHA-256 закреплены; установщики EXE только распаковываются.
    # Источники: ip7z/7zip, UB-Mannheim/tesseract, oschwartz10612/poppler-windows,
    # tesseract-ocr/tessdata_fast. Ссылки на версии — в docs/PRAVO-SCRAPER.md.
    $taskArtifacts = @(
        @('7zr-25.01.exe', 'https://github.com/ip7z/7zip/releases/download/25.01/7zr.exe', '27CBE3D5804AD09E90BBCAA916DA0D5C3B0BE9462D0E0FB6CB54BE5ED9030875'),
        @('7zip-25.01-x64.exe', 'https://github.com/ip7z/7zip/releases/download/25.01/7z2501-x64.exe', '78AFA2A1C773CAF3CF7EDF62F857D2A8A5DA55FB0FFF5DA416074C0D28B2B55F'),
        @('tesseract-5.4.0.exe', 'https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe', 'C885FFF6998E0608BA4BB8AB51436E1C6775C2BAFC2559A19B423E18678B60C9'),
        @('poppler-25.07.0.zip', 'https://github.com/oschwartz10612/poppler-windows/releases/download/v25.07.0-0/Release-25.07.0-0.zip', '435E6265738470B7AE6498B8A298A4025BACB9349656207EF54D0F069687CE57'),
        @('rus.traineddata', 'https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/923915d4ced2a7235221788285785a29c4a42d4a/rus.traineddata', 'E16E5E036CCE1D9EC2B00063CF8B54472625B9E14D893A169E2B0DEDEB4DF225')
    )
    foreach ($taskArtifact in $taskArtifacts) {
        Get-PravoDownload -Url $taskArtifact[1] -Target (Join-Path $taskDownloads $taskArtifact[0]) -Sha256 $taskArtifact[2]
    }
    $taskArchiveDir = Join-Path $taskDeps '7zip-25.01'
    $taskArchiveTool = Join-Path $taskArchiveDir '7z.exe'
    if (-not (Test-Path -LiteralPath $taskArchiveTool)) {
        & (Join-Path $taskDownloads '7zr-25.01.exe') x -y "-o$taskArchiveDir" (Join-Path $taskDownloads '7zip-25.01-x64.exe') | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось распаковать локальный 7-Zip.' }
    }
    $taskTessDir = Join-Path $taskDeps 'tesseract'
    $taskTesseract = Join-Path $taskTessDir 'tesseract.exe'
    if (-not (Test-Path -LiteralPath $taskTesseract)) {
        & $taskArchiveTool x -y "-o$taskTessDir" (Join-Path $taskDownloads 'tesseract-5.4.0.exe') | Out-Host
        # NSIS-дистрибутив имеет дополнительный хвост; 7-Zip выдаёт предупреждение 1.
        if ($LASTEXITCODE -gt 1) { throw 'Не удалось распаковать Tesseract.' }
    }
    $taskPopplerDir = Join-Path $taskDeps 'poppler'
    $taskPopplerBin = Join-Path $taskPopplerDir 'poppler-25.07.0\Library\bin'
    if (-not (Test-Path -LiteralPath (Join-Path $taskPopplerBin 'pdftotext.exe')) -or
        -not (Test-Path -LiteralPath (Join-Path $taskPopplerBin 'pdftoppm.exe'))) {
        Expand-Archive -LiteralPath (Join-Path $taskDownloads 'poppler-25.07.0.zip') -DestinationPath $taskPopplerDir -Force
    }
    Copy-Item -LiteralPath (Join-Path $taskDownloads 'rus.traineddata') -Destination (Join-Path $taskTessDir 'tessdata\rus.traineddata') -Force
    $taskLanguages = @(& $taskTesseract --list-langs)
    if ($LASTEXITCODE -ne 0 -or $taskLanguages -notcontains 'rus') {
        throw 'Tesseract не запустился или русская модель недоступна.'
    }
    foreach ($taskProgram in @('pdftotext.exe', 'pdftoppm.exe')) {
        & (Join-Path $taskPopplerBin $taskProgram) -v
        if ($LASTEXITCODE -ne 0) { throw "Не запускается $taskProgram. Проверьте системные библиотеки Windows." }
    }
    $taskInstallTarget = $taskRoot
    if ($InstallTests) { $taskInstallTarget = "$taskRoot[test]" }
    & $taskPython -m pip install -e $taskInstallTarget
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить Python-пакет проекта.' }

    # Относительные пути позволяют перенести checkout без путей компьютера автора.
    $taskConfig = [ordered]@{
        schema_version = 1
        tesseract = 'out/deps/tesseract/tesseract.exe'
        tessdata = 'out/deps/tesseract/tessdata'
        pdftotext = 'out/deps/poppler/poppler-25.07.0/Library/bin/pdftotext.exe'
        pdftoppm = 'out/deps/poppler/poppler-25.07.0/Library/bin/pdftoppm.exe'
    }
    [IO.File]::WriteAllText((Join-Path $taskDeps 'pravo-tools.json'), ($taskConfig | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    Write-Host 'Готово. Запуск: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_pravo.ps1 --help'
}

# Dot-source нужен только для офлайн-тестирования функций загрузки.
if ($MyInvocation.InvocationName -ne '.') {
    $taskOldProtocol = [Net.ServicePointManager]::SecurityProtocol
    $ProgressPreference = 'SilentlyContinue'
    try {
        [Net.ServicePointManager]::SecurityProtocol = $taskOldProtocol -bor [Net.SecurityProtocolType]::Tls12
        Install-Pravo -InstallTests:$WithTests
        exit 0
    }
    catch {
        Write-Error "Настройка не завершена: $($_.Exception.Message)" -ErrorAction Continue
        exit 1
    }
    finally { [Net.ServicePointManager]::SecurityProtocol = $taskOldProtocol }
}

#requires -Version 5.1
# Все аргументы передаются Python CLI; без аргументов показывается --help.
$ErrorActionPreference = 'Stop'

function Test-PravoOption {
    param([string[]]$Arguments, [string]$Name)
    foreach ($taskArgument in $Arguments) {
        if ($taskArgument -eq $Name -or $taskArgument.StartsWith("$Name=")) { return $true }
    }
    return $false
}

function Get-PravoScanBudget {
    param([string[]]$Arguments)
    $taskValue = '0'
    for ($taskIndex = 0; $taskIndex -lt $Arguments.Count; $taskIndex++) {
        if ($Arguments[$taskIndex].StartsWith('--scan-omnibus=')) {
            $taskValue = $Arguments[$taskIndex].Substring('--scan-omnibus='.Length)
        }
        elseif ($Arguments[$taskIndex] -eq '--scan-omnibus' -and $taskIndex + 1 -lt $Arguments.Count) {
            $taskValue = $Arguments[$taskIndex + 1]
        }
    }
    $taskBudget = 0
    [void][int]::TryParse($taskValue, [ref]$taskBudget)
    return $taskBudget
}

function Resolve-PravoTool {
    param([string]$Root, [string]$RelativePath)
    if (-not $RelativePath -or [IO.Path]::IsPathRooted($RelativePath)) {
        throw 'Неверный путь в out/deps/pravo-tools.json. Повторите setup_pravo.ps1.'
    }
    $taskFullPath = [IO.Path]::GetFullPath((Join-Path $Root $RelativePath))
    $taskAllowed = [IO.Path]::GetFullPath((Join-Path $Root 'out\deps')) + [IO.Path]::DirectorySeparatorChar
    if (-not $taskFullPath.StartsWith($taskAllowed, [StringComparison]::OrdinalIgnoreCase) -or
        -not (Test-Path -LiteralPath $taskFullPath)) {
        throw 'OCR-зависимость не найдена в out/deps. Повторите setup_pravo.ps1.'
    }
    return $taskFullPath
}

function ConvertTo-PravoNativeArgument {
    param([AllowEmptyString()][string]$Value)
    # Правила Windows argv: кавычки и завершающие обратные слеши экранируются.
    # PS 5.1 иначе разбивает аргумент вида --ocr="путь с пробелами".
    $taskEscaped = [regex]::Replace($Value, '(\\*)"', '$1$1\"')
    $taskEscaped = [regex]::Replace($taskEscaped, '(\\+)$', '$1$1')
    return '"' + $taskEscaped + '"'
}

$taskOldPath = $env:PATH
$taskOldTessdata = $env:TESSDATA_PREFIX
$taskOldEncoding = $env:PYTHONIOENCODING
$taskOldConsoleEncoding = [Console]::OutputEncoding
$taskExitCode = 1
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Этот скрипт предназначен для Windows.' }
    $taskRoot = Split-Path -Parent $PSScriptRoot
    $taskPython = Join-Path $taskRoot '.venv-pravo\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $taskPython)) {
        throw 'Сначала выполните: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_pravo.ps1'
    }
    $taskUserArguments = @($args)
    if ($taskUserArguments.Count -eq 0) { $taskUserArguments = @('--help') }
    $taskDefaults = @()
    $taskHelp = ($taskUserArguments -contains '--help') -or ($taskUserArguments -contains '-h')
    $taskMetadataOnly = Test-PravoOption $taskUserArguments '--metadata-only'
    $taskScanBudget = Get-PravoScanBudget $taskUserArguments
    if (-not $taskHelp -and (-not $taskMetadataOnly -or $taskScanBudget -gt 0)) {
        $taskConfigPath = Join-Path $taskRoot 'out\deps\pravo-tools.json'
        if (-not (Test-Path -LiteralPath $taskConfigPath)) {
            throw 'Сначала выполните scripts/setup_pravo.ps1: OCR-зависимости не настроены.'
        }
        $taskConfig = Get-Content -Raw -Encoding UTF8 -LiteralPath $taskConfigPath | ConvertFrom-Json
        if ($taskConfig.schema_version -ne 1) { throw 'Неизвестная версия pravo-tools.json. Повторите настройку.' }
        $taskTesseract = Resolve-PravoTool $taskRoot $taskConfig.tesseract
        $taskTessdata = Resolve-PravoTool $taskRoot $taskConfig.tessdata
        $taskPdfToText = Resolve-PravoTool $taskRoot $taskConfig.pdftotext
        $taskPdfToPpm = Resolve-PravoTool $taskRoot $taskConfig.pdftoppm
        $env:PATH = "$(Split-Path -Parent $taskPdfToPpm);$(Split-Path -Parent $taskTesseract);$taskOldPath"
        $env:TESSDATA_PREFIX = $taskTessdata
        if (-not (Test-PravoOption $taskUserArguments '--pdftotext')) { $taskDefaults += @('--pdftotext', $taskPdfToText) }
        if (-not (Test-PravoOption $taskUserArguments '--ocr')) { $taskDefaults += @('--ocr', $taskTesseract) }
        if (-not (Test-PravoOption $taskUserArguments '--ocr-lang')) { $taskDefaults += @('--ocr-lang', 'rus') }
    }
    $env:PYTHONIOENCODING = 'utf-8'
    $taskNativeArguments = @((Join-Path $taskRoot 'tools\pravo_scraper.py')) + $taskDefaults + $taskUserArguments
    $taskStartInfo = [Diagnostics.ProcessStartInfo]::new()
    $taskStartInfo.FileName = $taskPython
    $taskStartInfo.Arguments = ($taskNativeArguments | ForEach-Object { ConvertTo-PravoNativeArgument $_ }) -join ' '
    $taskStartInfo.WorkingDirectory = (Get-Location).ProviderPath
    $taskStartInfo.UseShellExecute = $false
    $taskStartInfo.CreateNoWindow = $true
    $taskStartInfo.RedirectStandardOutput = $true
    $taskStartInfo.RedirectStandardError = $true
    $taskStartInfo.StandardOutputEncoding = [Text.Encoding]::UTF8
    $taskStartInfo.StandardErrorEncoding = [Text.Encoding]::UTF8
    $taskProcess = [Diagnostics.Process]::new()
    try {
        $taskProcess.StartInfo = $taskStartInfo
        [void]$taskProcess.Start()
        # Читаем оба потока одновременно: заполненный stderr не блокирует Python.
        $taskStdout = $taskProcess.StandardOutput.ReadToEndAsync()
        $taskStderr = $taskProcess.StandardError.ReadToEndAsync()
        $taskProcess.WaitForExit()
        $taskExitCode = $taskProcess.ExitCode
        [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
        [Console]::Out.Write($taskStdout.Result)
        [Console]::Error.Write($taskStderr.Result)
    }
    finally { $taskProcess.Dispose() }
}
catch {
    Write-Error "Запуск не выполнен: $($_.Exception.Message)" -ErrorAction Continue
    $taskExitCode = 1
}
finally {
    $env:PATH = $taskOldPath
    $env:TESSDATA_PREFIX = $taskOldTessdata
    $env:PYTHONIOENCODING = $taskOldEncoding
    [Console]::OutputEncoding = $taskOldConsoleEncoding
}
exit $taskExitCode

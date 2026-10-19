param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# Pinned tessdata_fast models; keep in sync with .github/workflows/ci.yml.
$tessdataRef = "87416418657359cb625c412a48b6e1d6d41c29bd"
$requiredModels = [ordered]@{
    "eng.traineddata" = "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2"
    "rus.traineddata" = "e16e5e036cce1d9ec2b00063cf8b54472625b9e14d893a169e2b0dedeb4df225"
}

function Invoke-Native([scriptblock]$Command, [string]$FailureMessage) {
    # Windows PowerShell 5.1 turns every stderr line of a native program into an
    # error record, which "Stop" makes terminating once the output is redirected
    # (*> build.log). Log stderr as plain text and judge by the exit code instead.
    $ErrorActionPreference = "Continue"
    & $Command 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
    }
    if ($LASTEXITCODE -ne 0) {
        throw "$FailureMessage (код выхода $LASTEXITCODE)."
    }
}

function Find-TesseractRoot {
    if ($env:KONSPEKT_TESSERACT_DIR) {
        if (-not (Test-Path -LiteralPath (Join-Path $env:KONSPEKT_TESSERACT_DIR "tesseract.exe"))) {
            throw "В KONSPEKT_TESSERACT_DIR нет tesseract.exe: $env:KONSPEKT_TESSERACT_DIR"
        }
        return $env:KONSPEKT_TESSERACT_DIR
    }
    $command = Get-Command "tesseract.exe" -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($command) {
        return Split-Path -Parent $command.Source
    }
    $default = Join-Path $env:ProgramFiles "Tesseract-OCR"
    if (Test-Path -LiteralPath (Join-Path $default "tesseract.exe")) {
        return $default
    }
    throw "Не найден Tesseract. Установи его: winget install UB-Mannheim.TesseractOCR"
}

function Test-FileHash([string]$Path, [string]$Sha256) {
    (Test-Path -LiteralPath $Path) -and
        (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -eq $Sha256
}

function Save-PinnedModel([string]$Name, [string]$Destination) {
    $uri = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/$tessdataRef/$Name"
    $partial = "$Destination.download"
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
    for ($attempt = 1; ; $attempt++) {
        try {
            Invoke-WebRequest -Uri $uri -OutFile $partial -UseBasicParsing
            break
        } catch {
            if ($attempt -ge 3) {
                throw "Не удалось скачать $Name из tessdata_fast@${tessdataRef}: $($_.Exception.Message)"
            }
            Write-Host "Download of $Name failed, retrying: $($_.Exception.Message)" -ForegroundColor Yellow
            Start-Sleep -Seconds (5 * $attempt)
        }
    }
    if (-not (Test-FileHash $partial $requiredModels[$Name])) {
        Remove-Item -LiteralPath $partial -Force
        throw "Контрольная сумма $Name из tessdata_fast@$tessdataRef не совпала."
    }
    Move-Item -LiteralPath $partial -Destination $Destination -Force
}

function Get-ProjectVersion {
    $inProject = $false
    foreach ($line in Get-Content -LiteralPath "pyproject.toml" -Encoding UTF8) {
        if ($line -match '^\s*\[(.+)\]\s*$') {
            $inProject = $Matches[1].Trim() -eq "project"
        } elseif ($inProject -and $line -match '^\s*version\s*=\s*"([^"]+)"') {
            return $Matches[1]
        }
    }
    throw "Не удалось прочитать версию из pyproject.toml."
}

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Не найдено .venv. Сначала запусти .\setup_local_ai.ps1."
}
$version = Get-ProjectVersion

# The spec bundles this Tesseract runtime. Language models it lacks are taken
# from the pinned tessdata_fast commit, so the package always has eng and rus.
$tesseractRoot = Find-TesseractRoot
$extraModels = @()
foreach ($model in $requiredModels.Keys) {
    $installed = Join-Path $tesseractRoot "tessdata\$model"
    if ((Test-Path -LiteralPath $installed) -and (Get-Item -LiteralPath $installed).Length -gt 0) {
        continue
    }
    $cached = Join-Path $projectRoot "build\tessdata_fast\$tessdataRef\$model"
    if (-not (Test-FileHash $cached $requiredModels[$model])) {
        Write-Host "Fetching $model from tessdata_fast@$tessdataRef..." -ForegroundColor Cyan
        Save-PinnedModel $model $cached
    }
    $extraModels += $cached
}
Write-Host "Tesseract runtime: $tesseractRoot" -ForegroundColor Cyan
foreach ($model in $extraModels) {
    Write-Host "Adding pinned model: $model" -ForegroundColor Cyan
}

Write-Host "Installing build dependencies..." -ForegroundColor Cyan
Invoke-Native { & $python -m pip install -e ".[dev,local-ai]" } "Не удалось установить зависимости для сборки"

if (-not $SkipTests) {
    Write-Host "Running tests..." -ForegroundColor Cyan
    Invoke-Native { & $python -m unittest discover -s tests -v } "Сборка остановлена: тесты не прошли"
}

Write-Host "Building Konspekt.exe $version..." -ForegroundColor Cyan
Invoke-Native { & $python scripts\make_icon.py } "Не удалось подготовить иконку"
$savedTesseractDir = $env:KONSPEKT_TESSERACT_DIR
$savedExtraTessdata = $env:KONSPEKT_EXTRA_TESSDATA
try {
    $env:KONSPEKT_TESSERACT_DIR = $tesseractRoot
    $env:KONSPEKT_EXTRA_TESSDATA = $extraModels -join [IO.Path]::PathSeparator
    Invoke-Native { & $python -m PyInstaller --noconfirm --clean packaging\Konspekt.spec } "Не удалось собрать Konspekt.exe"
} finally {
    $env:KONSPEKT_TESSERACT_DIR = $savedTesseractDir
    $env:KONSPEKT_EXTRA_TESSDATA = $savedExtraTessdata
}

Copy-Item -LiteralPath "docs\release.md" -Destination "dist\Konspekt\README.md" -Force

Write-Host "Smoke-testing the package..." -ForegroundColor Cyan
Invoke-Native { & $python scripts\smoke_package.py --artifact dist\Konspekt } "Собранный пакет не прошёл smoke-тест"

$archive = "dist\Konspekt-$version-windows-x64.zip"
Write-Host "Packing $archive..." -ForegroundColor Cyan
if (Test-Path -LiteralPath $archive) {
    Remove-Item -LiteralPath $archive -Force
}
Invoke-Native {
    & $python -c "import shutil, sys; shutil.make_archive(sys.argv[1], 'zip', 'dist', 'Konspekt')" ($archive -replace '\.zip$', '')
} "Не удалось упаковать $archive"

Write-Host ""
Write-Host "Ready: dist\Konspekt\Konspekt.exe" -ForegroundColor Green
Write-Host "Release archive: $archive" -ForegroundColor Green
Write-Host "Copy the whole dist\Konspekt folder; do not move the exe out of it." -ForegroundColor Green

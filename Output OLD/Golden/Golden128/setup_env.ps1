param(
    [string]$EnvName = "ollama-vision-py312",
    [string]$RequirementsFile = "requirements.txt"
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Requirements = Join-Path $ProjectDir $RequirementsFile
$VenvRoot = Join-Path $env:LOCALAPPDATA "ScriptDataset\venvs"
$VenvDir = Join-Path $VenvRoot $EnvName
$PythonExe = $null

$Candidates = @(
    (Join-Path $env:LOCALAPPDATA "Python\pythoncore-3.12-64\python.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\python.exe")
)

foreach ($Candidate in $Candidates) {
    if (Test-Path $Candidate) {
        $PythonExe = $Candidate
        break
    }
}

if (-not $PythonExe) {
    $Command = Get-Command python3.12 -ErrorAction SilentlyContinue
    if ($Command) {
        $PythonExe = $Command.Source
    }
}

if (-not $PythonExe) {
    throw "Python 3.12 non trovato. Installa Python 3.12 e rilancia questo script."
}

if (-not (Test-Path $Requirements)) {
    throw "File requirements non trovato: $Requirements"
}

New-Item -ItemType Directory -Force -Path $VenvRoot | Out-Null

if (-not (Test-Path (Join-Path $VenvDir "Scripts\python.exe"))) {
    & $PythonExe -m venv $VenvDir
}

$VenvPython = Join-Path $VenvDir "Scripts\python.exe"

& $VenvPython -m pip install --upgrade pip setuptools wheel
& $VenvPython -m pip install -r $Requirements
& $VenvPython -m pip check

Write-Host ""
Write-Host "Ambiente pronto:" $VenvDir
Write-Host "Per eseguire lo script:"
Write-Host "& `"$VenvPython`" `"$ProjectDir\estrai_atti_OLLAMA_2.py`""

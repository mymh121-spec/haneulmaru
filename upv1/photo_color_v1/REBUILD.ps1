param([string]$SourceZip)
$ErrorActionPreference = 'Stop'
$datasetRepo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
if (-not $SourceZip) {
    Write-Host 'Download COCO ZIP: https://universe.roboflow.com/ai-bmkoo/drone-detection-inlmy/dataset/1/download/coco'
    $SourceZip = (Read-Host 'Path to downloaded COCO ZIP').Trim('"')
}
if (-not (Test-Path -LiteralPath $SourceZip -PathType Leaf)) { throw 'COCO ZIP was not found.' }
$datasetSource = (Resolve-Path -LiteralPath $SourceZip).Path
$datasetEnv = Join-Path $datasetRepo '.venv-photo-color'
$datasetPython = Join-Path $datasetEnv 'Scripts/python.exe'
if (-not (Test-Path -LiteralPath $datasetPython)) {
    if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m venv $datasetEnv }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { & python -m venv $datasetEnv }
    else { throw 'Python 3.10+ from python.org is required.' }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create local Python environment.' }
}
& $datasetPython -c 'import PIL'
if ($LASTEXITCODE -ne 0) {
    & $datasetPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Pillow installation failed.' }
}
$datasetOutput = Join-Path $datasetRepo ('output/photo_color_' + (Get-Date -Format 'yyyyMMdd_HHmmss'))
& $datasetPython -B -X utf8 (Join-Path $PSScriptRoot 'build_dataset.py') --source $datasetSource --output $datasetOutput --count 300
if ($LASTEXITCODE -ne 0) { throw 'Build failed. Review the message above.' }
Write-Host ('Dataset ready: ' + $datasetOutput)
Write-Host 'Open the HTML file inside the output directory for an offline photo preview.'
Start-Process explorer.exe -ArgumentList ('"' + $datasetOutput + '"') -WindowStyle Hidden

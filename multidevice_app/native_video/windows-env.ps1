param([switch]$Install)
$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$nativeRoot = Join-Path $repoRoot '.native-deps'
$opencvRoot = Join-Path $nativeRoot 'opencv/build'
$clangRoot = Join-Path $nativeRoot 'clang/clang/native'
if ($Install) {
    New-Item -ItemType Directory -Force $nativeRoot | Out-Null
    if (-not (Test-Path (Join-Path $opencvRoot 'include/opencv2/core.hpp'))) {
        $archive = Join-Path $nativeRoot 'opencv-4.11.0-windows.exe'
        if (-not (Test-Path $archive)) {
            Invoke-WebRequest 'https://github.com/opencv/opencv/releases/download/4.11.0/opencv-4.11.0-windows.exe' -OutFile $archive
        }
        if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne '7c9d1c0b70db1b1952cc815252fced9a07f51267563cf3eaa1674d734c49b8e4') { throw 'OpenCV checksum mismatch' }
        $process = Start-Process -FilePath $archive -ArgumentList @('-y', ('-o"' + $nativeRoot + '"')) -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -ne 0) { throw 'OpenCV extraction failed' }
    }
}
if (-not (Test-Path $opencvRoot)) {
    throw 'OpenCV SDK missing. Run windows-env.ps1 -Install, or configure OpenCV manually.'
}
$env:OPENCV_LINK_LIBS = 'opencv_world4110'
$env:OPENCV_LINK_PATHS = Join-Path $opencvRoot 'x64/vc16/lib'
$env:OPENCV_INCLUDE_PATHS = Join-Path $opencvRoot 'include'
$env:PATH = (Join-Path $opencvRoot 'x64/vc16/bin') + ';' + $env:PATH
$portableClang = Join-Path $nativeRoot 'clang+llvm-18.1.8-x86_64-pc-windows-msvc/bin'
if (Test-Path (Join-Path $portableClang 'clang.exe')) { $env:PATH = $portableClang + ';' + $env:PATH }
elseif (Test-Path 'C:/Program Files/LLVM/bin/clang.exe') { $env:PATH = 'C:/Program Files/LLVM/bin;' + $env:PATH }
if (-not (Get-Command clang -ErrorAction SilentlyContinue)) { throw 'clang.exe is required. Install LLVM or add its bin directory to PATH.' }
$clangBin = Split-Path (Get-Command clang).Source
# clang.exe supplies system/resource headers. libclang must come from the same
# LLVM installation; mixing LLVM 20 headers with the PyPI LLVM 18 parser fails.
if (Test-Path (Join-Path $clangBin 'libclang.dll')) {
    $clangRoot = $clangBin
} else {
    $clangVersion = (& clang --version | Select-Object -First 1)
    if ($clangVersion -notmatch 'clang version 18\.') {
        throw 'Matching libclang.dll is missing beside clang.exe. Install the complete LLVM distribution.'
    }
    if ($Install -and -not (Test-Path (Join-Path $clangRoot 'libclang.dll'))) {
        python -m pip install --target (Join-Path $nativeRoot 'clang') libclang==18.1.1
        if ($LASTEXITCODE -ne 0) { throw 'libclang installation failed' }
    }
    if (-not (Test-Path (Join-Path $clangRoot 'libclang.dll'))) {
        throw 'LLVM 18 libclang missing. Run windows-env.ps1 -Install.'
    }
}
$env:LIBCLANG_PATH = $clangRoot
$env:PATH = $clangRoot + ';' + $env:PATH

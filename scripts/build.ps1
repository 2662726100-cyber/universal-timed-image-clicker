$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $RepoRoot '.venv\Scripts\python.exe'
$PyInstaller = Join-Path $RepoRoot '.venv\Scripts\pyinstaller.exe'

if (-not (Test-Path -LiteralPath $Python)) {
    py -m venv (Join-Path $RepoRoot '.venv')
}

& $Python -m pip install --disable-pip-version-check -r (Join-Path $RepoRoot 'requirements-dev.txt')
& $Python -m unittest discover -s (Join-Path $RepoRoot 'tests') -v

& $PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name '通用定时图像点击器' `
    --distpath (Join-Path $RepoRoot 'dist') `
    --workpath (Join-Path $RepoRoot 'build') `
    --specpath $RepoRoot `
    --collect-all cv2 `
    (Join-Path $RepoRoot 'src\universal_clicker.py')

Write-Host "构建完成：$RepoRoot\dist\通用定时图像点击器.exe"

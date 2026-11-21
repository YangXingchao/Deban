#Requires -Version 5.1
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Tauri = Join-Path $Root 'src-tauri'
$Backend = Join-Path $Tauri 'backend'
$Cache = Join-Path $Root '.build-cache'
$Name = 'cpython-3.12.14+20260901-x86_64-pc-windows-msvc-install_only_stripped.tar.gz'
$Hash = '7c45c9622400d578709a9b2cddbe8124cc21d382409d9f13406d706d28e31b14'
foreach ($cmd in @('uv', 'npm', 'cargo', 'tar')) {
  if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) { throw "$cmd is required" }
}
& cargo tauri --version
if ($LASTEXITCODE -ne 0) { throw 'Install tauri-cli version 2 with cargo install tauri-cli --version ^2 --locked' }
New-Item -ItemType Directory -Force $Cache | Out-Null
$Archive = Join-Path $Cache $Name
if (-not (Test-Path $Archive)) {
  $Part = "$Archive.part"
  Invoke-WebRequest -Uri "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/$Name" -OutFile $Part
  Move-Item $Part $Archive
}
if ((Get-FileHash -Algorithm SHA256 $Archive).Hash.ToLowerInvariant() -ne $Hash) { throw 'Python archive checksum mismatch' }
Push-Location $Root
try {
  & npm --prefix ui ci --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw 'npm ci failed' }
  & npm --prefix ui run build
  if ($LASTEXITCODE -ne 0) { throw 'frontend build failed' }
  $Marker = Join-Path $Backend '.deban-generated'
  if ((Test-Path (Join-Path $Backend 'python')) -and -not (Test-Path $Marker)) { throw 'Refusing to replace an unrecognized backend directory' }
  foreach ($dir in @('python', 'approot')) {
    $target = Join-Path $Backend $dir
    if (Test-Path $target) { Remove-Item -Recurse -Force $target }
  }
  New-Item -ItemType Directory -Force $Backend | Out-Null
  New-Item -ItemType File -Force $Marker | Out-Null
  & tar -xzf $Archive -C $Backend
  if ($LASTEXITCODE -ne 0) { throw 'Python extraction failed' }
  $Python = Join-Path $Backend 'python\python.exe'
  $Requirements = Join-Path $Cache 'requirements.txt'
  & uv export --frozen --no-dev --no-emit-project --no-hashes --format requirements-txt --quiet -o $Requirements
  if ($LASTEXITCODE -ne 0) { throw 'Dependency export failed' }
  & uv pip install --python $Python -r $Requirements
  if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
  $AppRoot = Join-Path $Backend 'approot'
  New-Item -ItemType Directory -Force (Join-Path $AppRoot 'ui') | Out-Null
  Copy-Item -Recurse (Join-Path $Root 'app') $AppRoot
  Get-ChildItem -Path (Join-Path $AppRoot 'app') -Directory -Filter '__pycache__' -Recurse | Remove-Item -Recurse -Force
  Copy-Item (Join-Path $Root 'main.py') $AppRoot
  Copy-Item -Recurse (Join-Path $Root 'ui\dist') (Join-Path $AppRoot 'ui')
  Push-Location $Tauri
  try {
    & cargo tauri build
    if ($LASTEXITCODE -ne 0) { throw 'Windows application build failed' }
  } finally { Pop-Location }
} finally { Pop-Location }

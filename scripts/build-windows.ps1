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
$TauriCommand = Get-Command tauri -ErrorAction SilentlyContinue
if ($TauriCommand) { & $TauriCommand.Source --version }
else { & cargo tauri --version }
if ($LASTEXITCODE -ne 0) { throw 'Install Tauri CLI 2 with npm install -g @tauri-apps/cli@2.12.6 or cargo install tauri-cli --version ^2 --locked' }
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
  # PyTorch's DLLs need a matching VC++ runtime even on clean machines. Keep
  # the complete set together beside c10.dll, as in the validated build.
  $RuntimeNames = @('msvcp140.dll', 'msvcp140_atomic_wait.dll', 'vcruntime140.dll', 'vcruntime140_1.dll')
  $RuntimeDir = Join-Path $env:SystemRoot 'System32'
  if (@($RuntimeNames | Where-Object { -not (Test-Path (Join-Path $RuntimeDir $_)) }).Count -gt 0) {
    $RedistRoot = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio'
    $RuntimeDir = Get-ChildItem -Path $RedistRoot -Recurse -Filter 'msvcp140.dll' -File -ErrorAction SilentlyContinue |
      Where-Object { $_.FullName -match '\\x64\\Microsoft\.VC[0-9]+\.CRT\\msvcp140\.dll$' } |
      Sort-Object { $_.VersionInfo.FileVersion } -Descending | Select-Object -First 1 -ExpandProperty DirectoryName
  }
  if (-not $RuntimeDir) { throw 'The x64 VC++ runtime was not found; install the MSVC redistributable.' }
  $TorchLib = Join-Path $Backend 'python\Lib\site-packages\torch\lib'
  if (-not (Test-Path $TorchLib)) { throw "PyTorch DLL directory missing: $TorchLib" }
  foreach ($name in $RuntimeNames) {
    $source = Join-Path $RuntimeDir $name
    if (-not (Test-Path $source)) { throw "Required VC++ runtime DLL missing: $source" }
    Copy-Item -LiteralPath $source -Destination (Join-Path $TorchLib $name) -Force
  }
  $AppRoot = Join-Path $Backend 'approot'
  New-Item -ItemType Directory -Force (Join-Path $AppRoot 'ui') | Out-Null
  Copy-Item -Recurse (Join-Path $Root 'app') $AppRoot
  Get-ChildItem -Path (Join-Path $AppRoot 'app') -Directory -Filter '__pycache__' -Recurse | Remove-Item -Recurse -Force
  Copy-Item (Join-Path $Root 'main.py') $AppRoot
  Copy-Item -Recurse (Join-Path $Root 'ui\dist') (Join-Path $AppRoot 'ui')
  & $Python -m compileall -q -b -s $AppRoot -p '.' (Join-Path $AppRoot 'app') (Join-Path $AppRoot 'main.py')
  if ($LASTEXITCODE -ne 0) { throw 'Backend bytecode compilation failed' }
  Get-ChildItem -Path (Join-Path $AppRoot 'app') -Filter '*.py' -File -Recurse | Remove-Item -Force
  Remove-Item -LiteralPath (Join-Path $AppRoot 'main.py') -Force
  if (-not (Test-Path (Join-Path $AppRoot 'main.pyc')) -or @(Get-ChildItem -Path (Join-Path $AppRoot 'app') -Filter '*.py' -File -Recurse).Count -ne 0) { throw 'Backend source stripping failed' }
  Push-Location $Tauri
  try {
    if ($TauriCommand) { & $TauriCommand.Source build }
    else { & cargo tauri build }
    if ($LASTEXITCODE -ne 0) { throw 'Windows application build failed' }
  } finally { Pop-Location }
} finally { Pop-Location }

param(
    [ValidateSet('gui', 'debug', 'both')]
    [string]$Target = 'both'
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Invoke-CheckedPython {
    param([string]$Python, [string[]]$PythonArgs)
    & $Python @PythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Python step failed (exit $LASTEXITCODE): $PythonArgs"
    }
}

Push-Location $scriptDir
try {
    $pythonExe = Join-Path $scriptDir 'venv-arayashu/Scripts/python.exe'
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        & py -3.12 -m venv (Join-Path $scriptDir 'venv-arayashu')
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required to create the build environment.' }
    }

    Invoke-CheckedPython -Python $pythonExe -PythonArgs @('-m', 'compileall', '-q', 'App.py', 'Bot.py', 'source', 'source_app', 'tests')
    Invoke-CheckedPython -Python $pythonExe -PythonArgs @('-m', 'unittest', 'discover', '-s', 'tests', '-v')
    Invoke-CheckedPython -Python $pythonExe -PythonArgs @('-m', 'pip', 'install', '-r', 'requirements.txt', 'nuitka', 'ordered-set', 'zstandard')
    $buildArgs = @('release/windows/nuitka-windows.py')
    if ($Target -eq 'gui') { $buildArgs += '--gui-only' }
    if ($Target -eq 'debug') { $buildArgs += '--debug-only' }
    Invoke-CheckedPython -Python $pythonExe -PythonArgs $buildArgs

    $expected = (Get-Content version -Raw).Trim() + '.0'
    $names = if ($Target -eq 'gui') { @('app.exe') } elseif ($Target -eq 'debug') { @('app_debug.exe') } else { @('app.exe', 'app_debug.exe') }
    $hashes = foreach ($name in $names) {
        $file = Join-Path build $name
        if (-not (Test-Path -LiteralPath $file)) { throw "Missing executable: $file" }
        $actual = [System.Diagnostics.FileVersionInfo]::GetVersionInfo((Resolve-Path $file)).FileVersion
        if ($actual -ne $expected) { throw "Version mismatch: $actual, expected $expected" }
        $hash = (Get-FileHash $file -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $name"
    }
    $hashes | Set-Content 'build/SHA256SUMS.txt' -Encoding ascii
    Write-Host 'Built and checked executable(s) in build/. A live game test is still required.'
}
finally {
    Pop-Location
}

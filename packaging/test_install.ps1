param([switch]$TestMicrophone, [switch]$TestLlm)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$qaRoot = Join-Path $project 'packaging-qa'
$installDir = [IO.Path]::GetFullPath((Join-Path $qaRoot 'installed-test\Sentinel'))
if (!$installDir.StartsWith([IO.Path]::GetFullPath($qaRoot) + [IO.Path]::DirectorySeparatorChar)) { throw 'Install target escaped QA workspace' }
$setup = Join-Path $project 'release\Sentinel-Setup.exe'
if (!(Test-Path -LiteralPath $setup)) { throw 'Build installer first' }
$dataRoot = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Sentinel'
$markerDir = Join-Path $dataRoot ('.packaging-canary-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $markerDir | Out-Null
@('notes.jsonl','voice-profile.json','preferences.json') | ForEach-Object {
    [IO.File]::WriteAllText((Join-Path $markerDir $_), 'Sentinel installer preservation test, not user data')
}
function Snapshot-Data {
    $snapshot = @{}
    Get-ChildItem -LiteralPath $dataRoot -Recurse -File | ForEach-Object {
        $snapshot[$_.FullName] = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }
    return $snapshot
}
function Assert-Preserved($before, $stage) {
    foreach ($name in $before.Keys) {
        if (!(Test-Path -LiteralPath $name) -or (Get-FileHash -LiteralPath $name -Algorithm SHA256).Hash -ne $before[$name]) {
            throw ('User data changed at ' + $stage)
        }
    }
}
$before = Snapshot-Data
$results = [ordered]@{ install_dir=$installDir; default_user_data=$dataRoot; user_files_checked=$before.Count }
try {
    foreach ($stage in @('install','upgrade')) {
        $arguments = @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/SP-','/TASKS=',('/DIR="'+$installDir+'"'),('/LOG="'+(Join-Path $qaRoot ($stage+'.log'))+'"'))
        $process = Start-Process -FilePath $setup -ArgumentList $arguments -Wait -PassThru -WindowStyle Hidden
        if ($process.ExitCode -ne 0) { throw ('Installer failed at '+$stage+': '+$process.ExitCode) }
        if (!(Test-Path -LiteralPath (Join-Path $installDir 'Sentinel.exe'))) { throw 'Installed EXE missing' }
        Assert-Preserved $before $stage
        $results[$stage+'_data_preserved'] = $true
        if ($stage -eq 'install') {
            $env:SENTINEL_DATA_DIR = Join-Path $qaRoot 'full'
            $env:PYTHONHOME = ''; $env:PYTHONPATH = ''
            $env:PATH = "$env:WINDIR\System32;$env:WINDIR"
            $diagnostics = @('--self-test')
            if ($TestMicrophone) { $diagnostics += '--test-microphone' }
            if ($TestLlm) { $diagnostics += '--test-llm' }
            $app = Start-Process -FilePath (Join-Path $installDir 'Sentinel.exe') -ArgumentList $diagnostics -Wait -PassThru -WindowStyle Hidden
            if ($app.ExitCode -ne 0) { throw ('Installed EXE self-test failed: '+$app.ExitCode) }
            $report = Get-Content -LiteralPath (Join-Path $env:SENTINEL_DATA_DIR 'self-test.json') -Raw | ConvertFrom-Json
            if (!$report.passed) { throw 'Installed self-test reported failure' }
            Copy-Item -LiteralPath (Join-Path $env:SENTINEL_DATA_DIR 'self-test.json') -Destination (Join-Path $qaRoot 'installed-self-test.json')
            $results['installed_self_test_passed'] = $true
        }
    }
    $uninstaller = Join-Path $installDir 'unins000.exe'
    $process = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART',('/LOG="'+(Join-Path $qaRoot 'uninstall.log')+'"')) -Wait -PassThru -WindowStyle Hidden
    if ($process.ExitCode -ne 0) { throw ('Uninstall failed: '+$process.ExitCode) }
    Start-Sleep -Seconds 2
    if (Test-Path -LiteralPath (Join-Path $installDir 'Sentinel.exe')) { throw 'Uninstall did not remove executable' }
    Assert-Preserved $before 'uninstall'
    $results['uninstall_data_preserved'] = $true
    $results['uninstall_executable_removed'] = $true
    $results['passed'] = $true
} finally {
    $checked = [IO.Path]::GetFullPath($markerDir)
    if (!$checked.StartsWith([IO.Path]::GetFullPath($dataRoot)+[IO.Path]::DirectorySeparatorChar) -or (Split-Path -Leaf $checked) -notlike '.packaging-canary-*') { throw 'Canary cleanup target escaped scope' }
    Remove-Item -LiteralPath $checked -Recurse -Force
    $results | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath (Join-Path $qaRoot 'install-results.json') -Encoding UTF8
}
$results | ConvertTo-Json -Depth 8

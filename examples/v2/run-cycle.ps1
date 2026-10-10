param(
    [string]$Creator = 'demo',
    [string]$Workspace = 'workspace',
    [string]$Topic = '为什么先整理桌面再购买收纳工具',
    [int]$MaxRevisions = 2,
    [string]$Model = ''
)
# Real Agent calls; requires an installed/logged-in Codex and an approved Skill.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$pythonPath = Join-Path $projectRoot '.venv\Scripts\python.exe'
$cycleArgs = @('-X', 'utf8', '-m', 'creator_dna', 'quality', '--workspace', $Workspace,
    '--creator', $Creator, '--topic', $Topic, '--max-revisions', "$MaxRevisions")
if ($Model) { $cycleArgs += @('--model', $Model) }
$rawResult = & $pythonPath @cycleArgs
if ($LASTEXITCODE -ne 0) { throw 'Quality command failed; inspect retained run artifacts.' }
$result = ($rawResult -join "`n") | ConvertFrom-Json
$result | ConvertTo-Json -Depth 10
Write-Host "Quality status: $($result.status)"
Write-Host "Report: $(Join-Path $result.run 'evaluation-report.md')"
Write-Host "Revision history: $($result.history)"

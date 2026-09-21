#!/usr/bin/env pwsh
# 后端启动脚本(Windows / PowerShell)
# 自动加载 backend/.env(若存在)中的环境变量,再启动 uvicorn。
# 平台注入的环境变量(Docker -e / K8s Secret)优先级更高,不受影响。
$ErrorActionPreference = "Stop"

$BackendDir = Split-Path -Parent $PSScriptRoot
$EnvFile = if ($env:ENV_FILE) { $env:ENV_FILE } else { Join-Path $BackendDir ".env" }

if (Test-Path $EnvFile) {
    foreach ($rawLine in Get-Content -Path $EnvFile -Encoding utf8) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) { continue }
        if ($line.StartsWith("export ")) { $line = $line.Substring(7).Trim() }
        $eq = $line.IndexOf("=")
        if ($eq -le 0) { continue }
        $name = $line.Substring(0, $eq).Trim()
        $value = $line.Substring($eq + 1).Trim()
        # 去掉成对的引号
        if ($value.Length -ge 2 -and (
            ($value.StartsWith('"') -and $value.EndsWith('"')) -or
            ($value.StartsWith("'") -and $value.EndsWith("'")))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($name) { Set-Item -Path "env:$name" -Value $value }
    }
    Write-Host "[run-backend.ps1] 已加载环境变量文件: $EnvFile"
}
else {
    Write-Host "[run-backend.ps1] 未发现 $EnvFile,使用当前进程/平台注入的环境变量"
}

$Port = if ($env:PORT) { $env:PORT } else { "8001" }
$Python = Join-Path $BackendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = "python" }

& uv sync --directory $BackendDir
Set-Location $BackendDir
& $Python -m uvicorn app.main:app --app-dir $BackendDir --port $Port

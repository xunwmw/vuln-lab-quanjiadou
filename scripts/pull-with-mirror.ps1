<#
  用国内镜像站拉取 Docker Hub 镜像，并改回原名。

  背景：中国大陆直连 registry-1.docker.io / auth.docker.io 会超时，
  但镜像站可用（实测 docker.1ms.run 248ms、docker.m.daocloud.io 217ms）。
  本脚本把 `docker.1ms.run/vulnerables/web-dvwa` 拉下来后 `docker tag` 成
  `vulnerables/web-dvwa`，这样 third-party/*/docker-compose.yml 保持原样即可 up。

  用法：
    powershell -File scripts/pull-with-mirror.ps1                       # 自动读 compose 文件里的 image
    powershell -File scripts/pull-with-mirror.ps1 vulnerables/web-dvwa  # 指定镜像
    powershell -File scripts/pull-with-mirror.ps1 -Mirror docker.m.daocloud.io
    powershell -File scripts/pull-with-mirror.ps1 -SkipExisting         # 本地已有就跳过（默认行为）

  只读本地文件 + 调 docker，不修改任何 compose 文件。
#>
[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$Images,
    [string]$Mirror = '',
    [switch]$Force
)

$ErrorActionPreference = 'Continue'
$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)

# 镜像站候选：按实测延迟从快到慢
$mirrors = @('docker.1ms.run', 'docker.m.daocloud.io', 'docker.1panel.live')
if ($Mirror) { $mirrors = @($Mirror) + ($mirrors | Where-Object { $_ -ne $Mirror }) }

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Output 'ERR: 找不到 docker 命令。第三方靶场需要 Docker Engine 20.10+ 或 Docker Desktop 4.x。'
    exit 2
}

function Get-ComposeImages {
    $found = @()
    $files = Get-ChildItem -Path $repoRoot -Recurse -Filter 'docker-compose*.y*ml' -ErrorAction SilentlyContinue
    foreach ($f in $files) {
        foreach ($line in Get-Content -LiteralPath $f.FullName) {
            if ($line -match '^\s*image:\s*["'']?([^"''\s#]+)') {
                $found += $matches[1]
            }
        }
    }
    return ($found | Sort-Object -Unique)
}

if (-not $Images -or $Images.Count -eq 0) {
    $Images = Get-ComposeImages
    Write-Output ('自动从 compose 文件收集到 ' + $Images.Count + ' 个镜像')
}
if (-not $Images -or $Images.Count -eq 0) {
    Write-Output 'ERR: 没有镜像可拉。请显式传入镜像名，例如：pull-with-mirror.ps1 vulnerables/web-dvwa'
    exit 2
}

$summary = @()
foreach ($img in $Images) {
    $img = $img.Trim()
    if (-not $img) { continue }
    Write-Output ('=== ' + $img + ' ===')

    if (-not $Force) {
        & docker image inspect $img *> $null
        if ($LASTEXITCODE -eq 0) {
            Write-Output '  本地已有该镜像，跳过（要强制重拉加 -Force）'
            $summary += ('SKIP   ' + $img)
            continue
        }
    }

    $done = $false
    foreach ($m in $mirrors) {
        $src = $m + '/' + $img
        Write-Output ('  尝试 ' + $src)
        & docker pull $src
        if ($LASTEXITCODE -eq 0) {
            & docker tag $src $img
            if ($LASTEXITCODE -eq 0) {
                Write-Output ('  OK → ' + $img + '（原名已就绪，compose 可直接 up）')
                $summary += ('OK     ' + $img + '  ← ' + $src)
                $done = $true
                break
            }
            Write-Output '  ERR: docker tag 失败'
        } else {
            Write-Output ('  失败：' + $src)
        }
    }
    if (-not $done) {
        $summary += ('FAIL   ' + $img)
        Write-Output '  所有镜像站都失败。可能原因：镜像名写错、镜像站临时不可用，或公司网络限制。'
    }
}

Write-Output ''
Write-Output '=== 汇总 ==='
$summary | ForEach-Object { Write-Output $_ }
$fails = ($summary | Where-Object { $_ -like 'FAIL*' }).Count
if ($fails -gt 0) {
    Write-Output ('有 ' + $fails + ' 个镜像没拉成功。可以换镜像站重试：-Mirror docker.m.daocloud.io')
    exit 1
}
Write-Output '全部就绪。接下来照常：cd third-party/dvwa && docker compose up -d'
exit 0

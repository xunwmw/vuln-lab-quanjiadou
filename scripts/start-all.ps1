# 起两个自建靶场（仅本机）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$py = (Get-Command python).Source
foreach ($lab in @(@{d='labs/target-site'; p=8091}, @{d='labs/target-site-lab'; p=8095})) {
  $dir = Join-Path $root $lab.d
  Start-Process -FilePath $py -ArgumentList @('app.py', "$($lab.p)") -WorkingDirectory $dir -WindowStyle Hidden
  Write-Output ("started {0} -> http://127.0.0.1:{1}" -f $lab.d, $lab.p)
}
Start-Sleep -Seconds 2
foreach ($p in 8091, 8095) {
  try { $r = Invoke-WebRequest -Uri "http://127.0.0.1:$p/" -UseBasicParsing -TimeoutSec 5; Write-Output ("  {0} -> HTTP {1}" -f $p, $r.StatusCode) }
  catch { Write-Output ("  {0} -> 未就绪" -f $p) }
}

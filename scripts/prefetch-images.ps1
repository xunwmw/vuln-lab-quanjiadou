# 预拉镜像：打当前一条时，后台把下一批拉好（见 docs/PREFETCH.md）
param(
  [string]$Images = "vulhub/fastjson:1.2.24,vulhub/fastjson:1.2.47,vulhub/confluence:7.13.6,vulhub/shiro:1.2.4,vulhub/tomcat:8.5"
)
$list = $Images.Split(',') | ForEach-Object { $_.Trim() } | Where-Object { $_ }
Write-Output ("预拉 {0} 个镜像" -f $list.Count)
foreach ($img in $list) {
  $sw = [Diagnostics.Stopwatch]::StartNew()
  docker pull $img *> $null
  $sw.Stop()
  Write-Output ("  {0,-34} {1,7:N0}s" -f $img, $sw.Elapsed.TotalSeconds)
}
Write-Output "完成。把每条的耗时记进批次记录，用于下次排期。"

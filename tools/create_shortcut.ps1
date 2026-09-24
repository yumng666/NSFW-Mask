# 创建「NSFW Mask」带图标快捷方式。
# 右键本文件 -> "使用 PowerShell 运行"，会在项目根生成 NSFW Mask.lnk，
# 把它复制到桌面即可。图标来自项目根的 app.ico。

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut((Join-Path $root 'NSFW Mask.lnk'))
$lnk.TargetPath = (Join-Path $root 'start.bat')
$lnk.WorkingDirectory = $root
$lnk.IconLocation = (Join-Path $root 'app.ico') + ',0'
$lnk.Description = 'NSFW Mask - local image censoring service'
$lnk.Save()

Write-Host ''
Write-Host 'OK: shortcut created at' (Join-Path $root 'NSFW Mask.lnk')
Write-Host 'Copy it to your desktop if you like.'
Write-Host ''
Pause

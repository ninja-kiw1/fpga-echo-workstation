# _grab_screen.ps1 -- full-desktop screenshot + process snapshot.
#
# Used as evidence when checking things like "does the launcher really start
# without a console window?": run it detached with a delay, then start the GUI
# so the grab happens while the GUI is on screen.
#
#     powershell -NoProfile -ExecutionPolicy Bypass `
#                -File _grab_screen.ps1 out.png -DelaySeconds 5
#
# Writes out.png and out.png.processes.txt (python/pythonw/cmd tasklist lines).
# Needs an unlocked, visible desktop.

param(
    [string]$Path = "screen.png",
    [int]$DelaySeconds = 0
)

if ($DelaySeconds -gt 0) { Start-Sleep -Seconds $DelaySeconds }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$vs = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $vs.Width, $vs.Height
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($vs.Left, $vs.Top, 0, 0, $bmp.Size)
$bmp.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose()
$bmp.Dispose()

$fi = Get-Item $Path
$lines = @()
$lines += ("GRAB {0} {1}x{2} {3} bytes" -f $fi.FullName, $vs.Width, $vs.Height, $fi.Length)
foreach ($n in @("pythonw.exe", "python.exe")) {
    $p = @(Get-Process -Name ([IO.Path]::GetFileNameWithoutExtension($n)) -ErrorAction SilentlyContinue)
    $lines += ("{0}: {1} running  {2}" -f $n, $p.Count, ($p | ForEach-Object { $_.Id }) -join ",")
}
$lines | Set-Content -Path ($Path + ".processes.txt") -Encoding ASCII
$lines | ForEach-Object { Write-Output $_ }

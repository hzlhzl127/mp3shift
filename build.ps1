# 打包 MP3Shift：PyInstaller onedir，输出 dist\MP3Shift
param(
    [string]$FfmpegBin = ".\ffmpeg\bin"
)

$ErrorActionPreference = "Stop"
Push-Location $PSScriptRoot
try {
    Write-Host "==> 安装/更新 PyInstaller"
    python -m pip install --quiet --upgrade "pyinstaller>=6.0"
    python -m pip install --quiet -r ".\requirements.txt"

    Write-Host "==> 打包"
    python -m PyInstaller --noconfirm --clean --windowed --onedir --name MP3Shift `
        --collect-all tkinterdnd2 `
        --distpath ".\dist" --workpath ".\build" --specpath ".\build" `
        ".\mp4_to_mp3_gui.py"

    if (Test-Path $FfmpegBin) {
        New-Item -ItemType Directory -Force ".\dist\MP3Shift\ffmpeg\bin" | Out-Null
        Copy-Item "$FfmpegBin\*" ".\dist\MP3Shift\ffmpeg\bin\" -Recurse -Force
        Write-Host "==> ffmpeg 已复制到 dist\MP3Shift\ffmpeg\bin"
    } else {
        Write-Warning "没找到 $FfmpegBin，dist 里不会带 ffmpeg。请手动放入 ffmpeg\bin\ffmpeg.exe"
    }

    Write-Host "==> 完成：dist\MP3Shift\MP3Shift.exe"
} finally {
    Pop-Location
}

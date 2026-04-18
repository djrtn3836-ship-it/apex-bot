# start_bot.ps1 - 자동 재시작 스크립트
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"
$botDir = "C:\Users\hdw38\Desktop\달콩\bot\apex_bot"
$python = "C:\Users\hdw38\AppData\Local\Programs\Python\Python312\python.exe"

# 실행 중인 봇이 없을 때만 시작
$running = Get-Process python -ErrorAction SilentlyContinue
if (-not $running) {
    Start-Process -FilePath $python `
        -ArgumentList "main.py --mode paper" `
        -WorkingDirectory $botDir `
        -WindowStyle Hidden
    Add-Content "$botDir\logs\scheduler.log" "$(Get-Date) - Bot started by scheduler"
} else {
    Add-Content "$botDir\logs\scheduler.log" "$(Get-Date) - Bot already running (PID=$($running[0].Id))"
}

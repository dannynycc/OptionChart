"""
stop.py — OptionChart 精準停止腳本
由 stop.bat 呼叫，只終止屬於 OptionChart 的 python process
（uvicorn main:app + xqfap_feed.py + start.py 自身），不會誤殺系統上其他 python
（pyright langserver、jupyter、其他 script 等）。

對應 start.bat → start.py 啟動的 process。
"""
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PID_FILE = os.path.join(ROOT, 'monitor', 'xqfap.pid')

# 用 powershell 查 + 殺 OptionChart 相關 python.exe
# regex 對應 start.py 啟動的三種 commandline
PS_SCRIPT = r'''
$procs = Get-WmiObject Win32_Process -Filter "Name='python.exe'" |
         Where-Object { $_.CommandLine -match 'xqfap_feed\.py|uvicorn main:app|scripts.start\.py' }
if ($procs) {
    foreach ($p in $procs) {
        $cmd = if ($p.CommandLine) { $p.CommandLine.Substring(0, [Math]::Min(100, $p.CommandLine.Length)) } else { '' }
        Write-Output "kill|$($p.ProcessId)|$cmd"
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop }
        catch { Write-Output "fail|$($p.ProcessId)|$($_.Exception.Message)" }
    }
} else {
    Write-Output "none|0|"
}
'''


def main():
    print("停止 OptionChart (uvicorn + xqfap_feed)...")
    try:
        result = subprocess.run(
            ['powershell', '-NoProfile', '-Command', PS_SCRIPT],
            capture_output=True, text=True, timeout=15
        )
    except Exception as e:
        print(f"  [error] powershell 執行失敗：{e}")
        return

    killed_any = False
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split('|', 2)
        if len(parts) < 3:
            continue
        kind, pid, cmd = parts
        if kind == 'kill':
            print(f"  killed pid={pid}  {cmd}")
            killed_any = True
        elif kind == 'fail':
            print(f"  [fail] pid={pid}  {cmd}")
        elif kind == 'none':
            print("  (沒有執行中的 OptionChart python process)")

    # 清掉 xqfap.pid 檔（避免下次 start.py 誤判舊 pid 還在跑）
    if os.path.exists(PID_FILE):
        try:
            os.remove(PID_FILE)
            print(f"  cleaned {os.path.basename(PID_FILE)}")
        except OSError as e:
            print(f"  [warn] 無法刪除 {PID_FILE}：{e}")

    print("全部停止完畢。")


if __name__ == '__main__':
    main()

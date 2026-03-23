"""
config_bridge.py（從此 template 複製後改名，放在 Windows 上與 skcom_bridge.py 同目錄）
"""

# 群益策略王安裝路徑中的 SKCOM.dll
# 常見路徑（若不同請自行確認）：
SKCOM_DLL = r"C:\Program Files (x86)\Capital\Trade.Ca\SKCOM.dll"

# 群益期貨帳號（API 服務已申請）
ID       = "your_capital_id"
PASSWORD = "your_capital_password"

# WSL FastAPI server 位址（Windows 連 WSL2 通常用 localhost）
SERVER_URL = "http://localhost:8000"

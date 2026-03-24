# config_fubon_template.py
# 富邦期貨 API 設定範本
# 使用前請複製為 config_fubon.py 並填入真實帳密（config_fubon.py 不進 git）

ID            = "your_fubon_id"
PASSWORD      = "your_fubon_password"
CERT_PATH     = r"C:\path\to\your_cert.pfx"   # 憑證 .pfx 路徑
CERT_PASSWORD = "your_cert_password"
SERVER_URL    = "http://localhost:8000"
# TARGET_SERIES = "TX4"   # 預設 TX4（W4 週選）；可改為 TXO（月選）、TX1/TX2/TX5

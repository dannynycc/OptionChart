# config_xqfap_template.py — 複製為 config_xqfap.py 並填入實際值
#
# 每週換倉時更新：
#   XQ_SERIES       → 新富邦e01 合約系列碼（例：N03 = W4 of March）
#   SETTLEMENT_DATE → 結算日（例：20260326）
#
# 如何找 XQ_SERIES：
#   python xqfap_feed.py --discover

XQ_SERIES       = "N03"               # 系列碼：TX4 + XQ_SERIES + C/P + strike
SETTLEMENT_DATE = "20260326"          # 結算日（YYYYMMDD）
SERVER_URL      = "http://localhost:8000"

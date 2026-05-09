import sys; sys.path.insert(0, '/app/backend')
import requests, re

board_code = '308614'
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "http://q.10jqka.com.cn/gn/",
}

# Original detail page
url = f"http://q.10jqka.com.cn/gn/detail/code/{board_code}/"
r = requests.get(url, headers=headers, timeout=10)
r.encoding = "gbk"

# Better regex: look for stock links with /stockpage/ pattern
# THS stock pages: http://stockpage.10jqka.com.cn/000001/
matches = re.findall(r'stockpage\.10jqka\.com\.cn/(\d{6})/', r.text)
print(f"stockpage: {len(matches)}, sample: {matches[:10]}")

# Look for href to stock detail
matches2 = re.findall(r'href="//stockpage\.10jqka\.com\.cn/(\d{6})/"', r.text)
print(f"href stockpage: {len(matches2)}, sample: {matches2[:10]}")

# The detail page has an iframe or table - check
# Find all unique 6-digit numbers
all_codes = re.findall(r'(?<!\d)(\d{6})(?!\d)', r.text)
# Group by prefix
from collections import Counter
prefix_count = Counter(c[:2] for c in all_codes)
print("Prefix counts:", dict(prefix_count))

# Show board_code patterns - what's in concept_boards
from app.services.sync_service import _get_session
from sqlalchemy import text
s = _get_session()
r2 = s.execute(text("SELECT board_code FROM concept_boards LIMIT 20"))
board_codes_db = [row[0] for row in r2]
print("DB board codes:", board_codes_db[:10])
s.close()

# Filter: exclude all known board codes
board_set = set(board_codes_db)
# But we need ALL board codes
s = _get_session()
r2 = s.execute(text("SELECT board_code FROM concept_boards"))
all_board_codes = set(row[0] for row in r2)
s.close()
print(f"Total board codes: {len(all_board_codes)}")

# Now filter stock codes properly
stock_codes = set()
for c in all_codes:
    if c[0] in ('0', '3', '6') and c not in all_board_codes:
        stock_codes.add(c)
print(f"After filtering board codes: {len(stock_codes)} stocks")
print(sorted(list(stock_codes))[:20])


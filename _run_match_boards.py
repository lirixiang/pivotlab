"""Quick script: fetch THS concept boards and match to existing stock_concepts by name.
Skips Phase 1 (EM F10) - uses existing stock_concepts data.
"""
import sys; sys.path.insert(0, '/app/backend')
import logging; logging.basicConfig(level=logging.INFO)
import requests, re, time
from app.services.sync_service import _get_session
from sqlalchemy import text
from datetime import datetime

logger = logging.getLogger(__name__)

# Step 1: Fetch THS board list
ths_headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "http://q.10jqka.com.cn/",
}

resp = requests.get("http://q.10jqka.com.cn/gn/", headers=ths_headers, timeout=15)
resp.encoding = "gbk"
pairs = re.findall(r'/gn/detail/code/(\d+)/["\'][^>]*>([^<]+)<', resp.text)
seen = set()
boards = []
for code, name in pairs:
    name = name.strip()
    if code not in seen and name:
        seen.add(code)
        boards.append((code, name))
print(f"THS found {len(boards)} concept boards")

# Step 2: Fetch change% for top 50 boards
board_changes = {}
for board_code, board_name in boards[:50]:
    try:
        detail_url = f"http://q.10jqka.com.cn/gn/detail/code/{board_code}/"
        r = requests.get(detail_url, headers=ths_headers, timeout=10)
        r.encoding = "gbk"
        chg_match = re.search(r'class="board-zf"[^>]*>([+-]?\d+\.?\d*)%', r.text)
        if chg_match:
            board_changes[board_code] = float(chg_match.group(1))
    except Exception:
        pass
    time.sleep(0.1)
print(f"Got change% for {len(board_changes)} boards")

# Step 3: Sort by change% and assign rank, upsert into concept_boards
board_with_rank = [(bc, bn, board_changes.get(bc)) for bc, bn in boards]
board_with_rank.sort(key=lambda x: (x[2] or -999), reverse=True)

now = datetime.utcnow()
with _get_session() as s:
    for rank_idx, (bc, bn, chg) in enumerate(board_with_rank, 1):
        s.execute(
            text("INSERT INTO concept_boards (board_code, concept, change_pct_1d, rank, updated_at) "
                 "VALUES (:bc, :concept, :chg, :rank, :ts) "
                 "ON CONFLICT (board_code) DO UPDATE SET "
                 "concept=:concept, change_pct_1d=:chg, rank=:rank, updated_at=:ts"),
            {"bc": bc, "concept": bn, "chg": chg, "rank": rank_idx, "ts": now},
        )
    s.commit()
print(f"Upserted {len(board_with_rank)} concept_boards")

# Step 4: Match stock_concepts to boards by concept name
board_map = {bn: bc for bc, bn in boards}
total_updated = 0
with _get_session() as s:
    for concept_name, board_code in board_map.items():
        result = s.execute(
            text("UPDATE stock_concepts SET board_code = :bc, updated_at = :ts "
                 "WHERE concept = :concept AND (board_code IS NULL OR board_code != :bc)"),
            {"bc": board_code, "concept": concept_name, "ts": now},
        )
        total_updated += result.rowcount
    s.commit()
print(f"Updated {total_updated} stock_concepts with board_code")

# Verify
with _get_session() as s:
    r = s.execute(text("SELECT count(*) FROM stock_concepts WHERE board_code IS NOT NULL"))
    print(f"stock_concepts with board_code: {r.scalar()}")
    r = s.execute(text("SELECT count(*) FROM stock_concepts"))
    print(f"stock_concepts total: {r.scalar()}")
    r = s.execute(text("SELECT concept, board_code FROM stock_concepts WHERE code='603390' AND board_code IS NOT NULL"))
    for row in r:
        print(f"  603390: {row}")

print("Done")


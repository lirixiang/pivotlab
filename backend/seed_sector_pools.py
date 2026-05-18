"""Seed script — 把抖音图里那张"2026 核心赛道 20 大细分领域"灌入 sector_pool 表。

特点：
  * **幂等**：可重复运行；已存在的赛道/个股自动跳过
  * **校验**：每只股票必须在 stocks 表里存在（沐曦/摩尔线程等未上市公司会被跳过）
  * **tier 映射**：图里 top1→tier1(龙一)，top2→tier2(龙二)，top3-4→tier3(跟风)
  * **可裁剪**：直接改下面 SEED 字典即可

运行：
  cd backend
  python3 seed_sector_pools.py

或在 docker 容器里：
  docker compose exec backend python3 seed_sector_pools.py

注意：这是 2026.5 抖音用户 @蒙奇奇 整理的"年度主线赛道"，仅作为初始模板。
建议你跑一段时间后**按月复审**，把走弱的赛道归档、新热点手动加入。
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime

from sqlalchemy import select

# 允许从 backend/ 目录直接执行
sys.path.insert(0, ".")

from app.database import AsyncSessionLocal  # noqa: E402
from app.models import SectorPool, SectorPoolStock, Stock  # noqa: E402


# ── 数据：category → [(sector_name, description, [(code, name_hint, tier)])] ──
# tier: 1=龙一  2=龙二  3=跟风补涨
#
# 已剔除未上市/无法核实代码的公司：
#   沐曦股份、摩尔线程、ASI、DT、中菱环境、dataport、南网数字、智能股份

SEED: dict[str, list[tuple[str, str, list[tuple[str, str, int]]]]] = {
    "AI算力-光通信": [
        ("CPO", "共封装光学，AI 高速互联核心方向", [
            ("300308", "中际旭创", 1),
            ("300502", "新易盛", 2),
            ("300394", "天孚通信", 3),
            ("000988", "华工科技", 3),
        ]),
        ("OCS", "光路交换，下一代数据中心架构", [
            ("688195", "腾景科技", 1),
            ("002222", "福晶科技", 2),
            ("300620", "光库科技", 3),
            ("688205", "德科立", 3),
        ]),
        ("光芯片", "高速光通信上游核心元器件", [
            ("688498", "源杰科技", 1),
            ("688313", "仕佳光子", 2),
            ("002281", "光迅科技", 3),
            ("688048", "长光华芯", 3),
        ]),
        ("光纤光缆", "传统通信+海缆出海", [
            ("601869", "长飞光纤", 1),
            ("600487", "亨通光电", 2),
            ("600522", "中天科技", 3),
            ("600498", "烽火通信", 3),
        ]),
        ("光模块设备", "光模块产线自动化设备", [
            ("300757", "罗博特科", 1),
            ("002957", "科瑞技术", 2),
            ("002975", "博杰股份", 3),
        ]),
    ],
    "AI算力-硬件": [
        ("PCB", "AI 服务器高多层 PCB 主线", [
            ("300476", "胜宏科技", 1),
            ("002384", "东山精密", 2),
            ("002916", "深南电路", 3),
            ("002463", "沪电股份", 3),
        ]),
        ("AI服务器", "AI 算力整机", [
            ("601138", "工业富联", 1),
            ("000977", "浪潮信息", 2),
            ("000938", "紫光股份", 3),
            ("603019", "中科曙光", 3),
        ]),
        ("AI芯片", "国产 GPU/ASIC 自主可控", [
            ("688041", "海光信息", 1),
            ("688256", "寒武纪", 2),
        ]),
        ("存储芯片", "HBM/SSD/DRAM 全线", [
            ("001309", "德明利", 1),
            ("603986", "兆易创新", 2),
            ("688525", "佰维存储", 3),
            ("301308", "江波龙", 3),
        ]),
        ("高速连接", "高速线缆/连接器", [
            ("002475", "立讯精密", 1),
            ("300913", "兆龙互连", 2),
            ("002130", "沃尔核材", 3),
        ]),
    ],
    "AI算力-材料": [
        ("铜箔", "PCB 上游高频铜箔", [
            ("600110", "诺德股份", 1),
            ("301217", "铜冠铜箔", 2),
            ("688388", "嘉元科技", 3),
            ("301511", "德福科技", 3),
        ]),
        ("树脂", "高频高速覆铜板树脂", [
            ("601208", "东材科技", 1),
            ("605589", "圣泉集团", 2),
            ("300586", "美联新材", 3),
            ("603002", "宏昌电子", 3),
        ]),
        ("电子布", "覆铜板用 Low-Dk 电子布", [
            ("603256", "宏和科技", 1),
            ("600176", "中国巨石", 2),
            ("002080", "中材科技", 3),
            ("301526", "国际复材", 3),
        ]),
    ],
    "AI算力-基建": [
        ("液冷", "数据中心液冷散热", [
            ("002837", "英维克", 1),
            ("300499", "高澜股份", 2),
            ("603019", "中科曙光", 3),
        ]),
        ("电源", "数据中心 HVDC/UPS 电源", [
            ("002364", "中恒电气", 1),
            ("002580", "圣阳股份", 2),
            ("300870", "欧陆通", 3),
            ("002851", "麦格米特", 3),
        ]),
        ("AIDC", "智算中心 IDC 运营", [
            ("300442", "润泽科技", 1),
            ("300017", "网宿科技", 2),
            ("300383", "光环新网", 3),
        ]),
        ("算电协同", "源网荷储一体化", [
            ("001896", "豫能控股", 1),
            ("002015", "协鑫能科", 2),
        ]),
        ("算力租赁", "智算云/算力服务", [
            ("603629", "利通电子", 1),
            ("300857", "协创数据", 2),
            ("301396", "宏景科技", 3),
            ("688158", "优刻得", 3),
        ]),
    ],
    "能源-高端制造": [
        ("燃气轮机", "国产替代+电力调峰", [
            ("002353", "杰瑞股份", 1),
            ("605060", "联德股份", 2),
            ("603308", "应流股份", 3),
            ("600875", "东方电气", 3),
        ]),
        ("固态变压器", "新型电力系统核心装备", [
            ("601126", "四方股份", 1),
            ("601179", "中国西电", 2),
            ("002922", "伊戈尔", 3),
            ("688676", "金盘科技", 3),
        ]),
    ],
}


# ── 主流程 ─────────────────────────────────────────────────────────

async def main():
    # 统计
    pools_created = 0
    pools_existing = 0
    stocks_added = 0
    stocks_existing = 0
    stocks_unknown: list[tuple[str, str, str]] = []  # (sector, code, name_hint)
    rank_counter = 0

    async with AsyncSessionLocal() as session:
        for category, sectors in SEED.items():
            for sector_name, sector_desc, stock_list in sectors:
                rank_counter += 1

                # 1) 赛道：按 (name, status=active) 查重
                existing_pool = (await session.execute(
                    select(SectorPool).where(
                        SectorPool.name == sector_name,
                        SectorPool.status == "active",
                    )
                )).scalar_one_or_none()

                if existing_pool:
                    pool = existing_pool
                    pools_existing += 1
                    # 不覆盖用户可能已编辑的 category/description
                else:
                    pool = SectorPool(
                        name=sector_name,
                        category=category,
                        description=sector_desc,
                        rank=rank_counter,
                        status="active",
                        archived_at="",
                    )
                    session.add(pool)
                    await session.flush()  # 拿到 id
                    pools_created += 1

                # 2) 个股
                for code, name_hint, tier in stock_list:
                    # 校验 stocks 表存在
                    stk = await session.get(Stock, code)
                    if stk is None:
                        stocks_unknown.append((sector_name, code, name_hint))
                        continue

                    # 查重：同赛道、同代码、未删除
                    dup = (await session.execute(
                        select(SectorPoolStock).where(
                            SectorPoolStock.sector_id == pool.id,
                            SectorPoolStock.code == code,
                            SectorPoolStock.removed_at == "",
                        )
                    )).scalar_one_or_none()
                    if dup:
                        stocks_existing += 1
                        continue

                    session.add(SectorPoolStock(
                        sector_id=pool.id,
                        code=code,
                        tier=tier,
                        note="",
                        removed_at="",
                    ))
                    stocks_added += 1

        await session.commit()

    # ── 报告 ─────────────────────────────────────
    print()
    print("═" * 60)
    print(f"  Sector Pool 种子数据导入完成 @ {datetime.now():%Y-%m-%d %H:%M:%S}")
    print("═" * 60)
    print(f"  赛道：新增 {pools_created} 个 · 已存在 {pools_existing} 个")
    print(f"  个股：新增 {stocks_added} 只 · 已存在 {stocks_existing} 只")
    if stocks_unknown:
        print(f"  ⚠️  跳过 {len(stocks_unknown)} 只（不在 stocks 表里）：")
        for sec, code, hint in stocks_unknown:
            print(f"      · [{sec}] {code} {hint}")
        print(f"     提示：先去「数据同步」tab 同步股票列表，再重跑本脚本可补上。")
    print("═" * 60)


if __name__ == "__main__":
    asyncio.run(main())

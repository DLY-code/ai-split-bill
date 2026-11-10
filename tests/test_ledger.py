import json
import random
from collections import defaultdict

import pytest

from ledger_core import (validate_record, filter_bills, settle, min_transfers,
                         stat_top_payer, stat_top_consumer)


def bill(payer, amount, shares, date="2026-11-08", remark=""):
    return {"date": date, "payer": payer, "amount": amount,
            "shares": shares, "remark": remark}


# ================= 记账校验 =================
def test_valid_expense_passes():
    r = validate_record(bill("张三", 800, {"张三": 200, "李四": 200, "王五": 200, "赵六": 200}))
    assert r is not None
    assert r["amount"] == 800


def test_valid_refund_passes():
    r = validate_record(bill("赵六", -200, {"赵六": -50, "张三": -50, "李四": -50, "王五": -50}))
    assert r is not None


def test_amounts_are_rounded_to_cents():
    r = validate_record(bill("张三", "100.004", {"张三": 50.001, "李四": "50.003"}))
    assert r["amount"] == 100.0
    assert r["shares"] == {"张三": 50.0, "李四": 50.0}


@pytest.mark.parametrize("record", [
    bill("张三", 100, {"张三": 50, "李四": 40}),           # shares 求和 ≠ amount
    bill("张三", 0, {"张三": 0}),                          # 金额为 0
    bill("", 100, {"张三": 100}),                          # 付款人为空
    bill(None, 100, {"张三": 100}),
    bill("张三", 100, {}),                                 # 没有分摊
    bill("张三", 100, {"*": 100}),                         # 通配符
    bill("张三", 100, {"张三": 50, "所有人": 50}),
    bill("张三", 100, {"张三": "五十", "李四": 50}),        # 份额不是数字
    bill("张三", "一百", {"张三": 100}),                    # 金额不是数字
    bill("张三", 100, ["张三", "李四"]),                    # shares 不是字典
    {"payer": "张三", "shares": {"张三": 100}},            # 缺 amount
])
def test_invalid_records_are_rejected(record):
    assert validate_record(record) is None


def test_fake_ai_reply_keeps_only_valid_records():
    # 模拟 AI 返回的一段 JSON：一条正常、一条金额不平、一条用了通配符
    reply = json.dumps([
        bill("张三", 120, {"我": 30, "张三": 30, "李四": 30, "王五": 30}, remark="外卖"),
        bill("李四", 200, {"李四": 100, "王五": 50}),
        bill("王五", 90, {"*": 90}),
    ], ensure_ascii=False)
    valid = [x for x in (validate_record(i) for i in json.loads(reply)) if x]
    assert [v["remark"] for v in valid] == ["外卖"]


# ================= 筛选 =================
BILLS = [
    bill("张三", 800, {"张三": 200, "李四": 200, "王五": 200, "赵六": 200}, date="2026-10-01"),
    bill("李四", 90, {"李四": 45, "王五": 45}, date="2026-11-05"),
    bill("赵六", -200, {"赵六": -50, "张三": -50, "李四": -50, "王五": -50}, date="2026-11-08"),
]


def test_filter_by_date_range_is_inclusive():
    out = filter_bills(BILLS, "2026-11-05", "2026-11-08")
    assert [b["date"] for b in out] == ["2026-11-05", "2026-11-08"]


def test_filter_by_person_matches_payer_or_share():
    assert len(filter_bills(BILLS, None, None, ["王五"])) == 3
    assert len(filter_bills(BILLS, None, None, ["赵六"])) == 2
    assert filter_bills(BILLS, None, None, ["路人"]) == []


def test_filter_without_conditions_returns_all():
    assert filter_bills(BILLS, None, None) == BILLS


# ================= 结算 =================
def apply_transfers(balance, transfers):
    """执行转账后每个人剩下的净额"""
    left = defaultdict(float, balance)
    for debtor, creditor, amt in transfers:
        assert amt > 0
        left[debtor] -= amt
        left[creditor] += amt
    return left


def test_settle_simple_aa():
    balance, transfers = settle(BILLS[:1])
    assert balance == {"张三": -600, "李四": 200, "王五": 200, "赵六": 200}
    assert sorted(transfers) == [("李四", "张三", 200), ("王五", "张三", 200),
                                 ("赵六", "张三", 200)]


def test_settle_with_refund_clears_everyone():
    balance, transfers = settle(BILLS)
    left = apply_transfers(balance, transfers)
    assert all(abs(v) < 0.005 for v in left.values())


def test_settle_balanced_ledger_needs_no_transfer():
    _, transfers = settle([bill("张三", 100, {"张三": 100})])
    assert transfers == []


def test_min_transfers_beats_greedy():
    # 简单贪心会出 5 笔；B→D 单独结清后其余 3 笔即可，最少 4 笔
    balance = {"A": 6, "B": 3, "E": 2, "C": -5, "D": -3, "F": -3}
    transfers = min_transfers(balance)
    assert len(transfers) == 4
    assert all(abs(v) < 0.005 for v in apply_transfers(balance, transfers).values())


def test_min_transfers_absorbs_rounding_residual():
    # 100 元三人均摊会剩 1 分钱差额，记到净额绝对值最大的 C 身上
    transfers = min_transfers({"A": 33.33, "B": 33.33, "C": -66.67})
    assert sorted(transfers) == [("A", "C", 33.33), ("B", "C", 33.33)]


def brute_force_min(cents):
    """穷举：最少笔数 = 人数 - 最多能拆出的零和小组数"""
    n = len(cents)
    total = [sum(cents[k] for k in range(n) if m >> k & 1) for m in range(1 << n)]
    best = [0] * (1 << n)
    for m in range(1, 1 << n):
        best[m] = max(best[m ^ (1 << k)] for k in range(n) if m >> k & 1) + (total[m] == 0)
    return n - best[-1]


def test_min_transfers_matches_brute_force_on_random_ledgers():
    rng = random.Random(42)
    for _ in range(500):
        n = rng.randint(2, 8)
        cents = [rng.choice([-1, 1]) * rng.randint(1, 6) * 100 for _ in range(n - 1)]
        cents.append(-sum(cents))
        balance = {f"p{i}": c / 100 for i, c in enumerate(cents)}
        transfers = min_transfers(balance)
        assert len(transfers) == brute_force_min([c for c in cents if c])
        assert all(abs(v) < 0.005 for v in apply_transfers(balance, transfers).values())


def test_min_transfers_falls_back_to_greedy_for_large_groups():
    balance = {f"p{i}": 10 for i in range(20)}
    balance["x"] = -200
    transfers = min_transfers(balance)
    assert len(transfers) == 20
    assert all(abs(v) < 0.005 for v in apply_transfers(balance, transfers).values())


# ================= 统计 =================
def test_top_payer_and_top_consumer():
    assert stat_top_payer(BILLS) == [("张三", 800), ("李四", 90), ("赵六", -200)]
    consumer = dict(stat_top_consumer(BILLS))
    assert consumer == {"张三": 150, "李四": 195, "王五": 195, "赵六": 150}

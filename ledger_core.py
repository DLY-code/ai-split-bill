"""账本核心逻辑：校验、筛选、结算、统计。纯函数，不依赖 AI 与 Streamlit。"""
from collections import defaultdict


# ================= 记账校验 =================
def validate_record(info):
    try:
        amount = float(info["amount"])
    except (KeyError, TypeError, ValueError):
        return None
    if abs(amount) < 0.005:
        return None
    payer = info.get("payer")
    if not payer or not isinstance(payer, str):
        return None
    shares = info.get("shares", {})
    if not isinstance(shares, dict) or not shares:
        return None
    for k, v in shares.items():
        if k in ("*", "所有人", "全部", "all"):
            return None
        try:
            float(v)
        except (TypeError, ValueError):
            return None
    if abs(sum(float(v) for v in shares.values()) - amount) > 0.01:
        return None
    info["amount"] = round(amount, 2)
    info["shares"] = {k: round(float(v), 2) for k, v in shares.items()}
    return info


# ================= 查账与结算 =================
def filter_bills(bills, date_start, date_end, people=None):
    people_set = set(people or [])
    out = []
    for b in bills:
        d = b.get("date", "")
        if date_start and d < date_start:
            continue
        if date_end and d > date_end:
            continue
        if people_set:
            hit = (b["payer"] in people_set) or (set(b["shares"]) & people_set)
            if not hit:
                continue
        out.append(b)
    return out


def settle(bills):
    paid = defaultdict(float)
    owe = defaultdict(float)
    for b in bills:
        paid[b["payer"]] += float(b["amount"])
        for p, v in b["shares"].items():
            owe[p] += float(v)
    balance = defaultdict(float)
    for p in set(list(paid) + list(owe)):
        balance[p] = round(owe[p] - paid[p], 2)
    return dict(balance), min_transfers(balance)


MAX_EXACT_PEOPLE = 16  # 状态压缩 DP 的人数上限，超出时退回贪心


def greedy_transfers(people):
    """people: [(姓名, 分)]，正=欠钱，负=被欠。返回 [(转出人, 收款人, 分)]"""
    debtors = sorted([[p, v] for p, v in people if v > 0],
                     key=lambda x: x[1], reverse=True)
    creditors = sorted([[p, -v] for p, v in people if v < 0],
                       key=lambda x: x[1], reverse=True)
    transfers = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amt = min(debtors[i][1], creditors[j][1])
        transfers.append((debtors[i][0], creditors[j][0], amt))
        debtors[i][1] -= amt
        creditors[j][1] -= amt
        if debtors[i][1] == 0: i += 1
        if creditors[j][1] == 0: j += 1
    return transfers


def min_transfers(balance):
    """最少笔数转账：把人拆成尽可能多的"净额和为 0"的小组，k 人小组需 k-1 笔。"""
    people = [(p, round(v * 100)) for p, v in balance.items() if round(v * 100) != 0]
    # 分摊四舍五入可能留下几分钱差额，记到净额绝对值最大的人身上
    residual = sum(c for _, c in people)
    if residual:
        k = max(range(len(people)), key=lambda i: abs(people[i][1]))
        people[k] = (people[k][0], people[k][1] - residual)
        people = [x for x in people if x[1] != 0]

    n = len(people)
    if n > MAX_EXACT_PEOPLE:
        groups = [people]
    else:
        size = 1 << n
        total = [0] * size  # total[m]：集合 m 的净额之和
        for m in range(1, size):
            low = (m & -m).bit_length() - 1
            total[m] = total[m & (m - 1)] + people[low][1]
        best = [0] * size   # best[m]：集合 m 最多能拆出的零和小组数
        last = [0] * size   # 取得 best[m] 时最后加入的人
        for m in range(1, size):
            b, arg = -1, 0
            for k in range(n):
                if m >> k & 1 and best[m ^ (1 << k)] > b:
                    b, arg = best[m ^ (1 << k)], k
            best[m] = b + (total[m] == 0)
            last[m] = arg
        # 回溯加入顺序，前缀和每归零一次就切出一个小组
        order, m = [], size - 1
        while m:
            order.append(last[m])
            m ^= 1 << last[m]
        groups, cur, s = [], [], 0
        for k in reversed(order):
            cur.append(people[k])
            s += people[k][1]
            if s == 0:
                groups.append(cur)
                cur = []

    return [(a, b, c / 100) for g in groups for a, b, c in greedy_transfers(g)]


def stat_top_payer(bills):
    paid = defaultdict(float)
    for b in bills:
        paid[b["payer"]] += float(b["amount"])
    return sorted(paid.items(), key=lambda x: x[1], reverse=True)


def stat_top_consumer(bills):
    owe = defaultdict(float)
    for b in bills:
        for p, v in b["shares"].items():
            owe[p] += float(v)
    return sorted(owe.items(), key=lambda x: x[1], reverse=True)

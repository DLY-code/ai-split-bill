import os
import openpyxl
import json
import datetime
import requests
from collections import defaultdict

# ================= 1. 配置 =================
url = "https://agi.tongji.edu.cn/v1/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.getenv('API_KEY', '')}"  # 通过环境变量 API_KEY 提供
}
MODEL = "DeepSeek-V4.1-Flash"
excel_file = "AA记账本.xlsx"

# 设定当前用户名，与记账时保持一致
MY_NAME = "我"

# ================= 2. 读 Excel =================
def load_rows():
    wb = openpyxl.load_workbook(excel_file)
    ws = wb.active
    rows = []
    for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True)):
        if row[0] is None:
            continue
        date, payer, amount, shares_str, remark = row
        rows.append({
            "idx": idx,
            "date": str(date),
            "payer": payer,
            "amount": float(amount),
            "shares": json.loads(shares_str),
            "remark": remark or ""
        })
    return rows


# ================= 3. 时间与人员过滤 =================
def filter_rows(rows, date_start, date_end, people=None):
    people_set = set(people or [])
    out = []
    for r in rows:
        if date_start and r["date"] < date_start:
            continue
        if date_end and r["date"] > date_end:
            continue
        if people_set:
            hit = (r["payer"] in people_set) or (set(r["shares"]) & people_set)
            if not hit:
                continue
        out.append(r)
    return out


# ================= 4. AA 结算 =================
def calculate_settlement(rows):
    """净额 = Σ应承担(shares) - Σ垫付(payer)；正=欠钱，负=被欠"""
    paid = defaultdict(float)
    owe = defaultdict(float)
    for r in rows:
        paid[r["payer"]] += r["amount"]
        for p, v in r["shares"].items():
            owe[p] += float(v)

    balance = defaultdict(float)
    for p in set(list(paid) + list(owe)):
        balance[p] = round(owe[p] - paid[p], 2)

    debtors = sorted([[p, v] for p, v in balance.items() if v > 0.01],
                     key=lambda x: x[1], reverse=True)
    creditors = sorted([[p, -v] for p, v in balance.items() if v < -0.01],
                       key=lambda x: x[1], reverse=True)

    print("\n==== 💰 AA 结算方案 ====\n")
    if not debtors and not creditors:
        print("🎉 账目已平，不需要转账！\n")
        return

    print("【各人净额】（正=欠钱，负=被欠）")
    for p, v in balance.items():
        print(f"  {p}: {v:+.2f}")
    print()

    for debtor, creditor, amt in min_transfers(balance):
        print(f"👉 {debtor} 需要转账给 {creditor}： {amt:.2f} 元")
    print()


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


# ================= 5. 统计：谁付钱最多 =================
def stat_top_payer(rows):
    paid = defaultdict(float)
    for r in rows:
        paid[r["payer"]] += r["amount"]
    print("\n==== 💳 谁付钱最多（按垫付净额）====\n")
    if not paid:
        print("无数据。\n")
        return
    ranked = sorted(paid.items(), key=lambda x: x[1], reverse=True)
    for name, amt in ranked:
        print(f"  {name}: {amt:+.2f} 元")
    print()


# ================= 6. 统计：谁消费额最高 =================
def stat_top_consumer(rows):
    owe = defaultdict(float)
    for r in rows:
        for p, v in r["shares"].items():
            owe[p] += float(v)
    print("\n==== 🍽️ 谁消费额最高（按应承担净额）====\n")
    if not owe:
        print("无数据。\n")
        return
    ranked = sorted(owe.items(), key=lambda x: x[1], reverse=True)
    for name, amt in ranked:
        print(f"  {name}: {amt:+.2f} 元")
    print()


# ================= 7. 主循环 =================
print("\n=== 🤖 AI 财务秘书已上线 ===")
print("支持功能：")
print("  - 查明细：'最近一个月张三付了哪些钱？'")
print("  - 查消费：'我最近消费了多少？'")
print("  - AA 结算：'最近一个月A一下' / '全部结算'")
print("  - 谁付钱最多：'最近一个月谁付钱最多？'")
print("  - 谁消费最多：'最近一个月谁消费额最高？'")
print("输入 '退出' 结束。\n")

while True:
    user_query = input("你问: ").strip()
    if user_query == '退出':
        break
    if not user_query:
        continue

    rows = load_rows()
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- AI 只识别意图和参数 ----
    prompt = f"""你是 AA 账本助手，只做意图识别，不筛选账单。
当前时间：{current_time}
用户问题：“{user_query}”

【用户身份】
用户本人在账本中的名字是："{MY_NAME}"。
当用户问及"我"、"本人"、"自己"的消费情况时，请将 target_people 提取为 ["{MY_NAME}"]。

输出严格 JSON：
{{
  "intent": "query_detail / query_total / settle / top_payer / top_consumer / unknown",
  "target_people": ["人名"],
  "date_start": "YYYY-MM-DD 或 null",
  "date_end": "YYYY-MM-DD 或 null"
}}

规则：
- query_detail：查具体流水（如"张三付了哪些钱"）
- query_total：查某人一共消费了多少/花了多少（如"张三消费了多少"、"我花了多少"）
- settle：A钱/结算/出方案
- top_payer：谁付钱最多/谁垫付最多
- top_consumer：谁消费额最高/谁花得最多
- "最近一个月" → date_start = 今天-30天，date_end = 今天
- "最近一周" → date_start = 今天-7天
- "昨天" → date_start = 昨天，date_end = 昨天
- "这两天/这两天" → date_start = 今天-2天，date_end = 今天
- 未提时间范围则都填 null
- target_people：明确出现的人名，没有则 []
"""

    data = {"model": MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1}
    try:
        reply = requests.post(url, headers=headers, json=data).json()["choices"][0]["message"]["content"]
        reply = reply.replace("```json", "").replace("```", "").strip()
        ai_res = json.loads(reply)
    except Exception as e:
        print(f"❌ AI 解析失败：{e}\n")
        continue

    intent = ai_res.get("intent", "unknown")
    people = ai_res.get("target_people", [])
    ds = ai_res.get("date_start")
    de = ai_res.get("date_end")
    
    # 兜底：如果 AI 没识别出 "我"，但用户问题里包含 "我"
    if not people and ("我" in user_query or MY_NAME in user_query):
        people = [MY_NAME]

    # 如果目标人里面包含 "我"，转成 MY_NAME
    people = [MY_NAME if p in ("我", "本人", "自己") else p for p in people]

    if intent in ("query_detail", "query_total") and people:
        selected = filter_rows(rows, ds, de, people)
    else:
        selected = filter_rows(rows, ds, de, None)

    if not selected:
        print("⚠️ 没找到符合条件的账单。\n")
        continue

    if intent == "query_detail":
        print(f"\n🔍 找到 {len(selected)} 笔：")
        for r in selected:
            tag = "消费" if r["amount"] > 0 else "退款"
            print(f"  [{r['idx']}] 📅 {r['date']} | {r['payer']} {tag} {r['amount']} | "
                  f"分摊:{r['shares']} | {r['remark']}")
        print()

    elif intent == "query_total":
        if not people:
            print("⚠️ 没识别出要查谁，请明确人名。\n")
            continue
        total = 0.0
        for r in selected:
            for p in people:
                total += float(r["shares"].get(p, 0))
        print(f"\n💰 {', '.join(people)} 一共消费了 {total:.2f} 元（已扣除退款）。\n")

    elif intent == "settle":
        print(f"\n🧮 对 {len(selected)} 笔账单结算中...")
        calculate_settlement(selected)

    elif intent == "top_payer":
        stat_top_payer(selected)

    elif intent == "top_consumer":
        stat_top_consumer(selected)

    else:
        print("🤔 没听懂，换个说法试试？\n")
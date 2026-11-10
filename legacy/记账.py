import requests
import json
import datetime
import os
from openpyxl import Workbook, load_workbook

# ================= 配置 =================
url = "https://agi.tongji.edu.cn/v1/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {os.getenv('API_KEY', '')}"  # 通过环境变量 API_KEY 提供
}
MODEL = "DeepSeek-V4.1-Flash"
excel_file = "AA记账本.xlsx"
MY_NAME = os.getenv("MY_NAME", "我")  # 或 input("请输入你的名字：")

# ================= 初始化 Excel =================
if not os.path.exists(excel_file):
    wb = Workbook()
    ws = wb.active
    ws.append(["日期", "付款人", "总金额", "分摊明细", "备注"])
    wb.save(excel_file)
    print(f"✅ 已创建新的记账本：{excel_file}")


# ================= 校验函数 =================
def validate_record(info):
    """
    校验单条记录。规则：
    - amount 非零（正=消费，负=退款）
    - payer 非空字符串
    - shares 为合法人名字典，无通配符
    - shares 求和 ≈ amount（正负同号）
    """
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


# ================= 修改后的 AI 解析 =================
def extract_info(user_text):
    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    MY_NAME = "我" 

    prompt = f"""你是一个严谨的 AA 记账助手。请从用户输入中提取记账信息，输出 JSON 数组 []。
用户当前时间：{current_time}

【核心规则】
1. 只有一种记录类型。amount 正数=共同消费，负数=退款/冲销。
2. payer 是"钱从谁手里出去"的人。
3. shares 的 key 是【真实人名】，禁止 "*"。
4. shares 表示【每个人最终应承担的份额】，求和必须等于 amount。
5. 【防幻觉铁律】：如果用户提到“我们几个人”、“大家”AA，但文本中**没有明确列出所有人的名字**，导致你无法准确知道 shares 里该填哪些人，**请直接输出 []**。绝对不能使用示例中的名字（如王五、赵六）去填充！
6. 【人数校验铁律】：如果文本明确说“4个人”，但你只能从文本中找到少于 4 个人的名字，请直接输出 []。
7. 【当前用户身份】：用户本人在账本中的名字是："{MY_NAME}"。当用户说"我"时，一律替换为 "{MY_NAME}"。
8. 默认付钱人也参与了，除非明确说付钱人没参加。

【参考范例】
示例1：信息完整，正常提取
"今天中午张三付了外卖120，我、李四、王五四个人一起吃AA。"
→ [{{"date":"YYYY-MM-DD","payer":"张三","amount":120,
     "shares":{{"{MY_NAME}":30,"张三":30,"李四":30,"王五":30}},"remark":"外卖"}}]

示例2：信息缺失，直接报空
"今天下午张三买奶茶花了50，晚上李四买夜宵花了200，都是我们四个人AA。"
→ [] 
（因为文本里只出现了张三、李四和我，不知道第 4 个人是谁，绝不能瞎编）

示例3：退款
"剧本杀退款了200给赵六，赵六需要分给张三、李四、王五各50。"
→ [{{"date":"YYYY-MM-DD","payer":"赵六","amount":-200,
     "shares":{{"赵六":-50,"张三":-50,"李四":-50,"王五":-50}},"remark":"剧本杀退款"}}]

【输出格式】
[
  {{"date": "YYYY-MM-DD", "payer": "姓名",
    "amount": 数字(可负), "shares": {{"人名": 数字}}, "remark": "备注"}}
]

用户输入：{user_text}
"""

    data = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }

    try:
        response = requests.post(url, headers=headers, json=data)
        reply = response.json()["choices"][0]["message"]["content"]
        reply = reply.replace("```json", "").replace("```", "").strip()
        raw_list = json.loads(reply)
        
        if not isinstance(raw_list, list):
            return None
            
        return [x for x in (validate_record(i) for i in raw_list) if x]
    except Exception as e:
        print(f"❌ 解析失败：{e}")
        return None


# ================= 主循环 =================
print("\n=== 🤖 AI 智能记账本已启动 ===")
while True:
    user_input = input("\n请输入记账内容 (输入 '退出' 结束): ")
    if user_input.strip() == '退出':
        break
    if not user_input.strip():
        continue

    print("AI 正在解析...")
    info_list = extract_info(user_input)

    # 1. 拦截 AI 返回 None（网络或格式错误）
    if info_list is None:
        print("❌ AI 解析出错了，请换个说法试试。\n")
        continue

    # 2. 拦截 AI 返回空列表（防幻觉机制触发，或信息不全）
    if len(info_list) == 0:
        print("⚠️ 解析失败：无法确定完整的参与人员或金额，可能是人数不匹配。请检查是否漏说了人名哦~\n")
        continue

    # 3. 正常写入
    wb = load_workbook(excel_file)
    ws = wb.active
    for info in info_list:
        tag = "消费" if info["amount"] > 0 else "退款"
        print("-" * 40)
        print(f"📅 时间: {info.get('date')}")
        print(f"🏷️ 类型: {tag}")
        print(f"💳 付款人: {info['payer']}")
        print(f"💰 金额: {info['amount']}")
        print(f"👥 分摊: {info['shares']}")
        print(f"📝 备注: {info.get('remark', '')}")

        ws.append([
            info.get('date'), info["payer"], info["amount"],
            json.dumps(info["shares"], ensure_ascii=False), info.get('remark', '')
        ])
    wb.save(excel_file)
    print("-" * 40)
    print(f"✅ 成功记入 {len(info_list)} 笔账！\n")
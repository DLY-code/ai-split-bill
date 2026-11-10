import streamlit as st
import requests
import json
import datetime
import pandas as pd
from ledger_core import (validate_record, filter_bills, settle,
                         stat_top_payer, stat_top_consumer)

# ================= 页面 =================
st.set_page_config(page_title="AA 智能账本", page_icon="🤖", layout="wide")
st.title("🤖 AA 智能账本")
st.markdown("一句话记账，AI 帮你 A 账。")

MY_NAME = "我"

# ================= 侧栏 =================
with st.sidebar:
    st.header("⚙️ 设置")
    api_key = st.text_input("API Key", type="password",
                            help="同济/OpenAI 等兼容接口的 Key")
    base_url = st.text_input("Base URL", value="https://agi.tongji.edu.cn/v1")
    model_name = st.text_input("模型名称", value="DeepSeek-V4.1-Flash")
    st.divider()
    st.markdown("### 使用说明")
    st.markdown(
        "1. 填入 Key\n"
        "2. 【记账】页说人话 or 手动录入\n"
        "3. 【查账】页提问\n\n"
        "**规则**：正数=消费，负数=退款；shares 求和 = amount"
    )

# ================= 状态 =================
if "bills" not in st.session_state:
    st.session_state.bills = []
if "next_id" not in st.session_state:
    st.session_state.next_id = 1

# ================= 工具 =================
def call_ai(prompt):
    url = f"{base_url}/chat/completions"
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {api_key}"}
    data = {"model": model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1}
    try:
        # 🔴 修改点：把 timeout 从 60 改成 180（3分钟），给大模型充足的思考时间
        res = requests.post(url, headers=headers, json=data, timeout=180)
        reply = res.json()["choices"][0]["message"]["content"]
        return reply.replace("```json", "").replace("```", "").strip()
    except Exception as e:
        st.error(f"AI 调用失败: {e}")
        return None


def add_bill(info):
    info["id"] = st.session_state.next_id
    st.session_state.next_id += 1
    st.session_state.bills.append(info)


# ================= Tabs =================
tab1, tab2 = st.tabs(["📝 智能记账", "🧮 查账与结算"])

# ---------------- Tab 1 ----------------
with tab1:
    st.subheader("💬 一句话记账")
    user_input = st.text_area(
        "说句话吧：",
        placeholder="例：昨天张三付了KTV 800，李四王五赵六一起AA。",
        height=120
    )
    if st.button("✨ AI 解析并记账", type="primary", key="btn_ai_add"):
        if not api_key:
            st.warning("请先填入 API Key")
        elif not user_input:
            st.warning("请输入内容")
        else:
            with st.spinner("AI 正在解析..."):
                current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                prompt = f"""你是一个严谨的 AA 记账助手。请从用户输入中提取记账信息，输出 JSON 数组 []。
用户当前时间：{current_time}

【核心规则】
1. 只有一种记录类型。amount 正数=共同消费，负数=退款/冲销。
2. payer 是"钱从谁手里出去"的人。
3. shares 的 key 是【真实人名】，禁止 "*"。
4. shares 表示【每个人最终应承担的份额】，求和必须等于 amount。
5. 【防幻觉铁律】：如果用户提到“我们几个人”、“大家”AA，但文本中**没有明确列出所有人的名字**，导致你无法准确知道 shares 里该填哪些人，**请直接输出 []**。绝对不能使用示例中的名字去填充！
6. 【人数校验铁律】：如果文本明确说“4个人”，但你只能从文本中找到少于 4 个人的名字，请直接输出 []。
7. 【代垫/提前转账铁律】：如果文本中出现“某人私下转给某人钱”，或者“某人提前A了钱给某人”，这不能直接用“修改分摊份额”来算。必须拆出一条单独的记录：payer = 收钱的人，amount = 转账金额，shares = {{转出钱的人: 转账金额}}。因为这代表收钱的人替转出钱的人垫付了他应承担的钱。
8. 【当前用户身份】：用户本人在账本中的名字是："{MY_NAME}"。当用户说"我"、"本人"、"自己"时，一律替换为 "{MY_NAME}"。
9. 默认付钱人也参与了，除非明确说付钱人没参加。

【参考范例】
示例1：信息完整
"今天中午张三付了外卖120，我、李四、王五四个人一起吃AA。"
→ [{{"date":"YYYY-MM-DD","payer":"张三","amount":120,
     "shares":{{"{MY_NAME}":30,"张三":30,"李四":30,"王五":30}},"remark":"外卖"}}]

示例2：信息缺失
"今天下午张三买奶茶花了50，晚上李四买夜宵花了200，都是我们四个人AA。"
→ [] 

示例3：退款
"剧本杀退款了200给赵六，赵六需要分给张三、李四、王五各50。"
→ [{{"date":"YYYY-MM-DD","payer":"赵六","amount":-200,
     "shares":{{"赵六":-50,"张三":-50,"李四":-50,"王五":-50}},"remark":"剧本杀退款"}}]

【输出格式】
[{{"date": "YYYY-MM-DD", "payer": "姓名", "amount": 数字(可负), "shares": {{"人名": 数字}}, "remark": "备注"}}]

用户输入：{user_input}
"""
                reply = call_ai(prompt)
                if reply:
                    try:
                        raw = json.loads(reply)
                        if not isinstance(raw, list):
                            raise ValueError
                            
                        valid = [x for x in (validate_record(i) for i in raw) if x]
                        
                        if len(raw) == 0:
                            st.error("⚠️ 解析失败：无法确定完整的参与人员或金额，可能是人数不匹配。请检查是否漏说了人名，补齐后再试哦~")
                            st.stop()
                            
                        if not valid:
                            st.error("❌ AI 输出的记录全部不符合格式要求（如金额不平、人名乱码），已拦截。")
                        else:
                            for info in valid:
                                if "date" not in info or not info["date"]:
                                    info["date"] = datetime.datetime.now().strftime("%Y-%m-%d")
                                add_bill(info)
                            st.success(f"✅ 成功记入 {len(valid)} 笔账！")
                            st.rerun()
                    except Exception as e:
                        st.error("❌ AI 返回格式有误，请重试。")
                else:
                    st.error("❌ AI 响应异常，请检查网络或 API Key。")

    st.divider()

    # ---- 手动记账兜底 ----
    with st.expander("✍️ 手动记账（AI 抽风时兜底）"):
        with st.form("manual_form"):
            col1, col2 = st.columns(2)
            with col1:
                m_date = st.date_input("日期", value=datetime.date.today())
                m_payer = st.text_input("付款人（消费=垫资人，退款=退款人）")
            with col2:
                m_amount = st.number_input("金额（正=消费，负=退款）",
                                           value=0.0, step=1.0, format="%.2f")
                m_remark = st.text_input("备注")
            shares_raw = st.text_area(
                "分摊明细（JSON）",
                placeholder='消费例：{"张三": 200, "李四": 200}\n'
                            '退款例：{"张三": -50, "李四": -50}'
            )
            submitted = st.form_submit_button("写入账本")
            if submitted:
                try:
                    shares = json.loads(shares_raw)
                    info = {"date": str(m_date), "payer": m_payer,
                            "amount": m_amount, "shares": shares, "remark": m_remark}
                    v = validate_record(info)
                    if v:
                        add_bill(v)
                        st.success("✅ 已写入")
                        st.rerun()
                    else:
                        st.error("校验失败：检查 amount 非零、shares 求和 = amount、无通配符。")
                except Exception as e:
                    st.error(f"JSON 解析失败：{e}")

    st.divider()
    st.subheader("📒 当前账本明细")

    if st.session_state.bills:
        df = pd.DataFrame([
            {"ID": b["id"], "日期": b["date"],
             "类型": "消费" if b["amount"] > 0 else "退款",
             "付款人": b["payer"], "金额": b["amount"],
             "分摊": json.dumps(b["shares"], ensure_ascii=False),
             "备注": b.get("remark", "")}
            for b in st.session_state.bills
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)

        col_a, col_b = st.columns([1, 1])
        with col_a:
            if st.button("🗑️ 清空所有账单"):
                st.session_state.bills = []
                st.session_state.next_id = 1
                st.rerun()
        with col_b:
            del_id = st.number_input("按 ID 删除", min_value=1, step=1, key="del_id")
            if st.button("删除该条"):
                before = len(st.session_state.bills)
                st.session_state.bills = [b for b in st.session_state.bills
                                          if b["id"] != del_id]
                if len(st.session_state.bills) < before:
                    st.success(f"已删除 ID={del_id}")
                    st.rerun()
                else:
                    st.warning("未找到该 ID")
    else:
        st.info("账本空空如也，快去记第一笔账吧！")


# ================= 查账页 =================
with tab2:
    st.subheader("向你的 AI 财务秘书提问")

    if not st.session_state.bills:
        st.info("账本是空的，请先去【智能记账】记几笔。")
    else:
        query = st.text_input(
            "例如：最近一个月A一下 / 我消费了多少 / 谁付钱最多 / 谁消费额最高"
        )
        if st.button("🚀 执行", type="primary", key="btn_query"):
            if not api_key:
                st.warning("请先填入 API Key")
            elif not query:
                st.warning("请输入问题")
            else:
                with st.spinner("AI 正在分析意图..."):
                    current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    prompt = f"""你是 AA 账本助手，只做意图识别，不筛选账单。
当前时间：{current_time}
用户问题：“{query}”

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
- query_detail：查具体流水
- query_total：查某人一共消费了多少/花了多少
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
                    reply = call_ai(prompt)
                    if not reply:
                        st.stop()
                    try:
                        ai_res = json.loads(reply)
                    except Exception:
                        st.error("AI 返回格式有误")
                        st.stop()

                intent = ai_res.get("intent", "unknown")
                people = ai_res.get("target_people", [])
                ds = ai_res.get("date_start")
                de = ai_res.get("date_end")
                
                if not people and ("我" in query or MY_NAME in query):
                    people = [MY_NAME]
                people = [MY_NAME if p in ("我", "本人", "自己") else p for p in people]

                if intent in ("query_detail", "query_total") and people:
                    selected = filter_bills(st.session_state.bills, ds, de, people)
                else:
                    selected = filter_bills(st.session_state.bills, ds, de, None)

                if not selected:
                    st.warning("没找到符合条件的账单。")
                elif intent == "query_detail":
                    st.success(f"找到 {len(selected)} 笔：")
                    for b in selected:
                        tag = "消费" if b["amount"] > 0 else "退款"
                        st.write(f"📅 {b['date']} | {b['payer']} {tag} {b['amount']} 元 "
                                 f"| 分摊: {json.dumps(b['shares'], ensure_ascii=False)} "
                                 f"| 备注: {b.get('remark', '')}")
                elif intent == "query_total":
                    if not people:
                        st.warning("未识别出人名，请明确。")
                    else:
                        total = 0.0
                        for b in selected:
                            for p in people:
                                total += float(b["shares"].get(p, 0))
                        st.success(
                            f"💰 {', '.join(people)} 一共消费了 **{total:.2f} 元** "
                            f"（已扣除退款，共涉及 {len(selected)} 笔记录）"
                        )
                elif intent == "settle":
                    st.write(f"🧮 对 {len(selected)} 笔账单结算：")
                    balance, transfers = settle(selected)

                    st.markdown("**各人净额**（正=欠钱，负=被欠）")
                    bal_df = pd.DataFrame(
                        [{"姓名": k, "净额": v} for k, v in balance.items()]
                    ).sort_values("净额", ascending=False)
                    st.dataframe(bal_df, use_container_width=True, hide_index=True)

                    st.markdown("**💰 最终结算方案**")
                    if not transfers:
                        st.info("🎉 账目已平，无需转账")
                    else:
                        for d, c, amt in transfers:
                            st.markdown(f"👉 **{d}** 转账给 **{c}**：**{amt:.2f} 元**")
                elif intent == "top_payer":
                    st.markdown("**💳 谁付钱最多（按垫付净额）**")
                    ranked = stat_top_payer(selected)
                    df = pd.DataFrame([{"姓名": n, "垫付净额": v} for n, v in ranked])
                    st.dataframe(df, use_container_width=True, hide_index=True)
                elif intent == "top_consumer":
                    st.markdown("**🍽️ 谁消费额最高（按应承担净额）**")
                    ranked = stat_top_consumer(selected)
                    df = pd.DataFrame([{"姓名": n, "消费净额": v} for n, v in ranked])
                    st.dataframe(df, use_container_width=True, hide_index=True)
                else:
                    st.warning("🤔 没听懂，换个说法试试？")
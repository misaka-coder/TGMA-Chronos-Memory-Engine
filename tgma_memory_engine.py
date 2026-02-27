import asyncio
import aiosqlite
import time
import re
from datetime import datetime

# ======================
# ⚙️ 全局配置 (可根据需求修改)
# ======================
DB_PATH = "tgma_memory.db"
DEFAULT_USER = "Standard_User"
SUMMARIZE_THRESHOLD = 30  # 每 30 条记录触发一次史官压缩

# ======================
# 🗄️ 数据库核心模块 (基于异步 SQLite)
# ======================
class AsyncDB:
    async def init(self):
        """初始化数据库：包含原始日志表和摘要表"""
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS chat_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT, role TEXT, content TEXT,
                    timestamp INTEGER, summarized INTEGER DEFAULT 0
                )''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS summaries (
                    date TEXT PRIMARY KEY, content TEXT
                )''')
            await conn.commit()

    async def fetch(self, sql, args=()):
        async with aiosqlite.connect(DB_PATH) as conn:
            cursor = await conn.execute(sql, args)
            rows = await cursor.fetchall()
            cols = [d[0] for d in cursor.description] if cursor.description else []
            return [dict(zip(cols, r)) for r in rows]

    async def execute(self, sql, args=()):
        async with aiosqlite.connect(DB_PATH) as conn:
            await conn.execute(sql, args)
            await conn.commit()

    async def add_chat(self, user_id, role, content):
        """记录对话，自动打上绝对时间戳"""
        await self.execute(
            "INSERT INTO chat_logs (user_id, role, content, timestamp) VALUES (?,?,?,?)",
            (user_id, role, content, int(time.time()))
        )

    async def get_unsummarized_logs(self, user_id):
        """获取尚未被压缩的原始记录"""
        return await self.fetch('''
            SELECT id, role, content, timestamp FROM chat_logs
            WHERE user_id=? AND summarized=0 ORDER BY timestamp ASC
        ''', (user_id,))

    async def mark_summarized(self, ids):
        if not ids: return
        ph = ','.join(['?']*len(ids))
        await self.execute(
            f"UPDATE chat_logs SET summarized=1 WHERE id IN ({ph})", ids
        )

    async def save_summary(self, date_str, content):
        await self.execute(
            "INSERT OR REPLACE INTO summaries (date,content) VALUES (?,?)",
            (date_str, content)
        )

    async def get_logs_by_date(self, date_str):
        """调查员专用：按日期精准下潜提取日志"""
        d = datetime.strptime(date_str, "%Y-%m-%d")
        s = int(d.timestamp())
        e = s + 86400
        return await self.fetch('''
            SELECT role,content,timestamp FROM chat_logs
            WHERE timestamp>=? AND timestamp<? ORDER BY timestamp ASC
        ''', (s, e))

    async def get_recent_history(self, user_id, limit=30):
        return await self.fetch('''
            SELECT role,content,timestamp FROM chat_logs
            WHERE user_id=? ORDER BY timestamp DESC LIMIT ?
        ''', (user_id, limit))

db = AsyncDB()

# ==========================================
# 🧠 模块一：动态时间标签 (提供视觉呼吸感)
# ==========================================
async def get_formatted_history(user_id, limit=30):
    """
    根据时间跨度自动格式化标签。
    今日消息显示 [HH:MM]，往日消息显示 [MM-DD HH:MM]
    """
    rows = await db.get_recent_history(user_id, limit)
    now = datetime.now()
    hist = []
    for row in reversed(rows):
        t = datetime.fromtimestamp(row["timestamp"])
        tag = t.strftime("%H:%M") if t.date() == now.date() else t.strftime("%m-%d %H:%M")
        hist.append({"role": row["role"], "content": f"[{tag}] {row['content']}"})
    return hist

# ==========================================
# 📜 模块二：史官压缩 (异步代谢记忆)
# ==========================================
async def run_historian_ai(logs):
    """模拟史官 AI 进行中期事实提炼"""
    if not logs: return
    date_str = datetime.fromtimestamp(logs[0]["timestamp"]).strftime("%Y年%m月%d日")
    chat_text = "\n".join(
        f"[{datetime.fromtimestamp(log['timestamp']).strftime('%H:%M')}] {log['role']}: {log['content']}"
        for log in logs
    )
    prompt = f"""你是冷静的第三方史官。
任务：将聊天记录浓缩为客观事实，用于 AI 的长期记忆。
规则：
1. 每条事实必须以 [日期+模糊时段] 开头。
2. 使用第三人称描述事件（Master，AI）。
3. 只记录核心信息，无内容则回复 [NO_EVENT]。

日期锚点：{date_str}
原始记录：
{chat_text}""".strip()

    summary = await fake_llm(prompt)
    if "[NO_EVENT]" not in summary:
        await db.save_summary(date_str, summary)
    await db.mark_summarized([log["id"] for log in logs])

async def try_summarize(user_id):
    """
    触发器：当未总结记录达标时启动。
    核心点睛：保留最后 2 条不压缩，确保对话流的软着陆连续性。
    """
    logs = await db.get_unsummarized_logs(user_id)
    if len(logs) >= SUMMARIZE_THRESHOLD + 2:
        logs_to_process = logs[:-2]  # 保留最近 2 条不打标，维持当前语境
        print(f"[史官] 启动！正在压缩 {len(logs_to_process)} 条记录，保留 2 条作为上下文衔接。")
        await run_historian_ai(logs_to_process)

# ==========================================
# 🔍 模块三：调查员打捞 (主动回忆机制)
# ==========================================
async def internal_memory_recall(date_str, query):
    """
    调查员 AI：在限定的原始日志中寻找真相。
    """
    logs = await db.get_logs_by_date(date_str)
    if not logs: return "（未发现当天的原始记录）"
    
    text = "\n".join(f"{log['role']}: {log['content']}" for log in logs)
    prompt = f"""你是一个严谨的记忆调查员。
任务：根据提供的【原始日志】回答用户的疑问。
规则：
1. 只能根据提供的日志内容回答，禁止自行推理或编造。
2. 如果日志中找不到相关细节，请明确回复：“相关记忆已模糊，未发现匹配细节”。

用户疑问：{query}
【原始日志内容】：
{text}""".strip()
    return await fake_llm(prompt)

async def think_and_reply(user_input, user_id=DEFAULT_USER):
    """
    主推理循环：支持 ReAct 模式的主动打捞。
    """
    await db.add_chat(user_id, "user", user_input)
    history = await get_formatted_history(user_id)
    
    messages = history + [{"role": "user", "content": user_input}]
    reply = await fake_llm("\n".join(m["content"] for m in messages))

    # 🚀 Agentic RAG：正则拦截 AI 的主动回忆请求
    match = re.search(r"\[RECALL\|(\d{4}-\d{2}-\d{2})\|(.*?)\]", reply)
    if match:
        date, query = match.groups()
        print(f"[系统拦截] AI 正在尝试打捞 {date} 的记忆：{query}")
        evidence = await internal_memory_recall(date, query)
        
        # 将真相塞回上下文，触发二次思考
        messages.append({"role": "assistant", "content": reply})
        messages.append({"role": "system", "content": f"【打捞结果汇报】：\n{evidence}\n请结合此结果对用户做出最终回复。"})
        reply = await fake_llm("\n".join(m["content"] for m in messages))

    await db.add_chat(user_id, "assistant", reply)
    return reply

# ==========================================
# 🤖 演示用的 Fake LLM (实战时请替换为真实 API)
# ==========================================
async def fake_llm(prompt):
    # 模拟一个会主动回忆的 AI
    if "昨天聊了什么" in prompt:
        # 假设今天是 2026-02-27，它会去查 02-26
        return "[RECALL|2026-02-26|昨天下午我们讨论的关于橘猫的话题]"
    return "这是一条带有时间感的模拟回复：我已经记下你刚才说的话啦～"

# ======================
# 🏁 启动演示
# ======================
async def main():
    await db.init()
    print("==========================================")
    print("🌸 TGMA (Chronos) 记忆引擎已就绪")
    print("输入 'exit' 退出程序")
    print("==========================================")
    
    while True:
        msg = input("你: ")
        if msg.lower() in ("exit", "quit"): break
        response = await think_and_reply(msg)
        print(f"AI: {response}")
        
        # 每一轮对话后尝试检查是否需要史官介入
        await try_summarize(DEFAULT_USER)

if __name__ == "__main__":
    asyncio.run(main())
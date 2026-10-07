import os
import sqlite3
from datetime import datetime
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from google import genai

app = FastAPI()

# Gemini API 클라이언트 초기화
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

DB_PATH = "routines.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS routines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            title TEXT,
            is_done INTEGER DEFAULT 0,
            completed_at TEXT,
            created_date TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

class TaskCreate(BaseModel):
    category: str
    title: str

class TaskToggle(BaseModel):
    id: int

# 프론트엔드 HTML (파일 분리 없이 하나로 통합)
HTML_LAYOUT = """
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>루틴 트래커 & AI</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#0b0f17] text-white flex justify-center min-h-screen">
  <div class="w-full max-w-md bg-[#121620] border-x border-[#1e2330] flex flex-col min-h-screen shadow-2xl">
    
    <header class="p-5 border-b border-[#1e2330] flex justify-between items-center bg-[#161b26]">
      <div>
        <h1 class="text-xl font-bold tracking-tight">스마트 루틴 & AI</h1>
        <p class="text-xs text-slate-400 mt-0.5" id="current-date"></p>
      </div>
      <button onclick="requestAIFeedback()" class="bg-indigo-600 hover:bg-indigo-500 text-xs px-3 py-1.5 rounded-full font-semibold transition">
        ✨ AI 분석
      </button>
    </header>

    <div id="ai-card" class="m-4 p-4 rounded-xl bg-gradient-to-r from-indigo-950/40 to-slate-900 border border-indigo-500/30 hidden">
      <div class="flex items-center space-x-2 text-indigo-400 font-bold text-sm mb-2">
        <span>🤖</span> <span>Gemini 스마트 루틴 분석</span>
      </div>
      <p id="ai-text" class="text-xs leading-relaxed text-slate-300 whitespace-pre-line"></p>
    </div>

    <div class="px-4 py-3">
      <div class="flex items-center space-x-2 mb-3">
        <span class="bg-[#242b3d] text-teal-300 text-xs px-3 py-1 rounded-full font-semibold">🔒 일상 루틴</span>
      </div>
      <form onsubmit="addTask(event)" class="flex gap-2">
        <input type="text" id="task-input" placeholder="새 루틴 추가..." required
          class="flex-1 bg-[#1a202e] border border-[#2d354a] rounded-xl px-4 py-2.5 text-sm focus:outline-none focus:border-teal-400 placeholder-slate-500">
        <button type="submit" class="bg-teal-400 hover:bg-teal-300 text-black font-bold px-4 rounded-xl text-lg transition">+</button>
      </form>
    </div>

    <div class="flex-1 px-4 py-2 space-y-2 overflow-y-auto" id="routine-list"></div>
  </div>

  <script>
    document.getElementById('current-date').innerText = new Date().toLocaleDateString('ko-KR', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'short' });

    async function fetchRoutines() {
      const res = await fetch('/api/routines');
      const data = await res.json();
      const list = document.getElementById('routine-list');
      list.innerHTML = '';

      data.forEach(item => {
        const itemEl = document.createElement('div');
        itemEl.className = "flex items-center justify-between p-3.5 bg-[#171c28] hover:bg-[#1c2233] rounded-xl transition cursor-pointer select-none border border-slate-800/40";
        itemEl.onclick = () => toggleTask(item.id);

        const checkClass = item.is_done ? "bg-teal-400 text-black border-teal-400" : "border-slate-600 text-transparent";
        const textClass = item.is_done ? "line-through text-slate-500" : "text-slate-200 font-medium";

        itemEl.innerHTML = `
          <div class="flex items-center space-x-3">
            <div class="w-6 h-6 rounded-lg border-2 flex items-center justify-center font-bold text-xs transition ${checkClass}">✔</div>
            <span class="text-sm ${textClass}">${item.title}</span>
          </div>
          ${item.is_done && item.completed_at ? `<span class="text-[11px] font-mono text-teal-400 bg-teal-950/60 border border-teal-800/40 px-2 py-0.5 rounded-md">${item.completed_at} 완료</span>` : ''}
        `;
        list.appendChild(itemEl);
      });
    }

    async function addTask(e) {
      e.preventDefault();
      const input = document.getElementById('task-input');
      await fetch('/api/routines', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ category: '일상', title: input.value })
      });
      input.value = '';
      fetchRoutines();
    }

    async function toggleTask(id) {
      await fetch('/api/routines/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
      });
      fetchRoutines();
    }

    async function requestAIFeedback() {
      const card = document.getElementById('ai-card');
      const text = document.getElementById('ai-text');
      card.classList.remove('hidden');
      text.innerText = "Gemini가 완료 시간대 패턴을 분석 중입니다...";

      const res = await fetch('/api/ai/analyze', { method: 'POST' });
      const data = await res.json();
      text.innerText = data.feedback;
    }

    fetchRoutines();
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(content=HTML_LAYOUT)

@app.get("/api/routines")
def get_routines():
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, category, title, is_done, completed_at FROM routines WHERE created_date = ?", (today,))
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "category": r[1], "title": r[2], "is_done": bool(r[3]), "completed_at": r[4]}
        for r in rows
    ]

@app.post("/api/routines")
def add_routine(task: TaskCreate):
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO routines (category, title, is_done, completed_at, created_date) VALUES (?, ?, 0, NULL, ?)",
                (task.category, task.title, today))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/api/routines/toggle")
def toggle_routine(payload: TaskToggle):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT is_done FROM routines WHERE id = ?", (payload.id,))
    row = cur.fetchone()
    if not row:
        return {"error": "Not found"}

    now_done = 0 if row[0] == 1 else 1
    completed_at = datetime.now().strftime("%H:%M") if now_done == 1 else None

    cur.execute("UPDATE routines SET is_done = ?, completed_at = ? WHERE id = ?", (now_done, completed_at, payload.id))
    conn.commit()
    conn.close()
    return {"is_done": bool(now_done), "completed_at": completed_at}

@app.post("/api/ai/analyze")
def analyze_routines():
    today = datetime.now().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT title, is_done, completed_at FROM routines WHERE created_date = ?", (today,))
    items = cur.fetchall()
    conn.close()

    if not items:
        return {"feedback": "분석할 루틴 데이터가 없습니다. 먼저 할 일을 체크해 보세요!"}

    record_text = "\n".join([
        f"- {r[0]}: {'완료 (시각: ' + r[2] + ')' if r[1] else '미완료'}"
        for r in items
    ])

    prompt = f"""
    당신은 스마트 루틴 코치입니다. 사용자의 오늘 루틴 시간대별 기록입니다:
    {record_text}

    다음 사항을 4문장 이내로 친절하게 분석해 주세요:
    1. 오늘 실행력과 시간대별 특징 칭찬
    2. 시간 지연이나 패턴 분석
    3. 내일을 위한 실천 조언 1가지
    """

    if not ai_client:
        return {"feedback": "GEMINI_API_KEY가 설정되지 않았습니다."}

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return {"feedback": response.text}
    except Exception as e:
        return {"feedback": f"AI 분석 오류: {str(e)}"}

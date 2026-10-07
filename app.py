import os
import sqlite3
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from google import genai

app = FastAPI()

# 한국 표준시 (KST = UTC+9) 설정
KST = timezone(timedelta(hours=9))

def get_now_kst():
    return datetime.now(KST)

# Gemini API 클라이언트 초기화
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

DB_PATH = "routines.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # 계획 & 실행 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT,
            title TEXT,
            is_done INTEGER DEFAULT 0,
            completed_at TEXT,
            plan_date TEXT
        )
    """)
    # 카테고리 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE
        )
    """)
    # 기본 카테고리 초기 데이터
    default_cats = ["아침", "업무/학습", "운동", "개인", "저녁"]
    for cat in default_cats:
        cur.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat,))
    conn.commit()
    conn.close()

init_db()

class PlanCreate(BaseModel):
    category: str
    title: str
    plan_date: str

class PlanToggle(BaseModel):
    id: int

class CategoryCreate(BaseModel):
    name: str

class AIRequest(BaseModel):
    plan_date: str

HTML_LAYOUT = """
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>스마트 계획 & 실행 트래커</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-[#0b0f17] text-white flex justify-center min-h-screen font-sans">
  <div class="w-full max-w-md bg-[#121620] border-x border-[#1e2330] flex flex-col min-h-screen shadow-2xl">
    
    <!-- 상단 헤더 -->
    <header class="p-4 border-b border-[#1e2330] flex justify-between items-center bg-[#161b26]">
      <div>
        <h1 class="text-lg font-bold tracking-tight text-teal-400">⚡ 스마트 계획 & 실행</h1>
        <p class="text-xs text-slate-400" id="header-week-title">2026년 10월 2주차</p>
      </div>
      <button onclick="requestAIFeedback()" class="bg-indigo-600 hover:bg-indigo-500 text-xs px-3 py-1.5 rounded-full font-semibold transition shadow-md">
        ✨ AI 피드백
      </button>
    </header>

    <!-- 탭 네비게이션 (계획 / 타임라인 / 패치노트) -->
    <nav class="flex border-b border-[#1e2330] bg-[#141924] text-xs font-semibold">
      <button id="tab-plan-btn" onclick="switchTab('plan')" class="flex-1 py-3 text-center border-b-2 border-teal-400 text-teal-300">
        📋 계획 & 실행
      </button>
      <button id="tab-timeline-btn" onclick="switchTab('timeline')" class="flex-1 py-3 text-center border-b-2 border-transparent text-slate-400 hover:text-slate-200">
        ⏱️ 타임라인
      </button>
      <button id="tab-patch-btn" onclick="switchTab('patch')" class="flex-1 py-3 text-center border-b-2 border-transparent text-slate-400 hover:text-slate-200">
        📝 패치노트
      </button>
    </nav>

    <!-- [탭 1] 계획 & 실행 뷰 -->
    <div id="tab-plan" class="flex-1 flex flex-col">
      <!-- 주간 캘린더 네비게이터 -->
      <div class="p-3 bg-[#161b26] border-b border-[#1e2330]">
        <div class="flex justify-between items-center mb-2 px-1">
          <button onclick="changeWeek(-1)" class="text-slate-400 hover:text-white px-2 py-0.5 rounded bg-slate-800 text-xs">◀ 이전 주</button>
          <span id="calendar-title" class="text-xs font-bold text-slate-300"></span>
          <button onclick="changeWeek(1)" class="text-slate-400 hover:text-white px-2 py-0.5 rounded bg-slate-800 text-xs">다음 주 ▶</button>
        </div>
        <div class="grid grid-cols-7 gap-1 text-center" id="week-days-container"></div>
      </div>

      <!-- AI 피드백 카드 -->
      <div id="ai-card" class="mx-3 mt-3 p-3.5 rounded-xl bg-gradient-to-r from-indigo-950/50 to-slate-900 border border-indigo-500/30 hidden">
        <div class="flex items-center space-x-2 text-indigo-400 font-bold text-xs mb-1.5">
          <span>🤖</span> <span>Gemini 실행 피드백</span>
        </div>
        <p id="ai-text" class="text-xs leading-relaxed text-slate-300 whitespace-pre-line"></p>
      </div>

      <!-- 카테고리 선택 및 추가 바 -->
      <div class="p-3">
        <div class="flex items-center space-x-1.5 overflow-x-auto pb-2 scrollbar-none" id="category-bar"></div>

        <!-- 입력 폼 -->
        <form onsubmit="addPlan(event)" class="flex gap-2 mt-2">
          <input type="text" id="plan-input" placeholder="실행할 계획 입력..." required
            class="flex-1 bg-[#1a202e] border border-[#2d354a] rounded-xl px-3.5 py-2.5 text-sm focus:outline-none focus:border-teal-400 text-white placeholder-slate-500">
          <button type="submit" class="bg-teal-400 hover:bg-teal-300 text-black font-bold px-4 rounded-xl text-base transition">+</button>
        </form>
      </div>

      <!-- 계획 목록 -->
      <div class="flex-1 px-3 pb-4 space-y-2 overflow-y-auto" id="plan-list"></div>
    </div>

    <!-- [탭 2] 타임라인 뷰 -->
    <div id="tab-timeline" class="flex-1 flex flex-col p-4 hidden">
      <div class="flex justify-between items-center mb-4">
        <h2 class="text-sm font-bold text-teal-300">⏱️ 오늘 실행 타임라인</h2>
        <span class="text-xs text-slate-400 font-mono" id="timeline-date-label"></span>
      </div>
      <div class="flex-1 overflow-y-auto space-y-3" id="timeline-list"></div>
    </div>

    <!-- [탭 3] 패치노트 뷰 -->
    <div id="tab-patch" class="flex-1 flex flex-col p-4 overflow-y-auto hidden space-y-4">
      <div class="border-b border-slate-800 pb-2">
        <h2 class="text-sm font-bold text-teal-400">🚀 패치노트</h2>
        <p class="text-xs text-slate-400">프로그램 개선 및 업데이트 히스토리</p>
      </div>

      <div class="bg-[#171c28] p-3.5 rounded-xl border border-slate-800">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-bold text-teal-300 bg-teal-950/60 px-2 py-0.5 rounded border border-teal-800/40">v1.1.0</span>
          <span class="text-[11px] text-slate-500">2026-10-07</span>
        </div>
        <ul class="text-xs text-slate-300 space-y-1.5 list-disc list-inside">
          <li><strong>한국 표준시(KST) 적용</strong>: 완료 시각 9시간 오차 전면 수정</li>
          <li><strong>주차별/일자별 네비게이션</strong>: 10월 2주차 등 주간 선택 바 탑재</li>
          <li><strong>텍스트 시인성 개선</strong>: 완료 시 취소선 및 흐림 제거</li>
          <li><strong>실행 타임라인 탭 신설</strong>: 하루 실행 기록 시간순 조회</li>
          <li><strong>카테고리 커스텀 기능</strong>: 계획 카테고리 실시간 추가 지원</li>
        </ul>
      </div>

      <div class="bg-[#171c28] p-3.5 rounded-xl border border-slate-800">
        <div class="flex items-center justify-between mb-2">
          <span class="text-xs font-bold text-slate-300 bg-slate-800 px-2 py-0.5 rounded">v1.0.0</span>
          <span class="text-[11px] text-slate-500">2026-10-07</span>
        </div>
        <ul class="text-xs text-slate-400 space-y-1 list-disc list-inside">
          <li>기본 루틴 등록 및 토글 체크 기능</li>
          <li>완료 시각 자동 기록 및 Gemini 피드백 초안 연동</li>
        </ul>
      </div>
    </div>

  </div>

  <script>
    let currentDate = new Date(); // 로컬 브라우저 기준 날짜
    let selectedDateStr = formatDate(currentDate);
    let selectedCategory = "아침";
    let categories = [];

    function formatDate(d) {
      const year = d.getFullYear();
      const month = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${year}-${month}-${day}`;
    }

    // 주차 계산 함수 (월요일 시작 기준)
    function getWeekInfo(date) {
      const d = new Date(date);
      const dayNum = d.getDay() || 7;
      d.setDate(d.getDate() + 4 - dayNum);
      const yearStart = new Date(d.getFullYear(), 0, 1);
      const weekNo = Math.ceil((((d - yearStart) / 86400000) + 1) / 7);
      
      const month = date.getMonth() + 1;
      // 월별 주차 계산 (간이)
      const firstDayOfMonth = new Date(date.getFullYear(), date.getMonth(), 1);
      const monthWeek = Math.ceil((date.getDate() + firstDayOfMonth.getDay()) / 7);
      return `${date.getFullYear()}년 ${month}월 ${monthWeek}주차`;
    }

    function switchTab(tab) {
      ['plan', 'timeline', 'patch'].forEach(t => {
        document.getElementById(`tab-${t}`).classList.add('hidden');
        document.getElementById(`tab-${t}-btn`).className = "flex-1 py-3 text-center border-b-2 border-transparent text-slate-400 hover:text-slate-200";
      });
      document.getElementById(`tab-${tab}`).classList.remove('hidden');
      document.getElementById(`tab-${tab}-btn`).className = "flex-1 py-3 text-center border-b-2 border-teal-400 text-teal-300";

      if (tab === 'timeline') loadTimeline();
    }

    function renderWeekCalendar() {
      const weekTitle = getWeekInfo(currentDate);
      document.getElementById('header-week-title').innerText = weekTitle;
      document.getElementById('calendar-title').innerText = weekTitle;

      const container = document.getElementById('week-days-container');
      container.innerHTML = '';

      const dayOfWeek = currentDate.getDay(); // 0(일) ~ 6(토)
      const mondayOffset = (dayOfWeek === 0 ? -6 : 1) - dayOfWeek;
      const monday = new Date(currentDate);
      monday.setDate(currentDate.getDate() + mondayOffset);

      const dayNames = ['월', '화', '수', '목', '금', '토', '일'];

      for (let i = 0; i < 7; i++) {
        const target = new Date(monday);
        target.setDate(monday.getDate() + i);
        const targetStr = formatDate(target);
        const isSelected = targetStr === selectedDateStr;

        const col = document.createElement('div');
        col.className = `flex flex-col items-center py-1.5 rounded-xl cursor-pointer transition ${isSelected ? 'bg-teal-400 text-black font-bold' : 'hover:bg-slate-800 text-slate-300'}`;
        col.onclick = () => {
          selectedDateStr = targetStr;
          currentDate = new Date(target);
          renderWeekCalendar();
          loadPlans();
        };

        col.innerHTML = `
          <span class="text-[10px] ${isSelected ? 'text-black' : (i >= 5 ? 'text-rose-400' : 'text-slate-400')}">${dayNames[i]}</span>
          <span class="text-sm mt-0.5">${target.getDate()}</span>
        `;
        container.appendChild(col);
      }
    }

    function changeWeek(direction) {
      currentDate.setDate(currentDate.getDate() + (direction * 7));
      selectedDateStr = formatDate(currentDate);
      renderWeekCalendar();
      loadPlans();
    }

    async function loadCategories() {
      const res = await fetch('/api/categories');
      categories = await res.json();
      renderCategoryBar();
    }

    function renderCategoryBar() {
      const bar = document.getElementById('category-bar');
      bar.innerHTML = '';

      categories.forEach(cat => {
        const isSel = cat === selectedCategory;
        const btn = document.createElement('button');
        btn.type = 'button';
        btn.onclick = () => { selectedCategory = cat; renderCategoryBar(); };
        btn.className = `px-3 py-1 rounded-full text-xs font-semibold whitespace-nowrap transition ${isSel ? 'bg-teal-400 text-black' : 'bg-[#202738] text-slate-300 hover:bg-[#283247]'}`;
        btn.innerText = cat;
        bar.appendChild(btn);
      });

      const addBtn = document.createElement('button');
      addBtn.type = 'button';
      addBtn.onclick = promptAddCategory;
      addBtn.className = "px-2.5 py-1 rounded-full text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-teal-300 border border-slate-700 whitespace-nowrap";
      addBtn.innerText = "+ 카테고리";
      bar.appendChild(addBtn);
    }

    async function promptAddCategory() {
      const newCat = prompt("새로 추가할 카테고리 이름을 입력하세요:");
      if (newCat && newCat.trim()) {
        await fetch('/api/categories', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ name: newCat.trim() })
        });
        selectedCategory = newCat.trim();
        loadCategories();
      }
    }

    async function loadPlans() {
      const res = await fetch(`/api/plans?date=${selectedDateStr}`);
      const plans = await res.json();
      const list = document.getElementById('plan-list');
      list.innerHTML = '';

      if (plans.length === 0) {
        list.innerHTML = `<div class="text-center py-10 text-xs text-slate-500">등록된 계획이 없습니다. 아래에서 계획을 추가해보세요!</div>`;
        return;
      }

      plans.forEach(item => {
        const itemEl = document.createElement('div');
        itemEl.className = "flex items-center justify-between p-3.5 bg-[#171c28] hover:bg-[#1d2333] rounded-xl transition cursor-pointer select-none border border-slate-800/50";
        itemEl.onclick = () => togglePlan(item.id);

        const checkClass = item.is_done ? "bg-teal-400 text-black border-teal-400" : "border-slate-600 text-transparent";
        // 취소선(line-through) 및 흐린 회색(text-slate-500) 제거 -> 선명한 텍스트 유지
        const textClass = "text-slate-100 font-semibold";

        itemEl.innerHTML = `
          <div class="flex items-center space-x-3">
            <div class="w-6 h-6 rounded-lg border-2 flex items-center justify-center font-bold text-xs transition ${checkClass}">
              ✔
            </div>
            <div>
              <span class="text-[10px] text-teal-400/80 font-medium block">[${item.category}]</span>
              <span class="text-sm ${textClass}">${item.title}</span>
            </div>
          </div>
          ${item.is_done && item.completed_at ? `<span class="text-xs font-mono text-teal-300 bg-teal-950/80 border border-teal-700/50 px-2 py-0.5 rounded-md font-bold">${item.completed_at} 완료</span>` : ''}
        `;
        list.appendChild(itemEl);
      });
    }

    async function addPlan(e) {
      e.preventDefault();
      const input = document.getElementById('plan-input');
      await fetch('/api/plans', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ category: selectedCategory, title: input.value, plan_date: selectedDateStr })
      });
      input.value = '';
      loadPlans();
    }

    async function togglePlan(id) {
      await fetch('/api/plans/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
      });
      loadPlans();
    }

    async function loadTimeline() {
      document.getElementById('timeline-date-label').innerText = selectedDateStr;
      const res = await fetch(`/api/timeline?date=${selectedDateStr}`);
      const data = await res.json();
      const list = document.getElementById('timeline-list');
      list.innerHTML = '';

      if (data.length === 0) {
        list.innerHTML = `<div class="text-center py-10 text-xs text-slate-500">아직 완료된 실행 항목이 없습니다.<br>계획을 실행하고 체크해 보세요!</div>`;
        return;
      }

      data.forEach(item => {
        const el = document.createElement('div');
        el.className = "flex items-center space-x-3 p-3 bg-[#171c28] rounded-xl border border-teal-900/40";
        el.innerHTML = `
          <span class="text-xs font-mono font-bold text-teal-300 bg-teal-950 px-2 py-1 rounded border border-teal-700/40">
            ${item.completed_at}
          </span>
          <div class="flex-1">
            <span class="text-[10px] text-slate-400 block">${item.category}</span>
            <span class="text-sm font-semibold text-slate-100">${item.title}</span>
          </div>
          <span class="text-xs text-teal-400 font-bold">✔ 완료</span>
        `;
        list.appendChild(el);
      });
    }

    async function requestAIFeedback() {
      const card = document.getElementById('ai-card');
      const text = document.getElementById('ai-text');
      card.classList.remove('hidden');
      text.innerText = "Gemini가 한국시간 기준 계획 및 실행 기록을 분석 중입니다...";

      const res = await fetch('/api/ai/analyze', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ plan_date: selectedDateStr })
      });
      const data = await res.json();
      text.innerText = data.feedback;
    }

    // 초기화 실행
    renderWeekCalendar();
    loadCategories();
    loadPlans();
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(content=HTML_LAYOUT)

@app.get("/api/categories")
def get_categories():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT name FROM categories ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()
    return [r[0] for r in rows]

@app.post("/api/categories")
def add_category(cat: CategoryCreate):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT OR IGNORE INTO categories (name) VALUES (?)", (cat.name,))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.get("/api/plans")
def get_plans(date: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, category, title, is_done, completed_at FROM plans WHERE plan_date = ? ORDER BY id ASC", (date,))
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "category": r[1], "title": r[2], "is_done": bool(r[3]), "completed_at": r[4]}
        for r in rows
    ]

@app.post("/api/plans")
def add_plan(plan: PlanCreate):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO plans (category, title, is_done, completed_at, plan_date) VALUES (?, ?, 0, NULL, ?)",
        (plan.category, plan.title, plan.plan_date)
    )
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/api/plans/toggle")
def toggle_plan(payload: PlanToggle):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT is_done FROM plans WHERE id = ?", (payload.id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {"error": "Not found"}

    now_done = 0 if row[0] == 1 else 1
    # 한국 표준시(KST)로 정확한 시:분 기록
    completed_at = get_now_kst().strftime("%H:%M") if now_done == 1 else None

    cur.execute("UPDATE plans SET is_done = ?, completed_at = ? WHERE id = ?", (now_done, completed_at, payload.id))
    conn.commit()
    conn.close()
    return {"is_done": bool(now_done), "completed_at": completed_at}

@app.get("/api/timeline")
def get_timeline(date: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, category, title, completed_at FROM plans WHERE plan_date = ? AND is_done = 1 ORDER BY completed_at ASC",
        (date,)
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "category": r[1], "title": r[2], "completed_at": r[3]}
        for r in rows
    ]

@app.post("/api/ai/analyze")
def analyze_plans(req: AIRequest):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT category, title, is_done, completed_at FROM plans WHERE plan_date = ?", (req.plan_date,))
    items = cur.fetchall()
    conn.close()

    if not items:
        return {"feedback": f"{req.plan_date} 날짜에 등록된 계획 데이터가 없습니다. 먼저 할 일을 등록해 보세요!"}

    records = [
        f"[{r[0]}] {r[1]} -> {'완료 (한국시각 ' + r[3] + ')' if r[2] else '미완료'}"
        for r in items
    ]
    record_text = "\n".join(records)

    prompt = f"""
    당신은 스마트 계획 & 실행 코치입니다. 사용자의 {req.plan_date} 계획 및 실제 실행 기록입니다:
    {record_text}

    다음 사항을 포함하여 4문장 내외로 격려와 실천 중심의 스마트 피드백을 제공해 주세요:
    1. 오늘 계획의 달성률과 시간대별 실행 집중도 평가
    2. 미완료 항목 또는 딜레이된 시간에 대한 분석
    3. 내일 더 높은 실행력을 내기 위한 스마트 팁 1가지
    """

    if not ai_client:
        return {"feedback": "GEMINI_API_KEY가 등록되지 않았습니다."}

    try:
        response = ai_client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return {"feedback": response.text}
    except Exception as e:
        return {"feedback": f"AI 분석 오류: {str(e)}"}

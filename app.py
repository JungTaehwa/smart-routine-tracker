import os
import sqlite3
import hashlib
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
from google import genai

app = FastAPI()

# 한국 표준시 (KST = UTC+9)
KST = timezone(timedelta(hours=9))

def get_now_kst():
    return datetime.now(KST)

GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
ai_client = genai.Client(api_key=GEMINI_KEY) if GEMINI_KEY else None

DB_PATH = "routines.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # 1. 회원 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )
    """)
    # 2. 계획 및 실행 기록 테이블 (알람 및 순서 컬럼)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            title TEXT,
            is_done INTEGER DEFAULT 0,
            completed_at TEXT,
            plan_date TEXT,
            display_order INTEGER DEFAULT 0,
            alarm_time TEXT,
            snooze_count INTEGER DEFAULT 0
        )
    """)
    # 컬럼 누락 방지 마이그레이션
    for col_def in [
        ("display_order", "INTEGER DEFAULT 0"),
        ("alarm_time", "TEXT"),
        ("snooze_count", "INTEGER DEFAULT 0")
    ]:
        try:
            cur.execute(f"ALTER TABLE plans ADD COLUMN {col_def[0]} {col_def[1]}")
        except sqlite3.OperationalError:
            pass

    # 3. 자주 쓰는 항목 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS presets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            title TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

def hash_pw(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

# Pydantic 모델
class UserAuth(BaseModel):
    username: str
    password: str

class PlanCreate(BaseModel):
    username: str
    title: str
    plan_date: str
    alarm_time: Optional[str] = None  # 예: "14:30" 또는 "2026-10-11 14:30"

class PlanToggle(BaseModel):
    id: int

class PlanSnooze(BaseModel):
    id: int
    username: str
    minutes: int

class PlanReorder(BaseModel):
    username: str
    ordered_ids: List[int]

class PlanCopyRequest(BaseModel):
    username: str
    from_date: str
    to_date: str

class PresetCreate(BaseModel):
    username: str
    title: str

class AIRequest(BaseModel):
    username: str
    plan_date: str

HTML_LAYOUT = """
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>스마트 계획 & 알람 트래커</title>
  
  <link rel="manifest" href="/manifest.json">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
  <meta name="theme-color" content="#121620">

  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.2/Sortable.min.js"></script>

  <style>
    body.theme-dark {
      --bg-body: #0b0f17;
      --bg-card: #121620;
      --bg-header: #161b26;
      --bg-item: #171c28;
      --bg-hover: #1e2433;
      --border: #1e2330;
      --text-main: #f1f5f9;
      --text-sub: #94a3b8;
      --primary: #2dd4bf;
      --primary-text: #000000;
      --chip-bg: #1c2436;
      --chip-border: #28354f;
    }
    body.theme-pomeranian {
      --bg-body: #f5ede2;
      --bg-card: #fdfbf7;
      --bg-header: #faeee0;
      --bg-item: #ffffff;
      --bg-hover: #fcf4eb;
      --border: #ebdcc9;
      --text-main: #43302b;
      --text-sub: #8c7365;
      --primary: #d97706;
      --primary-text: #ffffff;
      --chip-bg: #f5e8d7;
      --chip-border: #e3cdb4;
    }
    body { background-color: var(--bg-body); color: var(--text-main); }
    .custom-card { background-color: var(--bg-card); border-color: var(--border); }
    .custom-header { background-color: var(--bg-header); border-color: var(--border); }
    .custom-item { background-color: var(--bg-item); border-color: var(--border); }
    .custom-border { border-color: var(--border); }
    .text-sub { color: var(--text-sub); }
  </style>
</head>
<body class="theme-dark flex justify-center min-h-screen font-sans">
  <div class="custom-card w-full max-w-md border-x flex flex-col min-h-screen shadow-2xl relative">
    
    <!-- 상단 헤더 -->
    <header class="custom-header p-4 border-b flex justify-between items-center">
      <div>
        <div class="flex items-center space-x-1.5">
          <span id="theme-logo-icon">⚡</span>
          <h1 class="text-base font-bold tracking-tight">스마트 계획 & 알람</h1>
        </div>
        <p class="text-xs text-sub mt-0.5" id="header-week-title"></p>
      </div>
      <div class="flex items-center space-x-2">
        <button onclick="toggleTheme()" id="theme-toggle-btn" class="text-xs px-2 py-1 rounded-full font-bold border custom-border bg-black/10">🐾 포메테마</button>
        <span id="user-display" class="text-xs font-semibold"></span>
        <button onclick="logout()" class="text-[11px] text-sub hover:text-rose-400">로그아웃</button>
        <button onclick="requestAIFeedback()" class="bg-indigo-600 hover:bg-indigo-500 text-white text-xs px-2.5 py-1.5 rounded-full font-semibold shadow-md">✨ AI</button>
      </div>
    </header>

    <!-- 탭 네비게이션 -->
    <nav class="custom-header flex border-b text-xs font-semibold">
      <button id="tab-plan-btn" onclick="switchTab('plan')" class="flex-1 py-3 text-center border-b-2 font-bold" style="border-color: var(--primary); color: var(--primary);">📋 계획 & 실행</button>
      <button id="tab-timeline-btn" onclick="switchTab('timeline')" class="flex-1 py-3 text-center border-b-2 border-transparent text-sub">⏱️ 타임라인</button>
      <button id="tab-widget-btn" onclick="switchTab('widget')" class="flex-1 py-3 text-center border-b-2 border-transparent text-sub">📱 홈위젯 가이드</button>
    </nav>

    <!-- [탭 1] 계획 & 실행 뷰 -->
    <div id="tab-plan" class="flex-1 flex flex-col">
      <!-- 캘린더 네비게이터 -->
      <div class="custom-header p-3 border-b">
        <div class="flex justify-between items-center mb-2 px-1">
          <button onclick="changeWeek(-1)" class="text-sub px-2 py-0.5 rounded text-xs bg-black/10">◀ 이전 주</button>
          <span id="calendar-title" class="text-xs font-bold"></span>
          <button onclick="changeWeek(1)" class="text-sub px-2 py-0.5 rounded text-xs bg-black/10">다음 주 ▶</button>
        </div>
        <div class="grid grid-cols-7 gap-1 text-center" id="week-days-container"></div>
      </div>

      <!-- 복사 툴바 -->
      <div class="custom-header flex items-center justify-between px-3 py-2 border-b text-[11px]">
        <span class="text-sub font-medium">📋 빠른 계획 복사</span>
        <div class="flex space-x-1.5">
          <button onclick="copyFromYesterday()" class="custom-item px-2 py-1 rounded-lg border text-xs">⬅️ 어제 계획 가져오기</button>
          <button onclick="copyToTomorrow()" class="px-2 py-1 rounded-lg border text-xs font-bold" style="background-color: var(--primary); color: var(--primary-text); border-color: var(--primary);">내일로 복사 ➡️</button>
        </div>
      </div>

      <!-- AI 분석 피드백 카드 -->
      <div id="ai-card" class="mx-3 mt-2.5 p-3.5 rounded-xl border border-indigo-500/30 bg-indigo-950/20 hidden">
        <div class="flex items-center space-x-2 text-indigo-400 font-bold text-xs mb-1.5">
          <span>🤖</span> <span>Gemini 실행 분석</span>
        </div>
        <p id="ai-text" class="text-xs leading-relaxed text-sub whitespace-pre-line"></p>
      </div>

      <!-- 자주 쓰는 항목 -->
      <div class="px-3 pt-3 pb-1">
        <div class="flex items-center justify-between mb-1.5">
          <span class="text-[11px] font-bold" style="color: var(--primary);">⭐ 자주 쓰는 항목</span>
          <button onclick="promptAddPreset()" class="text-[10px] text-sub underline">+ 항목 추가</button>
        </div>
        <div class="flex items-center space-x-1.5 overflow-x-auto pb-2 scrollbar-none" id="preset-bar"></div>
      </div>

      <!-- 입력 폼: 계획명 + 알람 시간 설정 -->
      <div class="px-3 pb-2">
        <form onsubmit="addPlan(event)" class="flex flex-col gap-2 mt-1">
          <div class="flex gap-2">
            <input type="text" id="plan-input" placeholder="실행할 계획 입력..." required
              autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false"
              class="custom-item custom-border flex-1 border rounded-xl px-3.5 py-2.5 text-sm focus:outline-none placeholder:text-slate-500">
            <button type="submit" class="font-bold px-4 rounded-xl text-base shadow-sm" style="background-color: var(--primary); color: var(--primary-text);">+</button>
          </div>
          <div class="flex items-center space-x-2 text-xs text-sub pl-1">
            <span>⏰ 알람 시간 (선택):</span>
            <input type="time" id="plan-alarm-time" class="custom-item custom-border border rounded-lg px-2 py-1 text-xs focus:outline-none">
            <span class="text-[10px] opacity-70">도래 시 사운드/미루기 알람 팝업</span>
          </div>
        </form>
      </div>

      <!-- 계획 목록 (드래그앤드롭 지원) -->
      <div class="flex-1 px-3 pb-4 space-y-2 overflow-y-auto" id="plan-list"></div>
    </div>

    <!-- [탭 2] 타임라인 뷰 -->
    <div id="tab-timeline" class="flex-1 flex flex-col p-4 hidden">
      <div class="flex justify-between items-center mb-4">
        <h2 class="text-sm font-bold" style="color: var(--primary);">⏱️ 오늘 실행 타임라인</h2>
        <span class="text-xs text-sub font-mono" id="timeline-date-label"></span>
      </div>
      <div class="flex-1 overflow-y-auto space-y-2.5" id="timeline-list"></div>
    </div>

    <!-- [탭 3] 스마트폰 홈 위젯 가이드 -->
    <div id="tab-widget" class="flex-1 flex flex-col p-4 overflow-y-auto hidden space-y-3">
      <div class="border-b custom-border pb-2">
        <h2 class="text-sm font-bold" style="color: var(--primary);">📱 휴대폰 바탕화면 위젯 연동 안내</h2>
        <p class="text-xs text-sub mt-0.5">웹페이지를 켜지 않고 바탕화면에서 바로 체크하기</p>
      </div>
      <div class="custom-item p-3.5 rounded-xl border space-y-2 text-xs leading-relaxed">
        <p class="font-bold text-teal-400">💡 3분 만에 위젯 만드는 법 (HTTP Shortcuts 앱)</p>
        <p>1. 구글 플레이스토어에서 무료 앱 <strong>HTTP Shortcuts</strong>를 설치합니다.</p>
        <p>2. 새 단축키를 만들고 아래 전용 위젯 주소를 입력합니다:</p>
        <div class="p-2 bg-black/30 rounded font-mono text-[11px] text-teal-300 break-all" id="widget-api-url"></div>
        <p>3. 휴대폰 바탕화면을 길게 누르고 <strong>위젯 추가 ➔ HTTP Shortcuts</strong>를 선택하면, 앱을 열지 않고도 남은 할 일과 원클릭 체크가 바탕화면에 바로 나타납니다.</p>
      </div>
    </div>

    <!-- [알람 울림 팝업 모달: 완료 or 미루기] -->
    <div id="alarm-ring-modal" class="fixed inset-0 bg-black/85 flex items-center justify-center p-4 z-50 hidden">
      <div class="custom-card border p-6 rounded-2xl w-full max-w-xs shadow-2xl text-center">
        <div class="text-3xl mb-2 animate-bounce">⏰</div>
        <h3 class="text-base font-bold mb-1" style="color: var(--primary);" id="alarm-ring-title">계획 알람</h3>
        <p class="text-xs text-sub mb-4" id="alarm-ring-time"></p>
        
        <div class="space-y-2">
          <!-- 완료 처리 -->
          <button onclick="handleAlarmComplete()" class="w-full py-2.5 rounded-xl font-bold text-xs shadow-md transition" style="background-color: var(--primary); color: var(--primary-text);">
            ✔ 지금 완료했습니다!
          </button>
          
          <div class="border-t custom-border pt-2 text-[11px] text-sub">미루기 시간 선택</div>
          <div class="grid grid-cols-3 gap-1.5">
            <button onclick="handleAlarmSnooze(5)" class="custom-item border py-1.5 rounded-lg text-xs hover:border-teal-400">5분</button>
            <button onclick="handleAlarmSnooze(10)" class="custom-item border py-1.5 rounded-lg text-xs hover:border-teal-400">10분</button>
            <button onclick="handleAlarmSnooze(30)" class="custom-item border py-1.5 rounded-lg text-xs hover:border-teal-400">30분</button>
          </div>
          <button onclick="handleAlarmSnoozeCustom()" class="w-full custom-item border py-1.5 rounded-lg text-xs text-sub">직접 분 입력 미루기</button>
        </div>
      </div>
    </div>

    <!-- 로그인 모달 -->
    <div id="auth-modal" class="fixed inset-0 bg-black/85 flex items-center justify-center p-4 z-50 hidden">
      <div class="custom-card border p-6 rounded-2xl w-full max-w-xs shadow-2xl">
        <h3 class="text-base font-bold text-center mb-4" id="auth-title" style="color: var(--primary);">로그인</h3>
        <input type="text" id="auth-id" placeholder="아이디" autocomplete="off" class="custom-item custom-border w-full border rounded-lg p-2.5 text-xs mb-2 focus:outline-none">
        <input type="password" id="auth-pw" placeholder="비밀번호" class="custom-item custom-border w-full border rounded-lg p-2.5 text-xs mb-4 focus:outline-none">
        <button onclick="submitAuth()" class="w-full font-bold py-2 rounded-lg text-xs mb-2" style="background-color: var(--primary); color: var(--primary-text);" id="auth-submit-btn">로그인</button>
        <p class="text-center text-[11px] text-sub">
          <span id="auth-switch-text">계정이 없으신가요?</span>
          <button onclick="toggleAuthMode()" class="underline ml-1 font-bold" id="auth-switch-btn" style="color: var(--primary);">회원가입</button>
        </p>
      </div>
    </div>

  </div>

  <script>
    let currentUser = localStorage.getItem('routine_user') || '';
    let currentTheme = localStorage.getItem('routine_theme') || 'theme-dark';
    let isSignUpMode = false;

    let currentDate = new Date();
    let selectedDateStr = formatDate(currentDate);
    let presets = [];
    let sortableInstance = null;
    let currentRingingPlan = null;
    let triggeredAlarms = new Set();

    // 웹 오디오 신시사이저로 청량한 알람 비프음 재생
    function playAlarmSound() {
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, ctx.currentTime);
        osc.frequency.setValueAtTime(1200, ctx.currentTime + 0.15);
        gain.gain.setValueAtTime(0.3, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.6);
        osc.start();
        osc.stop(ctx.currentTime + 0.6);
      } catch(e) {}
    }

    function applyTheme(theme) {
      document.body.className = `${theme} flex justify-center min-h-screen font-sans`;
      const btn = document.getElementById('theme-toggle-btn');
      const logo = document.getElementById('theme-logo-icon');
      if (theme === 'theme-pomeranian') {
        btn.innerText = "🌙 다크모드";
        logo.innerText = "🐾";
      } else {
        btn.innerText = "🐾 포메테마";
        logo.innerText = "⚡";
      }
      localStorage.setItem('routine_theme', theme);
    }

    function toggleTheme() {
      currentTheme = currentTheme === 'theme-dark' ? 'theme-pomeranian' : 'theme-dark';
      applyTheme(currentTheme);
      renderWeekCalendar();
    }

    function formatDate(d) {
      const year = d.getFullYear();
      const month = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${year}-${month}-${day}`;
    }

    function checkAuth() {
      if (!currentUser) {
        document.getElementById('auth-modal').classList.remove('hidden');
      } else {
        document.getElementById('auth-modal').classList.add('hidden');
        document.getElementById('user-display').innerText = `👤 ${currentUser}`;
        document.getElementById('widget-api-url').innerText = window.location.origin + `/api/widget/summary?username=${currentUser}`;
        if (Notification.permission !== "granted") {
          Notification.requestPermission();
        }
        loadPresets();
        loadPlans();
      }
    }

    function toggleAuthMode() {
      isSignUpMode = !isSignUpMode;
      document.getElementById('auth-title').innerText = isSignUpMode ? '회원가입' : '로그인';
      document.getElementById('auth-submit-btn').innerText = isSignUpMode ? '가입하기' : '로그인';
      document.getElementById('auth-switch-text').innerText = isSignUpMode ? '이미 계정이 있나요?' : '계정이 없으신가요?';
      document.getElementById('auth-switch-btn').innerText = isSignUpMode ? '로그인' : '회원가입';
    }

    async function submitAuth() {
      const u = document.getElementById('auth-id').value.trim();
      const p = document.getElementById('auth-pw').value.trim();
      if (!u || !p) return alert('아이디와 비밀번호를 입력해주세요.');

      const ep = isSignUpMode ? '/api/signup' : '/api/login';
      const res = await fetch(ep, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: u, password: p })
      });
      const data = await res.json();
      if (data.error) return alert(data.error);

      currentUser = u;
      localStorage.setItem('routine_user', u);
      checkAuth();
    }

    function logout() {
      localStorage.removeItem('routine_user');
      location.reload();
    }

    function getWeekInfo(date) {
      const month = date.getMonth() + 1;
      const firstDayOfMonth = new Date(date.getFullYear(), date.getMonth(), 1);
      const monthWeek = Math.ceil((date.getDate() + firstDayOfMonth.getDay()) / 7);
      return `${date.getFullYear()}년 ${month}월 ${monthWeek}주차`;
    }

    function switchTab(tab) {
      ['plan', 'timeline', 'widget'].forEach(t => {
        document.getElementById(`tab-${t}`).classList.add('hidden');
        const btn = document.getElementById(`tab-${t}-btn`);
        btn.style.borderColor = 'transparent';
        btn.style.color = 'var(--text-sub)';
      });
      document.getElementById(`tab-${tab}`).classList.remove('hidden');
      const activeBtn = document.getElementById(`tab-${tab}-btn`);
      activeBtn.style.borderColor = 'var(--primary)';
      activeBtn.style.color = 'var(--primary)';

      if (tab === 'timeline') loadTimeline();
    }

    function renderWeekCalendar() {
      const weekTitle = getWeekInfo(currentDate);
      document.getElementById('header-week-title').innerText = weekTitle;
      document.getElementById('calendar-title').innerText = weekTitle;

      const container = document.getElementById('week-days-container');
      container.innerHTML = '';

      const dayOfWeek = currentDate.getDay();
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
        col.className = `flex flex-col items-center py-1.5 rounded-xl cursor-pointer transition ${isSelected ? 'font-bold shadow-sm' : 'hover:opacity-80 text-sub'}`;
        if (isSelected) {
          col.style.backgroundColor = 'var(--primary)';
          col.style.color = 'var(--primary-text)';
        }

        col.onclick = () => {
          selectedDateStr = targetStr;
          currentDate = new Date(target);
          renderWeekCalendar();
          loadPlans();
        };

        col.innerHTML = `
          <span class="text-[10px] ${isSelected ? '' : (i >= 5 ? 'text-rose-400' : 'text-sub')}">${dayNames[i]}</span>
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

    // --- 자주 쓰는 항목(프리셋) ---
    async function loadPresets() {
      if (!currentUser) return;
      const res = await fetch(`/api/presets?username=${currentUser}`);
      presets = await res.json();
      const bar = document.getElementById('preset-bar');
      bar.innerHTML = '';

      presets.forEach(p => {
        const chip = document.createElement('div');
        chip.className = "flex items-center space-x-1 border text-xs px-2.5 py-1 rounded-full whitespace-nowrap cursor-pointer";
        chip.style.backgroundColor = "var(--chip-bg)";
        chip.style.borderColor = "var(--chip-border)";
        chip.innerHTML = `
          <span onclick="applyPreset('${p.title}')">+ ${p.title}</span>
          <span onclick="deletePreset(${p.id}, event)" class="text-sub hover:text-rose-400 text-xs ml-1">×</span>
        `;
        bar.appendChild(chip);
      });
    }

    async function applyPreset(title) {
      await fetch('/api/plans', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ username: currentUser, title: title, plan_date: selectedDateStr })
      });
      loadPlans();
    }

    async function promptAddPreset() {
      const title = prompt("자주 사용할 계획 이름을 입력하세요:");
      if (title && title.trim()) {
        await fetch('/api/presets', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ username: currentUser, title: title.trim() })
        });
        loadPresets();
      }
    }

    async function deletePreset(id, e) {
      e.stopPropagation();
      if (!confirm("이 항목을 삭제하시겠습니까?")) return;
      await fetch(`/api/presets/${id}?username=${currentUser}`, { method: 'DELETE' });
      loadPresets();
    }

    // --- 계획 목록 & 알람 감시 ---
    async function loadPlans() {
      if (!currentUser) return;
      const res = await fetch(`/api/plans?date=${selectedDateStr}&username=${currentUser}`);
      const plans = await res.json();
      const list = document.getElementById('plan-list');
      list.innerHTML = '';

      if (plans.length === 0) {
        list.innerHTML = `<div class="text-center py-10 text-xs text-sub">등록된 계획이 없습니다.</div>`;
        return;
      }

      plans.forEach(item => {
        const itemEl = document.createElement('div');
        itemEl.dataset.id = item.id;
        itemEl.className = "custom-item custom-border flex items-center justify-between p-3 rounded-xl border select-none shadow-sm";

        const checkBg = item.is_done ? 'background-color: var(--primary); border-color: var(--primary); color: var(--primary-text);' : 'border-color: var(--border); color: transparent;';

        itemEl.innerHTML = `
          <div class="flex items-center space-x-3 flex-1 cursor-pointer" onclick="togglePlan(${item.id})">
            <div class="w-6 h-6 rounded-lg border-2 flex items-center justify-center font-bold text-xs" style="${checkBg}">✔</div>
            <div>
              <span class="text-sm font-semibold">${item.title}</span>
              ${item.alarm_time && !item.is_done ? `<span class="block text-[10px] text-amber-400 mt-0.5">⏰ ${item.alarm_time} 알람 (${item.snooze_count ? item.snooze_count + '회 미룸' : '예정'})</span>` : ''}
            </div>
          </div>
          <div class="flex items-center space-x-2">
            ${item.is_done && item.completed_at ? `<span class="text-xs font-mono font-bold px-2 py-0.5 rounded-md border" style="color: var(--primary); border-color: var(--primary);">${item.completed_at} 완료</span>` : ''}
            <span class="drag-handle cursor-grab p-1 text-sub text-base">⋮⋮</span>
          </div>
        `;
        list.appendChild(itemEl);
      });

      if (sortableInstance) sortableInstance.destroy();
      sortableInstance = new Sortable(list, {
        handle: '.drag-handle',
        animation: 150,
        onEnd: async function () {
          const orderedIds = Array.from(list.children).map(el => parseInt(el.dataset.id)).filter(id => !isNaN(id));
          await fetch('/api/plans/reorder', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ username: currentUser, ordered_ids: orderedIds })
          });
        }
      });

      checkAlarms(plans);
    }

    async function addPlan(e) {
      e.preventDefault();
      const input = document.getElementById('plan-input');
      const alarmInput = document.getElementById('plan-alarm-time');
      await fetch('/api/plans', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          username: currentUser,
          title: input.value,
          plan_date: selectedDateStr,
          alarm_time: alarmInput.value || null
        })
      });
      input.value = '';
      alarmInput.value = '';
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

    // --- 실시간 알람 트리거 및 미루기 처리 ---
    function checkAlarms(plans) {
      const now = new Date();
      const curTimeStr = String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
      const todayStr = formatDate(now);

      if (selectedDateStr !== todayStr) return;

      plans.forEach(p => {
        if (!p.is_done && p.alarm_time && p.alarm_time === curTimeStr) {
          const alarmKey = `${p.id}-${p.alarm_time}`;
          if (!triggeredAlarms.has(alarmKey)) {
            triggeredAlarms.add(alarmKey);
            triggerAlarmModal(p);
          }
        }
      });
    }

    // 10초마다 알람 감시
    setInterval(() => {
      if (currentUser) loadPlans();
    }, 10000);

    function triggerAlarmModal(plan) {
      currentRingingPlan = plan;
      playAlarmSound();

      if (Notification.permission === "granted") {
        new Notification("⏰ 계획 알람!", {
          body: `[${plan.title}] 실행할 시간입니다!`,
          icon: "https://cdn-icons-png.flaticon.com/512/906/906334.png"
        });
      }

      document.getElementById('alarm-ring-title').innerText = plan.title;
      document.getElementById('alarm-ring-time').innerText = `설정 시각: ${plan.alarm_time} (지금 실행하세요!)`;
      document.getElementById('alarm-ring-modal').classList.remove('hidden');
    }

    async function handleAlarmComplete() {
      if (!currentRingingPlan) return;
      await togglePlan(currentRingingPlan.id);
      document.getElementById('alarm-ring-modal').classList.add('hidden');
      currentRingingPlan = null;
    }

    async function handleAlarmSnooze(minutes) {
      if (!currentRingingPlan) return;
      await fetch('/api/plans/snooze', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id: currentRingingPlan.id, username: currentUser, minutes: minutes })
      });
      document.getElementById('alarm-ring-modal').classList.add('hidden');
      currentRingingPlan = null;
      loadPlans();
    }

    function handleAlarmSnoozeCustom() {
      const min = prompt("몇 분 뒤로 미루시겠습니까? (숫자만 입력):", "15");
      if (min && !isNaN(min)) {
        handleAlarmSnooze(parseInt(min));
      }
    }

    // --- 복사 & 타임라인 & AI ---
    async function copyToTomorrow() {
      const cur = new Date(selectedDateStr);
      cur.setDate(cur.getDate() + 1);
      const tomorrowStr = formatDate(cur);
      if (!confirm(`[${selectedDateStr}] 계획들을 내일로 복사하시겠습니까?`)) return;
      const res = await fetch('/api/plans/copy', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ username: currentUser, from_date: selectedDateStr, to_date: tomorrowStr })
      });
      const data = await res.json();
      alert(data.message);
    }

    async function copyFromYesterday() {
      const cur = new Date(selectedDateStr);
      cur.setDate(cur.getDate() - 1);
      const yesterdayStr = formatDate(cur);
      if (!confirm(`어제 계획들을 가져오시겠습니까?`)) return;
      const res = await fetch('/api/plans/copy', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ username: currentUser, from_date: yesterdayStr, to_date: selectedDateStr })
      });
      const data = await res.json();
      alert(data.message);
      loadPlans();
    }

    async function loadTimeline() {
      if (!currentUser) return;
      document.getElementById('timeline-date-label').innerText = selectedDateStr;
      const res = await fetch(`/api/timeline?date=${selectedDateStr}&username=${currentUser}`);
      const data = await res.json();
      const list = document.getElementById('timeline-list');
      list.innerHTML = '';
      if (data.length === 0) {
        list.innerHTML = `<div class="text-center py-10 text-xs text-sub">완료된 계획이 없습니다.</div>`;
        return;
      }
      data.forEach(item => {
        const el = document.createElement('div');
        el.className = "custom-item custom-border flex items-center space-x-3 p-3 rounded-xl border";
        el.innerHTML = `
          <span class="text-xs font-mono font-bold px-2 py-1 rounded border" style="color: var(--primary); border-color: var(--primary);">${item.completed_at}</span>
          <div class="flex-1"><span class="text-sm font-semibold">${item.title}</span></div>
          <span class="text-xs font-bold" style="color: var(--primary);">✔ 완료</span>
        `;
        list.appendChild(el);
      });
    }

    async function requestAIFeedback() {
      if (!currentUser) return;
      const card = document.getElementById('ai-card');
      const text = document.getElementById('ai-text');
      card.classList.remove('hidden');
      text.innerText = "Gemini가 분석 중입니다...";
      const res = await fetch('/api/ai/analyze', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ username: currentUser, plan_date: selectedDateStr })
      });
      const data = await res.json();
      text.innerText = data.feedback;
    }

    applyTheme(currentTheme);
    renderWeekCalendar();
    checkAuth();
  </script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(content=HTML_LAYOUT)

@app.get("/manifest.json")
def get_manifest():
    return {
        "name": "스마트 계획 & 알람 트래커",
        "short_name": "루틴트래커",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#121620",
        "theme_color": "#121620",
        "icons": [{"src": "https://cdn-icons-png.flaticon.com/512/906/906334.png", "sizes": "512x512", "type": "image/png"}]
    }

# ----------------- 인증 API -----------------
@app.post("/api/signup")
def signup(data: UserAuth):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users (username, password) VALUES (?, ?)", (data.username, hash_pw(data.password)))
        default_presets = ["0650 기상", "콩산책", "푸쉬업, 아령", "몸무게 측정", "하루 계획 점검"]
        for title in default_presets:
            cur.execute("INSERT INTO presets (username, title) VALUES (?, ?)", (data.username, title))
        conn.commit()
        return {"status": "success", "username": data.username}
    except sqlite3.IntegrityError:
        return {"error": "이미 존재하는 아이디입니다."}
    finally:
        conn.close()

@app.post("/api/login")
def login(data: UserAuth):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT password FROM users WHERE username = ?", (data.username,))
    row = cur.fetchone()
    conn.close()
    if row and row[0] == hash_pw(data.password):
        return {"status": "success", "username": data.username}
    return {"error": "아이디 또는 비밀번호가 일치하지 않습니다."}

# ----------------- 자주 쓰는 항목 API -----------------
@app.get("/api/presets")
def get_presets(username: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, title FROM presets WHERE username = ? ORDER BY id ASC", (username,))
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1]} for r in rows]

@app.post("/api/presets")
def add_preset(preset: PresetCreate):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("INSERT INTO presets (username, title) VALUES (?, ?)", (preset.username, preset.title))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.delete("/api/presets/{preset_id}")
def delete_preset(preset_id: int, username: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM presets WHERE id = ? AND username = ?", (preset_id, username))
    conn.commit()
    conn.close()
    return {"status": "success"}

# ----------------- 계획 CRUD & 미루기 API -----------------
@app.get("/api/plans")
def get_plans(date: str, username: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title, is_done, completed_at, alarm_time, snooze_count FROM plans WHERE plan_date = ? AND username = ? ORDER BY display_order ASC, id ASC",
        (date, username)
    )
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "title": r[1], "is_done": bool(r[2]), "completed_at": r[3], "alarm_time": r[4], "snooze_count": r[5]}
        for r in rows
    ]

@app.post("/api/plans")
def add_plan(plan: PlanCreate):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(display_order), 0) + 1 FROM plans WHERE plan_date = ? AND username = ?", (plan.plan_date, plan.username))
    next_order = cur.fetchone()[0]

    cur.execute(
        "INSERT INTO plans (username, title, is_done, completed_at, plan_date, display_order, alarm_time, snooze_count) VALUES (?, ?, 0, NULL, ?, ?, ?, 0)",
        (plan.username, plan.title, plan.plan_date, next_order, plan.alarm_time)
    )
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/api/plans/snooze")
def snooze_plan(payload: PlanSnooze):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # 현재 KST 시각 기준 n분 뒤로 계산
    new_alarm_dt = get_now_kst() + timedelta(minutes=payload.minutes)
    new_alarm_time = new_alarm_dt.strftime("%H:%M")

    cur.execute(
        "UPDATE plans SET alarm_time = ?, snooze_count = snooze_count + 1 WHERE id = ? AND username = ?",
        (new_alarm_time, payload.id, payload.username)
    )
    conn.commit()
    conn.close()
    return {"status": "success", "new_alarm_time": new_alarm_time}

@app.post("/api/plans/reorder")
def reorder_plans(payload: PlanReorder):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    for idx, plan_id in enumerate(payload.ordered_ids):
        cur.execute("UPDATE plans SET display_order = ? WHERE id = ? AND username = ?", (idx, plan_id, payload.username))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/api/plans/copy")
def copy_plans(req: PlanCopyRequest):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT title, display_order, alarm_time FROM plans WHERE plan_date = ? AND username = ? ORDER BY display_order ASC", (req.from_date, req.username))
    items = cur.fetchall()

    if not items:
        conn.close()
        return {"message": f"{req.from_date}에 복사할 계획이 없습니다."}

    copied_count = 0
    for title, order, alarm in items:
        cur.execute(
            "INSERT INTO plans (username, title, is_done, completed_at, plan_date, display_order, alarm_time, snooze_count) VALUES (?, ?, 0, NULL, ?, ?, ?, 0)",
            (req.username, title, req.to_date, order, alarm)
        )
        copied_count += 1

    conn.commit()
    conn.close()
    return {"message": f"{copied_count}개의 계획이 복사되었습니다!"}

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
    completed_at = get_now_kst().strftime("%H:%M") if now_done == 1 else None

    cur.execute("UPDATE plans SET is_done = ?, completed_at = ? WHERE id = ?", (now_done, completed_at, payload.id))
    conn.commit()
    conn.close()
    return {"is_done": bool(now_done), "completed_at": completed_at}

@app.get("/api/timeline")
def get_timeline(date: str, username: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title, completed_at FROM plans WHERE plan_date = ? AND username = ? AND is_done = 1 ORDER BY completed_at ASC",
        (date, username)
    )
    rows = cur.fetchall()
    conn.close()
    return [{"id": r[0], "title": r[1], "completed_at": r[2]} for r in rows]

# ----------------- 📱 홈 화면 위젯 전용 API -----------------
@app.get("/api/widget/summary")
def widget_summary(username: str):
    """휴대폰 위젯에서 브라우저 없이 바로 남은 계획을 띄우고 체크하는 초경량 API"""
    today = get_now_kst().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT id, title, is_done, completed_at, alarm_time FROM plans WHERE plan_date = ? AND username = ? ORDER BY is_done ASC, display_order ASC",
        (today, username)
    )
    rows = cur.fetchall()
    conn.close()

    total = len(rows)
    done_count = sum(1 for r in rows if r[2] == 1)
    remaining_count = total - done_count

    items = [
        {
            "id": r[0],
            "title": r[1],
            "is_done": bool(r[2]),
            "completed_at": r[3],
            "alarm_time": r[4]
        }
        for r in rows
    ]

    return JSONResponse(content={
        "date": today,
        "total": total,
        "done": done_count,
        "remaining": remaining_count,
        "items": items
    })

# ----------------- Gemini AI 분석 API -----------------
@app.post("/api/ai/analyze")
def analyze_plans(req: AIRequest):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        "SELECT title, is_done, completed_at FROM plans WHERE plan_date = ? AND username = ? ORDER BY display_order ASC",
        (req.plan_date, req.username)
    )
    items = cur.fetchall()
    conn.close()

    if not items:
        return {"feedback": f"{req.plan_date}에 등록된 계획이 없습니다."}

    records = [f"- {r[0]} -> {'완료 (' + r[2] + ')' if r[1] else '미완료'}" for r in items]
    record_text = "\n".join(records)

    prompt = f"""
    당신은 스마트 계획 & 실행 코치입니다. 사용자({req.username})의 {req.plan_date} 실행 기록입니다:
    {record_text}

    4문장 내외로 오늘 실행률을 칭찬하고 개선점 1가지를 명확히 짚어주세요.
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

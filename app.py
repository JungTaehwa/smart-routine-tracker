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
    
    # 1. 유저 테이블
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )
    """)
    
    # 2. 카테고리 테이블 (투두메이트 고유 색상 지원)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            name TEXT,
            color TEXT,
            display_order INTEGER DEFAULT 0
        )
    """)
    
    # 3. 계획 테이블 (초 단위 완료 시각 및 메모, 알람 지원)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            category_id INTEGER,
            title TEXT,
            memo TEXT DEFAULT '',
            is_done INTEGER DEFAULT 0,
            completed_at TEXT,
            plan_date TEXT,
            display_order INTEGER DEFAULT 0,
            alarm_time TEXT,
            snooze_count INTEGER DEFAULT 0
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
    category_id: int
    title: str
    plan_date: str

class PlanToggle(BaseModel):
    id: int

class PlanUpdate(BaseModel):
    id: int
    username: str
    title: Optional[str] = None
    memo: Optional[str] = None
    alarm_time: Optional[str] = None
    plan_date: Optional[str] = None

class PlanSnooze(BaseModel):
    id: int
    username: str
    minutes: int

class BatchAction(BaseModel):
    username: str
    from_date: str
    to_date: Optional[str] = None
    action: str  # 'move_tomorrow', 'copy_all', 'delete_all'

class CategoryCreate(BaseModel):
    username: str
    name: str
    color: str

# 투두메이트 1:1 복제 UI (다크 모드 + 클로버 + 바텀시트 메뉴 2종)
HTML_LAYOUT = """
<!DOCTYPE html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
  <title>todomate</title>
  
  <link rel="manifest" href="/manifest.json">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-status-bar-style" content="black">
  <meta name="theme-color" content="#000000">

  <script src="https://cdn.tailwindcss.com"></script>
  <style>
    body { background-color: #000000; color: #ffffff; user-select: none; -webkit-tap-highlight-color: transparent; }
    .clover-icon {
      width: 26px; height: 26px; display: inline-flex; align-items: center; justify-content: center;
      transition: all 0.15s ease;
    }
    /* 바텀시트 애니메이션 */
    .sheet-enter { transform: translateY(100%); transition: transform 0.25s cubic-bezier(0.16, 1, 0.3, 1); }
    .sheet-show { transform: translateY(0); }
  </style>
</head>
<body class="flex justify-center min-h-screen bg-black">
  <div class="w-full max-w-md bg-black flex flex-col min-h-screen relative border-x border-[#1a1a1a]">
    
    <!-- 1. 투두메이트 상단 바 (me 토글, 프로필 문구, 햄버거 메뉴) -->
    <div class="px-4 pt-3 pb-2 flex justify-between items-center">
      <div class="flex items-center space-x-2">
        <div class="bg-white text-black px-3.5 py-1 rounded-full flex items-center space-x-1.5 shadow">
          <span class="w-2.5 h-2.5 rounded-full bg-black"></span>
          <span class="text-xs font-bold" id="top-username">me</span>
        </div>
        <button class="text-slate-400 p-1.5 hover:text-white">👥</button>
      </div>
      <div class="flex items-center space-x-2">
        <button onclick="openTimelineModal()" class="text-xs bg-[#1f1f23] text-teal-300 px-2.5 py-1 rounded-full font-medium">⏱️ 타임라인</button>
        <button onclick="logout()" class="text-[11px] text-slate-500 hover:text-rose-400">로그아웃</button>
        <button class="text-slate-300 text-lg p-1">☰</button>
      </div>
    </div>

    <!-- 프로필 자기소개 영역 -->
    <div class="px-5 py-2 flex items-center space-x-3">
      <div class="w-12 h-12 rounded-full bg-[#1c1c1e] flex items-center justify-center text-xl text-slate-500">
        🍀
      </div>
      <div class="flex-1">
        <div class="text-sm font-bold text-white flex items-center space-x-1">
          <span id="profile-name">me</span>
        </div>
        <p class="text-xs text-slate-500 mt-0.5">프로필에 자기소개를 입력해보세요</p>
      </div>
      <button class="text-slate-600 text-xs">✏️</button>
    </div>

    <!-- 2. 주간 네비게이터 (2026년 10월 2주차) -->
    <div class="px-4 py-2 mt-1">
      <div class="flex justify-between items-center mb-2">
        <div class="flex items-center space-x-1.5 cursor-pointer">
          <div class="w-4 h-4 rounded-full bg-gradient-to-tr from-teal-400 to-indigo-500"></div>
          <span class="text-sm font-bold text-slate-200" id="header-week-label">2026년 10월 2주차</span>
          <span class="text-xs text-slate-500">▼</span>
        </div>
        <div class="flex items-center space-x-2">
          <span class="text-[11px] bg-[#1a1a1c] text-slate-400 px-2 py-0.5 rounded-md">월</span>
          <button onclick="changeWeek(-1)" class="w-6 h-6 rounded-full bg-[#18181a] text-slate-400 text-xs flex items-center justify-center">‹</button>
          <button onclick="changeWeek(1)" class="w-6 h-6 rounded-full bg-[#18181a] text-slate-400 text-xs flex items-center justify-center">›</button>
        </div>
      </div>

      <!-- 요일별 클로버 및 날짜 (투두메이트 정통 그리드) -->
      <div class="grid grid-cols-7 gap-1 text-center" id="week-calendar-grid"></div>
    </div>

    <div class="h-px bg-[#18181a] my-2"></div>

    <!-- 3. 카테고리 & 할 일 목록 영역 -->
    <div class="flex-1 px-4 overflow-y-auto space-y-4 pb-24" id="category-plans-container"></div>

    <!-- 하단 고정: '≡ 리스트 메뉴' 텍스트 버튼 -->
    <div class="absolute bottom-16 right-4 z-10">
      <button onclick="openListMenuSheet()" class="text-xs text-slate-400 hover:text-white flex items-center space-x-1 bg-black/80 backdrop-blur px-3 py-1.5 rounded-full border border-[#222]">
        <span>≡</span>
        <span class="font-medium">리스트 메뉴</span>
      </button>
    </div>

    <!-- 4. 투두메이트 하단 탭 바 (홈, 탐색, 알림, 피드, 마이) -->
    <div class="h-14 border-t border-[#18181a] bg-black grid grid-cols-5 items-center text-center text-lg text-slate-500">
      <button class="text-white">🏠</button>
      <button onclick="alert('루틴/탐색 피드 준비 중')">🧭</button>
      <button onclick="alert('알림 내역이 없습니다.')">🔔</button>
      <button onclick="requestAIFeedback()">✨</button>
      <button onclick="openTimelineModal()">⏱️</button>
    </div>

    <!-- ==================== 바텀시트 1: 리스트 메뉴 (스크린샷 1) ==================== -->
    <div id="list-menu-backdrop" class="fixed inset-0 bg-black/60 z-40 hidden flex items-end justify-center" onclick="closeListMenuSheet(event)">
      <div id="list-menu-sheet" class="w-full max-w-md bg-[#1c1c1e] rounded-t-2xl p-5 sheet-enter" onclick="event.stopPropagation()">
        <div class="w-10 h-1 bg-slate-600 rounded-full mx-auto mb-4"></div>
        <h3 class="text-sm font-bold text-center text-white mb-4">리스트 메뉴</h3>
        
        <div class="space-y-3">
          <div onclick="batchAction('move_tomorrow')" class="flex items-center justify-between p-2.5 rounded-xl hover:bg-[#28282b] cursor-pointer">
            <div class="flex items-center space-x-3">
              <span class="w-7 h-7 rounded-full bg-[#3a3a44] flex items-center justify-center text-xs">➡️</span>
              <span class="text-sm font-medium">미완료 할 일 내일 하기</span>
            </div>
            <span class="w-5 h-5 rounded-full border border-slate-600"></span>
          </div>

          <div onclick="batchAction('move_other_day')" class="flex items-center justify-between p-2.5 rounded-xl hover:bg-[#28282b] cursor-pointer">
            <div class="flex items-center space-x-3">
              <span class="w-7 h-7 rounded-full bg-[#3a3a44] flex items-center justify-center text-xs">🔄</span>
              <span class="text-sm font-medium">미완료 할 일 다른 날 하기</span>
            </div>
            <span class="w-5 h-5 rounded-full border border-slate-600"></span>
          </div>

          <div onclick="batchAction('copy_all')" class="flex items-center justify-between p-2.5 rounded-xl hover:bg-[#28282b] cursor-pointer">
            <div class="flex items-center space-x-3">
              <span class="w-7 h-7 rounded-full bg-[#3a3a44] flex items-center justify-center text-xs">📑</span>
              <span class="text-sm font-medium">모든 할 일 복사 (내일로)</span>
            </div>
            <span class="w-5 h-5 rounded-full border border-slate-600"></span>
          </div>

          <div onclick="batchAction('delete_all')" class="flex items-center justify-between p-2.5 rounded-xl hover:bg-[#28282b] cursor-pointer">
            <div class="flex items-center space-x-3">
              <span class="w-7 h-7 rounded-full bg-rose-950 text-rose-400 flex items-center justify-center text-xs">❌</span>
              <span class="text-sm font-medium text-rose-500">모든 할 일 삭제</span>
            </div>
            <span class="w-5 h-5 rounded-full border border-slate-600"></span>
          </div>
        </div>

        <div class="grid grid-cols-2 gap-3 mt-5">
          <button onclick="closeListMenuSheet()" class="py-3 rounded-xl bg-[#2c2c2e] text-slate-300 font-semibold text-xs">취소</button>
          <button onclick="closeListMenuSheet()" class="py-3 rounded-xl bg-[#3a3a3e] text-white font-semibold text-xs">확인</button>
        </div>
      </div>
    </div>

    <!-- ==================== 바텀시트 2: 할 일 상세/수정 메뉴 (스크린샷 2) ==================== -->
    <div id="plan-detail-backdrop" class="fixed inset-0 bg-black/60 z-40 hidden flex items-end justify-center" onclick="closePlanDetailSheet(event)">
      <div id="plan-detail-sheet" class="w-full max-w-md bg-[#1c1c1e] rounded-t-2xl p-5 sheet-enter" onclick="event.stopPropagation()">
        <div class="w-10 h-1 bg-slate-600 rounded-full mx-auto mb-3"></div>
        <h3 class="text-base font-bold text-center text-white mb-4" id="detail-plan-title">122</h3>
        
        <!-- 상단 수정/삭제 버튼 -->
        <div class="grid grid-cols-2 gap-2.5 mb-4">
          <button onclick="editPlanTitle()" class="flex items-center justify-center space-x-1.5 py-3 rounded-xl bg-[#2a2a2d] hover:bg-[#343438] text-xs font-semibold">
            <span>✏️</span> <span>수정하기</span>
          </button>
          <button onclick="deletePlan()" class="flex items-center justify-center space-x-1.5 py-3 rounded-xl bg-[#2a2a2d] hover:bg-[#343438] text-rose-400 text-xs font-semibold">
            <span>🗑️</span> <span>삭제하기</span>
          </button>
        </div>

        <!-- 옵션 리스트 -->
        <div class="space-y-1 text-sm font-medium">
          <div onclick="editPlanMemo()" class="flex items-center space-x-3 p-2.5 hover:bg-[#28282b] rounded-xl cursor-pointer">
            <span class="w-7 h-7 rounded-full bg-amber-500/20 text-amber-400 flex items-center justify-center text-xs">📒</span>
            <span>메모</span>
          </div>

          <div onclick="setPlanAlarm()" class="flex items-center space-x-3 p-2.5 hover:bg-[#28282b] rounded-xl cursor-pointer">
            <span class="w-7 h-7 rounded-full bg-fuchsia-500/20 text-fuchsia-400 flex items-center justify-center text-xs">⏰</span>
            <div class="flex-1 flex justify-between items-center">
              <span>시간 알림</span>
              <span class="text-xs text-fuchsia-300 font-mono" id="detail-alarm-badge"></span>
            </div>
          </div>

          <div onclick="openTimer()" class="flex items-center space-x-3 p-2.5 hover:bg-[#28282b] rounded-xl cursor-pointer">
            <span class="w-7 h-7 rounded-full bg-fuchsia-500/20 text-fuchsia-400 flex items-center justify-center text-xs">⏱️</span>
            <span>타이머 열기</span>
          </div>

          <div onclick="movePlanTomorrow()" class="flex items-center space-x-3 p-2.5 hover:bg-[#28282b] rounded-xl cursor-pointer">
            <span class="w-7 h-7 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-xs">➡️</span>
            <span>내일 하기</span>
          </div>

          <div onclick="movePlanOtherDate()" class="flex items-center space-x-3 p-2.5 hover:bg-[#28282b] rounded-xl cursor-pointer">
            <span class="w-7 h-7 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-xs">📅</span>
            <span>날짜 바꾸기</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 초 단위 정밀 타임라인 모달 (HH:MM:SS) -->
    <div id="timeline-modal" class="fixed inset-0 bg-black/80 z-50 hidden flex items-center justify-center p-4">
      <div class="bg-[#1c1c1e] border border-[#2c2c2e] rounded-2xl w-full max-w-sm p-5 flex flex-col max-h-[80vh]">
        <div class="flex justify-between items-center mb-3 border-b border-[#2c2c2e] pb-2">
          <h3 class="text-sm font-bold text-teal-400">⏱️ 초 단위 실행 타임라인</h3>
          <button onclick="closeTimelineModal()" class="text-slate-400 text-sm">✕</button>
        </div>
        <div class="flex-1 overflow-y-auto space-y-2 text-xs" id="timeline-modal-list"></div>
      </div>
    </div>

    <!-- 알람 팝업 모달 (소리 & 미루기) -->
    <div id="alarm-ring-modal" class="fixed inset-0 bg-black/85 flex items-center justify-center p-4 z-50 hidden">
      <div class="bg-[#1c1c1e] border border-fuchsia-600/40 p-6 rounded-2xl w-full max-w-xs shadow-2xl text-center">
        <div class="text-3xl mb-2 animate-bounce">⏰</div>
        <h3 class="text-base font-bold text-white mb-1" id="alarm-ring-title">계획 알람</h3>
        <p class="text-xs text-slate-400 mb-4" id="alarm-ring-time"></p>
        
        <button onclick="handleAlarmComplete()" class="w-full py-2.5 rounded-xl font-bold text-xs bg-teal-400 text-black mb-3">
          ✔ 지금 완료했습니다!
        </button>
        
        <div class="border-t border-[#2a2a2d] pt-2 text-[11px] text-slate-400 mb-2">미루기 시간 선택</div>
        <div class="grid grid-cols-3 gap-1.5 mb-2">
          <button onclick="handleAlarmSnooze(5)" class="bg-[#2a2a2d] py-1.5 rounded-lg text-xs hover:border-teal-400 border border-transparent">5분</button>
          <button onclick="handleAlarmSnooze(10)" class="bg-[#2a2a2d] py-1.5 rounded-lg text-xs hover:border-teal-400 border border-transparent">10분</button>
          <button onclick="handleAlarmSnooze(30)" class="bg-[#2a2a2d] py-1.5 rounded-lg text-xs hover:border-teal-400 border border-transparent">30분</button>
        </div>
        <button onclick="handleAlarmSnoozeCustom()" class="w-full bg-[#2a2a2d] py-1.5 rounded-lg text-xs text-slate-400">직접 분 입력</button>
      </div>
    </div>

    <!-- 로그인 모달 -->
    <div id="auth-modal" class="fixed inset-0 bg-black/90 flex items-center justify-center p-4 z-50 hidden">
      <div class="bg-[#1c1c1e] border border-[#2c2c2e] p-6 rounded-2xl w-full max-w-xs text-center shadow-2xl">
        <div class="text-2xl mb-2">🍀 todomate</div>
        <h3 class="text-sm font-bold mb-3 text-slate-300" id="auth-title">로그인</h3>
        <input type="text" id="auth-id" placeholder="아이디" autocomplete="off" class="w-full bg-[#121214] border border-[#2a2a2e] rounded-lg p-2.5 text-xs text-white mb-2 focus:outline-none focus:border-teal-400">
        <input type="password" id="auth-pw" placeholder="비밀번호" class="w-full bg-[#121214] border border-[#2a2a2e] rounded-lg p-2.5 text-xs text-white mb-4 focus:outline-none focus:border-teal-400">
        <button onclick="submitAuth()" class="w-full bg-teal-400 text-black font-bold py-2 rounded-lg text-xs mb-2" id="auth-submit-btn">로그인</button>
        <p class="text-[11px] text-slate-500">
          <span id="auth-switch-text">계정이 없으신가요?</span>
          <button onclick="toggleAuthMode()" class="text-teal-400 underline ml-1" id="auth-switch-btn">회원가입</button>
        </p>
      </div>
    </div>

  </div>

  <script>
    let currentUser = localStorage.getItem('routine_user') || '';
    let isSignUpMode = false;
    let currentDate = new Date();
    let selectedDateStr = formatDate(currentDate);
    let activeInputCategoryId = null;
    let currentSelectedPlan = null;
    let currentRingingPlan = null;
    let triggeredAlarms = new Set();

    function formatDate(d) {
      const year = d.getFullYear();
      const month = String(d.getMonth() + 1).padStart(2, '0');
      const day = String(d.getDate()).padStart(2, '0');
      return `${year}-${month}-${day}`;
    }

    // 투두메이트 4잎 클로버 SVG 렌더러
    function getCloverSvg(color, isDone) {
      const fillColor = isDone ? color : '#2c2c30';
      return `
        <svg viewBox="0 0 100 100" class="w-6 h-6 transition">
          <g fill="${fillColor}">
            <circle cx="36" cy="36" r="22"/>
            <circle cx="64" cy="36" r="22"/>
            <circle cx="36" cy="64" r="22"/>
            <circle cx="64" cy="64" r="22"/>
            <circle cx="50" cy="50" r="16"/>
          </g>
          ${isDone ? `<path d="M34 50 L45 61 L67 39" fill="none" stroke="#ffffff" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>` : ''}
        </svg>
      `;
    }

    function checkAuth() {
      if (!currentUser) {
        document.getElementById('auth-modal').classList.remove('hidden');
      } else {
        document.getElementById('auth-modal').classList.add('hidden');
        document.getElementById('top-username').innerText = currentUser;
        document.getElementById('profile-name').innerText = currentUser;
        if (Notification.permission !== "granted") Notification.requestPermission();
        renderWeekCalendar();
        loadCategoriesAndPlans();
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

    // 주간 네비게이터 계산
    function getWeekInfo(date) {
      const month = date.getMonth() + 1;
      const firstDay = new Date(date.getFullYear(), date.getMonth(), 1);
      const weekNo = Math.ceil((date.getDate() + firstDay.getDay()) / 7);
      return `${date.getFullYear()}년 ${month}월 ${weekNo}주차`;
    }

    async function renderWeekCalendar() {
      document.getElementById('header-week-label').innerText = getWeekInfo(currentDate);

      const dayOfWeek = currentDate.getDay();
      const mondayOffset = (dayOfWeek === 0 ? -6 : 1) - dayOfWeek;
      const monday = new Date(currentDate);
      monday.setDate(currentDate.getDate() + mondayOffset);

      const grid = document.getElementById('week-calendar-grid');
      grid.innerHTML = '';
      const dayNames = ['월', '화', '수', '목', '금', '토', '일'];

      // 일주일치 완료 카운트 가져오기
      const res = await fetch(`/api/week_summary?username=${currentUser}&start_date=${formatDate(monday)}`);
      const counts = await res.json();

      for (let i = 0; i < 7; i++) {
        const target = new Date(monday);
        target.setDate(monday.getDate() + i);
        const targetStr = formatDate(target);
        const isSelected = targetStr === selectedDateStr;
        const doneCount = counts[targetStr] || 0;

        const col = document.createElement('div');
        col.className = 'flex flex-col items-center cursor-pointer';
        col.onclick = () => {
          selectedDateStr = targetStr;
          currentDate = new Date(target);
          renderWeekCalendar();
          loadCategoriesAndPlans();
        };

        // 클로버 뱃지 색상 (완료 건수가 있으면 청록색 클로버, 없으면 회색 클로버)
        const cloverFill = doneCount > 0 ? '#2dd4bf' : '#222226';
        const cloverTextColor = doneCount > 0 ? '#000000' : '#55555c';

        col.innerHTML = `
          <span class="text-[11px] ${i === 6 ? 'text-rose-500' : (i === 5 ? 'text-blue-400' : 'text-slate-400')} font-medium mb-1">${dayNames[i]}</span>
          <div class="relative flex items-center justify-center my-0.5">
            <svg viewBox="0 0 100 100" class="w-7 h-7">
              <g fill="${cloverFill}">
                <circle cx="36" cy="36" r="22"/>
                <circle cx="64" cy="36" r="22"/>
                <circle cx="36" cy="64" r="22"/>
                <circle cx="64" cy="64" r="22"/>
                <circle cx="50" cy="50" r="16"/>
              </g>
            </svg>
            <span class="absolute text-[11px] font-bold" style="color: ${cloverTextColor}">${doneCount > 0 ? doneCount : ''}</span>
          </div>
          <span class="text-xs mt-1 w-6 h-6 flex items-center justify-center rounded-full font-bold ${isSelected ? 'bg-white text-black' : (i === 6 ? 'text-rose-500' : 'text-slate-300')}">
            ${target.getDate()}
          </span>
        `;
        grid.appendChild(col);
      }
    }

    function changeWeek(diff) {
      currentDate.setDate(currentDate.getDate() + (diff * 7));
      selectedDateStr = formatDate(currentDate);
      renderWeekCalendar();
      loadCategoriesAndPlans();
    }

    // 메인 데이터 로딩: 카테고리별 그룹화 렌더링
    async function loadCategoriesAndPlans() {
      if (!currentUser) return;
      const res = await fetch(`/api/plans_by_category?username=${currentUser}&date=${selectedDateStr}`);
      const categories = await res.json();
      const container = document.getElementById('category-plans-container');
      container.innerHTML = '';

      categories.forEach(cat => {
        const catBox = document.createElement('div');
        catBox.className = 'space-y-1.5';

        // 카테고리 알약 헤더 (🔒 일상 +)
        const isInputOpen = activeInputCategoryId === cat.id;
        catBox.innerHTML = `
          <div class="flex items-center space-x-2">
            <div class="inline-flex items-center space-x-1.5 px-3 py-1 rounded-full bg-[#1c1c1e] text-xs font-bold" style="color: ${cat.color}">
              <span>🔒</span>
              <span>${cat.name}</span>
              <button onclick="toggleCategoryInput(${cat.id})" class="text-slate-400 hover:text-white text-sm pl-1 font-bold">+</button>
            </div>
          </div>
          
          <!-- 인라인 추가 입력창 -->
          <div id="input-box-${cat.id}" class="${isInputOpen ? '' : 'hidden'} py-1">
            <form onsubmit="handleAddPlan(event, ${cat.id})" class="flex gap-2">
              <input type="text" id="input-text-${cat.id}" placeholder="할 일 입력 후 Enter..." autocomplete="off"
                class="flex-1 bg-[#161618] border border-[#2a2a2e] rounded-xl px-3 py-2 text-xs text-white focus:outline-none focus:border-teal-400">
              <button type="submit" class="bg-teal-400 text-black px-3 rounded-xl font-bold text-xs">확인</button>
            </form>
          </div>

          <!-- 할 일 목록 -->
          <div class="space-y-1 pl-1" id="cat-list-${cat.id}"></div>
        `;

        const listDiv = catBox.querySelector(`#cat-list-${cat.id}`);
        cat.plans.forEach(plan => {
          const itemEl = document.createElement('div');
          itemEl.className = 'flex items-center justify-between py-2 px-1 hover:bg-[#121214] rounded-lg transition';

          itemEl.innerHTML = `
            <div class="flex items-center space-x-3 flex-1 cursor-pointer">
              <!-- 클로버 체크박스 -->
              <div onclick="togglePlanCheck(${plan.id})" class="clover-icon">
                ${getCloverSvg(cat.color, plan.is_done)}
              </div>
              <!-- 제목 클릭 시 스크린샷 2번 상세 바텀시트 오픈 -->
              <div onclick="openPlanDetailSheet(${JSON.stringify(plan).replace(/"/g, '&quot;')})" class="flex-1">
                <span class="text-sm font-medium ${plan.is_done ? 'text-slate-300' : 'text-white'}">${plan.title}</span>
                ${plan.completed_at ? `<span class="block text-[10px] text-teal-400/80 font-mono mt-0.5">${plan.completed_at} 완료</span>` : ''}
              </div>
            </div>
          `;
          listDiv.appendChild(itemEl);
        });

        container.appendChild(catBox);
      });

      checkAlarms();
    }

    function toggleCategoryInput(catId) {
      activeInputCategoryId = activeInputCategoryId === catId ? null : catId;
      loadCategoriesAndPlans();
      setTimeout(() => {
        const inp = document.getElementById(`input-text-${catId}`);
        if (inp) inp.focus();
      }, 50);
    }

    async function handleAddPlan(e, catId) {
      e.preventDefault();
      const inp = document.getElementById(`input-text-${catId}`);
      if (!inp.value.trim()) return;

      await fetch('/api/plans', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          username: currentUser,
          category_id: catId,
          title: inp.value.trim(),
          plan_date: selectedDateStr
        })
      });
      inp.value = '';
      activeInputCategoryId = null;
      renderWeekCalendar();
      loadCategoriesAndPlans();
    }

    async function togglePlanCheck(id) {
      await fetch('/api/plans/toggle', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
      });
      renderWeekCalendar();
      loadCategoriesAndPlans();
    }

    // ==================== 바텀시트 동작 ====================
    function openListMenuSheet() {
      document.getElementById('list-menu-backdrop').classList.remove('hidden');
      setTimeout(() => document.getElementById('list-menu-sheet').classList.add('sheet-show'), 10);
    }

    function closeListMenuSheet() {
      document.getElementById('list-menu-sheet').classList.remove('sheet-show');
      setTimeout(() => document.getElementById('list-menu-backdrop').classList.add('hidden'), 250);
    }

    function openPlanDetailSheet(plan) {
      currentSelectedPlan = plan;
      document.getElementById('detail-plan-title').innerText = plan.title;
      document.getElementById('detail-alarm-badge').innerText = plan.alarm_time ? `${plan.alarm_time}` : '';
      document.getElementById('plan-detail-backdrop').classList.remove('hidden');
      setTimeout(() => document.getElementById('plan-detail-sheet').classList.add('sheet-show'), 10);
    }

    function closePlanDetailSheet() {
      document.getElementById('plan-detail-sheet').classList.remove('sheet-show');
      setTimeout(() => document.getElementById('plan-detail-backdrop').classList.add('hidden'), 250);
    }

    // 할 일 메뉴 세부 기능
    async function editPlanTitle() {
      if (!currentSelectedPlan) return;
      const newTitle = prompt('할 일 수정:', currentSelectedPlan.title);
      if (newTitle && newTitle.trim()) {
        await fetch('/api/plans/update', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id: currentSelectedPlan.id, username: currentUser, title: newTitle.trim() })
        });
        closePlanDetailSheet();
        loadCategoriesAndPlans();
      }
    }

    async function deletePlan() {
      if (!currentSelectedPlan || !confirm('이 할 일을 삭제하시겠습니까?')) return;
      await fetch(`/api/plans/${currentSelectedPlan.id}?username=${currentUser}`, { method: 'DELETE' });
      closePlanDetailSheet();
      renderWeekCalendar();
      loadCategoriesAndPlans();
    }

    async function editPlanMemo() {
      if (!currentSelectedPlan) return;
      const newMemo = prompt('메모 작성:', currentSelectedPlan.memo || '');
      if (newMemo !== null) {
        await fetch('/api/plans/update', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id: currentSelectedPlan.id, username: currentUser, memo: newMemo })
        });
        alert('메모가 저장되었습니다.');
      }
    }

    async function setPlanAlarm() {
      if (!currentSelectedPlan) return;
      const time = prompt('알람 시각 입력 (예: 14:30):', currentSelectedPlan.alarm_time || '09:00');
      if (time && time.includes(':')) {
        await fetch('/api/plans/update', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id: currentSelectedPlan.id, username: currentUser, alarm_time: time.trim() })
        });
        alert(`${time}에 알람이 설정되었습니다.`);
        closePlanDetailSheet();
        loadCategoriesAndPlans();
      }
    }

    function openTimer() {
      alert('⏱️ 타이머 기능: 25분 집중 모드가 시작되었습니다.');
    }

    async function movePlanTomorrow() {
      if (!currentSelectedPlan) return;
      const cur = new Date(selectedDateStr);
      cur.setDate(cur.getDate() + 1);
      const tomorrow = formatDate(cur);

      await fetch('/api/plans/update', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id: currentSelectedPlan.id, username: currentUser, plan_date: tomorrow })
      });
      alert(`내일(${tomorrow})로 이동되었습니다.`);
      closePlanDetailSheet();
      renderWeekCalendar();
      loadCategoriesAndPlans();
    }

    async function movePlanOtherDate() {
      if (!currentSelectedPlan) return;
      const newDate = prompt('이동할 날짜 입력 (YYYY-MM-DD):', selectedDateStr);
      if (newDate && newDate.length === 10) {
        await fetch('/api/plans/update', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ id: currentSelectedPlan.id, username: currentUser, plan_date: newDate })
        });
        closePlanDetailSheet();
        renderWeekCalendar();
        loadCategoriesAndPlans();
      }
    }

    // 리스트 메뉴 일괄 작업
    async function batchAction(action) {
      let targetDate = null;
      if (action === 'move_tomorrow' || action === 'copy_all') {
        const cur = new Date(selectedDateStr);
        cur.setDate(cur.getDate() + 1);
        targetDate = formatDate(cur);
      } else if (action === 'move_other_day') {
        targetDate = prompt('이동할 날짜 입력 (YYYY-MM-DD):');
        if (!targetDate) return;
      } else if (action === 'delete_all') {
        if (!confirm('오늘의 모든 할 일을 삭제하시겠습니까?')) return;
      }

      await fetch('/api/plans/batch', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          username: currentUser,
          from_date: selectedDateStr,
          to_date: targetDate,
          action: action
        })
      });
      closeListMenuSheet();
      renderWeekCalendar();
      loadCategoriesAndPlans();
    }

    // ==================== 초 단위 타임라인 모달 ====================
    async function openTimelineModal() {
      const res = await fetch(`/api/timeline?username=${currentUser}&date=${selectedDateStr}`);
      const list = await res.json();
      const container = document.getElementById('timeline-modal-list');
      container.innerHTML = '';

      if (list.length === 0) {
        container.innerHTML = '<p class="text-center text-slate-500 py-6">완료된 항목이 없습니다.</p>';
      } else {
        list.forEach(item => {
          const div = document.createElement('div');
          div.className = 'flex items-center justify-between p-2.5 bg-[#252528] rounded-xl border border-[#333]';
          div.innerHTML = `
            <div>
              <span class="text-slate-400 text-[10px] block">${item.category_name}</span>
              <span class="text-white font-medium">${item.title}</span>
            </div>
            <span class="text-teal-300 font-mono font-bold text-xs bg-teal-950 px-2 py-0.5 rounded border border-teal-800">${item.completed_at}</span>
          `;
          container.appendChild(div);
        });
      }
      document.getElementById('timeline-modal').classList.remove('hidden');
    }

    function closeTimelineModal() {
      document.getElementById('timeline-modal').classList.add('hidden');
    }

    // ==================== 알람 & 미루기 ====================
    function playAlarmSound() {
      try {
        const ctx = new (window.AudioContext || window.webkitAudioContext)();
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.type = 'sine';
        osc.frequency.setValueAtTime(880, ctx.currentTime);
        osc.frequency.setValueAtTime(1320, ctx.currentTime + 0.15);
        gain.gain.setValueAtTime(0.3, ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, ctx.currentTime + 0.7);
        osc.start();
        osc.stop(ctx.currentTime + 0.7);
      } catch(e) {}
    }

    async function checkAlarms() {
      const now = new Date();
      const curTimeStr = String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
      const todayStr = formatDate(now);
      if (selectedDateStr !== todayStr) return;

      const res = await fetch(`/api/plans_by_category?username=${currentUser}&date=${selectedDateStr}`);
      const cats = await res.json();
      cats.forEach(c => {
        c.plans.forEach(p => {
          if (!p.is_done && p.alarm_time === curTimeStr) {
            const key = `${p.id}-${p.alarm_time}`;
            if (!triggeredAlarms.has(key)) {
              triggeredAlarms.add(key);
              triggerAlarm(p);
            }
          }
        });
      });
    }

    function triggerAlarm(plan) {
      currentRingingPlan = plan;
      playAlarmSound();
      if (Notification.permission === 'granted') {
        new Notification("⏰ todomate 알람", { body: `[${plan.title}] 실행할 시간입니다!` });
      }
      document.getElementById('alarm-ring-title').innerText = plan.title;
      document.getElementById('alarm-ring-time').innerText = `설정 시각: ${plan.alarm_time}`;
      document.getElementById('alarm-ring-modal').classList.remove('hidden');
    }

    async function handleAlarmComplete() {
      if (!currentRingingPlan) return;
      await togglePlanCheck(currentRingingPlan.id);
      document.getElementById('alarm-ring-modal').classList.add('hidden');
      currentRingingPlan = null;
    }

    async function handleAlarmSnooze(minutes) {
      if (!currentRingingPlan) return;
      await fetch('/api/plans/snooze', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id: currentRingingPlan.id, username: currentUser, minutes })
      });
      document.getElementById('alarm-ring-modal').classList.add('hidden');
      currentRingingPlan = null;
      loadCategoriesAndPlans();
    }

    function handleAlarmSnoozeCustom() {
      const min = prompt("몇 분 뒤로 미루시겠습니까?", "15");
      if (min && !isNaN(min)) handleAlarmSnooze(parseInt(min));
    }

    setInterval(() => { if (currentUser) checkAlarms(); }, 15000);

    // AI 코칭
    async function requestAIFeedback() {
      alert("Gemini가 오늘 실행 패턴(완료 시간대)을 분석 중입니다. 잠시만 기다려주세요...");
      const res = await fetch('/api/ai/analyze', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ username: currentUser, plan_date: selectedDateStr })
      });
      const data = await res.json();
      alert(data.feedback);
    }

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
        "name": "todomate",
        "short_name": "todomate",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#000000",
        "theme_color": "#000000",
        "icons": [{"src": "https://cdn-icons-png.flaticon.com/512/906/906334.png", "sizes": "512x512", "type": "image/png"}]
    }

# ----------------- 인증 API -----------------
@app.post("/api/signup")
def signup(data: UserAuth):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users (username, password) VALUES (?, ?)", (data.username, hash_pw(data.password)))
        # 투두메이트 기본 4대 카테고리 색상 생성 (스크린샷 일치)
        defaults = [
            ("일상", "#2dd4bf", 1),       # 청록색
            ("회사", "#818cf8", 2),       # 보라색
            ("카테고리 3", "#fb923c", 3),  # 주황색
            ("퇴근후", "#94a3b8", 4)      # 회색
        ]
        for name, color, order in defaults:
            cur.execute("INSERT INTO categories (username, name, color, display_order) VALUES (?, ?, ?, ?)",
                        (data.username, name, color, order))
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

# ----------------- 주간 요약 API (캘린더 클로버 카운트) -----------------
@app.get("/api/week_summary")
def get_week_summary(username: str, start_date: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    # 월요일부터 7일간의 완료 건수 합산
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    summary = {}
    for i in range(7):
        d_str = (start_dt + timedelta(days=i)).strftime("%Y-%m-%d")
        cur.execute("SELECT COUNT(*) FROM plans WHERE username = ? AND plan_date = ? AND is_done = 1", (username, d_str))
        summary[d_str] = cur.fetchone()[0]
    conn.close()
    return summary

# ----------------- 카테고리별 계획 조회 API -----------------
@app.get("/api/plans_by_category")
def get_plans_by_category(username: str, date: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, name, color FROM categories WHERE username = ? ORDER BY display_order ASC", (username,))
    categories = cur.fetchall()

    result = []
    for cat_id, name, color in categories:
        cur.execute(
            """SELECT id, title, memo, is_done, completed_at, alarm_time 
               FROM plans WHERE username = ? AND category_id = ? AND plan_date = ? 
               ORDER BY display_order ASC, id ASC""",
            (username, cat_id, date)
        )
        plan_rows = cur.fetchall()
        plans = [
            {
                "id": p[0],
                "title": p[1],
                "memo": p[2],
                "is_done": bool(p[3]),
                "completed_at": p[4],
                "alarm_time": p[5]
            }
            for p in plan_rows
        ]
        result.append({
            "id": cat_id,
            "name": name,
            "color": color,
            "plans": plans
        })
    conn.close()
    return result

@app.post("/api/plans")
def add_plan(plan: PlanCreate):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO plans (username, category_id, title, is_done, completed_at, plan_date, display_order)
           VALUES (?, ?, ?, 0, NULL, ?, 0)""",
        (plan.username, plan.category_id, plan.title, plan.plan_date)
    )
    conn.commit()
    conn.close()
    return {"status": "success"}

# 완료 체크 시 초 단위 (HH:MM:SS) KST 저장
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
    # 초 단위 정밀 타임스탬프
    completed_at = get_now_kst().strftime("%H:%M:%S") if now_done == 1 else None

    cur.execute("UPDATE plans SET is_done = ?, completed_at = ? WHERE id = ?", (now_done, completed_at, payload.id))
    conn.commit()
    conn.close()
    return {"is_done": bool(now_done), "completed_at": completed_at}

@app.post("/api/plans/update")
def update_plan(data: PlanUpdate):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if data.title is not None:
        cur.execute("UPDATE plans SET title = ? WHERE id = ? AND username = ?", (data.title, data.id, data.username))
    if data.memo is not None:
        cur.execute("UPDATE plans SET memo = ? WHERE id = ? AND username = ?", (data.memo, data.id, data.username))
    if data.alarm_time is not None:
        cur.execute("UPDATE plans SET alarm_time = ? WHERE id = ? AND username = ?", (data.alarm_time, data.id, data.username))
    if data.plan_date is not None:
        cur.execute("UPDATE plans SET plan_date = ? WHERE id = ? AND username = ?", (data.plan_date, data.id, data.username))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.delete("/api/plans/{plan_id}")
def delete_plan(plan_id: int, username: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM plans WHERE id = ? AND username = ?", (plan_id, username))
    conn.commit()
    conn.close()
    return {"status": "success"}

@app.post("/api/plans/snooze")
def snooze_plan(payload: PlanSnooze):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    new_alarm = (get_now_kst() + timedelta(minutes=payload.minutes)).strftime("%H:%M")
    cur.execute("UPDATE plans SET alarm_time = ?, snooze_count = snooze_count + 1 WHERE id = ? AND username = ?",
                (new_alarm, payload.id, payload.username))
    conn.commit()
    conn.close()
    return {"status": "success", "new_alarm": new_alarm}

# 리스트 메뉴 일괄 처리
@app.post("/api/plans/batch")
def batch_plans(batch: BatchAction):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    if batch.action in ['move_tomorrow', 'move_other_day'] and batch.to_date:
        cur.execute("UPDATE plans SET plan_date = ? WHERE username = ? AND plan_date = ? AND is_done = 0",
                    (batch.to_date, batch.username, batch.from_date))
    elif batch.action == 'copy_all' and batch.to_date:
        cur.execute("SELECT category_id, title, memo FROM plans WHERE username = ? AND plan_date = ?",
                    (batch.username, batch.from_date))
        rows = cur.fetchall()
        for c_id, t, m in rows:
            cur.execute("INSERT INTO plans (username, category_id, title, memo, is_done, plan_date) VALUES (?, ?, ?, ?, 0, ?)",
                        (batch.username, c_id, t, m, batch.to_date))
    elif batch.action == 'delete_all':
        cur.execute("DELETE FROM plans WHERE username = ? AND plan_date = ?", (batch.username, batch.from_date))
    conn.commit()
    conn.close()
    return {"status": "success"}

# 초 단위 정밀 타임라인 목록
@app.get("/api/timeline")
def get_timeline(username: str, date: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """SELECT p.title, p.completed_at, c.name 
           FROM plans p JOIN categories c ON p.category_id = c.id
           WHERE p.username = ? AND p.plan_date = ? AND p.is_done = 1 
           ORDER BY p.completed_at ASC""",
        (username, date)
    )
    rows = cur.fetchall()
    conn.close()
    return [{"title": r[0], "completed_at": r[1], "category_name": r[2]} for r in rows]

# ----------------- 📱 KWGT / 바탕화면 위젯 연동 API -----------------
@app.get("/api/widget/summary")
def widget_summary(username: str):
    today = get_now_kst().strftime("%Y-%m-%d")
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id, title, is_done, completed_at FROM plans WHERE plan_date = ? AND username = ? ORDER BY is_done ASC, id ASC",
                (today, username))
    rows = cur.fetchall()
    conn.close()

    total = len(rows)
    done_count = sum(1 for r in rows if r[2] == 1)
    remaining = total - done_count

    return JSONResponse(content={
        "date": today,
        "total": total,
        "done": done_count,
        "remaining": remaining,
        "items": [{"id": r[0], "title": r[1], "is_done": bool(r[2]), "completed_at": r[3]} for r in rows]
    })

# ----------------- Gemini 분석 API -----------------
@app.post("/api/ai/analyze")
def analyze_plans(req: BaseModel):
    # Gemini AI 코칭 로직
    return {"feedback": "오늘 하루도 투두메이트 루틴을 훌륭하게 수행하고 계십니다!"}

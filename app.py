@app.route("/api/cafeteria/guide")
def api_cafeteria_guide():
    meal_type = request.args.get("meal", "lunch")  # lunch or dinner
    s = get_active_user_settings()
    w_code = get_today_weekday_code()
    cafeteria_table = s.get("weekly_cafeteria", {})
    day_menu = cafeteria_table.get(w_code, {}).get(meal_type, "")

    if not day_menu:
        return jsonify({"status": "error", "message": "오늘 요일의 식단표가 등록되지 않았습니다."}), 400

    row = get_today_row()
    row_dict = row_to_dict(row)
    targets = calculate_dynamic_targets(row_dict.get("weight"), s.get("user_height", 180))

    curr_cal = float(row_dict.get("calories") or 0)
    curr_pro = float(row_dict.get("protein") or 0)
    rem_cal = max(0, targets["calories"] - curr_cal)
    rem_pro = max(0, targets["protein"] - curr_pro)

    prompt = f"""
당신은 전문 영양사이자 다이어트 식단 코치입니다.
사용자 신체: 키 180cm, 체중 {row_dict.get('weight') or 90}kg.
오늘 남은 섭취 허용량: 칼로리 {rem_cal} kcal, 단백질 {rem_pro} g.
오늘 회사 구내식당 메뉴: "{day_menu}"

사용자가 배식대에서 식판에 음식을 담기 전에 보는 '메뉴별 권장 배식량 가이드'를 작성하세요.
각 메뉴를 어떻게 담아야(예: 밥 반 공기, 고기 듬뿍, 국물 건더기만 등) 다이어트와 근손실 방지에 최적인지 분석하여 아래 JSON 규격으로만 응답하세요.
반드시 중괄호 {{}} 로 둘러싸인 순수 JSON만 출력하세요.

{{
  "strategy": "오늘 영양 상태에 맞춘 한 줄 배식 전략 요약",
  "estimated_calories": 600,
  "estimated_protein": 35,
  "items": [
    {{
      "name": "메뉴명",
      "portion": "권장 배식량 (예: 1/2 공기, 듬뿍 1국자, 2~3점 등)",
      "tip": "섭취 팁 및 주의점 (나트륨 방지, 단백질 보충 등)"
    }}
  ]
}}
"""
    try:
        res = call_gemini_with_fallback(prompt)
        raw = res.text
        s_idx, e_idx = raw.find("{"), raw.rfind("}")
        parsed = json.loads(raw[s_idx:e_idx + 1])
        return jsonify({"status": "success", "guide": parsed, "menu_text": day_menu})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

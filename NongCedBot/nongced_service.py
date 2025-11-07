import os
import re
import math
import pandas as pd
from functools import lru_cache

# ---------- LLM (Gemini) ----------
USE_LLM = True
try:
    import google.generativeai as genai
    from prompt import PROMPT_WORKAW
    GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")
    if GOOGLE_API_KEY:
        genai.configure(api_key=GOOGLE_API_KEY)
        MODEL_NAME = os.getenv("GEMINI_MODEL_NAME", "gemini-2.0-flash")
        _gen_model = genai.GenerativeModel(
            model_name=MODEL_NAME,
            system_instruction=PROMPT_WORKAW,
            generation_config={
                "temperature": 0.1,
                "top_p": 0.95,
                "top_k": 64,
                "max_output_tokens": 1200,
                "response_mime_type": "text/plain",
            },
        )
    else:
        USE_LLM = False
except Exception as e:
    print("[nongced_service] Gemini init error:", e)
    USE_LLM = False

# ---------- Data config ----------
XLSX_PATH = os.getenv("NONGCED_XLSX_PATH", "./NongCedBotFull.xlsx")

COLMAP = {
    "subject_th": ["ชื่อวิชา (ไทย)","ชื่อวิชาไทย","ชื่อรายวิชา","ชื่อวิชา","วิชา","course_name","subject","Subject","subject_th"],
    "subject_en": ["ชื่อวิชา (อังกฤษ)","ชื่อรายวิชา (อังกฤษ)","ชื่อวิชาอังกฤษ","english name","English Name","course_name_en","subject_en","English Title","EN Title","English"],
    "code":       ["รหัสวิชา","รหัส","course_code","Code","code"],
    "credits":    ["จำนวนหน่วยกิตตลอดหลักสูตร","หน่วยกิต","credits","credit","หน่วยกิต(ทฤษฎี-ปฏิบัติ)","total_credits"],
    "category":   ["หมวดวิชา","หมวด","category","กลุ่มวิชา","วิชาบังคับหรือไม่","ประเภทวิชา"],
    "year":       ["ชั้นปี","ปี","year"],
    "semester":   ["ภาคการศึกษา","เทอม","semester","ภาค"],
    "degree_name":["ชื่อปริญญา","degree","degree_name","ชื่อปริญญาและสาขา"],
    "major_name": ["สาขาวิชา","major","major_name","สาขา","หลักสูตรสาขา"],
    "course_name":["ชื่อหลักสูตร","ชื่อหลักสูตร (TH)","program_name","course_title"],
    "description":["คำอธิบายรายวิชา","รายละเอียดรายวิชา","description","course_description","desc"],
}

CURRICULUM_ALIASES = {
    "ป.ตรี 4 ปี": ["ป.ตรี 4 ปี","หลักสูตรปริญญาตรี 4 ปี","ปริญญาตรี 4 ปี","ตรี 4 ปี","Bachelor 4","B.Ed. 4","4-year"],
    "ป.ตรี 3 ปี (เทียบโอน/TCT)": ["ป.ตรี 3 ปี","หลักสูตรปริญญาตรี 3 ปี","ต่อเนื่อง","ป.ตรีต่อเนื่อง","เทียบโอน","TCT","Bridge","Transfer"],
    "ป.โท": ["ป.โท","หลักสูตรปริญญาโท","Master","M.Ed."],
    "ป.เอก": ["ป.เอก","หลักสูตรปริญญาเอก","PhD","Doctoral","Doctorate"],
}

def _norm(s): return str(s).strip()

# ---------- Load & normalize Excel ----------
@lru_cache(maxsize=1)
def load_all_sheets():
    xls = pd.ExcelFile(XLSX_PATH)
    data = {}
    for sheet in xls.sheet_names:
        df = pd.read_excel(XLSX_PATH, sheet_name=sheet).copy()
        for std, cands in COLMAP.items():
            for c in list(df.columns):
                if _norm(c) in cands:
                    df.rename(columns={c: std}, inplace=True)
        for std in COLMAP.keys():
            if std not in df.columns:
                df[std] = None
        data[sheet] = df
    return data

# ให้ LLM เห็นข้อมูลเหมือน app.py
@lru_cache(maxsize=1)
def build_context_csv(max_rows_per_sheet: int = 300) -> str:
    data = load_all_sheets()
    parts = []
    for sheet, df in data.items():
        if df.empty:
            continue
        cols = [c for c in ["code","subject_th","subject_en","credits","category","year","semester","degree_name","major_name","course_name","description"] if c in df.columns]
        sub = df[cols].head(max_rows_per_sheet).copy()
        parts.append(f"# SHEET: {sheet}\n{sub.to_csv(index=False)}")
    return "\n\n".join(parts)

# ---------- State & helpers ----------
USER_CTX = {}   # user_id -> {"curriculum": "...", "sheet": "..."}
USER_CHAT = {}  # user_id -> chat session

def _reset_user_chat(user_id: str):
    USER_CHAT.pop(user_id, None)

def _get_user_chat(user_id: str):
    if not USE_LLM:
        return None
    chat = USER_CHAT.get(user_id)
    if chat is None:
        context_csv = build_context_csv()
        hint = ""
        if USER_CTX.get(user_id, {}).get("curriculum"):
            hint = f"\n\nCURRENT_CURRICULUM={USER_CTX[user_id]['curriculum']}"
        chat = _gen_model.start_chat(history=[
            {"role": "user", "parts": [{"text": f"## DATA CONTEXT\n{context_csv}{hint}"}]}
        ])
        USER_CHAT[user_id] = chat
    return chat

# ---------- Curriculum detection ----------
def _auto_match_curriculum(text: str):
    """exact match (ข้อความมีแต่ชื่อหลักสูตร)"""
    t = _norm(text)
    if not t:
        return None, None
    data = load_all_sheets()

    # ชื่อมาตรฐาน
    for std_name, aliases in CURRICULUM_ALIASES.items():
        if t == std_name:
            for alias in aliases:
                for s in data.keys():
                    if s.startswith(alias):
                        return std_name, s
            if t in data:
                return std_name, t

    # alias
    for std_name, aliases in CURRICULUM_ALIASES.items():
        for alias in aliases:
            if t == alias:
                for s in data.keys():
                    if s.startswith(alias):
                        return std_name, s

    # ชื่อชีตจริง
    for s in data.keys():
        if t == s:
            for std_name, aliases in CURRICULUM_ALIASES.items():
                if any(s.startswith(a) for a in aliases):
                    return std_name, s
            return s, s
    return None, None

def _extract_curriculum_from_text(text: str):
    """
    NEW: จับหลักสูตรที่ 'อยู่ในประโยค' เช่น 'ป.ตรี 4 ปี ชื่อหลักสูตรคือ'
    คืน (std_name, sheet) ถ้าพบ alias/คำพ้องเป็น substring
    """
    data = load_all_sheets()
    t = (text or "").lower()
    for std_name, aliases in CURRICULUM_ALIASES.items():
        for alias in aliases:
            if alias.lower() in t:
                # หา sheet ที่ขึ้นต้นด้วย alias (หรือมี alias เป็นส่วนหนึ่ง)
                for s in data.keys():
                    if s.lower().startswith(alias.lower()) or alias.lower() in s.lower():
                        return std_name, s
    return None, None

# ---------- Common utils ----------
def _col(df: pd.DataFrame, candidates) -> str | None:
    for c in candidates:
        if c in df.columns:
            return c
        for col in df.columns:
            if str(col).strip().lower() == str(c).strip().lower():
                return col
    return None

_NUMPAT = re.compile(r"(\d+(?:\.\d+)?)")
def _parse_credits(val) -> float:
    if val is None or (isinstance(val, float) and math.isnan(val)): return 0.0
    s = str(val).replace(",", ".")
    m = _NUMPAT.search(s)
    return float(m.group(1)) if m else 0.0

def _detect_total_credit_query(text: str) -> bool:
    t = (text or "").lower()
    keys = [
        "หน่วยกิตตลอดหลักสูตร","รวมหน่วยกิต","หน่วยกิตทั้งหมด",
        "ต้องเรียนกี่หน่วยกิต","กี่หน่วยกิตทั้งหมด","รวมทั้งหมดกี่หน่วยกิต","รวมหน่วยกิตทั้งหมด",
    ]
    return ("หน่วยกิต" in t and ("รวม" in t or "ทั้งหมด" in t or "ตลอด" in t)) or any(k in t for k in keys)

def _filter_sheets_by_hint(sheets, text):
    t = (text or "").lower()
    if not t: return sheets
    selected = []
    for s in sheets:
        sl = s.lower()
        if any(k in t for k in ["ป.ตรี 4","ตรี 4","4 ปี","bachelor","b.ed"]):
            if any(k in sl for k in ["ป.ตรี 4","ตรี 4","4 ปี","bachelor","b.ed"]):
                selected.append(s)
        if any(k in t for k in ["ป.ตรี 3","เทียบโอน","tct","ต่อเนื่อง","bridge"]):
            if any(k in sl for k in ["ป.ตรี 3","เทียบโอน","tct","ต่อเนื่อง","bridge"]):
                selected.append(s)
        if any(k in t for k in ["ป.โท","master","m.ed"]):
            if any(k in sl for k in ["ป.โท","master","m.ed"]):
                selected.append(s)
        if any(k in t for k in ["ป.เอก","phd","doctoral","เอก"]):
            if any(k in sl for k in ["ป.เอก","phd","doctoral"]):
                selected.append(s)
    return selected if selected else sheets

def _sum_all_curricula() -> str:
    data = load_all_sheets()
    lines, hit = [], False
    for sheet, df in data.items():
        if "credits" not in df.columns:
            continue
        total = float(df["credits"].apply(_parse_credits).sum())
        lines.append(f"• {sheet} : {total:.0f} หน่วยกิต")
        hit = True
    return ("หน่วยกิตตลอดหลักสูตร (ทุกหลักสูตร):\n" + "\n".join(lines)) if hit else "ไม่พบคอลัมน์หน่วยกิตในไฟล์"

def _find_year_sem(df, y, sem):
    sub = df.copy()
    if "year" in sub.columns and sub["year"].notna().any():
        sub = sub[sub["year"].astype(str) == str(y)]
    if "semester" in sub.columns and sub["semester"].notna().any():
        sub = sub[sub["semester"].astype(str) == str(sem)]
    return sub

def _format_rows_lines(rows, limit=50):
    lines = []
    for _, r in rows.head(limit).iterrows():
        th = r.get("subject_th") or "-"
        en = r.get("subject_en") or "-"
        code = r.get("code") or "-"
        cr = r.get("credits") or "-"
        cat = r.get("category") or "-"
        lines.append(f"  - {code} — {th} / {en} — {cr} หน่วยกิต — {cat}")
    return lines

# ---------- Public API ----------
def answer_with_nongced(user_id: str, text: str) -> str:
    """
    LLM-first (PROMPT_WORKAW + CSV context) + fallback กฎ
    รองรับกรณีที่ชื่อหลักสูตรอยู่ 'ในประโยค' เช่น 'ป.ตรี 4 ปี ชื่อหลักสูตรคือ' แล้วตอบทันที
    """
    t = _norm(text)
    data = load_all_sheets()

    # 0) เมนู
    if t in ["เมนู","help","ช่วยเหลือ","เริ่ม"]:
        return ("เริ่มได้เลย: พิมพ์ชื่อหลักสูตร เช่น ป.ตรี 4 ปี / ป.ตรี 3 ปี (เทียบโอน/TCT) / ป.โท / ป.เอก\n"
                "เช่น 'รายวิชาปี 1 เทอม 1', 'หน่วยกิตตลอดหลักสูตร', 'วิชา Data Mining กี่หน่วยกิต'")

    # 1) ถ้าไม่ตั้งหลักสูตร แต่ถาม 'รวมหน่วยกิต' → รวมทุกหลักสูตร
    if _detect_total_credit_query(t) and not USER_CTX.get(user_id, {}).get("sheet"):
        return _sum_all_curricula()

    # 2) ตั้งหลักสูตรจาก "ข้อความทั้งประโยค" (ใหม่)
    std_name, sheet = _auto_match_curriculum(t)
    if not (std_name and sheet):
        std_name, sheet = _extract_curriculum_from_text(t)
    if std_name and sheet:
        USER_CTX[user_id] = {"curriculum": std_name, "sheet": sheet}
        _reset_user_chat(user_id)  # ให้ LLM รับ hint ใหม่
        # ไม่ต้องตอบว่า "ตั้งหลักสูตรแล้ว" ถ้าประโยคมีคำถามต่อ ให้ไหลไปตอบทันที
    # ขณะนี้อาจมีแล้ว หรือยังไม่มี

    # 3) ถ้าถามปี/เทอม ขณะยังไม่ตั้งหลักสูตร → รวมทุกหลักสูตร
    m_ysem = re.search(r"ปี\s*(\d)\s*เทอม\s*(\d)", t)
    if m_ysem and not USER_CTX.get(user_id, {}).get("sheet"):
        y, sem = m_ysem.group(1), m_ysem.group(2)
        targets = _filter_sheets_by_hint(list(data.keys()), t)
        found_any = False
        out = [f"รายวิชาปี {y} เทอม {sem}:"]
        for s in targets:
            sub = _find_year_sem(data[s], y, sem)
            if sub.empty: 
                continue
            found_any = True
            out.append(f"• {s}")
            out.extend(_format_rows_lines(sub))
        return "\n".join(out) if found_any else "ไม่พบข้อมูลรายวิชาสำหรับปี/เทอมที่ระบุ"

    # 4) ถ้ายังไม่มีหลักสูตร และไม่เข้าเคสพิเศษด้านบน → แจ้งให้ระบุ
    u = USER_CTX.get(user_id, {})
    if not u.get("sheet"):
        return ("โปรดพิมพ์ชื่อหลักสูตรก่อน เช่น ป.ตรี 4 ปี / ป.ตรี 3 ปี (เทียบโอน/TCT) / ป.โท / ป.เอก\n"
                + list_supported_curricula())

    # 5) มีหลักสูตรแล้ว → เตรียม df ปัจจุบัน
    df = data[u["sheet"]]

    # ---------- LLM FIRST ----------
    if USE_LLM:
        try:
            chat = _get_user_chat(user_id)
            if chat:
                hint = f"(CURRENT_CURRICULUM={u['curriculum']}) " if u.get("curriculum") else ""
                resp = chat.send_message(hint + t)
                ans = (resp.text or "").strip()
                if ans:
                    return ans
        except Exception as e:
            print("[nongced_service] LLM error -> fallback:", e)

    # ---------- FALLBACK (กฎ) ----------
    # ชื่อหลักสูตร/ปริญญา/สาขา
    if any(k in t for k in ["ชื่อหลักสูตร","ชื่อปริญญา","ชื่อสาขา","ปริญญาและสาขา"]):
        base = f"{u['curriculum']} (ชีต: {u['sheet']})"
        cols = []
        for key in ["course_name","degree_name","major_name"]:
            if key in df.columns and df[key].notna().any():
                cols.append(key)
        if cols:
            lines = []
            for c in cols:
                values = sorted({str(x).strip() for x in df[c].dropna().astype(str) if str(x).strip()})
                if values:
                    head = {"course_name":"ชื่อหลักสูตร","degree_name":"ชื่อปริญญา","major_name":"สาขาวิชา"}.get(c, c)
                    lines.append(f"- {head}: " + ("; ".join(values[:10])))
            if lines:
                return base + "\n" + "\n".join(lines)
        return base

    # หน่วยกิตตลอดหลักสูตร (เฉพาะหลักสูตรปัจจุบัน)
    if _detect_total_credit_query(t):
        total = float(df["credits"].apply(_parse_credits).sum()) if "credits" in df.columns else 0.0
        return f"หน่วยกิตตลอดหลักสูตร ({u['curriculum']}): {total:.0f} หน่วยกิต"

    # ปี–เทอม
    if m_ysem:
        y, sem = m_ysem.group(1), m_ysem.group(2)
        sub = _find_year_sem(df, y, sem)
        if sub.empty:
            return "ไม่พบข้อมูลรายวิชาสำหรับปี/เทอมที่ระบุ"
        return "\n".join(_format_rows_lines(sub))

    # คำอธิบายรายวิชา
    if any(k in t for k in ["คำอธิบายรายวิชา","รายละเอียดรายวิชา","อธิบายรายวิชา","description"]):
        name = t.replace("คำอธิบายรายวิชา","").replace("รายละเอียดรายวิชา","").replace("description","").strip()
        cdesc = _col(df, COLMAP["description"])
        if not cdesc:
            return "ไฟล์นี้ไม่มีคอลัมน์คำอธิบายรายวิชา"
        sub = df.copy()
        if name:
            mask = False
            for col in ["subject_th","subject_en","code"]:
                if col in sub.columns:
                    msk = sub[col].fillna("").astype(str).str.contains(re.escape(name), case=False, na=False)
                    mask = (mask | msk) if isinstance(mask, pd.Series) else msk
            if isinstance(mask, pd.Series):
                sub = sub[mask]
        if sub.empty:
            return "ไม่พบข้อมูลคำอธิบายรายวิชา"
        lines = []
        for _, r in sub.head(25).iterrows():
            th = r.get("subject_th") or "-"
            en = r.get("subject_en") or "-"
            code = r.get("code") or "-"
            desc = r.get(cdesc) or "-"
            lines.append(f"{code} — {th} / {en}\n• {desc}")
        return "\n\n".join(lines)

    # วิชา ... กี่หน่วยกิต
    m = re.search(r"(วิชา|รายวิชา)\s+(.+?)\s+(กี่หน่วยกิต|หน่วยกิตเท่าไหร่)", t)
    if m:
        name = m.group(2).strip()
        rows = _search_subject(df, name)
        if rows.empty:
            return "ไม่พบข้อมูล"
        ans = []
        for _, r in rows.iterrows():
            th = r.get("subject_th") or "-"
            en = r.get("subject_en") or "-"
            code = r.get("code") or "-"
            cr = r.get("credits") or "-"
            ans.append(f"{code} — {th} / {en}: {cr} หน่วยกิต")
        return "\n".join(ans[:25])

    # รายวิชาแต่ละหมวดหมู่
    if any(k in t for k in ["บังคับ","ไม่บังคับ","เลือก","หมวด"]):
        kw = t.replace("รายวิชา","").strip()
        ccat = _col(df, COLMAP["category"]) or "category"
        sub = df[df[ccat].fillna("").astype(str).str.contains(kw, case=False, na=False)]
        if sub.empty:
            return "ไม่พบข้อมูล"
        return "\n".join(_format_rows_lines(sub))

    # ค้นหาโดยรวม
    rows = _search_subject(df, t)
    if not rows.empty:
        return "\n".join(_format_rows_lines(rows))

    return "ไม่พบข้อมูล"

# ---------- helpers ----------
def list_supported_curricula():
    sheets = load_all_sheets().keys()
    out = []
    for std_name, aliases in CURRICULUM_ALIASES.items():
        matched = [s for s in sheets if any(s.startswith(a) for a in aliases)]
        if matched:
            out.append(f"• {std_name} → {', '.join(matched)}")
    return "หลักสูตรที่รองรับ:\n" + ("\n".join(out) if out else "ไม่พบข้อมูล")

def _search_subject(df, text):
    q = (text or "").lower().strip()
    if not q: return pd.DataFrame()
    for col in ["subject_th","subject_en","code"]:
        if col not in df.columns: 
            continue
        vals = df[col].fillna("").astype(str).str.lower()
        hits = df[vals.str.contains(re.escape(q), na=False)]
        if not hits.empty:
            return hits
    return pd.DataFrame()

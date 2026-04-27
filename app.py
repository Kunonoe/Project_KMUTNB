import json
import os
import re
import sqlite3
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from functools import wraps
from pathlib import Path

import cv2
import fitz
import numpy as np
import pandas as pd
import pytesseract
import requests
from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "data" / "app.db"
UPLOADS_DIR = BASE_DIR / "uploads"
EXPORTS_DIR = BASE_DIR / "data" / "exports"

ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "pdf"}

app = Flask(__name__)
app.secret_key = "diagram-grader-secret-key"
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024 * 1024

if os.getenv("TESSERACT_CMD"):
    pytesseract.pytesseract.tesseract_cmd = os.getenv("TESSERACT_CMD")


def get_ocr_provider():
    return (os.getenv("OCR_PROVIDER") or "local").strip().lower()


def is_ocr_available():
    provider = get_ocr_provider()
    if provider == "ocrspace":
        return True

    try:
        _ = pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


# ----------------------------
# Database helpers
# ----------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    conn = get_db_connection()
    cur = conn.cursor()

    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS exams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            diagram_type TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_by INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS answer_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL,
            file_path TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            analysis_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (exam_id) REFERENCES exams(id)
        );

        CREATE TABLE IF NOT EXISTS rubrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL,
            rubric_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (exam_id) REFERENCES exams(id)
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            exam_id INTEGER NOT NULL,
            student_id TEXT,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            status TEXT NOT NULL,
            score_total REAL,
            score_component REAL,
            score_text REAL,
            score_structure REAL,
            feedback_json TEXT,
            result_json TEXT,
            overridden_score REAL,
            override_note TEXT,
            processed_at TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (exam_id) REFERENCES exams(id)
        );

        CREATE TABLE IF NOT EXISTS detections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            class_name TEXT NOT NULL,
            x INTEGER,
            y INTEGER,
            w INTEGER,
            h INTEGER,
            confidence REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (submission_id) REFERENCES submissions(id)
        );

        CREATE TABLE IF NOT EXISTS ocr_texts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            text_value TEXT,
            confidence REAL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (submission_id) REFERENCES submissions(id)
        );

        CREATE TABLE IF NOT EXISTS graphs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            node_count INTEGER,
            edge_count INTEGER,
            graph_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (submission_id) REFERENCES submissions(id)
        );

        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            total_score REAL,
            breakdown_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (submission_id) REFERENCES submissions(id)
        );

        CREATE TABLE IF NOT EXISTS feedback_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER NOT NULL,
            message TEXT NOT NULL,
            meta_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (submission_id) REFERENCES submissions(id)
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            actor_user_id INTEGER,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id INTEGER,
            payload_json TEXT,
            created_at TEXT NOT NULL
        );
        """
    )

    cur.execute("SELECT COUNT(*) AS c FROM users")
    if cur.fetchone()["c"] == 0:
        now = utc_now()
        users = [
            ("admin", generate_password_hash("admin123"), "admin", now),
            ("teacher", generate_password_hash("teacher123"), "teacher", now),
            ("ta", generate_password_hash("ta123"), "ta", now),
        ]
        cur.executemany(
            "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            users,
        )

    conn.commit()
    conn.close()


def utc_now():
    return datetime.utcnow().isoformat(timespec="seconds")


def get_sample_exam_templates():
    def with_structure_rules(rubric, default_counts):
        rubric["structure_rules"] = {
            "rectangles": {"required": int(default_counts.get("rectangles", 1)), "points": 25},
            "diamonds": {"required": int(default_counts.get("diamonds", 1)), "points": 25},
            "circles": {"required": int(default_counts.get("circles", 1)), "points": 25},
            "lines": {"required": int(default_counts.get("lines", 4)), "points": 25},
        }
        return rubric

    return [
        {
            "title": "ER Basics - Student Course Enrollment",
            "diagram_type": "er",
            "description": "ตรวจ Entity, Relationship, Cardinality ของระบบลงทะเบียนเรียน",
            "rubric": with_structure_rules(
                {"component_weight": 45, "text_weight": 30, "structure_weight": 25, "pass_score": 60},
                {"rectangles": 2, "diamonds": 1, "circles": 4, "lines": 4},
            ),
        },
        {
            "title": "ER Advanced - Hospital Appointment",
            "diagram_type": "er",
            "description": "ตรวจ Weak Entity, Keys และข้อกำหนดความสัมพันธ์ในระบบโรงพยาบาล",
            "rubric": with_structure_rules(
                {"component_weight": 40, "text_weight": 30, "structure_weight": 30, "pass_score": 65},
                {"rectangles": 3, "diamonds": 2, "circles": 4, "lines": 6},
            ),
        },
        {
            "title": "Flowchart Basics - Login Process",
            "diagram_type": "flowchart",
            "description": "ตรวจลำดับขั้นตอนการเข้าสู่ระบบและ decision branches",
            "rubric": with_structure_rules(
                {"component_weight": 35, "text_weight": 25, "structure_weight": 40, "pass_score": 60},
                {"rectangles": 2, "diamonds": 1, "circles": 2, "lines": 5},
            ),
        },
        {
            "title": "Flowchart Intermediate - Library Borrowing",
            "diagram_type": "flowchart",
            "description": "ตรวจเส้นทางการยืมหนังสือ เงื่อนไข และการวนลูปในงานห้องสมุด",
            "rubric": with_structure_rules(
                {"component_weight": 35, "text_weight": 30, "structure_weight": 35, "pass_score": 60},
                {"rectangles": 3, "diamonds": 1, "circles": 2, "lines": 6},
            ),
        },
        {
            "title": "Flowchart Advanced - E-commerce Order Pipeline",
            "diagram_type": "flowchart",
            "description": "ตรวจ process ตั้งแต่รับคำสั่งซื้อ ชำระเงิน แพ็กสินค้า จนจัดส่ง",
            "rubric": with_structure_rules(
                {"component_weight": 30, "text_weight": 30, "structure_weight": 40, "pass_score": 70},
                {"rectangles": 4, "diamonds": 2, "circles": 2, "lines": 8},
            ),
        },
    ]


def build_default_rubric(structure_counts=None):
    counts = structure_counts or {"rectangles": 1, "diamonds": 1, "circles": 1, "lines": 4}
    return {
        "component_weight": 40,
        "text_weight": 30,
        "structure_weight": 30,
        "pass_score": 60,
        "structure_rules": {
            "rectangles": {"required": int(counts.get("rectangles", 1)), "points": 25},
            "diamonds": {"required": int(counts.get("diamonds", 1)), "points": 25},
            "circles": {"required": int(counts.get("circles", 1)), "points": 25},
            "lines": {"required": int(counts.get("lines", 4)), "points": 25},
        },
        "criteria": [],
        "er_keywords": [],
    }


def infer_criterion_signal(criterion_name):
    name = (criterion_name or "").lower()
    mapping = [
        (("entity", "process", "rectangle", "สี่เหลี่ยม", "องค์ประกอบ"), "rectangles"),
        (("decision", "relationship", "diamond", "ความสัมพันธ์", "เงื่อนไข", "เพชร"), "diamonds"),
        (("attribute", "circle", "วงกลม", "แอตทริบิวต์"), "circles"),
        (("line", "arrow", "flow", "cardinality", "ลูกศร", "เส้น", "การไหล"), "lines"),
        (("text", "label", "ชื่อ", "ข้อความ", "ป้าย"), "text"),
        (("logic", "structure", "โครงสร้าง", "ตรรกะ", "ความเชื่อมโยง"), "logic"),
    ]

    for keywords, signal in mapping:
        if any(k in name for k in keywords):
            return signal
    return "overall"


# ----------------------------
# Auth helpers
# ----------------------------
def login_required(handler):
    @wraps(handler)
    def wrapper(*args, **kwargs):
        if "user_id" not in session:
            return jsonify({"error": "Unauthorized"}), 401
        return handler(*args, **kwargs)

    return wrapper


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    conn = get_db_connection()
    row = conn.execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ----------------------------
# Utility
# ----------------------------
def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def extract_student_id(filename):
    match = re.search(r"(\d{5,})", filename)
    if match:
        return match.group(1)
    return Path(filename).stem


def ensure_exam_dirs(exam_id):
    base = UPLOADS_DIR / f"exam_{exam_id}"
    key_dir = base / "answer_keys"
    sub_dir = base / "submissions"
    key_dir.mkdir(parents=True, exist_ok=True)
    sub_dir.mkdir(parents=True, exist_ok=True)
    return key_dir, sub_dir


def load_image_any(file_path):
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        doc = fitz.open(file_path)
        if doc.page_count == 0:
            raise ValueError("Empty PDF")
        page = doc[0]
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        else:
            img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
        return img

    img = cv2.imread(str(file_path))
    if img is None:
        raise ValueError("Cannot read image")
    return img


def preprocess_image(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    denoise = cv2.GaussianBlur(gray, (5, 5), 0)
    _, th = cv2.threshold(denoise, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    inverted = 255 - th
    return inverted


def detect_components(binary_img):
    contours, _ = cv2.findContours(binary_img, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    counts = {
        "rectangles": 0,
        "diamonds": 0,
        "circles": 0,
        "lines": 0,
    }
    detections = []

    for c in contours:
        area = cv2.contourArea(c)
        if area < 180:
            continue

        peri = cv2.arcLength(c, True)
        approx = cv2.approxPolyDP(c, 0.04 * peri, True)
        x, y, w, h = cv2.boundingRect(c)

        if len(approx) == 4:
            ratio = w / max(h, 1)
            if 0.7 <= ratio <= 1.3:
                counts["diamonds"] += 1
                class_name = "diamond"
            else:
                counts["rectangles"] += 1
                class_name = "rectangle"
        elif len(approx) >= 7:
            counts["circles"] += 1
            class_name = "circle"
        else:
            continue

        detections.append({
            "class_name": class_name,
            "x": int(x),
            "y": int(y),
            "w": int(w),
            "h": int(h),
            "confidence": round(min(0.99, 0.55 + area / 50000), 3),
        })

    lines = cv2.HoughLinesP(binary_img, 1, np.pi / 180, threshold=90, minLineLength=45, maxLineGap=8)
    if lines is not None:
        counts["lines"] = int(len(lines))

    return counts, detections


def extract_ocr_text(img):
    provider = get_ocr_provider()
    if provider == "ocrspace":
        return extract_ocr_text_ocrspace(img)

    if not is_ocr_available():
        return []

    try:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (3, 3), 0)
        _, thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        raw = pytesseract.image_to_data(
            thr,
            output_type=pytesseract.Output.DICT,
            config="--oem 3 --psm 6",
        )
    except Exception:
        return []

    texts = []
    for i, token in enumerate(raw.get("text", [])):
        txt = token.strip()
        if len(txt) < 2:
            continue
        conf = raw.get("conf", ["0"])[i]
        try:
            confidence = float(conf)
        except ValueError:
            confidence = 0.0
        if confidence < 25:
            continue
        texts.append({"text": txt, "confidence": confidence})

    return texts


def extract_ocr_text_ocrspace(img):
    api_key = os.getenv("OCRSPACE_API_KEY", "helloworld")
    api_url = os.getenv("OCRSPACE_API_URL", "https://api.ocr.space/parse/image")

    try:
        ok, encoded = cv2.imencode(".png", img)
        if not ok:
            return []

        files = {"filename": ("image.png", encoded.tobytes(), "image/png")}
        payload = {
            "apikey": api_key,
            "language": "eng",
            "isOverlayRequired": True,
            "OCREngine": 2,
        }
        res = requests.post(api_url, data=payload, files=files, timeout=30)
        if res.status_code >= 400:
            return []

        data = res.json()
        if data.get("IsErroredOnProcessing"):
            return []

        parsed_list = data.get("ParsedResults") or []
        if not parsed_list:
            return []

        parsed = parsed_list[0]
        text_overlay = parsed.get("TextOverlay") or {}
        lines = text_overlay.get("Lines") or []

        texts = []
        for ln in lines:
            for w in ln.get("Words", []):
                token = (w.get("WordText") or "").strip()
                if len(token) < 2:
                    continue
                texts.append({"text": token, "confidence": float(w.get("Confidence") or 70.0)})

        if texts:
            return texts

        # Fallback when overlay words are not available.
        parsed_text = (parsed.get("ParsedText") or "").strip()
        if not parsed_text:
            return []

        fallback = []
        for token in re.split(r"\s+", parsed_text):
            clean = token.strip()
            if len(clean) >= 2:
                fallback.append({"text": clean, "confidence": 65.0})
        return fallback
    except Exception:
        return []


def build_graph_summary(component_counts):
    nodes = int(component_counts["rectangles"] + component_counts["diamonds"] + component_counts["circles"])
    edges = int(component_counts["lines"])
    graph = {
        "node_count": nodes,
        "edge_count": edges,
        "kind": "diagram_graph",
    }
    return graph


def normalize_tokens(text_items):
    tokens = []
    for item in text_items:
        token = re.sub(r"[^a-zA-Z0-9ก-๙_]", "", item["text"]).lower()
        if len(token) >= 2:
            tokens.append(token)
    return sorted(set(tokens))


def normalize_keyword(value):
    if value is None:
        return ""
    return re.sub(r"[^a-zA-Z0-9ก-๙_]", "", str(value)).lower().strip()


def build_er_keywords_from_answer(analysis, diagram_type):
    if diagram_type != "er":
        return [], "not-er"

    raw_texts = analysis.get("texts") or []
    comp = analysis.get("components") or {}

    # Keep OCR order, deduplicate by normalized token, and auto-fill as editable checklist rows.
    seen = set()
    normalized_tokens = []
    for item in raw_texts:
        norm = normalize_keyword(item.get("text"))
        if len(norm) < 2:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        normalized_tokens.append(norm)

    entity_budget = max(1, int(comp.get("rectangles", 0)))
    relation_budget = max(1, int(comp.get("diamonds", 0)))

    items = []
    if normalized_tokens:
        for idx, token in enumerate(normalized_tokens):
            if idx < entity_budget:
                topic = "entity"
            elif idx < entity_budget + relation_budget:
                topic = "relationship"
            else:
                topic = "attribute"

            items.append(
                {
                    "topic": topic,
                    "expected_text": token,
                    "points": 10.0,
                    "critical": False,
                }
            )
        return items, "ocr"

    # OCR fallback: create editable placeholders from detected ER structure counts.
    placeholder_items = []
    entity_count = max(1, int(comp.get("rectangles", 0)))
    relationship_count = max(1, int(comp.get("diamonds", 0)))
    attribute_count = max(1, int(comp.get("circles", 0)))

    for i in range(min(entity_count, 6)):
        placeholder_items.append(
            {
                "topic": "entity",
                "expected_text": f"entity_{i + 1}",
                "points": 10.0,
                "critical": False,
            }
        )
    for i in range(min(relationship_count, 6)):
        placeholder_items.append(
            {
                "topic": "relationship",
                "expected_text": f"relationship_{i + 1}",
                "points": 10.0,
                "critical": False,
            }
        )
    for i in range(min(attribute_count, 8)):
        placeholder_items.append(
            {
                "topic": "attribute",
                "expected_text": f"attribute_{i + 1}",
                "points": 5.0,
                "critical": False,
            }
        )

    return placeholder_items, "placeholder"


def score_er_keyword_checklist(items, submission_tokens, diagram_type):
    if diagram_type != "er" or not items:
        return {
            "er_keyword_score": None,
            "er_keyword_results": [],
            "er_keyword_critical_failed": 0,
        }

    token_set = set(submission_tokens)
    total_points = 0.0
    earned_points = 0.0
    critical_failed = 0
    results = []

    for item in items:
        if not isinstance(item, dict):
            continue

        topic = (item.get("topic") or "entity").strip().lower()
        expected_text = (item.get("expected_text") or "").strip()
        points = float(item.get("points", 0) or 0)
        critical = bool(item.get("critical", False))

        if not expected_text or points <= 0:
            continue

        normalized_candidates = []
        for raw in re.split(r"[,;|/]", expected_text):
            token = normalize_keyword(raw)
            if token:
                normalized_candidates.append(token)

        if not normalized_candidates:
            continue

        total_points += points

        matched = False
        matched_text = ""
        match_ratio = 0.0

        # Exact token hit has priority.
        for cand in normalized_candidates:
            if cand in token_set:
                matched = True
                matched_text = cand
                match_ratio = 1.0
                break

        # OCR noise fallback: allow close spelling match.
        if not matched:
            best_ratio = 0.0
            best_pair = ("", "")
            for cand in normalized_candidates:
                for found in token_set:
                    ratio = SequenceMatcher(None, cand, found).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_pair = (cand, found)
            if best_ratio >= 0.84:
                matched = True
                matched_text = best_pair[1]
                match_ratio = best_ratio

        earned = points if matched else 0.0
        earned_points += earned

        if critical and not matched:
            critical_failed += 1

        results.append(
            {
                "topic": topic,
                "expected_text": expected_text,
                "points": round(points, 2),
                "earned": round(earned, 2),
                "matched": matched,
                "matched_text": matched_text,
                "match_ratio": round(match_ratio, 4),
                "critical": critical,
                "status": "matched" if matched else "missing",
                "note": (f"เจอคำว่า {matched_text}" if matched else "ไม่พบคำที่กำหนด"),
            }
        )

    score = None
    if total_points > 0:
        score = (earned_points / total_points) * 100

    return {
        "er_keyword_score": round(score, 2) if score is not None else None,
        "er_keyword_results": results,
        "er_keyword_critical_failed": critical_failed,
    }


def score_rubric_criteria(criteria, expected_counts, submission_components, component_score, structure_score, text_score):
    if not criteria:
        return {
            "criteria_score": None,
            "criteria_results": [],
            "critical_failed": 0,
        }

    criteria_results = []
    auto_points_total = 0.0
    auto_points_earned = 0.0
    critical_failed = 0

    for item in criteria:
        name = (item.get("name") or "หัวข้อไม่ระบุ").strip()
        points = float(item.get("points", 0) or 0)
        mode = (item.get("mode") or "auto").lower()
        critical = bool(item.get("critical", False))
        signal = infer_criterion_signal(name)

        ratio = None
        status = "pending-manual"
        note = "ต้องตรวจโดยอาจารย์"

        if mode in {"auto", "hybrid"}:
            if signal in {"rectangles", "diamonds", "circles", "lines"}:
                expected = int(expected_counts.get(signal, 0))
                found = int(submission_components.get(signal, 0))
                ratio = max(0.0, 1.0 - abs(expected - found) / max(expected, 1))
                note = f"{signal}: expected {expected}, found {found}"
            elif signal == "text":
                ratio = max(0.0, min(1.0, text_score / 100.0))
                note = "อ้างอิงคะแนน OCR/Text"
            elif signal == "logic":
                ratio = max(0.0, min(1.0, structure_score / 100.0))
                note = "อ้างอิงคะแนนโครงสร้าง/ตรรกะ"
            else:
                blended = (component_score * 0.4 + structure_score * 0.4 + text_score * 0.2) / 100.0
                ratio = max(0.0, min(1.0, blended))
                note = "อ้างอิงภาพรวม"

            earned = points * ratio
            auto_points_total += points
            auto_points_earned += earned
            status = "scored"

            if critical and ratio < 0.60:
                critical_failed += 1

            criteria_results.append(
                {
                    "name": name,
                    "mode": mode,
                    "critical": critical,
                    "signal": signal,
                    "points": round(points, 2),
                    "earned": round(earned, 2),
                    "ratio": round(ratio, 4),
                    "status": status,
                    "note": note,
                }
            )
        else:
            criteria_results.append(
                {
                    "name": name,
                    "mode": mode,
                    "critical": critical,
                    "signal": signal,
                    "points": round(points, 2),
                    "earned": None,
                    "ratio": None,
                    "status": status,
                    "note": note,
                }
            )

    criteria_score = None
    if auto_points_total > 0:
        criteria_score = (auto_points_earned / auto_points_total) * 100

    return {
        "criteria_score": round(criteria_score, 2) if criteria_score is not None else None,
        "criteria_results": criteria_results,
        "critical_failed": critical_failed,
    }


def score_submission(answer_analysis, submission_analysis, rubric, diagram_type):
    a_comp = answer_analysis["components"]
    s_comp = submission_analysis["components"]

    structure_rules = rubric.get("structure_rules") or {}
    expected_counts = {
        "rectangles": int((structure_rules.get("rectangles") or {}).get("required", a_comp.get("rectangles", 0))),
        "diamonds": int((structure_rules.get("diamonds") or {}).get("required", a_comp.get("diamonds", 0))),
        "circles": int((structure_rules.get("circles") or {}).get("required", a_comp.get("circles", 0))),
        "lines": int((structure_rules.get("lines") or {}).get("required", a_comp.get("lines", 0))),
    }

    metric_weights = {
        "er": {"rectangles": 0.30, "diamonds": 0.30, "circles": 0.30, "lines": 0.10},
        "flowchart": {"rectangles": 0.35, "diamonds": 0.25, "circles": 0.15, "lines": 0.25},
    }
    active_weights = metric_weights.get(diagram_type, metric_weights["flowchart"])

    component_weighted = 0.0
    feedback = []

    for key, m_weight in active_weights.items():
        a_val = expected_counts.get(key, a_comp.get(key, 0))
        s_val = s_comp.get(key, 0)
        diff = abs(a_val - s_val)
        score = max(0.0, 1.0 - (diff / max(a_val, 1)))
        component_weighted += score * m_weight

        if s_val < a_val:
            feedback.append(f"ขาดองค์ประกอบ {key}: พบ {s_val} จากที่คาด {a_val}")
        elif s_val > a_val + 1:
            feedback.append(f"มีองค์ประกอบ {key} มากกว่าคาด: พบ {s_val} จากที่คาด {a_val}")

    component_score = component_weighted * 100

    # Strong type consistency penalty to avoid Flowchart scoring high on ER keys and vice versa.
    type_penalty = 0.0
    if diagram_type == "er":
        if s_comp.get("diamonds", 0) == 0:
            type_penalty += 18
            feedback.append("ลักษณะงานคล้ายไม่ใช่ ER: ไม่พบ relationship (diamond)")
        if s_comp.get("circles", 0) == 0:
            type_penalty += 14
            feedback.append("ลักษณะงานคล้ายไม่ใช่ ER: ไม่พบ attribute (circle)")
    elif diagram_type == "flowchart":
        if s_comp.get("lines", 0) < 2:
            type_penalty += 15
            feedback.append("ลักษณะงานคล้ายไม่ใช่ Flowchart: เส้นทางการไหลไม่เพียงพอ")
        if s_comp.get("diamonds", 0) == 0:
            type_penalty += 12
            feedback.append("ลักษณะงานคล้ายไม่ใช่ Flowchart: ไม่พบ decision (diamond)")

    answer_tokens = normalize_tokens(answer_analysis["texts"])
    submission_tokens = normalize_tokens(submission_analysis["texts"])

    answer_text = " ".join(answer_tokens)
    submission_text = " ".join(submission_tokens)

    if answer_text and submission_text:
        seq_ratio = SequenceMatcher(None, answer_text, submission_text).ratio()
        answer_set = set(answer_tokens)
        submission_set = set(submission_tokens)
        overlap = len(answer_set.intersection(submission_set))
        recall = overlap / max(len(answer_set), 1)
        precision = overlap / max(len(submission_set), 1)
        token_f1 = 0.0 if (precision + recall) == 0 else (2 * precision * recall / (precision + recall))
        text_score = ((seq_ratio * 0.45) + (token_f1 * 0.55)) * 100
        feedback.append(f"OCR จับคำได้ {len(submission_tokens)} token, ตรงเฉลย {overlap} token")
    elif not answer_text and not submission_text:
        text_score = 60.0
        feedback.append("OCR ไม่พบข้อความทั้งเฉลยและคำตอบ ระบบใช้คะแนนข้อความแบบกลาง")
    else:
        text_score = 15.0
        feedback.append("OCR ฝั่งหนึ่งไม่ครบ ทำให้คะแนนข้อความลดลงมาก")

    a_graph = answer_analysis["graph"]
    s_graph = submission_analysis["graph"]

    # Structure score from teacher-defined required counts and points per structure.
    structure_points_total = 0.0
    structure_points_earned = 0.0
    for key in ["rectangles", "diamonds", "circles", "lines"]:
        expected = expected_counts.get(key, 0)
        found = int(s_comp.get(key, 0))
        point_item = float((structure_rules.get(key) or {}).get("points", 25))

        ratio = max(0.0, 1.0 - abs(expected - found) / max(expected, 1))
        structure_points_total += point_item
        structure_points_earned += point_item * ratio

        if ratio < 1.0:
            feedback.append(f"โครงสร้าง {key}: ต้องมี {expected}, พบ {found}, ได้ {round(point_item * ratio, 2)}/{point_item} คะแนน")

    if structure_points_total > 0:
        structure_score = (structure_points_earned / structure_points_total) * 100
    else:
        node_ratio = max(0.0, 1.0 - abs(a_graph["node_count"] - s_graph["node_count"]) / max(a_graph["node_count"], 1))
        edge_ratio = max(0.0, 1.0 - abs(a_graph["edge_count"] - s_graph["edge_count"]) / max(a_graph["edge_count"], 1))
        structure_score = ((node_ratio + edge_ratio) / 2) * 100

    w_component = float(rubric.get("component_weight", 40))
    w_text = float(rubric.get("text_weight", 30))
    w_structure = float(rubric.get("structure_weight", 30))

    total_weight = w_component + w_text + w_structure
    if total_weight <= 0:
        w_component, w_text, w_structure = 40, 30, 30
        total_weight = 100

    base_total_score = (
        component_score * (w_component / total_weight)
        + text_score * (w_text / total_weight)
        + structure_score * (w_structure / total_weight)
    )

    criteria_info = {
        "criteria_score": None,
        "criteria_results": [],
        "critical_failed": 0,
    }

    er_keyword_info = score_er_keyword_checklist(
        rubric.get("er_keywords") or [],
        submission_tokens,
        diagram_type,
    )

    total_score = base_total_score

    if er_keyword_info["er_keyword_score"] is not None:
        # ER mode: keyword checklist is the primary grading source.
        if diagram_type == "er":
            total_score = er_keyword_info["er_keyword_score"]
        else:
            total_score = total_score * 0.25 + er_keyword_info["er_keyword_score"] * 0.75
        feedback.append(
            f"ER Checklist: ได้ {er_keyword_info['er_keyword_score']:.2f}/100 "
            f"จาก {len(er_keyword_info['er_keyword_results'])} หัวข้อ"
        )

    total_score = max(0.0, total_score - type_penalty)

    if er_keyword_info["er_keyword_critical_failed"] > 0:
        total_score = min(total_score, 59.0)
        feedback.append("มีหัวข้อ Critical ใน ER Checklist ไม่ผ่าน คะแนนรวมถูกจำกัดไม่เกิน 59")

    if total_score >= 85:
        feedback.insert(0, "ภาพรวมดีมาก โครงสร้างใกล้เคียงเฉลย")
    elif total_score >= 65:
        feedback.insert(0, "ผลตรวจผ่านเกณฑ์พื้นฐาน แต่ยังมีจุดที่ควรปรับ")
    else:
        feedback.insert(0, "ควรทบทวนองค์ประกอบหลักและความครบถ้วนของแผนภาพ")

    return {
        "total_score": round(total_score, 2),
        "component_score": round(component_score, 2),
        "text_score": round(text_score, 2),
        "structure_score": round(structure_score, 2),
        "type_penalty": round(type_penalty, 2),
        "criteria_score": criteria_info["criteria_score"],
        "criteria_results": criteria_info["criteria_results"],
        "critical_failed": criteria_info["critical_failed"],
        "er_keyword_score": er_keyword_info["er_keyword_score"],
        "er_keyword_results": er_keyword_info["er_keyword_results"],
        "er_keyword_critical_failed": er_keyword_info["er_keyword_critical_failed"],
        "ocr_available": is_ocr_available(),
        "ocr_provider": get_ocr_provider(),
        "ocr_token_count": len(submission_tokens),
        "structure_points_total": round(structure_points_total, 2),
        "structure_points_earned": round(structure_points_earned, 2),
        "expected_counts": expected_counts,
        "feedback": feedback[:8],
    }


def analyze_diagram(file_path):
    image = load_image_any(file_path)
    pre = preprocess_image(image)
    comp, detections = detect_components(pre)
    texts = extract_ocr_text(image)
    graph = build_graph_summary(comp)

    return {
        "components": comp,
        "detections": detections,
        "texts": texts,
        "graph": graph,
        "ocr_available": is_ocr_available(),
        "ocr_provider": get_ocr_provider(),
    }


# ----------------------------
# Pages
# ----------------------------
@app.get("/")
def home_page():
    user = get_current_user()
    return render_template("index.html", user=user)


# ----------------------------
# Auth endpoints
# ----------------------------
@app.post("/auth/login")
def login():
    payload = request.get_json(silent=True) or request.form
    username = (payload.get("username") or "").strip()
    password = payload.get("password") or ""

    conn = get_db_connection()
    row = conn.execute("SELECT id, username, password_hash, role FROM users WHERE username = ?", (username,)).fetchone()
    conn.close()

    if not row or not check_password_hash(row["password_hash"], password):
        return jsonify({"error": "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"}), 401

    session["user_id"] = row["id"]
    return jsonify({"message": "ok", "user": {"id": row["id"], "username": row["username"], "role": row["role"]}})


@app.post("/auth/logout")
def logout():
    session.clear()
    return jsonify({"message": "ok"})


@app.get("/auth/me")
def me():
    user = get_current_user()
    if not user:
        return jsonify({"user": None})
    return jsonify({"user": user})


# ----------------------------
# Exam APIs
# ----------------------------
@app.route("/exams", methods=["GET", "POST"])
@login_required
def exams():
    conn = get_db_connection()

    if request.method == "POST":
        payload = request.get_json(silent=True) or request.form
        title = (payload.get("title") or "").strip()
        diagram_type = (payload.get("diagram_type") or "flowchart").strip().lower()
        description = (payload.get("description") or "").strip()

        if not title:
            conn.close()
            return jsonify({"error": "title is required"}), 400

        now = utc_now()
        cur = conn.execute(
            """
            INSERT INTO exams (title, diagram_type, description, status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, 'draft', ?, ?, ?)
            """,
            (title, diagram_type, description, session["user_id"], now, now),
        )
        exam_id = cur.lastrowid

        default_rubric = build_default_rubric()
        conn.execute(
            "INSERT INTO rubrics (exam_id, rubric_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (exam_id, json.dumps(default_rubric), now, now),
        )

        conn.commit()
        conn.close()
        return jsonify({"message": "created", "exam_id": exam_id})

    rows = conn.execute(
        """
        SELECT e.id, e.title, e.diagram_type, e.description, e.status, e.created_at,
               ak.id AS answer_key_id,
               (SELECT COUNT(*) FROM submissions s WHERE s.exam_id = e.id) AS submission_count
        FROM exams e
        LEFT JOIN answer_keys ak ON ak.exam_id = e.id
        ORDER BY e.id DESC
        """
    ).fetchall()
    conn.close()

    return jsonify({"items": [dict(r) for r in rows]})


@app.post("/seed/sample-exams")
@login_required
def seed_sample_exams():
    templates = get_sample_exam_templates()
    now = utc_now()
    conn = get_db_connection()

    created = []
    skipped = []

    for item in templates:
        exists = conn.execute(
            "SELECT id FROM exams WHERE title = ? AND diagram_type = ? LIMIT 1",
            (item["title"], item["diagram_type"]),
        ).fetchone()

        if exists:
            skipped.append({"id": exists["id"], "title": item["title"]})
            continue

        cur = conn.execute(
            """
            INSERT INTO exams (title, diagram_type, description, status, created_by, created_at, updated_at)
            VALUES (?, ?, ?, 'published', ?, ?, ?)
            """,
            (item["title"], item["diagram_type"], item["description"], session["user_id"], now, now),
        )
        exam_id = cur.lastrowid
        conn.execute(
            "INSERT INTO rubrics (exam_id, rubric_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (exam_id, json.dumps(item["rubric"]), now, now),
        )
        created.append({"id": exam_id, "title": item["title"]})

    conn.commit()
    conn.close()

    return jsonify(
        {
            "message": "sample exams processed",
            "created_count": len(created),
            "skipped_count": len(skipped),
            "created": created,
            "skipped": skipped,
        }
    )


@app.post("/exams/<int:exam_id>/answer-key")
@login_required
def upload_answer_key(exam_id):
    if "file" not in request.files:
        return jsonify({"error": "file is required"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "file is required"}), 400
    if not allowed_file(file.filename):
        return jsonify({"error": "รองรับเฉพาะ jpg jpeg png pdf"}), 400

    key_dir, _ = ensure_exam_dirs(exam_id)
    ext = Path(file.filename).suffix.lower()
    filename = secure_filename(f"answer_key_{uuid.uuid4().hex[:10]}{ext}")
    file_path = key_dir / filename
    file.save(file_path)

    analysis = analyze_diagram(str(file_path))

    conn = get_db_connection()
    row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM answer_keys WHERE exam_id = ?", (exam_id,)).fetchone()
    version = row["v"] + 1
    now = utc_now()
    conn.execute(
        "INSERT INTO answer_keys (exam_id, file_path, version, analysis_json, created_at) VALUES (?, ?, ?, ?, ?)",
        (exam_id, str(file_path), version, json.dumps(analysis), now),
    )

    exam_row = conn.execute("SELECT diagram_type FROM exams WHERE id = ?", (exam_id,)).fetchone()
    diagram_type = (exam_row["diagram_type"] if exam_row else "flowchart").lower()

    # Sync internal structure counts from latest answer key.
    structure_counts = analysis.get("components") or {}
    rubric_row = conn.execute("SELECT rubric_json FROM rubrics WHERE exam_id = ?", (exam_id,)).fetchone()
    rubric_obj = json.loads(rubric_row["rubric_json"]) if rubric_row else build_default_rubric(structure_counts)

    structure_rules = rubric_obj.get("structure_rules") or {}
    for key in ["rectangles", "diamonds", "circles", "lines"]:
        old_item = structure_rules.get(key) or {}
        structure_rules[key] = {
            "required": int(structure_counts.get(key, old_item.get("required", 0))),
            "points": float(old_item.get("points", 25)),
        }
    rubric_obj["structure_rules"] = structure_rules
    rubric_obj["criteria"] = []
    generated_er_keywords, generated_source = build_er_keywords_from_answer(analysis, diagram_type)
    rubric_obj["er_keywords"] = generated_er_keywords

    if rubric_row:
        conn.execute("UPDATE rubrics SET rubric_json = ?, updated_at = ? WHERE exam_id = ?", (json.dumps(rubric_obj), now, exam_id))
    else:
        conn.execute(
            "INSERT INTO rubrics (exam_id, rubric_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (exam_id, json.dumps(rubric_obj), now, now),
        )

    conn.execute("UPDATE exams SET updated_at = ? WHERE id = ?", (now, exam_id))
    conn.commit()
    conn.close()

    return jsonify(
        {
            "message": "answer key uploaded",
            "version": version,
            "analysis": analysis["components"],
            "rubric": rubric_obj,
            "auto_er_keyword_count": len(generated_er_keywords),
            "auto_er_keyword_source": generated_source,
        }
    )


@app.post("/exams/<int:exam_id>/rubric")
@login_required
def save_rubric(exam_id):
    payload = request.get_json(silent=True) or request.form
    conn = get_db_connection()
    existing_row = conn.execute("SELECT rubric_json FROM rubrics WHERE exam_id = ?", (exam_id,)).fetchone()
    existing_rubric = json.loads(existing_row["rubric_json"]) if existing_row else build_default_rubric()
    structure_rules = existing_rubric.get("structure_rules") or build_default_rubric().get("structure_rules")

    er_keywords = payload.get("er_keywords", [])
    if isinstance(er_keywords, str):
        try:
            er_keywords = json.loads(er_keywords)
        except Exception:
            er_keywords = []

    normalized_er_keywords = []
    for item in er_keywords:
        if not isinstance(item, dict):
            continue

        expected_text = (item.get("expected_text") or "").strip()
        if not expected_text:
            continue

        normalized_er_keywords.append(
            {
                "topic": (item.get("topic") or "entity").strip().lower(),
                "expected_text": expected_text,
                "points": float(item.get("points", 0) or 0),
                "critical": bool(item.get("critical", False)),
            }
        )

    rubric = {
        "component_weight": float(payload.get("component_weight", 40)),
        "text_weight": float(payload.get("text_weight", 30)),
        "structure_weight": float(payload.get("structure_weight", 30)),
        "pass_score": float(payload.get("pass_score", 60)),
        "structure_rules": structure_rules,
        "criteria": [],
        "er_keywords": normalized_er_keywords,
    }

    now = utc_now()
    if existing_row:
        conn.execute(
            "UPDATE rubrics SET rubric_json = ?, updated_at = ? WHERE exam_id = ?",
            (json.dumps(rubric), now, exam_id),
        )
    else:
        conn.execute(
            "INSERT INTO rubrics (exam_id, rubric_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (exam_id, json.dumps(rubric), now, now),
        )
    conn.commit()
    conn.close()

    return jsonify({"message": "rubric saved", "rubric": rubric})


@app.get("/exams/<int:exam_id>/rubric")
@login_required
def get_rubric(exam_id):
    conn = get_db_connection()
    row = conn.execute("SELECT rubric_json FROM rubrics WHERE exam_id = ?", (exam_id,)).fetchone()
    conn.close()

    if not row:
        return jsonify({"rubric": build_default_rubric()})

    rubric = json.loads(row["rubric_json"])
    if not rubric.get("structure_rules"):
        rubric["structure_rules"] = build_default_rubric().get("structure_rules")
    rubric["criteria"] = []
    if rubric.get("er_keywords") is None:
        rubric["er_keywords"] = []

    return jsonify({"rubric": rubric})


@app.post("/exams/<int:exam_id>/submissions/batch")
@login_required
def upload_submissions_batch(exam_id):
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "กรุณาเลือกไฟล์อย่างน้อย 1 ไฟล์"}), 400

    conn = get_db_connection()
    answer_key_row = conn.execute(
        "SELECT analysis_json FROM answer_keys WHERE exam_id = ? ORDER BY version DESC LIMIT 1",
        (exam_id,),
    ).fetchone()
    exam_row = conn.execute("SELECT diagram_type FROM exams WHERE id = ?", (exam_id,)).fetchone()
    if not answer_key_row:
        conn.close()
        return jsonify({"error": "ยังไม่มี Answer Key สำหรับข้อสอบนี้"}), 400
    if not exam_row:
        conn.close()
        return jsonify({"error": "ไม่พบข้อสอบ"}), 404

    rubric_row = conn.execute("SELECT rubric_json FROM rubrics WHERE exam_id = ?", (exam_id,)).fetchone()
    rubric = json.loads(rubric_row["rubric_json"]) if rubric_row else build_default_rubric()
    diagram_type = exam_row["diagram_type"]

    answer_analysis = json.loads(answer_key_row["analysis_json"])
    _, sub_dir = ensure_exam_dirs(exam_id)

    now = utc_now()
    results = []

    for file in files:
        if not file or file.filename == "":
            continue
        if not allowed_file(file.filename):
            results.append({"file": file.filename, "status": "failed", "reason": "unsupported extension"})
            continue

        ext = Path(file.filename).suffix.lower()
        safe_name = secure_filename(file.filename)
        save_name = f"sub_{uuid.uuid4().hex[:8]}_{safe_name}"
        file_path = sub_dir / save_name
        file.save(file_path)

        student_id = extract_student_id(file.filename)

        cur = conn.execute(
            """
            INSERT INTO submissions (
                exam_id, student_id, file_name, file_path, status, created_at
            ) VALUES (?, ?, ?, ?, 'processing', ?)
            """,
            (exam_id, student_id, file.filename, str(file_path), now),
        )
        submission_id = cur.lastrowid

        try:
            submission_analysis = analyze_diagram(str(file_path))
            scoring = score_submission(answer_analysis, submission_analysis, rubric, diagram_type)

            conn.execute(
                """
                UPDATE submissions
                SET status = 'done',
                    score_total = ?,
                    score_component = ?,
                    score_text = ?,
                    score_structure = ?,
                    feedback_json = ?,
                    result_json = ?,
                    processed_at = ?
                WHERE id = ?
                """,
                (
                    scoring["total_score"],
                    scoring["component_score"],
                    scoring["text_score"],
                    scoring["structure_score"],
                    json.dumps(scoring["feedback"], ensure_ascii=False),
                    json.dumps(submission_analysis),
                    utc_now(),
                    submission_id,
                ),
            )

            for d in submission_analysis["detections"]:
                conn.execute(
                    """
                    INSERT INTO detections (submission_id, class_name, x, y, w, h, confidence, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (submission_id, d["class_name"], d["x"], d["y"], d["w"], d["h"], d["confidence"], utc_now()),
                )

            for txt in submission_analysis["texts"]:
                conn.execute(
                    "INSERT INTO ocr_texts (submission_id, text_value, confidence, created_at) VALUES (?, ?, ?, ?)",
                    (submission_id, txt["text"], txt["confidence"], utc_now()),
                )

            conn.execute(
                "INSERT INTO graphs (submission_id, node_count, edge_count, graph_json, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    submission_id,
                    submission_analysis["graph"]["node_count"],
                    submission_analysis["graph"]["edge_count"],
                    json.dumps(submission_analysis["graph"]),
                    utc_now(),
                ),
            )

            conn.execute(
                "INSERT INTO scores (submission_id, total_score, breakdown_json, created_at) VALUES (?, ?, ?, ?)",
                (
                    submission_id,
                    scoring["total_score"],
                    json.dumps(
                        {
                            "component_score": scoring["component_score"],
                            "text_score": scoring["text_score"],
                            "structure_score": scoring["structure_score"],
                        }
                    ),
                    utc_now(),
                ),
            )

            for msg in scoring["feedback"]:
                conn.execute(
                    "INSERT INTO feedback_items (submission_id, message, meta_json, created_at) VALUES (?, ?, ?, ?)",
                    (submission_id, msg, "{}", utc_now()),
                )

            results.append({
                "submission_id": submission_id,
                "file": file.filename,
                "student_id": student_id,
                "status": "done",
                "score": scoring["total_score"],
            })
        except Exception as ex:
            conn.execute(
                "UPDATE submissions SET status = 'failed', feedback_json = ?, processed_at = ? WHERE id = ?",
                (json.dumps([str(ex)], ensure_ascii=False), utc_now(), submission_id),
            )
            results.append({
                "submission_id": submission_id,
                "file": file.filename,
                "student_id": student_id,
                "status": "failed",
                "reason": str(ex),
            })

    conn.commit()
    conn.close()

    return jsonify({"message": "batch processed", "items": results})


@app.get("/exams/<int:exam_id>/results")
@login_required
def exam_results(exam_id):
    conn = get_db_connection()

    rubric_row = conn.execute("SELECT rubric_json FROM rubrics WHERE exam_id = ?", (exam_id,)).fetchone()
    rubric = json.loads(rubric_row["rubric_json"]) if rubric_row else {"pass_score": 60}

    rows = conn.execute(
        """
        SELECT id, student_id, file_name, status, score_total, score_component, score_text, score_structure,
               overridden_score, processed_at
        FROM submissions
        WHERE exam_id = ?
        ORDER BY id DESC
        """,
        (exam_id,),
    ).fetchall()
    conn.close()

    items = []
    for r in rows:
        record = dict(r)
        effective_score = record["overridden_score"] if record["overridden_score"] is not None else record["score_total"]
        record["effective_score"] = effective_score
        record["passed"] = (effective_score or 0) >= rubric.get("pass_score", 60)
        items.append(record)

    return jsonify({"items": items, "rubric": rubric})


@app.get("/submissions/<int:submission_id>/result-detail")
@login_required
def submission_detail(submission_id):
    conn = get_db_connection()
    row = conn.execute(
        """
        SELECT id, exam_id, student_id, file_name, status, score_total, score_component, score_text, score_structure,
               overridden_score, override_note, feedback_json, result_json, processed_at
        FROM submissions
        WHERE id = ?
        """,
        (submission_id,),
    ).fetchone()

    if not row:
        conn.close()
        return jsonify({"error": "not found"}), 404

    detections = conn.execute(
        "SELECT class_name, x, y, w, h, confidence FROM detections WHERE submission_id = ?",
        (submission_id,),
    ).fetchall()
    ocrs = conn.execute(
        "SELECT text_value, confidence FROM ocr_texts WHERE submission_id = ?",
        (submission_id,),
    ).fetchall()
    conn.close()

    item = dict(row)
    item["feedback"] = json.loads(item["feedback_json"] or "[]")
    item["result"] = json.loads(item["result_json"] or "{}")
    item["detections"] = [dict(d) for d in detections]
    item["ocr_texts"] = [dict(t) for t in ocrs]

    return jsonify(item)


@app.post("/submissions/<int:submission_id>/override-score")
@login_required
def override_score(submission_id):
    payload = request.get_json(silent=True) or request.form
    score = payload.get("score")
    note = (payload.get("note") or "").strip()

    try:
        score = float(score)
    except (TypeError, ValueError):
        return jsonify({"error": "score ต้องเป็นตัวเลข"}), 400

    now = utc_now()
    conn = get_db_connection()
    row = conn.execute("SELECT id, exam_id, score_total FROM submissions WHERE id = ?", (submission_id,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"error": "not found"}), 404

    conn.execute(
        "UPDATE submissions SET overridden_score = ?, override_note = ?, processed_at = ? WHERE id = ?",
        (score, note, now, submission_id),
    )

    conn.execute(
        """
        INSERT INTO audit_logs (actor_user_id, action, target_type, target_id, payload_json, created_at)
        VALUES (?, 'override_score', 'submission', ?, ?, ?)
        """,
        (
            session["user_id"],
            submission_id,
            json.dumps({"from": row["score_total"], "to": score, "note": note}, ensure_ascii=False),
            now,
        ),
    )

    conn.commit()
    conn.close()
    return jsonify({"message": "override saved"})


@app.get("/exams/<int:exam_id>/export")
@login_required
def export_results(exam_id):
    fmt = (request.args.get("format") or "xlsx").lower()
    if fmt not in {"xlsx", "csv"}:
        return jsonify({"error": "format must be xlsx or csv"}), 400

    conn = get_db_connection()
    rows = conn.execute(
        """
        SELECT id, student_id, file_name, status, score_total, overridden_score, score_component, score_text, score_structure, processed_at
        FROM submissions
        WHERE exam_id = ?
        ORDER BY id ASC
        """,
        (exam_id,),
    ).fetchall()
    conn.close()

    records = []
    for r in rows:
        item = dict(r)
        item["final_score"] = item["overridden_score"] if item["overridden_score"] is not None else item["score_total"]
        records.append(item)

    df = pd.DataFrame(records)
    stamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

    if fmt == "csv":
        path = EXPORTS_DIR / f"exam_{exam_id}_results_{stamp}.csv"
        df.to_csv(path, index=False, encoding="utf-8-sig")
        mimetype = "text/csv"
    else:
        path = EXPORTS_DIR / f"exam_{exam_id}_results_{stamp}.xlsx"
        df.to_excel(path, index=False)
        mimetype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    return send_file(path, as_attachment=True, download_name=path.name, mimetype=mimetype)


@app.post("/exams/<int:exam_id>/results/clear")
@login_required
def clear_exam_results(exam_id):
    conn = get_db_connection()

    rows = conn.execute("SELECT id FROM submissions WHERE exam_id = ?", (exam_id,)).fetchall()
    submission_ids = [r["id"] for r in rows]
    if not submission_ids:
        conn.close()
        return jsonify({"message": "no results to clear", "cleared": 0})

    placeholders = ",".join(["?"] * len(submission_ids))

    conn.execute(f"DELETE FROM detections WHERE submission_id IN ({placeholders})", submission_ids)
    conn.execute(f"DELETE FROM ocr_texts WHERE submission_id IN ({placeholders})", submission_ids)
    conn.execute(f"DELETE FROM graphs WHERE submission_id IN ({placeholders})", submission_ids)
    conn.execute(f"DELETE FROM scores WHERE submission_id IN ({placeholders})", submission_ids)
    conn.execute(f"DELETE FROM feedback_items WHERE submission_id IN ({placeholders})", submission_ids)
    conn.execute(f"DELETE FROM submissions WHERE id IN ({placeholders})", submission_ids)

    conn.execute(
        f"DELETE FROM audit_logs WHERE target_type = 'submission' AND target_id IN ({placeholders})",
        submission_ids,
    )

    conn.commit()
    conn.close()
    return jsonify({"message": "results cleared", "cleared": len(submission_ids)})


if __name__ == "__main__":
    init_db()
    app.run(debug=True, host="127.0.0.1", port=5000)

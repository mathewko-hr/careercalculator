from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
OFFER_DIR = DATA_DIR / "offers"
DB_PATH = DATA_DIR / "offer_system.db"


def now_text() -> str:
    return datetime.now().isoformat(timespec="seconds")


def is_missing_value(value: Any) -> bool:
    """Return True for None, NaN, pandas NA/NaT, and blank text."""
    if value is None:
        return True
    normalized = str(value).strip().lower()
    return normalized in {"", "none", "nan", "<na>", "nat"}


def safe_float(value: Any, default: float | None = None) -> float | None:
    if is_missing_value(value):
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int | None = None) -> int | None:
    numeric = safe_float(value, None)
    return int(numeric) if numeric is not None else default


def safe_bool(value: Any, default: bool = False) -> bool:
    if is_missing_value(value):
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "y", "yes", "예", "체크", "checked"}:
        return True
    if normalized in {"false", "0", "n", "no", "아니오", "미체크", "unchecked"}:
        return False
    try:
        return float(normalized) != 0
    except ValueError:
        return default


def ensure_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    OFFER_DIR.mkdir(parents=True, exist_ok=True)


def connect() -> sqlite3.Connection:
    ensure_directories()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                email TEXT,
                phone TEXT,
                current_company TEXT,
                department TEXT,
                target_job TEXT,
                work_location TEXT,
                employment_type TEXT,
                expected_join_date TEXT,
                status TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS career_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                company_name TEXT NOT NULL,
                start_date TEXT,
                end_date TEXT,
                job_title TEXT,
                recognition_type TEXT,
                recognition_rate REAL NOT NULL DEFAULT 0,
                source TEXT,
                source_file TEXT,
                remarks TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS compensation_records (
                candidate_id INTEGER PRIMARY KEY,
                current_fixed_salary REAL NOT NULL DEFAULT 0,
                current_incentive REAL NOT NULL DEFAULT 0,
                current_other REAL NOT NULL DEFAULT 0,
                offer_base_salary REAL NOT NULL DEFAULT 0,
                offer_fixed_allowance REAL NOT NULL DEFAULT 0,
                offer_target_incentive REAL NOT NULL DEFAULT 0,
                offer_other_recurring REAL NOT NULL DEFAULT 0,
                sign_on_bonus REAL NOT NULL DEFAULT 0,
                pay_band_min REAL NOT NULL DEFAULT 0,
                pay_band_mid REAL NOT NULL DEFAULT 0,
                pay_band_max REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS benefit_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                include_in_offer INTEGER NOT NULL DEFAULT 0,
                category TEXT,
                benefit_name TEXT NOT NULL,
                eligibility TEXT,
                remarks TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                document_type TEXT,
                original_name TEXT NOT NULL,
                stored_path TEXT NOT NULL,
                extracted_text TEXT,
                ocr_used INTEGER NOT NULL DEFAULT 0,
                analysis_message TEXT,
                uploaded_at TEXT NOT NULL,
                analyzed_at TEXT,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS offer_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                version_no INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(candidate_id, version_no),
                FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS candidate_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER NOT NULL,
                snapshot_json TEXT NOT NULL,
                reason TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                candidate_id INTEGER,
                action TEXT NOT NULL,
                detail_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS system_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS revenue_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                min_revenue_eok REAL NOT NULL DEFAULT 0,
                max_revenue_eok REAL,
                recognition_rate REAL NOT NULL DEFAULT 0,
                label TEXT,
                sort_order INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS company_mappings (
                alias_name TEXT PRIMARY KEY,
                normalized_alias TEXT NOT NULL,
                corp_code TEXT NOT NULL,
                corp_name TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pay_band_reference (
                grade TEXT NOT NULL,
                band TEXT NOT NULL,
                base_salary REAL NOT NULL DEFAULT 0,
                performance_salary REAL NOT NULL DEFAULT 0,
                fixed_overtime REAL NOT NULL DEFAULT 0,
                contract_subtotal REAL NOT NULL DEFAULT 0,
                job_allowance REAL NOT NULL DEFAULT 0,
                family_allowance REAL NOT NULL DEFAULT 0,
                vehicle_subsidy REAL NOT NULL DEFAULT 0,
                self_development REAL NOT NULL DEFAULT 0,
                homecoming_travel REAL NOT NULL DEFAULT 0,
                welfare_points REAL NOT NULL DEFAULT 0,
                flexible_welfare_points REAL NOT NULL DEFAULT 0,
                fitness REAL NOT NULL DEFAULT 0,
                pension REAL NOT NULL DEFAULT 0,
                cash_benefits_subtotal REAL NOT NULL DEFAULT 0,
                contract_plus_cash REAL NOT NULL DEFAULT 0,
                management_incentive REAL NOT NULL DEFAULT 0,
                total REAL NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (grade, band)
            );
            """
        )

        # 입사신분별 복리후생 기준에 사용할 후보자 속성을 기존 DB에 자동 추가합니다.
        _ensure_column(conn, "candidates", "job_group", "TEXT")
        _ensure_column(conn, "candidates", "entry_type", "TEXT")
        _ensure_column(conn, "candidates", "gender", "TEXT")
        _ensure_column(conn, "candidates", "birth_date", "TEXT")
        _ensure_column(conn, "candidates", "education", "TEXT")
        _ensure_column(conn, "candidates", "major", "TEXT")
        _ensure_column(conn, "candidates", "photo_path", "TEXT")

        # 기존 DB를 그대로 사용할 수 있도록 경력 테이블을 자동 마이그레이션합니다.
        _ensure_column(conn, "career_records", "standard_company_name", "TEXT")
        _ensure_column(conn, "career_records", "dart_corp_code", "TEXT")
        _ensure_column(conn, "career_records", "revenue_year", "INTEGER")
        _ensure_column(conn, "career_records", "consolidated_revenue_eok", "REAL")
        _ensure_column(conn, "career_records", "revenue_status", "TEXT")
        _ensure_column(conn, "career_records", "revenue_rule_rate", "REAL")
        _ensure_column(conn, "career_records", "revenue_rcept_no", "TEXT")
        _ensure_column(conn, "career_records", "external_source_type", "TEXT")
        _ensure_column(conn, "career_records", "external_source_url", "TEXT")
        _ensure_column(conn, "career_records", "external_statement_type", "TEXT")
        _ensure_column(conn, "career_records", "external_evidence_file", "TEXT")
        _ensure_column(conn, "career_records", "manual_override", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(conn, "career_records", "manual_reason", "TEXT")

        # 기존 보상 DB를 유지하면서 신규 보상구조를 자동 추가합니다.
        _ensure_column(conn, "compensation_records", "current_contract_salary", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "current_total_salary", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "selected_grade", "TEXT")
        _ensure_column(conn, "compensation_records", "selected_band", "TEXT")
        _ensure_column(conn, "compensation_records", "offer_performance_salary", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "offer_fixed_overtime", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "offer_incentive", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "cash_job_allowance", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "cash_family_allowance", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "cash_vehicle_subsidy", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "cash_self_development", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "cash_homecoming_travel", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "cash_welfare_points", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "cash_flexible_welfare_points", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "cash_fitness", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "cash_pension", "REAL NOT NULL DEFAULT 0")
        _ensure_column(conn, "compensation_records", "promotion_base_date", "TEXT")

        count = conn.execute("SELECT COUNT(*) AS cnt FROM revenue_rules").fetchone()["cnt"]
        if count == 0:
            timestamp = now_text()
            defaults = [
                (100000, None, 100, "10조원 이상", 1),
                (10000, 100000, 90, "1조원 이상 10조원 미만", 2),
                (1000, 10000, 80, "1천억원 이상 1조원 미만", 3),
                (0, 1000, 70, "1천억원 미만", 4),
            ]
            conn.executemany(
                """INSERT INTO revenue_rules
                   (min_revenue_eok, max_revenue_eok, recognition_rate, label, sort_order, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(a, b, c, d, e, timestamp) for a, b, c, d, e in defaults],
            )

        pay_count = conn.execute("SELECT COUNT(*) AS cnt FROM pay_band_reference").fetchone()["cnt"]
        if pay_count == 0:
            timestamp = now_text()
            default_rows = [
                ("G4/R4(실장)", "Max"), ("G4/R4(실장)", "초임"),
                ("G4/R4(팀장)", "Max"), ("G4/R4(팀장)", "초임"),
                ("G4/R4", "Max"), ("G4/R4", "초임"),
                ("G3/R3", "Max"), ("G3/R3", "초임"),
                ("G2/R2", "Max"), ("G2/R2", "초임"),
                ("G1/R1", "4년차"), ("G1/R1", "3년차(석사)"), ("G1/R1", "초임"),
            ]
            conn.executemany(
                """INSERT INTO pay_band_reference (
                    grade, band, updated_at
                ) VALUES (?, ?, ?)""",
                [(grade, band, timestamp) for grade, band in default_rows],
            )

        # v3.6부터 금액 단위를 '만원'에서 '원'으로 통일합니다.
        # 기존 v3.5 DB는 실제 값이 만원 단위였으므로 최초 1회만 10,000배 변환합니다.
        money_migration_key = "MONEY_UNIT_V36_WON"
        migrated = conn.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = ?",
            (money_migration_key,),
        ).fetchone()
        if not migrated:
            compensation_money_columns = [
                "current_fixed_salary", "current_incentive", "current_other",
                "offer_base_salary", "offer_fixed_allowance", "offer_target_incentive",
                "offer_other_recurring", "sign_on_bonus", "pay_band_min", "pay_band_mid", "pay_band_max",
                "current_contract_salary", "current_total_salary", "offer_performance_salary",
                "offer_fixed_overtime", "offer_incentive", "cash_job_allowance",
                "cash_family_allowance", "cash_vehicle_subsidy", "cash_self_development",
                "cash_homecoming_travel", "cash_welfare_points", "cash_flexible_welfare_points",
                "cash_fitness", "cash_pension",
            ]
            for column in compensation_money_columns:
                conn.execute(
                    f"UPDATE compensation_records SET {column} = COALESCE({column}, 0) * 10000"
                )

            pay_band_money_columns = [
                "base_salary", "performance_salary", "fixed_overtime", "contract_subtotal",
                "job_allowance", "family_allowance", "vehicle_subsidy", "self_development",
                "homecoming_travel", "welfare_points", "flexible_welfare_points", "fitness",
                "pension", "cash_benefits_subtotal", "contract_plus_cash", "management_incentive", "total",
            ]
            for column in pay_band_money_columns:
                conn.execute(
                    f"UPDATE pay_band_reference SET {column} = COALESCE({column}, 0) * 10000"
                )

            conn.execute(
                """INSERT INTO system_settings (setting_key, setting_value, updated_at)
                   VALUES (?, ?, ?)
                   ON CONFLICT(setting_key) DO UPDATE SET
                       setting_value = excluded.setting_value, updated_at = excluded.updated_at""",
                (money_migration_key, "1", now_text()),
            )


def generate_candidate_code() -> str:
    year = datetime.now().year
    return f"CAND-{year}-{uuid4().hex[:6].upper()}"


def list_candidates(search_text: str = "", status: str = "전체") -> list[dict[str, Any]]:
    sql = """
        SELECT
            c.*,
            COALESCE(
                (SELECT SUM(
                    CASE
                        WHEN cr.start_date IS NOT NULL AND cr.end_date IS NOT NULL
                        THEN (julianday(cr.end_date) - julianday(cr.start_date) + 1)
                             * cr.recognition_rate / 100.0
                        ELSE 0
                    END
                ) FROM career_records cr
                  WHERE cr.candidate_id = c.id
                    AND (cr.manual_override = 1 OR cr.revenue_status = '조회완료' OR cr.recognition_type = '미인정')),
                0
            ) AS recognized_days,
            COALESCE(
                (SELECT offer_base_salary + offer_performance_salary + offer_fixed_overtime
                        + offer_incentive + cash_job_allowance + cash_family_allowance
                        + cash_vehicle_subsidy + cash_self_development + cash_homecoming_travel
                        + cash_welfare_points + cash_flexible_welfare_points + cash_fitness + cash_pension
                 FROM compensation_records cp WHERE cp.candidate_id = c.id),
                0
            ) AS offered_total
        FROM candidates c
        WHERE 1=1
    """
    params: list[Any] = []
    if search_text.strip():
        like = f"%{search_text.strip()}%"
        sql += """
            AND (
                c.name LIKE ? OR c.candidate_code LIKE ? OR
                c.current_company LIKE ? OR c.target_job LIKE ?
            )
        """
        params.extend([like, like, like, like])
    if status != "전체":
        sql += " AND c.status = ?"
        params.append(status)
    sql += " ORDER BY c.updated_at DESC, c.id DESC"

    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def get_candidate(candidate_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        return dict(row) if row else None


def upsert_candidate(candidate: dict[str, Any]) -> int:
    timestamp = now_text()
    candidate_id = candidate.get("id")

    with connect() as conn:
        if candidate_id:
            conn.execute(
                """
                UPDATE candidates
                SET name = ?, email = ?, phone = ?, current_company = ?,
                    department = ?, target_job = ?, work_location = ?,
                    employment_type = ?, job_group = ?, entry_type = ?,
                    gender = ?, birth_date = ?, education = ?, major = ?, photo_path = ?,
                    expected_join_date = ?, status = ?, notes = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    candidate["name"],
                    candidate.get("email", ""),
                    candidate.get("phone", ""),
                    candidate.get("current_company", ""),
                    candidate.get("department", ""),
                    candidate.get("target_job", ""),
                    candidate.get("work_location", ""),
                    candidate.get("employment_type", ""),
                    candidate.get("job_group", ""),
                    candidate.get("entry_type", ""),
                    candidate.get("gender", ""),
                    candidate.get("birth_date", ""),
                    candidate.get("education", ""),
                    candidate.get("major", ""),
                    candidate.get("photo_path", ""),
                    candidate.get("expected_join_date", ""),
                    candidate.get("status", "검토중"),
                    candidate.get("notes", ""),
                    timestamp,
                    candidate_id,
                ),
            )
            return int(candidate_id)

        candidate_code = candidate.get("candidate_code") or generate_candidate_code()
        cursor = conn.execute(
            """
            INSERT INTO candidates (
                candidate_code, name, email, phone, current_company,
                department, target_job, work_location, employment_type, job_group, entry_type,
                gender, birth_date, education, major, photo_path,
                expected_join_date, status, notes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_code,
                candidate["name"],
                candidate.get("email", ""),
                candidate.get("phone", ""),
                candidate.get("current_company", ""),
                candidate.get("department", ""),
                candidate.get("target_job", ""),
                candidate.get("work_location", ""),
                candidate.get("employment_type", ""),
                candidate.get("job_group", ""),
                candidate.get("entry_type", ""),
                candidate.get("gender", ""),
                candidate.get("birth_date", ""),
                candidate.get("education", ""),
                candidate.get("major", ""),
                candidate.get("photo_path", ""),
                candidate.get("expected_join_date", ""),
                candidate.get("status", "검토중"),
                candidate.get("notes", ""),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)



def replace_career_records(candidate_id: int, records: list[dict[str, Any]]) -> None:
    timestamp = now_text()
    with connect() as conn:
        conn.execute("DELETE FROM career_records WHERE candidate_id = ?", (candidate_id,))
        for record in records:
            company_name = str(record.get("회사명", "") or "").strip()
            if not company_name:
                continue
            conn.execute(
                """
                INSERT INTO career_records (
                    candidate_id, company_name, start_date, end_date, job_title,
                    recognition_type, recognition_rate, source, source_file,
                    remarks, standard_company_name, dart_corp_code, revenue_year,
                    consolidated_revenue_eok, revenue_status, revenue_rule_rate,
                    revenue_rcept_no, external_source_type, external_source_url,
                    external_statement_type, external_evidence_file,
                    manual_override, manual_reason, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    company_name,
                    record.get("입사일", ""),
                    record.get("퇴사일", ""),
                    record.get("직무/직책", ""),
                    record.get("인정구분", "확인필요"),
                    safe_float(record.get("인정률(%)"), 0.0),
                    record.get("출처", "수기입력"),
                    record.get("출처파일", ""),
                    record.get("비고", ""),
                    record.get("표준회사명", ""),
                    record.get("DART고유번호", ""),
                    safe_int(record.get("매출연도"), None),
                    safe_float(record.get("연결매출(억원)"), None),
                    record.get("매출조회상태", "미조회"),
                    safe_float(record.get("매출기준인정률(%)"), None),
                    record.get("공시접수번호", ""),
                    record.get("외부자료출처", ""),
                    record.get("외부자료URL", ""),
                    record.get("재무기준", ""),
                    record.get("외부증빙파일", ""),
                    1 if safe_bool(record.get("수동확정"), False) else 0,
                    record.get("수동조정사유", ""),
                    timestamp,
                    timestamp,
                ),
            )


def get_career_records(candidate_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                company_name AS 회사명,
                start_date AS 입사일,
                end_date AS 퇴사일,
                job_title AS "직무/직책",
                standard_company_name AS 표준회사명,
                dart_corp_code AS DART고유번호,
                revenue_year AS 매출연도,
                consolidated_revenue_eok AS "연결매출(억원)",
                revenue_status AS 매출조회상태,
                revenue_rule_rate AS "매출기준인정률(%)",
                revenue_rcept_no AS 공시접수번호,
                external_source_type AS 외부자료출처,
                external_source_url AS 외부자료URL,
                external_statement_type AS 재무기준,
                external_evidence_file AS 외부증빙파일,
                manual_override AS 수동확정,
                manual_reason AS 수동조정사유,
                recognition_type AS 인정구분,
                recognition_rate AS "인정률(%)",
                source AS 출처,
                source_file AS 출처파일,
                remarks AS 비고
            FROM career_records
            WHERE candidate_id = ?
            ORDER BY start_date, id
            """,
            (candidate_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["수동확정"] = bool(item.get("수동확정"))
            result.append(item)
        return result

def upsert_compensation(candidate_id: int, data: dict[str, Any]) -> None:
    fields = [
        "current_contract_salary", "current_total_salary",
        "selected_grade", "selected_band",
        "offer_base_salary", "offer_performance_salary", "offer_fixed_overtime", "offer_incentive",
        "cash_job_allowance", "cash_family_allowance", "cash_vehicle_subsidy",
        "cash_self_development", "cash_homecoming_travel", "cash_welfare_points",
        "cash_flexible_welfare_points", "cash_fitness", "cash_pension",
        "sign_on_bonus", "promotion_base_date",
    ]
    numeric_fields = set(fields) - {"selected_grade", "selected_band", "promotion_base_date"}
    values = []
    for field in fields:
        if field in numeric_fields:
            values.append(float(safe_float(data.get(field), 0.0) or 0.0))
        else:
            values.append(str(data.get(field, "") or ""))

    with connect() as conn:
        conn.execute(
            f"""
            INSERT INTO compensation_records (
                candidate_id, {', '.join(fields)}, updated_at
            ) VALUES ({', '.join(['?'] * (len(fields) + 2))})
            ON CONFLICT(candidate_id) DO UPDATE SET
                {', '.join([f'{field} = excluded.{field}' for field in fields])},
                updated_at = excluded.updated_at
            """,
            (candidate_id, *values, now_text()),
        )


def get_compensation(candidate_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM compensation_records WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return dict(row) if row else None


PAY_BAND_COLUMNS = [
    "직급", "BAND", "기본연봉", "업적연봉", "고정연장", "계약연봉소계",
    "직책수당", "가족수당(본인)", "차량보조금", "자기계발지원금", "귀향여비",
    "복지포인트", "선택형 복지포인트", "체력단련비", "개인연금",
    "현금성지급소계", "계약연봉+현금성지급", "경영성과급", "총계",
]

_PAY_BAND_DB_MAP = {
    "직급": "grade", "BAND": "band", "기본연봉": "base_salary",
    "업적연봉": "performance_salary", "고정연장": "fixed_overtime",
    "계약연봉소계": "contract_subtotal", "직책수당": "job_allowance",
    "가족수당(본인)": "family_allowance", "차량보조금": "vehicle_subsidy",
    "자기계발지원금": "self_development", "귀향여비": "homecoming_travel",
    "복지포인트": "welfare_points", "선택형 복지포인트": "flexible_welfare_points",
    "체력단련비": "fitness", "개인연금": "pension",
    "현금성지급소계": "cash_benefits_subtotal",
    "계약연봉+현금성지급": "contract_plus_cash",
    "경영성과급": "management_incentive", "총계": "total",
}


def get_pay_band_reference() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM pay_band_reference").fetchall()
    order = {
        ("G4/R4(실장)", "Max"): 1, ("G4/R4(실장)", "초임"): 2,
        ("G4/R4(팀장)", "Max"): 3, ("G4/R4(팀장)", "초임"): 4,
        ("G4/R4", "Max"): 5, ("G4/R4", "초임"): 6,
        ("G3/R3", "Max"): 7, ("G3/R3", "초임"): 8,
        ("G2/R2", "Max"): 9, ("G2/R2", "초임"): 10,
        ("G1/R1", "4년차"): 11, ("G1/R1", "3년차(석사)"): 12, ("G1/R1", "초임"): 13,
    }
    result = []
    for row in rows:
        d = dict(row)
        result.append({ko: d.get(db) for ko, db in _PAY_BAND_DB_MAP.items()})
    result.sort(key=lambda x: order.get((x.get("직급"), x.get("BAND")), 999))
    return result


def replace_pay_band_reference(records: list[dict[str, Any]]) -> None:
    timestamp = now_text()
    with connect() as conn:
        conn.execute("DELETE FROM pay_band_reference")
        for record in records:
            grade = str(record.get("직급", "") or "").strip()
            band = str(record.get("BAND", "") or "").strip()
            if not grade or not band:
                continue
            values = []
            for ko, db in _PAY_BAND_DB_MAP.items():
                if ko in {"직급", "BAND"}:
                    continue
                values.append(float(safe_float(record.get(ko), 0.0) or 0.0))
            conn.execute(
                """INSERT INTO pay_band_reference (
                    grade, band, base_salary, performance_salary, fixed_overtime, contract_subtotal,
                    job_allowance, family_allowance, vehicle_subsidy, self_development, homecoming_travel,
                    welfare_points, flexible_welfare_points, fitness, pension, cash_benefits_subtotal,
                    contract_plus_cash, management_incentive, total, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (grade, band, *values, timestamp),
            )


def get_pay_band_row(grade: str, band: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM pay_band_reference WHERE grade = ? AND band = ?",
            (grade, band),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    return {ko: d.get(db) for ko, db in _PAY_BAND_DB_MAP.items()}


def replace_benefit_records(candidate_id: int, records: list[dict[str, Any]]) -> None:
    timestamp = now_text()
    with connect() as conn:
        conn.execute("DELETE FROM benefit_records WHERE candidate_id = ?", (candidate_id,))
        for record in records:
            name = str(record.get("복리후생", "") or "").strip()
            if not name:
                continue
            conn.execute(
                """
                INSERT INTO benefit_records (
                    candidate_id, include_in_offer, category, benefit_name,
                    eligibility, remarks, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate_id,
                    1 if safe_bool(record.get("오퍼레터 포함"), False) else 0,
                    record.get("구분", ""),
                    name,
                    record.get("적용여부", ""),
                    record.get("설명", record.get("비고", "")),
                    timestamp,
                    timestamp,
                ),
            )


def get_benefit_records(candidate_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT
                include_in_offer AS "오퍼레터 포함",
                category AS 구분,
                benefit_name AS 복리후생,
                eligibility AS 적용여부,
                remarks AS 설명
            FROM benefit_records
            WHERE candidate_id = ?
            ORDER BY id
            """,
            (candidate_id,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["오퍼레터 포함"] = bool(item["오퍼레터 포함"])
            result.append(item)
        return result


def add_document(
    candidate_id: int,
    document_type: str,
    original_name: str,
    stored_path: str,
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO documents (
                candidate_id, document_type, original_name, stored_path,
                uploaded_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                document_type,
                original_name,
                stored_path,
                now_text(),
            ),
        )
        return int(cursor.lastrowid)


def get_documents(candidate_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM documents
            WHERE candidate_id = ?
            ORDER BY uploaded_at DESC, id DESC
            """,
            (candidate_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def get_document(document_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        return dict(row) if row else None


def update_document_analysis(
    document_id: int,
    extracted_text: str,
    ocr_used: bool,
    analysis_message: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE documents
            SET extracted_text = ?, ocr_used = ?, analysis_message = ?,
                analyzed_at = ?
            WHERE id = ?
            """,
            (
                extracted_text,
                1 if ocr_used else 0,
                analysis_message,
                now_text(),
                document_id,
            ),
        )


def next_offer_version(candidate_id: int) -> int:
    with connect() as conn:
        row = conn.execute(
            "SELECT COALESCE(MAX(version_no), 0) + 1 AS next_version "
            "FROM offer_versions WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return int(row["next_version"])


def add_offer_version(
    candidate_id: int,
    version_no: int,
    file_path: str,
    snapshot: dict[str, Any],
) -> int:
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO offer_versions (
                candidate_id, version_no, file_path, snapshot_json, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                version_no,
                file_path,
                json.dumps(snapshot, ensure_ascii=False, default=str),
                now_text(),
            ),
        )
        return int(cursor.lastrowid)


def get_offer_versions(candidate_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, version_no, file_path, created_at, snapshot_json
            FROM offer_versions
            WHERE candidate_id = ?
            ORDER BY version_no DESC
            """,
            (candidate_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def add_snapshot(candidate_id: int, snapshot: dict[str, Any], reason: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO candidate_snapshots (
                candidate_id, snapshot_json, reason, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                candidate_id,
                json.dumps(snapshot, ensure_ascii=False, default=str),
                reason,
                now_text(),
            ),
        )


def get_snapshots(candidate_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, reason, created_at, snapshot_json
            FROM candidate_snapshots
            WHERE candidate_id = ?
            ORDER BY id DESC
            """,
            (candidate_id,),
        ).fetchall()
        return [dict(row) for row in rows]


def add_audit_log(
    candidate_id: int | None,
    action: str,
    detail: dict[str, Any] | None = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs (
                candidate_id, action, detail_json, created_at
            )
            VALUES (?, ?, ?, ?)
            """,
            (
                candidate_id,
                action,
                json.dumps(detail or {}, ensure_ascii=False, default=str),
                now_text(),
            ),
        )


def get_audit_logs(candidate_id: int) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, action, detail_json, created_at
            FROM audit_logs
            WHERE candidate_id = ?
            ORDER BY id DESC
            """,
            (candidate_id,),
        ).fetchall()
        return [dict(row) for row in rows]



def get_setting(key: str, default: str = "") -> str:
    with connect() as conn:
        row = conn.execute(
            "SELECT setting_value FROM system_settings WHERE setting_key = ?",
            (key,),
        ).fetchone()
        return str(row["setting_value"]) if row else default


def set_setting(key: str, value: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO system_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = excluded.updated_at
            """,
            (key, value, now_text()),
        )


def get_revenue_rules() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT id, min_revenue_eok, max_revenue_eok, recognition_rate,
                   label, sort_order, updated_at
            FROM revenue_rules
            ORDER BY sort_order, min_revenue_eok DESC
            """
        ).fetchall()
        return [dict(row) for row in rows]


def replace_revenue_rules(records: list[dict[str, Any]]) -> None:
    timestamp = now_text()
    with connect() as conn:
        conn.execute("DELETE FROM revenue_rules")
        for index, record in enumerate(records, start=1):
            minimum = safe_float(record.get("매출하한(억원)"), 0.0) or 0.0
            maximum = safe_float(record.get("매출상한(억원)"), None)
            conn.execute(
                """
                INSERT INTO revenue_rules (
                    min_revenue_eok, max_revenue_eok, recognition_rate,
                    label, sort_order, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    minimum,
                    maximum,
                    safe_float(record.get("인정률(%)"), 0.0),
                    str(record.get("구간명", "") or ""),
                    index,
                    timestamp,
                ),
            )


def get_company_mapping(alias_name: str) -> dict[str, Any] | None:
    normalized = "".join(str(alias_name or "").split()).lower()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT alias_name, normalized_alias, corp_code, corp_name, updated_at
            FROM company_mappings
            WHERE alias_name = ? OR normalized_alias = ?
            LIMIT 1
            """,
            (alias_name, normalized),
        ).fetchone()
        return dict(row) if row else None


def upsert_company_mapping(alias_name: str, normalized_alias: str, corp_code: str, corp_name: str) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO company_mappings (
                alias_name, normalized_alias, corp_code, corp_name, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(alias_name) DO UPDATE SET
                normalized_alias = excluded.normalized_alias,
                corp_code = excluded.corp_code,
                corp_name = excluded.corp_name,
                updated_at = excluded.updated_at
            """,
            (alias_name, normalized_alias, corp_code, corp_name, now_text()),
        )


def list_company_mappings() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT alias_name, corp_code, corp_name, updated_at
               FROM company_mappings ORDER BY updated_at DESC"""
        ).fetchall()
        return [dict(row) for row in rows]


def delete_company_mapping(alias_name: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM company_mappings WHERE alias_name = ?", (alias_name,))

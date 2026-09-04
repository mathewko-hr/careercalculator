from __future__ import annotations

from difflib import SequenceMatcher
from io import BytesIO
from pathlib import Path
import csv
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any

import pandas as pd
import requests


DART_CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml"
DART_FINANCIAL_URL = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
ANNUAL_REPORT_CODE = "11011"
CONSOLIDATED_FS = "CFS"


class DartError(RuntimeError):
    pass


def normalize_corp_code(value: Any) -> str:
    """DART 고유번호를 8자리 문자열로 정규화합니다.

    pandas의 빈값(NaN, pd.NA)이 "nan" 또는 "<NA>" 문자열로
    변환되어 수기 고유번호로 오인되는 문제를 방지합니다.
    """
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass

    text = str(value).strip()
    if text.lower() in {"", "nan", "none", "<na>", "nat", "null"}:
        return ""
    text = re.sub(r"\.0+$", "", text)
    digits = re.sub(r"[^0-9]", "", text)
    if not digits or len(digits) > 8:
        return ""
    return digits.zfill(8)


def normalize_company_name(name: str) -> str:
    text = str(name or "").strip().lower()
    text = text.replace("주식회사", "").replace("유한회사", "")
    # 사업장 단위가 별도 토큰으로 붙은 경우 먼저 제거합니다.
    text = re.sub(
        r"\s+\S*(본사|본점|지점|지사|영업소|사업소|사업장|공장|연구소|센터|현장|출장소)\s*$",
        "",
        text,
    )
    text = re.sub(r"\(\s*주\s*\)|㈜|（주）", "", text)
    text = re.sub(r"\(\s*유\s*\)|㈲", "", text)
    text = re.sub(r"[\s·ㆍ.,'\"`~!@#$%^&*+=:;?/\\|\[\]{}<>_-]+", "", text)
    # 건강보험 문서에 붙기 쉬운 사업장 단위 표현 제거
    text = re.sub(
        r"(본사|본점|지점|지사|영업소|사업소|사업장|공장|연구소|센터|현장|출장소|지점사업장)$",
        "",
        text,
    )
    return text


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        return response.json()
    except Exception as exc:
        raise DartError(f"DART 응답을 JSON으로 읽지 못했습니다: {exc}") from exc


def download_corp_codes(api_key: str, target_csv: str | Path) -> int:
    if not api_key or len(api_key.strip()) < 20:
        raise DartError("DART API 인증키를 관리자 화면에 입력하세요.")

    response = requests.get(
        DART_CORP_CODE_URL,
        params={"crtfc_key": api_key.strip()},
        timeout=90,
    )
    response.raise_for_status()

    content = response.content
    if not zipfile.is_zipfile(BytesIO(content)):
        # 오류 응답은 XML일 수 있음
        preview = content.decode("utf-8", errors="ignore")[:500]
        status = re.search(r"<status>(.*?)</status>", preview)
        message = re.search(r"<message>(.*?)</message>", preview)
        if status or message:
            raise DartError(
                f"DART 기업목록 조회 실패: "
                f"{status.group(1) if status else ''} "
                f"{message.group(1) if message else ''}".strip()
            )
        raise DartError("DART 기업목록 응답이 ZIP 파일이 아닙니다.")

    with zipfile.ZipFile(BytesIO(content)) as archive:
        xml_names = [name for name in archive.namelist() if name.lower().endswith(".xml")]
        if not xml_names:
            raise DartError("DART 기업목록 ZIP에서 XML 파일을 찾지 못했습니다.")
        xml_bytes = archive.read(xml_names[0])

    root = ET.fromstring(xml_bytes)
    rows: list[dict[str, str]] = []
    for item in root.findall("list"):
        corp_code = (item.findtext("corp_code") or "").strip()
        corp_name = (item.findtext("corp_name") or "").strip()
        if not corp_code or not corp_name:
            continue
        rows.append({
            "corp_code": corp_code,
            "corp_name": corp_name,
            "normalized_name": normalize_company_name(corp_name),
            "stock_code": (item.findtext("stock_code") or "").strip(),
            "modify_date": (item.findtext("modify_date") or "").strip(),
        })

    target = Path(target_csv)
    target.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(target, index=False, encoding="utf-8-sig")
    return len(rows)


def load_corp_codes(target_csv: str | Path) -> pd.DataFrame:
    path = Path(target_csv)
    if not path.exists():
        return pd.DataFrame(
            columns=["corp_code", "corp_name", "normalized_name", "stock_code", "modify_date"]
        )
    df = pd.read_csv(path, dtype=str, encoding="utf-8-sig").fillna("")
    if "corp_code" in df.columns:
        df["corp_code"] = df["corp_code"].map(normalize_corp_code)
    if "normalized_name" not in df.columns:
        df["normalized_name"] = df["corp_name"].map(normalize_company_name)
    else:
        empty_normalized = df["normalized_name"].astype(str).str.strip().eq("")
        df.loc[empty_normalized, "normalized_name"] = (
            df.loc[empty_normalized, "corp_name"].map(normalize_company_name)
        )
    return df


def find_company_match(
    company_name: str,
    corp_df: pd.DataFrame,
    mapped_corp_code: str = "",
    mapped_corp_name: str = "",
) -> dict[str, Any]:
    """회사명을 DART 법인목록에 매칭합니다.

    확정 우선순위: 기존 매핑/수기 고유번호 → 정규화 완전일치 → 매우 높은 단일 유사도.
    그 밖에는 모두 확인필요로 반환합니다.
    """
    if corp_df.empty:
        return {"matched": False, "status": "확인필요: DART 기업목록 없음", "suggestions": []}

    normalized_mapped_code = normalize_corp_code(mapped_corp_code)
    if normalized_mapped_code:
        hit = corp_df[corp_df["corp_code"].map(normalize_corp_code) == normalized_mapped_code]
        if len(hit) == 1:
            row = hit.iloc[0]
            return {
                "matched": True,
                "corp_code": normalize_corp_code(row["corp_code"]),
                "corp_name": row["corp_name"],
                "method": "저장/수기 매핑",
                "score": 1.0,
            }
        return {
            "matched": False,
            "status": f"확인필요: 입력한 DART 고유번호 없음({normalized_mapped_code})",
            "suggestions": [],
        }

    normalized = normalize_company_name(company_name)
    if not normalized:
        return {"matched": False, "status": "확인필요: 회사명 없음", "suggestions": []}

    exact = corp_df[corp_df["normalized_name"] == normalized]
    if len(exact) == 1:
        row = exact.iloc[0]
        return {
            "matched": True,
            "corp_code": row["corp_code"],
            "corp_name": row["corp_name"],
            "method": "정규화 완전일치",
            "score": 1.0,
        }
    if len(exact) > 1:
        suggestions = exact[["corp_code", "corp_name"]].head(5).to_dict("records")
        return {"matched": False, "status": "확인필요: 동일 회사명 복수", "suggestions": suggestions}

    # 전체 공시회사에 대한 유사도 계산. 자동확정 기준은 보수적으로 설정.
    scored: list[tuple[float, str, str]] = []
    for row in corp_df[["corp_code", "corp_name", "normalized_name"]].itertuples(index=False):
        candidate = str(row.normalized_name or "")
        if not candidate:
            continue
        # 첫 글자나 핵심 길이가 크게 다르면 계산량과 오매칭을 줄임
        if normalized[0] != candidate[0]:
            continue
        score = SequenceMatcher(None, normalized, candidate).ratio()
        if normalized in candidate or candidate in normalized:
            score = min(1.0, score + 0.04)
        if score >= 0.72:
            scored.append((score, str(row.corp_code), str(row.corp_name)))

    scored.sort(reverse=True)
    suggestions = [
        {"corp_code": code, "corp_name": name, "score": round(score, 3)}
        for score, code, name in scored[:5]
    ]
    if not scored:
        return {"matched": False, "status": "확인필요: DART 회사 매칭 실패", "suggestions": []}

    # 완전일치가 아닌 유사매칭은 자동 확정하지 않습니다.
    # 담당자가 후보 목록을 확인해 DART 고유번호를 직접 지정해야 합니다.
    return {
        "matched": False,
        "status": "확인필요: 회사 매칭 불확실",
        "suggestions": suggestions,
    }


def _parse_amount(value: Any) -> float | None:
    text = str(value or "").replace(",", "").strip()
    if text in {"", "-"}:
        return None
    text = text.replace("(", "-").replace(")", "")
    try:
        return float(text)
    except ValueError:
        return None


def _revenue_score(row: dict[str, Any]) -> int:
    account_id = str(row.get("account_id", "")).lower()
    account_name = re.sub(r"\s+", "", str(row.get("account_nm", "")))
    sj_div = str(row.get("sj_div", ""))

    if any(word in account_name for word in ["매출원가", "영업비용", "수익비용", "매출채권"]):
        return -100

    score = 0
    if sj_div == "IS":
        score += 20
    elif sj_div == "CIS":
        score += 10
    if account_id in {"ifrs-full_revenue", "ifrs_revenue", "dart_revenue"}:
        score += 100
    elif "revenue" in account_id:
        score += 70

    exact_names = {"매출액", "수익(매출액)", "영업수익", "수익", "매출"}
    if account_name in exact_names:
        score += 80
    elif any(keyword in account_name for keyword in ["매출액", "영업수익", "수익(매출액)"]):
        score += 45
    return score


def fetch_consolidated_revenue(api_key: str, corp_code: str, business_year: int) -> dict[str, Any]:
    if not api_key or len(api_key.strip()) < 20:
        return {"ok": False, "status": "확인필요: DART API 키 없음"}

    corp_code = normalize_corp_code(corp_code)
    if not corp_code:
        return {"ok": False, "status": "확인필요: 유효한 DART 고유번호 없음"}

    response = requests.get(
        DART_FINANCIAL_URL,
        params={
            "crtfc_key": api_key.strip(),
            "corp_code": corp_code,
            "bsns_year": str(int(business_year)),
            "reprt_code": ANNUAL_REPORT_CODE,
            "fs_div": CONSOLIDATED_FS,
        },
        timeout=60,
    )
    response.raise_for_status()
    payload = _response_json(response)
    status = str(payload.get("status", ""))
    if status != "000":
        return {
            "ok": False,
            "status": f"확인필요: {payload.get('message', 'DART 조회 실패')}",
            "dart_status": status,
        }

    rows = payload.get("list") or []
    candidates: list[tuple[int, float, dict[str, Any]]] = []
    for row in rows:
        score = _revenue_score(row)
        amount = _parse_amount(row.get("thstrm_amount"))
        if score > 0 and amount is not None:
            candidates.append((score, amount, row))

    if not candidates:
        return {"ok": False, "status": "확인필요: 연결매출 계정 없음"}

    candidates.sort(key=lambda item: (item[0], abs(item[1])), reverse=True)
    score, amount_won, row = candidates[0]
    if amount_won < 0:
        return {"ok": False, "status": "확인필요: 연결매출 금액 이상"}

    return {
        "ok": True,
        "status": "조회완료",
        "business_year": int(business_year),
        "revenue_won": amount_won,
        "revenue_eok": amount_won / 100_000_000,
        "account_name": row.get("account_nm", ""),
        "account_id": row.get("account_id", ""),
        "rcept_no": row.get("rcept_no", ""),
        "currency": row.get("currency", "KRW"),
        "score": score,
    }


def apply_revenue_rule(revenue_eok: float, rules: list[dict[str, Any]]) -> dict[str, Any] | None:
    value = float(revenue_eok)
    normalized_rules = sorted(
        rules,
        key=lambda rule: float(rule.get("min_revenue_eok", 0) or 0),
        reverse=True,
    )
    for rule in normalized_rules:
        minimum = float(rule.get("min_revenue_eok", 0) or 0)
        maximum_raw = rule.get("max_revenue_eok")
        maximum = None if maximum_raw is None or pd.isna(maximum_raw) or str(maximum_raw).strip() == "" else float(maximum_raw)
        if value >= minimum and (maximum is None or value < maximum):
            return rule
    return None

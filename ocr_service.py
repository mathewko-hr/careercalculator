from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
import os
import re
import shutil
from typing import Any

import fitz
import pandas as pd
import pytesseract
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

try:  # 고급 전처리. 설치되지 않아도 기본 OCR은 동작합니다.
    import cv2
    import numpy as np
except Exception:  # pragma: no cover - optional fallback
    cv2 = None
    np = None


OCR_SERVICE_VERSION = "3.4.1"

# 2024.01.31 / 2024-01-31 / 2024년 1월 31일 / 20240131
DATE_PATTERN = re.compile(
    r"(?<!\d)(?:(?P<y1>19\d{2}|20\d{2})\s*[.\-/년]\s*(?P<m1>\d{1,2})\s*[.\-/월]\s*(?P<d1>\d{1,2})\s*일?"
    r"|(?P<y2>19\d{2}|20\d{2})(?P<m2>\d{2})(?P<d2>\d{2}))(?!\d)"
)

HEALTH_KEYWORDS = (
    "건강보험", "자격득실", "사업장", "사업장명", "사업장명칭",
    "자격취득", "취득일", "자격상실", "상실일", "직장가입자",
)

# Windows 기본 설치경로를 우선 사용합니다. 설치 위치가 다르면 아래 두 줄만 바꾸면 됩니다.
TESSERACT_EXE = Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe")
TESSDATA_DIR = Path(r"C:\Program Files\Tesseract-OCR\tessdata")

COMMON_TESSERACT_PATHS = [
    TESSERACT_EXE,
    Path(r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"),
]

NULLISH = {"", "none", "nan", "<na>", "nat"}


def configure_tesseract() -> str | None:
    """Tesseract 실행파일을 찾아 pytesseract에 연결합니다."""
    # 1) 이 파일에 명시한 Windows 기본 경로를 가장 먼저 확인
    if TESSERACT_EXE.exists():
        pytesseract.pytesseract.tesseract_cmd = str(TESSERACT_EXE)
        return str(TESSERACT_EXE)

    # 2) 필요하면 환경변수 TESSERACT_CMD로 별도 경로 지정 가능
    configured = os.environ.get("TESSERACT_CMD", "").strip()
    if configured and Path(configured).exists():
        pytesseract.pytesseract.tesseract_cmd = configured
        return configured

    # 3) Windows PATH에 등록되어 있으면 자동 탐색
    command = shutil.which("tesseract")
    if command:
        pytesseract.pytesseract.tesseract_cmd = command
        return command

    # 4) 흔한 설치 위치 추가 탐색
    for path in COMMON_TESSERACT_PATHS:
        if path.exists():
            pytesseract.pytesseract.tesseract_cmd = str(path)
            return str(path)
    return None


def _candidate_tessdata_dirs(command: str | None = None) -> list[Path]:
    """설치 위치를 기준으로 사용할 수 있는 tessdata 폴더 후보를 반환합니다."""
    candidates: list[Path] = []
    if command:
        try:
            candidates.append(Path(command).resolve().parent / "tessdata")
        except Exception:
            candidates.append(Path(command).parent / "tessdata")
    candidates.extend([
        TESSDATA_DIR,
        Path(r"C:\\Program Files (x86)\\Tesseract-OCR\\tessdata"),
    ])

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _language_files_in(path: Path) -> list[str]:
    if not path.exists() or not path.is_dir():
        return []
    return sorted(file.stem for file in path.glob("*.traineddata"))


def _detect_languages(command: str) -> tuple[list[str], str]:
    """
    Tesseract 자체 기본 탐색을 먼저 사용합니다.
    실패하면 설치 폴더의 tessdata를 TESSDATA_PREFIX로 지정해 재시도합니다.
    --tessdata-dir 문자열을 직접 넘기지 않아 Windows 공백/따옴표 문제를 피합니다.
    """
    previous_prefix = os.environ.get("TESSDATA_PREFIX")

    # 1) Tesseract 기본 탐색. 일반적인 Windows 설치에서는 이것으로 충분합니다.
    try:
        if "TESSDATA_PREFIX" in os.environ:
            del os.environ["TESSDATA_PREFIX"]
        languages = list(pytesseract.get_languages(config=""))
        if languages:
            return languages, "Tesseract 기본 경로"
    except Exception:
        pass

    # 2) 설치 폴더/tessdata를 직접 환경변수로 지정해 재시도.
    last_error: Exception | None = None
    for tessdata_dir in _candidate_tessdata_dirs(command):
        if not tessdata_dir.exists():
            continue
        if not _language_files_in(tessdata_dir):
            continue

        for prefix in (str(tessdata_dir), str(tessdata_dir.parent)):
            try:
                os.environ["TESSDATA_PREFIX"] = prefix
                languages = list(pytesseract.get_languages(config=""))
                if languages:
                    return languages, f"TESSDATA_PREFIX={prefix}"
            except Exception as exc:
                last_error = exc

    # 실패 시 원래 환경변수 복원
    if previous_prefix is None:
        os.environ.pop("TESSDATA_PREFIX", None)
    else:
        os.environ["TESSDATA_PREFIX"] = previous_prefix

    if last_error:
        raise last_error
    raise RuntimeError("Tesseract 언어데이터를 찾지 못했습니다.")


def _ocr_language(status: dict[str, Any]) -> str:
    """설치된 언어팩에 맞춰 사용할 OCR 언어를 결정합니다."""
    languages = set(status.get("languages", []))
    if "kor" in languages and "eng" in languages:
        return "kor+eng"
    if "kor" in languages:
        return "kor"
    if "eng" in languages:
        return "eng"
    # 언어 목록 확인 자체가 실패했을 때의 최후 fallback
    return "eng"


def tesseract_status() -> dict[str, Any]:
    command = configure_tesseract()
    if not command:
        return {
            "available": False,
            "command": "",
            "has_korean": False,
            "has_english": False,
            "languages": [],
            "tessdata_dir": str(TESSDATA_DIR),
            "message": (
                "Tesseract 실행파일을 찾지 못했습니다. "
                f"먼저 {TESSERACT_EXE} 위치를 확인하세요. "
                "문자형 PDF는 분석할 수 있지만 스캔 PDF·이미지 OCR은 사용할 수 없습니다."
            ),
        }

    try:
        version = str(pytesseract.get_tesseract_version())
        languages, language_source = _detect_languages(command)
        has_korean = "kor" in languages
        has_english = "eng" in languages

        if has_korean:
            message = "Tesseract 및 한국어(kor) 언어데이터 사용 가능"
        else:
            message = (
                "Tesseract 실행파일은 정상이나 한국어(kor) 언어데이터가 없습니다. "
                f"{TESSDATA_DIR / 'kor.traineddata'} 파일을 확인하세요."
            )

        return {
            "available": True,
            "command": command,
            "version": version,
            "languages": languages,
            "has_korean": has_korean,
            "has_english": has_english,
            "tessdata_dir": str(TESSDATA_DIR),
            "language_source": language_source,
            "message": message,
        }
    except Exception as exc:
        return {
            "available": False,
            "command": command,
            "has_korean": False,
            "has_english": False,
            "languages": [],
            "tessdata_dir": str(TESSDATA_DIR),
            "message": f"Tesseract 확인 중 오류: {exc}",
        }


def mask_sensitive_text(text: str) -> str:
    # 주민등록번호 형식 마스킹. 생년월일 앞 6자리는 유지합니다.
    return re.sub(r"(\d{6})\s*[- ]?\s*(\d{7})", r"\1-*******", text or "")


def normalize_for_quality(text: str) -> str:
    text = (text or "").replace("\u00a0", " ")
    text = text.replace("．", ".").replace("。", ".")
    text = text.replace("–", "-").replace("—", "-")
    return text


def _date_from_match(match: re.Match[str]) -> datetime | None:
    try:
        if match.group("y1"):
            y, m, d = int(match.group("y1")), int(match.group("m1")), int(match.group("d1"))
        else:
            y, m, d = int(match.group("y2")), int(match.group("m2")), int(match.group("d2"))
        return datetime(y, m, d)
    except (TypeError, ValueError):
        return None


def find_dates(text: str) -> list[tuple[re.Match[str], datetime]]:
    output: list[tuple[re.Match[str], datetime]] = []
    for match in DATE_PATTERN.finditer(normalize_for_quality(text)):
        parsed = _date_from_match(match)
        if parsed:
            output.append((match, parsed))
    return output


def text_quality(text: str, health_document: bool = True) -> dict[str, Any]:
    """직접추출/OCR 결과가 실제 건강보험 내역을 읽을 만한지 점수화합니다."""
    text = normalize_for_quality(text)
    compact = re.sub(r"\s+", "", text)
    dates = find_dates(text)
    date_count = len(dates)
    keyword_count = sum(1 for word in HEALTH_KEYWORDS if word in text)
    hangul_count = len(re.findall(r"[가-힣]", text))
    latin_count = len(re.findall(r"[A-Za-z]", text))
    replacement_count = text.count("�") + text.count("□") + text.count("■")

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    two_date_lines = sum(1 for line in lines if len(find_dates(line)) >= 2)

    score = 0.0
    score += min(len(compact) / 20.0, 15.0)
    score += min(date_count * 5.0, 30.0)
    score += min(keyword_count * 3.0, 15.0)
    score += min(hangul_count / 6.0, 20.0)
    score += min(two_date_lines * 5.0, 15.0)

    if compact:
        bad_ratio = replacement_count / max(len(compact), 1)
        score -= min(bad_ratio * 250.0, 25.0)
    else:
        bad_ratio = 1.0

    if health_document and date_count < 2:
        score -= 20.0
    if health_document and hangul_count < 4:
        score -= 10.0

    score = max(0.0, min(round(score, 1), 100.0))
    return {
        "score": score,
        "length": len(compact),
        "date_count": date_count,
        "keyword_count": keyword_count,
        "hangul_count": hangul_count,
        "latin_count": latin_count,
        "two_date_lines": two_date_lines,
        "bad_char_ratio": round(bad_ratio, 4),
    }


def _quality_is_good(metrics: dict[str, Any], health_document: bool = True) -> bool:
    if health_document:
        return (
            metrics.get("score", 0) >= 48
            and metrics.get("date_count", 0) >= 2
            and metrics.get("hangul_count", 0) >= 4
        )
    return metrics.get("score", 0) >= 35 and metrics.get("length", 0) >= 30


def pixmap_to_image(pixmap: fitz.Pixmap) -> Image.Image:
    mode = "RGBA" if pixmap.alpha else "RGB"
    return Image.frombytes(mode, [pixmap.width, pixmap.height], pixmap.samples)


def _auto_orient(image: Image.Image) -> tuple[Image.Image, int]:
    """Tesseract OSD가 사용 가능하면 90/180/270도 회전을 자동 보정합니다."""
    try:
        data = pytesseract.image_to_osd(image, output_type=pytesseract.Output.DICT, timeout=25)
        rotation = int(data.get("rotate", 0) or 0)
        if rotation in {90, 180, 270}:
            return image.rotate(-rotation, expand=True, fillcolor="white"), rotation
    except Exception:
        pass
    return image, 0


def _deskew_cv(gray_array: Any) -> Any:
    if cv2 is None or np is None:
        return gray_array
    try:
        inv = cv2.threshold(gray_array, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        coords = np.column_stack(np.where(inv > 0))
        if coords.shape[0] < 100:
            return gray_array
        angle = cv2.minAreaRect(coords)[-1]
        if angle < -45:
            angle = -(90 + angle)
        else:
            angle = -angle
        # 큰 각도는 표/도형 때문에 오검출됐을 가능성이 높으므로 무시합니다.
        if abs(angle) > 12:
            return gray_array
        h, w = gray_array.shape[:2]
        center = (w // 2, h // 2)
        matrix = cv2.getRotationMatrix2D(center, angle, 1.0)
        return cv2.warpAffine(
            gray_array,
            matrix,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=255,
        )
    except Exception:
        return gray_array


def _preprocess_variants(image: Image.Image) -> list[tuple[str, Image.Image]]:
    """표/스캔/저대비 문서에 대응하는 OCR 입력 후보를 만듭니다."""
    base = image.convert("RGB")
    gray = ImageOps.grayscale(base)
    auto = ImageOps.autocontrast(gray, cutoff=1)
    auto = ImageEnhance.Contrast(auto).enhance(1.35)
    auto = auto.filter(ImageFilter.SHARPEN)

    variants: list[tuple[str, Image.Image]] = [("대비향상", auto)]

    if cv2 is not None and np is not None:
        arr = np.array(auto)
        arr = _deskew_cv(arr)
        # 배경 음영이 있는 스캔에 강한 adaptive threshold
        binary = cv2.adaptiveThreshold(
            arr,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            31,
            15,
        )
        binary = cv2.medianBlur(binary, 3)
        variants.append(("이진화+기울기보정", Image.fromarray(binary)))
    else:
        # OpenCV가 없을 때 PIL 기반 이진화 대체
        binary = auto.point(lambda p: 255 if p > 175 else 0)
        variants.append(("이진화", binary))

    return variants


def _ocr_once(image: Image.Image, language: str, psm: int) -> str:
    command = configure_tesseract()
    if not command:
        raise RuntimeError(
            "Tesseract 실행파일을 찾지 못했습니다. "
            f"{TESSERACT_EXE} 위치에 tesseract.exe가 있는지 확인하세요."
        )

    config_parts = [
        "--oem 3",
        f"--psm {psm}",
        "-c preserve_interword_spaces=1",
    ]
    # tessdata 경로는 tesseract_status()에서 자동 검증/설정합니다.
    # Windows 경로의 공백/따옴표 문제를 피하기 위해 --tessdata-dir은 직접 넘기지 않습니다.
    return pytesseract.image_to_string(
        image,
        lang=language,
        config=" ".join(config_parts),
        timeout=150,
    )


def _ocr_page_multistrategy(
    page: fitz.Page,
    status: dict[str, Any],
    health_document: bool,
) -> dict[str, Any]:
    """한 페이지를 여러 OCR 전략으로 읽고 건강보험 양식에 가장 적합한 결과를 선택합니다."""
    language = _ocr_language(status)
    attempts: list[dict[str, Any]] = []

    def run_for_image(image: Image.Image, dpi: int) -> None:
        oriented, rotation = _auto_orient(image)
        for variant_name, variant in _preprocess_variants(oriented):
            # 표형 문서는 psm 6, 열/희소 텍스트는 psm 11을 보조로 사용합니다.
            for psm in (6, 11):
                try:
                    text = _ocr_once(variant, language=language, psm=psm)
                    quality = text_quality(text, health_document=health_document)
                    attempts.append({
                        "text": text,
                        "score": quality["score"],
                        "quality": quality,
                        "strategy": f"{dpi}dpi/{variant_name}/PSM{psm}",
                        "rotation": rotation,
                    })
                    if _quality_is_good(quality, health_document=health_document) and quality["score"] >= 68:
                        return
                except Exception as exc:
                    attempts.append({
                        "text": "",
                        "score": 0,
                        "quality": text_quality("", health_document=health_document),
                        "strategy": f"{dpi}dpi/{variant_name}/PSM{psm}",
                        "rotation": rotation,
                        "error": str(exc),
                    })

    pixmap = page.get_pixmap(dpi=320, alpha=False, colorspace=fitz.csRGB)
    run_for_image(pixmap_to_image(pixmap), 320)

    best = max(attempts, key=lambda item: item.get("score", 0), default=None)
    # 여전히 품질이 낮으면 고해상도로 한 번 더 도전합니다.
    if not best or best.get("score", 0) < 48:
        pixmap = page.get_pixmap(dpi=400, alpha=False, colorspace=fitz.csRGB)
        image = pixmap_to_image(pixmap)
        oriented, rotation = _auto_orient(image)
        variants = _preprocess_variants(oriented)
        # 가장 강한 전처리만 400dpi로 재시도하여 처리시간을 제한합니다.
        variant_name, variant = variants[-1]
        for psm in (6, 11):
            try:
                text = _ocr_once(variant, language=language, psm=psm)
                quality = text_quality(text, health_document=health_document)
                attempts.append({
                    "text": text,
                    "score": quality["score"],
                    "quality": quality,
                    "strategy": f"400dpi/{variant_name}/PSM{psm}",
                    "rotation": rotation,
                })
            except Exception as exc:
                attempts.append({
                    "text": "",
                    "score": 0,
                    "quality": text_quality("", health_document=health_document),
                    "strategy": f"400dpi/{variant_name}/PSM{psm}",
                    "rotation": rotation,
                    "error": str(exc),
                })

    best = max(attempts, key=lambda item: item.get("score", 0), default={
        "text": "", "score": 0, "quality": text_quality("", health_document=health_document),
        "strategy": "OCR 실패", "rotation": 0,
    })
    best["attempt_count"] = len(attempts)
    return best


def _extract_direct_page_text(page: fitz.Page) -> str:
    # sort=True로 시각적 읽기 순서에 가깝게 정렬합니다.
    candidates = []
    try:
        candidates.append(page.get_text("text", sort=True) or "")
    except Exception:
        pass
    try:
        # 일부 PDF는 blocks 추출이 더 정상적인 경우가 있습니다.
        blocks = page.get_text("blocks", sort=True) or []
        block_text = "\n".join(str(block[4]) for block in blocks if len(block) > 4)
        candidates.append(block_text)
    except Exception:
        pass
    if not candidates:
        return ""
    return max(candidates, key=lambda value: text_quality(value)["score"])


def extract_pdf_text(
    path: Path,
    force_ocr: bool = False,
    password: str = "",
    health_document: bool = True,
) -> dict[str, Any]:
    document = fitz.open(path)
    password_used = False

    try:
        if document.needs_pass:
            clean_password = str(password or "").strip()
            if not clean_password:
                raise RuntimeError("암호화된 PDF입니다. 생년월일 6자리 비밀번호를 입력하세요.")
            if not re.fullmatch(r"\d{6}", clean_password):
                raise RuntimeError("PDF 비밀번호는 생년월일 6자리 숫자로 입력하세요. 예: 900101")
            if document.authenticate(clean_password) <= 0:
                raise RuntimeError("PDF 비밀번호가 일치하지 않습니다. 생년월일 6자리를 확인하세요.")
            password_used = True

        status = tesseract_status()
        page_texts: list[str] = []
        page_diagnostics: list[dict[str, Any]] = []
        used_ocr = False
        direct_pages = 0
        ocr_pages = 0

        for page_no, page in enumerate(document, start=1):
            direct_text = _extract_direct_page_text(page).strip()
            direct_quality = text_quality(direct_text, health_document=health_document)
            chosen_text = direct_text
            chosen_method = "직접 텍스트 추출"
            chosen_quality = direct_quality
            ocr_detail: dict[str, Any] | None = None

            should_ocr = force_ocr or not _quality_is_good(direct_quality, health_document=health_document)
            if should_ocr and status.get("available"):
                ocr_detail = _ocr_page_multistrategy(page, status=status, health_document=health_document)
                # 강제 OCR이면 OCR을 우선하되 완전히 망가진 경우 직접추출이 더 좋은 결과면 되돌립니다.
                if ocr_detail.get("score", 0) > direct_quality.get("score", 0) + (0 if force_ocr else 3):
                    chosen_text = ocr_detail.get("text", "")
                    chosen_quality = ocr_detail.get("quality", direct_quality)
                    chosen_method = f"OCR {ocr_detail.get('strategy', '')}"
                    used_ocr = True
                    ocr_pages += 1
                else:
                    direct_pages += 1
            else:
                direct_pages += 1

            page_texts.append(chosen_text)
            page_diagnostics.append({
                "page": page_no,
                "method": chosen_method,
                "score": chosen_quality.get("score", 0),
                "date_count": chosen_quality.get("date_count", 0),
                "hangul_count": chosen_quality.get("hangul_count", 0),
                "direct_score": direct_quality.get("score", 0),
                "ocr_score": ocr_detail.get("score", 0) if ocr_detail else None,
                "ocr_attempts": ocr_detail.get("attempt_count", 0) if ocr_detail else 0,
            })

        extracted_text = "\n".join(page_texts)
        total_quality = text_quality(extracted_text, health_document=health_document)
        message = (
            f"PDF {len(document)}페이지 처리 · 직접추출 {direct_pages}페이지 · OCR 선택 {ocr_pages}페이지 · "
            f"문서품질 {total_quality['score']}/100 · 날짜 {total_quality['date_count']}건"
        )
        if password_used:
            message += " · 암호화 PDF 인증 완료"
        if not status.get("available") and not _quality_is_good(total_quality, health_document=health_document):
            message += " · 직접추출 품질이 낮지만 Tesseract를 사용할 수 없어 OCR 재시도를 못했습니다."
        elif status.get("available") and not status.get("has_korean"):
            message += " · 한국어(kor) 언어데이터가 없어 회사명 OCR 정확도가 낮을 수 있습니다."

        return {
            "text": mask_sensitive_text(extracted_text),
            "ocr_used": used_ocr,
            "password_used": password_used,
            "message": message,
            "quality_score": total_quality["score"],
            "diagnostics": {
                "overall": total_quality,
                "pages": page_diagnostics,
                "tesseract": {
                    "available": status.get("available", False),
                    "has_korean": status.get("has_korean", False),
                    "version": status.get("version", ""),
                },
            },
        }
    finally:
        document.close()


def extract_image_text(path: Path, health_document: bool = True) -> dict[str, Any]:
    status = tesseract_status()
    if not status.get("available"):
        raise RuntimeError(status["message"])

    image = Image.open(path).convert("RGB")
    language = _ocr_language(status)
    image, rotation = _auto_orient(image)
    attempts: list[dict[str, Any]] = []

    for variant_name, variant in _preprocess_variants(image):
        for psm in (6, 11):
            text = _ocr_once(variant, language=language, psm=psm)
            quality = text_quality(text, health_document=health_document)
            attempts.append({
                "text": text,
                "quality": quality,
                "score": quality["score"],
                "strategy": f"{variant_name}/PSM{psm}",
            })

    best = max(attempts, key=lambda item: item["score"], default={
        "text": "", "quality": text_quality("", health_document=health_document), "score": 0, "strategy": "실패"
    })
    message = f"이미지 OCR 완료 · {best['strategy']} · 품질 {best['score']}/100"
    if not status.get("has_korean"):
        message += " · 한국어(kor) 언어데이터 없음"

    return {
        "text": mask_sensitive_text(best["text"]),
        "ocr_used": True,
        "message": message,
        "quality_score": best["score"],
        "diagnostics": {
            "overall": best["quality"],
            "pages": [{"page": 1, "method": f"OCR {best['strategy']}", "score": best["score"], "rotation": rotation}],
            "tesseract": {
                "available": True,
                "has_korean": status.get("has_korean", False),
                "version": status.get("version", ""),
            },
        },
    }


def extract_text_from_file(
    path: str | Path,
    force_ocr: bool = False,
    password: str = "",
    health_document: bool = True,
) -> dict[str, Any]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        return extract_pdf_text(
            file_path,
            force_ocr=force_ocr,
            password=password,
            health_document=health_document,
        )
    if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}:
        return extract_image_text(file_path, health_document=health_document)

    raise ValueError(f"지원하지 않는 파일 형식입니다: {suffix}")


EMPLOYMENT_PATTERN = re.compile(r"(?:직장\s*가입자|사업장\s*가입자)")
NON_EMPLOYMENT_PATTERN = re.compile(
    r"(?:직장\s*피부양자|피부양자|지역\s*세대주|지역\s*세대원|지역\s*가입자|임의\s*계속\s*가입자)"
)
ROW_NUMBER_ONLY_PATTERN = re.compile(r"^\s*\d{1,3}\s*[|ㅣ」』\]]?\s*$")
DOCUMENT_NOISE_PATTERN = re.compile(
    r"(?:건강보험.*자격득실|자격득실.*확인|국민건강보험공단|발급번호|확인청구자|"
    r"가입자구분|사업장명칭|사엄장명칭|자격취득일|자격상실일|취득일|상실일|이하여백|"
    r"실제의\s*사업장|법적인\s*책임|재직증명|경력증명|대출용)"
)


def clean_company_name(text: str) -> str:
    """OCR 잔여기호·행번호·자격구분을 제거해 사업장명만 남깁니다."""
    text = normalize_for_quality(text)
    text = DATE_PATTERN.sub(" ", text)

    # 표의 행번호 및 OCR에서 붙는 세로선/괄호형 기호 제거
    text = re.sub(r"^\s*\d{1,3}\s*[|ㅣ」』\]]*\s*", "", text)
    text = re.sub(r"^[\s|ㅣ」』【】\[\]{}:;·•=_~]+", "", text)

    # 회사명 좌측의 자격구분 및 표 머리글 제거
    text = EMPLOYMENT_PATTERN.sub(" ", text)
    text = NON_EMPLOYMENT_PATTERN.sub(" ", text)
    text = re.sub(
        r"(?:가입자구분|가임자구분|사업장명칭|사엄장명칭|사업장명|자격취득일자?|취득일자?|"
        r"자격상실일자?|상실일자?|자격득실내역|구분|가입자종별)",
        " ",
        text,
    )

    # OCR에서 회사명 앞에 남는 특수문자 제거. (주)는 보존합니다.
    text = re.sub(r"^[\s|ㅣ」』:;·•=_~\-]+", "", text)
    text = re.sub(r"[|ㅣ:：•·]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip(" -_/.,;:|ㅣ」』")
    return text


def is_excluded_line(text: str) -> bool:
    value = normalize_for_quality(text)
    if not value:
        return True
    if NON_EMPLOYMENT_PATTERN.search(value):
        return True
    if DOCUMENT_NOISE_PATTERN.search(value):
        return True
    excluded_words = [
        "건강보험자격득실확인서", "국민건강보험공단", "민원여기요",
        "주민등록번호", "확인용", "발급일", "성명",
    ]
    return any(word in value for word in excluded_words)


def _company_likeness(text: str) -> float:
    value = clean_company_name(text)
    if len(value) < 2 or len(value) > 80:
        return -100
    if is_excluded_line(value):
        return -100
    if EMPLOYMENT_PATTERN.search(value) or NON_EMPLOYMENT_PATTERN.search(value):
        return -100

    letters = len(re.findall(r"[가-힣A-Za-z]", value))
    digits = len(re.findall(r"\d", value))
    if letters < 2:
        return -100

    # 문서 안내문/표 머리글처럼 긴 일반문장은 회사명으로 보지 않습니다.
    if any(token in value for token in ("확인합니다", "알려드립니다", "자격득실", "확인내역", "이하여백")):
        return -100

    score = letters * 2 - digits
    if any(token in value for token in (
        "주식회사", "(주)", "㈜", "유한회사", "유한책임회사", "법인", "병원",
        "학교", "센터", "공사", "공단", "테크", "시스템", "산업", "전자", "건설",
    )):
        score += 7
    return score


def _coerce_deadline(value: date | datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    parsed = pd.to_datetime(value, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.to_pydatetime()


def _is_row_boundary(line: str) -> bool:
    value = normalize_for_quality(line)
    if not value:
        return False
    if ROW_NUMBER_ONLY_PATTERN.fullmatch(value):
        return True
    if EMPLOYMENT_PATTERN.search(value) or NON_EMPLOYMENT_PATTERN.search(value):
        return True
    if re.match(r"^\s*\d{1,3}\s*[|ㅣ」』\]]", value):
        return True
    if DOCUMENT_NOISE_PATTERN.search(value):
        return True
    return False


def _extract_company_from_employment_chunk(chunk: str) -> str:
    """직장가입자 행/행묶음에서 회사명만 추출합니다."""
    marker = EMPLOYMENT_PATTERN.search(chunk)
    if not marker:
        return ""

    tail = chunk[marker.end():]
    dates = find_dates(tail)
    before_first_date = tail[:dates[0][0].start()] if dates else tail

    candidates: list[str] = []
    for part in before_first_date.split("\n"):
        cleaned = clean_company_name(part)
        if _company_likeness(cleaned) > 1:
            candidates.append(cleaned)

    # PDF 추출 순서가 뒤집혀 회사명이 날짜 뒤에 나타난 경우도 같은 행 범위 안에서만 보완합니다.
    if not candidates:
        without_dates = DATE_PATTERN.sub(" ", tail)
        for part in without_dates.split("\n"):
            cleaned = clean_company_name(part)
            if _company_likeness(cleaned) > 1:
                candidates.append(cleaned)

    if not candidates:
        return ""
    return max(candidates, key=lambda item: (_company_likeness(item), len(item)))


def parse_health_insurance_career(
    text: str,
    subtract_one_day_from_loss_date: bool = True,
    current_employment_end_date: date | datetime | str | None = None,
) -> pd.DataFrame:
    """
    건강보험 자격득실확인서에서 '직장가입자' 행만 경력으로 추출합니다.

    원칙
    - 직장가입자/사업장가입자만 경력 후보로 인정
    - 지역세대주·지역세대원·지역가입자·직장피부양자는 모두 제외
    - 회사명이 비어 있거나 문서 잡문으로 판단되면 행 자체를 제외
    - 상실일이 없는 직장가입자는 재직중으로 보고 지원서 마감일을 종료일로 사용
    - 상실일이 있는 행만 선택적으로 1일 차감
    """
    normalized = normalize_for_quality(text).replace("\r", "\n")
    normalized = re.sub(r"(?<=\d)\s+([.\-/])\s+(?=\d)", r"\1", normalized)
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in normalized.split("\n")
        if re.sub(r"\s+", "", line)
    ]

    deadline_dt = _coerce_deadline(current_employment_end_date)
    rows_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}

    def add_row(
        company: str,
        start_dt: datetime,
        end_dt: datetime | None,
        is_current: bool = False,
    ) -> None:
        company = clean_company_name(company)
        if _company_likeness(company) <= 1:
            return

        if is_current:
            # 재직중은 상실일이 없으므로 지원서 마감일을 기준으로 산정합니다.
            actual_end = deadline_dt
            if actual_end is not None and actual_end < start_dt:
                return
            end_key = actual_end.strftime("%Y-%m-%d") if actual_end else "CURRENT"
            note = (
                f"재직중(자격상실일 없음): 지원서 마감일 {actual_end:%Y-%m-%d} 적용. 원본과 대조하세요."
                if actual_end else
                "재직중(자격상실일 없음): 지원서 마감일을 입력해 종료일을 확정하세요."
            )
        else:
            if end_dt is None or end_dt < start_dt or (end_dt - start_dt).days > 60 * 366:
                return
            actual_end = end_dt - timedelta(days=1) if subtract_one_day_from_loss_date and end_dt > start_dt else end_dt
            end_key = actual_end.strftime("%Y-%m-%d")
            note = "자동추출: 직장가입자 사업장명·취득/상실일을 원본과 대조하세요."

        key = (company, start_dt.strftime("%Y-%m-%d"), end_key)
        rows_by_key[key] = {
            "회사명": company,
            "입사일": start_dt.date(),
            "퇴사일": actual_end.date() if actual_end else pd.NaT,
            "직무/직책": "",
            "인정구분": "검토필요",
            "인정률(%)": 0,
            "출처": "건강보험 OCR",
            "출처파일": "",
            "비고": note,
        }

    # 직장가입자 표식이 있는 행만 시작점으로 사용합니다.
    for idx, line in enumerate(lines):
        if not EMPLOYMENT_PATTERN.search(line):
            continue
        if NON_EMPLOYMENT_PATTERN.search(line):
            continue

        chunk_lines = [line]
        # 회사명/날짜가 다음 줄로 분리된 PDF를 위해 동일 행 범위만 제한적으로 합칩니다.
        for j in range(idx + 1, min(len(lines), idx + 6)):
            candidate = lines[j]
            if _is_row_boundary(candidate):
                break
            chunk_lines.append(candidate)
            # 정상 종료행은 날짜 2개를 얻으면 더 볼 필요가 없습니다.
            if len(find_dates("\n".join(chunk_lines))) >= 2:
                break

        chunk = "\n".join(chunk_lines)
        company = _extract_company_from_employment_chunk(chunk)
        if _company_likeness(company) <= 1:
            # 회사명이 없으면 행을 생성하지 않습니다.
            continue

        dates = find_dates(chunk)
        if len(dates) >= 2:
            add_row(company, dates[0][1], dates[1][1], is_current=False)
        elif len(dates) == 1:
            add_row(company, dates[0][1], deadline_dt, is_current=True)

    rows = list(rows_by_key.values())
    rows.sort(key=lambda row: (row["입사일"], str(row["퇴사일"]), row["회사명"]))

    return pd.DataFrame(
        rows,
        columns=[
            "회사명", "입사일", "퇴사일", "직무/직책", "인정구분",
            "인정률(%)", "출처", "출처파일", "비고",
        ],
    )


def health_parse_diagnostics(text: str, parsed_df: pd.DataFrame | None = None) -> dict[str, Any]:
    quality = text_quality(text, health_document=True)
    parsed_rows = 0 if parsed_df is None else len(parsed_df)
    normalized = normalize_for_quality(text).replace("\r", "\n")
    employment_markers = sum(
        1 for line in normalized.split("\n")
        if EMPLOYMENT_PATTERN.search(line) and not NON_EMPLOYMENT_PATTERN.search(line)
    )
    expected_rows = employment_markers if employment_markers else quality["date_count"] // 2
    return {
        **quality,
        "parsed_rows": parsed_rows,
        "employment_marker_count": employment_markers,
        "expected_rows": expected_rows,
        # 하위 호환용 키. 이제 날짜쌍보다 직장가입자 행 수를 우선합니다.
        "expected_pairs_from_dates": expected_rows,
        "parse_coverage": round(parsed_rows / expected_rows, 2) if expected_rows else 0.0,
    }

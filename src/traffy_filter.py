"""Versioned, explainable Thai text rules. Not a flood occurrence detector.

Only the reporter description and source category are considered. Agency
resolution notes often describe a different time and must not become labels.
"""

import re
import unicodedata

RULE_VERSION = "traffy-flood-v1.0"
FLOOD_TERMS = (
    "น้ำท่วมขัง", "น้ำท่วม", "ท่วมขัง", "น้ำขัง", "น้ำเอ่อล้น", "น้ำล้นตลิ่ง",
    "น้ำเข้าบ้าน", "น้ำรอระบาย", "ท่วมถนน", "ท่วมบ้าน",
)
NEGATED = re.compile(r"(?:ไม่มี|ไม่พบ(?:ว่า|มี)?|ไม่เกิด|ยังไม่(?:มี)?|ไม่เคย|ไม่ได้|ไม่)\s*(?:ปัญหา|ภาวะ)?\s*$")
PREVENTIVE = re.compile(r"(?:ป้องกัน|ป้องกันปัญหา|เสี่ยง(?:ต่อ)?|อาจ(?:จะ)?(?:เกิด)?|กลัว(?:ว่า)?(?:จะ)?|เกรง(?:ว่า)?(?:จะ)?|เตรียมรับมือ|รับมือ)\s*(?:ปัญหา)?\s*$")
HISTORICAL = re.compile(r"(?:ปีที่แล้ว|ปีก่อน|เมื่อปี|ครั้งก่อน|อดีต|เคยเกิด|เคยมี)")
CURRENT = re.compile(r"(?:วันนี้|ตอนนี้|ขณะนี้|ปัจจุบัน|กำลัง|ทุกครั้ง|ทุกปี|ทุกที|ประจำ|ฝนตกทีไร)")
LEAK = re.compile(r"(?:ท่อประปา(?:แตก|รั่ว)|ประปารั่ว|น้ำประปา(?:แตก|รั่ว))")


def normalize_text(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = re.sub(r"[\u200b-\u200f\ufeff]", "", text)
    # NFKC decomposes Thai sara-am; accept both combining-mark orders.
    text = text.replace("น\u0e49\u0e4d\u0e32", "น้ำ").replace("น\u0e4d\u0e49\u0e32", "น้ำ")
    return re.sub(r"\s+", " ", text).strip()


def classify_flood(description, categories=None):
    text = normalize_text(description)
    if isinstance(categories, str):
        categories = [categories]
    category_flood = any("น้ำท่วม" in normalize_text(x) for x in (categories or []))
    affirmed, suppressed = [], []
    # Consume the longest matching phrase once; overlapping matches must not
    # turn "ไม่มีน้ำท่วมขัง" into a false positive on the nested "ท่วมขัง".
    terms = "|".join(re.escape(x) for x in sorted(FLOOD_TERMS, key=len, reverse=True))
    for match in re.finditer(terms, text):
        term = match.group()
        before = text[max(0, match.start() - 28):match.start()]
        if NEGATED.search(before):
            suppressed.append({"term": term, "reason": "negated"})
        elif PREVENTIVE.search(before):
            suppressed.append({"term": term, "reason": "preventive_or_possible"})
        else:
            affirmed.append(term)

    historical_only = bool(HISTORICAL.search(text)) and not CURRENT.search(text)
    reason = "no_flood_evidence"
    decision = "non_flood"
    if affirmed:
        if historical_only:
            decision, reason = "uncertain", "historical_mention_without_current_evidence"
        elif LEAK.search(text):
            decision, reason = "uncertain", "possible_water_supply_leak"
        else:
            decision, reason = "flood", "affirmative_flood_description"
    elif category_flood:
        decision, reason = "uncertain", "category_only_or_conflicting_description"
    elif re.search(r"(?:น้ำไม่ท่วม|ไม่ท่วม|ไม่เคยท่วม)", text) and not affirmed:
        reason = "only_negated_or_preventive_mentions"
    elif "ท่วม" in text and not suppressed:
        decision, reason = "uncertain", "ambiguous_flood_mention"
    elif suppressed:
        reason = "only_negated_or_preventive_mentions"

    return {
        "flood_classification": decision,
        "is_flood_report": {"flood": True, "non_flood": False, "uncertain": None}[decision],
        "flood_rule_version": RULE_VERSION,
        "flood_reason": reason,
        "flood_matched_terms": sorted(set(affirmed)),
        "flood_suppressed_terms": suppressed,
        "source_category_flood": category_flood,
        "needs_review": decision == "uncertain",
    }

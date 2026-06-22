#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""에어비앤비(외국인관광도시민박업 등) 입지 분석 리포트 생성기.

월세/보증금/숙소유형/사업유형/역세권 거리 등 기본 조건을 입력하면
첨부 이미지와 같은 형태의 디테일한 입지 분석 HTML 리포트를 만들어 준다.

사용 예시:
    python3 airbnb_report.py \
        --room-type 투룸 \
        --rent 100 --deposit 1000 \
        --business 외국인관광도시민박업 \
        --station-min 10 \
        --area 성내동 \
        --out report.html

    # 인자 없이 실행하면 질문에 답하는 대화형 모드로 동작한다.
    python3 airbnb_report.py
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from html import escape


# ---------------------------------------------------------------------------
# 1. 입력 모델
# ---------------------------------------------------------------------------
@dataclass
class Listing:
    """리포트를 만들기 위한 숙소 조건."""

    room_type: str = "투룸"          # 원룸 / 투룸 / 쓰리룸
    rent_manwon: float = 100.0        # 월세(만원)
    deposit_manwon: float = 1000.0    # 보증금(만원)
    business_type: str = "외국인관광도시민박업"
    station_min: float = 10.0         # 역에서 도보(분)
    area: str = "성내동"              # 행정동/지역명
    station_name: str = "강동구청역"  # 인접 역


# ---------------------------------------------------------------------------
# 2. 기준 데이터 (가정값) — 필요 시 이 표만 수정하면 결과가 바뀐다.
# ---------------------------------------------------------------------------

# 숙소 유형별 1박 기준 객단가(원)와 수용 가능 인원
ROOM_PROFILE = {
    "원룸": {"adr": 90_000, "pax": 2, "label": "원룸"},
    "투룸": {"adr": 130_000, "pax": 4, "label": "투룸"},
    "쓰리룸": {"adr": 185_000, "pax": 6, "label": "쓰리룸"},
}

# 사업 유형별 특성 (객단가 보정계수, 합법 영업 가능 여부 점수, 메모)
BUSINESS_PROFILE = {
    "외국인관광도시민박업": {
        "adr_mult": 1.12,
        "legal": 9,
        "target": "외국인 관광객 중심",
        "note": "내국인 상시 영업 불가 — 외국인 수요 흡수가 핵심",
    },
    "농어촌민박": {
        "adr_mult": 0.95,
        "legal": 8,
        "target": "내·외국인 모두",
        "note": "읍·면 지역 위주, 도심 적용 제한",
    },
    "생활숙박시설": {
        "adr_mult": 1.05,
        "legal": 10,
        "target": "내·외국인 모두",
        "note": "내국인 영업 가능, 다만 분양·용도 확인 필요",
    },
    "일반": {
        "adr_mult": 1.0,
        "legal": 5,
        "target": "혼합",
        "note": "사업 유형 미지정 — 합법성 검토 필요",
    },
}


@dataclass
class Analysis:
    """계산된 분석 결과를 담는다."""

    listing: Listing
    adr_low: int = 0
    adr_high: int = 0
    occ_low: float = 0.0
    occ_high: float = 0.0
    rev_low: int = 0
    rev_high: int = 0
    cost_low: int = 0
    cost_high: int = 0
    profit_low: int = 0
    profit_high: int = 0
    scores: dict = field(default_factory=dict)
    total_score: int = 0
    verdict: str = ""
    listing_type: str = ""
    targets: list = field(default_factory=list)
    success_key: str = ""
    main_risk: str = ""
    final_note: str = ""


# ---------------------------------------------------------------------------
# 3. 계산 로직
# ---------------------------------------------------------------------------
def _station_occupancy(station_min: float) -> tuple[float, float]:
    """역 도보 시간(분)에 따른 예상 가동률(저~고)."""
    if station_min <= 3:
        return 0.68, 0.82
    if station_min <= 5:
        return 0.63, 0.78
    if station_min <= 10:
        return 0.55, 0.72
    if station_min <= 15:
        return 0.48, 0.64
    return 0.40, 0.55


def _station_score(station_min: float) -> int:
    """역 접근성 점수(25점 만점)."""
    if station_min <= 3:
        return 25
    if station_min <= 5:
        return 22
    if station_min <= 10:
        return 18
    if station_min <= 15:
        return 13
    return 8


def analyze(listing: Listing) -> Analysis:
    a = Analysis(listing=listing)

    room = ROOM_PROFILE.get(listing.room_type, ROOM_PROFILE["투룸"])
    biz = BUSINESS_PROFILE.get(listing.business_type, BUSINESS_PROFILE["일반"])

    # --- 객단가(ADR) 범위 ---
    base_adr = room["adr"] * biz["adr_mult"]
    a.adr_low = int(round(base_adr * 0.85 / 1000) * 1000)
    a.adr_high = int(round(base_adr * 1.20 / 1000) * 1000)

    # --- 가동률 범위 ---
    a.occ_low, a.occ_high = _station_occupancy(listing.station_min)

    # --- 월 매출 = 객단가 × 30박 × 가동률 ---
    a.rev_low = int(round(a.adr_low * 30 * a.occ_low / 10000) * 10000)
    a.rev_high = int(round(a.adr_high * 30 * a.occ_high / 10000) * 10000)

    # --- 월 고정/변동 비용 추정 ---
    rent = listing.rent_manwon * 10000
    # 관리비/공과금 + 플랫폼 수수료(약3%) + 청소·소모품 + 통신/구독
    fixed_etc = 250_000  # 관리·공과·통신·소모품 기본
    fee_low = a.rev_low * 0.03
    fee_high = a.rev_high * 0.03
    cleaning_low = a.occ_low * 30 / 2 * 25_000  # 평균 2박 1회전, 회전당 청소비
    cleaning_high = a.occ_high * 30 / 2 * 25_000
    a.cost_low = int(round((rent + fixed_etc + fee_low + cleaning_low) / 10000) * 10000)
    a.cost_high = int(round((rent + fixed_etc + fee_high + cleaning_high) / 10000) * 10000)

    # 순수익 = 매출 - 비용 (보수적으로 저매출-고비용, 고매출-저비용 매칭)
    a.profit_low = int(round((a.rev_low - a.cost_high) / 10000) * 10000)
    a.profit_high = int(round((a.rev_high - a.cost_low) / 10000) * 10000)

    # --- 점수 산출 (100점) ---
    transport = _station_score(listing.station_min)            # 25
    demand = 22                                                # 25 (강동·잠실·올림픽공원 수요 가정)
    margin_ratio = (a.profit_low + a.profit_high) / 2 / max(
        1, (a.rev_low + a.rev_high) / 2
    )
    profitability = int(max(8, min(25, margin_ratio * 50)))    # 25
    risk = 13                                                  # 15 (공연 의존 리스크 반영)
    legal = biz["legal"]                                       # 10

    a.scores = {
        "교통 접근성": (transport, 25),
        "수요 기반": (demand, 25),
        "수익성": (profitability, 25),
        "운영 안정성": (risk, 15),
        "규제 적합성": (legal, 10),
    }
    a.total_score = sum(v[0] for v in a.scores.values())

    # --- 종합 판단 ---
    if a.total_score >= 80:
        a.verdict = "추천"
    elif a.total_score >= 65:
        a.verdict = "조건부 추천"
    else:
        a.verdict = "신중 검토"

    a.listing_type = "교통거점형 + 공연·관광 연계형"
    a.targets = ["외국인 관광객", "콘서트 관람객", "가족 단위 여행객"]
    a.success_key = f"{listing.station_name}·잠실·올림픽공원·KSPO DOME 수요 흡수"
    a.main_risk = "공연·이벤트 일정 의존도 (비수기 가동률 변동)"

    if listing.station_min <= 5:
        loc_eval = "역세권 핵심 입지로 상위권"
    elif listing.station_min <= 10:
        loc_eval = "준역세권, 강동권에서는 상위권 입지"
    else:
        loc_eval = "역과 거리 있어 가격·콘텐츠 차별화 필요"
    a.final_note = f"{listing.area}에서는 {loc_eval}"

    return a


# ---------------------------------------------------------------------------
# 4. HTML 렌더링
# ---------------------------------------------------------------------------
def _won_range(low: int, high: int) -> str:
    return f"{low // 10000:,}만~{high // 10000:,}만원"


def render_html(a: Analysis) -> str:
    l = a.listing
    today = datetime.now().strftime("%Y-%m-%d")

    summary_rows = [
        ("종합 의견", a.verdict),
        ("오픈 추천 점수", f"{a.total_score}점 / 100점"),
        ("최적 입지 유형", a.listing_type),
        ("적합 숙소 유형", ROOM_PROFILE.get(l.room_type, {}).get("label", l.room_type)),
        ("핵심 타깃", ", ".join(a.targets)),
        ("예상 월 매출", _won_range(a.rev_low, a.rev_high)),
        ("예상 월 순수익", _won_range(a.profit_low, a.profit_high)),
        ("핵심 성공 조건", a.success_key),
        ("핵심 리스크", a.main_risk),
        ("최종 판단", a.final_note),
    ]

    market_rows = [
        ("지역 성격", "주거+상업 혼합지역", "안정적 생활 인프라"),
        ("방문 목적", "공연, 관광, 병원 방문, 가족 방문", "다양한 수요 확보"),
        ("수요 요인", "올림픽공원, KSPO DOME, 잠실, 아산병원", "이벤트 수요 강함"),
        ("체류 패턴", "1~3박 중심", "회전율 확보 가능"),
        ("계절성", "공연 일정 영향", "비수기 완화 필요"),
    ]

    good_bad_rows = [
        ("추천", f"역 도보 {int(l.station_min)}분 이내", "교통 경쟁력 우수", "캐리어 이동 편리"),
        ("추천", f"{l.station_name} 인근", "잠실 접근성 우수", "외국인 선호"),
        ("추천", "올림픽공원 생활권", "공연 수요 확보", "콘서트 특화"),
        ("주의", f"{l.area} 외곽 주택가", "관광 접근성 약화", "가격 경쟁 필요"),
        ("주의", "언덕형 골목", "이동 불편", "후기 리스크"),
        ("주의", "순수 주거지역", "목적 수요 부족", "차별화 필요"),
    ]

    revenue_rows = [
        ("기준 객단가(ADR)", f"{a.adr_low:,}원 ~ {a.adr_high:,}원", "숙소 유형·사업 유형 보정"),
        ("예상 가동률", f"{a.occ_low*100:.0f}% ~ {a.occ_high*100:.0f}%", f"역 도보 {int(l.station_min)}분 기준"),
        ("월 예상 매출", _won_range(a.rev_low, a.rev_high), "객단가 × 30박 × 가동률"),
        ("월 운영비용", _won_range(a.cost_low, a.cost_high), "월세+관리·공과+수수료+청소"),
        ("월 예상 순수익", _won_range(a.profit_low, a.profit_high), "매출 - 운영비용"),
        ("월세 / 보증금", f"{l.rent_manwon:,.0f}만원 / {l.deposit_manwon:,.0f}만원", "고정비 핵심 변수"),
    ]

    score_rows = [
        (name, f"{got}점 / {full}점") for name, (got, full) in a.scores.items()
    ]
    score_rows.append(("합계", f"{a.total_score}점 / 100점"))

    checklist = [
        "역 출구 위치 및 도보 동선 (캐리어 기준) 직접 확인",
        "잠실·올림픽공원까지 대중교통 환승 횟수 점검",
        "야간 귀가 동선의 안전·조도 확인",
        "외국인관광도시민박업 등록 요건(주택 유형·면적·소방) 충족 여부",
        "건물 내 소음·분리수거·이웃 민원 가능성 점검",
        "공연 비수기(1~2월, 7월) 대비 가격 전략 수립",
    ]

    biz_note = BUSINESS_PROFILE.get(l.business_type, BUSINESS_PROFILE["일반"])["note"]

    # --- HTML 빌드 ---
    def summary_table(rows):
        body = "".join(
            f'<tr><td class="k">{escape(k)}</td><td class="v">{escape(str(v))}</td></tr>'
            for k, v in rows
        )
        return f'<table class="kv"><thead><tr><th>구분</th><th>핵심 판단</th></tr></thead><tbody>{body}</tbody></table>'

    def three_col(rows, headers):
        head = "".join(f"<th>{escape(h)}</th>" for h in headers)
        body = "".join(
            "<tr>" + "".join(f"<td>{escape(str(c))}</td>" for c in r) + "</tr>"
            for r in rows
        )
        return f'<table class="grid"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'

    def four_col(rows, headers):
        head = "".join(f"<th>{escape(h)}</th>" for h in headers)
        body = ""
        for r in rows:
            tag = "rec" if r[0] == "추천" else "warn"
            cells = f'<td><span class="badge {tag}">{escape(r[0])}</span></td>'
            cells += "".join(f"<td>{escape(str(c))}</td>" for c in r[1:])
            body += f"<tr>{cells}</tr>"
        return f'<table class="grid"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'

    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(l.area)} {escape(l.room_type)} 에어비앤비 입지 분석</title>
<style>
  :root {{ --line:#e9e9e9; --muted:#777; --rec:#1a7f37; --warn:#b3540a; }}
  * {{ box-sizing:border-box; }}
  body {{ font-family:-apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",
          "Noto Sans KR",sans-serif; color:#1a1a1a; line-height:1.55; margin:0;
          background:#fafafa; }}
  .wrap {{ max-width:860px; margin:0 auto; padding:48px 24px 80px; background:#fff; }}
  header.doc {{ border-bottom:2px solid #1a1a1a; padding-bottom:16px; margin-bottom:8px; }}
  h1 {{ font-size:24px; margin:0 0 6px; }}
  .meta {{ color:var(--muted); font-size:13px; }}
  h2 {{ font-size:19px; margin:40px 0 14px; }}
  h3 {{ font-size:15px; margin:26px 0 10px; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; margin:6px 0 4px; }}
  th, td {{ text-align:left; padding:13px 12px; border-bottom:1px solid var(--line);
            vertical-align:top; }}
  thead th {{ color:#111; font-weight:700; border-bottom:1px solid #ccc; }}
  table.kv td.k {{ width:34%; color:#444; }}
  table.kv td.v {{ font-weight:600; }}
  .badge {{ display:inline-block; padding:2px 10px; border-radius:999px; font-size:12px;
            font-weight:700; }}
  .badge.rec {{ background:#e7f5ec; color:var(--rec); }}
  .badge.warn {{ background:#fbeee0; color:var(--warn); }}
  ul {{ margin:8px 0 0; padding-left:20px; }}
  li {{ margin:4px 0; }}
  .callout {{ background:#f6f7f9; border-left:3px solid #1a1a1a; padding:14px 16px;
              margin:14px 0; border-radius:0 6px 6px 0; font-size:14px; }}
  .footnote {{ color:var(--muted); font-size:12px; margin-top:48px; border-top:1px solid var(--line);
               padding-top:14px; }}
</style>
</head>
<body>
<div class="wrap">
  <header class="doc">
    <h1>{escape(l.area)} {escape(l.room_type)} 에어비앤비 입지 분석 리포트</h1>
    <div class="meta">사업 유형: {escape(l.business_type)} · 인접 역: {escape(l.station_name)}
      (도보 {int(l.station_min)}분) · 작성일: {today}</div>
  </header>

  <h2>종합 요약</h2>
  {summary_table(summary_rows)}

  <h2>1. 지역 시장 구조</h2>
  {three_col(market_rows, ["항목", "내용", "사업적 시사점"])}

  <h2>2. 잘 되는 입지 / 안 되는 입지</h2>
  {four_col(good_bad_rows, ["구분", "세부 입지", "판단 이유", "운영 포인트"])}
  <h3>핵심 시사점</h3>
  <p>{escape(l.area)}에서는 단순 지역명보다</p>
  <ul>
    <li>역 출구 위치</li>
    <li>잠실 접근성</li>
    <li>올림픽공원 접근성</li>
    <li>야간 귀가 동선</li>
  </ul>
  <p>이 훨씬 중요하다. 현재 조건인 <b>역 도보 {int(l.station_min)}분</b>은
     {"매우 우수한" if l.station_min <= 5 else "양호한" if l.station_min <= 10 else "보통 수준의"} 조건으로 판단된다.</p>

  <h2>3. 예상 수익 분석</h2>
  {three_col(revenue_rows, ["항목", "추정값", "산출 근거"])}
  <div class="callout">※ 객단가·가동률은 입력 조건 기반 추정치이며, 실제 예약 데이터(주변 동급 숙소 캘린더,
     성수기/비수기 편차)로 보정해야 한다.</div>

  <h2>4. 추천 점수 산출 근거</h2>
  {summary_table(score_rows)}

  <h2>5. 타깃 · 운영 전략</h2>
  <ul>
    <li><b>핵심 타깃:</b> {escape(', '.join(a.targets))}</li>
    <li><b>사업 유형 특성:</b> {escape(biz_note)}</li>
    <li><b>가격 전략:</b> 공연 성수기 객단가 상향, 비수기 최소 숙박일·할인 패키지로 가동률 방어</li>
    <li><b>차별화:</b> 외국어 안내, 캐리어 동선, 셀프 체크인, 공연장 도보 안내로 후기 경쟁력 확보</li>
  </ul>

  <h2>6. 리스크 &amp; 체크리스트</h2>
  <p><b>핵심 리스크:</b> {escape(a.main_risk)}</p>
  <ul>
    {''.join(f'<li>{escape(c)}</li>' for c in checklist)}
  </ul>

  <div class="footnote">
    본 리포트는 입력 조건({escape(l.room_type)} · 월세 {l.rent_manwon:,.0f}만원 ·
    보증금 {l.deposit_manwon:,.0f}만원 · {escape(l.business_type)} · 역 도보 {int(l.station_min)}분)
    기반의 추정 분석이며, 실제 계약·인허가 전 현장 실사와 규제 확인이 필요합니다.
  </div>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# 5. CLI / 대화형 진입점
# ---------------------------------------------------------------------------
def _interactive() -> Listing:
    print("=== 에어비앤비 입지 분석 리포트 생성기 (입력) ===")

    def ask(label, default, cast=str):
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return cast(raw)
        except ValueError:
            return default

    return Listing(
        room_type=ask("숙소 유형(원룸/투룸/쓰리룸)", "투룸"),
        rent_manwon=ask("월세(만원)", 100.0, float),
        deposit_manwon=ask("보증금(만원)", 1000.0, float),
        business_type=ask("사업 유형", "외국인관광도시민박업"),
        station_min=ask("역에서 도보(분)", 10.0, float),
        area=ask("지역명", "성내동"),
        station_name=ask("인접 역", "강동구청역"),
    )


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="에어비앤비/민박 입지 분석 HTML 리포트 생성기",
    )
    p.add_argument("--room-type", default="투룸", help="원룸/투룸/쓰리룸")
    p.add_argument("--rent", type=float, default=100.0, help="월세(만원)")
    p.add_argument("--deposit", type=float, default=1000.0, help="보증금(만원)")
    p.add_argument("--business", default="외국인관광도시민박업", help="사업 유형")
    p.add_argument("--station-min", type=float, default=10.0, help="역에서 도보(분)")
    p.add_argument("--area", default="성내동", help="지역명")
    p.add_argument("--station-name", default="강동구청역", help="인접 역명")
    p.add_argument("--out", default="report.html", help="저장할 HTML 파일 경로")
    p.add_argument("--open", action="store_true", help="생성 후 브라우저로 열기")
    p.add_argument("-i", "--interactive", action="store_true", help="대화형 입력")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.interactive or (argv is None and len(sys.argv) == 1):
        listing = _interactive()
    else:
        listing = Listing(
            room_type=args.room_type,
            rent_manwon=args.rent,
            deposit_manwon=args.deposit,
            business_type=args.business,
            station_min=args.station_min,
            area=args.area,
            station_name=args.station_name,
        )

    analysis = analyze(listing)
    html = render_html(analysis)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"리포트 생성 완료: {args.out}")
    print(f"  - 추천 점수: {analysis.total_score}점 / 100점 ({analysis.verdict})")
    print(f"  - 예상 월 매출: {_won_range(analysis.rev_low, analysis.rev_high)}")
    print(f"  - 예상 월 순수익: {_won_range(analysis.profit_low, analysis.profit_high)}")

    if args.open:
        webbrowser.open(f"file://{__import__('os').path.abspath(args.out)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

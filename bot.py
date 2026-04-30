import os
import re
import json
import logging
import asyncio
import subprocess
import anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

subprocess.run(["python", "-m", "playwright", "install", "chromium"], check=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

ANALYSIS_PROMPT = """
당신은 기업 투자 분석 전문 어시스턴트입니다.

## 핵심 원칙
- 사용자는 투자 자격증 보유 현직 의사로, 투자 기초 설명은 불필요
- 기관 리포트 수준의 밀도와 시각으로 작성
- 웹 검색을 반드시 수행하여 최신 뉴스, 실적, 이슈를 반영 (최대 3회)
- 정성적 인사이트 중심
- 재무 데이터는 반드시 웹 검색으로 최신 실제 수치 확인. 모를 경우 "-"로 표기
- 오래된 데이터 사용 금지

## 출력 형식
반드시 아래 JSON만 출력. 앞뒤 텍스트 금지. cite 태그 및 HTML 태그 절대 사용 금지.

{
  "company": "기업명",
  "ticker": "티커/종목코드",
  "tagline": "한 줄 포지셔닝",
  "valuation": {
    "market_cap": "시가총액 (예: 1.2조)",
    "per": "PER (예: 12.3배)",
    "pbr": "PBR (예: 1.2배)",
    "roe": "ROE (예: 15.2%)"
  },
  "financials": [
    {"year": "2022", "revenue": "매출액", "op_profit": "영업이익", "opm": "OPM", "net_profit": "순이익", "npm": "NPM"},
    {"year": "2023", "revenue": "매출액", "op_profit": "영업이익", "opm": "OPM", "net_profit": "순이익", "npm": "NPM"},
    {"year": "2024", "revenue": "매출액", "op_profit": "영업이익", "opm": "OPM", "net_profit": "순이익", "npm": "NPM"}
  ],
  "biz_model": "비즈니스 모델 2~3문장",
  "points": [
    {"title": "포인트 제목", "desc": "2~3문장"},
    {"title": "포인트 제목", "desc": "2~3문장"},
    {"title": "포인트 제목", "desc": "2~3문장"}
  ],
  "attention": "지금 주목받는 이유 (없으면 빈 문자열)",
  "risks": [
    {"title": "리스크 제목", "desc": "구체적 시나리오"},
    {"title": "리스크 제목", "desc": "구체적 시나리오"},
    {"title": "리스크 제목", "desc": "구체적 시나리오"}
  ],
  "peers": [
    {"name": "기업명", "country": "국가", "desc": "비교 포인트"},
    {"name": "기업명", "country": "국가", "desc": "비교 포인트"},
    {"name": "기업명", "country": "국가", "desc": "비교 포인트"},
    {"name": "기업명", "country": "국가", "desc": "비교 포인트"}
  ],
  "summary": "한 줄 요약"
}
"""


def strip_tags(text: str) -> str:
    return re.sub(r'<[^>]+>', '', str(text)).strip()


def clean_data(data: dict) -> dict:
    def clean(v):
        if isinstance(v, str):
            return strip_tags(v)
        if isinstance(v, list):
            return [clean(i) for i in v]
        if isinstance(v, dict):
            return {k: clean(vv) for k, vv in v.items()}
        return v
    return {k: clean(v) for k, v in data.items()}


def build_text(data: dict) -> str:
    nums = ["①", "②", "③", "④", "⑤"]

    v = data.get('valuation', {})
    valuation = (
        f"시가총액: {v.get('market_cap','-')}  |  "
        f"PER: {v.get('per','-')}  |  "
        f"PBR: {v.get('pbr','-')}  |  "
        f"ROE: {v.get('roe','-')}"
    )

    fin_lines = ["연도      매출액      영업이익   OPM    순이익     NPM"]
    for f in data.get('financials', []):
        fin_lines.append(
            f"{f.get('year','-')}  {f.get('revenue','-')}  "
            f"{f.get('op_profit','-')}  {f.get('opm','-')}  "
            f"{f.get('net_profit','-')}  {f.get('npm','-')}"
        )
    financials = "\n".join(fin_lines)

    points_lines = []
    for i, p in enumerate(data.get('points', [])):
        num = nums[i] if i < len(nums) else f"{i+1}."
        points_lines.append(f"{num} {p.get('title','')}\n{p.get('desc','')}")
    points = "\n\n".join(points_lines)

    risks_lines = []
    for i, r in enumerate(data.get('risks', [])):
        num = nums[i] if i < len(nums) else f"{i+1}."
        risks_lines.append(f"{num} {r.get('title','')}\n{r.get('desc','')}")
    risks = "\n\n".join(risks_lines)

    peers_lines = []
    for p in data.get('peers', []):
        peers_lines.append(f"• {p.get('name','')} ({p.get('country','')}): {p.get('desc','')}")
    peers = "\n".join(peers_lines)

    attention = f"\n🔍 지금 주목받는 이유\n{data.get('attention','')}\n" if data.get('attention') else ""

    return (
        f"{'='*30}\n"
        f"{data.get('company','')} ({data.get('ticker','')})\n"
        f"{data.get('tagline','')}\n"
        f"{'='*30}\n\n"
        f"📊 밸류에이션\n{valuation}\n\n"
        f"📋 최근 3개년 실적\n{financials}\n\n"
        f"📌 비즈니스 모델\n{data.get('biz_model','')}\n\n"
        f"📈 투자 포인트\n{points}\n"
        f"{attention}\n"
        f"⚠️ 리스크\n{risks}\n\n"
        f"🌏 유사 기업\n{peers}\n\n"
        f"💡 한 줄 요약\n{data.get('summary','')}\n\n"
        f"*본 내용은 투자 참고용이며 투자 권유가 아닙니다.*"
    )


def build_html(data: dict) -> str:
    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    nums = ["①", "②", "③", "④", "⑤"]
    v = data.get('valuation', {})

    fin_rows = ""
    for f in data.get('financials', []):
        fin_rows += f"""
        <tr>
          <td class="year">{esc(f.get('year','-'))}</td>
          <td>{esc(f.get('revenue','-'))}</td>
          <td>{esc(f.get('op_profit','-'))}</td>
          <td class="pct">{esc(f.get('opm','-'))}</td>
          <td>{esc(f.get('net_profit','-'))}</td>
          <td class="pct">{esc(f.get('npm','-'))}</td>
        </tr>"""

    points_html = ""
    for i, p in enumerate(data.get("points", [])):
        num = nums[i] if i < len(nums) else str(i+1)
        points_html += f"""
        <div class="point">
          <div class="num">{num}</div>
          <div class="point-content">
            <div class="point-title">{esc(p.get('title',''))}</div>
            <div class="point-desc">{esc(p.get('desc',''))}</div>
          </div>
        </div>"""

    risks_html = ""
    for i, r in enumerate(data.get("risks", [])):
        num = nums[i] if i < len(nums) else str(i+1)
        risks_html += f"""
        <div class="point">
          <div class="num">{num}</div>
          <div class="point-content">
            <div class="point-title">{esc(r.get('title',''))}</div>
            <div class="point-desc">{esc(r.get('desc',''))}</div>
          </div>
        </div>"""

    peers_html = ""
    for p in data.get("peers", []):
        peers_html += f"""
        <div class="peer">
          <div class="peer-name">{esc(p.get('name',''))}</div>
          <div class="peer-country">{esc(p.get('country',''))}</div>
          <div class="peer-desc">{esc(p.get('desc',''))}</div>
        </div>"""

    attention_html = ""
    if data.get("attention"):
        attention_html = f"""
      <div class="section">
        <div class="section-title title-blue">지금 주목받는 이유</div>
        <div class="bm">{esc(data['attention'])}</div>
      </div>"""

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500&display=swap" rel="stylesheet">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Noto Sans KR', sans-serif; background: #f8f8f6; padding: 20px; width: 680px; }}
  .wrap {{ display: flex; flex-direction: column; gap: 12px; }}
  .header {{ background: #fff; border: 0.5px solid #e0ddd4; border-radius: 12px; padding: 20px 24px; }}
  .ticker {{ font-size: 12px; color: #888; letter-spacing: 0.05em; margin-bottom: 4px; }}
  .company {{ font-size: 24px; font-weight: 500; color: #1a1a1a; }}
  .tagline {{ font-size: 13px; color: #666; margin-top: 6px; }}
  .valuation {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }}
  .metric {{ background: #fff; border: 0.5px solid #e0ddd4; border-radius: 8px; padding: 14px 16px; }}
  .metric-label {{ font-size: 11px; color: #999; margin-bottom: 4px; }}
  .metric-value {{ font-size: 18px; font-weight: 500; color: #1a1a1a; }}
  .section {{ background: #fff; border: 0.5px solid #e0ddd4; border-radius: 12px; overflow: hidden; }}
  .section-title {{ font-size: 11px; font-weight: 500; padding: 8px 16px; letter-spacing: 0.06em; }}
  .title-green {{ background: #EAF3DE; color: #3B6D11; }}
  .title-red {{ background: #FCEBEB; color: #A32D2D; }}
  .title-blue {{ background: #E6F1FB; color: #185FA5; }}
  .title-amber {{ background: #FAEEDA; color: #854F0B; }}
  .title-purple {{ background: #EEEDFE; color: #534AB7; }}
  .title-gray {{ background: #F1EFE8; color: #5F5E5A; }}
  .bm {{ padding: 14px 16px; font-size: 13px; color: #555; line-height: 1.6; }}
  .fin-table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
  .fin-table th {{ padding: 8px 12px; text-align: right; color: #999; font-weight: 500; border-bottom: 0.5px solid #f0ede4; }}
  .fin-table th:first-child {{ text-align: left; }}
  .fin-table td {{ padding: 9px 12px; text-align: right; color: #444; border-bottom: 0.5px solid #f5f3ef; }}
  .fin-table tr:last-child td {{ border-bottom: none; }}
  .fin-table td.year {{ text-align: left; font-weight: 500; color: #1a1a1a; }}
  .fin-table td.pct {{ color: #185FA5; }}
  .point {{ display: flex; gap: 12px; padding: 12px 16px; border-top: 0.5px solid #f0ede4; }}
  .point:first-of-type {{ border-top: none; }}
  .num {{ font-size: 13px; font-weight: 500; min-width: 18px; color: #aaa; padding-top: 1px; }}
  .point-content {{ flex: 1; }}
  .point-title {{ font-size: 13px; font-weight: 500; color: #1a1a1a; margin-bottom: 3px; }}
  .point-desc {{ font-size: 12px; color: #666; line-height: 1.55; }}
  .peers {{ display: grid; grid-template-columns: repeat(2, 1fr); }}
  .peer {{ padding: 10px 16px; border-top: 0.5px solid #f0ede4; }}
  .peer:nth-child(odd) {{ border-right: 0.5px solid #f0ede4; }}
  .peer:nth-child(1), .peer:nth-child(2) {{ border-top: none; }}
  .peer-name {{ font-size: 13px; font-weight: 500; color: #1a1a1a; }}
  .peer-country {{ font-size: 11px; color: #aaa; }}
  .peer-desc {{ font-size: 12px; color: #666; margin-top: 2px; }}
  .notice {{ font-size: 11px; color: #bbb; text-align: center; padding-top: 4px; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div class="ticker">{esc(data.get('ticker',''))}</div>
    <div class="company">{esc(data.get('company',''))}</div>
    <div class="tagline">{esc(data.get('tagline',''))}</div>
  </div>

  <div class="valuation">
    <div class="metric">
      <div class="metric-label">시가총액</div>
      <div class="metric-value">{esc(v.get('market_cap','-'))}</div>
    </div>
    <div class="metric">
      <div class="metric-label">PER</div>
      <div class="metric-value">{esc(v.get('per','-'))}</div>
    </div>
    <div class="metric">
      <div class="metric-label">PBR</div>
      <div class="metric-value">{esc(v.get('pbr','-'))}</div>
    </div>
    <div class="metric">
      <div class="metric-label">ROE</div>
      <div class="metric-value">{esc(v.get('roe','-'))}</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title title-gray">최근 3개년 실적</div>
    <table class="fin-table">
      <thead>
        <tr>
          <th>연도</th><th>매출액</th><th>영업이익</th><th>OPM</th><th>순이익</th><th>NPM</th>
        </tr>
      </thead>
      <tbody>{fin_rows}</tbody>
    </table>
  </div>

  <div class="section">
    <div class="section-title title-blue">비즈니스 모델</div>
    <div class="bm">{esc(data.get('biz_model',''))}</div>
  </div>

  <div class="section">
    <div class="section-title title-green">투자 포인트</div>
    {points_html}
  </div>

  {attention_html}

  <div class="section">
    <div class="section-title title-red">리스크</div>
    {risks_html}
  </div>

  <div class="section">
    <div class="section-title title-purple">유사 기업</div>
    <div class="peers">{peers_html}</div>
  </div>

  <div class="section">
    <div class="section-title title-amber">한 줄 요약</div>
    <div class="bm">{esc(data.get('summary',''))}</div>
  </div>

  <div class="notice">본 내용은 투자 참고용 분석이며 투자 권유가 아닙니다.</div>
</div>
</body>
</html>"""


async def html_to_image(html: str) -> bytes:
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
        page = await browser.new_page(viewport={"width": 720, "height": 1200})
        await page.set_content(html, wait_until="networkidle")
        await page.wait_for_timeout(2000)
        img = await page.screenshot(full_page=True)
        await browser.close()
        return img


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    chat_id = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await update.message.reply_text("분석 중입니다... 30~60초 소요됩니다 ⏳")

    try:
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=4096,
            system=ANALYSIS_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": user_message}]
        )

        raw = ""
        for block in response.content:
            if block.type == "text":
                raw += block.text

        raw = strip_tags(raw)

        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            await update.message.reply_text("분석 데이터를 파싱하지 못했습니다. 다시 시도해주세요.")
            return

        data = json.loads(match.group())
        data = clean_data(data)

        text = build_text(data)
        await update.message.reply_text(text)

        await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")
        html = build_html(data)
        img_bytes = await html_to_image(html)
        await update.message.reply_photo(photo=img_bytes)

    except Exception as e:
        logging.error(f"Error: {e}", exc_info=True)
        await update.message.reply_text(f"오류가 발생했습니다: {str(e)}")


async def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("봇 시작!")
    await app.initialize()
    await app.bot.delete_webhook(drop_pending_updates=True)
    await app.start()
    await app.updater.start_polling(drop_pending_updates=True, allowed_updates=["message"])
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())

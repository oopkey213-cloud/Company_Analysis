import os
import re
import json
import logging
import asyncio
import anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from playwright.async_api import async_playwright

import subprocess
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

## 출력 형식
반드시 아래 JSON만 출력. 앞뒤 텍스트 금지. cite 태그 및 HTML 태그 절대 사용 금지.

{
  "company": "기업명",
  "ticker": "티커/종목코드",
  "tagline": "한 줄 포지셔닝",
  "current_price": "현재가",
  "target_price": "목표주가",
  "upside": "상승여력 (예: +51%)",
  "target_source": "증권사명",
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

    points_lines = []
    for i, p in enumerate(data.get('points', [])):
        num = nums[i] if i < len(nums) else f"{i+1}."
        points_lines.append(f"{num} {p.get('title', '')}\n{p.get('desc', '')}")
    points = "\n\n".join(points_lines)

    risks_lines = []
    for i, r in enumerate(data.get('risks', [])):
        num = nums[i] if i < len(nums) else f"{i+1}."
        risks_lines.append(f"{num} {r.get('title', '')}\n{r.get('desc', '')}")
    risks = "\n\n".join(risks_lines)

    peers_lines = []
    for p in data.get('peers', []):
        peers_lines.append(f"• {p.get('name', '')} ({p.get('country', '')}): {p.get('desc', '')}")
    peers = "\n".join(peers_lines)

    attention = f"\n🔍 지금 주목받는 이유\n{data.get('attention', '')}\n" if data.get('attention') else ""

    return (
        f"{'='*30}\n"
        f"{data.get('company', '')} ({data.get('ticker', '')})\n"
        f"{data.get('tagline', '')}\n"
        f"{'='*30}\n\n"
        f"현재가: {data.get('current_price', '-')}\n"
        f"목표주가: {data.get('target_price', '-')} ({data.get('target_source', '')})\n"
        f"상승여력: {data.get('upside', '-')}\n\n"
        f"📌 비즈니스 모델\n{data.get('biz_model', '')}\n\n"
        f"📈 투자 포인트\n{points}\n"
        f"{attention}\n"
        f"⚠️ 리스크\n{risks}\n\n"
        f"🌏 유사 기업\n{peers}\n\n"
        f"💡 한 줄 요약\n{data.get('summary', '')}\n\n"
        f"*본 내용은 투자 참고용이며 투자 권유가 아닙니다.*"
    )


def build_html(data: dict) -> str:
    def esc(s):
        return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    nums = ["①", "②", "③", "④", "⑤"]

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

    upside = data.get("upside", "")
    upside_color = "#3B6D11" if "+" in str(upside) else "#A32D2D"

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans KR', sans-serif; background: #f8f8f6; padding: 20px; width: 680px; }}
  .wrap {{ display: flex; flex-direction: column; gap: 12px; }}
  .header {{ background: #fff; border: 0.5px solid #e0ddd4; border-radius: 12px; padding: 20px 24px; }}
  .ticker {{ font-size: 12px; color: #888; letter-spacing: 0.05em; margin-bottom: 4px; }}
  .company {{ font-size: 24px; font-weight: 500; color: #1a1a1a; }}
  .tagline {{ font-size: 13px; color: #666; margin-top: 6px; }}
  .metrics {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }}
  .metric {{ background: #fff; border: 0.5px solid #e0ddd4; border-radius: 8px; padding: 14px 16px; }}
  .metric-label {{ font-size: 11px; color: #999; margin-bottom: 4px; }}
  .metric-value {{ font-size: 20px; font-weight: 500; color: #1a1a1a; }}
  .metric-sub {{ font-size: 11px; color: #aaa; margin-top: 2px; }}
  .section {{ background: #fff; border: 0.5px solid #e0ddd4; border-radius: 12px; overflow: hidden; }}
  .section-title {{ font-size: 11px; font-weight: 500; padding: 8px 16px; letter-spacing: 0.06em; }}
  .title-green {{ background: #EAF3DE; color: #3B6D11; }}
  .title-red {{ background: #FCEBEB; color: #A32D2D; }}
  .title-blue {{ background: #E6F1FB; color: #185FA5; }}
  .title-amber {{ background: #FAEEDA; color: #854F0B; }}
  .title-purple {{ background: #EEEDFE; color: #534AB7; }}
  .bm {{ padding: 14px 16px; font-size: 13px; color: #555; line-height: 1.6; }}
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
  <div class="metrics">
    <div class="metric">
      <div class="metric-label">현재가</div>
      <div class="metric-value">{esc(data.get('current_price','-'))}</div>
    </div>
    <div class="metric">
      <div class="metric-label">목표주가</div>
      <div class="metric-value">{esc(data.get('target_price','-'))}</div>
      <div class="metric-sub">{esc(data.get('target_source',''))}</div>
    </div>
    <div class="metric">
      <div class="metric-label">상승여력</div>
      <div class="metric-value" style="color:{upside_color};">{esc(upside)}</div>
    </div>
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
        await page.set_content(html, wait_until="domcontentloaded")
        await page.wait_for_timeout(500)
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

        # 텍스트 전송
        text = build_text(data)
        await update.message.reply_text(text)

        # 이미지 전송
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

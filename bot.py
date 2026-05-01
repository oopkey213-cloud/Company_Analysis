import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
import os, re, json, logging, asyncio, anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

ANALYSIS_PROMPT = """
당신은 기업 투자 분석 전문 어시스턴트입니다.

## 사용자 프로필
- 투자 자격증 보유 현직 의사
- 투자 기초 설명 불필요
- 기관 리포트 수준의 밀도와 시각 요구

## 분석 방향 (가장 중요)
단순한 기업 소개가 아니라 "지금 이 기업을 왜 봐야 하는가"에 집중하세요.
- 투자 포인트는 과거 실적이 아닌 **현재 진행 중인 변화와 향후 촉매**에 집중
- "요즘 왜 주목받는가"는 최근 3~6개월 내 발생한 구체적 트리거 중심으로
- 리스크는 generic("경쟁 심화")이 아닌 이 기업 고유의 리스크

## 웹 검색
최대 3회. 최신 뉴스, 실적, 증권사 코멘트, 업황 변화에 집중.

## 출력 형식
JSON만 출력. 앞뒤 텍스트 금지. cite 태그 및 HTML 태그 절대 금지.

{
  "company": "기업명",
  "ticker": "티커/종목코드",
  "tagline": "한 줄 포지셔닝 — 지금 이 기업의 본질을 한 문장으로",
  "quick_summary": [
    "💡 [핵심 투자 thesis — 왜 지금 이 기업인가]",
    "🟢 [현재 모멘텀 — 최근 무슨 일이 일어나고 있는가]",
    "🔴 [핵심 리스크 — 무엇이 틀릴 수 있는가]"
  ],
  "attention": "요즘 주목받는 이유 — 최근 3~6개월 내 구체적 트리거. 주가 움직임의 촉매가 된 이벤트, 수급 변화, 업황 전환점 등. 없으면 빈 문자열",
  "biz_model": "BM 2~3문장. 어떻게 돈 버는지, 핵심 수익원, 시장 내 포지션",
  "points": [
    {
      "title": "투자 포인트 제목 — 지금 유효한 이유 포함",
      "desc": "2~3문장. 과거 실적보다 향후 6~18개월 촉매와 변화 중심. 구체적 수치와 타임라인 포함"
    },
    {
      "title": "투자 포인트 제목",
      "desc": "2~3문장"
    },
    {
      "title": "투자 포인트 제목",
      "desc": "2~3문장"
    }
  ],
  "risks": [
    {
      "title": "리스크 제목 — 이 기업 고유의 리스크",
      "desc": "발현 시나리오와 주가 영향 구체적으로"
    },
    {
      "title": "리스크 제목",
      "desc": "발현 시나리오"
    },
    {
      "title": "리스크 제목",
      "desc": "발현 시나리오"
    }
  ],
  "peers": [
    {"name": "기업명", "country": "국가", "desc": "이 기업 대비 차별점 또는 비교 포인트"},
    {"name": "기업명", "country": "국가", "desc": "차별점"},
    {"name": "기업명", "country": "국가", "desc": "차별점"},
    {"name": "기업명", "country": "국가", "desc": "차별점"}
  ],
  "summary": "한 줄 결론 — 매수/중립/주의 스탠스와 핵심 이유"
}
"""

def strip_tags(text):
    return re.sub(r'<[^>]+>', '', str(text)).strip()

def clean_data(data):
    def clean(v):
        if isinstance(v, str): return strip_tags(v)
        if isinstance(v, list): return [clean(i) for i in v]
        if isinstance(v, dict): return {k: clean(vv) for k, vv in v.items()}
        return v
    return {k: clean(v) for k, v in data.items()}

def build_text(data):
    nums = ["①", "②", "③", "④", "⑤"]

    quick = "\n".join(s for s in data.get('quick_summary', []))

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

    peers = "\n".join(
        f"• {p.get('name','')} ({p.get('country','')}): {p.get('desc','')}"
        for p in data.get('peers', [])
    )

    attention = f"\n🔍 요즘 주목받는 이유\n{data.get('attention','')}\n" if data.get('attention') else ""

    return (
        f"{'━'*28}\n"
        f"{data.get('company','')}  {data.get('ticker','')}\n"
        f"{data.get('tagline','')}\n"
        f"{'━'*28}\n\n"
        f"{quick}\n\n"
        f"{'─'*28}\n"
        f"📌 비즈니스 모델\n{data.get('biz_model','')}\n"
        f"{attention}"
        f"\n📈 투자 포인트\n\n{points}\n\n"
        f"{'─'*28}\n"
        f"⚠️ 리스크\n\n{risks}\n\n"
        f"{'─'*28}\n"
        f"🌏 유사 기업\n{peers}\n\n"
        f"{'━'*28}\n"
        f"💡 {data.get('summary','')}\n\n"
        f"*투자 참고용이며 투자 권유가 아닙니다.*"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    chat_id = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")
    await update.message.reply_text("분석 중입니다... 30~60초 소요됩니다 ⏳")

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
            system=ANALYSIS_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": user_message}]
        )

        raw = ""
        for block in response.content:
            if block.type == "text":
                raw += block.text

        raw = re.sub(r'<[^>]+>', '', raw).strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            await update.message.reply_text("분석 데이터를 파싱하지 못했습니다. 다시 시도해주세요.")
            return

        data = json.loads(match.group())
        data = clean_data(data)
        await update.message.reply_text(build_text(data))

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

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'OK')
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()
if __name__ == "__main__":
    asyncio.run(main())

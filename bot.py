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
- 투자 자격증 보유 현직 의사. 투자 기초 설명 불필요.
- 기관 리포트 수준의 밀도와 시각 요구.

## 분석 원칙
- 단순 기업 소개 X. "왜 지금 이 기업인가"에 집중.
- 내러티브 중심, 수치는 본문에 자연스럽게 녹여서 신뢰도 보강.
- 단기 모멘텀(3M)과 중기 구조변화(12M) 구분.
- 일반론적 리스크(규제·환율·금리) 절대 금지. 종목 고유 리스크만.
- 해당 산업 글로벌 선행지표 반드시 체크해 본문 반영
  (반도체→메모리가격·TSMC, 절삭공구→일본 공작기계 수주, 양극재→니켈/리튬, 바이오→FDA·학회, 조선→Clarksons)
- BM 내 점유율/순위/추세 명시.

## 웹 검색
최대 3회. 최신 가격, 실적, 증권사 코멘트, 업황 변화 중심.

## 출력 형식
JSON만 출력. 앞뒤 텍스트 금지. cite 태그·HTML 절대 금지.

{
  "company": "기업명",
  "ticker": "티커/종목코드",
  "tagline": "한 줄 포지셔닝",
  "quick_summary": [
    "💡 [핵심 thesis]",
    "🟢 [현재 모멘텀]",
    "🔴 [핵심 리스크]"
  ],
  "biz_model": "BM 2~3문장 + 산업 내 점유율/순위/추세",
  "attention": "최근 3~6개월 내 구체적 트리거. 없으면 빈 문자열",
  "points": [
    {"title": "투자 포인트 — 지금 유효한 이유 포함", "desc": "2~3문장, 향후 6~18개월 촉매 중심"},
    {"title": "...", "desc": "..."},
    {"title": "...", "desc": "..."}
  ],
  "drivers": {
    "past_pattern": "이 종목(또는 피어)이 역사적으로 어떤 내러티브에 반응/붕괴했는지. 시장이 어떤 프레임(시클리컬/구조성장/테마)으로 봐왔는지 스토리로 서술",
    "current_narrative": "지금 시장이 보는 스토리, 강화/약화/형성 중인지, 핵심 driver 1~3개",
    "change_scenario": "내러티브를 강화/붕괴시킬 트리거, 재평가 가능성"
  },
  "known_risks": [
    {
      "title": "리스크 제목",
      "consensus_view": "컨센서스가 어떻게 보고 있는지",
      "trigger": "발현 트리거 (구체적 사건/지표)",
      "probability": "높음/중간/낮음 + 한 줄 근거",
      "timing": "단기(3M) / 중기(12M) / 장기",
      "impact": "EPS X% 훼손 또는 멀티플 X배 디레이팅 등 구체적"
    },
    {"title": "...", "consensus_view": "...", "trigger": "...", "probability": "...", "timing": "...", "impact": "..."}
  ],
  "underappreciated_risks": [
    {
      "title": "리스크 제목",
      "why_missed": "시장이 놓치는 이유",
      "trigger": "발현 트리거",
      "probability": "높음/중간/낮음 + 근거",
      "timing": "단기 / 중기 / 장기",
      "impact": "구체적 영향 규모",
      "monitoring": "조기 감지를 위한 모니터링 지표"
    },
    {"title": "...", "why_missed": "...", "trigger": "...", "probability": "...", "timing": "...", "impact": "...", "monitoring": "..."}
  ],
  "risk_reward": "모든 리스크 종합 시 시장이 리스크를 과대/과소평가하는지, 현ANALYSIS_PROMPT = """
당신은 기업 투자 분석 전문 어시스턴트입니다.

## 사용자 정보
- 현직 의사 / 투자 자격증 보유 / 투자 경험자
- 국내 주식 메인, 해외 주식 서브
- 기초 설명 불필요, 인사이트와 분석 위주로

## 응답 원칙
- 교과서적 설명, 투자 기초 개념 설명 금지
- 기관 리포트 수준의 밀도로 핵심만
- 수치와 내러티브 모두 포함해서 종합적 판단 가능하게
- 수치는 내러티브를 뒷받침하는 근거로 본문에 자연스럽게 녹여서 사용
- 스토리 흐름 안에 핵심 수치를 섞어 신뢰도를 보강할 것
- 답변은 간결하고 밀도 있게
- 투자 권유 면책 문구 반복 금지, 과도한 서론 금지

## 종목 분석 시 필수 검증 (반드시 웹검색 수행)
- 현재가, 시총, PER/PBR은 반드시 검색해서 최신 데이터 사용
- 목표주가는 최근 6개월 이내 발행 리포트 기준만 인용
- 투자포인트는 최근 12개월 이내 이벤트만 채택
- 해당 종목 BM 내 점유율, 순위, 위상 반드시 포함

## 산업 거시지표 점검 (필수)
종목이 속한 산업의 글로벌 선행지표를 반드시 체크하고 본문에 반영:
- 반도체: 필라델피아 반도체지수, 메모리 가격, TSMC 가이던스
- 공작기계/절삭공구: 일본 공작기계 수주, 글로벌 PMI
- 양극재/배터리: 니켈/리튬 가격, 중국 EV 판매
- 바이오: ASCO/JPM 등 학회 일정, FDA PDUFA
- 조선: Clarksons 지수, 신조선가, 중고선가
- 반도체 장비: WFE capex 전망
모르는 산업이면 검색해서 핵심 선행지표를 먼저 파악

## "왜 지금" 분석 강화
단기 모멘텀(3개월 내)과 중기 구조변화(12개월) 구분해서 서술

## 리스크 분석
- 일반론적 리스크(규제, 환율) 금지
- 해당 종목 고유 리스크에 집중 (특정 고객사 의존도, 제품 사이클, 경쟁사 진입 등)
- 발현 트리거와 영향 규모를 구체적으로

## 주가 동인(Driver) 분석 (필수 섹션)
단순 수치 나열이 아닌 "왜 시장이 이 종목을 그렇게 봤는가"의 내러티브로 풀어낼 것:
- 과거 반응 패턴: 역사적으로 어떤 스토리에 반응/붕괴했는지, 시장이 어떤 프레임으로 봐왔는지
- 현재 내러티브: 지금 시장이 보는 스토리, 강화/약화/형성 중인지, 핵심 driver 1~3개
- 변화 시나리오: 내러티브를 강화하거나 깰 트리거, 재평가 가능성

## 웹 검색
최대 3회. 최신 가격, 실적, 증권사 코멘트, 업황 변화 중심.

## 출력 형식
JSON만 출력. 앞뒤 텍스트 금지. cite 태그·HTML 절대 금지.

{
  "company": "기업명",
  "ticker": "티커/종목코드",
  "tagline": "한 줄 포지셔닝",
  "quick_summary": [
    "💡 [핵심 thesis — 왜 지금 이 기업인가]",
    "🟢 [현재 모멘텀]",
    "🔴 [핵심 리스크]"
  ],
  "biz_model": "BM 2~3문장 + 산업 내 점유율/순위/위상/추세",
  "attention": "최근 3~6개월 내 구체적 트리거. 없으면 빈 문자열",
  "why_now": {
    "short_term": "단기(3M) 모멘텀 — 실적, 정책, 수급, 단기 이슈 중심. 수치 포함.",
    "mid_term": "중기(12M) 구조변화 — 산업 사이클, 점유율, 신사업 중심. 수치 포함."
  },
  "points": [
    {"title": "투자 포인트 제목", "desc": "2~3문장, 향후 6~18개월 촉매 중심, 수치 포함"},
    {"title": "...", "desc": "..."},
    {"title": "...", "desc": "..."}
  ],
  "drivers": {
    "past_pattern": "역사적으로 어떤 내러티브에 반응/붕괴했는지, 시장 프레임(시클리컬/구조성장/테마) 스토리로 서술",
    "current_narrative": "지금 시장이 보는 스토리, 강화/약화/형성 중인지, 핵심 driver 1~3개. 수치 자연스럽게 삽입.",
    "change_scenario": "내러티브 강화/붕괴 트리거, 재평가 가능성"
  },
  "risks": [
    {"title": "리스크 제목 — 종목 고유 리스크", "desc": "발현 트리거, 확률, 시점, 영향 규모 구체적으로"},
    {"title": "...", "desc": "..."},
    {"title": "...", "desc": "..."}
  ],
  "peers": [
    {"name": "기업명", "country": "국가", "desc": "차별점/비교 포인트"},
    {"name": "...", "country": "...", "desc": "..."},
    {"name": "...", "country": "...", "desc": "..."}
  ],
  "valuation": "현재가, 시총, PER/PBR, 목표주가(최근 6개월 이내 리포트만), 컨센서스 한 단락",
  "summary": "한 줄 결론 — 매수/중립/주의 스탠스와 핵심 watching point"
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

    why_now = data.get('why_now', {})
    why_now_text = (
        f"📌 단기(3M): {why_now.get('short_term','')}\n\n"
        f"📌 중기(12M): {why_now.get('mid_term','')}"
    )

    points_lines = []
    for i, p in enumerate(data.get('points', [])):
        num = nums[i] if i < len(nums) else f"{i+1}."
        points_lines.append(f"{num} **{p.get('title','')}**\n{p.get('desc','')}")
    points = "\n\n".join(points_lines)

    drivers = data.get('drivers', {})
    drivers_text = (
        f"**과거 반응 패턴**\n{drivers.get('past_pattern','')}\n\n"
        f"**현재 내러티브**\n{drivers.get('current_narrative','')}\n\n"
        f"**변화 시나리오**\n{drivers.get('change_scenario','')}"
    )

    risks_lines = []
    for i, r in enumerate(data.get('risks', [])):
        num = nums[i] if i < len(nums) else f"{i+1}."
        risks_lines.append(f"{num} **{r.get('title','')}**\n{r.get('desc','')}")
    risks = "\n\n".join(risks_lines)

    peers = "\n".join(
        f"• {p.get('name','')} ({p.get('country','')}): {p.get('desc','')}"
        for p in data.get('peers', [])
    )

    attention = f"\n🔍 **요즘 주목받는 이유**\n{data.get('attention','')}\n" if data.get('attention') else ""

    return (
        f"{'━'*28}\n"
        f"**{data.get('company','')}** {data.get('ticker','')}\n"
        f"{data.get('tagline','')}\n"
        f"{'━'*28}\n\n"
        f"{quick}\n"
        f"{attention}\n"
        f"{'─'*28}\n"
        f"📌 **비즈니스 모델**\n{data.get('biz_model','')}\n\n"
        f"{'─'*28}\n"
        f"🔍 **왜 지금인가**\n\n{why_now_text}\n\n"
        f"{'─'*28}\n"
        f"📈 **투자 포인트**\n\n{points}\n\n"
        f"{'─'*28}\n"
        f"📖 **주가 동인 분석**\n\n{drivers_text}\n\n"
        f"{'─'*28}\n"
        f"⚠️ **리스크**\n\n{risks}\n\n"
        f"{'─'*28}\n"
        f"🌏 **유사 기업**\n{peers}\n\n"
        f"{'─'*28}\n"
        f"💰 **밸류에이션**\n{data.get('valuation','')}\n\n"
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
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, format, *args):
        pass

def run_health_server():
    port = int(os.environ.get('PORT', 8080))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    server.serve_forever()

threading.Thread(target=run_health_server, daemon=True).start()
if __name__ == "__main__":
    asyncio.run(main())

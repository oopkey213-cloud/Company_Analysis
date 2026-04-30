import os
import logging
import asyncio
import anthropic
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes
from telegram.constants import ParseMode

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """
당신은 기업 투자 분석 전문 어시스턴트입니다.

## 핵심 원칙
- 사용자는 투자 자격증 보유 현직 의사로, 투자 기초 설명은 불필요
- 기관 리포트 수준의 밀도와 시각으로 작성
- 웹 검색을 반드시 수행하여 최신 뉴스, 실적, 이슈를 반영
- 정성적 인사이트 중심 — 숫자 나열보다 "왜 중요한가"에 집중

## 기업 분석 요청 시 반드시 아래 구조로 출력하세요

# [기업명] ([티커]) — [한 줄 포지셔닝]

> [비즈니스 모델 2~3문장]

---

## 📈 투자 포인트

| # | 포인트 | 핵심 근거 |
|---|--------|-----------|
| ① | [제목] | [한 줄 요약] |
| ② | [제목] | [한 줄 요약] |
| ③ | [제목] | [한 줄 요약] |

**① [포인트 제목]**
[2~3문장 상세 설명]

**② [포인트 제목]**
[2~3문장]

**③ [포인트 제목]**
[2~3문장]

---

## 🔍 지금 주목받는 이유
[해당 없으면 섹션 생략]

---

## ⚠️ 리스크

| # | 리스크 | 발현 시 영향 |
|---|--------|-------------|
| ① | [제목] | [한 줄] |
| ② | [제목] | [한 줄] |
| ③ | [제목] | [한 줄] |

**① [리스크 제목]**
[구체적 시나리오]

**② [리스크 제목]**
[구체적 시나리오]

**③ [리스크 제목]**
[구체적 시나리오]

---

## 🌏 유사 기업 비교

| 기업명 | 국가 | 주력 사업 | 특징 |
|--------|------|-----------|------|
| [기업] | [국가] | [사업] | [비교 포인트] |

[상대적 포지션 코멘트 2~3줄]

---

## 💰 목표주가
[증권사명] **[목표가]** (현재가 대비 괴리율 **+X%**)

---

## 💡 한 줄 요약
[투자 매력도와 주의사항 한 문장]

---
*본 내용은 투자 참고용 분석이며 투자 권유가 아닙니다.*
"""


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_message = update.message.text
    chat_id = update.effective_chat.id

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        response = claude.messages.create(
            model="claude-sonnet-4-5-20251001",
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            tools=[{"type": "web_search_20250305", "name": "web_search"}],
            messages=[{"role": "user", "content": user_message}]
        )

        reply = ""
        for block in response.content:
            if block.type == "text":
                reply += block.text

        if not reply:
            reply = "분석 중 오류가 발생했습니다. 다시 시도해주세요."

        max_len = 4000
        if len(reply) <= max_len:
            await update.message.reply_text(reply, parse_mode=ParseMode.MARKDOWN)
        else:
            chunks = [reply[i:i+max_len] for i in range(0, len(reply), max_len)]
            for chunk in chunks:
                await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("오류가 발생했습니다. 잠시 후 다시 시도해주세요.")


async def main():
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = ApplicationBuilder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("봇 시작!")
    async with app:
        await app.start()
        await app.updater.start_polling()
        await asyncio.Event().wait()  # 영구 실행


if __name__ == "__main__":
    asyncio.run(main())

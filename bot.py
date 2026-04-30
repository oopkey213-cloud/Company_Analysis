import os
import re
import json
import logging
import asyncio
import io
import requests
import anthropic
from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

FONT_REG = "/tmp/font_reg.ttf"
FONT_BOLD = "/tmp/font_bold.ttf"


def ensure_fonts():
    items = [
        (FONT_REG, "https://cdn.jsdelivr.net/gh/google/fonts/ofl/nanumgothic/NanumGothic-Regular.ttf"),
        (FONT_BOLD, "https://cdn.jsdelivr.net/gh/google/fonts/ofl/nanumgothic/NanumGothic-ExtraBold.ttf"),
    ]
    for path, url in items:
        if not os.path.exists(path) or os.path.getsize(path) < 10000:
            try:
                r = requests.get(url, timeout=30)
                r.raise_for_status()
                with open(path, 'wb') as fh:
                    fh.write(r.content)
                logging.info(f"Font downloaded: {path} ({len(r.content)} bytes)")
            except Exception as e:
                logging.error(f"Font download failed: {e}")


ensure_fonts()


def f(size, bold=False):
    path = FONT_BOLD if bold else FONT_REG
    return ImageFont.truetype(path, size)


ANALYSIS_PROMPT = """
당신은 기업 투자 분석 전문 어시스턴트입니다.

## 핵심 원칙
- 사용자는 투자 자격증 보유 현직 의사로, 투자 기초 설명은 불필요
- 기관 리포트 수준의 밀도와 시각으로 작성
- 웹 검색 최대 2회 수행하여 최신 뉴스/이슈/모멘텀 파악
- 정성적 인사이트 중심

## 출력 형식
반드시 아래 JSON만 출력. 앞뒤 텍스트 금지. cite 태그 및 HTML 태그 절대 사용 금지.

{
  "company": "기업명",
  "ticker": "티커/종목코드",
  "tagline": "한 줄 포지셔닝",
  "quick_summary": [
    "선결론 1줄 - 핵심 투자 매력 (예: 반도체 사이클 턴어라운드 수혜 최선호주)",
    "선결론 2줄 - 현재 상황/모멘텀 (예: 2025년 영업이익 흑자전환 확실시, 주가 저점 탈출 구간)",
    "선결론 3줄 - 리스크/주의사항 (예: 중국 경쟁 심화로 마진 압박 지속, 단기 변동성 주의)"
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
  "summary": "최종 한 줄 요약"
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


def wrap_text(text, font, max_width, draw):
    lines = []
    line = ""
    for char in str(text):
        test = line + char
        w = draw.textlength(test, font=font)
        if w > max_width and line:
            lines.append(line)
            line = char
        else:
            line = test
    if line:
        lines.append(line)
    return lines


def text_height(font):
    bbox = font.getbbox("가나다")
    return bbox[3] - bbox[1]


class Card:
    W = 720
    PAD = 20
    IP = 16
    GAP = 10

    BG = (248, 248, 246)
    WHITE = (255, 255, 255)
    BORDER = (224, 221, 212)
    INNER = (240, 237, 228)
    TEXT1 = (26, 26, 26)
    TEXT2 = (85, 85, 85)
    TEXT3 = (153, 153, 153)

    SECTIONS = {
        'green':  ((234, 243, 222), (59, 109, 17)),
        'red':    ((252, 235, 235), (163, 45, 45)),
        'blue':   ((230, 241, 251), (24, 95, 165)),
        'amber':  ((250, 238, 218), (133, 79, 11)),
        'purple': ((238, 237, 254), (83, 74, 183)),
        'gray':   ((241, 239, 232), (95, 94, 90)),
        'teal':   ((225, 245, 238), (15, 110, 86)),
    }

    def __init__(self):
        self.img = Image.new('RGB', (self.W, 4000), self.BG)
        self.draw = ImageDraw.Draw(self.img)
        self.y = self.PAD

    def rect(self, x, y, w, h, fill, border=None, radius=8):
        self.draw.rounded_rectangle(
            [x, y, x + w, y + h],
            radius=radius, fill=fill,
            outline=border or self.BORDER, width=1
        )

    def section_header(self, x, y, w, label, color_key):
        bg, fg = self.SECTIONS[color_key]
        self.draw.rectangle([x, y, x + w, y + 26], fill=bg)
        self.draw.text((x + self.IP, y + 6), label, font=f(11, True), fill=fg)
        return 26

    def draw_header(self, data):
        h = 82
        self.rect(self.PAD, self.y, self.W - self.PAD * 2, h, self.WHITE)
        x = self.PAD + self.IP
        self.draw.text((x, self.y + 10), data.get('ticker', ''), font=f(11), fill=self.TEXT3)
        self.draw.text((x, self.y + 24), data.get('company', ''), font=f(22, True), fill=self.TEXT1)
        lines = wrap_text(data.get('tagline', ''), f(12), self.W - self.PAD * 2 - self.IP * 2, self.draw)
        ty = self.y + 54
        for line in lines:
            self.draw.text((x, ty), line, font=f(12), fill=self.TEXT2)
            ty += text_height(f(12)) + 3
        self.y += h + self.GAP

    def draw_quick_summary(self, items):
        x = self.PAD
        w = self.W - self.PAD * 2
        fn = f(13)
        lh = text_height(fn)
        icons = ["✦", "✦", "✦"]
        icon_colors = [(24, 95, 165), (59, 109, 17), (163, 45, 45)]

        all_lines = []
        for item in items:
            lines = wrap_text(item, fn, w - self.IP * 2 - 18, self.draw)
            all_lines.append(lines)

        total_h = 26
        for lines in all_lines:
            total_h += len(lines) * (lh + 3) + 12

        self.rect(x, self.y, w, total_h, self.WHITE)
        self.section_header(x, self.y, w, '선결론 3줄 요약', 'teal')
        iy = self.y + 26

        for i, (item, lines) in enumerate(zip(items, all_lines)):
            if i > 0:
                self.draw.line([(x + self.IP, iy), (x + w - self.IP, iy)], fill=self.INNER, width=1)
            iy += 8
            icon = icons[i] if i < len(icons) else "•"
            color = icon_colors[i] if i < len(icon_colors) else self.TEXT3
            self.draw.text((x + self.IP, iy + 1), icon, font=f(12, True), fill=color)
            tx = x + self.IP + 18
            for line in lines:
                self.draw.text((tx, iy), line, font=fn, fill=self.TEXT1)
                iy += lh + 3
            iy += 6

        self.y += total_h + self.GAP

    def draw_text_section(self, label, color_key, text):
        x = self.PAD
        w = self.W - self.PAD * 2
        fn = f(12)
        lines = wrap_text(text, fn, w - self.IP * 2, self.draw)
        lh = text_height(fn)
        total_h = 26 + len(lines) * (lh + 3) + 18
        self.rect(x, self.y, w, total_h, self.WHITE)
        self.section_header(x, self.y, w, label, color_key)
        iy = self.y + 26 + 10
        for line in lines:
            self.draw.text((x + self.IP, iy), line, font=fn, fill=self.TEXT2)
            iy += lh + 3
        self.y += total_h + self.GAP

    def draw_points_section(self, label, color_key, items):
        x = self.PAD
        w = self.W - self.PAD * 2
        nums = ["①", "②", "③", "④", "⑤"]
        fn_title = f(13, True)
        fn_desc = f(12)
        lh_t = text_height(fn_title)
        lh_d = text_height(fn_desc)
        inner_w = w - self.IP * 2 - 22

        all_lines = []
        for item in items:
            tl = wrap_text(item.get('title', ''), fn_title, inner_w, self.draw)
            dl = wrap_text(item.get('desc', ''), fn_desc, inner_w, self.draw)
            all_lines.append((tl, dl))

        total_h = 26
        for tl, dl in all_lines:
            total_h += len(tl) * (lh_t + 3) + len(dl) * (lh_d + 3) + 22

        self.rect(x, self.y, w, total_h, self.WHITE)
        self.section_header(x, self.y, w, label, color_key)
        iy = self.y + 26

        for i, (item, (tlines, dlines)) in enumerate(zip(items, all_lines)):
            if i > 0:
                self.draw.line([(x + self.IP, iy), (x + w - self.IP, iy)], fill=self.INNER, width=1)
            iy += 10
            num = nums[i] if i < len(nums) else str(i + 1)
            self.draw.text((x + self.IP, iy), num, font=fn_title, fill=self.TEXT3)
            tx = x + self.IP + 22
            for line in tlines:
                self.draw.text((tx, iy), line, font=fn_title, fill=self.TEXT1)
                iy += lh_t + 3
            iy += 2
            for line in dlines:
                self.draw.text((tx, iy), line, font=fn_desc, fill=self.TEXT2)
                iy += lh_d + 3
            iy += 8

        self.y += total_h + self.GAP

    def draw_peers(self, peers):
        x = self.PAD
        w = self.W - self.PAD * 2
        hw = (w - 1) // 2
        fn_name = f(13, True)
        fn_country = f(11)
        fn_desc = f(12)
        lh_n = text_height(fn_name)
        lh_c = text_height(fn_country)
        lh_d = text_height(fn_desc)

        def cell_h(p):
            dl = wrap_text(p.get('desc', ''), fn_desc, hw - self.IP * 2, self.draw)
            return lh_n + 4 + lh_c + 4 + len(dl) * (lh_d + 3) + 20

        rows = [peers[i:i+2] for i in range(0, len(peers), 2)]
        row_heights = [max(cell_h(p) for p in row) for row in rows]
        total_h = 26 + sum(row_heights)

        self.rect(x, self.y, w, total_h, self.WHITE)
        self.section_header(x, self.y, w, '유사 기업', 'purple')
        iy = self.y + 26

        for ri, (row, rh) in enumerate(zip(rows, row_heights)):
            if ri > 0:
                self.draw.line([(x, iy), (x + w, iy)], fill=self.INNER, width=1)
            for ci, peer in enumerate(row):
                cx = x + ci * (hw + 1)
                if ci == 1:
                    self.draw.line([(x + hw, iy), (x + hw, iy + rh)], fill=self.INNER, width=1)
                py = iy + 10
                self.draw.text((cx + self.IP, py), peer.get('name', ''), font=fn_name, fill=self.TEXT1)
                py += lh_n + 4
                self.draw.text((cx + self.IP, py), peer.get('country', ''), font=fn_country, fill=self.TEXT3)
                py += lh_c + 4
                dl = wrap_text(peer.get('desc', ''), fn_desc, hw - self.IP * 2, self.draw)
                for line in dl:
                    self.draw.text((cx + self.IP, py), line, font=fn_desc, fill=self.TEXT2)
                    py += lh_d + 3
            iy += rh

        self.y += total_h + self.GAP

    def draw_notice(self):
        notice = "본 내용은 투자 참고용 분석이며 투자 권유가 아닙니다."
        tw = self.draw.textlength(notice, font=f(11))
        self.draw.text(((self.W - tw) // 2, self.y), notice, font=f(11), fill=self.TEXT3)
        self.y += 20

    def render(self, data) -> io.BytesIO:
        self.draw_header(data)
        self.draw_quick_summary(data.get('quick_summary', []))
        self.draw_text_section('비즈니스 모델', 'blue', data.get('biz_model', ''))
        self.draw_points_section('투자 포인트', 'green', data.get('points', []))
        if data.get('attention'):
            self.draw_text_section('지금 주목받는 이유', 'blue', data.get('attention', ''))
        self.draw_points_section('리스크', 'red', data.get('risks', []))
        self.draw_peers(data.get('peers', []))
        self.draw_text_section('한 줄 요약', 'amber', data.get('summary', ''))
        self.draw_notice()

        final = self.img.crop((0, 0, self.W, self.y + self.PAD))
        buf = io.BytesIO()
        final.save(buf, format='JPEG', quality=90)
        buf.seek(0)
        return buf


def build_text(data: dict) -> str:
    nums = ["①", "②", "③", "④", "⑤"]
    quick = "\n".join(
        f"{'💡🟢🔴'[i] if i < 3 else '•'} {s}"
        for i, s in enumerate(data.get('quick_summary', []))
    )
    points = "\n\n".join(
        f"{nums[i] if i < len(nums) else i+1} {p.get('title','')}\n{p.get('desc','')}"
        for i, p in enumerate(data.get('points', []))
    )
    risks = "\n\n".join(
        f"{nums[i] if i < len(nums) else i+1} {r.get('title','')}\n{r.get('desc','')}"
        for i, r in enumerate(data.get('risks', []))
    )
    peers = "\n".join(
        f"• {p.get('name','')} ({p.get('country','')}): {p.get('desc','')}"
        for p in data.get('peers', [])
    )
    attention = f"\n🔍 지금 주목받는 이유\n{data.get('attention','')}\n" if data.get('attention') else ""
    return (
        f"{'='*30}\n{data.get('company','')} ({data.get('ticker','')})\n"
        f"{data.get('tagline','')}\n{'='*30}\n\n"
        f"📋 선결론 3줄 요약\n{quick}\n\n"
        f"📌 비즈니스 모델\n{data.get('biz_model','')}\n\n"
        f"📈 투자 포인트\n{points}\n{attention}\n"
        f"⚠️ 리스크\n{risks}\n\n"
        f"🌏 유사 기업\n{peers}\n\n"
        f"💡 한 줄 요약\n{data.get('summary','')}\n\n"
        f"*본 내용은 투자 참고용이며 투자 권유가 아닙니다.*"
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
await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")
        try:
            img_buf = Card().render(data)
            await update.message.reply_photo(photo=img_buf)
        except Exception as img_err:
            logging.error(f"Image error: {img_err}", exc_info=True)
            await update.message.reply_text("(이미지 생성 실패 — 텍스트만 전송됨)")

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

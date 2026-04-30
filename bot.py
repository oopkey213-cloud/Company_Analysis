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
    if not os.path.exists(FONT_REG):
        r = requests.get(
            "https://cdn.jsdelivr.net/gh/google/fonts/ofl/nanumgothic/NanumGothic-Regular.ttf",
            timeout=30
        )
        open(FONT_REG, 'wb').write(r.content)
    if not os.path.exists(FONT_BOLD):
        r = requests.get(
            "https://cdn.jsdelivr.net/gh/google/fonts/ofl/nanumgothic/NanumGothic-ExtraBold.ttf",
            timeout=30
        )
        open(FONT_BOLD, 'wb').write(r.content)


ensure_fonts()


def f(size, bold=False):
    path = FONT_BOLD if bold else FONT_REG
    return ImageFont.truetype(path, size)


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
    }

    def __init__(self):
        self.img = Image.new('RGB', (self.W, 4000), self.BG)
        self.draw = ImageDraw.Draw(self.img)
        self.y = self.PAD

    def rect(self, x, y, w, h, fill, border=None, radius=8):
        self.draw.rounded_rectangle(
            [x, y, x + w, y + h],
            radius=radius,
            fill=fill,
            outline=border or self.BORDER,
            width=1
        )

    def section_header(self, x, y, w, label, color_key):
        bg, fg = self.SECTIONS[color_key]
        h = 26
        self.draw.rectangle([x, y, x + w, y + h], fill=bg)
        self.draw.text((x + self.IP, y + 6), label, font=f(11, True), fill=fg)
        return h

    def text_block(self, x, y, w, text, font, color, line_gap=4):
        lines = wrap_text(text, font, w, self.draw)
        lh = text_height(font)
        for line in lines:
            self.draw.text((x, y), line, font=font, fill=color)
            y += lh + line_gap
        return y

    def draw_header(self, data):
        h = 80
        self.rect(self.PAD, self.y, self.W - self.PAD * 2, h, self.WHITE)
        x = self.PAD + self.IP
        ticker = f"{data.get('ticker','')} "
        self.draw.text((x, self.y + 12), ticker, font=f(11), fill=self.TEXT3)
        self.draw.text((x, self.y + 26), data.get('company', ''), font=f(22, True), fill=self.TEXT1)
        self.text_block(x, self.y + 54, self.W - self.PAD * 2 - self.IP * 2,
                        data.get('tagline', ''), f(12), self.TEXT2)
        self.y += h + self.GAP

    def draw_valuation(self, v):
        mw = (self.W - self.PAD * 2 - self.GAP * 3) // 4
        labels = ['시가총액', 'PER', 'PBR', 'ROE']
        keys = ['market_cap', 'per', 'pbr', 'roe']
        for i, (lbl, key) in enumerate(zip(labels, keys)):
            x = self.PAD + i * (mw + self.GAP)
            self.rect(x, self.y, mw, 58, self.WHITE)
            self.draw.text((x + self.IP, self.y + 10), lbl, font=f(11), fill=self.TEXT3)
            self.draw.text((x + self.IP, self.y + 26), v.get(key, '-'), font=f(16, True), fill=self.TEXT1)
        self.y += 58 + self.GAP

    def draw_financials(self, rows):
        x = self.PAD
        w = self.W - self.PAD * 2
        header_h = 26
        row_h = 28
        total_h = header_h + row_h * len(rows) + 1
        self.rect(x, self.y, w, total_h, self.WHITE)
        sh = self.section_header(x, self.y, w, '최근 3개년 실적', 'gray')

        cols = ['연도', '매출액', '영업이익', 'OPM', '순이익', 'NPM']
        col_w = [60, 130, 120, 70, 120, 70]
        col_x = [x + self.IP]
        for cw in col_w[:-1]:
            col_x.append(col_x[-1] + cw)

        hy = self.y + sh + 6
        for i, (col, cx) in enumerate(zip(cols, col_x)):
            align = 'left' if i == 0 else 'right'
            tw = self.draw.textlength(col, font=f(11))
            tx = cx if i == 0 else cx + col_w[i] - tw - 4
            self.draw.text((tx, hy), col, font=f(11, True), fill=self.TEXT3)

        for ri, row in enumerate(rows):
            ry = self.y + sh + 24 + ri * row_h
            self.draw.line([(x + self.IP, ry), (x + w - self.IP, ry)], fill=self.INNER, width=1)
            vals = [
                row.get('year', '-'),
                row.get('revenue', '-'),
                row.get('op_profit', '-'),
                row.get('opm', '-'),
                row.get('net_profit', '-'),
                row.get('npm', '-'),
            ]
            for i, (val, cx) in enumerate(zip(vals, col_x)):
                color = (24, 95, 165) if i in [3, 5] else self.TEXT1
                bold = i == 0
                font = f(12, bold)
                tw = self.draw.textlength(val, font=font)
                tx = cx if i == 0 else cx + col_w[i] - tw - 4
                self.draw.text((tx, ry + 8), val, font=font, fill=color)

        self.y += total_h + self.GAP

    def draw_section(self, label, color_key, items, title_key='title', desc_key='desc', nums=None):
        x = self.PAD
        w = self.W - self.PAD * 2
        nums = nums or ["①", "②", "③", "④", "⑤"]

        lines_per_item = []
        for item in items:
            title_lines = wrap_text(item.get(title_key, ''), f(13, True), w - self.IP * 2 - 24, self.draw)
            desc_lines = wrap_text(item.get(desc_key, ''), f(12), w - self.IP * 2 - 24, self.draw)
            lines_per_item.append((title_lines, desc_lines))

        lh_title = text_height(f(13, True))
        lh_desc = text_height(f(12))

        total_h = 26
        for tl, dl in lines_per_item:
            total_h += len(tl) * (lh_title + 3) + len(dl) * (lh_desc + 3) + 20

        self.rect(x, self.y, w, total_h, self.WHITE)
        self.section_header(x, self.y, w, label, color_key)
        iy = self.y + 26

        for i, (item, (tlines, dlines)) in enumerate(zip(items, lines_per_item)):
            if i > 0:
                self.draw.line([(x + self.IP, iy), (x + w - self.IP, iy)], fill=self.INNER, width=1)
            iy += 10
            num = nums[i] if i < len(nums) else str(i + 1)
            self.draw.text((x + self.IP, iy), num, font=f(13, True), fill=self.TEXT3)
            tx = x + self.IP + 22
            for line in tlines:
                self.draw.text((tx, iy), line, font=f(13, True), fill=self.TEXT1)
                iy += lh_title + 3
            iy += 2
            for line in dlines:
                self.draw.text((tx, iy), line, font=f(12), fill=self.TEXT2)
                iy += lh_desc + 3
            iy += 6

        self.y += total_h + self.GAP

    def draw_attention(self, text):
        if not text:
            return
        x = self.PAD
        w = self.W - self.PAD * 2
        lines = wrap_text(text, f(12), w - self.IP * 2, self.draw)
        lh = text_height(f(12))
        total_h = 26 + len(lines) * (lh + 3) + 16
        self.rect(x, self.y, w, total_h, self.WHITE)
        self.section_header(x, self.y, w, '지금 주목받는 이유', 'blue')
        iy = self.y + 26 + 10
        for line in lines:
            self.draw.text((x + self.IP, iy), line, font=f(12), fill=self.TEXT2)
            iy += lh + 3
        self.y += total_h + self.GAP

    def draw_peers(self, peers):
        x = self.PAD
        w = self.W - self.PAD * 2
        hw = (w - 1) // 2
        lh_name = text_height(f(13, True))
        lh_country = text_height(f(11))
        lh_desc = text_height(f(12))

        cell_heights = []
        for p in peers:
            dl = wrap_text(p.get('desc', ''), f(12), hw - self.IP * 2, self.draw)
            cell_heights.append(lh_name + 4 + lh_country + 4 + len(dl) * (lh_desc + 3) + 20)

        rows = [peers[i:i+2] for i in range(0, len(peers), 2)]
        row_heights = []
        for i, row in enumerate(rows):
            idxs = [i * 2, i * 2 + 1]
            rh = max((cell_heights[j] for j in idxs if j < len(peers)), default=60)
            row_heights.append(rh)

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
                self.draw.text((cx + self.IP, py), peer.get('name', ''), font=f(13, True), fill=self.TEXT1)
                py += lh_name + 4
                self.draw.text((cx + self.IP, py), peer.get('country', ''), font=f(11), fill=self.TEXT3)
                py += lh_country + 4
                dl = wrap_text(peer.get('desc', ''), f(12), hw - self.IP * 2, self.draw)
                for line in dl:
                    self.draw.text((cx + self.IP, py), line, font=f(12), fill=self.TEXT2)
                    py += lh_desc + 3
            iy += rh

        self.y += total_h + self.GAP

    def draw_summary(self, text):
        x = self.PAD
        w = self.W - self.PAD * 2
        lines = wrap_text(text, f(12), w - self.IP * 2, self.draw)
        lh = text_height(f(12))
        total_h = 26 + len(lines) * (lh + 3) + 16
        self.rect(x, self.y, w, total_h, self.WHITE)
        self.section_header(x, self.y, w, '한 줄 요약', 'amber')
        iy = self.y + 26 + 10
        for line in lines:
            self.draw.text((x + self.IP, iy), line, font=f(12), fill=self.TEXT2)
            iy += lh + 3
        self.y += total_h + self.GAP

    def draw_notice(self):
        notice = "본 내용은 투자 참고용 분석이며 투자 권유가 아닙니다."
        tw = self.draw.textlength(notice, font=f(11))
        self.draw.text(((self.W - tw) // 2, self.y), notice, font=f(11), fill=self.TEXT3)
        self.y += 20

    def render(self, data) -> bytes:
        self.draw_header(data)
        self.draw_valuation(data.get('valuation', {}))
        self.draw_financials(data.get('financials', []))
        self.draw_section('투자 포인트', 'green', data.get('points', []))
        self.draw_attention(data.get('attention', ''))
        self.draw_section('리스크', 'red', data.get('risks', []))
        self.draw_peers(data.get('peers', []))
        self.draw_summary(data.get('summary', ''))
        self.draw_notice()

        final = self.img.crop((0, 0, self.W, self.y + self.PAD))
        buf = io.BytesIO()
        final.save(buf, format='PNG', optimize=True)
        return buf.getvalue()


def build_text(data: dict) -> str:
    nums = ["①", "②", "③", "④", "⑤"]
    v = data.get('valuation', {})
    valuation = (
        f"시가총액: {v.get('market_cap','-')}  |  "
        f"PER: {v.get('per','-')}  |  "
        f"PBR: {v.get('pbr','-')}  |  "
        f"ROE: {v.get('roe','-')}"
    )
    fin_lines = ["연도   매출액     영업이익   OPM   순이익    NPM"]
    for row in data.get('financials', []):
        fin_lines.append(
            f"{row.get('year','-')}  {row.get('revenue','-')}  "
            f"{row.get('op_profit','-')}  {row.get('opm','-')}  "
            f"{row.get('net_profit','-')}  {row.get('npm','-')}"
        )
    points_lines = []
    for i, p in enumerate(data.get('points', [])):
        num = nums[i] if i < len(nums) else f"{i+1}."
        points_lines.append(f"{num} {p.get('title','')}\n{p.get('desc','')}")
    risks_lines = []
    for i, r in enumerate(data.get('risks', [])):
        num = nums[i] if i < len(nums) else f"{i+1}."
        risks_lines.append(f"{num} {r.get('title','')}\n{r.get('desc','')}")
    peers_lines = [f"• {p.get('name','')} ({p.get('country','')}): {p.get('desc','')}"
                   for p in data.get('peers', [])]
    attention = f"\n🔍 지금 주목받는 이유\n{data.get('attention','')}\n" if data.get('attention') else ""
    return (
        f"{'='*30}\n"
        f"{data.get('company','')} ({data.get('ticker','')})\n"
        f"{data.get('tagline','')}\n"
        f"{'='*30}\n\n"
        f"📊 밸류에이션\n{valuation}\n\n"
        f"📋 최근 3개년 실적\n" + "\n".join(fin_lines) + "\n\n"
        f"📌 비즈니스 모델\n{data.get('biz_model','')}\n\n"
        f"📈 투자 포인트\n" + "\n\n".join(points_lines) + "\n"
        f"{attention}\n"
        f"⚠️ 리스크\n" + "\n\n".join(risks_lines) + "\n\n"
        f"🌏 유사 기업\n" + "\n".join(peers_lines) + "\n\n"
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

        raw = re.sub(r'<[^>]+>', '', raw).strip()
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if not match:
            await update.message.reply_text("분석 데이터를 파싱하지 못했습니다. 다시 시도해주세요.")
            return

        data = json.loads(match.group())
        data = clean_data(data)

        await update.message.reply_text(build_text(data))

        await context.bot.send_chat_action(chat_id=chat_id, action="upload_photo")
        img_bytes = Card().render(data)
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

if __name__ == "__main__":
    asyncio.run(main())

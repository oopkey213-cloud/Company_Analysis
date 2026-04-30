import os, re, json, logging, asyncio, io, tempfile, anthropic
from PIL import Image, ImageDraw, ImageFont
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, filters, ContextTypes

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
claude = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

def find_font():
    font_path = "/tmp/NanumGothic.ttf"
    if not os.path.exists(font_path):
        try:
            import urllib.request
            url = "https://github.com/naver/nanumfont/raw/master/fonts/NanumFontSetup_TTF_GOTHIC/NanumGothic.ttf"
            urllib.request.urlretrieve(url, font_path)
            logging.info(f"Font downloaded: {font_path}")
        except Exception as e:
            logging.error(f"Font download failed: {e}")
            for p in ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]:
                if os.path.exists(p):
                    return p
            return None
    return font_path

FONT_PATH = find_font()

def get_font(size):
    if FONT_PATH:
        try:
            return ImageFont.truetype(FONT_PATH, size)
        except Exception as e:
            logging.error(f"Font load error ({FONT_PATH}): {e}")
    try:
        return ImageFont.load_default(size=size)
    except:
        return ImageFont.load_default()

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
    "선결론 1줄 - 핵심 투자 매력",
    "선결론 2줄 - 현재 상황/모멘텀",
    "선결론 3줄 - 리스크/주의사항"
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

def strip_tags(text):
    return re.sub(r'<[^>]+>', '', str(text)).strip()

def clean_data(data):
    def clean(v):
        if isinstance(v, str): return strip_tags(v)
        if isinstance(v, list): return [clean(i) for i in v]
        if isinstance(v, dict): return {k: clean(vv) for k, vv in v.items()}
        return v
    return {k: clean(v) for k, v in data.items()}

def wrap_text(text, font, max_width, draw):
    lines, line = [], ""
    for char in str(text):
        test = line + char
        try:
            w = draw.textlength(test, font=font)
        except:
            w = len(test) * 13
        if w > max_width and line:
            lines.append(line)
            line = char
        else:
            line = test
    if line:
        lines.append(line)
    return lines or [""]

def get_line_height(font):
    try:
        bbox = font.getbbox("Ag가나")
        return bbox[3] - bbox[1] + 2
    except:
        return 18

def create_image(data) -> bytes:
    W = 720
    PAD = 20
    IP = 16
    GAP = 8

    BG = (248, 248, 246)
    WHITE = (255, 255, 255)
    BORDER = (220, 217, 208)
    INNER = (238, 235, 226)
    T1 = (26, 26, 26)
    T2 = (85, 85, 85)
    T3 = (150, 150, 150)

    COLORS = {
        'teal':   ((220, 245, 235), (15, 110, 86)),
        'blue':   ((228, 240, 252), (24, 95, 165)),
        'green':  ((232, 244, 220), (55, 105, 15)),
        'red':    ((252, 233, 233), (160, 40, 40)),
        'purple': ((236, 235, 254), (80, 70, 180)),
        'amber':  ((250, 236, 215), (130, 75, 10)),
    }

    fn_sm = get_font(11)
    fn_reg = get_font(13)
    fn_md = get_font(14)
    fn_bold = get_font(14)
    fn_title = get_font(22)

    lh_sm = get_line_height(fn_sm)
    lh_reg = get_line_height(fn_reg)
    lh_bold = get_line_height(fn_bold)

    img = Image.new('RGB', (W, 5000), BG)
    draw = ImageDraw.Draw(img)
    y = PAD

    def hdr_block(label, color_key):
        bg, fg = COLORS[color_key]
        draw.rectangle([PAD, y, W-PAD, y+26], fill=bg)
        draw.text((PAD+IP, y+6), label, font=fn_sm, fill=fg)
        return 26

    def card_bg(x, cy, w, h):
        draw.rectangle([x, cy, x+w, cy+h], fill=WHITE, outline=BORDER, width=1)

    def text_section(label, color_key, text):
        nonlocal y
        inner_w = W - PAD*2 - IP*2
        lines = wrap_text(text, fn_reg, inner_w, draw)
        h = 26 + len(lines)*lh_reg + 16
        card_bg(PAD, y, W-PAD*2, h)
        hb = hdr_block(label, color_key)
        iy = y + hb + 8
        for line in lines:
            draw.text((PAD+IP, iy), line, font=fn_reg, fill=T2)
            iy += lh_reg
        y += h + GAP

    def points_section(label, color_key, items):
        nonlocal y
        nums = ["①","②","③","④","⑤"]
        inner_w = W - PAD*2 - IP*2 - 22
        all_lines = []
        for item in items:
            tl = wrap_text(item.get('title',''), fn_bold, inner_w, draw)
            dl = wrap_text(item.get('desc',''), fn_reg, inner_w, draw)
            all_lines.append((tl, dl))
        h = 26 + sum(len(tl)*lh_bold + len(dl)*lh_reg + 20 for tl, dl in all_lines)
        card_bg(PAD, y, W-PAD*2, h)
        hdr_block(label, color_key)
        iy = y + 26
        for i, (item, (tl, dl)) in enumerate(zip(items, all_lines)):
            if i > 0:
                draw.line([(PAD+IP, iy), (W-PAD-IP, iy)], fill=INNER, width=1)
            iy += 8
            num = nums[i] if i < len(nums) else str(i+1)
            draw.text((PAD+IP, iy), num, font=fn_bold, fill=T3)
            tx = PAD + IP + 22
            for line in tl:
                draw.text((tx, iy), line, font=fn_bold, fill=T1)
                iy += lh_bold
            iy += 2
            for line in dl:
                draw.text((tx, iy), line, font=fn_reg, fill=T2)
                iy += lh_reg
            iy += 8
        y += h + GAP

    # Header
    hdr_h = 84
    card_bg(PAD, y, W-PAD*2, hdr_h)
    draw.text((PAD+IP, y+10), data.get('ticker',''), font=fn_sm, fill=T3)
    draw.text((PAD+IP, y+24), data.get('company',''), font=fn_title, fill=T1)
    taglines = wrap_text(data.get('tagline',''), fn_reg, W-PAD*2-IP*2, draw)
    ty = y + 54
    for line in taglines:
        draw.text((PAD+IP, ty), line, font=fn_reg, fill=T2)
        ty += lh_reg
    y += hdr_h + GAP

    # Quick summary
    qs = data.get('quick_summary', [])
    ic = [(24,95,165),(59,109,17),(163,45,45)]
    qs_lines = [wrap_text(s, fn_reg, W-PAD*2-IP*2-18, draw) for s in qs]
    qs_h = 26 + sum(len(ll)*lh_reg + 14 for ll in qs_lines)
    card_bg(PAD, y, W-PAD*2, qs_h)
    hdr_block('선결론 3줄 요약', 'teal')
    iy = y + 26
    for i, (s, lines) in enumerate(zip(qs, qs_lines)):
        if i > 0:
            draw.line([(PAD+IP, iy), (W-PAD-IP, iy)], fill=INNER, width=1)
        iy += 6
        color = ic[i] if i < len(ic) else T3
        draw.text((PAD+IP, iy+1), "▶", font=fn_sm, fill=color)
        tx = PAD + IP + 16
        for line in lines:
            draw.text((tx, iy), line, font=fn_reg, fill=T1)
            iy += lh_reg
        iy += 6
    y += qs_h + GAP

    text_section('비즈니스 모델', 'blue', data.get('biz_model',''))
    points_section('투자 포인트', 'green', data.get('points',[]))
    if data.get('attention'):
        text_section('지금 주목받는 이유', 'blue', data.get('attention',''))
    points_section('리스크', 'red', data.get('risks',[]))

    # Peers
    peers = data.get('peers', [])
    hw = (W - PAD*2 - 1) // 2
    peer_lines = []
    for p in peers:
        dl = wrap_text(p.get('desc',''), fn_reg, hw-IP*2, draw)
        ph = get_line_height(fn_md) + 4 + lh_sm + 4 + len(dl)*lh_reg + 20
        peer_lines.append((dl, ph))
    rows = [(peers[i:i+2], peer_lines[i:i+2]) for i in range(0, len(peers), 2)]
    peer_h = 26 + sum(max(ph for _, ph in row[1]) for row in rows)
    card_bg(PAD, y, W-PAD*2, peer_h)
    hdr_block('유사 기업', 'purple')
    iy = y + 26
    for row_peers, row_lines in rows:
        rh = max(ph for _, ph in row_lines)
        draw.line([(PAD, iy), (W-PAD, iy)], fill=INNER, width=1)
        for ci, (p, (dl, ph)) in enumerate(zip(row_peers, row_lines)):
            cx = PAD + ci*(hw+1)
            if ci == 1:
                draw.line([(PAD+hw, iy), (PAD+hw, iy+rh)], fill=INNER, width=1)
            py = iy + 8
            draw.text((cx+IP, py), p.get('name',''), font=fn_md, fill=T1)
            py += get_line_height(fn_md) + 2
            draw.text((cx+IP, py), p.get('country',''), font=fn_sm, fill=T3)
            py += lh_sm + 4
            for line in dl:
                draw.text((cx+IP, py), line, font=fn_reg, fill=T2)
                py += lh_reg
        iy += rh
    y += peer_h + GAP

    text_section('한 줄 요약', 'amber', data.get('summary',''))

    notice = "본 내용은 투자 참고용 분석이며 투자 권유가 아닙니다."
    try:
        nw = draw.textlength(notice, font=fn_sm)
    except:
        nw = 300
    draw.text(((W-nw)//2, y+4), notice, font=fn_sm, fill=T3)
    y += 24

    final = img.crop((0, 0, W, y + PAD))
    buf = io.BytesIO()
    final.save(buf, format="PNG")
    buf.seek(0)
    return buf.getvalue()

def build_text(data):
    nums = ["①","②","③","④","⑤"]
    quick = "\n".join(f"{'💡🟢🔴'[i] if i<3 else '•'} {s}" for i,s in enumerate(data.get('quick_summary',[])))
    points = "\n\n".join(f"{nums[i] if i<len(nums) else i+1} {p.get('title','')}\n{p.get('desc','')}" for i,p in enumerate(data.get('points',[])))
    risks = "\n\n".join(f"{nums[i] if i<len(nums) else i+1} {r.get('title','')}\n{r.get('desc','')}" for i,r in enumerate(data.get('risks',[])))
    peers = "\n".join(f"• {p.get('name','')} ({p.get('country','')}): {p.get('desc','')}" for p in data.get('peers',[]))
    attention = f"\n🔍 지금 주목받는 이유\n{data.get('attention','')}\n" if data.get('attention') else ""
    return (f"{'='*30}\n{data.get('company','')} ({data.get('ticker','')})\n{data.get('tagline','')}\n{'='*30}\n\n"
            f"📋 선결론 3줄 요약\n{quick}\n\n"
            f"📌 비즈니스 모델\n{data.get('biz_model','')}\n\n"
            f"📈 투자 포인트\n{points}\n{attention}\n"
            f"⚠️ 리스크\n{risks}\n\n"
            f"🌏 유사 기업\n{peers}\n\n"
            f"💡 한 줄 요약\n{data.get('summary','')}\n\n"
            f"*본 내용은 투자 참고용이며 투자 권유가 아닙니다.*")

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
        img_bytes = create_image(data)
        await update.message.reply_photo(photo=io.BytesIO(img_bytes))
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

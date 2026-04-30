# 기업 분석 텔레그램 봇

## 파일 구조
```
telegram-bot/
├── bot.py
├── requirements.txt
├── Procfile
└── README.md
```

## 배포 방법

### 1. GitHub에 올리기
1. GitHub에서 새 repository 생성 (이름 예: `stock-bot`)
2. 이 폴더 안의 파일 4개를 모두 업로드

### 2. Railway 배포
1. railway.app 접속 → GitHub으로 로그인
2. "New Project" → "Deploy from GitHub repo" → 방금 만든 repo 선택
3. 좌측 Variables 탭에서 환경변수 2개 추가:
   - `TELEGRAM_BOT_TOKEN` = 텔레그램 봇 토큰
   - `ANTHROPIC_API_KEY` = Claude API 키
4. Deploy 버튼 클릭

## 사용법
텔레그램에서 봇에게 메시지 전송:
- "후성 분석해줘"
- "삼성바이오로직스 투자할 만해?"
- "엔비디아 요즘 왜 올라?"

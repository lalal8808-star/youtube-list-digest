# YouTube 맞춤형 뉴스레터 자동화 봇

지정된 관심 주제(국제정세, AI, 경제)에 대한 YouTube 영상을 매일 아침 검색하여, 사용자에게 요약 이메일을 전송하는 파이썬 스크립트입니다.

## 기능
- 여러 주제에 대해 YouTube 검색 수행
- 각 주제별 조회수, 관련성 높은 최근 영상 10개 추출
- 영상 제목, 링크, 요약(설명)을 포함한 HTML 이메일 전송

## 설정 방법

### 1. 환경 준비 및 의존성 설치
Mac 터미널을 열고 다음 명령어를 실행하여 필요한 패키지를 설치합니다:

```bash
cd /Users/imjonghwa/youtube-list-digest
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 이메일 발송 설정 (수정 필수)
자동으로 생성된 `.env` 파일을 열고(숨김 파일로 되어 있을 수 있습니다) 이메일 관련 정보를 변경하세요.

```env
SENDER_EMAIL=your_email@gmail.com
SENDER_PASSWORD=your_app_password
RECEIVER_EMAIL=receiver_email@gmail.com
GEMINI_API_KEY=your_gemini_api_key
```

* `SENDER_EMAIL`: 본인의 구글 이메일 주소 (예: `your_email@gmail.com`)
* `SENDER_PASSWORD`: 구글 계정 **앱 비밀번호** (구글 2단계 인증 설정 후 생성해야 합니다)
* `RECEIVER_EMAIL`: 메일을 받을 주소
* `GEMINI_API_KEY`: 구글 기반 AI(Gemini) 요약을 위한 API 키

### 3. 관심 주제 변경
`youtube_newsletter.py` 파일의 11번째 줄에 `TOPICS` 리스트가 있습니다. 여기서 관심 주제를 쉽게 추가/삭제/변경할 수 있습니다.

```python
# 기존
TOPICS = ["국제정세", "AI", "경제"]

# 변경 예시
TOPICS = ["국제정세", "인공지능 트렌드", "미국 주식", "개발자 브이로그"]
```

## 매일 아침 7시 자동 실행 설정 방법 (Mac Cron)

Mac의 내장 스케줄러인 `cron`을 사용하여 매일 오전 7시에 자동으로 메일이 오도록 설정할 수 있습니다.

1. 터미널(Terminal) 앱을 엽니다.
2. 아래 명령어를 입력하여 crontab 편집기를 엽니다:
   ```bash
   crontab -e
   ```
3. 에디터가 열리면 단축키 `i`를 눌러 입력 모드로 진입합니다.
4. 아래 내용을 복사하여 맨 아랫줄에 붙여넣습니다:
   ```bash
   0 7 * * * cd /Users/imjonghwa/youtube-list-digest && /Users/imjonghwa/youtube-list-digest/venv/bin/python youtube_newsletter.py >> /Users/imjonghwa/youtube-list-digest/cron.log 2>&1
   ```
5. `esc` 키를 누르고 `:wq`를 입력한 후 엔터를 쳐서 저장하고 빠져나옵니다.

이제 매일 아침 7시에 자동으로 스크립트가 실행되고 이메일이 발송됩니다. 실행 로그는 `cron.log` 파일에서 확인할 수 있습니다.

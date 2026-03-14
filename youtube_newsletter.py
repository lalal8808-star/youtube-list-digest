import os
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

import isodate
import urllib.parse
import time
import re
from urllib.parse import quote_plus
from youtube_transcript_api import YouTubeTranscriptApi
import google.generativeai as genai

from dotenv import load_dotenv
load_dotenv()

import socket
import signal
socket.setdefaulttimeout(10) # 네트워크 요청 타임아웃 (10초)

# 전체 스크립트 최대 실행 시간 50분 (GitHub Actions 6시간 제한 안전막)
MAX_SCRIPT_SECONDS = 50 * 60

class ScriptTimeout(Exception):
    pass

def _timeout_handler(signum, frame):
    raise ScriptTimeout("\u26a0️ 스크립트 실행 시간이 50분을 초과했습니다. 수집된 영상만으로 메일을 발송합니다.")

# ==========================================
# 설정 (Configuration)
# ==========================================

# 관심 주제 (언제든지 수정 가능)
# 관심 주제 (한국어 검색으로 변경)
TOPICS = ["국제정치", "인공지능", "세계 경제"]

# 각 주제당 추천할 영상 개수
VIDEOS_PER_TOPIC = 5

# 필터링 허들 (오래되거나 퀄리티 낮은 채널 제외)
# 필터링 허들 (한국 유튜브 환경에 맞춰 더 완화)
MIN_DURATION_SEC = 240           # 최소 4분 이상
MIN_SUBSCRIBERS = 2000           # 최소 2,000 구독자 (더 많은 채널 포함)
MIN_LIKE_TO_VIEW_RATIO = 0.005   # 최소 좋아요 비율 0.5% (대형 뉴스 채널 포함)

# 자극적인 제목 필터링 (정규식 기반)
CLICKBAIT_KEYWORDS = re.compile(
    r"(충격|경악|무조건 보세요|\?\?\?|!!!|단독|최초|폭로|이럴수가)",
    re.IGNORECASE
)

# 화이트리스트 (특정 주제는 이 채널에서만 검색)
WHITELIST_CHANNELS = {
    "산업안전": ["https://www.youtube.com/@KOSHA_official"],
    "송배전": ["https://www.youtube.com/@KEPCOnewmedia"]
}

# 이메일 설정
# 구글 계정인 경우 '앱 비밀번호'를 생성하여 사용해야 합니다.
EMAIL_ADDRESS = os.environ.get("SENDER_EMAIL", "your_email@gmail.com")
EMAIL_PASSWORD = os.environ.get("SENDER_PASSWORD", "your_app_password")
RECEIVER_EMAIL = os.environ.get("RECEIVER_EMAIL", "receiver_email@gmail.com")

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY.strip())

SENT_HISTORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sent_history.json")

# ==========================================
# 함수 정의
# ==========================================

def load_sent_history():
    """이전에 발송한 영상 ID 목록을 로드합니다."""
    if os.path.exists(SENT_HISTORY_FILE):
        try:
            with open(SENT_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data.get("sent_video_ids", []))
        except (json.JSONDecodeError, IOError):
            return set()
    return set()

def save_sent_history(sent_ids):
    """발송한 영상 ID 목록을 저장합니다."""
    data = {
        "sent_video_ids": list(sent_ids),
        "last_updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    with open(SENT_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def is_within_one_week(date_str):
    """업로드 날짜가 최근 1주일 이내인지 확인합니다."""
    try:
        upload_date = datetime.strptime(date_str, "%Y%m%d")
        one_week_ago = datetime.now() - timedelta(days=7)
        return upload_date >= one_week_ago
    except (ValueError, TypeError):
        return False

def summarize_with_gemini(transcript_text, title, fallback_description, max_retries=3):
    """Gemini API를 사용하여 자막 내용을 요약하고 신뢰도 점수를 평가합니다."""
    fallback_description = fallback_description or ""
    
    for attempt in range(max_retries):
        try:
            model = genai.GenerativeModel("gemini-2.5-flash")
            if transcript_text:
                prompt = (
                    f"다음은 유튜브 영상의 자막입니다. 이 영상이 객관적인 근거를 제시하고 있는지, 자극적인 어조로 선동하고 있는지 분석하고, 정보의 깊이와 전문성을 1~10점으로 평가해 주세요.\n"
                    f"반드시 첫 줄에 '점수: X' 형식으로 점수만 명시해 주세요.\n"
                    f"그 다음 줄부터는 영상의 핵심 내용을 3~4줄 이내로 명확하고 알기 쉽게 요약해 주세요.\n\n"
                    f"제목: {title}\n"
                    f"자막:\n{transcript_text[:6000]}"
                )
            else:
                prompt = (
                    f"다음은 유튜브 영상의 제목과 설명입니다. 이 영상은 자막을 파악할 수 없었습니다. 이 정보가 객관적인 근거가 있는지, 자극적인지 유추하여 정보의 깊이와 기대 전문성을 1~10점으로 평가해 주세요.\n"
                    f"반드시 첫 줄에 '점수: X' 형식으로 점수만 명시해 주세요.\n"
                    f"그 다음 줄부터는 영상을 보지 않은 사람도 이해할 수 있도록 3~4줄 이내로 핵심 내용을 유추 및 번역하여 요약해 주세요.\n\n"
                    f"제목: {title}\n"
                    f"설명:\n{fallback_description[:2000]}"
                )
                
            response = model.generate_content(prompt)
            text = response.text.strip()
            
            # 첫 줄에서 점수 추출
            lines = text.split('\n')
            score_line = lines[0]
            score = 0
            
            match = re.search(r"점수:\s*(\d+(\.\d+)?)", score_line)
            if match:
                score = float(match.group(1))
            
            summary_text = '\n'.join(lines[1:]).strip().replace('\n', '<br>')
            
            if not transcript_text:
                return f"<b>※(영상 자막 없음) 제목/설명 기반 AI 요약:</b><br>{summary_text}"
            return f"{summary_text}"
            
        except Exception as e:
            error_msg = str(e)
            if "429" in error_msg and attempt < max_retries - 1:
                # 429 에러 났을 시 5초 대기 후 재시도
                print(f"  - AI API 한도 초과(429). 5초 대기 후 재시도 합니다... ({attempt+1}/{max_retries})")
                time.sleep(5)
            else:
                print(f"  - AI 요약/평가 실패: {e}")
                return None
    return None

def yt_api(endpoint, params, timeout=15):
    """YouTube Data API v3 직접 호출 (requests 기반, 타임아웃 보장)"""
    import requests
    API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
    params["key"] = API_KEY
    url = f"https://www.googleapis.com/youtube/v3/{endpoint}"
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    return resp.json()

def search_youtube(topic, max_results=10, sent_ids=None):
    """주어진 주제로 YouTube Data API v3 (requests 기반)를 검색합니다."""
    print(f"\'{topic}\' 주제로 YouTube 검색 중...")

    API_KEY = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not API_KEY:
        print("[오류] YOUTUBE_API_KEY가 등록되지 않았습니다!")
        return []

    if sent_ids is None:
        sent_ids = set()

    videos = []
    is_whitelist_topic = topic in WHITELIST_CHANNELS
    whitelist_urls = WHITELIST_CHANNELS.get(topic, [])
    # 필터링을 고려하여 충분히 검색 (확률을 높이기 위해 검색량 증가)
    search_count = max_results * (5 if is_whitelist_topic else 20)  # 5 * 20 = 100개 검색

    try:
        entries_to_process = []
        one_week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat() + "Z"

        if is_whitelist_topic:
            print(f"  - 화이트리스트 적용됨: {whitelist_urls}")
            for channel_url in whitelist_urls:
                handle = channel_url.rstrip("/").split("@")[-1]
                try:
                    r = yt_api("search", {"part": "id", "q": handle, "type": "channel", "maxResults": 1})
                    if not r.get("items"):
                        continue
                    channel_id = r["items"][0]["id"]["channelId"]
                    r2 = yt_api("search", {
                        "part": "id,snippet", "channelId": channel_id,
                        "type": "video", "order": "date",
                        "publishedAfter": one_week_ago,
                        "videoDuration": "medium",  # 4분~20분 (Shorts 제외)
                        "maxResults": min(50, search_count)
                    })
                    entries_to_process.extend(r2.get("items", []))
                except Exception as e:
                    print(f"  - [오류] {channel_url} 채널 검색 실패: {e}")
        else:
            next_page_token = None
            total_fetched = 0
            while total_fetched < search_count:
                params = {
                    "part": "id,snippet", "q": topic, "type": "video",
                    "order": "relevance", "publishedAfter": one_week_ago,
                    "videoDuration": "medium",  # 4분~20분 (Shorts 제외)
                    "relevanceLanguage": "ko",  # 한국어 영상 우선
                    "regionCode": "KR",         # 한국 지역 우선
                    "maxResults": min(50, search_count - total_fetched)
                }
                if next_page_token:
                    params["pageToken"] = next_page_token
                try:
                    r = yt_api("search", params)
                except Exception as e:
                    print(f"  - [검색 오류]: {e}")
                    break
                items = r.get("items", [])
                if not items:
                    break
                entries_to_process.extend(items)
                total_fetched += len(items)
                next_page_token = r.get("nextPageToken")
                if not next_page_token:
                    break

        # ── 1단계: 후보 목록 구성 ──
        import html as _html
        candidate_entries = []
        for entry in entries_to_process:
            if "id" not in entry or "videoId" not in entry["id"]:
                continue
            video_id = entry["id"]["videoId"]
            title = _html.unescape(entry["snippet"]["title"])
            if CLICKBAIT_KEYWORDS.search(title):
                continue
            if video_id in sent_ids:
                continue
            candidate_entries.append((
                video_id, title,
                f"https://www.youtube.com/watch?v={video_id}",
                entry["snippet"]["channelTitle"],
                entry["snippet"].get("description", ""),
                entry["snippet"]["channelId"]
            ))

        # ── 2단계: 배치로 영상 상세 정보 조회 ──
        video_details_map = {}
        for i in range(0, len(candidate_entries), 50):
            batch_ids = ",".join(e[0] for e in candidate_entries[i:i+50])
            try:
                r = yt_api("videos", {"part": "contentDetails,statistics,snippet", "id": batch_ids})
                for item in r.get("items", []):
                    video_details_map[item["id"]] = item
            except Exception as e:
                print(f"  - [배치 상세정보 오류]: {e}")

        # ── 3단계: 배치로 채널 구독자 수 조회 ──
        channel_ids = list({e[5] for e in candidate_entries})
        channel_stats_map = {}
        for i in range(0, len(channel_ids), 50):
            batch_cids = ",".join(channel_ids[i:i+50])
            try:
                r = yt_api("channels", {"part": "statistics", "id": batch_cids})
                for item in r.get("items", []):
                    channel_stats_map[item["id"]] = int(item["statistics"].get("subscriberCount", 0))
            except Exception as e:
                print(f"  - [채널정보 배치 오류]: {e}")

        # ── 4단계: 필터링 → 자막 → Gemini ──
        for (video_id, title, url, channel, description, channel_id) in candidate_entries:
            if len(videos) >= max_results:
                break

            v_info = video_details_map.get(video_id)
            if not v_info:
                continue

            try:
                duration_sec = int(isodate.parse_duration(v_info["contentDetails"]["duration"]).total_seconds())
            except:
                continue
            if duration_sec < MIN_DURATION_SEC:
                print(f"  - [스킵: {duration_sec}초] \'{title}\'")
                continue

            try:
                published_at = v_info["snippet"]["publishedAt"]
                raw_date = published_at[:10].replace("-", "")
                upload_date_str = published_at[:10]
            except:
                raw_date = None
                upload_date_str = "날짜 알 수 없음"
            if not is_within_one_week(raw_date):
                print(f"  - [스킵: 1주일 이전] \'{title}\' ({upload_date_str})")
                continue

            subs = channel_stats_map.get(channel_id, 0)
            if subs < MIN_SUBSCRIBERS:
                print(f"  - [스킵: 구독자 {subs}명] \'{title}\'")
                continue

            stats = v_info.get("statistics", {})
            view_count = int(stats.get("viewCount", 0))
            like_count = int(stats.get("likeCount", 0))
            if view_count > 0 and like_count > 0:
                like_ratio = like_count / view_count
                if like_ratio < MIN_LIKE_TO_VIEW_RATIO:
                    print(f"  - [스킵: 좋아요 {like_ratio*100:.1f}%] \'{title}\'")
                    continue

            m, s = divmod(duration_sec, 60)
            h, m = divmod(m, 60)
            duration_formatted = f"{h}:{m:02d}:{s:02d}" if h > 0 else f"{m}:{s:02d}"

            transcript_text = ""
            try:
                transcript_list = YouTubeTranscriptApi.list_transcripts(video_id)
                try:
                    transcript = transcript_list.find_transcript(["ko", "en"])
                except:
                    transcript = next(iter(transcript_list))
                transcript_text = " ".join([t["text"] for t in transcript.fetch()])
            except:
                pass

            if not transcript_text and len(description.strip()) < 10:
                print(f"  - [스킵: 자막/설명 없음] \'{title}\'")
                continue

            print(f"  - [수집 중: {len(videos)+1}/{max_results}] [{channel}] \'{title}\'")

            summary_data = summarize_with_gemini(transcript_text, title, description)
            time.sleep(0.5)

            if not summary_data:
                print(f"  - [스킵: LLM 미달] \'{title}\'")
                continue

            videos.append({
                "title": title,
                "channel": channel,
                "date": upload_date_str,
                "duration": duration_formatted,
                "link": url,
                "summary": summary_data,
                "video_id": video_id
            })

    except Exception as e:
        print(f"\'{topic}\' 검색 중 오류 발생: {e}")

    return videos


def create_email_html(all_results):
    """검색 결과를 바탕으로 이메일 HTML 본문을 생성합니다."""
    html = """
    <html>
      <head>
        <style>
          body { font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif; line-height: 1.6; color: #333; }
          .container { max-width: 800px; margin: 0 auto; padding: 20px; }
          .header { background-color: #fce4ec; padding: 20px; text-align: center; border-radius: 8px 8px 0 0; }
          .header h1 { margin: 0; color: #d81b60; font-size: 24px; }
          .topic-section { margin-bottom: 30px; background-color: #fff; border: 1px solid #eee; border-radius: 8px; padding: 20px; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }
          .topic-title { color: #1976d2; border-bottom: 2px solid #1976d2; padding-bottom: 10px; margin-top: 0; }
          .video-item { margin-bottom: 20px; padding-bottom: 20px; border-bottom: 1px dashed #eee; }
          .video-item:last-child { border-bottom: none; margin-bottom: 0; padding-bottom: 0; }
          .video-title { font-size: 18px; font-weight: bold; margin: 0 0 10px 0; }
          .video-title a { color: #d32f2f; text-decoration: none; }
          .video-title a:hover { text-decoration: underline; }
          .video-summary { color: #555; font-size: 14px; background-color: #f9f9f9; padding: 10px; border-left: 4px solid #ddd; margin: 0; }
          .url-box { font-size: 12px; color: #555; background-color: #f5f5f5; border: 1px solid #e0e0e0; padding: 4px 8px; border-radius: 4px; display: block; margin: 5px 0 10px 0; word-break: break-all; width: 100%; box-sizing: border-box; cursor: text; user-select: all; -webkit-user-select: all; }
          .criteria-section { background-color: #f8f9fa; border: 1px solid #e9ecef; border-radius: 8px; padding: 15px; margin-bottom: 30px; }
          .criteria-title { font-size: 16px; font-weight: bold; color: #495057; margin-top: 0; margin-bottom: 10px; display: flex; align-items: center; }
          .criteria-list { margin: 0; padding-left: 20px; font-size: 13px; color: #6c757d; }
          .criteria-item { margin-bottom: 4px; }
          .footer { text-align: center; margin-top: 30px; padding: 20px; font-size: 12px; color: #888; border-top: 1px solid #eee; }
        </style>
      </head>
      <body>
        <div class="container">
          <div class="header">
            <h1>오늘의 맞춤형 YouTube 추천 (국제정치, 인공지능, 경제)</h1>
          </div>
          <div class="criteria-section">
            <h3 class="criteria-title">🔍 영상 선정 기준</h3>
            <ul class="criteria-list">
              <li class="criteria-item"><b>최근성:</b> 업로드 중 1주일 이내의 최신 영상만 선정</li>
              <li class="criteria-item"><b>분량 필터:</b> 심도 있는 분석을 위해 4분 이상의 영상 우선 (Shorts 제외)</li>
              <li class="criteria-item"><b>채널 신뢰도:</b> 구독자 2,000명 이상의 검증된 채널</li>
              <li class="criteria-item"><b>반응도:</b> 조회수 대비 좋아요 비율 0.5% 이상의 고품질 콘텐츠</li>
              <li class="criteria-item"><b>자막/대본/설명:</b> 정확한 요약을 위해 스크립트(자동생성 포함)나 설명글이 존재하는 영상만 대상</li>
              <li class="criteria-item"><b>클릭베이트 제외:</b> 자극적인 제목(충격, 경악 등) 정규식 필터링</li>
            </ul>
          </div>
    """
    
    total_videos = sum(len(videos) for videos in all_results.values())
    
    if total_videos == 0:
        html += """
        <div class="topic-section">
            <h2 class="topic-title">📌 안내</h2>
            <p>오늘은 추천 기준(최근성, 분량, 평점 피드백, 자극성 필터 등)을 모두 통과한 새로운 영상이 없었습니다. <br>
            (이미 발송된 영상은 중복을 피하기 위해 자동 제외되었습니다.)</p>
            <p>내일 다시 시도해주세요.</p>
        </div>
        """
    else:
        for topic, videos in all_results.items():
            if not videos:
                continue
            
            html += f'<div class="topic-section"><h2 class="topic-title">📌 {topic}</h2>'
            
            for idx, video in enumerate(videos, 1):
                html += f"""
                <div class="video-item">
                  <h3 class="video-title">
                    {idx}. <a href="{video['link']}">{video['title']}</a> 
                    <span style="font-size: 14px; color: #666; font-weight: normal;">(⏱️ {video.get('duration', '알 수 없음')} | {video.get('date', '')} | {video.get('channel', '채널명 없음')})</span>
                  </h3>
                  <div class="url-box">📋 복사용 주소 (드래그 후 복사): {video['link'].replace('://', '&#58;&#47;&#47;')}</div>
                  <p class="video-summary">{video['summary']}</p>
                </div>
                """
                
            html += '</div>'
        
    html += """
          <div class="footer">
            <p>본 메일은 자동화 스크립트에 의해 발송되었습니다.</p>
          </div>
        </div>
      </body>
    </html>
    """
    
    return html

def send_email(html_content):
    """생성된 HTML 내용을 이메일로 전송합니다."""
    if EMAIL_ADDRESS == "your_email@gmail.com":
        print("⚠️ 이메일 전송 실패: 이메일 설정(EMAIL_ADDRESS, EMAIL_PASSWORD)을 스크립트에서 먼저 확인해주세요.")
        return

    msg = MIMEMultipart('alternative')
    msg['Subject'] = "[일일 뉴스레터] 오늘의 맞춤형 YouTube 추천 영상 큐레이션"
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = RECEIVER_EMAIL

    part = MIMEText(html_content, 'html')
    msg.attach(part)

    try:
        # 네이버 등 다른 메일을 사용하려면 smtp 주소와 포트를 변경해야 합니다. (예: smtp.naver.com, 465)
        # 구글 메일(Gmail) 기준 설정
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
        server.sendmail(EMAIL_ADDRESS, RECEIVER_EMAIL, msg.as_string())
        server.quit()
        print("✅ 성공적으로 이메일을 발송했습니다!")
    except Exception as e:
        print(f"❌ 이메일 발송 중 오류 발생: {e}")

# ==========================================
# 메인 실행부
# ==========================================

def main():
    print("오늘의 YouTube 영상 수집 시작...")
    
    # 발송 이력 로드
    sent_ids = load_sent_history()
    print(f"📋 이전 발송 이력: {len(sent_ids)}개 영상")
    
    all_results = {}
    new_video_ids = []  # 이번에 새로 포함된 영상 ID
    
    # 50분 타임아웃 설정 (GitHub Actions 6h 제한 안전막)
    signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(MAX_SCRIPT_SECONDS)
    
    try:
        for topic in TOPICS:
            videos = search_youtube(topic, max_results=VIDEOS_PER_TOPIC, sent_ids=sent_ids)
            all_results[topic] = videos
            # 새로 보내는 영상 ID 수집
            for v in videos:
                vid_id = v.get('video_id')
                if vid_id:
                    new_video_ids.append(vid_id)
    except ScriptTimeout as e:
        print(str(e))
    finally:
        signal.alarm(0)  # 타임아웃 해제
        
    print("이메일 본문 생성 중...")
    html_content = create_email_html(all_results)
    
    # 로컬에서 결과 확인을 위해 HTML 파일로 저장 (테스트용)
    with open("test_output.html", "w", encoding="utf-8") as f:
        f.write(html_content)
    print("✅ 테스트용 HTML 파일(test_output.html)이 로컬에 저장되었습니다. 브라우저에서 열어보실 수 있습니다.")
    
    print("이메일 전송 중...")
    send_email(html_content)
    
    # 발송 이력 업데이트
    sent_ids.update(new_video_ids)
    save_sent_history(sent_ids)
    print(f"📋 발송 이력 업데이트 완료: 총 {len(sent_ids)}개 영상")
    
    print("모든 작업이 완료되었습니다.")

if __name__ == "__main__":
    main()

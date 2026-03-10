import os
import re

with open("youtube_newsletter.py", "r", encoding="utf-8") as f:
    content = f.read()

# Add imports
imports_to_add = """
from googleapiclient.discovery import build
import isodate
import urllib.parse
"""
content = re.sub(r'import yt_dlp\n', imports_to_add, content)

# Define the new search_youtube
new_search_youtube = '''def search_youtube(topic, max_results=10, sent_ids=None):
    """주어진 주제로 YouTube Data API v3를 검색하고 결과를 반환합니다."""
    print(f"'{topic}' 주제로 YouTube 검색 중...")
    
    API_KEY = os.environ.get("YOUTUBE_API_KEY")
    if not API_KEY:
        print("[오류] YOUTUBE_API_KEY가 등록되지 않았습니다! .env 파일이나 설정에 추가해주세요.")
        return []
        
    youtube = build('youtube', 'v3', developerKey=API_KEY, cache_discovery=False)
    
    if sent_ids is None:
        sent_ids = set()
    
    videos = []
    is_whitelist_topic = topic in WHITELIST_CHANNELS
    whitelist_urls = WHITELIST_CHANNELS.get(topic, [])
    
    search_count = max_results * (5 if is_whitelist_topic else 30)
    
    try:
        entries_to_process = []
        
        # 1주일 전 시간 계산 (RFC 3339 형식)
        one_week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat() + "Z"
        
        if is_whitelist_topic:
            print(f"  - 화이트리스트 적용됨: {whitelist_urls}")
            for channel_url in whitelist_urls:
                handle = channel_url.rstrip('/').split('@')[-1]
                try:
                    # 핸들로 채널 검색
                    channel_request = youtube.search().list(part="id", q=handle, type="channel", maxResults=1)
                    channel_response = channel_request.execute()
                    
                    if not channel_response['items']:
                        print(f"  - [오류] {handle} 채널을 찾을 수 없습니다.")
                        continue
                        
                    channel_id = channel_response['items'][0]['id']['channelId']
                    
                    # 채널의 최신 영상 검색
                    search_request = youtube.search().list(
                        part="id,snippet",
                        channelId=channel_id,
                        type="video",
                        order="date",
                        maxResults=min(50, search_count)
                    )
                    search_response = search_request.execute()
                    entries_to_process.extend(search_response.get('items', []))
                    
                except Exception as e:
                    print(f"  - [오류] {channel_url} 채널 검색 실패: {e}")
        else:
            # 일반 주제 검색
            search_request = youtube.search().list(
                part="id,snippet",
                q=topic,
                type="video",
                order="relevance",
                publishedAfter=one_week_ago,
                maxResults=min(50, search_count)
            )
            search_response = search_request.execute()
            entries_to_process.extend(search_response.get('items', []))

        # 순회하며 상세 정보 가져오기
        for idx, entry in enumerate(entries_to_process):
            if 'id' not in entry or 'videoId' not in entry['id']:
                continue
                
            video_id = entry['id']['videoId']
            title = entry['snippet']['title']
            
            # HTML 엔티티 제거 (ex: &quot; -> ")
            import html
            title = html.unescape(title)
            
            url = f"https://www.youtube.com/watch?v={video_id}"
            
            if CLICKBAIT_KEYWORDS.search(title):
                print(f"  - [스킵: 자극적 제목] '{title}'")
                continue
                
            if video_id in sent_ids:
                print(f"  - [스킵: 이미 발송됨] '{title}'")
                continue
                
            channel = entry['snippet']['channelTitle']
            description = entry['snippet']['description']
            channel_id = entry['snippet']['channelId']
            
            # 유튜브 자막 추출
            transcript_text = ""
            try:
                api = YouTubeTranscriptApi()
                transcript_list = api.list(video_id)
                try:
                    transcript = transcript_list.find_transcript(['ko', 'en'])
                except:
                    transcript = next(iter(transcript_list))
                transcript_text = " ".join([t.text if hasattr(t, 'text') else t['text'] for t in transcript.fetch()])
            except Exception as e:
                pass
                
            if not transcript_text:
                print(f"  - [스킵: 자막 및 대본 없음] '{title}'")
                continue
                
            print(f"  - [수집 중: {len(videos)+1}/{max_results}] [{channel}] '{title}' 요약 및 상세 정보 조회 중...")
            
            upload_date_str = "날짜 알 수 없음"
            duration_formatted = "알 수 없음"
            
            # Video Details & Channel Info (구독자수 확보)
            try:
                video_request = youtube.videos().list(part="contentDetails,statistics,snippet", id=video_id)
                video_response = video_request.execute()
                
                if not video_response['items']:
                    continue
                    
                v_info = video_response['items'][0]
                
                # 영상 길이
                duration_iso = v_info['contentDetails']['duration']
                duration_sec = int(isodate.parse_duration(duration_iso).total_seconds())
                m, s = divmod(duration_sec, 60)
                h, m = divmod(m, 60)
                if h > 0:
                    duration_formatted = f"{h}:{m:02d}:{s:02d}"
                else:
                    duration_formatted = f"{m}:{s:02d}"
                    
                if duration_sec < MIN_DURATION_SEC:
                    print(f"  - [스킵: 길이 짧음 ({duration_sec}초)] '{title}'")
                    continue
                    
                # 날짜 처리
                published_at = v_info['snippet']['publishedAt']
                upload_date_str = published_at[:10]  # YYYY-MM-DD
                raw_date = published_at[:10].replace("-", "") # YYYYMMDD
                
                # 통계(조회수, 좋아요)
                stats = v_info['statistics']
                view_count = int(stats.get('viewCount', 0))
                like_count = int(stats.get('likeCount', 0))
                
                # 채널 통계(구독자)
                channel_request = youtube.channels().list(part="statistics", id=channel_id)
                channel_response = channel_request.execute()
                channel_follower_count = 0
                if channel_response['items']:
                    channel_follower_count = int(channel_response['items'][0]['statistics'].get('subscriberCount', 0))
                
                if channel_follower_count < MIN_SUBSCRIBERS:
                    print(f"  - [스킵: 구독자 수 미달 ({channel_follower_count}명)] '{title}'")
                    continue
                    
                if view_count > 0 and like_count > 0:
                    like_ratio = like_count / view_count
                    if like_ratio < MIN_LIKE_TO_VIEW_RATIO:
                        print(f"  - [스킵: 좋아요 비율 미달 ({like_ratio*100:.1f}%)] '{title}'")
                        continue
                        
            except Exception as e:
                print(f"  - [상세정보 조회 오류] '{title}': {e}")
                pass
            
            # AI 요약 및 신뢰도 평가
            summary_data = summarize_with_gemini(transcript_text, title, description)
            time.sleep(0.5)
            
            if not summary_data:
                print(f"  - [스킵: LLM 평가 미달] '{title}'")
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
            if len(videos) >= max_results:
                break
                
    except Exception as e:
        print(f"'{topic}' 검색 중 오류 발생: {e}")
        
    return videos'''

content = re.sub(
    r'def search_youtube\(topic, max_results=10, sent_ids=None\):.*?(?=\ndef )',
    new_search_youtube + '\n\n',
    content,
    flags=re.DOTALL
)

with open("youtube_newsletter.py", "w", encoding="utf-8") as f:
    f.write(content)

print("youtube_newsletter.py updated successfully.")

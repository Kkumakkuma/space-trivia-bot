"""우주봇 YouTube OAuth 토큰 재발급 + 채널 검증.

사용법:
  cd 우주쇼츠봇
  python auth.py

작동:
1. credentials/youtube_client_secret.json 읽기 (= 동물봇과 동일 OAuth client = animal-trivia-bot project)
2. 브라우저 자동 열림 → 구글 계정 로그인 → ⚠️ "Space Trivia 60s" 채널 선택!
3. 토큰을 credentials/youtube_token.json에 저장
4. 토큰으로 실제 채널 이름 조회 → 콘솔에 출력 (잘못된 채널이면 다시 돌려야 함)

⚠️ 중요: OAuth 화면에서 "Animal Trivia 60s" / "Lofi & Lit" 채널 선택 X.
        반드시 "Space Trivia 60s" 채널 선택!

배경: 기존 우주봇 client(DRS 시절 northern-cooler-494617-p4 project)는 unverified라
      sensitive scope 토큰이 7일 만료. 동물봇 verified-ish project로 통일하여
      만료 정책 회피 (2026-05-08 fix).
"""
import os
import pickle
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
CREDS = os.path.join(ROOT, "credentials")
CLIENT = os.path.join(CREDS, "youtube_client_secret.json")
TOKEN = os.path.join(CREDS, "youtube_token.json")

if not os.path.exists(CLIENT):
    print(f"[ERROR] {CLIENT} 없음")
    sys.exit(1)

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

print("=" * 60)
print("⚠️  브라우저가 열리면 반드시 'Space Trivia 60s' 채널 선택!")
print("⚠️  Animal Trivia 60s / Lofi & Lit 등 다른 채널 고르면 안 됨.")
print("=" * 60)

flow = InstalledAppFlow.from_client_secrets_file(CLIENT, SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

with open(TOKEN, "wb") as f:
    pickle.dump(creds, f)

print(f"\n[OK] 토큰 저장됨: {TOKEN}")
print(f"[OK] refresh_token 포함: {bool(creds.refresh_token)}")

print("\n[검증] 발급된 토큰의 채널 확인 중...")
yt = build("youtube", "v3", credentials=creds)
resp = yt.channels().list(part="snippet", mine=True).execute()
items = resp.get("items", [])
if not items:
    print("[ERROR] 채널 정보 조회 실패")
    sys.exit(1)

ch = items[0]
title = ch["snippet"]["title"]
ch_id = ch["id"]
print(f"\n채널 이름: {title}")
print(f"채널 ID:  {ch_id}")

low = title.lower()
if "space" in low or "trivia 60s" in low or "spacetrivia" in low.replace(" ", ""):
    if "animal" in low or "lofi" in low:
        print(f"\n❌ 채널 이름 모호 ({title}) — animal/lofi 단어 포함")
        sys.exit(2)
    print("\n✅ Space Trivia 60s 채널로 정상 발급됨!")
else:
    print(f"\n❌ 잘못된 채널 ({title})! Space Trivia 60s 아님!")
    print("❌ 이 토큰 쓰지 말고 auth.py 다시 돌려서 Space Trivia 60s 채널 선택!")
    sys.exit(2)

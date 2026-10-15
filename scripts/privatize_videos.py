# 조회수 낮은 영상을 '비공개'로 돌린다(1회용, 2026-10-15 쿠마님 11591·11612 "조회수 500 이하 영상 지워").
# 영구 삭제는 되돌릴 수 없어 하지 않는다 — 비공개는 스튜디오에서 언제든 되돌릴 수 있다(완전 삭제는 쿠마님이 스튜디오에서).
#
# 사용: python scripts/privatize_videos.py archive/privatize_ids_20261015.txt
#   - 목록(영상 id 한 줄에 하나, # 줄은 주석)을 50개씩 읽어 지금 상태를 확인하고, 공개·일부공개만 비공개로 바꾼다.
#   - status 를 통째로 다시 보내므로 지금 값(AI 생성 표시 containsSyntheticMedia·아동용 자체 신고·퍼가기·라이선스·통계 공개)을
#     그대로 실어 보낸다(빼면 기본값으로 바뀔 수 있다).
#   - 할당량: 조회 1유닛/50편, 변경 50유닛/편. 할당량 초과(quotaExceeded)가 나면 멈추고, 남은 건 다시 돌리면 이어서 한다
#     (이미 비공개면 건너뛰므로 두 번 돌려도 안전).
import os
import pickle
import sys

from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

WRITABLE = ('embeddable', 'license', 'publicStatsViewable', 'selfDeclaredMadeForKids', 'containsSyntheticMedia')


def load_ids(path):
    with open(path, encoding='utf-8') as fh:
        return [ln.strip() for ln in fh if ln.strip() and not ln.lstrip().startswith('#')]


def main():
    ids = load_ids(sys.argv[1])
    with open(os.path.join('credentials', 'youtube_token.json'), 'rb') as fh:
        creds = pickle.load(fh)
    if not creds.valid:
        creds.refresh(Request())
    yt = build('youtube', 'v3', credentials=creds, cache_discovery=False)
    changed = skipped = missing = failed = 0
    stop = False
    for k in range(0, len(ids), 50):
        batch = ids[k:k + 50]
        items = yt.videos().list(part='status', id=','.join(batch), maxResults=50).execute().get('items', [])
        found = {it['id'] for it in items}
        missing += len([v for v in batch if v not in found])
        for it in items:
            st = it['status']
            if st.get('privacyStatus') == 'private':
                skipped += 1
                continue
            status = {'privacyStatus': 'private'}
            for key in WRITABLE:
                if key in st:
                    status[key] = st[key]
            try:
                yt.videos().update(part='status', body={'id': it['id'], 'status': status}).execute()
                changed += 1
            except HttpError as e:
                text = str(e)
                if 'quotaExceeded' in text or 'dailyLimitExceeded' in text:
                    print('할당량 초과 — 여기서 멈춤(다시 돌리면 이어서 한다)')
                    stop = True
                    break
                failed += 1
                print('실패', it['id'], text[:200])
        if stop:
            break
    print(f'비공개로 바꿈 {changed} · 이미 비공개 {skipped} · 못 찾음 {missing} · 실패 {failed} · 목록 {len(ids)}')
    # 목록에 있는데 못 찾은 영상(이미 지움·id 오류)도 실패로 본다 — 완료로 착각하지 않게
    if failed or stop or missing:
        sys.exit(1)


if __name__ == '__main__':
    main()

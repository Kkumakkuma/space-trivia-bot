# 대본 대기열 (Claude 작성 → 봇이 그대로 발행)

1. 새 대본은 `pending/NNN_slug.json` 으로 넣는다. 파일명 슬러그와 JSON 의 `animal_key` 가 같아야 하고, `author` 는 반드시 `"claude"` 다.
2. 필수 키: `animal_key/display/single/category/angle/format/hook/intro/facts(4개)/outro/title_en/title_ko/description_en/description_ko/comment/tags/pexels_keywords(5개 이상)/fact_keywords(4개)`.
3. 봇은 파일명 오름차순으로 최대 3개까지 시도한다. 스키마 검증에 걸리면 `failed/` 로 옮기고 같은 이름의 `.reason.txt` 에 사유를 남긴다.
4. Pexels 소재를 못 구하면(비전 검증 부족) 그 파일은 `pending/` 에 그대로 두고 다음 파일로 넘어간다 — 손대지 말고 다음 회차를 기다리면 된다.
5. 실제 업로드가 videoId 를 받은 뒤에만 `done/` 으로 옮겨지고 `published_at`·`video_id`·`video_url` 이 덧붙는다. `--dry-run`/`--no-upload` 는 대기열을 소비하지 않는다.

## OpenAI 무호출 모드 (NO_OPENAI=1, 2026-09-03부터 기본)
6. `clips`: facts 순서로 4슬롯, 슬롯당 Pexels 동영상 id 1~2개(`[[id, id], [id, id], [id, id], [id, id]]`). 포스터를 사람이 보고 고른다(=vision 대체). 봇은 이 id 만 내려받고 검색·검증을 하지 않는다. 첫 슬롯 첫 클립의 1초 프레임이 썸네일이 된다.
7. `localizations`: `{ko, ja, es, pt, de, fr, hi: {title(≤95자), description}}` 7개 전부 필수.
8. `captions`(선택): `{lang: [6줄]}` — intro, facts 4개, outro 순서의 번역. 있는 언어만 CC 등록(en 은 항상). 줄 수가 다르면 그 언어만 건너뛴다.
9. NO_OPENAI 인데 clips/localizations 가 없으면 스키마 오류로 `failed/` 로 간다.

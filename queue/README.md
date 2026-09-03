# 대본 대기열 (Claude 작성 → 봇이 그대로 발행)

1. 새 대본은 `pending/NNN_slug.json` 으로 넣는다. 파일명 슬러그와 JSON 의 `animal_key` 가 같아야 하고, `author` 는 반드시 `"claude"` 다.
2. 필수 키: `animal_key/display/single/category/angle/format/hook/intro/facts(4개)/outro/title_en/title_ko/description_en/description_ko/comment/tags/pexels_keywords(5개 이상)/fact_keywords(4개)`.
3. 봇은 파일명 오름차순으로 최대 3개까지 시도한다. 스키마 검증에 걸리면 `failed/` 로 옮기고 같은 이름의 `.reason.txt` 에 사유를 남긴다.
4. Pexels 소재를 못 구하면(비전 검증 부족) 그 파일은 `pending/` 에 그대로 두고 다음 파일로 넘어간다 — 손대지 말고 다음 회차를 기다리면 된다.
5. 실제 업로드가 videoId 를 받은 뒤에만 `done/` 으로 옮겨지고 `published_at`·`video_id`·`video_url` 이 덧붙는다. `--dry-run`/`--no-upload` 는 대기열을 소비하지 않는다.

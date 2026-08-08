#!/usr/bin/env bash
# Render 정적 사이트로 올릴 public/ 을 만든다.
# 셸은 bash 다 — Render 의 리눅스 컨테이너에 zsh 는 없다.
#
# 배포되는 것은 딱 두 파일이다. 그 외에는 아무것도 복사하지 않는다 —
# 화이트리스트 방식이라, 프로젝트에 새 파일이 생겨도 저절로 배포되지 않는다.
#   index.html      = dashboard.html
#   forecast.json   = election2026/data/forecast.json
#
# 배포 금지 대상(참고): .env(FEC 키), election2026/data/raw/(89MB 원본 캐시),
# prediction_log.jsonl, *.xlsx(여론조사 원본), data/manual/, data/baselines/
#
# 사용법:  ./build_public.sh   →  public/ 생성 후 검사 통과 시 exit 0

set -eu
cd "$(dirname "$0")"

SRC_JSON="election2026/data/forecast.json"
[ -f "$SRC_JSON" ] || { echo "FAIL: $SRC_JSON 이 없다. run 을 먼저 돌릴 것"; exit 1; }

rm -rf public
mkdir -p public
cp dashboard.html public/index.html
cp "$SRC_JSON" public/forecast.json

# --- 유출 검사 -------------------------------------------------------------
fail=0

# 1) 화이트리스트 밖의 파일이 있으면 실패
extra=$(find public -type f ! -name index.html ! -name forecast.json)
if [ -n "$extra" ]; then
  echo "FAIL: 예상 밖 파일이 배포 폴더에 있다:"; echo "$extra"; fail=1
fi

# 2) 키·토큰·로컬 경로 문자열
if grep -rlEi 'FEC_API_KEY|api[_-]?key|secret|/Users/|BEGIN [A-Z ]*PRIVATE KEY' public >/dev/null 2>&1; then
  echo "FAIL: 배포 파일에 키/경로로 보이는 문자열이 있다:"
  grep -rniEo 'FEC_API_KEY|api[_-]?key|secret|/Users/[^"]*' public | head
  fail=1
fi

# 3) .env 값이 통째로 새어 들어간 경우 (키 문자열 자체를 대조)
if [ -f .env ]; then
  while IFS='=' read -r k v; do
    case "$k" in ''|\#*) continue;; esac
    [ ${#v} -ge 8 ] || continue
    if grep -rqF "$v" public 2>/dev/null; then
      echo "FAIL: .env 의 $k 값이 배포 파일에 있다"; fail=1
    fi
  done < .env
fi

[ $fail -eq 0 ] || exit 1

echo "OK — public/ 준비 완료"
du -h public/* | sed 's/^/  /'

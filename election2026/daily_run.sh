#!/bin/zsh
# 매일 한 번 forecast.json을 갱신한다. launchd가 새벽 5시에 호출한다.
#   등록: launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.scbk.election2026.daily.plist
#   해제: launchctl bootout gui/$UID/com.scbk.election2026.daily
#   즉시 1회: launchctl kickstart -k gui/$UID/com.scbk.election2026.daily
#
# --skip gdelt,reddit,youtube 는 필수다. 셋 다 가중치 0이라 결과는 같은데,
# 빼면 GDELT가 차단당한 채 재시도를 반복해 약 1시간 40분 멈춘다.

set -u
PROJECT="$HOME/Desktop/SCBK_Intern_Project/2026_election_prediction"
LOG_DIR="$PROJECT/election2026/data/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/daily_run.log"

cd "$PROJECT" || exit 1

# 이미 런이 돌고 있으면(사람이 손으로 돌리는 중) 그냥 비킨다. 두 파이프라인이
# 같은 raw 캐시에 동시에 쓰면 반쯤 쓰인 JSON을 서로 읽는다.
if /usr/bin/pgrep -f "election2026 run" >/dev/null 2>&1; then
  mkdir -p "$LOG_DIR"
  echo "$(date '+%Y-%m-%d %H:%M:%S') 이미 실행 중이라 건너뜀" >>"$LOG"
  exit 0
fi

# 네트워크가 아직 안 올라온 상태(절전에서 막 깬 직후)면 최대 5분 기다린다.
for i in {1..10}; do
  if /usr/bin/curl -s -m 5 -o /dev/null https://gamma-api.polymarket.com/events?limit=1; then
    break
  fi
  sleep 30
done

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 시작 =====" >>"$LOG"
/usr/bin/python3 -m election2026 run --skip gdelt,reddit,youtube >>"$LOG" 2>&1
STATUS=$?
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 종료 (exit $STATUS) =====" >>"$LOG"

# 로그가 무한히 자라지 않게 마지막 2000줄만 남긴다.
tail -n 2000 "$LOG" >"$LOG.tmp" && mv "$LOG.tmp" "$LOG"

exit $STATUS

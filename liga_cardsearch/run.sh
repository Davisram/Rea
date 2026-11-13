#!/bin/bash
# Roda a varredura e faz commit/push do progresso a cada 30 min.
cd "$(dirname "$0")/.." || exit 1
BRANCH=$(git rev-parse --abbrev-ref HEAD)
push() {
  git add data && git commit -qm "Liga cardsearch: progresso da varredura ($(wc -l < data/queries.jsonl) buscas)" && \
  for i in 2 4 8 16; do git push -q -u origin "$BRANCH" && break; sleep $i; done
}
python3 -I liga_cardsearch/scrape.py --out data --delay 0.5 "$@" & PID=$!
while kill -0 $PID 2>/dev/null; do
  for _ in $(seq 180); do kill -0 $PID 2>/dev/null || break; sleep 10; done
  push
done
wait $PID; RC=$?
push
exit $RC

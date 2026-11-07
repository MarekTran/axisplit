#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
B=http://127.0.0.1:8777
rm -f data/splits.db
nohup uv run uvicorn axisplit.main:app --port 8777 > /tmp/axisplit.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
for i in $(seq 20); do curl -sf -o /dev/null $B/ && break; sleep 0.5; done

uv run python - <<'EOF'
from PIL import Image
im = Image.new("RGB", (400, 600), "#efe9dd")
ex = im.getexif(); ex[274] = 6  # EXIF orientation: 90° CW — phones do this constantly
im.save("/tmp/receipt.jpg", exif=ex.tobytes())
EOF
curl -s -o /tmp/r.html -L -F "image=@/tmp/receipt.jpg;type=image/jpeg" $B/receipts
RID=$(grep -oP 'data-receipt="\K[^"]+' /tmp/r.html); echo "receipt=$RID"
uv run python -c "
from PIL import Image
im = Image.open('data/uploads/$RID.jpg')
assert im.size == (600, 400), f'EXIF not baked: {im.size}'
assert im.getexif().get(274) is None, 'orientation tag still present'
print('EXIF normalization ok (pixels == rendered == bbox space)')"

for name in Adam Nina; do curl -s -o /dev/null -d "username=$name" $B/r/$RID/people; done
curl -s -o /dev/null -d "label=GrillFor2&price=20,00" $B/r/$RID/items
curl -s -o /dev/null -d "label=Nachos&price=5" $B/r/$RID/items
curl -s $B/r/$RID -o /tmp/r2.html
echo "boxes: $(grep -c 'class="box"' /tmp/r2.html)"
IT1=$(grep -oP 'data-item="\K[^"]+' /tmp/r2.html | head -1)
IT2=$(grep -oP 'data-item="\K[^"]+' /tmp/r2.html | tail -1)

# No accounts: the link is the key, and any op names whom it is for.
op() { echo -n "$1: "; curl -s -H 'content-type: application/json' -d "$2" $B/r/$RID/ops; echo; }
op "place Adam@Grill" "{\"op\":\"place\",\"item_id\":\"$IT1\",\"username\":\"Adam\"}"
op "place Nina@Grill" "{\"op\":\"place\",\"item_id\":\"$IT1\",\"username\":\"Nina\"}"
op "place Adam@Nachos" "{\"op\":\"place\",\"item_id\":\"$IT2\",\"username\":\"Adam\"}"
op "tick Nina" '{"op":"confirm","username":"Nina"}'
op "tick Adam" '{"op":"confirm","username":"Adam"}'
op "unplace Nina@Grill (Adam's tick lapses)" "{\"op\":\"unplace\",\"item_id\":\"$IT1\",\"username\":\"Nina\"}"
op "place a stranger (404)" "{\"op\":\"place\",\"item_id\":\"$IT1\",\"username\":\"Ghost\"}"

curl -s $B/r/$RID -o /tmp/r3.html
PIPS=$(grep -c 'class="pip"' /tmp/r3.html)
STATUS=$(grep -oP 'id="status">\K[^<]+' /tmp/r3.html)
echo "pips on board: $PIPS  status: $STATUS"
grep -i "traceback" /tmp/axisplit.log | head -3

fail=0
test "$STATUS" = "All assigned" || { echo "FAIL: expected All assigned once a share moved after the ticks"; fail=1; }
[ "$PIPS" -ge 2 ] || { echo "FAIL: expected >=2 pips"; fail=1; }
CODE=$(curl -s -o /dev/null -w "%{http_code}" $B/r/nope)
test "$CODE" = "404" || { echo "FAIL: unknown receipt should be 404, got $CODE"; fail=1; }
test $fail -eq 0 && echo "SMOKE OK" || echo "SMOKE FAILED"
exit $fail

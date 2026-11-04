#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."
B=http://127.0.0.1:8777
rm -f data/axisplit.db /tmp/adam.jar /tmp/nina.jar
nohup uv run uvicorn axisplit.main:app --port 8777 > /tmp/axisplit.log 2>&1 &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
for i in $(seq 20); do curl -sf -o /dev/null $B/ && break; sleep 0.5; done

curl -s -c /tmp/adam.jar -o /dev/null -L -d "username=Adam&next=/t" $B/claim
curl -s -b /tmp/adam.jar -o /tmp/tab.html -L -d "name=Brno dinner" $B/tabs
TAB=$(grep -oP '/t/\K[a-zA-Z0-9_-]+' /tmp/tab.html | head -1)
SECRET=$(grep -oP '\?s=\K[a-zA-Z0-9_-]+' /tmp/tab.html | head -1)
echo "tab=$TAB secret=$SECRET"

uv run python - <<'EOF'
from PIL import Image
im = Image.new("RGB", (400, 600), "#efe9dd")
ex = im.getexif(); ex[274] = 6  # EXIF orientation: 90° CW — phones do this constantly
im.save("/tmp/receipt.jpg", exif=ex.tobytes())
EOF
curl -s -b /tmp/adam.jar -o /tmp/r.html -L -F "image=@/tmp/receipt.jpg;type=image/jpeg" $B/t/$TAB/receipts
RID=$(grep -oP 'data-receipt="\K[^"]+' /tmp/r.html); echo "receipt=$RID"
uv run python -c "
from PIL import Image
im = Image.open('data/uploads/$RID.jpg')
assert im.size == (600, 400), f'EXIF not baked: {im.size}'
assert im.getexif().get(274) is None, 'orientation tag still present'
print('EXIF normalization ok (pixels == rendered == bbox space)')"

curl -s -b /tmp/adam.jar -o /dev/null -d "label=GrillFor2&price=20,00" $B/r/$RID/items
curl -s -b /tmp/adam.jar -o /dev/null -d "label=Nachos&price=5" $B/r/$RID/items
curl -s -b /tmp/adam.jar $B/r/$RID -o /tmp/r2.html
echo "boxes: $(grep -c 'class="box"' /tmp/r2.html)"
IT1=$(grep -oP 'data-item="\K[^"]+' /tmp/r2.html | head -1)
IT2=$(grep -oP 'data-item="\K[^"]+' /tmp/r2.html | tail -1)

echo -n "place Adam@Grill: "; curl -s -b /tmp/adam.jar -H 'content-type: application/json' -d "{\"op\":\"place\",\"item_id\":\"$IT1\",\"username\":\"Adam\"}" $B/r/$RID/ops; echo
echo -n "place Adam dup (idempotent): "; curl -s -b /tmp/adam.jar -H 'content-type: application/json' -d "{\"op\":\"place\",\"item_id\":\"$IT1\",\"username\":\"Adam\"}" $B/r/$RID/ops; echo

curl -s -b /tmp/nina.jar -c /tmp/nina.jar -o /dev/null -L -d "username=Nina&next=/t/$TAB?s=$SECRET" $B/claim
curl -s -b /tmp/nina.jar -o /dev/null -d "username=Nina&next=/r/$RID" $B/claim
echo -n "Nina place herself@Grill: "; curl -s -b /tmp/nina.jar -H 'content-type: application/json' -d "{\"op\":\"place\",\"item_id\":\"$IT1\",\"username\":\"Nina\"}" $B/r/$RID/ops; echo
echo -n "Nina place Adam@Nachos: "; curl -s -b /tmp/nina.jar -H 'content-type: application/json' -d "{\"op\":\"place\",\"item_id\":\"$IT2\",\"username\":\"Adam\"}" $B/r/$RID/ops; echo
echo -n "confirm Nina: "; curl -s -b /tmp/nina.jar -H 'content-type: application/json' -d '{"op":"confirm"}' $B/r/$RID/ops; echo
echo -n "confirm Adam: "; curl -s -b /tmp/adam.jar -H 'content-type: application/json' -d '{"op":"confirm"}' $B/r/$RID/ops; echo
echo -n "unplace Adam@Grill (bumps back): "; curl -s -b /tmp/adam.jar -H 'content-type: application/json' -d "{\"op\":\"unplace\",\"item_id\":\"$IT1\",\"username\":\"Adam\"}" $B/r/$RID/ops; echo
echo -n "re-place Adam@Grill: "; curl -s -b /tmp/adam.jar -H 'content-type: application/json' -d "{\"op\":\"place\",\"item_id\":\"$IT1\",\"username\":\"Adam\"}" $B/r/$RID/ops; echo

curl -s -b /tmp/adam.jar "$B/t/$TAB?s=$SECRET" -o /tmp/tab2.html
echo "--- ledger rows:"; grep -oP '(?<=<td>)[^<]+' /tmp/tab2.html
curl -s -b /tmp/nina.jar $B/r/$RID -o /tmp/r3.html
PIPS=$(grep -c 'class="pip"' /tmp/r3.html)
STATUS=$(grep -oP 'badge" id="status">\K[^<]+' /tmp/r3.html)
echo "pips on board: $PIPS  status: $STATUS"
grep -i "traceback" /tmp/axisplit.log | head -3

fail=0
test "$STATUS" = "splitting" || { echo "FAIL: expected status splitting after token move post-settle"; fail=1; }
[ "$PIPS" -ge 3 ] || { echo "FAIL: expected >=3 pips"; fail=1; }
test -s /tmp/tab2.html || { echo "FAIL: tab page empty"; fail=1; }
# Nina (claimed, but not a member of a new tab) must get 403 without the secret
curl -s -b /tmp/adam.jar -o /tmp/tab3.html -L -d "name=Second tab" $B/tabs
TAB2=$(grep -oP '/t/\K[a-zA-Z0-9_-]+' /tmp/tab3.html | head -1)
CODE=$(curl -s -b /tmp/nina.jar -o /dev/null -w "%{http_code}" $B/t/$TAB2)
test "$CODE" = "403" || { echo "FAIL: non-member without secret should get 403, got $CODE"; fail=1; }
test $fail -eq 0 && echo "SMOKE OK" || echo "SMOKE FAILED"
exit $fail

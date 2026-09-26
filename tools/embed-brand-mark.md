# Regenerating the embedded brand mark

`src/lib/brandmark.ts` carries `backend/static/img/tiles/shelfcloud-icon.png`
from the gateway repo as base64, so the drawn logo never has to fetch it and
can never fall back to an opaque tile.

If that art is redrawn, regenerate:

```bash
python3 - <<"PY"
import base64
src = "../shelfcloud/backend/static/img/tiles/shelfcloud-icon.png"
b64 = base64.b64encode(open(src, "rb").read()).decode()
print("\n".join(b64[i:i+96] for i in range(0, len(b64), 96)))
PY
```

Paste the chunks into `DATA` as quoted, `+`-joined lines, then bump
`ART_REVISION` in `src/lib/sync.ts` so tiles already on a shelf redraw.

The mark MUST keep its alpha channel — that is the whole point of embedding it.

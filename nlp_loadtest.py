#!/usr/bin/env python3
# 持续压测 GA(TCP443→ALB80→node:8000 vLLM),打到 deadline(epoch)。
# 用法: python3 nlp_loadtest.py <deadline_epoch> [workers]
import urllib.request, json, time, threading, sys

GA = "http://adc2647e1f239dca0.awsglobalaccelerator.com:443/v1/chat/completions"
DEADLINE = int(sys.argv[1])
WORKERS = int(sys.argv[2]) if len(sys.argv) > 2 else 8
BODY = json.dumps({
    "model": "gemma-4-26B-A4B-it",
    "messages": [{"role": "user", "content": "Reply with one short friendly sentence."}],
    "max_tokens": 48, "temperature": 0.7,
}).encode()

cnt = {"ok": 0, "err": 0, "lat": 0.0}
lock = threading.Lock()

def worker():
    while time.time() < DEADLINE:
        t = time.time()
        try:
            req = urllib.request.Request(GA, data=BODY, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=90) as r:
                r.read()
            with lock:
                cnt["ok"] += 1; cnt["lat"] += time.time() - t
        except Exception:
            with lock:
                cnt["err"] += 1
            time.sleep(0.5)

threads = [threading.Thread(target=worker, daemon=True) for _ in range(WORKERS)]
for t in threads:
    t.start()
print(f"start workers={WORKERS} deadline={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(DEADLINE))}", flush=True)

last, lastok = time.time(), 0
while time.time() < DEADLINE:
    time.sleep(30)
    with lock:
        ok, err, lat = cnt["ok"], cnt["err"], cnt["lat"]
    now = time.time()
    qps = (ok - lastok) / max(1e-6, now - last)
    print(f"{time.strftime('%H:%M:%S')} ok={ok} err={err} qps={qps:.1f} avg_lat={(lat/ok if ok else 0):.2f}s", flush=True)
    last, lastok = now, ok
print(f"DONE ok={cnt['ok']} err={cnt['err']}", flush=True)

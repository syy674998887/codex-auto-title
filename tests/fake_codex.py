"""仅用于协议回归的本地假服务，无模型、网络或用户配置。"""
import json
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "normal"
name = None
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    rid, method = request["id"], request["method"]
    if mode == "eof":
        break
    if mode == "timeout":
        time.sleep(10)
        break
    if mode == "handshake_error" and method == "initialize":
        print(json.dumps({"id": rid, "error": {"code": -1, "message": "denied"}}), flush=True)
        continue
    if mode == "missing_result":
        print(json.dumps({"id": rid}), flush=True)
        continue
    print("not json", flush=True)
    print("[]", flush=True)
    print(json.dumps({"method": "notification", "params": {"name": "中文通知"}}), flush=True)
    if method == "thread/read":
        result = {"thread": {"id": request["params"]["threadId"], "name": name}}
    elif method == "thread/name/set":
        name = request["params"]["name"]
        result = {}
    else:
        result = {}
    print(json.dumps({"id": rid, "result": result}, ensure_ascii=False), flush=True)

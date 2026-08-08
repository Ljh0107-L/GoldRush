#!/usr/bin/env python3
"""arena.py — GoldRush 2.0 平台命令行客户端（纯标准库，无依赖）

给 Agent 用的提交/对战/取日志工具，替代浏览器点击流程。

接口契约来自前端 bundle：
  鉴权      Authorization: <token>        （原始值，不是 Bearer）
  响应封装  {code, data}；code 非 0 即错误；1001-1004 = token 失效
  日志端点  直接返回纯文本

用法：
  ./arena.py rank                                    排行榜
  ./arena.py maps                                    地图列表
  ./arena.py opponents                               可挑战的他人代码
  ./arena.py games [-n 20]                           我的对局记录
  ./arena.py info <game_id>                          单局状态
  ./arena.py log <game_id> [-o out.log]              下载对局日志
  ./arena.py submit --map 1 --self A.py:v1 B.py:v2   自博弈
  ./arena.py submit --map 1 --vs <model_id> A.py:v1  挑战他人
  ./arena.py watch <game_id>                         轮询直到可回放，然后自动存日志
"""
import argparse
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE = os.environ.get("GR_BASE", "http://47.103.127.219")
KEY = os.environ.get("GR_KEY", "LqUcsD1cyA")
TOKEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".arena_token")

LANG = {"py": 1, "so": 2}          # Python=1, C++=2
LANG_NAME = {1: "Python", 2: "C++"}


# ───────────────────────── 传输层 ─────────────────────────

class ApiError(Exception):
    pass


def _multipart(fields, files):
    """手工编码 multipart/form-data。fields: [(k,v)]，files: [(k, filename, bytes)]"""
    boundary = "----GoldRush" + uuid.uuid4().hex
    out = bytearray()
    for k, v in fields:
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                % (boundary, k, v)).encode()
    for k, fname, blob in files:
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        out += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"; filename=\"%s\"\r\n"
                "Content-Type: %s\r\n\r\n" % (boundary, k, fname, ctype)).encode()
        out += blob + b"\r\n"
    out += ("--%s--\r\n" % boundary).encode()
    return bytes(out), "multipart/form-data; boundary=" + boundary


def _request(method, path, params=None, json_body=None, multipart=None, token=None, raw=False):
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    body, ctype = None, None
    if json_body is not None:
        body, ctype = json.dumps(json_body).encode(), "application/json"
    elif multipart is not None:
        body, ctype = _multipart(*multipart)

    req = urllib.request.Request(url, data=body, method=method)
    if ctype:
        req.add_header("Content-Type", ctype)
    if token:
        req.add_header("Authorization", token)

    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise ApiError("HTTP %s %s :: %s" % (e.code, path, detail))
    except urllib.error.URLError as e:
        raise ApiError("网络不可达 %s :: %s" % (path, e.reason))

    text = payload.decode("utf-8", "replace")
    if raw:
        return text
    try:
        env = json.loads(text)
    except ValueError:
        return text                                  # 纯文本端点
    if isinstance(env, dict) and env.get("code"):
        if env["code"] in (1001, 1002, 1003, 1004):
            raise ApiError("TOKEN_INVALID: %s" % env.get("message", ""))
        raise ApiError("code=%s %s" % (env["code"], env.get("message", "")))
    return env.get("data", env) if isinstance(env, dict) else env


# ───────────────────────── 会话 ─────────────────────────

def login():
    data = _request("POST", "/api/user/login", json_body={"key": KEY})
    token = data["token"]
    with open(TOKEN_FILE, "w") as f:
        f.write(token)
    os.chmod(TOKEN_FILE, 0o600)
    return token


def token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE) as f:
            t = f.read().strip()
        if t:
            return t
    return login()


def call(method, path, **kw):
    """带一次自动重登的调用。"""
    try:
        return _request(method, path, token=token(), **kw)
    except ApiError as e:
        if "TOKEN_INVALID" not in str(e):
            raise
        return _request(method, path, token=login(), **kw)


# ───────────────────────── 业务 ─────────────────────────

def _fmt_ns(ns):
    """平台的 user_cost1 单位是纳秒。"""
    try:
        ns = float(ns)
    except (TypeError, ValueError):
        return "-"
    if ns < 1000:
        return "%dns" % ns
    if ns < 1e6:
        return "%.2fus" % (ns / 1e3)
    return "%.2fms" % (ns / 1e6)


def cmd_rank(a):
    rows = call("GET", "/api/user/get_rank_list_1").get("list", [])
    print("%-4s %-22s %-22s %-9s %s" % ("#", "队伍", "学校", "外战胜率", "P90"))
    for i, r in enumerate(rows[:a.n], 1):
        wr = r.get("win_rate")
        wr = "%.2f%%" % (float(wr) * 100) if wr not in (None, "") else "-"
        print("%-4s %-22s %-22s %-9s %s" % (
            i, r.get("user_name_cn", "?"), r.get("user_school", "-"),
            wr, _fmt_ns(r.get("user_cost1"))))


def cmd_maps(a):
    for m in call("GET", "/api/user/get_map_list").get("list", []):
        print("%-6s %s" % (m.get("id"), m.get("name")))


def cmd_opponents(a):
    for m in call("GET", "/api/user/get_model_list_4").get("list", []):
        print("%-8s %-24s %s" % (m.get("id"), m.get("name"), m.get("user_name_cn", "")))


def cmd_games(a):
    d = call("GET", "/api/user/get_game_list_1",
             params={"page": 1, "page_size": a.n})
    rows = d.get("list", d if isinstance(d, list) else [])
    if not rows:
        print("(暂无对局记录)")
        return
    print("%-10s %-20s %-8s %s" % ("id", "创建时间", "可回放", "结果"))
    for g in rows:
        print("%-10s %-20s %-8s %s" % (
            g.get("id"), g.get("created_at", "-"),
            "是" if g.get("is_parse_log") else "否",
            g.get("result", g.get("status", "-"))))


def cmd_info(a):
    print(json.dumps(call("GET", "/api/user/get_game_info",
                          params={"id": a.game_id}), ensure_ascii=False, indent=1))


def cmd_log(a):
    txt = call("GET", "/api/user/get_game_log", params={"id": a.game_id}, raw=True)
    out = a.output or "game_%s.log" % a.game_id
    with open(out, "w") as f:
        f.write(txt)
    print("已保存 %s (%d 字节, %d 行)" % (out, len(txt), txt.count("\n") + 1))


def _spec(s):
    """'path/to/player.py:modelname' -> (name, lang, filename, bytes)"""
    if ":" not in s:
        raise SystemExit("代码参数格式应为 文件路径:Model名  (收到 %r)" % s)
    path, name = s.rsplit(":", 1)
    ext = path.rsplit(".", 1)[-1].lower()
    if ext not in LANG:
        raise SystemExit("只支持 .py 或 .so，收到 %r" % path)
    if not (name and name[0].isalpha() and name.isalnum()):
        raise SystemExit("Model 名限字母数字且字母开头，收到 %r" % name)
    with open(path, "rb") as f:
        blob = f.read()
    return name, LANG[ext], os.path.basename(path), blob


def cmd_submit(a):
    specs = [_spec(s) for s in a.code]
    if a.vs:
        if len(specs) != 1:
            raise SystemExit("挑战模式只需 1 份自己的代码")
    elif len(specs) != 2:
        raise SystemExit("自博弈模式需要 2 份代码")

    fields = [("map_id", str(a.map))]
    if a.vs:
        fields.append(("model_id", str(a.vs)))
    files = []
    for name, lang, fname, blob in specs:
        fields.append(("model_langs", str(lang)))
        fields.append(("model_names", name))
        files.append(("model_files", fname, blob))

    mode = "挑战 model_id=%s" % a.vs if a.vs else "自博弈"
    print("提交: 地图%s | %s" % (a.map, mode))
    for name, lang, fname, blob in specs:
        print("   %-14s %-8s %-18s %d 字节" % (name, LANG_NAME[lang], fname, len(blob)))
    if a.dry_run:
        print("(--dry-run，未实际发送)")
        return

    call("POST", "/api/user/add_model_1", multipart=(fields, files))
    print("提交成功。用 `gr.py games` 查看对局 id，再用 `gr.py watch <id>` 等结果。")


def cmd_publish(a):
    """上传公开代码(供他人挑战, add_model_4)。被动对局计入外战胜率。"""
    name, lang, fname, blob = _spec(a.code)
    # 注意: add_model_4 用单数字段名(model_file/model_lang/model_name), 与 add_model_1 不同
    fields = [("model_lang", str(lang)), ("model_name", name)]
    files = [("model_file", fname, blob)]
    print("发布公开代码: %-14s %-8s %-18s %d 字节" % (name, LANG_NAME[lang], fname, len(blob)))
    if a.dry_run:
        print("(--dry-run，未实际发送)")
        return
    call("POST", "/api/user/add_model_4", multipart=(fields, files))
    print("发布成功。他人挑战你时将使用该版本。")


def cmd_watch(a):
    deadline = time.time() + a.timeout
    while time.time() < deadline:
        info = call("GET", "/api/user/get_game_info", params={"id": a.game_id})
        if info.get("is_upload_log") == 2:
            raise SystemExit("系统错误: %s" % info.get("error_msg", "未知"))
        if info.get("is_parse_log"):
            print("对局完成。")
            a.output = a.output or "game_%s.log" % a.game_id
            cmd_log(a)
            return
        print("  游戏中… %ds" % int(time.time() - (deadline - a.timeout)))
        time.sleep(a.interval)
    raise SystemExit("等待超时 (%ds)" % a.timeout)


# ───────────────────────── CLI ─────────────────────────

def main():
    p = argparse.ArgumentParser(description="GoldRush 2.0 平台客户端")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("rank", help="排行榜")
    q.add_argument("-n", type=int, default=20)
    q.set_defaults(func=cmd_rank)

    sub.add_parser("maps", help="地图列表").set_defaults(func=cmd_maps)
    sub.add_parser("opponents", help="可挑战的他人代码").set_defaults(func=cmd_opponents)

    q = sub.add_parser("games", help="我的对局记录")
    q.add_argument("-n", type=int, default=10)
    q.set_defaults(func=cmd_games)

    q = sub.add_parser("info", help="单局状态")
    q.add_argument("game_id")
    q.set_defaults(func=cmd_info)

    q = sub.add_parser("log", help="下载对局日志")
    q.add_argument("game_id")
    q.add_argument("-o", "--output")
    q.set_defaults(func=cmd_log)

    q = sub.add_parser("submit", help="发起对局")
    q.add_argument("--map", required=True, help="地图 id")
    q.add_argument("--vs", help="对手 model_id (省略=自博弈)")
    q.add_argument("--dry-run", action="store_true")
    q.add_argument("code", nargs="+", metavar="文件:Model名")
    q.set_defaults(func=cmd_submit)

    q = sub.add_parser("publish", help="上传公开代码供他人挑战(add_model_4)")
    q.add_argument("--dry-run", action="store_true")
    q.add_argument("code", metavar="文件:Model名")
    q.set_defaults(func=cmd_publish)

    q = sub.add_parser("watch", help="轮询直到可回放并保存日志")
    q.add_argument("game_id")
    q.add_argument("-o", "--output")
    q.add_argument("--interval", type=int, default=10)
    q.add_argument("--timeout", type=int, default=900)
    q.set_defaults(func=cmd_watch)

    a = p.parse_args()
    try:
        a.func(a)
    except ApiError as e:
        raise SystemExit("接口错误: %s" % e)


if __name__ == "__main__":
    main()

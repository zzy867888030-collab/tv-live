#!/usr/bin/env python3
# jrs_scraper.py — 抓取 JRKAN(jrs) 当日赛程与频道流，生成 TVBox 直播源 TXT
# v3: 全频道类型支持（sm/kbs/mgxl/txf/vxt/pao/wen/vl）+ 国内源优先（腾讯/咪咕高清排前）
# 用法: python3 jrs_scraper.py <输出文件>
#
# 取流链路（全部实测逆向）：
#   赛程: https://im-imgs-bucket.oss-accelerate.aliyuncs.com/index.js
#         真实播放 id 在卡片 getPlayUrl("lineN","playid")（与 data-lid 不同）
#   次页: {line_host}/play/steam{playid}.html（线路 host 由首页 PLAY_HOSTS base64 按地区下发）
#   频道: 次页 <a data-play="...">，多种类型：
#     sm.html?id=N      → /play/N.html   → msss.html?id=<urlencode(m3u8)>  [域名反转]
#     /play/kbs/?id=X   → playerConfig.playerUrlBase64 + encodedStr
#     /play/mgxl/?id=X    → demo.songzenet.com/paps.html?id=<encodedStr>
#     /play/txf|vxt|pao|wen/?id=X
#     /play/vl/?id=vl
#   paps 页: jsjiami v6 混淆，核心 = XXTEA.decryptFromBase64(id, KEY) → JSON{ts,url}
#           （webcrack 反混淆逆向；ts 5 分钟时效 → 每次抓取现解）
#   msss 页: 域名主段反转（hdl3.888rrtt.com → hdl3.ttrr888.com）
import base64
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

INDEX_URL = "https://im-imgs-bucket.oss-accelerate.aliyuncs.com/index.js"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile Safari/604.1"
FALLBACK_HOSTS = ["http://play.sportsteam53.com", "http://m.jw1104.com", "http://play.sportsteam356.com"]
TIMEOUT = 12
HERE = os.path.dirname(os.path.abspath(__file__))

# 频道名优先级：越小越靠前（成为线路1）。国内平台高清源优先。
PRIORITY = [
    ("中文高清 Q", 0), ("中文高清 M", 0), ("中文高清M", 0),   # 腾讯/咪咕高清
    ("CCTV", 0), ("央视", 0), ("咪咕", 0), ("腾讯", 0),
    ("高清直播", 2),
    ("纬来", 3),
    ("主播解说", 4),
]


def channel_rank(name):
    n = name.lower()
    for kw, rank in PRIORITY:
        if kw.lower() in n:
            return rank
    return 3


def http(url, timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "http://m.jrs15.com/"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "ignore")


def restore_stream_host(url):
    """msss 播放器 restoreStreamUrl 的机关：主域名段反转"""
    m = re.match(r"^(https?://)([^/:]+)(.*)$", url)
    if not m:
        return url
    scheme, host, rest = m.groups()
    parts = host.split(".")
    if len(parts) >= 3:
        parts[-2] = parts[-2][::-1]
        host = ".".join(parts)
    return scheme + host + rest


def get_play_hosts():
    """线路 host 由站点按地区下发（首页内联 PLAY_HOSTS base64）"""
    hosts = []
    try:
        home = http("https://m.jrs15.com/", timeout=10)
        for m in re.finditer(r'(line\d+)\s*:\s*atob\("([A-Za-z0-9+/=]+)"\)', home):
            try:
                hosts.append(base64.b64decode(m.group(2)).decode("utf-8", "ignore").rstrip("/"))
            except Exception:
                pass
    except Exception:
        pass
    hosts = [h if h.startswith("http") else "http://" + h for h in hosts]
    return hosts or FALLBACK_HOSTS


def fetch_match_list():
    t = http(INDEX_URL)
    cards = re.split(r"document\.write\('<ul class=\"item play d-touch", t)[1:]
    out = []
    for c in cards:
        league = (re.search(r'class="name">([^<]*)</span>', c) or [None, "赛事"])[1]
        tm = re.search(r'<li class="lab_time">([^<]*)</li>', c)
        teams = re.findall(r'<strong class="name">([^<]*)</strong>', c)
        stype = re.search(r'data-lid="\d+,(\d+),', c)
        pid = re.search(r'getPlayUrl\("line1",\s*"(\d+)"\)', c)
        if not (tm and len(teams) >= 2 and pid):
            continue
        out.append({
            "league": league.strip(), "time": tm.group(1).strip(),
            "home": teams[0].strip(), "away": teams[1].strip(),
            "playid": pid.group(1), "sport": stype.all if False else (stype.group(1) if stype else "0"),
        })
    return out


def parse_channels(html):
    """播放页 → [{name, path}]（保持页面顺序）"""
    chs = []
    for m in re.finditer(r'<a[^>]*data-play="([^"]*)"[^>]*>\s*<em[^>]*></em>\s*<strong>([^<]*)</strong>', html, re.S):
        dp, name = m.group(1).strip(), m.group(2).strip()
        if dp:
            chs.append({"name": name, "path": dp})
    return chs


# ---------- paps (kbs/mgxl 型) 解密：node 沙箱 ----------

_PAPS = {"ready": False}

def load_paps_assets():
    """定位 node 解密器与依赖；文件应在脚本同目录"""
    global _PAPS
    runner = os.path.join(HERE, "paps_decrypt.js")
    xxtea = os.path.join(HERE, "xxtea_lib.js")
    obf = os.path.join(HERE, "paps_obf.js")
    _PAPS.update({"ready": all(os.path.exists(p) for p in (runner, xxtea, obf)),
                  "runner": runner, "xxtea": xxtea, "obf": obf})
    return _PAPS["ready"]


def _node_bin():
    import shutil
    p = shutil.which("node")
    if p:
        return p
    for c in ("/usr/local/bin/node", "/opt/homebrew/bin/node", "/usr/bin/node"):
        if os.path.exists(c):
            return c
    return "node"

def decrypt_paps(player_base, enc):
    """node 沙箱：XXTEA + paps 混淆代码 → m3u8 URL（stdout 输出）"""
    if not _PAPS["ready"]:
        load_paps_assets()
    if not _PAPS["ready"]:
        return None
    try:
        payload = json.dumps({"enc": enc})
        r = subprocess.run([_node_bin(), _PAPS["runner"]], input=payload,
                           capture_output=True, text=True, timeout=25,
                           cwd=HERE)
        out = r.stdout.strip().splitlines()
        if out:
            line = out[-1]
            if line.startswith("http"):
                return line
    except Exception:
        pass
    return None


def _resolve_paps(html):
    """kbs/mgxl 型页面：playerConfig.playerUrlBase64 + encodedStr → paps → m3u8"""
    pc = re.search(r'playerUrlBase64:\s*"([^"]+)"', html)
    enc = re.search(r'var encodedStr = "([^"]+)"', html)
    if not (pc and enc):
        return None
    player_base = base64.b64decode(pc.group(1)).decode("utf-8", "ignore")
    if "paps.html" not in player_base:
        return None
    # 直接抓 paps 页看有没有明文 m3u8（部分情况）
    url = player_base + enc.group(1)
    try:
        page = http(url, timeout=10)
    except Exception:
        return None
    m = re.search(r'(https?://[^"\'\s]+\.m3u8[^"\'\s]*)', page)
    if m:
        return m.group(1)
    # 混淆解密器 → node 沙箱
    return decrypt_paps(player_base, enc.group(1))


def resolve_channel(host, ch, playid):
    """通用频道解析 → m3u8（或 None）"""
    path = ch["path"]
    sm = re.search(r'sm\.html\?id=(\d+)', path)
    if sm:
        # sm 包装页 → 直接取频道页 /play/N.html（msss 结构）
        url = host + "/play/" + sm.group(1) + ".html"
    elif path.startswith("/play/"):
        # kbs/mgxl/vxt/txf/pao/wen/vl 型：路径即频道页
        url = host + path
        if url.endswith("id2="):
            url += playid
        elif "id2=" not in url:
            url += ("&" if "?" in url else "?") + "id2=" + playid
    else:
        return None
    try:
        h = http(url, timeout=10)
    except Exception:
        return None
    # 结构 A: msss.html?id=... （sm 型）
    m = re.search(r'msss\.html\?id=([^"\'\s>]+)', h)
    if m:
        u = urllib.parse.unquote(m.group(1))
        if u.startswith("//"):
            u = "http:" + u
        u = restore_stream_host(u)
        return u if ".m3u8" in u else None
    # 结构 B: playerConfig + encodedStr （kbs/mgxl 型 → paps）
    if "playerUrlBase64" in h and "encodedStr" in h:
        return _resolve_paps(h)
    # 结构 C: 页面直出 m3u8
    m = re.search(r'(https?://[^"\'\s]+\.m3u8[^"\'\s]*)', h)
    if m:
        return m.group(1)
    return None


def build_live_txt(max_matches=60):
    matches = fetch_match_list()
    hosts = get_play_hosts()
    groups = {"NBA直播": [], "篮球直播": [], "足球直播": []}
    stats = {"ok": 0, "fail": 0, "ch": 0}

    def handle(mt):
        title = f"{mt['time']} {mt['home']}vs{mt['away']}"
        html = None
        for host in hosts:
            try:
                html = http(f"{host}/play/steam{mt['playid']}.html", timeout=10)
                if "data-play" in html:
                    break
            except Exception:
                continue
        chs = parse_channels(html) if html else []
        if not chs:
            stats["fail"] += 1
            return
        chs.sort(key=lambda c: channel_rank(c["name"]))  # 国内源排前 → 线路1
        gkey = ("NBA直播" if mt["league"].upper() == "NBA"
                else "篮球直播" if mt["sport"] == "2"
                else "足球直播")
        for ch in chs:
            url = resolve_channel(hosts[0], ch, mt["playid"])
            if url:
                groups[gkey].append(f"{title},{url}")
                stats["ch"] += 1
        if any(l.startswith(title + ",") for l in groups[gkey]):
            stats["ok"] += 1
        else:
            stats["fail"] += 1

    with ThreadPoolExecutor(max_workers=10) as ex:
        list(ex.map(handle, matches[:max_matches]))

    parts = []
    for name, items in groups.items():
        if items:
            parts.append(f"{name},#genre#")
            parts.extend(items)
    return "\n".join(parts) + "\n", stats, len(matches), stats["ch"]


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "jrs.txt"
    load_paps_assets()
    t0 = time.time()
    txt, stats, total, ch = build_live_txt()
    with open(out, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"matches={total} games={stats['ok']} fail={stats['fail']} channels={ch} "
          f"elapsed={time.time()-t0:.1f}s -> {out}")
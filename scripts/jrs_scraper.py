#!/usr/bin/env python3
# jrs_scraper.py — 抓取 JRKAN(jrs) 当日赛程与频道流，生成 TVBox 直播源 TXT（GitHub Actions 用）
# 用法: python3 jrs_scraper.py <输出文件>
# 仅用标准库。原理:
#   1) 赛程: https://im-imgs-bucket.oss-accelerate.aliyuncs.com/index.js (document.write 卡片)
#      真实播放 id 在卡片 getPlayUrl("lineN","playid") 里（与 data-lid 不同）
#   2) 播放页: http://play.sportsteam53.com/play/steam{playid}.html（三线路容错）
#   3) 频道: 播放页 a[data-play]=sm.html?id=频道号 → /play/{频道号}.html → msss.html?id=<urlencode(m3u8)>
#   4) 关键: m3u8 域名主段需反转（hdl3.888rrtt.com → hdl3.ttrr888.com），对应站点播放器
#      restoreStreamUrl() 的行为；不反转则 DNS NXDOMAIN。
import re
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

INDEX_URL = "https://im-imgs-bucket.oss-accelerate.aliyuncs.com/index.js"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 Mobile Safari/604.1"
PLAY_HOSTS = ["http://play.sportsteam53.com", "http://m.jw1104.com", "http://play.sportsteam356.com"]
TIMEOUT = 12


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


def fetch_match_list():
    """index.js → [{league, time, home, away, playid, sport}]，sport: 1=足球 2=篮球"""
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
            "league": league.strip(),
            "time": tm.group(1).strip(),
            "home": teams[0].strip(),
            "away": teams[1].strip(),
            "playid": pid.group(1),
            "sport": stype.group(1) if stype else "0",
        })
    return out


def parse_play_page(html):
    chs = []
    for m in re.finditer(r'<a[^>]*data-play="([^"]*)"[^>]*>.*?<strong>([^<]*)</strong>', html, re.S):
        dp, name = m.group(1), m.group(2).strip()
        pm = re.search(r'sm\.html\?id=(\d+)', dp)
        if pm:
            chs.append({"name": name, "provider": pm.group(1)})
    return chs


def resolve_channel(host, provider):
    try:
        h = http(f"{host}/play/{provider}.html", timeout=8)
        m = re.search(r'msss\.html\?id=([^"\'\s>]+)', h)
        if not m:
            return None
        url = urllib.parse.unquote(m.group(1))
        if url.startswith("//"):
            url = "http:" + url
        url = restore_stream_host(url)
        return url if ".m3u8" in url else None
    except Exception:
        return None


def build_live_txt(max_matches=60):
    """分组输出：NBA直播/篮球直播/足球直播；同名条目=同场比赛多线路（TVBox 自动合并为线路N/M）"""
    matches = fetch_match_list()
    groups = {"NBA直播": [], "篮球直播": [], "足球直播": []}
    stats = {"ok": 0, "fail": 0}

    def handle(mt):
        title = f"{mt['time']} {mt['home']}vs{mt['away']}"
        html = None
        for host in PLAY_HOSTS:
            try:
                html = http(f"{host}/play/steam{mt['playid']}.html", timeout=10)
                if "data-play" in html:
                    break
            except Exception:
                continue
        chs = parse_play_page(html) if html else []
        if not chs:
            stats["fail"] += 1
            return None
        urls = []
        for ch in chs:
            url = resolve_channel(PLAY_HOSTS[0], ch["provider"])
            if url:
                urls.append(url)
        if not urls:
            stats["fail"] += 1
            return None
        stats["ok"] += 1
        return (mt, title, urls)

    with ThreadPoolExecutor(max_workers=12) as ex:
        for res in ex.map(handle, matches[:max_matches]):
            if not res:
                continue
            mt, title, urls = res
            if mt["league"].upper() == "NBA":
                g = groups["NBA直播"]
            elif mt["sport"] == "2":
                g = groups["篮球直播"]
            else:
                g = groups["足球直播"]
            for u in urls:
                g.append(f"{title},{u}")

    parts = []
    total_ch = 0
    for name, items in groups.items():
        if items:
            parts.append(f"{name},#genre#")
            parts.extend(items)
            total_ch += len(items)
    return "\n".join(parts) + "\n", stats, len(matches), total_ch


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "jrs.txt"
    t0 = time.time()
    txt, stats, total, ch = build_live_txt()
    with open(out, "w", encoding="utf-8") as f:
        f.write(txt)
    print(f"matches={total} games={stats['ok']} fail={stats['fail']} channels={ch} "
          f"elapsed={time.time()-t0:.1f}s -> {out}")

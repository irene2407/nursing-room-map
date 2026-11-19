#!/usr/bin/env python3
"""수유실 자료를 받아 data/places.json 을 새로 만든다.

GitHub Actions에서 한 달에 한 번 돌아간다. 키는 환경변수(GitHub Secret)로만 받는다.
  SOOYUSIL_KEY : 수유정보 알리미 API 키

알리미 API 응답 모양이 설명서와 다를 수 있어서, 처음 몇 건과 항목 이름을
data/_debug/sooyusil_sample.json 에 남긴다 (공개 정보만, 키는 남기지 않음).
"""
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", "places.json")
DEBUG_DIR = os.path.join(ROOT, "data", "_debug")
API = "https://sooyusil.com/api/nursingRoomJSON.do"
ZONES = ["서울", "부산", "대구", "인천", "광주", "대전", "울산", "세종", "경기",
         "강원", "충북", "충남", "전북", "전남", "경북", "경남", "제주"]
KST = datetime.timezone(datetime.timedelta(hours=9))


def log(*a):
    print(*a, flush=True)


def get_json(params, key):
    q = dict(params)
    q["confirmApiKey"] = key
    url = API + "?" + urllib.parse.urlencode(q)
    req = urllib.request.Request(url, headers={"User-Agent": "nursing-room-map (personal, monthly update)"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except Exception as e:  # 오류 문구에 키가 섞이지 않게 종류만 남김
        raise RuntimeError(f"요청 실패: {type(e).__name__} {getattr(e, 'code', '')}") from None
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError("JSON이 아닌 응답: " + text[:300].replace(key, "***"))


def find_rooms(obj):
    """응답 안에서 수유실 목록(dict 리스트)을 찾는다."""
    best = []
    def walk(o):
        nonlocal best
        if isinstance(o, list):
            if o and all(isinstance(x, dict) for x in o) and len(o) > len(best):
                best = o
            for x in o:
                walk(x)
        elif isinstance(o, dict):
            # room 이 하나뿐이면 dict 로 올 수 있음
            for k, v in o.items():
                if k.lower() == "room" and isinstance(v, dict):
                    if len(best) < 1:
                        best = [v]
                walk(v)
    walk(obj)
    return best


def pick(d, *names):
    low = {k.lower(): v for k, v in d.items()}
    for n in names:
        v = low.get(n.lower())
        if v not in (None, ""):
            return v
    return ""


def to_float(v):
    try:
        return float(str(v).strip())
    except (TypeError, ValueError):
        return None


def coords(d):
    lat = to_float(pick(d, "gpsLat", "lat", "latitude", "wgs84Lat", "mapY", "posY", "y", "roomLat", "latPos"))
    lng = to_float(pick(d, "gpsLong", "gpsLng", "lng", "lon", "longitude", "wgs84Lng", "wgs84Lon", "mapX", "posX", "x", "roomLong", "roomLng", "lngPos"))
    if lat is not None and lng is not None and 32 < lat < 39.6 and 124 < lng < 132.6:
        return lat, lng
    if lat is not None and lng is not None and 32 < lng < 39.6 and 124 < lat < 132.6:
        return lng, lat  # 뒤바뀐 경우
    # 이름을 모를 때: 범위에 맞는 숫자 항목을 찾는다
    la = lo = None
    for k, v in d.items():
        f = to_float(v)
        if f is None:
            continue
        if la is None and 32 < f < 39.6 and re.search(r"lat|y", k, re.I):
            la = f
        elif lo is None and 124 < f < 132.6 and re.search(r"lo|lng|x", k, re.I):
            lo = f
    if la is not None and lo is not None:
        return la, lo
    return None


def norm(d):
    c = coords(d)
    if not c:
        return None
    room_no = str(pick(d, "roomNo", "room_no", "no", "id")).strip()
    type_code = str(pick(d, "roomTypeCode", "roomType")).strip()
    type_name = str(pick(d, "roomTypeName", "roomTypeNm")).strip()
    if type_code == "3" or "가족" in type_name:
        typ = "family"
    elif type_code == "4" or "모유" in type_name or "착유" in type_name:
        typ = "breast"
    else:
        typ = ""
    fc = str(pick(d, "fatherUseCode")).strip()
    fn = str(pick(d, "fatherUseNm", "fatherUseName")).strip()
    father = True if fc == "1" or fn in ("가능", "Y") else False if fc == "0" or fn in ("불가능", "불가", "N") else None
    if typ == "family" and father is None:
        father = True
    zone = str(pick(d, "zoneName")).strip()
    city = str(pick(d, "cityName")).strip()
    name = str(pick(d, "roomName", "name")).strip() or "이름 없는 수유실"
    return {
        "id": "s" + (room_no or f"{c[0]:.5f},{c[1]:.5f}"),
        "kind": "subway" if re.search(r"역$|역\s*\(|역\s*\d+호선|지하철", name) else "room",
        "name": name,
        "type": typ,
        "father": father,
        "addr": str(pick(d, "address", "addr")).strip(),
        "place": str(pick(d, "location", "roomLocation")).strip(),
        "tel": str(pick(d, "managerTelNo", "telNo", "tel")).strip(),
        "lat": round(c[0], 6),
        "lng": round(c[1], 6),
        "region": (zone + " " + city).strip(),
        "src": "sooyusil",
        "roomNo": room_no,
    }


def fetch_sooyusil(key):
    rows = []
    first = get_json({}, key)
    rows = find_rooms(first)
    log(f"전체 한 번에 요청: {len(rows)}건")
    sample_resp = first
    if len(rows) < 1500:  # 한 번에 다 안 오면 시도별로 나눠 받기
        by_zone = []
        for z in ZONES:
            time.sleep(1.0)  # 서버에 부담 주지 않게 천천히
            try:
                part = find_rooms(get_json({"zoneName": z}, key))
            except RuntimeError as e:
                log(f"  {z}: {e}")
                continue
            log(f"  {z}: {len(part)}건")
            by_zone.extend(part)
        if len(by_zone) > len(rows):
            rows = by_zone
    return rows, sample_resp


def write_debug(rows, resp):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    keys = sorted({k for r in rows[:200] for k in r.keys()})
    top = {k: (type(v).__name__) for k, v in resp.items()} if isinstance(resp, dict) else type(resp).__name__
    with open(os.path.join(DEBUG_DIR, "sooyusil_sample.json"), "w", encoding="utf-8") as f:
        json.dump({"checked": datetime.datetime.now(KST).isoformat(timespec="seconds"),
                   "count": len(rows), "top_level": top, "field_names": keys, "first_rows": rows[:3]},
                  f, ensure_ascii=False, indent=1)


def main():
    key = os.environ.get("SOOYUSIL_KEY", "").strip()
    old = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)

    if not key:
        log("SOOYUSIL_KEY 가 없어서 수유실 자료는 그대로 둡니다.")
        return 0

    try:
        rows, resp = fetch_sooyusil(key)
    except RuntimeError as e:
        log("알리미 자료를 받지 못했어요:", e)
        return 1
    write_debug(rows, resp)

    rooms, skipped = [], 0
    seen = set()
    for r in rows:
        n = norm(r)
        if not n:
            skipped += 1
            continue
        if n["id"] in seen:
            continue
        seen.add(n["id"])
        rooms.append(n)
    log(f"좌표 있는 수유실 {len(rooms)}곳, 좌표 없어서 뺀 곳 {skipped}곳")

    if len(rooms) < 500:
        log("수유실이 너무 적어서(500곳 미만) 기존 자료를 유지합니다. _debug 파일을 확인하세요.")
        return 1

    # 다른 자료(지하철 보강·화장실)는 그대로 이어 붙임
    keep_rooms = [x for x in old.get("rooms", []) if x.get("src") not in ("sooyusil", "sample")]
    toilets = [x for x in old.get("toilets", []) if x.get("src") != "sample"]
    data = {
        "version": 1,
        "updated": datetime.datetime.now(KST).strftime("%Y-%m-%d"),
        "sample": False,
        "counts": {"sooyusil": len(rooms), "extra_rooms": len(keep_rooms), "toilets": len(toilets)},
        "rooms": sorted(rooms, key=lambda x: x["id"]) + keep_rooms,
        "toilets": toilets,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    log("저장 완료:", OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())

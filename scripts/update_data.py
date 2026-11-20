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
import urllib.error
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


def write_debug(rows, resp, name="sooyusil_sample.json", extra=None):
    os.makedirs(DEBUG_DIR, exist_ok=True)
    keys = sorted({k for r in rows[:200] for k in r.keys()})
    top = {k: (type(v).__name__) for k, v in resp.items()} if isinstance(resp, dict) else type(resp).__name__
    out = {"checked": datetime.datetime.now(KST).isoformat(timespec="seconds"),
           "count": len(rows), "top_level": top, "field_names": keys, "first_rows": rows[:3]}
    if extra:
        out.update(extra)
    with open(os.path.join(DEBUG_DIR, name), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------- 서울 지하철
SUBWAY_API = "https://apis.data.go.kr/B553766/facility/"
# 수유실 조회 주소 이름은 설명서에 없어서 후보를 차례로 시도 (엘리베이터는 getFcElvtr)
SUBWAY_OPS = ["getFcNrsrm", "getFcNrsRm", "getFcNursrm", "getFcBbyrm", "getFcFdrm"]
COORDS_FILE = os.path.join(ROOT, "data", "station_coords.json")


def http_json(url, headers=None, secrets=()):
    req = urllib.request.Request(url, headers={"User-Agent": "nursing-room-map", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            text = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        for sct in secrets:
            body = body.replace(sct, "***")
        raise RuntimeError(f"HTTP {e.code}: {body}") from None
    except Exception as e:
        raise RuntimeError(f"요청 실패: {type(e).__name__}") from None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        t = text[:300]
        for sct in secrets:
            t = t.replace(sct, "***")
        raise RuntimeError("JSON이 아닌 응답: " + t)


def find_key(o, name):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == name:
                return v
            r = find_key(v, name)
            if r is not None:
                return r
    elif isinstance(o, list):
        for x in o:
            r = find_key(x, name)
            if r is not None:
                return r
    return None


def fetch_subway_rows(key):
    tried = {}
    for op in SUBWAY_OPS:
        rows, page, total, first = [], 1, None, None
        try:
            while True:
                q = urllib.parse.urlencode({"serviceKey": key, "dataType": "JSON", "pageNo": page, "numOfRows": 1000})
                resp = http_json(SUBWAY_API + op + "?" + q, secrets=(key, urllib.parse.quote(key)))
                first = first or resp
                part = find_rooms(resp)
                total = total or int(find_key(resp, "totalCount") or 0)
                rows.extend(part)
                log(f"  {op} {page}쪽: {len(part)}건 (전체 {total})")
                if not part or len(rows) >= total or page >= 20:
                    break
                page += 1
                time.sleep(0.5)
        except RuntimeError as e:
            tried[op] = str(e)[:200]
            log(f"  {op}: {e}")
            continue
        if rows:
            return op, rows, first, tried
        tried[op] = "0건: " + json.dumps(first, ensure_ascii=False)[:200]
    return None, [], {}, tried


def station_query(stn, line):
    name = re.sub(r"\(.*?\)", "", str(stn)).strip()
    if not name.endswith("역"):
        name += "역"
    return (name + " " + str(line or "")).strip()


def geocode_stations(pairs, kakao_key):
    """역 이름 → 좌표. 한 번 찾은 역은 data/station_coords.json 에 저장해 두고 다시 찾지 않음."""
    cache = {}
    if os.path.exists(COORDS_FILE):
        with open(COORDS_FILE, encoding="utf-8") as f:
            cache = json.load(f)
    missing = [p for p in pairs if f"{p[0]}|{p[1]}" not in cache]
    if missing and not kakao_key:
        log(f"KAKAO_REST_KEY 가 없어서 역 {len(missing)}곳의 좌표를 찾지 못했어요.")
    elif missing:
        for stn, line in missing:
            for q in (station_query(stn, line), station_query(stn, "")):
                url = "https://dapi.kakao.com/v2/local/search/keyword.json?" + urllib.parse.urlencode(
                    {"query": q, "category_group_code": "SW8", "size": 1})
                try:
                    r = http_json(url, headers={"Authorization": "KakaoAK " + kakao_key}, secrets=(kakao_key,))
                except RuntimeError as e:
                    log(f"  좌표 찾기 실패 {q}: {e}")
                    break
                docs = r.get("documents") or []
                if docs:
                    cache[f"{stn}|{line}"] = [round(float(docs[0]["y"]), 6), round(float(docs[0]["x"]), 6), docs[0].get("place_name", "")]
                    break
            time.sleep(0.15)
        with open(COORDS_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=0, sort_keys=True)
    return cache


def gate_of(v):
    v = str(v or "").strip()
    if not v:
        return ""
    if v in ("내", "안", "IN", "in", "I", "1") or "내부" in v or "안" in v or v.startswith("내"):
        return "in"
    if v in ("외", "밖", "OUT", "out", "O", "2") or "외부" in v or "밖" in v or v.startswith("외"):
        return "out"
    return ""


# 물품 항목 (이름으로 뜻이 분명한 것만 표시)
SUBWAY_ITEMS = [
    ("dprSwchbrdCnt", "기저귀 교환대"), ("mwovenEn", "전자레인지"), ("wshstndEn", "세면대"),
    ("rfgrEn", "냉장고"), ("portEn", "전기포트"), ("infntBedEn", "아기 침대"),
    ("sofaCnt", "소파"), ("nrspcLockEn", "문 잠금"), ("acEn", "에어컨"), ("htrEn", "난방기"),
]


def fmt_tel(t):
    t = re.sub(r"\D", "", str(t or ""))
    if t.startswith("02") and len(t) in (9, 10):
        return f"02-{t[2:-4]}-{t[-4:]}"
    if len(t) in (10, 11):
        return f"{t[:3]}-{t[3:-4]}-{t[-4:]}"
    return t


def fmt_floor(f):
    f = str(f or "").strip().upper()
    m = re.fullmatch(r"B(\d+)", f)
    if m:
        return f"지하 {m.group(1)}층"
    m = re.fullmatch(r"(\d+)F?", f)
    if m:
        return f"지상 {m.group(1)}층"
    return f


def has_item(d, k):
    v = d.get(k)
    if isinstance(v, (int, float)):
        return v > 0
    return str(v).strip().upper() == "Y"


def norm_subway(d, coords, idx):
    stn = str(pick(d, "stnNm")).strip()
    line = str(pick(d, "lineNm")).strip()
    c = coords.get(f"{stn}|{line}")
    if not c:
        return None
    gate_raw = pick(d, "gateInoutSe", "gateInoutSeNm")
    gate = gate_of(gate_raw)
    floor = fmt_floor(pick(d, "stnFlr"))
    exitno = str(pick(d, "exitNo", "vcntEntrcNo")).strip()
    detail = re.sub(r"^\s*B?\d+\s*(층)?\s*", "", str(pick(d, "dtlPstn", "instlPlcDtlPstn")).strip())
    detail = re.sub(r"\s+", " ", detail)
    bits = []
    if gate == "in":
        bits.append("개찰구 안")
    elif gate == "out":
        bits.append("개찰구 밖")
    if floor:
        bits.append(floor)
    if detail:
        bits.append(detail)
    if exitno:
        bits.append(re.sub(r"\s*,\s*", "·", exitno) + "번 출구 쪽" if re.fullmatch(r"[\d,\s\-]+", exitno) else exitno)
    stn_name = re.sub(r"\(.*?\)", "", stn).strip()
    if not stn_name.endswith("역"):
        stn_name += "역"
    kind = str(pick(d, "fcltSeNm")).strip()
    typ = "family" if "가족" in kind else "breast" if ("모유" in kind or "착유" in kind) else ""
    how = str(pick(d, "nrsrmUtztnMth")).strip()
    how = {"1인가족 단독사용": "한 가족씩 따로 써요"}.get(how, how)
    hours = str(pick(d, "utztnHr", "utztnPsbltyHr")).strip()
    hours = {"영업시간내": "지하철 운행 시간 동안"}.get(hours, hours)
    items = [name for k, name in SUBWAY_ITEMS if has_item(d, k)]
    return {
        "id": "m" + str(pick(d, "stnCd") or stn) + ("f" if typ == "family" else "b" if typ == "breast" else ""),
        "kind": "subway",
        "name": f"{stn_name} 수유실" + (f" ({line})" if line else ""),
        "type": typ,
        "father": True if typ == "family" else False if typ == "breast" else None,
        "gate": gate,
        "addr": f"서울 지하철 {line} {stn_name}".strip(),
        "place": " · ".join(bits),
        "tel": fmt_tel(pick(d, "operInstTelno")),
        "hours": hours,
        "how": how,
        "items": items,
        "lat": c[0],
        "lng": c[1],
        "region": "서울 " + stn_name,
        "src": "seoulmetro",
    }


def update_subway(key, kakao_key):
    if "%" in key:  # 'Encoding' 키를 넣었으면 원래 글자로 되돌림 (주소에 넣을 때 다시 인코딩됨)
        key = urllib.parse.unquote(key)
    op, rows, resp, tried = fetch_subway_rows(key)
    if not rows:
        log("지하철 수유실 자료를 받지 못했어요.")
        write_debug([], {}, "subway_sample.json", {"tried": tried})
        return None
    # 수유실이 아닌 시설이 섞여 오면 거르기
    fcl = [r for r in rows if "수유" in str(pick(r, "fcltNm")) + str(pick(r, "fcltSeNm")) or not (pick(r, "fcltNm") or pick(r, "fcltSeNm"))]
    pairs = sorted({(str(pick(r, "stnNm")).strip(), str(pick(r, "lineNm")).strip()) for r in fcl})
    coords = geocode_stations(pairs, kakao_key)
    out = []
    for i, r in enumerate(fcl):
        n = norm_subway(r, coords, i)
        if n:
            base, k = n["id"], 2
            while any(x["id"] == n["id"] for x in out):   # 같은 역에 같은 종류가 또 있으면 번호 붙이기
                n["id"] = f"{base}-{k}"; k += 1
            out.append(n)
    gates = sorted({str(pick(r, "gateInoutSe")) for r in fcl})
    write_debug(fcl, resp, "subway_sample.json", {"operation": op, "tried": tried, "gate_values": gates,
                                                   "stations": len(pairs), "with_coords": len(out)})
    log(f"지하철 수유실 {len(fcl)}건 중 좌표 붙은 곳 {len(out)}곳 (역 {len(pairs)}곳, 개찰구 값 {gates})")
    return out


# ---------------------------------------------------------------- 알리미
def update_sooyusil(key):
    try:
        rows, resp = fetch_sooyusil(key)
    except RuntimeError as e:
        log("알리미 자료를 받지 못했어요:", e)
        return None
    write_debug(rows, resp)
    rooms, skipped, seen = [], 0, set()
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
        return None
    return sorted(rooms, key=lambda x: x["id"])


def main():
    old = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            old = json.load(f)
    old_rooms = old.get("rooms", [])
    by_src = lambda src: [x for x in old_rooms if x.get("src") == src]

    sooyusil_key = os.environ.get("SOOYUSIL_KEY", "").strip()
    data_key = os.environ.get("DATA_GO_KR_KEY", "").strip()
    kakao_key = os.environ.get("KAKAO_REST_KEY", "").strip()

    # 각 자료는 새로 받지 못하면 예전 것을 그대로 씀
    soo = update_sooyusil(sooyusil_key) if sooyusil_key else None
    if soo is None:
        soo = by_src("sooyusil")
        if not sooyusil_key:
            log("SOOYUSIL_KEY 가 없어서 알리미 자료는 그대로 둡니다.")
    sub = update_subway(data_key, kakao_key) if data_key else None
    if sub is None:
        sub = by_src("seoulmetro")
        if not data_key:
            log("DATA_GO_KR_KEY 가 없어서 지하철 자료는 그대로 둡니다.")

    real = bool(soo)
    rooms = list(soo) + list(sub)
    if not real:
        # 알리미 자료가 오기 전: 견본은 남기되, 진짜 지하철 자료가 있으면 견본 지하철은 뺌
        rooms = [x for x in old_rooms if x.get("src") == "sample" and not (sub and x.get("kind") == "subway")] + rooms
    toilets = [x for x in old.get("toilets", []) if (x.get("src") != "sample" or not real)]

    if sub and not real:
        note = "서울 지하철역 수유실은 실제 자료예요. 다른 곳은 아직 견본 데이터 몇 곳만 있어요."
    elif not real:
        note = "지금은 견본 데이터 몇 곳만 보여요. 수유정보 알리미 키가 오면 전국 수유실로 바뀌어요."
    else:
        note = ""
    data = {
        "version": 1,
        "updated": datetime.datetime.now(KST).strftime("%Y-%m-%d"),
        "sample": not real,
        "note": note,
        "counts": {"sooyusil": len(soo), "subway": len(sub), "toilets": len(toilets)},
        "rooms": rooms,
        "toilets": toilets,
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))
    log("저장 완료:", OUT, data["counts"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
data/build_html_db.py
======================
fetch_pokeapi.py로 수집한 JSON 파일들을
pokemon_battle_v3.html 안의 POKE_DB에 자동으로 통합합니다.

사용법:
    python data/build_html_db.py
    python data/build_html_db.py --html pokemon_battle_v3.html
    python data/build_html_db.py --min-moves 4   # 기술 4개 이상인 포켓몬만

출력:
    pokemon_battle_v3.html  (원본 덮어쓰기)
    pokemon_battle_v3.html.bak  (백업)

[변경사항]
  - abilities 배열 전체를 POKE_DB에 포함 (히든특성 포함)
  - 기술 effect 전체 타입 지원 (boost_*/drop_* 포함)
  - moves.json의 status 기술도 effect/special 있으면 포함
  - priority 필드 포함
"""

import argparse
import json
import os
import re
import shutil

# PokéAPI가 잘못 반환하는 한국어 이름 수정 테이블 (실제 게임 이름으로 교정)
MOVE_NAME_FIXES = {
    'slack-off':   '게으름피우기',   # PokéAPI: '태만함' (특성명 오류)
    'double-slap': '연속따귀',       # PokéAPI: '연속뺨치기'
    'roost':       '날개쉬기',       # PokéAPI: '깃털휴식'
}

from pathlib import Path

ROOT      = Path(__file__).parent.parent   # pokemon_rl/
DATA_DIR  = ROOT / "data"


# ══════════════════════════════════════════
# 타입 → CSS 클래스 매핑 (HTML에서 쓰는 것과 일치)
# ══════════════════════════════════════════
VALID_TYPES = {
    "normal","fire","water","electric","grass","ice","fighting","poison",
    "ground","flying","psychic","bug","rock","ghost","dragon","dark","steel","fairy",
}


# ══════════════════════════════════════════
# 기술 효과 태그 → HTML 특수 플래그 변환
# ══════════════════════════════════════════
EFFECT_TO_JS = {
    # 상태이상
    "burn":       "burn",
    "paralysis":  "paralyze",
    "sleep":      "sleep",
    "freeze":     "freeze",
    "poison":     "poison",
    "bad-poison": "toxic",
    "confusion":  "confuse",
    "flinch":     "flinch",
    "yawn":       "yawn",
    # 특수
    "leech-seed": "leech_seed",
    "trap":       "trap",
    "ingrain":    "ingrain",
}

# boost_*/drop_* 스탯 이름 매핑
STAT_MAP = {
    "attack":     "atk",
    "defense":    "def",
    "sp_attack":  "spa",
    "sp_defense": "spd",
    "speed":      "spe",
    "evasion":    "eva",
    "accuracy":   "acc",
}


def effect_fields(mv: dict) -> dict:
    """기술 딕셔너리 → JS move 객체에 들어갈 extra 필드"""
    extra = {}
    eff    = mv.get("effect", "")
    chance = mv.get("effect_chance", 0)
    special = mv.get("special", "")
    priority = mv.get("priority", 0)

    if special:
        extra["special"] = special

    if priority != 0:
        extra["priority"] = priority

    if eff in EFFECT_TO_JS:
        extra["effect"] = EFFECT_TO_JS[eff]
        # effectChance: 0이면 변화기(확정), 양수면 확률(%)
        extra["effectChance"] = chance

    elif eff.startswith("boost_") or eff.startswith("drop_"):
        # boost_attack_2 → effect:'atk_up2', effectChance:0
        parts = eff.split("_")  # ['boost','attack','2'] or ['drop','sp','attack','2']
        direction = parts[0]    # boost / drop

        # 스탯 이름: 숫자 제외한 나머지
        # e.g. boost_sp_attack_1 → ['boost','sp','attack','1']
        magnitude = int(parts[-1]) if parts[-1].isdigit() else 1
        stat_raw = "_".join(parts[1:-1])  # sp_attack, defense, ...
        stat_short = STAT_MAP.get(stat_raw, stat_raw)

        if direction == "boost":
            extra["effect"] = f"{stat_short}_up{magnitude}"
        else:
            extra["effect"] = f"{stat_short}_down{magnitude}"
        extra["effectChance"] = chance

    return extra


def build_move_obj(mv: dict, methods: list = None) -> dict:
    """moves.json 항목 → HTML JS 객체"""
    name_ko = MOVE_NAME_FIXES.get(mv.get("name_en", ""), mv["name_ko"])
    obj = {
        "name":    name_ko,
        "nameEn":  mv.get("name_en", ""),   # 영어 원본 (RECOIL/PIVOT/COMPOUND 매칭용)
        "type":    mv["type"],
        "cat":     mv["category"],
        "power":   mv["power"],
        "acc":     mv["accuracy"],
        "pp":      mv["pp"],
        "maxPp":   mv["pp"],
    }
    obj.update(effect_fields(mv))
    # 학습 방법 (egg=교배기, machine=TM, level-up=레벨업, tutor=가르치기)
    if methods:
        if "egg" in methods:
            obj["learnMethod"] = "egg"
        elif "level-up" in methods:
            obj["learnMethod"] = "level-up"
        elif "machine" in methods:
            obj["learnMethod"] = "machine"
        elif "tutor" in methods:
            obj["learnMethod"] = "tutor"
    return obj


def build_poke_js(p: dict, moves_db: dict, min_moves: int = 2) -> str | None:
    """pokemon.json 항목 → JS POKE_DB 항목 문자열"""

    # 스탯 필드 확인
    stats = p.get("stats", {})
    required = ["hp","attack","defense","sp_attack","sp_defense","speed"]
    if not all(k in stats for k in required):
        return None

    # 타입 필터
    types = [t for t in p.get("types", []) if t in VALID_TYPES]
    if not types:
        return None

    # 기술 목록 구성 (더 넓은 포함 기준)
    move_objs = []
    for mname in p.get("moves", []):
        if mname not in moves_db:
            continue
        mv = moves_db[mname]
        # 포함 기준:
        #   1) 위력 있는 기술
        #   2) 상태이상/효과 있는 기술 (수면가루, 전자파 등)
        #   3) special 플래그 있는 기술 (하품, 역린 등)
        #   4) boost_*/drop_* 효과 있는 변화기 (칼춤, 드래곤댄스 등)
        has_power    = mv["power"] > 0
        has_effect   = bool(mv.get("effect"))
        has_special  = bool(mv.get("special"))
        has_priority = mv.get("priority", 0) != 0

        # 포함 기준:
        #   1) 위력 있는 공격기
        #   2) 부가효과 있는 기술 (상태이상, 스탯 변화 등)
        #   3) special 플래그 있는 기술 (회복, 방어, 역린 등)
        #   4) priority != 0 인 기술 (방어/선제공격/느린기술)
        #   5) 배틀에서 중요한 변화기 화이트리스트
        MOVE_WHITELIST = {
            # 회복기
            "recover", "roost", "rest", "moonlight", "morning-sun", "synthesis",
            "slack-off", "soft-boiled", "milk-drink", "wish", "heal-order",
            "floral-healing", "jungle-healing", "healing-wish", "lunar-dance",
            "life-dew", "shore-up", "aqua-ring",
            # 스크린/베일
            "reflect", "light-screen", "aurora-veil",
            # 해저드
            "stealth-rock", "spikes", "toxic-spikes", "sticky-web",
            # 날씨
            "rain-dance", "sunny-day", "sandstorm", "hail", "snowscape",
            # 필드
            "electric-terrain", "grassy-terrain", "misty-terrain", "psychic-terrain",
            # 강제교체
            "roar", "whirlwind", "dragon-tail", "circle-throw",
            # 유틸
            "substitute", "encore", "taunt", "trick-room", "tailwind", "haze",
            "baton-pass", "u-turn", "volt-switch", "flip-turn",
            "belly-drum", "defog", "magic-coat", "magic-room", "wonder-room",
            # 가변 위력기 (HP 비례, power=0으로 저장됨)
            "flail", "reversal", "wring-out", "crush-grip", "final-gambit",
            # 반격기
            "counter", "mirror-coat", "metal-burst",
            # 기타 실전기
            "endeavor", "pain-split", "destiny-bond", "perish-song",
            "mean-look", "spider-web", "block",
            "trick", "switcheroo", "skill-swap", "role-play",
            "topsy-turvy", "soak", "gravity", "imprison",
            # 추가 배틀 유효 기술
            "after-you",             "aromatherapy",             "assist",             "bestow",             "camouflage",             "copycat",             "curse",             "destiny-bond",             "dragon-rage",             "electro-ball",             "entrainment",             "fissure",             "fling",             "focus-energy",             "forests-curse",             "gastro-acid",             "grass-knot",             "grudge",             "guard-split",             "guard-swap",             "guillotine",             "gyro-ball",             "heal-bell",             "heal-pulse",             "heart-swap",             "heat-crash",             "heavy-slam",             "horn-drill",             "instruct",             "laser-focus",             "low-kick",             "lucky-chant",             "magic-powder",             "magnet-rise",             "mat-block",             "me-first",             "metronome",             "mirror-move",             "mist",             "mud-sport",             "natural-gift",             "natures-madness",             "night-shade",             "octolock",             "perish-song",             "power-split",             "power-swap",             "power-trick",             "psych-up",             "psywave",             "purify",             "quash",             "recycle",             "reflect-type",             "refresh",             "safeguard",             "seismic-toss",             "shed-tail",             "sheer-cold",             "simple-beam",             "sleep-talk",             "snore",             "soak",             "sonic-boom",             "speed-swap",             "spit-up",             "spite",             "stockpile",             "super-fang",             "swallow",             "switcheroo",             "telekinesis",             "transform",             "trick",             "trick-or-treat",             "water-sport",             "worry-seed",
            # 신규 추가 기술
            "coaching", "dragon-cheer", "corrosive-gas", "chilly-reception",
            "grass-pledge", "fire-pledge", "water-pledge",
        }
        if not (has_power or has_effect or has_special or has_priority or mname in MOVE_WHITELIST):
            continue
        move_methods_for_move = p.get("move_methods", {}).get(mname, [])
        move_objs.append(build_move_obj(mv, methods=move_methods_for_move))

    if len(move_objs) < min_moves:
        return None

    # ── 특성 처리 ─────────────────────────────────────────
    # abilities 배열 전체 수집 (비히든 먼저, 히든 뒤에)
    abilities_list = p.get("abilities", [])
    non_hidden = [ab["name"] for ab in abilities_list if not ab.get("hidden")]
    hidden     = [ab["name"] for ab in abilities_list if ab.get("hidden")]
    all_abilities = non_hidden + hidden  # 비히든 먼저

    # ability 단일값 (첫 비히든, 없으면 첫 번째)
    ability_name = all_abilities[0] if all_abilities else ""

    # 이름 (한국어 우선)
    name = p.get("name_ko") or p.get("name_en", "???")

    # ── JS 객체 문자열 생성 ───────────────────────────────
    # abilities 배열을 JS 배열로 직렬화
    abilities_js = json.dumps(all_abilities, ensure_ascii=False)

    line1 = (
        f"  {{id:{p['id']},name:{json.dumps(name, ensure_ascii=False)},"
        f"types:{json.dumps(types)},"
        f"hp:{stats['hp']},atk:{stats['attack']},def:{stats['defense']},"
        f"spa:{stats['sp_attack']},spd:{stats['sp_defense']},spe:{stats['speed']},"
        f"ability:{json.dumps(ability_name, ensure_ascii=False)},"
        f"abilities:{abilities_js},"
    )

    move_strs = []
    for m in move_objs:
        parts = [
            f"name:{json.dumps(m['name'], ensure_ascii=False)}",
            f"type:{json.dumps(m['type'])}",
            f"cat:{json.dumps(m['cat'])}",
            f"power:{m['power']}",
            f"acc:{m['acc']}",
            f"pp:{m['pp']}",
            f"maxPp:{m['pp']}",
        ]
        for k in ("nameEn", "effect", "effectChance", "special", "priority", "learnMethod"):
            if k in m:
                val = m[k]
                if isinstance(val, str):
                    parts.append(f"{k}:{json.dumps(val)}")
                else:
                    parts.append(f"{k}:{val}")
        move_strs.append("{" + ",".join(parts) + "}")

    line2 = f"   allMoves:[{','.join(move_strs)}]}}"
    return line1 + "\n" + line2


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--html",       type=str, default="pokemon_battle_v3.html",
                        help="수정할 HTML 파일 경로 (기본: pokemon_battle_v3.html)")
    parser.add_argument("--min-moves",  type=int, default=3,
                        help="포함할 최소 기술 수 (기본: 3)")
    parser.add_argument("--gen",        type=str, default="all",
                        help="포함할 세대: 1 / 1-3 / all (기본: all)")
    args = parser.parse_args()

    # ── 파일 로드 ────────────────────────────────────────
    poke_path  = DATA_DIR / "pokemon.json"
    moves_path = DATA_DIR / "moves.json"
    items_path = DATA_DIR / "items.json"
    html_path  = ROOT / args.html

    if not poke_path.exists():
        print(f"[오류] {poke_path} 없음. 먼저 fetch_pokeapi.py 실행하세요.")
        return
    if not moves_path.exists():
        print(f"[오류] {moves_path} 없음. 먼저 fetch_pokeapi.py 실행하세요.")
        return
    if not html_path.exists():
        print(f"[오류] {html_path} 없음.")
        return

    print(f"[로드] pokemon.json ...")
    with open(poke_path, encoding="utf-8") as f:
        pokemon_raw: dict = json.load(f)

    print(f"[로드] moves.json ...")
    with open(moves_path, encoding="utf-8") as f:
        moves_db: dict = json.load(f)

    items_db = {}
    if items_path.exists():
        print(f"[로드] items.json ...")
        with open(items_path, encoding="utf-8") as f:
            items_db = json.load(f)

    # ── 세대 필터 ────────────────────────────────────────
    GEN_RANGES = {
        1:(1,151), 2:(152,251), 3:(252,386), 4:(387,493),
        5:(494,649), 6:(650,721), 7:(722,809), 8:(810,905), 9:(906,1025),
    }
    def in_gen(pid):
        if args.gen == "all":
            return True
        if "-" in args.gen:
            g1, g2 = args.gen.split("-")
            for g in range(int(g1), int(g2)+1):
                s, e = GEN_RANGES.get(g, (0,0))
                if s <= pid <= e:
                    return True
            return False
        g = int(args.gen)
        s, e = GEN_RANGES.get(g, (0,0))
        return s <= pid <= e

    # ── POKE_DB 빌드 ─────────────────────────────────────
    print(f"\n[빌드] POKE_DB 구성 중...")
    poke_entries = []
    skipped = 0

    sorted_pokes = sorted(pokemon_raw.values(), key=lambda p: p.get("id", 9999))

    for p in sorted_pokes:
        pid = p.get("id", 0)
        if not in_gen(pid):
            continue
        js = build_poke_js(p, moves_db, args.min_moves)
        if js:
            poke_entries.append(js)
        else:
            skipped += 1

    print(f"  포함: {len(poke_entries)}마리  |  스킵 (기술 부족 등): {skipped}마리")

    new_db_str = "const POKE_DB=[\n" + ",\n".join(poke_entries) + "\n];"

    # ── 도구 목록 빌드 ───────────────────────────────────
    item_lines = []
    for k, item in items_db.items():
        item_lines.append(
            f"  {json.dumps(k)}: {{"
            f"name:{json.dumps(item['name_ko'], ensure_ascii=False)},"
            f"effect:{json.dumps(item.get('effect','')[:60])}"
            f"}}"
        )
    new_items_str = "const ITEMS_DB={\n" + ",\n".join(item_lines) + "\n};"

    # ── HTML 백업 ────────────────────────────────────────
    bak_path = str(html_path) + ".bak"
    shutil.copy(str(html_path), bak_path)
    print(f"\n[백업] {bak_path}")

    # ── HTML 수정 ────────────────────────────────────────
    with open(html_path, encoding="utf-8") as f:
        html = f.read()

    # POKE_DB 교체
    poke_pattern = re.compile(r'const POKE_DB\s*=\s*\[.*?\];', re.DOTALL)
    m = poke_pattern.search(html)
    if m:
        html = html[:m.start()] + new_db_str + html[m.end():]
        print(f"[수정] POKE_DB 교체 완료 ({len(poke_entries)}마리)")
    else:
        html = html.replace("</script>", new_db_str + "\n</script>", 1)
        print(f"[삽입] POKE_DB 추가 완료 ({len(poke_entries)}마리)")

    # ITEMS_DB 교체
    items_pattern = re.compile(r'const ITEMS_DB\s*=\s*\{.*?\};', re.DOTALL)
    m2 = items_pattern.search(html)
    if m2:
        html = html[:m2.start()] + new_items_str + html[m2.end():]
        print(f"[수정] ITEMS_DB 교체 완료 ({len(items_db)}개)")
    elif items_db:
        html = html.replace(new_db_str, new_db_str + "\n" + new_items_str)
        print(f"[삽입] ITEMS_DB 추가 완료 ({len(items_db)}개)")

    # GEN_RANGES 업데이트
    gen_js = (
        "const GEN_RANGES=[[1,151],[152,251],[252,386],[387,493],"
        "[494,649],[650,721],[722,809],[810,905],[906,1025]];"
    )
    gen_pattern = re.compile(r'const GEN_RANGES\s*=\s*\[.*?\];', re.DOTALL)
    if gen_pattern.search(html):
        m3 = gen_pattern.search(html)
        if m3:
            html = html[:m3.start()] + gen_js + html[m3.end():]
        print(f"[수정] GEN_RANGES 9세대로 업데이트")

    # 세대 필터 버튼 추가 (5~9세대)
    old_gen_btns = '<button class="gen-btn" onclick="setGen(4,this)">4세대</button>'
    new_gen_btns = (
        '<button class="gen-btn" onclick="setGen(4,this)">4세대</button>\n'
        '      <button class="gen-btn" onclick="setGen(5,this)">5세대</button>\n'
        '      <button class="gen-btn" onclick="setGen(6,this)">6세대</button>\n'
        '      <button class="gen-btn" onclick="setGen(7,this)">7세대</button>\n'
        '      <button class="gen-btn" onclick="setGen(8,this)">8세대</button>\n'
        '      <button class="gen-btn" onclick="setGen(9,this)">9세대</button>'
    )
    if old_gen_btns in html and "5세대" not in html:
        html = html.replace(old_gen_btns, new_gen_btns)
        print(f"[수정] 세대 필터 버튼 5~9세대 추가")

    # 도구 선택 옵션 업데이트
    if items_db:
        item_options = ""
        for k, item in items_db.items():
            item_options += f'<option value="{k}">{item["name_ko"]}</option>\n'
        item_sel_pattern = re.compile(
            r'<select class="build-sel" id="itemSelect">.*?</select>',
            re.DOTALL
        )
        new_item_sel = (
            '<select class="build-sel" id="itemSelect">\n'
            '<option value="">없음</option>\n' +
            item_options +
            '</select>'
        )
        m4 = item_sel_pattern.search(html)
        if m4:
            html = html[:m4.start()] + new_item_sel + html[m4.end():]
            print(f"[수정] 도구 선택 옵션 {len(items_db)}개로 업데이트")

    # ── 저장 ─────────────────────────────────────────────
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"\n{'='*50}")
    print(f" ✅ HTML 업데이트 완료!")
    print(f"   파일: {html_path}")
    print(f"   포켓몬: {len(poke_entries)}마리")
    print(f"   도구:   {len(items_db)}개")
    print(f"   백업:   {bak_path}")
    print(f"{'='*50}\n")
    print(f" 이제 run.bat 실행하면 전 세대 포켓몬으로 플레이 가능합니다!")


if __name__ == "__main__":
    main()

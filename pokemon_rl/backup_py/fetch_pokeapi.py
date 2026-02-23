"""
data/fetch_pokeapi.py — PokéAPI로 전 세대 포켓몬 데이터 자동 수집

사용법:
  python data/fetch_pokeapi.py --gen 1        # 1세대만
  python data/fetch_pokeapi.py --gen all      # 전 세대 (시간 오래 걸림)
  python data/fetch_pokeapi.py --gen 1-3      # 1~3세대

출력:
  data/pokemon.json   — 포켓몬 스탯/타입/기술 목록
  data/moves.json     — 기술 상세 데이터
"""
import json
import os
import time
import argparse
import urllib.request
import urllib.error
from typing import Optional

# PokéAPI 기본 URL
BASE_URL = "https://pokeapi.co/api/v2"

# 세대별 포켓몬 번호 범위
GEN_RANGES = {
    1: (1, 151),
    2: (152, 251),
    3: (252, 386),
    4: (387, 493),
    5: (494, 649),
    6: (650, 721),
    7: (722, 809),
    8: (810, 905),
    9: (906, 1025),
}

# 배틀에서 쓸만한 기술 필터 (위력 또는 상태이상)
USABLE_MOVE_CATEGORIES = {"physical", "special", "status"}

# 한국어 이름 매핑 (주요 포켓몬)
KO_NAMES = {
    "bulbasaur": "이상해씨", "ivysaur": "이상해풀", "venusaur": "이상해꽃",
    "charmander": "파이리", "charmeleon": "리자드", "charizard": "리자몽",
    "squirtle": "꼬부기", "wartortle": "어니부기", "blastoise": "거북왕",
    "pikachu": "피카츄", "raichu": "라이츄", "mewtwo": "뮤츠", "mew": "뮤",
    "gengar": "팬텀", "alakazam": "후딘", "machamp": "괴력몬",
    "lapras": "라프라스", "snorlax": "잠만보", "dragonite": "망나뇽",
    "typhlosion": "블레이즈", "feraligatr": "엘리게이트", "meganium": "메가니움",
    "togekiss": "토게키스", "lucario": "루카리오", "garchomp": "한카리아스",
    "sylveon": "님피아", "goodra": "미끄래곤", "greninja": "개굴닌자",
    "aegislash": "기사검신", "mimikyu": "미미큐", "toxapex": "독파리",
}


def fetch_json(url: str, retries: int = 3) -> Optional[dict]:
    """URL에서 JSON 데이터 가져오기 (재시도 지원)"""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PokemonRL/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(1 * (attempt + 1))
        except Exception:
            time.sleep(1 * (attempt + 1))
    return None


def parse_stats(stat_list: list) -> dict:
    """스탯 리스트 → 딕셔너리"""
    stat_map = {
        "hp": "hp",
        "attack": "attack",
        "defense": "defense",
        "special-attack": "sp_attack",
        "special-defense": "sp_defense",
        "speed": "speed",
    }
    result = {}
    for entry in stat_list:
        key = entry["stat"]["name"]
        if key in stat_map:
            result[stat_map[key]] = entry["base_stat"]
    return result


def fetch_pokemon(pokemon_id: int) -> Optional[dict]:
    """단일 포켓몬 데이터 수집"""
    data = fetch_json(f"{BASE_URL}/pokemon/{pokemon_id}")
    if not data:
        return None

    name_en = data["name"]
    name_ko = KO_NAMES.get(name_en, name_en)

    types = [t["type"]["name"] for t in data["types"]]
    stats = parse_stats(data["stats"])

    # 배틀 가능한 기술 목록 (레벨업 기술만)
    level_moves = []
    for m in data["moves"]:
        for vg in m["version_group_details"]:
            if vg["move_learn_method"]["name"] == "level-up":
                level_moves.append({
                    "name": m["move"]["name"],
                    "level": vg["level_learned_at"],
                })
                break

    # 레벨순 정렬, 상위 20개
    level_moves.sort(key=lambda x: x["level"])
    move_names = [m["name"] for m in level_moves[-20:]]

    # 스프라이트 URL
    sprites = data.get("sprites", {})
    sprite_url = sprites.get("front_default", "")

    return {
        "id": pokemon_id,
        "name_en": name_en,
        "name_ko": name_ko,
        "types": types,
        "stats": stats,
        "moves": move_names,
        "sprite": sprite_url,
    }


def fetch_move(move_name: str) -> Optional[dict]:
    """단일 기술 데이터 수집"""
    data = fetch_json(f"{BASE_URL}/move/{move_name}")
    if not data:
        return None

    category = data.get("damage_class", {}).get("name", "status")
    type_ = data.get("type", {}).get("name", "normal")
    power = data.get("power") or 0
    accuracy = data.get("accuracy") or 0
    pp = data.get("pp") or 5

    # 부가효과 파싱
    effect_entries = data.get("effect_entries", [])
    effect_text = ""
    for e in effect_entries:
        if e.get("language", {}).get("name") == "en":
            effect_text = e.get("short_effect", "")
            break

    # 우선도
    priority = data.get("priority", 0)

    # 간단한 effect 분류
    effect_tag = ""
    effect_chance = data.get("effect_chance") or 0
    meta = data.get("meta", {})
    if meta:
        ailment = meta.get("ailment", {}).get("name", "none")
        if ailment != "none" and ailment != "unknown":
            effect_tag = ailment  # "burn", "paralysis", "sleep" 등
        stat_changes = data.get("stat_changes", [])
        if stat_changes:
            sc = stat_changes[0]
            stat_name = sc["stat"]["name"].replace("-", "_").replace("special_attack", "sp_attack").replace("special_defense", "sp_defense")
            change = sc["change"]
            effect_tag = f"boost_{stat_name}_{abs(change)}" if change > 0 else f"drop_{stat_name}_{abs(change)}"

    return {
        "name": move_name,
        "type": type_,
        "category": category,
        "power": power,
        "accuracy": accuracy,
        "pp": pp,
        "priority": priority,
        "effect": effect_tag,
        "effect_chance": effect_chance,
        "description": effect_text[:100],
    }


def get_gen_range(gen_str: str) -> list[int]:
    """세대 문자열 → 포켓몬 ID 목록"""
    if gen_str == "all":
        ids = []
        for start, end in GEN_RANGES.values():
            ids.extend(range(start, end + 1))
        return ids
    elif "-" in gen_str:
        g1, g2 = gen_str.split("-")
        ids = []
        for g in range(int(g1), int(g2) + 1):
            if g in GEN_RANGES:
                start, end = GEN_RANGES[g]
                ids.extend(range(start, end + 1))
        return ids
    else:
        g = int(gen_str)
        if g in GEN_RANGES:
            start, end = GEN_RANGES[g]
            return list(range(start, end + 1))
    return list(range(1, 152))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--gen", type=str, default="1",
                        help="세대 선택: 1, 1-3, all")
    parser.add_argument("--output-dir", type=str, default="data",
                        help="출력 디렉토리")
    parser.add_argument("--delay", type=float, default=0.3,
                        help="API 요청 간격 (초) — 너무 빠르면 차단됨")
    args = parser.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    pokemon_ids = get_gen_range(args.gen)
    print(f"🔍 포켓몬 {len(pokemon_ids)}마리 수집 시작 (세대: {args.gen})")

    # ── 포켓몬 수집 ─────────────────────────────────────
    pokemon_db = {}
    all_move_names = set()

    for i, pid in enumerate(pokemon_ids):
        poke = fetch_pokemon(pid)
        if poke:
            pokemon_db[poke["name_en"]] = poke
            all_move_names.update(poke["moves"])
            print(f"  [{i+1:3d}/{len(pokemon_ids)}] #{pid:04d} {poke['name_ko']} ({poke['name_en']}) — {'/'.join(poke['types'])}")
        else:
            print(f"  [{i+1:3d}/{len(pokemon_ids)}] #{pid:04d} — 데이터 없음 (스킵)")

        time.sleep(args.delay)

    pokemon_path = os.path.join(args.output_dir, "pokemon.json")
    with open(pokemon_path, "w", encoding="utf-8") as f:
        json.dump(pokemon_db, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 포켓몬 데이터 저장: {pokemon_path} ({len(pokemon_db)}마리)")

    # ── 기술 수집 ────────────────────────────────────────
    print(f"\n🔍 기술 {len(all_move_names)}개 수집 시작...")
    moves_db = {}
    move_list = sorted(all_move_names)

    for i, move_name in enumerate(move_list):
        move = fetch_move(move_name)
        if move:
            moves_db[move_name] = move
            cat = move["category"][0].upper()
            print(f"  [{i+1:4d}/{len(move_list)}] {move_name:25s} [{cat}] 위력:{move['power']:3d}")
        time.sleep(args.delay)

    moves_path = os.path.join(args.output_dir, "moves.json")
    with open(moves_path, "w", encoding="utf-8") as f:
        json.dump(moves_db, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 기술 데이터 저장: {moves_path} ({len(moves_db)}개)")

    print(f"\n🎉 데이터 수집 완료!")
    print(f"   포켓몬: {len(pokemon_db)}마리")
    print(f"   기술:   {len(moves_db)}개")
    print(f"\n다음 단계: python train.py --use-pokeapi-data")


if __name__ == "__main__":
    main()

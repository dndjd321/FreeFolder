"""
env/weather.py — 날씨 & 지형 효과 시스템

날씨 종류:
  rain        비 — 물 1.5배, 불꽃 0.5배, 썬더 명중 100%
  sun         쾌청 — 불꽃 1.5배, 물 0.5배, 솔라빔 충전 없이
  sandstorm   모래바람 — 바위/강철/땅 이외 턴 피해, 바위타입 특방 1.5배
  hail        싸라기눈 — 얼음 이외 턴 피해, 블리자드 명중 100%
  snow        눈 (9세대) — 얼음타입 방어 1.5배
  none        맑음

지형 종류 (10세대 미만):
  electric    전기장 — 전기 기술 1.3배, 수면 방지
  grassy      풀밭 — 풀 기술 1.3배, 턴 종료 HP 회복
  misty       안개 — 상태이상 방지, 드래곤 기술 0.5배
  psychic     사이코 — 사이코 기술 1.3배, 우선도 기술 무효
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from env.pokemon import Pokemon

# ── 날씨 지속 턴 ────────────────────────────────────────
DEFAULT_WEATHER_TURNS = 5   # 일반 날씨
ITEM_WEATHER_TURNS = 8      # 날씨 아이템 (더존돌/적조구 등) 사용 시

# ── 날씨가 면제되는 타입 ─────────────────────────────────
SANDSTORM_IMMUNE_TYPES = {"rock", "steel", "ground"}
HAIL_IMMUNE_TYPES = {"ice"}
SNOW_IMMUNE_TYPES = {"ice"}


# ══════════════════════════════════════════════════════════
# 날씨 데미지 계산
# ══════════════════════════════════════════════════════════

def get_weather_damage(pokemon: "Pokemon", weather: str) -> int:
    """턴 종료 날씨 피해량 계산"""
    if weather == "sandstorm":
        if not any(t in SANDSTORM_IMMUNE_TYPES for t in pokemon.types):
            if not getattr(pokemon, "ability_name", "") in ("sand-rush", "sand-veil", "sand-force", "magic-guard", "overcoat"):
                return max(1, pokemon.max_hp // 16)
    elif weather == "hail":
        if not any(t in HAIL_IMMUNE_TYPES for t in pokemon.types):
            if not getattr(pokemon, "ability_name", "") in ("ice-body", "snow-cloak", "magic-guard", "overcoat"):
                return max(1, pokemon.max_hp // 16)
    return 0


def get_weather_move_modifier(weather: str, move_type: str) -> float:
    """날씨에 따른 기술 위력 보정"""
    modifiers = {
        "rain": {"water": 1.5, "fire": 0.5},
        "sun":  {"fire": 1.5, "water": 0.5},
        "sandstorm": {},
        "hail": {},
        "snow": {},
        "none": {},
    }
    return modifiers.get(weather, {}).get(move_type, 1.0)


def get_weather_accuracy_modifier(weather: str, move_name: str) -> float:
    """날씨에 따른 명중률 보정"""
    # 비: 썬더 명중 100%, 불꽃소용돌이 명중 50%
    if weather == "rain":
        if move_name in ("thunder", "hurricane"):
            return float("inf")  # 반드시 명중
        if move_name in ("solar-beam", "solar-blade"):
            return 0.5
    # 쾌청: 솔라빔 바로, 썬더/폭풍 명중 50%
    elif weather == "sun":
        if move_name in ("thunder", "hurricane"):
            return 0.5
    # 싸라기눈: 블리자드 반드시 명중
    elif weather == "hail":
        if move_name == "blizzard":
            return float("inf")
    return 1.0


def get_weather_stat_modifier(weather: str, pokemon: "Pokemon", stat: str) -> float:
    """날씨에 따른 스탯 보정 (모래바람 → 바위타입 특방 1.5배 등)"""
    if weather == "sandstorm" and stat == "sp_defense":
        if "rock" in pokemon.types:
            return 1.5
    elif weather == "snow" and stat == "defense":
        if "ice" in pokemon.types:
            return 1.5
    return 1.0


def apply_weather_end_of_turn(pokemon: "Pokemon", weather: str, log: list) -> int:
    """턴 종료 날씨 데미지 적용, 실제 피해량 반환"""
    damage = get_weather_damage(pokemon, weather)
    if damage > 0:
        pokemon.take_damage(damage)
        weather_names = {
            "sandstorm": "모래바람", "hail": "싸라기눈", "snow": "눈"
        }
        log.append(f"  {pokemon.name}은(는) {weather_names.get(weather, '날씨')} 피해를 입었다! (-{damage}HP)")
    return damage


# ══════════════════════════════════════════════════════════
# 지형 시스템
# ══════════════════════════════════════════════════════════

class Terrain:
    """배틀 지형"""
    name: str = "none"
    turns: int = 0

    def get_move_modifier(self, move_type: str, user: "Pokemon") -> float:
        return 1.0

    def prevents_status(self, status: str) -> bool:
        return False

    def end_of_turn_effect(self, pokemon: "Pokemon", log: list) -> int:
        return 0


class ElectricTerrain(Terrain):
    """전기장 — 땅 위 포켓몬 수면 방지, 전기 기술 1.3배"""
    name = "electric"

    def get_move_modifier(self, move_type, user):
        if move_type == "electric":
            return 1.3
        return 1.0

    def prevents_status(self, status):
        return status == "sleep"


class GrassyTerrain(Terrain):
    """풀밭 — 풀 기술 1.3배, 턴 종료 HP 1/16 회복"""
    name = "grassy"

    def get_move_modifier(self, move_type, user):
        if move_type == "grass":
            return 1.3
        # 지진/땅가르기/매그니튜드 0.5배
        if move_type == "ground":
            return 0.5
        return 1.0

    def end_of_turn_effect(self, pokemon, log):
        heal = max(1, pokemon.max_hp // 16)
        pokemon.heal(heal)
        log.append(f"  {pokemon.name}은(는) 풀밭의 효과로 HP를 {heal} 회복!")
        return heal


class MistyTerrain(Terrain):
    """안개 — 상태이상 방지, 드래곤 기술 0.5배"""
    name = "misty"

    def get_move_modifier(self, move_type, user):
        if move_type == "dragon":
            return 0.5
        return 1.0

    def prevents_status(self, status):
        return True  # 모든 상태이상 방지


class PsychicTerrain(Terrain):
    """사이코 — 사이코 기술 1.3배, 우선도 기술 무효"""
    name = "psychic"

    def get_move_modifier(self, move_type, user):
        if move_type == "psychic":
            return 1.3
        return 1.0


TERRAIN_REGISTRY = {
    "none": Terrain(),
    "electric": ElectricTerrain(),
    "grassy": GrassyTerrain(),
    "misty": MistyTerrain(),
    "psychic": PsychicTerrain(),
}


def get_terrain(name: str) -> Terrain:
    return TERRAIN_REGISTRY.get(name, Terrain())


# ══════════════════════════════════════════════════════════
# 날씨 전환 메시지
# ══════════════════════════════════════════════════════════

WEATHER_START_MSG = {
    "rain":      "비가 내리기 시작했다!",
    "sun":       "날씨가 맑아졌다!",
    "sandstorm": "모래바람이 불기 시작했다!",
    "hail":      "싸라기눈이 내리기 시작했다!",
    "snow":      "눈이 내리기 시작했다!",
    "none":      "날씨가 돌아왔다.",
}

WEATHER_CONTINUE_MSG = {
    "rain":      "비가 계속 내리고 있다.",
    "sun":       "날씨는 계속 맑다.",
    "sandstorm": "모래바람이 몰아치고 있다.",
    "hail":      "싸라기눈이 내리고 있다.",
    "snow":      "눈이 내리고 있다.",
}

WEATHER_END_MSG = {
    "rain":      "비가 그쳤다.",
    "sun":       "날씨가 흐려졌다.",
    "sandstorm": "모래바람이 잦아들었다.",
    "hail":      "싸라기눈이 그쳤다.",
    "snow":      "눈이 그쳤다.",
}

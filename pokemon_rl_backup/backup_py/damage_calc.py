"""
damage_calc.py — 포켓몬 데미지 계산 엔진 (8세대 기준)
공식: ((2*레벨/5+2) * 위력 * 공격/방어) / 50 + 2) * 보정
"""

import random

# 타입 상성표 (공격타입 → 방어타입 → 배율)
TYPE_CHART = {
    "normal":   {"rock":0.5,"ghost":0,"steel":0.5},
    "fire":     {"fire":0.5,"water":0.5,"rock":0.5,"dragon":0.5,"grass":2,"ice":2,"bug":2,"steel":2},
    "water":    {"water":0.5,"grass":0.5,"dragon":0.5,"fire":2,"ground":2,"rock":2},
    "electric": {"electric":0.5,"grass":0.5,"dragon":0.5,"ground":0,"flying":2,"water":2},
    "grass":    {"fire":0.5,"grass":0.5,"poison":0.5,"flying":0.5,"bug":0.5,"dragon":0.5,"steel":0.5,
                 "water":2,"ground":2,"rock":2},
    "ice":      {"water":0.5,"ice":0.5,"fire":0.5,"steel":0.5,"grass":2,"ground":2,"flying":2,"dragon":2},
    "fighting": {"normal":2,"ice":2,"rock":2,"dark":2,"steel":2,
                 "poison":0.5,"flying":0.5,"psychic":0.5,"bug":0.5,"fairy":0.5,"ghost":0},
    "poison":   {"grass":2,"fairy":2,"poison":0.5,"ground":0.5,"rock":0.5,"ghost":0.5,"steel":0},
    "ground":   {"fire":2,"electric":2,"poison":2,"rock":2,"steel":2,
                 "grass":0.5,"bug":0.5,"flying":0},
    "flying":   {"grass":2,"fighting":2,"bug":2,"electric":0.5,"rock":0.5,"steel":0.5},
    "psychic":  {"fighting":2,"poison":2,"psychic":0.5,"steel":0.5,"dark":0,"ghost":0},  # 수정: dark에 무효
    "bug":      {"grass":2,"psychic":2,"dark":2,
                 "fire":0.5,"fighting":0.5,"flying":0.5,"ghost":0.5,"steel":0.5,"fairy":0.5},
    "rock":     {"fire":2,"ice":2,"flying":2,"bug":2,
                 "fighting":0.5,"ground":0.5,"steel":0.5},
    "ghost":    {"psychic":2,"ghost":2,"normal":0,"dark":0.5},
    "dragon":   {"dragon":2,"steel":0.5,"fairy":0},
    "dark":     {"psychic":2,"ghost":2,"fighting":0.5,"dark":0.5,"fairy":0.5},
    "steel":    {"ice":2,"rock":2,"fairy":2,
                 "fire":0.5,"water":0.5,"electric":0.5,"steel":0.5},
    "fairy":    {"fighting":2,"dragon":2,"dark":2,"fire":0.5,"poison":0.5,"steel":0.5},
}

ALL_TYPES = list(TYPE_CHART.keys())


def get_type_multiplier(attack_type: str, defender_types: list[str]) -> float:
    """공격 타입 vs 방어 포켓몬 타입(들)의 상성 배율"""
    multiplier = 1.0
    chart = TYPE_CHART.get(attack_type, {})
    for dtype in defender_types:
        multiplier *= chart.get(dtype, 1.0)
    return multiplier


def calc_damage(
    attacker,
    defender,
    move,
    critical: bool = False,
    random_roll: bool = True,
) -> int:
    """
    표준 데미지 공식 계산
    Returns: 최종 데미지 (int)
    """
    if move.power == 0:
        return 0

    level = attacker.level

    # 물리/특수 구분
    if move.category == "physical":
        atk = attacker.effective_stat("attack")
        def_ = defender.effective_stat("defense")
    else:
        atk = attacker.effective_stat("sp_attack")
        def_ = defender.effective_stat("sp_defense")

    # 급소 (1.5배, 8세대)
    crit_mult = 1.5 if critical else 1.0

    # 랜덤 보정 (0.85 ~ 1.00)
    rand = random.uniform(0.85, 1.0) if random_roll else 1.0

    # STAB (Same Type Attack Bonus)
    stab = 1.5 if move.type_ in attacker.types else 1.0

    # 타입 상성
    type_mult = get_type_multiplier(move.type_, defender.types)

    # 화상 보정 (물리 공격 시 0.5)
    burn_mult = 0.5 if attacker.status == "burn" and move.category == "physical" else 1.0

    damage = (
        ((2 * level / 5 + 2) * move.power * atk / def_) / 50 + 2
    ) * crit_mult * rand * stab * type_mult * burn_mult

    return max(1, int(damage))


def calc_critical_chance(move) -> float:
    """급소 확률 계산 (기술별 단계)"""
    stages = {0: 1/24, 1: 1/8, 2: 1/2, 3: 1.0}
    return stages.get(getattr(move, "crit_stage", 0), 1/24)

"""Named per-run balance rules; experimental profiles require explicit opt-in."""

LEGACY_PROFILE = 'balance-v1'
RESCUE_PROFILE = 'normal-rescue-v2'
DEFAULT_PROFILE = LEGACY_PROFILE
SUPPORTED_PROFILES = (LEGACY_PROFILE, RESCUE_PROFILE)


def validate_profile(profile):
    if not isinstance(profile, str) or profile not in SUPPORTED_PROFILES:
        raise ValueError('지원하지 않는 탐험 밸런스 프로필입니다. 기존 저장은 보존되었습니다.')
    return profile


def balance_profile(state):
    """Absence always means the frozen legacy behavior, never today's default."""
    return validate_profile(state.get('balance_profile', LEGACY_PROFILE))


def normal_rescue_rest(state):
    """Only the first normal-mode rest in an explicitly selected rescue run."""
    return (balance_profile(state) == RESCUE_PROFILE and state.get('difficulty', 'normal') == 'normal'
            and state.get('node_index') == 2 and state.get('node', {}).get('kind') == 'rest')

"""In-domain Big Five versus isolated OOD research tracks.

eng.1, esp.1, ger.1, ita.1 and fra.1 may appear in a Europe training set.
aus.1, jpn.1, kor.1 and usa.1 are separate EXPERIMENTAL / OOD tracks.
They must not be added to that Europe set or promoted to Champion.
This module does not fit a model and does not write the active registry.
"""

EUROPE_IN_DOMAIN = ('eng.1', 'esp.1', 'ger.1', 'ita.1', 'fra.1')
OOD_ASIA = ('aus.1', 'jpn.1', 'kor.1')
OOD_MLS = ('usa.1',)
OOD_RESEARCH_TRACKS = OOD_ASIA + OOD_MLS


def competition_code(value):
    return str(value).strip().split(':', 1)[0]


def reject_ood_in_europe_training(codes):
    """Raise if an OOD research league is listed in a Europe training set."""
    leaked = []
    for code in codes:
        base = competition_code(code)
        if base in OOD_RESEARCH_TRACKS and base not in leaked:
            leaked.append(base)
    if leaked:
        raise RuntimeError(
            'OOD research leagues cannot enter the Europe training set: ' + ', '.join(leaked))


def reject_ood_champion_promotion(code, promoted):
    """OOD tracks stay research-only. A false flag is the only accepted value."""
    base = competition_code(code)
    if base not in OOD_RESEARCH_TRACKS:
        return
    if promoted is not False:
        raise RuntimeError(base + ' is EXPERIMENTAL / OOD and cannot be promoted to Champion')


_europe = set(EUROPE_IN_DOMAIN)
_ood = set(OOD_RESEARCH_TRACKS)
if len(EUROPE_IN_DOMAIN) != len(_europe) or len(OOD_RESEARCH_TRACKS) != len(_ood):
    raise RuntimeError('duplicate league codes in the research map')
if _europe & _ood:
    raise RuntimeError('OOD research tracks overlap the Europe in-domain set')
if set(OOD_ASIA) & set(OOD_MLS):
    raise RuntimeError('MLS overlaps an Asian OOD track')

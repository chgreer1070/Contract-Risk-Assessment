"""Negated likelihood and severity phrases must not change the score.

A clause that contains a negated phrase scores the same likelihood and severity
as the same clause with that phrase deleted. An un-negated stem still raises
the score. A field that is only the label ``unlikely`` stays Unlikely.
"""

import glob
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_dashboard import (  # noqa: E402
    _NEGATED_PHRASES,
    _canon_level,
    _canon_likelihood,
    _score_for,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CLAUSE = 'The supplier sends a written status note each month and the draft says {phrase} here.'
_CARRIER = (
    'The exposure is significant and the draft is likely and the note stays material'
)


def _clause(phrase):
    if phrase:
        return _CLAUSE.format(phrase=phrase)
    return _CLAUSE.format(phrase='').replace('  ', ' ')


def _score_text(text):
    level = _canon_level(text)
    likelihood = _canon_likelihood(text)
    return level, likelihood, _score_for(level, likelihood)


def test_negated_phrases_are_longest_first():
    lengths = [len(phrase) for phrase in _NEGATED_PHRASES]
    assert lengths == sorted(lengths, reverse=True)
    assert 'no material risk' in _NEGATED_PHRASES
    assert 'not likely' in _NEGATED_PHRASES
    assert 'unlikely' in _NEGATED_PHRASES
    assert 'not high' in _NEGATED_PHRASES
    assert 'not very high' in _NEGATED_PHRASES
    assert 'not a material' in _NEGATED_PHRASES


@pytest.mark.parametrize('phrase', _NEGATED_PHRASES)
def test_negated_phrase_matches_phrase_removed(phrase):
    full = _clause(phrase)
    removed = full.replace(f' {phrase}', '', 1)
    assert removed == _clause('')
    assert _score_text(full) == _score_text(removed)


@pytest.mark.parametrize('phrase', _NEGATED_PHRASES)
def test_negation_does_not_erase_other_stems(phrase):
    full = f'{_CARRIER} {phrase}.'
    removed = full.replace(f' {phrase}', '', 1)
    assert _canon_level(full) == _canon_level(removed) == 'High'
    assert _canon_likelihood(full) == _canon_likelihood(removed) == 'Likely'


def test_each_negated_phrase_has_a_golden_case():
    ids = set()
    for path in glob.glob(os.path.join(_ROOT, 'golden', 'cases', '*.json')):
        with open(path, encoding='utf-8') as handle:
            case = json.load(handle)
        if 'id' in case:
            ids.add(case['id'])
    missing = [
        phrase for phrase in _NEGATED_PHRASES
        if f"negation-{phrase.replace(' ', '-')}" not in ids
    ]
    assert not missing, f'golden cases missing for: {missing}'


def test_golden_cases_actually_delete_the_phrase():
    for path in glob.glob(os.path.join(_ROOT, 'golden', 'cases', 'negation-*.json')):
        with open(path, encoding='utf-8') as handle:
            case = json.load(handle)
        phrase = case['id'].removeprefix('negation-').replace('-', ' ')
        risks = case['state']['risk_assessment_data']
        assert phrase in risks[0]['likelihood'], case['id']
        assert phrase in risks[0]['riskLevel'], case['id']
        assert risks[1]['likelihood'] == risks[0]['likelihood'].replace(f' {phrase}', '', 1)
        assert risks[1]['riskLevel'] == risks[0]['riskLevel'].replace(f' {phrase}', '', 1)
        assert phrase not in risks[1]['likelihood']


@pytest.mark.parametrize('stem', [
    'likely', 'certain', 'often', 'probable', 'high', 'very high', 'significant', 'material',
])
def test_unnegated_stem_raises_the_score(stem):
    base = _score_text('The supplier sends a written status note.')
    raised = _score_text(f'The supplier sends a written status note {stem}.')
    assert raised[2] > base[2]


def test_negated_phrase_does_not_flip_to_the_opposite_pole():
    """Deleting the phrase leaves the default, rather than Unlikely or Low."""
    neutral = _score_for('Medium', 'Possible')
    assert _score_text('not likely') == ('Medium', 'Possible', neutral)
    assert _score_text('no significant') == ('Medium', 'Possible', neutral)
    assert _score_text('no material risk') == ('Medium', 'Possible', neutral)
    assert _canon_likelihood('not likely') != 'Unlikely'
    assert _canon_level('not significant') != 'Low'
    assert _canon_level('not high') == 'Medium'
    assert _canon_level('not very high') == 'Medium'
    assert _canon_likelihood('not certain') == 'Possible'


@pytest.mark.parametrize('raw', [
    'not-likely',
    'not  likely',
    'less-likely',
    'no-material',
    'not-material',
    'non-material',
    'not-significant',
    'no  material  risk',
    'un-likely',
    'not a material',
    'not\u2013likely',
])
def test_hyphen_or_spaced_negation_matches_phrase_removed(raw):
    assert _canon_likelihood(raw) == _canon_likelihood('')
    assert _canon_level(raw) == _canon_level('')
    assert _canon_likelihood(raw) != 'Likely'
    assert _canon_level(raw) != 'High'


def test_bare_unlikely_label_stays_unlikely():
    assert _canon_likelihood('unlikely') == 'Unlikely'
    assert _canon_likelihood('Unlikely!') == 'Unlikely'
    assert _canon_likelihood('Improbable.') == 'Unlikely'
    embedded = _clause('unlikely')
    assert _canon_likelihood(embedded) == _canon_likelihood(embedded.replace(' unlikely', '', 1))
    assert _canon_likelihood('unlikely 2') == _canon_likelihood('2')
    assert _canon_likelihood('2 unlikely') == _canon_likelihood('2')
    assert _canon_likelihood('unlikely2') == 'Possible'
    assert _canon_likelihood('unlikely_') == 'Possible'
    assert _canon_likelihood('very unlikely') == _canon_likelihood('very')


def test_other_signals_survive_a_negated_phrase():
    full = 'The exposure is high and the draft says not likely here.'
    removed = full.replace(' not likely', '', 1)
    assert _canon_level(full) == _canon_level(removed) == 'High'
    assert _canon_likelihood(full) == _canon_likelihood(removed) == 'Possible'


def test_immaterial_is_still_low_after_material_was_added():
    assert _canon_level('immaterial exposure') == 'Low'
    assert _canon_level('material') == 'High'


def test_almost_certain_stays_likely():
    assert _canon_likelihood('almost certain') == 'Likely'

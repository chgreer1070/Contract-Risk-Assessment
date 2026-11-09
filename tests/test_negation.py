"""Negated likelihood and severity phrases must not change the score.

A clause that contains a negated phrase scores the same likelihood and severity
as the same clause with that phrase deleted. An un-negated stem still raises
the score. The word ``unlikely`` stays Unlikely wherever it appears.
"""

import glob
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generate_dashboard import (  # noqa: E402
    _LEVEL_HIGH,
    _LEVEL_LOW,
    _LIKE_LIKELY,
    _LIKE_UNLIKELY,
    _NEGATED_PHRASES,
    _NEGATION_EXTRAS,
    _NEGATION_PREFIXES,
    _canon_level,
    _canon_likelihood,
    _score_for,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CLAUSE = 'The supplier sends a written status note each month and the draft says {phrase} here.'
_CARRIER = 'The exposure is significant and the draft is likely'

# One dashboard case per prefix, plus the two material-risk phrases.
_PREFIX_GOLDEN = {
    'negation-not-a': 'not a significant',
    'negation-not': 'not likely',
    'negation-no': 'no significant',
    'negation-non': 'non significant',
    'negation-less': 'less likely',
    'negation-never': 'never likely',
    'negation-without': 'without significant',
    'negation-no-material-risk': 'no material risk',
    'negation-not-a-material-risk': 'not a material risk',
}


def _clause(phrase):
    if phrase:
        return _CLAUSE.format(phrase=phrase)
    return _CLAUSE.format(phrase='').replace('  ', ' ')


def _score_text(text):
    level = _canon_level(text)
    likelihood = _canon_likelihood(text)
    return level, likelihood, _score_for(level, likelihood)


def test_negated_phrases_are_longest_first_and_skip_real_low_words():
    lengths = [len(phrase) for phrase in _NEGATED_PHRASES]
    assert lengths == sorted(lengths, reverse=True)
    assert 'unlikely' not in _NEGATED_PHRASES
    assert 'un likely' not in _NEGATED_PHRASES
    assert 'material' not in _LEVEL_HIGH
    low_words = set(_LEVEL_LOW) | set(_LIKE_UNLIKELY)
    assert low_words.isdisjoint(_NEGATED_PHRASES)
    for phrase in _NEGATION_EXTRAS:
        assert phrase in _NEGATED_PHRASES
    for prefix in _NEGATION_PREFIXES:
        assert any(phrase.startswith(prefix + ' ') for phrase in _NEGATED_PHRASES)


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


def test_prefix_golden_cases_cover_each_prefix_and_the_extras():
    ids = set()
    for path in glob.glob(os.path.join(_ROOT, 'golden', 'cases', '*.json')):
        with open(path, encoding='utf-8') as handle:
            case = json.load(handle)
        if 'id' in case:
            ids.add(case['id'])
    missing = [case_id for case_id in _PREFIX_GOLDEN if case_id not in ids]
    assert not missing, f'golden cases missing: {missing}'
    assert 'positive-unnegated-phrase' in ids
    assert 'unlikely-sentence' in ids


def test_golden_prefix_cases_delete_the_phrase():
    for case_id, phrase in _PREFIX_GOLDEN.items():
        path = os.path.join(_ROOT, 'golden', 'cases', f'{case_id}.json')
        with open(path, encoding='utf-8') as handle:
            case = json.load(handle)
        assert case['id'] == case_id
        assert case.get('comparePhraseRemoval') is True
        risks = case['state']['risk_assessment_data']
        assert phrase in risks[0]['likelihood'], case_id
        assert phrase in risks[0]['riskLevel'], case_id
        assert risks[1]['likelihood'] == risks[0]['likelihood'].replace(f' {phrase}', '', 1)
        assert risks[1]['riskLevel'] == risks[0]['riskLevel'].replace(f' {phrase}', '', 1)
        assert phrase not in risks[1]['likelihood']


@pytest.mark.parametrize('stem', [
    'likely', 'certain', 'often', 'probable', 'high', 'very high', 'significant',
])
def test_unnegated_stem_raises_the_score(stem):
    base = _score_text('The supplier sends a written status note.')
    raised = _score_text(f'The supplier sends a written status note {stem}.')
    assert raised[2] > base[2]
    assert stem in _LEVEL_HIGH or stem in _LIKE_LIKELY


def test_negated_phrase_does_not_flip_to_the_opposite_pole():
    """Deleting the phrase leaves the default, rather than Unlikely or Low."""
    neutral = _score_for('Medium', 'Possible')
    assert _score_text('not likely') == ('Medium', 'Possible', neutral)
    assert _score_text('no significant') == ('Medium', 'Possible', neutral)
    assert _score_text('no material risk') == ('Medium', 'Possible', neutral)
    assert _score_text('not a material risk') == ('Medium', 'Possible', neutral)
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
    'not a material',
    'not\u2013likely',
])
def test_hyphen_or_spaced_negation_matches_phrase_removed(raw):
    assert _canon_likelihood(raw) == _canon_likelihood('')
    assert _canon_level(raw) == _canon_level('')
    assert _canon_likelihood(raw) != 'Likely'
    assert _canon_level(raw) != 'High'


@pytest.mark.parametrize('word', _LIKE_UNLIKELY)
def test_unlikely_words_in_a_sentence_stay_unlikely(word):
    assert _canon_likelihood(f'This risk is {word} to occur.') == 'Unlikely'


def test_unlikely_sentence_and_hyphen_form_stay_unlikely():
    assert _canon_likelihood('This risk is unlikely to occur.') == 'Unlikely'
    assert _canon_likelihood('very unlikely') == 'Unlikely'
    assert _canon_likelihood('unlikely 2') == 'Unlikely'
    assert _canon_likelihood('2 unlikely') == 'Unlikely'
    assert _canon_likelihood('Unlikely!') == 'Unlikely'
    assert _canon_likelihood('un-likely') == 'Unlikely'
    assert _canon_likelihood('un likely') == 'Unlikely'
    assert _canon_likelihood('This risk is un-likely to occur.') == 'Unlikely'
    assert _canon_likelihood('unlikely2') == 'Possible'
    assert _canon_likelihood('unlikely_') == 'Possible'


@pytest.mark.parametrize('word', _LEVEL_LOW)
def test_low_severity_words_in_a_sentence_stay_low(word):
    assert _canon_level(f'The exposure is {word} in this draft.') == 'Low'


def test_other_signals_survive_a_negated_phrase():
    full = 'The exposure is high and the draft says not likely here.'
    removed = full.replace(' not likely', '', 1)
    assert _canon_level(full) == _canon_level(removed) == 'High'
    assert _canon_likelihood(full) == _canon_likelihood(removed) == 'Possible'


def test_material_does_not_raise_severity():
    assert _canon_level('material') == 'Medium'
    assert _canon_level('immaterial exposure') == 'Low'
    assert _canon_level('no material risk') == _canon_level('')
    assert _canon_level('not a material risk') == _canon_level('')


def test_almost_certain_stays_likely():
    assert _canon_likelihood('almost certain') == 'Likely'

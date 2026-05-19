from feeds.category_priors import _extract_entities, _extract_yes_no_prob, _set_prior


def test_extract_entities_picks_proper_nouns():
    ents = _extract_entities("Will the Los Angeles Lakers beat the Boston Celtics?", max_entities=2)
    assert ents == ["Los Angeles Lakers", "Boston Celtics"]


def test_extract_yes_no_prob_handles_percent_or_fraction():
    prob, confidence = _extract_yes_no_prob({
        "markets": [{
            "outcomes": [
                {"probability": 62.5},
                {"probability": 37.5},
            ]
        }]
    })
    assert prob == 0.625
    assert confidence > 0.0


def test_set_prior_writes_nested_category_map():
    priors = {}
    _set_prior(priors, "politics", "Will X win?", 0.61, 0.4, "openfec")
    assert priors["politics"]["will x win?"]["prob"] == 0.61
    assert priors["politics"]["will x win?"]["source"] == "openfec"

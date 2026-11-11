from app.web.deps import short_source


def test_business_names_on_the_switch_are_short_and_in_normal_capitals():
    assert short_source("Loyverse / PUBLICO FAST FOOD CHAIN") == "Publico"
    assert short_source("Loyverse / Steamy Co.") == "Steamy Co."
    assert short_source("SipStop") == "SipStop"
    assert short_source("Food Chain") == "Food Chain"      # never reduced to nothing
    assert short_source("") == ""

from extractors.area_regex import extract_summary_areas


def test_extract_summary_areas_m2():
    text = "専有面積 72.5㎡ バルコニー面積: 10.2 m2 延床面積 84.8m2"
    result = extract_summary_areas(text)
    assert result["exclusive_area_m2"] == "72.5"
    assert result["balcony_area_m2"] == "10.2"
    assert result["total_area_m2"] == "84.8"


def test_extract_summary_areas_tsubo():
    text = "住戸専用面積：20.0 坪 バルコニー 2.0坪"
    result = extract_summary_areas(text)
    assert result["exclusive_area_m2"] == "66.2"
    assert result["balcony_area_m2"] == "6.62"

"""分類函式單元測試（Phase 1.4）。"""
from datetime import datetime, timedelta, timezone

from scenario04.ingestion.metadata import (
    classify_constellation,
    classify_country,
    classify_era,
    classify_purpose,
)


class TestClassifyPurpose:
    def test_debris(self):
        assert classify_purpose("COSMOS 2251 DEB") == "碎片"
        assert classify_purpose("FENGYUN 1C DEBRIS") == "碎片"

    def test_rocket_body(self):
        assert classify_purpose("CZ-4B R/B") == "火箭體"
        assert classify_purpose("ARIANE 5 ROCKET BODY") == "火箭體"

    def test_unknown_object(self):
        assert classify_purpose("OBJECT A") == "不明物體"

    def test_payload(self):
        assert classify_purpose("STARLINK-1234") == "有效載荷"
        assert classify_purpose("FORMOSAT-5") == "有效載荷"


class TestClassifyCountry:
    def test_known_source_codes(self):
        assert classify_country("United States") == "美國"
        assert classify_country("People's Republic of China") == "中國"
        assert classify_country("TBD") == "不明"

    def test_already_a_label(self):
        assert classify_country("美國") == "美國"

    def test_empty(self):
        assert classify_country(None) == "不明"
        assert classify_country("") == "不明"

    def test_unknown(self):
        assert classify_country("Wakanda") == "其他"


class TestClassifyConstellation:
    def test_starlink(self):
        assert classify_constellation("STARLINK-30001") == "Starlink"

    def test_yaogan(self):
        assert classify_constellation("YAOGAN-41") == "遙感/Yaogan"

    def test_first_match_wins(self):
        # SKYSAT 屬 Planet/Flock 規則
        assert classify_constellation("SKYSAT-C19") == "Planet/Flock"

    def test_no_match(self):
        assert classify_constellation("ISS (ZARYA)") is None


class TestClassifyEra:
    def test_recent(self):
        d = datetime.now(timezone.utc) - timedelta(days=100)
        assert classify_era(d, None) == "< 1 年"

    def test_one_to_five(self):
        d = datetime.now(timezone.utc) - timedelta(days=365 * 3)
        assert classify_era(d, None) == "1–5 年"

    def test_over_ten(self):
        d = datetime.now(timezone.utc) - timedelta(days=365 * 20)
        assert classify_era(d, None) == "> 10 年"

    def test_from_intl_code(self):
        # launch_date 缺失時由 intl_code 年份推斷
        assert classify_era(None, "1999-025A") == "> 10 年"

    def test_unknown(self):
        assert classify_era(None, None) == "不明"

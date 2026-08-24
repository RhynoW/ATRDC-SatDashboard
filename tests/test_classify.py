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

    def test_guowang(self):
        # 國網編目名為 HULIANWANG DIGUI（2026-08 SATCAT 實測 186 顆）
        assert classify_constellation("HULIANWANG DIGUI-01") == "國網/Guowang"

    def test_pwsa_codenames(self):
        # PWSA/SDA 以任務代號編目：T0=CHECKMATE/WILDFIRE、T1=PRAETORIAN
        assert classify_constellation("PRAETORIAN SDA_602") == "PWSA/SDA"
        assert classify_constellation("CHECKMATE 8") == "PWSA/SDA"
        assert classify_constellation("WILDFIRE 4") == "PWSA/SDA"

    def test_geesat_catalog_name(self):
        # 吉利星座編目名為 GEESAT（舊關鍵字 GEESPACE 比對不到）
        assert classify_constellation("GEESAT 1-01") == "GeeSat/Geespace"

    def test_jilin_branded_members(self):
        # 吉林一號家族含長光冠名星：JILIN 前綴優先於 GAOFEN 規則
        assert classify_constellation("JILIN-01 GAOFEN 3D 27") == "吉林/Jilin"
        assert classify_constellation("LINGQIAO VIDEO A") == "吉林/Jilin"
        assert classify_constellation("LQSAT") == "吉林/Jilin"

    def test_formosat7(self):
        # 福衛七號編目名為 FORMOSAT7-x/COSMIC2-x；7R 獵風者亦歸入
        assert classify_constellation("FORMOSAT7-1/COSMIC2-1") == "Formosat-7"
        assert classify_constellation("FORMOSAT-7R/TRITON") == "Formosat-7"

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

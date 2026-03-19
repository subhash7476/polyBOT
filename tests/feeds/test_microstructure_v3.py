from feeds.microstructure import BINANCE_SYMBOLS, SYMBOL_TO_ASSET


def test_binance_symbols_includes_all_assets():
    expected = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT",
                "BNBUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT"]
    for sym in expected:
        assert sym in BINANCE_SYMBOLS, f"Missing {sym}"


def test_binance_symbol_to_asset_mapping():
    assert SYMBOL_TO_ASSET["BTCUSDT"] == "BTC"
    assert SYMBOL_TO_ASSET["ETHUSDT"] == "ETH"
    assert SYMBOL_TO_ASSET["SOLUSDT"] == "SOL"
    assert SYMBOL_TO_ASSET["XRPUSDT"] == "XRP"
    assert SYMBOL_TO_ASSET["BNBUSDT"] == "BNB"
    assert SYMBOL_TO_ASSET["DOGEUSDT"] == "DOGE"
    assert SYMBOL_TO_ASSET["ADAUSDT"] == "ADA"
    assert SYMBOL_TO_ASSET["AVAXUSDT"] == "AVAX"


def test_all_symbols_have_mapping():
    for sym in BINANCE_SYMBOLS:
        assert sym in SYMBOL_TO_ASSET, f"No mapping for {sym}"

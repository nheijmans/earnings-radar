from earnings_radar import is_earnings_8k, parse_exhibits, symbol_and_name

BASE = "https://www.sec.gov/Archives/edgar/data/320193/000032019326000011"


def test_is_earnings_8k():
    assert is_earnings_8k("8-K", "2.02,9.01")
    assert is_earnings_8k("8-K", "9.01, 2.02")     # order / spacing don't matter
    assert not is_earnings_8k("8-K", "5.02,9.01")  # officer change, not earnings
    assert not is_earnings_8k("8-K", "")           # 8-K with no items
    assert not is_earnings_8k("10-Q", "")          # wrong form


def test_parse_exhibits():
    html = (
        '<td><a href="/x/aapl.htm">aapl.htm</a></td><td>8-K</td>'
        '<td><a href="/x/press.htm">press.htm</a></td><td>EX-99.1</td>'
        '<td><a href="pres.htm">pres.htm</a></td><td>EX-99.2</td>'
    )
    ex = parse_exhibits(html, BASE)
    assert ex == {
        "EX-99.1": "https://www.sec.gov/x/press.htm",  # absolute href
        "EX-99.2": f"{BASE}/pres.htm",                 # relative href
    }
    # 8-K itself is not an EX-99.x exhibit
    assert "8-K" not in ex
    # A filing with no press-release exhibits yields nothing
    assert parse_exhibits("<td>nothing here</td>", BASE) == {}


def test_symbol_and_name():
    assert symbol_and_name("MSFT") == ("MSFT", "MSFT")
    assert symbol_and_name({"symbol": "AAPL", "name": "Apple Inc."}) == ("AAPL", "Apple Inc.")
    assert symbol_and_name({"symbol": "NVDA"}) == ("NVDA", "NVDA")

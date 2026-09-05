from app.db.models.crawl import FetchStrategyStats
from app.services.crawling.fetch_router import FetchStrategy, decide_initial_strategy


def test_explicit_strategy_is_never_overridden():
    assert decide_initial_strategy(FetchStrategy.HTTP, None) == FetchStrategy.HTTP
    assert decide_initial_strategy(FetchStrategy.BROWSER, None) == FetchStrategy.BROWSER


def test_auto_defaults_to_http_with_no_history():
    assert decide_initial_strategy(FetchStrategy.AUTO, None) == FetchStrategy.HTTP


def test_auto_defaults_to_http_with_insufficient_history():
    stats = FetchStrategyStats(domain="x.com", http_attempts=2, http_successes=0)
    assert decide_initial_strategy(FetchStrategy.AUTO, stats) == FetchStrategy.HTTP


def test_auto_escalates_to_browser_when_http_mostly_fails_and_browser_works():
    stats = FetchStrategyStats(
        domain="spa.example.com",
        http_attempts=10,
        http_successes=9,
        http_extraction_failures=8,  # fetched fine, but extraction kept failing -> JS-rendered
        browser_attempts=5,
        browser_successes=5,
    )
    assert decide_initial_strategy(FetchStrategy.AUTO, stats) == FetchStrategy.BROWSER


def test_auto_stays_http_when_it_is_working_well():
    stats = FetchStrategyStats(
        domain="news.example.com",
        http_attempts=20,
        http_successes=19,
        http_extraction_failures=1,
        browser_attempts=0,
        browser_successes=0,
    )
    assert decide_initial_strategy(FetchStrategy.AUTO, stats) == FetchStrategy.HTTP


def test_auto_does_not_escalate_if_browser_also_fails_on_this_domain():
    stats = FetchStrategyStats(
        domain="broken.example.com",
        http_attempts=10,
        http_successes=1,
        http_extraction_failures=1,
        browser_attempts=8,
        browser_successes=0,  # browser doesn't help here either
    )
    assert decide_initial_strategy(FetchStrategy.AUTO, stats) == FetchStrategy.HTTP

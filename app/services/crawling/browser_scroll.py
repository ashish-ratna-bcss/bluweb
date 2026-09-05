"""Bounded infinite-scroll / load-more discovery for Playwright pages.

Everything is timeout-controlled and capped. Never an unbounded browser
loop. Detects both scroll-driven item growth and explicit "Load more"
buttons without an href.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from playwright.async_api import Page

_LOAD_MORE_RE = re.compile(
    r"^\s*(load more|show more|see more|view more|more results|more stories)\s*$",
    re.IGNORECASE,
)


@dataclass
class ScrollBudget:
    max_scrolls: int = 5
    max_new_items: int = 200
    max_browser_seconds: float = 15.0
    max_total_bytes: int = 5_000_000
    max_consecutive_no_change: int = 2


@dataclass
class ScrollResult:
    scrolls_performed: int = 0
    load_more_clicks: int = 0
    new_items_estimate: int = 0
    stopped_reason: str = "not_attempted"
    html: str | None = None
    signals: list[str] = field(default_factory=list)


async def expand_dynamic_content(page: Page, budget: ScrollBudget, *, started_monotonic: float) -> ScrollResult:
    """After initial navigation, optionally scroll / click load-more within
    budget. Returns the final HTML snapshot and diagnostic counters."""
    import time

    result = ScrollResult()
    try:
        baseline_items = await _item_estimate(page)
        baseline_links = await page.locator("a[href]").count()
        baseline_text_len = len(await page.inner_text("body"))
        no_change = 0

        for i in range(budget.max_scrolls):
            if (time.monotonic() - started_monotonic) >= budget.max_browser_seconds:
                result.stopped_reason = "timeout"
                break
            content = await page.content()
            if len(content.encode("utf-8")) >= budget.max_total_bytes:
                result.stopped_reason = "max_total_bytes"
                break

            clicked = await _try_load_more(page)
            if clicked:
                result.load_more_clicks += 1
                result.signals.append("load_more_click")
                await page.wait_for_timeout(400)
            else:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                result.scrolls_performed += 1
                result.signals.append("scroll")
                await page.wait_for_timeout(350)

            # Wait briefly for late DOM mutations / lazy content.
            try:
                await page.wait_for_load_state("networkidle", timeout=1500)
            except Exception:  # noqa: BLE001 - optional settle
                pass

            items = await _item_estimate(page)
            links = await page.locator("a[href]").count()
            text_len = len(await page.inner_text("body"))
            delta_items = max(0, items - baseline_items)
            delta_links = max(0, links - baseline_links)
            delta_text = max(0, text_len - baseline_text_len)
            result.new_items_estimate = delta_items

            if delta_items == 0 and delta_links == 0 and delta_text < 80:
                no_change += 1
                if no_change >= budget.max_consecutive_no_change:
                    result.stopped_reason = "no_new_content"
                    break
            else:
                no_change = 0
                baseline_items = items
                baseline_links = links
                baseline_text_len = text_len

            if result.new_items_estimate >= budget.max_new_items:
                result.stopped_reason = "max_new_items"
                break
        else:
            result.stopped_reason = "max_scrolls"

        # Open shadow roots where safely enumerable (open mode only).
        try:
            await page.evaluate(
                """() => {
                  const hosts = document.querySelectorAll('*');
                  for (const el of hosts) {
                    if (el.shadowRoot) {
                      const slot = document.createElement('div');
                      slot.setAttribute('data-webintel-shadow', '1');
                      slot.innerHTML = el.shadowRoot.innerHTML;
                      el.appendChild(slot);
                    }
                  }
                }"""
            )
            result.signals.append("open_shadow_dom_inlined")
        except Exception:  # noqa: BLE001 - shadow pierce is best-effort
            pass

        result.html = await page.content()
        if result.stopped_reason == "not_attempted":
            result.stopped_reason = "completed"
    except Exception as exc:  # noqa: BLE001
        result.stopped_reason = f"error:{type(exc).__name__}"
        try:
            result.html = await page.content()
        except Exception:  # noqa: BLE001
            result.html = None

    return result


async def _item_estimate(page: Page) -> int:
    """Heuristic count of repeating item-like nodes (cards/rows/results)."""
    return await page.evaluate(
        """() => {
          const selectors = [
            '[class*="result" i]', '[class*="item" i]', '[class*="card" i]',
            'li.cl-static-search-result', 'article', '.comtr'
          ];
          let best = 0;
          for (const sel of selectors) {
            try {
              best = Math.max(best, document.querySelectorAll(sel).length);
            } catch (e) {}
          }
          return best;
        }"""
    )


async def _try_load_more(page: Page) -> bool:
    """Click a visible Load-more style control that is a button/role=button
    (href-based 'load more' is handled by pagination.py as a normal link)."""
    candidates = page.locator("button, [role=button], a:not([href]), a[href='#']")
    count = await candidates.count()
    for i in range(min(count, 40)):
        el = candidates.nth(i)
        try:
            if not await el.is_visible():
                continue
            text = (await el.inner_text()).strip()
            if not text or not _LOAD_MORE_RE.match(text):
                continue
            await el.click(timeout=1000)
            return True
        except Exception:  # noqa: BLE001
            continue
    return False

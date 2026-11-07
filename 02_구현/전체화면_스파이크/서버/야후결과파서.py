"""Yahoo 검색 결과 페이지의 section.s-algo 레이아웃 파서.

ddgs 9.16.1의 Yahoo 파서는 div.relsrch만 읽는다. 2026-11-07 실제 응답은 결과를 section.s-algo로
돌려줘 0건이 파싱됐다(진단: 검증결과/웹진단실측_결과.json, 공개 보고 ddgs #498).
결과 section은 서로 중첩되므로 후손 전체가 아니라 각 결과의 직계 자식 경로만 읽는다.
"""

from lxml import html

ITEMS = "//section[contains(@class,'s-algo')]"
TITLE = (
    "./div[contains(@class,'grp-talgo') and not(contains(@class,'grp-talgo-ext'))]"
    "/div[contains(@class,'compTitle')]/h3/a"
)
DESCRIPTION = "./div[contains(@class,'grp-talgo-ext')]/div[contains(@class,'compText')]/p"
KNOWN_MARKERS = "//section[contains(@class,'s-algo')] | //div[contains(@class,'relsrch')]"
# 알려지지 않은 새 레이아웃을 놓치지 않기 위한 보조 판정: 결과 페이지는 제목(h3)이 여러 개다.
MIN_HEADINGS = 3


def _squash(parts):
    return " ".join("".join(parts).split())


def parse_algo_results(tree):
    """section.s-algo 결과를 {title, href, body}로 읽는다. 제목이나 링크가 없으면 건너뛴다."""
    rows = []
    for node in tree.xpath(ITEMS):
        anchors = node.xpath(TITLE)
        if not anchors:
            continue
        anchor = anchors[0]
        # 사이트명·주소는 a 안의 span이고 제목은 a의 직접 텍스트다.
        title = _squash(anchor.xpath("./text()"))
        href = anchor.get("href") or ""
        descriptions = node.xpath(DESCRIPTION)
        body = _squash(descriptions[0].xpath(".//text()")) if descriptions else ""
        if title and href:
            rows.append({"title": title, "href": href, "body": body})
    return rows


def looks_like_results(tree):
    """결과 페이지처럼 보이는지: 알려진 결과 구조가 있거나 제목(h3)이 여러 개다."""
    return bool(tree.xpath(KNOWN_MARKERS)) or len(tree.xpath("//h3")) >= MIN_HEADINGS


def extract_algo_results(engine, html_text):
    """ddgs 엔진 인스턴스의 결과 형식(TextResult)으로 신규 레이아웃 결과를 만든다."""
    tree = engine.extract_tree(engine.pre_process_html(html_text))
    results = []
    for row in parse_algo_results(tree):
        result = engine.result_type()
        result.title, result.href, result.body = row["title"], row["href"], row["body"]
        results.append(result)
    return results


def looks_like_results_html(html_text):
    try:
        return looks_like_results(html.fromstring(html_text))
    except (ValueError, html.etree.ParserError):
        return False

"""정본 바이트와 메타데이터만 받는 순수 보고서 표현 경계."""

import html
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit

from markdown_it import MarkdownIt

from 서버.보고서도식 import SVG_NS, SvgValidationError, validate_svg
from 서버.저장소 import ApiError

RENDERER_VERSION = "markdown-it4-playwright1248-svg-v2"
MAX_SOURCE_BYTES = 1024 * 1024
PRINT_CSS = """
@page { size: A4; margin: 20mm 16mm; }
* { box-sizing: border-box; }
html { color-scheme: light; }
body { font: 11pt/1.65 'Malgun Gothic', '맑은 고딕', sans-serif; color: #19212b;
       max-width: 180mm; margin: 24px auto; padding: 0 12px; overflow-wrap: anywhere; }
h1,h2,h3 { break-after: avoid; line-height: 1.3; }
p,li { orphans: 3; widows: 3; }
table { border-collapse: collapse; width: 100%; table-layout: fixed; margin: 1em 0; }
thead { display: table-header-group; }
tr,figure,blockquote { break-inside: avoid; }
th,td { border: 1px solid #aab2bc; padding: 6px; text-align: left;
        overflow-wrap: anywhere; word-break: normal; vertical-align: top; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; background: #f3f5f7; padding: 12px; }
code { font-family: Consolas, 'Malgun Gothic', monospace; }
a { color: #154f85; text-decoration: underline; overflow-wrap: anywhere; }
figure { margin: 1em 0; max-width: 100%; }
figure svg { display: block; width: 100%; height: auto; max-width: 100%;
             overflow: visible; font-family: 'Malgun Gothic', sans-serif; font-size: 18px; }
figcaption { margin-top: .5em; overflow-wrap: anywhere; }
.render-warning { border-left: 3px solid #936700; padding-left: .75em; }
@media (max-width: 480px) { body { margin: 12px auto; } th,td { padding: 4px; } }
@media print { body { max-width: none; margin: 0; padding: 0; }
    pre { overflow: visible; } figure svg { max-height: 235mm; } }
"""


def _http_link(value):
    try:
        parts = urlsplit(value)
        return (
            parts.scheme.lower() in {"http", "https"}
            and bool(parts.hostname)
            and not any(ord(char) <= 32 or ord(char) == 127 for char in value)
            and not parts.username
            and not parts.password
        )
    except ValueError:
        return False


def _inline_text(tokens):
    return "".join(
        _inline_text(token.children)
        if token.children
        else (
            token.content
            if token.type in {"text", "code_inline", "image"}
            else " "
            if token.type in {"softbreak", "hardbreak"}
            else ""
        )
        for token in tokens
    ).strip()


def create_markdown_parser():
    """표현과 가시 본문 검사가 같은 HTML·표·링크 문법을 사용한다."""
    parser = MarkdownIt("js-default", {"html": False}).enable("table")
    parser.validateLink = lambda value: value.startswith("#") or _http_link(value)
    return parser


def render_presentation(markdown: bytes, report: dict) -> dict:
    if len(markdown) > MAX_SOURCE_BYTES:
        raise ApiError(413, "export_too_large", "변환 원문은 1MiB 이하여야 합니다.")
    parser = create_markdown_parser()
    warnings = []
    links = []
    parser.renderer.rules["image"] = lambda tokens, index, options, env: html.escape(
        tokens[index].content
    )
    original_fence = parser.renderer.rules["fence"]

    def fence(tokens, index, options, env):
        token = tokens[index]
        if token.info.strip() != "svg":
            return original_fence(tokens, index, options, env)
        try:
            clean = validate_svg(token.content)
        except SvgValidationError:
            warning = "도식을 표시할 수 없습니다. 안전하지 않거나 허용 한도를 초과한 SVG입니다."
            warnings.append(warning)
            return f'<p class="render-warning">{warning}</p><pre><code>{html.escape(token.content)}</code></pre>\n'
        # 검증기가 새로 만든 제한 트리만 재파싱한다. DTD/entity/외부 참조는 없다.
        root = ET.fromstring(clean)
        description = (
            " · ".join(
                "".join(node.itertext()).strip()
                for node in root.iter()
                if node.tag in {f"{{{SVG_NS}}}title", f"{{{SVG_NS}}}desc"}
            ).strip(" ·")
            or "보고서 도식"
        )
        clean = clean.replace(
            "<svg ", '<svg role="img" aria-label="' + html.escape(description, quote=True) + '" ', 1
        )
        return f"<figure>{clean}<figcaption>{html.escape(description)}</figcaption></figure>\n"

    parser.renderer.rules["fence"] = fence
    tokens = parser.parse(markdown.decode("utf-8"))
    for token in tokens:
        children = token.children or []
        for index, child in enumerate(children):
            if child.type == "link_open" and _http_link(child.attrGet("href") or ""):
                # 빈 sandbox는 자기 프레임의 탐색은 허용한다. 새 탭 대상으로 지정하여
                # 뷰어 안에서는 차단하고 실제 출처 열기는 바깥의 안전한 링크 목록으로 한다.
                child.attrSet("target", "_blank")
                child.attrSet("rel", "noopener noreferrer")
                end = index + 1
                while end < len(children) and children[end].type != "link_close":
                    end += 1
                entry = {
                    "text": _inline_text(children[index + 1 : end]),
                    "url": child.attrGet("href"),
                }
                if entry not in links:
                    links.append(entry)
    body = parser.renderer.render(tokens, parser.options, {})
    title = html.escape(str(report.get("title", "보고서")))
    document = (
        '<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta http-equiv="Content-Security-Policy" content="default-src &apos;none&apos;; '
        "style-src &apos;unsafe-inline&apos;; img-src &apos;none&apos;; base-uri &apos;none&apos;; "
        'form-action &apos;none&apos;">'
        f"<title>{title}</title><style>{PRINT_CSS}</style></head><body>{body}</body></html>"
    )
    return {
        "report_id": report.get("report_id"),
        "source_report_hash": report.get("sha256"),
        "renderer_version": RENDERER_VERSION,
        "html": document,
        "warnings": warnings,
        "links": links,
    }

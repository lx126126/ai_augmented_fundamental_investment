# -*- coding: utf-8 -*-
"""静态检查：渲染器用到的样式类必须在 CSS 里有定义。

为什么需要这个测试——
缺 CSS 是**静默失败**：页面照样生成、测试照样通过、日志什么都不打，
只是那个元素退回浏览器默认样式（16px 系统字体、正文色），
在一堆 11.5px 的段落里突兀得像从别的板块粘过来的。

真实事故（2026-09）：新增「现金流异动归因」板块时，CSS 被加到了
`templates/valueline.html` 上，而那个文件是**构建产物** —— 下一次构建
（写 `templates/valueline.html` + `reports/<期>/<code>.html`）把它整体覆盖，
新规则的段落于是渲染成 16px。纯看 HTML 元素计数 / 文件大小完全发现不了。

本测试不启动浏览器，只做两件事：
1. 渲染源码里 `class="..."` 出现的样式类，都能在 `CSS` 变量里找到选择器；
2. 构建产物不被当成模板源（避免「改了产物、源头没改」再次发生）。
"""
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILDER = ROOT / "scripts" / "build_valueline.py"

# 不参与检查的类：无样式语义的纯钩子，或由外部 CSS 框架/内联样式承担
_IGNORED = {
    "page", "header", "section", "co-name", "report-period", "publish-date",
    "up", "down", "high", "low", "flat",   # 语义着色类，按需在行内组合
}


def _builder_src() -> str:
    if not BUILDER.exists():
        pytest.skip("build_valueline.py 不存在")
    return BUILDER.read_text(encoding="utf-8")


def _css_block(src: str, strip_comments: bool = True) -> str:
    """抽出 `CSS = \"\"\"...\"\"\"` 的内容。

    默认去掉 `/* ... */` 注释——注释常紧贴在规则前面（如「/* 财务造假检测 */\\n.fraud {」），
    不剥掉的话「上一个 } 到本规则 { 之间的文本」会含注释，选择器就解析不出来。

    ⚠️ 但**检查 raw-text 终止符时必须传 strip_comments=False**：
    HTML 解析器在样式元素里不认注释，写在注释里的闭合标签照样会结束样式块。
    实测过这个漏洞——检查剥了注释，于是注入到注释里的终止符被悄悄放过去了。
    """
    m = re.search(r'^CSS\s*=\s*"""(.*?)^"""', src, re.S | re.M)
    assert m, "未找到 CSS 变量，build_valueline.py 结构可能已变"
    css = m.group(1)
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S) if strip_comments else css


def test_rendered_classes_have_css_rules():
    """渲染源码里出现的每个样式类，必须在 CSS 里有 `.类名` 选择器。"""
    src = _builder_src()
    css = _css_block(src)

    # 只看赋值给 class 属性的字符串，避免把 CSS 自身的选择器也算进来
    used: set[str] = set()
    for m in re.finditer(r'class=\\?"([^"\\]+)\\?"', src):
        for cls in m.group(1).split():
            if cls and not cls.startswith("@") and "{" not in cls:
                used.add(cls)

    missing = sorted(
        c for c in used - _IGNORED
        if f".{c}" not in css
    )
    assert not missing, (
        "以下样式类在渲染里用到，但 CSS 变量中没有定义（元素会退回浏览器默认样式）：\n  "
        + "\n  ".join(missing)
        + "\n\n注意：CSS 定义在 scripts/build_valueline.py 的 CSS 变量里，"
          "不是 templates/valueline.html（后者是构建产物）。"
    )


def test_review_blocks_share_one_type_scale():
    """季报解读板块的正文字号必须一致。

    这条守的是「同一板块里出现 11.5px / 11px / 16px 三种尺寸」的回归：
    新增子块时最容易只写结构不写样式。
    """
    src = _builder_src()
    css = _css_block(src)

    def decl(selector: str) -> str:
        """取某条规则的声明体；选择器可能是逗号分组（如 `.qr-col p, .qr-watch-wrap p`）。"""
        for m in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
            parts = [p.strip() for p in m.group(1).split(",")]
            if selector in parts:
                return m.group(2)
        raise AssertionError(f"CSS 缺少规则：{selector}")

    prose = re.search(r"font-size:\s*([\d.]+)px", decl(".qr-col p"))
    assert prose, ".qr-col p 未声明 font-size"
    size = prose.group(1)

    for sel in (".qr-cf-wrap p", ".op-table", ".cf-strip"):
        body = decl(sel)
        got = re.search(r"font-size:\s*([\d.]+)px", body)
        assert got, f"{sel} 未声明 font-size（会退回浏览器默认 16px）"
        assert got.group(1) == size, (
            f"{sel} 字号 {got.group(1)}px 与季报解读正文 .qr-col p 的 {size}px 不一致"
        )


@pytest.mark.parametrize("raw_term", ["</style", "</script"])
def test_css_has_no_raw_text_terminator(raw_term):
    """CSS 里不能出现样式/脚本元素的闭合标签字面量。

    内联在 `<style>` 里的 CSS 是 HTML 的 **raw text** 元素内容：解析器只找闭合标签，
    找到就结束，不管它是不是写在注释里。在注释里引一次闭合标签，整份 CSS 就变成
    正文文本 —— 页面照样打开、不报错、测试全绿，只是所有样式消失：
    字号回 13px、`table-layout: fixed` 失效导致宽表被撑到 1260px、长图右侧多白边。

    本文件里的标签都用引号包着写，避免自己触发同一条规则。
    """
    src = _builder_src()
    # strip_comments=False：注释里的终止符同样会结束样式块，绝不能放过
    css = _css_block(src, strip_comments=False)
    assert raw_term not in css.lower(), (
        f"CSS 里出现了 {raw_term}... —— 会提前结束样式块，导致整份 CSS 失效。"
        "描述标签时请用引号包裹或改写措辞。"
    )


def test_template_wraps_css_in_raw_text_element():
    """占位符必须真的位于一个 raw text 元素内部（否则上面的检查没有意义）。"""
    src = _builder_src()
    # 必须从 TEMPLATE 开始找：CSS 的顶部注释里也会提到这个占位符，从头找会命中注释。
    tpl = src[src.find("TEMPLATE = "):]
    i = tpl.find("@@CSS@@")
    assert i > 0, "TEMPLATE 里没有 @@CSS@@ 占位符"
    around = tpl[max(0, i - 12): i + 24]
    assert "<style" in around and "/style>" in around, (
        f"@@CSS@@ 不在样式元素内（CSS 会被当正文渲染）：{around!r}"
    )


def test_builder_is_the_template_source():
    """构建产物不能被打上「模板」的名义，否则容易被直接编辑后覆盖。"""
    src = _builder_src()
    assert "TEMPLATE = " in src, "模板应内联在 build_valueline.py 的 TEMPLATE 变量里"
    # 产物路径必须由脚本写出（说明它是产物，不是源）
    assert 'root / "templates" / "valueline.html"' in src or "templates\" / \"valueline.html" in src

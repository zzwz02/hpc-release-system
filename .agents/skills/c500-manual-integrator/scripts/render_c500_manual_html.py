#!/usr/bin/env python3
"""Render C500 manuals and HPC release notes to preview-like or standalone HTML."""
from __future__ import annotations

import argparse
import base64
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote, urldefrag


DEFAULT_DOCS_ROOT = Path("/remote_home/zhawu/c500_rest_doc/module_pde/C500_Docs")
DEFAULT_X201_DOCS_ROOT = Path("/remote_home/zhawu/c500_rest_doc/module_pde/X201_Docs")
DEFAULT_OUT_DIR = Path("/tmp/c500_manual_html")
COPYRIGHT_TEXT = "© 版权所有 2026 沐曦集成电路（上海）股份有限公司。保留所有权利。"


@dataclass(frozen=True)
class DocProject:
    name: str
    root_key: str
    project_dir: str
    source_file: str
    html_file: str
    display_title: str


@dataclass(frozen=True)
class SplitPage:
    section_id: str
    filename: str
    title: str


PROJECTS = (
    DocProject(
        name="hpc_manual",
        root_key="c500",
        project_dir="HPC_Manual",
        source_file="source/HPC_Manual_CN.rst",
        html_file="HPC_Manual_CN.html",
        display_title="沐曦通用GPU HPC 应用用户手册",
    ),
    DocProject(
        name="ai4sci_user_guide",
        root_key="c500",
        project_dir="AI4Sci_User_Guide",
        source_file="source/C500_AI4SciUserGuide_CN.rst",
        html_file="C500_AI4SciUserGuide_CN.html",
        display_title="沐曦通用GPU AI for Science 应用用户手册",
    ),
    DocProject(
        name="maca_hpc_release_notes",
        root_key="c500",
        project_dir="HPC_Release_Notes",
        source_file="source/MACA_HPC_release_notes_CN.rst",
        html_file="MACA_HPC_release_notes_CN.html",
        display_title="沐曦通用GPU HPC Release Notes",
    ),
    DocProject(
        name="x201_hpc_release_notes",
        root_key="x201",
        project_dir="HPC_Release_Notes",
        source_file="source/X201_HPC_release_notes_CN.rst",
        html_file="X201_HPC_release_notes_CN.html",
        display_title="沐曦X201 HPC Release Notes",
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render C500 manual RST files and C500/X201 HPC release notes to HTML "
            "using their Sphinx projects."
        )
    )
    parser.add_argument(
        "--docs-root",
        type=Path,
        default=DEFAULT_DOCS_ROOT,
        help=f"C500_Docs directory. Default: {DEFAULT_DOCS_ROOT}",
    )
    parser.add_argument(
        "--x201-docs-root",
        type=Path,
        default=DEFAULT_X201_DOCS_ROOT,
        help=f"X201_Docs directory. Default: {DEFAULT_X201_DOCS_ROOT}",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help=f"HTML output directory. Default: {DEFAULT_OUT_DIR}",
    )
    parser.add_argument(
        "--sphinx-build",
        default=os.environ.get("SPHINXBUILD", "sphinx-build"),
        help="sphinx-build executable. Default: SPHINXBUILD env var or sphinx-build",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove this script's output directories before building.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat Sphinx warnings as errors.",
    )
    parser.add_argument(
        "--jobs",
        default="auto",
        help="Parallel build jobs passed to sphinx-build -j. Default: auto",
    )
    parser.add_argument(
        "--plain-sphinx",
        action="store_true",
        help="Keep native Sphinx HTML instead of creating preview-style split pages. Applies with --preview-folders.",
    )
    parser.add_argument(
        "--single-file",
        action="store_true",
        default=True,
        help="Write one standalone HTML file per document. This is the default.",
    )
    parser.add_argument(
        "--preview-folders",
        action="store_false",
        dest="single_file",
        help="Create preview-style output folders instead of standalone HTML files.",
    )
    return parser.parse_args()


def resolve_executable(name: str) -> str:
    if Path(name).is_file():
        return name
    resolved = shutil.which(name)
    if resolved:
        return resolved
    raise SystemExit(
        f"找不到 {name!r}。请先安装 Sphinx，或用 --sphinx-build 指定 sphinx-build 路径。"
    )


def validate_project(docs_roots: dict[str, Path], project: DocProject) -> Path:
    docs_root = docs_roots[project.root_key]
    root = docs_root / project.project_dir
    source = root / project.source_file
    conf = root / "source" / "conf.py"
    if not source.is_file():
        raise SystemExit(f"找不到源文件: {source}")
    if not conf.is_file():
        raise SystemExit(f"找不到 Sphinx 配置: {conf}")
    return root


def build_project(
    *,
    sphinx_build: str,
    project: DocProject,
    project_root: Path,
    out_dir: Path,
    doctree_dir: Path,
    strict: bool,
    jobs: str,
) -> Path:
    html_dir = out_dir / project.name
    cmd = [
        sphinx_build,
        "-b",
        "html",
        "-d",
        str(doctree_dir),
        "-j",
        jobs,
    ]
    if strict:
        cmd.append("-W")
    cmd.extend(["source", str(html_dir)])

    print(f"[build] {project.name}: {' '.join(cmd)}")
    subprocess.run(cmd, cwd=project_root, check=True)

    html_file = html_dir / project.html_file
    if not html_file.is_file():
        raise SystemExit(f"构建完成但未找到目标 HTML: {html_file}")
    return html_file


def require_bs4():
    try:
        from bs4 import BeautifulSoup, NavigableString, Tag
    except ImportError as exc:
        raise SystemExit(
            "生成发布预览布局需要 beautifulsoup4。请先执行: "
            "python3 -m pip install --user beautifulsoup4"
        ) from exc
    return BeautifulSoup, NavigableString, Tag


def compact_text(value: str) -> str:
    value = value.replace("", "")
    return re.sub(r"\s+", " ", value).strip()


def heading_text(heading) -> str:
    if heading is None:
        return "未命名章节"
    return compact_text(" ".join(heading.stripped_strings))


def safe_filename(title: str, used: set[str]) -> str:
    title = re.sub(r"^\d+(?:\.\d+)*\.\s*", "", title).strip()
    title = unicodedata.normalize("NFKC", title).lower()
    chars: list[str] = []
    for char in title:
        if char.isalnum() or "\u4e00" <= char <= "\u9fff":
            chars.append(char)
        elif char in {" ", "_", "-", "/", "\\"}:
            chars.append("_")
    stem = re.sub(r"_+", "_", "".join(chars)).strip("_") or "section"
    filename = f"{stem}.html"
    if filename not in used:
        used.add(filename)
        return filename

    index = 2
    while f"{stem}_{index}.html" in used:
        index += 1
    filename = f"{stem}_{index}.html"
    used.add(filename)
    return filename


def is_external_href(href: str) -> bool:
    return href.startswith(("http://", "https://", "mailto:", "tel:", "javascript:", "data:"))


def is_data_uri(value: str) -> bool:
    return value.startswith("data:")


def add_class(tag, class_name: str) -> None:
    classes = list(tag.get("class", []))
    if class_name not in classes:
        classes.append(class_name)
        tag["class"] = classes


def remove_class(tag, class_name: str) -> None:
    classes = [name for name in tag.get("class", []) if name != class_name]
    if classes:
        tag["class"] = classes
    elif tag.has_attr("class"):
        del tag["class"]


def replace_text(tag, text: str, navigable_string_type) -> None:
    if tag is None:
        return
    tag.clear()
    tag.append(navigable_string_type(text))


def rewrite_doc_href(
    href: str,
    *,
    project: DocProject,
    pages: list[SplitPage],
    id_to_page: dict[str, str],
    top_id_by_page: dict[str, str],
    current_page: str | None,
    in_split_dir: bool,
) -> str:
    if not href or is_external_href(href):
        return href

    url, fragment = urldefrag(href)
    decoded_url = unquote(url)
    first_page = pages[0].filename

    if href == "#" and not in_split_dir:
        return href
    if not in_split_dir and decoded_url == "" and href.startswith("#"):
        return href

    if decoded_url in {"", project.html_file}:
        target_page = id_to_page.get(fragment, first_page)
        top_id = top_id_by_page.get(target_page)
        include_fragment = bool(fragment and fragment != top_id)

        if in_split_dir:
            base = "" if target_page == current_page else quote(target_page)
        else:
            base = "split_files/" + quote(target_page)

        if include_fragment:
            return f"{base}#{fragment}" if base else f"#{fragment}"
        return base or "#"

    if decoded_url.startswith("split_files/") and in_split_dir:
        return quote(Path(decoded_url).name)

    return href


def rewrite_doc_links(
    soup,
    *,
    project: DocProject,
    pages: list[SplitPage],
    id_to_page: dict[str, str],
    top_id_by_page: dict[str, str],
    current_page: str | None,
    in_split_dir: bool,
) -> None:
    for tag in soup.find_all(["a", "link"], href=True):
        tag["href"] = rewrite_doc_href(
            tag["href"],
            project=project,
            pages=pages,
            id_to_page=id_to_page,
            top_id_by_page=top_id_by_page,
            current_page=current_page,
            in_split_dir=in_split_dir,
        )


def prefix_split_resources(soup) -> None:
    resource_prefixes = ("_static/", "_images/", "_sources/")
    root_pages = {"index.html", "search.html", "genindex.html", "changelog.html"}

    def should_prefix(value: str) -> bool:
        if not value or is_external_href(value) or value.startswith(("#", "../", "/")):
            return False
        plain_url = value.split("?", 1)[0].split("#", 1)[0]
        return plain_url.startswith(resource_prefixes) or plain_url in root_pages

    for tag in soup.find_all(src=True):
        if should_prefix(tag["src"]):
            tag["src"] = "../" + tag["src"]
    for tag in soup.find_all(href=True):
        if should_prefix(tag["href"]):
            tag["href"] = "../" + tag["href"]
    for tag in soup.find_all(action=True):
        if should_prefix(tag["action"]):
            tag["action"] = "../" + tag["action"]


def patch_browser_title(soup, page_title: str, project: DocProject) -> None:
    title = soup.find("title")
    if title is not None:
        title.string = f"{page_title} — {project.display_title}"


def patch_rel_links(soup, previous_link: tuple[str, str] | None, next_link: tuple[str, str] | None) -> None:
    for tag in list(soup.find_all("link")):
        rel = tag.get("rel", [])
        if "prev" in rel or "next" in rel:
            tag.decompose()

    head = soup.find("head")
    if head is None:
        return
    for rel_name, link in (("prev", previous_link), ("next", next_link)):
        if link is None:
            continue
        href, title = link
        new_link = soup.new_tag("link", rel=rel_name, title=title, href=href)
        head.append(new_link)


def patch_footer_buttons(soup, previous_link: tuple[str, str] | None, next_link: tuple[str, str] | None) -> None:
    buttons = soup.find("div", class_="rst-footer-buttons")
    if buttons is None:
        return
    buttons.clear()

    if previous_link is not None:
        href, title = previous_link
        previous = soup.new_tag(
            "a",
            href=href,
            **{"class": "btn btn-neutral float-left", "title": title, "rel": "prev"},
        )
        previous.append("上一页 ")
        previous.append(soup.new_tag("span", **{"class": "fa fa-arrow-circle-left", "aria-hidden": "true"}))
        buttons.append(previous)

    if next_link is not None:
        href, title = next_link
        next_tag = soup.new_tag(
            "a",
            href=href,
            **{"class": "btn btn-neutral float-right", "title": title, "rel": "next"},
        )
        next_tag.append("下一页 ")
        next_tag.append(soup.new_tag("span", **{"class": "fa fa-arrow-circle-right", "aria-hidden": "true"}))
        buttons.append(next_tag)


def patch_common_page(
    soup,
    *,
    project: DocProject,
    page_title: str,
    home_href: str,
    content_root: str,
    navigable_string_type,
) -> None:
    html = soup.find("html")
    if html is not None:
        html["data-content_root"] = content_root

    patch_browser_title(soup, page_title, project)

    rst_content = soup.find("div", class_="rst-content")
    if rst_content is not None:
        add_class(rst_content, "style-external-links")

    for home in soup.select(".wy-side-nav-search a.icon-home"):
        home["href"] = home_href
        replace_text(home, project.display_title, navigable_string_type)

    for mobile_home in soup.select(".wy-nav-top a"):
        mobile_home["href"] = home_href
        replace_text(mobile_home, project.display_title, navigable_string_type)

    breadcrumb_home = soup.select_one(".wy-breadcrumbs a.icon-home")
    if breadcrumb_home is not None:
        breadcrumb_home["href"] = home_href

    aside = soup.find("li", class_="wy-breadcrumbs-aside")
    if aside is not None:
        aside.clear()

    contentinfo = soup.find("div", attrs={"role": "contentinfo"})
    if contentinfo is not None:
        paragraph = contentinfo.find("p")
        if paragraph is not None:
            replace_text(paragraph, COPYRIGHT_TEXT, navigable_string_type)


def mark_sidebar_current(soup, current_page: str | None) -> None:
    menu = soup.find("div", class_="wy-menu-vertical")
    if menu is None:
        return

    for tag in menu.select(".current"):
        remove_class(tag, "current")

    if current_page is None:
        return

    for link in menu.find_all("a", href=True):
        url, fragment = urldefrag(link["href"])
        decoded_name = unquote(Path(url).name) if url else current_page
        if decoded_name != current_page or fragment:
            continue
        add_class(link, "current")
        for parent in link.parents:
            if parent == menu:
                break
            if parent.name in {"li", "ul"}:
                add_class(parent, "current")


def collect_split_pages(soup, project: DocProject) -> tuple[list[SplitPage], dict[str, str], dict[str, str]]:
    article = soup.find("div", attrs={"itemprop": "articleBody"})
    if article is None:
        raise SystemExit(f"{project.html_file} 中找不到 articleBody，无法生成发布预览布局。")

    sections = [child for child in article.find_all("section", recursive=False) if child.get("id")]
    if not sections:
        raise SystemExit(f"{project.html_file} 中找不到一级章节，无法生成发布预览布局。")

    used_filenames: set[str] = set()
    pages: list[SplitPage] = []
    id_to_page: dict[str, str] = {}
    top_id_by_page: dict[str, str] = {}

    for section in sections:
        title = heading_text(section.find(["h1", "h2"], recursive=False))
        filename = safe_filename(title, used_filenames)
        page = SplitPage(section_id=section["id"], filename=filename, title=title)
        pages.append(page)
        top_id_by_page[filename] = section["id"]
        for nested in section.find_all("section"):
            if nested.get("id"):
                id_to_page[nested["id"]] = filename
        id_to_page[section["id"]] = filename

    return pages, id_to_page, top_id_by_page


def page_href(page: SplitPage, *, from_split_dir: bool) -> str:
    prefix = "" if from_split_dir else "split_files/"
    return prefix + quote(page.filename)


def patch_index_page(
    *,
    html_dir: Path,
    project: DocProject,
    pages: list[SplitPage],
    id_to_page: dict[str, str],
    top_id_by_page: dict[str, str],
    navigable_string_type,
) -> Path:
    index_file = html_dir / "index.html"
    BeautifulSoup, _, _ = require_bs4()
    soup = BeautifulSoup(index_file.read_text(encoding="utf-8"), "html.parser")

    patch_common_page(
        soup,
        project=project,
        page_title="沐曦开发者",
        home_href="#",
        content_root="./",
        navigable_string_type=navigable_string_type,
    )
    rewrite_doc_links(
        soup,
        project=project,
        pages=pages,
        id_to_page=id_to_page,
        top_id_by_page=top_id_by_page,
        current_page=None,
        in_split_dir=False,
    )
    mark_sidebar_current(soup, None)
    next_link = (page_href(pages[0], from_split_dir=False), pages[0].title)
    patch_footer_buttons(soup, None, next_link)
    patch_rel_links(soup, None, next_link)

    index_file.write_text(str(soup), encoding="utf-8")
    return index_file


def patch_other_root_pages(
    *,
    html_dir: Path,
    project: DocProject,
    pages: list[SplitPage],
    id_to_page: dict[str, str],
    top_id_by_page: dict[str, str],
    navigable_string_type,
) -> None:
    BeautifulSoup, _, _ = require_bs4()
    for html_file in sorted(html_dir.glob("*.html")):
        if html_file.name in {"index.html", project.html_file}:
            continue
        soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "html.parser")
        title = heading_text(soup.find(["h1", "h2"])) or html_file.stem
        patch_common_page(
            soup,
            project=project,
            page_title=title,
            home_href="index.html",
            content_root="./",
            navigable_string_type=navigable_string_type,
        )
        rewrite_doc_links(
            soup,
            project=project,
            pages=pages,
            id_to_page=id_to_page,
            top_id_by_page=top_id_by_page,
            current_page=None,
            in_split_dir=False,
        )
        mark_sidebar_current(soup, None)
        html_file.write_text(str(soup), encoding="utf-8")


def write_split_pages(
    *,
    html_file: Path,
    project: DocProject,
    pages: list[SplitPage],
    id_to_page: dict[str, str],
    top_id_by_page: dict[str, str],
    navigable_string_type,
) -> None:
    BeautifulSoup, _, _ = require_bs4()
    html_dir = html_file.parent
    split_dir = html_dir / "split_files"
    shutil.rmtree(split_dir, ignore_errors=True)
    split_dir.mkdir(parents=True, exist_ok=True)
    main_html = html_file.read_text(encoding="utf-8")

    for index, page in enumerate(pages):
        soup = BeautifulSoup(main_html, "html.parser")
        article = soup.find("div", attrs={"itemprop": "articleBody"})
        section = soup.find("section", id=page.section_id)
        if article is None or section is None:
            raise SystemExit(f"{project.html_file} 中找不到章节 {page.section_id}。")

        section.extract()
        article.clear()
        article.append(section)

        previous_page = pages[index - 1] if index > 0 else None
        next_page = pages[index + 1] if index + 1 < len(pages) else None
        previous_link = (
            (page_href(previous_page, from_split_dir=True), previous_page.title)
            if previous_page is not None
            else ("../index.html", "沐曦开发者")
        )
        next_link = (
            (page_href(next_page, from_split_dir=True), next_page.title)
            if next_page is not None
            else None
        )

        patch_common_page(
            soup,
            project=project,
            page_title=page.title,
            home_href="../index.html",
            content_root="../",
            navigable_string_type=navigable_string_type,
        )
        active = soup.find("li", class_="breadcrumb-item active")
        if active is not None:
            replace_text(active, page.title, navigable_string_type)

        rewrite_doc_links(
            soup,
            project=project,
            pages=pages,
            id_to_page=id_to_page,
            top_id_by_page=top_id_by_page,
            current_page=page.filename,
            in_split_dir=True,
        )
        prefix_split_resources(soup)
        mark_sidebar_current(soup, page.filename)
        patch_footer_buttons(soup, previous_link, next_link)
        patch_rel_links(soup, previous_link, next_link)

        (split_dir / page.filename).write_text(str(soup), encoding="utf-8")


def write_redirect(html_file: Path, target: str, title: str) -> None:
    escaped_target = target.replace('"', "%22")
    html_file.write_text(
        "\n".join(
            [
                "<!DOCTYPE html>",
                '<html lang="zh-CN">',
                "<head>",
                '  <meta charset="utf-8" />',
                f'  <meta http-equiv="refresh" content="0; url={escaped_target}" />',
                f"  <title>{title}</title>",
                "</head>",
                "<body>",
                f'  <p><a href="{escaped_target}">打开 {title}</a></p>',
                "</body>",
                "</html>",
                "",
            ]
        ),
        encoding="utf-8",
    )


def apply_preview_layout(project: DocProject, html_file: Path) -> Path:
    BeautifulSoup, NavigableString, _ = require_bs4()
    soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "html.parser")
    pages, id_to_page, top_id_by_page = collect_split_pages(soup, project)

    write_split_pages(
        html_file=html_file,
        project=project,
        pages=pages,
        id_to_page=id_to_page,
        top_id_by_page=top_id_by_page,
        navigable_string_type=NavigableString,
    )
    index_file = patch_index_page(
        html_dir=html_file.parent,
        project=project,
        pages=pages,
        id_to_page=id_to_page,
        top_id_by_page=top_id_by_page,
        navigable_string_type=NavigableString,
    )
    patch_other_root_pages(
        html_dir=html_file.parent,
        project=project,
        pages=pages,
        id_to_page=id_to_page,
        top_id_by_page=top_id_by_page,
        navigable_string_type=NavigableString,
    )
    write_redirect(html_file, "index.html", project.display_title)
    print(f"[preview] {project.name}: split {len(pages)} pages under {html_file.parent / 'split_files'}")
    return index_file


def local_asset_path(value: str, *, base_dir: Path, html_dir: Path) -> Path | None:
    if not value or is_external_href(value) or is_data_uri(value):
        return None
    if value.startswith(("#", "/", "\\")):
        return None

    url, _fragment = urldefrag(value)
    url = url.split("?", 1)[0]
    if not url:
        return None

    candidate = (base_dir / unquote(url)).resolve()
    html_root = html_dir.resolve()
    try:
        candidate.relative_to(html_root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def is_local_asset_reference(value: str) -> bool:
    if not value or is_external_href(value) or is_data_uri(value):
        return False
    return not value.startswith(("#", "/", "\\"))


def data_uri_for(path: Path, cache: dict[Path, str]) -> str:
    resolved = path.resolve()
    if resolved in cache:
        return cache[resolved]

    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    uri = f"data:{mime_type};base64,{payload}"
    cache[resolved] = uri
    return uri


CSS_URL_RE = re.compile(r"url\((?P<quote>['\"]?)(?P<value>[^)'\"\n]+)(?P=quote)\)")


def inline_css_urls(css: str, *, css_dir: Path, html_dir: Path, asset_cache: dict[Path, str]) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group("value").strip()
        if value.startswith(("#", "data:")) or is_external_href(value):
            return match.group(0)
        path = local_asset_path(value, base_dir=css_dir, html_dir=html_dir)
        if path is None:
            return match.group(0)
        return f"url({data_uri_for(path, asset_cache)})"

    return CSS_URL_RE.sub(replace, css)


def inline_srcset(value: str, *, base_dir: Path, html_dir: Path, asset_cache: dict[Path, str]) -> str:
    parts: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        tokens = item.split()
        path = local_asset_path(tokens[0], base_dir=base_dir, html_dir=html_dir)
        if path is not None:
            tokens[0] = data_uri_for(path, asset_cache)
        parts.append(" ".join(tokens))
    return ", ".join(parts)


def inline_local_assets(soup, *, html_dir: Path) -> None:
    asset_cache: dict[Path, str] = {}

    for tag in list(soup.find_all("link", href=True)):
        href = tag["href"]
        path = local_asset_path(href, base_dir=html_dir, html_dir=html_dir)
        rel = {str(item).lower() for item in tag.get("rel", [])}
        if path is None:
            if "stylesheet" in rel and is_local_asset_reference(href):
                tag.decompose()
            continue

        if "stylesheet" in rel:
            style = soup.new_tag("style")
            style["data-inline-source"] = href
            css = path.read_text(encoding="utf-8")
            style.string = inline_css_urls(
                css,
                css_dir=path.parent,
                html_dir=html_dir,
                asset_cache=asset_cache,
            )
            tag.replace_with(style)
        elif rel & {"icon", "shortcut icon", "apple-touch-icon"}:
            tag["href"] = data_uri_for(path, asset_cache)

    for tag in soup.find_all("script", src=True):
        src = tag["src"]
        path = local_asset_path(src, base_dir=html_dir, html_dir=html_dir)
        if path is None:
            continue
        script = soup.new_tag("script")
        for attr in ("type", "id"):
            if tag.has_attr(attr):
                script[attr] = tag[attr]
        script["data-inline-source"] = src
        script.string = path.read_text(encoding="utf-8")
        tag.replace_with(script)

    for tag in soup.find_all(src=True):
        src = tag["src"]
        path = local_asset_path(src, base_dir=html_dir, html_dir=html_dir)
        if path is not None:
            tag["src"] = data_uri_for(path, asset_cache)

    for tag in soup.find_all(srcset=True):
        tag["srcset"] = inline_srcset(
            tag["srcset"],
            base_dir=html_dir,
            html_dir=html_dir,
            asset_cache=asset_cache,
        )

    for tag in soup.find_all(href=True):
        if tag.name == "link":
            continue
        path = local_asset_path(tag["href"], base_dir=html_dir, html_dir=html_dir)
        if path is not None:
            tag["href"] = data_uri_for(path, asset_cache)


def rewrite_single_file_links(soup, *, project: DocProject) -> None:
    local_html_files = {"", "index.html", project.html_file}
    for tag in soup.find_all("a", href=True):
        href = tag["href"]
        if not href or is_external_href(href) or href.startswith("#"):
            continue

        url, fragment = urldefrag(href)
        decoded_url = unquote(url)
        if decoded_url in local_html_files or decoded_url.endswith(".html"):
            tag["href"] = f"#{fragment}" if fragment else "#"


def write_single_file(project: DocProject, html_file: Path, target_file: Path) -> Path:
    BeautifulSoup, NavigableString, _ = require_bs4()
    soup = BeautifulSoup(html_file.read_text(encoding="utf-8"), "html.parser")
    page_title = heading_text(soup.find(["h1", "h2"])) or project.display_title

    patch_common_page(
        soup,
        project=project,
        page_title=page_title,
        home_href="#",
        content_root="./",
        navigable_string_type=NavigableString,
    )
    rewrite_single_file_links(soup, project=project)
    inline_local_assets(soup, html_dir=html_file.parent)

    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(str(soup), encoding="utf-8")
    print(f"[single-file] {project.name}: {target_file}")
    return target_file


def main() -> int:
    args = parse_args()
    docs_root = args.docs_root.expanduser().resolve()
    x201_docs_root = args.x201_docs_root.expanduser().resolve()
    out_dir = args.out_dir.expanduser().resolve()
    sphinx_build = resolve_executable(args.sphinx_build)

    if not docs_root.is_dir():
        raise SystemExit(f"找不到 C500_Docs 目录: {docs_root}")
    if not x201_docs_root.is_dir():
        raise SystemExit(f"找不到 X201_Docs 目录: {x201_docs_root}")

    docs_roots = {"c500": docs_root, "x201": x201_docs_root}
    project_roots = {project: validate_project(docs_roots, project) for project in PROJECTS}

    if args.clean or args.single_file:
        for path in [out_dir / project.name for project in PROJECTS]:
            shutil.rmtree(path, ignore_errors=True)
        for path in [out_dir / f"{project.name}.html" for project in PROJECTS]:
            path.unlink(missing_ok=True)
        shutil.rmtree(out_dir / "_doctrees", ignore_errors=True)
        shutil.rmtree(out_dir / "_single_build", ignore_errors=True)

    out_dir.mkdir(parents=True, exist_ok=True)
    build_out_dir = out_dir / "_single_build" if args.single_file else out_dir
    results = []
    for project in PROJECTS:
        html_file = build_project(
            sphinx_build=sphinx_build,
            project=project,
            project_root=project_roots[project],
            out_dir=build_out_dir,
            doctree_dir=out_dir / "_doctrees" / project.name,
            strict=args.strict,
            jobs=args.jobs,
        )
        if args.single_file:
            results.append(write_single_file(project, html_file, out_dir / f"{project.name}.html"))
        elif args.plain_sphinx:
            results.append(html_file)
        else:
            results.append(apply_preview_layout(project, html_file))

    if args.single_file:
        shutil.rmtree(build_out_dir, ignore_errors=True)
        shutil.rmtree(out_dir / "_doctrees", ignore_errors=True)

    print("\nHTML 入口:")
    for html_file in results:
        print(f"  {html_file}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        print(f"构建失败，退出码: {exc.returncode}", file=sys.stderr)
        raise SystemExit(exc.returncode)

#!/usr/bin/env python3
"""
EPUB 解析器：把 epub 转成按章节组织的 markdown，提取杨天真的核心论点。

用法:
    python3 parse_epub.py <epub_path> <output_md_path>
"""

import sys, zipfile, re, json
from pathlib import Path
from html.parser import HTMLParser
from xml.etree import ElementTree as ET


class TextExtractor(HTMLParser):
    """提取 HTML 纯文本，保留段落结构"""
    def __init__(self):
        super().__init__()
        self.parts = []
        self.cur_para = []
        self.in_skip = False
        self.heading_level = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.in_skip = True
        if tag in ('h1', 'h2', 'h3', 'h4'):
            self.flush_para()
            self.heading_level = int(tag[1])
        if tag in ('p', 'div', 'br'):
            self.flush_para()

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.in_skip = False
        if tag in ('h1', 'h2', 'h3', 'h4'):
            text = ''.join(self.cur_para).strip()
            if text:
                self.parts.append(('heading', self.heading_level, text))
            self.cur_para = []
            self.heading_level = 0
        elif tag in ('p', 'div'):
            self.flush_para()

    def handle_data(self, data):
        if self.in_skip:
            return
        self.cur_para.append(data)

    def flush_para(self):
        text = ''.join(self.cur_para).strip()
        if text and self.heading_level == 0:
            text = re.sub(r'\s+', ' ', text)
            if len(text) > 1:
                self.parts.append(('para', 0, text))
        if self.heading_level == 0:
            self.cur_para = []


def parse_opf(opf_content, opf_dir):
    """从 OPF XML 提取 spine（章节顺序）和 manifest"""
    ns = {'opf': 'http://www.idpf.org/2007/opf'}
    root = ET.fromstring(opf_content)

    # manifest: id → href
    manifest = {}
    for item in root.findall('.//opf:item', ns):
        manifest[item.get('id')] = item.get('href')

    # spine: 顺序
    spine = []
    for itemref in root.findall('.//opf:itemref', ns):
        idref = itemref.get('idref')
        if idref in manifest:
            spine.append(manifest[idref])

    # title
    title = ''
    title_el = root.find('.//{http://purl.org/dc/elements/1.1/}title')
    if title_el is not None:
        title = title_el.text or ''

    return spine, title


def extract_html_to_chapter(html, href):
    """单个 HTML 文件 → chapter 结构"""
    extractor = TextExtractor()
    extractor.feed(html)
    extractor.flush_para()
    if extractor.parts:
        return {'href': href, 'parts': extractor.parts}
    return None


def parse_epub(epub_path):
    """主流程：解压 EPUB，按 spine 顺序提取所有章节文本。spine 失败时 fallback 到遍历所有 html。"""
    with zipfile.ZipFile(epub_path, 'r') as z:
        all_files = z.namelist()

        # 找 OPF 文件
        with z.open('META-INF/container.xml') as f:
            container = f.read()
        m = re.search(rb'full-path="([^"]+)"', container)
        if not m:
            raise ValueError("无法找到 OPF 文件")
        opf_path = m.group(1).decode()
        opf_dir = str(Path(opf_path).parent)

        # 读 OPF
        with z.open(opf_path) as f:
            opf_content = f.read()
        spine, title = parse_opf(opf_content, opf_dir)

        # Try 1: spine 顺序
        chapters = []
        spine_total_chars = 0
        for href in spine:
            full_path = f"{opf_dir}/{href}" if opf_dir and opf_dir != '.' else href
            try:
                with z.open(full_path) as f:
                    html = f.read().decode('utf-8', errors='ignore')
            except KeyError:
                continue
            ch = extract_html_to_chapter(html, href)
            if ch:
                chapters.append(ch)
                spine_total_chars += sum(len(t) for _, _, t in ch['parts'])

        # Fallback: 如果 spine 提取出来字数太少，遍历所有 .html/.xhtml/.htm
        if spine_total_chars < 5000:
            print(f"  ⚠️ spine 字数仅 {spine_total_chars}，启用 fallback 全文件遍历")
            chapters = []
            html_files = sorted([f for f in all_files
                                 if f.lower().endswith(('.html', '.xhtml', '.htm'))
                                 and 'cover' not in f.lower()
                                 and 'toc' not in f.lower()])
            for href in html_files:
                try:
                    with z.open(href) as f:
                        html = f.read().decode('utf-8', errors='ignore')
                except KeyError:
                    continue
                ch = extract_html_to_chapter(html, href)
                if ch:
                    chapters.append(ch)

    return title, chapters


def chapters_to_markdown(title, chapters, source_path):
    """组装 markdown 输出"""
    lines = [f"# {title}\n"]
    lines.append(f"**来源**: `{source_path}`\n")

    total_chars = 0
    for ch in chapters:
        for kind, level, text in ch['parts']:
            if kind == 'heading':
                lines.append(f"\n{'#' * (level + 1)} {text}\n")
            elif kind == 'para':
                lines.append(text)
                lines.append('')
                total_chars += len(text)

    return '\n'.join(lines), total_chars


# 杨天真自创术语 + 高价值短语模式（用于自动标注）
SIGNATURE_TERMS = [
    '底层自信', '喜恶同因', '经纪人视角', '把自己当回事',
    '能量场', '回馈分析法', '九分仪', '高情商公式',
    '通透', '微习惯', '主动攒局',
]

HIGH_VALUE_PATTERNS = {
    'mental_model': [r'我觉得', r'我认为', r'本质上', r'其实就是', r'核心是', r'关键是'],
    'decision_rule': [r'我从不', r'我一定', r'我绝不', r'必须', r'宁可.{0,15}也不'],
    'self_correction': [r'以前我', r'后来我', r'之前我以为', r'我才发现'],
    'experience': [r'我做经纪人', r'我那时候', r'我当年', r'我以前', r'壹心'],
    'reframing': [r'换个角度', r'反过来想', r'其实不是'],
}


def extract_high_value_passages(chapters, max_passages=80):
    """提取含高价值标记的段落"""
    passages = []
    for ch_idx, ch in enumerate(chapters):
        for kind, level, text in ch['parts']:
            if kind != 'para' or len(text) < 30:
                continue
            tags = []
            sigs = [s for s in SIGNATURE_TERMS if s in text]
            if sigs:
                tags.append('signature')
            for tag, patterns in HIGH_VALUE_PATTERNS.items():
                if any(re.search(p, text) for p in patterns):
                    tags.append(tag)
            if tags:
                score = len(tags) + len(sigs) * 2
                passages.append({
                    'score': score,
                    'tags': tags,
                    'sigs': sigs,
                    'text': text,
                    'ch_idx': ch_idx,
                })
    passages.sort(key=lambda x: -x['score'])
    return passages[:max_passages]


def main():
    if len(sys.argv) < 3:
        print("用法: parse_epub.py <epub> <output_md>")
        sys.exit(1)

    epub_path = sys.argv[1]
    out_path = sys.argv[2]

    print(f"解析 {epub_path}...")
    title, chapters = parse_epub(epub_path)

    md, total_chars = chapters_to_markdown(title, chapters, epub_path)
    Path(out_path).write_text(md, encoding='utf-8')
    print(f"✅ 全文落盘: {out_path}")
    print(f"   章节: {len(chapters)}, 总字数: {total_chars:,}")

    # 高价值段落
    hv = extract_high_value_passages(chapters)
    hv_path = out_path.replace('.md', '_highlights.md')
    hv_lines = [f"# {title} — 高价值段落（自动标注）\n"]
    hv_lines.append(f"> 共 {len(hv)} 条按价值分排序。来自全书自动提取。\n")
    for i, p in enumerate(hv, 1):
        sigs_str = ' '.join(f'**「{s}」**' for s in p['sigs'])
        tags_str = ' '.join(f'`{t}`' for t in p['tags'])
        hv_lines.append(f"\n## #{i} [score={p['score']}] {tags_str} {sigs_str}\n")
        hv_lines.append(f"> {p['text']}\n")

    Path(hv_path).write_text('\n'.join(hv_lines), encoding='utf-8')
    print(f"✅ 高价值段落: {hv_path} ({len(hv)} 条)")


if __name__ == '__main__':
    main()

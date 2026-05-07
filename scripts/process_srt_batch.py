#!/usr/bin/env python3
"""
批量处理杨天真课程的132集SRT字幕：
1. 清洗为纯文本（去时间戳、序号、重复行）
2. 自动按"公式集(P1-P32)"和"案例集(P33-P132)"分类
3. 每集生成结构化 markdown：含元信息 + 清洗后的全文
4. 生成总目录索引 INDEX.md

用法:
    python3 process_srt_batch.py <ximalaya-32-formulas目录>
"""

import sys
import re
from pathlib import Path


def clean_srt(content: str) -> str:
    """清洗SRT为纯文本，合并为可读段落"""
    lines = content.strip().split('\n')
    texts = []

    for line in lines:
        line = line.strip()
        if re.match(r'^\d+$', line):
            continue
        if re.match(r'\d{2}:\d{2}:\d{2}', line):
            continue
        if not line:
            continue
        line = re.sub(r'<[^>]+>', '', line)
        line = re.sub(r'align:.*$|position:.*$', '', line).strip()
        if line:
            texts.append(line)

    # 去除连续重复
    deduped = []
    for text in texts:
        if not deduped or text != deduped[-1]:
            deduped.append(text)

    # 合并段落：累积到 ~200 字或遇到句末标点
    result = []
    current = []
    for text in deduped:
        current.append(text)
        joined = ' '.join(current)
        if len(joined) > 200 or re.search(r'[。！？.!?]$', text):
            result.append(joined)
            current = []
    if current:
        result.append(' '.join(current))

    return '\n\n'.join(result)


def parse_filename(filename: str):
    """从B站文件名提取信息

    示例: P002-杨天真的32个高情商公式+100例高情商案例实战 132节完整版 p02 【公式2】自我定位用好自己的优劣势，建立你的能量场.ai-zh.srt
    返回: (集号, 类型, 子标题)
    """
    # 提取集号
    m = re.match(r'P(\d{3})-', filename)
    if not m:
        return None, None, None
    episode_num = int(m.group(1))

    # 提取主题块（公式 N / 案例 X-Y）
    # 在【...】中
    block_match = re.search(r'【([^】]+)】(.+?)\.ai-zh\.srt', filename)
    if block_match:
        block_type = block_match.group(1).strip()  # 例如 "公式2" 或 "案例1-1"
        subtitle = block_match.group(2).strip()
    else:
        block_type = "未知"
        subtitle = ""

    return episode_num, block_type, subtitle


def main():
    if len(sys.argv) < 2:
        print("用法: python3 process_srt_batch.py <directory>")
        sys.exit(1)

    base_dir = Path(sys.argv[1])
    if not base_dir.exists():
        print(f"❌ 目录不存在: {base_dir}")
        sys.exit(1)

    # 创建分类子目录
    formulas_dir = base_dir / "01-公式集"
    cases_dir = base_dir / "02-案例集"
    formulas_dir.mkdir(exist_ok=True)
    cases_dir.mkdir(exist_ok=True)

    srt_files = sorted(base_dir.glob("P*.srt"))
    print(f"找到 {len(srt_files)} 个SRT文件")

    formulas_index = []
    cases_index = []
    failed = []

    for srt in srt_files:
        episode_num, block_type, subtitle = parse_filename(srt.name)
        if episode_num is None:
            failed.append(srt.name)
            continue

        # 读取并清洗
        try:
            content = srt.read_text(encoding='utf-8')
            transcript = clean_srt(content)
        except Exception as e:
            print(f"❌ 处理失败 {srt.name}: {e}")
            failed.append(srt.name)
            continue

        # 决定输出路径
        is_formula = block_type.startswith("公式")
        out_dir = formulas_dir if is_formula else cases_dir

        # 安全文件名
        safe_subtitle = re.sub(r'[/\\:*?"<>|]', '_', subtitle)[:60]
        out_name = f"P{episode_num:03d}-{block_type}-{safe_subtitle}.md"
        out_path = out_dir / out_name

        # 生成 markdown
        url = f"https://www.bilibili.com/video/BV1vWkmBhECJ/?p={episode_num}"
        char_count = len(transcript.replace('\n', '').replace(' ', ''))

        md = f"""# {block_type}：{subtitle}

**集号**: P{episode_num:03d}
**B站URL**: {url}
**字数**: {char_count}
**类型**: {'核心公式' if is_formula else '案例实战'}

---

{transcript}
"""
        out_path.write_text(md, encoding='utf-8')

        index_entry = (episode_num, block_type, subtitle, char_count, out_name)
        if is_formula:
            formulas_index.append(index_entry)
        else:
            cases_index.append(index_entry)

    # 生成总目录 INDEX.md
    index_md = f"""# 杨天真《32个高情商公式+100例案例实战》— 索引

**B站源**: https://www.bilibili.com/video/BV1vWkmBhECJ/
**总集数**: {len(srt_files)} 集
**公式数**: {len(formulas_index)} 集
**案例数**: {len(cases_index)} 集
**失败**: {len(failed)} 集

---

## 📘 第一部分：32个核心公式（P1-P32）

| 集号 | 公式 | 主题 | 字数 |
|------|------|------|------|
"""
    for ep, block, sub, cnt, fname in sorted(formulas_index):
        index_md += f"| P{ep:03d} | {block} | {sub} | {cnt} |\n"

    index_md += "\n---\n\n## 📗 第二部分：100个案例实战（P33-P132）\n\n"
    index_md += "| 集号 | 案例 | 主题 | 字数 |\n|------|------|------|------|\n"
    for ep, block, sub, cnt, fname in sorted(cases_index):
        index_md += f"| P{ep:03d} | {block} | {sub} | {cnt} |\n"

    if failed:
        index_md += f"\n---\n\n## ❌ 处理失败列表\n\n"
        for f in failed:
            index_md += f"- {f}\n"

    (base_dir / "INDEX.md").write_text(index_md, encoding='utf-8')

    # 总结
    total_chars = sum(e[3] for e in formulas_index) + sum(e[3] for e in cases_index)
    print(f"\n✅ 处理完成")
    print(f"   公式集: {len(formulas_index)} 集 → {formulas_dir}")
    print(f"   案例集: {len(cases_index)} 集 → {cases_dir}")
    print(f"   总字数: {total_chars:,}")
    print(f"   索引文件: {base_dir / 'INDEX.md'}")
    if failed:
        print(f"   ⚠️ 失败: {len(failed)} 集")


if __name__ == '__main__':
    main()

#!/usr/bin/env python3
"""
Layer 5: 跨集聚合脚本

把所有 enhanced-llm 产物聚合到 6 Agent 文件结构：
- 02-conversations.md: 杨天真在 50 集播客中的高价值对话片段（按 Topic 分类）
- 03-expression-dna.md: 跨集表达 DNA 提取（自创术语 / 高频词 / 句式特征）

输入: enhanced-llm/*/full.md + meta.json
输出: references/research/02-conversations.md（追加节）
       references/research/03-expression-dna.md（追加节）
       references/research/golden_moments.md（金句索引层，跳转锚点）
"""

import json, re, sys
from pathlib import Path
from collections import Counter, defaultdict

ENH = Path('/Users/elroy/.claude/skills/yangtianzhen-perspective/references/sources/transcripts/xiaoyuzhou/enhanced-llm')
RES = Path('/Users/elroy/.claude/skills/yangtianzhen-perspective/references/research')

HOST = "杨天真"

# 杨天真自创术语清单（Phase 2 提炼时检验）
SIGNATURE_TERMS = [
    '底层自信', '喜恶同因', '经纪人视角', '把自己当回事儿',
    '能量场', '回馈分析法', '九分仪', '高情商公式',
    '通透', '微习惯', '人脉搜索', '主动攒局',
]

# Topic 关键词字典（用于把杨天真的发言分类到不同心智主题）
TOPIC_KEYWORDS = {
    '自我定位与优势': ['自我定位', '优势', '劣势', '特点', '特质', '能量场', '九分仪', '回馈分析法'],
    '底层自信': ['底层自信', '自信', '自我反馈', '自卑'],
    '决策与判断': ['决策', '判断', '选择', '取舍', '路线'],
    '职场进阶': ['职场', '汇报', '老板', '领导', '同事', '晋升', '涨薪'],
    '向上管理': ['向上', '主动汇报', '主动谈判', '主动求助', '主动推动'],
    '人际与人脉': ['人脉', '关系', '链接', '网络', '搭建关系', '维护'],
    '处理 PUA / 拒绝': ['PUA', '拒绝', '说不', '边界'],
    '艰难谈话': ['艰难谈话', '冲突', '分手', '开人', '辞退'],
    '时间与平衡': ['时间管理', '平衡', '聚焦', '优先级'],
    '失败与成长': ['失败', '挫折', '成长', '自驱', '内耗'],
    '情绪与压力': ['情绪', '压力', '内耗', '负面', '焦虑'],
    '人设与表达': ['人设', '表达', '沟通', '说话'],
    '创业与商业': ['创业', '公司', '团队', '管理', '老板', 'CEO'],
}


def load_episode(ep_dir):
    """加载单集 enhanced 数据"""
    meta_file = ep_dir / 'meta.json'
    full_file = ep_dir / 'full.md'
    if not meta_file.exists() or not full_file.exists():
        return None
    with open(meta_file) as f:
        meta = json.load(f)
    full_text = full_file.read_text(encoding='utf-8')
    return {'meta': meta, 'full_text': full_text, 'dir': ep_dir.name}


def extract_yt_turns(full_text):
    """从 full.md 提取所有杨天真的 turn (text + timestamp)"""
    # 格式: **[MM:SS] 杨天真**: ... \n> text
    turns = []
    pattern = re.compile(r'\*\*\[(\d{2}:\d{2})\]\s+杨天真\*\*:([^\n]*)\n>\s*([^\n]+)')
    for m in pattern.finditer(full_text):
        ts, tags_part, text = m.group(1), m.group(2), m.group(3)
        tags = re.findall(r'`([^`]+)`', tags_part)
        turns.append({'ts': ts, 'text': text.strip(), 'tags': tags})
    return turns


def classify_topic(text):
    """根据关键词把 turn 归到 Topic"""
    matches = []
    for topic, keywords in TOPIC_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            matches.append(topic)
    return matches if matches else ['其他']


def find_signature_terms(text):
    """找出含有的自创术语"""
    return [t for t in SIGNATURE_TERMS if t in text]


def extract_high_freq_phrases(all_yt_text, top_k=30):
    """提取杨天真发言中的高频短语（2-4 字组合）"""
    # 简单的字符 n-gram 统计
    text = all_yt_text
    # 去除标点和空格
    clean = re.sub(r'[^一-鿿]', '', text)
    # 2-3 字 n-gram
    ngrams_3 = Counter(clean[i:i+3] for i in range(len(clean)-2))
    ngrams_4 = Counter(clean[i:i+4] for i in range(len(clean)-3))

    # 过滤太常见的功能词
    STOPWORDS_3 = {'你的的', '我觉得', '是不是', '什么是', '不是说', '就是说',
                   '那个时', '我跟你', '你知道', '我就是', '是因为', '一定要'}
    # 保留 mental model 相关、个性短语
    interesting_3 = [(g, c) for g, c in ngrams_3.most_common(200)
                     if c >= 5 and g not in STOPWORDS_3]
    interesting_4 = [(g, c) for g, c in ngrams_4.most_common(100) if c >= 3]
    return interesting_3[:top_k], interesting_4[:top_k]


def main():
    episodes = []
    for ep_dir in sorted(ENH.iterdir()):
        if ep_dir.is_dir():
            ep = load_episode(ep_dir)
            if ep:
                episodes.append(ep)
    print(f"加载 {len(episodes)} 集 enhanced data")

    # 1. 收集所有 YT turns（带 episode 上下文）
    all_yt_turns = []  # [{vol, title, ts, text, tags, topics, sigs}]
    for ep in episodes:
        meta = ep['meta']
        title = meta.get('episode_title', '')
        ep_id = meta.get('episode_id', '')
        # 提取 vol 号
        vol_m = re.search(r'vol\.?\s*(\d+)', title, re.IGNORECASE)
        vol = vol_m.group(1) if vol_m else '?'
        if 'vol' not in title.lower():
            vol = '00'

        yt_turns = extract_yt_turns(ep['full_text'])
        for t in yt_turns:
            t['vol'] = vol
            t['episode_title'] = title
            t['episode_id'] = ep_id
            t['ep_dir'] = ep['dir']
            t['topics'] = classify_topic(t['text'])
            t['sigs'] = find_signature_terms(t['text'])
            all_yt_turns.append(t)

    print(f"杨天真总 turn 数: {len(all_yt_turns)}")

    # 2. 按 Topic 聚合 → 02-conversations.md 追加节
    by_topic = defaultdict(list)
    for t in all_yt_turns:
        for topic in t['topics']:
            by_topic[topic].append(t)

    # 优先取 long_form / mental_model / decision_rule / counter_question 等高价值 tag
    HIGH_VALUE_TAGS = {'long_form', 'mental_model', 'decision_rule', 'counter_question',
                       'experience', 'reframing', 'signature_term'}

    convo_section = ['\n\n---\n\n## 跨集聚合（Layer 5 自动生成）\n']
    convo_section.append(f'> 来源：{len(episodes)} 集小宇宙 enhanced-llm 数据。共 {len(all_yt_turns)} 个杨天真 turn。\n')
    convo_section.append('> 按 Topic 分类。每个 Topic 下保留含蒸馏相关 tag 的高价值发言。\n\n')

    # Topic 排序：按 turn 数量降序
    topic_order = sorted(by_topic.keys(), key=lambda t: -len(by_topic[t]))

    for topic in topic_order:
        if topic == '其他':
            continue
        turns = by_topic[topic]
        # 过滤：保留有高价值 tag 的或长发言
        high_value = [t for t in turns
                      if (set(t.get('tags', [])) & HIGH_VALUE_TAGS) or len(t['text']) > 80]
        if not high_value:
            continue
        # 限制每个 topic 最多 30 条
        high_value = sorted(high_value, key=lambda t: -len(t['text']))[:30]

        convo_section.append(f'### Topic: {topic}（{len(turns)} 个 turn，下面取最高价值 {len(high_value)} 条）\n')
        for t in high_value:
            tags_str = ' '.join(f'`{tg}`' for tg in t.get('tags', []))
            sigs_str = ' '.join(f'**「{s}」**' for s in t['sigs']) if t['sigs'] else ''
            convo_section.append(f"\n**vol.{t['vol']} [{t['ts']}]** {tags_str} {sigs_str}\n")
            convo_section.append(f"> {t['text']}\n")
            convo_section.append(f"> *(集名：{t['episode_title'][:50]}...)*\n")
        convo_section.append('\n')

    # 写入 02-conversations.md（追加）
    convo_file = RES / '02-conversations.md'
    existing = convo_file.read_text(encoding='utf-8') if convo_file.exists() else ''
    # 移除之前的「跨集聚合」section
    existing = re.sub(r'\n\n---\n\n## 跨集聚合（Layer 5 自动生成）.*$', '', existing, flags=re.DOTALL)
    convo_file.write_text(existing + ''.join(convo_section), encoding='utf-8')

    # 3. 表达 DNA → 03-expression-dna.md 追加节
    all_yt_text = ' '.join(t['text'] for t in all_yt_turns)

    # 自创术语统计
    sig_counts = Counter()
    sig_locations = defaultdict(list)
    for t in all_yt_turns:
        for s in t['sigs']:
            sig_counts[s] += 1
            sig_locations[s].append((t['vol'], t['ts'], t['text'][:80]))

    # 高频短语
    ngrams_3, ngrams_4 = extract_high_freq_phrases(all_yt_text)

    dna_section = ['\n\n---\n\n## 跨集聚合（Layer 5 自动生成）\n']
    dna_section.append(f'> 来源：{len(episodes)} 集 × {len(all_yt_turns)} 个杨天真 turn。总字数 {len(all_yt_text):,} 字。\n\n')

    dna_section.append('### 自创术语 / 标签语 出现频次\n\n')
    dna_section.append('| 术语 | 出现次数 | 第一次出现 |\n|------|---------|----------|\n')
    for term, count in sig_counts.most_common():
        if sig_locations[term]:
            vol, ts, sample = sig_locations[term][0]
            dna_section.append(f'| **{term}** | {count} | vol.{vol} [{ts}] {sample[:50]}... |\n')

    dna_section.append('\n### 高频 3 字短语（Top 20，可能是口头禅或思维模式标记）\n\n')
    for phrase, count in ngrams_3[:20]:
        dna_section.append(f'- `{phrase}` × {count}\n')

    dna_section.append('\n### 高频 4 字短语（Top 15）\n\n')
    for phrase, count in ngrams_4[:15]:
        dna_section.append(f'- `{phrase}` × {count}\n')

    # 句式特征统计
    questions = sum(1 for t in all_yt_turns if '？' in t['text'] or '?' in t['text'])
    statements = len(all_yt_turns) - questions
    long_form = sum(1 for t in all_yt_turns if len(t['text']) > 100)
    short_form = sum(1 for t in all_yt_turns if len(t['text']) < 30)

    dna_section.append('\n### 句式特征统计\n\n')
    dna_section.append(f'- 总 turn 数: {len(all_yt_turns)}\n')
    dna_section.append(f'- 含问号的 turn: {questions} ({questions/len(all_yt_turns)*100:.1f}%) — 提问/反问占比\n')
    dna_section.append(f'- 长发言（>100 字）: {long_form} ({long_form/len(all_yt_turns)*100:.1f}%)\n')
    dna_section.append(f'- 短发言（<30 字）: {short_form} ({short_form/len(all_yt_turns)*100:.1f}%) — 短反应/承接占比\n')

    # 写入 03-expression-dna.md（追加）
    dna_file = RES / '03-expression-dna.md'
    existing = dna_file.read_text(encoding='utf-8') if dna_file.exists() else ''
    existing = re.sub(r'\n\n---\n\n## 跨集聚合（Layer 5 自动生成）.*$', '', existing, flags=re.DOTALL)
    dna_file.write_text(existing + ''.join(dna_section), encoding='utf-8')

    # 4. 金句库 golden_moments.md（索引层）
    gm_file = RES / 'golden_moments.md'
    gm = ['# 金句库 / Golden Moments — 索引层\n\n']
    gm.append(f'> 来源：{len(episodes)} 集小宇宙 + 132 集喜马拉雅。\n')
    gm.append('> 仅保留**含至少 2 个高价值 tag** 或**含自创术语**的杨天真发言。Phase 2 提炼时按此索引回到 enhanced-llm/full.md 看上下文。\n\n')

    golden = []
    for t in all_yt_turns:
        tags_set = set(t.get('tags', []))
        score = len(tags_set & HIGH_VALUE_TAGS) + len(t['sigs']) * 2  # 自创术语权重高
        if score >= 2:
            golden.append((score, t))
    golden.sort(key=lambda x: -x[0])

    gm.append(f'## 金句总数: {len(golden)}\n\n')
    for i, (score, t) in enumerate(golden[:200], 1):
        tags_str = ' '.join(f'`{tg}`' for tg in t.get('tags', []))
        sigs_str = ' '.join(f'**「{s}」**' for s in t['sigs']) if t['sigs'] else ''
        gm.append(f"### #{i} [score={score}] vol.{t['vol']} [{t['ts']}]\n")
        gm.append(f'{tags_str} {sigs_str}\n')
        gm.append(f"> {t['text']}\n\n")
        gm.append(f"**回到原集**: enhanced-llm/{t['ep_dir']}/full.md\n")
        gm.append(f"**集名**: {t['episode_title']}\n\n---\n\n")

    gm_file.write_text(''.join(gm), encoding='utf-8')

    # 总结
    print(f"\n✅ Layer 5 聚合完成")
    print(f"   02-conversations.md: 添加 {len([t for t in topic_order if t != '其他'])} 个 Topic 聚合")
    print(f"   03-expression-dna.md: {len(sig_counts)} 个自创术语 + {len(ngrams_3)} 个高频 3 字短语")
    print(f"   golden_moments.md: {len(golden)} 个金句（已写前 200）")


if __name__ == '__main__':
    main()

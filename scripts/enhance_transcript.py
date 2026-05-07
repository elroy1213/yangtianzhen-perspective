#!/usr/bin/env python3
"""
Layer 1-4 处理流水线：paraformer raw JSON → 蒸馏可用产物

输入: --raw <paraformer raw JSON> --episode-id <小宇宙 episode id> --output-dir <目录>
输出: full.md + extract.md + meta.json

Layer 1: 说话人修正（规则版）
Layer 2: Turn 合并 + 章节对齐
Layer 3: 高价值片段标注
Layer 4: 元数据
"""

import json, re, sys, urllib.request, argparse
from pathlib import Path
from collections import Counter

HOST_NAME = "杨天真"

# === Layer 1 规则: 高置信度主持人锚点 ===
HOST_PATTERNS = [
    r'欢迎来到天真不天真', r'欢迎收听', r'今天.{0,5}邀请',
    r'今天.{0,5}嘉宾', r'我是杨天真', r'今天我们聊',
    r'今天的主题', r'我的播客天真不天真',
]

# 杨天真专属术语（强证据：含此词大概率是她）
YT_SIGNATURE_TERMS = [
    '底层自信', '喜恶同因', '经纪人视角', '把自己当回事儿',
    '通透', '能量场', '回馈分析法', '九分仪',
    '高情商公式', '艺人', '壹心',
]

# 高价值发言模式
HIGH_VALUE_PATTERNS = {
    'mental_model':    [r'我觉得', r'我认为', r'本质上', r'其实就是', r'核心是', r'关键是'],
    'decision_rule':   [r'我从不', r'我一定', r'我绝不', r'必须', r'宁可.{0,15}也不', r'就两个原则'],
    'reframing':       [r'换个角度', r'反过来想', r'其实不是', r'看到的.{0,5}其实'],
    'counter_question':[r'你怎么看', r'你为什么', r'那你是', r'你觉得呢'],
    'experience':      [r'我当年', r'我以前', r'我做经纪人', r'我那时候'],
}

def rule_based_correction(sentences):
    """规则修正说话人，返回 (corrected_sentences, yt_speaker_id)"""
    yt_anchors = set()
    for i, s in enumerate(sentences):
        text = s['text']
        # 主持人特征句
        for p in HOST_PATTERNS:
            if re.search(p, text):
                yt_anchors.add(i)
                break
        if i in yt_anchors:
            continue
        # 专属术语
        if any(term in text for term in YT_SIGNATURE_TERMS):
            yt_anchors.add(i)
            continue
        # 短问句模式（"你..."开头 + 疑问号）
        if re.match(r'^你.{2,40}[？?]$', text):
            yt_anchors.add(i)

    # 看哪个 paraformer speaker_id 在 anchor 里出现最多 → 那就是杨天真
    if yt_anchors:
        anchor_spk = Counter(sentences[i]['speaker_id'] for i in yt_anchors)
        yt_id = anchor_spk.most_common(1)[0][0]
        anchor_yt_count = anchor_spk[yt_id]
        # 计算锚点准确率（用于诊断）
        anchor_purity = anchor_yt_count / len(yt_anchors)
    else:
        yt_id = sentences[0]['speaker_id']  # 兜底
        anchor_purity = 0

    # 标注每句
    for i, s in enumerate(sentences):
        if i in yt_anchors:
            s['speaker'] = HOST_NAME
            s['confidence'] = 'high'
        elif s['speaker_id'] == yt_id:
            s['speaker'] = HOST_NAME
            s['confidence'] = 'medium'
        else:
            s['speaker'] = '嘉宾'
            s['confidence'] = 'medium'

    return sentences, yt_id, len(yt_anchors), anchor_purity


def merge_turns(sentences):
    """连续相同说话人合并为 turn"""
    turns = []
    cur = None
    for s in sentences:
        if cur is None or cur['speaker'] != s['speaker']:
            if cur:
                cur['text'] = ' '.join(cur.pop('parts'))
                turns.append(cur)
            cur = {
                'speaker': s['speaker'],
                'begin_time': s['begin_time'],
                'end_time': s['end_time'],
                'parts': [s['text']],
                'sentence_count': 1,
                'high_conf': s.get('confidence') == 'high',
            }
        else:
            cur['parts'].append(s['text'])
            cur['end_time'] = s['end_time']
            cur['sentence_count'] += 1
            if s.get('confidence') == 'high':
                cur['high_conf'] = True
    if cur:
        cur['text'] = ' '.join(cur.pop('parts'))
        turns.append(cur)
    return turns


def parse_chapters(description):
    """从 shownotes description 解析章节"""
    chapters = []
    for m in re.finditer(r'(\d{1,2}:\d{2}(?::\d{2})?)\s+([^\n0-9][^\n]+?)(?=\n|$)', description):
        time_str = m.group(1)
        title = m.group(2).strip()
        parts = time_str.split(':')
        if len(parts) == 2:
            sec = int(parts[0])*60 + int(parts[1])
        else:
            sec = int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
        chapters.append({'time_sec': sec, 'time_str': time_str, 'title': title})
    return chapters


def assign_chapters(turns, chapters):
    """每个 turn 标注所属章节"""
    for t in turns:
        t_sec = t['begin_time'] / 1000
        idx = -1
        for i, ch in enumerate(chapters):
            if t_sec >= ch['time_sec']:
                idx = i
            else:
                break
        t['chapter_idx'] = idx
    return turns


def tag_high_value(turns):
    """给杨天真的 turn 标注高价值类型"""
    for t in turns:
        if t['speaker'] != HOST_NAME:
            t['tags'] = []
            continue
        text = t['text']
        tags = []
        if len(text) > 100:
            tags.append('long_form')
        for tag_name, patterns in HIGH_VALUE_PATTERNS.items():
            for p in patterns:
                if re.search(p, text):
                    tags.append(tag_name)
                    break
        # 含专属术语
        if any(term in text for term in YT_SIGNATURE_TERMS):
            tags.append('signature_term')
        t['tags'] = sorted(set(tags))
    return turns


def fetch_episode_meta(episode_id):
    """抓 episode 页面拿标题 + description"""
    url = f"https://www.xiaoyuzhoufm.com/episode/{episode_id}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode('utf-8', errors='ignore')
    m = re.search(r'<script[^>]*"schema:podcast-show"[^>]*>(\{.+?\})</script>', html, re.DOTALL)
    if not m:
        return {'title': '', 'description': ''}
    ld = json.loads(m.group(1))
    return {
        'title': ld.get('name', ''),
        'description': ld.get('description', ''),
        'duration': ld.get('timeRequired', ''),
        'datePublished': ld.get('datePublished', ''),
    }


def fmt_ts(ms):
    s = ms / 1000
    return f"{int(s//60):02d}:{int(s%60):02d}"


def render_full_md(turns, chapters, meta):
    L = []
    L.append(f"# {meta['episode_title']}")
    L.append('')
    L.append(f"**总 turn 数**: {meta['stats']['total_turns']}  |  **杨天真发言比例**: {meta['stats']['yt_speaking_ratio']*100:.1f}%  |  **章节数**: {len(chapters)}")
    L.append('')
    L.append('> 自动生成。说话人为规则版修正（锚点纯度: ' + f"{meta['stats']['anchor_purity']*100:.0f}%）。模糊段标记 `confidence:medium`。")
    L.append('')
    L.append('---')
    L.append('')

    cur_ch = -99
    for t in turns:
        ci = t.get('chapter_idx', -1)
        if ci != cur_ch:
            cur_ch = ci
            if ci >= 0 and ci < len(chapters):
                ch = chapters[ci]
                L.append(f"\n## [{ch['time_str']}] {ch['title']}\n")
            elif ci == -1:
                L.append(f"\n## (开场前)\n")

        ts = fmt_ts(t['begin_time'])
        speaker = t['speaker']
        tags = ' '.join(f'`{tg}`' for tg in t.get('tags', []))
        tag_part = f"  {tags}" if tags else ''

        if speaker == HOST_NAME:
            L.append(f"**[{ts}] 杨天真**:{tag_part}")
        else:
            L.append(f"[{ts}] {speaker}:")
        L.append(f"> {t['text']}")
        L.append('')

    return '\n'.join(L)


def render_extract_md(turns, chapters, meta):
    """只保留杨天真的高价值发言（任何 tag）"""
    L = []
    L.append(f"# {meta['episode_title']} — 杨天真高价值发言提取")
    L.append('')
    L.append('> 仅保留杨天真的高价值 turn（含蒸馏相关 tag）。Phase 1 调研 Agent 2 优先读这个。')
    L.append('')
    L.append('---')
    L.append('')

    by_ch = {}
    for t in turns:
        if t['speaker'] != HOST_NAME or not t.get('tags'):
            continue
        ci = t.get('chapter_idx', -1)
        by_ch.setdefault(ci, []).append(t)

    for ci in sorted(by_ch.keys()):
        if ci >= 0 and ci < len(chapters):
            ch = chapters[ci]
            L.append(f"\n## [{ch['time_str']}] {ch['title']}\n")
        else:
            L.append('\n## (未对齐章节)\n')
        for t in by_ch[ci]:
            ts = fmt_ts(t['begin_time'])
            tags = ' '.join(f'`{tg}`' for tg in t['tags'])
            L.append(f"**[{ts}]** {tags}")
            L.append(f"> {t['text']}")
            L.append('')

    return '\n'.join(L)


def process_one(raw_path, episode_id, out_dir):
    with open(raw_path) as f:
        data = json.load(f)
    sentences = data['transcripts'][0]['sentences']

    ep_meta = fetch_episode_meta(episode_id)

    # Layer 1
    sentences, yt_id, anchor_count, anchor_purity = rule_based_correction(sentences)

    # Layer 2
    turns = merge_turns(sentences)
    chapters = parse_chapters(ep_meta['description'])
    turns = assign_chapters(turns, chapters)

    # Layer 3
    turns = tag_high_value(turns)

    # Layer 4
    yt_turns = [t for t in turns if t['speaker'] == HOST_NAME]
    yt_dur = sum(t['end_time'] - t['begin_time'] for t in yt_turns) / 1000
    total_dur = sum(t['end_time'] - t['begin_time'] for t in turns) / 1000
    all_tags = []
    for t in turns:
        all_tags.extend(t.get('tags', []))

    meta = {
        'episode_id': episode_id,
        'episode_title': ep_meta['title'],
        'duration': ep_meta['duration'],
        'datePublished': ep_meta['datePublished'],
        'chapters': chapters,
        'stats': {
            'total_turns': len(turns),
            'yt_turns': len(yt_turns),
            'guest_turns': len(turns) - len(yt_turns),
            'yt_speaking_seconds': round(yt_dur, 1),
            'guest_speaking_seconds': round(total_dur - yt_dur, 1),
            'yt_speaking_ratio': yt_dur / total_dur if total_dur else 0,
            'anchor_count': anchor_count,
            'anchor_purity': anchor_purity,
        },
        'high_value_tags': dict(Counter(all_tags)),
    }

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'full.md').write_text(render_full_md(turns, chapters, meta), encoding='utf-8')
    (out / 'extract.md').write_text(render_extract_md(turns, chapters, meta), encoding='utf-8')
    (out / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')

    return meta


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--raw', required=True)
    ap.add_argument('--episode-id', required=True)
    ap.add_argument('--output-dir', required=True)
    args = ap.parse_args()

    meta = process_one(args.raw, args.episode_id, args.output_dir)
    print(f"✅ 处理完成: {meta['episode_title']}")
    print(f"   总 turn: {meta['stats']['total_turns']}, 杨天真 {meta['stats']['yt_turns']} ({meta['stats']['yt_speaking_ratio']*100:.1f}%)")
    print(f"   锚点数: {meta['stats']['anchor_count']}, 锚点纯度: {meta['stats']['anchor_purity']*100:.0f}%")
    print(f"   高价值 tag: {meta['high_value_tags']}")

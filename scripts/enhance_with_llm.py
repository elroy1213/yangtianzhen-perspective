#!/usr/bin/env python3
"""
Layer 1+ : 在规则修正基础上，用 DeepSeek V3.2 做逐句说话人校对。

输入: paraformer raw JSON + episode_id
输出: full.md + extract.md + meta.json (与 enhance_transcript.py 兼容)

策略:
1. 抓 episode 页拿嘉宾名 + shownotes 章节
2. 整集 sentences 一次性丢给 DeepSeek（128K 上下文足够）
3. 让 LLM 输出每句的真正 speaker (杨天真 / 嘉宾名)
4. 合并 turn → 章节对齐 → 高价值标注 → 输出 markdown

价格预估：单集约 22K input + 5K output ≈ ¥0.085
"""

import json, re, sys, urllib.request, argparse, os, time
from pathlib import Path
from collections import Counter
from openai import OpenAI

HOST_NAME = "杨天真"

# 高价值标签模式（与 enhance_transcript.py 同步）
HIGH_VALUE_PATTERNS = {
    'mental_model':    [r'我觉得', r'我认为', r'本质上', r'其实就是', r'核心是', r'关键是'],
    'decision_rule':   [r'我从不', r'我一定', r'我绝不', r'必须', r'宁可.{0,15}也不', r'就两个原则'],
    'reframing':       [r'换个角度', r'反过来想', r'其实不是', r'看到的.{0,5}其实'],
    'counter_question':[r'你怎么看', r'你为什么', r'那你是', r'你觉得呢', r'你有没有'],
    'experience':      [r'我当年', r'我以前', r'我做经纪人', r'我那时候', r'壹心'],
}

YT_SIGNATURE_TERMS = [
    '底层自信', '喜恶同因', '经纪人视角', '把自己当回事儿',
    '能量场', '回馈分析法', '九分仪', '高情商公式',
]


def fetch_episode_meta(episode_id):
    """抓 episode 页面拿标题、description、嘉宾"""
    url = f"https://www.xiaoyuzhoufm.com/episode/{episode_id}"
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=30) as r:
        html = r.read().decode('utf-8', errors='ignore')
    m = re.search(r'<script[^>]*"schema:podcast-show"[^>]*>(\{.+?\})</script>', html, re.DOTALL)
    if not m:
        return {'title': '', 'description': '', 'guests': []}
    ld = json.loads(m.group(1))
    title = ld.get('name', '')
    desc = ld.get('description', '')

    # 从标题提取嘉宾名
    guests = []
    m_guest = re.search(r'对谈([^：:]+?)[：:]', title)
    if m_guest:
        guest_str = m_guest.group(1)
        # 拆分：嘻哈王越 / 马思纯 / 刘开心 等
        # 简单处理：原样作为单一嘉宾标签
        guests = [guest_str]

    return {
        'title': title,
        'description': desc,
        'duration': ld.get('timeRequired', ''),
        'datePublished': ld.get('datePublished', ''),
        'guests': guests,
    }


def parse_chapters(description):
    chapters = []
    for m in re.finditer(r'(\d{1,2}:\d{2}(?::\d{2})?)\s+([^\n0-9][^\n]+?)(?=\n|$)', description):
        time_str = m.group(1)
        title = m.group(2).strip()
        parts = time_str.split(':')
        sec = int(parts[0])*60 + int(parts[1]) if len(parts) == 2 else int(parts[0])*3600 + int(parts[1])*60 + int(parts[2])
        chapters.append({'time_sec': sec, 'time_str': time_str, 'title': title})
    return chapters


def llm_correct_speakers(sentences, ep_meta):
    """整集丢给 DeepSeek，逐句校对说话人"""
    client = OpenAI(
        api_key=os.environ['DASHSCOPE_API_KEY'],
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )

    # 构造紧凑输入：每句 id + paraformer speaker + text
    lines = []
    for i, s in enumerate(sentences):
        lines.append(f"{i}|{s['speaker_id']}|{s['text']}")
    transcript_text = '\n'.join(lines)

    # 嘉宾说明
    guest_str = '、'.join(ep_meta.get('guests') or ['嘉宾'])

    # 章节大纲
    chapters = parse_chapters(ep_meta.get('description', ''))
    chapter_str = '\n'.join(f"  {c['time_str']} {c['title'][:50]}" for c in chapters[:15])

    system_prompt = f"""你是中文播客对话说话人识别专家。

节目：《天真不天真》
主持人：杨天真（女，前壹心娱乐创始人/明星经纪人，作家。代表书：《把自己当回事儿》《通透》《去遇见》）
本集嘉宾：{guest_str}
本集章节大纲：
{chapter_str}

任务：根据中文语义和对话上下文，给每句话判断真正的说话人。

判断规则：
1. 杨天真特征：
   - 主持人开场（"欢迎来到天真不天真"）、提问、引导、总结
   - 用经纪人/娱乐圈视角（"做艺人"、"我做经纪人时"、"壹心"）
   - 用她的专属术语（"底层自信"、"喜恶同因"、"经纪人视角"）
   - 给嘉宾建议、做点评（"我觉得你..."、"我建议..."）

2. 嘉宾特征：
   - 第一人称讲自己的具体经历（"我那时候在县城"、"我演单口"）
   - 被问到时回答
   - 用嘉宾自身职业语境（脱口秀演员、运动员、作家等）

3. 重要：paraformer 的原始 speaker_id 仅供参考，可能错。**请基于内容语义独立判断**。

输出格式（严格 JSON，无任何解释）：
{{"speakers": [{{"id": 0, "speaker": "杨天真"}}, {{"id": 1, "speaker": "{guest_str}"}}, ...]}}

speaker 字段值只能是："杨天真" 或 "{guest_str}"（多嘉宾时可写具体名字如"嘻哈"/"王越"，一定要用嘉宾原名）。
对每个 sentence id 都必须输出。"""

    user_prompt = f"""转写句子（格式 id|paraformer_speaker_id|text）：

{transcript_text}

请输出 JSON。"""

    print(f"  发送 LLM 请求 ({len(sentences)} 句)...")
    t0 = time.time()
    resp = client.chat.completions.create(
        model="deepseek-v3.2-exp",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
        response_format={"type": "json_object"},
        max_tokens=32000,
    )
    elapsed = time.time() - t0
    usage = resp.usage
    print(f"  LLM 完成 {elapsed:.1f}s | tokens: in={usage.prompt_tokens} out={usage.completion_tokens}")

    content = resp.choices[0].message.content

    # 兜底处理: 剥离 markdown 代码块（DeepSeek 偶尔会忽略 response_format 输出 ```json ... ```)
    content_clean = content.strip()
    if content_clean.startswith('```'):
        # 去掉首尾 ``` 包裹
        content_clean = re.sub(r'^```(?:json)?\s*', '', content_clean)
        content_clean = re.sub(r'\s*```\s*$', '', content_clean)
    # 兜底处理: 截断 JSON（max_tokens 不够时输出被截断）
    # 找最后一个完整的 } 收尾
    try:
        data = json.loads(content_clean)
        speakers_arr = data.get('speakers', [])
    except json.JSONDecodeError as e:
        # 二次尝试：截断到最后一个完整 }
        try:
            # 找 "speakers" 数组结束的最近 ] 然后包 }
            last_complete = content_clean.rfind('}')
            if last_complete > 0:
                # 试着补全：找最后一个完整的 {"id": N, "speaker": "..."}
                truncated = content_clean[:last_complete+1]
                # 找 speakers 数组开头
                m = re.search(r'"speakers"\s*:\s*\[', truncated)
                if m:
                    array_start = m.end()
                    # 找最后一个完整的 } 在 array 内
                    array_content = truncated[array_start:]
                    last_obj_end = array_content.rfind('}')
                    if last_obj_end > 0:
                        repaired = '{"speakers":[' + array_content[:last_obj_end+1] + ']}'
                        data = json.loads(repaired)
                        speakers_arr = data.get('speakers', [])
                        print(f"  ⚠️ JSON 截断修复成功，恢复 {len(speakers_arr)} 句")
                    else:
                        raise e
                else:
                    raise e
            else:
                raise e
        except Exception:
            print(f"❌ JSON 解析失败: {e}")
            print(f"原始返回前 500 字: {content[:500]}")
            return None, usage

    # 构造 id → speaker 映射
    id_to_speaker = {item['id']: item['speaker'] for item in speakers_arr}

    # 应用到 sentences
    fallback_count = 0
    for i, s in enumerate(sentences):
        if i in id_to_speaker:
            s['speaker'] = id_to_speaker[i]
        else:
            # 缺失：用 paraformer 兜底
            s['speaker'] = HOST_NAME if s['speaker_id'] == 0 else '嘉宾'
            fallback_count += 1

    if fallback_count > 0:
        print(f"  ⚠️ {fallback_count} 句 LLM 未返回，用 paraformer 兜底")

    return sentences, usage


def merge_turns(sentences):
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
            }
        else:
            cur['parts'].append(s['text'])
            cur['end_time'] = s['end_time']
            cur['sentence_count'] += 1
    if cur:
        cur['text'] = ' '.join(cur.pop('parts'))
        turns.append(cur)
    return turns


def assign_chapters(turns, chapters):
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
        if any(term in text for term in YT_SIGNATURE_TERMS):
            tags.append('signature_term')
        t['tags'] = sorted(set(tags))
    return turns


def fmt_ts(ms):
    s = ms / 1000
    return f"{int(s//60):02d}:{int(s%60):02d}"


def render_full_md(turns, chapters, meta):
    L = []
    L.append(f"# {meta['episode_title']}")
    L.append('')
    L.append(f"**总 turn**: {meta['stats']['total_turns']}  |  **杨天真发言比例**: {meta['stats']['yt_speaking_ratio']*100:.1f}%  |  **章节**: {len(chapters)}")
    L.append('')
    L.append(f"> Layer 1+ (DeepSeek V3.2 校对) | LLM tokens: in={meta['llm']['input_tokens']} out={meta['llm']['output_tokens']}")
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
    L = []
    L.append(f"# {meta['episode_title']} — 杨天真高价值发言提取")
    L.append('')
    L.append('> 仅保留杨天真的高价值 turn (含蒸馏相关 tag)。Phase 1 调研 Agent 2 优先读这个。')
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

    print(f"加载 {len(sentences)} 句")
    ep_meta = fetch_episode_meta(episode_id)
    print(f"标题: {ep_meta['title']}")
    print(f"嘉宾: {ep_meta.get('guests', [])}")

    # LLM 校对
    sentences, usage = llm_correct_speakers(sentences, ep_meta)
    if sentences is None:
        return None

    # Turn 合并
    turns = merge_turns(sentences)
    chapters = parse_chapters(ep_meta['description'])
    turns = assign_chapters(turns, chapters)
    turns = tag_high_value(turns)

    # 元数据
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
        'guests': ep_meta.get('guests', []),
        'chapters': chapters,
        'stats': {
            'total_turns': len(turns),
            'yt_turns': len(yt_turns),
            'guest_turns': len(turns) - len(yt_turns),
            'yt_speaking_seconds': round(yt_dur, 1),
            'guest_speaking_seconds': round(total_dur - yt_dur, 1),
            'yt_speaking_ratio': yt_dur / total_dur if total_dur else 0,
        },
        'high_value_tags': dict(Counter(all_tags)),
        'llm': {
            'model': 'deepseek-v3.2-exp',
            'input_tokens': usage.prompt_tokens,
            'output_tokens': usage.completion_tokens,
        },
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

    # 加载 API key
    if 'DASHSCOPE_API_KEY' not in os.environ:
        env_file = Path.home() / '.config/xiaoyuzhou-podcast.env'
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if '=' in line:
                    k, v = line.split('=', 1)
                    os.environ[k.strip()] = v.strip()

    meta = process_one(args.raw, args.episode_id, args.output_dir)
    if meta:
        print(f"\n✅ 处理完成")
        print(f"   总 turn: {meta['stats']['total_turns']}, 杨天真 {meta['stats']['yt_turns']} ({meta['stats']['yt_speaking_ratio']*100:.1f}%)")
        print(f"   高价值 tag: {meta['high_value_tags']}")
        # 估算成本
        in_cost = meta['llm']['input_tokens'] * 2 / 1_000_000  # ¥2/M
        out_cost = meta['llm']['output_tokens'] * 8 / 1_000_000  # ¥8/M
        print(f"   LLM 成本: ¥{in_cost+out_cost:.4f} (in: ¥{in_cost:.4f}, out: ¥{out_cost:.4f})")

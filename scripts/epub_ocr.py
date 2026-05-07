#!/usr/bin/env python3
"""
扫描版 EPUB OCR 处理：解压所有图片 → tesseract 中文 OCR → 拼接 markdown

用法: python3 epub_ocr.py <epub> <output_md>
"""

import sys, zipfile, subprocess, tempfile, re, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


def ocr_one(image_path):
    """对一张图调 tesseract，返回 OCR 文本"""
    try:
        result = subprocess.run(
            ['tesseract', str(image_path), '-', '-l', 'chi_sim', '--psm', '6'],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout
    except Exception as e:
        return f"[OCR 失败: {e}]"


def main():
    if len(sys.argv) < 3:
        print("用法: epub_ocr.py <epub> <output_md>")
        sys.exit(1)

    epub_path = sys.argv[1]
    output_md = sys.argv[2]

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        # 解压所有 jpg/jpeg/png
        with zipfile.ZipFile(epub_path) as z:
            images = sorted([n for n in z.namelist()
                             if n.lower().endswith(('.jpg', '.jpeg', '.png'))
                             and 'cover' not in n.lower()])
            print(f"找到 {len(images)} 张图片")
            for img in images:
                z.extract(img, tmpdir)

        # 并发 OCR
        image_paths = [tmpdir / img for img in images]

        results = [None] * len(image_paths)
        t0 = time.time()
        done = 0
        with ThreadPoolExecutor(max_workers=6) as pool:
            future_to_idx = {pool.submit(ocr_one, p): i for i, p in enumerate(image_paths)}
            for fut in as_completed(future_to_idx):
                idx = future_to_idx[fut]
                results[idx] = fut.result()
                done += 1
                if done % 30 == 0 or done == len(image_paths):
                    elapsed = time.time() - t0
                    print(f"  进度 {done}/{len(image_paths)} ({elapsed:.0f}s, {elapsed/done:.1f}s/张)")

    # 拼接
    full_text = '\n\n'.join(results)
    # 清洗：合并连续多个空行
    full_text = re.sub(r'\n{3,}', '\n\n', full_text)

    # 输出
    title = Path(epub_path).stem.replace('02-', '').replace('.epub', '')
    md = f"# {title}\n\n**来源**: `{epub_path}` (扫描版 OCR)\n\n---\n\n{full_text}\n"
    Path(output_md).write_text(md, encoding='utf-8')

    chars = len(full_text.replace('\n', '').replace(' ', ''))
    print(f"\n✅ OCR 完成: {output_md}")
    print(f"   总字数（去空白）: {chars:,}")


if __name__ == '__main__':
    main()

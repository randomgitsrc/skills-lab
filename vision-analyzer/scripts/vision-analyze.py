#!/usr/bin/env python3
"""
Vision Analyze — 通用图片分析 CLI。

从 .env 读取 API 凭据，支持 anthropic/openai 双格式。
任何 provider 只需改 .env，不改此脚本。

用法:
  scripts/vision-analyze.py --image /tmp/screenshot.png --prompt "描述这张截图"
  scripts/vision-analyze.py -i /tmp/screenshot.png
  scripts/vision-analyze.py -i /tmp/screenshot.png -p "输出YAML" --format yaml

环境变量（从项目根目录 .env 自动读取）:
  VISION_API_KEY      API key
  VISION_API_BASE_URL  端点 URL（如 https://api.minimaxi.com/anthropic）
  VISION_MODEL         模型 ID（如 MiniMax-M3）
  VISION_API_FORMAT    anthropic (默认) 或 openai
"""
import argparse, asyncio, base64, json, os, sys
from pathlib import Path

# ── 从 .env 读取 ──
def load_env():
    env = {}
    candidates = [
        Path.cwd() / '.env',          # 当前项目（优先）
        Path.home() / '.env',         # 全局（备选）
    ]
    for f in candidates:
        if f.is_file():
            for line in f.read_text().splitlines():
                if '=' in line and not line.startswith('#'):
                    k, v = line.strip().split('=', 1)
                    env[k.strip()] = v.strip()
            if 'VISION_API_KEY' in env:
                break
    return env

# ── 主逻辑 ──
async def analyze(image_path: str, prompt: str, output_format: str = 'text'):
    env = load_env()
    for var in ('VISION_API_KEY', 'VISION_API_BASE_URL', 'VISION_MODEL'):
        if var not in env:
            print(f"Error: {var} not found in .env", file=sys.stderr)
            sys.exit(1)

    key = env['VISION_API_KEY']
    base = env['VISION_API_BASE_URL'].rstrip('/')
    model = env['VISION_MODEL']
    fmt = env.get('VISION_API_FORMAT', 'anthropic')

    import httpx

    with open(image_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode()

    if fmt == 'anthropic':
        body = {'model': model, 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': [
            {'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png', 'data': b64}},
            {'type': 'text', 'text': prompt}
        ]}]}
        headers = {'x-api-key': key, 'anthropic-version': '2023-06-01'}
        api_path = '/v1/messages'
    else:
        body = {'model': model, 'max_tokens': 1024, 'messages': [{'role': 'user', 'content': [
            {'type': 'image_url', 'image_url': {'url': f'data:image/png;base64,{b64}'}},
            {'type': 'text', 'text': prompt}
        ]}]}
        headers = {'Authorization': f'Bearer {key}'}
        api_path = '/v1/chat/completions'

    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(f'{base}{api_path}', headers=headers, json=body)
        r.raise_for_status()
        data = r.json()

    if fmt == 'anthropic':
        result = data['content'][0]['text']
    else:
        result = data['choices'][0]['message']['content']

    if output_format == 'yaml':
        lines = result.strip().split('\n')
        print('vision_result:')
        print('  text: |')
        for line in lines:
            print(f'    {line}')
    else:
        print(result)

def main():
    parser = argparse.ArgumentParser(description='Vision Analyze — 通用图片分析')
    parser.add_argument('-i', '--image', required=True, help='图片路径 (png/jpg/gif/webp)')
    parser.add_argument('-p', '--prompt', default='Describe this image in detail.', help='分析提示词')
    parser.add_argument('--format', choices=['text', 'yaml'], default='text', help='输出格式')
    args = parser.parse_args()

    if not os.path.isfile(args.image):
        print(f'Error: file not found: {args.image}', file=sys.stderr)
        sys.exit(1)

    asyncio.run(analyze(args.image, args.prompt, args.format))

if __name__ == '__main__':
    main()

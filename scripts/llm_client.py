# -*- coding: utf-8 -*-
"""极简 LLM 客户端（OpenAI 兼容 /chat/completions）。

密钥解析顺序（先命中先用）：
  1. 环境变量
  2. config/secrets.local.env（KEY=VALUE 每行一条，本文件不入库）
  3. DSH 凭据文件 ~/.dsh/.credentials.yaml 的 refs 段（本机便利路径）

用法：
    from llm_client import chat
    txt = chat([{'role': 'user', 'content': '...'}], provider='deepseek')
"""
import json
import os
import re
import urllib.error
import urllib.request

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_ENV = os.path.join(PROJECT, 'config', 'secrets.local.env')
DSH_CRED = os.path.expanduser(os.path.join('~', '.dsh', '.credentials.yaml'))

PROVIDERS = {
    'deepseek': {
        'base': 'https://api.deepseek.com/v1',
        'model': 'deepseek-chat',
        'env': ['DEEPSEEK_KEY', 'DEEPSEEK_API_KEY'],
    },
    'zhipu': {
        'base': 'https://open.bigmodel.cn/api/paas/v4',
        'model': 'glm-5.3',
        'env': ['ZAI_API_KEY', 'GLM_API_KEY', 'ZHIPU_API_KEY'],
    },
}
DEFAULT_PROVIDER = 'deepseek'
FALLBACK_PROVIDER = 'zhipu'


def _local_env():
    out = {}
    try:
        with open(LOCAL_ENV, encoding='utf-8') as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _dsh_refs():
    out = {}
    try:
        with open(DSH_CRED, encoding='utf-8') as fh:
            txt = fh.read()
    except OSError:
        return out
    if 'refs:' not in txt:
        return out
    for line in txt.split('refs:', 1)[1].splitlines():
        m = re.match(r'\s{2}([A-Za-z0-9_]+):\s*(\S+)\s*$', line)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def key_for(provider):
    cfg = PROVIDERS[provider]
    local = None
    for name in cfg['env']:
        v = os.environ.get(name)
        if v:
            return v
    if local is None:
        local = _local_env()
    for name in cfg['env']:
        v = local.get(name)
        if v:
            return v
    refs = _dsh_refs()
    for name in cfg['env']:
        v = refs.get(name)
        if v:
            return v
    return ''


def available():
    return [p for p in PROVIDERS if key_for(p)]


def chat(messages, provider=None, model=None, json_mode=True, timeout=180,
         temperature=0.3, max_tokens=4000):
    """调用一次对话补全，返回正文文本。失败抛 RuntimeError。"""
    order = [provider] if provider else [DEFAULT_PROVIDER, FALLBACK_PROVIDER]
    order = [p for p in order if p in PROVIDERS]
    last = None
    for p in order:
        key = key_for(p)
        if not key:
            last = f'{p}: 无密钥'
            continue
        cfg = PROVIDERS[p]
        body = {
            'model': model or cfg['model'],
            'messages': messages,
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        if json_mode:
            body['response_format'] = {'type': 'json_object'}
        req = urllib.request.Request(
            cfg['base'].rstrip('/') + '/chat/completions',
            data=json.dumps(body).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Authorization': 'Bearer ' + key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode('utf-8'))
            return d['choices'][0]['message']['content']
        except urllib.error.HTTPError as e:
            detail = ''
            try:
                detail = e.read().decode('utf-8', 'replace')[:300]
            except Exception:
                pass
            last = f'{p}: HTTP {e.code} {detail}'
        except Exception as e:  # noqa: BLE001
            last = f'{p}: {type(e).__name__} {str(e)[:200]}'
    raise RuntimeError('LLM 调用失败：' + (last or '无可用提供商'))


def parse_json(txt):
    """从模型输出里取出 JSON 对象（容忍 ```json 包裹）。"""
    txt = (txt or '').strip()
    m = re.search(r'```(?:json)?\s*(.*?)```', txt, re.S)
    if m:
        txt = m.group(1).strip()
    i, j = txt.find('{'), txt.rfind('}')
    if i >= 0 and j > i:
        txt = txt[i:j + 1]
    return json.loads(txt)


if __name__ == '__main__':
    print('可用提供商：', available())
    for p in available():
        print(p, PROVIDERS[p]['model'], '密钥前后各 6 位：',
              key_for(p)[:6] + '…' + key_for(p)[-4:])

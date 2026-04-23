from DrissionPage import ChromiumPage, ChromiumOptions
import time
import json
import hashlib
import hmac
import base64
import urllib.parse
import requests
from pathlib import Path
from datetime import datetime

FORUMS = [
    {'name': '山东联通', 'url': 'https://tieba.baidu.com/f?kw=%E5%B1%B1%E4%B8%9C%E8%81%94%E9%80%9A&fr=frs'},
    {'name': '山东电信', 'url': 'https://tieba.baidu.com/f?kw=%E5%B1%B1%E4%B8%9C%E7%94%B5%E4%BF%A1&fr=frs'},
    {'name': '山东移动', 'url': 'https://tieba.baidu.com/f?kw=%E5%B1%B1%E4%B8%9C%E7%A7%BB%E5%8A%A8&fr=frs'},
]

DINGTALK_WEBHOOK = 'https://oapi.dingtalk.com/robot/send?access_token=f2113cac758bbe3896f62d2028fc9c6aa5dd54bd35fe2b9ef3db7ae3b6e8bf6f'
DINGTALK_SECRET = 'SECe7e260001429e1d6653be520d74fdda7abf02adb8c579b48d3fa041f94cfab0b'

COLLECT_COUNT = 10
SCAN_INTERVAL = 600  # 10分钟
import sys

if getattr(sys, 'frozen', False):
    _BASE_DIR = Path(sys.executable).parent
else:
    _BASE_DIR = Path(__file__).parent

DATA_FILE = _BASE_DIR / 'seen_posts.json'


def load_seen_posts():
    if DATA_FILE.exists():
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_seen_posts(data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def dingtalk_sign():
    timestamp = str(round(time.time() * 1000))
    string_to_sign = f'{timestamp}\n{DINGTALK_SECRET}'
    hmac_code = hmac.new(
        DINGTALK_SECRET.encode('utf-8'),
        string_to_sign.encode('utf-8'),
        digestmod=hashlib.sha256
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
    return timestamp, sign


def send_dingtalk(title, posts):
    """推送新帖子到钉钉"""
    if not posts:
        return

    timestamp, sign = dingtalk_sign()
    url = f'{DINGTALK_WEBHOOK}&timestamp={timestamp}&sign={sign}'

    lines = [f'### {title}\n']
    for p in posts:
        lines.append(f"- **{p['title']}**  \n  {p.get('link', '')}\n")

    payload = {
        'msgtype': 'markdown',
        'markdown': {
            'title': title,
            'text': '\n'.join(lines)
        }
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        result = resp.json()
        if result.get('errcode') == 0:
            print(f'  钉钉推送成功: {len(posts)} 条')
        else:
            print(f'  钉钉推送失败: {result}')
    except Exception as e:
        print(f'  钉钉推送异常: {e}')


def extract_post_id(link):
    """从帖子链接中提取帖子ID，如 /p/10436198542 -> 10436198542"""
    if not link:
        return None
    import re
    m = re.search(r'/p/(\d+)', link)
    return m.group(1) if m else None


def get_posts_from_visible_items(page, limit):
    """从当前可见的虚拟列表项中提取帖子信息"""
    results = []
    items = page.eles('.virtual-list-item')
    for item in items:
        if len(results) >= limit:
            break
        try:
            prefix = ''
            prefix_el = item.ele('tag:span@class=title-prefix', timeout=0.5)
            if prefix_el:
                prefix = f'[{prefix_el.text.strip()}]'

            title = ''
            text_els = item.eles('tag:span@class=text', timeout=0.5)
            for el in text_els:
                parent = el.parent()
                if parent and 'title-richtext' in (parent.attr('class') or ''):
                    title = el.text.strip()
                    break

            if not title:
                title_div = item.ele('.title-richtext', timeout=0.5)
                if title_div:
                    title = title_div.text.strip()

            link = ''
            link_el = item.ele('.thread-content-link', timeout=0.5)
            if link_el:
                link = link_el.attr('href') or ''
                if link and not link.startswith('http'):
                    link = 'https://tieba.baidu.com' + link

            post_id = extract_post_id(link)
            if title and post_id:
                results.append({
                    'post_id': post_id,
                    'title': f'{prefix}{title}',
                    'link': link,
                })
        except Exception:
            pass
    return results


def scrape_forum(page, forum_url, forum_name):
    """采集单个贴吧，返回帖子列表（最多 COLLECT_COUNT 条）"""
    print(f'\n{"=" * 60}')
    print(f'[{forum_name}] 开始采集...')

    page.get(forum_url)
    page.wait.doc_loaded()
    time.sleep(3)

    tab_latest = page.ele('#tab-503', timeout=10)
    if tab_latest:
        print(f'[{forum_name}] 点击"最新"')
        tab_latest.click()
        time.sleep(3)
    else:
        print(f'[{forum_name}] 未找到"最新"标签')

    print(f'[{forum_name}] 等待子菜单渲染...')
    sub_menu = page.ele('.sub-menu-container', timeout=15)
    if sub_menu:
        time.sleep(1)
        menu_items = sub_menu.eles('.menu-item')
        for mi in menu_items:
            if '发布' in mi.text:
                print(f'[{forum_name}] 点击"发布"')
                mi.click()
                time.sleep(3)
                break
    else:
        print(f'[{forum_name}] 未找到子菜单')

    page.wait.ele_displayed('.thread-title', timeout=10)
    time.sleep(2)

    all_posts = {}
    no_new_count = 0

    js_scroll = '''
        const el = document.querySelector('.frs-page-wrap');
        if (el) { el.scrollTop += 500; return el.scrollTop; }
        return -1;
    '''

    for scroll_i in range(20):
        batch = get_posts_from_visible_items(page, COLLECT_COUNT * 2)
        added = 0
        for p in batch:
            if p['post_id'] not in all_posts:
                all_posts[p['post_id']] = p
                added += 1

        if len(all_posts) >= COLLECT_COUNT:
            break

        if added == 0:
            no_new_count += 1
            if no_new_count >= 3:
                break
        else:
            no_new_count = 0

        page.run_js(js_scroll)
        time.sleep(1.5)

    posts = list(all_posts.values())[:COLLECT_COUNT]
    print(f'[{forum_name}] 采集到 {len(posts)} 条帖子')
    for i, p in enumerate(posts, 1):
        print(f'  {i}. {p["title"]}')

    return posts


def run_once(page):
    """执行一轮扫描"""
    seen = load_seen_posts()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f'\n{"#" * 60}')
    print(f'开始扫描 [{now}]')

    for forum in FORUMS:
        name = forum['name']
        try:
            posts = scrape_forum(page, forum['url'], name)
        except Exception as e:
            print(f'[{name}] 采集异常: {e}')
            continue

        if name not in seen:
            seen[name] = []
        seen_keys = set(seen[name])

        new_posts = [p for p in posts if p['post_id'] not in seen_keys]

        if new_posts:
            print(f'[{name}] 发现 {len(new_posts)} 条新帖子，推送钉钉...')
            send_dingtalk(f'{name}贴吧新帖', new_posts)
            seen[name] = list(seen_keys | {p['post_id'] for p in new_posts})
        else:
            print(f'[{name}] 无新帖子')

    save_seen_posts(seen)
    print(f'\n扫描完成 [{now}]')


def main():
    print('请选择浏览器运行模式:')
    print('  1 - 无头模式（后台运行，不显示浏览器窗口）')
    print('  2 - 有头模式（显示浏览器窗口）')
    choice = input('请输入 1 或 2: ').strip()

    co = ChromiumOptions()
    if choice == '2':
        mode_label = '有头模式'
    else:
        co.headless()
        mode_label = '无头模式'

    page = ChromiumPage(co)
    print(f'浏览器已启动（{mode_label}），开始监控...')
    print(f'监控贴吧: {", ".join(f["name"] for f in FORUMS)}')
    print(f'扫描间隔: {SCAN_INTERVAL // 60} 分钟')

    try:
        while True:
            run_once(page)
            print(f'\n等待 {SCAN_INTERVAL // 60} 分钟后进行下一轮扫描...')
            time.sleep(SCAN_INTERVAL)
    except KeyboardInterrupt:
        print('\n手动停止监控')
    finally:
        page.quit()


if __name__ == '__main__':
    main()

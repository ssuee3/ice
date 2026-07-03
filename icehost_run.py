import os
import time
import json
import urllib.parse
import requests
# 引入 SeleniumBase 高级过盾包
from seleniumbase import SB

SERVER_URL = os.getenv("ICEHOST_SERVER_URL")
ICEHOST_COOKIES = os.getenv("ICEHOST_COOKIES")
SCREENSHOT_PATH = "icehost_debug_screenshot.png"


def send_tg_notification(message, photo_path=None):
    """发送结果和截图至 Telegram。"""
    token = os.getenv("TG_BOT_TOKEN")
    chat_id = os.getenv("TG_CHAT_ID")
    if not token or not chat_id:
        print("未配置 TG 机器人变量，跳过发送 TG 推送。")
        return

    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        }
        resp = requests.post(url, json=payload, timeout=20)
        if resp.ok:
            print("TG 状态通知发送成功。")
        else:
            print(f"TG 状态通知发送失败: HTTP {resp.status_code} {resp.text[:500]}")
    except Exception as e:
        print(f"发送 TG 消息异常: {e}")

    if photo_path and os.path.exists(photo_path):
        try:
            url = f"https://api.telegram.org/bot{token}/sendPhoto"
            with open(photo_path, "rb") as f:
                files = {"photo": f}
                data = {"chat_id": chat_id, "caption": "IceHost 实时画面"}
                resp = requests.post(url, data=data, files=files, timeout=40)
            if resp.ok:
                print("TG 截图发送成功。")
            else:
                print(f"TG 截图发送失败: HTTP {resp.status_code} {resp.text[:500]}")
        except Exception as e:
            print(f"发送 TG 截图异常: {e}")


def xpath_literal(value):
    """把任意字符串安全转成 XPath 字符串字面量。"""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    return "concat(" + ", \"'\", ".join(f"'{part}'" for part in value.split("'")) + ")"


def build_renew_button_xpath():
    """
    同时兼容 IceHost 面板的英文和波兰语续期按钮。

    原脚本只匹配 dodaj 6，但当前截图里的按钮是 ADD 6 HOURS VALIDITY，
    所以 GitHub Actions 会误判找不到按钮。
    """
    upper = "ABCDEFGHIJKLMNOPQRSTUVWXYZĄĆĘŁŃÓŚŹŻ"
    lower = "abcdefghijklmnopqrstuvwxyząćęłńóśźż"

    keywords = [
        # English UI
        "add 6",
        "add 6 hours",
        "add 6 hours validity",
        "6 hours validity",
        "validity",
        # Polish UI / fallback
        "dodaj 6",
        "przedluz",
        "przedłuż",
        "ważności",
        "waznosci",
    ]

    normalized_text = (
        f"translate(normalize-space(.), {xpath_literal(upper)}, {xpath_literal(lower)})"
    )
    keyword_conditions = " or ".join(
        f"contains({normalized_text}, {xpath_literal(keyword)})" for keyword in keywords
    )

    # 不再使用 not(*)，因为真实按钮可能包含 span/i 等子元素。
    # 同时允许 button、a、input，以及 role=button 的元素。
    return f"""
    //*[
        (
            self::button
            or self::a
            or self::input[@type='button' or @type='submit']
            or @role='button'
        )
        and not(@disabled)
        and not(contains(@class, 'disabled'))
        and ({keyword_conditions})
    ]
    """


def dump_clickable_texts(sb):
    """找不到续期按钮时打印页面里的按钮/链接文字，方便排查面板文案变化。"""
    try:
        script = """
        const els = Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"], [role="button"]'));
        return els.map((el, idx) => ({
            idx,
            tag: el.tagName,
            text: (el.innerText || el.value || el.getAttribute('aria-label') || '').trim(),
            cls: el.className || '',
            disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true'
        })).filter(x => x.text);
        """
        items = sb.execute_script(script)
        print("页面中检测到的可点击元素文字如下：")
        for item in items:
            print(
                f"[{item.get('idx')}] {item.get('tag')} "
                f"disabled={item.get('disabled')} "
                f"text={item.get('text')!r} class={item.get('cls')!r}"
            )
    except Exception as e:
        print(f"打印可点击元素列表失败: {e}")



def find_renew_button_by_js(sb):
    """用浏览器 JS 按 innerText 寻找续期按钮。这个比 XPath 更适合 React/Styled Components 页面。"""
    script = """
    const keywords = [
        'add 6',
        'add 6 hours',
        'add 6 hours validity',
        '6 hours validity',
        'validity',
        'dodaj 6',
        'przedluz',
        'przedłuż',
        'ważności',
        'waznosci'
    ];

    function normalizeText(value) {
        return String(value || '')
            .normalize('NFD')
            .replace(/[\u0300-\u036f]/g, '')
            .toLowerCase()
            .replace(/\s+/g, ' ')
            .trim();
    }

    function isVisible(el) {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none'
            && style.visibility !== 'hidden'
            && Number(style.opacity || 1) !== 0
            && rect.width > 0
            && rect.height > 0;
    }

    const candidates = Array.from(document.querySelectorAll(
        'button, a, input[type="button"], input[type="submit"], [role="button"]'
    ));

    for (const el of candidates) {
        const disabled = !!el.disabled
            || el.getAttribute('disabled') !== null
            || el.getAttribute('aria-disabled') === 'true'
            || String(el.className || '').toLowerCase().includes('disabled');
        if (disabled || !isVisible(el)) continue;

        const rawText = el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '';
        const text = normalizeText(rawText);
        if (!text) continue;

        if (keywords.some(k => text.includes(normalizeText(k)))) {
            return el;
        }
    }
    return null;
    """
    return sb.execute_script(script)


def wait_for_renew_button_by_js(sb, timeout=30):
    deadline = time.time() + timeout
    last_seen = None
    while time.time() < deadline:
        el = find_renew_button_by_js(sb)
        if el:
            return el
        try:
            last_seen = sb.execute_script("""
                return Array.from(document.querySelectorAll('button, a, input[type="button"], input[type="submit"], [role="button"]'))
                    .map(el => (el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').trim())
                    .filter(Boolean)
                    .slice(0, 80);
            """)
        except Exception:
            pass
        sb.sleep(1)
    raise Exception(f"JS 等待 {timeout} 秒后仍未找到续期按钮。最近检测到的可点击文字: {last_seen}")

def click_renew_button(sb, selector):
    """滚动到续期按钮并点击。优先使用 JS 按 innerText 找按钮，XPath 仅作为兜底。"""
    try:
        element = wait_for_renew_button_by_js(sb, timeout=30)
        print("已通过 JS innerText 找到续期按钮。")
    except Exception as js_error:
        print(f"JS 查找续期按钮失败，尝试 XPath 兜底: {js_error}")
        sb.wait_for_element_visible(selector, timeout=15)
        element = sb.find_element(selector)
        print("已通过 XPath 兜底找到续期按钮。")

    sb.execute_script("arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element)
    sb.sleep(0.8)

    try:
        element.click()
        print("已使用 Selenium WebElement.click() 点击续期按钮。")
    except Exception as e:
        print(f"普通点击续期按钮失败，尝试 JS click 兜底: {e}")
        sb.execute_script("arguments[0].click();", element)
        print("已使用 JS click 点击续期按钮。")


def run():
    if not SERVER_URL:
        print("错误: 缺少 ICEHOST_SERVER_URL 环境变量")
        return

    # 1. 启动 SeleniumBase 并开启 UC 免密/防检测模式与 Xvfb 虚拟桌面 (xvfb=True)
    with SB(uc=True, xvfb=True) as sb:
        print(f"正在访问 IceHost 面板: {SERVER_URL}")
        # 使用 UC 专属重连模式访问，能极大缓解首屏 Cloudflare 阻断
        sb.uc_open_with_reconnect(SERVER_URL, reconnect_time=8)
        sb.sleep(5)

        # 2. 注入 Cookies
        if ICEHOST_COOKIES:
            try:
                raw_data = json.loads(ICEHOST_COOKIES)
                cookies_to_add = []
                if isinstance(raw_data, list):
                    cookies_to_add = raw_data
                elif isinstance(raw_data, dict):
                    cookies_to_add = raw_data.get("cookies", [])

                for c in cookies_to_add:
                    raw_value = c["value"]
                    decoded_value = urllib.parse.unquote(raw_value)

                    # 转换格式为 Selenium 格式
                    cookie_dict = {
                        "name": c["name"],
                        "value": decoded_value,
                        "domain": c["domain"],
                        "path": c.get("path", "/"),
                        "secure": c.get("secure", True),
                    }
                    if "sameSite" in c:
                        ss = str(c["sameSite"]).lower()
                        if ss in ["lax", "strict", "none"]:
                            cookie_dict["sameSite"] = ss.capitalize()

                    sb.add_cookie(cookie_dict)
                print("Cookie 成功注入！")

                # 重新刷新加载，应用 Cookie
                sb.refresh()
                sb.sleep(5)
            except Exception as e:
                print(f"注入 Cookie 过程中发生异常，跳过: {e}")

        # 3. 核心过盾：自动寻找并执行系统级物理点击过 Cloudflare Turnstile 验证盾
        sb.save_screenshot(SCREENSHOT_PATH)
        try:
            print("正在检测并调用系统级 PyAutoGUI 驱动，物理点击 Cloudflare 人机验证码...")
            # 在虚拟桌面上定位验证框并模拟发送系统硬件级点击事件
            sb.uc_gui_click_captcha()
            sb.sleep(10)  # 给予 10 秒跳转缓冲
            sb.save_screenshot(SCREENSHOT_PATH)
        except Exception as e:
            print(f"验证盾已被跳过或点击执行完毕: {e}")

        # 4. 判断登录状态
        current_url = sb.get_current_url()
        # 判断是否停留在登录页
        if "login" in current_url or sb.is_element_visible("input[type='email']"):
            msg = "❌ <b>IceHost 登录失效！</b>\n请在浏览器重新提取并更新 ICEHOST_COOKIES。"
            print(msg)
            send_tg_notification(msg, SCREENSHOT_PATH)
            return

        # 5. 判定未到续期时间的限制提示。保留波兰语，并增加英文兜底。
        page_source = sb.get_page_source()
        limit_keywords = [
            "Nie możesz przedłużyć",
            "niedawno to zrobiłeś",
            "kolejne 6 godziny",
            "cannot extend",
            "recently extended",
            "try again later",
        ]
        is_limited = any(kw.lower() in page_source.lower() for kw in limit_keywords)

        if is_limited:
            print("检测到限制提示：说明未到可续期时间。结束本次运行（不发送 Telegram 提醒）。")
            return

        # 6. 安全寻找并点击续期按钮
        renew_btn_selector = build_renew_button_xpath()

        try:
            print("正在等待续期按钮加载（优先使用 JS innerText 识别 ADD 6 HOURS VALIDITY / dodaj 6 等文案）...")
            click_renew_button(sb, renew_btn_selector)
            print("未检测到限制提示，找到续期按钮，并已点击。")

            # 点击后，在不刷新页面的前提下，先等待 5 秒让可能弹出的红框提示充分渲染
            sb.sleep(5)
            sb.save_screenshot(SCREENSHOT_PATH)

            # 立即读取当前最真实的页面源码（此时若有报错红条，通常还挂在屏幕上）
            current_source = sb.get_page_source()
            is_failed_due_to_limit = any(
                kw.lower() in current_source.lower() for kw in limit_keywords
            )

            if is_failed_due_to_limit:
                # 如果点击后页面上弹出了红框，说明“未到可续期时间”（续期未成功）
                # 此时精准拦截：安静退出，不发送 Telegram 提醒。
                print("点击后，页面立刻弹出了限制提示：说明未到可续期时间（续期未成功）。结束本次运行（不发送 Telegram 提醒）。")
                return

            # 如果没有弹出红框，说明时间大概率被成功延长了，这时再刷新验证结果
            print("点击后未检测到报错红条，正在刷新页面确认续期结果...")
            sb.refresh()
            sb.sleep(5)
            sb.save_screenshot(SCREENSHOT_PATH)

            updated_source = sb.get_page_source()
            is_now_limited = any(
                kw.lower() in updated_source.lower() for kw in limit_keywords
            )

            if is_now_limited:
                msg = "⚡ <b>IceHost 服务器续期成功！</b>\n服务器已真正成功延长 6 小时有效期。"
                print(msg)
                send_tg_notification(msg, SCREENSHOT_PATH)
            else:
                msg = "ℹ️ <b>IceHost 续期指令已发送</b>\n按钮已点击，请检查下方截图确认是否成功。"
                print(msg)
                send_tg_notification(msg, SCREENSHOT_PATH)
        except Exception as e:
            sb.save_screenshot(SCREENSHOT_PATH)
            print(f"未找到匹配当前语言文案的续期按钮，或按钮不可点击: {e}")
            dump_clickable_texts(sb)
            msg = (
                "❌ <b>IceHost 续期脚本异常</b>\n"
                "页面里可能能看到按钮，但自动点击失败。请查看 GitHub Actions 日志和截图。"
            )
            send_tg_notification(msg, SCREENSHOT_PATH)


if __name__ == "__main__":
    run()

"""Responsive sign-in checks use a temporary database and local test requests."""
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

from tests.test_web import add_user, client  # noqa: F401


def test_login_layout_and_controls(client, session, tmp_path):
    add_user(session, 'layout_user', 'admin')

    def serve(route):
        req = route.request
        url = urlsplit(req.url)
        response = client.request(req.method, url.path + ('?' + url.query if url.query else ''),
                                  content=req.post_data_buffer,
                                  headers={k: v for k, v in req.all_headers().items()
                                           if k in ('content-type', 'x-requested-with')})
        route.fulfill(status=response.status_code, body=response.content,
                      headers={k: v for k, v in response.headers.items()
                               if k.lower() not in ('content-length', 'content-encoding', 'transfer-encoding')})

    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel='msedge', headless=True)
        page = browser.new_page(viewport={'width': 390, 'height': 844})
        page.route('http://qa.test/**', serve)
        page.route('https://**', lambda route: route.abort())
        for width, height in ((320, 568), (390, 844), (700, 700), (760, 700), (761, 700), (1024, 768), (1440, 900)):
            page.set_viewport_size({'width': width, 'height': height})
            page.goto('http://qa.test/login')
            for theme in ('light', 'dark'):
                page.evaluate('(theme) => document.documentElement.dataset.theme = theme', theme)
                card = page.locator('.login-card').bounding_box()
                assert card['x'] >= 0 and card['x'] + card['width'] <= width
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert page.locator('#login-username').is_visible()
                assert page.locator('#login-password').is_visible()
                assert page.get_by_role('button', name='Sign in').is_visible()
                if width <= 760:
                    content_width = page.locator('body').bounding_box()['width']
                    assert abs(card['x'] - (content_width - card['x'] - card['width'])) < 2
                    assert not page.locator('.login-story').is_visible()
                else:
                    assert page.locator('.login-story').is_visible()
                if (width, theme) in ((390, 'light'), (1440, 'dark')):
                    page.screenshot(path=str(tmp_path / f'login-{width}-{theme}.png'))

        page.set_viewport_size({'width': 390, 'height': 844})
        page.goto('http://qa.test/login')
        page.locator('#theme-toggle').click()
        assert page.locator('html').get_attribute('data-theme') == 'dark'
        page.locator('#login-username').fill('layout_user')
        page.locator('#login-password').fill('incorrect-test-password')
        page.get_by_role('button', name='Sign in').click()
        page.get_by_role('alert').wait_for()
        assert 'Wrong username' in page.get_by_role('alert').inner_text()
        assert page.get_by_role('link', name='中文').is_visible()
        browser.close()

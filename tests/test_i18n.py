"""Language toggle (English / 中文): cookie, route, catalog fallback, and rendered chrome."""
from app import i18n
from tests.test_web import add_user, client, csrf_of, login  # noqa: F401


def test_translate_falls_back_to_english_for_unknown_strings():
    assert i18n.translate("zh", "Sales summary") == "销售汇总"
    assert i18n.translate("zh", "some untranslated string") == "some untranslated string"
    assert i18n.translate("en", "Sales summary") == "Sales summary"
    assert i18n.norm("fr") == "en" and i18n.norm("zh") == "zh"


def test_lang_route_sets_cookie_and_blocks_open_redirects(client):
    r = client.get("/lang/zh?back=/sales")
    assert r.status_code == 303 and r.headers["location"] == "/sales"
    assert 'lang=zh' in r.headers["set-cookie"]
    r = client.get("/lang/zh?back=https://evil.example")
    assert r.headers["location"] == "/"
    r = client.get("/lang/klingon")
    assert 'lang=en' in r.headers["set-cookie"]  # unknown codes fall back to English


def test_pages_render_chinese_when_the_cookie_is_set(client, session):
    add_user(session, "boss", "management")
    login(client, "boss")
    client.cookies.set("lang", "zh")
    dash = client.get("/")
    assert dash.status_code == 200 and "销售汇总" in dash.text and "需要关注" in dash.text
    assert 'lang="zh"' in dash.text
    login_page = client.get("/login")
    assert "欢迎回来" in login_page.text
    client.cookies.set("lang", "en")
    assert "Sales summary" in client.get("/").text


def test_chinese_content_on_list_forms_and_detail_pages(client, session, sync, fake, monkeypatch):
    from sqlalchemy import select
    from app.models import Inspection, Location, Product
    from app.services import counts, inspections, issues, receiving, replenishment
    from app.timeutil import today_local
    from tests.test_services import _po
    from app.ai import service
    from app.ai.provider import Provider

    monkeypatch.setattr(service, "get_provider", lambda *args, **kwargs: Provider())

    po = _po(sync, fake, session, qty=1)
    boss = add_user(session, "boss", "admin")
    loc = session.scalar(select(Location).where(Location.storehub_id == "S_A"))
    product = session.scalar(select(Product).where(Product.storehub_id == "P1"))
    inspections.generate_tasks(session, today_local())
    count = counts.create(session, boss, loc)
    issue = issues.create_issue(session, type="equipment", severity="P2", title="Printer jam", owner_id=boss.id)
    receipt = receiving.start(session, boss, po)
    replenishment.run(session)
    session.commit()
    inspection = session.scalar(select(Inspection).where(Inspection.location_id == loc.id))
    login(client, "boss")

    # Check page content itself, so a translated sidebar cannot satisfy this test.
    pages = {
        "/inventory": ("现有库存", "在途库存", "搜索"),
        "/sales": ("现金销售额", "每日净销售额", "交易笔数"),
        "/sales/products": ("售出数量", "全部分类", "排序方式"),
        f"/sales/product/{product.id}": ("Cola 500ml", "每日售出数量", "按门店"),
        "/replenishment": ("待审核", "为什么是这个数量", "批准"),
        "/replenishment/batches": ("订货批次", "返回建议"),
        "/purchase-orders": ("采购单", "供应商"),
        f"/purchase-orders/{po.id}": ("开始收货盘点", "已收货（StoreHub）"),
        f"/receiving/{receipt.id}": ("损坏数量", "保存并完成"),
        "/issues": ("指派给我", "Printer jam", "待处理"),
        "/issues/new": ("新建工单", "设备", "负责人"),
        f"/issues/{issue.id}": ("Printer jam", "确认收到", "历史记录"),
        "/inspections": ("门店每日巡检", "检查清单"),
        f"/inspections/{inspection.id}": ("门店入口", "提交检查清单", "请添加照片供经理查看"),
        "/counts": ("开始今日盘点", "循环盘点"),
        f"/counts/{count.id}": ("实盘数量", "保存并提交审核"),
        "/stock-events": ("记录报损", "计划外收货"),
        "/stock-events/new": ("记录报损", "失窃 / 丢失", "照片（可选证据）"),
        "/stock-events/new?kind=unplanned_receipt": ("记录计划外收货", "无采购单到货"),
        "/expiry": ("效期跟踪", "记录批次"),
        "/compliance": ("添加证照", "办理日期"),
        "/performance": ("盘点准确率", "确认用时中位数"),
        "/data-quality": ("发现的问题", "首次发现"),
        "/sync": ("最近成功", "备份", "任务"),
        "/settings": ("地点与规则", "规则与限制", "库存数据超过此分钟数后视为过期"),
        "/suppliers": ("填写供应商真实名称", "交货期（天）"),
        "/products": ("商品设置", "每箱数量"),
        "/users": ("添加用户", "门店经理", "新密码"),
        "/audit": ("操作包含", "审计日志"),
        "/assistant": ("助手尚未连接", "AI"),
        "/assistant/metrics": ("助手是否有帮助", "平均回答时间"),
        "/password": ("当前密码", "再次输入新密码"),
        "/notifications": ("全部标为已读", "通知"),
    }
    client.cookies.set("lang", "zh")
    for path, labels in pages.items():
        response = client.get(path)
        assert response.status_code == 200, path
        content = response.text.split('<main class="content"', 1)[1].split('</main>', 1)[0]
        for label in labels:
            assert label in content, (path, label)
        assert 'lang="zh"' in response.text
    client.cookies.set("lang", "en")
    for path, label in (("/inventory", "Stock now"), ("/settings", "Rules and limits"), ("/issues/new", "Add problem")):
        assert label in client.get(path).text


def test_translated_forms_keep_server_values_and_save_successfully(client, session, sync):
    import re
    from sqlalchemy import select
    from app.models import Location

    sync.sync_stores()
    add_user(session, "boss", "admin")
    login(client, "boss")
    client.cookies.set("lang", "zh")
    html = client.get("/settings").text
    assert re.search(r'<option[^>]*value="store"[^>]*>门店</option>', html)
    assert re.search(r'<option[^>]*value="warehouse"[^>]*>仓库</option>', html)
    assert '<option value="staff">员工</option>' in client.get("/users").text
    assert '<option value="damaged">损坏</option>' in client.get("/stock-events/new").text
    assert 'value="equipment">设备</option>' in client.get("/issues/new").text
    loc = session.scalar(select(Location).where(Location.type == "store"))
    result = client.post(f"/settings/locations/{loc.id}", data={
        "csrf": csrf_of(client, "/settings"), "type": "store", "monitored": "1",
        "opens": "08:00", "closes": "20:00", "manager": "0", "lead": "1",
    })
    assert result.status_code == 303
    page = client.get(result.headers["location"])
    assert f"已保存 {loc.name}" in page.text
    session.expire_all()
    assert session.get(Location, loc.id).type == "store"


def test_chinese_chat_fragment_and_browser_messages(client, session, sync, fake, monkeypatch):
    import json
    import re
    from app.ai import service
    from app.ai.provider import LlmResult, Provider
    from tests.test_services import seeded

    class Model(Provider):
        name, model, available = "fake", "fake-1", True

        def complete(self, system, messages, max_tokens=0, timeout=0):
            return LlmResult(json.dumps({
                "answer": [{"type": "hypothesis", "text": "业务回答", "refs": []}],
                "suggested_actions": [], "follow_ups": [],
            }), "fake", "fake-1", 3, 0.0)

    monkeypatch.setattr(service, "get_provider", lambda *args, **kwargs: Model())
    seeded(sync, fake, session)
    add_user(session, "boss", "management")
    login(client, "boss")
    client.cookies.set("lang", "zh")
    html = client.get("/assistant").text
    assert 'data-ask="本周哪些商品最可能缺货？"' in html
    messages = json.loads(re.search(r'<script id="ui-messages" type="application/json">(.*?)</script>', html, re.S)[1])
    assert messages["Collapse navigation"] == "收起导航"
    assert messages["Something went wrong."] == "出现错误。"
    result = client.post("/assistant/chat", data={"question": "销售如何？", "csrf": csrf_of(client, "/assistant")}).json()
    assert result["ok"]
    assert "业务回答" in result["html"] and "推测" in result["html"] and "证据" in result["html"]
    assert client.get(f"/assistant/output/{result['id']}").status_code == 200


def test_localized_messages_preserve_dynamic_values_and_fallback():
    assert i18n.translate("zh", "Saved Cola <500ml>") == "已保存 Cola <500ml>"
    assert i18n.translate("zh", "5 min ago") == "5 分钟前"
    assert i18n.translate("en", "5 min ago") == "5 minutes ago"
    assert i18n.translate("zh", "{count} units", count=12) == "12 件"
    assert i18n.translate("zh", "Custom user text with {braces}") == "Custom user text with {braces}"


def test_error_pages_use_chinese_and_keep_permission_status(client, session):
    add_user(session, "cashier", "staff")
    login(client, "cashier")
    client.cookies.set("lang", "zh")
    response = client.get("/settings")
    assert response.status_code == 403
    assert "你的角色无权执行此操作" in response.text and "返回" in response.text
    assert 'lang="zh"' in response.text


def test_plain_english_uses_stable_keys_and_preserves_values():
    assert i18n.translate("en", "Cycle counts") == "Stock counts"
    assert i18n.translate("en", "Replenishment") == "Order suggestions"
    assert i18n.translate("en", "Data quality") == "Data checks"
    assert i18n.translate("en", "3 item(s) show negative stock") == "3 items have stock below zero"
    assert i18n.translate("en", "Photo {number}", number=2) == "Photo 2"
    assert i18n.translate("zh", "Replenishment") == "补货"
    assert i18n.translate("zh", "Photo {number}", number=2) == "照片 2"
    assert i18n.translate("en", "Custom product {name}") == "Custom product {name}"

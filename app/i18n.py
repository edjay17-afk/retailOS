"""Interface translations (English / 中文). English is the source of truth: the English string itself is the key,
gettext-style. Plain English wording lives in locales/en.json; Chinese uses the same stable keys.
The toggle sets a `lang` cookie. Unknown strings keep their original text."""
from __future__ import annotations

import json
import re
from pathlib import Path

LANGS = {"en": "English", "zh": "中文"}
DEFAULT = "en"
COOKIE_NAME = "lang"

ZH = {
    # nav groups and labels
    "Overview": "概览", "Today": "今日", "Operations": "运营", "Issues": "问题工单", "Inspections": "巡检",
    "Cycle counts": "循环盘点", "Stock events": "库存事件", "Expiry": "效期",
    "Permits and maintenance": "证照与维护", "Stock and ordering": "库存与订货", "Replenishment": "补货",
    "Inventory": "库存", "Purchase orders": "采购单", "Sales": "销售", "Sales by product": "商品销售",
    "Intelligence": "智能", "Assistant": "助手", "Performance": "绩效", "Data quality": "数据质量",
    "Sync status": "同步状态", "Administration": "管理", "Settings": "设置", "Audit log": "审计日志",
    # app chrome
    "Skip to content": "跳到主要内容", "Search products, barcodes": "搜索商品名称或条码",
    "Print report": "打印报表", "Notifications": "通知", "Change password": "修改密码", "Sign out": "退出登录",
    "Expand navigation": "展开导航", "Close navigation": "收起导航", "Open navigation": "打开导航",
    "Retail OS · StoreHub is the source of truth for sales, stock and purchase orders. This system never writes to StoreHub.":
        "Retail OS · StoreHub 是销售、库存与采购单的真相来源，本系统从不写入 StoreHub。",
    "Data warning.": "数据警告。", "not synced yet": "尚未同步", "stale": "数据过期", " min old": " 分钟前",
    "Figures may be out of date; order suggestions are paused until it recovers.":
        "数据可能不是最新；补货建议已暂停，直至同步恢复。",
    # dashboard
    "Daily overview": "每日概览", "Good morning": "早上好", "Good afternoon": "下午好", "Good evening": "晚上好",
    "Your stores, stock and priorities in one place.": "你的门店、库存与优先事项，尽在一屏。",
    "Sales summary": "销售汇总", "Last 14 complete days": "最近 14 个完整日",
    "Total net sales": "总净销售额", "Daily average": "日均", "Highest day": "最高单日", "Lowest day": "最低单日",
    "Explore sales": "查看销售", "Net sales across your stores": "全部门店净销售额",
    "Focus or hover for exact amounts": "聚焦或悬停查看确切金额",
    "Sales will appear after your first StoreHub sync.": "首次 StoreHub 同步后才会显示销售数据。",
    "Check sync status": "查看同步状态",
    "Sales by store": "各门店销售", "Count deleted": "盘点已删除", "No Expiry": "无到期日", "Wastage": "损耗", "Record wastage": "记录损耗", "Record anything thrown away or lost. Every record also goes to the Wastage Record table in Lark.": "记录所有丢弃或丢失的商品。每条记录也会同步到 Lark 的损耗记录表。", "Store": "门店", "Date Created": "创建日期", "Quantity": "数量", "Period": "时间段", "Last {n} days": "最近 {n} 天", "records": "条记录", "units": "件", "No wastage recorded in this period.": "此时间段内没有损耗记录。", "Spoiled": "变质", "Expired": "已过期", "Damaged": "损坏", "Lost / theft": "丢失/被盗", "Supplier return": "退回供应商", "Other": "其他", "Category": "类别", "By": "记录人", "Planogram": "陈列图", "Open full screen": "全屏打开", "Walk through the store in 3D and see where each product goes on the shelf.": "以 3D 方式漫游门店，查看每个商品的货架位置。", "The 3D store has not been added yet. Run scripts/sync_planogram.py once to copy it in.": "尚未添加 3D 门店。请运行一次 scripts/sync_planogram.py 将其复制进来。", "Product name or barcode": "产品名称或条形码", "Store / branch": "门店/分店", "All users": "所有用户", "Access to all locations": "可访问所有地点", "Search": "搜索", "Name or username": "姓名或用户名", "Filter": "筛选", "Clear": "清除", "Showing {n} of {total} users.": "显示 {n} / {total} 位用户。", "No users match this filter.": "没有符合筛选条件的用户。", "Leave empty if the permit has no expiry": "如果证件没有到期日，请留空", "Based on sales only for now.": "目前仅按销量计算。", "Stock on hand is not used while inventory is not accurate. Each suggestion covers {days} days of sales, minus what is already on order, and anything approved in the last {days} days is not suggested again.": "库存不准确期间不使用现有库存。每条建议覆盖 {days} 天的销量，并扣除已在途订单；最近 {days} 天内已批准的商品不会再次建议。", "Sales-only suggestion: stock on hand is not used, so the figures below that mention stock are for reference.": "仅按销量的建议：不使用现有库存，下方涉及库存的数字仅供参考。", "Operations at a glance": "运营概览", "Order suggestions, stock, purchase orders and transfers": "订货建议、库存、采购订单和调拨", "Order suggestions today": "今日订货建议", "How many suggestions are waiting, being decided or done": "等待、处理中和已完成的建议数量", "Waiting to be ordered, by store": "各门店待下单金额", "Value of suggestions not yet turned into an order": "尚未转为订单的建议金额", "Ordering need, last 14 days": "近 14 天订货需求", "Total value of the suggestions raised each day": "每天产生的建议总金额", "Days of stock left": "剩余库存天数", "Open suggestions grouped by how long stock will last": "未处理建议按库存可支撑天数分组", "Stock health by store": "各门店库存状况", "Items that sold in the last 30 days: in stock, running low, out of stock": "近 30 天有销量的商品：有库存、偏低、缺货", "Purchase orders": "采购订单", "By status": "按状态", "overdue": "逾期", "Stock transfers": "库存调拨", "From the StoreHub export, by status": "来自 StoreHub 导出，按状态", "Best sellers, last 7 days": "近 7 天畅销商品", "By net sales across your stores": "按各门店净销售额", "Expiry exposure": "临期风险", "Batches still on the shelf, by how soon they expire": "仍在货架上的批次，按到期时间分组", "Under 2 days": "不足 2 天", "2 to 4 days": "2 至 4 天", "5 to 9 days": "5 至 9 天", "10 to 14 days": "10 至 14 天", "15+ days": "15 天以上", "Expired": "已过期", "Within 3 days": "3 天内", "4 to 7 days": "4 至 7 天", "8 to 14 days": "8 至 14 天", "15 to 30 days": "15 至 30 天", "No suggestions yet today.": "今天还没有建议。", "Nothing waiting.": "没有待处理项。", "Not enough days yet.": "天数还不够。", "No open suggestions.": "没有未处理的建议。", "No stock data yet.": "暂无库存数据。", "No purchase orders yet.": "暂无采购订单。", "No transfers imported yet.": "尚未导入调拨。", "No sales yet.": "暂无销售。", "No batches recorded.": "暂无批次记录。", "Send for approval right away (otherwise it stays open so you can review the differences)": "立即提交审批（否则保持未提交，方便先查看差异）", "Columns: Product (barcode, SKU or name), Counted, Note (optional), Location (optional if one is chosen above). Counts go into today's open count and are compared with the StoreHub stock.": "列：产品（条码、SKU 或名称）、实盘数量、备注（可选）、门店（上方已选择时可不填）。数量会记入今日未提交的盘点，并与 StoreHub 库存比较。", "Type from the file": "类型取自文件", "Write-off": "报损", "Unplanned receipt": "计划外收货", "These are already entered in StoreHub (no to-do to key them in)": "这些已录入 StoreHub（无需再创建录入任务）", "Columns: Product (barcode, SKU or name), Qty, Type (write-off or unplanned receipt), Reason (optional), Note (optional), Location (optional if one is chosen above), Supplier (optional, for receipts). The same line uploaded twice on one day is recorded once.": "列：产品（条码、SKU 或名称）、数量、类型（报损或计划外收货）、原因（可选）、备注（可选）、门店（上方已选择时可不填）、供应商（可选，收货用）。同一天重复上传的同一行只记录一次。", "Last import from StoreHub": "最近一次 StoreHub 导入", "No StoreHub export imported yet": "尚未导入 StoreHub 导出文件", "A StoreHub stock-transfer export could not be imported": "StoreHub 调拨导出文件无法导入", "Stock transfers have not been updated from StoreHub": "库存调拨尚未从 StoreHub 更新", "Average per day": "日均", "Avg / day": "日均", "{done} of {total} items photographed": "已拍照 {done} / {total} 项", "Stock health": "库存状况", "Closest to running out": "最接近缺货", "healthy": "健康", "In stock": "有库存", "Running low": "库存偏低", "Out of stock": "缺货", "No stock data for your store yet.": "你的门店暂无库存数据。", "Today's checklists": "今日清单", "waiting for your review": "等待你审核", "Check each suggestion, then approve, change or reject it.": "逐条查看建议，然后批准、修改或拒绝。", "Nothing needs action right now.": "目前没有需要处理的事项。", "Ask the assistant": "询问助手", "Answers come from your own data": "答案来自你自己的数据", "All checklists are done. Great work today.": "所有清单已完成，今天辛苦了。", "checklist(s) still to do today.": "项清单今天还需完成。", "All done": "全部完成", "of checklists completed": "清单已完成", "done": "已完成", "Start": "开始", "due": "截止", "Update permit": "更新证照", "Cancel": "取消", "Remove this permit from the list?": "确定将此证照从列表中移除？", "Permits": "证照", "Permit": "证照", "Add a permit": "添加证照", "Processed on": "办理日期", "Expires on": "到期日期", "New expiry date": "新的到期日期", "Permit name, e.g. Mayor's permit": "证照名称，例如市长许可证", "No permits recorded yet. Add your permits with their expiry dates.": "尚无证照记录。请添加证照及其到期日期。", "Your permits with the date each was processed and when it expires. You are alerted 30, 14 and 7 days before expiry, and when it has expired.": "每份证照的办理日期和到期日期。到期前 30、14、7 天以及过期后都会提醒您。", "The expiry date must be after the date the permit was processed": "到期日期必须晚于证照办理日期", "The new expiry date must be later than the current one": "新的到期日期必须晚于当前日期", "Add photos for the manager to review.": "请添加照片供经理查看。", "Search and choose a product to fill in its details. You can also enter a SKU yourself.": "搜索并选择商品即可自动填写信息，也可以自己输入 SKU。", "Write a reason for changing or rejecting this suggestion": "请写明修改或拒绝此建议的原因", "Expired": "已过期", "Within 7 days": "7 天内", "8 to 30 days": "8 至 30 天", "Batches tracked": "跟踪中的批次", "File": "文件", "Upload a CSV or Excel file": "上传 CSV 或 Excel 文件", "Location from the file": "使用文件中的门店", "Upload": "上传", "Download template": "下载模板", "The file is larger than 5 MB": "文件超过 5 MB", "Columns: Product (barcode, SKU or name), Qty, Expiry date, Batch (optional), Location (optional if one is chosen above). Uploading the same file twice does not duplicate batches.": "列：产品（条码、SKU 或名称）、数量、到期日、批次（可选）、门店（上方已选择时可不填）。重复上传同一文件不会产生重复批次。", "Delete": "删除", "Delete count #{id}? Its entered quantities will be lost. This is recorded in the audit log.": "删除盘点 #{id}？已录入的数量将丢失，此操作会记入审计日志。", "Yesterday · PHP": "昨天 · PHP", "Today · PHP": "今天 · PHP", "Yesterday": "昨天", "Net sales today": "今日净销售额", "Today so far, from the latest sync.": "今日截至最近一次同步。",
    "Change vs the previous 13-day average.": "与之前 13 天均值相比的变化。",
    "Compared with the previous 13-day average": "与之前 13 天均值相比",
    "Net sales yesterday": "昨日净销售额", "vs previous 13-day average": "对比前 13 天均值",
    "Across your stores": "全部门店合计",
    "Open issues": "待处理工单", "urgent": "紧急", "No urgent issues": "无紧急工单",
    "Checklists completed": "已完成清单", "For today's operations": "今日运营",
    "Orders ready for review": "待审核订单", "Suggested value: PHP": "建议金额：PHP",
    "Needs attention": "需要关注", "Most urgent first": "最紧急优先",
    "Nothing needs management action right now.": "目前没有需要管理层处理的事项。",
    "View supporting facts": "查看支持事实",
    "Daily checklists": "每日清单", "View all": "查看全部", "completed": "已完成",
    "Late · ": "已逾期 · ",
    "All scheduled checklists are complete.": "今日所有计划清单已完成。",
    "No checklists scheduled today.": "今日没有安排的清单。",
    "AI briefing": "AI 简报", "Generated": "生成于", "statements verified": "条陈述已核实",
    "Your next briefing is ready to generate.": "下一份简报已可生成。",
    "Connect your AI provider to get daily briefings.": "连接 AI 服务以获取每日简报。",
    "Read the briefing": "阅读简报", "Open assistant": "打开助手",
    "Data status": "数据状态", "Sync details": "同步详情",
    "Products": "商品", "Staff and timesheets": "员工与工时", "last synced": "最近同步",
    "high": "高", "medium": "中", "low": "低",
    # login
    "Sign in": "登录", "Welcome back": "欢迎回来", "Sign in to continue.": "登录以继续使用。",
    "Username": "用户名", "Password": "密码",
    "Wrong username or password.": "用户名或密码错误。",
    "Too many failed attempts. Try again in a few minutes.": "失败次数过多，请稍后再试。",
    "Run every store from one calm screen.": "在一方平静的屏幕上，运营每一家门店。",
    "Sales and stock from StoreHub, checked and explained. Orders suggested, issues owned, nothing lost.":
        "销售与库存来自 StoreHub，经核查并解释。订单有建议，问题有归属，无一遗漏。",
    "Every number traces back to StoreHub data": "每个数字都可追溯至 StoreHub 数据",
    "An AI assistant that cites its facts and asks before it acts": "AI 助手引用事实依据，行动必先征得同意",
    "Full audit trail of who did what, and why": "完整审计记录：谁、做了什么、为什么",
}


# Page content shares the same English keys and fallback as the app chrome.
ZH.update(json.loads((Path(__file__).parent / "locales" / "zh.json").read_text(encoding="utf-8")))
EN = json.loads((Path(__file__).parent / "locales" / "en.json").read_text(encoding="utf-8"))

# Only known system message formats are matched; unknown text and business
# names retain the English fallback rather than being translated in pieces.
MESSAGE_KEYS = (
    "{count} min ago", "{count} h ago", "{count} d ago", "Saved {name}", "Added {name}",
    "Started: {job}. Refresh in a few seconds.", "Backup written: {name}",
    "Suggestions updated: {created} new, {updated} refreshed",
    "Approved {count} suggestion(s)", "{count} order batch(es) created",
    "Transfer #{id} marked as dispatched", "{count} unverified",
    "overdue by {count} d", "{count} days left",
    "{product} at {location}: on hand is {qty}",
    "{domain} data is stale: never synced",
    "{domain} data is stale: last success {age} min ago (limit {limit})",
    "{product}: sells but has no supplier, so it cannot be ordered",
    "{product}: no cost set in StoreHub, margin and order value are unreliable",
    "{product}: no barcode", "Barcode {barcode} is used by {count} products: {names}",
    "PO #{id} is still {status} and {days} days past its date; it is not counted as incoming (limit {limit} days). Receive it or cancel it in StoreHub.",
    "Open PO #{id} has {count} line(s) with no ordered quantity",
    "Sales at {location} on {date} were {amount}, far above the recent average of {average}",
    "{product} at {location}: {qty} sold in 24h but stock did not change",
    "PO #{id} shows {qty} received but stock never increased",
    "{count} sale line(s) in the last 7 days refer to a product not in the catalog ({id})",
    "{count} issue(s) need attention", "{count} item(s) show negative stock",
    "{count} open PO(s) are far past their date",
    "{ready} order suggestion(s) ready, {review} need review",
    "{count} batch(es) expire within {days} days",
    "{count} checklist(s) are late or missed today",
)
MESSAGE_PATTERNS = [
    (re.compile("^" + re.sub(r"\\\{(\w+)\\\}", r"(?P<\1>.+?)", re.escape(key)) + "$"), key)
    for key in MESSAGE_KEYS
]

JS_MESSAGES = (
    "Clear search", "Please complete this field.",
    "Enter a value of at least {value}.", "Enter a value no greater than {value}.",
    "Enter at least {value} characters.", "Enter a valid email address.",
    "Enter a valid number using the required increment.", "Check this value and try again.",
    "Collapse navigation", "Expand navigation", "Close navigation", "Open navigation",
    "{done} of {total} answered", "{done} of {total} items photographed", "Tap to replace", "Remove file", "Remove",
    "Add more photos", "{count} photos added", "Photo {number}", "Tap to view full size",
    "The AI could not explain this.", "Could not reach the server.",
    "Hi, I can answer questions about stock, sales, purchase orders and issues, using only what is in the system. What would you like to know?",
    "Reading your data, this takes 20 to 40 seconds...",
    "Your session ended. Reload the page and sign in again.",
    "Not allowed, or the page is out of date. Reload and try again.",
    "Something went wrong.", "That took too long. Try a shorter question.",
    "Dark mode", "Light mode",
)


def norm(lang: str | None) -> str:
    return lang if lang in LANGS else DEFAULT


def translate(lang: str, text: str, **values) -> str:
    result = text
    words = ZH if norm(lang) == "zh" else EN
    if text:
        result = words.get(text, text)
        if result == text:
            for pattern, key in MESSAGE_PATTERNS:
                match = pattern.fullmatch(text)
                if match and key in words:
                    fields = match.groupdict()
                    for field in ("domain", "status", "job"):
                        if field in fields:
                            fields[field] = words.get(fields[field], fields[field])
                    result = words[key].format_map(fields)
                    break
    return result.format_map(values) if values else result

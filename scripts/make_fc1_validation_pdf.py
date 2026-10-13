# -*- coding: utf-8 -*-
"""Builds Retail_OS_V2_1_FC1_Field_Validation_Update_EN_ZH_v2.pdf — bilingual (EN/ZH)
field-validation update for Retail OS V2.1-FC1. v2 replaces the two staff-described
workflow sequences with the on-site observed flows (12 Oct 2026):

  Supplier receiving: warehouse receiver receives goods -> takes the receipt -> passes
  it to the assigned worker -> worker checks quantity and expiry -> only if confirmed,
  receiving is posted to the PO in StoreHub.

  Warehouse -> store: wait for a scheduled delivery to that destination -> create the
  stock transfer in StoreHub first -> pack -> load -> store receives and counts ->
  store reports back to the assigned warehouse worker.

One-off document generator; not part of the app. Requires reportlab."""
import os

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (HRFlowable, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

FONTS = "C:/Windows/Fonts"
pdfmetrics.registerFont(TTFont("ZH", os.path.join(FONTS, "msyh.ttc"), subfontIndex=0))
pdfmetrics.registerFont(TTFont("ZH-B", os.path.join(FONTS, "msyhbd.ttc"), subfontIndex=0))

INK = colors.HexColor("#0f172a")
MUTE = colors.HexColor("#475569")
LINE = colors.HexColor("#d6d9e6")
ACCENT = colors.HexColor("#4f46e5")
SOFT = colors.HexColor("#f8f9fc")
GOOD = colors.HexColor("#0f766e")
WARN = colors.HexColor("#a85a00")

s_title = ParagraphStyle("title", fontName="ZH-B", fontSize=19, leading=25, textColor=INK)
s_sub = ParagraphStyle("sub", fontName="ZH", fontSize=9.5, leading=14, textColor=MUTE)
s_h1 = ParagraphStyle("h1", fontName="ZH-B", fontSize=14, leading=19, textColor=ACCENT, spaceBefore=15, spaceAfter=5)
s_h2 = ParagraphStyle("h2", fontName="ZH-B", fontSize=11, leading=15, textColor=INK, spaceBefore=10, spaceAfter=3)
s_en = ParagraphStyle("en", fontName="ZH", fontSize=9.6, leading=14.5, textColor=INK, spaceAfter=4)
s_zh = ParagraphStyle("zh", fontName="ZH", fontSize=9.6, leading=15, textColor=MUTE, spaceAfter=8, leftIndent=10)
s_cell = ParagraphStyle("cell", fontName="ZH", fontSize=8.6, leading=12.6, textColor=INK)
s_cellm = ParagraphStyle("cellm", parent=s_cell, textColor=MUTE)
s_cellh = ParagraphStyle("cellh", parent=s_cell, fontName="ZH-B", textColor=colors.white)

story = []


def H(t): story.append(Paragraph(t, s_h1))
def H2(t): story.append(Paragraph(t, s_h2))
def EN(t): story.append(Paragraph(t, s_en))
def ZH(t): story.append(Paragraph(t, s_zh))
def rule(): story.append(HRFlowable(width="100%", thickness=0.6, color=LINE, spaceBefore=2, spaceAfter=8))


def P(t, style=None): return Paragraph(t, style or s_cell)


def simple_table(rows, widths, header=True, header_bg=INK):
    t = Table(rows, colWidths=widths, repeatRows=1 if header else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.4, LINE),
          ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
          ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if header:
        st += [("BACKGROUND", (0, 0), (-1, 0), header_bg)]
    st += [("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT])]
    t.setStyle(TableStyle(st))
    story.append(t)


def label(txt_en, txt_zh, color):
    return (f'<font color="{color.hexval()}"><b>{txt_en}</b></font> / '
            f'<font color="{color.hexval()}"><b>{txt_zh}</b></font>')


OBS = label("Observed on-site", "现场观察", GOOD)
STAFF = label("Staff confirmed", "人员确认", ACCENT)
SYS = label("System confirmed", "系统确认", INK)
INF = label("Inferred", "推断", WARN)

# ---------------------------------------------------------------- cover block
story.append(Paragraph("RETAIL OS V2.1", s_title))
story.append(Paragraph("Field validation update — v2 (observed warehouse flows)", s_h2))
story.append(Paragraph("现场验证进展报告 — v2（仓库流程已现场观察）", s_sub))
story.append(Paragraph("Management edition | English / Chinese | 12 October 2026", s_sub))
story.append(Paragraph("管理层审阅版 | 英文／中文 | 2026年10月12日", s_sub))
story.append(Spacer(1, 6))
simple_table([
    [P("VERSION / 版本", s_cellh), P("CURRENT POSITION / 当前状态", s_cellh)],
    [P("<b>2.1-FC1</b><br/>Freeze candidate / 冻结候选版"),
     P("Direction approved; both warehouse flows now observed on-site. Ten data cases remain open.<br/>"
       "方向已批准；两条仓库流程已完成现场观察。10个数据案例仍待结案。")],
], [45 * mm, 133 * mm], header_bg=ACCENT)
story.append(Spacer(1, 6))

EN(f"{STAFF} — The direction is approved, as communicated by management through the user. The scope of this "
   f"phase is limited to one supplier receiving flow, one complete warehouse-to-store transfer, and the original "
   f"five negative-stock cases and five stale POs. Benchmark research is paused.")
ZH("人员确认 — 用户转述的管理层意见已批准V2.1方向。本阶段工作仅限一次供应商收货、一次完整的仓库到门店调拨，"
   "以及原报告中的5个负库存案例和5张长期未结采购单。竞品研究暂停。")
EN(f"{OBS} — On 12 October 2026 the reviewer observed the supplier receiving flow and the warehouse-to-store "
   f"transfer flow at the warehouse and supplied the corrected step sequences in Sections 1 and 2. These replace "
   f"the earlier staff-described sequences. Transaction-level evidence (references, quantities, timestamps) is "
   f"still to be attached per the completion table in Section 7.")
ZH("现场观察 — 2026年10月12日，观察人在仓库现场观察了供应商收货流程及仓库到门店调拨流程，并提供了第1、2节中"
   "更正后的步骤顺序，取代此前由人员描述的顺序。交易级证据（单号、数量、时间）仍须按第7节完成表补齐。")
EN(f"{SYS} — A read-only system investigation was completed on 09 October at 14:30 Philippine time. It updated "
   f"the same ten cases (Sections 3–5). Those cases remain open for physical verification and disposition.")
ZH("系统确认 — 已于10月9日菲律宾时间14:30完成只读系统调查，更新了原来的10个案例（第3–5节）。这些案例仍待"
   "实物核实及处理结论。")

H2("Evidence labels / 证据标识")
simple_table([
    [P("EVIDENCE LABEL / 证据标识", s_cellh), P("MEANING / 含义", s_cellh)],
    [P(f"<b>{OBS}</b>"), P("Directly witnessed transaction or process, with observer and date. "
                           "直接见证的交易或流程，附观察人及日期。")],
    [P(f"<b>{STAFF}</b>"), P("Process or fact stated by a person; identify who and what was confirmed. "
                             "人员陈述的流程或事实；明确确认者及确认内容。")],
    [P(f"<b>{SYS}</b>"), P("Recorded data or documented test result; not proof of physical stock. "
                           "系统记录或已记载的测试结果；不等于实物库存证据。")],
    [P(f"<b>{INF}</b>"), P("Hypothesis, interpretation or proposed action requiring verification. "
                           "仍需验证的假设、解释或建议措施。")],
], [55 * mm, 123 * mm])

# ---------------------------------------------------------------- section 1
H("1. Supplier receiving — warehouse / 供应商收货 — 仓库")
EN(f"{OBS} — Observed on 12 October 2026 at the warehouse. Confirmed sequence: "
   f"<b>warehouse receiver receives the goods → receiver takes the receipt → receipt is passed to the assigned "
   f"worker → assigned worker checks quantity and expiry → only if all confirmed, the receiving is added to the "
   f"purchase order in StoreHub.</b> The physical check happens before any system posting, and posting is gated "
   f"on the worker's confirmation.")
ZH("现场观察 — 2026年10月12日在仓库现场观察。已确认顺序：<b>仓库收货人接收货物 → 收货人取得收据／送货单 → "
   "将单据交给该单指定的经办人 → 经办人核对数量并检查有效期 → 全部确认无误后，才在StoreHub中将收货登记到"
   "对应采购单。</b>实物核对先于系统入账，入账以经办人确认为前提。")
simple_table([
    [P("OBSERVED STEP / 已观察步骤", s_cellh), P("WHAT HAPPENS / 实际发生", s_cellh), P("STILL TO VERIFY / 仍待核实", s_cellh)],
    [P("<b>1. Receive delivery</b><br/>接收货物"),
     P("The warehouse receiver is the one who receives the goods from the supplier.<br/>由仓库收货人统一接收供应商货物。"),
     P("Record supplier, actual arrival time and receiver name for the evidence packet.<br/>为证据包记录供应商、实际到货时间及收货人姓名。")],
    [P("<b>2. Take the receipt</b><br/>取得收据"),
     P("The receiver gets the receipt / delivery document together with the goods.<br/>收货人随货取得收据／送货单。"),
     P("Identify the exact document type, keep its number, and match supplier, SKU, units and quantities.<br/>确认单据类型，保留单号，核对供应商、商品、单位及数量。")],
    [P("<b>3. Hand over the receipt</b><br/>转交单据"),
     P("The receiver passes the receipt to the assigned worker responsible for that delivery.<br/>收货人将单据交给负责该批货物的指定经办人。"),
     P("Record how the assigned worker is designated; keep both names per transaction.<br/>记录经办人的指派方式；每笔交易保留两人姓名。")],
    [P("<b>4. Check quantity and expiry</b><br/>核对数量与有效期"),
     P("The assigned worker physically checks the delivered quantity and the expiry dates against the receipt.<br/>经办人按单据实物核对数量并检查有效期。"),
     P("Observe how short / over / damaged / near-expiry goods are decided, where the result is recorded, and who approves exceptions.<br/>观察短少、超量、破损及临期商品如何判定、结果记录在哪里、异常由谁批准。")],
    [P("<b>5. Post to the PO in StoreHub</b><br/>在StoreHub登记采购单收货"),
     P("Only after everything is confirmed does the assigned worker add the receiving to the purchase order in StoreHub.<br/>全部确认无误后，经办人才在StoreHub中把收货登记到对应采购单。"),
     P("Capture PO reference, posting actor and posting time; measure the delay between physical check and posting; confirm who closes partial receipts.<br/>记录采购单号、入账人及入账时间；测量实物核对与入账之间的时差；确认部分收货的结单责任人。")],
], [34 * mm, 72 * mm, 72 * mm])
EN(f"{INF} — This observed order is stronger than the earlier description: StoreHub posting is the last step and "
   f"is conditional on confirmation. V2.1's receiving loop (physical count → compare → variance → StoreHub "
   f"confirmation → reconcile) matches reality; the capture point should sit at step 4, beside the assigned "
   f"worker, on mobile.")
ZH("推断 — 本次观察到的顺序比此前描述更明确：StoreHub入账是最后一步，且以确认为前提。V2.1的收货闭环"
   "（实点 → 比对 → 差异 → StoreHub确认 → 对账）与实际流程一致；数据采集点应设在第4步，由经办人用手机完成。")

# ---------------------------------------------------------------- section 2
H("2. Warehouse → store transfer / 仓库 → 门店调拨")
EN(f"{OBS} — Observed on 12 October 2026. Confirmed sequence: "
   f"<b>wait for a scheduled delivery to that destination → if one exists, create the stock transfer in StoreHub "
   f"first → pack the products against the transfer → load → the store receives and checks the quantity → the "
   f"store reports the result back to the assigned warehouse worker.</b> The StoreHub transfer is created before "
   f"any physical movement.")
ZH("现场观察 — 2026年10月12日现场观察。已确认顺序：<b>等待有发往该门店的配送／装车安排 → 若有，则先在"
   "StoreHub建立库存调拨单 → 按调拨单打包商品 → 装车 → 门店收货并核对数量 → 门店将验收结果反馈给仓库指定"
   "经办人。</b>StoreHub调拨单在任何实物移动之前建立。")
simple_table([
    [P("OBSERVED STEP / 已观察步骤", s_cellh), P("WHAT HAPPENS / 实际发生", s_cellh), P("STILL TO VERIFY / 仍待核实", s_cellh)],
    [P("<b>1. Wait for a scheduled delivery</b><br/>等待配送安排"),
     P("Transfers are batched: the warehouse waits until there is a delivery / loading going to that destination.<br/>调拨按配送批次处理：仓库等到有发往该门店的配送／装车安排才执行。"),
     P("Record how deliveries are scheduled and who decides; note the typical wait between request and loading.<br/>记录配送如何排期、由谁决定；记录申请到装车的典型等待时间。")],
    [P("<b>2. Create the stock transfer first</b><br/>先建立调拨单"),
     P("If a delivery exists, the stock transfer is created in StoreHub before packing starts.<br/>有配送安排后，先在StoreHub建立库存调拨单，再开始打包。"),
     P("Capture transfer reference, creator and creation time; verify the store-request source (informal chat vs system).<br/>记录调拨单号、创建人及创建时间；核实门店要货来源（聊天／电话或系统）。")],
    [P("<b>3. Pack</b><br/>打包"),
     P("Products are picked and packed against the created transfer.<br/>按已建立的调拨单拣货并打包。"),
     P("Observe scanning vs manual checks, shortages, substitutions and packer confirmation.<br/>观察扫码／人工核对、缺货、替代品及打包人确认方式。")],
    [P("<b>4. Load</b><br/>装车"),
     P("Packed goods are loaded for delivery to the store.<br/>打包好的商品装车发往门店。"),
     P("Record dispatch time, driver/handover and package count; compare physical departure with the transfer time.<br/>记录发车时间、司机／交接人及件数；比较实物离仓与调拨单时间。")],
    [P("<b>5. Store receives and counts</b><br/>门店收货并核对数量"),
     P("The store receives the goods and physically checks the quantity.<br/>门店收货并实物核对数量。"),
     P("Identify the receiver; check whether the count is compared against the transfer document; record short / over / damaged quantities.<br/>确认收货人；核实是否按调拨单比对；记录短少、超量及破损数量。")],
    [P("<b>6. Store reports back</b><br/>门店反馈结果"),
     P("The store reports the receiving result to the assigned worker in the warehouse.<br/>门店将验收结果反馈给仓库指定经办人。"),
     P("Record the channel (chat / phone / paper), who closes the transfer in StoreHub and when, and how variances are resolved.<br/>记录反馈渠道（聊天／电话／纸质）、谁在何时于StoreHub完成调拨，以及差异如何处理。")],
], [34 * mm, 72 * mm, 72 * mm])
EN(f"{INF} — Two design facts follow. First, the system record already leads the physical flow (transfer before "
   f"packing), so RetailOS can key its dispatch event to the created transfer reference. Second, the loop closes "
   f"person-to-person (store reports back to a worker), not system-to-system: V2.1's store-receiving confirmation "
   f"should digitize exactly this report-back, with the transfer reference attached.")
ZH("推断 — 由此得出两个设计结论。第一，系统记录已先于实物流动（先建单后打包），RetailOS可将发货事件直接"
   "关联到已建立的调拨单号。第二，闭环目前是人对人（门店反馈给经办人）而非系统对系统：V2.1的门店收货确认"
   "应正是把这一反馈环节数字化，并关联调拨单号。")
EN(f"{SYS} — The stock-transfer page imports StoreHub CSV records; importing does not create physical receiving "
   f"confirmations or update canonical stock. A CSV status alone cannot establish actual store receipt.")
ZH("系统确认 — 库存调拨页面导入StoreHub CSV记录；导入不会生成实物收货确认，也不会更新权威库存。仅凭CSV状态"
   "不能证明门店实际收货。")

# ---------------------------------------------------------------- section 3
H("3. Negative stock — warehouse cases / 负库存 — 仓库案例")
EN(f"{SYS} — Same two warehouse cases as the original 78-test report. Inventory sync: 09 Oct 2026, 14:29 "
   f"Philippine time. Both causes remain OPEN.")
ZH("系统确认 — 保留原78项测试报告的两个仓库案例。库存同步：2026年10月9日14:29，菲律宾时间。两项原因仍未查实。")
simple_table([
    [P("CASE / 案例", s_cellh), P("ORIGINAL → CURRENT / 原值 → 当前", s_cellh), P("LINKED RECORDS / 关联记录", s_cellh)],
    [P("N1 · Product 159<br/>Baygon Mosq Coil Scented 150g<br/>4801234134426"),
     P("−540 → −540<br/>Warehouse / 仓库"),
     P("PO90: 180 ordered / 180 received<br/>ST0029: Shipped, ordered qty 720")],
    [P("N2 · Product 158<br/>Baygon Coil Jumbo Lavander 10s<br/>4801234134495"),
     P("−420 → −420<br/>Warehouse / 仓库"),
     P("PO90: 180 ordered / 180 received<br/>ST0029: Shipped, ordered qty 600")],
], [62 * mm, 40 * mm, 76 * mm])
EN(f"{SYS} — ST0029 is dated 02 September and is marked Shipped from the warehouse to Bantayan. PO90 is dated "
   f"21 September and is marked completed. ST0048 is dated 26 September, marked Created, and lists 24 units for "
   f"N1 and 22 for N2 to Getafe.")
ZH("系统确认 — ST0029日期为9月2日，状态为Shipped，由仓库发往Bantayan。PO90日期为9月21日，状态为completed。"
   "ST0048日期为9月26日，状态为Created，向Getafe列出N1的24单位及N2的22单位。")
EN(f"{INF} — For N1, 180 − 720 = −540; for N2, 180 − 600 = −420. The arithmetic matches the balances, but is "
   f"not a verified stock ledger: PO90 is later than ST0029, earlier receipts/opening balances are unknown, and "
   f"exported “Ordered Qty” is not independently confirmed dispatched quantity.")
ZH("推断 — N1为180 − 720 = −540，N2为180 − 600 = −420。数值吻合，但并非经验证的库存账：PO90晚于ST0029，"
   "早期收货／期初余额未知，导出的“Ordered Qty”也未独立确认为实际发货数。")
EN(f"{INF} — Required case closure: count each SKU at the warehouse; verify pieces versus case packs; trace PO90 "
   f"delivery/receiving proof, ST0029 packing/dispatch and Bantayan receiving. Obtain the opening balance and "
   f"intervening receipts, sales, returns, write-offs and adjustments. Check ST0048's actual movement separately; "
   f"Created does not establish dispatch.")
ZH("推断 — 结案所需证据：在仓库分别清点两款商品；核实单件与箱规；追查PO90送货／收货证据、ST0029打包／发货及"
   "Bantayan验收。取得期初余额及期间收货、销售、退货、报损和调整。另查ST0048是否实际移动；Created状态不能"
   "证明已发货。")
EN(f"{SYS} — Neither selected warehouse case has a local RetailOS receiving line, stock event or completed "
   f"physical count in the reviewed data. This absence does not prove that no delivery or transfer occurred.")
ZH("系统确认 — 所审查数据中，这两个仓库案例均没有本地RetailOS收货明细、库存事件或已完成的实物盘点。缺少本地"
   "记录不能证明实际未发生收货或调拨。")

# ---------------------------------------------------------------- section 4
H("4. Negative stock — Bantayan cases / 负库存 — Bantayan门店案例")
EN(f"{SYS} — Same three store cases; all are at The Daily Centro - Bantayan, location 5. Inventory sync: 09 Oct "
   f"2026, 14:29. Root causes remain OPEN.")
ZH("系统确认 — 保留原来的三个门店案例，均属于The Daily Centro - Bantayan（地点5）。库存同步：10月9日14:29。"
   "根因仍未查实。")
simple_table([
    [P("CASE / 案例", s_cellh), P("ORIGINAL → CURRENT / 原值 → 当前", s_cellh), P("AVAILABLE SALES / 已有销售记录", s_cellh)],
    [P("N3 · Product 640<br/>Hotdog Roll · 100110"), P("−128 → −128"), P("98 lines / 133 units<br/>21 Sep–07 Oct")],
    [P("N4 · Product 1003<br/>Peach Mango Pie · 100112"), P("−99 → −100"), P("77 lines / 105 units<br/>21 Sep–09 Oct")],
    [P("N5 · Product 1353<br/>Tuna Pie · 100111"), P("−62 → −64"), P("42 lines / 69 units<br/>21 Sep–09 Oct")],
], [62 * mm, 40 * mm, 76 * mm])
EN(f"{SYS} — N3 last recorded a net change of −4 to −128 on 08 October at 07:40. N4 changed from −99 to −100 "
   f"on 09 October at 12:15. N5 changed from −62 to −63 at 11:25 and then to −64 at 14:09 on 09 October. Net "
   f"snapshot changes do not identify an individual transaction cause.")
ZH("系统确认 — N3最近于10月8日07:40净减4，变为−128。N4于10月9日12:15从−99变为−100。N5于10月9日11:25从"
   "−62变为−63，14:09再变为−64。快照净变化不能确定具体交易原因。")
EN(f"{SYS} — Each case has an open local cycle-count item with no entered count. No matching PO history, "
   f"imported transfer line, local receiving line or stock event was found in the reviewed data. Available "
   f"inventory snapshots start on 07 October; earlier movements are not reconstructed.")
ZH("系统确认 — 三项均存在尚未填写实点数的开放盘点项目。所审查数据未找到对应采购单历史、导入调拨明细、本地收货"
   "明细或库存事件。现有库存快照始于10月7日，尚未重建此前的库存移动。")
EN(f"{INF} — N3: count Hotdog Roll and verify its receiving, preparation or opening-balance recording method. "
   f"N4: count Peach Mango Pie and trace its new −1 change to source records. N5: count Tuna Pie and trace both "
   f"new −1 changes. For all three, verify barcode mapping, unit conversions, receipts, sales/returns, waste and "
   f"adjustment history.")
ZH("推断 — N3：清点Hotdog Roll，核实其收货、备制或期初库存的记录方式。N4：清点Peach Mango Pie，追溯新增−1"
   "对应的原始记录。N5：清点Tuna Pie，追溯两次新增−1。三项均须核实条码映射、单位换算、收货、销售／退货、损耗"
   "及调整历史。")
EN(f"{INF} — Missing or mismatched incoming records, opening balances or item units are possible leads, not "
   f"established causes. A negative system balance does not prove a physical stockout or quantify a verified loss.")
ZH("推断 — 入库记录缺失或不匹配、期初库存及商品单位问题均为可能线索，并非已确定原因。系统负库存不能证明实物"
   "缺货，也不能据此确定实际损失。")

# ---------------------------------------------------------------- section 5
H("5. Five stale purchase orders / 五张长期未结采购单")
EN(f"{SYS} — Original sample retained: PO36, PO101, PO112, PO114, PO115. All belong to the warehouse; refreshed "
   f"09 Oct 2026 at 14:30. All dispositions remain OPEN.")
ZH("系统确认 — 保留原样本：PO36、PO101、PO112、PO114、PO115。均属仓库，10月9日14:30更新。五张单的处理结论"
   "均未完成。")
simple_table([
    [P("PO / 采购单 · SUPPLIER / 供应商", s_cellh), P("STATUS · REFERENCE · AGE / 状态 · 参考日期 · 天数", s_cellh),
     P("LINES · ORDERED / RECEIVED / 明细数 · 订货／已收", s_cellh)],
    [P("PO36<br/>River Valley Distribution, Inc."), P("Open / 未结<br/>2026-08-15 · 55 days"), P("62 lines<br/>2416 / 0")],
    [P("PO101<br/>H-MART PHILIPPINES COMMERCIAL INC."), P("Partial / 部分收货<br/>2026-09-24 · 15 days"), P("35 lines<br/>743 / 743")],
    [P("PO112<br/>Akroasia Trading Corporation"), P("Open / 未结<br/>2026-09-25 · 14 days"), P("8 lines<br/>816 / 0")],
    [P("PO114<br/>Basic Essential Marketing Inc."), P("Open / 未结<br/>2026-09-25 · 14 days"), P("17 lines<br/>318 / 0")],
    [P("PO115<br/>Mixedload Distribution Network Inc."), P("Open / 未结<br/>2026-09-25 · 14 days"), P("5 lines<br/>888 / 0")],
], [66 * mm, 56 * mm, 56 * mm])
EN(f"{SYS} — PO101 is partiallyReceived, but all 35 lines are fully received (743/743). This is a mirrored "
   f"header/line discrepancy; physical acceptance remains unverified. The other four show zero received. All five "
   f"POs have no local RetailOS receiving record.")
ZH("系统确认 — PO101为partiallyReceived，但35条明细全部已收足（743／743）。这是镜像数据的表头／明细差异，实物"
   "验收尚未核实。其余四张单已收数均为零；五张单均无本地RetailOS收货记录。")
EN(f"{INF} — PO101: a closing/status issue is a lead; check StoreHub receiving logs, invoice/receipt, status "
   f"semantics, rejected goods and warehouse confirmation before closure. For the other four: establish each "
   f"delivery/cancellation outcome using original documents and named purchasing/warehouse confirmation; record a "
   f"disposition and owner per PO.")
ZH("推断 — PO101：结单／状态问题是一个线索；结单前核查StoreHub收货日志、发票／收据、状态含义、拒收商品及仓库"
   "确认。其余四张：用原始单据及具名采购／仓库人员确认，逐单查清交货／取消结果，记录结论及责任人。")
EN(f"{SYS} — Open/partial POs over 7 days (expected date, otherwise creation date) are excluded from calculated "
   f"incoming, not automatically cancelled. Reference dates are not verified supplier promises; zero recorded "
   f"receipt does not prove zero physical delivery.")
ZH("系统确认 — 超过7天的未结／部分收货单（按预计日期，缺失时按创建日期）排除在计算在途量之外，不会自动取消。"
   "参考日期并非已核实的供应商承诺；已收记录为零不代表实际交货为零。")

# ---------------------------------------------------------------- section 6
H("6. Why 112 tests versus 78? / 为什么112项测试变为78项？")
simple_table([
    [P("REVIEW / 审查", s_cellh), P("EVIDENCE AND SCOPE / 证据与范围", s_cellh)],
    [P("<b>112 tests</b><br/>Earlier technical review"),
     P(f"{SYS} — The technical review states that the full suite had 112 tests, all passing at the last run. This "
       f"is a historical report statement, not a new run.<br/>系统确认 — 技术审查记载：完整测试集112项，上次运行全部"
       "通过。这是历史报告记载，并非本次重新运行。")],
    [P("<b>78 tests</b><br/>Warehouse review"),
     P(f"{SYS} — The warehouse evidence records 78 passed on 09 October 2026 from four selected test files "
       f"(service, stock-event, sync and web checks):<br/><font face='ZH'>"
       ".venv\\Scripts\\python -m pytest tests/test_services.py tests/test_stock_events.py tests/test_sync.py "
       "tests/test_web.py -q</font><br/>系统确认 — 仓库证据记录：2026年10月9日，四个指定测试文件的78项测试通过，"
       "覆盖服务、库存事件、同步及网页。")],
], [42 * mm, 136 * mm])
EN(f"{INF} — The counts reflect different test scopes. The difference of 34 is not evidence that 34 tests failed "
   f"or were removed. The historical 112-test command, test manifest and exact code revision were not recovered, "
   f"so the specific 34 tests cannot be identified from the available reports.")
ZH("推断 — 数量差异反映测试范围不同。相差34项并不能证明34项失败或被删除。尚未取得历史112项测试的命令、测试"
   "清单及准确代码版本，因此无法仅凭现有报告确定具体是哪34项。")
EN(f"{SYS} — The 78-test validation used disposable databases and a fake StoreHub client. Passing it does not "
   f"establish physical receiving, store acceptance or final V2.1 acceptance. For reference: the current live "
   f"suite is larger than both (226 tests, all passing on 10 October 2026); final freeze evidence should record "
   f"the then-current full-suite command, code revision and pass/fail totals.")
ZH("系统确认 — 78项验证使用临时数据库及模拟StoreHub客户端，测试通过并不能证明实物收货、门店验收或V2.1已通过"
   "最终验收。供参考：当前线上测试集已超过两者（226项，2026年10月10日全部通过）；最终冻结证据应记录当时的完整"
   "测试命令、代码版本及通过／失败数量。")

# ---------------------------------------------------------------- section 7
H("7. Completion and final freeze approval / 完成验证及最终冻结审批")
EN(f"{INF} — Retain 2.1-FC1. Both warehouse flows are now observed; the ten data cases remain open. A proposed "
   f"FINAL V2.1 FROZEN version should be sent for management approval only after the remaining evidence and case "
   f"dispositions are complete. This report is the validation update, not the final frozen release.")
ZH("推断 — 保留2.1-FC1。两条仓库流程已完成现场观察；10个数据案例仍未结案。只有在剩余证据及案例处理结论完成后"
   "，才应提交拟定的FINAL V2.1 FROZEN版本供管理层审批。本报告是验证进展更新，并非正式冻结版本。")
simple_table([
    [P("OPEN ITEM / 事项", s_cellh), P("STATUS / 状态", s_cellh), P("REMAINING COMPLETION EVIDENCE / 剩余完成证据", s_cellh)],
    [P("One supplier receiving flow<br/>一次供应商收货"),
     P(f"<b>{OBS}</b><br/>12 Oct 2026"),
     P("Attach the transaction-level packet: observer/date, supplier and PO number, receipt reference, document "
       "vs counted quantities, expiry evidence, variance example, posting actor/time and StoreHub status.<br/>"
       "补齐交易级证据包：观察人及日期、供应商及采购单号、收据单号、单据与实点数量、有效期证据、差异实例、"
       "入账人／入账时间及StoreHub结果状态。")],
    [P("One warehouse → store flow<br/>一次仓库到门店调拨"),
     P(f"<b>{OBS}</b><br/>12 Oct 2026"),
     P("Tie one transfer reference end-to-end: creation, packing, dispatch, delivery and the store's independent "
       "count; capture the store's report-back channel, one variance example and the closing actor/time.<br/>"
       "用同一调拨单号贯通全流程：建单、打包、发货、配送及门店独立清点；记录门店反馈渠道、一个差异实例及"
       "完成人／完成时间。")],
    [P("N1–N5 negative-stock cases<br/>N1–N5负库存案例"),
     P(f"<b>OPEN</b>"),
     P("Each: physical count, traceable ledger/documents, supported cause, named owner and recorded resolution "
       "or accepted exception.<br/>逐项：实点数、可追溯账目／单据、有证据的原因、具名责任人、处理结果或获批准的例外。")],
    [P("PO36 / 101 / 112 / 114 / 115"),
     P(f"<b>OPEN</b>"),
     P("Each: delivery disposition, original proof, staff confirmation, close/receive/cancel decision and "
       "responsible owner.<br/>逐单：交货结论、原始凭证、人员确认、结单／收货／取消决定及责任人。")],
], [40 * mm, 30 * mm, 108 * mm])
EN(f"{SYS} — The baseline retains six validation gates: supplier receiving; picking/dispatch; store receipt; "
   f"damage/write-off; stock adjustment; and putaway/bin impact. Each needs an evidence-labelled conclusion "
   f"before freeze, alongside architecture acceptance, agreed MUST tests/metrics and no unresolved operational "
   f"contradiction.")
ZH("系统确认 — 原基线保留六项验证关卡：供应商收货、拣货／发货、门店收货、破损／报损、库存调整及上架／库位影响。"
   "冻结前，每项须有明确证据标识的结论，并满足架构认可、MUST测试／指标达成且没有未解决的运营矛盾。")
EN(f"{INF} — The first three gates are now partially covered by the two observed flows (receiving, "
   f"picking/dispatch, store receipt); collect the remaining gate evidence (damage/write-off, stock adjustment, "
   f"putaway/bin) and the ten case dispositions. Do not start additional feature or benchmark work. If a gate "
   f"remains unsupported, retain FC1 and state the exact missing evidence.")
ZH("推断 — 前三个关卡已由两条观察到的流程部分覆盖（收货、拣货／发货、门店收货）；继续收集其余关卡证据"
   "（破损／报损、库存调整、上架／库位）及10个案例的处理结论，不新增功能或竞品研究。若某项仍缺证据，继续保留"
   "FC1，并明确缺少什么。")
story.append(Spacer(1, 4))
rule()
EN("<b>Evidence and source trail.</b> Observed on-site: warehouse receiving and warehouse→store sequences, 12 Oct "
   "2026 (Sections 1–2). Staff confirmed: user-supplied management direction. System confirmed: original management "
   "report and SYSTEM_EVIDENCE.json; read-only snapshot at 14:30, 09 Oct 2026; test sources in Section 6. Baselines: "
   "RETAIL_OS_V2_1_FREEZE_CANDIDATE.md and WAREHOUSE_ONSITE_VALIDATION_CHECKLIST.md. Original baselines and "
   "operational records were not changed for this report.")
ZH("<b>证据与来源。</b>现场观察：2026年10月12日仓库收货及仓库→门店顺序（第1–2节）。人员确认：用户转述的管理层"
   "指示。系统确认：原管理报告及SYSTEM_EVIDENCE.json；2026年10月9日14:30只读快照；第6节测试来源。基线："
   "RETAIL_OS_V2_1_FREEZE_CANDIDATE.md及WAREHOUSE_ONSITE_VALIDATION_CHECKLIST.md。本报告未修改原基线或"
   "运营记录。")


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("ZH", 7.5)
    canvas.setFillColor(MUTE)
    canvas.drawString(16 * mm, 10 * mm, "2.1-FC1 | Field validation update v2 / 现场验证更新 v2")
    canvas.drawRightString(194 * mm, 10 * mm, f"{doc.page}")
    canvas.restoreState()


out = r"C:\Users\User\Downloads\Retail_OS_V2_1_FC1_Field_Validation_Update_EN_ZH_v2.pdf"
doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                        topMargin=14 * mm, bottomMargin=16 * mm, title="Retail OS V2.1 FC1 Field Validation Update v2")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print("written:", out)

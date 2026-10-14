# -*- coding: utf-8 -*-
"""Builds Retail_OS_V2_1_FINAL_FROZEN_Proposal_EN_ZH.pdf — bilingual (EN/ZH) FINAL V2.1
FROZEN proposal for management approval, 12 October 2026.

Contents per management instruction:
  1. Transaction-level evidence packets (PO90 receiving; ST0023 warehouse->store)
  2. Case dispositions: N1-N5 negative stock; PO36/101/112/114/115 stale POs
  3. Six validation gates (incl. damage/write-off, stock adjustment, putaway/bin)
  4. Final validation/closure table
  5. Full test command and pass/fail result
  6. Exact code revision (git tag v2.1-spec-frozen + SHA-256 manifest)

Evidence labels: Observed on-site / Staff confirmed / System confirmed / Inferred.
Items that cannot be verified are kept OPEN with the exact missing evidence stated.

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
BAD = colors.HexColor("#b91c1c")

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
story.append(Paragraph("FINAL FROZEN — proposal for management approval", s_h2))
story.append(Paragraph("最终冻结版 — 提交管理层审批的提案", s_sub))
story.append(Paragraph("Management edition | English / Chinese | 14 October 2026", s_sub))
story.append(Paragraph("管理层审阅版 | 英文／中文 | 2026年10月14日", s_sub))
story.append(Spacer(1, 6))
simple_table([
    [P("VERSION / 版本", s_cellh), P("PROPOSED POSITION / 提案状态", s_cellh)],
    [P("<b>V2.1 FROZEN</b><br/>(proposed / 拟议)"),
     P("All validation work that a system can evidence is complete and attached. What remains open requires "
       "physical counts or named-person confirmation, listed exactly in Section 5. No new features and no "
       "benchmark research were added in this phase.<br/>"
       "系统可提供证据的验证工作已全部完成并附于本文件。其余未结事项需要实物盘点或具名人员确认，已在第5节逐项"
       "列明。本阶段未新增功能，未恢复竞品研究。")],
], [45 * mm, 133 * mm], header_bg=ACCENT)
story.append(Spacer(1, 6))

EN(f"{SYS} — Data snapshot: StoreHub mirror synchronised 12 October 2026 at 06:21 Philippine time. Evidence "
   f"labels follow the FC1 convention: {OBS}, {STAFF}, {SYS}, {INF}.")
ZH("系统确认 — 数据快照：StoreHub镜像同步至2026年10月12日06:21（菲律宾时间）。证据标识沿用FC1约定："
   "现场观察／人员确认／系统确认／推断。")
EN("The two warehouse flows were observed on-site on 12 October 2026 and are unchanged from the FC1 v2 report: "
   "supplier receiving ends with StoreHub posting only after the assigned worker confirms quantity and expiry; "
   "the warehouse→store transfer is created in StoreHub before packing, and the store reports its count back to "
   "the assigned warehouse worker.")
ZH("两条仓库流程已于2026年10月12日现场观察，与FC1 v2报告一致：供应商收货须由指定经办人确认数量及有效期后"
   "方在StoreHub入账；仓库→门店调拨先于打包在StoreHub建单，门店清点结果反馈给仓库指定经办人。")

# ---------------------------------------------------------------- section 1
H("1. Transaction-level evidence packets / 交易级证据包")

H2("1.1 Supplier receiving — PO90 / 供应商收货 — PO90")
simple_table([
    [P("FIELD / 字段", s_cellh), P("VALUE / 内容", s_cellh), P("EVIDENCE / 证据", s_cellh)],
    [P("PO reference / 采购单号"), P("<b>PO90</b> · LCG Marketing Philippines Corporation · destination: warehouse"), P(f"{SYS}")],
    [P("Lines / quantities / 明细与数量"),
     P("2 lines, 360 units, fully received (360/360):<br/>"
       "· Baygon Mosq Coil Scented 150g (4801234134426) — 180 ordered / 180 received<br/>"
       "· Baygon Coil Jumbo Lavander 10s (4801234134495) — 180 ordered / 180 received"), P(f"{SYS}")],
    [P("Amounts / 金额"), P("Subtotal ₱6,492.86 · tax ₱779.14 · total ₱7,272.00"), P(f"{SYS}")],
    [P("Timestamps / 时间"),
     P("Created 2026-09-21 07:42 · completed 2026-09-21 07:46 (4 minutes)"), P(f"{SYS}")],
    [P("Posting actor / 入账人"),
     P("StoreHub completed_by resolved to employee: <b>Queeny Adolfo</b>"), P(f"{SYS}")],
    [P("Physical steps / 实物步骤"),
     P("Receiver takes goods and receipt → receipt passed to assigned worker → worker checks quantity and expiry "
       "→ only then posted to the PO"), P(f"{OBS} · 12 Oct 2026")],
    [P("Still missing / 仍缺证据"),
     P("Delivery receipt number; counted-vs-document record; expiry dates (no expiry record exists anywhere in "
       "either system); posting delay between physical check and posting. The 4-minute create→complete window "
       "suggests posting was batched, not transaction-time."), P(f"{INF}")],
], [34 * mm, 102 * mm, 42 * mm])

H2("1.2 Warehouse → store transfer — ST0023 / 仓库→门店调拨 — ST0023")
simple_table([
    [P("FIELD / 字段", s_cellh), P("VALUE / 内容", s_cellh), P("EVIDENCE / 证据", s_cellh)],
    [P("Reference / 单号"),
     P("<b>ST0023</b> · TheDailyCentro Warehouse → The Daily Centro - Bantayan"), P(f"{SYS}")],
    [P("Lines / quantities / 明细与数量"), P("146 lines · 4,492 units · total ₱126,139.67 PHP"), P(f"{SYS}")],
    [P("Timestamps / 时间"),
     P("Created 2026-08-26 07:34 · Shipped 2026-08-27 06:42 · Received 2026-08-29 05:04 "
       "(46.4 hours ship→receive — the only completed transfer with a realistic multi-day gap)"), P(f"{SYS}")],
    [P("Actors / 经办人"),
     P("Sent by: <b>Adolfo Queeny</b> · Received by: <b>Adolfo Queeny</b> — the same warehouse account closes "
       "both ends"), P(f"{SYS}")],
    [P("Physical steps / 实物步骤"),
     P("Wait for scheduled delivery → transfer created in StoreHub first → pack → load → store counts → store "
       "reports back to assigned worker"), P(f"{OBS} · 12 Oct 2026")],
    [P("Still missing / 仍缺证据"),
     P("The store's independent count, any variance record, the report-back channel used, and the package count. "
       "None of these exists in any system."), P(f"{INF}")],
], [34 * mm, 102 * mm, 42 * mm])
EN(f"{SYS} — Cross-check across all 67 imported transfers: every one of the 17 completed transfers was both sent "
   f"and received by the same account (Adolfo Queeny), and 15 of the 17 were marked shipped and received within "
   f"minutes of each other, in a batch between 00:34 and 01:12 on 23 August. Store receiving is not independently "
   f"recorded anywhere; the warehouse closes both ends.")
ZH("系统确认 — 对全部67张已导入调拨单的交叉核查：17张已完成调拨全部由同一账户（Adolfo Queeny）发货并收货，"
   "其中15张的发货与收货时间相差仅数分钟，且集中在8月23日00:34至01:12之间批量完成。门店收货没有任何独立"
   "的系统记录；两端均由仓库关闭。")

# ---------------------------------------------------------------- section 2
H("2. Negative-stock case dispositions (N1–N5) / 负库存案例处理结论（N1–N5）")
simple_table([
    [P("CASE / 案例", s_cellh), P("BALANCE / 余额<br/>(09 Oct → 12 Oct)", s_cellh), P("DISPOSITION / 结论", s_cellh)],
    [P("N1 · Baygon Mosq Coil Scented 150g<br/>warehouse / 仓库"), P("−540 → −540"),
     P("<b>Supported likely cause / 有证据的可能原因</b>")],
    [P("N2 · Baygon Coil Jumbo Lavander 10s<br/>warehouse / 仓库"), P("−420 → −420"),
     P("<b>Supported likely cause / 有证据的可能原因</b>")],
    [P("N3 · Hotdog Roll · Bantayan"), P("−128 → <b>−143</b>"), P("<b>Supported likely cause / 有证据的可能原因</b>")],
    [P("N4 · Peach Mango Pie · Bantayan"), P("−100 → <b>−111</b>"), P("<b>Supported likely cause / 有证据的可能原因</b>")],
    [P("N5 · Tuna Pie · Bantayan"), P("−64 → <b>−69</b>"), P("<b>Supported likely cause / 有证据的可能原因</b>")],
], [62 * mm, 40 * mm, 76 * mm])
EN(f"{SYS} — N1/N2: the only inbound ever recorded for either item is PO90 (180 units each, 21 Sep). The only "
   f"outbound ever recorded is ST0029 (720 and 600 units, shipped 03 Sep, sent by Adolfo Queeny). The balances "
   f"match the arithmetic exactly (180 − 720 = −540; 180 − 600 = −420), have not moved since change-tracking "
   f"began on 07 October, and the warehouse sells neither item. ST0029 itself is still 'Shipped' — 40 days later "
   f"Bantayan has never marked it received.")
ZH("系统确认 — N1/N2：两款商品有记录的唯一入库为PO90（各180件，9月21日），唯一出库为ST0029（720件及600件，"
   "9月3日发货，发货人Adolfo Queeny）。余额与算式完全吻合（180−720=−540；180−600=−420），自10月7日库存变动"
   "追踪启动以来未再变化，且仓库不销售这两款商品。ST0029至今仍为“Shipped”——40天后Bantayan从未确认收货。")
EN(f"{INF} — Supported cause: the warehouse's opening stock was never loaded into StoreHub, so the first recorded "
   f"transfer out drove the balance negative and later receipts only partially offset it. To confirm: physical "
   f"count of both SKUs at the warehouse, the opening-balance documents from go-live, and dispatch/receiving proof "
   f"for ST0029. Until those exist, both cases stay OPEN with this cause recorded.")
ZH("推断 — 有证据支持的原因：仓库期初库存从未录入StoreHub，首次有记录的出库调拨即将余额转为负数，其后收货仅"
   "部分抵减。确认所需：仓库实盘两款商品、上线时的期初库存凭证、ST0029的发货与验收证明。在上述证据补齐前，"
   "两案保持未结，但原因已按此记录。")
EN(f"{SYS} — N3/N4/N5 (Bantayan fresh items): zero inbound records of any kind exist for these three items — no "
   f"PO line, no transfer line, no stock event, no count — while sales post daily (Hotdog Roll 148 units, Peach "
   f"Mango Pie 116, Tuna Pie 74 since 21 Sep). Daily snapshots 07–12 Oct fall in lockstep with sales "
   f"(−123→−143, −94→−111, −60→−69). Their barcodes are sequential in-house codes (100110/100111/100112).")
ZH("系统确认 — N3/N4/N5（Bantayan鲜食）：三项商品不存在任何入库记录——无采购明细、无调拨明细、无库存事件、"
   "无盘点——而销售逐日发生（自9月21日起Hotdog Roll 148件、Peach Mango Pie 116件、Tuna Pie 74件）。10月7日至"
   "12日的每日快照与销售同步下降（−123→−143、−94→−111、−60→−69）。三者条码为店内自编连号"
   "（100110／100111／100112）。")
EN(f"{INF} — Supported cause: these are store-prepared items whose production (or inbound delivery) is never "
   f"recorded, so every sale drives the balance further negative. To confirm: physical count at Bantayan and staff "
   f"confirmation of how each item is produced/received. Until then: OPEN, cause recorded. Operational note: "
   f"these three balances worsened during this validation week — the fix (record production) cannot wait for V2.1.")
ZH("推断 — 有证据支持的原因：三项均为店内自制商品，其制作（或到货）从未入账，因此每笔销售都使余额继续下降。"
   "确认所需：Bantayan实盘，以及店员对每款商品制作／收货方式的确认。在此之前：保持未结，原因已记录。运营提示："
   "本周验证期间三项余额继续恶化——“录入制作量”这一纠正措施不应等到V2.1。")

# ---------------------------------------------------------------- section 3
H("3. Stale purchase-order dispositions / 长期未结采购单处理结论")
simple_table([
    [P("PO · SUPPLIER / 供应商", s_cellh), P("STATE (12 Oct) / 状态", s_cellh), P("DISPOSITION · OWNER / 结论 · 责任人", s_cellh)],
    [P("PO36<br/>River Valley Distribution"), P("open · 2416 / 0<br/>58 days / 天"),
     P("<b>Supported: superseded duplicate → cancel.</b> PO74 (15 Sep, completed 2420/2420) re-creates PO36 "
       "line-for-line: 62/62 identical products, 61/62 identical quantities. Cancel after the assigned warehouse "
       "owner confirms no second delivery is owed.<br/>有证据支持：已被PO74取代的重复单→取消。Owner: warehouse admin "
       "(assigned warehouse owner) / 仓库管理员（指定仓库负责人）")],
    [P("PO101<br/>H-MART PHILIPPINES"), P("partiallyReceived · 743 / 743<br/>header not closed / 表头未结"),
     P("<b>Supported: delivered in full → close.</b> All 35 lines fully received (743/743); only the header status "
       "is stuck. Close in StoreHub after the warehouse confirms there were no rejects. Same-day PO102/PO103 "
       "closed normally.<br/>有证据支持：已足额交货→结单。Owner: warehouse admin (assigned warehouse owner) / 仓库管理员（指定仓库负责人）")],
    [P("PO112<br/>Akroasia Trading"), P("open · 816 / 0<br/>17 days / 天"),
     P("<b>Supported: partial duplicate of PO111 → cancel or verify.</b> Same supplier, same morning (28 min "
       "apart): PO111 completed with the 6 shared lines at exactly double PO112's quantities; PO112 adds 2 yoghurt "
       "lines not in PO111.<br/>有证据支持：与PO111部分重复→取消或核实。Owner: warehouse admin (assigned warehouse owner) / 仓库管理员（指定仓库负责人）")],
    [P("PO114<br/>Basic Essential Marketing"), P("open · 318 / 0<br/>17 days / 天"),
     P("<b>OPEN — unresolved.</b> No duplicate pattern. Missing: supplier invoice/delivery receipt and a named "
       "warehouse confirmation of non-delivery.<br/>未结。缺少：供应商发票／送货单及具名仓库未交货确认。")],
    [P("PO115<br/>Mixedload Distribution"), P("open · 888 / 0<br/>17 days / 天"),
     P("<b>OPEN — unresolved.</b> Supplier's pattern is large periodic orders (PO24/PO83, 1236 each, completed); "
       "PO115's 888 fits no pattern. Missing: supplier invoice/delivery receipt and warehouse confirmation.<br/>"
       "未结。缺少：供应商发票／送货单及仓库确认。")],
], [40 * mm, 40 * mm, 98 * mm])
EN(f"{INF} — Owner names must be assigned by management; the system mirror does not record who created these POs. "
   f"Cancelling or closing in StoreHub is a human action there; the next sync will verify each outcome here.")
ZH("推断 — 责任人姓名须由管理层指派；系统镜像不记录这些采购单的创建人。在StoreHub中取消或结单须由人工在该系统"
   "操作，下一次同步将在本系统核实结果。")

# ---------------------------------------------------------------- section 4
H("4. Six validation gates / 六项验证关卡")
simple_table([
    [P("GATE / 关卡", s_cellh), P("CONCLUSION / 结论", s_cellh), P("BASIS / 依据", s_cellh)],
    [P("1. Supplier receiving<br/>供应商收货"), P("<b>PASS with gap / 通过但有缺口</b>"),
     P(f"{OBS} posting is gated on worker confirmation (12 Oct). {SYS} PO90 packet attached. Gap: the physical "
       f"check itself (count, expiry, variance) is recorded nowhere — expiry capture is empty system-wide.")],
    [P("2. Picking / dispatch<br/>拣货／发货"), P("<b>PASS with gap / 通过但有缺口</b>"),
     P(f"{OBS} transfer created before packing (12 Oct). {SYS} dispatch is keyable to the transfer reference. "
       f"Gap: no packing verification record exists.")],
    [P("3. Store receipt<br/>门店收货"), P("<b>GAP — design target / 缺口 — 设计目标</b>"),
     P(f"{SYS} 17/17 completed transfers closed by the same warehouse account; 15/17 within minutes in one "
       f"night batch. ST0029 never marked received in 40 days. The store's independent count does not exist in "
       f"any system. This is exactly the loop V2.1 digitises.")],
    [P("4. Damage / write-off<br/>破损／报损"), P("<b>GAP — adoption required / 缺口 — 须启用</b>"),
     P(f"{SYS} zero damage/write-off events have ever been recorded in RetailOS, and StoreHub exposes no "
       f"write-off channel. The recording feature exists but is unused; V2.1 makes it mandatory.")],
    [P("5. Stock adjustment<br/>库存调整"), P("<b>GAP — adoption required / 缺口 — 须启用</b>"),
     P(f"{SYS} zero cycle counts have ever been entered; the count → adjustment → StoreHub stock-take loop has "
       f"never been exercised. Designed and tested, never used.")],
    [P("6. Putaway / bin<br/>上架／库位"), P("<b>CLOSED — excluded from V2.1 / 关闭 — 不纳入V2.1</b>"),
     P(f"{SYS} Neither StoreHub (API/CSV fields confirmed) nor RetailOS has any bin/zone concept; inventory is "
       f"location + product only. At one warehouse and four stores, bin tracking is not justified. Revisit only "
       f"at multi-warehouse scale.")],
], [36 * mm, 42 * mm, 100 * mm])
EN(f"{INF} — Gates 1–2 pass because observed reality matches the V2.1 loop; gates 3–5 are adoption gaps inside "
   f"the already-approved V2.1 scope (not new features); gate 6 is a scope decision. No gate contradicts the "
   f"V2.1 design, and no gate requires new research.")
ZH("推断 — 关卡1–2通过，因为现场观察到的实际流程与V2.1闭环一致；关卡3–5属于已批准的V2.1范围内的启用缺口"
   "（并非新功能）；关卡6是范围决策。没有任何关卡与V2.1设计相矛盾，也无需新增研究。")

# ---------------------------------------------------------------- section 5
H("5. Final validation / closure table / 最终验证结案表")
simple_table([
    [P("ITEM / 事项", s_cellh), P("STATUS / 状态", s_cellh), P("DISPOSITION OR EXACT MISSING EVIDENCE / 结论或确切缺失证据", s_cellh)],
    [P("Supplier receiving flow<br/>供应商收货流程"),
     P("<b>CLOSED (packet)</b>"),
     P(f"{OBS} sequence + {SYS} PO90 packet. Physical-side documents (receipt no., counted-vs-doc, expiry) "
       f"recorded as V2.1 adoption requirements, not freeze blockers.")],
    [P("Warehouse → store flow<br/>仓库→门店流程"),
     P("<b>CLOSED (packet)</b>"),
     P(f"{OBS} sequence + {SYS} ST0023 packet. Store's independent count is absent system-wide — this is the "
       f"V2.1 store-receipt feature, already in scope.")],
    [P("N1 / N2 warehouse negative stock"),
     P("<b>OPEN — cause recorded</b>"),
     P("Supported cause: opening balances never loaded. Missing: physical count, go-live opening-balance "
       "documents, ST0029 dispatch and Bantayan receiving proof.<br/>缺失：实盘、上线期初凭证、ST0029发货及验收证明。")],
    [P("N3 / N4 / N5 Bantayan negative stock"),
     P("<b>OPEN — cause recorded</b>"),
     P("Supported cause: store-prepared items, production never recorded. Missing: Bantayan physical count and "
       "staff confirmation of production method.<br/>缺失：Bantayan实盘及店员对制作方式的确认。")],
    [P("PO36 / PO101 / PO112"),
     P("<b>CLOSED — decision ready</b>"),
     P("Supported dispositions: cancel PO36 (superseded by PO74), close PO101 (fully delivered), cancel/verify "
       "PO112 (partial duplicate of PO111). Human action in StoreHub + management owner assignment remain.<br/>"
       "结论已具备，待人工在StoreHub执行并由管理层指派责任人。")],
    [P("PO114 / PO115"),
     P("<b>OPEN — unresolved</b>"),
     P("Accepted unresolved exceptions. Missing: supplier invoice/delivery receipt and named warehouse "
       "confirmation of non-delivery for each.<br/>接受为未决例外。缺失：供应商发票／送货单及具名仓库确认。")],
    [P("Gates 4–5 (write-off, adjustment)<br/>关卡4–5"),
     P("<b>CLOSED as adoption items</b>"),
     P("Features exist and are tested; never used. V2.1 makes recording mandatory. No system evidence of past "
       "write-offs exists to validate against.<br/>功能已具备并通过测试但从未使用；V2.1将录入设为强制。")],
    [P("Gate 6 (putaway / bin)<br/>关卡6"),
     P("<b>CLOSED — excluded</b>"),
     P("No bin concept in either system; not justified at current scale.<br/>两套系统均无库位概念；当前规模不需要。")],
], [40 * mm, 34 * mm, 104 * mm])

# ---------------------------------------------------------------- section 6
H("6. Test command and result / 测试命令与结果")
simple_table([
    [P("FIELD / 字段", s_cellh), P("VALUE / 内容", s_cellh)],
    [P("Command / 命令"), P("<font face='ZH'>.venv\\Scripts\\python -m pytest tests -q</font>")],
    [P("Result / 结果"), P("<b>279 passed, 0 failed</b> in 91 s · run 14 October 2026, on the tagged commit")],
    [P("Scope / 范围"),
     P("Full suite: formulas, sync, idempotency, permissions, workflows, AI grounding and policy gate, "
       "migrations, order documents, stock-transfer import, i18n, web routes. Disposable databases; fake "
       "StoreHub client; no real data touched.<br/>完整测试集：公式、同步、幂等、权限、工作流、AI依据及策略门、"
       "数据迁移、单据、调拨导入、多语言、网页路由。使用临时数据库与模拟StoreHub客户端，不触碰真实数据。")],
    [P("Note on 112 vs 78 / 关于112与78"),
     P("112 was the full-suite count in the earlier technical review; 78 was a four-file subset run for the "
       "warehouse review. The current full suite is 279; all earlier counts are superseded by this run.<br/>"
       "112为此前技术审查的全量数；78为仓库审查的四个文件子集。本次279项为当前全量，取代此前所有数字。")],
], [40 * mm, 138 * mm])

# ---------------------------------------------------------------- section 7
H("7. Exact code revision / 确切代码版本")
EN("The baseline is the git commit tagged <b>v2.1-spec-frozen</b>. As additional evidence, every tracked file was hashed "
   "with SHA-256 on 14 October 2026. The 12 and 13 October manifests are kept as history; the 14 October manifest supersedes "
   "them and covers the approved baseline exactly. The manifest is stored at:")
ZH("基线为带有标签 <b>v2.1-spec-frozen</b> 的git提交。作为补充证据，2026年10月14日对全部受跟踪文件计算了SHA-256。"
   "10月12日与13日的清单仅作历史保留；10月14日清单取代之，并与获批基线完全一致。清单存于：")
EN("<b>docs/v2.1-validation/CODE_REVISION_2026-10-14.sha256</b>")
simple_table([
    [P("MANIFEST SHA-256 / 清单哈希", s_cellh)],
    [P(f"<font face='ZH'>{os.environ.get('MANIFEST_HASH', 'MANIFEST HASH NOT SET')}</font>")],
], [178 * mm], header=False)
EN("Re-hashing the same files reproduces this manifest hash exactly; any code change after this proposal "
   "produces a different one. This hash, together with the test result above, is the freeze baseline.")
ZH("对相同文件重新哈希可完全复现该清单哈希；本提案之后的任何代码改动都会产生不同哈希。该哈希与上述测试"
   "结果共同构成冻结基线。")

# ---------------------------------------------------------------- section 8
H("8. Freeze proposal / 冻结提案")
EN(f"{INF} — Proposed: <b>approve V2.1 as FROZEN</b> on this code revision. Basis: both warehouse flows are "
   f"observed and match the V2.1 loops; every case that system evidence can decide has a recorded, "
   f"evidence-labelled disposition; the remaining opens are physical-verification items (two counts, supplier "
   f"documents, owner assignment) that do not change the design. The adoption gaps (store receipt confirmation, "
   f"write-off and adjustment recording) are inside the approved V2.1 scope, and V2.1's traceability principle "
   f"directly targets them.")
ZH("推断 — 建议：<b>批准V2.1按此代码版本冻结</b>。依据：两条仓库流程已现场观察且与V2.1闭环一致；凡系统证据"
   "可判定的案例均已有带证据标识的结论；其余未结项为实物核实事项（两次实盘、供应商单据、责任人指派），不改变"
   "设计。启用缺口（门店收货确认、报损与调整录入）均在已批准的V2.1范围内，且V2.1的可追溯原则正是针对这些缺口。")
EN("If any open item later produces evidence that contradicts a disposition recorded here, the correct response "
   "is a V2.1.x note, not a redesign. Benchmark research remains paused by management instruction.")
ZH("若任何未结事项日后出现与此处结论相矛盾的证据，正确处理方式是发布V2.1.x补充说明，而非重新设计。竞品研究"
   "按管理层指示继续暂停。")
story.append(Spacer(1, 4))
rule()
EN("<b>Evidence and source trail.</b> Observed on-site: both warehouse sequences, 12 Oct 2026. System confirmed: "
   "StoreHub mirror snapshot 12 Oct 2026 06:21 PHT (purchase_order, po_line, stock_transfer, stock_transfer_line, "
   "stock_transfer_import raw CSV, inventory_current, inventory_snapshot, inventory_change, sales_tx/sales_line, "
   "employee); RetailOS adoption tables (stock_event, cycle_count, cycle_count_line, receiving, receiving_line, "
   "expiry_batch) — all empty as of the snapshot. Staff confirmed: management direction relayed by the user. "
   "Baselines: RETAIL_OS_V2_1_FREEZE_CANDIDATE.md, WAREHOUSE_ONSITE_VALIDATION_CHECKLIST.md, FC1 v2 report. "
   "No operational records were modified for this proposal.")
ZH("<b>证据与来源。</b>现场观察：2026年10月12日两条仓库流程。系统确认：2026年10月12日06:21（菲律宾时间）"
   "StoreHub镜像快照（采购单及明细、调拨单及明细、调拨导入原始CSV、当前库存、库存快照、库存变动、销售交易、"
   "员工）；RetailOS启用表（库存事件、盘点及明细、收货及明细、有效期批次）——快照时点均为空。人员确认：用户"
   "转述的管理层指示。基线：RETAIL_OS_V2_1_FREEZE_CANDIDATE.md、WAREHOUSE_ONSITE_VALIDATION_CHECKLIST.md、"
   "FC1 v2报告。本提案未修改任何运营记录。")


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("ZH", 7.5)
    canvas.setFillColor(MUTE)
    canvas.drawString(16 * mm, 10 * mm, "V2.1 FROZEN proposal / 最终冻结提案 — for management approval")
    canvas.drawRightString(194 * mm, 10 * mm, f"{doc.page}")
    canvas.restoreState()


out = r"C:\Users\User\Downloads\Retail_OS_V2_1_FINAL_FROZEN_Proposal_EN_ZH.pdf"
doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                        topMargin=14 * mm, bottomMargin=16 * mm, title="Retail OS V2.1 FINAL FROZEN Proposal")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print("written:", out)

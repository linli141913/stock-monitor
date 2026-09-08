"""从官方催化公告正文提取事件与明确业务对象。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import re
from typing import Any, Optional, Sequence, Tuple
import unicodedata

from radar.leader_business_automatic_contracts import (
    AutomaticBusinessEvidenceStatus,
    OfficialBusinessDocumentKind,
)
from radar.leader_business_document_facts import (
    OfficialBusinessEvidenceFragment,
)
from radar.sources.leader_business_catalyst_official import (
    OfficialBusinessCatalystDocument,
    OfficialBusinessCatalystKind,
)
from radar.sources.leader_business_document_content import (
    OfficialBusinessDocumentContentResult,
    OfficialBusinessDocumentPage,
)


BUSINESS_CATALYST_FACT_CONTRACT_ID = (
    "radar-leader-business-catalyst-facts-v1"
)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GENERIC_TERMS = frozenset({
    "产品", "服务", "业务", "行业", "项目", "技术", "平台", "系统",
})
NEGATIVE_MARKERS = ("终止", "取消", "未中标", "不再履行")
TERM_SPLIT_PATTERN = re.compile(r"、|以及|及|和|与")
EARNINGS_DIRECTION_PATTERN = r"(?:增长|提升|增加|上升|下降|减少|承压|回落|扭亏)"
EXPLICIT_METRIC_DIRECTION_PATTERN = (
    r"(?:增长|提升|增加|上升|下降|减少|承压|回落|扭亏|"
    r"上涨|下滑|下调)"
)
PROSPECTIVE_MARKERS = ("拟", "计划", "意向", "预计")
NEGATIVE_CONFIRMATION_MARKERS = ("未", "不", "无法", "不能")
EARNINGS_PROSPECTIVE_MARKERS = (
    *PROSPECTIVE_MARKERS,
    "有望",
    "可能",
    "预期",
    "预测",
    "推测",
    "或将",
    "将",
)
EARNINGS_UNCONFIRMED_MARKERS = (
    *EARNINGS_PROSPECTIVE_MARKERS,
    *NEGATIVE_CONFIRMATION_MARKERS,
    "是否",
    "尚待",
    "有待",
    "待确定",
)
EARNINGS_HEADING_CONTAMINATION_MARKERS = (
    *EARNINGS_PROSPECTIVE_MARKERS,
    "尚待",
    "有待",
    "待确定",
)
EARNINGS_ASSERTION_DENIAL_MARKERS = (
    "没有证据",
    "无证据",
    "尚无",
    "否认",
    "不支持",
    "无法证明",
    "不能证明",
    "未能证明",
)
EARNINGS_ASSERTION_DENIAL_PATTERN = re.compile(
    r"(?:否认|否定|不支持|无法证明|不能证明|未能证明|"
    r"(?:没有|尚无|缺乏|无)[^。!?！？；;]{0,32}(?:证据|依据|材料)|"
    r"未发现[^。!?！？；;]{0,32}(?:证据|依据|材料))"
)
EARNINGS_FOLLOWUP_RETRACTION_PATTERN = re.compile(
    r"(?:"
    r"(?:上述|以上)(?:仅为(?:预测|推测)|系(?:预测|推测)|"
    r"为(?:预测|推测))"
    r"|(?:(?:上述|以上|该|此)(?:说法|表述|内容|消息)"
    r"|对此(?:说法|消息)?)"
    r"[^。!?！？；;]{0,32}"
    r"(?:不实|不属实|仅为(?:预测|推测)|系(?:预测|推测)|"
    r"为(?:预测|推测)|(?:只是|仅是|属于)"
    r"[^。!?！？；;]{0,12}(?:预测|推测)|否认|否定|撤回)"
    r")"
)
EARNINGS_FOLLOWUP_DIRECT_RETRACTION_PATTERN = re.compile(
    r"(?:否认|否定|撤回)(?:了|过)?"
    r"(?:上述|以上|该|此)(?:说法|表述|内容|消息)"
)
EARNINGS_FOLLOWUP_ASSERTION_CONFIRMED_PATTERN = re.compile(
    r"(?:上述|以上|该|此)(?:说法|表述|内容|消息)"
    r"[^，,。!?！？；;]{0,16}"
    r"(?:(?:并未|未曾|没有)(?:被公司)?(?:否认|否定|撤回)"
    r"|(?:仍然|依然)(?:成立|有效|属实))"
)
EARNINGS_FOLLOWUP_PASSIVE_RETRACTION_PATTERN = re.compile(
    r"(?:但|却|随后|其后|后来)"
    r"[^。!?！？；;]{0,12}"
    r"(?:被(?:公司)?|遭(?:公司)?|受到(?:公司)?)"
    r"(?:否认|否定|撤回)"
)
EARNINGS_HYPOTHETICAL_MARKERS = ("假设", "假定")
STRICT_CONFIRMED_HISTORICAL_PLAN_PHRASES = (
    "原计划建设项目开工率不足",
)
STRICT_NON_PROSPECTIVE_CONTEXT_PHRASES = (
    "不断挖掘3D打印多方位应用的可能性",
)
STRICT_CONFIRMED_ACTUAL_OUTCOME_PHRASES = (
    "整体收入未达预期",
    "销量及售价不及预期",
    "不断提升",
)
GENERIC_OBJECT_PREFIXES = (
    "相关",
    "其他",
    "某",
    "部分",
    "各类",
    "多种",
    "多类",
    "若干",
    "系列",
    "多款",
    "多项",
    "多个",
    "一系列",
    "全部",
    "这类",
    "同类",
    "各款",
    "众多",
)
UNEXECUTED_CONTRACT_MARKERS = (
    *PROSPECTIVE_MARKERS,
    *NEGATIVE_CONFIRMATION_MARKERS,
)
PRELIMINARY_AWARD_MARKERS = ("预中标", "候选")
UNCONFIRMED_AWARD_MARKERS = (
    *PROSPECTIVE_MARKERS,
    *NEGATIVE_CONFIRMATION_MARKERS,
    "是否",
    "尚待",
    "有待",
    "待确定",
    "初步确定",
    "暂时确定",
    "临时确定",
    "预先确定",
)
EARNINGS_REASON_HEADING_MARKERS = ("业绩变动原因说明",)
EARNINGS_REASON_INTRO_PATTERN = re.compile(
    r"(?:主要原因(?:是|为)?|主要是|主要系)[:：]?"
)
FORECASTED_FINANCIAL_RESULT_PATTERN = re.compile(
    r"(?:"
    r"预计[^。;；!?！？]{0,100}(?:净利润|利润总额|业绩)"
    r"|(?:净利润|利润总额|业绩)[^。;；!?！？]{0,40}预计"
    r")[^。;；!?！？]{0,40}$"
)
SENTENCE_END_MARKERS = ("。", "!", "！", "?", "？")
QUOTED_EPC_CONTRACT_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])"
    r"(?:(?!(?:拟|计划|意向|预计))[^，,。.;；：:]){0,120}?"
    r"就[“\"]([^”\"]{2,60}?项目)[”\"]"
    r"签署EPC承包合同"
)
CONFIRMED_QUOTED_PROJECT_AWARD_PATTERN = re.compile(
    r"(?:被(?:确认|确定)为|(?:确认|确定)公司为|"
    r"确定[^“”\"。!?！？]{2,240}?组成的联合体为)"
    r"[“\"]([^”\"]{2,60}?项目[^”\"]{0,30})[”\"]"
    r"的中标(?:单位|供应商)"
    r"(?=$|[，,。.;；：:!?！？])"
)
CONFIRMED_NOTICE_PROJECT_AWARD_PATTERN = re.compile(
    r"收到[^。!?！？]{0,60}?《中标通知书》"
    r"[^。!?！？]{0,40}?[，,]确认"
    r"(?:公司|本公司|[一-鿿A-Za-z0-9]{2,32}?)中标"
    r"《([^》]{2,60}?项目[^》]{0,30})》"
    r"(?=$|[，,。.;；：:!?！？])"
)
CONFIRMED_DALONG_CONSTRUCTION_AWARD_PATTERN = re.compile(
    r"关于(建筑施工)项目收到中标通知书并签订合同的公告"
    r".{0,2400}?"
    r"(?:20\d{2}年\d{1,2}月\d{1,2}日[，,])?"
    r"大龙顺发收到《中标通知书》[，,]被确认为该项目中标人"
    r"(?=$|[，,。.;；：:!?！？])"
)
CONFIRMED_UNQUOTED_UNION_PROJECT_AWARD_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])"
    r"[^。!?！？]{0,100}?组成的联合体被确定为"
    r"([一-鿿A-Za-z0-9()（）-]{6,90}?(?:项目|工程总承包))"
    r"(?:[(（]以下简称[“\"]?项目[”\"]?[)）])?中标人"
    r"(?=$|[，,。.;；：:!?！？])"
)
CONFIRMED_NOTICE_UNQUOTED_UNION_PROJECT_AWARD_PATTERN = re.compile(
    r"收到[^。!?！？]{0,180}?发来的中标通知书[：:]"
    r"[^。!?！？]{0,100}?(?:组成的)?联合体被确定为"
    r"([一-鿿A-Za-z0-9()（）-]{6,90}?(?:项目|工程总承包))"
    r"(?:[(（]以下简称[“\"]?项目[”\"]?[)）])?中标人"
    r"(?=$|[，,。.;；：:!?！？])"
)
PLAIN_PROJECT_AWARD_PATTERN = re.compile(
    r"(?:中标|未中标)([一-鿿A-Za-z0-9]{2,20}?)(?:项目)"
)
REASON_PREFIX_BUSINESS_METRIC_PATTERN = re.compile(
    r"(?:业绩变动原因主要是|主要原因(?:是|为)|主要是|主要系)本期"
    r"(?!公司|主营|主要|整体|相关|新|核心|行业|市场)"
    r"([一-鿿A-Za-z0-9]{2,20}?)(?:业务|产品)(?:的)?"
    r"(?:营业收入|收入|毛利率|毛利|利润)"
    r"[^。!?！？]{0,12}"
    + EXPLICIT_METRIC_DIRECTION_PATTERN
)
NAMED_PRODUCT_AVERAGE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])报告期内[，,]"
    r"(?!公司|行业|市场|产品|原材料|能源|运输|采购|成本)"
    r"([一-鿿A-Za-z0-9]{2,20}?)(?:销售)?均价(?:同比)?"
    r"[^。!?！？]{0,6}"
    + EXPLICIT_METRIC_DIRECTION_PATTERN
    + r"[，,]公司\1(?:及[一-鿿A-Za-z0-9]{2,12})?产品盈利"
    + r"[^。!?！？]{0,6}"
    + EARNINGS_DIRECTION_PATTERN
)
NAMED_PRODUCT_DELIVERY_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:]|业绩变动原因说明)报告期内[，,](?:受益于|得益于)"
    r"(?!公司|行业|市场|主营|主要|整体|相关|新|核心|产品)"
    r"([一-鿿A-Za-z0-9]{2,20}?)产品交付量的同比(?:大幅)?增加"
    r"[，,]公司营业收入同比增长"
)
NAMED_HIGH_VALUE_PRODUCT_SHIPMENT_SHARE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])"
    r"(?!(?:拟|计划|意向|预计))"
    r"(锂电铜箔(?:、|及|和|与)电子电路铜箔)"
    r"高附加值产品的出货占比均显著提升"
)
NAMED_PRODUCT_DEMAND_REVENUE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])(?:报告期内[，,])?公司"
    r"(新能源电源(?:、|及|和|与)其他电源)"
    r"产品市场需求较好[，,]带动公司整体营业收入"
    r"实现同比增长"
)
NAMED_INDUSTRY_SUBSEGMENT_REVENUE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])"
    r"(?!公司|主营|主要|整体|相关|新|核心|行业|市场)"
    r"([一-鿿A-Za-z0-9]{2,12}?业)"
    r"[一-鿿A-Za-z0-9]{1,12}?板块收入(?:同比)?"
    r"(?:显著|大幅|有较大幅度)?"
    + EXPLICIT_METRIC_DIRECTION_PATTERN
)
NAMED_PRODUCT_OUTPUT_SHARE_PATTERN = re.compile(
    r"(?:^|[。!?！？；;]|业绩变动原因说明)"
    r"[^。!?！？；;]{0,180}?(?:该业务|该项业务)占公司营收比重已达"
    r"(?:约)?\d+(?:\.\d+)?%[^。!?！？；;]{0,30}?[，,]产(?!量|日)"
    r"(?!(?:公司|主营|主要|整体|相关|新|核心|行业|市场|产品|业务|项目|赛道|"
    r"资源化|利用|固危废))"
    r"((?!(?:[一-鿿A-Za-z0-9]{0,18}(?:业务|行业|项目|赛道|资源化|利用|固危废|"
    r"公司|产品)))[一-鿿A-Za-z0-9]{2,20}?)"
    r"(?:超过|达到)(?:约)?\d+(?:\.\d+)?(?:吨|万吨|公斤|千克|kg)"
    r"(?=$|[。!?！？；;]|[(（]|[，,]公司整体营业收入)"
)
CAUSAL_SOLD_PRODUCT_MARGIN_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])(?:导致|致使)"
    r"(?:本期|本年度|报告期内)?销售(皮棉)毛利率"
    r"(?:比上年|同比)[^。!?！？]{0,10}"
    + EXPLICIT_METRIC_DIRECTION_PATTERN
)
CAUSAL_NAMED_SALES_BUSINESS_MARGIN_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])(?:导致|致使)"
    r"(沥青)销售业务毛利率(?:同比)?"
    r"[^。!?！？]{0,8}" + EXPLICIT_METRIC_DIRECTION_PATTERN
)
REGISTERED_NAMED_PRODUCT_INCOME_PATTERN = re.compile(
    r"中国首款四价流脑结合疫苗"
    r"(((?!(?:研发|试验|临床|开发|候选|预计|的))"
    r"[一-鿿A-Za-z0-9]){2,10}®)收入保持持续增长"
)
FIXED_SINGLE_TICKET_EXPRESS_REVENUE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])"
    r"报告期内公司单票(快递)服务收入2\.33元"
    r"[，,]同比较大幅度上升"
    r"(?=$|[，,。!?！？；;])"
)
NAMED_PRIMARY_FOOTWEAR_SALES_PRESSURE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])公司主营的(皮鞋)业务销售面临压力"
    r"[，,]整体收入未达预期"
    r"(?=$|[，,。!?！？；;])"
)
NAMED_CATERING_PRODUCT_SALES_GROWTH_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])(餐饮)业务表现亮眼"
    r"[，,]年宵品、端午粽销售均实现大幅增长"
    r"(?=$|[，,。!?！？；;])"
)
NAMED_FIELD_VEHICLE_CUSTOMIZATION_COST_PRESSURE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])(?:除此之外[，,])?在非美国市场[，,]"
    r"(场地电动车)的市场需求较为分散且产品以定制化为主"
    r"[，,]定制化业务对人员、研发及项目管理要求较高"
    r"[，,]人工及管理成本上升"
    r"[，,]从而也一定程度影响了公司盈利能力"
    r"(?=$|[，,。!?！？；;])"
)
COMPANY_NAMED_INDUSTRY_FIELD_REVENUE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])公司在"
    r"(?!相关|所在|主营|主要|整体|新|核心|公司|市场|产品|业务)"
    r"([一-鿿A-Za-z0-9]{2,20}?)行业领域取得了?显著成效[，,]"
    r"整体收入相较于?20\d{2}年有所"
    r"(?:提升|增长|下降|下滑)"
)
CONFIRMED_NAMED_PRODUCT_CAUSAL_REVENUE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])(?:报告期内[，,])?"
    r"(?:[一二三四五六七八九十]+是)?公司持续迭代全线"
    r"(?!相关|主要|整体|新|核心|各类|多款|产品)"
    r"([一-鿿A-Za-z0-9]{2,20}?产品)[，,]"
    r"[^。!?！？；;]{1,100}?驱动\1业务营收增长[，,]"
    r"毛利水平同步提升"
)
CONFIRMED_REAL_ESTATE_SETTLEMENT_MARGIN_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:]|业绩变动原因说明)"
    r"报告期内[，,]公司"
    r"(房地产开发)业务结转的收入虽较上年同期上升[，,]"
    r"但受结转收入的房地产项目毛利率降低的影响[，,]"
    r"公司整体营业毛利率同比下降"
)
CONFIRMED_BROKERAGE_TRANSACTION_IMPACT_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])但由于二手房价格出现了一定程度的下降"
    r"[，,]对公司(经纪)业务的交易金额和佣金收入也产生了"
    r"一定的负面影响"
)
CONFIRMED_3D_PRINTING_EQUIPMENT_SALES_GROWTH_PATTERN = re.compile(
    r"(3D打印设备)销售量较上年同期增加"
    r"(?=$|[，,。!?！？；;])"
)
CONFIRMED_PUBLICATION_BUSINESS_PRESSURE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])公司(出版)业务以大众图书出版为主[，,]"
    r"受市场整体疲软、行业竞争加剧等因素影响[，,]"
    r"对营收、利润形成较大冲击[，,]经营承压明显"
)
CONFIRMED_RUBBER_PRODUCT_SALES_PRESSURE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])公司(橡胶产品)销量及售价不及预期"
)
CONFIRMED_FILM_TELEVISION_REVENUE_PRESSURE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])由于(影视)业务的生产制作和发行周期"
    r"导致公司收入确认存在一定的季节性波动等原因[，,]"
    r"公司上半年\1业务确认收入较少"
)
STRICT_CONFIRMED_EARNINGS_PATTERNS = (
    CAUSAL_SOLD_PRODUCT_MARGIN_PATTERN,
    CAUSAL_NAMED_SALES_BUSINESS_MARGIN_PATTERN,
    REGISTERED_NAMED_PRODUCT_INCOME_PATTERN,
    FIXED_SINGLE_TICKET_EXPRESS_REVENUE_PATTERN,
    NAMED_PRIMARY_FOOTWEAR_SALES_PRESSURE_PATTERN,
    NAMED_CATERING_PRODUCT_SALES_GROWTH_PATTERN,
    NAMED_FIELD_VEHICLE_CUSTOMIZATION_COST_PRESSURE_PATTERN,
    COMPANY_NAMED_INDUSTRY_FIELD_REVENUE_PATTERN,
    CONFIRMED_NAMED_PRODUCT_CAUSAL_REVENUE_PATTERN,
    CONFIRMED_REAL_ESTATE_SETTLEMENT_MARGIN_PATTERN,
    CONFIRMED_BROKERAGE_TRANSACTION_IMPACT_PATTERN,
    CONFIRMED_3D_PRINTING_EQUIPMENT_SALES_GROWTH_PATTERN,
    CONFIRMED_PUBLICATION_BUSINESS_PRESSURE_PATTERN,
    CONFIRMED_RUBBER_PRODUCT_SALES_PRESSURE_PATTERN,
    CONFIRMED_FILM_TELEVISION_REVENUE_PRESSURE_PATTERN,
    NAMED_INDUSTRY_SUBSEGMENT_REVENUE_PATTERN,
)
NAMED_FOREIGN_BUSINESS_TURNAROUND_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:()（）])"
    r"(?!公司|主营|主要|整体|相关|新|核心|行业|市场)"
    r"([一-鿿A-Za-z0-9]{2,20}?)业务多措并举[，,]"
    r"营业收入实现增长[，,]利润大幅减亏"
)
NAMED_ANNUAL_BUSINESS_GROWTH_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])公司"
    r"(?!主营|主要|整体|相关|新|核心|业务|产品|行业|市场)"
    r"([一-鿿A-Za-z0-9]{2,20}?)业务在20\d{2}年度"
    r"实现了显著业绩增长"
)
NAMED_BUSINESS_SEGMENT_REVENUE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])(?:[一二三四五六七八九十]+是)?公司"
    r"(?!主营|主要|整体|相关|新|核心|业务|产品|行业|市场)"
    r"([一-鿿A-Za-z0-9]{2,20}?)业务板块巩固拓展[，,]"
    r"经营收入稳定增长"
)
NAMED_CORE_PRODUCT_ANAPHORA_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:])[^\u3002!?！？]{0,30}?核心产品"
    r"(?!该产品|主要产品|公司产品|产品)"
    r"([一-鿿A-Za-z0-9]{2,24}?)(?:[(（][^()（）。!?！？]{2,30}[)）])?"
    r"(?:20\d{2}年[^。!?！？，,]{2,40}[，,])?"
    r"受[^。!?！？]{2,30}?影响[，,]"
    r"该产品销售单价[^。!?！？]{0,8}?下调、销量"
    r"[^。!?！？]{0,8}?下滑[，,]对公司营业收入及"
    r"经营利润形成[^。!?！？]{0,8}?冲击"
)
OFFICIAL_METALS_PRODUCT_PRICE_PATTERN = re.compile(
    r"(?:^|[，,。.;；：:]|本期业绩变化的主要原因|"
    r"业绩变动原因说明)"
    r"(?:20\d{2}年(?:半年度|年度))?"
    r"((?:有色金属|贵金属)(?:及(?:有色金属|贵金属))?)"
    r"产品市场价格同比(?:上升|上涨|下降|下跌)"
)
OBJECT_PATTERNS = {
    OfficialBusinessCatalystKind.MAJOR_CONTRACT: (
        QUOTED_EPC_CONTRACT_PATTERN,
        re.compile(
            r"(?:签订|签署|续签|终止|取消)(?:了)?《"
            r"([^》]{2,40}?)(?:合同|协议)》"
        ),
        re.compile(
            r"(?:签订|签署|续签|终止|取消)(?:了)?"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:项目合同|项目|合同|协议)"
        ),
    ),
    OfficialBusinessCatalystKind.PROJECT_AWARD: (
        QUOTED_EPC_CONTRACT_PATTERN,
        CONFIRMED_QUOTED_PROJECT_AWARD_PATTERN,
        CONFIRMED_NOTICE_PROJECT_AWARD_PATTERN,
        CONFIRMED_DALONG_CONSTRUCTION_AWARD_PATTERN,
        CONFIRMED_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
        CONFIRMED_NOTICE_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
        re.compile(
            r"第[0-9一二三四五六七八九十、，至和及-]{1,16}标段"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:项目)"
        ),
        PLAIN_PROJECT_AWARD_PATTERN,
    ),
    OfficialBusinessCatalystKind.CAPACITY_START: (
        re.compile(
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:生产线|项目)"
            r"(?:已|正式)?(?:建成|投产)"
        ),
    ),
    OfficialBusinessCatalystKind.PRODUCT_CERTIFICATION: (
        re.compile(
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:产品)?"
            r"(?:获得|通过).{0,12}(?:认证|注册证|许可)"
        ),
    ),
    OfficialBusinessCatalystKind.PRIVATE_PLACEMENT_PROJECT: (
        re.compile(
            r"募集资金(?:拟)?用于([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:项目)"
        ),
    ),
    OfficialBusinessCatalystKind.EARNINGS_FORECAST: (
        OFFICIAL_METALS_PRODUCT_PRICE_PATTERN,
        REASON_PREFIX_BUSINESS_METRIC_PATTERN,
        NAMED_PRODUCT_AVERAGE_PATTERN,
        NAMED_PRODUCT_DELIVERY_PATTERN,
        NAMED_HIGH_VALUE_PRODUCT_SHIPMENT_SHARE_PATTERN,
        NAMED_PRODUCT_DEMAND_REVENUE_PATTERN,
        NAMED_INDUSTRY_SUBSEGMENT_REVENUE_PATTERN,
        NAMED_PRODUCT_OUTPUT_SHARE_PATTERN,
        CAUSAL_SOLD_PRODUCT_MARGIN_PATTERN,
        CAUSAL_NAMED_SALES_BUSINESS_MARGIN_PATTERN,
        REGISTERED_NAMED_PRODUCT_INCOME_PATTERN,
        FIXED_SINGLE_TICKET_EXPRESS_REVENUE_PATTERN,
        NAMED_PRIMARY_FOOTWEAR_SALES_PRESSURE_PATTERN,
        NAMED_CATERING_PRODUCT_SALES_GROWTH_PATTERN,
        NAMED_FIELD_VEHICLE_CUSTOMIZATION_COST_PRESSURE_PATTERN,
        COMPANY_NAMED_INDUSTRY_FIELD_REVENUE_PATTERN,
        CONFIRMED_NAMED_PRODUCT_CAUSAL_REVENUE_PATTERN,
        CONFIRMED_REAL_ESTATE_SETTLEMENT_MARGIN_PATTERN,
        CONFIRMED_BROKERAGE_TRANSACTION_IMPACT_PATTERN,
        CONFIRMED_3D_PRINTING_EQUIPMENT_SALES_GROWTH_PATTERN,
        CONFIRMED_PUBLICATION_BUSINESS_PRESSURE_PATTERN,
        CONFIRMED_RUBBER_PRODUCT_SALES_PRESSURE_PATTERN,
        CONFIRMED_FILM_TELEVISION_REVENUE_PRESSURE_PATTERN,
        NAMED_FOREIGN_BUSINESS_TURNAROUND_PATTERN,
        NAMED_ANNUAL_BUSINESS_GROWTH_PATTERN,
        NAMED_BUSINESS_SEGMENT_REVENUE_PATTERN,
        NAMED_CORE_PRODUCT_ANAPHORA_PATTERN,
        re.compile(
            r"(?:导致|致使)公司(?:报告期内)?"
            r"(?!报告期内|项目|公司|整体)"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,16}?养殖)(?:业务)?"
            r"(?:利润|毛利)[^。!?！？]{0,8}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:推动|带动)(?!公司|行业|主营|整体)"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,16}?)板块"
            r"(?:营业收入|收入)(?:实现)?(?:同比)?"
            r"[^。!?！？]{0,4}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])因"
            r"(?!市场|原材料|采购|能源|运输|成本)"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,16}?)(?:市场)?"
            r"价格(?:上涨|下降|回落|承压)导致当期营业收入"
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])"
            r"(?!公司业务|主营业务|主要业务)(?:公司)?"
            r"([\u4e00-\u9fffA-Za-z0-9]{1,18}?"
            r"(?:加工|生产|制造|服务|销售|运营))(?:业务)"
            r"(?:有序|逐步|全面)?(?:恢复|复工复产|恢复生产)"
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])[一二三四五六七八九十]+是"
            r"20\d{2}年(?:度|上半年|下半年|一季度|前三季度)?"
            r"(?!度|上半年|下半年|一季度|前三季度|公司|市场|"
            r"原材料|能源|运输|采购|成本)"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,24}?)"
            r"(?:销售价格|销售均价|销售单价)"
            r"[^。!?！？]{0,12}上涨"
        ),
        re.compile(
            r"(?:公司)?主要产品"
            r"([\u4e00-\u9fffA-Za-z0-9、及和]{2,48}?)(?:产品)?(?:的)?(?:市场)?"
            r"(?:销售价格|销售均价|销售单价|销量|产量|产销量)"
            r"[^。!?！？]{0,12}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])(?:报告期内)?"
            r"(?!公司主营|公司主要产品|主营|主要产品|净利润)(?:公司)?"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,24}?)(?:业务|产品)(?:的)?"
            r"(?:销售价格|销售均价|销售单价|销量|产量|产销量|"
            r"收入|营业收入|毛利率|毛利|利润)"
            r"[^。!?！？]{0,12}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])(?:报告期内)?"
            r"(?!公司主营业务|公司主要产品|主营业务|主要产品|净利润)(?:公司)?"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,24}?)(?:销售价格|销售均价|销售单价|"
            r"销量|产量|产销量)[^。!?！？]{0,12}"
            + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:^|[，,。.;；：:])(?:报告期内)?本期"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}(?:及|和)"
            r"[\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:营业收入|毛利率|毛利)"
            r"[^。!?！？]{0,12}" + EARNINGS_DIRECTION_PATTERN
        ),
        re.compile(
            r"(?:主要)?(?:受益于|得益于)"
            r"([\u4e00-\u9fffA-Za-z0-9]{2,20}?)(?:业务|产品)?"
            r"(?:实现|保持|持续)?" + EARNINGS_DIRECTION_PATTERN
        ),
    ),
}


@dataclass(frozen=True, repr=False)
class OfficialBusinessCatalystFactResult:
    status: AutomaticBusinessEvidenceStatus
    document_id: Optional[str]
    document_version: Optional[str]
    symbol: Optional[str]
    issuer_identity: Optional[str]
    event_kind: Optional[OfficialBusinessCatalystKind]
    content_sha256: Optional[str] = None
    business_terms: Tuple[str, ...] = field(default_factory=tuple, repr=False)
    fragments: Tuple[OfficialBusinessEvidenceFragment, ...] = field(
        default_factory=tuple,
        repr=False,
    )
    negative_event: bool = False
    source_time: Optional[datetime] = None
    validated_at: Optional[datetime] = None
    reasons: Tuple[str, ...] = ()
    contract_id: str = BUSINESS_CATALYST_FACT_CONTRACT_ID
    formal_score_ready: bool = False
    formal_gate_ready: bool = False
    formal_usable: bool = False
    state_transition_allowed: bool = False


def _result(
    status: AutomaticBusinessEvidenceStatus,
    *,
    document: Any,
    content: Any,
    business_terms: Sequence[str] = (),
    fragments: Sequence[OfficialBusinessEvidenceFragment] = (),
    negative_event: bool = False,
    reasons: Sequence[str] = (),
) -> OfficialBusinessCatalystFactResult:
    valid_document = (
        document if type(document) is OfficialBusinessCatalystDocument else None
    )
    valid_content = (
        content if type(content) is OfficialBusinessDocumentContentResult else None
    )
    return OfficialBusinessCatalystFactResult(
        status=status,
        document_id=valid_document.document_id if valid_document else None,
        document_version=(valid_document.document_version if valid_document else None),
        symbol=valid_document.symbol if valid_document else None,
        issuer_identity=(valid_document.issuer_identity if valid_document else None),
        event_kind=valid_document.event_kind if valid_document else None,
        content_sha256=(valid_content.content_sha256 if valid_content else None),
        business_terms=tuple(business_terms),
        fragments=tuple(fragments),
        negative_event=negative_event,
        source_time=valid_document.published_at if valid_document else None,
        validated_at=valid_content.fetched_at if valid_content else None,
        reasons=tuple(dict.fromkeys(reason for reason in reasons if reason)),
    )


def _aware(value: Any) -> bool:
    return bool(
        isinstance(value, datetime)
        and value.tzinfo is not None
        and value.utcoffset() is not None
    )


def _bound(document: Any, content: Any) -> bool:
    if (
        type(document) is not OfficialBusinessCatalystDocument
        or type(content) is not OfficialBusinessDocumentContentResult
        or content.status is not AutomaticBusinessEvidenceStatus.READY
        or content.document_kind is not OfficialBusinessDocumentKind.CATALYST
        or content.document_id != document.document_id
        or content.document_version != document.document_version
        or content.symbol != document.symbol
        or content.issuer_identity != document.issuer_identity
        or not isinstance(content.content_sha256, str)
        or SHA256_PATTERN.fullmatch(content.content_sha256) is None
        or not isinstance(content.pages, tuple)
        or not content.pages
        or content.page_count != len(content.pages)
        or not _aware(document.published_at)
        or not _aware(content.fetched_at)
        or content.fetched_at < document.published_at
    ):
        return False
    return all(
        type(page) is OfficialBusinessDocumentPage
        and isinstance(page.page_number, int)
        and not isinstance(page.page_number, bool)
        and page.page_number >= 1
        and isinstance(page.text, str)
        for page in content.pages
    )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value))


def _clean_term(value: str, *, maximum_length: int = 20) -> Optional[str]:
    term = _normalize(value).strip("：:。；;、,，‘’“”\"'()（）")
    term = re.sub(
        r"^(?:20\d{2}年(?:度|上半年|下半年|一季度|前三季度)?|"
        r"报告期内|本期|主要受益于|受益于|得益于|"
        r"主要是|主要系|主要原因是|公司主要产品|本公司|公司)+",
        "",
        term,
    )
    term = re.sub(r"^(?:全年|整体上公司)", "", term)
    term = re.sub(r"(?:业务|产品|板块)$", "", term)
    if (
        not 2 <= len(term) <= maximum_length
        or term in GENERIC_TERMS
        or term in {
            "主营", "主营业务", "主要", "公司", "整体", "营业", "该产品", "重大", "上述",
            "补充", "项目主要内容", "日常经营重大", "产品力", "品牌力的",
            "承包", "工程承包", "总承包", "总包", "工程总包", "EPC承包",
        }
        or re.fullmatch(
            r"(?:(?:EPC|施工|机电|工程|建设|总承包|承包|总包)+)项目",
            term,
            flags=re.IGNORECASE,
        ) is not None
        or re.fullmatch(
            r"(?:智慧城市|重点工程|重大项目|一般工程|综合治理)建设项目"
            r"(?:EPC工程总承包)?",
            term,
            flags=re.IGNORECASE,
        ) is not None
        or term.startswith(GENERIC_OBJECT_PREFIXES)
        or term.startswith((
            "导致", "致使", "因", "用以", "通知书后",
            "的第", "项目对公司",
        ))
        or any(marker in term for marker in (
            "净利润", "归属于", "股东", "业绩", "预计", "万元",
            "扣除非经常", "同比", "本年度", "影响该", "当事人之间",
            "权利义务", "招标人洽谈", "签订上述",
            "交付量",
        ))
        or not re.search(r"[\u4e00-\u9fffA-Za-z0-9]", term)
    ):
        return None
    return term


def _matched_terms(
    event_kind: OfficialBusinessCatalystKind,
    raw_value: str,
) -> Tuple[str, ...]:
    raw_terms = (
        TERM_SPLIT_PATTERN.split(raw_value)
        if event_kind is OfficialBusinessCatalystKind.EARNINGS_FORECAST
        else (raw_value,)
    )
    return tuple(dict.fromkeys(
        term
        for raw_term in raw_terms
        if (
            term := _clean_term(
                raw_term,
                maximum_length=(
                    60
                    if event_kind in {
                        OfficialBusinessCatalystKind.MAJOR_CONTRACT,
                        OfficialBusinessCatalystKind.PROJECT_AWARD,
                    }
                    else 20
                ),
            )
        ) is not None
    ))


def _has_prospective_sentence_context(value: str, match_start: int) -> bool:
    sentence_start = max(
        (value.rfind(marker, 0, match_start) for marker in SENTENCE_END_MARKERS),
        default=-1,
    ) + 1
    return any(
        marker in value[sentence_start:match_start]
        for marker in PROSPECTIVE_MARKERS
    )


def _has_unconfirmed_award_context(value: str, match: re.Match) -> bool:
    context = _sentence_context(value, match)
    return any(
        marker in context
        for marker in (
            *UNCONFIRMED_AWARD_MARKERS,
            *PRELIMINARY_AWARD_MARKERS,
        )
    )


def _has_unexecuted_contract_context(value: str, match: re.Match) -> bool:
    sentence_start = max(
        (
            value.rfind(marker, 0, match.start())
            for marker in SENTENCE_END_MARKERS
        ),
        default=-1,
    ) + 1
    return any(
        marker in value[sentence_start:match.start(1)]
        for marker in UNEXECUTED_CONTRACT_MARKERS
    )


def _has_preliminary_award_context(value: str, match: re.Match) -> bool:
    context = _sentence_context(value, match)
    return any(marker in context for marker in PRELIMINARY_AWARD_MARKERS)


def _earnings_logical_start(value: str, match: re.Match) -> int:
    # PDF 文本常丢失句号：页首的“预计净利润”不能否掉后续
    # “业绩变动原因说明”中已发生的经营事实。同理，明确的
    # 财务结果预测可由“主要是/主要原因”引出已发生的对象事实。
    sentence_start = max(
        (value.rfind(marker, 0, match.start()) for marker in SENTENCE_END_MARKERS),
        default=-1,
    ) + 1
    logical_start = sentence_start
    for heading in EARNINGS_REASON_HEADING_MARKERS:
        position = value.rfind(heading, sentence_start, match.end())
        if position < 0 or position > match.end():
            continue
        immediate_prefix = value[max(sentence_start, position - 8):position]
        if not any(
            marker in immediate_prefix
            for marker in EARNINGS_HEADING_CONTAMINATION_MARKERS
        ):
            logical_start = max(logical_start, position)
    prefix_and_match = value[logical_start:match.end()]
    for intro in EARNINGS_REASON_INTRO_PATTERN.finditer(prefix_and_match):
        forecast_prefix = prefix_and_match[:intro.start()]
        if FORECASTED_FINANCIAL_RESULT_PATTERN.search(forecast_prefix):
            logical_start += intro.start()
            break
    return logical_start


def _has_unconfirmed_earnings_context(value: str, match: re.Match) -> bool:
    logical_start = _earnings_logical_start(value, match)
    prefix_and_match = value[logical_start:match.end()]
    context = prefix_and_match
    return any(marker in context for marker in EARNINGS_UNCONFIRMED_MARKERS)


def _followup_retracts_earnings_assertion(followup: str) -> bool:
    if EARNINGS_FOLLOWUP_DIRECT_RETRACTION_PATTERN.search(followup):
        return True
    confirmed = EARNINGS_FOLLOWUP_ASSERTION_CONFIRMED_PATTERN.search(followup)
    if confirmed is not None:
        remaining = followup[:confirmed.start()] + followup[confirmed.end():]
        return bool(
            EARNINGS_FOLLOWUP_DIRECT_RETRACTION_PATTERN.search(remaining)
            or EARNINGS_FOLLOWUP_RETRACTION_PATTERN.search(remaining)
            or EARNINGS_FOLLOWUP_PASSIVE_RETRACTION_PATTERN.search(remaining)
        )
    return EARNINGS_FOLLOWUP_RETRACTION_PATTERN.search(followup) is not None


def _has_unconfirmed_strict_earnings_context(
    value: str,
    match: re.Match,
) -> bool:
    logical_start = _earnings_logical_start(value, match)
    prefix = value[logical_start:match.start()]
    immediate_prefix = value[max(logical_start, match.start() - 12):match.start()]
    sentence_ends = tuple(
        position
        for marker in SENTENCE_END_MARKERS
        if (position := value.find(marker, match.end())) >= 0
    )
    sentence_end = min(sentence_ends) + 1 if sentence_ends else len(value)
    matched_and_tail = value[
        match.start():min(sentence_end, match.end() + 48)
    ]
    confirmed_matched_and_tail = matched_and_tail
    for phrase in STRICT_CONFIRMED_ACTUAL_OUTCOME_PHRASES:
        confirmed_matched_and_tail = confirmed_matched_and_tail.replace(
            phrase,
            "",
        )
    followup_ends = tuple(
        position
        for marker in SENTENCE_END_MARKERS
        if (position := value.find(marker, sentence_end)) >= 0
    )
    followup_end = (
        min(followup_ends) + 1
        if followup_ends
        else min(len(value), sentence_end + 64)
    )
    followup = value[sentence_end:min(followup_end, sentence_end + 64)]
    prospective_prefix = prefix
    for phrase in (
        *STRICT_CONFIRMED_HISTORICAL_PLAN_PHRASES,
        *STRICT_NON_PROSPECTIVE_CONTEXT_PHRASES,
    ):
        prospective_prefix = prospective_prefix.replace(phrase, "")
    return bool(
        any(marker in prefix for marker in EARNINGS_HYPOTHETICAL_MARKERS)
        or any(
            marker in prospective_prefix
            for marker in EARNINGS_PROSPECTIVE_MARKERS
        )
        or any(
            marker in prefix
            for marker in EARNINGS_ASSERTION_DENIAL_MARKERS
        )
        or EARNINGS_ASSERTION_DENIAL_PATTERN.search(prefix) is not None
        or any(
            marker in immediate_prefix
            for marker in (
                *NEGATIVE_CONFIRMATION_MARKERS,
                "是否",
                "尚待",
                "有待",
                "待确定",
            )
        )
        or any(
            marker in confirmed_matched_and_tail
            for marker in EARNINGS_UNCONFIRMED_MARKERS
        )
        or EARNINGS_ASSERTION_DENIAL_PATTERN.search(confirmed_matched_and_tail)
        is not None
        or _followup_retracts_earnings_assertion(followup)
    )


def _sentence_context(value: str, match: re.Match) -> str:
    sentence_start = max(
        (
            value.rfind(marker, 0, match.start())
            for marker in SENTENCE_END_MARKERS
        ),
        default=-1,
    ) + 1
    sentence_ends = tuple(
        position
        for marker in SENTENCE_END_MARKERS
        if (position := value.find(marker, match.end())) >= 0
    )
    sentence_end = min(sentence_ends) + 1 if sentence_ends else len(value)
    return value[sentence_start:sentence_end]


def extract_official_business_catalyst_facts(
    document: Any,
    content: Any,
) -> OfficialBusinessCatalystFactResult:
    if not _bound(document, content):
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            document=document,
            content=content,
            reasons=("business_catalyst_fact_identity_unverified",),
        )
    assert isinstance(document, OfficialBusinessCatalystDocument)
    assert isinstance(content, OfficialBusinessDocumentContentResult)
    patterns = OBJECT_PATTERNS[document.event_kind]
    terms = []
    fragments = []
    seen_fragments = set()
    normalized_pages = tuple(_normalize(page.text) for page in content.pages)
    normalized_document = "".join(normalized_pages)
    for page, normalized in zip(content.pages, normalized_pages):
        for pattern in patterns:
            for match in pattern.finditer(normalized):
                if (
                    document.event_kind in {
                        OfficialBusinessCatalystKind.MAJOR_CONTRACT,
                        OfficialBusinessCatalystKind.PROJECT_AWARD,
                    }
                    and _has_prospective_sentence_context(
                        normalized,
                        match.start(),
                    )
                ):
                    continue
                if (
                    pattern in {
                        CONFIRMED_QUOTED_PROJECT_AWARD_PATTERN,
                        CONFIRMED_NOTICE_PROJECT_AWARD_PATTERN,
                        CONFIRMED_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                        CONFIRMED_NOTICE_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                    }
                    and _has_unconfirmed_award_context(
                        normalized,
                        match,
                    )
                ):
                    continue
                if (
                    document.event_kind
                    is OfficialBusinessCatalystKind.MAJOR_CONTRACT
                    and _has_unexecuted_contract_context(normalized, match)
                ):
                    continue
                if (
                    document.event_kind
                    is OfficialBusinessCatalystKind.PROJECT_AWARD
                    and pattern not in {
                        CONFIRMED_QUOTED_PROJECT_AWARD_PATTERN,
                        CONFIRMED_NOTICE_PROJECT_AWARD_PATTERN,
                        CONFIRMED_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                        CONFIRMED_NOTICE_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                    }
                    and _has_preliminary_award_context(normalized, match)
                ):
                    continue
                if (
                    document.event_kind
                    is OfficialBusinessCatalystKind.PROJECT_AWARD
                    and pattern is PLAIN_PROJECT_AWARD_PATTERN
                    and "中标通知书" in _sentence_context(normalized, match)
                ):
                    continue
                if (
                    pattern in {
                        CONFIRMED_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                        CONFIRMED_NOTICE_UNQUOTED_UNION_PROJECT_AWARD_PATTERN,
                    }
                    and any(marker in normalized_document for marker in ("终止", "取消"))
                ):
                    continue
                if (
                    document.event_kind
                    is OfficialBusinessCatalystKind.EARNINGS_FORECAST
                    and (
                        _has_unconfirmed_strict_earnings_context(
                            normalized,
                            match,
                        )
                        if pattern in STRICT_CONFIRMED_EARNINGS_PATTERNS
                        else _has_unconfirmed_earnings_context(
                            normalized,
                            match,
                        )
                    )
                ):
                    continue
                matched_terms = _matched_terms(
                    document.event_kind,
                    match.group(1),
                )
                if not matched_terms:
                    continue
                fragment = match.group(0)
                digest = hashlib.sha256(fragment.encode("utf-8")).hexdigest()
                if digest not in seen_fragments:
                    seen_fragments.add(digest)
                    fragments.append(OfficialBusinessEvidenceFragment(
                        page_number=page.page_number,
                        fragment_sha256=digest,
                        text=fragment,
                    ))
                for term in matched_terms:
                    if term not in terms:
                        terms.append(term)
    if not terms or not fragments:
        return _result(
            AutomaticBusinessEvidenceStatus.SOURCE_UNVERIFIED,
            document=document,
            content=content,
            reasons=("business_catalyst_fact_object_missing",),
        )
    return _result(
        AutomaticBusinessEvidenceStatus.READY,
        document=document,
        content=content,
        business_terms=terms,
        fragments=fragments,
        negative_event=any(marker in normalized_document for marker in NEGATIVE_MARKERS),
    )

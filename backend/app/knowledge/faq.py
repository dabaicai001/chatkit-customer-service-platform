"""knowledge:FAQ 语料(公司知识库的"书")。

演示语料;生产环境可换成从 CMS/Confluence/数据库批量导入,
只要最终变成 ``Document`` 列表交给 RagService 索引即可。
"""

from __future__ import annotations

from typing import List

from .vector_store import Document

FAQ_DOCUMENTS: List[Document] = [
    Document(
        doc_id="faq-refund-policy",
        title="退款政策",
        content=(
            "订单在发货前可随时申请退款,款项原路返回,预计 1-3 个工作日到账;"
            "发货后需先拒收或申请退货,仓库验收通过后办理退款;"
            "已签收订单支持 7 天无理由退货(定制类商品除外)。"
        ),
        tags=["退款", "退货", "政策", "售后"],
    ),
    Document(
        doc_id="faq-shipping",
        title="物流与配送",
        content=(
            "普通订单 48 小时内发货,顺丰/京东配送,全国大部分地区 2-4 天送达;"
            "物流进度可在「我的订单」中实时查看;大促期间可能延迟 1-2 天。"
        ),
        tags=["物流", "发货", "快递", "配送", "多久到"],
    ),
    Document(
        doc_id="faq-invoice",
        title="发票开具",
        content=(
            "支持电子普通发票和增值税专用发票;订单完成后在「我的订单-开具发票」提交,"
            "电子发票 1 个工作日开出并发送至邮箱;专票需提供税号与开票信息。"
        ),
        tags=["发票", "开票", "电子发票"],
    ),
    Document(
        doc_id="faq-warranty",
        title="保修与质保",
        content=(
            "数码配件自签收之日起享 1 年质保,主机类产品 2 年质保;"
            "质保期内非人为损坏免费维修或更换;寄修前请先在「我的工单」中登记,"
            "客服会提供寄修地址与运单号。"
        ),
        tags=["保修", "质保", "维修", "售后"],
    ),
    Document(
        doc_id="faq-points",
        title="会员与积分",
        content=(
            "消费 1 元累计 1 积分,积分可在结算时抵扣现金(100 积分 = 1 元);"
            "VIP 会员专享 95 折、优先发货与专属客服;积分有效期至获得次年年底。"
        ),
        tags=["会员", "积分", "VIP", "权益"],
    ),
    Document(
        doc_id="faq-human",
        title="人工客服服务时间",
        content=(
            "人工客服服务时间为每天 9:00-22:00;非服务时间可留言,"
            "人工客服会在次日优先处理;紧急问题(如资金安全)可拨打 400-000-0000。"
        ),
        tags=["人工", "客服", "电话", "服务时间"],
    ),
]

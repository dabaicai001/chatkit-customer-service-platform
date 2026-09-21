"""通用 Widget 构建器(与行业无关)。

option_list.widget —— 通用选择列表(订单/商品/工单选一)
confirm.widget     —— 通用确认卡片(退款/取消等敏感操作二次确认)
"""

from __future__ import annotations

from typing import Any, Dict, List

from chatkit.widgets import WidgetRoot, WidgetTemplate

CONFIRM_ACTION_TYPE = "support.confirm"
CANCEL_ACTION_TYPE = "support.cancel"
SELECT_ACTION_TYPE = "support.select_option"

option_list_template = WidgetTemplate.from_file("widgets/option_list.widget")
confirm_template = WidgetTemplate.from_file("widgets/confirm.widget")


def build_option_list_widget(
    options: List[Dict[str, Any]],
    *,
    title: str = "请选择",
    selected_id: str | None = None,
    action_type: str = SELECT_ACTION_TYPE,
) -> WidgetRoot:
    """渲染通用选择列表。option 需含 id/title/subtitle/badge/badge_color。"""

    payload = {
        "options": options,
        "title": title,
        "selectedId": selected_id,
        "actionType": action_type,
    }
    return option_list_template.build(payload)


def build_confirm_widget(
    *,
    action_id: str,
    prompt: str,
    title: str = "操作确认",
) -> WidgetRoot:
    """渲染确认卡片,确认/取消均走服务端 action。"""

    payload = {
        "actionId": action_id,
        "prompt": prompt,
        "title": title,
        "confirmActionType": CONFIRM_ACTION_TYPE,
        "cancelActionType": CANCEL_ACTION_TYPE,
    }
    return confirm_template.build(payload)

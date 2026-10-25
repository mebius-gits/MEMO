from memo.domain.environment import event_words
from memo.domain.interaction import (
    EmotionLabel,
    PreparedInteraction,
    Reply,
    Tone,
    notice_emotion,
)

LABELS = {
    "temperature_c": ("溫度", "°C"),
    "humidity_pct": ("濕度", "%"),
    "co2_ppm": ("CO₂", " ppm"),
    "feels_like_c": ("體感溫度", "°C"),
    "illuminance_lux": ("亮度", " lux"),
}


def spoken(metrics):
    return "、".join(
        f"{LABELS[key][0]} {value:g}{LABELS[key][1]}"
        for key, value in metrics.items()
        if key in LABELS
    )


def followup_words(schedule) -> str:
    """Before/after report for a follow-up, e.g. 你開窗之後，CO₂ 從 1400 降到 900 ppm。"""
    before, after = schedule.get("baseline") or {}, schedule.get("current") or {}
    parts = []
    for key in ("co2_ppm", "feels_like_c", "humidity_pct"):
        if key in before and key in after:
            name, unit = LABELS[key]
            old, new = before[key], after[key]
            trend = "降到" if new < old else "升到" if new > old else "維持在"
            parts.append(
                f"{name}{trend} {new:g}{unit}"
                if trend == "維持在"
                else f"{name}從 {old:g} {trend} {new:g}{unit}"
            )
    action = schedule["message"].removeprefix("我")
    if not parts:
        return f"你{action}之後，我還沒有足夠的前後資料，沒辦法判斷有沒有改善。"
    return f"你{action}之後，" + "、".join(parts) + "。"


LOOK_EMOTIONS = {
    "alert": EmotionLabel.CONCERNED,
    "repeat": EmotionLabel.CONCERNED,
    "recovered": EmotionLabel.HAPPY,
    "changed": EmotionLabel.SURPRISED,
    "report": EmotionLabel.FRIENDLY,
    "unseen": EmotionLabel.CONFUSED,
}


def look_words(schedule) -> str:
    """A look notice without the model: what the photo showed, in one sentence."""
    notice, detail = schedule.get("notice"), schedule.get("detail")
    if notice == "unseen":
        quality = "、".join(schedule.get("quality") or [])
        why = f"，照片{quality}" if quality else ""
        return f"我連續幾次拍照都看不清楚{why}，可以幫我看一下鏡頭嗎？"
    if notice == "recovered":
        return f"剛剛看了一下，{detail}，好多了！" if detail else "剛剛看了一下，已經好多了！"
    if notice == "changed":
        return f"我拍照比對發現有變化：{detail or '和之前不一樣了'}。"
    if notice in ("alert", "repeat"):
        count = schedule.get("today_count") or 0
        times = f"（今天第 {count} 次）" if count > 1 else ""
        return f"提醒一下：{detail or schedule['message']}{times}。"
    if schedule.get("mode") == "report":
        return f"我看了一下：{detail or '這次看不太清楚'}。"
    answer = {"yes": "是", "no": "不是", "unknown": "看不出來"}.get(schedule.get("result"), "")
    return f"我看了一下「{schedule['question']}」：{detail or answer}。"


def observed_words(schedule) -> str:
    """An observation notice without the model."""
    if schedule.get("notice") == "arrival":
        text = "你回來啦！"
        away = schedule.get("while_away") or []
        if away:
            first = away[0]
            text += f"你不在的時候，{first['at']} {first['event']}"
            text += f"：{first['detail']}。" if first.get("detail") else "。"
        return text
    return f"我看到{schedule.get('detail') or '有動物跑進畫面'}了。"


def search_words(search) -> str:
    """One sentence from the first answer or result, naming where it came from."""
    status = search.get("status")
    if status == "not_configured":
        return "我還沒接上搜尋服務，現在沒辦法幫你上網查。"
    if status == "unavailable":
        return "搜尋服務現在連不上，等一下再幫你查。"
    if search.get("answers"):
        return f"我查到：{search['answers'][0]}"
    results = search.get("results") or []
    if not results:
        return f"我上網查了「{search['query']}」，但沒找到相關的資料。"
    first = results[0]
    text = f"我在 {first['source']} 查到：{first['snippet'] or first['title']}"
    # Read aloud, so keep it to about one spoken sentence.
    return text if len(text) <= 80 else text[:79] + "…"


class TemplateReplyGenerator:
    """Deterministic preview; does not claim to understand arbitrary user intent."""

    def generate(self, prepared: PreparedInteraction) -> Reply:
        tone = prepared.input.personality.tone
        request = prepared.input
        if request.context_source == "stored_readings":
            if request.image:
                return Reply(
                    "我現在看不到這張圖片，可以用文字跟我說說看嗎？", EmotionLabel.CONFUSED, 0.4
                )
            if request.web_search is not None:
                return Reply(search_words(request.web_search), EmotionLabel.FRIENDLY, 0.4)
            if request.actions_done and not request.proactive:
                return Reply(
                    "好，" + "；".join(request.actions_done) + "。", EmotionLabel.HAPPY, 0.5
                )
            if request.schedule:
                schedule = request.schedule
                if schedule["purpose"] == "observe":
                    emotion = (
                        EmotionLabel.HAPPY
                        if schedule.get("notice") == "arrival"
                        else EmotionLabel.SURPRISED
                    )
                    return Reply(observed_words(schedule), emotion, 0.5)
                if schedule["purpose"] == "look":
                    emotion = LOOK_EMOTIONS.get(schedule.get("notice"), EmotionLabel.FRIENDLY)
                    text = look_words(schedule)
                    if schedule.get("mode") == "report" and prepared.usable_metrics:
                        text += "環境：" + spoken(prepared.usable_metrics) + "。"
                    return Reply(text, emotion, 0.5)
                if schedule["purpose"] == "followup":
                    return Reply(followup_words(schedule), EmotionLabel.FRIENDLY, 0.5)
                if schedule["purpose"] == "summary":
                    text = (
                        "現在環境：" + spoken(prepared.usable_metrics) + "。"
                        if prepared.usable_metrics
                        else "現在沒有可用的環境資料，等收到新消息我再跟你說。"
                    )
                    return Reply(text, EmotionLabel.FRIENDLY, 0.4)
                return Reply(f"提醒你：{schedule['message']}。", EmotionLabel.FRIENDLY, 0.5)
            if request.missed:
                text = f"你不在的時候，有 {len(request.missed)} 則提醒沒說到。"
                if request.attention and request.attention.get("level"):
                    text += "現在的狀況還在持續，我會繼續留意。"
                return Reply(text, EmotionLabel.CONCERNED, 0.4)
            if not prepared.environment_relevant:
                # Everyday chat: no readings, no event talk; just own up to the fallback.
                return Reply(
                    "我在這裡陪你，不過現在是備援回覆，還沒辦法好好接住這句話，可以再說一次嗎？",
                    EmotionLabel.FRIENDLY,
                    0.4,
                )
            event = request.event
            if event:
                rule_id = event.rule_evidence.split(":", 1)[0]
                attention = request.attention or {}
                text = event_words(
                    rule_id,
                    event.status,
                    tone.value,
                    kind=attention.get("notice"),
                    level=attention.get("level"),
                    minutes=attention.get("minutes_active"),
                )
                emotion = notice_emotion(event.status, attention.get("level"))
                return Reply(text, emotion, 0.5)
            if not prepared.usable_metrics:
                return Reply(
                    "目前沒有可用的近期環境資料。我先陪著你，等收到新消息再一起看看。",
                    EmotionLabel.CONFUSED,
                    0.3,
                )
            if any(
                word in request.user_message for word in ("數據", "數值", "幾度", "多少", "ppm")
            ):
                text = "最近的讀值：" + spoken(prepared.usable_metrics) + "。"
                return Reply(text, EmotionLabel.FRIENDLY, 0.4)
            if any(word in request.user_message for word in ("環境", "空氣", "溫度", "濕度")):
                text = (
                    "我有收到空間的新消息，會繼續留意有沒有值得告訴你的變化。你可以先做自己的事。"
                )
            else:
                text = (
                    "我在這裡陪你。現在使用備援回覆，還沒辦法好好接住這個問題，"
                    "但我會繼續留意周圍的變化。"
                )
            return Reply(text, EmotionLabel.FRIENDLY, 0.4)
        intro = {
            Tone.GENTLE: "我是 MEMO，我陪你看看環境資料。",
            Tone.LIVELY: "嗨！我是 MEMO，一起看看環境資料吧！",
            Tone.CONCISE: "MEMO 環境摘要：",
        }[tone]
        parts = [intro]
        emotion = EmotionLabel.FRIENDLY
        intensity = 0.4
        if prepared.usable_metrics:
            source_label = (
                "後端儲存的近期讀值為："
                if prepared.input.context_source == "stored_readings"
                else "你提供的近期讀值為："
            )
            parts.append(source_label + spoken(prepared.usable_metrics) + "。")
            parts.append("這些數值本身不代表環境已被判定正常或異常。")
        else:
            parts.append("目前沒有可用的近期環境讀值，無法判斷目前環境狀態。")
            emotion, intensity = EmotionLabel.CONFUSED, 0.3
        event = prepared.input.event
        if event:
            parts.append(
                f"呼叫端提供的事件 {event.event_id}（版本 {event.event_version}）"
                f"狀態為 {event.status}；尚未由事件資料庫核對。"
            )
            if event.status in ("active", "recovering"):
                emotion, intensity = EmotionLabel.CONCERNED, 0.5
        parts.append("此為模板預覽，尚未啟用模型問答，也未執行任何裝置或事件操作。")
        return Reply("".join(parts), emotion, intensity)

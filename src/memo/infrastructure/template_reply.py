from memo.domain.environment import event_words
from memo.domain.interaction import EmotionLabel, PreparedInteraction, Reply, Tone

LABELS = {
    "temperature_c": ("溫度", "°C"),
    "humidity_pct": ("濕度", "%"),
    "co2_ppm": ("CO₂", " ppm"),
}


def spoken(metrics):
    return "、".join(
        f"{LABELS[key][0]} {value:g}{LABELS[key][1]}" for key, value in metrics.items()
    )


class TemplateReplyGenerator:
    """Deterministic preview; does not claim to understand arbitrary user intent."""

    def generate(self, prepared: PreparedInteraction) -> Reply:
        tone = prepared.input.personality.tone
        request = prepared.input
        if request.context_source == "stored_readings":
            event = request.event
            if event:
                rule_id = event.rule_evidence.split(":", 1)[0]
                text = event_words(rule_id, event.status, tone.value)
                emotion = (
                    EmotionLabel.HAPPY if event.status == "resolved" else EmotionLabel.CONCERNED
                )
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

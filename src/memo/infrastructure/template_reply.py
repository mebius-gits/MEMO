from memo.domain.interaction import EmotionLabel, PreparedInteraction, Reply, Tone


class TemplateReplyGenerator:
    """Deterministic preview; does not claim to understand arbitrary user intent."""

    def generate(self, prepared: PreparedInteraction) -> Reply:
        tone = prepared.input.personality.tone
        intro = {
            Tone.GENTLE: "我是 MEMO，我陪你看看環境資料。",
            Tone.LIVELY: "嗨！我是 MEMO，一起看看環境資料吧！",
            Tone.CONCISE: "MEMO 環境摘要：",
        }[tone]
        parts = [intro]
        emotion = EmotionLabel.FRIENDLY
        intensity = 0.4
        if prepared.usable_metrics:
            labels = {
                "temperature_c": ("溫度", "°C"),
                "humidity_pct": ("濕度", "%"),
                "co2_ppm": ("CO₂", " ppm"),
            }
            values = [
                f"{labels[key][0]} {value:g}{labels[key][1]}"
                for key, value in prepared.usable_metrics.items()
            ]
            parts.append("你提供的近期讀值為：" + "、".join(values) + "。")
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

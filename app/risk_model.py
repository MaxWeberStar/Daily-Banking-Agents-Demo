from __future__ import annotations

import json
import math
from pathlib import Path


ARTIFACT = json.loads(Path(__file__).with_name("risk_model_coefficients.json").read_text(encoding="utf-8"))
FEATURE_LABELS = {
    "new_recipient": "本会话首次收款对象",
    "amount_ratio": "金额占当前借记卡余额比例",
    "short_frequency": "10 分钟内成功转账次数",
    "failed_codes": "模拟验证码连续失败次数",
}


def score_risk(features: dict[str, float]) -> dict[str, object]:
    contributions = {
        name: weight * (features[name] / scale)
        for name, scale, weight in zip(ARTIFACT["features"], ARTIFACT["scales"], ARTIFACT["weights"])
    }
    logit = ARTIFACT["intercept"] + sum(contributions.values())
    score = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, logit))))
    reasons = [
        {"feature": name, "label": FEATURE_LABELS[name], "value": features[name],
         "contribution": round(contribution, 4)}
        for name, contribution in sorted(contributions.items(), key=lambda item: abs(item[1]), reverse=True)
    ]
    return {"model_version": ARTIFACT["version"], "score": round(score, 4),
            "meaning": "仅为虚构案例训练的关注提示，不是欺诈概率或授权判定",
            "reasons": reasons}

def score(samples, recovered, errors, fault):
    if not samples:
        raise ValueError("A score requires at least one fault-window sample")
    intact = all(not sample["blank"] for sample in samples)
    final = samples[-1]
    if fault == "delay":
        handled = final["error"] or final["success"]
        resilient = (recovered and final["error"]) or (final["success"] and any(
            sample["loading"] for sample in samples
        ))
        labels = ("延迟后正常加载或显示错误提示", "重试后恢复，或等待期间显示加载状态")
    else:
        handled = final["error"]
        resilient = recovered
        labels = ("显示了可见的错误提示", "重试发起新请求并恢复页面")

    checks = [
        ("故障期间页面未白屏", 40, intact),
        (labels[0], 30, intact and handled),
        (labels[1], 20, intact and handled and resilient),
        ("页面没有未捕获的异常", 10, intact and not errors),
    ]
    items = [{"check": label, "points": points, "passed": passed} for label, points, passed in checks]
    return sum(item["points"] for item in items if item["passed"]), items

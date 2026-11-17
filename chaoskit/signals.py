import base64
from time import monotonic

MARKERS = {name: f'[data-chaoskit="{name}"]' for name in ("loading", "error", "retry", "success")}

DOM_SIGNALS = """
selectors => {
  function visible(el) {
    if (!el.checkVisibility({checkOpacity: true, checkVisibilityCSS: true})) return false;
    const box = el.getBoundingClientRect();
    return box.width > 0 && box.height > 0;
  }
  function clickable(el) {
    if (el.matches(':disabled,[aria-disabled="true"]') || el.closest('[inert]')) return false;
    const box = el.getBoundingClientRect();
    const top = document.elementFromPoint(box.x + box.width / 2, box.y + box.height / 2);
    return el.contains(top);
  }
  const items = Object.entries(selectors).map(([name, selector]) => {
    const elements = Array.from(document.querySelectorAll(selector)).filter(visible);
    return [name, name === "retry" ? elements.some(clickable) : elements.length > 0];
  });
  const text = document.body.innerText.trim();
  return {...Object.fromEntries(items), text: text.slice(0, 300), textLength: text.length};
}
"""

PNG_VARIANCE = """
async png => {
  const bytes = Uint8Array.from(atob(png), value => value.charCodeAt(0));
  const image = await createImageBitmap(new Blob([bytes], {type: "image/png"}));
  const width = 48, height = 32;
  const context = new OffscreenCanvas(width, height).getContext("2d");
  context.drawImage(image, 0, 0, width, height);
  image.close();
  const data = context.getImageData(0, 0, width, height).data;
  let sum = 0, squares = 0;
  for (let i = 0; i < data.length; i += 4) {
    const luma = .2126 * data[i] + .7152 * data[i + 1] + .0722 * data[i + 2];
    sum += luma;
    squares += luma * luma;
  }
  return (squares / (width * height)) - (sum / (width * height)) ** 2;
}
"""


def sample(page, outfile, started, planned_ms):
    actual_ms = round((monotonic() - started) * 1000)
    signals = page.evaluate(DOM_SIGNALS, MARKERS)
    screenshot = page.screenshot(path=outfile, animations="disabled", timeout=3000)
    variance = page.evaluate(PNG_VARIANCE, base64.b64encode(screenshot).decode("ascii"))
    signals["blank"] = signals["textLength"] < 2 and variance < 1.0
    state = next((flag for flag in ("blank", "error", "loading", "success") if signals[flag]), "unknown")
    return {"planned_ms": planned_ms, "actual_ms": actual_ms, "state": state,
            "image": outfile.name, "variance": round(variance, 2), **signals}

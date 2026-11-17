const fixture = document.body.dataset.fixture;
const signal = name => document.querySelector('[data-chaoskit="' + name + '"]');
const loading = signal("loading");
const errorMessage = signal("error");
const success = signal("success");
const retry = signal("retry");

function setState(state) {
  loading.hidden = state !== "loading";
  errorMessage.hidden = state !== "error";
  success.hidden = state !== "success";
  if (retry) retry.hidden = state !== "error";
}

async function loadOrder() {
  setState("loading");
  try {
    const response = await fetch("/api/orders/1", {signal: AbortSignal.timeout(2200)});
    if (!response.ok) throw new Error("HTTP " + response.status);
    const order = await response.json();
    const product = order.item === "Mechanical keyboard" ? "机械键盘" : order.item;
    const status = order.status === "Paid" ? "已付款" : order.status;
    document.querySelector("#product").textContent = product;
    document.querySelector("#payment").textContent = `${status} · ${order.amount}`;
    setState("success");
  } catch {
    if (fixture === "spinner") return;
    if (fixture === "blank") {
      document.documentElement.style.background = "white";
      document.body.replaceChildren();
      document.body.className = "";
      return;
    }
    setState("error");
  }
}

if (retry) retry.addEventListener("click", loadOrder);
loadOrder();

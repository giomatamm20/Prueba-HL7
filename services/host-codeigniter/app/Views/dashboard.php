<!doctype html>
<html lang="es">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>HL7 Host Middleware</title>
  <style>
    :root { --bg: #f3f7f7; --card: #fff; --ink: #163135; --muted: #5d7377; --ok: #087f5b; --bad: #c92a2a; --line: #dae5e5; --accent: #126b78; }
    * { box-sizing: border-box; }
    body { margin: 0; font: 14px Arial, sans-serif; color: var(--ink); background: var(--bg); }
    header { background: #103f49; color: white; padding: 20px 28px; display: flex; justify-content: space-between; align-items: center; }
    h1 { margin: 0; font-size: 22px; } header p { margin: 5px 0 0; color: #d3e4e6; }
    .badge { padding: 8px 12px; border-radius: 18px; font-weight: bold; background: #294f57; }
    .badge.ok { background: var(--ok); } .badge.bad { background: var(--bad); }
    main { padding: 20px; max-width: 1500px; margin: auto; }
    .metrics { display: grid; grid-template-columns: repeat(5, minmax(135px, 1fr)); gap: 12px; margin-bottom: 18px; }
    .card { background: var(--card); border: 1px solid var(--line); border-radius: 9px; padding: 14px; box-shadow: 0 1px 2px #0000000d; }
    .metric label { display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; margin-bottom: 8px; }
    .metric strong { font-size: 26px; }
    .grid { display: grid; grid-template-columns: 1.2fr 1fr; gap: 16px; }
    .order-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px; }
    h2 { font-size: 16px; margin: 0 0 12px; color: var(--accent); }
    label { display: block; color: var(--muted); font-size: 12px; text-transform: uppercase; margin-bottom: 6px; }
    input { width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 9px; font: inherit; color: var(--ink); background: white; }
    button { border: 0; border-radius: 6px; padding: 9px 12px; background: var(--accent); color: white; font-weight: bold; cursor: pointer; }
    button:disabled { background: #9aabad; cursor: not-allowed; }
    .form-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
    .full { grid-column: 1 / -1; }
    .tests { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
    .test-option { border: 1px solid var(--line); border-radius: 6px; padding: 8px; background: #f8fbfb; display: flex; gap: 7px; align-items: flex-start; min-height: 48px; }
    .test-option input { width: auto; margin-top: 2px; }
    .test-option span { font-weight: bold; display: block; }
    .test-option small { color: var(--muted); display: block; margin-top: 2px; }
    .actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .status { color: var(--muted); font-size: 12px; }
    table { width: 100%; border-collapse: collapse; font-size: 13px; }
    th, td { padding: 8px 7px; border-bottom: 1px solid var(--line); text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: normal; text-transform: uppercase; font-size: 11px; }
    .scroll { max-height: 320px; overflow: auto; }
    pre { background: #122a30; color: #e8f2f2; border-radius: 6px; padding: 10px; white-space: pre-wrap; word-break: break-all; margin: 8px 0 0; font-size: 12px; }
    details summary { cursor: pointer; color: var(--accent); }
    .ack-AA { color: var(--ok); font-weight: bold; } .ack-AE { color: var(--bad); font-weight: bold; }
    @media (max-width: 900px) { .metrics, .grid, .order-grid, .form-grid, .tests { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <div><h1>HL7 Host / CRM Monitor</h1><p>MLLP receiver, ACK and clinical result processing</p></div>
    <div id="connection" class="badge">Esperando analyzer</div>
  </header>
  <main>
    <section class="metrics">
      <div class="card metric"><label>Mensajes</label><strong id="messagesCount">0</strong></div>
      <div class="card metric"><label>Procesados</label><strong id="processedCount">0</strong></div>
      <div class="card metric"><label>Rechazados</label><strong id="rejectedCount">0</strong></div>
      <div class="card metric"><label>Ultimo ACK</label><strong id="ackCode">-</strong></div>
      <div class="card metric"><label>Origen</label><strong id="sourceIp" style="font-size:18px">-</strong></div>
    </section>
    <section class="order-grid">
      <article class="card">
        <h2>Nueva orden para CELL-DYN Ruby</h2>
        <form id="orderForm" class="form-grid">
          <div><label>Orden</label><input id="orderId" required></div>
          <div><label>Barcode / muestra</label><input id="sampleId" required></div>
          <div><label>ID paciente</label><input id="patientId" required></div>
          <div><label>Nombre paciente</label><input id="patientName" placeholder="APELLIDO^NOMBRE"></div>
          <div><label>Destino</label><input id="destinationHost" value="localhost"></div>
          <div><label>Puerto</label><input id="destinationPort" type="number" min="1" max="65535" value="5001"></div>
          <div class="full"><label>Examenes</label><div id="testCatalog" class="tests"></div></div>
          <div class="full actions"><button id="sendOrder" type="submit">Enviar orden</button><span id="orderStatus" class="status"></span></div>
        </form>
      </article>
      <article class="card">
        <h2>Catalogo de examenes</h2>
        <form id="catalogForm" class="form-grid">
          <div><label>Codigo</label><input id="testCode" required placeholder="MCV"></div>
          <div><label>Nombre</label><input id="testName" required placeholder="Volumen corpuscular medio"></div>
          <div class="full actions"><button type="submit">Agregar / actualizar</button><span id="catalogStatus" class="status"></span></div>
        </form>
        <div class="scroll" style="margin-top:12px"><table><thead><tr><th>Codigo</th><th>Nombre</th></tr></thead><tbody id="catalogRows"></tbody></table></div>
      </article>
    </section>
    <section class="grid">
      <article class="card">
        <h2>Ordenes enviadas al Ruby</h2>
        <div class="scroll"><table><thead><tr><th>Fecha</th><th>Orden</th><th>Muestra</th><th>Examenes</th><th>ACK</th><th>Estado</th></tr></thead><tbody id="outboundOrders"></tbody></table></div>
      </article>
      <article class="card">
        <h2>Mensajes HL7 recibidos y ACK</h2>
        <div id="messages" class="scroll"></div>
      </article>
      <article class="card">
        <h2>Resultados procesados</h2>
        <div class="scroll"><table><thead><tr><th>Paciente</th><th>Orden</th><th>Prueba</th><th>Valor</th><th>Fecha</th></tr></thead><tbody id="results"></tbody></table></div>
      </article>
      <article class="card">
        <h2>Pacientes</h2>
        <table><thead><tr><th>ID</th><th>Nombre</th><th>Actualizado</th></tr></thead><tbody id="patients"></tbody></table>
      </article>
      <article class="card">
        <h2>Ordenes / Paneles</h2>
        <table><thead><tr><th>Orden</th><th>Paciente</th><th>Panel</th><th>Mensaje</th></tr></thead><tbody id="orders"></tbody></table>
      </article>
    </section>
  </main>
  <script>
    const el = id => document.getElementById(id);
    const fmt = value => value ? new Date(value).toLocaleString() : "-";
    const esc = value => String(value ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
    async function get(path) { const r = await fetch(path); if (!r.ok) throw Error(path); return r.json(); }
    async function post(path, body) {
      const r = await fetch(path, { method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify(body) });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw Error(data.messages?.error || data.message || path);
      return data;
    }
    function nextId(prefix) {
      const stamp = new Date().toISOString().replace(/\D/g, "").slice(0, 14);
      return `${prefix}${stamp}`;
    }
    function selectedTests() {
      return [...document.querySelectorAll("[name=test]:checked")].map(item => item.value);
    }
    function testsFromRow(value) {
      if (Array.isArray(value)) return value;
      try { return JSON.parse(value || "[]"); } catch { return []; }
    }
    async function loadCatalog() {
      const catalog = await get("/api/test-catalog");
      el("testCatalog").innerHTML = catalog.map(t => `<label class="test-option"><input name="test" type="checkbox" value="${esc(t.code)}"><div><span>${esc(t.code)}</span><small>${esc(t.name)}</small></div></label>`).join("");
      el("catalogRows").innerHTML = catalog.map(t => `<tr><td>${esc(t.code)}</td><td>${esc(t.name)}</td></tr>`).join("");
    }
    async function refreshOutbound() {
      const outbound = await get("/api/outbound-orders?limit=20");
      el("outboundOrders").innerHTML = outbound.map(o => `<tr><td>${fmt(o.created_at)}</td><td>${esc(o.order_external_id)}</td><td>${esc(o.sample_id)}</td><td>${esc(testsFromRow(o.requested_tests).join(", "))}</td><td><span class="ack-${esc(o.ack_code)}">${esc(o.ack_code || "-")}</span></td><td>${esc(o.status)}${o.error_message ? `<br><small>${esc(o.error_message)}</small>` : ""}</td></tr>`).join("");
    }
    async function refresh() {
      try {
        const [status, messages, patients, orders, results] = await Promise.all([
          get("/api/status"), get("/api/messages?limit=20"), get("/api/patients"), get("/api/orders"), get("/api/results?limit=40")
        ]);
        const connected = Boolean(status.listener.lastConnectionAt);
        el("connection").textContent = connected ? "Analyzer conectado / activo" : "Esperando analyzer";
        el("connection").className = "badge " + (connected ? "ok" : "");
        el("messagesCount").textContent = status.database.message_count;
        el("processedCount").textContent = status.database.processed_count;
        el("rejectedCount").textContent = status.database.rejected_count;
        el("ackCode").textContent = status.listener.lastAckCode || "-";
        el("sourceIp").textContent = status.listener.lastSourceIp || "-";
        el("messages").innerHTML = messages.map(m => `<details><summary>${fmt(m.received_at)} | ${esc(m.message_type)} | ${esc(m.message_control_id)} | <span class="ack-${esc(m.ack_code)}">${esc(m.ack_code)}</span></summary><pre>${esc(m.raw_message)}</pre><pre>${esc(m.ack_message)}</pre></details>`).join("");
        el("patients").innerHTML = patients.map(p => `<tr><td>${esc(p.patient_external_id)}</td><td>${esc(p.full_name)}</td><td>${fmt(p.updated_at)}</td></tr>`).join("");
        el("orders").innerHTML = orders.map(o => `<tr><td>${esc(o.order_external_id)}</td><td>${esc(o.patient_external_id)}</td><td>${esc(o.panel_code)}</td><td>${esc(o.last_message_type)}</td></tr>`).join("");
        el("results").innerHTML = results.map(r => `<tr><td>${esc(r.patient_external_id)}</td><td>${esc(r.order_external_id)}</td><td>${esc(r.test_code)}</td><td>${esc(r.value_text)} ${esc(r.unit)}</td><td>${fmt(r.observed_at)}</td></tr>`).join("");
        await refreshOutbound();
      } catch (error) {
        el("connection").textContent = "Host sin respuesta";
        el("connection").className = "badge bad";
      }
    }
    el("orderId").value = nextId("ORD");
    el("sampleId").value = nextId("SMP");
    el("patientId").value = "12345";
    el("orderForm").addEventListener("submit", async event => {
      event.preventDefault();
      el("sendOrder").disabled = true;
      el("orderStatus").textContent = "Enviando...";
      try {
        await post("/api/outbound-orders", {
          orderId: el("orderId").value, sampleId: el("sampleId").value,
          patientId: el("patientId").value, patientName: el("patientName").value,
          destinationHost: el("destinationHost").value, destinationPort: Number(el("destinationPort").value),
          tests: selectedTests()
        });
        el("orderStatus").textContent = "Orden enviada";
        el("orderId").value = nextId("ORD");
        el("sampleId").value = nextId("SMP");
        await refreshOutbound();
      } catch (error) {
        el("orderStatus").textContent = error.message;
      } finally {
        el("sendOrder").disabled = false;
      }
    });
    el("catalogForm").addEventListener("submit", async event => {
      event.preventDefault();
      el("catalogStatus").textContent = "Guardando...";
      try {
        await post("/api/test-catalog", { code: el("testCode").value, name: el("testName").value });
        el("catalogStatus").textContent = "Catalogo actualizado";
        el("catalogForm").reset();
        await loadCatalog();
      } catch (error) {
        el("catalogStatus").textContent = error.message;
      }
    });
    loadCatalog();
    refresh();
    setInterval(refresh, 2000);
  </script>
</body>
</html>

// ui_drive scenario: the page stays in step with connect, disconnect, and send
// replies that arrive late or are refused. Replaces the page's connect and send
// bridge calls with stand-ins whose replies the scenario releases on demand, so
// each race happens the same way every run. Disconnect, Disconnect all, and
// saving a server go through the real Python side.
//
// Stays offline: no real connection is ever opened. The test server's host is
// 192.0.2.10, an address reserved for documentation that no real machine uses.

export default async function sessionSync(helpers) {
  const { click, type, evaluate, waitFor, check, screenshot } = helpers;
  const settle = (id) => waitFor(
    `document.getElementById('${id}').classList.contains('show') && ` +
    `document.querySelector('#${id} .modal').getAnimations().length === 0`, 5000);
  const errCount = (text) => evaluate(
    `Array.from(document.querySelectorAll('#console .ln.err .msg')).filter(m => m.textContent === ${JSON.stringify(text)}).length`);
  const isConnected = () => evaluate("document.querySelector('[data-card]').classList.contains('connected')");
  const connectIdle = () => waitFor("document.querySelector('[data-card] [data-connect]').textContent === 'Connect'", 5000);
  const settled = () => new Promise(r => setTimeout(r, 300));

  // Connect stand-ins: "held" keeps the reply until release(reply); "instant"
  // answers at once with the reply given.
  const holdConnect = () => evaluate(
    "(() => { api().connect = () => new Promise(res => { window.__release = res; }); })()");
  const instantConnect = (reply) => evaluate(
    `(() => { api().connect = () => Promise.resolve(${JSON.stringify(reply)}); })()`);
  const release = (reply) => evaluate(`window.__release(${JSON.stringify(reply)})`);
  const stubSend = (js) => evaluate(`(() => { api().send = ${js}; })()`);

  const startConnect = async () => {
    await type("[data-card] [data-pw]", "test-password");
    await click("[data-card] [data-connect]");
    await waitFor("document.querySelector('[data-card] [data-connect]').textContent === 'Connecting…'", 5000);
  };
  const disconnectAll = async () => {
    await click("#endAllBtn");
    await settle("confirm");
    await click("#confirmYes");
    await waitFor("!document.getElementById('confirm').classList.contains('show')", 5000);
    await settled();
  };
  const saveServerAs = async (name) => {
    await click("[data-card] [data-edit]");
    await settle("modal");
    await type("#fName", name);
    await click("#saveBtn");
    await waitFor("!document.getElementById('modal').classList.contains('show')", 5000);
    await settled();
  };

  await waitFor("window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_config === 'function'", 10000);

  // Set up one server through the real editor.
  await click("#addBtn");
  await settle("modal");
  await evaluate("(() => { const s = document.getElementById('fGame'); s.value = 'cs2'; s.dispatchEvent(new Event('change', {bubbles: true})); })()");
  await type("#fName", "Sync Server");
  await type("#fHost", "192.0.2.10");
  await type("#fPort", "27015");
  await click("#saveBtn");
  await waitFor("document.querySelectorAll('[data-card]').length === 1 && !document.getElementById('modal').classList.contains('show')", 5000);

  await holdConnect();
  const stubbed = await evaluate("api().connect.toString().includes('__release')");
  check("connect stand-in installed", stubbed);

  // a) A connect failure that arrives after Disconnect all stays silent.
  await startConnect();
  await disconnectAll();
  await release({ ok: false, error: "Connection was cancelled." });
  await connectIdle();
  await settled();
  check("superseded connect failure is not shown", (await errCount("Connection was cancelled.")) === 0,
    String(await errCount("Connection was cancelled.")));
  check("card stays disconnected after a superseded failure", (await isConnected()) === false);

  // b) A connect success that arrives after Disconnect all does not mark the card.
  await startConnect();
  await disconnectAll();
  await release({ ok: true });
  await connectIdle();
  await settled();
  check("superseded connect success leaves the card disconnected", (await isConnected()) === false);

  // c) A current connect still works, and a store warning always shows.
  // Disconnect all first, so a failure in b) cannot hide the password box.
  await disconnectAll();
  await startConnect();
  await release({ ok: true, warning: "Store warning for the check." });
  await connectIdle();
  await settled();
  check("current connect marks the card connected", (await isConnected()) === true);
  check("connect warning is shown", (await errCount("Store warning for the check.")) === 1);

  // d) Saving a server keeps the connected look.
  await saveServerAs("Sync Server Renamed");
  check("connected look survives saving a server", (await isConnected()) === true);
  await screenshot("connected-after-save");

  // e) Disconnect clears it (real Python call).
  if (await isConnected()) {
    await click("[data-card] [data-disconnect]");
    await waitFor("!document.querySelector('[data-card]').classList.contains('connected')", 5000)
      .catch(() => {});
    check("disconnect clears the connected look", (await isConnected()) === false);
  } else {
    check("disconnect clears the connected look", false, "not run: the card lost its connected look in d)");
  }

  // f) A connect that finishes after the cards were rebuilt marks the card on screen.
  await holdConnect();
  await startConnect();
  await saveServerAs("Sync Server Rebuilt");
  await release({ ok: true });
  await settled();
  check("late connect after a rebuild marks the card on screen", (await isConnected()) === true);
  // The send checks need the command box, so connect again if f) failed.
  if (!(await isConnected())) {
    await instantConnect({ ok: true });
    await type("[data-card] [data-pw]", "test-password");
    await click("[data-card] [data-connect]");
    await waitFor("document.querySelector('[data-card]').classList.contains('connected')", 5000);
  }

  // g) Refused and failed sends from the custom command box.
  await stubSend("() => Promise.resolve({ ok: false, error: 'Not connected.' })");
  await type("[data-card] [data-custom]", "status");
  await click("[data-card] [data-run]");
  await settled();
  check("refused custom command shows its error", (await errCount("Not connected.")) === 1,
    String(await errCount("Not connected.")));
  const kept = await evaluate("document.querySelector('[data-card] [data-custom]').value");
  check("refused custom command keeps the typed text", kept === "status", kept);

  await stubSend("() => Promise.reject(new Error('Bridge call failed.'))");
  await click("[data-card] [data-run]");
  await settled();
  check("failed custom command shows an error", (await errCount("Bridge call failed.")) === 1);
  const keptAfterThrow = await evaluate("document.querySelector('[data-card] [data-custom]').value");
  check("failed custom command keeps the typed text", keptAfterThrow === "status", keptAfterThrow);

  await stubSend("() => Promise.resolve({ ok: true })");
  await click("[data-card] [data-run]");
  await settled();
  const clearedValue = await evaluate("document.querySelector('[data-card] [data-custom]').value");
  check("accepted custom command clears the box", clearedValue === "", clearedValue);

  // h) A refused quick button shows its error. Uses a button with no confirm and no value.
  const qIndex = await evaluate(
    "CAT_BY_KEY.cs2.buttons.findIndex(b => !b.confirm && !b.command.includes('{arg}'))");
  check("found a plain quick button", qIndex >= 0, String(qIndex));
  await stubSend("() => Promise.resolve({ ok: false, error: 'Not connected.' })");
  await click(`[data-card] [data-qbtn="${qIndex}"]`);
  await settled();
  check("refused quick button shows its error", (await errCount("Not connected.")) === 2,
    String(await errCount("Not connected.")));
  await screenshot("send-errors");

  // Leave the page disconnected through the real Python side.
  await disconnectAll();
  check("Disconnect all clears the connected look", (await isConnected()) === false);
}

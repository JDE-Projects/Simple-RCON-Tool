// ui_drive scenario: connect the real app to the local Counter-Strike 2 test
// server (started by cs2_fixture.py) and press every built-in button, checking
// the exact text each reply puts in the console. Needs the server installed by
// the live test runner (`python -m tools.live_test.run run cs2`).
//
// Replies are checked character by character: a stray control character (for
// example a leftover end-marker packet) shows as a box in the console and
// fails the check. Kick uses one of the map's own bots, named as Status shows it.

export default async function cs2Console(helpers) {
  const { click, type, evaluate, waitFor, check, screenshot, fixture } = helpers;
  const settle = (id) => waitFor(
    `document.getElementById('${id}').classList.contains('show') && ` +
    `document.querySelector('#${id} .modal').getAnimations().length === 0`, 5000);
  // Every console line, with any control character shown as <code>.
  const lines = () => evaluate(
    "Array.from(document.querySelectorAll('#console .ln .msg')).map(m => " +
    "Array.from(m.textContent).map(c => c.charCodeAt(0) < 32 && c !== '\\t' ? '<' + c.charCodeAt(0) + '>' : c).join(''))");
  // The page disables the card's buttons while a command runs.
  const idle = () => waitFor("!document.querySelector('[data-card] [data-qbtn]').disabled", 10000).catch(() => {});

  // Presses a built-in button (typing its value when it asks for one) and
  // returns the console lines its reply added.
  const press = async (label, value) => {
    const index = await evaluate(
      `Array.from(document.querySelectorAll('[data-card] [data-qbtn]')).findIndex(b => b.textContent === ${JSON.stringify(label)})`);
    const before = (await lines()).length;
    await click(`[data-card] [data-qbtn="${index}"]`);
    if (value !== undefined) {
      await settle("prompt");
      await type("#promptInput", value);
      await click("#promptYes");
    }
    await waitFor(`document.querySelectorAll('#console .ln').length >= ${before} + 2`, 10000).catch(() => {});
    await new Promise(r => setTimeout(r, 1000));
    await idle();
    const added = (await lines()).slice(before);
    return added[0] === `$ ${label}` ? added.slice(1) : added;
  };
  const statusOk = (reply) => reply[0] === "Server:  Running [127.0.0.1:27015]" && reply[reply.length - 1] === "#end";
  const onMap = (reply, map) => reply.some(l => l.startsWith(`loaded spawngroup(  1)  : SV:  [1: ${map} `));
  const bots = (reply) => reply.map(l => /^\s+\d+\s+BOT\s.*'(.+)'$/.exec(l)).filter(Boolean).map(m => m[1]);

  await waitFor("window.pywebview && window.pywebview.api && typeof window.pywebview.api.get_config === 'function'", 10000);

  await click("#addBtn");
  await settle("modal");
  await evaluate("(() => { const s = document.getElementById('fGame'); s.value = 'cs2'; s.dispatchEvent(new Event('change', {bubbles: true})); })()");
  await type("#fHost", fixture.host);
  await type("#fPort", String(fixture.port));
  await click("#saveBtn");
  await waitFor("document.querySelectorAll('[data-card]').length === 1 && !document.getElementById('modal').classList.contains('show')", 5000);

  await type("[data-card] [data-pw]", fixture.password);
  await click("[data-card] [data-connect]");
  const connected = await waitFor("document.querySelector('[data-card]').classList.contains('connected')", 15000)
    .then(() => true).catch(() => false);
  check("connected to the Counter-Strike 2 server", connected, JSON.stringify(await lines()));

  const status1 = await press("Status");
  check("Status: whole reply, from the server line to #end", statusOk(status1), JSON.stringify(status1));
  check("Status: names the starting map", onMap(status1, "de_dust2"), JSON.stringify(status1));

  const status2 = await press("Status");
  check("Status: a second reply on the same connection is clean", statusOk(status2), JSON.stringify(status2));

  const say = await press("Say", "SRT app check");
  check("Say: reply is the chat line", JSON.stringify(say) === JSON.stringify(["[All Chat][Console (0)]: SRT app check"]), JSON.stringify(say));

  const bot = bots(status2)[0];
  check("Status: lists a bot to kick", Boolean(bot), JSON.stringify(status2));
  const kick = await press("Kick", bot);
  check("Kick: reply names the kicked bot", JSON.stringify(kick) === JSON.stringify([`${bot} kicked by Console (NETWORK_DISCONNECT_KICKED)`]), JSON.stringify(kick));
  const status3 = await press("Status");
  check("Status: the kicked bot is gone", statusOk(status3) && !bots(status3).includes(bot), JSON.stringify(status3));

  const change = await press("Change Level", "de_mirage");
  check("Change Level: reply confirms the change",
    change[0] === "Changelevel to de_mirage" && change[1] === 'changelevel "de_mirage"', JSON.stringify(change));

  // The map change itself, through the app (the new map loads in about a second).
  let status4 = [];
  for (let attempt = 0; attempt < 5 && !onMap(status4, "de_mirage"); attempt++) {
    await new Promise(r => setTimeout(r, 3000));
    status4 = await press("Status");
  }
  check("Status: names the new map after Change Level", onMap(status4, "de_mirage"), JSON.stringify(status4));

  const all = await lines();
  const marked = all.filter(l => /<\d+>/.test(l));
  check("no control characters anywhere in the console", marked.length === 0, JSON.stringify(marked));
  const errors = await evaluate("Array.from(document.querySelectorAll('#console .ln.err .msg')).map(m => m.textContent)");
  check("no error lines in the console", errors.length === 0, JSON.stringify(errors));
  await screenshot("console");
}
